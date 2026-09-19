from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.intelligence.domain import (
    CanonicalRef,
    Confidence,
    DataTrustLevel,
    ExperimentDecision,
    ExperimentDecisionKind,
    ExperimentProposal,
    InsightCandidate,
    InsightDecision,
    InsightStatus,
    IntelligenceReport,
)

from .intelligence_schema import (
    experiment_decisions,
    experiment_proposals,
    insight_candidates,
    insight_decisions,
    reports,
)


def _report(row: Mapping[str, Any]) -> IntelligenceReport:
    return IntelligenceReport(
        row["tenant_id"],
        row["product_id"],
        row["agent_run_id"],
        row["agent_version_id"],
        row["context_manifest_id"],
        row["context_manifest_digest"],
        DataTrustLevel(row["data_trust_level"]),
        row["summary"],
        tuple(row["observations"]),
        tuple(row["comparative_findings"]),
        tuple(row["limitations"]),
        row["semantic_digest"],
        row["id"],
        row["schema_version"],
        row["created_at"],
    )


def _candidate(row: Mapping[str, Any]) -> InsightCandidate:
    return InsightCandidate(
        row["tenant_id"],
        row["product_id"],
        row["report_id"],
        row["statement"],
        tuple(
            CanonicalRef(item["kind"], UUID(item["id"]), item.get("digest"))
            for item in row["evidence_refs"]
        ),
        row["sample_size"],
        row["metric"],
        Decimal(row["baseline"]) if row["baseline"] is not None else None,
        Decimal(row["observed_delta"]) if row["observed_delta"] is not None else None,
        Confidence(row["confidence"]),
        row["scope"],
        tuple(row["limitations"]),
        DataTrustLevel(row["data_trust_level"]),
        row["semantic_digest"],
        row["id"],
        InsightStatus(row["status"]),
        row["created_at"],
        row["valid_until"],
    )


def _proposal(row: Mapping[str, Any]) -> ExperimentProposal:
    return ExperimentProposal(
        row["tenant_id"],
        row["product_id"],
        row["report_id"],
        row["report_digest"],
        tuple(row["candidate_ids"]),
        row["hypothesis"],
        row["primary_variable"],
        tuple(row["controlled_elements"]),
        row["target_metric"],
        row["platform"],
        row["measurement_window"],
        row["creative_direction"],
        row["rationale"],
        row["expected_learning"],
        DataTrustLevel(row["data_trust_level"]),
        row["semantic_digest"],
        row["id"],
        row["schema_version"],
        row["created_at"],
    )


class SqlAlchemyIntelligenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reports(self, product_id: UUID) -> tuple[IntelligenceReport, ...]:
        rows = (
            await self.session.execute(
                select(reports)
                .where(reports.c.product_id == product_id)
                .order_by(reports.c.created_at.desc())
            )
        ).mappings()
        return tuple(_report(cast(Mapping[str, Any], row)) for row in rows)

    async def report(self, report_id: UUID) -> IntelligenceReport | None:
        row = (
            (await self.session.execute(select(reports).where(reports.c.id == report_id)))
            .mappings()
            .one_or_none()
        )
        return _report(cast(Mapping[str, Any], row)) if row else None

    async def candidates(self, report_id: UUID) -> tuple[InsightCandidate, ...]:
        rows = (
            await self.session.execute(
                select(insight_candidates)
                .where(insight_candidates.c.report_id == report_id)
                .order_by(insight_candidates.c.created_at)
            )
        ).mappings()
        return tuple(_candidate(cast(Mapping[str, Any], row)) for row in rows)

    async def proposals(self, report_id: UUID) -> tuple[ExperimentProposal, ...]:
        rows = (
            await self.session.execute(
                select(experiment_proposals)
                .where(experiment_proposals.c.report_id == report_id)
                .order_by(experiment_proposals.c.created_at)
            )
        ).mappings()
        return tuple(_proposal(cast(Mapping[str, Any], row)) for row in rows)

    async def candidate(
        self, candidate_id: UUID, *, for_update: bool = False
    ) -> InsightCandidate | None:
        if for_update:
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"intelligence-insight:{candidate_id}"},
            )
        row = (
            (
                await self.session.execute(
                    select(insight_candidates).where(insight_candidates.c.id == candidate_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        return _candidate(cast(Mapping[str, Any], row)) if row else None

    async def proposal(
        self, proposal_id: UUID, *, for_update: bool = False
    ) -> ExperimentProposal | None:
        if for_update:
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"intelligence-experiment:{proposal_id}"},
            )
        row = (
            (
                await self.session.execute(
                    select(experiment_proposals).where(experiment_proposals.c.id == proposal_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        return _proposal(cast(Mapping[str, Any], row)) if row else None

    async def add_insight_decision(self, value: InsightDecision) -> None:
        await self.session.execute(
            insert(insight_decisions).values(
                id=value.id,
                tenant_id=value.tenant_id,
                product_id=value.product_id,
                candidate_id=value.candidate_id,
                candidate_digest=value.candidate_digest,
                decision=value.decision.value,
                decided_by=value.decided_by,
                created_at=value.created_at,
            )
        )

    async def add_experiment_decision(self, value: ExperimentDecision) -> None:
        await self.session.execute(
            insert(experiment_decisions).values(
                id=value.id,
                tenant_id=value.tenant_id,
                product_id=value.product_id,
                proposal_id=value.proposal_id,
                proposal_digest=value.proposal_digest,
                decision=value.decision.value,
                decided_by=value.decided_by,
                created_at=value.created_at,
            )
        )

    async def current_experiment_decision(self, proposal_id: UUID) -> ExperimentDecision | None:
        row = (
            (
                await self.session.execute(
                    select(experiment_decisions)
                    .where(experiment_decisions.c.proposal_id == proposal_id)
                    .order_by(
                        experiment_decisions.c.created_at.desc(), experiment_decisions.c.id.desc()
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return ExperimentDecision(
            row["tenant_id"],
            row["product_id"],
            row["proposal_id"],
            row["proposal_digest"],
            ExperimentDecisionKind(row["decision"]),
            row["decided_by"],
            row["id"],
            row["created_at"],
        )
