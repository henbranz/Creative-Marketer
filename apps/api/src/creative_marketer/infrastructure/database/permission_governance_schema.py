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

tool_permissions = Table(
    "tool_permissions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("agent_definition_id", UUID(as_uuid=True), nullable=False),
    Column("tool_definition_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_by_actor_kind", String(32), nullable=False),
    Column("created_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("status IN ('active','disabled','archived')", name="status"),
    CheckConstraint("created_by_actor_kind = 'user'", name="created_actor_kind"),
    ForeignKeyConstraint(
        ["tenant_id", "agent_definition_id"],
        ["agent_governance.agent_definitions.tenant_id", "agent_governance.agent_definitions.id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tool_definition_id"], ["tool_governance.tool_definitions.id"], ondelete="RESTRICT"
    ),
    UniqueConstraint("tenant_id", "agent_definition_id", "tool_definition_id"),
    UniqueConstraint("tenant_id", "id"),
    schema="permission_governance",
)

tool_permission_versions = Table(
    "tool_permission_versions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("permission_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("effect", String(16), nullable=False),
    Column("allowed_scopes", JSONB, nullable=False),
    Column("allowed_environments", JSONB, nullable=False),
    Column("approval_behavior", String(32), nullable=False),
    Column("policy_schema_version", Integer, nullable=False),
    Column("configuration_digest", String(71), nullable=False),
    Column("created_by_actor_kind", String(32), nullable=False),
    Column("created_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("version_number > 0", name="version_number"),
    CheckConstraint("effect IN ('GRANT','DENY')", name="effect"),
    CheckConstraint("approval_behavior IN ('RISK_DEFAULT','ALWAYS')", name="approval_behavior"),
    CheckConstraint("policy_schema_version = 1", name="policy_schema_version"),
    CheckConstraint(
        "jsonb_typeof(allowed_scopes) = 'array' AND "
        "jsonb_typeof(allowed_environments) = 'array' AND "
        "jsonb_array_length(allowed_environments) > 0",
        name="json_shapes",
    ),
    CheckConstraint("configuration_digest ~ '^sha256:[0-9a-f]{64}$'", name="digest"),
    CheckConstraint("created_by_actor_kind = 'user'", name="created_actor_kind"),
    ForeignKeyConstraint(
        ["tenant_id", "permission_id"],
        [
            "permission_governance.tool_permissions.tenant_id",
            "permission_governance.tool_permissions.id",
        ],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("permission_id", "version_number"),
    UniqueConstraint("tenant_id", "permission_id", "id"),
    schema="permission_governance",
)

tool_permission_activations = Table(
    "tool_permission_activations",
    metadata,
    Column("permission_id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("active_version_id", UUID(as_uuid=True), nullable=False),
    Column("activated_by_actor_kind", String(32), nullable=False),
    Column("activated_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("activated_by_actor_kind = 'user'", name="activated_actor_kind"),
    ForeignKeyConstraint(
        ["tenant_id", "permission_id"],
        [
            "permission_governance.tool_permissions.tenant_id",
            "permission_governance.tool_permissions.id",
        ],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "permission_id", "active_version_id"],
        [
            "permission_governance.tool_permission_versions.tenant_id",
            "permission_governance.tool_permission_versions.permission_id",
            "permission_governance.tool_permission_versions.id",
        ],
        ondelete="RESTRICT",
    ),
    schema="permission_governance",
)
