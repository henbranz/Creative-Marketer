"""Permit governed Intelligence runs in the common AgentRuntime.

Revision ID: 20260919_0026
Revises: 20260919_0025
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260919_0026"
down_revision: str | None = "20260919_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _constraint(agent_types: str) -> None:
    op.create_check_constraint(
        "ck_agent_runs_selected_evidence",
        "agent_runs",
        "jsonb_typeof(selected_evidence) = 'array' AND "
        "((agent_type = 'researcher' AND jsonb_array_length(selected_evidence) "
        "BETWEEN 1 AND 120) OR "
        f"(agent_type IN ({agent_types}) AND jsonb_array_length(selected_evidence) = 0))",
        schema="agent_runtime",
    )


def upgrade() -> None:
    op.drop_constraint(
        "ck_agent_runs_selected_evidence",
        "agent_runs",
        schema="agent_runtime",
        type_="check",
    )
    _constraint("'creative_strategist','producer','intelligence'")


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM agent_runtime.agent_runs WHERE agent_type = 'intelligence'
          ) THEN
            RAISE EXCEPTION USING MESSAGE =
              'cannot downgrade while Intelligence AgentRuns exist; '
              || 'historical provenance is immutable';
          END IF;
        END $$
        """
    )
    op.drop_constraint(
        "ck_agent_runs_selected_evidence",
        "agent_runs",
        schema="agent_runtime",
        type_="check",
    )
    _constraint("'creative_strategist','producer'")
