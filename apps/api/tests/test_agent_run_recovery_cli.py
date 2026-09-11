from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from creative_marketer.agent_runtime.domain import (
    AgentRun,
    AgentRunStatus,
    ModelAttempt,
    ModelAttemptStatus,
    RecoveryClassification,
    StrandedAgentRun,
)
from creative_marketer.infrastructure.recovery_operator import ConfiguredRecoveryOperatorProvider
from creative_marketer_api import agent_run_recovery
from creative_marketer_api.agent_run_recovery import _parser, _service
from creative_marketer_api.config import Settings
from tests.test_agent_runtime_domain import run


class RecoveryService:
    def __init__(self) -> None:
        self.agent_run = run()
        self.calls: list[tuple[object, ...]] = []

    async def find_stranded(self) -> tuple[StrandedAgentRun, ...]:
        now = datetime.now(UTC)
        attempt = ModelAttempt(
            tenant_id=self.agent_run.tenant_id,
            agent_run_id=self.agent_run.id,
            attempt_number=1,
            workload_id="worker",
            model_route_version="route-v1",
            pricing_version="pricing-v1",
            provider="provider",
            model="model",
            claimed_at=now - timedelta(hours=1),
            lease_expires_at=now - timedelta(minutes=45),
            status=ModelAttemptStatus.UNKNOWN,
            provider_started_at=now - timedelta(minutes=59),
            finished_at=now - timedelta(minutes=58),
            unknown_cost=Decimal("1.25"),
            failure_code="MODEL_TIMEOUT",
        )
        return (
            StrandedAgentRun(
                replace(self.agent_run, status=AgentRunStatus.RUNNING),
                attempt,
                RecoveryClassification.PROVIDER_OUTCOME_UNKNOWN,
            ),
        )

    async def abandon(self, run_id: UUID) -> AgentRun:
        self.calls.append(("abandon", run_id))
        return replace(self.agent_run, id=run_id)

    async def rerun_as_new(self, run_id: UUID) -> AgentRun:
        self.calls.append(("rerun", run_id))
        return replace(self.agent_run, recovery_of_run_id=run_id)

    async def reconcile_unknown_cost(
        self, run_id: UUID, *, actual_cost: Decimal, currency: str
    ) -> None:
        self.calls.append(("reconcile", run_id, actual_cost, currency))


@pytest.mark.asyncio
async def test_recovery_cli_inspects_stranded_runs_without_sensitive_output(
    monkeypatch: Any, capsys: Any
) -> None:
    service = RecoveryService()
    monkeypatch.setattr(agent_run_recovery, "_service", lambda _settings: service)
    await agent_run_recovery.run(_parser().parse_args(["stranded"]))
    output = capsys.readouterr().out
    assert '"classification": "PROVIDER_OUTCOME_UNKNOWN"' in output
    assert '"unknown_cost": "1.25"' in output
    assert "system_instructions" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["abandon", "rerun"])
async def test_recovery_cli_dispatches_terminal_operator_actions(
    command: str, monkeypatch: Any, capsys: Any
) -> None:
    service = RecoveryService()
    monkeypatch.setattr(agent_run_recovery, "_service", lambda _settings: service)
    run_id = uuid4()
    await agent_run_recovery.run(_parser().parse_args([command, str(run_id)]))
    assert service.calls == [(command, run_id)]
    assert str(run_id) in capsys.readouterr().out


@pytest.mark.asyncio
async def test_recovery_cli_reconciles_exact_decimal_once(monkeypatch: Any, capsys: Any) -> None:
    service = RecoveryService()
    monkeypatch.setattr(agent_run_recovery, "_service", lambda _settings: service)
    run_id = uuid4()
    await agent_run_recovery.run(
        _parser().parse_args(["reconcile-cost", str(run_id), "0.012345", "USD"])
    )
    assert service.calls == [("reconcile", run_id, Decimal("0.012345"), "USD")]
    assert '"status": "reconciled"' in capsys.readouterr().out
    with pytest.raises(ValueError, match="exact decimal"):
        await agent_run_recovery.run(
            _parser().parse_args(["reconcile-cost", str(run_id), "invalid", "USD"])
        )


def test_recovery_service_requires_paired_trusted_configuration() -> None:
    with pytest.raises(RuntimeError, match="AGENT_RECOVERY_OPERATOR_ID"):
        _service(Settings())
    configured = _service(
        Settings(
            agent_recovery_operator_id="operations/recovery",
            agent_recovery_tenant_id=uuid4(),
        )
    )
    assert configured.operator_provider is not None


@pytest.mark.asyncio
async def test_configured_recovery_operator_has_stable_workload_and_correlation() -> None:
    tenant_id = uuid4()
    provider = ConfiguredRecoveryOperatorProvider(tenant_id, "operations/recovery", "test")
    first = await provider.current()
    second = await provider.current()
    assert first is second
    assert first.tenant_id == tenant_id
    assert first.workload.workload_id == "operations/recovery"
