from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import monotonic
from types import TracebackType
from typing import Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from jsonschema import Draft202012Validator

from creative_marketer.agent_governance.domain import AgentVersionConfiguration, BudgetPeriod
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditActorKind, AuditOutcome, AuditRecord, AuditScopeKind
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.catalog.domain import ProductKnowledgeSnapshot
from creative_marketer.creative.application import (
    CREATIVE_CONTRACT_KEY,
    CREATIVE_CONTRACT_VERSION,
    build_creative_context,
    load_creative_output_schema,
    validate_creative_output,
)
from creative_marketer.creative.domain import (
    ChannelIntent,
    CreativeBriefIncomplete,
    CreativeError,
    CreativeResearchRefreshRequired,
    CreativeStrategyContext,
    CreativeStrategyRequest,
    ProductClaimRef,
)
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import DomainEvent, EventScopeKind, tenant_event
from creative_marketer.identity.application.authentication import ActorKind, ExecutionContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.observability.ports import NullTelemetry, OperationalTelemetry
from creative_marketer.research.domain import (
    EvidenceSnapshot,
    ResearchCategory,
    ResearchContextManifest,
)

from .domain import (
    AgentRun,
    AgentRunConflict,
    AgentRunDenied,
    AgentRunNotFound,
    AgentRunNotReady,
    AgentRunRecoveryConflict,
    AgentRunStatus,
    AgentRuntimeError,
    BudgetExceeded,
    EvidenceBlockRef,
    ModelAttempt,
    ModelCapabilityUnavailable,
    ModelContext,
    ModelInvocation,
    ModelInvocationResult,
    ModelPricing,
    ModelProviderError,
    ModelRoute,
    ModelRouteUnavailable,
    RecoveryAgentUnavailable,
    RecoveryBlockedBudget,
    RecoveryClassification,
    ResearchSnapshot,
    StrandedAgentRun,
    UnknownCostReconciliationConflict,
    canonical_digest,
    parse_research_output,
)

MAX_EVIDENCE_SOURCES = 20
MAX_BLOCKS_PER_SOURCE = 10
MAX_EVIDENCE_BLOCKS = 120
MAX_EVIDENCE_TEXT_CHARACTERS = 120_000
MAX_PROVIDER_TRANSPORT_ATTEMPTS = 2
MODEL_ATTEMPT_LEASE = timedelta(minutes=15)
RESEARCH_CONTRACT_KEY = "research.research_snapshot"
RESEARCH_CONTRACT_VERSION = 1
_CATEGORY_PRIORITY = {
    ResearchCategory.COMPETITOR: 0,
    ResearchCategory.PRODUCT_PAGE: 1,
    ResearchCategory.PRICING: 2,
    ResearchCategory.LANDING_PAGE: 3,
    ResearchCategory.REVIEW: 4,
}


