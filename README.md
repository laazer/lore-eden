# lore-eden

Shared libraries for building on agents.

Three packages that do not depend on each other, so take the one you need:

- **[`python/`](python/)** — a harness for driving agent workflows. Mount an MCP
  server on FastAPI, run a stage machine over your own work items, supervise CLI
  agents, and gate their tool calls behind approvals.
- **[`ts/`](ts/)** — a React UI kit. Design tokens with dark/light theming,
  responsive breakpoints, a named-region window layout, a pane/canvas system, and
  chat.
- **[`gates/`](gates/)** — CI gates that grade a repository whose layout they
  detect rather than assume, from lefthook hooks or from a build step.

Agent work here means any agent work. Nothing in the harness knows what a ticket
or a pull request is; that is the host's vocabulary, supplied through interfaces.

## The harness

An MCP server is a tool registry plus a router:

```python
from fastapi import FastAPI
from lore_eden.mcp import McpServer, ServerInfo, ToolRegistry, make_mcp_router

registry = ToolRegistry()

@registry.tool("summarize", "Summarize a document")
async def summarize(document_id: str) -> dict:
    return {"summary": ...}

app = FastAPI()
app.include_router(make_mcp_router(McpServer(ServerInfo("my-host", "1.0"), registry)))
```

Registering a name twice raises `DuplicateToolError` rather than replacing the
first — a silent overwrite is a tool that stops being callable while everything
still looks wired up.

A workflow is stages, transitions, and the shell commands that gate them:

```python
from lore_eden.workflow import GatesConfig, WorkflowCursor, advance, run_gates, start

moved = start(WorkflowCursor(item_id="doc-42"), stages)

gate = run_gates(GatesConfig(enabled=True, commands=["pytest"]),
                 repo_root=repo, context={"stage": moved.cursor.stage_key})

result = advance(moved.cursor, stages, transitions,
                 outcome="pass" if gate.ok else "reject")
```

Stages route on the *outcome*, not on a step counter, so a failing gate sends
work backwards without the caller sequencing it. `WorkflowCursor` is frozen and
every move returns a new one — the host holds the record a UI renders from, and
mutating it underneath means a screen that disagrees with what was stored.

A gate that ran and passed is distinguishable from one that never ran:
`GateRunResult` carries an explicit `outcome` alongside `ok`, because collapsing
"passed" and "skipped" into one indistinguishable success is a bug that has
shipped before.

Approvals are a separate concern — `GateService` parks a work item on a decision
a human or another system has to make, and resumes it when the answer arrives.

`lore_eden.agents` supervises a CLI agent as a subprocess: `ProcessSupervisor`
enforces an idle timeout distinctly from a hard one (a silent agent and a slow
agent are different problems), and `PermissionBridge` turns the agent's
tool-permission prompts into decisions a host can approve, deny, or defer.

## The UI kit

```bash
cd ts && npm install
```

Tokens are defined once and derived into the views callers need, so a
component references a token rather than freezing its value:

```tsx
import { ThemeProvider, vars, useScreenSize, useRegion } from '@lore-eden/ui';

<ThemeProvider mode="light">
  <div style={{ color: vars.accent }}>…</div>
</ThemeProvider>
```

Two independent notions of size, which is worth getting straight early:

| | measures | stops |
|---|---|---|
| `useScreenSize()` | the window | `xs` `sm` `md` `lg` `xl` `x2` |
| `usePaneSize()` | the container a pane is in | three tiers |

A component inside a pane usually wants the second — a narrow pane on a wide
monitor is still narrow.

`useRegion('appBar')` asks where a piece of chrome belongs instead of deriving a
position, and the z-index comes from a named ladder rather than from whatever
number beat the last one somebody saw.

The chat primitives are a composer, a message list, a part registry, and a
streaming transport, each usable without the others.

Underneath sits a layer with no dependency on the rest: typed CSS lengths that
refuse to be added across units, an observer registry with named change
channels, a "go back to where I was" checkpoint stack over any router, an async
result as a discriminated union, and a handful of hooks.

## The gates

```bash
gates/scripts/install-workspace-hooks.sh /path/to/repo
```

That writes a marker-delimited managed block into the target's `lefthook.yml`
pointing back at this checkout, rather than copying scripts in — a copy in each
repo is a copy that drifts, which is the problem these were extracted to solve.

The gates are diff-scoped: only lines your change added or edited can fail one,
so installing them into an old repository does not stop work on day one. They
run on the worktree as readily as on the index, because at a build step an
agent's edits are uncommitted and a module just written is the least-reviewed
code there is.

See [`gates/README.md`](gates/README.md) for the individual gates, scopes,
configuration and waivers.

## What this is made of

Nothing here is written from scratch. Every module arrives by extraction from one
of two working projects — `loregarden`, an agent SDLC control plane, and
`loremaker` — taking whichever implementation was already the stronger one, and
fixing what the comparison exposed rather than copying it forward. The defects
each extraction found are recorded in its pull request.

The extraction discipline, and the rules that keep the source projects running
while it happens, are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Requirements

Python 3.10+. The gates are dependency-free and run under whatever `python3` a
repo hands them; the TypeScript gate needs `npm install` in `gates/` once. The UI
kit needs `npm install` in `ts/`, and the harness `pip install -e "python[dev]"`.

## License

AGPL-3.0. See [LICENSE](LICENSE).
