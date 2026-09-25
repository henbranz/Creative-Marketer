"""Preserve authoritative returned-provider response semantics.

Revision ID: 20260925_0033
Revises: 20260923_0032
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260925_0033"
down_revision: str | None = "20260923_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_CONSTRAINT = "ck_model_attempts_ck_model_attempts_status"
LIFECYCLE_CONSTRAINT = "ck_model_attempts_ck_model_attempts_lifecycle"


def _lifecycle(*, returned_responses: bool) -> str:
    response_metadata_empty = ""
    response_metadata_recorded = ""
    failed_response = ""
    if returned_responses:
        reason_compatibility = (
            "((provider_response_status='completed' AND provider_failure_reason IN "
            "('SAFETY','INVALID_OUTPUT','USAGE_UNAVAILABLE')) OR "
            "(provider_response_status='incomplete' AND "
            "provider_failure_reason IN ('MAX_OUTPUT_TOKENS','SAFETY','OTHER')) OR "
            "(provider_response_status='failed' AND provider_failure_reason='PROVIDER_FAILED') OR "
            "(provider_response_status='cancelled' AND "
            "provider_failure_reason='PROVIDER_CANCELLED'))"
        )
        response_metadata_empty = (
            "AND provider_response_status IS NULL AND provider_failure_reason IS NULL "
            "AND usage_available IS NULL "
        )
        response_metadata_recorded = (
            "AND provider_response_status IN ('completed','incomplete','failed','cancelled') "
            "AND usage_available IS NOT NULL "
            "AND ((provider_response_status='completed' AND provider_failure_reason IS NULL "
            f"AND usage_available) OR {reason_compatibility}) "
            "AND (usage_available OR (input_tokens=0 AND output_tokens=0 AND total_tokens=0 "
            "AND estimated_cost=0)) "
        )
        failed_response = (
            " OR (status='FAILED_RESPONSE' AND provider_started_at IS NOT NULL "
            "AND response_recorded_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND failure_code IS NOT NULL AND provider_response_status IN "
            "('completed','incomplete','failed','cancelled') "
            "AND provider_failure_reason IN ('MAX_OUTPUT_TOKENS','SAFETY','PROVIDER_FAILED',"
            "'PROVIDER_CANCELLED','INVALID_OUTPUT','USAGE_UNAVAILABLE','OTHER') "
            "AND usage_available IS NOT NULL "
            f"AND {reason_compatibility} AND ((usage_available AND unknown_cost=0) OR "
            "(NOT usage_available AND input_tokens=0 AND output_tokens=0 AND total_tokens=0 "
            "AND estimated_cost=0 AND unknown_cost>0)))"
        )
    return (
        "(status='CLAIMED' AND provider_started_at IS NULL AND response_recorded_at IS NULL "
        "AND finished_at IS NULL AND provider_response_id IS NULL AND input_tokens=0 "
        "AND output_tokens=0 AND total_tokens=0 AND estimated_cost=0 AND unknown_cost=0 "
        f"AND failure_code IS NULL {response_metadata_empty}) OR "
        "(status='PROVIDER_STARTED' AND provider_started_at IS NOT NULL "
        "AND response_recorded_at IS NULL AND finished_at IS NULL "
        "AND provider_response_id IS NULL AND input_tokens=0 AND output_tokens=0 "
        "AND total_tokens=0 AND estimated_cost=0 AND unknown_cost=0 AND failure_code IS NULL "
        f"{response_metadata_empty}) OR "
        "(status='RESPONSE_RECORDED' AND provider_started_at IS NOT NULL "
        "AND response_recorded_at IS NOT NULL AND finished_at IS NULL AND unknown_cost=0 "
        f"AND failure_code IS NULL {response_metadata_recorded}) OR "
        "(status='SUCCEEDED' AND provider_started_at IS NOT NULL "
        "AND response_recorded_at IS NOT NULL AND finished_at IS NOT NULL "
        + (
            "AND provider_response_status='completed' AND provider_failure_reason IS NULL "
            "AND usage_available "
            if returned_responses
            else ""
        )
        + ") OR (status='FAILED_NO_RESPONSE' AND response_recorded_at IS NULL "
        "AND finished_at IS NOT NULL AND provider_response_id IS NULL AND input_tokens=0 "
        "AND output_tokens=0 AND total_tokens=0 AND estimated_cost=0 AND unknown_cost=0 "
        f"{response_metadata_empty}) OR "
        "(status='UNKNOWN' AND provider_started_at IS NOT NULL AND response_recorded_at IS NULL "
        "AND finished_at IS NOT NULL AND provider_response_id IS NULL AND input_tokens=0 "
        "AND output_tokens=0 AND total_tokens=0 AND estimated_cost=0 AND unknown_cost>0 "
        f"{response_metadata_empty}){failed_response}"
    )


def _protect_model_attempt(*, returned_responses: bool) -> str:
    response_transition = (
        "('SUCCEEDED','FAILED_RESPONSE')" if returned_responses else "('SUCCEEDED')"
    )
    terminal = (
        "('SUCCEEDED','FAILED_RESPONSE','FAILED_NO_RESPONSE','UNKNOWN')"
        if returned_responses
        else "('SUCCEEDED','FAILED_NO_RESPONSE','UNKNOWN')"
    )
    return (
        "CREATE OR REPLACE FUNCTION agent_runtime.protect_model_attempt() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF (NEW.tenant_id,NEW.agent_run_id,NEW.attempt_number,NEW.workload_id,"
        "NEW.model_route_version,NEW.pricing_version,NEW.provider,NEW.model,NEW.claimed_at,"
        "NEW.lease_expires_at) IS DISTINCT FROM (OLD.tenant_id,OLD.agent_run_id,"
        "OLD.attempt_number,OLD.workload_id,OLD.model_route_version,OLD.pricing_version,"
        "OLD.provider,OLD.model,OLD.claimed_at,OLD.lease_expires_at) THEN "
        "RAISE EXCEPTION 'ModelAttempt identity is immutable'; END IF; "
        "IF OLD.status=NEW.status AND NEW IS DISTINCT FROM OLD THEN "
        "RAISE EXCEPTION 'ModelAttempt update requires a lifecycle transition'; END IF; "
        "IF OLD.status<>NEW.status AND NOT ((OLD.status='CLAIMED' AND NEW.status IN "
        "('PROVIDER_STARTED','FAILED_NO_RESPONSE')) OR (OLD.status='PROVIDER_STARTED' AND "
        "NEW.status IN ('RESPONSE_RECORDED','UNKNOWN','FAILED_NO_RESPONSE')) OR "
        f"(OLD.status='RESPONSE_RECORDED' AND NEW.status IN {response_transition})) THEN "
        "RAISE EXCEPTION 'invalid ModelAttempt transition'; END IF; "
        f"IF OLD.status IN {terminal} AND NEW IS DISTINCT FROM OLD THEN "
        "RAISE EXCEPTION 'terminal ModelAttempt is immutable'; END IF; RETURN NEW; END; $$"
    )


def upgrade() -> None:
    op.add_column(
        "model_attempts",
        sa.Column("provider_response_status", sa.String(32)),
        schema="agent_runtime",
    )
    op.add_column(
        "model_attempts",
        sa.Column("provider_failure_reason", sa.String(64)),
        schema="agent_runtime",
    )
    op.add_column(
        "model_attempts",
        sa.Column("usage_available", sa.Boolean()),
        schema="agent_runtime",
    )
    op.execute(
        "UPDATE agent_runtime.model_attempts SET provider_response_status='completed', "
        "usage_available=TRUE WHERE status IN ('RESPONSE_RECORDED','SUCCEEDED')"
    )
    op.execute(f"ALTER TABLE agent_runtime.model_attempts DROP CONSTRAINT {STATUS_CONSTRAINT}")
    op.execute(f"ALTER TABLE agent_runtime.model_attempts DROP CONSTRAINT {LIFECYCLE_CONSTRAINT}")
    op.execute(
        f"ALTER TABLE agent_runtime.model_attempts ADD CONSTRAINT {STATUS_CONSTRAINT} CHECK "
        "(status IN ('CLAIMED','PROVIDER_STARTED','RESPONSE_RECORDED','SUCCEEDED',"
        "'FAILED_RESPONSE','FAILED_NO_RESPONSE','UNKNOWN'))"
    )
    op.execute(
        f"ALTER TABLE agent_runtime.model_attempts ADD CONSTRAINT {LIFECYCLE_CONSTRAINT} "
        f"CHECK ({_lifecycle(returned_responses=True)})"
    )
    op.execute(_protect_model_attempt(returned_responses=True))
    op.execute("REVOKE ALL ON FUNCTION agent_runtime.protect_model_attempt() FROM PUBLIC")


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_runtime.model_attempts "
        "WHERE status='FAILED_RESPONSE') THEN RAISE EXCEPTION "
        "'cannot downgrade authoritative returned-response evidence'; END IF; END $$"
    )
    op.execute(f"ALTER TABLE agent_runtime.model_attempts DROP CONSTRAINT {STATUS_CONSTRAINT}")
    op.execute(f"ALTER TABLE agent_runtime.model_attempts DROP CONSTRAINT {LIFECYCLE_CONSTRAINT}")
    op.drop_column("model_attempts", "usage_available", schema="agent_runtime")
    op.drop_column("model_attempts", "provider_failure_reason", schema="agent_runtime")
    op.drop_column("model_attempts", "provider_response_status", schema="agent_runtime")
    op.execute(
        f"ALTER TABLE agent_runtime.model_attempts ADD CONSTRAINT {STATUS_CONSTRAINT} CHECK "
        "(status IN ('CLAIMED','PROVIDER_STARTED','RESPONSE_RECORDED','SUCCEEDED',"
        "'FAILED_NO_RESPONSE','UNKNOWN'))"
    )
    op.execute(
        f"ALTER TABLE agent_runtime.model_attempts ADD CONSTRAINT {LIFECYCLE_CONSTRAINT} "
        f"CHECK ({_lifecycle(returned_responses=False)})"
    )
    op.execute(_protect_model_attempt(returned_responses=False))
    op.execute("REVOKE ALL ON FUNCTION agent_runtime.protect_model_attempt() FROM PUBLIC")
