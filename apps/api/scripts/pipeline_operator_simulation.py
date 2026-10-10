# mypy: disable-error-code="import-untyped"
"""Print and validate the authoritative Product-to-FinalCreative action matrix."""

from __future__ import annotations

from creative_marketer.orchestration.pipeline import (
    EXECUTION_BEHAVIOR_REGISTRY,
    PipelineAction,
    PipelineExecutionBehavior,
)


def validate_action_matrix() -> tuple[str, ...]:
    errors: list[str] = []
    missing = set(PipelineAction) - set(EXECUTION_BEHAVIOR_REGISTRY)
    extra = set(EXECUTION_BEHAVIOR_REGISTRY) - set(PipelineAction)
    if missing:
        errors.append("missing: " + ", ".join(sorted(item.value for item in missing)))
    if extra:
        errors.append("unknown registry entries")
    for action in PipelineAction:
        definition = EXECUTION_BEHAVIOR_REGISTRY.get(action)
        if definition is None:
            continue
        if (
            not definition.operation
            or not definition.api_boundary
            or not definition.resulting_states
        ):
            errors.append(f"{action.value}: incomplete execution metadata")
        if definition.behavior is not PipelineExecutionBehavior.EXECUTE:
            if definition.provider_execution_permitted:
                errors.append(f"{action.value}: passive action permits provider execution")
        elif definition.provider_cost and not definition.explicit_approval_required:
            errors.append(f"{action.value}: paid execution lacks explicit approval")
    return tuple(errors)


def run() -> int:
    errors = validate_action_matrix()
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 2
    for action in PipelineAction:
        definition = EXECUTION_BEHAVIOR_REGISTRY[action]
        cost = "PAID" if definition.provider_cost else "FREE"
        approval = "APPROVAL" if definition.explicit_approval_required else "NO_APPROVAL"
        print(
            f"{action.value:<36} {definition.behavior.value:<14} "
            f"{cost:<5} {approval:<11} {definition.operation}"
        )
    print("ALL RESOLVER ACTIONS HAVE EXECUTION BEHAVIOR")
    print("NO NORMAL PIPELINE DEAD ENDS FOUND")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
