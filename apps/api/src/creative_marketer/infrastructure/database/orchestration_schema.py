from typing import Any

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table
from sqlalchemy.dialects.postgresql import JSONB, UUID

from creative_marketer.infrastructure.database.schema import NAMING_CONVENTION

metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _table(name: str, *columns: Any) -> Table:
    return Table(
        name,
        metadata,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("tenant_id", UUID(as_uuid=True), nullable=False),
        *columns,
        schema="orchestration",
    )


creative_cycles = _table(
    "creative_cycles",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("initiated_by", UUID(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("current_stage", String(64), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("product_snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("product_snapshot_digest", String(71), nullable=False),
    Column("artifact_bindings", JSONB, nullable=False),
    Column("parent_cycle_id", UUID(as_uuid=True)),
    Column("source_experiment_proposal_id", UUID(as_uuid=True)),
    Column("blocker_code", String(100)),
    Column("failure_code", String(100)),
    Column("state_machine_version", String(64), nullable=False),
    Column("cycle_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

cycle_transitions = _table(
    "cycle_transitions",
    Column("cycle_id", UUID(as_uuid=True), nullable=False),
    Column("from_stage", String(64)),
    Column("to_stage", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("reason_code", String(100), nullable=False),
    Column("actor_kind", String(32), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)

cycle_steps = _table(
    "cycle_steps",
    Column("cycle_id", UUID(as_uuid=True), nullable=False),
    Column("step_key", String(64), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("agent_run_id", UUID(as_uuid=True)),
    Column("workflow_ref", String(256)),
    Column("artifact_ref", String(256)),
    Column("failure_code", String(100)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
)

supervisor_context_manifests = _table(
    "supervisor_context_manifests",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("cycle_id", UUID(as_uuid=True), nullable=False),
    Column("cycle_version", Integer, nullable=False),
    Column("current_stage", String(64), nullable=False),
    Column("manifest", JSONB, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

supervisor_reports = _table(
    "supervisor_reports",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("cycle_id", UUID(as_uuid=True), nullable=False),
    Column("context_manifest_id", UUID(as_uuid=True), nullable=False),
    Column("context_manifest_digest", String(71), nullable=False),
    Column("agent_run_id", UUID(as_uuid=True)),
    Column("summary", String(1000), nullable=False),
    Column("current_stage_explanation", String(1000), nullable=False),
    Column("blockers", JSONB, nullable=False),
    Column("attention_items", JSONB, nullable=False),
    Column("suggested_next_actions", JSONB, nullable=False),
    Column("completion_summary", String(1000)),
    Column("semantic_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
