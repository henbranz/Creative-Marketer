from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import and_, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.agent_governance.domain import AgentDefinitionStatus
from creative_marketer.agent_runtime.application import (
    ResearcherPreparation,
    ResolvedResearcher,
)
from creative_marketer.agent_runtime.domain import (
    AgentRun,
    AgentRunStatus,
    BudgetExceeded,
    Citation,
    Confidence,
    EvidenceBlockRef,
    Finding,
    FindingCategory,
    ModelContext,
    ModelInvocationResult,
    ModelRoute,
    RecommendedSource,
    ResearchSnapshot,
    canonical_digest,
)
from creative_marketer.catalog.domain import ProductKnowledgeSnapshot
from creative_marketer.infrastructure.database.agent_governance_repositories import _configuration
from creative_marketer.infrastructure.database.agent_governance_schema import (
    agent_activations,
    agent_definitions,
    agent_versions,
)
from creative_marketer.infrastructure.database.agent_runtime_schema import (
    agent_budget_usage,
    agent_runs,
    research_snapshots,
)
from creative_marketer.infrastructure.database.catalog_schema import product_knowledge_snapshots
from creative_marketer.infrastructure.database.research_repositories import _evidence
from creative_marketer.infrastructure.database.research_schema import evidence_snapshots, sources
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
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{definition_id}:{period_start.isoformat()}"},
        )
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
            or d["actual_cost"] + d["reserved_cost"] + reserve_cost > max_cost
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

    async def claim(self, run_id: UUID, workload_id: str, route: ModelRoute) -> AgentRun | None:
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
        return _run(row) if row else None

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

    async def finish_success(
        self,
        run: AgentRun,
        result: ModelInvocationResult,
        snapshot: ResearchSnapshot,
        cost: Decimal,
    ) -> AgentRun:
        now = datetime.now(UTC)
        result_ref = f"research-snapshot://{snapshot.id}"
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
                    agent_runs.c.id == run.id, agent_runs.c.status == AgentRunStatus.RUNNING.value
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
        ).one()
        await self._settle_budget(run, cost)
        return _run(row)

    async def finish_failure(
        self,
        run: AgentRun,
        *,
        failure_code: str,
        result: ModelInvocationResult | None = None,
        cost: Decimal = Decimal("0"),
    ) -> AgentRun:
        usage = result.usage if result else None
        row = (
            await self._session.execute(
                update(agent_runs)
                .where(
                    agent_runs.c.id == run.id, agent_runs.c.status == AgentRunStatus.RUNNING.value
                )
                .values(
                    status=AgentRunStatus.FAILED.value,
                    completed_at=datetime.now(UTC),
                    failure_code=failure_code,
                    input_tokens=usage.input_tokens if usage else 0,
                    output_tokens=usage.output_tokens if usage else 0,
                    total_tokens=usage.total_tokens if usage else 0,
                    estimated_cost=cost,
                    provider_response_id=result.provider_response_id if result else None,
                )
                .returning(agent_runs)
            )
        ).one()
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
