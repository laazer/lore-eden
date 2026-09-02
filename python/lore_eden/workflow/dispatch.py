"""Moving a work item through its workflow.

The thing being worked on is a **work item**, and this module knows almost
nothing about it: an id, where its cursor sits, which stages have resolved, and
a place to record why it is stuck. Whether that is a support case, a manuscript
or a software ticket is the host's business.

The version this came from was typed against one product's ticket model in every
method, so the engine and that product's schema could not be separated — which
is why the same dispatch logic could not be reused for anything else even though
none of it was ticket-specific.

State is a value, not a row. :class:`WorkflowCursor` goes in and a new one comes
out; the host maps it to and from wherever it stores things. That is what lets
the whole of this be tested without a database, and it is why a reject that
should have hard-blocked can be asserted rather than inspected.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from lore_eden.workflow.models import StageOutcome, StageStatus, WorkflowStageDef
from lore_eden.workflow.state_machine import StageRoutePlan, StateMachine
from lore_eden.workflow.terminal import find_terminal_stage, is_terminal_stage


@dataclass(frozen=True)
class WorkflowCursor:
    """Where one work item stands.

    Frozen: every move returns a new cursor. The host holds the record the UI is
    rendering from, and mutating it underneath means a screen that disagrees with
    what was stored.
    """

    item_id: str
    #: The stage the item is at. Empty means it has not started.
    stage_key: str = ""
    stage_statuses: dict[str, StageStatus] = field(default_factory=dict)
    #: Why the item cannot proceed, in terms someone can act on. Empty when it can.
    blocking_issues: str = ""

    def status_of(self, stage_key: str) -> StageStatus:
        return self.stage_statuses.get(stage_key, StageStatus.PENDING)

    def with_status(self, stage_key: str, status: StageStatus) -> WorkflowCursor:
        return replace(
            self, stage_statuses={**self.stage_statuses, stage_key: status}
        )


@dataclass(frozen=True)
class DispatchResult:
    """Where the cursor ended up, and what moved it."""

    cursor: WorkflowCursor
    plan: StageRoutePlan | None = None
    #: True when the item reached a stage that ends the workflow.
    finished: bool = False
    #: True when nothing could move it and it is stuck where it is.
    blocked: bool = False


def start(cursor: WorkflowCursor, stages: list[WorkflowStageDef]) -> DispatchResult:
    """Put an unstarted item on its first stage."""
    first = StateMachine.next_stage_key(stages, "")
    if first is None:
        # A workflow with no stages cannot be started, and pretending otherwise
        # produces an item parked on a stage that does not exist.
        return DispatchResult(
            cursor=replace(cursor, blocking_issues="This workflow has no stages"),
            blocked=True,
        )
    moved = replace(cursor, stage_key=first, blocking_issues="")
    return DispatchResult(cursor=moved, finished=_at_terminal(moved, stages))


def advance(
    cursor: WorkflowCursor,
    stages: list[WorkflowStageDef],
    transitions: list[dict[str, str]],
    *,
    outcome: str = StageOutcome.PASS,
    next_stage_key: str = "",
    blocking_issues: str = "",
) -> DispatchResult:
    """Resolve the current stage and move on.

    A pass marks the stage done. A reject does not: the stage has to be redone,
    and marking it done first would leave a workflow that looks complete while
    its work was rejected.

    A reject with nowhere to go falls back to the preceding stage, and if there
    is none — the first stage rejecting — the item hard-blocks in place. Routing
    it forward instead would advance work that was just refused.
    """
    from_key = cursor.stage_key
    if not from_key:
        return start(cursor, stages)

    rejected = outcome == StageOutcome.REJECT
    settled = cursor if rejected else cursor.with_status(from_key, StageStatus.DONE)

    plan = StateMachine.resolve_next_stage_key(
        stages, transitions, from_key, outcome=outcome, explicit_to=next_stage_key
    )
    if plan is None and rejected:
        fallback = _previous_stage_key(stages, from_key)
        if fallback:
            plan = StageRoutePlan(
                from_key=from_key, to_key=fallback, outcome=outcome, upstream=True
            )

    if plan is None:
        if rejected:
            # Nowhere to send it back to. Stuck where it is, and said so.
            stuck = replace(
                settled.with_status(from_key, StageStatus.BLOCKED),
                blocking_issues=blocking_issues or "Rejected with no earlier stage to return to",
            )
            return DispatchResult(cursor=stuck, blocked=True)
        # A pass off the last stage is the end of the workflow, not a fault.
        return DispatchResult(cursor=settled, finished=True)

    return DispatchResult(
        cursor=_apply(settled, stages, plan, blocking_issues=blocking_issues),
        plan=plan,
        finished=_reaches_terminal(stages, plan.to_key),
    )


def _apply(
    cursor: WorkflowCursor,
    stages: list[WorkflowStageDef],
    plan: StageRoutePlan,
    *,
    blocking_issues: str,
) -> WorkflowCursor:
    statuses = dict(cursor.stage_statuses)

    if plan.upstream:
        # The work between here and there has to be redone.
        statuses = StateMachine.reset_upstream_stages(
            statuses, stages, from_key=plan.from_key, to_key=plan.to_key
        )
    else:
        # Stages a forward branch jumped over never resolve if left pending, and
        # an item derived from stage statuses then hangs after finishing its
        # branch.
        statuses = StateMachine.skip_intermediate_stages(
            statuses, stages, from_key=plan.from_key, to_key=plan.to_key
        )

    return replace(
        cursor,
        stage_key=plan.to_key,
        stage_statuses=statuses,
        blocking_issues=blocking_issues,
    )


def block(cursor: WorkflowCursor, reason: str) -> DispatchResult:
    """Stop the item where it is, with a reason.

    A blocked stage rather than a blocked *item*: which stage could not proceed
    is the first thing anyone asks, and an item-level flag cannot answer it.
    """
    return DispatchResult(
        cursor=replace(
            cursor.with_status(cursor.stage_key, StageStatus.BLOCKED),
            blocking_issues=reason,
        ),
        blocked=True,
    )


def _previous_stage_key(stages: list[WorkflowStageDef], stage_key: str) -> str:
    ordered = [stage.key for stage in sorted(stages, key=lambda s: s.order)]
    try:
        index = ordered.index(stage_key)
    except ValueError:
        return ""
    return ordered[index - 1] if index > 0 else ""


def _reaches_terminal(stages: list[WorkflowStageDef], stage_key: str) -> bool:
    for stage in stages:
        if stage.key == stage_key:
            return is_terminal_stage(stage)
    return False


def _at_terminal(cursor: WorkflowCursor, stages: list[WorkflowStageDef]) -> bool:
    return _reaches_terminal(stages, cursor.stage_key)


def terminal_stage_key(stages: list[WorkflowStageDef]) -> str:
    stage = find_terminal_stage(stages)
    return stage.key if stage else ""
