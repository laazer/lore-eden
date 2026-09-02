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
- **A run can report *nothing* and exit 0.** `BridgeOutcome.ok` requires a
  result event, not just a clean exit — see below.


## Building the command line

`build_invocation` turns an adapter-neutral request into argv. It exists because
every CLI spells the same six ideas differently, and several of the couplings
between them are undocumented and silent when broken.

```python
from lore_eden.agents import CliAdapter, InvocationRequest, build_invocation, claude_oauth_env

request = InvocationRequest(
    adapter=CliAdapter.CLAUDE,
    workspace_root=repo,
    prompt_file=prompt_path,
    interactive=True,          # hold stdin open so the bridge can answer
    model="opus",
    effort="high",
    env=claude_oauth_env(token_file=cached_token),
)
invocation = build_invocation(request)
```

### What it knows that you would otherwise learn the hard way

- **`claude -p --output-format stream-json` is rejected without `--verbose`.**
- **Print mode needs `--include-partial-messages`** — it is the only stdout
  heartbeat print mode produces, and without it a long silent think is
  indistinguishable from a hung process, so the idle timeout fires on a working
  agent. A bridged run already emits an event per message, so there the flag is
  opt-in volume.
- **`cursor-agent` has no `--input-format`**, so it cannot be bridged at all.
  Asking for `interactive=True` raises `UnsupportedInvocationError` rather than
  producing a command that hangs on stdin nobody will read. Same for codex and
  opencode.
- **Effort is not a flag everywhere.** `claude` takes `--effort`; **cursor folds
  it into the model id** (`sonnet-4.5` → `sonnet-4.5-high`). A host that sets
  effort on a cursor run expecting a flag gets the default effort and nothing
  says so. `CliInvocation.model` records what was actually pinned.

### The token, and the silence

`claude` reads `CLAUDE_CODE_OAUTH_TOKEN`. Shell out without it and the CLI
reports "not logged in" while the terminal that spawned it is signed in — its
interactive session lives somewhere a subprocess cannot reach.

Worse is an *expired* token: the CLI prints a login message and **exits 0**,
having produced no stream. Exit code, no reported failure, no timeout — every
signal says the run was fine, and a stage advances on work nobody did.

So `BridgeOutcome.ok` requires `saw_result`: a run that reported nothing did not
succeed, whatever it exited with. `ended_silently` separates that case from a
real failure, because the remedy is different — it is an authentication problem
on the host, and retrying gets the same silence.

`claude_oauth_env` returns `{}` when there is no token rather than raising: an
interactive host may be authenticated another way, and guessing that this is
broken would break it.


## Telling an agent what to do

Prompt assembly is the most domain-specific thing in a harness and the one most
often welded to the runner. In the source project `_build_prompt` ran to ~140
lines of tickets, acceptance criteria, evidence ledgers and a stage-report
contract — none of which means anything to a host doing something else.

So the harness knows only that something takes a context and returns text:

```python
class Draft:
    def build(self, context: PromptContext) -> str:
        return f"Write about {context.values['topic']}."
```

`PromptContext` carries a free-form `values` mapping rather than named fields,
because naming them means choosing a vocabulary and every candidate belongs to
some particular host. `StaticPrompt` and `TemplatePrompt` cover the easy cases;
`TemplatePrompt` **raises on a missing key** rather than rendering an empty slot,
since a prompt with a hole gets answered anyway, badly, and the run then looks
like a model failure rather than a wiring one.


## Dispatch and approvals

Moving a work item through its workflow, and asking a human when a stage needs
signing off.

```python
from lore_eden.workflow import GateService, WorkflowCursor, advance, start

cursor = start(WorkflowCursor(item_id="app-1"), stages).cursor
cursor = advance(cursor, stages, transitions).cursor
```

The item is a **work item**, and this knows almost nothing about it: an id, a
cursor, which stages resolved, and somewhere to record why it is stuck. The
version this came from was typed against one product's ticket model in every
method, which is why the same dispatch logic could not be reused for anything
else even though none of it was ticket-specific.

State is a **value**, not a row. A cursor goes in and a new one comes out; the
host maps it to and from its own storage. That is what lets all of this be
tested without a database.

### Approvals name a subject, not a ticket

```python
Approval(subject_type="grant_application", subject_id="app-1", title="Fund it")
```

