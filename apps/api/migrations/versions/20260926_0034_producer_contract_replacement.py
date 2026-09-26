"""Permit one explicit Producer v1 validation-failure successor.

Revision ID: 20260926_0034
Revises: 20260925_0033
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260926_0034"
down_revision: str | None = "20260925_0033"
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
_PLAN_DIGEST_CONSTRAINT = (
    "concept_digest ~ '^sha256:[0-9a-f]{64}$' "
    "AND concept_set_digest ~ '^sha256:[0-9a-f]{64}$' "
    "AND product_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' "
    "AND research_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' "
    "AND context_digest ~ '^sha256:[0-9a-f]{64}$' "
    "AND semantic_digest ~ '^sha256:[0-9a-f]{64}$'"
)


def _recovery_guard(*, allow_producer_replacement: bool) -> str:
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
    producer_failure = ""
    if allow_producer_replacement:
        producer_failure = (
            " OR (predecessor.agent_type='producer' "
            "AND NEW.agent_type='producer' "
            "AND NEW.product_id=predecessor.product_id "
            "AND predecessor.output_contract_key='production.production_plan' "
            "AND predecessor.output_contract_version=1 "
            "AND NEW.output_contract_key=predecessor.output_contract_key "
            "AND NEW.output_contract_version=2 "
            "AND NEW.agent_version_id<>predecessor.agent_version_id "
            "AND predecessor.failure_code='MODEL_INVALID_OUTPUT' "
            "AND predecessor.provider_response_id IS NOT NULL "
            "AND predecessor.result_ref IS NULL AND predecessor.model_call_count=1 "
            "AND (SELECT count(*) FROM agent_runtime.model_attempts attempt_count "
            "WHERE attempt_count.tenant_id=predecessor.tenant_id "
            "AND attempt_count.agent_run_id=predecessor.id)=1 "
            "AND EXISTS (SELECT 1 FROM agent_runtime.model_attempts attempt "
            "WHERE attempt.tenant_id=predecessor.tenant_id "
            "AND attempt.agent_run_id=predecessor.id "
            "AND attempt.status='SUCCEEDED' "
            "AND attempt.failure_code=predecessor.failure_code "
            "AND attempt.provider_response_status='completed' "
            "AND attempt.provider_failure_reason IS NULL "
            "AND attempt.usage_available "
            "AND attempt.provider_response_id=predecessor.provider_response_id "
            "AND attempt.input_tokens=predecessor.input_tokens "
            "AND attempt.output_tokens=predecessor.output_tokens "
            "AND attempt.total_tokens=predecessor.total_tokens "
            "AND attempt.estimated_cost=predecessor.estimated_cost "
            "AND attempt.unknown_cost=0))"
        )
    return (
        "CREATE OR REPLACE FUNCTION agent_runtime.validate_agent_run_recovery() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF NEW.recovery_of_run_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM agent_runtime.agent_runs predecessor "
        "WHERE predecessor.tenant_id=NEW.tenant_id "
        "AND predecessor.id=NEW.recovery_of_run_id "
        "AND predecessor.status='FAILED' AND "
        f"(predecessor.failure_code IN ({_STRANDED_CODES}){known_failure}{producer_failure})) "
        "THEN RAISE EXCEPTION 'recovery predecessor is not terminal and qualified'; "
        "END IF; RETURN NEW; END; $$"
    )


def upgrade() -> None:
    op.drop_constraint(
        "ck_production_plans_digests",
        "production_plans",
        schema="production",
        type_="check",
    )
    op.create_check_constraint(
        "ck_production_plans_digests",
        "production_plans",
        f"schema_version IN (1,2) AND {_PLAN_DIGEST_CONSTRAINT}",
        schema="production",
    )
    op.execute(_recovery_guard(allow_producer_replacement=True))
    op.execute("REVOKE ALL ON FUNCTION agent_runtime.validate_agent_run_recovery() FROM PUBLIC")


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM production.production_plans "
        "WHERE schema_version=2) THEN "
        "RAISE EXCEPTION 'cannot downgrade with ProductionPlan v2 rows'; "
        "END IF; IF EXISTS (SELECT 1 FROM agent_runtime.agent_runs successor "
        "JOIN agent_runtime.agent_runs predecessor "
        "ON predecessor.tenant_id=successor.tenant_id "
        "AND predecessor.id=successor.recovery_of_run_id "
        "WHERE predecessor.agent_type='producer' "
        "AND predecessor.output_contract_version=1 "
        "AND predecessor.failure_code='MODEL_INVALID_OUTPUT') THEN "
        "RAISE EXCEPTION 'cannot downgrade Producer validation replacement lineage'; "
        "END IF; END $$"
    )
    op.drop_constraint(
        "ck_production_plans_digests",
        "production_plans",
        schema="production",
        type_="check",
    )
    op.create_check_constraint(
        "ck_production_plans_digests",
        "production_plans",
        f"schema_version=1 AND {_PLAN_DIGEST_CONSTRAINT}",
        schema="production",
    )
    op.execute(_recovery_guard(allow_producer_replacement=False))
    op.execute("REVOKE ALL ON FUNCTION agent_runtime.validate_agent_run_recovery() FROM PUBLIC")
