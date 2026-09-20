"""Deterministic policy principal for governed social publishing execution."""

from decimal import Decimal

from creative_marketer.agent_governance.domain import (
    AgentVersionConfiguration,
    BudgetPeriod,
    ModelPolicy,
    PeriodBudgetPolicy,
    RunBudgetPolicy,
)


def social_execution_configuration() -> AgentVersionConfiguration:
    """Return a zero-model workload principal; this is never routed to AgentRuntime."""
    return AgentVersionConfiguration(
        display_name="Social Publishing Execution Workload",
        mission="Execute only exact human-approved PublicationDrafts through Tool Gateway.",
        responsibilities=(
            "Reload publication authority from PostgreSQL",
            "Submit exact approved drafts through governed social tools",
            "Reconcile known provider operations without blind resubmission",
        ),
        system_instructions=(
            "Deterministic workload policy principal. Never invoke a model. Never accept "
            "caption, destination, media, or credentials from Temporal. Reload PostgreSQL "
            "authority and use only immutable social.publish Tool contracts."
        ),
        prompt_revision="social_publishing_execution_v1",
        model_policy=ModelPolicy("publishing_execution", ("text",), 1),
        run_budget_policy=RunBudgetPolicy(0, 3, 0, Decimal("0"), "USD"),
        period_budget_policy=PeriodBudgetPolicy(BudgetPeriod.DAILY, None, Decimal("0"), "USD"),
        read_scopes=(),
        write_scopes=("social.publishing",),
        memory_scopes=(),
        allowed_tool_keys=(
            "social.publish.submit",
            "social.publish.status",
            "social.publish.cancel",
        ),
        denied_tool_keys=(),
        approval_policy_key="publishing.human_review",
    )
