"""Whether a registered MCP server actually answers.

A server can be registered, enabled, and completely unreachable — a URL that
moved, a command that is not installed, a credential that was never exported.
Nothing found that out until an agent tried to use it mid-run, which is the
most expensive moment to discover it.

The check is a real MCP `initialize` handshake rather than a ping. A URL that
returns 200 for anything, or a command that starts and does nothing, is not a
working MCP server, and a check that called either healthy would be worse than
no check at all.

Deliberately out of the request path: this runs when an operator asks, and
records what it found. It is not a proxy and never sits between an agent and a
server.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session

from lore_eden.mcp.servers.models import McpServerRecord
from lore_eden.mcp.servers.registry import parse_args

logger = logging.getLogger(__name__)

#: A server that has not answered by now is not usable inside a run either.
TIMEOUT_SECONDS = 8.0

#: How this checker identifies itself in the handshake. A server operator
#: reading their logs should be able to tell who dialled them.
CLIENT_NAME = "lore-eden-health"

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": CLIENT_NAME, "version": "0.1.0"},
    },
}

#: Required by the protocol before a server will answer anything else.
_INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}

_TOOLS_LIST_ID = 2
_TOOLS_LIST = {"jsonrpc": "2.0", "id": _TOOLS_LIST_ID, "method": "tools/list"}


@dataclass
class HealthResult:
    ok: bool
    latency_ms: int
    #: Empty when ok. Otherwise what an operator would need to fix it.
    error: str
    #: Server name reported by the handshake, when it gave one.
    server_name: str = ""
    #: Tool names the server listed, or None if it was never asked or refused.
    #: None and [] are different answers — one is "we do not know", the other is
    #: "this server exposes nothing" — so a refused listing must not overwrite a
    #: catalogue an earlier check collected.
    tools: list[str] | None = None


class _HandshakeServerInfo(BaseModel):
    """The `serverInfo` block of an initialize result."""

    model_config = ConfigDict(extra="ignore")

    name: str = ""


class _InitializeResult(BaseModel):
    """The `result` of an MCP initialize, as far as a health check cares.

    Modelled rather than poked at by hand because this is a boundary: the JSON
    comes from a third-party server that may answer with anything at all,
    including a 200 from something that is not an MCP server.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    protocol_version: str | None = Field(default=None, alias="protocolVersion")
    capabilities: dict[str, Any] | None = None
    server_info: _HandshakeServerInfo | None = Field(default=None, alias="serverInfo")

    @property
    def is_recognisable(self) -> bool:
        """Whether any handshake field was actually present.

        Presence, not truthiness — `model_fields_set` records what the payload
        carried, so a server that sends an explicit null still counts as having
        answered the question, exactly as the key-membership test it replaces.
        """
        return bool(
            {"protocol_version", "capabilities", "server_info"} & self.model_fields_set
        )


class _ToolsListResult(BaseModel):
    """The `result` of a `tools/list`. Entries stay raw so one malformed tool
    does not discard the catalogue around it."""

    model_config = ConfigDict(extra="ignore")

    tools: list[Any]


class _ToolInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""


def _result_of(payload: dict | None, model: type[BaseModel]) -> Any:
    """The JSON-RPC `result` parsed as ``model``, or None if it is not one.

    A JSON-RPC error, a missing result, or a result of the wrong shape all mean
    the same thing to every caller here: this payload does not answer the
    question that was asked.
    """
    if not payload or payload.get("error"):
        return None
    try:
        return model.model_validate(payload.get("result"))
    except ValidationError:
        return None


def _handshake_name(payload: dict) -> str:
    result = _result_of(payload, _InitializeResult)
    if result is None or result.server_info is None:
        return ""
    return result.server_info.name


