"""Driving a workflow: where the cursor goes, and what gates each move.

Four independent pieces, none of which knows what a work item is:

- :mod:`~lore_eden.workflow.models` — the vocabulary (stages, statuses, outcomes)
- :mod:`~lore_eden.workflow.state_machine` — routing, as pure functions
- :mod:`~lore_eden.workflow.templates` — loading templates, expanding checklists
- :mod:`~lore_eden.workflow.gates` — running the commands that gate a transition
"""

from lore_eden.workflow.approvals import (
    AUTOMATION,
    AlreadyResolvedError,
    Approval,
    ApprovalKind,
    ApprovalNotFoundError,
    ApprovalStatus,
    ApprovalStore,
)
from lore_eden.workflow.dispatch import (
    DispatchResult,
    WorkflowCursor,
    advance,
    block,
    start,
    terminal_stage_key,
)
from lore_eden.workflow.events import EventBus
from lore_eden.workflow.gate_service import GateResolution, GateService
from lore_eden.workflow.gates import (
    GateAutofixResult,
    GateCycleResult,
    GateRunResult,
    GatesConfig,
    gates_can_run,
    run_autofix,
    run_gates,
    run_gates_with_autofix,
    transition_name,
)
from lore_eden.workflow.models import (
    ClassifyRoute,
    GateOutcome,
    ParallelAgentSpec,
    StageOutcome,
    StageStatus,
    WorkflowStageDef,
)
from lore_eden.workflow.state_machine import StageRoutePlan, StateMachine
from lore_eden.workflow.templates import (
    ChecklistExpander,
    WorkflowTemplate,
    WorkflowTemplateError,
    expand_checklist,
    load_template,
    load_templates,
    parse_template,
)
from lore_eden.workflow.terminal import (
    TERMINAL_STAGE_KEY,
    find_terminal_stage,
    is_terminal_stage,
    parse_stage_defs,
)

__all__ = [
    "AUTOMATION",
    "TERMINAL_STAGE_KEY",
    "AlreadyResolvedError",
    "Approval",
    "ApprovalKind",
    "ApprovalNotFoundError",
    "ApprovalStatus",
    "ApprovalStore",
    "DispatchResult",
    "GateResolution",
    "GateService",
    "WorkflowCursor",
    "advance",
    "block",
    "start",
    "terminal_stage_key",
    "ChecklistExpander",
    "ClassifyRoute",
    "EventBus",
    "GateAutofixResult",
    "GateCycleResult",
    "GateOutcome",
    "GateRunResult",
    "GatesConfig",
    "ParallelAgentSpec",
    "StageOutcome",
    "StageRoutePlan",
    "StageStatus",
    "StateMachine",
    "WorkflowStageDef",
    "WorkflowTemplate",
    "WorkflowTemplateError",
    "expand_checklist",
    "find_terminal_stage",
    "gates_can_run",
    "is_terminal_stage",
    "load_template",
    "load_templates",
    "parse_stage_defs",
    "parse_template",
    "run_autofix",
    "run_gates",
    "run_gates_with_autofix",
    "transition_name",
]
