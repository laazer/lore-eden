"""Resolving a gate, and moving the work item accordingly.

Three outcomes, and the middle one is the reason this is not a boolean:

- **approved** — the stage passes and the item moves on.
- **approved with rework** — the gate is satisfied *and* something has to be
  redone first. A reviewer who says "yes, but formalize the prototype" is not
  rejecting, and forcing that into a rejection loses the distinction between
  "this failed" and "this passed, with follow-up".
- **rejected** — the stage did not pass. The item goes back, or blocks in place
  if there is nowhere to send it.

Resuming after an approval is the host's, through a hook. Approving a gate
leaves the item pointing at a stage that is ready to run, and asking the operator
to then press Run is a second decision carrying no information — but *what*
running means is not something this package can know.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from lore_eden.workflow.approvals import (
    AUTOMATION,
    Approval,
    ApprovalStore,
)
from lore_eden.workflow.dispatch import DispatchResult, WorkflowCursor, advance, block
from lore_eden.workflow.models import StageOutcome, StageStatus, WorkflowStageDef

#: Called after an approval leaves the item ready to run. The host dispatches.
ResumeHook = Callable[[WorkflowCursor], None]

#: How the host loads and stores a cursor it owns.
CursorReader = Callable[[str, str], WorkflowCursor]
CursorWriter = Callable[[WorkflowCursor], None]


@dataclass(frozen=True)
class GateResolution:
    """What resolving one approval did."""

    approval: Approval
    dispatch: DispatchResult
    #: True when the resume hook was called.
    resumed: bool = False


DEFAULT_REWORK_NOTE = "Approved with rework requested"
DEFAULT_REJECT_NOTE = "Rejected"


class GateService:
    """Answers approvals and moves the work item that was waiting on them."""

    def __init__(
        self,
        store: ApprovalStore,
        *,
        read_cursor: CursorReader,
        write_cursor: CursorWriter,
        on_resume: ResumeHook | None = None,
    ) -> None:
        self.store = store
        self._read_cursor = read_cursor
        self._write_cursor = write_cursor
        self._on_resume = on_resume

    def open_gate(
        self,
        *,
        subject_type: str,  # py-org: allow-string (the host's vocabulary, not this package's)
        subject_id: str,
        stage_key: str,
        title: str,
        checklist: list[str] | None = None,
        impact: str = "",
        level: str = "medium",
    ) -> Approval:
        """Raise a gate and park the stage awaiting an answer."""
        approval = self.store.add(
            Approval(
                subject_type=subject_type,
                subject_id=subject_id,
                stage_key=stage_key,
                title=title,
                checklist=checklist or [],
                impact=impact,
                level=level,
            )
        )
        cursor = self._read_cursor(subject_type, subject_id)
        self._write_cursor(cursor.with_status(stage_key, StageStatus.AWAITING))
        return approval

    def resolve(
        self,
        approval_id: str,
        *,
        approved: bool,
        resolved_by: str,
        response: str = "",
        rework_stage_key: str = "",
        stages: list[WorkflowStageDef],
        transitions: list[dict[str, str]] | None = None,
        resume: bool = True,
    ) -> GateResolution:
        """Answer a gate and move the item.

        ``rework_stage_key`` with ``approved=True`` is the middle outcome: the
        gate is satisfied, and the item is routed back to that stage to do the
        follow-up. It is still an approval in the record, because that is what
        happened.
        """
        approval = self.store.get(approval_id)
        approval.resolve(approved=approved, resolved_by=resolved_by, response=response)
        self.store.save(approval)

        cursor = self._read_cursor(approval.subject_type, approval.subject_id)
        dispatch = self._apply(
            cursor,
            approval,
            approved=approved,
            response=response,
            rework_stage_key=rework_stage_key,
            stages=stages,
            transitions=transitions or [],
        )
        self._write_cursor(dispatch.cursor)

        resumed = False
        if approved and resume and not dispatch.blocked and not dispatch.finished:
            if self._on_resume is not None:
                self._on_resume(dispatch.cursor)
                resumed = True

        return GateResolution(approval=approval, dispatch=dispatch, resumed=resumed)

    def auto_resolve(
        self,
        approval_id: str,
        *,
        stages: list[WorkflowStageDef],
        transitions: list[dict[str, str]] | None = None,
        response: str = "",
        resume: bool = True,
    ) -> GateResolution:
        """Approve without a human, leaving a record that says so.

        The record is the point. A gate that auto-approved silently would be
        indistinguishable from one that never ran, and "was this signed off, and
        by whom?" would have no answer for exactly the runs where it matters.
        """
        return self.resolve(
            approval_id,
            approved=True,
            resolved_by=AUTOMATION,
            response=response,
            stages=stages,
            transitions=transitions,
            resume=resume,
        )

    def _apply(
        self,
        cursor: WorkflowCursor,
        approval: Approval,
        *,
        approved: bool,
        response: str,
        rework_stage_key: str,
        stages: list[WorkflowStageDef],
        transitions: list[dict[str, str]],
    ) -> DispatchResult:
        if not approval.stage_key:
            # Not a workflow gate — a permission or a question. Nothing moves.
            return DispatchResult(cursor=cursor)

        if cursor.stage_key != approval.stage_key:
            # The item moved on while the question sat unanswered. Applying the
            # answer to wherever it is *now* would resolve a stage nobody asked
            # about, so it is refused rather than guessed at.
            return block(
                cursor,
                f"Gate for '{approval.stage_key}' answered while the item was at "
                f"'{cursor.stage_key}'",
            )

        note = response.strip()

        if approved and rework_stage_key:
            return advance(
                cursor,
                stages,
                transitions,
                outcome=StageOutcome.REJECT,
                next_stage_key=rework_stage_key,
                blocking_issues=(
                    f"'{approval.stage_key}' gate approved with rework: "
                    f"{note or DEFAULT_REWORK_NOTE}"
                ),
            )

        if approved:
            return advance(cursor, stages, transitions, outcome=StageOutcome.PASS)

        return advance(
            cursor,
            stages,
            transitions,
            outcome=StageOutcome.REJECT,
            next_stage_key=rework_stage_key,
            blocking_issues=note or DEFAULT_REJECT_NOTE,
        )
