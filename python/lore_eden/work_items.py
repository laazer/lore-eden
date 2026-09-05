"""The work-item engine: hierarchy, dependencies, and roll-ups over a subtree.

loremaker's `crucible` project plans to build this — 24 backlog items for a
board engine whose README cites the same Milestone/Feature/Capability/Task
convention loregarden's own enums cite. This is that engine, on the seams from
`lore_eden.store` and `lore_eden.cache` rather than on one host's database.

Three things it does that a host should not have to rewrite:

- **Hierarchy validation**, so an invalid parent is refused at the API rather
  than discovered as a tree that will not render.
- **Cycle rejection on insert.** A dependency graph that can contain a cycle
  needs every later read to defend against one. Refusing the edge that would
  close it is what keeps `prerequisites`, ordering and roll-ups total.
- **Roll-ups over a subtree**, which are the expensive read and therefore the
  one that has to be cached, and therefore the one where a stale answer does the
  most damage.

## What it does not do

It does not know what a state *means*. One host runs five states and another
wants six, and neither set contains the other, so the vocabulary is
:class:`StateVocabulary` — supplied by the host, validated here, interpreted
nowhere. The only judgement this module makes about a state is whether the host
declared it *done*, and the host says which those are.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lore_eden.cache import Cache, DerivedRead, DerivedReadRegistry, NullCache
from lore_eden.store.records import (
    VALID_CHILDREN,
    WorkItemRecord,
    WorkItemState,
    WorkItemType,
)


class WorkItemError(Exception):
    """Base for every refusal this engine makes."""


class InvalidParentError(WorkItemError):
    """A parent that the hierarchy does not allow, or a milestone given one."""


class UnknownStateError(WorkItemError):
    """A state the host's vocabulary does not contain."""


class DependencyCycleError(WorkItemError):
    """An edge that would make an item wait, transitively, for itself."""


@dataclass(frozen=True)
class StateVocabulary:
    """The states a host uses, and which of them mean finished.

    Both are required. A vocabulary with no ``done`` states would make every
    roll-up read zero per cent complete forever — correct arithmetic over a
    question nobody asked.
    """

    states: tuple[WorkItemState, ...]
    done: frozenset[WorkItemState]

    def __post_init__(self) -> None:
        unknown = self.done - set(self.states)
        if unknown:
            raise UnknownStateError(
                f"{sorted(unknown)} marked done but not in the vocabulary {list(self.states)}"
            )

    def validate(self, state: WorkItemState) -> None:
        if state not in self.states:
            raise UnknownStateError(
                f"{state!r} is not one of {list(self.states)}"
            )

    def is_done(self, state: WorkItemState) -> bool:
        return state in self.done


@dataclass(frozen=True)
class Rollup:
    """How a subtree stands, counted once and cached.

    ``total`` counts the descendants, not the root: a milestone is not part of
    its own completion. A root with no children is zero of zero, and
    ``completion`` is 0.0 rather than an error — nothing has been done, which is
    true and is what a board shows.
    """

    item_id: str
    total: int
    done: int
    by_state: Mapping[WorkItemState, int]

    @property
    def completion(self) -> float:
        if self.total == 0:
            return 0.0
        return self.done / self.total


def item_tag(item_id: str) -> str:
    """What a cached read derives from when it read this item."""
    return f"work-item:{item_id}"


def children_tag(parent_id: str) -> str:
    """What a cached read derives from when it listed this item's children."""
    return f"work-item-children:{parent_id}"


