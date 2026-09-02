"""Driving a work item that is not a software ticket.

A grant application, through a review panel. It has no acceptance criteria, no
branch, no test suite and no stage report — if any of those were needed the
engine would not be an engine, it would be one product's control flow.
"""

from __future__ import annotations

import pytest
from lore_eden.workflow import (
    AUTOMATION,
    AlreadyResolvedError,
    Approval,
    ApprovalNotFoundError,
    ApprovalStatus,
    ApprovalStore,
    GateService,
    StageStatus,
    WorkflowCursor,
    WorkflowStageDef,
    advance,
    start,
)

STAGES = [
    WorkflowStageDef(key="submitted", name="Submitted", order=0),
    WorkflowStageDef(key="eligibility", name="Eligibility check", order=1),
    WorkflowStageDef(key="review", name="Panel review", order=2),
    WorkflowStageDef(key="decision", name="Funding decision", order=3),
    WorkflowStageDef(key="awarded", name="Awarded", order=4, terminal=True),
]

TRANSITIONS = [
    {"from": "review", "when": "reject", "to": "eligibility"},
    {"from": "decision", "when": "reject", "to": "review"},
]

APPLICATION = "grant_application"


def cursor_at(stage_key: str, **statuses: StageStatus) -> WorkflowCursor:
    return WorkflowCursor(item_id="app-1", stage_key=stage_key, stage_statuses=dict(statuses))


class TestMovingThroughAWorkflow:
    def test_an_unstarted_item_begins_at_the_first_stage(self):
        result = start(WorkflowCursor(item_id="app-1"), STAGES)

        assert result.cursor.stage_key == "submitted"
        assert not result.finished

    def test_a_pass_advances_and_marks_the_stage_done(self):
        result = advance(cursor_at("submitted"), STAGES, TRANSITIONS)

        assert result.cursor.stage_key == "eligibility"
        assert result.cursor.status_of("submitted") is StageStatus.DONE

    def test_reaching_the_terminal_stage_finishes_the_workflow(self):
        result = advance(cursor_at("decision"), STAGES, TRANSITIONS)

        assert result.cursor.stage_key == "awarded"
        assert result.finished is True

    def test_a_workflow_with_no_stages_cannot_be_started(self):
        # Pretending otherwise parks the item on a stage that does not exist.
        result = start(WorkflowCursor(item_id="app-1"), [])

        assert result.blocked is True
        assert result.cursor.stage_key == ""

    def test_the_cursor_handed_in_is_not_mutated(self):
        # The host is holding the record its UI renders from.
        before = cursor_at("submitted")

        advance(before, STAGES, TRANSITIONS)

        assert before.stage_key == "submitted"
        assert before.status_of("submitted") is StageStatus.PENDING


class TestRejection:
    def test_a_reject_follows_its_declared_transition(self):
        result = advance(cursor_at("review"), STAGES, TRANSITIONS, outcome="reject")

        assert result.cursor.stage_key == "eligibility"

    def test_a_reject_does_not_mark_the_stage_done(self):
        # Marking it done would leave a workflow that looks complete while its
        # work was rejected.
        result = advance(cursor_at("review"), STAGES, TRANSITIONS, outcome="reject")

        assert result.cursor.status_of("review") is not StageStatus.DONE

    def test_a_reject_resets_the_stages_it_sends_the_item_back_over(self):
        started = cursor_at(
            "review",
            submitted=StageStatus.DONE,
            eligibility=StageStatus.DONE,
            review=StageStatus.DONE,
        )

        result = advance(started, STAGES, TRANSITIONS, outcome="reject")

        assert result.cursor.status_of("eligibility") is StageStatus.PENDING
        assert result.cursor.status_of("submitted") is StageStatus.DONE

    def test_a_reject_with_no_transition_falls_back_to_the_previous_stage(self):
        result = advance(cursor_at("eligibility"), STAGES, [], outcome="reject")

        assert result.cursor.stage_key == "submitted"

    def test_rejecting_the_first_stage_blocks_in_place(self):
        # There is nowhere to send it back to, and routing it forward would
        # advance work that was just refused.
        result = advance(
            cursor_at("submitted"), STAGES, [], outcome="reject", blocking_issues="Not eligible"
        )

        assert result.blocked is True
        assert result.cursor.stage_key == "submitted"
        assert result.cursor.status_of("submitted") is StageStatus.BLOCKED
        assert result.cursor.blocking_issues == "Not eligible"

    def test_a_block_always_says_why(self):
        result = advance(cursor_at("submitted"), STAGES, [], outcome="reject")

        assert result.cursor.blocking_issues


