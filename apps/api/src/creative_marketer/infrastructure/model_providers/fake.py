from collections.abc import Callable

from creative_marketer.agent_runtime.domain import (
    ModelInvocation,
    ModelInvocationResult,
    ProviderContractCompilation,
)
from creative_marketer.infrastructure.model_providers.openai_schema import (
    compile_openai_strict_output_schema,
)


class FakeModelProvider:
    """Deterministic, credential-free model adapter for CI and application tests."""

    def __init__(
        self, result: ModelInvocationResult | Callable[[ModelInvocation], ModelInvocationResult]
    ) -> None:
        self._result = result
        self.calls: list[ModelInvocation] = []

    def validate_invocation(self, invocation: ModelInvocation) -> ProviderContractCompilation:
        compiled = compile_openai_strict_output_schema(
            invocation.output_schema,
            contract_key=invocation.output_contract_key,
            contract_version=invocation.output_contract_version,
        )
        return ProviderContractCompilation(
            compiled.contract_key,
            compiled.contract_version,
            compiled.schema,
            compiled.digest,
            compiled.compiler_revision,
        )

    async def generate_structured(self, invocation: ModelInvocation) -> ModelInvocationResult:
        self.calls.append(invocation)
        return self._result(invocation) if callable(self._result) else self._result