class WorkItemEngine:
    """Hierarchy, dependencies and roll-ups, over protocols a host implements.

    No session, connection or ORM model appears in any signature here. The
    codebase this came from passed a live SQLModel ``Session`` into every
    service — ``TicketDependencies.__init__(self, session)`` was the shape
    throughout — which reads as harmless and is what pinned that engine to one
    database, on SQLite, in one process.
    """

    def __init__(
        self,
        items: Any,
        dependencies: Any,
        vocabulary: StateVocabulary,
        cache: Cache | None = None,
    ) -> None:
        self._items = items
        self._dependencies = dependencies
        self._vocabulary = vocabulary
        self._cache = cache if cache is not None else NullCache()
        self._reads = DerivedReadRegistry()
        self._rollup_read = self._reads.register(
            DerivedRead("work-item-rollup", self._compute_rollup, self._rollup_tags)
        )

    # ---------------------------------------------------------------- writing

    def save(self, record: WorkItemRecord) -> WorkItemRecord:
        """Validate the item against the hierarchy and the vocabulary, then store.

        Both checks happen before anything is written, so a refusal leaves the
        store exactly as it was.
        """
        self._vocabulary.validate(record.state)
        self._check_parent(record)

        previous = self._items.get_item(record.id)
        stored = self._items.save_item(record)
        self._invalidate_for(stored, previous)
        return stored

    def _check_parent(self, record: WorkItemRecord) -> None:
        if not record.parent_id:
            return
        if record.item_type is WorkItemType.MILESTONE:
            raise InvalidParentError("a milestone is a root; it cannot have a parent")
        parent = self._items.get_item(record.parent_id)
        if parent is None:
            raise InvalidParentError(f"no such parent: {record.parent_id}")
        allowed = VALID_CHILDREN[parent.item_type]
        if record.item_type not in allowed:
            raise InvalidParentError(
                f"a {parent.item_type.value} may hold {[t.value for t in allowed]}, "
                f"not a {record.item_type.value}"
            )

    def _invalidate_for(
        self, stored: WorkItemRecord, previous: WorkItemRecord | None
    ) -> None:
        """Drop what this write could have changed, derived from the write.

        Both parents when an item moves: the tree it left is as wrong as the one
        it joined, and a roll-up of the old parent that nobody invalidated is
        the bug this whole seam exists to make impossible.
        """
        tags = {item_tag(stored.id), children_tag(stored.parent_id)}
        if previous is not None and previous.parent_id != stored.parent_id:
            tags.add(children_tag(previous.parent_id))
        self._cache.invalidate_tags(frozenset(tags))

    # ---------------------------------------------------------- dependencies

    def add_dependency(self, item_id: str, depends_on_id: str) -> bool:
        """Record that ``item_id`` waits for ``depends_on_id``.

        Refused if it is a self-edge, or if ``depends_on_id`` already waits —
        directly or through any chain — for ``item_id``. Checking at write time
        is what lets every read afterwards assume the graph is acyclic.
        """
        if item_id == depends_on_id:
            raise DependencyCycleError(f"{item_id} cannot wait for itself")
        if self._reaches(depends_on_id, item_id):
            raise DependencyCycleError(
                f"{depends_on_id} already waits for {item_id}; this edge would close a cycle"
            )
        return self._dependencies.add_dependency(item_id, depends_on_id)

    def remove_dependency(self, item_id: str, depends_on_id: str) -> bool:
        return self._dependencies.remove_dependency(item_id, depends_on_id)

    def prerequisites(self, item_ids: Sequence[str]) -> Mapping[str, frozenset[str]]:
        return self._dependencies.prerequisites_map(item_ids)

    def dependents(self, item_ids: Sequence[str]) -> Mapping[str, frozenset[str]]:
        return self._dependencies.dependents_map(item_ids)

    def _reaches(self, start: str, target: str) -> bool:
        """Whether ``start`` waits for ``target``, transitively.

        Breadth-first and a level at a time, so the number of queries follows the
        graph's depth rather than its size. A node-at-a-time walk would be
        correct and would issue one query per edge, which is the shape that makes
        a cycle check too expensive to leave switched on.
        """
        seen: set[str] = set()
        frontier = [start]
        while frontier:
            if target in frontier:
                return True
            found = self._dependencies.prerequisites_map(frontier)
            seen.update(frontier)
            nxt: set[str] = set()
            for edges in found.values():
                nxt.update(edges)
            frontier = sorted(nxt - seen)
        return False

    # ------------------------------------------------------------------ reads

    def subtree(self, root_id: str) -> list[WorkItemRecord]:
        """Every descendant of ``root_id``, one query per level of depth.

        The root is not included: callers asking for a subtree are asking what
        is *under* something, and a roll-up that counted the root would report a
        milestone as one item closer to done than it is.
        """
        found: list[WorkItemRecord] = []
        frontier = [root_id]
        seen: set[str] = {root_id}
        while frontier:
            children = self._items.children_of(frontier)
            level = [child for group in children.values() for child in group]
            level = [child for child in level if child.id not in seen]
            if not level:
                break
            found.extend(level)
            seen.update(child.id for child in level)
            frontier = [child.id for child in level]
        return found

    def rollup(self, root_id: str) -> Rollup:
        """How the subtree under ``root_id`` stands, cached.

        The cache entry declares every item and every parent the walk read, so
        any write that touches one of them drops it. A host adding another
        cached read declares its own; nothing keeps a central list in step.
        """
        return self._reads.value_of(self._rollup_read, self._cache, [root_id])

    def portfolio(self, root_ids: Sequence[str]) -> Mapping[str, Rollup]:
        """Roll-ups for several roots, each cached on its own terms."""
        return {root_id: self.rollup(root_id) for root_id in root_ids}

    def _compute_rollup(self, root_id: str) -> Rollup:
        descendants = self.subtree(root_id)
        by_state: dict[WorkItemState, int] = {}
        done = 0
        for item in descendants:
            by_state[item.state] = by_state.get(item.state, 0) + 1
            if self._vocabulary.is_done(item.state):
                done += 1
        return Rollup(item_id=root_id, total=len(descendants), done=done, by_state=by_state)

    def _rollup_tags(self, root_id: str) -> frozenset[str]:
        """Everything the roll-up read, named.

        Derived by walking the same tree rather than from a list kept alongside
        it: a declaration that could disagree with the computation is a
        declaration that eventually will.
        """
        descendants = self.subtree(root_id)
        tags = {children_tag(root_id), item_tag(root_id)}
        for item in descendants:
            tags.add(item_tag(item.id))
            tags.add(children_tag(item.id))
        return frozenset(tags)

    def cached_reads(self) -> Iterable[str]:
        """The names of every cached read, so a test can enumerate them."""
        return self._reads.all_reads().keys()
