"""A complete lore-eden host, in one file.

Two agents take turns on a text file: one drafts, one critiques, and the critic
can send it back. Deliberately **not** software work — no tickets, no reviews, no
acceptance criteria — because a harness for "interacting with agents" that can
only express its own authors' job is not general, and the fastest way to find out
is to make it do something else.

It exercises, in one script:

- an MCP server the agents call back into, with one host tool
- a workflow template with a reject edge and a terminal stage
- an agent binding per stage, with a prompt builder that names no domain
- a permission policy that denies one tool and allows the rest
- a shell gate between stages
- persistence, so the run survives the process ending and resumes

Run it::

    python examples/draft_and_critique.py

It drives a **fake** agent, because CI has no authenticated `claude`. The real
invocation is at the bottom, commented, with the three flags that are otherwise
undocumented.
"""

from __future__ import annotations

from pathlib import Path

from fake_bridge import make_fake_bridge_factory

from lore_eden.agents import PermissionDecision, TemplatePrompt
from lore_eden.mcp import McpServer, ServerInfo, ToolRegistry
from lore_eden.runner import AgentBinding, AgentRegistry, StageRunner
from lore_eden.store import (
    CursorRecord,
    InMemoryCursorStore,
    InMemoryRunStore,
    RunRecord,
    RunStatus,
    to_cursor_record,
    to_workflow_cursor,
)
from lore_eden.workflow import GatesConfig, WorkflowStageDef, WorkflowTemplate

# --- what the agents may call back into -------------------------------------


def build_mcp_server(document: Path) -> McpServer:
    """One tool, so the agents have something of the host's to call."""
    tools = ToolRegistry()

    @tools.tool("read_document", "Read the document being worked on")
    async def read_document() -> dict:
        return {"text": document.read_text(encoding="utf-8") if document.exists() else ""}

    @tools.tool("write_document", "Replace the document being worked on")
    async def write_document(text: str) -> dict:
        document.write_text(text, encoding="utf-8")
        return {"written": len(text)}

    return McpServer(ServerInfo("draft-and-critique", "1.0"), tools)


# --- who may do what --------------------------------------------------------


class HousePolicy:
    """Allows everything except one tool, which it refuses by name.

    A real host would ask a person, or consult a table. The shape is what
    matters: a decision per request, with a message the agent can read — a
    denial with no reason leaves it guessing, and it will usually guess "try
    again differently".
    """

    def __init__(self, denied: str) -> None:
        self.denied = denied
        self.seen: list[str] = []

    def decide(self, request, *, cancelled) -> PermissionDecision:
        self.seen.append(request.tool_name)
        if request.tool_name == self.denied:
            return PermissionDecision(
                approved=False,
                message=f"{self.denied} is not available here; work with the document tools.",
            )
        return PermissionDecision(approved=True)


# --- the workflow -----------------------------------------------------------

TEMPLATE = WorkflowTemplate(
    slug="draft-and-critique",
    name="Draft and critique",
    stages=[
        WorkflowStageDef(key="draft", name="Draft", agent_id="writer", order=1),
        WorkflowStageDef(key="critique", name="Critique", agent_id="critic", order=2),
        WorkflowStageDef(key="done", name="Done", order=3, terminal=True),
    ],
    transitions=[
        {"from": "draft", "to": "critique", "when": "pass"},
        {"from": "critique", "to": "done", "when": "pass"},
        # The edge that makes this a workflow rather than a list: the critic can
        # send the draft back, and the runner resets the stages in between.
        {"from": "critique", "to": "draft", "when": "reject"},
    ],
)

DRAFT_PROMPT = TemplatePrompt(
    "Write a short paragraph about {topic}.\n"
    "Use the write_document tool to save it.\n"
    "End your reply with a line reading exactly: STAGE-OUTCOME: pass"
)

CRITIQUE_PROMPT = TemplatePrompt(
    "Read the document with read_document and judge it as a paragraph about {topic}.\n"
    "If it is good, end with: STAGE-OUTCOME: pass\n"
    "If it needs another attempt, end with: STAGE-OUTCOME: reject <what is wrong>"
)


