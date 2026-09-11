# mypy: disable-error-code="no-untyped-def,union-attr"

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from creative_marketer.events.domain import DomainEvent, EventScopeKind
from creative_marketer.identity.application.authentication import ActorKind
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
