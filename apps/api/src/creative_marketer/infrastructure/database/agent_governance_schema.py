from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from creative_marketer.infrastructure.database.schema import metadata

agent_definitions = Table(
    "agent_definitions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("scope_kind", String(32), nullable=False),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("platform_template_id", UUID(as_uuid=True)),
    Column("agent_key", String(64), nullable=False),
    Column("agent_type", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_by_actor_kind", String(32), nullable=False),
    Column("created_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "(scope_kind = 'platform' AND tenant_id IS NULL AND platform_template_id IS NULL) OR "
        "(scope_kind = 'tenant' AND tenant_id IS NOT NULL)",
        name="ownership",
    ),
    CheckConstraint("agent_key ~ '^[a-z][a-z0-9_]{1,63}$'", name="agent_key"),
    CheckConstraint("agent_type ~ '^[a-z][a-z0-9_]{1,63}$'", name="agent_type"),
    CheckConstraint("status IN ('active', 'disabled', 'archived')", name="status"),
    CheckConstraint(
        "created_by_actor_kind IN ('user', 'workload', 'system')", name="created_actor_kind"
    ),
    ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
    ForeignKeyConstraint(
        ["platform_template_id"],
        ["agent_governance.agent_definitions.id"],
        ondelete="RESTRICT",
    ),
    schema="agent_governance",
)
Index(
    "uq_agent_definitions_platform_key",
    agent_definitions.c.agent_key,
    unique=True,
    postgresql_where=text("scope_kind = 'platform'"),
)
Index(
    "uq_agent_definitions_tenant_key",
    agent_definitions.c.tenant_id,
    agent_definitions.c.agent_key,
    unique=True,
    postgresql_where=text("scope_kind = 'tenant'"),
)
Index("ix_agent_definitions_platform_template", agent_definitions.c.platform_template_id)

agent_versions = Table(
    "agent_versions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("definition_id", UUID(as_uuid=True), nullable=False),
    Column("scope_kind", String(32), nullable=False),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("version_number", Integer, nullable=False),
    Column("display_name", String(200), nullable=False),
    Column("mission", String(2000), nullable=False),
    Column("responsibilities", JSONB, nullable=False),
    Column("system_instructions", Text, nullable=False),
    Column("prompt_revision", String(64), nullable=False),
    Column("model_policy", JSONB, nullable=False),
    Column("run_budget_policy", JSONB, nullable=False),
    Column("period_budget_policy", JSONB, nullable=False),
    Column("read_scopes", JSONB, nullable=False),
    Column("write_scopes", JSONB, nullable=False),
    Column("memory_scopes", JSONB, nullable=False),
    Column("allowed_tool_keys", JSONB, nullable=False),
    Column("denied_tool_keys", JSONB, nullable=False),
    Column("approval_policy_key", String(128), nullable=False),
    Column("output_contract_key", String(128)),
    Column("output_contract_version", Integer),
    Column("configuration_schema_version", Integer, nullable=False),
    Column("configuration_digest", String(71), nullable=False),
    Column("created_by_actor_kind", String(32), nullable=False),
    Column("created_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "(scope_kind = 'platform' AND tenant_id IS NULL) OR "
        "(scope_kind = 'tenant' AND tenant_id IS NOT NULL)",
        name="ownership",
    ),
    CheckConstraint("version_number > 0", name="version_number"),
    CheckConstraint(
        "length(btrim(display_name)) > 0 AND length(btrim(mission)) > 0", name="required_text"
    ),
    CheckConstraint(
        "octet_length(system_instructions) BETWEEN 1 AND 20000", name="instruction_size"
    ),
    CheckConstraint(
        "lower(display_name || ' ' || mission || ' ' || responsibilities::text || ' ' || "
        "system_instructions || ' ' || model_policy::text || ' ' || "
        "run_budget_policy::text || ' ' || period_budget_policy::text) "
        "!~ '(bearer[[:space:]]+[^[:space:]]+|sk-[a-z0-9_-]{8,}|"
        "shpat_[a-z0-9]{8,}|gh[pousr]_[a-z0-9]{12,}|"
        '"(authorization|cookie|password|secret|access_token|refresh_token|id_token|'
        "api_key|client_secret|credential)\"[[:space:]]*:)'",
        name="no_credentials",
    ),
    CheckConstraint("configuration_schema_version = 1", name="configuration_schema_version"),
    CheckConstraint("configuration_digest ~ '^sha256:[0-9a-f]{64}$'", name="configuration_digest"),
    CheckConstraint(
        "created_by_actor_kind IN ('user', 'workload', 'system')", name="created_actor_kind"
    ),
    CheckConstraint(
        "jsonb_typeof(responsibilities) = 'array' AND "
        "jsonb_typeof(model_policy) = 'object' AND "
        "jsonb_typeof(run_budget_policy) = 'object' AND "
        "jsonb_typeof(period_budget_policy) = 'object' AND "
        "jsonb_typeof(read_scopes) = 'array' AND "
        "jsonb_typeof(write_scopes) = 'array' AND "
        "jsonb_typeof(memory_scopes) = 'array' AND "
        "jsonb_typeof(allowed_tool_keys) = 'array' AND "
        "jsonb_typeof(denied_tool_keys) = 'array'",
        name="json_shapes",
    ),
    CheckConstraint(
        "(output_contract_key IS NULL) = (output_contract_version IS NULL) AND "
        "(output_contract_version IS NULL OR output_contract_version > 0)",
        name="output_contract",
    ),
    ForeignKeyConstraint(
        ["definition_id"], ["agent_governance.agent_definitions.id"], ondelete="RESTRICT"
    ),
    UniqueConstraint("definition_id", "version_number"),
    UniqueConstraint("definition_id", "id"),
    schema="agent_governance",
)

agent_activations = Table(
    "agent_activations",
    metadata,
    Column("definition_id", UUID(as_uuid=True), primary_key=True),
    Column("active_version_id", UUID(as_uuid=True), nullable=False),
    Column("scope_kind", String(32), nullable=False),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("activated_by_actor_kind", String(32), nullable=False),
    Column("activated_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "(scope_kind = 'platform' AND tenant_id IS NULL) OR "
        "(scope_kind = 'tenant' AND tenant_id IS NOT NULL)",
        name="ownership",
    ),
    CheckConstraint("activated_by_actor_kind IN ('user', 'workload', 'system')", name="actor_kind"),
    ForeignKeyConstraint(
        ["definition_id"], ["agent_governance.agent_definitions.id"], ondelete="RESTRICT"
    ),
    ForeignKeyConstraint(
        ["definition_id", "active_version_id"],
        ["agent_governance.agent_versions.definition_id", "agent_governance.agent_versions.id"],
        name="fk_agent_activations_definition_version",
        ondelete="RESTRICT",
    ),
    schema="agent_governance",
)
