"""Loading workflow templates, and expanding the checklists they carry.

A template is data: a slug, a list of stages, a list of transitions. This module
reads that from YAML and hands back typed stage definitions. It does not store
them — where templates live, and how they are versioned, is the host's.

## The checklist extension point

A stage may carry a ``checklist`` of items for whoever evaluates it. The version
this was extracted from expanded three placeholders inline —
``{{acceptance_criteria}}``, ``{{ticket_intent}}``, ``{{playtest_scenes}}`` —
reading fields off a software-ticket model to do it. Those are that product's
vocabulary, not a property of workflows, and hardcoding them here would mean
every other host carrying three placeholders it can never fill.

So expansion is a registry: a host maps its own placeholder tokens to functions
that produce items. :func:`expand_checklist` walks the checklist, replaces any
item that is exactly a registered token, and passes everything else through.

    def acceptance_criteria(context):
        return [f"Check by hand — {c}" for c in context.criteria]

    expand_checklist(stage.checklist, {"{{acceptance_criteria}}": acceptance_criteria}, ticket)

Idempotent, because an already-expanded checklist contains no token. Hosts apply
it on both the write and the read path, so a raw token cannot reach a UI even if
one was recorded while the template and the code were out of step.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from lore_eden.workflow.models import WorkflowStageDef

#: Produces the items one placeholder expands to, given the host's context.
#: Returning an empty list drops the placeholder, which is a real answer —
#: "nothing to check here" — and distinct from declining to expand.
ChecklistExpander = Callable[[Any], Sequence[str]]


class WorkflowTemplate(BaseModel):
    """A workflow as authored: stages, and the transitions between them."""

    slug: str
    name: str = ""
    description: str = ""
    stages: list[WorkflowStageDef] = Field(default_factory=list)
    #: Each entry is a mapping with ``from``/``to`` and an optional ``when``
    #: (``pass`` / ``reject``) and ``agent_id``. Left untyped because YAML 1.1
    #: turns a bare ``on:`` key into the boolean ``True``, and the state machine
    #: accepts both spellings rather than rejecting templates already in use.
    transitions: list[dict] = Field(default_factory=list)

    def stages_json(self) -> str:
        return json.dumps([stage.model_dump() for stage in self.stages])

    def transitions_json(self) -> str:
        return json.dumps(self.transitions)


class WorkflowTemplateError(ValueError):
    """A template that cannot be loaded, or that would not terminate."""


def load_workflow_yaml(path: Path) -> dict:
    """Decode one template file. Raises rather than returning a partial dict."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise WorkflowTemplateError(f"{path}: cannot read workflow template: {exc}") from exc
    if not isinstance(data, dict):  # py-org: allow-isinstance (untyped YAML document)
        raise WorkflowTemplateError(f"{path}: expected a mapping at the top level")
    return data


def parse_template(data: Mapping[str, Any], *, source: str = "") -> WorkflowTemplate:
    """Validate a decoded template payload.

    A missing ``slug`` is refused here rather than at first use: a template
    nothing can address is not a template, and the error is far cheaper now than
    mid-run.
    """
    if not data.get("slug"):
        raise WorkflowTemplateError(f"{source or 'template'}: `slug` is required")
    try:
        return WorkflowTemplate.model_validate(
            {
                "slug": data["slug"],
                "name": data.get("name") or data["slug"],
                "description": data.get("description", ""),
                "stages": data.get("stages", []),
                "transitions": data.get("transitions", []),
            }
        )
    except ValueError as exc:
        raise WorkflowTemplateError(f"{source or 'template'}: {exc}") from exc


def load_template(path: Path) -> WorkflowTemplate:
    return parse_template(load_workflow_yaml(path), source=str(path))


def load_templates(directory: Path) -> list[WorkflowTemplate]:
    """Every ``*.yaml`` template in ``directory``, in filename order.

    A missing directory yields nothing; an unreadable file in it raises. Those
    are different situations — "this host ships no templates" is ordinary, and a
    template that exists but cannot be parsed is a fault worth stopping for.
    """
    if not directory.is_dir():
        return []
    return [load_template(path) for path in sorted(directory.glob("*.yaml"))]


def expand_checklist(
    checklist: Sequence[str],
    expanders: Mapping[str, ChecklistExpander],
    context: Any = None,
) -> list[str]:
    """Replace registered placeholder items with the items they stand for.

    An item is a placeholder only if it is *exactly* a registered token once
    stripped. Everything else passes through unchanged, including a token this
    host has not registered — dropping those would silently shorten a checklist
    a template author wrote deliberately.
    """
    expanded: list[str] = []
    for item in checklist:
        expander = expanders.get(item.strip())
        if expander is None:
            expanded.append(item)
            continue
        expanded.extend(expander(context))
    return expanded
