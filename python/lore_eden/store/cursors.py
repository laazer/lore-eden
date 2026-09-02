"""Converting between a stored cursor and a workflow one.

Two types for one idea, deliberately. :class:`~lore_eden.workflow.WorkflowCursor`
is frozen, holds :class:`~lore_eden.workflow.StageStatus` members, and knows
nothing about storage. :class:`~lore_eden.store.CursorRecord` holds plain
strings, carries an ``agent_override`` and an ``updated_at``, and knows nothing
about workflows.

Keeping them apart is what lets the workflow package stay free of any storage
concept and the store stay free of the workflow's enums. The cost is one
conversion, and it belongs here rather than in either package — or, as it was
until an example needed it, in every host that uses both.

Written because the end-to-end example could not be assembled without it.
"""

from __future__ import annotations

from lore_eden.store.records import CursorRecord
from lore_eden.workflow.dispatch import WorkflowCursor
from lore_eden.workflow.models import StageStatus


def to_workflow_cursor(record: CursorRecord) -> WorkflowCursor:
    """A stored record as a cursor the dispatcher can move.

    An unrecognised status becomes ``PENDING`` rather than raising: a status
    written by a newer build should leave a work item runnable, not stuck behind
    a value this one cannot name.
    """
    statuses: dict[str, StageStatus] = {}
    for key, raw in record.stage_statuses.items():
        try:
            statuses[key] = StageStatus(raw)
        except ValueError:
            statuses[key] = StageStatus.PENDING
    return WorkflowCursor(
        item_id=record.item_id,
        stage_key=record.stage_key,
        stage_statuses=statuses,
        blocking_issues=record.blocking_issues,
    )


def to_cursor_record(cursor: WorkflowCursor) -> CursorRecord:
    """A cursor as a record to store.

    ``agent_override`` is deliberately absent: a cursor never carries one, and a
    round trip through here must not be able to write a pin back. Setting one is
    :meth:`CursorStore.set_agent_override`, and only that.
    """
    return CursorRecord(
        item_id=cursor.item_id,
        stage_key=cursor.stage_key,
        stage_statuses={key: status.value for key, status in cursor.stage_statuses.items()},
        blocking_issues=cursor.blocking_issues,
    )
