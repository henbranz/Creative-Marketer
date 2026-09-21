from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.agent_governance.domain import (
    AgentDefinitionStatus,
    AgentVersionConfiguration,
)
from creative_marketer.agent_runtime.application import (
    CommercePreparation,
    CreativePreparation,
    IntelligencePreparation,
    ProducerPreparation,
    ResearcherPreparation,
    ResolvedResearcher,
    SupervisorPreparation,
    build_commerce_model_context,
    build_creative_model_context,
    build_intelligence_model_context,
    build_producer_model_context,
    build_supervisor_model_context,
    compact_context,
)
from creative_marketer.agent_runtime.domain import (
    AgentPeriodBudgetExceeded,
    AgentRun,
    AgentRunRecoveryConflict,
    AgentRunStatus,
    Citation,
    Confidence,
    EvidenceBlockRef,
    Finding,
    FindingCategory,
    ModelAttempt,
    ModelAttemptStatus,
    ModelContext,
    ModelInvocationResult,
    ModelRoute,
    RecommendedSource,
    RecoveryClassification,
    ResearchSnapshot,
    StrandedAgentRun,
    UnknownCostReconciliationConflict,
    canonical_digest,
    classify_stranded_attempt,
)
from creative_marketer.catalog.domain import (
    ProductKnowledgeSnapshot,
    evaluate_semantic_completeness,
)
from creative_marketer.commerce.domain import (
    CommerceAgentResult,
    CommerceOperationsContextManifest,
    FulfillmentState,
    InventoryObservation,
    OrderLineObservation,
    OrderObservation,
    PaymentState,
    inventory_exceptions,
    order_exceptions,
)
from creative_marketer.creative.domain import (
    ApprovedCreativeConcept,
    ApprovedExperimentContext,
    ChannelIntent,
    CreativeConcept,
    CreativeConceptDecision,
    CreativeConceptSet,
    CreativeDecisionState,
    CreativeStrategyContext,
    CreativeStrategyRequest,
    product_claim_refs,
)
from creative_marketer.infrastructure.database.agent_governance_repositories import _configuration
from creative_marketer.infrastructure.database.agent_governance_schema import (
    agent_activations,
    agent_definitions,
    agent_versions,
)
from creative_marketer.infrastructure.database.agent_runtime_schema import (
    agent_budget_usage,
    agent_runs,
    model_attempts,
    model_cost_reconciliations,
    research_snapshots,
)
from creative_marketer.infrastructure.database.assembly_schema import (
    assembly_plans,
    final_creatives,
)
from creative_marketer.infrastructure.database.catalog_schema import (
    assets,
    product_knowledge_snapshots,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    action_proposals as commerce_action_proposals,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    context_manifests as commerce_context_manifests,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    inventory_observations as commerce_inventory_observations,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    order_observations as commerce_order_observations,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    product_mappings as commerce_product_mappings,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    reports as commerce_reports,
)
from creative_marketer.infrastructure.database.creative_schema import (
    concept_decisions,
    concept_sets,
    concepts,
)
from creative_marketer.infrastructure.database.intelligence_schema import (
    context_manifests,
    creative_feature_snapshots,
    experiment_decisions,
    experiment_proposals,
    insight_candidates,
    performance_comparisons,
)
from creative_marketer.infrastructure.database.intelligence_schema import (
    reports as intelligence_reports,
)
from creative_marketer.infrastructure.database.measurement_schema import (
    attribution_results,
    collection_runs,
    performance_observations,
    performance_snapshots,
)
from creative_marketer.infrastructure.database.orchestration_schema import (
    supervisor_context_manifests,
    supervisor_reports,
)
from creative_marketer.infrastructure.database.production_schema import (
    generation_segments,
    production_plans,
    production_scenes,
    production_shots,
)
from creative_marketer.infrastructure.database.publishing_schema import (
    publication_drafts,
    publications,
)
from creative_marketer.infrastructure.database.research_repositories import (
    _evidence,
    _social_evidence,
)
from creative_marketer.infrastructure.database.research_schema import (
    evidence_snapshots,
    research_targets,
    social_evidence_snapshots,
    sources,
)
from creative_marketer.intelligence.domain import (
    CanonicalRef,
    ComparableSnapshot,
    CreativeFeatureSnapshot,
    DataTrustLevel,
    IntelligenceContextManifest,
    IntelligenceResult,
    PerformanceComparison,
    build_comparisons,
)
from creative_marketer.orchestration.domain import (
    CycleArtifacts,
    CycleReadiness,
    CycleStage,
    ReadinessRequirement,
    ReadinessState,
    SupervisorAction,
    SupervisorContextManifest,
    SupervisorReport,
)
from creative_marketer.production.application import production_context_from_payload
from creative_marketer.production.domain import ProductionPlan, ProductionPlanningRequest
from creative_marketer.research.domain import (
    EvidenceSnapshot,
    ResearchCategory,
    ResearchContextManifest,
    ResearchEvidenceReference,
    SocialEvidenceReference,
    SocialEvidenceSnapshot,
    SocialPlatform,
)


def _run(row: object) -> AgentRun:
    d = row._mapping  # type: ignore[attr-defined]
    return AgentRun(
        id=d["id"],
        tenant_id=d["tenant_id"],
        requested_agent_definition_id=d["requested_agent_definition_id"],
        resolved_agent_definition_id=d["resolved_agent_definition_id"],
        agent_version_id=d["agent_version_id"],
        agent_version_number=d["agent_version_number"],
        agent_configuration_digest=d["agent_configuration_digest"],
        prompt_revision=d["prompt_revision"],
        product_id=d["product_id"],
        product_snapshot_id=d["product_snapshot_id"],
        product_snapshot_digest=d["product_snapshot_digest"],
        product_snapshot_schema_version=d["product_snapshot_schema_version"],
        research_context_digest=d["research_context_digest"],
        context_digest=d["context_digest"],
        selected_evidence=tuple(dict(item) for item in d["selected_evidence"]),
        model_profile_key=d["model_profile_key"],
        output_contract_key=d["output_contract_key"],
        output_contract_version=d["output_contract_version"],
        correlation_id=d["correlation_id"],
        initiated_by_actor_kind=d["initiated_by_actor_kind"],
        initiated_by_actor_id=d["initiated_by_actor_id"],
        period_start=d["period_start"],
        reserved_cost=d["reserved_cost"],
        currency=d["currency"],
        idempotency_key=d["idempotency_key"],
        agent_type=d.get("agent_type", "researcher"),
        input_context_kind=d.get("input_context_kind", "researcher.v1"),
        input_context_schema_version=d.get("input_context_schema_version", 1),
        input_context_digest=d.get("input_context_digest", d["context_digest"]),
        input_context_refs=tuple(
            dict(item) for item in d.get("input_context_refs", d["selected_evidence"])
        ),
        recovery_of_run_id=d["recovery_of_run_id"],
        status=AgentRunStatus(d["status"]),
        created_at=d["created_at"],
        started_at=d["started_at"],
        completed_at=d["completed_at"],
        executed_by_workload_id=d["executed_by_workload_id"],
        resolved_provider=d["resolved_provider"],
        resolved_model=d["resolved_model"],
        resolved_model_route_version=d["resolved_model_route_version"],
        pricing_version=d["pricing_version"],
        reasoning_effort=d["reasoning_effort"],
        max_output_tokens=d["max_output_tokens"],
        max_total_tokens=d["max_total_tokens"],
        model_call_count=d["model_call_count"],
        input_tokens=d["input_tokens"],
        output_tokens=d["output_tokens"],
        total_tokens=d["total_tokens"],
        estimated_cost=d["estimated_cost"],
        provider_response_id=d["provider_response_id"],
        result_ref=d["result_ref"],
        failure_code=d["failure_code"],
    )


def _run_values(value: AgentRun) -> dict[str, object]:
    return {
        "id": value.id,
        "tenant_id": value.tenant_id,
        "requested_agent_definition_id": value.requested_agent_definition_id,
        "resolved_agent_definition_id": value.resolved_agent_definition_id,
        "agent_version_id": value.agent_version_id,
        "agent_version_number": value.agent_version_number,
        "agent_configuration_digest": value.agent_configuration_digest,
        "prompt_revision": value.prompt_revision,
        "product_id": value.product_id,
        "product_snapshot_id": value.product_snapshot_id,
        "product_snapshot_digest": value.product_snapshot_digest,
        "product_snapshot_schema_version": value.product_snapshot_schema_version,
        "research_context_digest": value.research_context_digest,
        "context_digest": value.context_digest,
        "selected_evidence": [dict(item) for item in value.selected_evidence],
        "model_profile_key": value.model_profile_key,
        "output_contract_key": value.output_contract_key,
        "output_contract_version": value.output_contract_version,
        "correlation_id": value.correlation_id,
        "initiated_by_actor_kind": value.initiated_by_actor_kind,
        "initiated_by_actor_id": value.initiated_by_actor_id,
        "period_start": value.period_start,
        "reserved_cost": value.reserved_cost,
        "currency": value.currency,
        "idempotency_key": value.idempotency_key,
        "agent_type": value.agent_type,
        "input_context_kind": value.input_context_kind,
        "input_context_schema_version": value.input_context_schema_version,
        "input_context_digest": value.input_context_digest,
        "input_context_refs": [dict(item) for item in value.input_context_refs],
        "recovery_of_run_id": value.recovery_of_run_id,
        "status": value.status.value,
        "created_at": value.created_at,
        "started_at": value.started_at,
        "completed_at": value.completed_at,
        "executed_by_workload_id": value.executed_by_workload_id,
        "resolved_provider": value.resolved_provider,
        "resolved_model": value.resolved_model,
        "resolved_model_route_version": value.resolved_model_route_version,
        "pricing_version": value.pricing_version,
        "reasoning_effort": value.reasoning_effort,
        "max_output_tokens": value.max_output_tokens,
        "max_total_tokens": value.max_total_tokens,
        "model_call_count": value.model_call_count,
        "input_tokens": value.input_tokens,
        "output_tokens": value.output_tokens,
        "total_tokens": value.total_tokens,
        "estimated_cost": value.estimated_cost,
        "provider_response_id": value.provider_response_id,
        "result_ref": value.result_ref,
        "failure_code": value.failure_code,
    }


def _supervisor_manifest(row: Mapping[str, object]) -> SupervisorContextManifest:
    payload = cast(Mapping[str, object], row["manifest"])
    readiness_payload = cast(Mapping[str, object], payload["readiness"])
    requirements = tuple(
        ReadinessRequirement(
            str(item["key"]),
            ReadinessState(str(item["state"])),
            str(item["message"]),
            bool(item["required"]),
            str(item["resource_ref"]) if item.get("resource_ref") else None,
        )
        for item in cast(list[Mapping[str, object]], readiness_payload["requirements"])
    )
    artifacts_payload = cast(Mapping[str, object], payload["artifacts"])
    artifacts = CycleArtifacts(
        **{key: UUID(str(value)) if value else None for key, value in artifacts_payload.items()}
    )
    readiness = CycleReadiness(
        ReadinessState(str(readiness_payload["state"])),
        requirements,
        tuple(
            SupervisorAction(str(item))
            for item in cast(list[object], readiness_payload["allowed_actions"])
        ),
    )
    return SupervisorContextManifest(
        cast(UUID, row["tenant_id"]),
        cast(UUID, row["product_id"]),
        cast(UUID, row["cycle_id"]),
        int(cast(int, row["cycle_version"])),
        CycleStage(str(row["current_stage"])),
        readiness,
        UUID(str(payload["product_snapshot_id"])),
        str(payload["product_snapshot_digest"]),
        artifacts,
        tuple(str(item) for item in cast(list[object], payload["pending_approvals"])),
        str(payload["provider_mode"]),
        int(cast(int, row["schema_version"])),
        cast(UUID, row["id"]),
        str(row["semantic_digest"]),
        cast(datetime, row["created_at"]),
    )


def _attempt(row: object) -> ModelAttempt:
    d = row._mapping  # type: ignore[attr-defined]
    return ModelAttempt(
        id=d["id"],
        tenant_id=d["tenant_id"],
        agent_run_id=d["agent_run_id"],
        attempt_number=d["attempt_number"],
        workload_id=d["workload_id"],
        model_route_version=d["model_route_version"],
        pricing_version=d["pricing_version"],
        provider=d["provider"],
        model=d["model"],
        status=ModelAttemptStatus(d["status"]),
        claimed_at=d["claimed_at"],
        provider_started_at=d["provider_started_at"],
        response_recorded_at=d["response_recorded_at"],
        finished_at=d["finished_at"],
        lease_expires_at=d["lease_expires_at"],
        provider_response_id=d["provider_response_id"],
        input_tokens=d["input_tokens"],
        output_tokens=d["output_tokens"],
        total_tokens=d["total_tokens"],
        estimated_cost=d["estimated_cost"],
        unknown_cost=d["unknown_cost"],
        failure_code=d["failure_code"],
    )


def _snapshot(row: object) -> ResearchSnapshot:
    d = row._mapping  # type: ignore[attr-defined]
    findings = tuple(
        Finding(
            key=item["key"],
            category=FindingCategory(item["category"]),
            statement=item["statement"],
            confidence=Confidence(item["confidence"]),
            scope=item["scope"],
            implication=item.get("implication"),
            citations=tuple(
                Citation(UUID(c["evidence_snapshot_id"]), c["block_index"], c["block_digest"])
                for c in item["citations"]
            ),
        )
        for item in d["findings"]
    )
    recommendations = tuple(RecommendedSource(**item) for item in d["recommended_next_sources"])
    return ResearchSnapshot(
        id=d["id"],
        tenant_id=d["tenant_id"],
        product_id=d["product_id"],
        agent_run_id=d["agent_run_id"],
        schema_version=d["schema_version"],
        product_snapshot_id=d["product_snapshot_id"],
        product_snapshot_digest=d["product_snapshot_digest"],
        research_context_digest=d["research_context_digest"],
        findings=findings,
        research_gaps=tuple(d["research_gaps"]),
        recommended_next_sources=recommendations,
        semantic_digest=d["semantic_digest"],
        created_at=d["created_at"],
        valid_until=d["valid_until"],
    )


class SqlAlchemyAgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def prepare_researcher(self, product_id: UUID) -> ResearcherPreparation | None:
        requested_rows = (
            await self._session.execute(
                select(agent_definitions)
                .where(
                    agent_definitions.c.tenant_id.is_not(None),
                    agent_definitions.c.agent_type == "researcher",
                    agent_definitions.c.status == AgentDefinitionStatus.ACTIVE.value,
                )
                .order_by(agent_definitions.c.created_at)
                .limit(2)
            )
        ).all()
        if len(requested_rows) != 1:
            return None
        requested_row = requested_rows[0]
        requested = requested_row._mapping
        resolved_id = requested["id"]
        activation = (
            await self._session.execute(
                select(agent_activations).where(agent_activations.c.definition_id == resolved_id)
            )
        ).first()
        if activation is None and requested["platform_template_id"] is not None:
            resolved_id = requested["platform_template_id"]
            template = (
                await self._session.execute(
                    select(agent_definitions).where(
                        agent_definitions.c.id == resolved_id,
                        agent_definitions.c.status == AgentDefinitionStatus.ACTIVE.value,
                    )
                )
            ).first()
            if template is None:
                return None
            activation = (
                await self._session.execute(
                    select(agent_activations).where(
                        agent_activations.c.definition_id == resolved_id
                    )
                )
            ).first()
        if activation is None:
            return None
        activation_data = activation._mapping
        version_row = (
            await self._session.execute(
                select(agent_versions).where(
                    agent_versions.c.id == activation_data["active_version_id"]
                )
            )
        ).first()
        snapshot_row = (
            await self._session.execute(
                select(product_knowledge_snapshots)
                .where(product_knowledge_snapshots.c.product_id == product_id)
                .order_by(
                    product_knowledge_snapshots.c.created_at.desc(),
                    product_knowledge_snapshots.c.id.desc(),
                )
                .limit(1)
            )
        ).first()
        if version_row is None or snapshot_row is None:
            return None
        v = version_row._mapping
        s = snapshot_row._mapping
        product_snapshot = ProductKnowledgeSnapshot(
            id=s["id"],
            tenant_id=s["tenant_id"],
            product_id=s["product_id"],
            schema_version=s["schema_version"],
            source_revision=s["source_revision"],
            content=s["content"],
            digest=s["digest"],
            created_by=s["created_by"],
            created_at=s["created_at"],
        )
        ranked = (
            select(evidence_snapshots, sources.c.category, sources.c.display_name)
            .join(
                sources,
                and_(
                    sources.c.id == evidence_snapshots.c.source_id,
                    sources.c.tenant_id == evidence_snapshots.c.tenant_id,
                ),
            )
            .where(evidence_snapshots.c.product_id == product_id, sources.c.status == "active")
            .distinct(evidence_snapshots.c.source_id)
            .order_by(evidence_snapshots.c.source_id, evidence_snapshots.c.captured_at.desc())
            .subquery()
        )
        evidence_rows = (await self._session.execute(select(ranked))).all()
        web_evidence = tuple(
            (
                _evidence(row),
                ResearchCategory(row._mapping["category"]),
                row._mapping["display_name"],
            )
            for row in evidence_rows
        )
        social_rows = (
            await self._session.execute(
                select(social_evidence_snapshots, research_targets.c.display_name)
                .join(
                    research_targets,
                    and_(
                        research_targets.c.id == social_evidence_snapshots.c.research_target_id,
                        research_targets.c.tenant_id == social_evidence_snapshots.c.tenant_id,
                    ),
                )
                .where(
                    social_evidence_snapshots.c.product_id == product_id,
                    research_targets.c.status == "active",
                )
                .order_by(social_evidence_snapshots.c.captured_at.desc())
                .limit(20)
            )
        ).all()
        social_items: list[tuple[SocialEvidenceSnapshot, ResearchCategory, str]] = []
        seen_social: set[tuple[UUID, str]] = set()
        for row in social_rows:
            item = _social_evidence(row)
            key = (
                item.research_target_id,
                item.platform_content_id or item.source_url or str(item.id),
            )
            if key in seen_social:
                continue
            seen_social.add(key)
            social_items.append((item, ResearchCategory.COMPETITOR, row._mapping["display_name"]))
        social_evidence = tuple(social_items)
        evidence = web_evidence + social_evidence
        if not evidence:
            return None
        references = tuple(
            ResearchEvidenceReference(
                item.source_id, item.id, item.semantic_digest, category, item.captured_at
            )
            for item, category, _label in web_evidence
        )
        social_references = tuple(
            SocialEvidenceReference(
                item.research_target_id,
                item.id,
                item.semantic_digest,
                item.platform,
                item.captured_at,
            )
            for item, _category, _label in social_evidence
        )
        manifest = ResearchContextManifest.build(
            s["tenant_id"], product_id, references, social_references
        )
        researcher = ResolvedResearcher(
            requested["id"],
            resolved_id,
            v["id"],
            v["version_number"],
            v["configuration_digest"],
            _configuration(v),
        )
        return ResearcherPreparation(researcher, product_snapshot, manifest, evidence)

    async def prepare_creative(
        self, product_id: UUID, experiment_proposal_id: UUID | None = None
    ) -> CreativePreparation | None:
        requested_rows = (
            await self._session.execute(
                select(agent_definitions)
                .where(
                    agent_definitions.c.tenant_id.is_not(None),
                    agent_definitions.c.agent_type == "creative_strategist",
                    agent_definitions.c.status == AgentDefinitionStatus.ACTIVE.value,
                )
                .order_by(agent_definitions.c.created_at)
                .limit(2)
            )
        ).all()
        if len(requested_rows) != 1:
            return None
        requested = requested_rows[0]._mapping
        resolved_id = requested["id"]
        activation = (
            await self._session.execute(
                select(agent_activations).where(agent_activations.c.definition_id == resolved_id)
            )
        ).first()
        if activation is None and requested["platform_template_id"] is not None:
            resolved_id = requested["platform_template_id"]
            template = (
                await self._session.execute(
                    select(agent_definitions).where(
                        agent_definitions.c.id == resolved_id,
                        agent_definitions.c.status == AgentDefinitionStatus.ACTIVE.value,
                    )
                )
            ).first()
            if template is None:
                return None
            activation = (
                await self._session.execute(
                    select(agent_activations).where(
                        agent_activations.c.definition_id == resolved_id
                    )
                )
            ).first()
        if activation is None:
            return None
        version_row = (
            await self._session.execute(
                select(agent_versions).where(
                    agent_versions.c.id == activation._mapping["active_version_id"]
                )
            )
        ).first()
        snapshot_row = (
            await self._session.execute(
                select(product_knowledge_snapshots)
                .where(
                    product_knowledge_snapshots.c.product_id == product_id,
                    product_knowledge_snapshots.c.schema_version == 2,
                )
                .order_by(
                    product_knowledge_snapshots.c.created_at.desc(),
                    product_knowledge_snapshots.c.id.desc(),
                )
                .limit(1)
            )
        ).first()
        research_row = (
            await self._session.execute(
                select(research_snapshots)
                .where(research_snapshots.c.product_id == product_id)
                .order_by(research_snapshots.c.created_at.desc(), research_snapshots.c.id.desc())
                .limit(1)
            )
        ).first()
        if version_row is None or snapshot_row is None or research_row is None:
            return None
        v, s = version_row._mapping, snapshot_row._mapping
        product = ProductKnowledgeSnapshot(
            id=s["id"],
            tenant_id=s["tenant_id"],
            product_id=s["product_id"],
            schema_version=s["schema_version"],
            source_revision=s["source_revision"],
            content=s["content"],
            digest=s["digest"],
            created_by=s["created_by"],
            created_at=s["created_at"],
        )
        research = _snapshot(research_row)
        freshness = await self.snapshot_freshness(research)
        profile = product.content.get("profile", {})
        brief = product.content.get("brief", {})
        if not isinstance(profile, Mapping) or not isinstance(brief, Mapping):
            completeness = 0
        else:
            completeness = evaluate_semantic_completeness(profile, brief).score
        strategist = ResolvedResearcher(
            requested["id"],
            resolved_id,
            v["id"],
            v["version_number"],
            v["configuration_digest"],
            _configuration(v),
        )
        approved_experiment: ApprovedExperimentContext | None = None
        if experiment_proposal_id is not None:
            proposal_row = (
                await self._session.execute(
                    select(experiment_proposals).where(
                        experiment_proposals.c.id == experiment_proposal_id,
                        experiment_proposals.c.product_id == product_id,
                    )
                )
            ).first()
            decision_row = (
                await self._session.execute(
                    select(experiment_decisions)
                    .where(experiment_decisions.c.proposal_id == experiment_proposal_id)
                    .order_by(
                        experiment_decisions.c.created_at.desc(), experiment_decisions.c.id.desc()
                    )
                    .limit(1)
                )
            ).first()
            if proposal_row is None or decision_row is None:
                return None
            p, decision = proposal_row._mapping, decision_row._mapping
            report_exists = await self._session.scalar(
                select(intelligence_reports.c.id).where(
                    intelligence_reports.c.id == p["report_id"],
                    intelligence_reports.c.semantic_digest == p["report_digest"],
                )
            )
            if (
                decision["decision"] != "APPROVED_FOR_CREATIVE"
                or decision["proposal_digest"] != p["semantic_digest"]
                or report_exists is None
            ):
                return None
            approved_experiment = ApprovedExperimentContext(
                p["id"],
                p["semantic_digest"],
                p["report_id"],
                p["report_digest"],
                p["data_trust_level"],
                p["hypothesis"],
                p["primary_variable"],
                tuple(p["controlled_elements"]),
                p["target_metric"],
                p["platform"],
                p["measurement_window"],
                p["creative_direction"],
            )
        return CreativePreparation(
            strategist, product, research, freshness, completeness, approved_experiment
        )

    async def prepare_intelligence(self, product_id: UUID) -> IntelligencePreparation | None:
        requested_rows = (
            await self._session.execute(
                select(agent_definitions)
                .where(
                    agent_definitions.c.tenant_id.is_not(None),
                    agent_definitions.c.agent_type == "intelligence",
                    agent_definitions.c.status == AgentDefinitionStatus.ACTIVE.value,
                )
                .order_by(agent_definitions.c.created_at)
                .limit(2)
            )
        ).all()
        if len(requested_rows) != 1:
            return None
        requested = requested_rows[0]._mapping
        resolved_id = requested["id"]
        activation = (
            await self._session.execute(
                select(agent_activations).where(agent_activations.c.definition_id == resolved_id)
            )
        ).first()
        if activation is None and requested["platform_template_id"] is not None:
            resolved_id = requested["platform_template_id"]
            activation = (
                await self._session.execute(
                    select(agent_activations)
                    .join(
                        agent_definitions,
                        agent_definitions.c.id == agent_activations.c.definition_id,
                    )
                    .where(
                        agent_activations.c.definition_id == resolved_id,
                        agent_definitions.c.status == AgentDefinitionStatus.ACTIVE.value,
                    )
                )
            ).first()
        if activation is None:
            return None
        version_row = (
            await self._session.execute(
                select(agent_versions).where(
                    agent_versions.c.id == activation._mapping["active_version_id"]
                )
            )
        ).first()
        product_row = (
            await self._session.execute(
                select(product_knowledge_snapshots)
                .where(product_knowledge_snapshots.c.product_id == product_id)
                .order_by(
                    product_knowledge_snapshots.c.created_at.desc(),
                    product_knowledge_snapshots.c.id.desc(),
                )
                .limit(1)
            )
        ).first()
        snapshot_rows = (
            await self._session.execute(
                select(performance_snapshots)
                .where(performance_snapshots.c.product_id == product_id)
                .order_by(
                    performance_snapshots.c.created_at.desc(), performance_snapshots.c.id.desc()
                )
            )
        ).all()
        if version_row is None or product_row is None or not snapshot_rows:
            return None
        subject_snapshot_id = snapshot_rows[0]._mapping["id"]
        product_data = product_row._mapping
        product = ProductKnowledgeSnapshot(
            id=product_data["id"],
            tenant_id=product_data["tenant_id"],
            product_id=product_data["product_id"],
            schema_version=product_data["schema_version"],
            source_revision=product_data["source_revision"],
            content=product_data["content"],
            digest=product_data["digest"],
            created_by=product_data["created_by"],
            created_at=product_data["created_at"],
        )
        window_by_checkpoint = {
            "schedule-v1:0": "+1h",
            "schedule-v1:1": "+6h",
            "schedule-v1:2": "+24h",
            "schedule-v1:3": "+72h",
            "schedule-v1:4": "+7d",
        }
        comparable: list[ComparableSnapshot] = []
        feature_values: list[CreativeFeatureSnapshot] = []
        publication_refs: list[CanonicalRef] = []
        final_refs: list[CanonicalRef] = []
        concept_refs: list[CanonicalRef] = []
        snapshot_refs: list[CanonicalRef] = []
        attribution_refs: list[CanonicalRef] = []
        observation_facts: list[dict[str, object]] = []
        feature_publications: set[UUID] = set()
        for snapshot_row in snapshot_rows:
            snap = snapshot_row._mapping
            lineage = (
                await self._session.execute(
                    select(
                        publications,
                        publication_drafts,
                        final_creatives,
                        assembly_plans,
                        production_plans,
                        concepts,
                    )
                    .join(
                        publication_drafts,
                        and_(
                            publication_drafts.c.tenant_id == publications.c.tenant_id,
                            publication_drafts.c.id == publications.c.publication_draft_id,
                        ),
                    )
                    .join(
                        final_creatives,
                        and_(
                            final_creatives.c.tenant_id == publications.c.tenant_id,
                            final_creatives.c.id == publications.c.final_creative_id,
                        ),
                    )
                    .join(
                        assembly_plans,
                        and_(
                            assembly_plans.c.tenant_id == final_creatives.c.tenant_id,
                            assembly_plans.c.id == final_creatives.c.assembly_plan_id,
                        ),
                    )
                    .join(
                        production_plans,
                        and_(
                            production_plans.c.tenant_id == final_creatives.c.tenant_id,
                            production_plans.c.id == final_creatives.c.production_plan_id,
                        ),
                    )
                    .join(
                        concepts,
                        and_(
                            concepts.c.tenant_id == final_creatives.c.tenant_id,
                            concepts.c.id == final_creatives.c.creative_concept_id,
                        ),
                    )
                    .where(publications.c.id == snap["publication_id"])
                )
            ).first()
            if lineage is None:
                continue
            data = lineage._mapping
            checkpoint = await self._session.scalar(
                select(collection_runs.c.checkpoint)
                .where(
                    collection_runs.c.publication_id == snap["publication_id"],
                    collection_runs.c.status == "SUCCEEDED",
                    collection_runs.c.completed_at <= snap["created_at"],
                )
                .order_by(collection_runs.c.completed_at.desc())
                .limit(1)
            )
            window = window_by_checkpoint.get(str(checkpoint), "UNMATCHED")
            provider_rows = (
                (
                    await self._session.execute(
                        select(performance_observations.c.provider).where(
                            performance_observations.c.id.in_(list(snap["observation_ids"]))
                        )
                    )
                )
                .scalars()
                .all()
            )
            trust = DataTrustLevel.SYNTHETIC if "fake" in provider_rows else DataTrustLevel.OBSERVED
            metrics = {key: Decimal(value) for key, value in snap["latest_metrics"].items()}
            for derived in snap["derived_metrics"]:
                if derived.get("value") is not None:
                    metrics[str(derived["key"])] = Decimal(str(derived["value"]))
            feature = CreativeFeatureSnapshot.extract(
                tenant_id=snap["tenant_id"],
                product_id=product_id,
                publication_id=data["id"],
                final_creative_id=data["final_creative_id"],
                concept=data["concept_payload"],
                production_plan={"strategy": data["strategy"]},
                assembly_plan={"duration_seconds": data["timeline_duration_ms"] // 1000},
                final_creative={
                    "duration_seconds": Decimal(data["duration_ms"]) / Decimal(1000),
                    "aspect_ratio": f"{data['width']}:{data['height']}",
                    "media_type": "video",
                },
                publication={
                    "platform": data["platform"],
                    "mode": data["mode"],
                    "caption": data["caption"],
                },
            )
            first_publication_snapshot = data["id"] not in feature_publications
            if first_publication_snapshot:
                feature_publications.add(data["id"])
                feature_values.append(feature)
            if window != "UNMATCHED":
                comparable.append(
                    ComparableSnapshot(
                        snap["id"],
                        snap["semantic_digest"],
                        data["id"],
                        data["final_creative_id"],
                        snap["tenant_id"],
                        product_id,
                        data["platform"],
                        "video",
                        window,
                        metrics,
                        trust,
                    )
                )
            observation_facts.append(
                {
                    "performance_snapshot_id": str(snap["id"]),
                    "publication_id": str(data["id"]),
                    "window": window,
                    "platform": data["platform"],
                    "metrics": {key: str(value) for key, value in metrics.items()},
                    "attributed_conversions": snap["attributed_conversions"],
                    "attributed_revenue": snap["attributed_revenue"],
                    "data_trust_level": trust.value,
                }
            )
            if first_publication_snapshot:
                publication_refs.append(
                    CanonicalRef("publication", data["id"], data["semantic_digest"])
                )
                final_refs.append(
                    CanonicalRef(
                        "final_creative",
                        data["final_creative_id"],
                        data["final_creative_digest"],
                    )
                )
                concept_refs.append(
                    CanonicalRef(
                        "creative_concept",
                        data["creative_concept_id"],
                        data["creative_concept_digest"],
                    )
                )
            snapshot_refs.append(
                CanonicalRef("performance_snapshot", snap["id"], snap["semantic_digest"])
            )
            if first_publication_snapshot:
                attr_rows = (
                    (
                        await self._session.execute(
                            select(attribution_results.c.id).where(
                                attribution_results.c.publication_id == data["id"]
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                attribution_refs.extend(
                    CanonicalRef("attribution_result", item) for item in attr_rows
                )
        if not observation_facts:
            return None
        subject = next(
            (item for item in comparable if item.snapshot_id == subject_snapshot_id), None
        )
        comparisons = build_comparisons(subject, comparable) if subject else ()
        for feature_item in feature_values:
            await self._session.execute(
                pg_insert(creative_feature_snapshots)
                .values(
                    id=feature_item.id,
                    tenant_id=feature_item.tenant_id,
                    product_id=feature_item.product_id,
                    publication_id=feature_item.publication_id,
                    final_creative_id=feature_item.final_creative_id,
                    extraction_version=feature_item.extraction_version,
                    features=dict(feature_item.features),
                    semantic_digest=feature_item.semantic_digest,
                    created_at=feature_item.created_at,
                )
                .on_conflict_do_nothing()
            )
        for comparison_item in comparisons:
            await self._session.execute(
                insert(performance_comparisons).values(
                    id=comparison_item.id,
                    tenant_id=comparison_item.tenant_id,
                    product_id=comparison_item.product_id,
                    publication_id=comparison_item.publication_id,
                    final_creative_id=comparison_item.final_creative_id,
                    comparison_window=comparison_item.comparison_window,
                    metric_key=comparison_item.metric_key,
                    observed_value=comparison_item.observed_value,
                    baseline_value=comparison_item.baseline_value,
                    absolute_delta=comparison_item.absolute_delta,
                    relative_delta=comparison_item.relative_delta,
                    sample_size=comparison_item.sample_size,
                    baseline_publication_ids=list(comparison_item.baseline_publication_ids),
                    source_snapshot_ids=list(comparison_item.source_snapshot_ids),
                    comparability_policy_version=comparison_item.comparability_policy_version,
                    calculation_version=comparison_item.calculation_version,
                    data_trust_level=comparison_item.data_trust_level.value,
                    semantic_digest=comparison_item.semantic_digest,
                    created_at=comparison_item.created_at,
                )
            )
        research_row = (
            await self._session.execute(
                select(research_snapshots)
                .where(research_snapshots.c.product_id == product_id)
                .order_by(research_snapshots.c.created_at.desc(), research_snapshots.c.id.desc())
                .limit(1)
            )
        ).first()
        research_ref = (
            CanonicalRef(
                "research_snapshot",
                research_row._mapping["id"],
                research_row._mapping["semantic_digest"],
            )
            if research_row
            else None
        )
        overall_trust = (
            DataTrustLevel.SYNTHETIC
            if any(item["data_trust_level"] == "SYNTHETIC" for item in observation_facts)
            else DataTrustLevel.OBSERVED
        )
        manifest_id = uuid4()
        product_ref = CanonicalRef("product_knowledge_snapshot", product.id, product.digest)
        manifest_content = {
            "product_snapshot": product_ref.primitive(),
            "research_snapshot": research_ref.primitive() if research_ref else None,
            "creative_concepts": [item.primitive() for item in concept_refs],
            "final_creatives": [item.primitive() for item in final_refs],
            "publications": [item.primitive() for item in publication_refs],
            "performance_snapshots": [item.primitive() for item in snapshot_refs],
            "attribution_results": [item.primitive() for item in attribution_refs],
            "comparison_ids": [str(item.id) for item in comparisons],
            "feature_snapshot_ids": [str(item.id) for item in feature_values],
            "comparison_policy_version": "intelligence-comparability-v1",
            "feature_extraction_version": "creative-features-v1",
            "data_trust_level": overall_trust.value,
        }
        manifest = IntelligenceContextManifest(
            product.tenant_id,
            product_id,
            product_ref,
            research_ref,
            tuple(concept_refs),
            tuple(final_refs),
            tuple(publication_refs),
            tuple(snapshot_refs),
            tuple(attribution_refs),
            tuple(item.id for item in comparisons),
            tuple(item.id for item in feature_values),
            overall_trust,
            canonical_digest(manifest_content),
            id=manifest_id,
        )
        await self._session.execute(
            insert(context_manifests).values(
                id=manifest.id,
                tenant_id=manifest.tenant_id,
                product_id=manifest.product_id,
                manifest=manifest.semantic_content(),
                data_trust_level=manifest.data_trust_level.value,
                semantic_digest=manifest.semantic_digest,
                created_at=manifest.created_at,
            )
        )
        payload: dict[str, object] = {
            "manifest": manifest.semantic_content(),
            "observed_facts": observation_facts,
            "creative_features": [
                {
                    "id": str(item.id),
                    "features": dict(item.features),
                    "digest": item.semantic_digest,
                }
                for item in feature_values
            ],
            "performance_comparisons": [
                {"id": str(item.id), **item.semantic_content()} for item in comparisons
            ],
            "deterministic_limitations": [
                *(
                    ["Synthetic demo data; not real market evidence."]
                    if overall_trust is DataTrustLevel.SYNTHETIC
                    else []
                ),
                "Observational evidence does not establish causality.",
                *(
                    [
                        "Insufficient comparable matched-window sample; no baseline comparison "
                        "is available."
                    ]
                    if not comparisons
                    else []
                ),
                "No advertising spend facts are available; ROAS and spend recommendations "
                "are prohibited.",
            ],
        }
        v = version_row._mapping
        resolved = ResolvedResearcher(
            requested["id"],
            resolved_id,
            v["id"],
            v["version_number"],
            v["configuration_digest"],
            _configuration(v),
        )
        return IntelligencePreparation(resolved, product, manifest, comparisons, payload)

    async def prepare_commerce(self, product_id: UUID) -> CommercePreparation | None:
        requested_rows = (
            await self._session.execute(
                select(agent_definitions)
                .where(
                    agent_definitions.c.tenant_id.is_not(None),
                    agent_definitions.c.agent_type == "commerce_operations",
                    agent_definitions.c.status == AgentDefinitionStatus.ACTIVE.value,
                )
                .order_by(agent_definitions.c.created_at)
                .limit(2)
            )
        ).all()
        if len(requested_rows) != 1:
            return None
        requested = requested_rows[0]._mapping
        resolved_id = requested["id"]
        activation = (
            await self._session.execute(
                select(agent_activations).where(agent_activations.c.definition_id == resolved_id)
            )
        ).first()
        if activation is None:
            return None
        version_row = (
            await self._session.execute(
                select(agent_versions).where(
                    agent_versions.c.id == activation._mapping["active_version_id"]
                )
            )
        ).first()
        product_row = (
            await self._session.execute(
                select(product_knowledge_snapshots)
                .where(product_knowledge_snapshots.c.product_id == product_id)
                .order_by(product_knowledge_snapshots.c.created_at.desc())
                .limit(1)
            )
        ).first()
        mapping_row = (
            await self._session.execute(
                select(commerce_product_mappings)
                .where(
                    commerce_product_mappings.c.product_id == product_id,
                    commerce_product_mappings.c.status == "ACTIVE",
                )
                .order_by(commerce_product_mappings.c.created_at.desc())
                .limit(1)
            )
        ).first()
        if version_row is None or product_row is None or mapping_row is None:
            return None
        s, mapping = product_row._mapping, mapping_row._mapping
        product = ProductKnowledgeSnapshot(
            id=s["id"],
            tenant_id=s["tenant_id"],
            product_id=s["product_id"],
            schema_version=s["schema_version"],
            source_revision=s["source_revision"],
            content=s["content"],
            digest=s["digest"],
            created_by=s["created_by"],
            created_at=s["created_at"],
        )
        inventory_rows = (
            await self._session.execute(
                select(commerce_inventory_observations)
                .where(
                    commerce_inventory_observations.c.connection_id == mapping["connection_id"],
                    commerce_inventory_observations.c.external_product_id
                    == mapping["external_product_id"],
                )
                .order_by(commerce_inventory_observations.c.captured_at.desc())
            )
        ).all()
        inventories_by_variant: dict[str, InventoryObservation] = {}
        for row in inventory_rows:
            d = row._mapping
            inventories_by_variant.setdefault(
                d["external_variant_id"],
                InventoryObservation(
                    d["tenant_id"],
                    d["connection_id"],
                    d["external_product_id"],
                    d["external_variant_id"],
                    d["available_quantity"],
                    d["captured_at"],
                    d["source_digest"],
                    d["sku"],
                    d["location_id"],
                    d["committed_quantity"],
                    d["on_hand_quantity"],
                    d["id"],
                    d["provider"],
                    d["provider_version"],
                    d["schema_version"],
                ),
            )
        order_rows = (
            await self._session.execute(
                select(commerce_order_observations)
                .where(commerce_order_observations.c.connection_id == mapping["connection_id"])
                .order_by(commerce_order_observations.c.captured_at.desc())
                .limit(100)
            )
        ).all()
        orders_by_external: dict[str, OrderObservation] = {}
        for row in order_rows:
            d = row._mapping
            lines = tuple(
                OrderLineObservation(
                    str(item["external_line_id"]),
                    str(item["external_product_id"]),
                    item.get("external_variant_id"),
                    item.get("sku"),
                    int(item["quantity"]),
                    Decimal(str(item["unit_price"])),
                    str(item["currency"]),
                    UUID(item["mapped_product_id"]) if item.get("mapped_product_id") else None,
                )
                for item in d["lines"]
            )
            if not any(
                item.external_product_id == mapping["external_product_id"] for item in lines
            ):
                continue
            orders_by_external.setdefault(
                d["external_order_id"],
                OrderObservation(
                    d["tenant_id"],
                    d["connection_id"],
                    d["external_order_id"],
                    d["order_reference"],
                    d["currency"],
                    Decimal(d["subtotal"]),
                    Decimal(d["discount_total"]),
                    Decimal(d["tax_total"]),
                    Decimal(d["shipping_total"]),
                    Decimal(d["total"]),
                    PaymentState(d["financial_status"]),
                    FulfillmentState(d["fulfillment_status"]),
                    d["created_at_external"],
                    d["updated_at_external"],
                    d["captured_at"],
                    lines,
                    d["source_digest"],
                    d["attribution_code"],
                    d["id"],
                    d["schema_version"],
                ),
            )
        inventories, orders = (
            tuple(inventories_by_variant.values()),
            tuple(orders_by_external.values()),
        )
        if not inventories and not orders:
            return None
        observation_refs = tuple(
            [
                {"kind": "inventory_observation", "id": str(item.id), "digest": item.source_digest}
                for item in inventories
            ]
            + [
                {"kind": "order_observation", "id": str(item.id), "digest": item.source_digest}
                for item in orders
            ]
        )
        deterministic = (*inventory_exceptions(inventories), *order_exceptions(orders))
        product_ref = {
            "kind": "product_knowledge_snapshot",
            "id": str(product.id),
            "digest": product.digest,
        }
        mapping_ref = {"kind": "product_commerce_mapping", "id": str(mapping["id"])}
        material = {
            "product_snapshot_ref": product_ref,
            "mapping_ref": mapping_ref,
            "observation_refs": [dict(v) for v in observation_refs],
            "deterministic_exceptions": [dict(v) for v in deterministic],
            "schema_version": 1,
        }
        manifest = CommerceOperationsContextManifest(
            product.tenant_id,
            product_id,
            product_ref,
            mapping_ref,
            observation_refs,
            tuple(deterministic),
            canonical_digest(material),
        )
        manifest_insert = (
            pg_insert(commerce_context_manifests)
            .values(
                id=manifest.id,
                tenant_id=manifest.tenant_id,
                product_id=manifest.product_id,
                product_snapshot_ref=dict(manifest.product_snapshot_ref),
                mapping_ref=dict(manifest.mapping_ref),
                observation_refs=[dict(v) for v in manifest.observation_refs],
                deterministic_exceptions=[dict(v) for v in manifest.deterministic_exceptions],
                semantic_digest=manifest.semantic_digest,
                schema_version=manifest.schema_version,
                created_at=manifest.created_at,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "semantic_digest"])
            .returning(
                commerce_context_manifests.c.id,
                commerce_context_manifests.c.created_at,
            )
        )
        persisted_manifest = (await self._session.execute(manifest_insert)).mappings().one_or_none()
        if persisted_manifest is None:
            persisted_manifest = (
                (
                    await self._session.execute(
                        select(
                            commerce_context_manifests.c.id,
                            commerce_context_manifests.c.created_at,
                        ).where(
                            commerce_context_manifests.c.tenant_id == manifest.tenant_id,
                            commerce_context_manifests.c.semantic_digest
                            == manifest.semantic_digest,
                        )
                    )
                )
                .mappings()
                .one()
            )
        manifest = replace(
            manifest,
            id=persisted_manifest["id"],
            created_at=persisted_manifest["created_at"],
        )
        v = version_row._mapping
        resolved = ResolvedResearcher(
            requested["id"],
            resolved_id,
            v["id"],
            v["version_number"],
            v["configuration_digest"],
            _configuration(v),
        )
        payload = {
            "manifest": {
                "product_snapshot_ref": product_ref,
                "mapping_ref": mapping_ref,
                "observation_refs": [dict(v) for v in observation_refs],
            },
            "inventory": [
                {
                    "observation_id": str(item.id),
                    "external_product_id": item.external_product_id,
                    "external_variant_id": item.external_variant_id,
                    "sku": item.sku,
                    "available_quantity": item.available_quantity,
                    "captured_at": item.captured_at.isoformat(),
                    "source_digest": item.source_digest,
                }
                for item in inventories
            ],
            "orders": [
                {
                    "observation_id": str(item.id),
                    "external_order_id": item.external_order_id,
                    "order_reference": item.order_reference,
                    "currency": item.currency,
                    "total": str(item.total),
                    "payment_state": item.financial_status.value,
                    "fulfillment_state": item.fulfillment_status.value,
                    "captured_at": item.captured_at.isoformat(),
                    "source_digest": item.source_digest,
                }
                for item in orders
            ],
            "deterministic_exceptions": [dict(v) for v in deterministic],
            "deterministic_limitations": [
                "Fake Store observations; not real commerce data.",
                "Refunded revenue reversal is deferred; historical purchases are never "
                "overwritten.",
            ],
        }
        return CommercePreparation(
            resolved, product, manifest, mapping["connection_id"], inventories, orders, payload
        )

    async def prepare_supervisor(self, context_manifest_id: UUID) -> SupervisorPreparation | None:
        manifest_row = (
            (
                await self._session.execute(
                    select(supervisor_context_manifests).where(
                        supervisor_context_manifests.c.id == context_manifest_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if manifest_row is None:
            return None
        manifest = _supervisor_manifest(cast(Mapping[str, object], manifest_row))
        requested_rows = (
            await self._session.execute(
                select(agent_definitions)
                .where(
                    agent_definitions.c.scope_kind == "tenant",
                    agent_definitions.c.tenant_id.is_not(None),
                    agent_definitions.c.agent_type == "supervisor",
                    agent_definitions.c.status == AgentDefinitionStatus.ACTIVE.value,
                )
                .order_by(agent_definitions.c.created_at)
                .limit(2)
            )
        ).all()
        if len(requested_rows) != 1:
            return None
        requested = requested_rows[0]._mapping
        resolved_id = requested["id"]
        activation = (
            await self._session.execute(
                select(agent_activations).where(agent_activations.c.definition_id == resolved_id)
            )
        ).first()
        if activation is None and requested["platform_template_id"] is not None:
            resolved_id = requested["platform_template_id"]
            activation = (
                await self._session.execute(
                    select(agent_activations)
                    .join(
                        agent_definitions,
                        agent_definitions.c.id == agent_activations.c.definition_id,
                    )
                    .where(
                        agent_activations.c.definition_id == resolved_id,
                        agent_definitions.c.status == AgentDefinitionStatus.ACTIVE.value,
                    )
                )
            ).first()
        if activation is None:
            return None
        version_row = (
            await self._session.execute(
                select(agent_versions).where(
                    agent_versions.c.id == activation._mapping["active_version_id"]
                )
            )
        ).first()
        snapshot_row = (
            await self._session.execute(
                select(product_knowledge_snapshots).where(
                    product_knowledge_snapshots.c.id == manifest.product_snapshot_id
                )
            )
        ).first()
        if version_row is None or snapshot_row is None:
            return None
        s = snapshot_row._mapping
        product = ProductKnowledgeSnapshot(
            id=s["id"],
            tenant_id=s["tenant_id"],
            product_id=s["product_id"],
            schema_version=s["schema_version"],
            source_revision=s["source_revision"],
            content=s["content"],
            digest=s["digest"],
            created_by=s["created_by"],
            created_at=s["created_at"],
        )
        if product.product_id != manifest.product_id:
            return None
        v = version_row._mapping
        resolved = ResolvedResearcher(
            requested["id"],
            resolved_id,
            v["id"],
            v["version_number"],
            v["configuration_digest"],
            _configuration(v),
        )
        payload = {
            "cycle": {
                "id": str(manifest.cycle_id),
                "version": manifest.cycle_version,
                "stage": manifest.current_stage.value,
                "product_snapshot_id": str(manifest.product_snapshot_id),
                "product_snapshot_digest": manifest.product_snapshot_digest,
                "artifacts": manifest.artifacts.as_dict(),
            },
            "readiness": {
                "state": manifest.readiness.state.value,
                "requirements": [
                    {
                        "key": item.key,
                        "state": item.state.value,
                        "message": item.message,
                        "required": item.required,
                        "resource_ref": item.resource_ref,
                    }
                    for item in manifest.readiness.requirements
                ],
                "allowed_actions": [item.value for item in manifest.readiness.allowed_actions],
            },
            "pending_approvals": list(manifest.pending_approvals),
            "provider_mode": manifest.provider_mode,
        }
        return SupervisorPreparation(resolved, product, manifest, payload)

    async def prepare_producer(self, concept_id: UUID) -> ProducerPreparation | None:
        concept_row = (
            await self._session.execute(select(concepts).where(concepts.c.id == concept_id))
        ).first()
        if concept_row is None:
            return None
        c = concept_row._mapping
        set_row = (
            await self._session.execute(
                select(concept_sets).where(concept_sets.c.id == c["concept_set_id"])
            )
        ).first()
        decision_row = (
            await self._session.execute(
                select(concept_decisions)
                .where(concept_decisions.c.concept_id == concept_id)
                .order_by(concept_decisions.c.created_at.desc(), concept_decisions.c.id.desc())
                .limit(1)
            )
        ).first()
        if set_row is None or decision_row is None:
            return None
        d = decision_row._mapping
        if d["state"] != CreativeDecisionState.APPROVED_FOR_PRODUCTION.value:
            return None
        cs = set_row._mapping
        concept_rows = (
            await self._session.execute(
                select(concepts)
                .where(concepts.c.concept_set_id == cs["id"])
                .order_by(concepts.c.ordinal)
            )
        ).all()
        concept_values = tuple(
            CreativeConcept(
                row._mapping["tenant_id"],
                row._mapping["product_id"],
                row._mapping["concept_set_id"],
                row._mapping["concept_key"],
                row._mapping["ordinal"],
                row._mapping["concept_payload"],
                row._mapping["semantic_digest"],
                row._mapping["id"],
                row._mapping["created_at"],
            )
            for row in concept_rows
        )
        concept = next(item for item in concept_values if item.id == concept_id)
        concept_set = CreativeConceptSet(
            cs["tenant_id"],
            cs["product_id"],
            cs["agent_run_id"],
            cs["product_snapshot_id"],
            cs["product_snapshot_digest"],
            cs["research_snapshot_id"],
            cs["research_snapshot_digest"],
            cs["input_context_digest"],
            concept_values,
            cs["semantic_digest"],
            id=cs["id"],
            schema_version=cs["schema_version"],
            created_at=cs["created_at"],
            experiment_proposal_id=cs["experiment_proposal_id"],
            experiment_proposal_digest=cs["experiment_proposal_digest"],
        )
        decision = CreativeConceptDecision(
            d["tenant_id"],
            d["product_id"],
            d["concept_id"],
            CreativeDecisionState(d["state"]),
            d["decided_by"],
            d["reason_code"],
            d["note"],
            d["id"],
            d["created_at"],
        )

        definition_rows = (
            await self._session.execute(
                select(agent_definitions)
                .where(
                    agent_definitions.c.tenant_id.is_not(None),
                    agent_definitions.c.agent_type == "producer",
                    agent_definitions.c.status == AgentDefinitionStatus.ACTIVE.value,
                )
                .order_by(agent_definitions.c.created_at)
                .limit(2)
            )
        ).all()
        if len(definition_rows) != 1:
            return None
        requested = definition_rows[0]._mapping
        resolved_id = requested["id"]
        activation = (
            await self._session.execute(
                select(agent_activations).where(agent_activations.c.definition_id == resolved_id)
            )
        ).first()
        if activation is None and requested["platform_template_id"] is not None:
            resolved_id = requested["platform_template_id"]
            template = (
                await self._session.execute(
                    select(agent_definitions).where(
                        agent_definitions.c.id == resolved_id,
                        agent_definitions.c.status == AgentDefinitionStatus.ACTIVE.value,
                    )
                )
            ).first()
            if template is None:
                return None
            activation = (
                await self._session.execute(
                    select(agent_activations).where(
                        agent_activations.c.definition_id == resolved_id
                    )
                )
            ).first()
        if activation is None:
            return None
        version_row = (
            await self._session.execute(
                select(agent_versions).where(
                    agent_versions.c.id == activation._mapping["active_version_id"]
                )
            )
        ).first()
        product_row = (
            await self._session.execute(
                select(product_knowledge_snapshots)
                .where(
                    product_knowledge_snapshots.c.product_id == c["product_id"],
                    product_knowledge_snapshots.c.schema_version == 2,
                )
                .order_by(
                    product_knowledge_snapshots.c.created_at.desc(),
                    product_knowledge_snapshots.c.id.desc(),
                )
                .limit(1)
            )
        ).first()
        research_row = (
            await self._session.execute(
                select(research_snapshots)
                .where(research_snapshots.c.product_id == c["product_id"])
                .order_by(research_snapshots.c.created_at.desc(), research_snapshots.c.id.desc())
                .limit(1)
            )
        ).first()
        if version_row is None or product_row is None or research_row is None:
            return None
        p = product_row._mapping
        research = _snapshot(research_row)
        if (
            p["id"] != concept_set.product_snapshot_id
            or p["digest"] != concept_set.product_snapshot_digest
            or research.id != concept_set.research_snapshot_id
            or research.semantic_digest != concept_set.research_snapshot_digest
            or await self.snapshot_freshness(research) != "current"
        ):
            return None
        product = ProductKnowledgeSnapshot(
            id=p["id"],
            tenant_id=p["tenant_id"],
            product_id=p["product_id"],
            schema_version=p["schema_version"],
            source_revision=p["source_revision"],
            content=p["content"],
            digest=p["digest"],
            created_by=p["created_by"],
            created_at=p["created_at"],
        )
        asset_rows = (
            await self._session.execute(
                select(assets).where(assets.c.product_id == c["product_id"])
            )
        ).all()
        manifest = tuple(
            {
                "asset_id": str(row._mapping["id"]),
                "kind": row._mapping["kind"],
                "role": row._mapping["role"],
                "status": row._mapping["status"],
                "rights_status": row._mapping["rights_status"],
                "allowed_uses": list(row._mapping["allowed_uses"]),
                "digest": row._mapping["digest"],
            }
            for row in asset_rows
            if row._mapping["digest"] is not None
        )
        v = version_row._mapping
        producer = ResolvedResearcher(
            requested["id"],
            resolved_id,
            v["id"],
            v["version_number"],
            v["configuration_digest"],
            _configuration(v),
        )
        return ProducerPreparation(
            producer,
            ApprovedCreativeConcept(concept, concept_set, decision),
            product,
            research,
            manifest,
        )

    async def get_by_idempotency(self, idempotency_key: str) -> AgentRun | None:
        row = (
            await self._session.execute(
                select(agent_runs).where(agent_runs.c.idempotency_key == idempotency_key)
            )
        ).first()
        return _run(row) if row else None

    async def active_for_product(self, product_id: UUID, definition_id: UUID) -> AgentRun | None:
        row = (
            await self._session.execute(
                select(agent_runs)
                .where(
                    agent_runs.c.product_id == product_id,
                    agent_runs.c.requested_agent_definition_id == definition_id,
                    agent_runs.c.status.in_(["PENDING", "RUNNING"]),
                )
                .limit(1)
            )
        ).first()
        return _run(row) if row else None

    async def add(self, run: AgentRun) -> bool:
        result = await self._session.execute(
            pg_insert(agent_runs)
            .values(**_run_values(run))
            .on_conflict_do_nothing()
            .returning(agent_runs.c.id)
        )
        return result.scalar_one_or_none() is not None

    async def get(self, run_id: UUID, *, for_update: bool = False) -> AgentRun | None:
        query = select(agent_runs).where(agent_runs.c.id == run_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._session.execute(query)).first()
        return _run(row) if row else None

    async def list_for_product(self, product_id: UUID) -> tuple[AgentRun, ...]:
        rows = (
            await self._session.execute(
                select(agent_runs)
                .where(agent_runs.c.product_id == product_id)
                .order_by(agent_runs.c.created_at.desc())
            )
        ).all()
        return tuple(_run(row) for row in rows)

    async def lock_period_budgets(
        self, definition_id: UUID, period_starts: tuple[datetime, ...]
    ) -> None:
        for period_start in sorted(set(period_starts)):
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"{definition_id}:{period_start.isoformat()}"},
            )

    async def reserve_period_budget(
        self,
        *,
        run_id: UUID,
        definition_id: UUID,
        period_start: datetime,
        max_runs: int | None,
        max_cost: Decimal,
        reserve_cost: Decimal,
        currency: str,
    ) -> None:
        await self.lock_period_budgets(definition_id, (period_start,))
        statement = (
            pg_insert(agent_budget_usage)
            .values(
                tenant_id=cast(
                    UUID,
                    (
                        await self._session.execute(
                            text(
                                "SELECT nullif(current_setting("
                                "'app.current_tenant_id', true), '')::uuid"
                            )
                        )
                    ).scalar_one(),
                ),
                agent_definition_id=definition_id,
                period_start=period_start,
                currency=currency,
                reserved_runs=0,
                reserved_cost=Decimal("0"),
                actual_cost=Decimal("0"),
                unknown_cost=Decimal("0"),
                updated_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(statement)
        row = (
            await self._session.execute(
                select(agent_budget_usage)
                .where(
                    agent_budget_usage.c.agent_definition_id == definition_id,
                    agent_budget_usage.c.period_start == period_start,
                )
                .with_for_update()
            )
        ).one()
        d = row._mapping
        if (
            d["currency"] != currency
            or (max_runs is not None and d["reserved_runs"] + 1 > max_runs)
            or d["actual_cost"] + d["reserved_cost"] + d["unknown_cost"] + reserve_cost > max_cost
        ):
            raise AgentPeriodBudgetExceeded("period budget is exhausted")
        await self._session.execute(
            update(agent_budget_usage)
            .where(
                agent_budget_usage.c.agent_definition_id == definition_id,
                agent_budget_usage.c.period_start == period_start,
            )
            .values(
                reserved_runs=d["reserved_runs"] + 1,
                reserved_cost=d["reserved_cost"] + reserve_cost,
                updated_at=datetime.now(UTC),
            )
        )

    async def claim(
        self,
        run_id: UUID,
        workload_id: str,
        route: ModelRoute,
        lease_expires_at: datetime,
    ) -> tuple[AgentRun, ModelAttempt] | None:
        now = datetime.now(UTC)
        result = await self._session.execute(
            update(agent_runs)
            .where(
                agent_runs.c.id == run_id,
                agent_runs.c.status == AgentRunStatus.PENDING.value,
                select(agent_definitions.c.status)
                .where(agent_definitions.c.id == agent_runs.c.requested_agent_definition_id)
                .scalar_subquery()
                == AgentDefinitionStatus.ACTIVE.value,
                select(agent_definitions.c.status)
                .where(agent_definitions.c.id == agent_runs.c.resolved_agent_definition_id)
                .scalar_subquery()
                == AgentDefinitionStatus.ACTIVE.value,
            )
            .values(
                status=AgentRunStatus.RUNNING.value,
                started_at=now,
                executed_by_workload_id=workload_id,
                resolved_provider=route.provider,
                resolved_model=route.model,
                resolved_model_route_version=route.route_version,
                pricing_version=route.pricing.version,
                reasoning_effort=route.reasoning_effort,
                max_output_tokens=route.max_output_tokens,
                model_call_count=1,
            )
            .returning(agent_runs)
        )
        row = result.first()
        if row is None:
            return None
        run = _run(row)
        attempt = ModelAttempt(
            tenant_id=run.tenant_id,
            agent_run_id=run.id,
            attempt_number=1,
            workload_id=workload_id,
            model_route_version=route.route_version,
            pricing_version=route.pricing.version,
            provider=route.provider,
            model=route.model,
            claimed_at=now,
            lease_expires_at=lease_expires_at,
        )
        await self._session.execute(
            insert(model_attempts).values(
                id=attempt.id,
                tenant_id=attempt.tenant_id,
                agent_run_id=attempt.agent_run_id,
                attempt_number=attempt.attempt_number,
                workload_id=attempt.workload_id,
                model_route_version=attempt.model_route_version,
                pricing_version=attempt.pricing_version,
                provider=attempt.provider,
                model=attempt.model,
                status=attempt.status.value,
                claimed_at=attempt.claimed_at,
                lease_expires_at=attempt.lease_expires_at,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                estimated_cost=Decimal("0"),
                unknown_cost=Decimal("0"),
            )
        )
        return run, attempt

    async def mark_provider_started(
        self, run_id: UUID, attempt_id: UUID, workload_id: str
    ) -> ModelAttempt:
        now = datetime.now(UTC)
        row = (
            await self._session.execute(
                update(model_attempts)
                .where(
                    model_attempts.c.id == attempt_id,
                    model_attempts.c.agent_run_id == run_id,
                    model_attempts.c.workload_id == workload_id,
                    model_attempts.c.status == ModelAttemptStatus.CLAIMED.value,
                    select(agent_runs.c.status).where(agent_runs.c.id == run_id).scalar_subquery()
                    == AgentRunStatus.RUNNING.value,
                )
                .values(
                    status=ModelAttemptStatus.PROVIDER_STARTED.value,
                    provider_started_at=now,
                )
                .returning(model_attempts)
            )
        ).first()
        if row is None:
            raise AgentRunRecoveryConflict("model attempt ownership is no longer valid")
        return _attempt(row)

    async def record_provider_response(
        self,
        run_id: UUID,
        attempt_id: UUID,
        workload_id: str,
        result: ModelInvocationResult,
        cost: Decimal,
    ) -> ModelAttempt:
        now = datetime.now(UTC)
        row = (
            await self._session.execute(
                update(model_attempts)
                .where(
                    model_attempts.c.id == attempt_id,
                    model_attempts.c.agent_run_id == run_id,
                    model_attempts.c.workload_id == workload_id,
                    model_attempts.c.status == ModelAttemptStatus.PROVIDER_STARTED.value,
                    select(agent_runs.c.status).where(agent_runs.c.id == run_id).scalar_subquery()
                    == AgentRunStatus.RUNNING.value,
                )
                .values(
                    status=ModelAttemptStatus.RESPONSE_RECORDED.value,
                    response_recorded_at=now,
                    provider_response_id=result.provider_response_id,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    total_tokens=result.usage.total_tokens,
                    estimated_cost=cost,
                )
                .returning(model_attempts)
            )
        ).first()
        if row is None:
            raise AgentRunRecoveryConflict("late provider response cannot be recorded")
        return _attempt(row)

    async def mark_attempt_unknown(
        self, run_id: UUID, attempt_id: UUID, workload_id: str, failure_code: str
    ) -> ModelAttempt:
        now = datetime.now(UTC)
        reserved = (
            select(agent_runs.c.reserved_cost).where(agent_runs.c.id == run_id).scalar_subquery()
        )
        row = (
            await self._session.execute(
                update(model_attempts)
                .where(
                    model_attempts.c.id == attempt_id,
                    model_attempts.c.agent_run_id == run_id,
                    model_attempts.c.workload_id == workload_id,
                    model_attempts.c.status == ModelAttemptStatus.PROVIDER_STARTED.value,
                    select(agent_runs.c.status).where(agent_runs.c.id == run_id).scalar_subquery()
                    == AgentRunStatus.RUNNING.value,
                )
                .values(
                    status=ModelAttemptStatus.UNKNOWN.value,
                    finished_at=now,
                    unknown_cost=reserved,
                    failure_code=failure_code,
                )
                .returning(model_attempts)
            )
        ).first()
        if row is None:
            raise AgentRunRecoveryConflict("model attempt ownership is no longer valid")
        return _attempt(row)

    async def resolve_context(self, run: AgentRun) -> ModelContext:
        version_row = (
            await self._session.execute(
                select(agent_versions).where(agent_versions.c.id == run.agent_version_id)
            )
        ).one()
        v = version_row._mapping
        if v["configuration_digest"] != run.agent_configuration_digest:
            raise ValueError("bound AgentVersion digest mismatch")
        snapshot_row = (
            await self._session.execute(
                select(product_knowledge_snapshots).where(
                    product_knowledge_snapshots.c.id == run.product_snapshot_id
                )
            )
        ).one()
        s = snapshot_row._mapping
        if s["digest"] != run.product_snapshot_digest:
            raise ValueError("bound Product snapshot digest mismatch")
        if run.agent_type == "supervisor":
            supervisor_manifest_ref = next(
                (
                    item
                    for item in run.input_context_refs
                    if item.get("kind") == "supervisor_context_manifest"
                ),
                None,
            )
            if supervisor_manifest_ref is None:
                raise ValueError("Supervisor run context reference is incomplete")
            supervisor_manifest_row = (
                (
                    await self._session.execute(
                        select(supervisor_context_manifests).where(
                            supervisor_context_manifests.c.id
                            == UUID(str(supervisor_manifest_ref["id"]))
                        )
                    )
                )
                .mappings()
                .one()
            )
            supervisor_manifest = _supervisor_manifest(
                cast(Mapping[str, object], supervisor_manifest_row)
            )
            if (
                supervisor_manifest.semantic_digest != supervisor_manifest_ref["digest"]
                or supervisor_manifest.semantic_digest != run.input_context_digest
            ):
                raise ValueError("bound Supervisor manifest digest mismatch")
            product = ProductKnowledgeSnapshot(
                id=s["id"],
                tenant_id=s["tenant_id"],
                product_id=s["product_id"],
                schema_version=s["schema_version"],
                source_revision=s["source_revision"],
                content=s["content"],
                digest=s["digest"],
                created_by=s["created_by"],
                created_at=s["created_at"],
            )
            supervisor_preparation = SupervisorPreparation(
                ResolvedResearcher(
                    run.requested_agent_definition_id,
                    run.resolved_agent_definition_id,
                    run.agent_version_id,
                    run.agent_version_number,
                    run.agent_configuration_digest,
                    _configuration(v),
                ),
                product,
                supervisor_manifest,
                {
                    "cycle": {
                        "id": str(supervisor_manifest.cycle_id),
                        "version": supervisor_manifest.cycle_version,
                        "stage": supervisor_manifest.current_stage.value,
                        "product_snapshot_id": str(supervisor_manifest.product_snapshot_id),
                        "product_snapshot_digest": supervisor_manifest.product_snapshot_digest,
                        "artifacts": supervisor_manifest.artifacts.as_dict(),
                    },
                    "readiness": {
                        "state": supervisor_manifest.readiness.state.value,
                        "requirements": [
                            {
                                "key": item.key,
                                "state": item.state.value,
                                "message": item.message,
                                "required": item.required,
                                "resource_ref": item.resource_ref,
                            }
                            for item in supervisor_manifest.readiness.requirements
                        ],
                        "allowed_actions": [
                            item.value for item in supervisor_manifest.readiness.allowed_actions
                        ],
                    },
                    "pending_approvals": list(supervisor_manifest.pending_approvals),
                    "provider_mode": supervisor_manifest.provider_mode,
                },
            )
            return build_supervisor_model_context(supervisor_preparation)
        if run.agent_type == "commerce_operations":
            manifest_ref = next(
                (
                    item
                    for item in run.input_context_refs
                    if item.get("kind") == "commerce_context_manifest"
                ),
                None,
            )
            if manifest_ref is None:
                raise ValueError("Commerce run context reference is incomplete")
            row = (
                await self._session.execute(
                    select(commerce_context_manifests).where(
                        commerce_context_manifests.c.id == UUID(str(manifest_ref["id"]))
                    )
                )
            ).one()
            m = row._mapping
            if (
                m["semantic_digest"] != manifest_ref["digest"]
                or m["semantic_digest"] != run.input_context_digest
            ):
                raise ValueError("bound Commerce manifest digest mismatch")
            commerce_manifest = CommerceOperationsContextManifest(
                m["tenant_id"],
                m["product_id"],
                m["product_snapshot_ref"],
                m["mapping_ref"],
                tuple(m["observation_refs"]),
                tuple(m["deterministic_exceptions"]),
                m["semantic_digest"],
                m["id"],
                m["schema_version"],
                m["created_at"],
            )
            commerce_inventory_ids = [
                UUID(str(item["id"]))
                for item in commerce_manifest.observation_refs
                if item["kind"] == "inventory_observation"
            ]
            commerce_order_ids = [
                UUID(str(item["id"]))
                for item in commerce_manifest.observation_refs
                if item["kind"] == "order_observation"
            ]
            commerce_inventory_rows = (
                (
                    await self._session.execute(
                        select(commerce_inventory_observations).where(
                            commerce_inventory_observations.c.id.in_(commerce_inventory_ids)
                        )
                    )
                ).all()
                if commerce_inventory_ids
                else []
            )
            commerce_order_rows = (
                (
                    await self._session.execute(
                        select(commerce_order_observations).where(
                            commerce_order_observations.c.id.in_(commerce_order_ids)
                        )
                    )
                ).all()
                if commerce_order_ids
                else []
            )
            commerce_inventories = tuple(
                InventoryObservation(
                    commerce_inventory_data["tenant_id"],
                    commerce_inventory_data["connection_id"],
                    commerce_inventory_data["external_product_id"],
                    commerce_inventory_data["external_variant_id"],
                    commerce_inventory_data["available_quantity"],
                    commerce_inventory_data["captured_at"],
                    commerce_inventory_data["source_digest"],
                    commerce_inventory_data["sku"],
                    commerce_inventory_data["location_id"],
                    commerce_inventory_data["committed_quantity"],
                    commerce_inventory_data["on_hand_quantity"],
                    commerce_inventory_data["id"],
                    commerce_inventory_data["provider"],
                    commerce_inventory_data["provider_version"],
                    commerce_inventory_data["schema_version"],
                )
                for commerce_inventory_data in (
                    commerce_row._mapping for commerce_row in commerce_inventory_rows
                )
            )
            commerce_orders = []
            for commerce_order_row in commerce_order_rows:
                commerce_order_data = commerce_order_row._mapping
                commerce_lines = tuple(
                    OrderLineObservation(
                        str(v["external_line_id"]),
                        str(v["external_product_id"]),
                        v.get("external_variant_id"),
                        v.get("sku"),
                        int(v["quantity"]),
                        Decimal(str(v["unit_price"])),
                        str(v["currency"]),
                        UUID(v["mapped_product_id"]) if v.get("mapped_product_id") else None,
                    )
                    for v in commerce_order_data["lines"]
                )
                commerce_orders.append(
                    OrderObservation(
                        commerce_order_data["tenant_id"],
                        commerce_order_data["connection_id"],
                        commerce_order_data["external_order_id"],
                        commerce_order_data["order_reference"],
                        commerce_order_data["currency"],
                        Decimal(commerce_order_data["subtotal"]),
                        Decimal(commerce_order_data["discount_total"]),
                        Decimal(commerce_order_data["tax_total"]),
                        Decimal(commerce_order_data["shipping_total"]),
                        Decimal(commerce_order_data["total"]),
                        PaymentState(commerce_order_data["financial_status"]),
                        FulfillmentState(commerce_order_data["fulfillment_status"]),
                        commerce_order_data["created_at_external"],
                        commerce_order_data["updated_at_external"],
                        commerce_order_data["captured_at"],
                        commerce_lines,
                        commerce_order_data["source_digest"],
                        commerce_order_data["attribution_code"],
                        commerce_order_data["id"],
                        commerce_order_data["schema_version"],
                    )
                )
            commerce_mapping_result = await self._session.execute(
                select(commerce_product_mappings).where(
                    commerce_product_mappings.c.id == UUID(str(commerce_manifest.mapping_ref["id"]))
                )
            )
            commerce_mapping_row = commerce_mapping_result.first()
            if commerce_mapping_row is None:
                raise ValueError("bound Commerce mapping is missing")
            commerce_payload = {
                "manifest": {
                    "product_snapshot_ref": dict(commerce_manifest.product_snapshot_ref),
                    "mapping_ref": dict(commerce_manifest.mapping_ref),
                    "observation_refs": [dict(v) for v in commerce_manifest.observation_refs],
                },
                "inventory": [
                    {
                        "observation_id": str(v.id),
                        "external_variant_id": v.external_variant_id,
                        "available_quantity": v.available_quantity,
                        "source_digest": v.source_digest,
                    }
                    for v in commerce_inventories
                ],
                "orders": [
                    {
                        "observation_id": str(v.id),
                        "external_order_id": v.external_order_id,
                        "currency": v.currency,
                        "total": str(v.total),
                        "payment_state": v.financial_status.value,
                        "fulfillment_state": v.fulfillment_status.value,
                        "source_digest": v.source_digest,
                    }
                    for v in commerce_orders
                ],
                "deterministic_exceptions": [
                    dict(v) for v in commerce_manifest.deterministic_exceptions
                ],
                "deterministic_limitations": [
                    "Fake Store observations; not real commerce data.",
                    "AI proposals require deterministic validation and human approval.",
                ],
            }
            commerce_preparation = CommercePreparation(
                ResolvedResearcher(
                    run.requested_agent_definition_id,
                    run.resolved_agent_definition_id,
                    run.agent_version_id,
                    run.agent_version_number,
                    run.agent_configuration_digest,
                    _configuration(v),
                ),
                ProductKnowledgeSnapshot(
                    id=s["id"],
                    tenant_id=s["tenant_id"],
                    product_id=s["product_id"],
                    schema_version=s["schema_version"],
                    source_revision=s["source_revision"],
                    content=s["content"],
                    digest=s["digest"],
                    created_by=s["created_by"],
                    created_at=s["created_at"],
                ),
                commerce_manifest,
                commerce_mapping_row._mapping["connection_id"],
                commerce_inventories,
                tuple(commerce_orders),
                commerce_payload,
            )
            return build_commerce_model_context(commerce_preparation)
        if run.agent_type == "intelligence":
            manifest_ref = next(
                (
                    item
                    for item in run.input_context_refs
                    if item.get("kind") == "intelligence_context_manifest"
                ),
                None,
            )
            comparison_ref = next(
                (
                    item
                    for item in run.input_context_refs
                    if item.get("kind") == "performance_comparisons"
                ),
                None,
            )
            if manifest_ref is None or comparison_ref is None:
                raise ValueError("Intelligence run context references are incomplete")
            manifest_row = (
                await self._session.execute(
                    select(context_manifests).where(
                        context_manifests.c.id == UUID(str(manifest_ref["id"]))
                    )
                )
            ).one()
            mr = manifest_row._mapping
            if (
                mr["semantic_digest"] != manifest_ref["digest"]
                or mr["semantic_digest"] != run.input_context_digest
            ):
                raise ValueError("bound Intelligence manifest digest mismatch")
            raw = mr["manifest"]

            def ref(value: Mapping[str, object] | None) -> CanonicalRef | None:
                return (
                    CanonicalRef(
                        str(value["kind"]),
                        UUID(str(value["id"])),
                        str(value["digest"]) if value.get("digest") else None,
                    )
                    if value
                    else None
                )

            manifest = IntelligenceContextManifest(
                mr["tenant_id"],
                mr["product_id"],
                cast(CanonicalRef, ref(raw["product_snapshot"])),
                ref(raw.get("research_snapshot")),
                tuple(cast(CanonicalRef, ref(item)) for item in raw["creative_concepts"]),
                tuple(cast(CanonicalRef, ref(item)) for item in raw["final_creatives"]),
                tuple(cast(CanonicalRef, ref(item)) for item in raw["publications"]),
                tuple(cast(CanonicalRef, ref(item)) for item in raw["performance_snapshots"]),
                tuple(cast(CanonicalRef, ref(item)) for item in raw["attribution_results"]),
                tuple(UUID(item) for item in raw["comparison_ids"]),
                tuple(UUID(item) for item in raw["feature_snapshot_ids"]),
                DataTrustLevel(mr["data_trust_level"]),
                mr["semantic_digest"],
                mr["id"],
                raw["comparison_policy_version"],
                raw["feature_extraction_version"],
                mr["created_at"],
            )
            raw_comparison_ids = comparison_ref.get("ids", ())
            comparison_id_values = (
                raw_comparison_ids if isinstance(raw_comparison_ids, (list, tuple)) else ()
            )
            comparison_ids = [UUID(str(item)) for item in comparison_id_values]
            comparison_rows = (
                (
                    await self._session.execute(
                        select(performance_comparisons).where(
                            performance_comparisons.c.id.in_(comparison_ids)
                        )
                    )
                ).all()
                if comparison_ids
                else []
            )
            by_id: dict[UUID, PerformanceComparison] = {}
            for row in comparison_rows:
                item = row._mapping
                value = PerformanceComparison(
                    item["tenant_id"],
                    item["product_id"],
                    item["publication_id"],
                    item["final_creative_id"],
                    item["comparison_window"],
                    item["metric_key"],
                    Decimal(item["observed_value"]),
                    Decimal(item["baseline_value"]),
                    Decimal(item["absolute_delta"]),
                    Decimal(item["relative_delta"]) if item["relative_delta"] is not None else None,
                    item["sample_size"],
                    tuple(item["baseline_publication_ids"]),
                    tuple(item["source_snapshot_ids"]),
                    DataTrustLevel(item["data_trust_level"]),
                    item["semantic_digest"],
                    item["id"],
                    item["comparability_policy_version"],
                    item["calculation_version"],
                    item["created_at"],
                )
                by_id[value.id] = value
            comparisons = tuple(by_id[item] for item in comparison_ids)
            snapshot_ids = [item.id for item in manifest.performance_snapshots]
            observed_rows = (
                await self._session.execute(
                    select(performance_snapshots).where(
                        performance_snapshots.c.id.in_(snapshot_ids)
                    )
                )
            ).all()
            observed = [
                {
                    "performance_snapshot_id": str(item._mapping["id"]),
                    "publication_id": str(item._mapping["publication_id"]),
                    "metrics": item._mapping["latest_metrics"],
                    "derived_metrics": item._mapping["derived_metrics"],
                    "attributed_conversions": item._mapping["attributed_conversions"],
                    "attributed_revenue": item._mapping["attributed_revenue"],
                }
                for item in observed_rows
            ]
            feature_rows = (
                await self._session.execute(
                    select(creative_feature_snapshots).where(
                        creative_feature_snapshots.c.id.in_(list(manifest.feature_snapshot_ids))
                    )
                )
            ).all()
            payload: dict[str, object] = {
                "manifest": manifest.semantic_content(),
                "observed_facts": observed,
                "creative_features": [
                    {
                        "id": str(item._mapping["id"]),
                        "features": item._mapping["features"],
                        "digest": item._mapping["semantic_digest"],
                    }
                    for item in feature_rows
                ],
                "performance_comparisons": [
                    {"id": str(item.id), **item.semantic_content()} for item in comparisons
                ],
                "deterministic_limitations": [
                    *(
                        ["Synthetic demo data; not real market evidence."]
                        if manifest.data_trust_level is DataTrustLevel.SYNTHETIC
                        else []
                    ),
                    "Observational evidence does not establish causality.",
                    *(
                        [
                            "Insufficient comparable matched-window sample; no baseline "
                            "comparison is available."
                        ]
                        if not comparisons
                        else []
                    ),
                    "No advertising spend facts are available; ROAS and spend recommendations "
                    "are prohibited.",
                ],
            }
            preparation = IntelligencePreparation(
                ResolvedResearcher(
                    run.requested_agent_definition_id,
                    run.resolved_agent_definition_id,
                    run.agent_version_id,
                    run.agent_version_number,
                    run.agent_configuration_digest,
                    _configuration(v),
                ),
                ProductKnowledgeSnapshot(
                    id=s["id"],
                    tenant_id=s["tenant_id"],
                    product_id=s["product_id"],
                    schema_version=s["schema_version"],
                    source_revision=s["source_revision"],
                    content=s["content"],
                    digest=s["digest"],
                    created_by=s["created_by"],
                    created_at=s["created_at"],
                ),
                manifest,
                comparisons,
                payload,
            )
            return build_intelligence_model_context(preparation)
        if run.agent_type == "producer":
            concept_ref = next(
                (item for item in run.input_context_refs if item.get("kind") == "approved_concept"),
                None,
            )
            research_ref = next(
                (
                    item
                    for item in run.input_context_refs
                    if item.get("kind") == "research_snapshot"
                ),
                None,
            )
            assets_ref = next(
                (item for item in run.input_context_refs if item.get("kind") == "selected_assets"),
                None,
            )
            request_ref = next(
                (
                    item
                    for item in run.input_context_refs
                    if item.get("kind") == "production_request"
                ),
                None,
            )
            if None in (concept_ref, research_ref, assets_ref, request_ref):
                raise ValueError("Producer run context references are incomplete")
            assert concept_ref is not None and research_ref is not None
            assert assets_ref is not None and request_ref is not None
            concept_row = (
                await self._session.execute(
                    select(concepts).where(concepts.c.id == UUID(str(concept_ref["id"])))
                )
            ).one()
            c = concept_row._mapping
            set_row = (
                await self._session.execute(
                    select(concept_sets).where(
                        concept_sets.c.id == UUID(str(concept_ref["concept_set_id"]))
                    )
                )
            ).one()
            cs = set_row._mapping
            all_concept_rows = (
                await self._session.execute(
                    select(concepts)
                    .where(concepts.c.concept_set_id == cs["id"])
                    .order_by(concepts.c.ordinal)
                )
            ).all()
            concept_values = tuple(
                CreativeConcept(
                    row._mapping["tenant_id"],
                    row._mapping["product_id"],
                    row._mapping["concept_set_id"],
                    row._mapping["concept_key"],
                    row._mapping["ordinal"],
                    row._mapping["concept_payload"],
                    row._mapping["semantic_digest"],
                    row._mapping["id"],
                    row._mapping["created_at"],
                )
                for row in all_concept_rows
            )
            concept = next(item for item in concept_values if item.id == c["id"])
            concept_set = CreativeConceptSet(
                cs["tenant_id"],
                cs["product_id"],
                cs["agent_run_id"],
                cs["product_snapshot_id"],
                cs["product_snapshot_digest"],
                cs["research_snapshot_id"],
                cs["research_snapshot_digest"],
                cs["input_context_digest"],
                concept_values,
                cs["semantic_digest"],
                id=cs["id"],
                schema_version=cs["schema_version"],
                created_at=cs["created_at"],
                experiment_proposal_id=cs["experiment_proposal_id"],
                experiment_proposal_digest=cs["experiment_proposal_digest"],
            )
            decision_row = (
                await self._session.execute(
                    select(concept_decisions).where(
                        concept_decisions.c.id == UUID(str(concept_ref["creative_decision_id"]))
                    )
                )
            ).one()
            d = decision_row._mapping
            decision = CreativeConceptDecision(
                d["tenant_id"],
                d["product_id"],
                d["concept_id"],
                CreativeDecisionState(d["state"]),
                d["decided_by"],
                d["reason_code"],
                d["note"],
                d["id"],
                d["created_at"],
            )
            research = await self.get_snapshot(UUID(str(research_ref["id"])))
            if (
                concept.semantic_digest != concept_ref["digest"]
                or concept_set.semantic_digest != concept_ref["concept_set_digest"]
                or research is None
                or research.semantic_digest != research_ref["digest"]
            ):
                raise ValueError("bound Producer provenance digest mismatch")
            product = ProductKnowledgeSnapshot(
                id=s["id"],
                tenant_id=s["tenant_id"],
                product_id=s["product_id"],
                schema_version=s["schema_version"],
                source_revision=s["source_revision"],
                content=s["content"],
                digest=s["digest"],
                created_by=s["created_by"],
                created_at=s["created_at"],
            )
            raw_assets = assets_ref.get("assets", ())
            asset_manifest = tuple(raw_assets) if isinstance(raw_assets, (list, tuple)) else ()
            producer_preparation = ProducerPreparation(
                ResolvedResearcher(
                    run.requested_agent_definition_id,
                    run.resolved_agent_definition_id,
                    run.agent_version_id,
                    run.agent_version_number,
                    run.agent_configuration_digest,
                    _configuration(v),
                ),
                ApprovedCreativeConcept(concept, concept_set, decision),
                product,
                research,
                tuple(dict(item) for item in asset_manifest if isinstance(item, Mapping)),
            )
            frozen_context = production_context_from_payload(
                {
                    "concept_id": concept_ref["id"],
                    "concept_digest": concept_ref["digest"],
                    "concept_set_id": concept_ref["concept_set_id"],
                    "concept_set_digest": concept_ref["concept_set_digest"],
                    "product_snapshot_id": run.product_snapshot_id,
                    "product_snapshot_digest": run.product_snapshot_digest,
                    "research_snapshot_id": research_ref["id"],
                    "research_snapshot_digest": research_ref["digest"],
                    "creative_decision_id": concept_ref["creative_decision_id"],
                    "selected_assets": asset_manifest,
                    "production_request": {
                        "target_format": request_ref["target_format"],
                        "aspect_ratio": request_ref["aspect_ratio"],
                    },
                },
                run.input_context_digest,
            )
            planning, model = build_producer_model_context(
                producer_preparation,
                ProductionPlanningRequest(
                    str(request_ref["target_format"]), str(request_ref["aspect_ratio"])
                ),
                frozen_context=frozen_context,
            )
            if planning.context_digest != run.input_context_digest:
                raise ValueError("bound Producer context digest mismatch")
            return model
        if run.agent_type == "creative_strategist":
            research_ref = next(
                (
                    item
                    for item in run.input_context_refs
                    if item.get("kind") == "research_snapshot"
                ),
                None,
            )
            request_ref = next(
                (item for item in run.input_context_refs if item.get("kind") == "strategy_request"),
                None,
            )
            experiment_ref = next(
                (
                    item
                    for item in run.input_context_refs
                    if item.get("kind") == "approved_experiment_proposal"
                ),
                None,
            )
            if research_ref is None or request_ref is None:
                raise ValueError("Creative run context references are incomplete")
            research = await self.get_snapshot(UUID(str(research_ref["id"])))
            if research is None or research.semantic_digest != research_ref["digest"]:
                raise ValueError("bound Research snapshot digest mismatch")
            product = ProductKnowledgeSnapshot(
                id=s["id"],
                tenant_id=s["tenant_id"],
                product_id=s["product_id"],
                schema_version=s["schema_version"],
                source_revision=s["source_revision"],
                content=s["content"],
                digest=s["digest"],
                created_by=s["created_by"],
                created_at=s["created_at"],
            )
            approved_experiment: ApprovedExperimentContext | None = None
            if experiment_ref is not None:
                proposal_row = (
                    await self._session.execute(
                        select(experiment_proposals).where(
                            experiment_proposals.c.id == UUID(str(experiment_ref["id"]))
                        )
                    )
                ).one()
                p = proposal_row._mapping
                if (
                    p["semantic_digest"] != experiment_ref["digest"]
                    or p["report_digest"] != experiment_ref["report_digest"]
                ):
                    raise ValueError("bound ExperimentProposal digest mismatch")
                approved_experiment = ApprovedExperimentContext(
                    p["id"],
                    p["semantic_digest"],
                    p["report_id"],
                    p["report_digest"],
                    p["data_trust_level"],
                    p["hypothesis"],
                    p["primary_variable"],
                    tuple(p["controlled_elements"]),
                    p["target_metric"],
                    p["platform"],
                    p["measurement_window"],
                    p["creative_direction"],
                )
            creative_preparation = CreativePreparation(
                ResolvedResearcher(
                    run.requested_agent_definition_id,
                    run.resolved_agent_definition_id,
                    run.agent_version_id,
                    run.agent_version_number,
                    run.agent_configuration_digest,
                    _configuration(v),
                ),
                product,
                research,
                "frozen",
                100,
                approved_experiment,
            )
            request = CreativeStrategyRequest(
                cast(int, request_ref["concept_count"]),
                ChannelIntent(str(request_ref["channel_intent"])),
            )
            creative, model = build_creative_model_context(creative_preparation, request)
            if creative.context_digest != run.input_context_digest:
                raise ValueError("bound Creative context digest mismatch")
            return model
        blocks: list[EvidenceBlockRef] = []
        for identity in run.selected_evidence:
            evidence_id = UUID(str(identity["evidence_snapshot_id"]))
            evidence_row = (
                await self._session.execute(
                    select(evidence_snapshots, sources.c.display_name)
                    .join(
                        sources,
                        and_(
                            sources.c.id == evidence_snapshots.c.source_id,
                            sources.c.tenant_id == evidence_snapshots.c.tenant_id,
                        ),
                    )
                    .where(evidence_snapshots.c.id == evidence_id)
                )
            ).first()
            snapshot: EvidenceSnapshot | SocialEvidenceSnapshot
            if evidence_row is not None:
                snapshot = _evidence(evidence_row)
                source_id = snapshot.source_id
                source_label = evidence_row._mapping["display_name"]
                source_blocks = snapshot.blocks
            else:
                social_row = (
                    await self._session.execute(
                        select(social_evidence_snapshots, research_targets.c.display_name)
                        .join(
                            research_targets,
                            and_(
                                research_targets.c.id
                                == social_evidence_snapshots.c.research_target_id,
                                research_targets.c.tenant_id
                                == social_evidence_snapshots.c.tenant_id,
                            ),
                        )
                        .where(social_evidence_snapshots.c.id == evidence_id)
                    )
                ).one()
                social_snapshot = _social_evidence(social_row)
                snapshot = social_snapshot
                source_id = social_snapshot.research_target_id
                source_label = social_row._mapping["display_name"]
                source_blocks = social_snapshot.analysis_blocks()
            index = cast(int, identity["block_index"])
            source_block = next((item for item in source_blocks if item.ordinal == index), None)
            if source_block is None:
                raise ValueError("bound evidence block is unavailable")
            expected = canonical_digest(
                {
                    "evidence_snapshot_id": str(snapshot.id),
                    "block_index": index,
                    "kind": source_block.kind.value,
                    "text": source_block.text,
                }
            )
            if expected != identity["block_digest"]:
                raise ValueError("bound evidence block digest mismatch")
            blocks.append(
                EvidenceBlockRef(
                    snapshot.id,
                    source_id,
                    source_label,
                    index,
                    source_block.kind.value,
                    expected,
                    source_block.text,
                    bool(identity["stale"]),
                )
            )
        product = ProductKnowledgeSnapshot(
            id=s["id"],
            tenant_id=s["tenant_id"],
            product_id=s["product_id"],
            schema_version=s["schema_version"],
            source_revision=s["source_revision"],
            content=s["content"],
            digest=s["digest"],
            created_by=s["created_by"],
            created_at=s["created_at"],
        )
        context = ModelContext(
            v["system_instructions"],
            cast(dict[str, object], compact_context(product.content)),
            tuple(blocks),
            run.context_digest,
        )
        expected = canonical_digest(
            {
                "schema_version": 1,
                "agent_configuration_digest": run.agent_configuration_digest,
                "product_snapshot_digest": run.product_snapshot_digest,
                "research_context_digest": run.research_context_digest,
                "evidence_blocks": [item.identity() for item in blocks],
            }
        )
        if expected != run.context_digest:
            raise ValueError("bound context digest mismatch")
        return context

    async def resolve_creative_context(self, run: AgentRun) -> CreativeStrategyContext:
        if run.agent_type != "creative_strategist":
            raise ValueError("AgentRun is not a Creative Strategist run")
        model = await self.resolve_context(run)
        research_ref = next(
            (item for item in run.input_context_refs if item.get("kind") == "research_snapshot"),
            None,
        )
        request_ref = next(
            (item for item in run.input_context_refs if item.get("kind") == "strategy_request"),
            None,
        )
        experiment_ref = next(
            (
                item
                for item in run.input_context_refs
                if item.get("kind") == "approved_experiment_proposal"
            ),
            None,
        )
        if research_ref is None or request_ref is None or model.capability_context is None:
            raise ValueError("Creative strategy context is incomplete")
        research = await self.get_snapshot(UUID(str(research_ref["id"])))
        snapshot_row = (
            await self._session.execute(
                select(product_knowledge_snapshots).where(
                    product_knowledge_snapshots.c.id == run.product_snapshot_id
                )
            )
        ).one()
        s = snapshot_row._mapping
        if research is None:
            raise ValueError("bound ResearchSnapshot is unavailable")
        product_content = dict(s["content"])
        request = CreativeStrategyRequest(
            cast(int, request_ref["concept_count"]),
            ChannelIntent(str(request_ref["channel_intent"])),
        )
        assets = model.capability_context.get("available_assets", ())
        findings = model.capability_context.get("research_findings", ())
        gaps = model.capability_context.get("research_gaps", ())
        asset_items = assets if isinstance(assets, (list, tuple)) else ()
        finding_items = findings if isinstance(findings, (list, tuple)) else ()
        gap_items = gaps if isinstance(gaps, (list, tuple)) else ()
        experiment_value = model.capability_context.get("approved_experiment")
        approved_experiment = (
            ApprovedExperimentContext(
                UUID(str(experiment_value["proposal_id"])),
                str(experiment_value["proposal_digest"]),
                UUID(str(experiment_value["source_intelligence_report_id"])),
                str(experiment_value["source_intelligence_report_digest"]),
                str(experiment_value["data_trust_level"]),
                str(experiment_value["hypothesis"]),
                str(experiment_value["primary_variable"]),
                tuple(str(item) for item in experiment_value["controlled_elements"]),
                str(experiment_value["target_metric"]),
                str(experiment_value["platform"]),
                str(experiment_value["measurement_window"]),
                str(experiment_value["creative_direction"]),
            )
            if experiment_ref is not None and isinstance(experiment_value, Mapping)
            else None
        )
        return CreativeStrategyContext(
            run.product_snapshot_id,
            run.product_snapshot_digest,
            run.product_snapshot_schema_version,
            research.id,
            research.semantic_digest,
            research.agent_run_id,
            product_content,
            tuple(dict(item) for item in finding_items if isinstance(item, dict)),
            tuple(str(item) for item in gap_items),
            tuple(dict(item) for item in asset_items if isinstance(item, dict)),
            product_claim_refs(run.product_snapshot_digest, product_content),
            request,
            run.input_context_digest,
            approved_experiment,
        )

    async def finish_success(
        self,
        run: AgentRun,
        result: ModelInvocationResult,
        snapshot: object,
        cost: Decimal,
        attempt_id: UUID,
        workload_id: str,
    ) -> AgentRun:
        if not isinstance(
            snapshot,
            (
                ResearchSnapshot,
                CreativeConceptSet,
                ProductionPlan,
                IntelligenceResult,
                CommerceAgentResult,
                SupervisorReport,
            ),
        ):
            raise ValueError("unsupported capability result type")
        now = datetime.now(UTC)
        if isinstance(snapshot, SupervisorReport):
            result_ref = f"supervisor-report://{snapshot.id}"
        elif isinstance(snapshot, CommerceAgentResult):
            result_ref = f"commerce-report://{snapshot.report.id}"
        elif isinstance(snapshot, IntelligenceResult):
            result_ref = f"intelligence-report://{snapshot.report.id}"
        elif isinstance(snapshot, ProductionPlan):
            result_ref = f"production-plan://{snapshot.id}"
        elif isinstance(snapshot, CreativeConceptSet):
            result_ref = f"creative-concept-set://{snapshot.id}"
        else:
            result_ref = f"research-snapshot://{snapshot.id}"
        owned_run = await self._session.scalar(
            select(agent_runs.c.id)
            .where(
                agent_runs.c.id == run.id,
                agent_runs.c.status == AgentRunStatus.RUNNING.value,
                agent_runs.c.executed_by_workload_id == workload_id,
            )
            .with_for_update()
        )
        if owned_run is None:
            raise AgentRunRecoveryConflict("late worker cannot persist AgentRun success")
        attempt_row = (
            await self._session.execute(
                update(model_attempts)
                .where(
                    model_attempts.c.id == attempt_id,
                    model_attempts.c.agent_run_id == run.id,
                    model_attempts.c.workload_id == workload_id,
                    model_attempts.c.status == ModelAttemptStatus.RESPONSE_RECORDED.value,
                )
                .values(status=ModelAttemptStatus.SUCCEEDED.value, finished_at=now)
                .returning(model_attempts.c.id)
            )
        ).scalar_one_or_none()
        if attempt_row is None:
            raise AgentRunRecoveryConflict("stale model attempt cannot persist success")
        if isinstance(snapshot, SupervisorReport):
            await self._session.execute(
                insert(supervisor_reports).values(
                    id=snapshot.id,
                    tenant_id=snapshot.tenant_id,
                    product_id=snapshot.product_id,
                    cycle_id=snapshot.cycle_id,
                    context_manifest_id=snapshot.context_manifest_id,
                    context_manifest_digest=snapshot.context_manifest_digest,
                    agent_run_id=snapshot.agent_run_id,
                    summary=snapshot.summary,
                    current_stage_explanation=snapshot.current_stage_explanation,
                    blockers=list(snapshot.blockers),
                    attention_items=list(snapshot.attention_items),
                    suggested_next_actions=[item.value for item in snapshot.suggested_next_actions],
                    completion_summary=snapshot.completion_summary,
                    semantic_digest=snapshot.semantic_digest,
                    schema_version=snapshot.schema_version,
                    created_at=snapshot.created_at,
                )
            )
        elif isinstance(snapshot, CommerceAgentResult):
            commerce_report = snapshot.report
            await self._session.execute(
                insert(commerce_reports).values(
                    id=commerce_report.id,
                    tenant_id=commerce_report.tenant_id,
                    product_id=commerce_report.product_id,
                    agent_run_id=commerce_report.agent_run_id,
                    agent_version_id=commerce_report.agent_version_id,
                    context_manifest_id=commerce_report.context_manifest_id,
                    context_manifest_digest=commerce_report.context_manifest_digest,
                    summary=commerce_report.summary,
                    inventory_exceptions=[dict(v) for v in commerce_report.inventory_exceptions],
                    order_exceptions=[dict(v) for v in commerce_report.order_exceptions],
                    limitations=list(commerce_report.limitations),
                    semantic_digest=commerce_report.semantic_digest,
                    schema_version=commerce_report.schema_version,
                    created_at=commerce_report.created_at,
                )
            )
            if snapshot.proposals:
                await self._session.execute(
                    insert(commerce_action_proposals),
                    [
                        {
                            "id": item.id,
                            "tenant_id": item.tenant_id,
                            "connection_id": item.connection_id,
                            "product_id": item.product_id,
                            "action_type": item.action_type.value,
                            "external_product_id": item.external_product_id,
                            "external_variant_id": item.external_variant_id,
                            "external_order_id": item.external_order_id,
                            "exact_quantity": item.exact_quantity,
                            "exact_amount": item.exact_amount,
                            "currency": item.currency,
                            "reason": item.reason,
                            "evidence_refs": [dict(v) for v in item.evidence_refs],
                            "semantic_digest": item.semantic_digest,
                            "schema_version": item.schema_version,
                            "created_at": item.created_at,
                        }
                        for item in snapshot.proposals
                    ],
                )
        elif isinstance(snapshot, IntelligenceResult):
            report = snapshot.report
            await self._session.execute(
                insert(intelligence_reports).values(
                    id=report.id,
                    tenant_id=report.tenant_id,
                    product_id=report.product_id,
                    agent_run_id=report.agent_run_id,
                    agent_version_id=report.agent_version_id,
                    context_manifest_id=report.context_manifest_id,
                    context_manifest_digest=report.context_manifest_digest,
                    schema_version=report.schema_version,
                    data_trust_level=report.data_trust_level.value,
                    summary=report.summary,
                    observations=[dict(item) for item in report.observations],
                    comparative_findings=[dict(item) for item in report.comparative_findings],
                    limitations=list(report.limitations),
                    semantic_digest=report.semantic_digest,
                    created_at=report.created_at,
                )
            )
            if snapshot.candidates:
                await self._session.execute(
                    insert(insight_candidates),
                    [
                        {
                            "id": item.id,
                            "tenant_id": item.tenant_id,
                            "product_id": item.product_id,
                            "report_id": item.report_id,
                            "statement": item.statement,
                            "evidence_refs": [ref.primitive() for ref in item.evidence_refs],
                            "sample_size": item.sample_size,
                            "metric": item.metric,
                            "baseline": item.baseline,
                            "observed_delta": item.observed_delta,
                            "confidence": item.confidence.value,
                            "scope": dict(item.scope),
                            "limitations": list(item.limitations),
                            "data_trust_level": item.data_trust_level.value,
                            "status": item.status.value,
                            "semantic_digest": item.semantic_digest,
                            "created_at": item.created_at,
                            "valid_until": item.valid_until,
                        }
                        for item in snapshot.candidates
                    ],
                )
            if snapshot.proposals:
                await self._session.execute(
                    insert(experiment_proposals),
                    [
                        {
                            "id": item.id,
                            "tenant_id": item.tenant_id,
                            "product_id": item.product_id,
                            "report_id": item.source_intelligence_report_id,
                            "report_digest": item.source_intelligence_report_digest,
                            "candidate_ids": list(item.source_insight_candidate_ids),
                            "hypothesis": item.hypothesis,
                            "primary_variable": item.primary_variable,
                            "controlled_elements": list(item.controlled_elements),
                            "target_metric": item.target_metric,
                            "platform": item.platform,
                            "measurement_window": item.recommended_measurement_window,
                            "creative_direction": item.creative_direction,
                            "rationale": item.rationale,
                            "expected_learning": item.expected_learning,
                            "data_trust_level": item.data_trust_level.value,
                            "schema_version": item.schema_version,
                            "semantic_digest": item.semantic_digest,
                            "created_at": item.created_at,
                        }
                        for item in snapshot.proposals
                    ],
                )
        elif isinstance(snapshot, ProductionPlan):
            await self._session.execute(
                insert(production_plans).values(
                    id=snapshot.id,
                    tenant_id=snapshot.tenant_id,
                    product_id=snapshot.product_id,
                    agent_run_id=snapshot.agent_run_id,
                    concept_id=snapshot.context.concept_id,
                    concept_digest=snapshot.context.concept_digest,
                    concept_set_id=snapshot.context.concept_set_id,
                    concept_set_digest=snapshot.context.concept_set_digest,
                    product_snapshot_id=snapshot.context.product_snapshot_id,
                    product_snapshot_digest=snapshot.context.product_snapshot_digest,
                    research_snapshot_id=snapshot.context.research_snapshot_id,
                    research_snapshot_digest=snapshot.context.research_snapshot_digest,
                    creative_decision_id=snapshot.context.creative_decision_id,
                    context_digest=snapshot.context.context_digest,
                    schema_version=snapshot.schema_version,
                    strategy=snapshot.strategy,
                    required_assets=list(snapshot.required_assets),
                    semantic_digest=snapshot.semantic_digest,
                    estimated_max_video_cost=snapshot.cost.estimated_max_video_cost,
                    estimated_max_image_cost=snapshot.cost.estimated_max_image_cost,
                    currency=snapshot.cost.currency,
                    video_pricing_version=snapshot.cost.video_pricing_version,
                    image_pricing_version=snapshot.cost.image_pricing_version,
                    created_at=snapshot.created_at,
                )
            )
            scene_ids = {
                scene.scene_key: uuid5(NAMESPACE_URL, f"{snapshot.id}:scene:{scene.scene_key}")
                for scene in snapshot.scenes
            }
            await self._session.execute(
                insert(production_scenes),
                [
                    {
                        "id": scene_ids[scene.scene_key],
                        "tenant_id": snapshot.tenant_id,
                        "production_plan_id": snapshot.id,
                        "scene_key": scene.scene_key,
                        "ordinal": scene.ordinal,
                        "duration_seconds": scene.duration_seconds,
                        "content": {
                            "purpose": scene.purpose,
                            "message": scene.message,
                            "voiceover": scene.voiceover,
                            "on_screen_text": scene.on_screen_text,
                        },
                        "created_at": snapshot.created_at,
                    }
                    for scene in snapshot.scenes
                ],
            )
            await self._session.execute(
                insert(production_shots),
                [
                    {
                        "id": uuid5(NAMESPACE_URL, f"{snapshot.id}:shot:{shot.shot_key}"),
                        "tenant_id": snapshot.tenant_id,
                        "production_plan_id": snapshot.id,
                        "production_scene_id": scene_ids[scene.scene_key],
                        "shot_key": shot.shot_key,
                        "ordinal": shot.ordinal,
                        "source_strategy": shot.source_strategy.value,
                        "specification": dict(shot.specification),
                        "created_at": snapshot.created_at,
                    }
                    for scene in snapshot.scenes
                    for shot in scene.shots
                ],
            )
            await self._session.execute(
                insert(generation_segments),
                [
                    {
                        "id": uuid5(NAMESPACE_URL, f"{snapshot.id}:segment:{item.segment_key}"),
                        "tenant_id": snapshot.tenant_id,
                        "production_plan_id": snapshot.id,
                        "segment_key": item.segment_key,
                        "ordinal": ordinal,
                        "media_kind": item.media_kind.value,
                        "duration_seconds": item.duration_seconds,
                        "shot_keys": list(item.shot_keys),
                        "continuity": list(item.continuity),
                        "reference_assets": [str(value) for value in item.reference_asset_ids],
                        "generation_spec": dict(item.generation_spec),
                        "generation_spec_digest": canonical_digest(dict(item.generation_spec)),
                        "created_at": snapshot.created_at,
                    }
                    for ordinal, item in enumerate(snapshot.generation_segments, 1)
                ],
            )
        elif isinstance(snapshot, CreativeConceptSet):
            await self._session.execute(
                insert(concept_sets).values(
                    id=snapshot.id,
                    tenant_id=snapshot.tenant_id,
                    product_id=snapshot.product_id,
                    agent_run_id=snapshot.agent_run_id,
                    schema_version=snapshot.schema_version,
                    product_snapshot_id=snapshot.product_snapshot_id,
                    product_snapshot_digest=snapshot.product_snapshot_digest,
                    research_snapshot_id=snapshot.research_snapshot_id,
                    research_snapshot_digest=snapshot.research_snapshot_digest,
                    input_context_digest=snapshot.input_context_digest,
                    semantic_digest=snapshot.semantic_digest,
                    created_at=snapshot.created_at,
                    experiment_proposal_id=snapshot.experiment_proposal_id,
                    experiment_proposal_digest=snapshot.experiment_proposal_digest,
                )
            )
            await self._session.execute(
                insert(concepts),
                [
                    {
                        "id": item.id,
                        "tenant_id": item.tenant_id,
                        "concept_set_id": item.concept_set_id,
                        "product_id": item.product_id,
                        "concept_key": item.concept_key,
                        "ordinal": item.ordinal,
                        "concept_payload": dict(item.payload),
                        "semantic_digest": item.semantic_digest,
                        "created_at": item.created_at,
                    }
                    for item in snapshot.concepts
                ],
            )
        else:
            await self._session.execute(
                insert(research_snapshots).values(
                    id=snapshot.id,
                    tenant_id=snapshot.tenant_id,
                    product_id=snapshot.product_id,
                    agent_run_id=snapshot.agent_run_id,
                    schema_version=snapshot.schema_version,
                    product_snapshot_id=snapshot.product_snapshot_id,
                    product_snapshot_digest=snapshot.product_snapshot_digest,
                    research_context_digest=snapshot.research_context_digest,
                    findings=[item.semantic() for item in snapshot.findings],
                    research_gaps=list(snapshot.research_gaps),
                    recommended_next_sources=[
                        {
                            "category": i.category,
                            "reason": i.reason,
                            "suggested_query": i.suggested_query,
                        }
                        for i in snapshot.recommended_next_sources
                    ],
                    semantic_digest=snapshot.semantic_digest,
                    created_at=snapshot.created_at,
                    valid_until=snapshot.valid_until,
                )
            )
        row = (
            await self._session.execute(
                update(agent_runs)
                .where(
                    agent_runs.c.id == run.id,
                    agent_runs.c.status == AgentRunStatus.RUNNING.value,
                    agent_runs.c.executed_by_workload_id == workload_id,
                )
                .values(
                    status=AgentRunStatus.SUCCEEDED.value,
                    completed_at=now,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    total_tokens=result.usage.total_tokens,
                    estimated_cost=cost,
                    provider_response_id=result.provider_response_id,
                    result_ref=result_ref,
                )
                .returning(agent_runs)
            )
        ).first()
        if row is None:  # pragma: no cover - protected by the row lock above
            raise AgentRunRecoveryConflict("AgentRun success transition was lost")
        await self._settle_budget(run, cost)
        return _run(row)

    async def finish_failure(
        self,
        run: AgentRun,
        *,
        failure_code: str,
        result: ModelInvocationResult | None = None,
        cost: Decimal = Decimal("0"),
        attempt_id: UUID,
        workload_id: str,
    ) -> AgentRun:
        usage = result.usage if result else None
        now = datetime.now(UTC)
        owned_run = await self._session.scalar(
            select(agent_runs.c.id)
            .where(
                agent_runs.c.id == run.id,
                agent_runs.c.status == AgentRunStatus.RUNNING.value,
                agent_runs.c.executed_by_workload_id == workload_id,
            )
            .with_for_update()
        )
        if owned_run is None:
            raise AgentRunRecoveryConflict("late worker cannot persist AgentRun failure")
        expected_status = (
            ModelAttemptStatus.RESPONSE_RECORDED
            if result is not None
            else ModelAttemptStatus.CLAIMED
        )
        terminal_status = (
            ModelAttemptStatus.SUCCEEDED
            if result is not None
            else ModelAttemptStatus.FAILED_NO_RESPONSE
        )
        attempt_row = (
            await self._session.execute(
                update(model_attempts)
                .where(
                    model_attempts.c.id == attempt_id,
                    model_attempts.c.agent_run_id == run.id,
                    model_attempts.c.workload_id == workload_id,
                    model_attempts.c.status == expected_status.value,
                )
                .values(
                    status=terminal_status.value,
                    finished_at=now,
                    failure_code=failure_code,
                )
                .returning(model_attempts.c.id)
            )
        ).scalar_one_or_none()
        if attempt_row is None:
            raise AgentRunRecoveryConflict("stale model attempt cannot persist failure")
        row = (
            await self._session.execute(
                update(agent_runs)
                .where(
                    agent_runs.c.id == run.id,
                    agent_runs.c.status == AgentRunStatus.RUNNING.value,
                    agent_runs.c.executed_by_workload_id == workload_id,
                )
                .values(
                    status=AgentRunStatus.FAILED.value,
                    completed_at=now,
                    failure_code=failure_code,
                    input_tokens=usage.input_tokens if usage else 0,
                    output_tokens=usage.output_tokens if usage else 0,
                    total_tokens=usage.total_tokens if usage else 0,
                    estimated_cost=cost,
                    provider_response_id=result.provider_response_id if result else None,
                )
                .returning(agent_runs)
            )
        ).first()
        if row is None:  # pragma: no cover - protected by the row lock above
            raise AgentRunRecoveryConflict("AgentRun failure transition was lost")
        await self._settle_budget(run, cost)
        return _run(row)

    async def _settle_budget(self, run: AgentRun, cost: Decimal) -> None:
        await self._session.execute(
            update(agent_budget_usage)
            .where(
                agent_budget_usage.c.agent_definition_id == run.requested_agent_definition_id,
                agent_budget_usage.c.period_start == run.period_start,
            )
            .values(
                reserved_cost=agent_budget_usage.c.reserved_cost - run.reserved_cost,
                actual_cost=agent_budget_usage.c.actual_cost + cost,
                updated_at=datetime.now(UTC),
            )
        )

    async def operational_status(self, run: AgentRun, now: datetime) -> AgentRun:
        if run.status is not AgentRunStatus.RUNNING:
            return run
        attempt = (
            await self._session.execute(
                select(model_attempts).where(
                    model_attempts.c.agent_run_id == run.id,
                    model_attempts.c.lease_expires_at <= now,
                    model_attempts.c.status.in_(
                        [
                            ModelAttemptStatus.CLAIMED.value,
                            ModelAttemptStatus.PROVIDER_STARTED.value,
                            ModelAttemptStatus.RESPONSE_RECORDED.value,
                            ModelAttemptStatus.UNKNOWN.value,
                        ]
                    ),
                )
            )
        ).first()
        if attempt is None:
            return run
        return replace(run, operational_status="recovery_required", is_stranded=True)

    async def find_stranded(self, now: datetime) -> tuple[StrandedAgentRun, ...]:
        run_ids = tuple(
            await self._session.scalars(
                select(agent_runs.c.id)
                .join(
                    model_attempts,
                    and_(
                        model_attempts.c.agent_run_id == agent_runs.c.id,
                        model_attempts.c.tenant_id == agent_runs.c.tenant_id,
                    ),
                )
                .where(
                    agent_runs.c.status == AgentRunStatus.RUNNING.value,
                    model_attempts.c.lease_expires_at <= now,
                    model_attempts.c.status.in_(
                        [
                            ModelAttemptStatus.CLAIMED.value,
                            ModelAttemptStatus.PROVIDER_STARTED.value,
                            ModelAttemptStatus.RESPONSE_RECORDED.value,
                            ModelAttemptStatus.UNKNOWN.value,
                        ]
                    ),
                )
                .order_by(model_attempts.c.lease_expires_at, agent_runs.c.id)
            )
        )
        values: list[StrandedAgentRun] = []
        for run_id in run_ids:
            value = await self.get_stranded(run_id, now)
            if value is not None:
                values.append(value)
        return tuple(values)

    async def get_stranded(
        self, run_id: UUID, now: datetime, *, for_update: bool = False
    ) -> StrandedAgentRun | None:
        run_query = select(agent_runs).where(
            agent_runs.c.id == run_id,
            agent_runs.c.status == AgentRunStatus.RUNNING.value,
        )
        if for_update:
            run_query = run_query.with_for_update()
        run_row = (await self._session.execute(run_query)).first()
        if run_row is None:
            return None
        attempt_query = select(model_attempts).where(
            model_attempts.c.agent_run_id == run_id,
            model_attempts.c.lease_expires_at <= now,
            model_attempts.c.status.in_(
                [
                    ModelAttemptStatus.CLAIMED.value,
                    ModelAttemptStatus.PROVIDER_STARTED.value,
                    ModelAttemptStatus.RESPONSE_RECORDED.value,
                    ModelAttemptStatus.UNKNOWN.value,
                ]
            ),
        )
        if for_update:
            attempt_query = attempt_query.with_for_update()
        attempt_row = (await self._session.execute(attempt_query)).first()
        if attempt_row is None:
            return None
        attempt = _attempt(attempt_row)
        return StrandedAgentRun(_run(run_row), attempt, classify_stranded_attempt(attempt))

    async def recovery_configuration(self, run: AgentRun) -> AgentVersionConfiguration | None:
        definition_ids = {
            run.requested_agent_definition_id,
            run.resolved_agent_definition_id,
        }
        statuses = tuple(
            await self._session.scalars(
                select(agent_definitions.c.status).where(agent_definitions.c.id.in_(definition_ids))
            )
        )
        if len(statuses) != len(definition_ids) or any(
            status != AgentDefinitionStatus.ACTIVE.value for status in statuses
        ):
            return None
        row = (
            await self._session.execute(
                select(agent_versions).where(
                    agent_versions.c.id == run.agent_version_id,
                    agent_versions.c.definition_id == run.resolved_agent_definition_id,
                    agent_versions.c.configuration_digest == run.agent_configuration_digest,
                )
            )
        ).first()
        return _configuration(row._mapping) if row is not None else None

    async def abandon_stranded(
        self,
        stranded: StrandedAgentRun,
        *,
        workload_id: str,
        failure_code: str,
    ) -> AgentRun:
        now = datetime.now(UTC)
        run, attempt = stranded.run, stranded.attempt
        actual_cost = Decimal("0")
        unknown_cost = Decimal("0")
        if stranded.classification is RecoveryClassification.RESPONSE_RECORDED:
            actual_cost = attempt.estimated_cost
            attempt_status = ModelAttemptStatus.SUCCEEDED
        elif stranded.classification is RecoveryClassification.SAFE_BEFORE_PROVIDER:
            attempt_status = ModelAttemptStatus.FAILED_NO_RESPONSE
        else:
            attempt_status = ModelAttemptStatus.UNKNOWN
            unknown_cost = attempt.unknown_cost or run.reserved_cost
        if attempt.status is not ModelAttemptStatus.UNKNOWN:
            updated_attempt = (
                await self._session.execute(
                    update(model_attempts)
                    .where(
                        model_attempts.c.id == attempt.id,
                        model_attempts.c.agent_run_id == run.id,
                        model_attempts.c.status == attempt.status.value,
                    )
                    .values(
                        status=attempt_status.value,
                        finished_at=now,
                        unknown_cost=unknown_cost,
                        failure_code=failure_code,
                    )
                    .returning(model_attempts.c.id)
                )
            ).scalar_one_or_none()
            if updated_attempt is None:
                raise AgentRunRecoveryConflict("model attempt changed during recovery")
        row = (
            await self._session.execute(
                update(agent_runs)
                .where(
                    agent_runs.c.id == run.id,
                    agent_runs.c.status == AgentRunStatus.RUNNING.value,
                )
                .values(
                    status=AgentRunStatus.FAILED.value,
                    completed_at=now,
                    input_tokens=attempt.input_tokens,
                    output_tokens=attempt.output_tokens,
                    total_tokens=attempt.total_tokens,
                    estimated_cost=actual_cost,
                    provider_response_id=attempt.provider_response_id,
                    failure_code=failure_code,
                )
                .returning(agent_runs)
            )
        ).first()
        if row is None:
            raise AgentRunRecoveryConflict("AgentRun changed during recovery")
        budget = agent_budget_usage
        budget_updated = (
            await self._session.execute(
                update(budget)
                .where(
                    budget.c.agent_definition_id == run.requested_agent_definition_id,
                    budget.c.period_start == run.period_start,
                    budget.c.currency == run.currency,
                    budget.c.reserved_cost >= run.reserved_cost,
                )
                .values(
                    reserved_cost=budget.c.reserved_cost - run.reserved_cost,
                    actual_cost=budget.c.actual_cost + actual_cost,
                    unknown_cost=budget.c.unknown_cost + unknown_cost,
                    updated_at=now,
                )
                .returning(budget.c.agent_definition_id)
            )
        ).scalar_one_or_none()
        if budget_updated is None:
            raise AgentRunRecoveryConflict("budget reservation changed during recovery")
        del workload_id  # workload provenance is recorded in the mandatory Audit record.
        return _run(row)

    async def add_recovery_run(self, run: AgentRun) -> bool:
        return await self.add(run)

    async def reconcile_unknown_cost(
        self,
        stranded_run: AgentRun,
        *,
        actual_cost: Decimal,
        workload_id: str,
    ) -> None:
        if (
            stranded_run.status is not AgentRunStatus.FAILED
            or stranded_run.failure_code != "STRANDED_PROVIDER_OUTCOME_UNKNOWN"
        ):
            raise UnknownCostReconciliationConflict("run has no reconcilable unknown cost")
        attempt_row = (
            await self._session.execute(
                select(model_attempts)
                .where(
                    model_attempts.c.agent_run_id == stranded_run.id,
                    model_attempts.c.status == ModelAttemptStatus.UNKNOWN.value,
                    model_attempts.c.unknown_cost > 0,
                )
                .with_for_update()
            )
        ).first()
        if attempt_row is None:
            raise UnknownCostReconciliationConflict("run has no reconcilable unknown cost")
        attempt = _attempt(attempt_row)
        inserted = (
            await self._session.execute(
                pg_insert(model_cost_reconciliations)
                .values(
                    id=uuid4(),
                    tenant_id=stranded_run.tenant_id,
                    agent_run_id=stranded_run.id,
                    model_attempt_id=attempt.id,
                    unknown_cost=attempt.unknown_cost,
                    actual_cost=actual_cost,
                    currency=stranded_run.currency,
                    reconciled_by_workload_id=workload_id,
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing()
                .returning(model_cost_reconciliations.c.id)
            )
        ).scalar_one_or_none()
        if inserted is None:
            raise UnknownCostReconciliationConflict("unknown cost was already reconciled")
        budget = agent_budget_usage
        updated = (
            await self._session.execute(
                update(budget)
                .where(
                    budget.c.agent_definition_id == stranded_run.requested_agent_definition_id,
                    budget.c.period_start == stranded_run.period_start,
                    budget.c.currency == stranded_run.currency,
                    budget.c.unknown_cost >= attempt.unknown_cost,
                )
                .values(
                    unknown_cost=budget.c.unknown_cost - attempt.unknown_cost,
                    actual_cost=budget.c.actual_cost + actual_cost,
                    updated_at=datetime.now(UTC),
                )
                .returning(budget.c.agent_definition_id)
            )
        ).scalar_one_or_none()
        if updated is None:
            raise UnknownCostReconciliationConflict("budget ledger cannot reconcile unknown cost")

    async def get_snapshot(self, snapshot_id: UUID) -> ResearchSnapshot | None:
        row = (
            await self._session.execute(
                select(research_snapshots).where(research_snapshots.c.id == snapshot_id)
            )
        ).first()
        return _snapshot(row) if row else None

    async def list_snapshots(self, product_id: UUID) -> tuple[ResearchSnapshot, ...]:
        rows = (
            await self._session.execute(
                select(research_snapshots)
                .where(research_snapshots.c.product_id == product_id)
                .order_by(research_snapshots.c.created_at.desc())
            )
        ).all()
        return tuple(_snapshot(row) for row in rows)

    async def snapshot_freshness(self, snapshot: ResearchSnapshot) -> str:
        if snapshot.valid_until <= datetime.now(UTC):
            return "stale"
        current_product_digest = await self._session.scalar(
            select(product_knowledge_snapshots.c.digest)
            .where(product_knowledge_snapshots.c.product_id == snapshot.product_id)
            .order_by(
                product_knowledge_snapshots.c.created_at.desc(),
                product_knowledge_snapshots.c.id.desc(),
            )
            .limit(1)
        )
        if current_product_digest != snapshot.product_snapshot_digest:
            return "outdated"
        ranked = (
            select(
                evidence_snapshots.c.source_id,
                evidence_snapshots.c.id.label("evidence_snapshot_id"),
                evidence_snapshots.c.semantic_digest,
                evidence_snapshots.c.captured_at,
                sources.c.category,
            )
            .join(
                sources,
                and_(
                    sources.c.id == evidence_snapshots.c.source_id,
                    sources.c.tenant_id == evidence_snapshots.c.tenant_id,
                ),
            )
            .where(
                evidence_snapshots.c.product_id == snapshot.product_id,
                sources.c.status == "active",
            )
            .distinct(evidence_snapshots.c.source_id)
            .order_by(evidence_snapshots.c.source_id, evidence_snapshots.c.captured_at.desc())
            .subquery()
        )
        rows = (await self._session.execute(select(ranked))).all()
        references = tuple(
            ResearchEvidenceReference(
                row._mapping["source_id"],
                row._mapping["evidence_snapshot_id"],
                row._mapping["semantic_digest"],
                ResearchCategory(row._mapping["category"]),
                row._mapping["captured_at"],
            )
            for row in rows
        )
        social_rows = (
            await self._session.execute(
                select(
                    social_evidence_snapshots.c.research_target_id,
                    social_evidence_snapshots.c.id.label("social_evidence_snapshot_id"),
                    social_evidence_snapshots.c.semantic_digest,
                    social_evidence_snapshots.c.platform,
                    social_evidence_snapshots.c.captured_at,
                    social_evidence_snapshots.c.platform_content_id,
                    social_evidence_snapshots.c.source_url,
                )
                .join(
                    research_targets,
                    and_(
                        research_targets.c.id == social_evidence_snapshots.c.research_target_id,
                        research_targets.c.tenant_id == social_evidence_snapshots.c.tenant_id,
                    ),
                )
                .where(
                    social_evidence_snapshots.c.product_id == snapshot.product_id,
                    research_targets.c.status == "active",
                )
                .order_by(social_evidence_snapshots.c.captured_at.desc())
                .limit(20)
            )
        ).all()
        social_reference_values: list[SocialEvidenceReference] = []
        seen_social_refs: set[tuple[UUID, str]] = set()
        for row in social_rows:
            identity = (
                row._mapping["platform_content_id"]
                or row._mapping["source_url"]
                or str(row._mapping["social_evidence_snapshot_id"])
            )
            key = (row._mapping["research_target_id"], identity)
            if key in seen_social_refs:
                continue
            seen_social_refs.add(key)
            social_reference_values.append(
                SocialEvidenceReference(
                    row._mapping["research_target_id"],
                    row._mapping["social_evidence_snapshot_id"],
                    row._mapping["semantic_digest"],
                    SocialPlatform(row._mapping["platform"]),
                    row._mapping["captured_at"],
                )
            )
        social_references = tuple(social_reference_values)
        current_manifest = ResearchContextManifest.build(
            snapshot.tenant_id, snapshot.product_id, references, social_references
        )
        return (
            "current" if current_manifest.digest == snapshot.research_context_digest else "outdated"
        )
