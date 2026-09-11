from creative_marketer.agent_runtime.domain import (
    ModelInvocation,
    ModelInvocationResult,
    ModelProviderError,
)


class ExecutionProcessOnlyModelProvider:
    """API-side availability marker that never holds credentials or performs inference."""

    async def generate_structured(self, invocation: ModelInvocation) -> ModelInvocationResult:
        del invocation
        raise ModelProviderError("model invocation is restricted to the Agent worker process")
