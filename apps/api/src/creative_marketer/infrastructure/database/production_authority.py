from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import case, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.agent_runtime.domain import canonical_digest
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.catalog.asset_application import ObjectStore
from creative_marketer.catalog.asset_domain import detect_mime
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import tenant_event
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.production.application import (
    ImageReservationPricing,
    MediaRoute,
    SeedancePricing,
    initial_media_router,
)
from creative_marketer.production.domain import (
    GenerationJob,
    GenerationJobStatus,
    MediaKind,
    ProductionCapabilityChanged,
    ProductionCreativeRefreshRequired,
    ProductionNotFound,
    ProductionPermissionDenied,
    ProductionPlan,
    ProductionPlanDecisionState,
    ProductionPricingChanged,
    ProductionRightsChanged,
)
from creative_marketer.production.execution import ExecutableGeneration
from creative_marketer.production.media import MaterializedReference

from .agent_runtime_schema import agent_runs, research_snapshots
from .audit import PostgresAuditWriter
from .catalog_schema import assets, product_knowledge_snapshots, products
from .creative_schema import concept_decisions, concepts
from .event_delivery import PostgresOutboxWriter
from .production_repositories import SqlAlchemyProductionRepository
from .production_schema import (
    generation_jobs,
    generation_segments,
    media_budget_usage,
    production_plans,
    production_shots,
)
from .schema import memberships, tenants, users


@dataclass(frozen=True, slots=True)
class MediaWorkloadIdentity:
    actor_id: UUID
    workload_id: str
    environment: str

    def __post_init__(self) -> None:
        if not self.workload_id.strip() or len(self.workload_id) > 128:
            raise ValueError("media workload identity is invalid")


@dataclass(frozen=True, slots=True)
class _Prepared:
    tenant_id: UUID
    context: ExecutionContext
    requested_agent_definition_id: UUID