class TestForwardBranches:
    def test_a_forward_jump_marks_what_it_skipped(self):
        # Left pending they never resolve, and an item derived from stage
        # statuses hangs after finishing its branch.
        result = advance(
            cursor_at("submitted"), STAGES, [], next_stage_key="decision"
        )

        assert result.cursor.stage_key == "decision"
        assert result.cursor.status_of("eligibility") is StageStatus.WONT_DO
        assert result.cursor.status_of("review") is StageStatus.WONT_DO

    def test_a_jump_to_a_stage_that_does_not_exist_is_refused(self):
        with pytest.raises(ValueError, match="Unknown target stage"):
            advance(cursor_at("submitted"), STAGES, [], next_stage_key="nowhere")


class Cursors:
    """Stands in for a host's storage."""

    def __init__(self, initial: WorkflowCursor) -> None:
        self.saved = initial

    def read(self, subject_type: str, subject_id: str) -> WorkflowCursor:
        return self.saved

    def write(self, cursor: WorkflowCursor) -> None:
        self.saved = cursor


class TestGates:
    def gate_service(self, cursor: WorkflowCursor, resumed: list | None = None):
        cursors = Cursors(cursor)
        store = ApprovalStore()
        service = GateService(
            store,
            read_cursor=cursors.read,
            write_cursor=cursors.write,
            on_resume=(lambda c: resumed.append(c)) if resumed is not None else None,
        )
        return service, store, cursors

    def test_opening_a_gate_parks_the_stage_awaiting_an_answer(self):
        service, _, cursors = self.gate_service(cursor_at("decision"))

        approval = service.open_gate(
            subject_type=APPLICATION,
            subject_id="app-1",
            stage_key="decision",
            title="Approve funding",
            checklist=["Budget checked", "Panel signed off"],
        )

        assert approval.pending
        assert cursors.saved.status_of("decision") is StageStatus.AWAITING

    def test_approving_advances_the_item(self):
        service, _, cursors = self.gate_service(cursor_at("decision"))
        approval = service.open_gate(
            subject_type=APPLICATION, subject_id="app-1", stage_key="decision", title="Fund it"
        )

        result = service.resolve(
            approval.id, approved=True, resolved_by="panel-chair", stages=STAGES,
            transitions=TRANSITIONS,
        )

        assert result.approval.status is ApprovalStatus.APPROVED
        assert result.approval.resolved_by == "panel-chair"
        assert cursors.saved.stage_key == "awarded"

    def test_rejecting_sends_it_back_with_the_reason(self):
        service, _, cursors = self.gate_service(cursor_at("decision"))
        approval = service.open_gate(
            subject_type=APPLICATION, subject_id="app-1", stage_key="decision", title="Fund it"
        )

        result = service.resolve(
            approval.id,
            approved=False,
            resolved_by="panel-chair",
            response="Budget does not add up",
            stages=STAGES,
            transitions=TRANSITIONS,
        )

        assert result.approval.status is ApprovalStatus.REJECTED
        assert cursors.saved.stage_key == "review"
        assert cursors.saved.blocking_issues == "Budget does not add up"

    def test_approved_with_rework_is_an_approval_that_still_goes_back(self):
        # The middle outcome. A reviewer saying "yes, but fix the budget line"
        # is not rejecting, and forcing it into one loses the distinction
        # between "this failed" and "this passed, with follow-up".
        service, _, cursors = self.gate_service(cursor_at("decision"))
        approval = service.open_gate(
            subject_type=APPLICATION, subject_id="app-1", stage_key="decision", title="Fund it"
        )

        result = service.resolve(
            approval.id,
            approved=True,
            resolved_by="panel-chair",
            response="Fix the budget line first",
            rework_stage_key="review",
            stages=STAGES,
            transitions=TRANSITIONS,
        )

        assert result.approval.status is ApprovalStatus.APPROVED
        assert cursors.saved.stage_key == "review"
        assert "Fix the budget line first" in cursors.saved.blocking_issues

    def test_an_approval_resumes_the_run(self):
        # Asking the operator to press Run after approving is a second decision
        # carrying no information.
        resumed: list = []
        service, _, _ = self.gate_service(cursor_at("review"), resumed)
        approval = service.open_gate(
            subject_type=APPLICATION, subject_id="app-1", stage_key="review", title="Review"
        )

        result = service.resolve(
            approval.id, approved=True, resolved_by="chair", stages=STAGES,
            transitions=TRANSITIONS,
        )

        assert result.resumed is True
        assert resumed[0].stage_key == "decision"

    def test_a_rejection_does_not_resume(self):
        resumed: list = []
        service, _, _ = self.gate_service(cursor_at("decision"), resumed)
        approval = service.open_gate(
            subject_type=APPLICATION, subject_id="app-1", stage_key="decision", title="Fund it"
        )

        service.resolve(
            approval.id, approved=False, resolved_by="chair", stages=STAGES,
            transitions=TRANSITIONS,
        )

        assert resumed == []

    def test_finishing_the_workflow_does_not_resume(self):
        resumed: list = []
        service, _, _ = self.gate_service(cursor_at("decision"), resumed)
        approval = service.open_gate(
            subject_type=APPLICATION, subject_id="app-1", stage_key="decision", title="Fund it"
        )

        service.resolve(
            approval.id, approved=True, resolved_by="chair", stages=STAGES,
            transitions=TRANSITIONS,
        )

        # It reached the terminal stage; there is nothing left to dispatch.
        assert resumed == []

    def test_an_answer_to_a_stage_the_item_has_left_is_refused(self):
        # The item moved on while the question sat unanswered. Applying the
        # answer to wherever it is now would resolve a stage nobody asked about.
        service, _, cursors = self.gate_service(cursor_at("review"))
        approval = service.open_gate(
            subject_type=APPLICATION, subject_id="app-1", stage_key="review", title="Review"
        )
        cursors.write(cursor_at("decision"))

        result = service.resolve(
            approval.id, approved=True, resolved_by="chair", stages=STAGES,
            transitions=TRANSITIONS,
        )

        assert result.dispatch.blocked is True
        assert "answered while the item was at" in cursors.saved.blocking_issues


