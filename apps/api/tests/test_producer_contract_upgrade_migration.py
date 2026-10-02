# mypy: disable-error-code="no-untyped-def,no-untyped-call,union-attr"

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def migration_module():
    path = (
        Path(__file__).parents[1]
        / "migrations/versions/20261002_0035_producer_contract_upgrade_replacement.py"
    )
    spec = spec_from_file_location("producer_contract_upgrade_migration", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generalized_recovery_guard_requires_strict_upgrade_and_authoritative_response() -> None:
    sql = migration_module()._recovery_guard(generalized_producer_upgrade=True)
    assert "NEW.output_contract_version>predecessor.output_contract_version" in sql
    assert "predecessor.output_contract_version=1" in sql
    assert "predecessor.failure_code='MODEL_INVALID_OUTPUT'" in sql
    assert "predecessor.output_contract_version>=2" in sql
    assert "predecessor.failure_code='PRODUCTION_PLAN_INVALID'" in sql
    assert "attempt.status='SUCCEEDED'" in sql
    assert "attempt.provider_response_status='completed'" in sql
    assert "attempt.usage_available" in sql
    assert "attempt.unknown_cost=0" in sql
    assert "production.production_plans existing_plan" in sql


def test_downgrade_guard_restores_historical_v1_to_v2_constraint() -> None:
    sql = migration_module()._recovery_guard(generalized_producer_upgrade=False)
    assert "predecessor.output_contract_version=1" in sql
    assert "NEW.output_contract_version=2" in sql
    assert "NEW.output_contract_version>predecessor.output_contract_version" not in sql
    assert "production.production_plans existing_plan" not in sql
