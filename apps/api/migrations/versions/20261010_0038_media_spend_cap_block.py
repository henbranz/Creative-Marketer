"""Persist pre-provider media spend-cap blocks.

Revision ID: 20261010_0038
Revises: 20261010_0037
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0038"
down_revision: str | None = "20261010_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMON = (
    "kind IN ('IMAGE','VIDEO') AND {statuses} "
    "AND generation_spec_digest ~ '^sha256:[0-9a-f]{{64}}$' "
    "AND reserved_cost>=0 AND actual_cost>=0 AND unknown_cost>=0 "
    "AND currency ~ '^[A-Z]{{3}}$'"
)


def _constraint(statuses: str) -> str:
    return _COMMON.format(statuses=statuses)


def upgrade() -> None:
    op.drop_constraint(
        "ck_generation_jobs_values", "generation_jobs", schema="production", type_="check"
    )
    op.create_check_constraint(
        "ck_generation_jobs_values",
        "generation_jobs",
        _constraint(
            "status IN ('PENDING_APPROVAL','READY','BLOCKED_SPEND_CAP','STARTING',"
            "'PROCESSING','IMPORTING','SUCCEEDED','FAILED','OUTCOME_UNKNOWN')"
        ),
        schema="production",
    )


def downgrade() -> None:
    blocked = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM production.generation_jobs WHERE status='BLOCKED_SPEND_CAP'"
            )
        )
        .scalar_one()
    )
    if blocked:
        raise RuntimeError("refusing lossy downgrade while spend-cap-blocked jobs exist")
    op.drop_constraint(
        "ck_generation_jobs_values", "generation_jobs", schema="production", type_="check"
    )
    op.create_check_constraint(
        "ck_generation_jobs_values",
        "generation_jobs",
        _constraint(
            "status IN ('PENDING_APPROVAL','READY','STARTING','PROCESSING','IMPORTING',"
            "'SUCCEEDED','FAILED','OUTCOME_UNKNOWN')"
        ),
        schema="production",
    )
