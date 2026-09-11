from creative_marketer.agent_runtime.application import WorkloadIdentity


class ConfiguredWorkloadIdentityProvider:
    """Expose the validated, deployment-injected workload identity."""

    def __init__(self, workload_id: str, environment: str) -> None:
        self._identity = WorkloadIdentity(workload_id, environment)

    async def current(self) -> WorkloadIdentity:
        return self._identity
