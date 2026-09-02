"""A host that runs **one stage per invocation** and then exits.

The shape a cron job, a queue worker, or a CI step actually has: wake up, find
where the item is, do one thing, write it down, stop. Nothing stays in memory
between stages, so everything the next invocation needs has to be in the
database — which is the claim the storage layer exists to support, and which the
in-process example cannot demonstrate.

    python examples/resumable_host.py --db run.db --workspace ./work --item paragraph

Prints one line per invocation and exits 0 while there is more to do, so a caller
can loop:

    while python examples/resumable_host.py ...; do :; done

## What this proves that the other example does not

`draft_and_critique.py` re-reads its cursor each iteration, but the loop is one
process — so a mistake that only bites across a real process boundary (a datetime
that does not survive SQLite, a pin consumed in memory but not on disk) would not
show. Here every stage is a fresh interpreter.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fake_bridge import make_fake_bridge_factory
from sqlmodel import Session, SQLModel, create_engine

from lore_eden.agents import PermissionDecision, TemplatePrompt
from lore_eden.runner import AgentBinding, AgentRegistry, StageRunner
from lore_eden.store import (
    DEFAULT_STALE_AFTER,
    CursorRecord,
    RunRecord,
    RunStatus,
    to_cursor_record,
    to_workflow_cursor,
)
from lore_eden.store.migrations import run_migrations
from lore_eden.store.sql import SqlCursorStore, SqlRunStore, enforce_sqlite_foreign_keys
from lore_eden.workflow import GatesConfig, WorkflowStageDef

STAGES = [
    WorkflowStageDef(key="draft", name="Draft", agent_id="writer", order=1),
    WorkflowStageDef(key="critique", name="Critique", agent_id="critic", order=2),
    WorkflowStageDef(key="done", name="Done", order=3, terminal=True),
]
TRANSITIONS = [
    {"from": "draft", "to": "critique", "when": "pass"},
    {"from": "critique", "to": "done", "when": "pass"},
    {"from": "critique", "to": "draft", "when": "reject"},
]


class AllowExceptBash:
    def decide(self, request, *, cancelled) -> PermissionDecision:
        if request.tool_name == "Bash":
            return PermissionDecision(approved=False, message="Not available here.")
        return PermissionDecision(approved=True)


def open_database(path: Path) -> Session:
    """Open the database, creating and migrating it if this is the first run."""
    enforce_sqlite_foreign_keys()
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    run_migrations(session)
    return session


def build_runner(workspace: Path, script: dict[str, str]) -> StageRunner:
    registry = AgentRegistry()
    for agent_id, prompt in (
        ("writer", "Write a short paragraph about {topic}."),
        ("critic", "Judge the paragraph about {topic}."),
    ):
        registry.register(
            AgentBinding(
                agent_id=agent_id,
                prompt=TemplatePrompt(prompt),
                policy=AllowExceptBash(),
                idle_timeout=10.0,
            )
        )

    return StageRunner(
        registry=registry,
        stages=STAGES,
        transitions=TRANSITIONS,
        workspace_root=workspace,
        prompt_dir=workspace / ".prompts",
        gates=GatesConfig(enabled=True, commands=["true"]),
        make_bridge=make_fake_bridge_factory(workspace, script),
    )


def sweep_abandoned(runs: SqlRunStore) -> list[str]:
    """Close out runs left behind by a process that died.

    A row still saying `running` from an invocation that never came back would
    otherwise stay that way forever, and an operator looking at the item would
    see work in flight that stopped hours ago.
    """
    stranded = runs.stale_runs(stale_after=DEFAULT_STALE_AFTER)
    for record in stranded:
        runs.finish_run(
            record.finish(
                RunStatus.ABANDONED,
                reason="The process running this stage did not come back.",
            )
        )
    return [record.run_id for record in stranded]


def advance_one_stage(
    *, db: Path, workspace: Path, item_id: str, script: dict[str, str]
) -> dict:
    """Run exactly one stage, write everything down, and return."""
    workspace.mkdir(parents=True, exist_ok=True)
    session = open_database(db)
    cursors = SqlCursorStore(session)
    runs = SqlRunStore(session)

    swept = sweep_abandoned(runs)

    stored = cursors.get_cursor(item_id) or cursors.save_cursor(
        CursorRecord(item_id=item_id)
    )
    cursor = to_workflow_cursor(stored)

    # Consumed here, in this process. The next invocation must not see it again.
    override = cursors.take_agent_override(item_id)

    record = runs.start_run(
        RunRecord(
            item_id=item_id,
            stage_key=cursor.stage_key or STAGES[0].key,
            agent_id=override,
            attempt=len(runs.list_runs(item_id=item_id)) + 1,
        )
    )
    execution = build_runner(workspace, script).run_stage(
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
    cursors.save_cursor(to_cursor_record(execution.dispatch.cursor))
    session.close()

    return {
        "stage": execution.stage_key,
        "outcome": execution.outcome.value,
        "next": execution.dispatch.cursor.stage_key,
        "finished": execution.dispatch.finished,
        "blocked": execution.dispatch.blocked,
        "swept": swept,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--item", default="paragraph")
    parser.add_argument("--draft-script", default="write")
    parser.add_argument("--critique-script", default="reject-then-pass")
    args = parser.parse_args(argv)

    result = advance_one_stage(
        db=args.db,
        workspace=args.workspace,
        item_id=args.item,
        script={"draft": args.draft_script, "critique": args.critique_script},
    )
    swept = f" (swept {len(result['swept'])})" if result["swept"] else ""
    print(f"{result['stage']}:{result['outcome']} -> {result['next'] or 'end'}{swept}")
    # Non-zero once there is nothing left to do, so a `while` loop terminates.
    return 1 if result["finished"] or result["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
