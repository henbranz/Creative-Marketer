from typing import Any

from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData(schema="commerce")


def _table(name: str, *columns: Column[Any]) -> Table:
    return Table(
        name,
        metadata,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("tenant_id", UUID(as_uuid=True), nullable=False),
        *columns,
    )


connections = _table(
    "connections",
    Column("provider", String(32), nullable=False),
    Column("display_name", String(200), nullable=False),
    Column("external_store_id", String(200), nullable=False),
    Column("safe_store_identifier", String(255), nullable=False),
    Column("status", String(32), nullable=False),
    Column("capabilities", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
product_observations = _table(
    "product_observations",
    Column("connection_id", UUID(as_uuid=True), nullable=False),
    Column("external_product_id", String(255), nullable=False),
    Column("title", String(500), nullable=False),
    Column("status", String(64), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("provider_version", String(64), nullable=False),
    Column("source_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
)
variant_observations = _table(
    "variant_observations",
    Column("connection_id", UUID(as_uuid=True), nullable=False),
    Column("external_product_id", String(255), nullable=False),
    Column("external_variant_id", String(255), nullable=False),
    Column("sku", String(255)),
    Column("price", Numeric(30, 9), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("inventory_tracked", String(16)),
    Column("source_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
)
product_mappings = _table(
    "product_mappings",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("connection_id", UUID(as_uuid=True), nullable=False),
    Column("external_product_id", String(255), nullable=False),
    Column("external_variant_id", String(255)),
    Column("status", String(32), nullable=False),
    Column("created_by", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
inventory_observations = _table(
    "inventory_observations",
    Column("connection_id", UUID(as_uuid=True), nullable=False),
    Column("external_product_id", String(255), nullable=False),
    Column("external_variant_id", String(255), nullable=False),
    Column("sku", String(255)),
    Column("location_id", String(255)),
    Column("available_quantity", Integer),
    Column("committed_quantity", Integer),
    Column("on_hand_quantity", Integer),
    Column("provider", String(32), nullable=False),
    Column("provider_version", String(64), nullable=False),
    Column("source_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
)
order_observations = _table(
    "order_observations",
    Column("connection_id", UUID(as_uuid=True), nullable=False),
    Column("external_order_id", String(255), nullable=False),
    Column("order_reference", String(255), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("subtotal", Numeric(30, 9), nullable=False),
    Column("discount_total", Numeric(30, 9), nullable=False),
    Column("tax_total", Numeric(30, 9), nullable=False),
    Column("shipping_total", Numeric(30, 9), nullable=False),
    Column("total", Numeric(30, 9), nullable=False),
    Column("financial_status", String(32), nullable=False),
    Column("fulfillment_status", String(32), nullable=False),
    Column("created_at_external", DateTime(timezone=True), nullable=False),
    Column("updated_at_external", DateTime(timezone=True), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("lines", JSONB, nullable=False),
    Column("attribution_code", String(128)),
    Column("source_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
)
payment_observations = _table(
    "payment_observations",
    Column("connection_id", UUID(as_uuid=True), nullable=False),
    Column("external_order_id", String(255), nullable=False),
    Column("state", String(32), nullable=False),
    Column("amount", Numeric(30, 9), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("provider_payment_reference", String(255)),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("source_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
)
fulfillment_observations = _table(
    "fulfillment_observations",
    Column("connection_id", UUID(as_uuid=True), nullable=False),
    Column("external_order_id", String(255), nullable=False),
    Column("state", String(32), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("source_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
)
sync_runs = _table(
    "sync_runs",
    Column("connection_id", UUID(as_uuid=True), nullable=False),
    Column("sync_type", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("cursor", String(512)),
    Column("safe_failure_code", String(100)),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
)
sync_requests = _table(
    "sync_requests",
    Column("connection_id", UUID(as_uuid=True), nullable=False),
    Column("requested_by_user_id", UUID(as_uuid=True), nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("sync_types", JSONB, nullable=False),
    Column("status", String(32), nullable=False),
    Column("safe_failure_code", String(100)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
context_manifests = _table(
    "context_manifests",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("product_snapshot_ref", JSONB, nullable=False),
    Column("mapping_ref", JSONB, nullable=False),
    Column("observation_refs", JSONB, nullable=False),
    Column("deterministic_exceptions", JSONB, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
reports = _table(
    "reports",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("agent_run_id", UUID(as_uuid=True), nullable=False),
    Column("agent_version_id", UUID(as_uuid=True), nullable=False),
    Column("context_manifest_id", UUID(as_uuid=True), nullable=False),
    Column("context_manifest_digest", String(71), nullable=False),
    Column("summary", String(2000), nullable=False),
    Column("inventory_exceptions", JSONB, nullable=False),
    Column("order_exceptions", JSONB, nullable=False),
    Column("limitations", JSONB, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
action_proposals = _table(
    "action_proposals",
    Column("connection_id", UUID(as_uuid=True), nullable=False),
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("action_type", String(32), nullable=False),
    Column("external_product_id", String(255)),
    Column("external_variant_id", String(255)),
    Column("external_order_id", String(255)),
    Column("exact_quantity", Integer),
    Column("exact_amount", Numeric(30, 9)),
    Column("currency", String(3)),
    Column("reason", String(1000), nullable=False),
    Column("evidence_refs", JSONB, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
action_decisions = _table(
    "action_decisions",
    Column("proposal_id", UUID(as_uuid=True), nullable=False),
    Column("proposal_digest", String(71), nullable=False),
    Column("decision", String(16), nullable=False),
    Column("decided_by", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
action_jobs = _table(
    "action_jobs",
    Column("proposal_id", UUID(as_uuid=True), nullable=False),
    Column("proposal_digest", String(71), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("external_operation_id", String(255)),
    Column("safe_failure_code", String(100)),
    Column("executing_workload_actor_id", UUID(as_uuid=True)),
    Column("executing_workload_id", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
action_results = _table(
    "action_results",
    Column("proposal_id", UUID(as_uuid=True), nullable=False),
    Column("proposal_digest", String(71), nullable=False),
    Column("action_type", String(32), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("external_operation_id", String(255)),
    Column("observed_fact_refs", JSONB, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
)
