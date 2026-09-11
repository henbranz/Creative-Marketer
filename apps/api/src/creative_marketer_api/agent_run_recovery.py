"""Explicit internal AgentRun recovery CLI; there is deliberately no HTTP equivalent."""

import argparse
import asyncio
import json
import sys
from decimal import Decimal, InvalidOperation
from uuid import UUID

from creative_marketer.agent_runtime.application import (
    AgentRunRecoveryService,
    ModelRouter,
    initial_researcher_route,
)
from creative_marketer.infrastructure.database.agent_runtime_uow import (
    SqlAlchemyAgentRuntimeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.recovery_operator import ConfiguredRecoveryOperatorProvider
from creative_marketer.observability.ports import NullTelemetry
from creative_marketer_api.config import Settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-run-recovery")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stranded")
    for command in ("abandon", "rerun"):
        child = commands.add_parser(command)
        child.add_argument("run_id", type=UUID)
    reconcile = commands.add_parser("reconcile-cost")
    reconcile.add_argument("run_id", type=UUID)
    reconcile.add_argument("actual_cost")
    reconcile.add_argument("currency")
    return parser


def _service(settings: Settings) -> AgentRunRecoveryService:
    if settings.agent_recovery_operator_id is None or settings.agent_recovery_tenant_id is None:
        raise RuntimeError("AGENT_RECOVERY_OPERATOR_ID and AGENT_RECOVERY_TENANT_ID are required")
    return AgentRunRecoveryService(
        SqlAlchemyAgentRuntimeUnitOfWorkFactory(create_session_factory(str(settings.database_url))),
        ModelRouter((initial_researcher_route(),)),
        ConfiguredRecoveryOperatorProvider(
            settings.agent_recovery_tenant_id,
            settings.agent_recovery_operator_id,
            settings.app_env,
        ),
        NullTelemetry(),
    )


async def run(arguments: argparse.Namespace) -> None:
    service = _service(Settings())
    if arguments.command == "stranded":
        values = await service.find_stranded()
        sys.stdout.write(
            json.dumps(
                [
                    {
                        "agent_run_id": str(value.run.id),
                        "tenant_id": str(value.run.tenant_id),
                        "agent_version_id": str(value.run.agent_version_id),
                        "attempt_id": str(value.attempt.id),
                        "attempt_status": value.attempt.status.value,
                        "classification": value.classification.value,
                        "provider": value.attempt.provider,
                        "model": value.attempt.model,
                        "lease_expires_at": value.attempt.lease_expires_at.isoformat(),
                        "provider_response_id": value.attempt.provider_response_id,
                        "input_tokens": value.attempt.input_tokens,
                        "output_tokens": value.attempt.output_tokens,
                        "total_tokens": value.attempt.total_tokens,
                        "estimated_cost": str(value.attempt.estimated_cost),
                        "unknown_cost": str(value.attempt.unknown_cost),
                        "currency": value.run.currency,
                    }
                    for value in values
                ],
                sort_keys=True,
            )
            + "\n"
        )
        return
    if arguments.command == "abandon":
        value = await service.abandon(arguments.run_id)
    elif arguments.command == "rerun":
        value = await service.rerun_as_new(arguments.run_id)
    else:
        try:
            actual_cost = Decimal(arguments.actual_cost)
        except InvalidOperation as error:
            raise ValueError("actual_cost must be an exact decimal") from error
        await service.reconcile_unknown_cost(
            arguments.run_id,
            actual_cost=actual_cost,
            currency=arguments.currency,
        )
        sys.stdout.write(
            json.dumps({"agent_run_id": str(arguments.run_id), "status": "reconciled"}) + "\n"
        )
        return
    sys.stdout.write(
        json.dumps(
            {
                "agent_run_id": str(value.id),
                "status": value.status.value,
                "recovery_of_run_id": (
                    str(value.recovery_of_run_id) if value.recovery_of_run_id else None
                ),
            },
            sort_keys=True,
        )
        + "\n"
    )


def main() -> None:
    asyncio.run(run(_parser().parse_args()))


if __name__ == "__main__":
    main()
