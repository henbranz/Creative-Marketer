from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import monotonic
from types import TracebackType
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from jsonschema import Draft202012Validator

from creative_marketer.agent_governance.domain import AgentVersionConfiguration, BudgetPeriod
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditActorKind, AuditOutcome, AuditRecord, AuditScopeKind
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.catalog.domain import ProductKnowledgeSnapshot
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
    AgentRunStatus,
    AgentRuntimeError,
    BudgetExceeded,
    EvidenceBlockRef,
    ModelCapabilityUnavailable,
    ModelContext,
    ModelInvocation,
    ModelInvocationResult,
    ModelPricing,
    ModelProviderError,
    ModelRoute,
    ModelRouteUnavailable,
    ResearchSnapshot,
    canonical_digest,
    parse_research_output,
)

MAX_EVIDENCE_SOURCES = 20
MAX_BLOCKS_PER_SOURCE = 10
MAX_EVIDENCE_BLOCKS = 120
MAX_EVIDENCE_TEXT_CHARACTERS = 120_000
MAX_PROVIDER_TRANSPORT_ATTEMPTS = 2
RESEARCH_CONTRACT_KEY = "research.research_snapshot"
RESEARCH_CONTRACT_VERSION = 1
_CATEGORY_PRIORITY = {
    ResearchCategory.COMPETITOR: 0,
    ResearchCategory.PRODUCT_PAGE: 1,
    ResearchCategory.PRICING: 2,
    ResearchCategory.LANDING_PAGE: 3,
    ResearchCategory.REVIEW: 4,
}


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


class AgentRunRepository(Protocol):
    async def get_by_idempotency(self, idempotency_key: str) -> AgentRun | None: ...
    async def prepare_researcher(self, product_id: UUID) -> ResearcherPreparation | None: ...
    async def active_for_product(
        self, product_id: UUID, definition_id: UUID
    ) -> AgentRun | None: ...
    async def add(self, run: AgentRun) -> bool: ...
    async def get(self, run_id: UUID, *, for_update: bool = False) -> AgentRun | None: ...
    async def list_for_product(self, product_id: UUID) -> tuple[AgentRun, ...]: ...
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
    async def claim(self, run_id: UUID, workload_id: str, route: ModelRoute) -> AgentRun | None: ...
    async def resolve_context(self, run: AgentRun) -> ModelContext: ...
    async def finish_success(
        self,
        run: AgentRun,
        result: ModelInvocationResult,
        snapshot: ResearchSnapshot,
        cost: Decimal,
    ) -> AgentRun: ...
    async def finish_failure(
        self,
        run: AgentRun,
        *,
        failure_code: str,
        result: ModelInvocationResult | None = None,
        cost: Decimal = Decimal("0"),
    ) -> AgentRun: ...
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