The record this came from had four foreign keys into one product's schema, which
is what stopped it being used for anything else. A type and an id means one store
holds approvals for whatever a host has.

The keys are **gone rather than nullable**: a nullable FK still constrains what
can be stored to rows in that table, which is the coupling, and a column nothing
references reads as an oversight.

### Three outcomes, not two

- **approved** — the stage passes, the item moves on
- **approved with rework** — the gate is satisfied *and* something must be redone
  first. A reviewer saying "yes, but fix the budget line" is not rejecting, and
  forcing it into a rejection loses the difference between "this failed" and
  "this passed, with follow-up"
- **rejected** — back to the previous stage, or blocked in place if there is
  nowhere to send it

### Auto-approval still writes a row

A gate that auto-approved silently would be indistinguishable from one that never
ran, so "was this signed off, and by whom?" would have no answer for exactly the
runs where it matters most. `resolved_by` distinguishes a person from
`AUTOMATION`.

### Answering a stage the item has left is refused

The item moves on while a question sits unanswered; applying the answer to
wherever it is *now* would resolve a stage nobody asked about. It blocks and says
so.

### Resuming is a hook

Approving a gate leaves the item on a stage ready to run, and asking the operator
to then press Run is a second decision carrying no information — but *what*
running means is the host's.

## Running a stage

`lore_eden.runner` is the loop that joins the two halves. Before it, the workflow
package could say which stage is next and the agents package could run a
subprocess, and nothing connected them — so every host wrote this loop, and this
loop is where all the decisions are.

```python
from lore_eden.runner import AgentBinding, AgentRegistry, StageRunner

registry = AgentRegistry()
registry.register(AgentBinding(agent_id="writer", prompt=Draft(), policy=my_policy))
registry.register(AgentBinding(agent_id="critic", prompt=Critique(), policy=my_policy))

runner = StageRunner(
    registry=registry, stages=stages, transitions=transitions,
    workspace_root=repo, prompt_dir=repo / ".prompts",
    gates=GatesConfig(enabled=True, commands=["pytest"]),
)

execution = runner.run_stage(cursor)
cursor = execution.dispatch.cursor
```

Resolve the agent, build the prompt, run it through the bridge, judge the run,
gate it, advance the cursor.

### Exit 0 is not a pass

The rule the package exists to hold. A CLI agent exiting 0 means the process
ended; it says nothing about whether the work was done. An agent that misread the
task, ran out of context, or decided the request was impossible all exit 0 after
saying so politely.

Mapping `returncode == 0` to a pass is the most tempting shortcut available,
because it works in every happy-path test anyone writes. So the default reader
**requires an explicit report**:

```
STAGE-OUTCOME: pass
STAGE-OUTCOME: reject   the opening does not say what this is about
```

Anything else is a reject carrying a reason that names *which* failure it was —
a run that crashed, one that timed out, one that ended in silence, and one that
finished chattily without a verdict are four different problems and a caller has
to tell them apart. `StageReport.reported` says whether the verdict came from the
agent or was inferred.

The last matching line wins, not the first: an agent that reconsiders leaves
both, and its final word is the one it meant.

### Gates run only on a pass

Gating work the agent has already disowned spends a test suite to confirm
something known. A gate that then fails turns the pass into a reject, and
`StageExecution.gate_blocked` distinguishes that from the agent rejecting its own
work — same outcome, different conversation with whoever looks at it next.

### Agent resolution is one function

`resolve_agent(stage, override=..., default=...)` — precedence stated, no I/O,
testable without running anything. Override, then the stage, then a host default.

This is a module rather than two lines because the source project resolved it
from three places at once with no stated order, and the result is a filed bug: a
stage honours a **stale** pin, dispatches to the wrong agent, which routes back,
which reads the same stale pin. A loop costing a full agent run per iteration.

The failure was never that three sources is too many — it was that the
precedence lived in the order of some `if` statements spread across a service,
so nobody could see it, test it, or say what it was. `AgentResolution.explain()`
names the winner and what lost.

**An override is consumed.** `consumes_override` tells the caller to clear the
stored pin, because a pin that outlives the run it was set for is read again by
the next one — which is the loop above.

## Storage

Protocols first, implementations second — and that ordering is the point.
Whichever schema gets written first becomes the thing everything couples to, so
the interface has to exist before any schema does. The source project has no
storage seam at all, which is what makes its orchestration hard to lift.

