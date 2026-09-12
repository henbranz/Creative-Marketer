from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.assembly.domain import (
    AssemblyJobStatus,
    AssemblyNotFound,
    AssemblyOutputInvalid,
    AssemblyPlan,
    AssemblySourceDigestMismatch,
    AssemblySourceRightsChanged,
    FinalCreative,
    final_creative_digest,
)
from creative_marketer.assembly.rendering import (
    MaterializedSource,
    MediaAssemblyRenderer,
    RenderResult,
)
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.catalog.asset_application import AssetService, ObjectStore
from creative_marketer.catalog.asset_domain import (
    AllowedUse,
    Asset,
    AssetKind,
    AssetRole,
    RightsStatus,
)
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import tenant_event
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.infrastructure.database.assembly_repositories import (
    SqlAlchemyAssemblyRepository,
)
from creative_marketer.infrastructure.database.assembly_schema import (
    assembly_jobs,
    final_creatives,
)
from creative_marketer.infrastructure.database.audit import PostgresAuditWriter
from creative_marketer.infrastructure.database.catalog_schema import assets, products
from creative_marketer.infrastructure.database.event_delivery import PostgresOutboxWriter
from creative_marketer.infrastructure.database.production_schema import asset_lineage
from creative_marketer.infrastructure.database.schema import memberships, tenants, users


@dataclass(frozen=True, slots=True)
class AssemblyWorkloadIdentity:
    actor_id: UUID
    workload_id: str
    environment: str

    def __post_init__(self) -> None:
        if not self.workload_id.strip() or len(self.workload_id) > 128:
            raise ValueError("assembly workload identity is invalid")


