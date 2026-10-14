"""Add immutable governed assembly render revisions.

Revision ID: 20261016_0040
Revises: 20261015_0039
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261016_0040"
down_revision: str | None = "20261015_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assembly_plans",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        schema="assembly",
    )
    op.add_column(
        "assembly_plans",
        sa.Column("rerender_of_final_creative_id", postgresql.UUID(as_uuid=True)),
        schema="assembly",
    )
    op.alter_column("assembly_plans", "revision", server_default=None, schema="assembly")
    op.drop_constraint(
        "uq_assembly_plans_semantic_revision",
        "assembly_plans",
        schema="assembly",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_assembly_plans_revision",
        "assembly_plans",
        ["tenant_id", "production_plan_id", "revision"],
        schema="assembly",
    )
    op.create_index(
        "uq_assembly_plans_rerender_source",
        "assembly_plans",
        ["tenant_id", "rerender_of_final_creative_id"],
        unique=True,
        schema="assembly",
        postgresql_where=sa.text("rerender_of_final_creative_id IS NOT NULL"),
    )
    op.create_foreign_key(
        "fk_assembly_plans_rerender_final",
        "assembly_plans",
        "final_creatives",
        ["tenant_id", "rerender_of_final_creative_id"],
        ["tenant_id", "id"],
        source_schema="assembly",
        referent_schema="assembly",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_assembly_plans_revision",
        "assembly_plans",
        "revision >= 1 AND ((revision = 1 AND rerender_of_final_creative_id IS NULL) "
        "OR (revision > 1 AND rerender_of_final_creative_id IS NOT NULL))",
        schema="assembly",
    )


def downgrade() -> None:
    evidence = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM assembly.assembly_plans "
                "WHERE revision > 1 OR rerender_of_final_creative_id IS NOT NULL"
            )
        )
        .scalar_one()
    )
    if evidence:
        raise RuntimeError("refusing lossy downgrade while assembly rerender evidence exists")
    op.drop_constraint(
        "ck_assembly_plans_revision", "assembly_plans", schema="assembly", type_="check"
    )
    op.drop_constraint(
        "fk_assembly_plans_rerender_final",
        "assembly_plans",
        schema="assembly",
        type_="foreignkey",
    )
    op.drop_index(
        "uq_assembly_plans_rerender_source", table_name="assembly_plans", schema="assembly"
    )
    op.drop_constraint(
        "uq_assembly_plans_revision", "assembly_plans", schema="assembly", type_="unique"
    )
    op.create_unique_constraint(
        "uq_assembly_plans_semantic_revision",
        "assembly_plans",
        ["tenant_id", "production_plan_id", "semantic_digest"],
        schema="assembly",
    )
    op.drop_column("assembly_plans", "rerender_of_final_creative_id", schema="assembly")
    op.drop_column("assembly_plans", "revision", schema="assembly")
