"""Bind bounded human feedback to rejected Production Plans.

Revision ID: 20261010_0037
Revises: 20261009_0036
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0037"
down_revision: str | None = "20261009_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plan_decisions",
        sa.Column("rejection_feedback", sa.String(1000), nullable=True),
        schema="production",
    )
    # Historical rejected decisions predate feedback capture and remain readable.
    # New writes are required by the application service to include feedback.
    op.create_check_constraint(
        "ck_plan_decisions_rejection_feedback",
        "plan_decisions",
        "(rejection_feedback IS NULL) OR "
        "(state='REJECTED' AND char_length(rejection_feedback) BETWEEN 10 AND 1000 "
        "AND rejection_feedback=btrim(rejection_feedback))",
        schema="production",
    )


def downgrade() -> None:
    evidence = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM production.plan_decisions "
                "WHERE rejection_feedback IS NOT NULL"
            )
        )
        .scalar_one()
    )
    if evidence:
        raise RuntimeError(
            "refusing lossy Production rejection-feedback downgrade while records exist"
        )
    op.drop_constraint(
        "ck_plan_decisions_rejection_feedback",
        "plan_decisions",
        schema="production",
        type_="check",
    )
    op.drop_column("plan_decisions", "rejection_feedback", schema="production")
