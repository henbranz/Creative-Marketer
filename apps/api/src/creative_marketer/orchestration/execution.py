from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from creative_marketer.agent_runtime.application import AgentRunService
from creative_marketer.agent_runtime.domain import AgentRun
from creative_marketer.assembly.application import AssemblyService
from creative_marketer.creative.application import CreativeService
from creative_marketer.creative.domain import ChannelIntent, CreativeStrategyRequest
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.production.domain import ProductionPlanningRequest
from creative_marketer.production.service import ProductionService

from .application import PipelineStateService
from .domain import (
    PipelineActionMismatch,
    PipelineApprovalRequired,
    PipelineExecutionInvariant,
)
from .pipeline import (
    EXECUTION_BEHAVIOR_REGISTRY,
    NextPipelineAction,
    PipelineAction,
    PipelineExecutionBehavior,
    PipelineLocator,
)


class PipelineExecutionOutcome(StrEnum):
    EXECUTED = "EXECUTED"
    WAITING = "WAITING"
    HUMAN_ACTION_REQUIRED = "HUMAN_ACTION_REQUIRED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True, slots=True)
class PipelineExecutionResult:
    outcome: PipelineExecutionOutcome
    behavior: PipelineExecutionBehavior
    operation: str
    api_boundary: str
    before: NextPipelineAction
    after: NextPipelineAction | None
    locator: PipelineLocator
    resource_type: str | None = None
    resource_id: UUID | None = None
    provider_execution_occurred: bool = False


def pipeline_approval_phrase(action: PipelineAction) -> str:
    return f"I_APPROVE_{action.value}"


def _transition_id(
    context: ExecutionContext, locator: PipelineLocator, action: PipelineAction
) -> UUID:
    components = (
        str(context.tenant_id),
        str(locator.product_id),
        action.value,
        str(locator.research_run_id or ""),
        str(locator.creative_run_id or ""),
        str(locator.concept_id or ""),
        str(locator.creative_revalidation_id or ""),
        str(locator.producer_run_id or ""),
        str(locator.production_plan_id or ""),
        str(locator.assembly_plan_id or ""),
        str(locator.final_creative_id or ""),
    )
    return uuid5(NAMESPACE_URL, "creative-marketer:pipeline:" + ":".join(components))


def _idempotency_key(action: PipelineAction, transition_id: UUID) -> str:
    return f"pipeline:{action.value.lower()}:{transition_id}"


