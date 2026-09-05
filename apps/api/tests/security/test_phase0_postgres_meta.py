from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

RUNTIME = "creative_marketer_runtime"
PUBLISHER = "creative_marketer_event_publisher"
APPLICATION_SCHEMAS = (
    "identity",
    "audit",
    "agent_governance",
    "tool_governance",
    "permission_governance",
    "approval_governance",
    "execution_control",
    "event_delivery",
    "tool_execution",
    "catalog",
)


async def _seed_memberships(admin_engine: AsyncEngine) -> tuple[UUID, UUID]:
    tenant_a, tenant_b, user_id = uuid4(), uuid4(), uuid4()
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO identity.tenants (id, name, slug, status) VALUES "
                "(:a, 'Pool A', :slug_a, 'active'), (:b, 'Pool B', :slug_b, 'active')"
            ),
            {"a": tenant_a, "b": tenant_b, "slug_a": f"a-{tenant_a}", "slug_b": f"b-{tenant_b}"},
        )
        await connection.execute(
            text(
                "INSERT INTO identity.users (id, email, normalized_email, status) "
                "VALUES (:id, :email, :email, 'active')"
            ),
            {"id": user_id, "email": f"{user_id}@example.test"},
        )
        await connection.execute(
            text(
                "INSERT INTO identity.memberships (tenant_id, user_id, role, status) "
                "VALUES (:a, :user, 'owner', 'active'), (:b, :user, 'member', 'active')"
            ),
            {"a": tenant_a, "b": tenant_b, "user": user_id},
        )
    return tenant_a, tenant_b


