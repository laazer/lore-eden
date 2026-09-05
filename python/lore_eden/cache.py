"""Caching derived reads, with invalidation that cannot be forgotten.

Three reads cannot be recomputed per request at loremaker's size: hierarchy
roll-ups, portfolio completion rates, and transitive reachability in the
dependency graph. loregarden recomputes all three every time, which is fine at
its size and will not be at loremaker's.

The hard part is not storing values. It is that **a cached read added in six
months must not be able to ship without its invalidation**. The usual shape —
a cache with `get`/`set`, and a list somewhere of what to clear on write — fails
exactly there: the list is maintained by whoever remembers, and the failure mode
is silent. A stale roll-up served as current is the same class of defect as a
swallowed exception. It reports a success the system never had.

So the dependency declaration is not a convention here, it is the constructor.
A :class:`DerivedRead` takes its ``tags`` function positionally and has no
default, and :meth:`Cache.set` will not store a value without tags. There is no
way to cache something and forget to say what it derives from, because there is
no overload that accepts less.

Writes then invalidate by tag rather than by key, so a write path does not have
to know which reads exist — only which entities it touched. That is the half
that scales: new reads keep working when someone adds a write, and new writes
keep working when someone adds a read.

## The default does nothing, on purpose

:class:`NullCache` stores nothing and reports every lookup as a miss, and it is
the default everywhere. A host that wants no cache pays no cost and, more
importantly, gets identical behaviour — which is what makes "the engine behaves
the same with and without" a property a test can assert rather than a hope.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

Value = TypeVar("Value")


@dataclass(frozen=True)
class Lookup(Generic[Value]):
    """The result of asking a cache, with the miss made explicit.

    Returning ``None`` for a miss is the mistake this exists to prevent: a
    legitimately cached ``None`` — no roll-up, an empty portfolio, zero
    prerequisites — is indistinguishable from an empty cache, so every such read
    recomputes forever and nobody notices because the answers stay correct.
    """

    found: bool
    value: Any = None


MISS: Lookup[Any] = Lookup(found=False)


class Cache(Protocol):
    """What a host supplies, and nothing about how it stores it.

    Errors are deliberately not caught anywhere in this package. A cache backend
    that is down should say so: silently degrading to recompute turns a
    production outage into unexplained latency, and silently degrading to stale
    turns it into wrong answers.
    """

    def get(self, key: str) -> Lookup[Any]:
        """The value under ``key``, with ``found`` saying whether there was one."""
        ...

    def set(self, key: str, value: Any, tags: frozenset[str]) -> None:
        """Store a value together with what it derives from.

        ``tags`` is required. An implementation must not offer a way to store
        without them — that overload is the bug this design exists to remove.
        """
        ...

    def invalidate_tags(self, tags: frozenset[str]) -> int:
        """Drop everything derived from any of ``tags``; return how many went."""
        ...

    def clear(self) -> None: ...


class NullCache:
    """The default. Remembers nothing, so behaviour is identical without a cache."""

    def get(self, key: str) -> Lookup[Any]:
        return MISS

    def set(self, key: str, value: Any, tags: frozenset[str]) -> None:
        return None

    def invalidate_tags(self, tags: frozenset[str]) -> int:
        return 0

    def clear(self) -> None:
        return None


class DictCache:
    """An in-process cache, tag-indexed.

    Enough for one process, and the reference implementation the conformance
    tests hold a host's own cache to. Not bounded: a host that needs eviction
    brings its own, which is precisely why :class:`Cache` is a protocol.
    """

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._tags: dict[str, frozenset[str]] = {}

    def get(self, key: str) -> Lookup[Any]:
        if key not in self._values:
            return MISS
        return Lookup(found=True, value=self._values[key])

    def set(self, key: str, value: Any, tags: frozenset[str]) -> None:
        if not tags:
            raise ValueError(
                f"refusing to cache {key!r} with no tags: nothing could ever invalidate it"
            )
        self._values[key] = value
        self._tags[key] = tags

    def invalidate_tags(self, tags: frozenset[str]) -> int:
        doomed = [key for key, stored in self._tags.items() if stored & tags]
        for key in doomed:
            del self._values[key]
            del self._tags[key]
        return len(doomed)

    def clear(self) -> None:
        self._values.clear()
        self._tags.clear()


@dataclass(frozen=True)
class DerivedRead(Generic[Value]):
    """A computed value, and the declaration of what it derives from.

    ``compute`` and ``tags`` are both required and take the same arguments, so
    the answer and the reason it can go stale are written next to each other.
    There is no constructor that omits ``tags``; a test asserts that, because
    the guarantee is only as good as the absence of an easier path.
    """

    name: str
    compute: Callable[..., Value]
    tags: Callable[..., frozenset[str]]

    def key(self, arguments: Sequence[Any]) -> str:
        """The cache key: the read's name plus the arguments it was asked with."""
        return ":".join([self.name, *(str(argument) for argument in arguments)])


class DerivedReadRegistry:
    """Every cached read a host has, so they can be enumerated and checked.

    A registry rather than free functions because "does every cached read
    declare its dependencies?" has to be answerable by a test, and a test cannot
    enumerate decorators it was never told about.
    """

    def __init__(self) -> None:
        self._reads: dict[str, DerivedRead[Any]] = {}

    def register(self, read: DerivedRead[Value]) -> DerivedRead[Value]:
        if read.name in self._reads:
            raise ValueError(
                f"a derived read named {read.name!r} is already registered; two "
                "reads sharing a name would share a cache key and answer each other"
            )
        self._reads[read.name] = read
        return read

    def all_reads(self) -> Mapping[str, DerivedRead[Any]]:
        return dict(self._reads)

    def value_of(
        self, read: DerivedRead[Value], cache: Cache, arguments: Sequence[Any]
    ) -> Value:
        """The cached value if there is one, else compute, store and return it.

        The tags stored are the ones ``read`` declares for these arguments — so
        an entry can only ever be invalidated by something that touched what it
        was actually built from.
        """
        key = read.key(arguments)
        found = cache.get(key)
        if found.found:
            return found.value
        value = read.compute(*arguments)
        cache.set(key, value, read.tags(*arguments))
        return value
