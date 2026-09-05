from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from creative_marketer.infrastructure.database.schema import metadata

tool_definitions = Table(
    "tool_definitions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tool_key", String(128), nullable=False, unique=True),
    Column("category", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_by_actor_kind", String(32), nullable=False),
    Column("created_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("tool_key ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$'", name="tool_key"),
    CheckConstraint("category ~ '^[a-z][a-z0-9_]{0,63}$'", name="category"),
    CheckConstraint("status IN ('active', 'disabled', 'archived')", name="status"),
    CheckConstraint("created_by_actor_kind IN ('workload', 'system')", name="created_actor_kind"),
    schema="tool_governance",
)

tool_versions = Table(
    "tool_versions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("definition_id", UUID(as_uuid=True), nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("display_name", String(200), nullable=False),
    Column("description", String(2000), nullable=False),
    Column("risk_level", String(2), nullable=False),
    Column("side_effect_class", String(32), nullable=False),
    Column("execution_class", String(32), nullable=False),
    Column("credential_boundary", String(32), nullable=False),
    Column("idempotency_requirement", String(32), nullable=False),
    Column("input_schema", JSONB, nullable=False),
    Column("output_schema", JSONB, nullable=False),
    Column("input_schema_digest", String(71), nullable=False),
    Column("output_schema_digest", String(71), nullable=False),
    Column("capability_tags", JSONB, nullable=False),
    Column("configuration_schema_version", Integer, nullable=False),
    Column("configuration_digest", String(71), nullable=False),
    Column("created_by_actor_kind", String(32), nullable=False),
    Column("created_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("version_number > 0", name="version_number"),
    CheckConstraint(
        "length(btrim(display_name)) > 0 AND length(btrim(description)) > 0",
        name="required_text",
    ),
    CheckConstraint("risk_level IN ('R0','R1','R2','R3','R4','R5','R6','R7')", name="risk"),
    CheckConstraint(
        "side_effect_class IN ('READ_ONLY','INTERNAL_MUTATION','EXTERNAL_MUTATION')",
        name="side_effect",
    ),
    CheckConstraint("execution_class IN ('INTERNAL','CONNECTOR','PROVIDER')", name="execution"),
    CheckConstraint("credential_boundary IN ('NONE','CONNECTOR')", name="credential"),
    CheckConstraint(
        "idempotency_requirement IN ('NOT_APPLICABLE','SUPPORTED','REQUIRED')",
        name="idempotency",
    ),
    CheckConstraint(
        "jsonb_typeof(input_schema) = 'object' AND jsonb_typeof(output_schema) = 'object' "
        "AND jsonb_typeof(capability_tags) = 'array'",
        name="json_shapes",
    ),
    CheckConstraint(
        "octet_length(input_schema::text) <= 131072 AND "
        "octet_length(output_schema::text) <= 131072",
        name="schema_size",
    ),
    CheckConstraint("configuration_schema_version = 1", name="configuration_schema_version"),
    CheckConstraint(
        "input_schema_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "output_schema_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "configuration_digest ~ '^sha256:[0-9a-f]{64}$'",
        name="digests",
    ),
    CheckConstraint("created_by_actor_kind IN ('workload', 'system')", name="created_actor_kind"),
    CheckConstraint(
        "lower(display_name || ' ' || description || ' ' || input_schema::text || ' ' || "
        "output_schema::text || ' ' || capability_tags::text) "
        "!~ '(bearer[[:space:]]+[^[:space:]\"}]+|sk-[a-z0-9_-]{8,}|"
        "shpat_[a-z0-9]{8,}|gh[pousr]_[a-z0-9]{12,}|"
        "(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password|"
        "authorization)[[:space:]]*[:=][[:space:]]*[^[:space:]\"}]+)'",
        name="no_credentials",
    ),
    ForeignKeyConstraint(
        ["definition_id"], ["tool_governance.tool_definitions.id"], ondelete="RESTRICT"
    ),
    UniqueConstraint("definition_id", "version_number"),
    UniqueConstraint("definition_id", "id"),
    schema="tool_governance",
)

tool_activations = Table(
    "tool_activations",
    metadata,
    Column("definition_id", UUID(as_uuid=True), primary_key=True),
    Column("active_version_id", UUID(as_uuid=True), nullable=False),
    Column("activated_by_actor_kind", String(32), nullable=False),
    Column("activated_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "activated_by_actor_kind IN ('workload', 'system')", name="activated_actor_kind"
    ),
    ForeignKeyConstraint(
        ["definition_id"], ["tool_governance.tool_definitions.id"], ondelete="RESTRICT"
    ),
    ForeignKeyConstraint(
        ["definition_id", "active_version_id"],
        ["tool_governance.tool_versions.definition_id", "tool_governance.tool_versions.id"],
        name="fk_tool_activations_definition_version",
        ondelete="RESTRICT",
    ),
    schema="tool_governance",
)
