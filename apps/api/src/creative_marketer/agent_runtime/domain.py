from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Decimal
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID, uuid4

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MAX_FINDINGS = 30
MAX_CITATIONS_PER_FINDING = 8
MAX_STATEMENT_CHARACTERS = 1500
MAX_GAPS = 20
MAX_RECOMMENDED_SOURCES = 20


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class AgentRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED_BUDGET = "BLOCKED_BUDGET"


class ModelAttemptStatus(StrEnum):
    CLAIMED = "CLAIMED"
    PROVIDER_STARTED = "PROVIDER_STARTED"
    RESPONSE_RECORDED = "RESPONSE_RECORDED"
    SUCCEEDED = "SUCCEEDED"
    FAILED_NO_RESPONSE = "FAILED_NO_RESPONSE"
    UNKNOWN = "UNKNOWN"


class RecoveryClassification(StrEnum):
    SAFE_BEFORE_PROVIDER = "SAFE_BEFORE_PROVIDER"
    PROVIDER_OUTCOME_UNKNOWN = "PROVIDER_OUTCOME_UNKNOWN"
    RESPONSE_RECORDED = "RESPONSE_RECORDED"


class FindingCategory(StrEnum):
    COMPETITOR = "competitor"
    POSITIONING = "positioning"
    PRICING = "pricing"
    MESSAGING = "messaging"
    AUDIENCE = "audience"
    OFFER_PROMOTION = "offer_promotion"
    CREATIVE_PATTERN = "creative_pattern"
    OTHER = "other"


class Confidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ModelProviderError(Exception):
    code = "MODEL_PROVIDER_UNAVAILABLE"
    retryable = True


class ModelRateLimited(ModelProviderError):
    code = "MODEL_RATE_LIMITED"


class ModelTimeout(ModelProviderError):
    code = "MODEL_TIMEOUT"


class ModelRefusal(ModelProviderError):
    code = "MODEL_REFUSAL"
    retryable = False


class AgentRuntimeError(Exception):
    code = "AGENT_RUNTIME_ERROR"


class AgentRunNotFound(AgentRuntimeError):
    code = "AGENT_RUN_NOT_FOUND"


class AgentRunConflict(AgentRuntimeError):
    code = "AGENT_RUN_CONFLICT"


class AgentRunDenied(AgentRuntimeError):
    code = "AGENT_RUN_DENIED"


class AgentRunNotReady(AgentRuntimeError):
    code = "AGENT_RUN_NOT_READY"


class BudgetExceeded(AgentRuntimeError):
    code = "AGENT_BUDGET_EXCEEDED"


class AgentRunRecoveryConflict(AgentRuntimeError):
    code = "AGENT_RUN_RECOVERY_CONFLICT"


class RecoveryAgentUnavailable(AgentRuntimeError):
    code = "RECOVERY_AGENT_UNAVAILABLE"


class RecoveryBlockedBudget(BudgetExceeded):
    code = "RECOVERY_BLOCKED_BUDGET"


class UnknownCostReconciliationConflict(AgentRuntimeError):
    code = "UNKNOWN_COST_RECONCILIATION_CONFLICT"


class ModelRouteUnavailable(AgentRuntimeError):
    code = "MODEL_ROUTE_UNAVAILABLE"


class ModelCapabilityUnavailable(AgentRuntimeError):
    code = "MODEL_CAPABILITY_UNAVAILABLE"


class InvalidModelOutput(AgentRuntimeError):
    code = "MODEL_INVALID_OUTPUT"


class InvalidResearchCitation(InvalidModelOutput):
    code = "INVALID_RESEARCH_CITATION"


@dataclass(frozen=True, slots=True)
class ModelPricing:
    version: str
    input_price_per_million: Decimal
    output_price_per_million: Decimal
    currency: str

    def __post_init__(self) -> None:
        if (
            self.input_price_per_million < 0
            or self.output_price_per_million < 0
            or not re.fullmatch(r"[A-Z]{3}", self.currency)
        ):
            raise ValueError("model pricing is invalid")

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        value = (
            Decimal(input_tokens) * self.input_price_per_million
            + Decimal(output_tokens) * self.output_price_per_million
        ) / Decimal(1_000_000)
        return value.quantize(Decimal("0.000001"), rounding=ROUND_UP)


@dataclass(frozen=True, slots=True)
class ModelRoute:
    profile_key: str
    route_version: str
    provider: str
    model: str
    capabilities: frozenset[str]
    reasoning_effort: str
    max_output_tokens: int
    pricing: ModelPricing

    def __post_init__(self) -> None:
        if self.max_output_tokens <= 0 or not self.capabilities:
            raise ValueError("model route must be bounded and capable")


