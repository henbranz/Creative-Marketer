from collections.abc import Callable

from creative_marketer.agent_runtime.domain import ModelInvocation, ModelInvocationResult


class FakeModelProvider:
    """Deterministic, credential-free model adapter for CI and application tests."""

    def __init__(
        self, result: ModelInvocationResult | Callable[[ModelInvocation], ModelInvocationResult]
    ) -> None:
        self._result = result
        self.calls: list[ModelInvocation] = []

    async def generate_structured(self, invocation: ModelInvocation) -> ModelInvocationResult:
        self.calls.append(invocation)
        return self._result(invocation) if callable(self._result) else self._result
