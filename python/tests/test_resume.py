"""Resuming a work item in a **new process**.

The claim the storage layer exists to support, and the one nothing had actually
executed. `draft_and_critique.py` re-reads its cursor each iteration but stays in
one interpreter, so a mistake that only bites across a real process boundary — a
datetime that does not survive SQLite, a pin consumed in memory but not on disk —
would not show there.

Every test here runs `examples/resumable_host.py` as a subprocess. That is the
point: what breaks resumption lives at the boundary, and an in-process version of
these tests would prove considerably less than it appears to.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from lore_eden.store import DEFAULT_STALE_AFTER, RunRecord, RunStatus
from lore_eden.store.sql import SqlCursorStore, SqlRunStore, enforce_sqlite_foreign_keys

HOST = Path(__file__).resolve().parent.parent / "examples" / "resumable_host.py"


def invoke(db: Path, workspace: Path, **flags: str) -> tuple[int, str]:
    """One invocation of the host, as a separate interpreter."""
    argv = [sys.executable, str(HOST), "--db", str(db), "--workspace", str(workspace)]
    for name, value in flags.items():
        argv += [f"--{name.replace('_', '-')}", value]
    done = subprocess.run(argv, capture_output=True, text=True)
    assert done.returncode in (0, 1), done.stderr
    return done.returncode, done.stdout.strip()


def session_for(db: Path) -> Session:
    enforce_sqlite_foreign_keys()
    engine = create_engine(f"sqlite:///{db}")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "run.db", tmp_path / "work"


class TestResumingAcrossProcesses:
    def test_a_second_process_picks_up_where_the_first_left_off(self, paths) -> None:
        db, workspace = paths
        _, first = invoke(db, workspace)
        assert first == "draft:pass -> critique"

        # A completely fresh interpreter, holding nothing.
        _, second = invoke(db, workspace)
        assert second.startswith("critique:")

    def test_the_stage_statuses_the_first_wrote_survive(self, paths) -> None:
        db, workspace = paths
        invoke(db, workspace)
        with session_for(db) as session:
            stored = SqlCursorStore(session).get_cursor("paragraph")
        assert stored is not None
        assert stored.stage_key == "critique"
        # Not merely that a row exists: the statuses are what the next process
        # resolves the workflow from.
        assert stored.stage_statuses.get("draft") == "done"

    def test_the_whole_workflow_completes_one_process_per_stage(self, paths) -> None:
        db, workspace = paths
        history: list[str] = []
        for _ in range(8):
            code, line = invoke(db, workspace)
            history.append(line)
            if code == 1:
                break
        assert history == [
            "draft:pass -> critique",
            "critique:reject -> draft",
            "draft:pass -> critique",
            "critique:pass -> done",
        ]

    def test_running_a_finished_item_again_is_a_no_op(self, paths) -> None:
        # Found by writing this: a terminal stage names no agent, so the runner
        # raised UnknownAgentError. Reachable whenever a caller reads the cursor
        # fresh rather than remembering it finished — a cron firing once more, a
        # duplicate queue message, an operator re-running a command. The
        # in-process example never hit it because its loop broke on `finished`.
        db, workspace = paths
        for _ in range(8):
            if invoke(db, workspace)[0] == 1:
                break
        code, line = invoke(db, workspace)
        assert code == 1
        assert line == "done:pass -> done"

    def test_every_stage_left_a_run_row(self, paths) -> None:
        db, workspace = paths
        for _ in range(8):
            if invoke(db, workspace)[0] == 1:
                break
        with session_for(db) as session:
            runs = SqlRunStore(session).list_runs(item_id="paragraph")
        assert len(runs) == 4
        assert [run.attempt for run in runs] == [4, 3, 2, 1]


class TestAnOverrideIsConsumedExactlyOnce:
    def test_the_second_process_does_not_see_it_again(self, paths) -> None:
        # Three guards already exist against writing a pin back — the two
        # save_cursor implementations and to_cursor_record. This is the test
        # that would catch a fourth route, because it crosses a real boundary.
        db, workspace = paths
        with session_for(db) as session:
            cursors = SqlCursorStore(session)
            cursors.set_agent_override("paragraph", "writer")

        invoke(db, workspace)
        with session_for(db) as session:
            assert SqlCursorStore(session).take_agent_override("paragraph") == ""

    def test_it_reached_the_run_that_consumed_it(self, paths) -> None:
        db, workspace = paths
        with session_for(db) as session:
            SqlCursorStore(session).set_agent_override("paragraph", "writer")
        invoke(db, workspace)
        with session_for(db) as session:
            runs = SqlRunStore(session).list_runs(item_id="paragraph")
        assert runs[0].agent_id == "writer"


class TestARunLeftByADeadProcess:
    def test_is_reported_abandoned_from_another_process(self, paths) -> None:
        db, workspace = paths
        # Stand in for a process killed mid-stage: a row that says `running`
        # and a heartbeat that stopped.
        with session_for(db) as session:
            SqlRunStore(session).start_run(
                RunRecord(
                    item_id="paragraph",
                    stage_key="draft",
                    run_id="orphan",
                    heartbeat_at=RunRecord(item_id="x", stage_key="y").started_at
                    - timedelta(hours=2),
                )
            )

        with session_for(db) as session:
            stale = SqlRunStore(session).stale_runs(stale_after=DEFAULT_STALE_AFTER)
        assert [run.run_id for run in stale] == ["orphan"]
        assert stale[0].effective_status() is RunStatus.ABANDONED

    def test_the_next_invocation_sweeps_it(self, paths) -> None:
        db, workspace = paths
        with session_for(db) as session:
            SqlRunStore(session).start_run(
                RunRecord(
                    item_id="paragraph",
                    stage_key="draft",
                    run_id="orphan",
                    heartbeat_at=RunRecord(item_id="x", stage_key="y").started_at
                    - timedelta(hours=2),
                )
            )

        _, line = invoke(db, workspace)
        assert "swept 1" in line

        with session_for(db) as session:
            swept = SqlRunStore(session).get_run("orphan")
        assert swept is not None
        assert swept.status is RunStatus.ABANDONED
        assert "did not come back" in swept.reason

    def test_a_live_run_is_not_swept(self, paths) -> None:
        db, workspace = paths
        with session_for(db) as session:
            SqlRunStore(session).start_run(
                RunRecord(item_id="paragraph", stage_key="draft", run_id="alive")
            )
        _, line = invoke(db, workspace)
        assert "swept" not in line


class TestDatetimesSurviveTheRoundTrip:
    """SQLite hands back naive datetimes whatever went in.

    So a comparison against an aware `now` raises `TypeError`, and the place it
    surfaces is whichever code compares first — usually not the code at fault.
    """

    def test_a_stored_run_can_be_compared_against_now(self, paths) -> None:
        db, workspace = paths
        invoke(db, workspace)
        with session_for(db) as session:
            runs = SqlRunStore(session).list_runs(item_id="paragraph")
        # Would raise TypeError if `_aware` were not coercing on read.
        assert runs[0].effective_status() is not None
        assert runs[0].duration() >= timedelta(0)

    def test_a_stored_cursor_carries_its_timestamp(self, paths) -> None:
        db, workspace = paths
        invoke(db, workspace)
        with session_for(db) as session:
            stored = SqlCursorStore(session).get_cursor("paragraph")
        assert stored is not None and stored.updated_at is not None

    def test_stale_detection_works_on_rows_that_went_through_the_database(
        self, paths
    ) -> None:
        # The combination that matters: an aware cutoff, a naive stored value,
        # and a comparison between them.
        db, workspace = paths
        old = RunRecord(item_id="x", stage_key="y").started_at - timedelta(hours=2)
        with session_for(db) as session:
            SqlRunStore(session).start_run(
                RunRecord(
                    item_id="paragraph", stage_key="draft", run_id="old", heartbeat_at=old
                )
            )
        with session_for(db) as session:
            stale = SqlRunStore(session).stale_runs(stale_after=DEFAULT_STALE_AFTER)
        assert [run.run_id for run in stale] == ["old"]