@dataclass(frozen=True, slots=True)
class EvidenceBlockRef:
    evidence_snapshot_id: UUID
    source_id: UUID
    source_label: str
    block_index: int
    block_kind: str
    block_digest: str
    text: str
    stale: bool = False

    def __post_init__(self) -> None:
        if self.block_index < 0 or not self.text.strip() or not DIGEST.fullmatch(self.block_digest):
            raise ValueError("evidence block reference is invalid")

    def identity(self) -> dict[str, object]:
        return {
            "evidence_snapshot_id": str(self.evidence_snapshot_id),
            "source_id": str(self.source_id),
            "block_index": self.block_index,
            "block_kind": self.block_kind,
            "block_digest": self.block_digest,
            "stale": self.stale,
        }


@dataclass(frozen=True, slots=True)
class ModelContext:
    system_instructions: str
    product_context: Mapping[str, object]
    evidence_blocks: tuple[EvidenceBlockRef, ...]
    context_digest: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_context", MappingProxyType(dict(self.product_context)))
        if not self.evidence_blocks or not DIGEST.fullmatch(self.context_digest):
            raise ValueError("model context requires evidence and a digest")


@dataclass(frozen=True, slots=True)
class ModelInvocation:
    route: ModelRoute
    system_instructions: str
    trusted_product_context: Mapping[str, object]
    untrusted_evidence: tuple[EvidenceBlockRef, ...]
    output_schema: Mapping[str, object]
    output_contract_key: str
    output_contract_version: int
    max_output_tokens: int
    reasoning_effort: str


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.total_tokens) < 0:
            raise ValueError("model usage cannot be negative")
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("total tokens cannot be smaller than input plus output")


@dataclass(frozen=True, slots=True)
class ModelInvocationResult:
    output: Mapping[str, object]
    provider_response_id: str | None
    usage: ModelUsage
    provider: str
    model: str
    status: str = "completed"


@dataclass(frozen=True, slots=True)
class ModelAttempt:
    tenant_id: UUID
    agent_run_id: UUID
    attempt_number: int
    workload_id: str
    model_route_version: str
    pricing_version: str
    provider: str
    model: str
    claimed_at: datetime
    lease_expires_at: datetime
    id: UUID = field(default_factory=uuid4)
    status: ModelAttemptStatus = ModelAttemptStatus.CLAIMED
    provider_started_at: datetime | None = None
    response_recorded_at: datetime | None = None
    finished_at: datetime | None = None
    provider_response_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")
    unknown_cost: Decimal = Decimal("0")
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if (
            self.attempt_number < 1
            or not self.workload_id.strip()
            or not self.model_route_version.strip()
            or not self.pricing_version.strip()
            or not self.provider.strip()
            or not self.model.strip()
            or self.lease_expires_at <= self.claimed_at
        ):
            raise ValueError("model attempt identity and lease must be explicit")
        if min(self.input_tokens, self.output_tokens, self.total_tokens) < 0:
            raise ValueError("model attempt usage cannot be negative")
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("model attempt total usage is inconsistent")
        if self.estimated_cost < 0 or self.unknown_cost < 0:
            raise ValueError("model attempt cost cannot be negative")
        pre_provider = self.status is ModelAttemptStatus.CLAIMED
        provider_active = self.status is ModelAttemptStatus.PROVIDER_STARTED
        response_recorded = self.status is ModelAttemptStatus.RESPONSE_RECORDED
        succeeded = self.status is ModelAttemptStatus.SUCCEEDED
        failed_no_response = self.status is ModelAttemptStatus.FAILED_NO_RESPONSE
        unknown = self.status is ModelAttemptStatus.UNKNOWN
        valid_lifecycle = (
            (
                pre_provider
                and self.provider_started_at is None
                and self.response_recorded_at is None
                and self.finished_at is None
            )
            or (
                provider_active
                and self.provider_started_at is not None
                and self.response_recorded_at is None
                and self.finished_at is None
            )
            or (
                response_recorded
                and self.provider_started_at is not None
                and self.response_recorded_at is not None
                and self.finished_at is None
            )
            or (
                succeeded
                and self.provider_started_at is not None
                and self.response_recorded_at is not None
                and self.finished_at is not None
            )
            or (
                failed_no_response
                and self.provider_started_at is None
                and self.response_recorded_at is None
                and self.finished_at is not None
            )
            or (
                unknown
                and self.provider_started_at is not None
                and self.response_recorded_at is None
                and self.finished_at is not None
            )
        )
        if not valid_lifecycle:
            raise ValueError("model attempt lifecycle timestamps are inconsistent")


