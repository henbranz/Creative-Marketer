# ruff: noqa: E501
"""Compose durable Commerce worker requests and workload provenance.

Revision ID: 20260919_0028
Revises: 20260919_0027
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260919_0028"
down_revision: str | None = "20260919_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE commerce.sync_requests(
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES identity.tenants(id),
          connection_id uuid NOT NULL,
          requested_by_user_id uuid NOT NULL,
          correlation_id uuid NOT NULL,
          sync_types jsonb NOT NULL,
          status varchar(32) NOT NULL,
          safe_failure_code varchar(100),
          created_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL,
          UNIQUE(tenant_id,id),
          FOREIGN KEY(tenant_id,connection_id) REFERENCES commerce.connections(tenant_id,id),
          FOREIGN KEY(requested_by_user_id) REFERENCES identity.users(id),
          CHECK(jsonb_typeof(sync_types)='array' AND jsonb_array_length(sync_types) BETWEEN 1 AND 3),
          CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED'))
        );
        ALTER TABLE commerce.sync_requests ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commerce.sync_requests FORCE ROW LEVEL SECURITY;
        CREATE POLICY sync_requests_migration_control ON commerce.sync_requests FOR ALL TO creative_marketer_migrator USING (true) WITH CHECK (true);
        CREATE POLICY sync_requests_runtime_tenant ON commerce.sync_requests FOR ALL TO creative_marketer_runtime USING (tenant_id=nullif(current_setting('app.current_tenant_id', true), '')::uuid) WITH CHECK (tenant_id=nullif(current_setting('app.current_tenant_id', true), '')::uuid);
        REVOKE ALL ON commerce.sync_requests FROM PUBLIC, creative_marketer_runtime;
        GRANT SELECT, INSERT, UPDATE ON commerce.sync_requests TO creative_marketer_runtime;
        ALTER TABLE commerce.action_jobs ADD COLUMN executing_workload_actor_id uuid;
        ALTER TABLE commerce.action_jobs ADD COLUMN executing_workload_id varchar(128);
        ALTER TABLE commerce.action_jobs ADD CONSTRAINT ck_action_jobs_workload_pair CHECK ((executing_workload_actor_id IS NULL) = (executing_workload_id IS NULL));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM commerce.sync_requests)
             OR EXISTS(SELECT 1 FROM commerce.action_jobs WHERE executing_workload_actor_id IS NOT NULL)
          THEN RAISE EXCEPTION 'cannot downgrade while durable commerce worker state exists';
          END IF;
        END $$;
        ALTER TABLE commerce.action_jobs DROP CONSTRAINT ck_action_jobs_workload_pair;
        ALTER TABLE commerce.action_jobs DROP COLUMN executing_workload_id;
        ALTER TABLE commerce.action_jobs DROP COLUMN executing_workload_actor_id;
        DROP TABLE commerce.sync_requests;
        """
    )
