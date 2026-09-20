"""Make experiment-to-cycle handoffs replay safe.

Revision ID: 20260920_0030
Revises: 20260919_0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260920_0030"
down_revision: str | None = "20260919_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_creative_cycles_source_experiment",
        "creative_cycles",
        ["tenant_id", "source_experiment_proposal_id"],
        unique=True,
        schema="orchestration",
        postgresql_where=sa.text("source_experiment_proposal_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_creative_cycles_source_experiment",
        table_name="creative_cycles",
        schema="orchestration",
    )
