"""Running an agent and answering what it asks for.

This is the loop: spawn, read the stream, and when a tool asks permission, put
the question to the host's policy and write the answer back down the agent's
stdin. Everything specific — who approves, what they are allowed to approve,
what gets recorded — belongs to the policy.

The reason it is worth having at all, rather than each host writing it: the
handshake is unforgiving in ways that are invisible until they bite. A response
must carry the request's own id. An approval with an empty ``updatedInput``
silently strips the agent's arguments. A denial still has to be *sent*, because
an agent waiting on an answer that never comes hangs rather than exits. And the
answer has to go out while the process is still running, which means writing to
stdin from inside the read loop.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lore_eden.agents.policy import PermissionDecision, PermissionPolicy, deny_all
from lore_eden.agents.process import ProcessResult, ProcessSupervisor, TimeoutKind, close_stdin
from lore_eden.agents.protocol import (
    ToolPermissionRequest,
    build_control_response,
    build_user_message,
    extract_permission_request,
    parse_stream_line,
    result_payload_status,
    session_id_from,
)


@dataclass
class BridgeOutcome:
    """What happened, in terms a caller can act on."""

    result: ProcessResult
    #: Session the agent announced, for a later resume. Empty when it did not.
    session_id: str = ""
    #: True when the agent emitted a result event saying it failed.
    reported_failure: bool = False
    #: Every request put to the policy, with what was decided.
    decisions: list[tuple[ToolPermissionRequest, PermissionDecision]] = field(
        default_factory=list
    )

    @property
    def ok(self) -> bool:
        return self.result.ok and not self.reported_failure

    @property
    def timed_out(self) -> TimeoutKind:
        return self.result.timed_out


@dataclass
class PermissionBridge:
    """Drives one agent run, answering its permission requests.

    The policy defaults to refusing everything. An unconfigured bridge that
    approved would let an agent run tools nobody decided to allow, with nothing
    saying so — the worst of the available behaviours.
    """

    argv: list[str]
    cwd: Path | None = None
    env_overlay: dict[str, str] = field(default_factory=dict)
    policy: PermissionPolicy = field(default_factory=deny_all)
    idle_timeout: float = 300.0
    #: Consulted between lines and while the policy blocks.
    cancelled: Callable[[], bool] = lambda: False
    #: Called for every decoded message, for a host that wants telemetry.
    on_message: Callable[[dict[str, Any]], None] | None = None
    #: What a denial says when the policy gives no message of its own.
    denial_default: str = "Denied"

    def run(self, *, stdin_text: str | None = None) -> BridgeOutcome:
        supervisor = ProcessSupervisor(
            argv=self.argv,
            cwd=self.cwd,
            env_overlay=self.env_overlay,
            idle_timeout=self.idle_timeout,
            cancelled=self.cancelled,
        )

        state = _RunState()

        def handle(line: str) -> None:
            payload = parse_stream_line(line)
            if payload is None:
                return
            if self.on_message is not None:
                self.on_message(payload)

            session = session_id_from(payload)
            if session:
                state.session_id = session

            finished, failed = result_payload_status(payload)
            if finished and failed:
                state.reported_failure = True

            request = extract_permission_request(payload)
            if request is None:
                return
            decision = self.policy.decide(request, cancelled=self.cancelled)
            state.decisions.append((request, decision))
            state.pending_responses.append(
                build_control_response(
                    request_id=request.request_id,
                    approved=decision.approved,
                    message=decision.message,
                    updated_input=decision.updated_input,
                    denial_default=self.denial_default,
                )
            )
            self._flush(state)

        def started(proc: Any) -> None:
            state.proc = proc

        result = supervisor.run(stdin_text=stdin_text, on_line=handle, on_started=started)

        if state.proc is not None:
            close_stdin(state.proc)

        return BridgeOutcome(
            result=result,
            session_id=state.session_id,
            reported_failure=state.reported_failure,
            decisions=state.decisions,
        )

    def _flush(self, state: _RunState) -> None:
        """Write queued responses to the agent's stdin.

        Queued and flushed rather than written inline so a process that died
        between the request and the answer does not raise out of the read loop —
        the run has already failed, and a BrokenPipeError on top of it reports
        the wrong cause.
        """
        proc = state.proc
        if proc is None or proc.stdin is None:
            return
        while state.pending_responses:
            message = state.pending_responses.pop(0)
            try:
                proc.stdin.write(json.dumps(message) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                # The agent is gone. Put nothing back: there is no one to answer,
                # and retrying would spin.
                state.pending_responses.clear()
                return

    def steer(self, state: _RunState, prompt: str) -> bool:
        """Send a message into a running agent. False when it could not be sent."""
        proc = state.proc
        if proc is None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(json.dumps(build_user_message(prompt, session_id=state.session_id)) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError):
            return False
        return True


@dataclass
class _RunState:
    proc: Any = None
    session_id: str = ""
    reported_failure: bool = False
    pending_responses: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[tuple[ToolPermissionRequest, PermissionDecision]] = field(
        default_factory=list
    )