def _tool_names(payload: dict | None) -> list[str] | None:
    """Tool names from a `tools/list` result, or None if that is not one.

    A malformed or errored listing returns None rather than an empty list: a
    gateway shows a tool count, and "0 tools" claimed from a failed call is a
    number an operator would act on.
    """
    result = _result_of(payload, _ToolsListResult)
    if result is None:
        return None
    names: list[str] = []
    for raw in result.tools:
        try:
            tool = _ToolInfo.model_validate(raw)
        except ValidationError:
            # One unusable entry is not a reason to discard the tools around it.
            continue
        if tool.name:
            names.append(tool.name)
    return names


def _looks_like_initialize_result(payload: dict) -> bool:
    """Whether this is an MCP initialize response rather than any old JSON.

    Checked structurally: a JSON-RPC error, or a 200 from something that is not
    an MCP server at all, must not read as healthy.
    """
    result = _result_of(payload, _InitializeResult)
    return result is not None and result.is_recognisable


def _check_http(server: McpServerRecord) -> HealthResult:
    if not server.url:
        return HealthResult(ok=False, latency_ms=0, error="No URL configured")

    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if server.auth_env_var:
        token = os.environ.get(server.auth_env_var, "")
        if not token:
            return HealthResult(
                ok=False,
                latency_ms=0,
                error=f"{server.auth_env_var} is not set in this process",
            )
        headers["Authorization"] = f"Bearer {token}"

    started = time.monotonic()
    try:
        # Redirects are not followed: a registered URL that redirects elsewhere
        # is a change the operator should see, not one to quietly chase.
        response = httpx.post(
            server.url,
            json=_INITIALIZE,
            headers=headers,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.TimeoutException:
        return HealthResult(
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=f"No response within {TIMEOUT_SECONDS:.0f}s",
        )
    except httpx.HTTPError as exc:
        return HealthResult(
            ok=False, latency_ms=int((time.monotonic() - started) * 1000), error=str(exc)
        )

    latency_ms = int((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        return HealthResult(
            ok=False,
            latency_ms=latency_ms,
            error=f"HTTP {response.status_code}",
        )

    payload = _first_json_payload(response.text)
    if payload is None:
        return HealthResult(ok=False, latency_ms=latency_ms, error="Response was not JSON-RPC")
    if not _looks_like_initialize_result(payload):
        detail = (payload.get("error") or {}).get("message") if payload.get("error") else ""
        return HealthResult(
            ok=False,
            latency_ms=latency_ms,
            error=detail or "Did not answer an MCP initialize",
        )
    return HealthResult(
        ok=True,
        latency_ms=latency_ms,
        error="",
        server_name=_handshake_name(payload),
        tools=_list_tools_http(server.url, headers, response.headers.get("mcp-session-id", "")),
    )


def _list_tools_http(url: str, headers: dict[str, str], session_id: str) -> list[str] | None:
    """Ask an http server what tools it exposes, after a successful handshake.

    Best effort by design. A server that handshakes but will not list is
    healthy — the operator's question was "does this answer", and a listing that
    failed must not turn into a health failure or into a claim of zero tools.
    """
    listing_headers = dict(headers)
    if session_id:
        listing_headers["Mcp-Session-Id"] = session_id
    try:
        # The protocol wants the initialized notification before any request.
        httpx.post(
            url,
            json=_INITIALIZED,
            headers=listing_headers,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        response = httpx.post(
            url,
            json=_TOOLS_LIST,
            headers=listing_headers,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        logger.debug("tools/list failed for %s", url, exc_info=True)
        return None
    if response.status_code >= 400:
        return None
    return _tool_names(_payload_with_id(response.text, _TOOLS_LIST_ID))


def _json_payloads(body: str) -> list[dict]:
    """Every JSON object in a response body, in order.

    Streamable-HTTP servers answer in SSE frames (`data: {...}`), and a stdio
    server answers several requests down one pipe, so a body is not always one
    bare JSON object.
    """
    text = body.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return [parsed] if isinstance(parsed, dict) else []  # py-org: allow-isinstance (wire form, not a schema)
    except json.JSONDecodeError:
        pass

    payloads: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        candidate = line[len("data:") :].strip() if line.startswith("data:") else line
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):  # py-org: allow-isinstance (wire form, not a schema)
            payloads.append(parsed)
    return payloads


def _first_json_payload(body: str) -> dict | None:
    payloads = _json_payloads(body)
    return payloads[0] if payloads else None


def _payload_with_id(body: str, message_id: int) -> dict | None:
    """The response to one request, picked out of a multiplexed body."""
    for payload in _json_payloads(body):
        if payload.get("id") == message_id:
            return payload
    return None


def _check_stdio(server: McpServerRecord) -> HealthResult:
    if not server.command:
        return HealthResult(ok=False, latency_ms=0, error="No command configured")

    env = dict(os.environ)
    if server.auth_env_var and not env.get(server.auth_env_var):
        return HealthResult(
            ok=False,
            latency_ms=0,
            error=f"{server.auth_env_var} is not set in this process",
        )

    argv = [server.command, *parse_args(server.args_json)]
    started = time.monotonic()
    proc = None
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is operator-configured, not user input
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        # All three messages go down the pipe at once and the server answers
        # them in order. Keeping it to one `communicate` means one timeout and
        # one process teardown, rather than hand-rolled deadline reads.
        request = "".join(
            json.dumps(message) + "\n" for message in (_INITIALIZE, _INITIALIZED, _TOOLS_LIST)
        )
        stdout, stderr = proc.communicate(request, timeout=TIMEOUT_SECONDS)
    except FileNotFoundError:
        return HealthResult(ok=False, latency_ms=0, error=f"Command not found: {server.command}")
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
            proc.communicate()
        return HealthResult(
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=f"No response within {TIMEOUT_SECONDS:.0f}s",
        )
    except OSError as exc:
        return HealthResult(ok=False, latency_ms=0, error=str(exc))

    latency_ms = int((time.monotonic() - started) * 1000)
    payload = _first_json_payload(stdout)
    if payload is None:
        detail = (stderr or "").strip().splitlines()
        return HealthResult(
            ok=False,
            latency_ms=latency_ms,
            error=detail[-1] if detail else "No JSON-RPC response on stdout",
        )
    if not _looks_like_initialize_result(payload):
        return HealthResult(
            ok=False, latency_ms=latency_ms, error="Did not answer an MCP initialize"
        )
    return HealthResult(
        ok=True,
        latency_ms=latency_ms,
        error="",
        server_name=_handshake_name(payload),
        tools=_tool_names(_payload_with_id(stdout, _TOOLS_LIST_ID)),
    )


def check_server(server: McpServerRecord) -> HealthResult:
    """Reach one registered server and report what happened.

    Never raises: a check that blew up would be indistinguishable from a server
    that is down, and the operator needs to know which.
    """
    try:
        if server.transport == "http":
            return _check_http(server)
        return _check_stdio(server)
    except Exception as exc:  # noqa: BLE001 - the check itself must not fail the caller
        logger.warning("Health check for %s raised", server.name, exc_info=True)
        return HealthResult(ok=False, latency_ms=0, error=f"Check failed: {exc}")


def record_health(session: Session, server: McpServerRecord, result: HealthResult) -> McpServerRecord:
    """Store what a check found.

    `updated_at` is deliberately untouched: a check observes the server, it does
    not change how it is configured, and bumping that would make every check
    look like an edit in the audit trail.

    Lives here rather than in the registry so the dependency runs one way —
    health reads the registry, never the reverse.
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    server.last_checked_at = checked_at
    server.last_health_ok = result.ok
    server.last_health_latency_ms = result.latency_ms
    server.last_health_error = result.error
    # None means the listing was never answered, so the last catalogue we do
    # have stands. Overwriting it with nothing would report a server that went
    # briefly unreachable as one exposing no tools.
    if result.tools is not None:
        server.tools_json = json.dumps(result.tools)
        server.tools_listed_at = checked_at
    session.add(server)
    session.commit()
    session.refresh(server)
    return server
