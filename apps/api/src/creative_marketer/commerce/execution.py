from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, cast
from uuid import UUID

from creative_marketer.action_binding import NormalizedToolInput
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.permission_governance.domain import (
    ScopeAccess,
    ScopeRequirement,
    TrustedScopeRequirements,
)
from creative_marketer.tool_execution.application import (
    ResourceAccessDenied,
    ResourceResolution,
    ToolExecutionBinding,
)
from creative_marketer.tool_execution.domain import (
    OutcomeUnknown,
    PreEffectFailure,
    ToolExecutionContext,
    ToolExecutorResult,
)
from creative_marketer.tool_governance.domain import ResolvedToolVersion

from .domain import ActionType, CommerceActionProposal
from .provider import CommerceMutationProvider, MutationDisposition, ProviderMutationResult


@dataclass(frozen=True, slots=True)
class CommerceWorkloadIdentity:
    actor_id: UUID
    workload_id: str
    environment: str

    def __post_init__(self) -> None:
        if not self.workload_id.strip() or len(self.workload_id) > 128:
            raise ValueError("commerce workload identity is invalid")


def _proposal_id(value: NormalizedToolInput) -> UUID:
    raw = value.value()
    if not isinstance(raw, Mapping) or set(raw) != {"commerce_action_proposal_id"}:
        raise ValueError("governed commerce input must contain only commerce_action_proposal_id")
    return UUID(str(raw["commerce_action_proposal_id"]))


def normalize_commerce_input(value: object) -> NormalizedToolInput:
    normalized = NormalizedToolInput.from_trusted_value(value)
    _proposal_id(normalized)
    return normalized


class CommerceExecutionAuthority(Protocol):
    """PostgreSQL authority reloads exact proposal, approval, store, and job state."""

    async def authorize_resource(self, tenant_id: UUID, proposal_id: UUID) -> None: ...
    async def prepare(
        self, tenant_id: UUID, proposal_id: UUID
    ) -> tuple[CommerceActionProposal, str]: ...
    async def operation(
        self, tenant_id: UUID, proposal_id: UUID
    ) -> tuple[CommerceActionProposal, str, str]: ...
    async def begin(
        self, tenant_id: UUID, proposal: CommerceActionProposal, idempotency_key: str
    ) -> None: ...
    async def record(
        self,
        tenant_id: UUID,
        proposal: CommerceActionProposal,
        result: ProviderMutationResult,
    ) -> None: ...


@dataclass(slots=True)
class CommerceResourceResolver:
    authority: CommerceExecutionAuthority

    async def __call__(
        self,
        context: ExecutionContext,
        tool: ResolvedToolVersion,
        normalized_input: NormalizedToolInput,
    ) -> ResourceResolution:
        del tool
        proposal_id = _proposal_id(normalized_input)
        try:
            await self.authority.authorize_resource(context.tenant_id, proposal_id)
        except Exception as error:
            raise ResourceAccessDenied(
                "Commerce proposal is outside trusted tenant scope"
            ) from error
        return ResourceResolution(
            TrustedScopeRequirements(
                (
                    ScopeRequirement(
                        "commerce.operations",
                        ScopeAccess.WRITE,
                        resource_type="commerce_action_proposal",
                        resource_id=str(proposal_id),
                    ),
                )
            ),
            "commerce_action_proposal",
            str(proposal_id),
        )


@dataclass(slots=True)
class CommerceToolExecutor:
    tool_key: str
    authority: CommerceExecutionAuthority
    provider: CommerceMutationProvider

    async def execute(
        self, context: ToolExecutionContext, normalized_input: NormalizedToolInput
    ) -> ToolExecutorResult:
        proposal_id = _proposal_id(normalized_input)
        proposal: CommerceActionProposal | None = None
        try:
            if self.tool_key == "commerce.operation.status":
                proposal, store, external_operation_id = await self.authority.operation(
                    context.tenant_id, proposal_id
                )
                if hasattr(
                    self.provider, "restore_unknown_operation"
                ) and external_operation_id not in getattr(self.provider, "operations", {}):
                    effect: dict[str, object]
                    if proposal.action_type is ActionType.INVENTORY_ADJUSTMENT:
                        effect = {
                            "kind": "SET_AVAILABLE_TO",
                            "store": store,
                            "variant": cast(str, proposal.external_variant_id),
                            "quantity": cast(int, proposal.exact_quantity),
                        }
                    else:
                        effect = {
                            "kind": "REFUND",
                            "store": store,
                            "order": cast(str, proposal.external_order_id),
                            "amount": cast(Decimal, proposal.exact_amount),
                            "currency": cast(str, proposal.currency),
                        }
                    self.provider.restore_unknown_operation(external_operation_id, effect)
                result = await self.provider.get_operation_status(store, external_operation_id)
                proposal_id_value = proposal_id
            else:
                proposal, store = await self.authority.prepare(context.tenant_id, proposal_id)
                if hasattr(self.provider, "seed_store"):
                    self.provider.seed_store(store)
                proposal_id_value = proposal.id
                await self.authority.begin(context.tenant_id, proposal, context.operation_id)
            if (
                self.tool_key == "commerce.inventory.adjust"
                and proposal is not None
                and proposal.action_type is ActionType.INVENTORY_ADJUSTMENT
            ):
                result = await self.provider.set_available_to(
                    external_store_id=store,
                    external_variant_id=cast(str, proposal.external_variant_id),
                    quantity=cast(int, proposal.exact_quantity),
                    idempotency_key=context.operation_id,
                )
            elif (
                self.tool_key == "commerce.refund.submit"
                and proposal is not None
                and proposal.action_type is ActionType.REFUND
            ):
                result = await self.provider.submit_refund(
                    external_store_id=store,
                    external_order_id=cast(str, proposal.external_order_id),
                    amount=cast(Decimal, proposal.exact_amount),
                    currency=cast(str, proposal.currency),
                    idempotency_key=context.operation_id,
                )
            elif self.tool_key == "commerce.operation.status":
                pass
            else:
                raise PreEffectFailure("tool and immutable commerce proposal do not match")
            assert proposal is not None
            await self.authority.record(context.tenant_id, proposal, result)
        except (PreEffectFailure, OutcomeUnknown):
            raise
        except Exception as error:
            raise PreEffectFailure("commerce provider failed before a known effect") from error
        if result.disposition is MutationDisposition.OUTCOME_UNKNOWN:
            raise OutcomeUnknown("commerce provider outcome is unknown")
        if result.disposition is MutationDisposition.FAILED:
            raise PreEffectFailure("commerce provider rejected operation safely")
        ref = f"result://commerce/operations/{result.operation_id}"
        return ToolExecutorResult(
            {
                "commerce_action_proposal_id": str(proposal_id_value),
                "status": "SUCCEEDED",
                "result_ref": ref,
            },
            ref,
        )


def commerce_tool_bindings(
    authority: CommerceExecutionAuthority,
    provider: CommerceMutationProvider,
    tools: Mapping[str, ResolvedToolVersion],
) -> tuple[ToolExecutionBinding, ...]:
    resolver = CommerceResourceResolver(authority)
    return tuple(
        ToolExecutionBinding(
            tools[key].definition_id,
            tools[key].version_id,
            normalize_commerce_input,
            resolver,
            CommerceToolExecutor(key, authority, provider),
            credential_capable=True,
        )
        for key in (
            "commerce.inventory.adjust",
            "commerce.refund.submit",
            "commerce.operation.status",
        )
    )