def conservative_input_token_bound(context: ModelContext) -> int:
    """UTF-8 bytes upper-bound tokenizer input without provider-specific dependencies."""

    def plain(value: object) -> object:
        if isinstance(value, Mapping):
            return {str(key): plain(child) for key, child in value.items()}
        if isinstance(value, (tuple, list)):
            return [plain(child) for child in value]
        return value

    document = {
        "system_instructions": context.system_instructions,
        "trusted_product_context": plain(context.product_context),
        "untrusted_external_evidence": [
            {"reference": item.identity(), "source_label": item.source_label, "text": item.text}
            for item in context.evidence_blocks
        ],
        "output_schema": load_output_schema(),
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

    async def get_run(self, context: ExecutionContext, run_id: UUID) -> AgentRun:
        async with self.uow_factory(context.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if run is None:
                raise AgentRunNotFound("AgentRun not found")
            return run

    async def list_runs(self, context: ExecutionContext, product_id: UUID) -> tuple[AgentRun, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.runs.list_for_product(product_id)

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
        actual_cost = Decimal("0")
        claim_committed = False
        try:
            async with self.uow_factory(tenant_id) as uow:
                pending = await uow.runs.get(run_id, for_update=True)
                if pending is None:
                    raise AgentRunNotFound("AgentRun not found")
                route = self.router.resolve(
                    pending.model_profile_key, ("text", "reasoning", "structured_output")
                )
                run = await uow.runs.claim(run_id, workload.workload_id, route)
                if run is None:
                    existing = await uow.runs.get(run_id)
                    if existing is not None and existing.status is AgentRunStatus.SUCCEEDED:
                        return existing
                    raise AgentRunNotReady("AgentRun cannot be claimed")
                context = await uow.runs.resolve_context(run)
                await self._append_workload_started(uow, run, workload)
                await uow.commit()
                claim_committed = True
            schema = load_output_schema()
            provider = self.providers.resolve(route.provider)
            invocation = ModelInvocation(
                route=route,
                system_instructions=context.system_instructions,
                trusted_product_context=context.product_context,
                untrusted_evidence=context.evidence_blocks,
                output_schema=schema,
                output_contract_key=run.output_contract_key,
                output_contract_version=run.output_contract_version,
                max_output_tokens=route.max_output_tokens,
                reasoning_effort=route.reasoning_effort,
            )
            with self.telemetry.span(
                "model.invoke",
                {
                    "agent.type": "researcher",
                    "model.profile": run.model_profile_key,
                    "provider": route.provider,
                    "model": route.model,
                },
            ) as span:
                for attempt in range(1, MAX_PROVIDER_TRANSPORT_ATTEMPTS + 1):
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
                        if not error.retryable or attempt == MAX_PROVIDER_TRANSPORT_ATTEMPTS:
                            raise
                        # Only transport outcomes with no response usage are retried. These are
                        # attempts within one bounded logical model call, not an autonomous loop.
                        await asyncio.sleep(0.25 * attempt)
                    except Exception as error:
                        span.record_error("MODEL_PROVIDER_UNAVAILABLE")
                        raise ModelProviderError("model provider failed") from error
                else:  # pragma: no cover - the bounded loop either returns or raises
                    raise ModelProviderError("model provider exhausted attempts")
            actual_cost = route.pricing.cost(result.usage.input_tokens, result.usage.output_tokens)
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
            snapshot = parse_research_output(
                result.output, run=run, selected_blocks=context.evidence_blocks
            )
            async with self.uow_factory(tenant_id) as uow:
                completed = await uow.runs.finish_success(run, result, snapshot, actual_cost)
                await self._append_workload_completion(uow, completed, workload, snapshot)
                await uow.commit()
            self.telemetry.count(
                "agent_runs", attributes={"agent.type": "researcher", "result": "succeeded"}
            )
            self.telemetry.duration(
                "agent_run.duration",
                monotonic() - timer,
                {"agent.type": "researcher", "result": "succeeded"},
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
                "researcher.findings", len(snapshot.findings), {"result": "accepted"}
            )
            return completed
        except Exception as error:
            if run is not None and claim_committed:
                code = (
                    error.code
                    if isinstance(error, (AgentRuntimeError, ModelProviderError))
                    else "MODEL_INVALID_OUTPUT"
                )
                async with self.uow_factory(tenant_id) as uow:
                    failed = await uow.runs.finish_failure(
                        run, failure_code=code, result=result, cost=actual_cost
                    )
                    await self._append_workload_completion(uow, failed, workload, None)
                    await uow.commit()
                self.telemetry.count(
                    "agent_runs", attributes={"agent.type": "researcher", "result": "failed"}
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
        snapshot: ResearchSnapshot | None,
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
                action="agent.run.succeeded" if snapshot else "agent.run.failed",
                outcome=AuditOutcome.SUCCESS if snapshot else AuditOutcome.FAILED,
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
        if snapshot is not None:
            snapshot_payload = {
                "research_snapshot_id": str(snapshot.id),
                "agent_run_id": str(run.id),
                "product_id": str(run.product_id),
                "requested_agent_definition_id": str(run.requested_agent_definition_id),
                "agent_version_id": str(run.agent_version_id),
                "product_snapshot_digest": run.product_snapshot_digest,
                "research_context_digest": run.research_context_digest,
                "semantic_digest": snapshot.semantic_digest,
            }
            await uow.outbox.append(
                DomainEvent(
                    event_type="research.snapshot.created.v1",
                    schema_version=1,
                    scope_kind=EventScopeKind.TENANT,
                    tenant_id=run.tenant_id,
                    aggregate_type="research_snapshot",
                    aggregate_id=snapshot.id,
                    occurred_at=snapshot.created_at,
                    actor_kind=ActorKind.WORKLOAD,
                    actor_id=workload.actor_id,
                    agent_definition_id=run.requested_agent_definition_id,
                    agent_version_id=run.agent_version_id,
                    agent_run_id=run.id,
                    correlation_id=run.correlation_id,
                    payload=snapshot_payload,
                    payload_schema_digest=contracts.schema_digest("research.snapshot.created.v1"),
                )
            )
