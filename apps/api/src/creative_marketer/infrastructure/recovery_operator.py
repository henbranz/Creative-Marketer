from uuid import UUID, uuid4

from creative_marketer.agent_runtime.application import RecoveryOperator, WorkloadIdentity


class ConfiguredRecoveryOperatorProvider:
    """Resolve trusted operator authority exclusively from immutable process configuration."""

    def __init__(self, tenant_id: UUID, workload_id: str, environment: str) -> None:
        self._operator = RecoveryOperator(
            tenant_id,
            WorkloadIdentity(workload_id, environment),
            uuid4(),
        )

    async def current(self) -> RecoveryOperator:
        return self._operator
