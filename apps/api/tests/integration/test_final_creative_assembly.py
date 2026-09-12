# mypy: disable-error-code="no-untyped-def,no-untyped-call"

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from creative_marketer.assembly.application import AssemblyService
from creative_marketer.assembly.domain import (
    AssemblyJobStatus,
    AssemblyNotFound,
    FinalCreativeDecisionState,
)
from creative_marketer.assembly.execution import AssemblyJobExecutor, AssemblyWorkloadIdentity
from creative_marketer.assembly.rendering import MaterializedSource, RenderResult
from creative_marketer.catalog.asset_application import AssetService
from creative_marketer.catalog.asset_domain import AssetKind, AssetRole, AssetStatus
from creative_marketer.infrastructure.database.assembly_uow import (
    SqlAlchemyAssemblyUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.catalog_uow import (
    SqlAlchemyCatalogUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.knowledge_projection import (
    SqlAlchemyCanonicalKnowledgeReader,
    SqlAlchemyKnowledgeProjectionStore,
)
from creative_marketer.infrastructure.database.production_authority import (
    MediaWorkloadIdentity,
    SqlAlchemyGenerationAuthority,
)
from creative_marketer.infrastructure.database.production_uow import (
    SqlAlchemyProductionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.object_storage.s3 import S3ObjectStore
from creative_marketer.knowledge.application import KnowledgeGraphProjector
from creative_marketer.knowledge.domain import KnowledgeNodeType
from creative_marketer.production.application import initial_media_router
from creative_marketer.production.domain import MediaKind, ProductionPlanDecisionState
from creative_marketer.production.infrastructure.fakes import DEMO_MP4
from creative_marketer.production.service import ProductionService
from creative_marketer_api.config import Settings
from creative_marketer_api.main import create_app
from scripts import bootstrap_demo
from tests.integration.test_catalog import owner_context, seed_catalog_identity


@dataclass(slots=True)
class FixtureRenderer:
    calls: int = 0

    async def source_has_audio(self, path: str, workspace: str) -> bool:
        assert Path(path).is_relative_to(Path(workspace))
        return False

    async def render(
        self,
        plan,
        sources: tuple[MaterializedSource, ...],
        workspace: str,
    ) -> RenderResult:
        self.calls += 1
        assert len(sources) == len(plan.items) == 3
        assert all(Path(source.path).is_file() for source in sources)
        output = Path(workspace) / "final.mp4"
        output.write_bytes(DEMO_MP4)
        digest = "sha256:" + sha256(DEMO_MP4).hexdigest()
        return RenderResult(
            str(output),
            "ffmpeg",
            "fixture-7.1",
            plan.timeline_duration_ms,
            1080,
            1920,
            24,
            False,
            len(DEMO_MP4),
            digest,
        )


@pytest.mark.postgres
@pytest.mark.object_storage
@pytest.mark.asyncio
async def test_final_assembly_vertical_is_private_idempotent_and_cross_tenant_safe(
    monkeypatch,
    admin_engine,
    runtime_engine,
    admin_database_url: str,
    runtime_database_url: str,
) -> None:
    endpoint = __import__("os").environ["TEST_OBJECT_STORAGE_URL"]
    for key, value in {
        "APP_ENV": "development",
        "DATABASE_URL": admin_database_url,
        "OBJECT_STORAGE_BACKEND": "s3",
        "OBJECT_STORAGE_ENDPOINT_URL": endpoint,
        "OBJECT_STORAGE_PUBLIC_ENDPOINT_URL": endpoint,
        "OBJECT_STORAGE_ACCESS_KEY_ID": "creative-marketer-test",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": "creative-marketer-test-secret",
        "CORS_ORIGINS": '["http://localhost:3000"]',
        "MODEL_PROVIDER_BACKEND": "disabled",
        "MEDIA_IMAGE_PROVIDER": "fake",
        "MEDIA_VIDEO_PROVIDER": "fake",
    }.items():
        monkeypatch.setenv(key, value)
    await bootstrap_demo.run()

    sessions = create_session_factory(runtime_database_url)
    context = bootstrap_demo._context()
    store = S3ObjectStore(
        endpoint_url=endpoint,
        public_endpoint_url=endpoint,
        region="us-east-1",
        bucket="creative-marketer-assets",
        access_key_id="creative-marketer-test",
        secret_access_key="creative-marketer-test-secret",
    )
    production_factory = SqlAlchemyProductionUnitOfWorkFactory(sessions)
    async with production_factory(context.tenant_id) as uow:
        plans = await uow.production.list_plans(bootstrap_demo.PRODUCT_ID)
    production = ProductionService(production_factory, initial_media_router())
    await production.decide(
        context, plans[0].plan.id, ProductionPlanDecisionState.APPROVED_FOR_GENERATION
    )
    jobs = await production.list_jobs(context, plans[0].plan.id)
    assets = AssetService(SqlAlchemyCatalogUnitOfWorkFactory(sessions), store)
    ready_assets = await assets.list(
        context, product_id=bootstrap_demo.PRODUCT_ID, status=AssetStatus.READY
    )
    image = next(asset for asset in ready_assets if asset.kind is AssetKind.IMAGE)
    video = next(asset for asset in ready_assets if asset.kind is AssetKind.VIDEO)
    authority = SqlAlchemyGenerationAuthority(
        sessions,
        store,
        MediaWorkloadIdentity(uuid4(), "assembly-test-media", "test"),
    )
    for job in jobs:
        await authority.prepare(context.tenant_id, job.id, job.kind)
        await authority.provider_started(job.id, None)
        if job.kind == MediaKind.VIDEO:
            await authority.provider_started(job.id, "test-ref")
        await authority.importing(job.id)
        await authority.succeeded(
            job.id,
            image.id if job.kind == MediaKind.IMAGE else video.id,
            Decimal(0),
        )

    assembly_factory = SqlAlchemyAssemblyUnitOfWorkFactory(sessions)
    assembly = AssemblyService(assembly_factory)
    assert (await assembly.readiness(context, plans[0].plan.id)).ready
    record = await assembly.create_plan(context, plans[0].plan.id)
    assert len(record.plan.items) == 3
    assert len({shot for item in record.plan.items for shot in item.production_shot_ids}) == 3
    assert await assembly.create_plan(context, plans[0].plan.id) == record

    other_tenant, other_user = await seed_catalog_identity(admin_engine)
    other = owner_context(other_tenant, other_user)
    assert await assembly.list_plans(other, plans[0].plan.id) == ()
    with pytest.raises(AssemblyNotFound):
        await assembly.get_job(other, record.job.id)

    renderer = FixtureRenderer()
    executor = AssemblyJobExecutor(
        sessions,
        store,
        assets,
        renderer,
        AssemblyWorkloadIdentity(uuid4(), "assembly-test-worker", "test"),
    )
    final = await executor.execute(context.tenant_id, record.job.id)
    assert renderer.calls == 1
    assert (await executor.execute(context.tenant_id, record.job.id)).id == final.id
    assert renderer.calls == 1
    finished = await assembly.get_job(context, record.job.id)
    assert finished.status is AssemblyJobStatus.SUCCEEDED
    assert finished.final_creative_id == final.id
    stored = await assets.get(context, final.output_asset_id)
    assert stored.role is AssetRole.FINAL_CREATIVE and stored.status is AssetStatus.READY
    assert (await store.head(key=stored.object_key or "")).byte_size == len(DEMO_MP4)

    approved = await assembly.decide_final(
        context, final.id, FinalCreativeDecisionState.APPROVED_FOR_PUBLISHING
    )
    assert approved.decision is not None
    graph, _ = await KnowledgeGraphProjector(
        SqlAlchemyCanonicalKnowledgeReader(sessions),
        SqlAlchemyKnowledgeProjectionStore(sessions),
    ).full(context)
    node_types = {node.node_type for node in graph.nodes}
    assert {
        KnowledgeNodeType.ASSEMBLY_PLAN,
        KnowledgeNodeType.ASSEMBLY_JOB,
        KnowledgeNodeType.FINAL_CREATIVE,
        KnowledgeNodeType.FINAL_CREATIVE_DECISION,
    } <= node_types

    app = create_app(
        Settings(
            app_env="test",
            database_url=runtime_database_url,
            dev_identity_enabled=True,
            audit_fingerprint_key="assembly-test-fingerprint-key-32-bytes",
            cors_origins=["http://localhost:3000"],
            object_storage_backend="s3",
            object_storage_endpoint_url=endpoint,
            object_storage_public_endpoint_url=endpoint,
            object_storage_access_key_id="creative-marketer-test",
            object_storage_secret_access_key="creative-marketer-test-secret",
        )
    )
    auth = {
        "Authorization": "Bearer local-demo|owner",
        "X-Tenant-ID": str(context.tenant_id),
    }
    manual_shot_id = next(
        shot_id
        for item in record.plan.items
        if item.source_kind.value == "MANUAL_VIDEO"
        for shot_id in item.production_shot_ids
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api") as http:
        assert (
            await http.get(
                f"/v1/production/plans/{record.plan.production_plan_id}/assembly-readiness",
                headers=auth,
            )
        ).status_code == 200
        assert (
            await http.put(
                f"/v1/production/shots/{manual_shot_id}/manual-source",
                headers=auth,
                json={"asset_id": str(video.id)},
            )
        ).status_code == 200
        assert (
            await http.post(
                f"/v1/production/plans/{record.plan.production_plan_id}/assembly-plans",
                headers=auth,
            )
        ).status_code == 202
        listed = await http.get(
            f"/v1/production/plans/{record.plan.production_plan_id}/assembly-plans",
            headers=auth,
        )
        assert len(listed.json()) == 1
        assert (
            await http.get(f"/v1/assembly/plans/{record.plan.id}", headers=auth)
        ).status_code == 200
        assert (
            await http.get(f"/v1/assembly/jobs/{record.job.id}", headers=auth)
        ).status_code == 200
        assert (await http.get(f"/v1/final-creatives/{final.id}", headers=auth)).status_code == 200
        assert (await http.post(f"/v1/final-creatives/{final.id}/reject", headers=auth)).json()[
            "decision_state"
        ] == "REJECTED"
        assert (await http.get(f"/v1/assembly/plans/{uuid4()}", headers=auth)).status_code == 404
        assert (await http.get(f"/v1/assembly/jobs/{record.job.id}")).status_code == 401

    for statement in (
        "DELETE FROM assembly.final_creatives WHERE id=:id",
        "UPDATE assembly.assembly_jobs SET status='READY' WHERE id=:id",
    ):
        with pytest.raises(DBAPIError):
            async with runtime_engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
                    {"tenant": str(context.tenant_id)},
                )
                await connection.execute(
                    text(statement),
                    {"id": final.id if "final_creatives" in statement else record.job.id},
                )
