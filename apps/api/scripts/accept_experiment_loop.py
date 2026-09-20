"""Seed the isolated, synthetic Phase 8.1.5 experiment acceptance fixture."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from creative_marketer.agent_runtime.application import (
    AgentRunService,
    ModelProviderRegistry,
    ModelRouter,
    initial_intelligence_route,
)
from creative_marketer.agent_runtime.domain import (
    ModelInvocation,
    ModelInvocationResult,
    ModelUsage,
)
from creative_marketer.infrastructure.database.agent_runtime_uow import (
    SqlAlchemyAgentRuntimeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.assembly_schema import (
    assembly_plans,
    final_creatives,
)
from creative_marketer.infrastructure.database.catalog_schema import (
    assets,
    product_knowledge_snapshots,
)
from creative_marketer.infrastructure.database.creative_schema import concepts
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.intelligence_uow import (
    SqlAlchemyIntelligenceUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.measurement_schema import (
    collection_runs,
    performance_observations,
    performance_snapshots,
)
from creative_marketer.infrastructure.database.orchestration_uow import (
    SqlAlchemyOrchestrationUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.production_schema import production_plans
from creative_marketer.infrastructure.database.publishing_schema import (
    publication_drafts,
    publications,
    social_accounts,
)
from creative_marketer.infrastructure.model_providers.fake import FakeModelProvider
from creative_marketer.intelligence.application import IntelligenceService
from creative_marketer.orchestration.domain import (
    CreativeCycle,
    CycleArtifacts,
    CycleStage,
    CycleStatus,
    CycleTransition,
)
from creative_marketer_api.config import Settings
from scripts import bootstrap_demo


def _id(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"creative-marketer:phase815:{label}")


def _digest(label: str) -> str:
    return "sha256:" + label.encode().hex().ljust(64, "0")[:64]


async def run() -> None:
    settings = Settings()
    database_name = make_url(str(settings.database_url)).database or ""
    if not database_name.startswith("creative_marketer_phase815_"):
        raise RuntimeError("Phase 8.1.5 fixture requires an isolated acceptance database")
    if settings.allow_billable_media:
        raise RuntimeError("billable media must remain disabled")

    engine = create_async_engine(str(settings.database_url))
    now = datetime.now(UTC)
    tenant_id = bootstrap_demo.TENANT_ID
    product_id = bootstrap_demo.PRODUCT_ID
    user_id = bootstrap_demo.USER_ID
    assembly_plan_id = _id("assembly-plan")
    final_creative_id = _id("final-creative")

    async with engine.begin() as connection:
        product_snapshot = (
            (
                await connection.execute(
                    select(product_knowledge_snapshots)
                    .where(product_knowledge_snapshots.c.product_id == product_id)
                    .order_by(product_knowledge_snapshots.c.created_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one()
        )
        plan = (
            (
                await connection.execute(
                    select(production_plans)
                    .where(production_plans.c.product_id == product_id)
                    .order_by(production_plans.c.created_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one()
        )
        concept = (
            (await connection.execute(select(concepts).where(concepts.c.id == plan["concept_id"])))
            .mappings()
            .one()
        )
        output_asset = (
            (
                await connection.execute(
                    select(assets)
                    .where(
                        assets.c.product_id == product_id,
                        assets.c.kind == "video",
                        assets.c.status == "ready",
                    )
                    .order_by(assets.c.created_at)
                    .limit(1)
                )
            )
            .mappings()
            .one()
        )
        social_account = (
            (
                await connection.execute(
                    select(social_accounts)
                    .where(social_accounts.c.tenant_id == tenant_id)
                    .order_by(social_accounts.c.created_at)
                    .limit(1)
                )
            )
            .mappings()
            .one()
        )

        await connection.execute(
            insert(assembly_plans).values(
                id=assembly_plan_id,
                tenant_id=tenant_id,
                product_id=product_id,
                production_plan_id=plan["id"],
                production_plan_digest=plan["semantic_digest"],
                creative_concept_id=concept["id"],
                creative_concept_digest=concept["semantic_digest"],
                schema_version=1,
                render_profile_key="social_vertical_v1",
                render_profile_version=1,
                timeline_duration_ms=15_000,
                semantic_digest=_digest("phase815-assembly-plan"),
                created_by_user_id=user_id,
                created_at=now - timedelta(days=10),
            )
        )
        await connection.execute(
            insert(final_creatives).values(
                id=final_creative_id,
                tenant_id=tenant_id,
                product_id=product_id,
                assembly_plan_id=assembly_plan_id,
                assembly_plan_digest=_digest("phase815-assembly-plan"),
                production_plan_id=plan["id"],
                production_plan_digest=plan["semantic_digest"],
                creative_concept_id=concept["id"],
                creative_concept_digest=concept["semantic_digest"],
                output_asset_id=output_asset["id"],
                output_asset_digest=output_asset["digest"],
                render_profile_key="social_vertical_v1",
                render_profile_version=1,
                renderer="acceptance_fixture",
                renderer_version="phase815-v1",
                duration_ms=15_000,
                width=1080,
                height=1920,
                fps=24,
                has_audio=False,
                source_count=1,
                semantic_digest=_digest("phase815-final-creative"),
                created_at=now - timedelta(days=10),
            )
        )

        subject_snapshot_id = None
        for index, impressions in enumerate((120, 160, 200, 260), start=1):
            published_at = now - timedelta(days=5 - index)
            draft_id = _id(f"draft-{index}")
            publication_id = _id(f"publication-{index}")
            observation_id = _id(f"observation-{index}")
            snapshot_id = _id(f"performance-snapshot-{index}")
            completed_at = published_at + timedelta(hours=24)
            await connection.execute(
                insert(publication_drafts).values(
                    id=draft_id,
                    tenant_id=tenant_id,
                    product_id=product_id,
                    final_creative_id=final_creative_id,
                    final_creative_digest=_digest("phase815-final-creative"),
                    output_asset_id=output_asset["id"],
                    output_asset_digest=output_asset["digest"],
                    platform=social_account["platform"],
                    social_account_id=social_account["id"],
                    external_destination_id=social_account["external_account_id"],
                    caption=f"Synthetic acceptance publication {index}",
                    title=None,
                    hashtags=["synthetic", "acceptance"],
                    destination_url=None,
                    mode="POST_NOW",
                    scheduled_at=None,
                    platform_settings={},
                    schema_version=1,
                    semantic_digest=_digest(f"phase815-draft-{index}"),
                    created_by_user_id=user_id,
                    created_at=published_at,
                )
            )
            await connection.execute(
                insert(publications).values(
                    id=publication_id,
                    tenant_id=tenant_id,
                    product_id=product_id,
                    publication_draft_id=draft_id,
                    final_creative_id=final_creative_id,
                    output_asset_id=output_asset["id"],
                    platform=social_account["platform"],
                    social_account_id=social_account["id"],
                    external_post_id=f"phase815-fake-{index}",
                    external_operation_id=f"phase815-operation-{index}",
                    canonical_permalink=f"https://social.invalid/phase815-fake-{index}",
                    provider="fake",
                    connector_version="phase815-fixture-v1",
                    request_digest=_digest(f"phase815-request-{index}"),
                    response_metadata_digest=_digest(f"phase815-response-{index}"),
                    status="PUBLISHED",
                    submitted_at=published_at,
                    published_at=published_at,
                    semantic_digest=_digest(f"phase815-publication-{index}"),
                    created_at=published_at,
                )
            )
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
                    observed_at=completed_at,
                    provider="fake",
                    provider_version="phase815-fixture-v1",
                    source_digest=_digest(f"phase815-source-{index}"),
                    created_at=completed_at,
                )
            )
            await connection.execute(
                insert(collection_runs).values(
                    id=_id(f"collection-{index}"),
                    tenant_id=tenant_id,
                    publication_id=publication_id,
                    status="SUCCEEDED",
                    checkpoint="schedule-v1:2",
                    cursor=None,
                    failure_code=None,
                    created_at=completed_at,
                    completed_at=completed_at,
                )
            )
            await connection.execute(
                insert(performance_snapshots).values(
                    id=snapshot_id,
                    tenant_id=tenant_id,
                    product_id=product_id,
                    publication_id=publication_id,
                    observation_ids=[observation_id],
                    latest_metrics={"impressions": impressions},
                    derived_metrics=[],
                    attributed_conversions=0,
                    attributed_revenue={},
                    freshness="CURRENT",
                    semantic_digest=_digest(f"phase815-performance-{index}"),
                    created_at=completed_at,
                )
            )
            if index == 4:
                subject_snapshot_id = snapshot_id

    sessions = create_session_factory(str(settings.database_url))

    def fake_intelligence(invocation: ModelInvocation) -> ModelInvocationResult:
        return ModelInvocationResult(
            bootstrap_demo._intelligence_output(invocation),
            "phase815-fake-intelligence",
            ModelUsage(100, 100, 200),
            "openai",
            "gpt-5.6-sol",
        )

    runtime = AgentRunService(
        SqlAlchemyAgentRuntimeUnitOfWorkFactory(sessions),
        ModelRouter((initial_intelligence_route(),)),
        ModelProviderRegistry({"openai": FakeModelProvider(fake_intelligence)}),
        bootstrap_demo.DemoWorkload(),
    )
    context = bootstrap_demo._context()
    requested = await runtime.request_intelligence(
        context,
        product_id=product_id,
        idempotency_key="phase815-intelligence-v1",
    )
    completed = await runtime.execute(tenant_id, requested.id)
    if completed.status.value != "SUCCEEDED":
        raise RuntimeError(f"fake Intelligence failed safely: {completed.failure_code}")

    intelligence = IntelligenceService(SqlAlchemyIntelligenceUnitOfWorkFactory(sessions))
    report = (await intelligence.list_reports(context, product_id))[0]
    report, candidates, proposals = await intelligence.get_report(context, report.id)
    if len(candidates) != 1 or len(proposals) != 1:
        raise RuntimeError("synthetic comparable cohort did not produce one governed proposal")
    proposal = proposals[0]

    cycle = CreativeCycle(
        tenant_id,
        product_id,
        user_id,
        product_snapshot["id"],
        product_snapshot["digest"],
        artifacts=CycleArtifacts(
            performance_snapshot_id=subject_snapshot_id,
            intelligence_report_id=report.id,
            experiment_proposal_id=proposal.id,
        ),
    )
    cycle = replace(cycle, current_stage=CycleStage.AWAITING_EXPERIMENT_DECISION)
    orchestration = SqlAlchemyOrchestrationUnitOfWorkFactory(sessions)
    async with orchestration(tenant_id) as uow:
        await uow.cycles.add(cycle)
        await uow.cycles.add_transition(
            CycleTransition(
                tenant_id,
                cycle.id,
                None,
                CycleStage.CREATED,
                CycleStatus.ACTIVE,
                "ACCEPTANCE_FIXTURE_CREATED",
                "user",
                user_id,
                context.correlation_id,
            )
        )
        await uow.cycles.add_transition(
            CycleTransition(
                tenant_id,
                cycle.id,
                CycleStage.INTELLIGENCE,
                CycleStage.AWAITING_EXPERIMENT_DECISION,
                CycleStatus.ACTIVE,
                "ACCEPTANCE_FIXTURE_AWAITING_EXPERIMENT",
                "user",
                user_id,
                context.correlation_id,
            )
        )
        await uow.commit()

    print(
        json.dumps(
            {
                "tenant_id": str(tenant_id),
                "product_id": str(product_id),
                "cycle_id": str(cycle.id),
                "intelligence_report_id": str(report.id),
                "insight_candidate_count": len(candidates),
                "experiment_proposal_id": str(proposal.id),
                "experiment_proposal_count": len(proposals),
                "cohort_size": 3,
                "comparison_window": "+24h",
                "data_trust_level": report.data_trust_level.value,
            },
            sort_keys=True,
        )
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
