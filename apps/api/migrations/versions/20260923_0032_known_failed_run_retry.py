"""Permit one immutable successor for authoritative zero-usage failures.

Revision ID: 20260923_0032
Revises: 20260922_0031
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260923_0032"
down_revision: str | None = "20260922_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STRANDED_CODES = (
    "'STRANDED_BEFORE_PROVIDER','STRANDED_PROVIDER_OUTCOME_UNKNOWN','STRANDED_RESPONSE_RECORDED'"
)
_KNOWN_NO_RESPONSE_CODES = (
    "'MODEL_PROVIDER_BAD_REQUEST','MODEL_PROVIDER_SCHEMA_UNSUPPORTED',"
    "'MODEL_PROVIDER_AUTHENTICATION_FAILED','MODEL_PROVIDER_PERMISSION_DENIED',"
    "'MODEL_PROVIDER_MODEL_UNAVAILABLE','MODEL_PROVIDER_CONFLICT','MODEL_RATE_LIMITED'"
)


def _recovery_guard(*, allow_known_failure: bool) -> str:
    known_failure = ""
    if allow_known_failure:
        known_failure = (
            f" OR (predecessor.failure_code IN ({_KNOWN_NO_RESPONSE_CODES}) "
            "AND predecessor.provider_response_id IS NULL "
            "AND predecessor.result_ref IS NULL AND predecessor.model_call_count=1 "
            "AND predecessor.input_tokens=0 AND predecessor.output_tokens=0 "
            "AND predecessor.total_tokens=0 AND predecessor.estimated_cost=0 "
            "AND (SELECT count(*) FROM agent_runtime.model_attempts attempt_count "
            "WHERE attempt_count.tenant_id=predecessor.tenant_id "
            "AND attempt_count.agent_run_id=predecessor.id)=1 "
            "AND EXISTS (SELECT 1 FROM agent_runtime.model_attempts attempt "
            "WHERE attempt.tenant_id=predecessor.tenant_id "
            "AND attempt.agent_run_id=predecessor.id "
            "AND attempt.status='FAILED_NO_RESPONSE' "
            "AND attempt.failure_code=predecessor.failure_code "
            "AND attempt.provider_response_id IS NULL "
            "AND attempt.input_tokens=0 AND attempt.output_tokens=0 "
            "AND attempt.total_tokens=0 AND attempt.estimated_cost=0 "
            "AND attempt.unknown_cost=0) "
            "AND NOT EXISTS (SELECT 1 FROM agent_runtime.model_cost_reconciliations rec "
            "WHERE rec.tenant_id=predecessor.tenant_id "
            "AND rec.agent_run_id=predecessor.id))"
        )
    return (
        "CREATE OR REPLACE FUNCTION agent_runtime.validate_agent_run_recovery() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF NEW.recovery_of_run_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM agent_runtime.agent_runs predecessor "
        "WHERE predecessor.tenant_id=NEW.tenant_id "
        "AND predecessor.id=NEW.recovery_of_run_id "
        "AND predecessor.status='FAILED' AND "
        f"(predecessor.failure_code IN ({_STRANDED_CODES}){known_failure})) "
        "THEN RAISE EXCEPTION 'recovery predecessor is not terminal and qualified'; "
        "END IF; RETURN NEW; END; $$"
    )


def upgrade() -> None:
    op.execute(_recovery_guard(allow_known_failure=True))
    op.execute("REVOKE ALL ON FUNCTION agent_runtime.validate_agent_run_recovery() FROM PUBLIC")


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_runtime.agent_runs successor "
        "JOIN agent_runtime.agent_runs predecessor "
        "ON predecessor.tenant_id=successor.tenant_id "
        "AND predecessor.id=successor.recovery_of_run_id "
        f"WHERE predecessor.failure_code IN ({_KNOWN_NO_RESPONSE_CODES})) THEN "
        "RAISE EXCEPTION 'cannot downgrade known-failure AgentRun retry lineage'; "
        "END IF; END $$"
    )
    op.execute(_recovery_guard(allow_known_failure=False))
    op.execute("REVOKE ALL ON FUNCTION agent_runtime.validate_agent_run_recovery() FROM PUBLIC")