class TestAutoApproval:
    def test_it_still_leaves_a_record(self):
        # A gate that auto-approved silently would be indistinguishable from one
        # that never ran, and "was this signed off, and by whom?" would have no
        # answer for exactly the runs where it matters most.
        cursors = Cursors(cursor_at("review"))
        store = ApprovalStore()
        service = GateService(store, read_cursor=cursors.read, write_cursor=cursors.write)
        approval = service.open_gate(
            subject_type=APPLICATION, subject_id="app-1", stage_key="review", title="Review"
        )

        result = service.auto_resolve(approval.id, stages=STAGES, transitions=TRANSITIONS)

        assert result.approval.status is ApprovalStatus.APPROVED
        assert result.approval.resolved_by == AUTOMATION
        assert len(store) == 1

    def test_automation_is_distinguishable_from_a_person(self):
        assert AUTOMATION != ""


class TestTheStore:
    def test_one_store_holds_approvals_for_different_kinds_of_subject(self):
        # The whole reason the foreign keys went: a host with several kinds of
        # subject should not need several tables.
        store = ApprovalStore()
        store.add(Approval(subject_type="grant_application", subject_id="app-1", title="Fund it"))
        store.add(Approval(subject_type="purchase_order", subject_id="po-9", title="Buy it"))
        store.add(Approval(subject_type="grant_application", subject_id="app-2", title="Fund it"))

        assert len(store.pending()) == 3
        assert len(store.pending(subject_type="grant_application")) == 2
        assert len(store.pending(subject_type="purchase_order")) == 1

    def test_approvals_can_be_found_for_one_subject(self):
        store = ApprovalStore()
        store.add(Approval(subject_type="grant_application", subject_id="app-1", title="A"))
        store.add(Approval(subject_type="grant_application", subject_id="app-2", title="B"))

        found = store.for_subject("grant_application", "app-1")

        assert [a.title for a in found] == ["A"]

    def test_a_resolved_approval_leaves_the_pending_list(self):
        store = ApprovalStore()
        approval = store.add(Approval(subject_type="x", subject_id="1", title="A"))

        approval.resolve(approved=True, resolved_by="someone")
        store.save(approval)

        assert store.pending() == []

    def test_answering_twice_is_refused(self):
        # Re-resolving overwrites who signed off and when, which is the part of
        # the record that has to be trustworthy.
        approval = Approval(subject_type="x", subject_id="1", title="A")
        approval.resolve(approved=True, resolved_by="first")

        with pytest.raises(AlreadyResolvedError):
            approval.resolve(approved=False, resolved_by="second")

    def test_an_unknown_approval_is_an_error_not_a_silent_miss(self):
        with pytest.raises(ApprovalNotFoundError):
            ApprovalStore().get("no-such-approval")


