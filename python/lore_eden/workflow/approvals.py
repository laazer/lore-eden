"""Asking a human, and acting on the answer.

An approval is a question a run stopped to ask: a gate before a stage may pass,
a tool wanting permission, a plain question. It is stored so the asking survives
a restart, and so an auto-approval leaves a record rather than being inferable
only from its absence.

## Subjects, not tickets

The record this came from had four foreign keys into one product's schema —
ticket, workspace, run, orchestration run — which is what stopped it being used
for anything else. Here an approval names its subject with a **type and an id**,
so one store holds approvals for whatever a host has, and a host with several
kinds of subject does not need several tables.

The keys are gone rather than made nullable. A nullable FK still constrains what
can be stored to rows that exist in *that* table, which is the coupling; and a
column nothing references reads to the next person as an oversight.

``subject_type`` is a plain string, and the organization gate objects — a
vocabulary parameter usually wants an enum. It is waived rather than typed
because this package cannot know what kinds of subject a host has, and an enum
here would recreate exactly the coupling the foreign keys were removed to break.
A host with a closed set of its own should narrow it at its own boundary.

## Auto-approval still leaves a row

Deliberately, and it is the property most worth keeping. An auto-approved gate
that wrote nothing would be indistinguishable from a gate that never ran, so
"was this signed off, and by whom?" would have no answer for exactly the runs
where it matters most. Every resolution writes a row; ``resolved_by``
distinguishes a human from automation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from lore_eden.timestamps import utcnow


class ApprovalKind(str, Enum):
    """What is being asked."""

    #: A workflow stage may not pass until someone says so.
    WORKFLOW_GATE = "workflow_gate"
    #: A tool wants to run. See :mod:`lore_eden.agents.policy`.
    TOOL_PERMISSION = "tool_permission"
    #: A plain question from a run, with no gate behind it.
    QUESTION = "question"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


#: Marks a resolution made by the system rather than a person. A distinct value
#: rather than an empty one: "nobody" and "the machine" are different answers.
AUTOMATION = "automation"




@dataclass
class Approval:
    """One question, and its answer once there is one."""

    #: What kind of thing is being asked about — the host's own vocabulary.
    subject_type: str
    #: Which one.
    subject_id: str
    title: str
    kind: ApprovalKind = ApprovalKind.WORKFLOW_GATE
    id: str = field(default_factory=lambda: str(uuid4()))
    #: The stage this gates, for a workflow gate. Empty otherwise.
    stage_key: str = ""
    level: str = "medium"
    impact: str = ""
    checklist: list[str] = field(default_factory=list)
    #: For a tool permission: what was asked for.
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    #: What the answerer said, when they said anything.
    response: str = ""
    #: Who or what resolved it. ``AUTOMATION`` for a machine, a person's
    #: identifier otherwise, empty while pending.
    resolved_by: str = ""
    created_at: datetime = field(default_factory=utcnow)
    resolved_at: datetime | None = None

    @property
    def pending(self) -> bool:
        return self.status is ApprovalStatus.PENDING

    @property
    def approved(self) -> bool:
        return self.status is ApprovalStatus.APPROVED

    def resolve(
        self, *, approved: bool, resolved_by: str, response: str = ""
    ) -> Approval:
        """Answer it. Refuses to answer one that is already answered.

        Re-resolving would overwrite who signed off and when, which is the part
        of the record that has to be trustworthy.
        """
        if not self.pending:
            raise AlreadyResolvedError(
                f"Approval {self.id} was already {self.status.value}"
            )
        self.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        self.resolved_by = resolved_by
        self.response = response
        self.resolved_at = utcnow()
        return self


class AlreadyResolvedError(RuntimeError):
    """An approval that has been answered cannot be answered again."""


class ApprovalNotFoundError(LookupError):
    """No approval with that id."""


class ApprovalStore:
    """Approvals, kept in memory.

    A real host stores these in its own database — that is why the record above
    is a plain dataclass rather than an ORM model. This exists so the resolution
    logic can be exercised, and so a host has a reference for what a store owes
    the service: find one, list what is pending, and persist a change.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Approval] = {}

    def add(self, approval: Approval) -> Approval:
        self._by_id[approval.id] = approval
        return approval

    def get(self, approval_id: str) -> Approval:
        try:
            return self._by_id[approval_id]
        except KeyError as exc:
            raise ApprovalNotFoundError(approval_id) from exc

    def save(self, approval: Approval) -> Approval:
        self._by_id[approval.id] = approval
        return approval

    def pending(
        self, *, subject_type: str = "", subject_id: str = ""  # py-org: allow-string (the host's vocabulary, not this package's)
    ) -> list[Approval]:
        """Everything still waiting, oldest first.

        Oldest first because an inbox is a queue: the question that has been
        waiting longest is the one holding something up.
        """
        found = [a for a in self._by_id.values() if a.pending]
        if subject_type:
            found = [a for a in found if a.subject_type == subject_type]
        if subject_id:
            found = [a for a in found if a.subject_id == subject_id]
        return sorted(found, key=lambda a: a.created_at)

    def for_subject(self, subject_type: str, subject_id: str) -> list[Approval]:  # py-org: allow-string (the host's vocabulary, not this package's)
        return sorted(
            (
                a
                for a in self._by_id.values()
                if a.subject_type == subject_type and a.subject_id == subject_id
            ),
            key=lambda a: a.created_at,
        )

    def __len__(self) -> int:
        return len(self._by_id)


def serialize_tool_input(tool_input: Any) -> str:
    """Store tool input whole.

    Not truncated: the argument that made a call worth refusing is often the
    long one, and a record that elides it cannot answer why the decision was
    made.
    """
    return json.dumps(tool_input, ensure_ascii=False)


def parse_tool_input(raw: str) -> dict[str, Any]:
    """Read stored tool input, tolerating a payload an older build truncated."""
    if not raw or raw == "{}":
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}  # py-org: allow-isinstance
