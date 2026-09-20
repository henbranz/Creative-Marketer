from dataclasses import replace
from uuid import uuid4

import pytest

from creative_marketer.agent_runtime.application import initial_supervisor_route
from creative_marketer.orchestration.application import CyclePreflight, CycleReadinessEngine
from creative_marketer.orchestration.domain import (
    CreativeCycle,
    CycleStage,
    CycleStatus,
    IllegalCycleTransition,
    ReadinessRequirement,
    ReadinessState,
    SupervisorAction,
    SupervisorContextManifest,
    SupervisorReport,
    validate_supervisor_actions,
    validate_transition,
)


def cycle() -> CreativeCycle:
    return CreativeCycle(
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        "sha256:" + "a" * 64,
    )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (CycleStage.CREATED, CycleStage.CHECKING_READINESS),
        (CycleStage.CHECKING_READINESS, CycleStage.RESEARCHING),
        (CycleStage.RESEARCHING, CycleStage.CREATIVE_STRATEGY),
        (CycleStage.CREATIVE_STRATEGY, CycleStage.AWAITING_CONCEPT_APPROVAL),
        (CycleStage.AWAITING_CONCEPT_APPROVAL, CycleStage.PRODUCTION_PLANNING),
        (CycleStage.PRODUCTION_PLANNING, CycleStage.AWAITING_PRODUCTION_APPROVAL),
        (CycleStage.AWAITING_PRODUCTION_APPROVAL, CycleStage.GENERATING_MEDIA),
        (CycleStage.GENERATING_MEDIA, CycleStage.ASSEMBLING_FINAL_CREATIVE),
        (
            CycleStage.ASSEMBLING_FINAL_CREATIVE,
            CycleStage.AWAITING_FINAL_CREATIVE_APPROVAL,
        ),
        (
            CycleStage.AWAITING_FINAL_CREATIVE_APPROVAL,
            CycleStage.AWAITING_PUBLICATION_INPUT,
        ),
        (
            CycleStage.AWAITING_PUBLICATION_INPUT,
            CycleStage.AWAITING_PUBLICATION_APPROVAL,
        ),
        (CycleStage.AWAITING_PUBLICATION_APPROVAL, CycleStage.PUBLISHING),
        (CycleStage.PUBLISHING, CycleStage.MEASURING),
        (CycleStage.MEASURING, CycleStage.INTELLIGENCE),
        (CycleStage.INTELLIGENCE, CycleStage.AWAITING_EXPERIMENT_DECISION),
        (CycleStage.INTELLIGENCE, CycleStage.COMPLETED),
        (CycleStage.AWAITING_EXPERIMENT_DECISION, CycleStage.COMPLETED),
    ],
)
def test_every_forward_transition_is_explicit(source: CycleStage, target: CycleStage) -> None:
    validate_transition(source, target)


def test_illegal_jump_to_publishing_fails_closed() -> None:
    with pytest.raises(IllegalCycleTransition):
        validate_transition(CycleStage.CREATED, CycleStage.PUBLISHING)


def test_terminal_status_is_derived_by_the_state_machine() -> None:
    current = replace(cycle(), current_stage=CycleStage.AWAITING_EXPERIMENT_DECISION)
    completed = current.transition(CycleStage.COMPLETED)
    assert completed.status is CycleStatus.COMPLETED
    assert completed.cycle_version == current.cycle_version + 1


def test_start_readiness_has_readable_blockers() -> None:
    readiness = CycleReadinessEngine().start(CyclePreflight(True, 63, None, None, 0, True))
    assert readiness.state is ReadinessState.BLOCKED
    assert readiness.allowed_actions == (
        SupervisorAction.COMPLETE_BRIEF,
        SupervisorAction.ADD_RESEARCH_SOURCE,
    )
    assert "80%" in readiness.requirements[0].message
    assert "agent_run_not_ready" not in " ".join(item.message for item in readiness.requirements)


def test_snapshot_preparation_is_non_blocking_when_other_requirements_are_ready() -> None:
    readiness = CycleReadinessEngine().start(CyclePreflight(True, 82, None, None, 1, True))
    assert readiness.state is ReadinessState.READY
    assert readiness.requirements[-1].state is ReadinessState.WAITING
    assert readiness.requirements[-1].required is False


def test_supervisor_actions_are_a_deterministic_allowlist() -> None:
    value = cycle()
    readiness = CycleReadinessEngine().start(CyclePreflight(True, 63, None, None, 0, True))
    manifest = SupervisorContextManifest(
        value.tenant_id,
        value.product_id,
        value.id,
        value.cycle_version,
        value.current_stage,
        readiness,
        value.product_snapshot_id,
        value.product_snapshot_digest,
        value.artifacts,
        (),
    )
    report = SupervisorReport(
        value.tenant_id,
        value.product_id,
        value.id,
        manifest.id,
        manifest.semantic_digest,
        "The cycle needs Product input.",
        "The Brief is below the readiness threshold.",
        ("Complete the Product Brief.",),
        ("Complete the Product Brief before continuing.",),
        (SupervisorAction.COMPLETE_BRIEF,),
    )
    validate_supervisor_actions(report, readiness.allowed_actions)
    unsafe = replace(
        report,
        suggested_next_actions=(SupervisorAction.APPROVE_PUBLICATION,),
        semantic_digest="",
    )
    with pytest.raises(ValueError, match="outside deterministic readiness"):
        validate_supervisor_actions(unsafe, readiness.allowed_actions)


def test_supervisor_route_is_sol_medium_and_provider_neutral_to_orchestrator() -> None:
    route = initial_supervisor_route()
    assert route.model == "gpt-5.6-sol"
    assert route.reasoning_effort == "medium"
    assert route.max_output_tokens == 4000
    assert route.profile_key == "supervisor_balanced"


def test_orchestration_contracts_reject_invalid_versions_digests_and_text() -> None:
    with pytest.raises(ValueError, match="bounded"):
        ReadinessRequirement("", ReadinessState.READY, "Ready")
    with pytest.raises(ValueError, match="state machine"):
        replace(cycle(), state_machine_version="creative-cycle-v0")
    with pytest.raises(ValueError, match="snapshot digest"):
        replace(cycle(), product_snapshot_digest="mutable")

    value = cycle()
    readiness = CycleReadinessEngine().start(CyclePreflight(True, 100, uuid4(), "x", 1, True))
    manifest = SupervisorContextManifest(
        value.tenant_id,
        value.product_id,
        value.id,
        value.cycle_version,
        value.current_stage,
        readiness,
        value.product_snapshot_id,
        value.product_snapshot_digest,
        value.artifacts,
        (),
    )
    with pytest.raises(ValueError, match="unsupported Supervisor context"):
        replace(manifest, provider_mode="LIVE")
    with pytest.raises(ValueError, match="digest mismatch"):
        replace(manifest, semantic_digest="sha256:wrong")

    report = SupervisorReport(
        value.tenant_id,
        value.product_id,
        value.id,
        manifest.id,
        manifest.semantic_digest,
        "Ready.",
        "The cycle is ready.",
        (),
        (),
        (),
    )
    with pytest.raises(ValueError, match="unsupported Supervisor report"):
        replace(report, schema_version=99)
    with pytest.raises(ValueError, match="text must be bounded"):
        replace(report, summary="", semantic_digest="")
    with pytest.raises(ValueError, match="digest mismatch"):
        replace(report, semantic_digest="sha256:wrong")
