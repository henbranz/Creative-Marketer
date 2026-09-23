from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Protocol
from uuid import UUID

from jsonschema import Draft202012Validator

from creative_marketer.agent_runtime.domain import canonical_digest
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import tenant_event
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus

from .domain import (
    CanonicalRef,
    Confidence,
    DataTrustLevel,
    ExperimentDecision,
    ExperimentDecisionKind,
    ExperimentHandoffDenied,
    ExperimentProposal,
    InsightCandidate,
    InsightDecision,
    InsightDecisionKind,
    IntelligenceContextManifest,
    IntelligenceNotFound,
    IntelligencePermissionDenied,
    IntelligenceReport,
    IntelligenceResult,
    InvalidIntelligenceOutput,
    PerformanceComparison,
    reject_unsafe_claims,
)

INTELLIGENCE_CONTRACT_KEY = "intelligence.intelligence_report"
INTELLIGENCE_CONTRACT_VERSION = 1
NUMERIC_TOKEN = re.compile(r"(?<![\w-])[-+]?\d+(?:\.\d+)?(?:%|[kKmM])?(?![\w-])")


def reject_unsupported_numeric_claims(
    output: Mapping[str, object], grounding: Mapping[str, object]
) -> None:
    """Reject model-authored quantities absent from deterministic input facts."""

    allowed: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            allowed.add(str(value))
            return
        if isinstance(value, str) and re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
            allowed.add(value)
            return
        if isinstance(value, Mapping):
            for child in value.values():
                collect(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child)

    collect(grounding)
    generated_fragments: list[str] = []

    def collect_generated(value: object, key: str = "") -> None:
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                collect_generated(child, str(child_key))
        elif isinstance(value, list | tuple):
            for child in value:
                collect_generated(child, key)
        elif isinstance(value, str) and key in {
            "summary",
            "statement",
            "interpretation",
            "hypothesis",
            "creative_direction",
            "rationale",
            "expected_learning",
            "limitations",
        }:
            generated_fragments.append(value)

    collect_generated(output)
    generated_text = " ".join(generated_fragments)
    unsupported = {
        token
        for token in NUMERIC_TOKEN.findall(generated_text)
        if token.rstrip("%kKmM") not in allowed
    }
    if unsupported:
        raise InvalidIntelligenceOutput("provider output contains an unsupported numeric claim")