async def _set_tenant(connection: AsyncConnection, tenant_id: UUID) -> None:
    await connection.execute(
        text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
        {"tenant": str(tenant_id)},
    )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_database_roles_remain_non_owner_non_elevated_and_separate(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        roles = (
            (
                await connection.execute(
                    text(
                        "SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, "
                        "rolcanlogin, rolreplication, rolbypassrls "
                        "FROM pg_roles WHERE rolname IN (:runtime, :publisher) ORDER BY rolname"
                    ),
                    {"runtime": RUNTIME, "publisher": PUBLISHER},
                )
            )
            .tuples()
            .all()
        )
        database_owner = await connection.scalar(
            text(
                "SELECT owner.rolname FROM pg_database database "
                "JOIN pg_roles owner ON owner.oid = database.datdba "
                "WHERE database.datname = current_database()"
            )
        )
        memberships = (
            (
                await connection.execute(
                    text(
                        "SELECT member.rolname, parent.rolname FROM pg_auth_members membership "
                        "JOIN pg_roles member ON member.oid = membership.member "
                        "JOIN pg_roles parent ON parent.oid = membership.roleid "
                        "WHERE member.rolname IN (:runtime, :publisher)"
                    ),
                    {"runtime": RUNTIME, "publisher": PUBLISHER},
                )
            )
            .tuples()
            .all()
        )
        owned_schemas = (
            (
                await connection.execute(
                    text(
                        "SELECT namespace.nspname, owner.rolname FROM pg_namespace namespace "
                        "JOIN pg_roles owner ON owner.oid = namespace.nspowner "
                        "WHERE namespace.nspname = ANY(:schemas) "
                        "AND owner.rolname IN (:runtime, :publisher)"
                    ),
                    {
                        "schemas": list(APPLICATION_SCHEMAS),
                        "runtime": RUNTIME,
                        "publisher": PUBLISHER,
                    },
                )
            )
            .tuples()
            .all()
        )
        owned_tables = (
            (
                await connection.execute(
                    text(
                        "SELECT schemaname, tablename, tableowner FROM pg_tables "
                        "WHERE schemaname = ANY(:schemas) "
                        "AND tableowner IN (:runtime, :publisher)"
                    ),
                    {
                        "schemas": list(APPLICATION_SCHEMAS),
                        "runtime": RUNTIME,
                        "publisher": PUBLISHER,
                    },
                )
            )
            .tuples()
            .all()
        )

    assert roles == [
        (PUBLISHER, False, True, False, False, True, False, False),
        (RUNTIME, False, True, False, False, True, False, False),
    ]
    assert database_owner not in {RUNTIME, PUBLISHER}
    assert memberships == []
    assert owned_schemas == []
    assert owned_tables == []


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_every_tenant_column_table_has_enabled_and_forced_rls(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT namespace.nspname, relation.relname, relation.relrowsecurity, "
                        "relation.relforcerowsecurity "
                        "FROM pg_class relation "
                        "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
                        "JOIN pg_attribute attribute ON attribute.attrelid = relation.oid "
                        "WHERE relation.relkind IN ('r', 'p') AND attribute.attname = 'tenant_id' "
                        "AND NOT attribute.attisdropped AND namespace.nspname = ANY(:schemas) "
                        "ORDER BY namespace.nspname, relation.relname"
                    ),
                    {"schemas": list(APPLICATION_SCHEMAS)},
                )
            )
            .tuples()
            .all()
        )
    assert rows
    assert not [
        f"{schema}.{table}" for schema, table, enabled, forced in rows if not enabled or not forced
    ]


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_tenant_tables_have_policies_without_runtime_true_predicates(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        tenant_tables = {
            (row[0], row[1])
            for row in (
                await connection.execute(
                    text(
                        "SELECT DISTINCT table_schema, table_name FROM information_schema.columns "
                        "WHERE column_name='tenant_id' AND table_schema = ANY(:schemas)"
                    ),
                    {"schemas": list(APPLICATION_SCHEMAS)},
                )
            ).tuples()
        }
        policies = (
            (
                await connection.execute(
                    text(
                        "SELECT schemaname, tablename, policyname, roles::text, qual, with_check "
                        "FROM pg_policies WHERE schemaname = ANY(:schemas)"
                    ),
                    {"schemas": list(APPLICATION_SCHEMAS)},
                )
            )
            .tuples()
            .all()
        )
    covered = {(row[0], row[1]) for row in policies}
    assert tenant_tables <= covered
    unsafe = [
        f"{schema}.{table}.{name}"
        for schema, table, name, roles, using, check in policies
        if RUNTIME in roles
        and any(
            value is not None and value.strip("() ").lower() == "true" for value in (using, check)
        )
    ]
    assert unsafe == []


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_missing_tenant_context_fails_closed_across_every_implemented_context(
    runtime_engine: AsyncEngine,
) -> None:
    tables = (
        "identity.memberships",
        "agent_governance.agent_definitions",
        "agent_governance.agent_versions",
        "agent_governance.agent_activations",
        "permission_governance.tool_permissions",
        "permission_governance.tool_permission_versions",
        "permission_governance.tool_permission_activations",
        "approval_governance.approval_requests",
        "approval_governance.approval_decisions",
        "approval_governance.approval_revocations",
        "execution_control.idempotency_records",
        "event_delivery.inbox_receipts",
        "tool_execution.tool_calls",
        "catalog.brands",
        "catalog.brand_profiles",
        "catalog.products",
        "catalog.product_profiles",
        "catalog.product_briefs",
        "catalog.product_knowledge_snapshots",
    )
    for table in tables:
        try:
            async with runtime_engine.begin() as connection:
                count = await connection.scalar(text(f"SELECT count(*) FROM {table}"))
                assert count == 0, table
        except DBAPIError:
            # Absence of a read grant is also a valid fail-closed outcome.
            pass


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_runtime_cannot_disable_rls_or_use_migration_authority(
    runtime_engine: AsyncEngine,
) -> None:
    statements = (
        "ALTER TABLE identity.memberships DISABLE ROW LEVEL SECURITY",
        "CREATE SCHEMA runtime_escape",
        "CREATE ROLE runtime_escape",
    )
    for statement in statements:
        with pytest.raises(DBAPIError):
            async with runtime_engine.begin() as connection:
                await connection.execute(text(statement))


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transaction_local_tenant_context_never_leaks_under_repeated_pool_reuse(
    admin_engine: AsyncEngine,
    runtime_engine: AsyncEngine,
) -> None:
    tenant_a, tenant_b = await _seed_memberships(admin_engine)
    for _ in range(8):
        async with runtime_engine.begin() as connection:
            await _set_tenant(connection, tenant_a)
            assert await connection.scalar(text("SELECT count(*) FROM identity.memberships")) == 1
        async with runtime_engine.begin() as connection:
            await _set_tenant(connection, tenant_b)
            assert await connection.scalar(text("SELECT count(*) FROM identity.memberships")) == 1
        async with runtime_engine.begin() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM identity.memberships")) == 0


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_publisher_has_only_outbox_delivery_privileges(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        forbidden = (
            "identity.users",
            "agent_governance.agent_versions",
            "approval_governance.approval_requests",
            "tool_execution.tool_calls",
            "event_delivery.inbox_receipts",
            "catalog.products",
        )
        for table in forbidden:
            assert not await connection.scalar(
                text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
                {"role": PUBLISHER, "table": table},
            )
        assert await connection.scalar(
            text("SELECT has_table_privilege(:role, 'event_delivery.outbox_events', 'SELECT')"),
            {"role": PUBLISHER},
        )
        assert not await connection.scalar(
            text("SELECT has_table_privilege(:role, 'event_delivery.outbox_events', 'INSERT')"),
            {"role": PUBLISHER},
        )
        for column in (
            "publication_state",
            "attempt_count",
            "next_attempt_at",
            "lease_owner",
            "lease_expires_at",
            "published_at",
            "last_error_code",
            "last_error_digest",
            "updated_at",
        ):
            assert await connection.scalar(
                text(
                    "SELECT has_column_privilege(:role, "
                    "'event_delivery.outbox_events', :column, 'UPDATE')"
                ),
                {"role": PUBLISHER, "column": column},
            )
        for column in ("event_id", "tenant_id", "event_type", "payload", "event_digest"):
            assert not await connection.scalar(
                text(
                    "SELECT has_column_privilege(:role, "
                    "'event_delivery.outbox_events', :column, 'UPDATE')"
                ),
                {"role": PUBLISHER, "column": column},
            )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_no_security_definer_or_public_application_object_privileges(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        functions = (
            (
                await connection.execute(
                    text(
                        "SELECT namespace.nspname, procedure.proname, procedure.prosecdef, "
                        "EXISTS (SELECT 1 FROM aclexplode(COALESCE(procedure.proacl, "
                        "acldefault('f', procedure.proowner))) acl "
                        "WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE') "
                        "FROM pg_proc procedure "
                        "JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace "
                        "WHERE namespace.nspname = ANY(:schemas)"
                    ),
                    {"schemas": list(APPLICATION_SCHEMAS)},
                )
            )
            .tuples()
            .all()
        )
        public_schemas = (
            (
                await connection.execute(
                    text(
                        "SELECT nspname FROM pg_namespace namespace "
                        "WHERE nspname = ANY(:schemas) AND EXISTS ("
                        "SELECT 1 FROM aclexplode(COALESCE(namespace.nspacl, "
                        "acldefault('n', namespace.nspowner))) acl "
                        "WHERE acl.grantee=0 AND acl.privilege_type IN ('USAGE', 'CREATE'))"
                    ),
                    {"schemas": list(APPLICATION_SCHEMAS)},
                )
            )
            .scalars()
            .all()
        )
        public_tables = (
            (
                await connection.execute(
                    text(
                        "SELECT namespace.nspname || '.' || relation.relname "
                        "FROM pg_class relation "
                        "JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace "
                        "WHERE relation.relkind IN ('r', 'p') "
                        "AND namespace.nspname = ANY(:schemas) AND EXISTS ("
                        "SELECT 1 FROM aclexplode(COALESCE(relation.relacl, "
                        "acldefault('r', relation.relowner))) acl "
                        "WHERE acl.grantee=0)"
                    ),
                    {"schemas": list(APPLICATION_SCHEMAS)},
                )
            )
            .scalars()
            .all()
        )
    assert functions
    assert not [f"{schema}.{name}" for schema, name, is_definer, _ in functions if is_definer]
    assert not [f"{schema}.{name}" for schema, name, _, public in functions if public]
    assert public_schemas == []
    assert public_tables == []


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_every_tenant_to_tenant_foreign_key_carries_tenant_identity(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT source_ns.nspname, source.relname, target_ns.nspname, "
                        "target.relname, "
                        "ARRAY(SELECT attribute.attname FROM unnest(constraint_row.conkey) "
                        "WITH ORDINALITY key(attnum, position) "
                        "JOIN pg_attribute attribute ON attribute.attrelid=constraint_row.conrelid "
                        "AND attribute.attnum=key.attnum ORDER BY key.position), "
                        "ARRAY(SELECT attribute.attname FROM unnest(constraint_row.confkey) "
                        "WITH ORDINALITY key(attnum, position) "
                        "JOIN pg_attribute attribute "
                        "ON attribute.attrelid=constraint_row.confrelid "
                        "AND attribute.attnum=key.attnum ORDER BY key.position), "
                        "EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=constraint_row.conrelid "
                        "AND attname='tenant_id' AND NOT attisdropped), "
                        "EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=constraint_row.confrelid "
                        "AND attname='tenant_id' AND NOT attisdropped) "
                        "FROM pg_constraint constraint_row "
                        "JOIN pg_class source ON source.oid=constraint_row.conrelid "
                        "JOIN pg_namespace source_ns ON source_ns.oid=source.relnamespace "
                        "JOIN pg_class target ON target.oid=constraint_row.confrelid "
                        "JOIN pg_namespace target_ns ON target_ns.oid=target.relnamespace "
                        "WHERE constraint_row.contype='f' AND source_ns.nspname = ANY(:schemas)"
                    ),
                    {"schemas": list(APPLICATION_SCHEMAS)},
                )
            )
            .tuples()
            .all()
        )
    # These relationships deliberately use definition identity instead of tenant identity:
    # Agent versions may be platform templates, while activation/version pairs and resolved
    # approval versions are constrained to the exact definition. Focused ownership triggers
    # enforce the denormalized tenant/scope fields on mutable Agent Registry relationships.
    reviewed_relationship_exceptions = {
        (
            "agent_governance",
            "agent_versions",
            "agent_governance",
            "agent_definitions",
        ),
        (
            "agent_governance",
            "agent_activations",
            "agent_governance",
            "agent_definitions",
        ),
        (
            "agent_governance",
            "agent_activations",
            "agent_governance",
            "agent_versions",
        ),
        (
            "agent_governance",
            "agent_definitions",
            "agent_governance",
            "agent_definitions",
        ),
        (
            "approval_governance",
            "approval_requests",
            "agent_governance",
            "agent_versions",
        ),
    }
    checked = 0
    failures: list[str] = []
    for (
        source_schema,
        source,
        target_schema,
        target,
        source_columns,
        target_columns,
        source_tenant,
        target_tenant,
    ) in rows:
        if not source_tenant or not target_tenant:
            continue
        checked += 1
        source_values = list(source_columns)
        target_values = list(target_columns)
        relationship = (source_schema, source, target_schema, target)
        if relationship in reviewed_relationship_exceptions:
            continue
        if "tenant_id" not in source_values or "tenant_id" not in target_values:
            failures.append(f"{source_schema}.{source}->{target_schema}.{target}")
            continue
        if source_values.index("tenant_id") != target_values.index("tenant_id"):
            failures.append(f"{source_schema}.{source}->{target_schema}.{target}")
    assert checked > 0
    assert failures == []

    async with admin_engine.connect() as connection:
        owner_triggers = set(
            (
                await connection.execute(
                    text(
                        "SELECT trigger.tgname FROM pg_trigger trigger "
                        "JOIN pg_class relation ON relation.oid=trigger.tgrelid "
                        "JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace "
                        "WHERE namespace.nspname='agent_governance' AND NOT trigger.tgisinternal"
                    )
                )
            ).scalars()
        )
    assert {
        "enforce_platform_template",
        "enforce_agent_version_owner",
        "enforce_agent_activation_owner",
    } <= owner_triggers