def build_runner(workspace: Path, *, policy: HousePolicy, script: dict[str, str]):
    """Wire the registry and the runner.

    ``script`` says how the fake agent behaves per stage, which is the only part
    a real host would not have.
    """
    registry = AgentRegistry()
    registry.register(
        AgentBinding(agent_id="writer", prompt=DRAFT_PROMPT, policy=policy, idle_timeout=10.0)
    )
    registry.register(
        AgentBinding(agent_id="critic", prompt=CRITIQUE_PROMPT, policy=policy, idle_timeout=10.0)
    )

    return StageRunner(
        registry=registry,
        stages=list(TEMPLATE.stages),
        transitions=list(TEMPLATE.transitions),
        workspace_root=workspace,
        prompt_dir=workspace / ".prompts",
        # `true` is a stand-in for whatever proves the work: a linter, a test
        # suite, a spell-checker. It runs only after the agent claims a pass.
        gates=GatesConfig(enabled=True, commands=["true"]),
        make_bridge=make_fake_bridge_factory(workspace, script),
    )


# --- driving it -------------------------------------------------------------


def run(workspace: Path, *, script: dict[str, str], max_stages: int = 8) -> dict:
    """Take one item as far as it goes, storing everything as it happens."""
    workspace.mkdir(parents=True, exist_ok=True)
    document = workspace / "paragraph.txt"

    cursors = InMemoryCursorStore()
    runs = InMemoryRunStore()
    policy = HousePolicy(denied="Bash")
    server = build_mcp_server(document)
    runner = build_runner(workspace, policy=policy, script=script)

    cursors.save_cursor(CursorRecord(item_id="paragraph"))
    history: list[str] = []

    for _ in range(max_stages):
        stored = cursors.get_cursor("paragraph")
        assert stored is not None
        cursor = to_workflow_cursor(stored)

        # A pin set for one run only. Reading it clears it.
        override = cursors.take_agent_override("paragraph")

        record = runs.start_run(
            RunRecord(item_id="paragraph", stage_key=cursor.stage_key or "draft")
        )
        execution = runner.run_stage(
            cursor, agent_override=override, values={"topic": "otters"}
        )
        runs.finish_run(
            record.finish(
                RunStatus.SUCCEEDED if execution.report.reported else RunStatus.FAILED,
                outcome=execution.outcome.value,
                summary=execution.report.summary,
                reason=execution.report.reason,
            )
        )

        history.append(f"{execution.stage_key}:{execution.outcome.value}")
        cursors.save_cursor(to_cursor_record(execution.dispatch.cursor))

        if execution.dispatch.finished or execution.dispatch.blocked:
            break

    return {
        "history": history,
        "denied": [t for t in policy.seen if t == policy.denied],
        "tools": server.tools.names(),
        "runs": len(runs.list_runs(item_id="paragraph")),
        "document": document.read_text(encoding="utf-8") if document.exists() else "",
    }


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        # The critic rejects once, then accepts — so the reject edge runs.
        result = run(
            Path(tmp),
            script={"draft": "write", "critique": "reject-then-pass"},
        )
    for line in result["history"]:
        print(line)
    print(f"denied {len(result['denied'])} tool call(s); {result['runs']} run(s) recorded")
    return 0


# --- the real thing ---------------------------------------------------------
#
# Swap `make_bridge` for the default and build a real invocation. The three
# things worth knowing, none of which are documented anywhere else:
#
#     from lore_eden.agents import claude_oauth_env
#
#     runner = StageRunner(
#         ...,
#         make_bridge=None,                       # the default PermissionBridge
#         env=claude_oauth_env(token_file=Path.home() / ".claude-oauth-token"),
#     )
#
# 1. `CLAUDE_CODE_OAUTH_TOKEN` — without it the CLI reports "not logged in"
#    while the terminal that spawned it is signed in, because its interactive
#    session lives somewhere a subprocess cannot reach.
#
# 2. An **expired** token makes the CLI print a login message and exit **0**,
#    having produced no stream. Exit code, no reported failure, no timeout: every
#    signal says the run was fine. `BridgeOutcome.saw_result` is what catches it,
#    and the runner turns it into a reject naming authentication.
#
# 3. `build_invocation` adds `--verbose` and `--include-partial-messages` for
#    you. Both are required and neither is obvious: stream-json print mode is
#    rejected without the first, and without the second a long silent think is
#    indistinguishable from a hung process, so the idle timeout fires on a
#    working agent.

if __name__ == "__main__":
    raise SystemExit(main())