def load_intelligence_output_schema() -> Mapping[str, object]:
    path = Path(__file__).with_name("schemas") / "intelligence.intelligence_report.v1.json"
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def _scope_from_provider(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    dimensions = value["dimensions"]
    assert isinstance(dimensions, list)
    scope: dict[str, object] = {}
    for item in dimensions:
        assert isinstance(item, dict)
        key = str(item["key"])
        if key in scope:
            raise InvalidIntelligenceOutput("provider scope contains duplicate dimensions")
        scope[key] = item["value"]
    return scope


def validate_intelligence_output(
    output: Mapping[str, object],
    *,
    tenant_id: UUID,
    product_id: UUID,
    agent_run_id: UUID,
    agent_version_id: UUID,
    manifest: IntelligenceContextManifest,
    comparisons: tuple[PerformanceComparison, ...],
) -> IntelligenceResult:
    try:
        Draft202012Validator(load_intelligence_output_schema()).validate(dict(output))
    except Exception as error:
        raise InvalidIntelligenceOutput(
            "provider output does not match intelligence contract"
        ) from error
    comparison_by_id = {str(item.id): item for item in comparisons}
    raw_findings = output["comparative_findings"]
    raw_candidates = output["insight_candidates"]
    raw_proposals = output["next_experiments"]
    assert isinstance(raw_findings, list)
    assert isinstance(raw_candidates, list)
    assert isinstance(raw_proposals, list)
    for item in raw_findings:
        assert isinstance(item, dict)
        comparison_id = str(item["comparison_id"])
        if comparison_id not in comparison_by_id:
            raise InvalidIntelligenceOutput(
                "finding references an unknown deterministic comparison"
            )
        reject_unsafe_claims(str(item["interpretation"]))
    raw_limitations = output["limitations"]
    raw_observations = output["observations"]
    assert isinstance(raw_limitations, list)
    assert isinstance(raw_observations, list)
    limitations = tuple(str(item) for item in raw_limitations)
    report_id = UUID(
        bytes=__import__("hashlib")
        .sha256(f"{agent_run_id}:intelligence-report".encode())
        .digest()[:16],
        version=4,
    )
    observations = tuple(dict(item) for item in raw_observations)
    findings = tuple(dict(item) for item in raw_findings)
    report_content = {
        "schema_version": 1,
        "context_manifest_id": str(manifest.id),
        "context_manifest_digest": manifest.semantic_digest,
        "data_trust_level": manifest.data_trust_level.value,
        "summary": str(output["summary"]),
        "observations": [dict(item) for item in observations],
        "comparative_findings": [dict(item) for item in findings],
        "limitations": list(limitations),
    }
    report = IntelligenceReport(
        tenant_id,
        product_id,
        agent_run_id,
        agent_version_id,
        manifest.id,
        manifest.semantic_digest,
        manifest.data_trust_level,
        str(output["summary"]),
        observations,
        findings,
        limitations,
        canonical_digest(report_content),
        id=report_id,
    )
    candidates: list[InsightCandidate] = []
    for raw in raw_candidates:
        assert isinstance(raw, dict)
        evidence_ids = tuple(str(item) for item in raw["comparison_ids"])
        if any(item not in comparison_by_id for item in evidence_ids):
            raise InvalidIntelligenceOutput("candidate references unknown comparison evidence")
        evidence = tuple(
            CanonicalRef(
                "performance_comparison",
                comparison_by_id[item].id,
                comparison_by_id[item].semantic_digest,
            )
            for item in evidence_ids
        )
        sample_size = min((comparison_by_id[item].sample_size for item in evidence_ids), default=1)
        confidence = Confidence(str(raw["confidence"]))
        if manifest.data_trust_level is DataTrustLevel.SYNTHETIC or sample_size < 3:
            confidence = Confidence.LOW
        comparison = comparison_by_id[evidence_ids[0]] if evidence_ids else None
        scope = _scope_from_provider(raw["scope"])
        content = {
            "report_id": str(report.id),
            "statement": str(raw["statement"]),
            "evidence_refs": [item.primitive() for item in evidence],
            "sample_size": sample_size,
            "metric": str(raw["metric"]),
            "baseline": str(comparison.baseline_value) if comparison else None,
            "observed_delta": str(comparison.absolute_delta) if comparison else None,
            "confidence": confidence.value,
            "scope": scope,
            "limitations": [str(item) for item in raw["limitations"]],
            "data_trust_level": manifest.data_trust_level.value,
        }
        candidates.append(
            InsightCandidate(
                tenant_id,
                product_id,
                report.id,
                str(raw["statement"]),
                evidence,
                sample_size,
                str(raw["metric"]),
                comparison.baseline_value if comparison else None,
                comparison.absolute_delta if comparison else None,
                confidence,
                scope,
                tuple(str(item) for item in raw["limitations"]),
                manifest.data_trust_level,
                canonical_digest(content),
            )
        )
    proposals: list[ExperimentProposal] = []
    by_key = {str(index): candidate for index, candidate in enumerate(candidates)}
    for raw in raw_proposals:
        assert isinstance(raw, dict)
        candidate_keys = tuple(str(item) for item in raw["candidate_indexes"])
        if any(item not in by_key for item in candidate_keys):
            raise InvalidIntelligenceOutput("experiment references an unknown candidate")
        candidate_ids = tuple(by_key[item].id for item in candidate_keys)
        content = {
            "schema_version": 1,
            "source_intelligence_report_id": str(report.id),
            "source_intelligence_report_digest": report.semantic_digest,
            "source_insight_candidate_ids": [str(item) for item in candidate_ids],
            "hypothesis": str(raw["hypothesis"]),
            "primary_variable": str(raw["primary_variable"]),
            "controlled_elements": [str(item) for item in raw["controlled_elements"]],
            "target_metric": str(raw["target_metric"]),
            "platform": str(raw["platform"]),
            "recommended_measurement_window": str(raw["recommended_measurement_window"]),
            "creative_direction": str(raw["creative_direction"]),
            "rationale": str(raw["rationale"]),
            "expected_learning": str(raw["expected_learning"]),
            "data_trust_level": manifest.data_trust_level.value,
        }
        proposals.append(
            ExperimentProposal(
                tenant_id,
                product_id,
                report.id,
                report.semantic_digest,
                candidate_ids,
                str(raw["hypothesis"]),
                str(raw["primary_variable"]),
                tuple(str(item) for item in raw["controlled_elements"]),
                str(raw["target_metric"]),
                str(raw["platform"]),
                str(raw["recommended_measurement_window"]),
                str(raw["creative_direction"]),
                str(raw["rationale"]),
                str(raw["expected_learning"]),
                manifest.data_trust_level,
                canonical_digest(content),
            )
        )
    return IntelligenceResult(report, tuple(candidates), tuple(proposals))


class IntelligenceRepository(Protocol):
    async def reports(self, product_id: UUID) -> tuple[IntelligenceReport, ...]: ...
    async def report(self, report_id: UUID) -> IntelligenceReport | None: ...
    async def candidates(self, report_id: UUID) -> tuple[InsightCandidate, ...]: ...
    async def proposals(self, report_id: UUID) -> tuple[ExperimentProposal, ...]: ...
    async def candidate(
        self, candidate_id: UUID, *, for_update: bool = False
    ) -> InsightCandidate | None: ...
    async def proposal(
        self, proposal_id: UUID, *, for_update: bool = False
    ) -> ExperimentProposal | None: ...
    async def add_insight_decision(self, decision: InsightDecision) -> None: ...
    async def add_experiment_decision(self, decision: ExperimentDecision) -> None: ...
    async def current_experiment_decision(self, proposal_id: UUID) -> ExperimentDecision | None: ...


class IntelligenceUnitOfWork(Protocol):
    intelligence: IntelligenceRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> IntelligenceUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class IntelligenceUnitOfWorkFactory(Protocol):
    def __call__(self, tenant_id: UUID) -> IntelligenceUnitOfWork: ...


@dataclass(slots=True)
class IntelligenceService:
    uow_factory: IntelligenceUnitOfWorkFactory
    contracts: EventContractRegistry = field(default_factory=EventContractRegistry)

    @staticmethod
    def _write(context: ExecutionContext) -> UUID:
        if (
            context.membership_status is not MembershipStatus.ACTIVE
            or context.membership_role not in {MembershipRole.OWNER, MembershipRole.ADMIN}
            or context.user_id is None
        ):
            raise IntelligencePermissionDenied("OWNER or ADMIN membership required")
        return context.user_id

    async def list_reports(
        self, context: ExecutionContext, product_id: UUID
    ) -> tuple[IntelligenceReport, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.intelligence.reports(product_id)

    async def get_report(
        self, context: ExecutionContext, report_id: UUID
    ) -> tuple[IntelligenceReport, tuple[InsightCandidate, ...], tuple[ExperimentProposal, ...]]:
        async with self.uow_factory(context.tenant_id) as uow:
            report = await uow.intelligence.report(report_id)
            if report is None:
                raise IntelligenceNotFound("IntelligenceReport not found")
            return (
                report,
                await uow.intelligence.candidates(report_id),
                await uow.intelligence.proposals(report_id),
            )

    async def decide_insight(
        self, context: ExecutionContext, candidate_id: UUID, decision: InsightDecisionKind
    ) -> InsightDecision:
        actor = self._write(context)
        async with self.uow_factory(context.tenant_id) as uow:
            candidate = await uow.intelligence.candidate(candidate_id, for_update=True)
            if candidate is None:
                raise IntelligenceNotFound("InsightCandidate not found")
            value = InsightDecision(
                context.tenant_id,
                candidate.product_id,
                candidate.id,
                candidate.semantic_digest,
                decision,
                actor,
            )
            await uow.intelligence.add_insight_decision(value)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="intelligence.insight.decided",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="insight_candidate",
                    resource_id=str(candidate.id),
                    after_digest=candidate.semantic_digest,
                    metadata=safe_metadata({"decision": decision.value}),
                )
            )
            if decision is InsightDecisionKind.PROPOSE_FOR_TESTING:
                event_type = "intelligence.insight.proposed.v1"
                await uow.outbox.append(
                    tenant_event(
                        context,
                        event_type=event_type,
                        schema_version=1,
                        aggregate_type="insight_candidate",
                        aggregate_id=candidate.id,
                        payload={
                            "insight_candidate_id": str(candidate.id),
                            "product_id": str(candidate.product_id),
                            "candidate_digest": candidate.semantic_digest,
                            "status": "PROPOSED",
                        },
                        payload_schema_digest=self.contracts.schema_digest(event_type),
                        occurred_at=value.created_at,
                    )
                )
            await uow.commit()
            return value

    async def decide_experiment(
        self, context: ExecutionContext, proposal_id: UUID, decision: ExperimentDecisionKind
    ) -> ExperimentDecision:
        actor = self._write(context)
        async with self.uow_factory(context.tenant_id) as uow:
            proposal = await uow.intelligence.proposal(proposal_id, for_update=True)
            if proposal is None:
                raise IntelligenceNotFound("ExperimentProposal not found")
            current = await uow.intelligence.current_experiment_decision(proposal_id)
            if current is not None:
                if (
                    current.decision is decision
                    and current.proposal_digest == proposal.semantic_digest
                ):
                    return current
                raise ExperimentHandoffDenied(
                    "ExperimentProposal already has an immutable human decision"
                )
            value = ExperimentDecision(
                context.tenant_id,
                proposal.product_id,
                proposal.id,
                proposal.semantic_digest,
                decision,
                actor,
            )
            await uow.intelligence.add_experiment_decision(value)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="intelligence.experiment.decided",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="experiment_proposal",
                    resource_id=str(proposal.id),
                    after_digest=proposal.semantic_digest,
                    metadata=safe_metadata(
                        {
                            "decision": decision.value,
                            "data_trust_level": proposal.data_trust_level.value,
                        }
                    ),
                )
            )
            if decision is ExperimentDecisionKind.APPROVED_FOR_CREATIVE:
                event_type = "intelligence.experiment.approved.v1"
                await uow.outbox.append(
                    tenant_event(
                        context,
                        event_type=event_type,
                        schema_version=1,
                        aggregate_type="experiment_proposal",
                        aggregate_id=proposal.id,
                        payload={
                            "experiment_proposal_id": str(proposal.id),
                            "product_id": str(proposal.product_id),
                            "proposal_digest": proposal.semantic_digest,
                            "data_trust_level": proposal.data_trust_level.value,
                            "status": decision.value,
                        },
                        payload_schema_digest=self.contracts.schema_digest(event_type),
                        occurred_at=value.created_at,
                    )
                )
            await uow.commit()
            return value

    async def current_experiment_decision(
        self, context: ExecutionContext, proposal_id: UUID
    ) -> ExperimentDecision | None:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.intelligence.current_experiment_decision(proposal_id)

    async def approved_proposal(
        self, context: ExecutionContext, proposal_id: UUID
    ) -> ExperimentProposal:
        async with self.uow_factory(context.tenant_id) as uow:
            proposal = await uow.intelligence.proposal(proposal_id)
            decision = await uow.intelligence.current_experiment_decision(proposal_id)
            if proposal is None or decision is None:
                raise ExperimentHandoffDenied("approved proposal is required")
            if (
                decision.decision is not ExperimentDecisionKind.APPROVED_FOR_CREATIVE
                or decision.proposal_digest != proposal.semantic_digest
            ):
                raise ExperimentHandoffDenied("decision does not approve the exact proposal digest")
            if (
                proposal.data_trust_level is DataTrustLevel.SYNTHETIC
                and context.environment not in {"development", "test"}
            ):
                raise ExperimentHandoffDenied(
                    "synthetic proposals may seed Creative only in development/test"
                )
            if await uow.intelligence.report(proposal.source_intelligence_report_id) is None:
                raise ExperimentHandoffDenied("source IntelligenceReport is unavailable")
            return proposal