@dataclass(slots=True)
class PipelineActionExecutor:
    """Execute exactly the currently resolved governed pipeline action.

    Resolution remains authoritative. This boundary performs no model/provider
    execution: paid actions only admit a governed PENDING AgentRun after an
    explicit action-bound operator acknowledgement.
    """

    state: PipelineStateService
    agents: AgentRunService
    creative: CreativeService
    production: ProductionService
    assembly: AssemblyService | None = None

    async def execute(
        self,
        context: ExecutionContext,
        locator: PipelineLocator,
        *,
        expected_action: PipelineAction,
        explicit_approval: str | None = None,
    ) -> PipelineExecutionResult:
        before = await self.state.resolve_next_action(context, locator)
        if before.action is not expected_action:
            raise PipelineActionMismatch(
                f"expected {expected_action.value}, current action is {before.action.value}"
            )
        definition = EXECUTION_BEHAVIOR_REGISTRY[before.action]
        passive = {
            PipelineExecutionBehavior.WAIT: PipelineExecutionOutcome.WAITING,
            PipelineExecutionBehavior.HUMAN_GATE: (PipelineExecutionOutcome.HUMAN_ACTION_REQUIRED),
            PipelineExecutionBehavior.RECOVERY_GATE: PipelineExecutionOutcome.RECOVERY_REQUIRED,
            PipelineExecutionBehavior.TERMINAL: PipelineExecutionOutcome.COMPLETE,
        }
        if definition.behavior is not PipelineExecutionBehavior.EXECUTE:
            return PipelineExecutionResult(
                passive[definition.behavior],
                definition.behavior,
                definition.operation,
                definition.api_boundary,
                before,
                None,
                locator,
            )
        if definition.explicit_approval_required and explicit_approval != pipeline_approval_phrase(
            before.action
        ):
            raise PipelineApprovalRequired(
                f"explicit approval is required for {before.action.value}"
            )

        bound_locator = PipelineLocator(
            product_id=locator.product_id,
            research_run_id=before.research_run_id,
            creative_run_id=before.creative_run_id,
            concept_id=before.concept_id,
            creative_revalidation_id=before.creative_revalidation_id,
            producer_run_id=before.producer_run_id,
            production_plan_id=before.production_plan_id,
            use_latest_when_unbound=False,
            assembly_plan_id=before.assembly_plan_id,
            final_creative_id=before.final_creative_id,
        )
        transition_id = _transition_id(context, bound_locator, before.action)
        resource_type, resource_id, updated = await self._execute(
            context,
            bound_locator,
            before,
            transition_id,
        )
        after = await self.state.resolve_next_action(context, updated)
        return PipelineExecutionResult(
            PipelineExecutionOutcome.EXECUTED,
            definition.behavior,
            definition.operation,
            definition.api_boundary,
            before,
            after,
            updated,
            resource_type,
            resource_id,
            False,
        )

    async def _execute(
        self,
        context: ExecutionContext,
        locator: PipelineLocator,
        before: NextPipelineAction,
        transition_id: UUID,
    ) -> tuple[str, UUID, PipelineLocator]:
        action = before.action
        idempotency_key = _idempotency_key(action, transition_id)

        if action in {
            PipelineAction.RUN_RESEARCH,
            PipelineAction.REFRESH_RESEARCH,
            PipelineAction.RERUN_RESEARCH,
        }:
            run = await self.agents.request_researcher(
                context,
                product_id=locator.product_id,
                idempotency_key=idempotency_key,
            )
            return (
                "agent_run",
                run.id,
                replace(locator, research_run_id=run.id),
            )

        if action in {
            PipelineAction.RUN_CREATIVE,
            PipelineAction.RERUN_CREATIVE,
            PipelineAction.RESTRATEGIZE_CREATIVE,
        }:
            creative_request = CreativeStrategyRequest(5, ChannelIntent.ORGANIC_SHORT_FORM)
            if before.creative_run_id is not None:
                prior = await self.agents.get_run(context, before.creative_run_id)
                creative_request = self._creative_request(prior)
            restrategy_id = (
                before.concept_id if action is PipelineAction.RESTRATEGIZE_CREATIVE else None
            )
            if action is PipelineAction.RESTRATEGIZE_CREATIVE and restrategy_id is None:
                raise PipelineExecutionInvariant(
                    "Creative restrategy requires exact Concept authority"
                )
            run = await self.agents.request_creative_strategist(
                context,
                product_id=locator.product_id,
                request=creative_request,
                idempotency_key=idempotency_key,
                restrategy_of_concept_id=restrategy_id,
            )
            return (
                "agent_run",
                run.id,
                replace(
                    locator,
                    creative_run_id=run.id,
                    concept_id=None,
                    creative_revalidation_id=None,
                    producer_run_id=None,
                    production_plan_id=None,
                ),
            )

        if action is PipelineAction.REPLACE_OUTPUT_LIMITED_CREATIVE:
            if before.creative_run_id is None:
                raise PipelineExecutionInvariant("Creative replacement requires a failed run")
            run = await self.agents.request_creative_replacement(
                context,
                product_id=locator.product_id,
                failed_run_id=before.creative_run_id,
                transition_id=transition_id,
            )
            return (
                "agent_run",
                run.id,
                replace(
                    locator,
                    creative_run_id=run.id,
                    concept_id=None,
                    creative_revalidation_id=None,
                    producer_run_id=None,
                    production_plan_id=None,
                ),
            )

        if action is PipelineAction.REVALIDATE_CREATIVE:
            if before.concept_id is None:
                raise PipelineExecutionInvariant("Creative revalidation requires a Concept")
            value = await self.creative.revalidate(context, before.concept_id)
            return (
                "creative_concept_revalidation",
                value.id,
                replace(locator, creative_revalidation_id=value.id),
            )

        if action in {PipelineAction.RUN_PRODUCER, PipelineAction.RERUN_PRODUCER}:
            if before.concept_id is None:
                raise PipelineExecutionInvariant("Producer admission requires an approved Concept")
            production_request = ProductionPlanningRequest()
            if before.producer_run_id is not None:
                prior = await self.agents.get_run(context, before.producer_run_id)
                production_request = self._producer_request(prior)
            if action is PipelineAction.RERUN_PRODUCER and before.production_plan_id is not None:
                plan = await self.production.get_plan(context, before.production_plan_id)
                if plan.decision is None or plan.decision.rejection_feedback is None:
                    raise PipelineExecutionInvariant(
                        "rejected Production Plan rerun requires bounded human feedback"
                    )
                production_request = replace(
                    production_request,
                    rejection_feedback=plan.decision.rejection_feedback,
                )
            run = await self.agents.request_producer(
                context,
                concept_id=before.concept_id,
                request=production_request,
                idempotency_key=idempotency_key,
            )
            return (
                "agent_run",
                run.id,
                replace(locator, producer_run_id=run.id, production_plan_id=None),
            )

        if action is PipelineAction.UPGRADE_PRODUCER_CONTRACT:
            if before.producer_run_id is None:
                raise PipelineExecutionInvariant("Producer upgrade requires a failed run")
            run = await self.agents.request_producer_replacement(
                context,
                product_id=locator.product_id,
                failed_run_id=before.producer_run_id,
                transition_id=transition_id,
            )
            return (
                "agent_run",
                run.id,
                replace(locator, producer_run_id=run.id, production_plan_id=None),
            )

        if action is PipelineAction.CREATE_ASSEMBLY_PLAN:
            if before.production_plan_id is None:
                raise PipelineExecutionInvariant(
                    "Assembly planning requires exact ProductionPlan authority"
                )
            if self.assembly is None:
                raise PipelineExecutionInvariant("Assembly executor is not configured")
            record = await self.assembly.create_plan(context, before.production_plan_id)
            return (
                "assembly_plan",
                record.plan.id,
                replace(locator, assembly_plan_id=record.plan.id),
            )

        raise PipelineExecutionInvariant(f"no executable operation for {action.value}")

    @staticmethod
    def _creative_request(run: AgentRun) -> CreativeStrategyRequest:
        reference = next(
            (item for item in run.input_context_refs if item.get("kind") == "strategy_request"),
            None,
        )
        try:
            if reference is None:
                raise ValueError
            count = reference["concept_count"]
            channel = reference["channel_intent"]
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or not isinstance(channel, str)
            ):
                raise ValueError
            return CreativeStrategyRequest(count, ChannelIntent(channel))
        except (KeyError, TypeError, ValueError):
            raise PipelineExecutionInvariant("Creative request provenance is invalid") from None

    @staticmethod
    def _producer_request(run: AgentRun) -> ProductionPlanningRequest:
        reference = next(
            (item for item in run.input_context_refs if item.get("kind") == "production_request"),
            None,
        )
        try:
            if reference is None:
                raise ValueError
            return ProductionPlanningRequest(
                str(reference["target_format"]),
                str(reference["aspect_ratio"]),
                (
                    str(reference["rejection_feedback"])
                    if reference.get("rejection_feedback") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            raise PipelineExecutionInvariant("Producer request provenance is invalid") from None
