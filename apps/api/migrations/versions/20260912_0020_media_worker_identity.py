"""Bind generation execution to initiating-user and workload identities.

Revision ID: 20260912_0020
Revises: 20260912_0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0020"
down_revision: str | None = "20260912_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("initiated_by_user_id", postgresql.UUID(as_uuid=True)),
        schema="production",
    )
    op.add_column(
        "generation_jobs",
        sa.Column("executed_by_workload_id", sa.String(128)),
        schema="production",
    )
    op.create_foreign_key(
        "fk_generation_jobs_initiated_by_user_id_users",
        "generation_jobs",
        "users",
        ["initiated_by_user_id"],
        ["id"],
        source_schema="production",
        referent_schema="identity",
        ondelete="RESTRICT",
    )
    # Preserve an honest identity trail for in-flight/terminal jobs created by
    # the pre-authority runtime. Their initiating user is recoverable from the
    # immutable AgentRun; the historical worker process was not identified.
    op.execute(
        "UPDATE production.generation_jobs AS jobs "
        "SET initiated_by_user_id = runs.initiated_by_actor_id, "
        "executed_by_workload_id = 'legacy-pre-authority' "
        "FROM production.production_plans AS plans "
        "JOIN agent_runtime.agent_runs AS runs ON runs.id = plans.agent_run_id "
        "WHERE jobs.production_plan_id = plans.id "
        "AND jobs.status NOT IN ('PENDING_APPROVAL','READY') "
        "AND runs.initiated_by_actor_kind = 'user'"
    )
    op.create_check_constraint(
        "ck_generation_jobs_execution_identity",
        "generation_jobs",
        "(status IN ('PENDING_APPROVAL','READY') AND executed_by_workload_id IS NULL) OR "
        "(status NOT IN ('PENDING_APPROVAL','READY') AND initiated_by_user_id IS NOT NULL "
        "AND length(btrim(executed_by_workload_id)) BETWEEN 1 AND 128)",
        schema="production",
    )


def downgrade() -> None:
    evidence = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM production.generation_jobs "
                "WHERE initiated_by_user_id IS NOT NULL OR executed_by_workload_id IS NOT NULL"
            )
        )
        .scalar_one()
    )
    if evidence:
        raise RuntimeError("refusing lossy media workload identity downgrade")
    op.drop_constraint(
        "ck_generation_jobs_execution_identity",
        "generation_jobs",
        schema="production",
        type_="check",
    )
    op.drop_constraint(
        "fk_generation_jobs_initiated_by_user_id_users",
        "generation_jobs",
        schema="production",
        type_="foreignkey",
    )
    op.drop_column("generation_jobs", "executed_by_workload_id", schema="production")
    op.drop_column("generation_jobs", "initiated_by_user_id", schema="production")