@dataclass(slots=True)
class AssemblyJobExecutor:
    sessions: async_sessionmaker[AsyncSession]
    object_store: ObjectStore
    assets_service: AssetService
    renderer: MediaAssemblyRenderer
    workload: AssemblyWorkloadIdentity
    maximum_total_source_bytes: int = 750 * 1024 * 1024

    async def execute(self, tenant_id: UUID, job_id: UUID) -> FinalCreative:
        context, plan, source_rows = await self._claim(tenant_id, job_id)
        if isinstance(plan, FinalCreative):
            return plan
        try:
            with tempfile.TemporaryDirectory(prefix="cm-assembly-") as directory:
                os.chmod(directory, 0o700)
                sources = await self._materialize(plan, source_rows, directory)
                result = await self.renderer.render(plan, sources, directory)
                await self._transition(
                    tenant_id, job_id, AssemblyJobStatus.RENDERING, AssemblyJobStatus.IMPORTING
                )
                output = await self._find_or_import(context, job_id, plan, result)
                final = FinalCreative(
                    tenant_id,
                    plan.product_id,
                    plan.id,
                    plan.semantic_digest,
                    plan.production_plan_id,
                    plan.production_plan_digest,
                    plan.creative_concept_id,
                    plan.creative_concept_digest,
                    output.id,
                    output.digest or "",
                    result.renderer,
                    result.renderer_version,
                    result.duration_ms,
                    result.width,
                    result.height,
                    result.fps,
                    result.has_audio,
                    len(plan.items),
                    final_creative_digest(
                        assembly_plan_digest=plan.semantic_digest,
                        output_asset_digest=output.digest or "",
                        renderer=result.renderer,
                        renderer_version=result.renderer_version,
                    ),
                )
                await self._persist_success(context, job_id, plan, final)
                return final
        except Exception as error:
            await self._fail(tenant_id, job_id, getattr(error, "code", "ASSEMBLY_RENDER_FAILED"))
            raise

    async def _claim(
        self, tenant_id: UUID, job_id: UUID
    ) -> tuple[ExecutionContext, AssemblyPlan | FinalCreative, list[dict[str, object]]]:
        async with self.sessions() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            job = (
                (
                    await session.execute(
                        select(assembly_jobs).where(assembly_jobs.c.id == job_id).with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if job is None:
                raise AssemblyNotFound("AssemblyJob not found")
            repo = SqlAlchemyAssemblyRepository(session)
            record = await repo.get_plan(job["assembly_plan_id"])
            if record is None:
                raise AssemblyNotFound("AssemblyPlan not found")
            if job["status"] == "SUCCEEDED" and job["final_creative_id"]:
                existing = await repo.get_final(job["final_creative_id"])
                if existing is None:
                    raise AssemblyNotFound("FinalCreative mapping is invalid")
                return (
                    await self._context(session, tenant_id, job["created_by_user_id"]),
                    existing.final_creative,
                    [],
                )
            if job["status"] not in {"READY", "RENDERING"}:
                raise AssemblyNotFound("AssemblyJob is not executable")
            source_rows = list(
                (
                    await session.execute(
                        select(assets).where(
                            assets.c.id.in_([item.source_asset_id for item in record.plan.items])
                        )
                    )
                ).mappings()
            )
            by_id = {row["id"]: row for row in source_rows}
            total = 0
            for item in record.plan.items:
                row = by_id.get(item.source_asset_id)
                if (
                    row is None
                    or row["status"] != "ready"
                    or row["digest"] != item.source_asset_digest
                ):
                    raise AssemblySourceDigestMismatch("ASSEMBLY_SOURCE_DIGEST_MISMATCH")
                if row["rights_status"] != "confirmed" or "generation_input" not in (
                    row["allowed_uses"] or []
                ):
                    raise AssemblySourceRightsChanged("ASSEMBLY_SOURCE_RIGHTS_CHANGED")
                total += int(row["byte_size"] or 0)
            if total > self.maximum_total_source_bytes:
                raise AssemblyOutputInvalid("ASSEMBLY_SOURCE_LIMIT")
            if job["status"] == "READY":
                await session.execute(
                    update(assembly_jobs)
                    .where(assembly_jobs.c.id == job_id)
                    .values(status="RENDERING", renderer="ffmpeg", updated_at=datetime.now(UTC))
                )
            return (
                await self._context(session, tenant_id, job["created_by_user_id"]),
                record.plan,
                [dict(row) for row in source_rows],
            )

    async def _context(
        self, session: AsyncSession, tenant_id: UUID, user_id: UUID
    ) -> ExecutionContext:
        row = (
            await session.execute(
                select(
                    memberships.c.role,
                    memberships.c.status,
                    users.c.status.label("user_status"),
                    tenants.c.status.label("tenant_status"),
                )
                .join(users, users.c.id == memberships.c.user_id)
                .join(tenants, tenants.c.id == memberships.c.tenant_id)
                .where(memberships.c.tenant_id == tenant_id, memberships.c.user_id == user_id)
            )
        ).first()
        if (
            row is None
            or row.status != "active"
            or row.user_status != "active"
            or row.tenant_status != "active"
        ):
            raise AssemblySourceRightsChanged("ASSEMBLY_INITIATOR_INACTIVE")
        return ExecutionContext(
            tenant_id,
            Actor(ActorKind.WORKLOAD, self.workload.actor_id),
            user_id,
            MembershipRole(row.role),
            MembershipStatus(row.status),
            self.workload.environment,
            AuthenticationAssurance(datetime.now(UTC), "workload", "deployment"),
        )

    async def _materialize(
        self, plan: AssemblyPlan, rows: list[dict[str, object]], directory: str
    ) -> tuple[MaterializedSource, ...]:
        by_id = {row["id"]: row for row in rows}
        result: list[MaterializedSource] = []
        for index, item in enumerate(plan.items):
            row = by_id[item.source_asset_id]
            suffix = ".png" if row["kind"] == "image" else ".mp4"
            target = Path(directory) / f"source-{index:03d}{suffix}"
            digest = hashlib.sha256()
            size = 0
            with target.open("wb") as handle:
                async for chunk in self.object_store.stream(key=str(row["object_key"])):
                    size += len(chunk)
                    if size > self.maximum_total_source_bytes:
                        raise AssemblyOutputInvalid("ASSEMBLY_SOURCE_LIMIT")
                    digest.update(chunk)
                    handle.write(chunk)
            if "sha256:" + digest.hexdigest() != item.source_asset_digest:
                raise AssemblySourceDigestMismatch("ASSEMBLY_SOURCE_DIGEST_MISMATCH")
            has_audio = False
            if row["kind"] == "video" and hasattr(self.renderer, "source_has_audio"):
                probe = self.renderer.source_has_audio
                has_audio = await probe(str(target), directory)
            result.append(
                MaterializedSource(
                    str(item.source_asset_id), str(target), str(row["kind"]).upper(), has_audio
                )
            )
        return tuple(result)

    async def _find_or_import(
        self,
        context: ExecutionContext,
        job_id: UUID,
        plan: AssemblyPlan,
        result: RenderResult,
    ) -> Asset:
        filename = f"assembly-{job_id}.mp4"
        async with self.sessions() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
            existing = (
                (
                    await session.execute(
                        select(assets).where(
                            assets.c.original_filename == filename,
                            assets.c.role == "final_creative",
                            assets.c.status == "ready",
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                from creative_marketer.infrastructure.database.catalog_repositories import _asset

                return _asset(existing)
            brand_id = await session.scalar(
                select(products.c.brand_id).where(products.c.id == plan.product_id)
            )
        if brand_id is None:
            raise AssemblyNotFound("Product not found")
        with Path(result.path).open("rb") as content:
            output = await self.assets_service.ingest_generated(
                context,
                brand_id=brand_id,
                product_id=plan.product_id,
                kind=AssetKind.VIDEO,
                role=AssetRole.FINAL_CREATIVE,
                content=content,
                media_type="video/mp4",
                rights_status=RightsStatus.CONFIRMED,
                allowed_uses=(AllowedUse.INTERNAL_ANALYSIS, AllowedUse.GENERATION_INPUT),
                original_filename=filename,
                width=result.width,
                height=result.height,
                duration_ms=result.duration_ms,
            )
        if output.digest != result.digest or output.byte_size != result.byte_size:
            raise AssemblyOutputInvalid("ASSEMBLY_OUTPUT_INVALID")
        return output

    async def _persist_success(
        self, context: ExecutionContext, job_id: UUID, plan: AssemblyPlan, final: FinalCreative
    ) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
            existing = await session.scalar(
                select(final_creatives.c.id).where(
                    final_creatives.c.assembly_plan_id == final.assembly_plan_id
                )
            )
            if existing is None:
                await session.execute(
                    insert(final_creatives).values(
                        **{
                            column.name: getattr(final, column.name)
                            for column in final_creatives.c
                            if hasattr(final, column.name)
                        }
                    )
                )
                for source_id in dict.fromkeys(item.source_asset_id for item in plan.items):
                    await session.execute(
                        insert(asset_lineage).values(
                            id=uuid4(),
                            tenant_id=context.tenant_id,
                            parent_asset_id=source_id,
                            child_asset_id=final.output_asset_id,
                            relationship_type="ASSEMBLED_FROM",
                            created_at=datetime.now(UTC),
                        )
                    )
            await session.execute(
                update(assembly_jobs)
                .where(assembly_jobs.c.id == job_id)
                .values(
                    status="SUCCEEDED",
                    output_asset_id=final.output_asset_id,
                    final_creative_id=final.id,
                    renderer=final.renderer,
                    renderer_version=final.renderer_version,
                    updated_at=datetime.now(UTC),
                )
            )
            audit = PostgresAuditWriter(session)
            await audit.append(
                tenant_audit(
                    context,
                    action="assembly.render.succeeded",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="assembly_job",
                    resource_id=str(job_id),
                    after_digest=final.semantic_digest,
                    metadata=safe_metadata(
                        {
                            "final_creative_id": str(final.id),
                            "output_asset_id": str(final.output_asset_id),
                        }
                    ),
                )
            )
            outbox = PostgresOutboxWriter(session)
            await outbox.append(
                tenant_event(
                    context,
                    event_type="assembly.final_creative.created.v1",
                    schema_version=1,
                    aggregate_type="final_creative",
                    aggregate_id=final.id,
                    payload={
                        "final_creative_id": str(final.id),
                        "assembly_plan_id": str(final.assembly_plan_id),
                        "assembly_job_id": str(job_id),
                        "output_asset_id": str(final.output_asset_id),
                        "final_creative_digest": final.semantic_digest,
                        "output_asset_digest": final.output_asset_digest,
                    },
                    payload_schema_digest=EventContractRegistry().schema_digest(
                        "assembly.final_creative.created.v1"
                    ),
                    occurred_at=final.created_at,
                )
            )

    async def _transition(
        self, tenant_id: UUID, job_id: UUID, expected: AssemblyJobStatus, target: AssemblyJobStatus
    ) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            await session.execute(
                update(assembly_jobs)
                .where(assembly_jobs.c.id == job_id, assembly_jobs.c.status == expected.value)
                .values(status=target.value, updated_at=datetime.now(UTC))
            )

    async def _fail(self, tenant_id: UUID, job_id: UUID, code: str) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            await session.execute(
                update(assembly_jobs)
                .where(
                    assembly_jobs.c.id == job_id,
                    assembly_jobs.c.status.in_(("READY", "RENDERING", "IMPORTING")),
                )
                .values(status="FAILED", failure_code=str(code)[:64], updated_at=datetime.now(UTC))
            )
