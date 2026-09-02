"""Loading a workflow from YAML, and driving it start to terminal.

The template is a recipe going through a test kitchen. Nothing in it is a
software-development stage, and the engine neither knows nor cares.
"""

from __future__ import annotations

import pytest
from lore_eden.workflow import (
    StageStatus,
    StateMachine,
    WorkflowTemplateError,
    expand_checklist,
    find_terminal_stage,
    load_template,
    load_templates,
    parse_template,
)

RECIPE_YAML = """
slug: recipe-to-print
name: Recipe to print
description: A dish from idea to published recipe.
stages:
  - key: propose
    name: Propose the dish
    order: 0
  - key: cook
    name: Cook it
    order: 1
    checklist:
      - "Weigh everything before starting"
      - "{{tasting_notes}}"
  - key: taste
    name: Taste panel
    order: 2
  - key: photograph
    name: Photograph the plate
    order: 3
  - key: printed
    name: Printed
    order: 4
    terminal: true
transitions:
  - from: taste
    when: reject
    to: cook
    agent_id: chef
  - from: photograph
    when: reject
    to: photograph
"""


@pytest.fixture
def template_dir(tmp_path):
    (tmp_path / "recipe.yaml").write_text(RECIPE_YAML, encoding="utf-8")
    return tmp_path


def test_a_template_loads_from_yaml(template_dir):
    template = load_template(template_dir / "recipe.yaml")

    assert template.slug == "recipe-to-print"
    assert template.name == "Recipe to print"
    assert [stage.key for stage in template.stages] == [
        "propose", "cook", "taste", "photograph", "printed",
    ]


def test_a_directory_of_templates_loads_in_filename_order(template_dir):
    (template_dir / "another.yaml").write_text("slug: another\nstages: []\n", encoding="utf-8")

    templates = load_templates(template_dir)

    assert [t.slug for t in templates] == ["another", "recipe-to-print"]


def test_a_missing_directory_yields_nothing(tmp_path):
    """"This host ships no templates" is ordinary, not a fault."""
    assert load_templates(tmp_path / "nope") == []


def test_a_template_that_cannot_be_parsed_raises(tmp_path):
    """Unlike a missing directory, a template that exists but is broken is a
    fault worth stopping for."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("slug: x\nstages: [\n", encoding="utf-8")

    with pytest.raises(WorkflowTemplateError):
        load_template(bad)


def test_a_template_without_a_slug_is_refused():
    """A template nothing can address is not a template."""
    with pytest.raises(WorkflowTemplateError, match="slug"):
        parse_template({"stages": []})


def test_a_non_mapping_document_is_refused(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(WorkflowTemplateError, match="mapping"):
        load_template(path)


def test_the_name_falls_back_to_the_slug():
    assert parse_template({"slug": "bare"}).name == "bare"


def test_a_loaded_template_drives_start_to_terminal(template_dir):
    """Walk the whole workflow through the state machine: forward passes, a
    reject that sends it back, the redo, and the terminal stage."""
    template = load_template(template_dir / "recipe.yaml")
    stages, transitions = template.stages, template.transitions

    visited = ["propose"]
    cursor = "propose"

    # Straight through to the taste panel.
    for _ in range(2):
        cursor = StateMachine.resolve_next_stage_key(stages, transitions, cursor).to_key
        visited.append(cursor)
    assert visited == ["propose", "cook", "taste"]

    # The panel rejects it: back to cooking, and everything since is undone.
    plan = StateMachine.resolve_next_stage_key(stages, transitions, cursor, outcome="reject")
    assert plan.to_key == "cook"
    assert plan.upstream is True
    assert plan.transition_agent_id == "chef"

    stage_map = {"propose": StageStatus.DONE, "cook": StageStatus.DONE, "taste": StageStatus.DONE}
    stage_map = StateMachine.reset_upstream_stages(
        stage_map, stages, from_key="taste", to_key="cook"
    )
    assert stage_map["cook"] == StageStatus.PENDING
    assert stage_map["propose"] == StageStatus.DONE

    # Second attempt runs to the end.
    cursor = "cook"
    for _ in range(3):
        cursor = StateMachine.resolve_next_stage_key(stages, transitions, cursor).to_key
    assert cursor == "printed"
    assert find_terminal_stage(stages).key == "printed"
    assert StateMachine.resolve_next_stage_key(stages, transitions, cursor) is None


def test_a_transition_onto_itself_repeats_the_stage(template_dir):
    """Reshoot the photograph rather than moving on."""
    template = load_template(template_dir / "recipe.yaml")

    plan = StateMachine.resolve_next_stage_key(
        template.stages, template.transitions, "photograph", outcome="reject"
    )

    assert plan.to_key == "photograph"
    assert plan.upstream is False


def test_yaml_bare_on_key_is_accepted_as_a_condition(tmp_path):
    """YAML 1.1 parses a bare `on:` as the boolean True, so a transition written
    that way arrives keyed by True rather than "on". Templates in the wild use
    both spellings."""
    path = tmp_path / "on.yaml"
    path.write_text(
        "slug: s\n"
        "stages:\n"
        "  - {key: a, name: A, order: 0}\n"
        "  - {key: b, name: B, order: 1}\n"
        "  - {key: c, name: C, order: 2}\n"
        "transitions:\n"
        "  - from: a\n"
        "    on: pass\n"
        "    to: c\n",
        encoding="utf-8",
    )
    template = load_template(path)

    plan = StateMachine.resolve_next_stage_key(template.stages, template.transitions, "a")

    assert plan.to_key == "c"


def test_checklist_placeholders_expand_through_the_registry():
    """The engine ships no placeholders of its own — a host registers the
    vocabulary it actually has."""
    checklist = ["Weigh everything before starting", "{{tasting_notes}}"]

    expanded = expand_checklist(
        checklist,
        {"{{tasting_notes}}": lambda panel: [f"Record {taster}'s notes" for taster in panel]},
        ["Ada", "Grace"],
    )

    assert expanded == [
        "Weigh everything before starting",
        "Record Ada's notes",
        "Record Grace's notes",
    ]


def test_an_unregistered_placeholder_passes_through_untouched():
    """Dropping it would silently shorten a checklist its author wrote
    deliberately."""
    assert expand_checklist(["{{unknown}}"], {}, None) == ["{{unknown}}"]


def test_an_expander_may_produce_nothing():
    """"Nothing to check here" is a real answer, distinct from declining."""
    assert expand_checklist(["{{none}}"], {"{{none}}": lambda ctx: []}, None) == []


def test_expansion_is_idempotent():
    """Hosts apply this on both the write and the read path, so a raw token
    cannot reach a UI even if one was recorded out of step."""
    expanders = {"{{x}}": lambda ctx: ["expanded"]}

    once = expand_checklist(["{{x}}"], expanders, None)
    twice = expand_checklist(once, expanders, None)

    assert once == twice == ["expanded"]


@pytest.mark.parametrize(
    "token",
    ["{{acceptance_criteria}}", "{{playtest_scenes}}", "{{ticket_intent}}"],
)
def test_the_engine_has_no_built_in_ticket_vocabulary(token):
    """The three placeholders this was extracted with were one product's
    vocabulary, expanded by reading fields off a software-ticket model. Shipping
    them here would mean every other host carrying tokens it can never fill.

    Asserted behaviourally rather than by grepping the source: with no expander
    registered, each token is just text and passes through."""
    assert expand_checklist([token], {}, None) == [token]
