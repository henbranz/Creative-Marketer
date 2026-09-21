"""Allow known provider rejections after the provider-start checkpoint.

Revision ID: 20260922_0031
Revises: 20260920_0030
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260922_0031"
down_revision: str | None = "20260920_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIFECYCLE_CONSTRAINT = "ck_model_attempts_ck_model_attempts_lifecycle"


def _protect_model_attempt(*, allow_known_provider_rejection: bool) -> str:
    provider_transitions = (
        "('RESPONSE_RECORDED','UNKNOWN','FAILED_NO_RESPONSE')"
        if allow_known_provider_rejection
        else "('RESPONSE_RECORDED','UNKNOWN')"
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
        "IF OLD.status = NEW.status AND NEW IS DISTINCT FROM OLD THEN "
        "RAISE EXCEPTION 'ModelAttempt update requires a lifecycle transition'; END IF; "
        "IF OLD.status <> NEW.status AND NOT ((OLD.status='CLAIMED' AND NEW.status IN "
        "('PROVIDER_STARTED','FAILED_NO_RESPONSE')) OR (OLD.status='PROVIDER_STARTED' AND "
        f"NEW.status IN {provider_transitions}) OR (OLD.status='RESPONSE_RECORDED' AND "
        "NEW.status='SUCCEEDED')) THEN RAISE EXCEPTION 'invalid ModelAttempt transition'; "
        "END IF; IF OLD.status IN ('SUCCEEDED','FAILED_NO_RESPONSE','UNKNOWN') AND "
        "NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'terminal ModelAttempt is immutable'; "
        "END IF; RETURN NEW; END; $$"
    )


def _model_attempt_lifecycle(*, allow_provider_started_failure: bool) -> str:
    failed_provider_started = (
        "" if allow_provider_started_failure else "provider_started_at IS NULL AND "
    )
    return (
        "(status = 'CLAIMED' AND provider_started_at IS NULL AND response_recorded_at IS NULL "
        "AND finished_at IS NULL AND provider_response_id IS NULL AND input_tokens = 0 AND "
        "output_tokens = 0 AND total_tokens = 0 AND estimated_cost = 0 AND unknown_cost = 0 "
        "AND failure_code IS NULL) OR (status = 'PROVIDER_STARTED' AND provider_started_at IS "
        "NOT NULL AND response_recorded_at IS NULL AND finished_at IS NULL AND "
        "provider_response_id IS NULL AND input_tokens = 0 AND output_tokens = 0 AND "
        "total_tokens = 0 AND estimated_cost = 0 AND unknown_cost = 0 AND failure_code IS NULL) "
        "OR (status = 'RESPONSE_RECORDED' AND provider_started_at IS NOT NULL AND "
        "response_recorded_at IS NOT NULL AND finished_at IS NULL AND unknown_cost = 0 AND "
        "failure_code IS NULL) OR (status = 'SUCCEEDED' AND provider_started_at IS NOT NULL "
        "AND response_recorded_at IS NOT NULL AND finished_at IS NOT NULL) OR (status = "
        f"'FAILED_NO_RESPONSE' AND {failed_provider_started}response_recorded_at IS NULL AND "
        "finished_at IS NOT NULL AND provider_response_id IS NULL AND input_tokens = 0 AND "
        "output_tokens = 0 AND total_tokens = 0 AND estimated_cost = 0 AND unknown_cost = 0) "
        "OR (status = 'UNKNOWN' AND provider_started_at IS NOT NULL AND response_recorded_at IS "
        "NULL AND finished_at IS NOT NULL AND provider_response_id IS NULL AND input_tokens = 0 "
        "AND output_tokens = 0 AND total_tokens = 0 AND estimated_cost = 0 AND unknown_cost > 0)"
    )


def upgrade() -> None:
    op.execute(f"ALTER TABLE agent_runtime.model_attempts DROP CONSTRAINT {LIFECYCLE_CONSTRAINT}")
    op.execute(
        f"ALTER TABLE agent_runtime.model_attempts ADD CONSTRAINT {LIFECYCLE_CONSTRAINT} "
        f"CHECK ({_model_attempt_lifecycle(allow_provider_started_failure=True)})"
    )
    op.execute(_protect_model_attempt(allow_known_provider_rejection=True))
    op.execute("REVOKE ALL ON FUNCTION agent_runtime.protect_model_attempt() FROM PUBLIC")


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_runtime.model_attempts "
        "WHERE status='FAILED_NO_RESPONSE' AND provider_started_at IS NOT NULL) THEN "
        "RAISE EXCEPTION 'cannot downgrade known provider rejection evidence'; END IF; END $$"
    )
    op.execute(f"ALTER TABLE agent_runtime.model_attempts DROP CONSTRAINT {LIFECYCLE_CONSTRAINT}")
    op.execute(
        f"ALTER TABLE agent_runtime.model_attempts ADD CONSTRAINT {LIFECYCLE_CONSTRAINT} "
        f"CHECK ({_model_attempt_lifecycle(allow_provider_started_failure=False)})"
    )
    op.execute(_protect_model_attempt(allow_known_provider_rejection=False))
    op.execute("REVOKE ALL ON FUNCTION agent_runtime.protect_model_attempt() FROM PUBLIC")
