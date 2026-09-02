"""Routing a workflow that has nothing to do with writing software.

The stages below are a magazine going to print. That is the point: if any of
this needed a ticket, a branch, or a test suite, the engine would not be an
engine — it would be one product's control flow wearing a general name.
"""

from __future__ import annotations

import json

import pytest
from lore_eden.workflow import (
    StageStatus,
    StateMachine,
    WorkflowStageDef,
    find_terminal_stage,
    is_terminal_stage,
)

STAGES_YAML_ORDER = [
    ("commission", "Commission the piece"),
    ("draft", "Write the draft"),
    ("fact_check", "Check the facts"),
    ("edit", "Edit"),
    ("layout", "Lay out the page"),
    ("published", "Published"),
]

TRANSITIONS = [
    {"from": "fact_check", "when": "reject", "to": "draft"},
    {"from": "edit", "when": "reject", "to": "draft", "agent_id": "writer"},
]


@pytest.fixture
def stages() -> list[WorkflowStageDef]:
    return [
        WorkflowStageDef(key=key, name=name, order=index, terminal=(key == "published"))
        for index, (key, name) in enumerate(STAGES_YAML_ORDER)
    ]


def test_a_pass_advances_to_the_next_stage_in_order(stages):
    plan = StateMachine.resolve_next_stage_key(stages, [], "draft")

    assert plan.to_key == "fact_check"
    assert plan.upstream is False


def test_a_reject_follows_its_declared_transition(stages):
    plan = StateMachine.resolve_next_stage_key(stages, TRANSITIONS, "fact_check", outcome="reject")

    assert plan.to_key == "draft"
    assert plan.upstream is True


def test_a_reject_with_no_transition_routes_nowhere(stages):
    """Linear order is a *forward* default. Falling back to it on a reject would
    advance a stage that just failed."""
    assert StateMachine.resolve_next_stage_key(stages, [], "fact_check", outcome="reject") is None


def test_a_transition_can_name_who_picks_the_work_up(stages):
    plan = StateMachine.resolve_next_stage_key(stages, TRANSITIONS, "edit", outcome="reject")

    assert plan.transition_agent_id == "writer"


def test_reaching_the_last_stage_routes_nowhere(stages):
    assert StateMachine.resolve_next_stage_key(stages, [], "published") is None


def test_the_terminal_stage_is_found_by_flag(stages):
    assert find_terminal_stage(stages).key == "published"
    assert is_terminal_stage(stages[-1]) is True
    assert is_terminal_stage(stages[0]) is False


def test_a_stage_keyed_done_terminates_without_the_flag():
    """Templates authored before the flag existed must keep terminating."""
    assert is_terminal_stage(WorkflowStageDef(key="done", name="Done")) is True


def test_a_workflow_with_no_terminal_stage_is_reported_as_such():
    """It does not end — it re-runs its last stage forever."""
    stages = [WorkflowStageDef(key="a", name="A", order=0)]

    assert find_terminal_stage(stages) is None


def test_an_explicit_target_is_honoured(stages):
    plan = StateMachine.resolve_next_stage_key(stages, [], "layout", explicit_to="draft")

    assert plan.to_key == "draft"
    assert plan.upstream is True


def test_an_explicit_target_that_does_not_exist_is_refused(stages):
    """Honouring it verbatim parks the cursor on a phantom stage."""
    with pytest.raises(ValueError, match="Unknown target stage"):
        StateMachine.resolve_next_stage_key(stages, [], "layout", explicit_to="nowhere")


def test_a_cursor_on_an_unknown_stage_routes_nowhere(stages):
    """It used to return the first stage, silently rewinding the workflow."""
    assert StateMachine.next_stage_key(stages, "not_a_stage") is None


def test_an_empty_cursor_starts_at_the_first_stage(stages):
    assert StateMachine.next_stage_key(stages, "") == "commission"


