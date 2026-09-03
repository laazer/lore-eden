"""Display-line builders — ``/`` composition for mid-dot labels and tagged log lines.

A streaming harness produces a great many one-line human-readable labels:
``session init · haiku``, ``$ pytest -q``, ``tool result · in=1 out=2``. Written
as f-strings they drift — a stray separator on an empty segment, a different
separator in the next module, a ``None`` rendered as the four characters
``None`` — and every such line is something a person reads while trying to
work out what an agent did.

``Dot`` composes those segments and drops the empty ones. ``Tag`` binds a
channel and yields a ``LogLine`` that keeps accepting ``/``:

>>> str(Dot("session init") / "haiku")
'session init · haiku'
>>> str(Dot("$ pytest") / "")
'$ pytest'
>>> SYS / "codex thread" / "abc123"
LogLine('SYS', 'codex thread · abc123')

``LogLine`` compares equal to a ``(tag, text)`` tuple so a host can adopt the
builders without rewriting call sites or tests that already speak in tuples.

The ``py-organization`` gate's mid-dot rule points at this module when a repo
sets ``mid_dot_helper`` in its gate config; see ``gates/README.md``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

SEPARATOR = " · "


@runtime_checkable
class DisplaySegments(Protocol):
    """A value that already carries display segments rather than needing ``str()``.

    ``Dot`` and ``LogLine`` implement this, which is how ``Dot(a) / b`` flattens
    instead of nesting a repr. A host's own label type can implement it too and
    compose on equal terms — that is the reason this is a protocol and not an
    internal type switch.
    """

    def display_parts(self) -> tuple[str, ...]:
        """The already-split segments, none of them empty."""
        ...


def _parts_from(part: object) -> list[str]:
    """Split one composition argument into zero or more non-empty segments.

    ``None`` and the empty string contribute nothing — that is what makes
    ``Dot(label) / optional_detail`` safe to write unconditionally.

    ``isinstance`` here is a structural check against the declared protocol
    above, which is what makes a host's own label type compose; the callability
    guard is because ``runtime_checkable`` only checks that the attribute
    exists, not that it is a method.
    """
    if part is None:
        return []
    if isinstance(part, DisplaySegments) and callable(part.display_parts):
        return list(part.display_parts())
    text = str(part)
    return [text] if text else []


class Dot:
    """Compose display segments with ``·``, skipping the empty ones.

    >>> str(Dot("codex thread") / "abc")
    'codex thread · abc'
    >>> str(Dot("only"))
    'only'
    >>> str(Dot())
    ''
    """

    __slots__ = ("_parts",)

    SEP = SEPARATOR

    def __init__(self, *parts: object) -> None:
        self._parts: list[str] = []
        for part in parts:
            self._parts.extend(_parts_from(part))

    def display_parts(self) -> tuple[str, ...]:
        return tuple(self._parts)

    def __truediv__(self, other: object) -> Dot:
        return Dot(*self._parts, other)

    def __rtruediv__(self, other: object) -> Dot:
        """``"prefix" / Dot(rest)`` — for building a label left-to-right from a plain string."""
        return Dot(other, *self._parts)

    def __str__(self) -> str:
        return SEPARATOR.join(self._parts)

    def __repr__(self) -> str:
        return f"Dot({', '.join(map(repr, self._parts))})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LogLine):  # py-org: allow-isinstance (__eq__)
            return NotImplemented
        if isinstance(other, DisplaySegments) and callable(other.display_parts):
            return self._parts == list(other.display_parts())
        if isinstance(other, str):  # py-org: allow-isinstance (__eq__)
            return str(self) == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(tuple(self._parts))


class LogLine:
    """A tagged display line: ``SYS / "codex thread" / thread_id``.

    Iterates and compares as ``(tag, text)``, so a host whose stream formatter
    already takes tuples can pass one straight through.

    >>> tag, text = SYS / "codex thread" / "abc"
    >>> (tag, text)
    ('SYS', 'codex thread · abc')
    >>> (SYS / "ready") == ("SYS", "ready")
    True
    """

    __slots__ = ("tag", "_parts")

    def __init__(self, tag: str, *parts: object) -> None:
        self.tag = tag
        self._parts: list[str] = []
        for part in parts:
            self._parts.extend(_parts_from(part))

    def display_parts(self) -> tuple[str, ...]:
        return tuple(self._parts)

    def __truediv__(self, other: object) -> LogLine:
        return LogLine(self.tag, *self._parts, other)

    @property
    def text(self) -> str:
        return SEPARATOR.join(self._parts)

    def with_body(self, body: object, *, width: int = 2000) -> LogLine:
        """Keep the mid-dot headline, then a clipped newline-separated body.

        For a tool result whose stdout belongs under the label rather than
        inside it. An empty body leaves the line alone instead of appending a
        blank line.
        """
        text = str(body).strip() if body is not None else ""
        if not text:
            return self
        return LogLine(self.tag, f"{self.text}\n{text[:width]}")

    def as_tuple(self) -> tuple[str, str]:
        return self.tag, self.text

    def __iter__(self) -> Iterator[str]:
        yield self.tag
        yield self.text

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: int) -> str:
        return self.as_tuple()[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LogLine):  # py-org: allow-isinstance
            return self.tag == other.tag and self._parts == other._parts
        if isinstance(other, tuple) and len(other) == 2:  # py-org: allow-isinstance
            return self.as_tuple() == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.as_tuple())

    def __repr__(self) -> str:
        return f"LogLine({self.tag!r}, {self.text!r})"


class Tag:
    """A channel name that starts a ``LogLine`` via ``/`` or a call.

    >>> SYS / "turn started"
    LogLine('SYS', 'turn started')
    >>> SYS("turn started", "haiku")
    LogLine('SYS', 'turn started · haiku')
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __truediv__(self, other: object) -> LogLine:
        return LogLine(self.name, other)

    def __call__(self, *parts: object) -> LogLine:
        """``SYS("turn started")`` — for a line that needs no ``/`` chain."""
        return LogLine(self.name, *parts)

    def maybe(self, text: object) -> LogLine | None:
        """``OUT.maybe(raw)`` — ``None`` for blank text, rather than an empty line.

        Agent stdout arrives with trailing newlines and sometimes nothing else;
        emitting that as a line is how a transcript fills with blanks.
        """
        if text is None:
            return None
        stripped = str(text).strip()
        if not stripped:
            return None
        return LogLine(self.name, stripped)

    def __repr__(self) -> str:
        return f"Tag({self.name!r})"


