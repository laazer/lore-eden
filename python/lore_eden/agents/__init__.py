"""Driving a CLI coding agent as a subprocess, and answering what it asks for.

Four pieces, deliberately separate:

- :mod:`~lore_eden.agents.protocol` — the stream-json wire format. Pure
  functions over dicts; no process, no policy, no I/O.
- :mod:`~lore_eden.agents.policy` — what a host decides when a tool asks
  permission.
- :mod:`~lore_eden.agents.process` — supervising the subprocess: idle and hard
  deadlines, line reading, teardown.
- :mod:`~lore_eden.agents.bridge` — the loop that joins them.

The version this came from had all four in one 1,400-line class, interleaved
with one control plane's scope-rerouting, rework budgets, rate limits and
telemetry. The protocol is the same everywhere; the policy never is.
"""

from lore_eden.agents.bridge import PermissionBridge, BridgeOutcome
from lore_eden.agents.policy import (
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
    allow_all,
    deny_all,
)
from lore_eden.agents.process import (
    ProcessResult,
    ProcessSupervisor,
    TimeoutKind,
)
from lore_eden.agents.protocol import (
    build_control_response,
    build_user_message,
    extract_permission_request,
    parse_stream_line,
    result_payload_status,
)

__all__ = [
    "BridgeOutcome",
    "PermissionBridge",
    "PermissionDecision",
    "PermissionPolicy",
    "PermissionRequest",
    "ProcessResult",
    "ProcessSupervisor",
    "TimeoutKind",
    "allow_all",
    "build_control_response",
    "build_user_message",
    "deny_all",
    "extract_permission_request",
    "parse_stream_line",
    "result_payload_status",
]
