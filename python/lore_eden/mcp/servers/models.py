"""The record for a third-party MCP server a host knows about.

Two departures from a naive "store the config" table, both carried over
deliberately:

``auth_env_var`` holds the **name** of an environment variable, never a token.
A control-plane database gets copied around freely — into scratch directories
for migration dry-runs, into worktrees — and a secret at rest in it travels with
every copy. The value is read from the environment at the moment the server is
used, and never persisted.

The health columns record what an actual ``initialize`` handshake found, and
distinguish *never checked* from *checked and failing*. Those are different
facts and a UI that renders them the same way is lying about one of them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Field, SQLModel

#: Transports a CLI config understands. A stdio server is launched by the client
#: itself; an http one is dialled.
TRANSPORTS = ("http", "stdio")

#: Whether a server's tools run without asking the operator. The default is
#: "prompt", because trusting a third party is a decision an operator makes
#: rather than one inherited from having registered it.
POLICY_PROMPT = "prompt"
POLICY_AUTO = "auto"
TOOL_POLICIES = (POLICY_PROMPT, POLICY_AUTO)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class McpServerRecord(SQLModel, table=True):
    """A third-party MCP server, and how clients should reach it."""

    __tablename__ = "mcp_servers"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    #: Key under `mcpServers` in a client config, so it must be unique.
    name: str = Field(index=True, unique=True)
    description: str = ""
    #: "http" (url) or "stdio" (command + args).
    transport: str = "http"
    url: str = ""
    command: str = ""
    args_json: str = "[]"
    #: Name of the env var holding the credential — not the credential.
    auth_env_var: str = ""
    #: Disabled servers stay registered but are withheld from clients, so a
    #: misbehaving server can be parked without losing how it was configured.
    enabled: bool = True
    #: Server-level rather than per-tool: it matches how an operator reasons
    #: about a third party, and a per-tool allowlist would have to be maintained
    #: for every server added.
    tool_policy: str = POLICY_PROMPT
    #: Calls per minute before further calls are refused. 0 means no ceiling,
    #: which is the default — a limit nobody set should not start refusing work.
    #: Nothing in this package enforces it; it is recorded for hosts that do.
    rate_limit_per_min: int = 0
    #: Empty `last_checked_at` means never checked, which is not the same as
    #: checked-and-failing.
    last_checked_at: str = ""
    last_health_ok: bool = False
    last_health_latency_ms: int = 0
    #: Why the last check failed, in terms an operator can act on.
    last_health_error: str = ""
    #: Tool names reported to `tools/list` during the last check. Cached rather
    #: than fetched per request: listing means dialling the server, which is not
    #: something a page render should do.
    tools_json: str = "[]"
    #: Empty means the tools were never listed — a server can answer
    #: `initialize` and still refuse `tools/list`, and "no tools" and "we never
    #: asked" are not the same answer.
    tools_listed_at: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class McpServerCreate(SQLModel):
    name: str
    description: str = ""
    transport: str = "http"
    url: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    #: Name of an environment variable holding the credential — never the value.
    auth_env_var: str = ""
    enabled: bool = True
    tool_policy: str = POLICY_PROMPT
    rate_limit_per_min: int = 0


class McpServerUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    transport: str | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    auth_env_var: str | None = None
    enabled: bool | None = None
    tool_policy: str | None = None
    rate_limit_per_min: int | None = None


class McpServerView(SQLModel):
    id: str
    name: str
    description: str
    transport: str
    url: str
    command: str
    args: list[str]
    auth_env_var: str
    enabled: bool
    #: Whether that environment variable is actually set in this process. Lets a
    #: UI show "credential missing" without ever reading the value.
    auth_present: bool = False
    tool_policy: str = POLICY_PROMPT
    rate_limit_per_min: int = 0
    #: Empty means never checked — distinct from checked-and-failing.
    last_checked_at: str = ""
    last_health_ok: bool = False
    last_health_latency_ms: int = 0
    last_health_error: str = ""
    tools: list[str] = Field(default_factory=list)
    tools_listed_at: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