class TestEndToEnd:
    def test_a_grant_runs_from_submission_to_award_through_a_rejection(self):
        cursors = Cursors(WorkflowCursor(item_id="app-1"))
        store = ApprovalStore()
        dispatched: list = []
        service = GateService(
            store,
            read_cursor=cursors.read,
            write_cursor=cursors.write,
            on_resume=dispatched.append,
        )

        # Start, and walk forward to the panel.
        cursors.write(start(cursors.saved, STAGES).cursor)
        assert cursors.saved.stage_key == "submitted"
        cursors.write(advance(cursors.saved, STAGES, TRANSITIONS).cursor)
        cursors.write(advance(cursors.saved, STAGES, TRANSITIONS).cursor)
        assert cursors.saved.stage_key == "review"

        # The panel refuses it and it goes back for another eligibility check.
        gate = service.open_gate(
            subject_type=APPLICATION, subject_id="app-1", stage_key="review", title="Panel"
        )
        service.resolve(
            gate.id, approved=False, resolved_by="panel", response="Missing accounts",
            stages=STAGES, transitions=TRANSITIONS,
        )
        assert cursors.saved.stage_key == "eligibility"
        assert cursors.saved.blocking_issues == "Missing accounts"

        # Second time through, the panel approves.
        cursors.write(advance(cursors.saved, STAGES, TRANSITIONS).cursor)
        assert cursors.saved.stage_key == "review"
        second = service.open_gate(
            subject_type=APPLICATION, subject_id="app-1", stage_key="review", title="Panel"
        )
        service.resolve(
            second.id, approved=True, resolved_by="panel", stages=STAGES,
            transitions=TRANSITIONS,
        )
        assert cursors.saved.stage_key == "decision"
        assert dispatched[-1].stage_key == "decision"

        # And the funding decision finishes it.
        final = advance(cursors.saved, STAGES, TRANSITIONS)
        assert final.cursor.stage_key == "awarded"
        assert final.finished is True

        # Both panel sittings are on the record, with who decided each.
        history = store.for_subject(APPLICATION, "app-1")
        assert [a.status for a in history] == [
            ApprovalStatus.REJECTED,
            ApprovalStatus.APPROVED,
        ]
        assert {a.resolved_by for a in history} == {"panel"}
