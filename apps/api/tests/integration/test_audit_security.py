import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.audit.builders import platform_audit, tenant_audit
from creative_marketer.audit.domain import AuditActorKind, AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.infrastructure.database.audit import (
    PostgresAuditWriter,
    PostgresStandaloneAuditWriter,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.schema import audit_records


def execution_context(tenant_id: UUID, user_id: UUID) -> ExecutionContext:
    return ExecutionContext(
        tenant_id=tenant_id,
        actor=Actor(ActorKind.USER, user_id),
        user_id=user_id,
        membership_role=MembershipRole.MEMBER,
        membership_status=MembershipStatus.ACTIVE,
        environment="test",
        authentication=AuthenticationAssurance(datetime.now(UTC), "test", "verified"),
    )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_platform_and_tenant_append_are_insert_only_and_authoritative(
    admin_engine: AsyncEngine, runtime_database_url: str
) -> None:
    writer = PostgresStandaloneAuditWriter(create_session_factory(runtime_database_url))
    tenant_a, user_id, forged_actor = uuid4(), uuid4(), uuid4()
    context = execution_context(tenant_a, user_id)
    await writer.append(
        platform_audit(
            actor_kind=AuditActorKind.ANONYMOUS,
            actor_id=None,
            action="authentication.failed",
            outcome=AuditOutcome.DENIED,
            correlation_id=uuid4(),
            environment="test",
            reason_code="unknown_external_identity",
        )
    )
    tenant_record = tenant_audit(
        context,
        action="identity.tenant_context.resolved",
        outcome=AuditOutcome.SUCCESS,
        metadata=safe_metadata(
            {
                "actor_id": str(forged_actor),
                "actor_kind": "admin",
                "tenant_id": str(uuid4()),
                "safe": "ok",
            }
        ),
    )
    sessions = create_session_factory(runtime_database_url)
    async with sessions.begin() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
            {"tenant": str(tenant_a)},
        )
        await PostgresAuditWriter(session).append(tenant_record)

    with pytest.raises(ValueError, match="trusted context-bound transaction"):
        await writer.append(tenant_record)

    async with admin_engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT scope_kind, tenant_id, actor_kind, actor_id, correlation_id, "
                        "safe_metadata "
                        "FROM audit.audit_records ORDER BY scope_kind"
                    )
                )
            )
            .mappings()
            .all()
        )
    tenant_row = next(row for row in rows if row["scope_kind"] == "tenant")
    assert tenant_row["tenant_id"] == tenant_a
    assert tenant_row["actor_kind"] == "user"
    assert tenant_row["actor_id"] == str(user_id)
    assert tenant_row["correlation_id"] == context.correlation_id
    assert tenant_row["safe_metadata"]["safe"] == "ok"

    for statement in (
        "SELECT * FROM audit.audit_records",
        "UPDATE audit.audit_records SET outcome='error'",
        "DELETE FROM audit.audit_records",
        "TRUNCATE audit.audit_records",
    ):
        with pytest.raises(ProgrammingError):
            engine = create_session_factory(runtime_database_url)
            async with engine.begin() as session:
                await session.execute(text(statement))


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_database_rejects_scope_and_cross_tenant_spoofing(
    admin_engine: AsyncEngine, runtime_database_url: str
) -> None:
    tenant_a, tenant_b = uuid4(), uuid4()
    values = {
        "id": uuid4(),
        "scope_kind": "tenant",
        "tenant_id": tenant_b,
        "actor_kind": "user",
        "actor_id": str(uuid4()),
        "action": "test.denied",
        "outcome": "denied",
        "correlation_id": uuid4(),
        "environment": "test",
        "safe_metadata": {},
        "audit_schema_version": 1,
    }
    sessions = create_session_factory(runtime_database_url)
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
                {"tenant": str(tenant_a)},
            )
            await session.execute(insert(audit_records).values(**values))

    for scope, tenant_id in (("platform", tenant_a), ("tenant", None)):
        async with admin_engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    insert(audit_records).values(
                        **{**values, "id": uuid4(), "scope_kind": scope, "tenant_id": tenant_id}
                    )
                )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transactional_audit_rolls_back_but_standalone_audit_persists(
    admin_engine: AsyncEngine, runtime_database_url: str
) -> None:
    sessions = create_session_factory(runtime_database_url)
    transactional_record = platform_audit(
        actor_kind=AuditActorKind.SYSTEM,
        actor_id="test",
        action="test.transactional",
        outcome=AuditOutcome.SUCCESS,
        correlation_id=uuid4(),
        environment="test",
    )
    session = sessions()
    transaction = await session.begin()
    try:
        await PostgresAuditWriter(session).append(transactional_record)
        await transaction.rollback()
    finally:
        await session.close()

    standalone_record = platform_audit(
        actor_kind=AuditActorKind.SYSTEM,
        actor_id="test",
        action="test.standalone",
        outcome=AuditOutcome.ERROR,
        correlation_id=uuid4(),
        environment="test",
    )
    await PostgresStandaloneAuditWriter(sessions).append(standalone_record)

    async with admin_engine.connect() as connection:
        actions = set(
            (await connection.execute(text("SELECT action FROM audit.audit_records"))).scalars()
        )
    assert "test.transactional" not in actions
    assert "test.standalone" in actions


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_secret_values_never_reach_persisted_json(
    admin_engine: AsyncEngine, runtime_database_url: str
) -> None:
    writer = PostgresStandaloneAuditWriter(create_session_factory(runtime_database_url))
    metadata = safe_metadata(
        {
            "safe": "retained",
            "authorization": "Bearer TOPSECRET",
            "Api_Key": "api-secret",
            "nested": {
                "refresh_token": "refresh-secret",
                "password": "password-secret",
                "cookie": "cookie-secret",
                "provider_credential": "provider-secret",
                "email": "pii@example.test",
            },
        }
    )
    await writer.append(
        platform_audit(
            actor_kind=AuditActorKind.EXTERNAL_PRINCIPAL,
            actor_id="hmac-sha256:fingerprint",
            action="authentication.failed",
            outcome=AuditOutcome.DENIED,
            correlation_id=uuid4(),
            environment="test",
            metadata=metadata,
        )
    )
    async with admin_engine.connect() as connection:
        persisted = await connection.scalar(
            text("SELECT safe_metadata::text FROM audit.audit_records")
        )
    assert json.loads(persisted)["safe"] == "retained"
    lowered = persisted.lower()
    for value in (
        "topsecret",
        "api-secret",
        "refresh-secret",
        "password-secret",
        "cookie-secret",
        "provider-secret",
        "pii@example",
    ):
        assert value not in lowered