class SqlAlchemyGenerationAuthority:
    """Reload and revalidate canonical Production authority immediately before media I/O."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        object_store: ObjectStore,
        workload: MediaWorkloadIdentity,
        *,
        maximum_reference_bytes: int = 25 * 1024 * 1024,
        live_spend_cap: Decimal | None = None,
        live_product_id: UUID | None = None,
    ) -> None:
        self._factory = session_factory
        self._store = object_store
        self._workload = workload
        self._maximum_reference_bytes = maximum_reference_bytes
        self._live_spend_cap = live_spend_cap
        self._live_product_id = live_product_id
        self._prepared: dict[UUID, _Prepared] = {}

    async def authorize_resource(self, tenant_id: UUID, job_id: UUID) -> None:
        await self.current_job(tenant_id, job_id)

    async def current_job(self, tenant_id: UUID, job_id: UUID) -> GenerationJob:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            job = await SqlAlchemyProductionRepository(session).get_job(job_id)
            if job is None or job.tenant_id != tenant_id:
                raise ProductionNotFound("GenerationJob not found")
            return job

    async def prepare(self, tenant_id: UUID, job_id: UUID, kind: MediaKind) -> ExecutableGeneration:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            repository = SqlAlchemyProductionRepository(session)
            job = await repository.get_job(job_id)
            if job is None:
                raise ProductionNotFound("GenerationJob not found")
            if job.tenant_id != tenant_id or job.kind is not kind:
                raise ProductionPermissionDenied("GenerationJob authority mismatch")
            if job.status not in {
                GenerationJobStatus.READY,
                GenerationJobStatus.STARTING,
                GenerationJobStatus.PROCESSING,
                GenerationJobStatus.IMPORTING,
            }:
                raise ProductionPermissionDenied("GenerationJob lifecycle forbids execution")
            record = await repository.get_plan(job.production_plan_id)
            if record is None or record.decision is None:
                raise ProductionPermissionDenied("ProductionPlan is not approved")
            plan, decision = record.plan, record.decision
            if (
                decision.state is not ProductionPlanDecisionState.APPROVED_FOR_GENERATION
                or decision.plan_digest != plan.semantic_digest
                or decision.currency != plan.cost.currency
                or decision.estimated_max_cost != plan.cost.estimated_total_cost
            ):
                raise ProductionPermissionDenied("ProductionPlan approval binding is stale")
            await self._enforce_live_spend_cap(session, tenant_id, plan.product_id)
            route = initial_media_router().resolve(job.media_profile)
            expected_route = (
                decision.image_route_version
                if kind is MediaKind.IMAGE
                else decision.video_route_version
            )
            expected_pricing = (
                decision.image_pricing_version
                if kind is MediaKind.IMAGE
                else decision.video_pricing_version
            )
            if (
                route.capabilities.media_kind is not kind
                or route.route_version != expected_route
                or route.pricing_version != expected_pricing
                or job.route_version != route.route_version
                or job.pricing_version != route.pricing_version
                or job.provider != route.provider
                or job.model != route.model
            ):
                raise ProductionPricingChanged(
                    "exact approved media route or pricing is unavailable"
                )
            segment_row = (
                await session.execute(
                    select(generation_segments).where(
                        generation_segments.c.production_plan_id == plan.id,
                        generation_segments.c.segment_key == job.segment_key,
                    )
                )
            ).first()
            if segment_row is None:
                raise ProductionNotFound("GenerationSegment not found")
            segment = segment_row._mapping
            specification = dict(segment["generation_spec"])
            if canonical_digest(specification) != job.generation_spec_digest:
                raise ProductionPermissionDenied("generation specification digest changed")
            self._validate_capability(route, specification, segment["duration_seconds"])
            reserved = await self._expected_reservation(session, job, specification)
            if reserved != job.reserved_cost or job.currency != decision.currency:
                raise ProductionPricingChanged(
                    "GenerationJob reservation no longer matches pricing"
                )
            reserved_total = await session.scalar(
                select(func.coalesce(func.sum(media_budget_usage.c.amount), 0)).where(
                    media_budget_usage.c.generation_job_id == job.id,
                    media_budget_usage.c.entry_kind == "RESERVED",
                    media_budget_usage.c.currency == job.currency,
                )
            )
            if Decimal(reserved_total or 0) != job.reserved_cost:
                raise ProductionPermissionDenied("GenerationJob spend reservation is unavailable")
            await self._validate_freshness(session, plan)
            context, requested_agent = await self._execution_context(session, plan.agent_run_id)
            product_row = (
                await session.execute(select(products).where(products.c.id == plan.product_id))
            ).first()
            if product_row is None:
                raise ProductionNotFound("Production Product is unavailable")
            references = await self._references(session, job)
            if kind is MediaKind.IMAGE:
                specification["size"] = "1024x1536"
                specification["quality"] = await self._image_quality(
                    session, plan.id, dict(segment)
                )
            self._prepared[job.id] = _Prepared(tenant_id, context, requested_agent)
            return ExecutableGeneration(
                job,
                specification,
                references,
                segment["duration_seconds"],
                context,
                product_row._mapping["brand_id"],
                plan.product_id,
                requested_agent,
            )

    async def _enforce_live_spend_cap(
        self, session: AsyncSession, tenant_id: UUID, product_id: UUID
    ) -> None:
        if self._live_spend_cap is None:
            return
        if self._live_product_id is not None and product_id != self._live_product_id:
            raise ProductionPermissionDenied("GenerationJob is outside LIVE_E2E_PRODUCT_ID")
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"live-e2e-spend:{tenant_id}:{product_id}"},
        )
        model_amount = await session.scalar(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                agent_runs.c.status.in_(("SUCCEEDED", "FAILED")),
                                agent_runs.c.estimated_cost,
                            ),
                            else_=agent_runs.c.reserved_cost,
                        )
                    ),
                    0,
                )
            ).where(
                agent_runs.c.tenant_id == tenant_id,
                agent_runs.c.product_id == product_id,
                agent_runs.c.currency == "USD",
            )
        )
        media_amount = await session.scalar(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                generation_jobs.c.status == "SUCCEEDED",
                                generation_jobs.c.actual_cost,
                            ),
                            (
                                generation_jobs.c.status == "OUTCOME_UNKNOWN",
                                generation_jobs.c.unknown_cost,
                            ),
                            (generation_jobs.c.status == "FAILED", Decimal(0)),
                            else_=generation_jobs.c.reserved_cost,
                        )
                    ),
                    0,
                )
            )
            .select_from(
                generation_jobs.join(
                    production_plans,
                    generation_jobs.c.production_plan_id == production_plans.c.id,
                )
            )
            .where(
                generation_jobs.c.tenant_id == tenant_id,
                production_plans.c.product_id == product_id,
                generation_jobs.c.currency == "USD",
            )
        )
        committed = Decimal(model_amount or 0) + Decimal(media_amount or 0)
        if committed > self._live_spend_cap:
            raise ProductionPermissionDenied("LIVE_E2E_MAX_USD spend cap reached")

    @staticmethod
    async def _tenant(session: AsyncSession, tenant_id: UUID) -> None:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )

    @staticmethod
    def _validate_capability(
        route: MediaRoute, spec: dict[str, object], duration: int | None
    ) -> None:
        capabilities = route.capabilities
        # The production plan records the creative canvas (9:16), while image
        # providers advertise the discrete output sizes they can actually
        # render. The executor normalizes that canvas to 1024x1536 below.
        if (
            route.capabilities.media_kind is MediaKind.VIDEO
            and str(spec.get("aspect_ratio")) not in capabilities.aspect_ratios
        ):
            raise ProductionCapabilityChanged("media aspect ratio is unsupported")
        resolution = str(spec.get("resolution"))
        if route.capabilities.media_kind is MediaKind.VIDEO:
            if resolution not in capabilities.resolutions or duration is None:
                raise ProductionCapabilityChanged("video capability no longer matches")
            minimum = capabilities.minimum_duration_seconds
            maximum = capabilities.maximum_duration_seconds
            if minimum is None or maximum is None or not minimum <= duration <= maximum:
                raise ProductionCapabilityChanged("video duration capability no longer matches")
        elif str(spec.get("size", "1024x1536")) not in capabilities.resolutions:
            raise ProductionCapabilityChanged("image size capability no longer matches")

    @staticmethod
    async def _image_quality(
        session: AsyncSession, plan_id: UUID, segment: Mapping[str, object]
    ) -> str:
        raw_shot_keys = segment["shot_keys"]
        if not isinstance(raw_shot_keys, (list, tuple)):
            raise ProductionCapabilityChanged("image shot binding is malformed")
        shot_keys = {str(value) for value in raw_shot_keys}
        rows = (
            await session.execute(
                select(production_shots.c.shot_key, production_shots.c.specification).where(
                    production_shots.c.production_plan_id == plan_id
                )
            )
        ).all()
        for key, value in rows:
            if key in shot_keys and isinstance(value, dict):
                image = value.get("image_generation_spec")
                if isinstance(image, dict):
                    quality = str(image.get("quality_intent", "")).casefold()
                    if quality in {"medium", "high"}:
                        return quality
        raise ProductionCapabilityChanged("image quality binding is unavailable")

    async def _expected_reservation(
        self, session: AsyncSession, job: GenerationJob, spec: dict[str, object]
    ) -> Decimal:
        if job.kind is MediaKind.VIDEO:
            duration = await session.scalar(
                select(generation_segments.c.duration_seconds).where(
                    generation_segments.c.production_plan_id == job.production_plan_id,
                    generation_segments.c.segment_key == job.segment_key,
                )
            )
            return SeedancePricing().cost(
                output_duration_seconds=int(duration or 0), resolution=str(spec["resolution"])
            )
        quality = await self._image_quality(
            session,
            job.production_plan_id,
            {
                "shot_keys": (
                    await session.scalar(
                        select(generation_segments.c.shot_keys).where(
                            generation_segments.c.production_plan_id == job.production_plan_id,
                            generation_segments.c.segment_key == job.segment_key,
                        )
                    )
                    or []
                )
            },
        )
        return ImageReservationPricing().reserve(quality.upper())

    @staticmethod
    async def _validate_freshness(session: AsyncSession, plan: ProductionPlan) -> None:
        product_snapshot = (
            await session.execute(
                select(product_knowledge_snapshots.c.id, product_knowledge_snapshots.c.digest)
                .where(product_knowledge_snapshots.c.product_id == plan.product_id)
                .order_by(
                    product_knowledge_snapshots.c.created_at.desc(),
                    product_knowledge_snapshots.c.id.desc(),
                )
                .limit(1)
            )
        ).first()
        research = (
            await session.execute(
                select(research_snapshots.c.id, research_snapshots.c.semantic_digest)
                .where(research_snapshots.c.product_id == plan.product_id)
                .order_by(research_snapshots.c.created_at.desc(), research_snapshots.c.id.desc())
                .limit(1)
            )
        ).first()
        concept = (
            await session.execute(
                select(concepts.c.semantic_digest).where(concepts.c.id == plan.context.concept_id)
            )
        ).first()
        creative_decision = (
            await session.execute(
                select(concept_decisions.c.id, concept_decisions.c.state)
                .where(concept_decisions.c.concept_id == plan.context.concept_id)
                .order_by(concept_decisions.c.created_at.desc(), concept_decisions.c.id.desc())
                .limit(1)
            )
        ).first()
        if (
            product_snapshot is None
            or product_snapshot.id != plan.context.product_snapshot_id
            or product_snapshot.digest != plan.context.product_snapshot_digest
            or research is None
            or research.id != plan.context.research_snapshot_id
            or research.semantic_digest != plan.context.research_snapshot_digest
            or concept is None
            or concept.semantic_digest != plan.context.concept_digest
            or creative_decision is None
            or creative_decision.id != plan.context.creative_decision_id
            or creative_decision.state != "APPROVED_FOR_PRODUCTION"
        ):
            raise ProductionCreativeRefreshRequired("ProductionPlan input provenance is outdated")

    async def _execution_context(
        self, session: AsyncSession, run_id: UUID
    ) -> tuple[ExecutionContext, UUID]:
        row = (
            await session.execute(
                select(
                    agent_runs.c.initiated_by_actor_kind,
                    agent_runs.c.initiated_by_actor_id,
                    agent_runs.c.tenant_id,
                    agent_runs.c.requested_agent_definition_id,
                    memberships.c.role,
                    memberships.c.status,
                    users.c.status.label("user_status"),
                    tenants.c.status.label("tenant_status"),
                )
                .join(users, users.c.id == agent_runs.c.initiated_by_actor_id)
                .join(
                    memberships,
                    (memberships.c.user_id == users.c.id)
                    & (memberships.c.tenant_id == agent_runs.c.tenant_id),
                )
                .join(tenants, tenants.c.id == agent_runs.c.tenant_id)
                .where(agent_runs.c.id == run_id)
            )
        ).first()
        if (
            row is None
            or row.initiated_by_actor_kind != "user"
            or row.user_status != "active"
            or row.tenant_status != "active"
            or row.status != "active"
        ):
            raise ProductionPermissionDenied("initiating user authority is no longer active")
        context = ExecutionContext(
            tenant_id=row.tenant_id,
            actor=Actor(ActorKind.WORKLOAD, self._workload.actor_id),
            user_id=row.initiated_by_actor_id,
            membership_role=MembershipRole(row.role),
            membership_status=MembershipStatus(row.status),
            environment=self._workload.environment,
            authentication=AuthenticationAssurance(datetime.now(UTC), "workload", "deployment"),
        )
        return context, row.requested_agent_definition_id

    async def _references(
        self, session: AsyncSession, job: GenerationJob
    ) -> tuple[MaterializedReference, ...]:
        result: list[MaterializedReference] = []
        for reference in job.input_assets:
            row = (
                await session.execute(select(assets).where(assets.c.id == reference.asset_id))
            ).first()
            if row is None:
                raise ProductionRightsChanged("generation reference is unavailable")
            value = row._mapping
            uses = {str(item).casefold() for item in value["allowed_uses"]}
            if (
                value["status"] != "ready"
                or value["rights_status"] != "confirmed"
                or "generation_input" not in uses
                or value["digest"] != reference.digest
                or value["object_key"] is None
            ):
                raise ProductionRightsChanged("generation reference rights or digest changed")
            body = bytearray()
            async for chunk in self._store.stream(key=str(value["object_key"])):
                body.extend(chunk)
                if len(body) > self._maximum_reference_bytes:
                    raise ProductionRightsChanged(
                        "generation reference exceeds materialization bound"
                    )
            content = bytes(body)
            if "sha256:" + sha256(content).hexdigest() != reference.digest:
                raise ProductionRightsChanged(
                    "generation reference bytes failed digest verification"
                )
            detected_mime = str(value["detected_mime_type"])
            if detect_mime(content[:64]) != detected_mime:
                raise ProductionRightsChanged("generation reference bytes failed MIME verification")
            result.append(MaterializedReference(detected_mime, content, reference.role))
        return tuple(result)

    def _state(self, job_id: UUID) -> _Prepared:
        try:
            return self._prepared[job_id]
        except KeyError as error:
            raise ProductionPermissionDenied(
                "GenerationJob was not authoritatively prepared"
            ) from error

    async def provider_started(self, job_id: UUID, provider_operation_ref: str | None) -> None:
        await self._transition(
            job_id,
            GenerationJobStatus.PROCESSING
            if provider_operation_ref
            else GenerationJobStatus.STARTING,
            provider_operation_ref=provider_operation_ref,
        )

    async def processing(self, job_id: UUID) -> None:
        await self._transition(job_id, GenerationJobStatus.PROCESSING)

    async def importing(self, job_id: UUID) -> None:
        await self._transition(job_id, GenerationJobStatus.IMPORTING)

    async def failed(self, job_id: UUID, failure_code: str, actual_cost: Decimal) -> None:
        await self._transition(
            job_id, GenerationJobStatus.FAILED, actual_cost=actual_cost, failure_code=failure_code
        )

    async def outcome_unknown(self, job_id: UUID) -> None:
        await self._transition(job_id, GenerationJobStatus.OUTCOME_UNKNOWN)

    async def succeeded(self, job_id: UUID, asset_id: UUID, actual_cost: Decimal) -> None:
        await self._transition(
            job_id, GenerationJobStatus.SUCCEEDED, actual_cost=actual_cost, output_asset_id=asset_id
        )

    async def _transition(
        self,
        job_id: UUID,
        target: GenerationJobStatus,
        *,
        provider_operation_ref: str | None = None,
        actual_cost: Decimal | None = None,
        output_asset_id: UUID | None = None,
        failure_code: str | None = None,
    ) -> None:
        prepared = self._state(job_id)
        async with self._factory() as session, session.begin():
            await self._tenant(session, prepared.tenant_id)
            repository = SqlAlchemyProductionRepository(session)
            current = await repository.get_job(job_id)
            if current is None:
                raise ProductionNotFound("GenerationJob not found")
            if target is GenerationJobStatus.SUCCEEDED:
                output = (
                    None
                    if output_asset_id is None
                    else (
                        await session.execute(
                            select(
                                assets.c.tenant_id,
                                assets.c.product_id,
                                assets.c.status,
                                assets.c.origin,
                            ).where(assets.c.id == output_asset_id)
                        )
                    ).first()
                )
                plan_record = await repository.get_plan(current.production_plan_id)
                if (
                    output is None
                    or plan_record is None
                    or output.tenant_id != prepared.tenant_id
                    or output.product_id != plan_record.plan.product_id
                    or output.status != "ready"
                    or output.origin != "generated"
                ):
                    raise ProductionPermissionDenied(
                        "GenerationJob output Asset is not authoritative"
                    )
            changed = current.transition(
                target,
                provider_operation_ref=provider_operation_ref,
                actual_cost=actual_cost,
                output_asset_id=output_asset_id,
                failure_code=failure_code,
                initiated_by_user_id=prepared.context.user_id,
                executed_by_workload_id=self._workload.workload_id,
            )
            result = await session.execute(
                update(generation_jobs)
                .where(
                    generation_jobs.c.id == job_id, generation_jobs.c.status == current.status.value
                )
                .values(
                    status=changed.status.value,
                    provider_operation_ref=changed.provider_operation_ref,
                    actual_cost=changed.actual_cost,
                    unknown_cost=changed.unknown_cost,
                    output_asset_id=changed.output_asset_id,
                    failure_code=changed.failure_code,
                    initiated_by_user_id=changed.initiated_by_user_id,
                    executed_by_workload_id=changed.executed_by_workload_id,
                    updated_at=changed.updated_at,
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise ProductionPermissionDenied("stale GenerationJob transition rejected")
            if target in {
                GenerationJobStatus.SUCCEEDED,
                GenerationJobStatus.FAILED,
                GenerationJobStatus.OUTCOME_UNKNOWN,
            }:
                kind = {
                    GenerationJobStatus.SUCCEEDED: "ACTUAL",
                    GenerationJobStatus.FAILED: "RELEASED",
                    GenerationJobStatus.OUTCOME_UNKNOWN: "UNKNOWN",
                }[target]
                amount = (
                    changed.actual_cost
                    if target is GenerationJobStatus.SUCCEEDED
                    else changed.unknown_cost
                    if target is GenerationJobStatus.OUTCOME_UNKNOWN
                    else changed.reserved_cost
                )
                await session.execute(
                    insert(media_budget_usage).values(
                        id=uuid4(),
                        tenant_id=prepared.tenant_id,
                        generation_job_id=job_id,
                        entry_kind=kind,
                        amount=amount,
                        currency=changed.currency,
                        created_at=datetime.now(UTC),
                    )
                )
            audit = PostgresAuditWriter(session)
            await audit.append(
                tenant_audit(
                    prepared.context,
                    action=f"production.generation.{target.value.casefold()}",
                    outcome=AuditOutcome.SUCCESS
                    if target is GenerationJobStatus.SUCCEEDED
                    else AuditOutcome.FAILED
                    if target in {GenerationJobStatus.FAILED, GenerationJobStatus.OUTCOME_UNKNOWN}
                    else AuditOutcome.SUCCESS,
                    reason_code=failure_code,
                    resource_type="generation_job",
                    resource_id=str(job_id),
                    agent_definition_id=prepared.requested_agent_definition_id,
                    metadata=safe_metadata(
                        {"status": target.value, "executed_by_workload": self._workload.workload_id}
                    ),
                )
            )
            if target is GenerationJobStatus.SUCCEEDED and output_asset_id is not None:
                await PostgresOutboxWriter(session).append(
                    tenant_event(
                        prepared.context,
                        event_type="production.generation.completed.v1",
                        schema_version=1,
                        aggregate_type="generation_job",
                        aggregate_id=job_id,
                        payload={
                            "production_plan_id": str(changed.production_plan_id),
                            "generation_job_id": str(job_id),
                            "kind": changed.kind.value,
                            "output_asset_id": str(output_asset_id),
                            "actual_cost": str(changed.actual_cost),
                            "currency": changed.currency,
                        },
                        payload_schema_digest=EventContractRegistry().schema_digest(
                            "production.generation.completed.v1"
                        ),
                        occurred_at=changed.updated_at,
                    )
                )
