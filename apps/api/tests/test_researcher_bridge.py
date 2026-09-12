# mypy: disable-error-code="no-untyped-def,union-attr"

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from creative_marketer.events.domain import DomainEvent, EventScopeKind
from creative_marketer.identity.application.authentication import ActorKind
from creative_marketer.workflow_orchestration.agent_bridge import (
    RouteAgentWorkflow,
    StartAgentExecutionWorkflow,
)
from creative_marketer.workflow_orchestration.researcher_bridge import StartResearcherWorkflow


def event(*, event_type: str = "agent.run.requested.v1") -> DomainEvent:
    tenant_id, run_id = uuid4(), uuid4()
    return DomainEvent(
        event_type=event_type,
        schema_version=1,
        scope_kind=EventScopeKind.TENANT,
        tenant_id=tenant_id,
        aggregate_type="agent_run",
        aggregate_id=run_id,
        occurred_at=datetime.now(UTC),
        actor_kind=ActorKind.USER,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        payload={"agent_run_id": str(run_id)},
        payload_schema_digest="sha256:" + "a" * 64,
    )


@pytest.mark.asyncio
async def test_bridge_starts_tenant_scoped_researcher_workflow() -> None:
    class Starter:
        request = None

        async def start_researcher(self, request) -> None:
            self.request = request

    starter = Starter()
    value = event()
    await StartResearcherWorkflow(starter)(value, None)  # type: ignore[arg-type]
    assert starter.request.agent_run_id == str(value.aggregate_id)
    assert starter.request.tenant_id == str(value.tenant_id)


@pytest.mark.asyncio
async def test_bridge_rejects_wrong_contract_or_identity() -> None:
    class Never:
        async def start_researcher(self, request) -> None:
            raise AssertionError(request)

    with pytest.raises(ValueError):
        await StartResearcherWorkflow(Never())(event(event_type="other.v1"), None)  # type: ignore[arg-type]
    mismatched = event()
    mismatched = replace(mismatched, payload={"agent_run_id": str(uuid4())})
    with pytest.raises(ValueError):
        await StartResearcherWorkflow(Never())(mismatched, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_generic_bridge_routes_only_authoritative_capabilities() -> None:
    class Starter:
        researcher_request = None
        agent_request = None

        async def start_researcher(self, request) -> None:
            self.researcher_request = request

        async def start_agent(self, request) -> None:
            self.agent_request = request

    class Resolver:
        value = "creative_strategist"

        async def agent_type(self, tenant_id, run_id):
            return self.value

    starter, resolver = Starter(), Resolver()
    value = event()
    await StartAgentExecutionWorkflow(starter, resolver)(value, None)  # type: ignore[arg-type]
    assert starter.agent_request.agent_run_id == str(value.aggregate_id)
    await RouteAgentWorkflow(starter, starter, resolver)(value, None)  # type: ignore[arg-type]
    assert starter.agent_request.agent_run_id == str(value.aggregate_id)
    resolver.value = "researcher"
    await RouteAgentWorkflow(starter, starter, resolver)(value, None)  # type: ignore[arg-type]
    assert starter.researcher_request.agent_run_id == str(value.aggregate_id)


@pytest.mark.asyncio
async def test_generic_bridge_fails_closed_for_bad_events_and_unknown_types() -> None:
    class Never:
        async def start_researcher(self, request) -> None:
            raise AssertionError(request)

        async def start_agent(self, request) -> None:
            raise AssertionError(request)

    class Resolver:
        async def agent_type(self, tenant_id, run_id):
            return "unknown"

    bridge = RouteAgentWorkflow(Never(), Never(), Resolver())
    with pytest.raises(ValueError, match="capability is unavailable"):
        await bridge(event(), None)  # type: ignore[arg-type]
    invalid = replace(event(), scope_kind=EventScopeKind.PLATFORM, tenant_id=None)
    with pytest.raises(ValueError, match="tenant scoped"):
        await bridge(invalid, None)  # type: ignore[arg-type]
    mismatched = event()
    mismatched = replace(mismatched, payload={"agent_run_id": str(uuid4())})
    with pytest.raises(ValueError, match="unsupported"):
        await bridge(mismatched, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not routed"):
        await StartAgentExecutionWorkflow(Never(), Resolver())(event(), None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported"):
        await StartAgentExecutionWorkflow(Never(), Resolver())(invalid, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="identity mismatch"):
        await StartAgentExecutionWorkflow(Never(), Resolver())(mismatched, None)  # type: ignore[arg-type]
