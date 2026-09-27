"""How a failed instance operation is reported, over HTTP and over MCP alike.

One table, so the router's status code and a tool's ``error_kind`` cannot
disagree about what went wrong. A caller that cannot tell "that template does
not exist" from "no port is free right now" will retry the one that never
succeeds and give up on the one that would.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from lore_eden.instances.manager import InstanceLaunchError, InstanceNotFoundError, NotManagedError
from lore_eden.instances.ports import NoFreePortError
from lore_eden.instances.templates import TemplateParamError, UnknownTemplateError


class FailureKind(str, Enum):
    NOT_FOUND = "not_found"
    UNKNOWN_TEMPLATE = "unknown_template"
    INVALID_PARAMS = "invalid_params"
    NOT_MANAGED = "not_managed"
    NO_FREE_PORT = "no_free_port"
    LAUNCH_FAILED = "launch_failed"


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    status: int
    #: Whether the same call can succeed later without changing it.
    retryable: bool = False


_FAILURES: dict[type[Exception], Failure] = {
    InstanceNotFoundError: Failure(FailureKind.NOT_FOUND, 404),
    UnknownTemplateError: Failure(FailureKind.UNKNOWN_TEMPLATE, 404),
    TemplateParamError: Failure(FailureKind.INVALID_PARAMS, 422),
    NotManagedError: Failure(FailureKind.NOT_MANAGED, 409),
    NoFreePortError: Failure(FailureKind.NO_FREE_PORT, 503, retryable=True),
    InstanceLaunchError: Failure(FailureKind.LAUNCH_FAILED, 500),
}

#: KeyError's str() is the repr of its key; these say what was missing instead.
_MISSING_NOUN: dict[type[Exception], str] = {
    InstanceNotFoundError: "instance",
    UnknownTemplateError: "template",
}

#: Every exception an instance operation is expected to raise, for an ``except``.
EXPECTED_FAILURES: tuple[type[Exception], ...] = tuple(_FAILURES)


def classify(exc: Exception) -> tuple[Failure, str]:
    """The failure an expected exception stands for, and a sentence for a person.

    Walks the MRO, so a host's subclass of one of these maps like its parent.
    """
    mro = type(exc).__mro__
    failure = next((_FAILURES[k] for k in mro if k in _FAILURES), None)
    if failure is None:
        raise TypeError(f"{type(exc).__name__} is not an expected instance failure") from exc
    noun = next((_MISSING_NOUN[k] for k in mro if k in _MISSING_NOUN), None)
    detail = f"no {noun} {exc.args[0]!r}" if noun else str(exc)
    return failure, detail