```python
from lore_eden.store import InMemoryCursorStore, InMemoryRunStore, RunRecord
```

`lore_eden.store` imports no database library. `lore_eden.store.sql` does, behind
the `sql` extra:

```bash
pip install "lore-eden[sql]"
```

A host with its own database implements `CursorStore` and `RunStore` and installs
neither. A test asserts the purity by importing `workflow`, `runner`, `agents` and
`store` in a **subprocess** and inspecting its `sys.modules` — by the time the test
file itself runs, its own imports have polluted the picture.

The in-memory stores are not only a test double. A host running one work item at a
time in one process — a script, a CLI, a scheduled job — needs no database, and
making it install one would tax the simplest case. Both implementations run through
the same test suite, which is what keeps the dict version a usable deployment rather
than a stub that drifts.

### A run that died is not a run that is going

A process holding a `running` row that is then killed — a reboot, an eviction, a
Ctrl-C — leaves that row saying `running` forever. Nothing can tell it from work in
flight, so a UI shows it busy, a queue will not start the next item, and the work
item reads as blocked. That is how a dead run becomes a blocked ticket nobody can
explain.

So a run carries a **heartbeat**, and `effective_status()` reports a stale `running`
as `abandoned`. Computed, never stored: whatever kills the process is exactly the
thing that stops it writing a final status, so a status only a live process could
write is no use for describing a dead one.

Read `effective_status()` anywhere a human or a queue acts on the answer. `status` is
what was written; `effective_status()` is what is true.

### Reading an override consumes it

`take_agent_override` returns the pin **and clears it**, in one call, because the
alternative is a caller that reads and forgets to clear — which is the source's
stale-pin dispatch loop. `save_cursor` deliberately does not write the field either:
a caller holding a record read *before* the take would otherwise put the pin back.

### Foreign keys, and where the PRAGMA goes

SQLite ignores foreign keys unless asked, **per connection**. A schema declaring them
enforces nothing by default, so a test writing an orphan passes and proves the
opposite of what it looks like it proves.

`enforce_sqlite_foreign_keys()` registers the PRAGMA on the SQLAlchemy `Engine`
*class*, so the application's engine, each test's, and a one-off script's all get it.
Registered per engine, the one engine somebody forgot is the one that writes the
orphan.

### Migrations are append-only

A migration that has run somewhere cannot be edited, because the database it ran
against will not run it again — editing one produces two schemas both claiming the
same version. Each guards its own changes and is safe to re-run, which is why there
is no ledger table: a ledger is one more thing that can disagree with the schema it
describes.

## A worked example

[`examples/draft_and_critique.py`](examples/draft_and_critique.py) is a complete
host in one file. Two agents take turns on a text file: one drafts, one
critiques, and the critic can send it back.

```bash
python examples/draft_and_critique.py
```

```
draft:pass
critique:reject
draft:pass
critique:pass
denied 4 tool call(s); 4 run(s) recorded
```

It exercises an MCP server the agents call back into, a workflow template with a
reject edge and a terminal stage, a prompt builder per stage, a permission policy
that denies one tool and allows the rest, a shell gate, and persistence.

It runs a **fake** agent — CI has no authenticated `claude`, and an example that
cannot run rots into a lie within two refactors. The real invocation is at the
bottom of the file, commented, with the three things about it that are documented
nowhere else.

**It is deliberately not software work.** No tickets, no reviews, no acceptance
criteria — because a harness for "interacting with agents" that can only express
its own authors' job is not general, and the fastest way to find out is to make it
do something else. A test asserts the example's code and the prompts reaching the
agent contain none of that vocabulary.

### What writing it found

The point of building a host was to find the gaps, and it found two:

- **Nothing converted a stored cursor to a workflow one.** `CursorRecord` holds
  strings; `WorkflowCursor` holds `StageStatus` members. Every host using both
  packages would have written that conversion. It is `to_workflow_cursor` /
  `to_cursor_record` now.
- **`ToolRegistry` could not list its tool names.** `definitions()` returns wire
  payloads, so a host asking "what do I offer?" either dug through those or
  reached for a private attribute. Added `names()`, `__contains__` and `__len__`.

A test asserts the example imports only public names, and that each one appears in
its package's `__all__` — a name importable but undiscoverable is the same gap in a
quieter form.
