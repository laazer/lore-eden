"""What a host decides when a tool asks to run.

The bridge asks; the host answers. Everything the version this came from did
between those two points — scope rerouting, rework budgets, rate limits, an
approval inbox, telemetry — was that control plane's policy, and none of it is a
property of the protocol.

A policy may block. That is the whole point of an approval inbox: the answer
arrives when a human gives it, and the agent waits. It may also decline to
answer forever, which is why :meth:`PermissionPolicy.decide` is handed a
cancellation check rather than being expected to poll one it invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from lore_eden.agents.protocol import ToolPermissionRequest

#: The request, as the bridge hands it to a policy.
PermissionRequest = ToolPermissionRequest


@dataclass(frozen=True)
class PermissionDecision:
    """The answer, and optionally an amended call.

    ``updated_input`` lets a policy narrow what was asked for rather than
    refusing it outright — approving a shell command with a flag removed, say.
    Empty means "as requested"; it is never sent as an empty map, because that
    would overwrite the agent's own arguments.
    """

    approved: bool
    message: str = ""
    updated_input: dict[str, Any] | None = None
    #: Anything the host wants to carry back out of the run for its own records.
    metadata: dict[str, Any] = field(default_factory=dict)


class PermissionPolicy(Protocol):
    """Decides whether a tool may run."""

    def decide(
        self,
        request: PermissionRequest,
        *,
        cancelled: Callable[[], bool],
    ) -> PermissionDecision:
        """Answer one request.

        May block — waiting for a human is the normal case, not an edge one.
        While blocking, consult ``cancelled()``: a run the operator has stopped
        should not sit on a question nobody is going to answer. Returning a
        denial on cancellation is the right shape; raising is not, because the
        agent still needs a reply to shut down cleanly.
        """
        ...


class _Constant:
    """A policy that always answers the same way."""

    def __init__(self, approved: bool, message: str = "") -> None:
        self._approved = approved
        self._message = message

    def decide(
        self,
        request: PermissionRequest,
        *,
        cancelled: Callable[[], bool],
    ) -> PermissionDecision:
        if cancelled():
            return PermissionDecision(approved=False, message="Run cancelled")
        return PermissionDecision(approved=self._approved, message=self._message)


def allow_all() -> PermissionPolicy:
    """Approve everything.

    For a sandbox, a test, or a run whose blast radius is already bounded by
    something else. Naming it plainly is deliberate: a host that wants this
    should have to say so, rather than getting it by leaving a policy unset.
    """
    return _Constant(approved=True)


def deny_all(message: str = "No permission policy configured") -> PermissionPolicy:
    """Refuse everything, saying why.

    The bridge's default. An unconfigured host that silently approved would be
    the worst of the available behaviours — the agent would run tools nobody
    decided to allow, and nothing would say so.
    """
    return _Constant(approved=False, message=message)
