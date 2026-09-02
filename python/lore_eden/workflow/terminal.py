"""What it means for a workflow stage to end the workflow.

Its own module so a caller can ask the question without pulling in stage routing
or the gate runner — in the codebase this came from, that separation existed to
stop an import cycle, and it is worth keeping for the same reason.
"""

from __future__ import annotations

from lore_eden.workflow.models import WorkflowStageDef

#: Fallback terminal key, for templates authored before the flag existed.
TERMINAL_STAGE_KEY = "done"


def is_terminal_stage(stage: WorkflowStageDef) -> bool:
    """Whether reaching this stage ends the workflow.

    The ``terminal`` flag is authoritative; ``key == "done"`` remains a fallback
    so templates authored before the flag — including version-pinned instances
    still in flight — keep terminating. A workflow with no terminal stage does
    not end: it re-runs its last stage forever, which is a real failure that has
    happened, so hosts should require one at authoring time.
    """
    return bool(stage.terminal) or stage.key == TERMINAL_STAGE_KEY


def find_terminal_stage(stages: list[WorkflowStageDef]) -> WorkflowStageDef | None:
    """First stage by order that ends the workflow, or None."""
    for stage in sorted(stages, key=lambda s: s.order):
        if is_terminal_stage(stage):
            return stage
    return None


def parse_stage_defs(raw: list[dict]) -> list[WorkflowStageDef]:
    """Validate a decoded stages payload into stage definitions.

    Snapshots and templates both store stages as JSON; modelling them before
    asking about ``terminal`` keeps the question typed rather than a dict probe.
    """
    return [WorkflowStageDef.model_validate(item) for item in raw]
