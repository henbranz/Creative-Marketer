"""Harden internal trigger-function execution privileges.

Revision ID: 20260905_0011
Revises: 20260905_0010
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260905_0011"
down_revision: str | None = "20260905_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGER_FUNCTIONS = (
    "agent_governance.enforce_platform_template()",
    "agent_governance.protect_agent_definition_identity()",
    "agent_governance.enforce_agent_version_owner()",
    "agent_governance.enforce_agent_activation_owner()",
    "tool_governance.protect_tool_definition_identity()",
    "permission_governance.protect_permission_identity()",
    "approval_governance.reject_history_mutation()",
    "execution_control.protect_idempotency_transition()",
    "event_delivery.protect_outbox_event()",
    "event_delivery.reject_inbox_mutation()",
    "event_delivery.protect_outbox_trace_context()",
    "tool_execution.protect_tool_call()",
)

FORMER_DEFINER_FUNCTIONS = (
    "agent_governance.enforce_platform_template()",
    "agent_governance.enforce_agent_version_owner()",
    "agent_governance.enforce_agent_activation_owner()",
)


def upgrade() -> None:
    for function in FORMER_DEFINER_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {function} SECURITY INVOKER")
    for function in TRIGGER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")


def downgrade() -> None:
    for function in TRIGGER_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC")
    for function in FORMER_DEFINER_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {function} SECURITY DEFINER")