def test_rerouting_upstream_resets_the_stages_in_between(stages):
    """The work has to be redone, so those stages are pending again."""
    stage_map = {key: StageStatus.DONE for key, _ in STAGES_YAML_ORDER}

    updated = StateMachine.reset_upstream_stages(
        stage_map, stages, from_key="edit", to_key="draft"
    )

    assert updated["draft"] == StageStatus.PENDING
    assert updated["fact_check"] == StageStatus.PENDING
    assert updated["edit"] == StageStatus.PENDING
    assert updated["commission"] == StageStatus.DONE
    assert updated["layout"] == StageStatus.DONE


def test_a_forward_branch_marks_what_it_jumped_over_as_wont_do(stages):
    """Left pending they never resolve, so a work item derived from stage
    statuses hangs after finishing its branch."""
    stage_map = {key: StageStatus.PENDING for key, _ in STAGES_YAML_ORDER}
    stage_map["commission"] = StageStatus.DONE

    updated = StateMachine.skip_intermediate_stages(
        stage_map, stages, from_key="commission", to_key="layout"
    )

    assert updated["draft"] == StageStatus.WONT_DO
    assert updated["fact_check"] == StageStatus.WONT_DO
    assert updated["edit"] == StageStatus.WONT_DO
    assert updated["layout"] == StageStatus.PENDING


def test_skipping_leaves_already_resolved_stages_alone(stages):
    stage_map = {key: StageStatus.PENDING for key, _ in STAGES_YAML_ORDER}
    stage_map["fact_check"] = StageStatus.DONE

    updated = StateMachine.skip_intermediate_stages(
        stage_map, stages, from_key="commission", to_key="layout"
    )

    assert updated["fact_check"] == StageStatus.DONE


def test_adjacent_stages_have_nothing_to_skip(stages):
    stage_map = {key: StageStatus.PENDING for key, _ in STAGES_YAML_ORDER}

    updated = StateMachine.skip_intermediate_stages(
        stage_map, stages, from_key="draft", to_key="fact_check"
    )

    assert updated == stage_map


def test_stages_and_transitions_parse_from_json(stages):
    stages_json = json.dumps([stage.model_dump() for stage in stages])

    parsed = StateMachine.parse_stages(stages_json)
    transitions = StateMachine.parse_transitions(json.dumps(TRANSITIONS))

    assert [stage.key for stage in parsed] == [key for key, _ in STAGES_YAML_ORDER]
    assert transitions[0]["to"] == "draft"


def test_empty_json_parses_to_nothing():
    assert StateMachine.parse_stages("") == []
    assert StateMachine.parse_transitions("") == []


def test_a_lone_reject_transition_does_not_capture_passes(stages):
    """Regression: the fallback that takes a single transition as the
    unconditional one ignored `when`, so a stage whose only declared transition
    was a reject route pushed *successful* work backwards — forever, since the
    stage it returned to would pass and be sent back again.

    Extracted with this defect; found by driving a workflow end to end.
    """
    only_a_reject = [{"from": "fact_check", "when": "reject", "to": "draft"}]

    passed = StateMachine.resolve_next_stage_key(stages, only_a_reject, "fact_check")
    rejected = StateMachine.resolve_next_stage_key(
        stages, only_a_reject, "fact_check", outcome="reject"
    )

    assert passed.to_key == "edit", "a pass must follow linear order, not the reject edge"
    assert rejected.to_key == "draft"


def test_a_lone_unconditional_transition_still_routes_passes(stages):
    """The fallback exists so templates written before `when` keep working, and
    the fix must not have taken that away."""
    legacy = [{"from": "draft", "to": "layout"}]

    assert StateMachine.resolve_next_stage_key(stages, legacy, "draft").to_key == "layout"


def test_an_explicit_pass_transition_still_wins(stages):
    both = [
        {"from": "draft", "when": "pass", "to": "layout"},
        {"from": "draft", "when": "reject", "to": "commission"},
    ]

    assert StateMachine.resolve_next_stage_key(stages, both, "draft").to_key == "layout"