def mid_dot(*parts: object) -> str:
    """``str(Dot(...))`` for call sites that want the joined string directly."""
    return str(Dot(*parts))


def clip(text: object, width: int, *, fallback: str = "") -> str:
    """Truncate display text to ``width``; empty or ``None`` input yields ``fallback``."""
    if text is None:
        return fallback
    clipped = str(text)[:width]
    return clipped if clipped else fallback


def shell(command: object, *, width: int = 180) -> str:
    """A shell-style headline for a command segment: ``$ pytest -q``."""
    stripped = str(command).strip() if command is not None else ""
    return f"$ {clip(stripped, width, fallback='command')}"


def kv(key: str, value: object, *, empty: str = "—") -> str:
    """One ``key=value`` segment; a blank value becomes ``empty`` (an em dash by default).

    The placeholder matters: ``tokens=`` reads as a formatting bug, while
    ``tokens=—`` reads as "nothing to report", which is usually the truth.
    """
    text = "" if value is None else str(value)
    return f"{key}={text or empty}"


def kv_space(**pairs: object) -> str:
    """A space-joined ``key=value`` group for one segment: ``in=1 out=2``.

    A trailing underscore on a key is stripped, so ``in_=1`` yields ``in=1``
    for keys that collide with Python keywords.
    """
    return " ".join(kv(key.removesuffix("_"), value, empty="?") for key, value in pairs.items())


#: Channels a streaming agent harness emits on. ``SYS`` is harness narration,
#: ``OUT`` is agent stdout, ``TOOL``/``CMD`` are tool and command activity,
#: ``RUN`` is run lifecycle, and ``OK``/``FAIL``/``ERR`` are terminal outcomes.
SYS = Tag("SYS")
OUT = Tag("OUT")
TOOL = Tag("TOOL")
RUN = Tag("RUN")
CMD = Tag("CMD")
ERR = Tag("ERR")
OK = Tag("OK")
FAIL = Tag("FAIL")

#: What a stream formatter accepts: a built line, or the tuple it compares equal to.
LogPayload = LogLine | tuple[str, str]