class AgentCapabilityUnavailable(AgentRuntimeError):
    code = "AGENT_CAPABILITY_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    """Capability-owned durable result metadata consumed by the common lifecycle."""

    value: object
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    occurred_at: datetime
    event_payload: Mapping[str, object]
    metric_name: str
    metric_value: int


class AgentCapabilityHandler(Protocol):
    """Trusted capability seam; the common runtime alone performs provider execution."""

    agent_type: str
    output_contract_key: str
    output_contract_version: int

    def output_schema(self) -> Mapping[str, object]: ...

    def invocation(
        self, run: AgentRun, route: ModelRoute, context: ModelContext
    ) -> ModelInvocation: ...

    def validate_result(
        self, output: Mapping[str, object], run: AgentRun, context: ModelContext
    ) -> CapabilityResult: ...


class AgentCapabilityRegistry:
    """Explicit code-owned registry; database configuration cannot import executable code."""

    def __init__(self, handlers: tuple[AgentCapabilityHandler, ...]) -> None:
        self._handlers = {item.agent_type: item for item in handlers}
        if len(self._handlers) != len(handlers):
            raise ValueError("agent capability types must be unique")

    def resolve(self, agent_type: str) -> AgentCapabilityHandler:
        handler = self._handlers.get(agent_type)
        if handler is None:
            raise AgentCapabilityUnavailable("agent capability is not installed")
        return handler


@dataclass(frozen=True, slots=True)
class ResearcherCapability:
    agent_type: str = "researcher"
    output_contract_key: str = RESEARCH_CONTRACT_KEY
    output_contract_version: int = RESEARCH_CONTRACT_VERSION

    def output_schema(self) -> Mapping[str, object]:
        return load_output_schema()

    def invocation(
        self, run: AgentRun, route: ModelRoute, context: ModelContext
    ) -> ModelInvocation:
        return ModelInvocation(
            route,
            context.system_instructions,
            context.product_context,
            context.evidence_blocks,
            self.output_schema(),
            run.output_contract_key,
            run.output_contract_version,
            route.max_output_tokens,
            route.reasoning_effort,
            output_task=context.output_task,
        )

    def validate_result(
        self, output: Mapping[str, object], run: AgentRun, context: ModelContext
    ) -> CapabilityResult:
        snapshot = parse_research_output(output, run=run, selected_blocks=context.evidence_blocks)
        return CapabilityResult(
            value=snapshot,
            event_type="research.snapshot.created.v1",
            aggregate_type="research_snapshot",
            aggregate_id=snapshot.id,
            occurred_at=snapshot.created_at,
            event_payload={
                "research_snapshot_id": str(snapshot.id),
                "agent_run_id": str(run.id),
                "product_id": str(run.product_id),
                "requested_agent_definition_id": str(run.requested_agent_definition_id),
                "agent_version_id": str(run.agent_version_id),
                "product_snapshot_digest": run.product_snapshot_digest,
                "research_context_digest": snapshot.research_context_digest,
                "semantic_digest": snapshot.semantic_digest,
            },
            metric_name="researcher.findings",
            metric_value=len(snapshot.findings),
        )


@dataclass(frozen=True, slots=True)
class CreativeStrategistCapability:
    agent_type: str = "creative_strategist"
    output_contract_key: str = CREATIVE_CONTRACT_KEY
    output_contract_version: int = CREATIVE_CONTRACT_VERSION

    def output_schema(self) -> Mapping[str, object]:
        return load_creative_output_schema()

    def invocation(
        self, run: AgentRun, route: ModelRoute, context: ModelContext
    ) -> ModelInvocation:
        return ModelInvocation(
            route,
            context.system_instructions,
            {},
            (),
            self.output_schema(),
            run.output_contract_key,
            run.output_contract_version,
            route.max_output_tokens,
            route.reasoning_effort,
            capability_context=context.capability_context,
            output_task=context.output_task,
        )

    def validate_result(
        self, output: Mapping[str, object], run: AgentRun, context: ModelContext
    ) -> CapabilityResult:
        values = context.capability_context
        if values is None:
            raise AgentRunNotReady("Creative capability context is unavailable")
        research_ref = next(
            (item for item in run.input_context_refs if item.get("kind") == "research_snapshot"),
            None,
        )
        request_ref = next(
            (item for item in run.input_context_refs if item.get("kind") == "strategy_request"),
            None,
        )
        if research_ref is None or request_ref is None:
            raise AgentRunNotReady("Creative input references are incomplete")
        claims_value = values.get("product_claim_refs", ())
        findings_value = values.get("research_findings", ())
        assets_value = values.get("available_assets", ())
        gaps_value = values.get("research_gaps", ())
        claims = claims_value if isinstance(claims_value, (list, tuple)) else ()
        findings = findings_value if isinstance(findings_value, (list, tuple)) else ()
        assets = assets_value if isinstance(assets_value, (list, tuple)) else ()
        gaps = gaps_value if isinstance(gaps_value, (list, tuple)) else ()
        product_value = values.get("product_brand_data", {})
        product = dict(product_value) if isinstance(product_value, Mapping) else {}
        creative_context = CreativeStrategyContext(
            run.product_snapshot_id,
            run.product_snapshot_digest,
            run.product_snapshot_schema_version,
            UUID(str(research_ref["id"])),
            str(research_ref["digest"]),
            UUID(str(research_ref["agent_run_id"])),
            product,
            tuple(dict(item) for item in findings if isinstance(item, Mapping)),
            tuple(str(item) for item in gaps),
            tuple(dict(item) for item in assets if isinstance(item, Mapping)),
            tuple(
                ProductClaimRef(str(item["key"]), str(item["text"]))
                for item in claims
                if isinstance(item, Mapping)
            ),
            CreativeStrategyRequest(
                int(str(request_ref["concept_count"])),
                ChannelIntent(str(request_ref["channel_intent"])),
            ),
            run.input_context_digest,
        )
        concept_set = validate_creative_output(
            output,
            tenant_id=run.tenant_id,
            product_id=run.product_id,
            agent_run_id=run.id,
            context=creative_context,
        )
        return CapabilityResult(
            value=concept_set,
            event_type="creative.concept_set.created.v1",
            aggregate_type="creative_concept_set",
            aggregate_id=concept_set.id,
            occurred_at=concept_set.created_at,
            event_payload={
                "concept_set_id": str(concept_set.id),
                "agent_run_id": str(run.id),
                "product_id": str(run.product_id),
                "requested_agent_definition_id": str(run.requested_agent_definition_id),
                "agent_version_id": str(run.agent_version_id),
                "concept_count": len(concept_set.concepts),
                "semantic_digest": concept_set.semantic_digest,
            },
            metric_name="creative.concepts",
            metric_value=len(concept_set.concepts),
        )


def default_capability_registry() -> AgentCapabilityRegistry:
    handlers = cast(
        tuple[AgentCapabilityHandler, ...],
        (ResearcherCapability(), CreativeStrategistCapability()),
    )
    return AgentCapabilityRegistry(handlers)


def initial_researcher_route() -> ModelRoute:
    """Versioned operational route; Agent Registry remains provider-neutral."""
    return ModelRoute(
        profile_key="research_balanced",
        route_version="openai-gpt-5.6-terra-2026-09",
        provider="openai",
        model="gpt-5.6-terra",
        capabilities=frozenset({"text", "reasoning", "structured_output"}),
        reasoning_effort="medium",
        max_output_tokens=6000,
        pricing=ModelPricing("openai-2026-09-11", Decimal("2.00"), Decimal("12.00"), "USD"),
    )


def initial_creative_strategist_route() -> ModelRoute:
    return ModelRoute(
        profile_key="creative_balanced",
        route_version="openai-gpt-5.6-terra-creative-2026-09",
        provider="openai",
        model="gpt-5.6-terra",
        capabilities=frozenset({"text", "reasoning", "structured_output"}),
        reasoning_effort="medium",
        max_output_tokens=8000,
        pricing=ModelPricing("openai-2026-09-11", Decimal("2.00"), Decimal("12.00"), "USD"),
    )


class ModelProvider(Protocol):
    async def generate_structured(self, invocation: ModelInvocation) -> ModelInvocationResult: ...


class ModelRouter:
    def __init__(self, routes: tuple[ModelRoute, ...]) -> None:
        self._routes = {route.profile_key: route for route in routes}
        if len(self._routes) != len(routes):
            raise ValueError("model route profile keys must be unique")

    def resolve(self, profile_key: str, required_capabilities: tuple[str, ...]) -> ModelRoute:
        route = self._routes.get(profile_key)
        if route is None:
            raise ModelRouteUnavailable("logical model profile is not configured")
        if not set(required_capabilities).issubset(route.capabilities):
            raise ModelCapabilityUnavailable("model route lacks required capabilities")
        return route


class ModelProviderRegistry:
    def __init__(self, providers: Mapping[str, ModelProvider]) -> None:
        self._providers = dict(providers)

    def resolve(self, provider: str) -> ModelProvider:
        try:
            return self._providers[provider]
        except KeyError as error:
            raise ModelRouteUnavailable("configured model provider is unavailable") from error


@dataclass(frozen=True, slots=True)
class ResolvedResearcher:
    requested_definition_id: UUID
    resolved_definition_id: UUID
    version_id: UUID
    version_number: int
    configuration_digest: str
    configuration: AgentVersionConfiguration


@dataclass(frozen=True, slots=True)
class ResearcherPreparation:
    researcher: ResolvedResearcher
    product_snapshot: ProductKnowledgeSnapshot
    manifest: ResearchContextManifest
    evidence: tuple[tuple[EvidenceSnapshot, ResearchCategory, str], ...]


@dataclass(frozen=True, slots=True)
class CreativePreparation:
    strategist: ResolvedResearcher
    product_snapshot: ProductKnowledgeSnapshot
    research_snapshot: ResearchSnapshot
    research_freshness: str
    brief_completeness: int


@dataclass(frozen=True, slots=True)
class WorkloadIdentity:
    workload_id: str
    environment: str
    actor_id: UUID = field(init=False)

    def __post_init__(self) -> None:
        if not self.workload_id.strip() or len(self.workload_id) > 128:
            raise ValueError("workload identity must be explicit and bounded")
        object.__setattr__(
            self,
            "actor_id",
            uuid5(NAMESPACE_URL, f"creative-marketer:{self.environment}:{self.workload_id}"),
        )


class WorkloadIdentityProvider(Protocol):
    async def current(self) -> WorkloadIdentity: ...


@dataclass(frozen=True, slots=True)
class RecoveryOperator:
    tenant_id: UUID
    workload: WorkloadIdentity
    correlation_id: UUID


class RecoveryOperatorProvider(Protocol):
    async def current(self) -> RecoveryOperator: ...


class AgentRunRepository(Protocol):
    async def get_by_idempotency(self, idempotency_key: str) -> AgentRun | None: ...
    async def prepare_researcher(self, product_id: UUID) -> ResearcherPreparation | None: ...
    async def prepare_creative(self, product_id: UUID) -> CreativePreparation | None: ...
    async def active_for_product(
        self, product_id: UUID, definition_id: UUID
    ) -> AgentRun | None: ...
    async def add(self, run: AgentRun) -> bool: ...
    async def get(self, run_id: UUID, *, for_update: bool = False) -> AgentRun | None: ...
    async def list_for_product(self, product_id: UUID) -> tuple[AgentRun, ...]: ...
    async def lock_period_budgets(
        self, definition_id: UUID, period_starts: tuple[datetime, ...]
    ) -> None: ...
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
    ) -> None: ...
    async def claim(
        self,
        run_id: UUID,
        workload_id: str,
        route: ModelRoute,
        lease_expires_at: datetime,
    ) -> tuple[AgentRun, ModelAttempt] | None: ...
    async def mark_provider_started(
        self, run_id: UUID, attempt_id: UUID, workload_id: str
    ) -> ModelAttempt: ...
    async def record_provider_response(
        self,
        run_id: UUID,
        attempt_id: UUID,
        workload_id: str,
        result: ModelInvocationResult,
        cost: Decimal,
    ) -> ModelAttempt: ...
    async def mark_attempt_unknown(
        self, run_id: UUID, attempt_id: UUID, workload_id: str, failure_code: str
    ) -> ModelAttempt: ...
    async def resolve_context(self, run: AgentRun) -> ModelContext: ...
    async def resolve_creative_context(self, run: AgentRun) -> CreativeStrategyContext: ...
    async def finish_success(
        self,
        run: AgentRun,
        result: ModelInvocationResult,
        snapshot: object,
        cost: Decimal,
        attempt_id: UUID,
        workload_id: str,
    ) -> AgentRun: ...
    async def finish_failure(
        self,
        run: AgentRun,
        *,
        failure_code: str,
        result: ModelInvocationResult | None = None,
        cost: Decimal = Decimal("0"),
        attempt_id: UUID,
        workload_id: str,
    ) -> AgentRun: ...
    async def operational_status(self, run: AgentRun, now: datetime) -> AgentRun: ...
    async def find_stranded(self, now: datetime) -> tuple[StrandedAgentRun, ...]: ...
    async def get_stranded(
        self, run_id: UUID, now: datetime, *, for_update: bool = False
    ) -> StrandedAgentRun | None: ...
    async def recovery_configuration(self, run: AgentRun) -> AgentVersionConfiguration | None: ...
    async def abandon_stranded(
        self,
        stranded: StrandedAgentRun,
        *,
        workload_id: str,
        failure_code: str,
    ) -> AgentRun: ...
    async def add_recovery_run(self, run: AgentRun) -> bool: ...
    async def reconcile_unknown_cost(
        self,
        stranded_run: AgentRun,
        *,
        actual_cost: Decimal,
        workload_id: str,
    ) -> None: ...
    async def get_snapshot(self, snapshot_id: UUID) -> ResearchSnapshot | None: ...
    async def list_snapshots(self, product_id: UUID) -> tuple[ResearchSnapshot, ...]: ...
    async def snapshot_freshness(self, snapshot: ResearchSnapshot) -> str: ...


class AgentRuntimeUnitOfWork(Protocol):
    runs: AgentRunRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> AgentRuntimeUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class AgentRuntimeUnitOfWorkFactory(Protocol):
    def __call__(self, tenant_id: UUID) -> AgentRuntimeUnitOfWork: ...


def _period_start(now: datetime, period: BudgetPeriod) -> datetime:
    at = now.astimezone(UTC)
    if period is BudgetPeriod.DAILY:
        return at.replace(hour=0, minute=0, second=0, microsecond=0)
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def select_evidence_blocks(
    evidence: tuple[tuple[EvidenceSnapshot, ResearchCategory, str], ...],
    *,
    now: datetime | None = None,
) -> tuple[EvidenceBlockRef, ...]:
    at = now or datetime.now(UTC)
    ordered = sorted(
        evidence,
        key=lambda item: (
            _CATEGORY_PRIORITY.get(item[1], 99),
            -item[0].captured_at.timestamp(),
            str(item[0].source_id),
        ),
    )[:MAX_EVIDENCE_SOURCES]
    selected: list[EvidenceBlockRef] = []
    characters = 0
    for snapshot, _category, source_label in ordered:
        for block in snapshot.blocks[:MAX_BLOCKS_PER_SOURCE]:
            if len(selected) >= MAX_EVIDENCE_BLOCKS:
                return tuple(selected)
            remaining = MAX_EVIDENCE_TEXT_CHARACTERS - characters
            if remaining <= 0:
                return tuple(selected)
            text = block.text[:remaining]
            if not text.strip():
                continue
            digest = canonical_digest(
                {
                    "evidence_snapshot_id": str(snapshot.id),
                    "block_index": block.ordinal,
                    "kind": block.kind.value,
                    "text": block.text,
                }
            )
            selected.append(
                EvidenceBlockRef(
                    snapshot.id,
                    snapshot.source_id,
                    source_label,
                    block.ordinal,
                    block.kind.value,
                    digest,
                    text,
                    snapshot.captured_at < at - timedelta(days=30),
                )
            )
            characters += len(text)
    return tuple(selected)


def build_context(
    preparation: ResearcherPreparation,
    blocks: tuple[EvidenceBlockRef, ...],
) -> ModelContext:
    configuration = preparation.researcher.configuration
    # ProductKnowledgeSnapshot already contains only intentional Product Brain fields. Binary
    # Asset data and storage internals are absent; V2 contains bounded metadata only.
    product = dict(preparation.product_snapshot.content)
    digest = canonical_digest(
        {
            "schema_version": 1,
            "agent_configuration_digest": preparation.researcher.configuration_digest,
            "product_snapshot_digest": preparation.product_snapshot.digest,
            "research_context_digest": preparation.manifest.digest,
            "evidence_blocks": [item.identity() for item in blocks],
        }
    )
    return ModelContext(configuration.system_instructions, product, blocks, digest)


def load_output_schema() -> Mapping[str, object]:
    path = Path(__file__).with_name("schemas") / "research.research_snapshot.v1.json"
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_json(child) for child in value]
    return value


def _compact_json(value: object) -> object:
    """Remove absent snapshot fields from provider context without changing provenance."""

    if isinstance(value, Mapping):
        compacted = {str(key): _compact_json(child) for key, child in value.items()}
        return {
            key: child
            for key, child in compacted.items()
            if child is not None and child != "" and child != [] and child != {}
        }
    if isinstance(value, (tuple, list)):
        return [_compact_json(child) for child in value]
    return value


def conservative_input_token_bound(context: ModelContext) -> int:
    """UTF-8 bytes upper-bound tokenizer input without provider-specific dependencies."""

    document = {
        "system_instructions": context.system_instructions,
        "trusted_product_context": _plain_json(context.product_context),
        "untrusted_external_evidence": [
            {"reference": item.identity(), "source_label": item.source_label, "text": item.text}
            for item in context.evidence_blocks
        ],
        "output_schema": load_output_schema(),
    }
    return len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode())


def build_creative_model_context(
    preparation: CreativePreparation, request: CreativeStrategyRequest
) -> tuple[CreativeStrategyContext, ModelContext]:
    creative = build_creative_context(
        preparation.product_snapshot, preparation.research_snapshot, request
    )
    sections: dict[str, object] = {
        "product_brand_data": _compact_json(creative.product_context),
        "available_assets": [dict(item) for item in creative.asset_manifest],
        "research_findings": [dict(item) for item in creative.research_findings],
        "research_gaps": list(creative.research_gaps),
        "product_claim_refs": [
            {"key": item.key, "text": item.text} for item in creative.product_claims
        ],
        "strategy_request": {
            "concept_count": request.concept_count,
            "channel_intent": request.channel_intent.value,
        },
    }
    model = ModelContext(
        preparation.strategist.configuration.system_instructions,
        {},
        (),
        creative.context_digest,
        capability_context=sections,
        output_task=(
            "Return only the creative.creative_concept_set.v1 structure. Produce exactly "
            f"{request.concept_count} materially different short-form vertical-video concepts."
        ),
    )
    return creative, model


def conservative_creative_input_token_bound(context: ModelContext) -> int:
    document = {
        "system_instructions": context.system_instructions,
        "context_sections": _plain_json(context.capability_context or {}),
        "output_task": context.output_task,
        "output_schema": load_creative_output_schema(),
    }
    return len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode())


async def _event(
    uow: AgentRuntimeUnitOfWork,
    context: ExecutionContext,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: dict[str, object],
) -> None:
    contracts = EventContractRegistry()
    await uow.outbox.append(
        tenant_event(
            context,
            event_type=event_type,
            schema_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            payload_schema_digest=contracts.schema_digest(event_type),
            occurred_at=datetime.now(UTC),
            agent_definition_id=UUID(str(payload["requested_agent_definition_id"])),
            agent_version_id=UUID(str(payload["agent_version_id"])),
            agent_run_id=UUID(str(payload["agent_run_id"])),
        )
    )


@dataclass(slots=True)
class AgentRunService:
    uow_factory: AgentRuntimeUnitOfWorkFactory
    router: ModelRouter
    providers: ModelProviderRegistry
    workload_identity_provider: WorkloadIdentityProvider
    telemetry: OperationalTelemetry = field(default_factory=NullTelemetry)
    capabilities: AgentCapabilityRegistry = field(default_factory=default_capability_registry)

    async def request_researcher(
        self, context: ExecutionContext, *, product_id: UUID, idempotency_key: str
    ) -> AgentRun:
        if (
            context.membership_status is not MembershipStatus.ACTIVE
            or context.membership_role
            not in {
                MembershipRole.OWNER,
                MembershipRole.ADMIN,
            }
        ):
            raise AgentRunDenied("starting a billed AgentRun requires owner or admin")
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ValueError("idempotency key is required and bounded")
        now = datetime.now(UTC)
        async with self.uow_factory(context.tenant_id) as uow:
            replay = await uow.runs.get_by_idempotency(idempotency_key)
            if replay is not None:
                if replay.product_id != product_id:
                    raise AgentRunNotReady("idempotency key is bound to another request")
                return replay
            preparation = await uow.runs.prepare_researcher(product_id)
            if preparation is None:
                raise AgentRunNotReady(
                    "product snapshot, evidence, or active Researcher is missing"
                )
            cfg = preparation.researcher.configuration
            if (
                cfg.output_contract_key != RESEARCH_CONTRACT_KEY
                or cfg.output_contract_version != RESEARCH_CONTRACT_VERSION
                or cfg.model_policy.max_turns != 1
                or not cfg.model_policy.structured_output_required
                or cfg.model_policy.fallback_allowed
                or set(cfg.model_policy.required_capabilities)
                != {"text", "reasoning", "structured_output"}
                or cfg.allowed_tool_keys
                or cfg.run_budget_policy.max_tool_calls != 0
                or cfg.run_budget_policy.max_model_calls != 1
                or cfg.memory_scopes
                or set(cfg.read_scopes) != {"catalog.product", "research.evidence"}
                or set(cfg.write_scopes) != {"research.snapshot"}
            ):
                raise AgentRunNotReady("active Researcher configuration violates v1 invariants")
            route = self.router.resolve(
                cfg.model_policy.profile_key, cfg.model_policy.required_capabilities
            )
            self.providers.resolve(route.provider)
            if (
                route.pricing.currency != cfg.run_budget_policy.currency
                or cfg.period_budget_policy.currency != cfg.run_budget_policy.currency
            ):
                raise BudgetExceeded("model pricing currency does not match run budget")
            input_allowance = cfg.run_budget_policy.max_total_tokens - route.max_output_tokens
            worst_cost = max(
                route.pricing.cost(input_allowance, route.max_output_tokens),
                route.pricing.cost(cfg.run_budget_policy.max_total_tokens, 0),
            )
            if (
                cfg.run_budget_policy.max_total_tokens <= route.max_output_tokens
                or worst_cost > cfg.run_budget_policy.max_cost
                or cfg.run_budget_policy.max_cost <= 0
            ):
                raise BudgetExceeded("configured route cannot fit the run budget envelope")
            active = await uow.runs.active_for_product(
                product_id, preparation.researcher.requested_definition_id
            )
            if active is not None:
                return active
            blocks = select_evidence_blocks(preparation.evidence, now=now)
            if not blocks:
                raise AgentRunNotReady("Researcher requires at least one evidence block")
            model_context = build_context(preparation, blocks)
            if conservative_input_token_bound(model_context) > input_allowance:
                raise BudgetExceeded("bounded Researcher context exceeds the input token envelope")
            period_start = _period_start(now, cfg.period_budget_policy.period)
            run = AgentRun(
                tenant_id=context.tenant_id,
                requested_agent_definition_id=preparation.researcher.requested_definition_id,
                resolved_agent_definition_id=preparation.researcher.resolved_definition_id,
                agent_version_id=preparation.researcher.version_id,
                agent_version_number=preparation.researcher.version_number,
                agent_configuration_digest=preparation.researcher.configuration_digest,
                prompt_revision=cfg.prompt_revision,
                product_id=product_id,
                product_snapshot_id=preparation.product_snapshot.id,
                product_snapshot_digest=preparation.product_snapshot.digest,
                product_snapshot_schema_version=preparation.product_snapshot.schema_version,
                research_context_digest=preparation.manifest.digest,
                context_digest=model_context.context_digest,
                selected_evidence=tuple(item.identity() for item in blocks),
                model_profile_key=cfg.model_policy.profile_key,
                output_contract_key=RESEARCH_CONTRACT_KEY,
                output_contract_version=RESEARCH_CONTRACT_VERSION,
                correlation_id=context.correlation_id,
                initiated_by_actor_kind=context.actor.kind.value,
                initiated_by_actor_id=context.actor.id,
                period_start=period_start,
                reserved_cost=cfg.run_budget_policy.max_cost,
                currency=cfg.run_budget_policy.currency,
                idempotency_key=idempotency_key,
                max_total_tokens=cfg.run_budget_policy.max_total_tokens,
                created_at=now,
            )
            await uow.runs.reserve_period_budget(
                run_id=run.id,
                definition_id=run.requested_agent_definition_id,
                period_start=period_start,
                max_runs=cfg.period_budget_policy.max_runs,
                max_cost=cfg.period_budget_policy.max_cost,
                reserve_cost=run.reserved_cost,
                currency=run.currency,
            )
            if not await uow.runs.add(run):
                replay = await uow.runs.get_by_idempotency(idempotency_key)
                if replay is not None and replay.product_id == product_id:
                    return replay
                raise AgentRunConflict("another active Researcher request won the race")
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="agent.run.requested",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="agent_run",
                    resource_id=str(run.id),
                    agent_definition_id=run.requested_agent_definition_id,
                    agent_version_id=run.agent_version_id,
                    agent_run_id=run.id,
                    metadata=safe_metadata(
                        {
                            "product_id": str(product_id),
                            "product_snapshot_digest": run.product_snapshot_digest,
                            "research_context_digest": run.research_context_digest,
                            "model_profile": run.model_profile_key,
                        }
                    ),
                )
            )
            payload: dict[str, object] = {
                "agent_run_id": str(run.id),
                "product_id": str(product_id),
                "requested_agent_definition_id": str(run.requested_agent_definition_id),
                "agent_version_id": str(run.agent_version_id),
                "product_snapshot_digest": run.product_snapshot_digest,
                "research_context_digest": run.research_context_digest,
                "context_digest": run.context_digest,
            }
            await _event(uow, context, "agent.run.requested.v1", "agent_run", run.id, payload)
            await uow.commit()
            self.telemetry.count(
                "agent_runs", attributes={"agent.type": "researcher", "result": "pending"}
            )
            return run

    async def request_creative_strategist(
        self,
        context: ExecutionContext,
        *,
        product_id: UUID,
        request: CreativeStrategyRequest,
        idempotency_key: str,
    ) -> AgentRun:
        if (
            context.membership_status is not MembershipStatus.ACTIVE
            or context.membership_role not in {MembershipRole.OWNER, MembershipRole.ADMIN}
        ):
            raise AgentRunDenied("starting a billed AgentRun requires owner or admin")
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ValueError("idempotency key is required and bounded")
        now = datetime.now(UTC)
        async with self.uow_factory(context.tenant_id) as uow:
            replay = await uow.runs.get_by_idempotency(idempotency_key)
            if replay is not None:
                if replay.product_id != product_id or replay.agent_type != "creative_strategist":
                    raise AgentRunNotReady("idempotency key is bound to another request")
                return replay
            preparation = await uow.runs.prepare_creative(product_id)
            if preparation is None:
                raise AgentRunNotReady(
                    "Product snapshot V2, current Research, or active Creative "
                    "Strategist is missing"
                )
            if preparation.brief_completeness < 80:
                raise CreativeBriefIncomplete("Product Brief must be at least 80% complete")
            if preparation.research_freshness != "current":
                raise CreativeResearchRefreshRequired("ResearchSnapshot must be current")
            cfg = preparation.strategist.configuration
            if (
                cfg.output_contract_key != CREATIVE_CONTRACT_KEY
                or cfg.output_contract_version != CREATIVE_CONTRACT_VERSION
                or cfg.model_policy.max_turns != 1
                or not cfg.model_policy.structured_output_required
                or cfg.model_policy.fallback_allowed
                or set(cfg.model_policy.required_capabilities)
                != {"text", "reasoning", "structured_output"}
                or cfg.allowed_tool_keys
                or cfg.run_budget_policy.max_tool_calls != 0
                or cfg.run_budget_policy.max_model_calls != 1
                or cfg.memory_scopes
                or set(cfg.read_scopes)
                != {"catalog.product", "research.snapshot", "catalog.asset_manifest"}
                or set(cfg.write_scopes) != {"creative.concept_set"}
            ):
                raise AgentRunNotReady(
                    "active Creative Strategist configuration violates v1 invariants"
                )
            route = self.router.resolve(
                cfg.model_policy.profile_key, cfg.model_policy.required_capabilities
            )
            self.providers.resolve(route.provider)
            input_allowance = cfg.run_budget_policy.max_total_tokens - route.max_output_tokens
            worst_cost = max(
                route.pricing.cost(input_allowance, route.max_output_tokens),
                route.pricing.cost(cfg.run_budget_policy.max_total_tokens, 0),
            )
            if (
                route.pricing.currency != cfg.run_budget_policy.currency
                or cfg.period_budget_policy.currency != cfg.run_budget_policy.currency
                or input_allowance <= 0
                or worst_cost > cfg.run_budget_policy.max_cost
            ):
                raise BudgetExceeded("creative model route cannot fit the budget envelope")
            active = await uow.runs.active_for_product(
                product_id, preparation.strategist.requested_definition_id
            )
            if active is not None:
                return active
            creative_context, model_context = build_creative_model_context(preparation, request)
            if conservative_creative_input_token_bound(model_context) > input_allowance:
                raise BudgetExceeded("bounded Creative context exceeds input token envelope")
            period_start = _period_start(now, cfg.period_budget_policy.period)
            run = AgentRun(
                tenant_id=context.tenant_id,
                requested_agent_definition_id=preparation.strategist.requested_definition_id,
                resolved_agent_definition_id=preparation.strategist.resolved_definition_id,
                agent_version_id=preparation.strategist.version_id,
                agent_version_number=preparation.strategist.version_number,
                agent_configuration_digest=preparation.strategist.configuration_digest,
                prompt_revision=cfg.prompt_revision,
                product_id=product_id,
                product_snapshot_id=preparation.product_snapshot.id,
                product_snapshot_digest=preparation.product_snapshot.digest,
                product_snapshot_schema_version=2,
                research_context_digest=preparation.research_snapshot.research_context_digest,
                context_digest=creative_context.context_digest,
                selected_evidence=(),
                model_profile_key=cfg.model_policy.profile_key,
                output_contract_key=CREATIVE_CONTRACT_KEY,
                output_contract_version=CREATIVE_CONTRACT_VERSION,
                correlation_id=context.correlation_id,
                initiated_by_actor_kind=context.actor.kind.value,
                initiated_by_actor_id=context.actor.id,
                period_start=period_start,
                reserved_cost=cfg.run_budget_policy.max_cost,
                currency=cfg.run_budget_policy.currency,
                idempotency_key=idempotency_key,
                agent_type="creative_strategist",
                input_context_kind="creative_strategy.v1",
                input_context_schema_version=1,
                input_context_digest=creative_context.context_digest,
                input_context_refs=creative_context.refs(),
                max_total_tokens=cfg.run_budget_policy.max_total_tokens,
                created_at=now,
            )
            await uow.runs.reserve_period_budget(
                run_id=run.id,
                definition_id=run.requested_agent_definition_id,
                period_start=period_start,
                max_runs=cfg.period_budget_policy.max_runs,
                max_cost=cfg.period_budget_policy.max_cost,
                reserve_cost=run.reserved_cost,
                currency=run.currency,
            )
            if not await uow.runs.add(run):
                raise AgentRunConflict("another Creative Strategist request won the race")
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="creative.strategy.requested",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="agent_run",
                    resource_id=str(run.id),
                    agent_definition_id=run.requested_agent_definition_id,
                    agent_version_id=run.agent_version_id,
                    agent_run_id=run.id,
                    metadata=safe_metadata(
                        {
                            "product_id": str(product_id),
                            "input_context_digest": run.input_context_digest,
                            "concept_count": request.concept_count,
                            "channel_intent": request.channel_intent.value,
                        }
                    ),
                )
            )
            payload: dict[str, object] = {
                "agent_run_id": str(run.id),
                "product_id": str(product_id),
                "requested_agent_definition_id": str(run.requested_agent_definition_id),
                "agent_version_id": str(run.agent_version_id),
                "product_snapshot_digest": run.product_snapshot_digest,
                "research_context_digest": run.research_context_digest,
                "context_digest": run.context_digest,
            }
            await _event(uow, context, "agent.run.requested.v1", "agent_run", run.id, payload)
            await uow.commit()
            self.telemetry.count(
                "agent_runs",
                attributes={"agent.type": "creative_strategist", "result": "pending"},
            )
            return run

    async def get_run(self, context: ExecutionContext, run_id: UUID) -> AgentRun:
        async with self.uow_factory(context.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if run is None:
                raise AgentRunNotFound("AgentRun not found")
            return await uow.runs.operational_status(run, datetime.now(UTC))

    async def list_runs(self, context: ExecutionContext, product_id: UUID) -> tuple[AgentRun, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            now = datetime.now(UTC)
            return tuple(
                [
                    await uow.runs.operational_status(run, now)
                    for run in await uow.runs.list_for_product(product_id)
                ]
            )

    async def get_snapshot(self, context: ExecutionContext, snapshot_id: UUID) -> ResearchSnapshot:
        async with self.uow_factory(context.tenant_id) as uow:
            value = await uow.runs.get_snapshot(snapshot_id)
            if value is None:
                raise AgentRunNotFound("ResearchSnapshot not found")
            return value

    async def list_snapshots(
        self, context: ExecutionContext, product_id: UUID
    ) -> tuple[ResearchSnapshot, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.runs.list_snapshots(product_id)

    async def snapshot_freshness(
        self, context: ExecutionContext, snapshot: ResearchSnapshot
    ) -> str:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.runs.snapshot_freshness(snapshot)

    async def execute(self, tenant_id: UUID, run_id: UUID) -> AgentRun:
        workload = await self.workload_identity_provider.current()
        timer = monotonic()
        run: AgentRun | None = None
        result: ModelInvocationResult | None = None
        attempt: ModelAttempt | None = None
        actual_cost = Decimal("0")
        claim_committed = False
        provider_started_committed = False
        try:
            async with self.uow_factory(tenant_id) as uow:
                pending = await uow.runs.get(run_id, for_update=True)
                if pending is None:
                    raise AgentRunNotFound("AgentRun not found")
                route = self.router.resolve(
                    pending.model_profile_key, ("text", "reasoning", "structured_output")
                )
                claim = await uow.runs.claim(
                    run_id,
                    workload.workload_id,
                    route,
                    datetime.now(UTC) + MODEL_ATTEMPT_LEASE,
                )
                if claim is None:
                    existing = await uow.runs.get(run_id)
                    if existing is not None and existing.status is AgentRunStatus.SUCCEEDED:
                        return existing
                    raise AgentRunNotReady("AgentRun cannot be claimed")
                run, attempt = claim
                context = await uow.runs.resolve_context(run)
                await self._append_workload_started(uow, run, workload)
                await uow.commit()
                claim_committed = True
            capability = self.capabilities.resolve(run.agent_type)
            if (
                capability.output_contract_key != run.output_contract_key
                or capability.output_contract_version != run.output_contract_version
            ):
                raise AgentCapabilityUnavailable("AgentRun contract does not match capability")
            schema = capability.output_schema()
            provider = self.providers.resolve(route.provider)
            invocation = capability.invocation(run, route, context)
            async with self.uow_factory(tenant_id) as uow:
                attempt = await uow.runs.mark_provider_started(
                    run.id, attempt.id, workload.workload_id
                )
                await uow.commit()
                provider_started_committed = True
            with self.telemetry.span(
                "model.invoke",
                {
                    "agent.type": run.agent_type,
                    "model.profile": run.model_profile_key,
                    "provider": route.provider,
                    "model": route.model,
                },
            ) as span:
                for transport_attempt in range(1, MAX_PROVIDER_TRANSPORT_ATTEMPTS + 1):
                    try:
                        result = await provider.generate_structured(invocation)
                        break
                    except ModelProviderError as error:
                        span.record_error(error.code)
                        self.telemetry.count(
                            "model.provider_attempts",
                            attributes={
                                "provider": route.provider,
                                "result": error.code.lower(),
                            },
                        )
                        if (
                            not error.retryable
                            or transport_attempt == MAX_PROVIDER_TRANSPORT_ATTEMPTS
                        ):
                            raise
                        # Only transport outcomes with no response usage are retried. These are
                        # attempts within one bounded logical model call, not an autonomous loop.
                        await asyncio.sleep(0.25 * transport_attempt)
                    except Exception as error:
                        span.record_error("MODEL_PROVIDER_UNAVAILABLE")
                        raise ModelProviderError("model provider failed") from error
                else:  # pragma: no cover - the bounded loop either returns or raises
                    raise ModelProviderError("model provider exhausted attempts")
            actual_cost = route.pricing.cost(result.usage.input_tokens, result.usage.output_tokens)
            async with self.uow_factory(tenant_id) as uow:
                attempt = await uow.runs.record_provider_response(
                    run.id,
                    attempt.id,
                    workload.workload_id,
                    result,
                    actual_cost,
                )
                await uow.commit()
            if result.provider != route.provider or result.model != route.model:
                raise ModelRouteUnavailable(
                    "provider response did not match the frozen model route"
                )
            Draft202012Validator(schema).validate(dict(result.output))
            if result.usage.output_tokens > route.max_output_tokens:
                raise BudgetExceeded("provider output exceeded the configured output token limit")
            if result.usage.total_tokens > run.max_total_tokens:
                raise BudgetExceeded("provider usage exceeded the configured token envelope")
            if actual_cost > run.reserved_cost:
                raise BudgetExceeded("provider usage exceeded the reserved run cost")
            capability_result = capability.validate_result(result.output, run, context)
            async with self.uow_factory(tenant_id) as uow:
                completed = await uow.runs.finish_success(
                    run,
                    result,
                    capability_result.value,
                    actual_cost,
                    attempt.id,
                    workload.workload_id,
                )
                await self._append_workload_completion(uow, completed, workload, capability_result)
                await uow.commit()
            self.telemetry.count(
                "agent_runs", attributes={"agent.type": run.agent_type, "result": "succeeded"}
            )
            self.telemetry.duration(
                "agent_run.duration",
                monotonic() - timer,
                {"agent.type": run.agent_type, "result": "succeeded"},
            )
            self.telemetry.count(
                "model.calls", attributes={"provider": route.provider, "result": "succeeded"}
            )
            self.telemetry.count(
                "model.tokens",
                result.usage.total_tokens,
                {"provider": route.provider, "model.profile": run.model_profile_key},
            )
            self.telemetry.gauge(
                "model.cost",
                float(actual_cost),
                {"provider": route.provider, "currency": run.currency},
            )
            self.telemetry.count(
                capability_result.metric_name,
                capability_result.metric_value,
                {"result": "accepted"},
            )
            return completed
        except Exception as error:
            if run is not None and attempt is not None and claim_committed:
                code = (
                    error.code
                    if isinstance(error, (AgentRuntimeError, ModelProviderError, CreativeError))
                    else "MODEL_INVALID_OUTPUT"
                )
                if result is None and provider_started_committed:
                    async with self.uow_factory(tenant_id) as uow:
                        await uow.runs.mark_attempt_unknown(
                            run.id, attempt.id, workload.workload_id, code
                        )
                        await uow.commit()
                    raise AgentRunNotReady(
                        "provider outcome is ambiguous and requires operator recovery"
                    ) from error
                async with self.uow_factory(tenant_id) as uow:
                    failed = await uow.runs.finish_failure(
                        run,
                        failure_code=code,
                        result=result,
                        cost=actual_cost,
                        attempt_id=attempt.id,
                        workload_id=workload.workload_id,
                    )
                    await self._append_workload_completion(uow, failed, workload, None)
                    await uow.commit()
                self.telemetry.count(
                    "agent_runs",
                    attributes={
                        "agent.type": run.agent_type,
                        "result": "failed",
                    },
                )
                if code == "INVALID_RESEARCH_CITATION":
                    self.telemetry.count(
                        "researcher.invalid_citations", attributes={"result": "rejected"}
                    )
                return failed
            raise

    async def _append_workload_started(
        self,
        uow: AgentRuntimeUnitOfWork,
        run: AgentRun,
        workload: WorkloadIdentity,
    ) -> None:
        await uow.audit.append(
            AuditRecord(
                scope_kind=AuditScopeKind.TENANT,
                tenant_id=run.tenant_id,
                actor_kind=AuditActorKind.WORKLOAD,
                actor_id=workload.workload_id,
                action="agent.run.started",
                outcome=AuditOutcome.SUCCESS,
                resource_type="agent_run",
                resource_id=str(run.id),
                agent_definition_id=run.requested_agent_definition_id,
                agent_version_id=run.agent_version_id,
                agent_run_id=run.id,
                correlation_id=run.correlation_id,
                environment=workload.environment,
                safe_metadata=safe_metadata(
                    {
                        "product_id": str(run.product_id),
                        "model_profile": run.model_profile_key,
                        "provider": run.resolved_provider,
                        "model": run.resolved_model,
                    }
                ),
            )
        )

    async def _append_workload_completion(
        self,
        uow: AgentRuntimeUnitOfWork,
        run: AgentRun,
        workload: WorkloadIdentity,
        capability_result: CapabilityResult | None,
    ) -> None:
        metadata = safe_metadata(
            {
                "product_id": str(run.product_id),
                "model_profile": run.model_profile_key,
                "provider": run.resolved_provider,
                "model": run.resolved_model,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "total_tokens": run.total_tokens,
                "estimated_cost": str(run.estimated_cost),
                "currency": run.currency,
                "failure_code": run.failure_code,
            }
        )
        await uow.audit.append(
            AuditRecord(
                scope_kind=AuditScopeKind.TENANT,
                tenant_id=run.tenant_id,
                actor_kind=AuditActorKind.WORKLOAD,
                actor_id=workload.workload_id,
                action="agent.run.succeeded" if capability_result else "agent.run.failed",
                outcome=AuditOutcome.SUCCESS if capability_result else AuditOutcome.FAILED,
                reason_code=run.failure_code,
                resource_type="agent_run",
                resource_id=str(run.id),
                agent_definition_id=run.requested_agent_definition_id,
                agent_version_id=run.agent_version_id,
                agent_run_id=run.id,
                correlation_id=run.correlation_id,
                environment=workload.environment,
                safe_metadata=metadata,
            )
        )
        contracts = EventContractRegistry()
        completed_payload: dict[str, object] = {
            "agent_run_id": str(run.id),
            "product_id": str(run.product_id),
            "requested_agent_definition_id": str(run.requested_agent_definition_id),
            "agent_version_id": str(run.agent_version_id),
            "status": run.status.value,
            "result_ref": run.result_ref,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "total_tokens": run.total_tokens,
            "estimated_cost": str(run.estimated_cost),
            "currency": run.currency,
        }
        await uow.outbox.append(
            DomainEvent(
                event_type="agent.run.completed.v1",
                schema_version=1,
                scope_kind=EventScopeKind.TENANT,
                tenant_id=run.tenant_id,
                aggregate_type="agent_run",
                aggregate_id=run.id,
                occurred_at=run.completed_at or datetime.now(UTC),
                actor_kind=ActorKind.WORKLOAD,
                actor_id=workload.actor_id,
                agent_definition_id=run.requested_agent_definition_id,
                agent_version_id=run.agent_version_id,
                agent_run_id=run.id,
                correlation_id=run.correlation_id,
                payload=completed_payload,
                payload_schema_digest=contracts.schema_digest("agent.run.completed.v1"),
            )
        )
        if capability_result is not None:
            await uow.outbox.append(
                DomainEvent(
                    event_type=capability_result.event_type,
                    schema_version=1,
                    scope_kind=EventScopeKind.TENANT,
                    tenant_id=run.tenant_id,
                    aggregate_type=capability_result.aggregate_type,
                    aggregate_id=capability_result.aggregate_id,
                    occurred_at=capability_result.occurred_at,
                    actor_kind=ActorKind.WORKLOAD,
                    actor_id=workload.actor_id,
                    agent_definition_id=run.requested_agent_definition_id,
                    agent_version_id=run.agent_version_id,
                    agent_run_id=run.id,
                    correlation_id=run.correlation_id,
                    payload=capability_result.event_payload,
                    payload_schema_digest=contracts.schema_digest(capability_result.event_type),
                )
            )


@dataclass(slots=True)
class AgentRunRecoveryService:
    """Trusted operator use cases for explicit, conservative model-run recovery."""

    uow_factory: AgentRuntimeUnitOfWorkFactory
    router: ModelRouter
    operator_provider: RecoveryOperatorProvider
    telemetry: OperationalTelemetry = field(default_factory=NullTelemetry)
    clock: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(UTC))

    async def find_stranded(self) -> tuple[StrandedAgentRun, ...]:
        operator = await self.operator_provider.current()
        with self.telemetry.span("agent_recovery.inspect", {}):
            async with self.uow_factory(operator.tenant_id) as uow:
                values = await uow.runs.find_stranded(self.clock())
        for value in values:
            self.telemetry.gauge(
                "agent_runtime.stranded_runs",
                1,
                {
                    "recovery.classification": value.classification.value.lower(),
                    "provider": value.attempt.provider,
                },
            )
        return values

    async def abandon(self, run_id: UUID) -> AgentRun:
        operator = await self.operator_provider.current()
        with self.telemetry.span("agent_recovery.abandon", {}) as span:
            async with self.uow_factory(operator.tenant_id) as uow:
                stranded = await uow.runs.get_stranded(run_id, self.clock(), for_update=True)
                if stranded is None:
                    raise AgentRunRecoveryConflict("run is not currently stranded")
                failure_code = _recovery_failure_code(stranded.classification)
                closed = await uow.runs.abandon_stranded(
                    stranded,
                    workload_id=operator.workload.workload_id,
                    failure_code=failure_code,
                )
                await uow.audit.append(
                    _recovery_audit(
                        operator,
                        closed,
                        action="agent.run.abandoned",
                        reason_code=failure_code,
                        metadata={
                            "model_attempt_id": str(stranded.attempt.id),
                            "classification": stranded.classification.value,
                            "provider": stranded.attempt.provider,
                            "model": stranded.attempt.model,
                            "cost_category": _cost_category(stranded.classification),
                        },
                    )
                )
                await uow.commit()
            span.set_attribute("result", "abandoned")
        self.telemetry.count(
            "agent_runtime.recovery_actions",
            attributes={"action": "abandon", "result": "succeeded"},
        )
        return closed

    async def rerun_as_new(self, run_id: UUID) -> AgentRun:
        operator = await self.operator_provider.current()
        with self.telemetry.span("agent_recovery.rerun", {}) as span:
            async with self.uow_factory(operator.tenant_id) as uow:
                stranded = await uow.runs.get_stranded(run_id, self.clock(), for_update=True)
                if stranded is None:
                    raise AgentRunRecoveryConflict("run is not currently stranded")
                original = stranded.run
                cfg = await uow.runs.recovery_configuration(original)
                if cfg is None:
                    raise RecoveryAgentUnavailable("bound Agent is no longer available")
                route = self.router.resolve(
                    original.model_profile_key, cfg.model_policy.required_capabilities
                )
                if (
                    route.route_version != original.resolved_model_route_version
                    or route.pricing.version != original.pricing_version
                    or route.provider != original.resolved_provider
                    or route.model != original.resolved_model
                ):
                    raise ModelRouteUnavailable("historical model route is unavailable")
                failure_code = _recovery_failure_code(stranded.classification)
                recovered_at = self.clock()
                recovery_period_start = _period_start(recovered_at, cfg.period_budget_policy.period)
                await uow.runs.lock_period_budgets(
                    original.requested_agent_definition_id,
                    (original.period_start, recovery_period_start),
                )
                await uow.runs.abandon_stranded(
                    stranded,
                    workload_id=operator.workload.workload_id,
                    failure_code=failure_code,
                )
                successor = replace(
                    original,
                    id=uuid4(),
                    recovery_of_run_id=original.id,
                    idempotency_key=f"recovery:{original.id}",
                    correlation_id=operator.correlation_id,
                    initiated_by_actor_kind=ActorKind.WORKLOAD.value,
                    initiated_by_actor_id=operator.workload.actor_id,
                    period_start=recovery_period_start,
                    status=AgentRunStatus.PENDING,
                    created_at=recovered_at,
                    started_at=None,
                    completed_at=None,
                    executed_by_workload_id=None,
                    resolved_provider=None,
                    resolved_model=None,
                    resolved_model_route_version=None,
                    pricing_version=None,
                    reasoning_effort=None,
                    max_output_tokens=None,
                    model_call_count=0,
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    estimated_cost=Decimal("0"),
                    provider_response_id=None,
                    result_ref=None,
                    failure_code=None,
                    operational_status="normal",
                    is_stranded=False,
                )
                try:
                    await uow.runs.reserve_period_budget(
                        run_id=successor.id,
                        definition_id=successor.requested_agent_definition_id,
                        period_start=successor.period_start,
                        max_runs=cfg.period_budget_policy.max_runs,
                        max_cost=cfg.period_budget_policy.max_cost,
                        reserve_cost=successor.reserved_cost,
                        currency=successor.currency,
                    )
                except BudgetExceeded as error:
                    raise RecoveryBlockedBudget("recovery requires a fresh budget") from error
                if not await uow.runs.add_recovery_run(successor):
                    raise AgentRunRecoveryConflict("run already has a recovery successor")
                await uow.audit.append(
                    _recovery_audit(
                        operator,
                        original,
                        action="agent.run.recovery_requested",
                        reason_code=failure_code,
                        metadata={
                            "model_attempt_id": str(stranded.attempt.id),
                            "recovery_run_id": str(successor.id),
                            "classification": stranded.classification.value,
                            "cost_category": _cost_category(stranded.classification),
                        },
                    )
                )
                contracts = EventContractRegistry()
                payload: dict[str, object] = {
                    "agent_run_id": str(successor.id),
                    "product_id": str(successor.product_id),
                    "requested_agent_definition_id": str(successor.requested_agent_definition_id),
                    "agent_version_id": str(successor.agent_version_id),
                    "product_snapshot_digest": successor.product_snapshot_digest,
                    "research_context_digest": successor.research_context_digest,
                    "context_digest": successor.context_digest,
                }
                await uow.outbox.append(
                    DomainEvent(
                        event_type="agent.run.requested.v1",
                        schema_version=1,
                        scope_kind=EventScopeKind.TENANT,
                        tenant_id=successor.tenant_id,
                        aggregate_type="agent_run",
                        aggregate_id=successor.id,
                        occurred_at=successor.created_at,
                        actor_kind=ActorKind.WORKLOAD,
                        actor_id=operator.workload.actor_id,
                        agent_definition_id=successor.requested_agent_definition_id,
                        agent_version_id=successor.agent_version_id,
                        agent_run_id=successor.id,
                        correlation_id=successor.correlation_id,
                        causation_id=original.id,
                        payload=payload,
                        payload_schema_digest=contracts.schema_digest("agent.run.requested.v1"),
                    )
                )
                await uow.commit()
            span.set_attribute("result", "requested")
        self.telemetry.count(
            "agent_runtime.recovery_actions",
            attributes={"action": "rerun", "result": "succeeded"},
        )
        return successor

    async def reconcile_unknown_cost(
        self, run_id: UUID, *, actual_cost: Decimal, currency: str
    ) -> None:
        operator = await self.operator_provider.current()
        if actual_cost < 0 or currency != currency.upper() or len(currency) != 3:
            raise ValueError("authoritative cost and currency must be valid")
        with self.telemetry.span("agent_recovery.cost_reconcile", {}) as span:
            async with self.uow_factory(operator.tenant_id) as uow:
                run = await uow.runs.get(run_id, for_update=True)
                if run is None:
                    raise AgentRunNotFound("AgentRun not found")
                if run.currency != currency:
                    raise UnknownCostReconciliationConflict("currency does not match AgentRun")
                await uow.runs.reconcile_unknown_cost(
                    run,
                    actual_cost=actual_cost,
                    workload_id=operator.workload.workload_id,
                )
                await uow.audit.append(
                    _recovery_audit(
                        operator,
                        run,
                        action="agent.run.unknown_cost_reconciled",
                        metadata={
                            "actual_cost": str(actual_cost),
                            "currency": currency,
                            "cost_category": "reconciled",
                        },
                    )
                )
                await uow.commit()
            span.set_attribute("result", "reconciled")
        self.telemetry.gauge(
            "agent_runtime.unknown_cost",
            float(actual_cost),
            {"result": "reconciled", "provider": run.resolved_provider or "unknown"},
        )


def _recovery_failure_code(classification: RecoveryClassification) -> str:
    return {
        RecoveryClassification.SAFE_BEFORE_PROVIDER: "STRANDED_BEFORE_PROVIDER",
        RecoveryClassification.PROVIDER_OUTCOME_UNKNOWN: "STRANDED_PROVIDER_OUTCOME_UNKNOWN",
        RecoveryClassification.RESPONSE_RECORDED: "STRANDED_RESPONSE_RECORDED",
    }[classification]


def _cost_category(classification: RecoveryClassification) -> str:
    return {
        RecoveryClassification.SAFE_BEFORE_PROVIDER: "released",
        RecoveryClassification.PROVIDER_OUTCOME_UNKNOWN: "unknown",
        RecoveryClassification.RESPONSE_RECORDED: "actual",
    }[classification]


def _recovery_audit(
    operator: RecoveryOperator,
    run: AgentRun,
    *,
    action: str,
    metadata: Mapping[str, str],
    reason_code: str | None = None,
) -> AuditRecord:
    return AuditRecord(
        scope_kind=AuditScopeKind.TENANT,
        tenant_id=run.tenant_id,
        actor_kind=AuditActorKind.WORKLOAD,
        actor_id=operator.workload.workload_id,
        action=action,
        outcome=AuditOutcome.SUCCESS,
        reason_code=reason_code,
        resource_type="agent_run",
        resource_id=str(run.id),
        agent_definition_id=run.requested_agent_definition_id,
        agent_version_id=run.agent_version_id,
        agent_run_id=run.id,
        correlation_id=operator.correlation_id,
        environment=operator.workload.environment,
        safe_metadata=safe_metadata(metadata),
    )