@dataclass(frozen=True, slots=True)
class StrandedAgentRun:
    run: AgentRun
    attempt: ModelAttempt
    classification: RecoveryClassification


def classify_stranded_attempt(attempt: ModelAttempt) -> RecoveryClassification:
    if attempt.status is ModelAttemptStatus.CLAIMED:
        return RecoveryClassification.SAFE_BEFORE_PROVIDER
    if attempt.status is ModelAttemptStatus.RESPONSE_RECORDED:
        return RecoveryClassification.RESPONSE_RECORDED
    return RecoveryClassification.PROVIDER_OUTCOME_UNKNOWN


@dataclass(frozen=True, slots=True)
class Citation:
    evidence_snapshot_id: UUID
    block_index: int
    block_digest: str

    def __post_init__(self) -> None:
        if (
            self.block_index < 0
            or self.block_index > 499
            or not DIGEST.fullmatch(self.block_digest)
        ):
            raise InvalidModelOutput("citation identity is invalid")


@dataclass(frozen=True, slots=True)
class Finding:
    key: str
    category: FindingCategory
    statement: str
    confidence: Confidence
    citations: tuple[Citation, ...]
    scope: str
    implication: str | None = None

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.key)
            or not self.statement.strip()
            or len(self.statement) > MAX_STATEMENT_CHARACTERS
            or not self.scope.strip()
            or len(self.scope) > 500
            or (self.implication is not None and len(self.implication) > 1500)
            or not 1 <= len(self.citations) <= MAX_CITATIONS_PER_FINDING
        ):
            raise InvalidModelOutput("finding violates the bounded output contract")

    def semantic(self) -> dict[str, object]:
        return {
            "key": self.key,
            "category": self.category.value,
            "statement": self.statement,
            "confidence": self.confidence.value,
            "citations": [
                {
                    "evidence_snapshot_id": str(c.evidence_snapshot_id),
                    "block_index": c.block_index,
                    "block_digest": c.block_digest,
                }
                for c in self.citations
            ],
            "scope": self.scope,
            "implication": self.implication,
        }


@dataclass(frozen=True, slots=True)
class RecommendedSource:
    category: str
    reason: str
    suggested_query: str

    def __post_init__(self) -> None:
        if (
            not self.category.strip()
            or len(self.category) > 64
            or not self.reason.strip()
            or len(self.reason) > 1000
            or not self.suggested_query.strip()
            or len(self.suggested_query) > 500
        ):
            raise InvalidModelOutput("recommended source violates output bounds")


@dataclass(frozen=True, slots=True)
class ResearchSnapshot:
    tenant_id: UUID
    product_id: UUID
    agent_run_id: UUID
    product_snapshot_id: UUID
    product_snapshot_digest: str
    research_context_digest: str
    findings: tuple[Finding, ...]
    research_gaps: tuple[str, ...]
    recommended_next_sources: tuple[RecommendedSource, ...]
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    valid_until: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(days=7))

    def __post_init__(self) -> None:
        if not 1 <= len(self.findings) <= MAX_FINDINGS:
            raise InvalidModelOutput("research snapshot must contain 1 to 30 findings")
        if len({item.key for item in self.findings}) != len(self.findings):
            raise InvalidModelOutput("finding keys must be unique")
        if (
            len(self.research_gaps) > MAX_GAPS
            or len(self.recommended_next_sources) > MAX_RECOMMENDED_SOURCES
            or any(not item.strip() or len(item) > 1000 for item in self.research_gaps)
        ):
            raise InvalidModelOutput("research output exceeds bounded collection limits")
        if self.semantic_digest != canonical_digest(self.semantic_content()):
            raise InvalidModelOutput("research snapshot digest does not match content")

    def semantic_content(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "product_snapshot_id": str(self.product_snapshot_id),
            "product_snapshot_digest": self.product_snapshot_digest,
            "research_context_digest": self.research_context_digest,
            "findings": [item.semantic() for item in self.findings],
            "research_gaps": list(self.research_gaps),
            "recommended_next_sources": [
                {
                    "category": item.category,
                    "reason": item.reason,
                    "suggested_query": item.suggested_query,
                }
                for item in self.recommended_next_sources
            ],
        }


