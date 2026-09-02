# lore-eden (python)

The agent harness. Today: MCP transport, and the registry of MCP servers a host
makes reachable.

```bash
pip install -e "python[dev]"
```

## Serving MCP

Two pieces, kept apart on purpose. A `ToolRegistry` holds what you offer; an
`McpServer` speaks JSON-RPC and dispatches into it. The transport knows nothing
about any tool, which is what makes it reusable — the version this was extracted
from imported one application's tool table at module scope, so serving a
different set of tools meant editing the protocol handler.

```python
from fastapi import FastAPI
from lore_eden.mcp import McpServer, ServerInfo, ToolRegistry, make_mcp_router

tools = ToolRegistry()

@tools.tool("greet", "Greet somebody.", {"type": "object", "properties": {"name": {"type": "string"}}})
def greet(context, arguments):
    return f"hello {arguments['name']}"

app = FastAPI()
app.include_router(make_mcp_router(McpServer(ServerInfo("my-server"), tools)), prefix="/mcp")
```

That serves `initialize`, `tools/list`, `tools/call`, `ping` and the
`notifications/initialized` handshake, single messages and batches.

### Context

Handlers receive a `context` the host supplies per request, and this package
never inspects it — a database session for one host, a config object or nothing
for another. Pass a FastAPI dependency and it is resolved per request, so a
session opened for one call is not shared with the next:

```python
make_mcp_router(server, get_session)
```

### Tool failure

A tool that raises becomes a failed *call*, not a dead connection: the message
reaches the client in `result.content` with `isError: true`. MCP models it that
way because the model on the other end is expected to read it and try something
else. A malformed request body is different — it never reached a tool, so it
comes back as a JSON-RPC parse error with a 400.

## Reaching other MCP servers

The other direction: which third-party servers this host knows about, whether
they answer, and the client config that reaches them.

```python
from lore_eden.mcp.servers import (
    McpServerCreate, check_server, client_server_entries, create_server, record_health,
)

create_server(session, McpServerCreate(
    name="docs", transport="http", url="https://example.com/mcp", auth_env_var="DOCS_TOKEN",
))

client_server_entries(session)
# {"docs": {"type": "http", "url": "...", "headers": {"Authorization": "Bearer ..."}}}
```

Two properties worth knowing, both deliberate:

**Credentials are never stored.** `auth_env_var` holds the *name* of an
environment variable. A control-plane database gets copied into scratch
directories and worktrees, and a secret at rest in it travels with every copy.
The value is read from the environment at the moment the config is built.

**Disabled servers are withheld, not removed.** A misbehaving server can be
parked without losing how it was configured.

### Health

`check_server` performs a real `initialize` handshake rather than a ping — a URL
that returns 200 for anything, or a command that starts and does nothing, is not
a working MCP server, and a check that called either healthy would be worse than
no check at all. It never raises: a check that blew up would be
indistinguishable from a server that is down.

`record_health` stores the result without touching `updated_at`, so a check does
not read as an edit in the audit trail. It also keeps *never checked* distinct
from *checked and failing*, and `tools=None` (never listed, or refused) distinct
from `tools=[]` (this server exposes nothing).

## Tests

```bash
cd python && python -m pytest
```

The health tests run a real MCP server over a real subprocess — one built from
this package's own `McpServer`. Both halves are exercised against each other
rather than against a fixture that agrees with itself.
