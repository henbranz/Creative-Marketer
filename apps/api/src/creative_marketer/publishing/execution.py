from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
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
    GatewayResult,
    GatewayStatus,
    OutcomeUnknown,
    PreEffectFailure,
    ToolExecutionContext,
    ToolExecutorResult,
    ToolInvocationRequest,
    TrustedAgentInvocation,
)
from creative_marketer.tool_governance.domain import ResolvedToolVersion
from creative_marketer.workflow_orchestration.contracts import PublicationWorkflowResult

from .domain import PublicationStatus


def _draft_id(value: NormalizedToolInput) -> UUID:
    raw = value.value()
    if not isinstance(raw, Mapping) or set(raw) != {"publication_draft_id"}:
        raise ValueError("governed publishing input must contain only publication_draft_id")
    return UUID(str(raw["publication_draft_id"]))


def normalize_publication_input(value: object) -> NormalizedToolInput:
    normalized = NormalizedToolInput.from_trusted_value(value)
    _draft_id(normalized)
    return normalized


@dataclass(frozen=True, slots=True)
class ExecutablePublication:
    context: ExecutionContext
    requested_agent_definition_id: UUID
    status: PublicationStatus
    operation_id: str


class PublicationExecutionAuthority(Protocol):
    """Reload and revalidate all publication authority from PostgreSQL."""

    async def authorize_resource(self, tenant_id: UUID, draft_id: UUID) -> None: ...

    async def prepare(self, tenant_id: UUID, draft_id: UUID) -> ExecutablePublication: ...

    async def execute_tool(
        self, tenant_id: UUID, draft_id: UUID, operation: str
    ) -> PublicationWorkflowResult: ...

    async def current(self, tenant_id: UUID, draft_id: UUID) -> PublicationWorkflowResult: ...


@dataclass(slots=True)
class PublicationResourceResolver:
    authority: PublicationExecutionAuthority

    async def __call__(
        self,
        context: ExecutionContext,
        tool: ResolvedToolVersion,
        normalized_input: NormalizedToolInput,
    ) -> ResourceResolution:
        del tool
        draft_id = _draft_id(normalized_input)
        try:
            await self.authority.authorize_resource(context.tenant_id, draft_id)
        except Exception as error:
            raise ResourceAccessDenied(
                "PublicationDraft is outside the trusted tenant scope"
            ) from error
        return ResourceResolution(
            TrustedScopeRequirements(
                (
                    ScopeRequirement(
                        "social.publishing",
                        ScopeAccess.WRITE,
                        resource_type="publication_draft",
                        resource_id=str(draft_id),
                    ),
                )
            ),
            "publication_draft",
            str(draft_id),
        )


@dataclass(slots=True)
class SocialPublishingToolExecutor:
    authority: PublicationExecutionAuthority
    operation: str

    async def execute(
        self, context: ToolExecutionContext, normalized_input: NormalizedToolInput
    ) -> ToolExecutorResult:
        draft_id = _draft_id(normalized_input)
        try:
            result = await self.authority.execute_tool(context.tenant_id, draft_id, self.operation)
        except OutcomeUnknown:
            raise
        except Exception as error:
            code = str(getattr(error, "code", "PUBLISHING_EXECUTION_FAILED"))
            if code == "PUBLICATION_OUTCOME_UNKNOWN":
                raise OutcomeUnknown("publication provider outcome is unknown") from error
            raise PreEffectFailure("publication execution failed safely") from error
        if result.status == PublicationStatus.OUTCOME_UNKNOWN.value:
            raise OutcomeUnknown("publication provider outcome is unknown")
        if result.status == PublicationStatus.FAILED.value:
            raise PreEffectFailure("publication execution failed safely")
        result_ref = (
            f"result://publishing/publications/{result.publication_id}"
            if result.publication_id
            else f"result://publishing/drafts/{draft_id}"
        )
        return ToolExecutorResult(
            {
                "publication_draft_id": str(draft_id),
                "status": result.status,
                "result_ref": result_ref,
            },
            result_ref,
        )


class PublishingGateway(Protocol):
    async def request(
        self, invocation: TrustedAgentInvocation, request: ToolInvocationRequest
    ) -> GatewayResult: ...

    async def invoke(
        self, invocation: TrustedAgentInvocation, request: ToolInvocationRequest
    ) -> GatewayResult: ...


@dataclass(slots=True)
class GovernedPublicationJobExecutor:
    """Temporal activity adapter. Every operation enters the Tool Gateway."""

    authority: PublicationExecutionAuthority
    gateway: PublishingGateway

    async def submit(self, tenant_id: UUID, draft_id: UUID) -> PublicationWorkflowResult:
        return await self._invoke(tenant_id, draft_id, "social.publish.submit")

    async def reconcile(self, tenant_id: UUID, draft_id: UUID) -> PublicationWorkflowResult:
        return await self._invoke(tenant_id, draft_id, "social.publish.status")

    async def cancel(self, tenant_id: UUID, draft_id: UUID) -> PublicationWorkflowResult:
        return await self._invoke(tenant_id, draft_id, "social.publish.cancel")

    async def _invoke(
        self, tenant_id: UUID, draft_id: UUID, tool_key: str
    ) -> PublicationWorkflowResult:
        execution = await self.authority.prepare(tenant_id, draft_id)
        if execution.context.tenant_id != tenant_id:
            raise ValueError("Publication authority returned another tenant")
        operation_id = (
            "op_" + hashlib.sha256(f"{tenant_id}:{draft_id}:{tool_key}".encode()).hexdigest()[:32]
        )
        if tool_key == "social.publish.submit" and operation_id != execution.operation_id:
            raise ValueError("Publication operation identity is not authoritative")
        result = await self.gateway.invoke(
            TrustedAgentInvocation(execution.context, execution.requested_agent_definition_id),
            ToolInvocationRequest(
                tool_key,
                {"publication_draft_id": str(draft_id)},
                operation_id,
            ),
        )
        if result.status not in {GatewayStatus.EXECUTED, GatewayStatus.REPLAYED}:
            current = await self.authority.current(tenant_id, draft_id)
            return PublicationWorkflowResult(
                str(draft_id),
                current.status,
                current.publication_id,
                result.reason_code or current.failure_code,
            )
        return await self.authority.current(tenant_id, draft_id)


def social_tool_bindings(
    authority: PublicationExecutionAuthority,
    tools: Mapping[str, ResolvedToolVersion],
) -> tuple[ToolExecutionBinding, ...]:
    """Bind only explicitly resolved immutable ToolVersions to publishing executors."""

    resolver = PublicationResourceResolver(authority)
    bindings: list[ToolExecutionBinding] = []
    for key, operation in (
        ("social.publish.submit", "submit"),
        ("social.publish.status", "reconcile"),
        ("social.publish.cancel", "cancel"),
    ):
        tool = tools[key]
        bindings.append(
            ToolExecutionBinding(
                tool.definition_id,
                tool.version_id,
                normalize_publication_input,
                resolver,
                SocialPublishingToolExecutor(authority, operation),
                credential_capable=True,
            )
        )
    return tuple(bindings)
