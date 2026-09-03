"""Where a harness keeps what it knows.

Protocols first, implementations second, and that ordering is the point. The
source project has no storage seam — its cursors and approvals live in tables
its own services query directly, which is what makes its orchestration hard to
lift. Whichever schema is written first becomes the thing everything couples to,
so the interface has to exist before any schema does.

- :mod:`lore_eden.store.records` — the values, with no database import.
- :mod:`lore_eden.store.protocols` — what a harness needs from storage.
- :mod:`lore_eden.store.memory` — a dict. Enough for one process doing one
  thing at a time, which is a real deployment and not only a test.
- :mod:`lore_eden.store.sql` — SQLModel, behind the ``sql`` extra.
- :mod:`lore_eden.store.migrations` — schema changes for that SQL store.
  Deliberately **not** re-exported here: it imports SQLAlchemy, and this barrel
  is the one a host without a database imports. Reach for it by its own path,
  as the SQL store itself is reached.
- :mod:`lore_eden.store.cursors` — converting between a stored cursor and the
  workflow's own, which is the one thing a host using both packages needs and
  had to write itself until an example proved it.

Importing this package pulls in none of SQLModel; reach for
``lore_eden.store.sql`` explicitly when you want it.
"""

from lore_eden.store.cursors import to_cursor_record, to_workflow_cursor
from lore_eden.store.memory import (
    InMemoryCursorStore,
    InMemoryCycleStore,
    InMemoryLedgerStore,
    InMemoryDependencyStore,
    InMemoryRelationStore,
    InMemoryRunStore,
    InMemoryTagStore,
    InMemoryWorkItemStore,
)
from lore_eden.store.protocols import (
    ApprovalStorage,
    CursorStore,
    CycleStore,
    DependencyStore,
    LedgerStore,
    RelationStore,
    RunStore,
    TagStore,
    WorkItemStore,
)
from lore_eden.store.records import (
    DEFAULT_STALE_AFTER,
    VALID_CHILDREN,
    CursorRecord,
    CycleRecord,
    RelationKind,
    RelationRecord,
    RunRecord,
    RunStatus,
    WorkItemRecord,
    WorkItemType,
    utcnow,
)

__all__ = [
    "DEFAULT_STALE_AFTER",
    "VALID_CHILDREN",
    "ApprovalStorage",
    "CursorRecord",
    "CursorStore",
    "CycleRecord",
    "CycleStore",
    "DependencyStore",
    "InMemoryCursorStore",
    "InMemoryCycleStore",
    "InMemoryLedgerStore",
    "InMemoryDependencyStore",
    "InMemoryRelationStore",
    "InMemoryRunStore",
    "InMemoryTagStore",
    "LedgerStore",
    "InMemoryWorkItemStore",
    "RelationKind",
    "RelationRecord",
    "RelationStore",
    "RunRecord",
    "RunStatus",
    "RunStore",
    "TagStore",
    "WorkItemRecord",
    "WorkItemStore",
    "WorkItemType",
    "to_cursor_record",
    "to_workflow_cursor",
    "utcnow",
]