@dataclass(frozen=True, slots=True)
class AgentRun:
    tenant_id: UUID
    requested_agent_definition_id: UUID
    resolved_agent_definition_id: UUID
    agent_version_id: UUID
    agent_version_number: int
    agent_configuration_digest: str
    prompt_revision: str
    product_id: UUID
    product_snapshot_id: UUID
    product_snapshot_digest: str
    product_snapshot_schema_version: int
    research_context_digest: str
    context_digest: str
    selected_evidence: tuple[Mapping[str, object], ...]
    model_profile_key: str
    output_contract_key: str
    output_contract_version: int
    correlation_id: UUID
    initiated_by_actor_kind: str
    initiated_by_actor_id: UUID
    period_start: datetime
    reserved_cost: Decimal
    currency: str
    idempotency_key: str
    recovery_of_run_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)
    status: AgentRunStatus = AgentRunStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    executed_by_workload_id: str | None = None
    resolved_provider: str | None = None
    resolved_model: str | None = None
    resolved_model_route_version: str | None = None
    pricing_version: str | None = None
    reasoning_effort: str | None = None
    max_output_tokens: int | None = None
    max_total_tokens: int = 0
    model_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")
    provider_response_id: str | None = None
    result_ref: str | None = None
    failure_code: str | None = None
    operational_status: str = "normal"
    is_stranded: bool = False

    def __post_init__(self) -> None:
        for value in (
            self.agent_configuration_digest,
            self.product_snapshot_digest,
            self.research_context_digest,
            self.context_digest,
        ):
            if not DIGEST.fullmatch(value):
                raise ValueError("AgentRun provenance digest is invalid")
        if not self.selected_evidence:
            raise ValueError("AgentRun requires frozen selected evidence references")
        if self.max_total_tokens < 0:
            raise ValueError("AgentRun token budget cannot be negative")
        if self.recovery_of_run_id == self.id:
            raise ValueError("AgentRun recovery lineage cannot point to itself")
        if self.operational_status not in {"normal", "recovery_required"} or self.is_stranded != (
            self.operational_status == "recovery_required"
        ):
            raise ValueError("AgentRun operational state is inconsistent")


def parse_research_output(
    output: Mapping[str, object],
    *,
    run: AgentRun,
    selected_blocks: tuple[EvidenceBlockRef, ...],
    now: datetime | None = None,
) -> ResearchSnapshot:
    try:
        raw_findings = output["findings"]
        raw_gaps = output["research_gaps"]
        raw_sources = output["recommended_next_sources"]
        if (
            not isinstance(raw_findings, list)
            or not isinstance(raw_gaps, list)
            or not isinstance(raw_sources, list)
        ):
            raise TypeError
        findings = tuple(
            Finding(
                key=str(item["key"]),
                category=FindingCategory(str(item["category"])),
                statement=str(item["statement"]),
                confidence=Confidence(str(item["confidence"])),
                citations=tuple(
                    Citation(
                        UUID(str(citation["evidence_snapshot_id"])),
                        int(citation["block_index"]),
                        str(citation["block_digest"]),
                    )
                    for citation in item["citations"]
                ),
                scope=str(item["scope"]),
                implication=(
                    str(item["implication"]) if item.get("implication") is not None else None
                ),
            )
            for item in raw_findings
            if isinstance(item, dict)
        )
        gaps = tuple(str(item) for item in raw_gaps)
        sources = tuple(
            RecommendedSource(
                str(item["category"]), str(item["reason"]), str(item["suggested_query"])
            )
            for item in raw_sources
            if isinstance(item, dict)
        )
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidModelOutput("provider output does not match research contract") from error
    allowed = {
        (item.evidence_snapshot_id, item.block_index, item.block_digest) for item in selected_blocks
    }
    if any(
        (citation.evidence_snapshot_id, citation.block_index, citation.block_digest) not in allowed
        for finding in findings
        for citation in finding.citations
    ):
        raise InvalidResearchCitation("model cited evidence outside the frozen context")
    at = now or datetime.now(UTC)
    semantic = {
        "schema_version": 1,
        "product_snapshot_id": str(run.product_snapshot_id),
        "product_snapshot_digest": run.product_snapshot_digest,
        "research_context_digest": run.research_context_digest,
        "findings": [item.semantic() for item in findings],
        "research_gaps": list(gaps),
        "recommended_next_sources": [
            {
                "category": item.category,
                "reason": item.reason,
                "suggested_query": item.suggested_query,
            }
            for item in sources
        ],
    }
    return ResearchSnapshot(
        tenant_id=run.tenant_id,
        product_id=run.product_id,
        agent_run_id=run.id,
        product_snapshot_id=run.product_snapshot_id,
        product_snapshot_digest=run.product_snapshot_digest,
        research_context_digest=run.research_context_digest,
        findings=findings,
        research_gaps=gaps,
        recommended_next_sources=sources,
        semantic_digest=canonical_digest(semantic),
        created_at=at,
        valid_until=at + timedelta(days=7),
    )
