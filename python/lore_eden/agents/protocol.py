"""The stream-json wire format a CLI agent speaks.

Pure functions over decoded JSON. No process, no policy, no I/O — which is what
lets the format be tested against recorded transcripts rather than against a
live agent.

Two CLIs spell the same messages slightly differently (``control_request`` vs
``sdk_control_request``, ``tool_name`` vs ``tool``, ``tool_input`` vs
``input``). Normalizing that here, once, is the difference between a bridge that
works with both and one that has a vendor branch in every method.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: Message types that can carry a permission request.
_CONTROL_REQUEST_TYPES = frozenset({"control_request", "sdk_control_request"})

#: Request subtypes that mean "may I use this tool?".
_PERMISSION_SUBTYPES = frozenset({"permission", "can_use_tool"})


@dataclass(frozen=True)
class ToolPermissionRequest:
    """A tool asking to run, normalized across CLIs."""

    request_id: str
    tool_name: str
    tool_input: dict[str, Any]
    #: The message as received, for a host that needs a field this does not model.
    raw: dict[str, Any]


def parse_stream_line(line: str) -> dict[str, Any] | None:
    """One NDJSON line as a message, or None.

    None covers a blank line, a line that is not JSON, and a line that is JSON
    but not an object. All three are things a CLI legitimately emits — progress
    noise, a partial flush — and none is worth failing a run over.
    """
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    # Discriminating the wire form, before any schema applies: a line that is
    # valid JSON but not an object is noise, not a message.
    return payload if isinstance(payload, dict) else None  # py-org: allow-isinstance


def extract_permission_request(payload: dict[str, Any]) -> ToolPermissionRequest | None:
    """The permission request in this message, if it is one."""
    if payload.get("type", "") not in _CONTROL_REQUEST_TYPES:
        return None
    request = payload.get("request") or {}
    if request.get("subtype", "") not in _PERMISSION_SUBTYPES:
        return None
    request_id = (
        payload.get("request_id") or request.get("request_id") or request.get("id") or ""
    )
    return ToolPermissionRequest(
        request_id=str(request_id),
        tool_name=str(request.get("tool_name") or request.get("tool") or "tool"),
        tool_input=request.get("tool_input") or request.get("input") or {},
        raw=payload,
    )


def build_control_response(
    *,
    request_id: str,
    approved: bool,
    message: str = "",
    updated_input: dict[str, Any] | None = None,
    denial_default: str = "Denied",
) -> dict[str, Any]:
    """The reply to a permission request.

    ``updated_input`` is omitted when empty rather than sent as ``{}``: an empty
    map *overwrites* the agent's original arguments, so a well-meant "no changes"
    silently strips the command a shell tool was about to run.
    """
    if approved:
        inner: dict[str, Any] = {"behavior": "allow"}
        if updated_input:
            inner["updatedInput"] = updated_input
    else:
        inner = {"behavior": "deny", "message": message or denial_default}
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": inner,
        },
    }


def build_user_message(prompt: str, *, session_id: str | None = None) -> dict[str, Any]:
    """A message sent *into* a running agent — a steer, or a follow-up turn."""
    message: dict[str, Any] = {
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
    }
    if session_id:
        message["session_id"] = session_id
    return message


def result_payload_status(payload: dict[str, Any]) -> tuple[bool, bool]:
    """``(finished, failed)`` for a stream-json result event.

    Two booleans rather than an enum because "not a result" and "a result that
    passed" are different answers, and collapsing them loses the ability to tell
    a stream that ended from one that has not.
    """
    if payload.get("type") != "result":
        return False, False
    failed = bool(payload.get("is_error")) or payload.get("subtype") == "error"
    return True, failed


def session_id_from(payload: dict[str, Any]) -> str:
    """The session id an init event announces, or "".

    Worth capturing: it is what a later invocation resumes, and it arrives once
    at the start of a stream rather than being derivable afterwards.
    """
    if payload.get("type") == "system" and payload.get("subtype") == "init":
        return str(payload.get("session_id") or "")
    return ""
