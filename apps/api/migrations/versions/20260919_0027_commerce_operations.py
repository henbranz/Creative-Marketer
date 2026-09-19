# ruff: noqa: E501
"""Add governed privacy-minimal commerce observations and action state.

Revision ID: 20260919_0027
Revises: 20260919_0026
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260919_0027"
down_revision: str | None = "20260919_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"
TABLES = (
    "connections",
    "product_observations",
    "variant_observations",
    "product_mappings",
    "inventory_observations",
    "order_observations",
    "payment_observations",
    "fulfillment_observations",
    "sync_runs",
    "context_manifests",
    "reports",
    "action_proposals",
    "action_decisions",
    "action_jobs",
    "action_results",
)
IMMUTABLE = (
    "product_observations",
    "variant_observations",
    "product_mappings",
    "inventory_observations",
    "order_observations",
    "payment_observations",
    "fulfillment_observations",
    "context_manifests",
    "reports",
    "action_proposals",
    "action_decisions",
    "action_results",
)

DDL = """
CREATE SCHEMA commerce;
CREATE TABLE commerce.connections(id uuid PRIMARY KEY,tenant_id uuid NOT NULL REFERENCES identity.tenants(id),provider varchar(32) NOT NULL,display_name varchar(200) NOT NULL,external_store_id varchar(200) NOT NULL,safe_store_identifier varchar(255) NOT NULL,status varchar(32) NOT NULL,capabilities jsonb NOT NULL,created_at timestamptz NOT NULL,updated_at timestamptz NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,external_store_id));
CREATE TABLE commerce.product_observations(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,connection_id uuid NOT NULL,external_product_id varchar(255) NOT NULL,title varchar(500) NOT NULL,status varchar(64) NOT NULL,provider varchar(32) NOT NULL,provider_version varchar(64) NOT NULL,source_digest varchar(71) NOT NULL,schema_version int NOT NULL CHECK(schema_version=1),captured_at timestamptz NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_digest),FOREIGN KEY(tenant_id,connection_id) REFERENCES commerce.connections(tenant_id,id));
CREATE TABLE commerce.variant_observations(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,connection_id uuid NOT NULL,external_product_id varchar(255) NOT NULL,external_variant_id varchar(255) NOT NULL,sku varchar(255),price numeric(30,9) NOT NULL CHECK(price>=0),currency varchar(3) NOT NULL CHECK(currency ~ '^[A-Z]{3}$'),inventory_tracked varchar(16),source_digest varchar(71) NOT NULL,schema_version int NOT NULL CHECK(schema_version=1),captured_at timestamptz NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_digest),FOREIGN KEY(tenant_id,connection_id) REFERENCES commerce.connections(tenant_id,id));
CREATE TABLE commerce.product_mappings(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,product_id uuid NOT NULL,connection_id uuid NOT NULL,external_product_id varchar(255) NOT NULL,external_variant_id varchar(255),status varchar(32) NOT NULL,created_by uuid NOT NULL,created_at timestamptz NOT NULL,UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,product_id) REFERENCES catalog.products(tenant_id,id),FOREIGN KEY(tenant_id,connection_id) REFERENCES commerce.connections(tenant_id,id));
CREATE UNIQUE INDEX uq_commerce_active_product_mapping ON commerce.product_mappings(tenant_id,product_id) WHERE status='ACTIVE';
CREATE TABLE commerce.inventory_observations(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,connection_id uuid NOT NULL,external_product_id varchar(255) NOT NULL,external_variant_id varchar(255) NOT NULL,sku varchar(255),location_id varchar(255),available_quantity int,committed_quantity int,on_hand_quantity int,provider varchar(32) NOT NULL,provider_version varchar(64) NOT NULL,source_digest varchar(71) NOT NULL,schema_version int NOT NULL CHECK(schema_version=1),captured_at timestamptz NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_digest),FOREIGN KEY(tenant_id,connection_id) REFERENCES commerce.connections(tenant_id,id));
CREATE TABLE commerce.order_observations(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,connection_id uuid NOT NULL,external_order_id varchar(255) NOT NULL,order_reference varchar(255) NOT NULL,currency varchar(3) NOT NULL CHECK(currency ~ '^[A-Z]{3}$'),subtotal numeric(30,9) NOT NULL,discount_total numeric(30,9) NOT NULL,tax_total numeric(30,9) NOT NULL,shipping_total numeric(30,9) NOT NULL,total numeric(30,9) NOT NULL,financial_status varchar(32) NOT NULL,fulfillment_status varchar(32) NOT NULL,created_at_external timestamptz NOT NULL,updated_at_external timestamptz NOT NULL,captured_at timestamptz NOT NULL,lines jsonb NOT NULL,attribution_code varchar(128),source_digest varchar(71) NOT NULL,schema_version int NOT NULL CHECK(schema_version=1),UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_digest),FOREIGN KEY(tenant_id,connection_id) REFERENCES commerce.connections(tenant_id,id));
CREATE TABLE commerce.payment_observations(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,connection_id uuid NOT NULL,external_order_id varchar(255) NOT NULL,state varchar(32) NOT NULL,amount numeric(30,9) NOT NULL CHECK(amount>=0),currency varchar(3) NOT NULL,provider_payment_reference varchar(255),captured_at timestamptz NOT NULL,source_digest varchar(71) NOT NULL,schema_version int NOT NULL CHECK(schema_version=1),UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_digest),FOREIGN KEY(tenant_id,connection_id) REFERENCES commerce.connections(tenant_id,id));
CREATE TABLE commerce.fulfillment_observations(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,connection_id uuid NOT NULL,external_order_id varchar(255) NOT NULL,state varchar(32) NOT NULL,captured_at timestamptz NOT NULL,source_digest varchar(71) NOT NULL,schema_version int NOT NULL CHECK(schema_version=1),UNIQUE(tenant_id,id),UNIQUE(tenant_id,source_digest),FOREIGN KEY(tenant_id,connection_id) REFERENCES commerce.connections(tenant_id,id));
CREATE TABLE commerce.sync_runs(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,connection_id uuid NOT NULL,sync_type varchar(32) NOT NULL,status varchar(32) NOT NULL,cursor varchar(512),safe_failure_code varchar(100),started_at timestamptz NOT NULL,completed_at timestamptz,UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,connection_id) REFERENCES commerce.connections(tenant_id,id));
CREATE TABLE commerce.context_manifests(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,product_id uuid NOT NULL,product_snapshot_ref jsonb NOT NULL,mapping_ref jsonb NOT NULL,observation_refs jsonb NOT NULL,deterministic_exceptions jsonb NOT NULL,semantic_digest varchar(71) NOT NULL,schema_version int NOT NULL CHECK(schema_version=1),created_at timestamptz NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,semantic_digest),FOREIGN KEY(tenant_id,product_id) REFERENCES catalog.products(tenant_id,id));
CREATE TABLE commerce.reports(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,product_id uuid NOT NULL,agent_run_id uuid NOT NULL,agent_version_id uuid NOT NULL,context_manifest_id uuid NOT NULL,context_manifest_digest varchar(71) NOT NULL,summary varchar(2000) NOT NULL,inventory_exceptions jsonb NOT NULL,order_exceptions jsonb NOT NULL,limitations jsonb NOT NULL,semantic_digest varchar(71) NOT NULL,schema_version int NOT NULL CHECK(schema_version=1),created_at timestamptz NOT NULL,UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,product_id) REFERENCES catalog.products(tenant_id,id),FOREIGN KEY(tenant_id,context_manifest_id) REFERENCES commerce.context_manifests(tenant_id,id));
CREATE TABLE commerce.action_proposals(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,connection_id uuid NOT NULL,product_id uuid NOT NULL,action_type varchar(32) NOT NULL,external_product_id varchar(255),external_variant_id varchar(255),external_order_id varchar(255),exact_quantity int,exact_amount numeric(30,9),currency varchar(3),reason varchar(1000) NOT NULL,evidence_refs jsonb NOT NULL,semantic_digest varchar(71) NOT NULL,schema_version int NOT NULL CHECK(schema_version=1),created_at timestamptz NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,semantic_digest),FOREIGN KEY(tenant_id,connection_id) REFERENCES commerce.connections(tenant_id,id),FOREIGN KEY(tenant_id,product_id) REFERENCES catalog.products(tenant_id,id),CHECK((action_type='INVENTORY_ADJUSTMENT' AND external_variant_id IS NOT NULL AND exact_quantity IS NOT NULL AND exact_amount IS NULL) OR (action_type='REFUND' AND external_order_id IS NOT NULL AND exact_amount>0 AND currency ~ '^[A-Z]{3}$' AND exact_quantity IS NULL)));
CREATE TABLE commerce.action_decisions(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,proposal_id uuid NOT NULL,proposal_digest varchar(71) NOT NULL,decision varchar(16) NOT NULL,decided_by uuid NOT NULL,created_at timestamptz NOT NULL,UNIQUE(tenant_id,id),FOREIGN KEY(tenant_id,proposal_id) REFERENCES commerce.action_proposals(tenant_id,id));
CREATE TABLE commerce.action_jobs(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,proposal_id uuid NOT NULL,proposal_digest varchar(71) NOT NULL,idempotency_key varchar(128) NOT NULL,status varchar(32) NOT NULL,external_operation_id varchar(255),safe_failure_code varchar(100),created_at timestamptz NOT NULL,updated_at timestamptz NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,idempotency_key),FOREIGN KEY(tenant_id,proposal_id) REFERENCES commerce.action_proposals(tenant_id,id));
CREATE TABLE commerce.action_results(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,proposal_id uuid NOT NULL,proposal_digest varchar(71) NOT NULL,action_type varchar(32) NOT NULL,provider varchar(32) NOT NULL,status varchar(32) NOT NULL,external_operation_id varchar(255),observed_fact_refs jsonb NOT NULL,semantic_digest varchar(71) NOT NULL,completed_at timestamptz NOT NULL,UNIQUE(tenant_id,id),UNIQUE(tenant_id,proposal_id,proposal_digest),FOREIGN KEY(tenant_id,proposal_id) REFERENCES commerce.action_proposals(tenant_id,id));
"""


def upgrade() -> None:
    op.execute(DDL)
    op.execute(f"GRANT USAGE ON SCHEMA commerce TO {RUNTIME}, {MIGRATOR}")
    for table in TABLES:
        op.execute(f"ALTER TABLE commerce.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE commerce.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_migration_control ON commerce.{table} FOR ALL TO {MIGRATOR} USING (true) WITH CHECK (true)"
        )
        op.execute(
            f"CREATE POLICY {table}_runtime_tenant ON commerce.{table} FOR ALL TO {RUNTIME} USING (tenant_id={TENANT}) WITH CHECK (tenant_id={TENANT})"
        )
        op.execute(f"REVOKE ALL ON commerce.{table} FROM PUBLIC, {RUNTIME}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON commerce.{table} TO {RUNTIME}")
    for table in IMMUTABLE:
        op.execute(
            f"CREATE FUNCTION commerce.protect_{table}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION '{table} is immutable'; END; $$"
        )
        op.execute(
            f"CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON commerce.{table} FOR EACH ROW EXECUTE FUNCTION commerce.protect_{table}()"
        )
        op.execute(f"REVOKE UPDATE, DELETE ON commerce.{table} FROM {RUNTIME}")
        op.execute(f"REVOKE ALL ON FUNCTION commerce.protect_{table}() FROM PUBLIC")
    op.drop_constraint(
        "ck_agent_runs_selected_evidence", "agent_runs", schema="agent_runtime", type_="check"
    )
    op.create_check_constraint(
        "ck_agent_runs_selected_evidence",
        "agent_runs",
        "jsonb_typeof(selected_evidence)='array' AND ((agent_type='researcher' AND jsonb_array_length(selected_evidence) BETWEEN 1 AND 120) OR (agent_type IN ('creative_strategist','producer','intelligence','commerce_operations') AND jsonb_array_length(selected_evidence)=0))",
        schema="agent_runtime",
    )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN IF EXISTS(
        SELECT 1 FROM commerce.product_observations UNION ALL
        SELECT 1 FROM commerce.variant_observations UNION ALL
        SELECT 1 FROM commerce.product_mappings UNION ALL
        SELECT 1 FROM commerce.inventory_observations UNION ALL
        SELECT 1 FROM commerce.order_observations UNION ALL
        SELECT 1 FROM commerce.payment_observations UNION ALL
        SELECT 1 FROM commerce.fulfillment_observations UNION ALL
        SELECT 1 FROM commerce.context_manifests UNION ALL
        SELECT 1 FROM commerce.reports UNION ALL
        SELECT 1 FROM commerce.action_proposals UNION ALL
        SELECT 1 FROM commerce.action_decisions UNION ALL
        SELECT 1 FROM commerce.action_results
        ) THEN RAISE EXCEPTION 'cannot downgrade while immutable commerce facts exist';
        END IF; END $$"""
    )
    op.drop_constraint(
        "ck_agent_runs_selected_evidence", "agent_runs", schema="agent_runtime", type_="check"
    )
    op.create_check_constraint(
        "ck_agent_runs_selected_evidence",
        "agent_runs",
        "jsonb_typeof(selected_evidence)='array' AND ((agent_type='researcher' AND jsonb_array_length(selected_evidence) BETWEEN 1 AND 120) OR (agent_type IN ('creative_strategist','producer','intelligence') AND jsonb_array_length(selected_evidence)=0))",
        schema="agent_runtime",
    )
    op.execute("DROP SCHEMA commerce CASCADE")
