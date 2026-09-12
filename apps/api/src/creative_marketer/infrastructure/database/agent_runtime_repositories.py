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
    CreativePreparation,
    ProducerPreparation,
    ResearcherPreparation,
    ResolvedResearcher,
    build_creative_model_context,
    build_producer_model_context,
)
from creative_marketer.agent_runtime.domain import (
    AgentRun,
    AgentRunRecoveryConflict,
    AgentRunStatus,
    BudgetExceeded,
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
from creative_marketer.catalog.domain import ProductKnowledgeSnapshot
from creative_marketer.creative.domain import (
    ApprovedCreativeConcept,
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
from creative_marketer.infrastructure.database.catalog_schema import (
    assets,
    product_knowledge_snapshots,
)
from creative_marketer.infrastructure.database.creative_schema import (
    concept_decisions,
    concept_sets,
    concepts,
)
from creative_marketer.infrastructure.database.production_schema import (
    generation_segments,
    production_plans,
    production_scenes,
    production_shots,
)
from creative_marketer.infrastructure.database.research_repositories import _evidence
from creative_marketer.infrastructure.database.research_schema import evidence_snapshots, sources
from creative_marketer.production.domain import ProductionPlan, ProductionPlanningRequest
from creative_marketer.research.domain import (
    ResearchCategory,
    ResearchContextManifest,
    ResearchEvidenceReference,
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
        evidence = tuple(
            (
                _evidence(row),
                ResearchCategory(row._mapping["category"]),
                row._mapping["display_name"],
            )
            for row in evidence_rows
        )
        if not evidence:
            return None
        references = tuple(
            ResearchEvidenceReference(
                item.source_id, item.id, item.semantic_digest, category, item.captured_at
            )
            for item, category, _label in evidence
        )
        manifest = ResearchContextManifest.build(s["tenant_id"], product_id, references)
        researcher = ResolvedResearcher(
            requested["id"],
            resolved_id,
            v["id"],
            v["version_number"],
            v["configuration_digest"],
            _configuration(v),
        )
        return ResearcherPreparation(researcher, product_snapshot, manifest, evidence)

    async def prepare_creative(self, product_id: UUID) -> CreativePreparation | None:
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
            primary = brief.get("primary_audience")
            pain_points = primary.get("pain_points") if isinstance(primary, Mapping) else ()
            checks = (
                profile.get("description"),
                brief.get("product_why"),
                profile.get("target_audiences"),
                pain_points,
                brief.get("positioning_statement"),
                profile.get("differentiators"),
                profile.get("features"),
                profile.get("benefits"),
                brief.get("desired_creative_style"),
                profile.get("prohibited_claims") or brief.get("prohibited_messaging"),
            )
            completeness = 100 - 10 * sum(not bool(item) for item in checks)
        strategist = ResolvedResearcher(
            requested["id"],
            resolved_id,
            v["id"],
            v["version_number"],
            v["configuration_digest"],
            _configuration(v),
        )
        return CreativePreparation(strategist, product, research, freshness, completeness)

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
            raise BudgetExceeded("period budget is exhausted")
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
            manifest = tuple(raw_assets) if isinstance(raw_assets, (list, tuple)) else ()
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
                tuple(dict(item) for item in manifest if isinstance(item, Mapping)),
            )
            planning, model = build_producer_model_context(
                producer_preparation,
                ProductionPlanningRequest(
                    str(request_ref["target_format"]), str(request_ref["aspect_ratio"])
                ),
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
            preparation = CreativePreparation(
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
            )
            request = CreativeStrategyRequest(
                cast(int, request_ref["concept_count"]),
                ChannelIntent(str(request_ref["channel_intent"])),
            )
            creative, model = build_creative_model_context(preparation, request)
            if creative.context_digest != run.input_context_digest:
                raise ValueError("bound Creative context digest mismatch")
            return model
        blocks: list[EvidenceBlockRef] = []
        for identity in run.selected_evidence:
            evidence_id = UUID(str(identity["evidence_snapshot_id"]))
            row = (
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
            ).one()
            snapshot = _evidence(row)
            index = cast(int, identity["block_index"])
            source_block = next((item for item in snapshot.blocks if item.ordinal == index), None)
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
                    snapshot.source_id,
                    row._mapping["display_name"],
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
            v["system_instructions"], dict(product.content), tuple(blocks), run.context_digest
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
        if not isinstance(snapshot, (ResearchSnapshot, CreativeConceptSet, ProductionPlan)):
            raise ValueError("unsupported capability result type")
        now = datetime.now(UTC)
        if isinstance(snapshot, ProductionPlan):
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
        if isinstance(snapshot, ProductionPlan):
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
        current_manifest = ResearchContextManifest.build(
            snapshot.tenant_id, snapshot.product_id, references
        )
        return (
            "current" if current_manifest.digest == snapshot.research_context_digest else "outdated"
        )
