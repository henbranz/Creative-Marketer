# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment"

from types import SimpleNamespace
from uuid import uuid4

import pytest

from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.infrastructure.database.publishing_authority import (
    PUBLISHING_WORKLOAD_ACTOR_ID,
    SqlAlchemyPublicationExecutionAuthority,
)
from creative_marketer.publishing.domain import PublicationNotFound, PublicationStatus


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Mappings:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _Result:
    def __init__(self, *, row=None, values=()):
        self.row, self.values = row, values

    def mappings(self):
        return _Mappings(self.row)

    def scalars(self):
        return _Scalars(self.values)


class _Session:
    def __init__(self, row, principals, scalar):
        self.row, self.principals, self.scalar_value = row, principals, scalar
        self.selects = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def begin(self):
        return _Transaction()

    async def scalar(self, _query):
        return self.scalar_value

    async def execute(self, query, *_args):
        if query.__class__.__name__ == "TextClause":
            return _Result()
        self.selects += 1
        return _Result(row=self.row) if self.selects % 2 else _Result(values=self.principals)


class _Sessions:
    def __init__(self, row, principals, scalar):
        self.row, self.principals, self.scalar = row, principals, scalar

    def __call__(self):
        return _Session(self.row, self.principals, self.scalar)


class _Service:
    def __init__(self, record, publication=None):
        self.record, self.publication, self.calls = record, publication, []

    async def get_draft(self, context, draft_id):
        self.calls.append(("get", context, draft_id))
        return self.record

    async def execute(self, context, draft_id):
        self.calls.append(("submit", context, draft_id))
        return self.record

    async def reconcile(self, context, draft_id):
        self.calls.append(("reconcile", context, draft_id))
        return self.record

    async def cancel(self, context, draft_id):
        self.calls.append(("cancel", context, draft_id))
        return self.record

    async def list_publications(self, context, product_id):
        self.calls.append(("list", context, product_id))
        return () if self.publication is None else (self.publication,)


def _values(*, job=True, row=True, principal_count=1, publication=True):
    tenant_id, user_id, draft_id, product_id = uuid4(), uuid4(), uuid4(), uuid4()
    database_row = (
        {
            "created_by_user_id": user_id,
            "role": MembershipRole.OWNER.value,
            "membership_status": MembershipStatus.ACTIVE.value,
            "user_status": "active",
            "tenant_status": "active",
        }
        if row
        else None
    )
    job_value = (
        SimpleNamespace(
            status=PublicationStatus.APPROVED,
            operation_id="op_" + "a" * 32,
            failure_code=None,
        )
        if job
        else None
    )
    record = SimpleNamespace(draft=SimpleNamespace(product_id=product_id), job=job_value)
    published = SimpleNamespace(id=uuid4(), publication_draft_id=draft_id) if publication else None
    service = _Service(record, published)
    authority = SqlAlchemyPublicationExecutionAuthority(
        _Sessions(database_row, [uuid4() for _ in range(principal_count)], draft_id),
        service,
        "test",
    )
    return authority, service, tenant_id, draft_id


@pytest.mark.asyncio
async def test_publication_authority_rebuilds_context_and_executes_each_governed_operation() -> (
    None
):
    authority, service, tenant_id, draft_id = _values()
    await authority.authorize_resource(tenant_id, draft_id)
    execution = await authority.prepare(tenant_id, draft_id)
    assert execution.context.actor.id == PUBLISHING_WORKLOAD_ACTOR_ID
    assert execution.context.user_id is not None
    assert execution.operation_id == "op_" + "a" * 32

    for operation in ("submit", "reconcile", "cancel"):
        result = await authority.execute_tool(tenant_id, draft_id, operation)
        assert result.status == PublicationStatus.APPROVED.value
        assert result.publication_id is not None
    assert (await authority.current(tenant_id, draft_id)).publication_id is not None
    with pytest.raises(ValueError, match="unsupported"):
        await authority.execute_tool(tenant_id, draft_id, "erase")
    assert {call[0] for call in service.calls} >= {"submit", "reconcile", "cancel", "list"}


@pytest.mark.asyncio
async def test_publication_authority_fails_closed_for_missing_or_stale_facts() -> None:
    authority, _, tenant_id, draft_id = _values()
    authority._sessions.scalar = None
    with pytest.raises(PublicationNotFound, match="not found"):
        await authority.authorize_resource(tenant_id, draft_id)

    missing_row, _, tenant_id, draft_id = _values(row=False)
    with pytest.raises(PublicationNotFound, match="authority"):
        await missing_row.prepare(tenant_id, draft_id)
    missing_principal, _, tenant_id, draft_id = _values(principal_count=0)
    with pytest.raises(PublicationNotFound, match="principal"):
        await missing_principal.prepare(tenant_id, draft_id)
    missing_job, _, tenant_id, draft_id = _values(job=False)
    with pytest.raises(PublicationNotFound, match="Job"):
        await missing_job.prepare(tenant_id, draft_id)

    no_publication, _, tenant_id, draft_id = _values(publication=False)
    assert (await no_publication.current(tenant_id, draft_id)).publication_id is None
