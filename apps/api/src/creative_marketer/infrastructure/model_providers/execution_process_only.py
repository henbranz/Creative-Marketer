from creative_marketer.agent_runtime.domain import (
    ModelInvocation,
    ModelInvocationResult,
    ModelProviderError,
    ProviderContractCompilation,
)
from creative_marketer.infrastructure.model_providers.openai_schema import (
    compile_openai_strict_output_schema,
)


class ExecutionProcessOnlyModelProvider:
    """API-side availability marker that never holds credentials or performs inference."""

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
        del invocation
        raise ModelProviderError("model invocation is restricted to the Agent worker process")
