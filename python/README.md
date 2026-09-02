# lore-eden (python)

The agent harness: MCP transport, the registry of MCP servers a host makes
reachable, a workflow engine, and driving a CLI agent as a subprocess.

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


## Driving a workflow

A workflow is a list of stages and the transitions between them, both data. This
package routes the cursor and runs the gates; it has no idea what a stage *does*,
and no idea what a work item is.

```python
from lore_eden.workflow import StateMachine, load_template

template = load_template(Path("workflows/recipe.yaml"))
plan = StateMachine.resolve_next_stage_key(template.stages, template.transitions, "cook")
plan.to_key        # "taste"
plan.upstream      # False
```

A reject follows its declared transition; a pass falls through to linear stage
order. `reset_upstream_stages` puts the stages a reroute invalidated back to
pending, and `skip_intermediate_stages` marks what a forward branch jumped over
as `wont_do` — left pending they never resolve, and a work item derived from
stage statuses would hang after finishing its branch.

### Checklists

A stage may carry a `checklist`. The version this was extracted from expanded
three placeholders inline by reading fields off a software-ticket model. Those
are one product's vocabulary, so expansion is a registry instead:

```python
expand_checklist(stage.checklist, {"{{tasting_notes}}": lambda panel: [...]}, panel)
```

Unregistered tokens pass through unchanged — dropping them would silently
shorten a checklist its author wrote deliberately.

### Gates

Gates are configuration: command templates, run in a directory, with
placeholders filled from a context you build.

```python
from lore_eden.workflow import GatesConfig, run_gates_with_autofix

config = GatesConfig(enabled=True, commands=["ruff check ."], autofix_commands=["ruff check --fix ."])
cycle = run_gates_with_autofix(config, repo_root=worktree, context={"transition": "draft_to_review"})
```

Nothing in the engine names a linter — that example is data you supplied.

Two distinctions the outcomes preserve, both of which cost real time when they
were collapsed:

- **`UNAVAILABLE` is not `FAILED`.** A gate that timed out or is not on PATH
  never ran. Reporting it as a failure sends it to the stage's agent to fix, and
  no agent can install a toolchain it cannot see.
- **`SKIPPED` is not `PASSED`.** A run that passed a real gate and one where
  nothing was configured must not read the same.

`run_gates_with_autofix` runs one repair cycle — fixers, then a re-run. Not a
loop: if the fixers did not clear it, running them again will not either. What
happens next is your escalation policy, and `autofix_agent_fallback` records the
intent for it. A gate that could not *run* is never retried.

**`repo_root` must be the tree the stage actually wrote in.** Run gates in a
shared checkout and every one of them passes on work it never saw.


## Running a CLI agent

```python
from lore_eden.agents import PermissionBridge, PermissionDecision

class Inbox:
    def decide(self, request, *, cancelled):
        # Blocking is fine — waiting for a human is the normal case.
        answer = wait_for_approval(request.tool_name, request.tool_input, cancelled)
        return PermissionDecision(approved=answer.allowed, message=answer.reason)

outcome = PermissionBridge(
    argv=["claude", "-p", "--output-format", "stream-json", ...],
    cwd=worktree,
    policy=Inbox(),
    idle_timeout=300,
).run(stdin_text=prompt)
```

Four pieces, separate on purpose: `protocol` (the wire format, pure functions),
`policy` (what a host decides), `process` (subprocess supervision), `bridge`
(the loop joining them). The version this came from had all four in one
1,400-line class interleaved with one control plane's scope-rerouting, rework
budgets, rate limits and telemetry. The protocol is the same everywhere; the
policy never is.

### The default policy refuses

An unconfigured bridge that approved would run tools nobody decided to allow,
with nothing saying so. `allow_all()` exists and has to be asked for by name.

### Two timeouts, not one

A single wall-clock deadline kills a long run that is working perfectly — an
agent editing twenty files legitimately takes longer than one editing two, and
no number is generous enough for the second without being useless for the first.

- **Idle** — the longest the process may go saying *nothing*. Output is
  progress, so any line resets it. This is what catches a hung agent.
- **Hard** — an absolute ceiling (6× idle by default). A process emitting a line
  every few seconds forever never trips the idle budget; this catches the loop
  that is busy rather than stuck.

They are reported separately because they diagnose different faults: idle is
usually a wedged tool, hard is usually a loop, and a caller routes them
differently.

The reading happens on a thread. Iterating the pipe directly blocks until a line
arrives, so nothing else runs while the process is silent — and a deadline
checked only when output appears cannot fire on a process producing none, which
makes it a timeout that works for every case except the one it was written for.

### Traps the protocol module handles

- **An approval with an empty `updatedInput` overwrites the agent's arguments.**
  A well-meant "no changes" silently strips the command a shell tool was about
  to run, so it is omitted rather than sent empty.
- **A denial still has to be sent.** An agent waiting on an answer that never
  comes hangs rather than exits.
- **A result event can report failure while the process exits 0.** Reading only
  the exit code calls that a success.
