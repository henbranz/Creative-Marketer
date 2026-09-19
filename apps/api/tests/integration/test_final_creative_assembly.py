# mypy: disable-error-code="no-untyped-def,no-untyped-call"

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError

from creative_marketer.agent_runtime.application import (
    AgentRunService,
    ModelProviderRegistry,
    ModelRouter,
    initial_creative_strategist_route,
    initial_intelligence_route,
)
from creative_marketer.agent_runtime.domain import (
    AgentRunNotReady,
    ModelInvocationResult,
    ModelUsage,
)
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
from creative_marketer.creative.application import CreativeService
from creative_marketer.creative.domain import ChannelIntent, CreativeStrategyRequest
from creative_marketer.identity.domain import MembershipRole
from creative_marketer.infrastructure.database.agent_runtime_uow import (
    SqlAlchemyAgentRuntimeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.assembly_uow import (
    SqlAlchemyAssemblyUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.catalog_uow import (
    SqlAlchemyCatalogUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.creative_uow import (
    SqlAlchemyCreativeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.intelligence_uow import (
    SqlAlchemyIntelligenceUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.knowledge_projection import (
    SqlAlchemyCanonicalKnowledgeReader,
    SqlAlchemyKnowledgeProjectionStore,
)
from creative_marketer.infrastructure.database.measurement_schema import (
    collection_runs,
    performance_observations,
    performance_snapshots,
)
from creative_marketer.infrastructure.database.measurement_uow import (
    SqlAlchemyMeasurementUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.production_authority import (
    MediaWorkloadIdentity,
    SqlAlchemyGenerationAuthority,
)
from creative_marketer.infrastructure.database.production_uow import (
    SqlAlchemyProductionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.publishing_schema import (
    publication_drafts,
)
from creative_marketer.infrastructure.database.publishing_schema import (
    publications as publication_rows,
)
from creative_marketer.infrastructure.database.publishing_uow import (
    SqlAlchemyPublishingUnitOfWorkFactory,
)
from creative_marketer.infrastructure.model_providers.fake import FakeModelProvider
from creative_marketer.infrastructure.object_storage.s3 import S3ObjectStore
from creative_marketer.intelligence.application import IntelligenceService
from creative_marketer.intelligence.domain import (
    DataTrustLevel,
    ExperimentDecisionKind,
    ExperimentHandoffDenied,
    InsightDecisionKind,
    IntelligenceNotFound,
    IntelligencePermissionDenied,
)
from creative_marketer.knowledge.application import KnowledgeGraphProjector
from creative_marketer.knowledge.domain import KnowledgeNodeType
from creative_marketer.measurement.application import MeasurementService
from creative_marketer.measurement.domain import MeasurementNotFound
from creative_marketer.measurement.provider import FakeSocialMetricsProvider
from creative_marketer.production.application import initial_media_router
from creative_marketer.production.domain import MediaKind, ProductionPlanDecisionState
from creative_marketer.production.infrastructure.fakes import DEMO_MP4
from creative_marketer.production.service import ProductionService
from creative_marketer.publishing.application import PublishingService
from creative_marketer.publishing.domain import (
    PublicationDecisionState,
    PublicationMode,
    PublicationStatus,
    SocialPlatform,
)
from creative_marketer.publishing.provider import FakeSocialProvider
from creative_marketer_api.config import Settings
from creative_marketer_api.main import create_app
from scripts import bootstrap_demo
from tests.integration.test_asset_library import (
    OBJECT_STORAGE_ACCESS_KEY_ID,
    OBJECT_STORAGE_SECRET_ACCESS_KEY,
)
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


async def _seed_matched_synthetic_history(
    admin_engine,
    *,
    tenant_id,
    product_id,
    source_draft_id,
    source_publication_id,
) -> None:
    """Add three older canonical +6h fixtures for the deterministic median baseline."""

    async with admin_engine.begin() as connection:
        source_draft = dict(
            (
                await connection.execute(
                    select(publication_drafts).where(publication_drafts.c.id == source_draft_id)
                )
            )
            .mappings()
            .one()
        )
        source_publication = dict(
            (
                await connection.execute(
                    select(publication_rows).where(publication_rows.c.id == source_publication_id)
                )
            )
            .mappings()
            .one()
        )
        for index, impressions in enumerate((120, 160, 200), start=1):
            created_at = datetime.now(UTC) - timedelta(days=8 - index)
            draft_id, publication_id, observation_id = uuid4(), uuid4(), uuid4()
            draft = {
                **source_draft,
                "id": draft_id,
                "semantic_digest": "sha256:"
                + f"history-draft-{index}".encode().hex().ljust(64, "0")[:64],
                "created_at": created_at,
            }
            publication = {
                **source_publication,
                "id": publication_id,
                "publication_draft_id": draft_id,
                "external_post_id": f"fake-history-{index}",
                "external_operation_id": f"fake-history-operation-{index}",
                "request_digest": "sha256:"
                + f"history-request-{index}".encode().hex().ljust(64, "0")[:64],
                "response_metadata_digest": "sha256:"
                + f"history-response-{index}".encode().hex().ljust(64, "0")[:64],
                "semantic_digest": "sha256:"
                + f"history-publication-{index}".encode().hex().ljust(64, "0")[:64],
                "submitted_at": created_at,
                "published_at": created_at,
                "created_at": created_at,
            }
            await connection.execute(insert(publication_drafts).values(**draft))
            await connection.execute(insert(publication_rows).values(**publication))
            await connection.execute(
                insert(performance_observations).values(
                    id=observation_id,
                    tenant_id=tenant_id,
                    product_id=product_id,
                    publication_id=publication_id,
                    metric_key="impressions",
                    semantics="CUMULATIVE",
                    value=Decimal(impressions),
                    unit="COUNT",
                    observed_at=created_at + timedelta(hours=6),
                    provider="fake",
                    provider_version="fixture-v1",
                    source_digest="sha256:"
                    + f"history-source-{index}".encode().hex().ljust(64, "0")[:64],
                    created_at=created_at + timedelta(hours=6),
                )
            )
            completed_at = created_at + timedelta(hours=6)
            await connection.execute(
                insert(collection_runs).values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    publication_id=publication_id,
                    status="SUCCEEDED",
                    checkpoint="schedule-v1:1",
                    cursor=None,
                    failure_code=None,
                    created_at=completed_at,
                    completed_at=completed_at,
                )
            )
            await connection.execute(
                insert(performance_snapshots).values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    product_id=product_id,
                    publication_id=publication_id,
                    observation_ids=[observation_id],
                    latest_metrics={"impressions": impressions},
                    derived_metrics=[],
                    attributed_conversions=0,
                    attributed_revenue={},
                    freshness="CURRENT",
                    semantic_digest="sha256:"
                    + f"history-snapshot-{index}".encode().hex().ljust(64, "0")[:64],
                    created_at=completed_at,
                )
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
        "OBJECT_STORAGE_ACCESS_KEY_ID": OBJECT_STORAGE_ACCESS_KEY_ID,
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": OBJECT_STORAGE_SECRET_ACCESS_KEY,
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
        access_key_id=OBJECT_STORAGE_ACCESS_KEY_ID,
        secret_access_key=OBJECT_STORAGE_SECRET_ACCESS_KEY,
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

    publishing = PublishingService(
        SqlAlchemyPublishingUnitOfWorkFactory(sessions), FakeSocialProvider()
    )
    social_account = await publishing.create_account(
        context,
        platform=SocialPlatform.INSTAGRAM,
        display_name="Fake Instagram acceptance",
        external_account_id="fake-instagram-acceptance",
        username="@fake-acceptance",
    )
    assert social_account in await publishing.list_accounts(context)
    publication_draft = await publishing.create_draft(
        context,
        product_id=bootstrap_demo.PRODUCT_ID,
        final_creative_id=final.id,
        social_account_id=social_account.id,
        caption="Exact approved fake acceptance caption",
        hashtags=("phase5", "fake"),
        mode=PublicationMode.POST_NOW,
    )
    assert (
        await publishing.get_draft(context, publication_draft.draft.id)
    ).draft.semantic_digest == publication_draft.draft.semantic_digest
    assert len(await publishing.list_drafts(context, bootstrap_demo.PRODUCT_ID)) == 1
    await publishing.decide(context, publication_draft.draft.id, PublicationDecisionState.APPROVED)
    published_record = await publishing.execute(context, publication_draft.draft.id)
    assert published_record.job and published_record.job.status is PublicationStatus.PUBLISHED
    publications = await publishing.list_publications(context, bootstrap_demo.PRODUCT_ID)
    assert len(publications) == 1
    assert (await publishing.get_publication(context, publications[0].id)) == publications[0]
    assert publications[0].canonical_permalink and publications[0].canonical_permalink.startswith(
        "https://social.invalid/"
    )
    assert await publishing.list_drafts(other, bootstrap_demo.PRODUCT_ID) == ()
    assert await publishing.list_publications(other, bootstrap_demo.PRODUCT_ID) == ()

    measurement = MeasurementService(
        SqlAlchemyMeasurementUnitOfWorkFactory(sessions), FakeSocialMetricsProvider()
    )
    first_snapshot = await measurement.collect(
        context, publications[0].id, checkpoint="schedule-v1:0"
    )
    assert first_snapshot.latest_metrics["impressions"] == 100
    second_snapshot = await measurement.collect(
        context, publications[0].id, checkpoint="schedule-v1:1"
    )
    assert second_snapshot.latest_metrics["impressions"] == 250
    assert len(second_snapshot.observation_ids) > len(first_snapshot.observation_ids)
    await _seed_matched_synthetic_history(
        admin_engine,
        tenant_id=context.tenant_id,
        product_id=bootstrap_demo.PRODUCT_ID,
        source_draft_id=publication_draft.draft.id,
        source_publication_id=publications[0].id,
    )
    reference = await measurement.issue_reference(
        context, publications[0].id, "https://example.invalid/product"
    )
    conversion, attribution = await measurement.ingest_fake_conversion(
        context,
        external_id="integration-order-1",
        amount=Decimal("49.95"),
        currency="USD",
        observed_at=publications[0].published_at or publications[0].created_at,
        public_code=reference.public_code,
    )
    assert conversion.amount == Decimal("49.95")
    assert attribution and attribution.publication_id == publications[0].id
    attributed_snapshot = await measurement.get_publication(context, publications[0].id)
    assert attributed_snapshot.attributed_revenue == {"USD": Decimal("49.95")}
    with pytest.raises(MeasurementNotFound):
        await measurement.get_publication(other, publications[0].id)

    def intelligence_model(invocation):
        value = (
            bootstrap_demo._intelligence_output(invocation)
            if invocation.output_contract_key == "intelligence.intelligence_report"
            else bootstrap_demo._creative_output(invocation)
        )
        return ModelInvocationResult(
            value,
            f"phase6-{invocation.output_contract_key}",
            ModelUsage(100, 100, 200),
            "openai",
            "gpt-5.6-sol",
        )

    runtime = AgentRunService(
        SqlAlchemyAgentRuntimeUnitOfWorkFactory(sessions),
        ModelRouter((initial_intelligence_route(), initial_creative_strategist_route())),
        ModelProviderRegistry({"openai": FakeModelProvider(intelligence_model)}),
        bootstrap_demo.DemoWorkload(),
    )
    intelligence_run = await runtime.request_intelligence(
        context,
        product_id=bootstrap_demo.PRODUCT_ID,
        idempotency_key="phase6-intelligence-integration",
    )
    completed_intelligence = await runtime.execute(context.tenant_id, intelligence_run.id)
    assert completed_intelligence.status.value == "SUCCEEDED"
    intelligence = IntelligenceService(SqlAlchemyIntelligenceUnitOfWorkFactory(sessions))
    reports = await intelligence.list_reports(context, bootstrap_demo.PRODUCT_ID)
    assert len(reports) == 1
    report, candidates, proposals = await intelligence.get_report(context, reports[0].id)
    assert report.data_trust_level is DataTrustLevel.SYNTHETIC
    assert len(candidates) == len(proposals) == 1
    assert candidates[0].confidence.value == "LOW"
    assert proposals[0].source_intelligence_report_id == report.id
    assert await intelligence.list_reports(other, bootstrap_demo.PRODUCT_ID) == ()
    with pytest.raises(IntelligenceNotFound):
        await intelligence.get_report(context, uuid4())
    with pytest.raises(IntelligenceNotFound):
        await intelligence.decide_insight(context, uuid4(), InsightDecisionKind.REJECT)
    with pytest.raises(IntelligenceNotFound):
        await intelligence.decide_experiment(context, uuid4(), ExperimentDecisionKind.REJECTED)
    with pytest.raises(IntelligencePermissionDenied):
        await intelligence.decide_insight(
            replace(context, membership_role=MembershipRole.MEMBER),
            candidates[0].id,
            InsightDecisionKind.REJECT,
        )
    rejected_insight = await intelligence.decide_insight(
        context, candidates[0].id, InsightDecisionKind.REJECT
    )
    rejected_experiment = await intelligence.decide_experiment(
        context, proposals[0].id, ExperimentDecisionKind.REJECTED
    )
    assert rejected_insight.decision is InsightDecisionKind.REJECT
    assert rejected_experiment.decision is ExperimentDecisionKind.REJECTED
    with pytest.raises(ExperimentHandoffDenied):
        await intelligence.approved_proposal(context, proposals[0].id)
    with pytest.raises(AgentRunNotReady):
        await runtime.request_creative_strategist(
            context,
            product_id=bootstrap_demo.PRODUCT_ID,
            request=CreativeStrategyRequest(3, ChannelIntent.ORGANIC_SHORT_FORM),
            idempotency_key="phase6-rejected-experiment",
            approved_experiment_proposal_id=proposals[0].id,
        )

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
            object_storage_access_key_id=OBJECT_STORAGE_ACCESS_KEY_ID,
            object_storage_secret_access_key=OBJECT_STORAGE_SECRET_ACCESS_KEY,
            model_provider_backend="fake",
        )
    )
    auth = {
        "Authorization": "Bearer local-demo|owner",
        "X-Tenant-ID": str(context.tenant_id),
    }
    creative_run_id = None
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
        performance = await http.get(
            f"/v1/publications/{publications[0].id}/performance", headers=auth
        )
        assert performance.status_code == 200
        assert Decimal(performance.json()["attributed_revenue"]["USD"]) == Decimal("49.95")
        assert (
            await http.get(
                f"/v1/publications/{publications[0].id}/performance/history", headers=auth
            )
        ).status_code == 200
        assert (
            await http.get(f"/v1/products/{bootstrap_demo.PRODUCT_ID}/performance", headers=auth)
        ).status_code == 200
        assert (
            await http.post(
                f"/v1/publications/{publications[0].id}/performance/collect", headers=auth
            )
        ).status_code == 200
        reference_response = await http.post(
            f"/v1/publications/{publications[0].id}/attribution-references",
            headers=auth,
            json={"destination_url": "https://example.invalid/api-product"},
        )
        assert reference_response.status_code == 201
        conversion_response = await http.post(
            "/v1/measurement/dev/fake-conversions",
            headers=auth,
            json={
                "external_id": "integration-api-order-2",
                "amount": "12.50",
                "currency": "EUR",
                "observed_at": (
                    publications[0].published_at or publications[0].created_at
                ).isoformat(),
                "attribution_code": reference_response.json()["public_code"],
            },
        )
        assert conversion_response.status_code == 200
        assert conversion_response.json()["method"] == "DIRECT_REFERENCE"
        assert (
            await http.get(f"/v1/publications/{uuid4()}/performance", headers=auth)
        ).status_code == 404
        assert (
            await http.get(f"/v1/products/{bootstrap_demo.PRODUCT_ID}/performance")
        ).status_code == 401
        listed_reports = await http.get(
            f"/v1/products/{bootstrap_demo.PRODUCT_ID}/intelligence/reports", headers=auth
        )
        assert listed_reports.status_code == 200
        assert listed_reports.json()[0]["data_trust_level"] == "SYNTHETIC"
        analysis_request = await http.post(
            f"/v1/products/{bootstrap_demo.PRODUCT_ID}/intelligence/analyze",
            headers=auth,
            json={"idempotency_key": "phase6-api-analysis-rerun"},
        )
        assert analysis_request.status_code == 202
        assert (
            await http.get(f"/v1/intelligence/reports/{report.id}", headers=auth)
        ).status_code == 200
        unknown_id = uuid4()
        assert (await http.get(f"/v1/intelligence/reports/{unknown_id}")).status_code == 401
        assert (
            await http.get(f"/v1/intelligence/reports/{unknown_id}", headers=auth)
        ).status_code == 404
        assert (
            await http.post(
                f"/v1/intelligence/insights/{unknown_id}/decision",
                headers=auth,
                json={"decision": InsightDecisionKind.REJECT.value},
            )
        ).status_code == 409
        assert (
            await http.post(
                f"/v1/intelligence/experiments/{unknown_id}/decision",
                headers=auth,
                json={"decision": ExperimentDecisionKind.REJECTED.value},
            )
        ).status_code == 409
        assert (
            await http.post(
                f"/v1/intelligence/experiments/{unknown_id}/generate-concepts",
                headers=auth,
                json={"idempotency_key": "phase6-unknown-proposal", "concept_count": 3},
            )
        ).status_code == 409
        assert (
            await http.post(
                f"/v1/intelligence/insights/{candidates[0].id}/decision",
                headers=auth,
                json={"decision": InsightDecisionKind.PROPOSE_FOR_TESTING.value},
            )
        ).status_code == 201
        assert (
            await http.post(
                f"/v1/intelligence/experiments/{proposals[0].id}/decision",
                headers=auth,
                json={"decision": ExperimentDecisionKind.APPROVED_FOR_CREATIVE.value},
            )
        ).status_code == 201
        assert (await intelligence.approved_proposal(context, proposals[0].id)).id == proposals[
            0
        ].id
        with pytest.raises(ExperimentHandoffDenied):
            await intelligence.approved_proposal(
                replace(context, environment="production"), proposals[0].id
            )
        generated = await http.post(
            f"/v1/intelligence/experiments/{proposals[0].id}/generate-concepts",
            headers=auth,
            json={
                "idempotency_key": "phase6-approved-experiment-creative",
                "concept_count": 3,
                "channel_intent": "ORGANIC_SHORT_FORM",
            },
        )
        assert generated.status_code == 202, generated.text
        creative_run_id = generated.json()["id"]
        assert (await http.post(f"/v1/final-creatives/{final.id}/reject", headers=auth)).json()[
            "decision_state"
        ] == "REJECTED"
        assert (await http.get(f"/v1/assembly/plans/{uuid4()}", headers=auth)).status_code == 404
        assert (await http.get(f"/v1/assembly/jobs/{record.job.id}")).status_code == 401

    assert creative_run_id is not None
    completed_creative = await runtime.execute(
        context.tenant_id, __import__("uuid").UUID(creative_run_id)
    )
    assert completed_creative.status.value == "SUCCEEDED", completed_creative.failure_code
    concept_sets = await CreativeService(SqlAlchemyCreativeUnitOfWorkFactory(sessions)).list_sets(
        context, bootstrap_demo.PRODUCT_ID
    )
    generated_set = next(
        item for item in concept_sets if item.agent_run_id == completed_creative.id
    )
    assert generated_set.experiment_proposal_id == proposals[0].id
    assert generated_set.experiment_proposal_digest == proposals[0].semantic_digest
    learned_graph, _ = await KnowledgeGraphProjector(
        SqlAlchemyCanonicalKnowledgeReader(sessions),
        SqlAlchemyKnowledgeProjectionStore(sessions),
    ).full(context)
    learned_types = {node.node_type for node in learned_graph.nodes}
    assert {
        KnowledgeNodeType.INTELLIGENCE_REPORT,
        KnowledgeNodeType.INSIGHT_CANDIDATE,
        KnowledgeNodeType.EXPERIMENT_PROPOSAL,
        KnowledgeNodeType.CREATIVE_CONCEPT_SET,
    } <= learned_types

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
