"""Storage: the records, the protocols, and the two implementations."""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, create_engine

from lore_eden.store import (
    DEFAULT_STALE_AFTER,
    CursorRecord,
    InMemoryCursorStore,
    InMemoryRunStore,
    RunRecord,
    RunStatus,
    utcnow,
)
from lore_eden.store.migrations import MIGRATIONS, run_migrations
from lore_eden.store.sql import (
    CursorRow,
    RunRow,
    SqlCursorStore,
    SqlRunStore,
    enforce_sqlite_foreign_keys,
)

STORES = ["memory", "sql"]


@pytest.fixture
def engine(tmp_path: Path):
    enforce_sqlite_foreign_keys()
    engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(params=STORES)
def cursors(request, engine):
    """Both implementations, through the same tests.

    A protocol nothing checks is a suggestion. Running one suite over both is
    what makes the in-memory store a usable deployment rather than a stub that
    drifts.
    """
    if request.param == "memory":
        yield InMemoryCursorStore()
        return
    with Session(engine) as session:
        yield SqlCursorStore(session)


@pytest.fixture(params=STORES)
def runs(request, engine):
    if request.param == "memory":
        yield InMemoryRunStore()
        return
    with Session(engine) as session:
        yield SqlRunStore(session)


class TestCursorStore:
    def test_round_trips(self, cursors) -> None:
        cursors.save_cursor(
            CursorRecord(item_id="doc-1", stage_key="draft", stage_statuses={"draft": "running"})
        )
        found = cursors.get_cursor("doc-1")
        assert found is not None
        assert found.stage_key == "draft"
        assert found.stage_statuses == {"draft": "running"}

    def test_absent_is_none_rather_than_an_error(self, cursors) -> None:
        assert cursors.get_cursor("nobody") is None

    def test_reading_an_override_consumes_it(self, cursors) -> None:
        # The whole reason this is one call. A read that leaves the pin behind
        # is the source's stale-pin dispatch loop: the next stage reads a pin
        # set for a run that already happened and routes to the wrong agent.
        cursors.set_agent_override("doc-1", "reviewer")
        assert cursors.take_agent_override("doc-1") == "reviewer"
        assert cursors.take_agent_override("doc-1") == ""

    def test_no_override_is_empty(self, cursors) -> None:
        cursors.save_cursor(CursorRecord(item_id="doc-1"))
        assert cursors.take_agent_override("doc-1") == ""
        assert cursors.take_agent_override("never-seen") == ""

    def test_saving_does_not_resurrect_a_consumed_override(self, cursors) -> None:
        # A caller holding a record it read *before* the take would otherwise
        # write the pin back, and the loop returns.
        cursors.save_cursor(CursorRecord(item_id="doc-1", stage_key="draft"))
        cursors.set_agent_override("doc-1", "reviewer")
        stale = cursors.get_cursor("doc-1")
        assert stale is not None and stale.agent_override == "reviewer"
        cursors.take_agent_override("doc-1")
        cursors.save_cursor(stale)
        assert cursors.take_agent_override("doc-1") == ""

    def test_lists_by_stage(self, cursors) -> None:
        cursors.save_cursor(CursorRecord(item_id="a", stage_key="draft"))
        cursors.save_cursor(CursorRecord(item_id="b", stage_key="critique"))
        assert [c.item_id for c in cursors.list_cursors(stage_key="draft")] == ["a"]
        assert len(cursors.list_cursors()) == 2

    def test_a_stored_record_is_not_the_callers_object(self, cursors) -> None:
        record = CursorRecord(item_id="doc-1", stage_key="draft")
        cursors.save_cursor(record)
        record.stage_key = "tampered"
        found = cursors.get_cursor("doc-1")
        assert found is not None and found.stage_key == "draft"


class TestRunStore:
    def test_round_trips_and_lists_newest_first(self, runs) -> None:
        now = utcnow()
        runs.start_run(RunRecord(item_id="doc-1", stage_key="draft", run_id="r1", started_at=now))
        runs.start_run(
            RunRecord(
                item_id="doc-1",
                stage_key="critique",
                run_id="r2",
                started_at=now + timedelta(seconds=1),
            )
        )
        assert [r.run_id for r in runs.list_runs(item_id="doc-1")] == ["r2", "r1"]
        assert [r.run_id for r in runs.list_runs(stage_key="draft")] == ["r1"]

    def test_carries_argv_and_metadata(self, runs) -> None:
        runs.start_run(
            RunRecord(
                item_id="doc-1",
                stage_key="draft",
                run_id="r1",
                argv=["claude", "-p"],
                metadata={"tokens": 1200},
            )
        )
        found = runs.get_run("r1")
        assert found is not None
        assert found.argv == ["claude", "-p"]
        assert found.metadata == {"tokens": 1200}

    def test_finishing_records_the_verdict(self, runs) -> None:
        record = runs.start_run(RunRecord(item_id="doc-1", stage_key="draft", run_id="r1"))
        runs.finish_run(
            record.finish(RunStatus.SUCCEEDED, outcome="pass", summary="drafted it")
        )
        found = runs.get_run("r1")
        assert found is not None
        assert found.status is RunStatus.SUCCEEDED
        assert found.summary == "drafted it"
        assert found.finished

    def test_a_heartbeat_for_a_missing_run_is_silent(self, runs) -> None:
        # A caller beating in a loop would otherwise have to guard every call.
        assert runs.beat("never-existed") is None


class TestAbandonedRuns:
    """Telling a run that died from one that is going.

    Without this, a process killed mid-run leaves a row saying `running`
    forever: a UI shows it busy, a queue will not start the next item, and the
    work item reads as blocked. That is how a dead run becomes a blocked ticket
    nobody can explain.
    """

    def test_a_fresh_run_is_running(self) -> None:
        record = RunRecord(item_id="doc-1", stage_key="draft")
        assert record.effective_status() is RunStatus.RUNNING

    def test_a_stale_heartbeat_reads_as_abandoned(self) -> None:
        now = utcnow()
        record = RunRecord(
            item_id="doc-1", stage_key="draft", heartbeat_at=now - timedelta(hours=1)
        )
        assert record.status is RunStatus.RUNNING, "nothing wrote a final status"
        assert record.effective_status(now) is RunStatus.ABANDONED

    def test_abandonment_is_computed_not_stored(self) -> None:
        # Whatever kills the process is exactly the thing that prevents it
        # writing a final status, so a stored ABANDONED could never be set by
        # the run it describes.
        now = utcnow()
        record = RunRecord(
            item_id="doc-1", stage_key="draft", heartbeat_at=now - timedelta(hours=1)
        )
        assert record.status is RunStatus.RUNNING
        assert record.effective_status(now) is RunStatus.ABANDONED

    def test_a_finished_run_never_becomes_abandoned(self) -> None:
        now = utcnow()
        record = RunRecord(
            item_id="doc-1", stage_key="draft", heartbeat_at=now - timedelta(days=7)
        ).finish(RunStatus.SUCCEEDED, now=now - timedelta(days=7))
        assert record.effective_status(now) is RunStatus.SUCCEEDED

    def test_a_beat_rescues_it(self, runs) -> None:
        now = utcnow()
        runs.start_run(
            RunRecord(
                item_id="doc-1",
                stage_key="draft",
                run_id="r1",
                heartbeat_at=now - timedelta(hours=1),
            )
        )
        assert len(runs.stale_runs(stale_after=DEFAULT_STALE_AFTER, now=now)) == 1
        runs.beat("r1", now=now)
        assert runs.stale_runs(stale_after=DEFAULT_STALE_AFTER, now=now) == []

    def test_the_sweep_finds_only_the_dead(self, runs) -> None:
        now = utcnow()
        runs.start_run(
            RunRecord(item_id="a", stage_key="draft", run_id="alive", heartbeat_at=now)
        )
        runs.start_run(
            RunRecord(
                item_id="b",
                stage_key="draft",
                run_id="dead",
                heartbeat_at=now - timedelta(hours=2),
            )
        )
        finished = RunRecord(
            item_id="c",
            stage_key="draft",
            run_id="done",
            heartbeat_at=now - timedelta(hours=2),
        ).finish(RunStatus.SUCCEEDED, now=now - timedelta(hours=2))
        runs.finish_run(finished)
        stale = runs.stale_runs(stale_after=DEFAULT_STALE_AFTER, now=now)
        assert [r.run_id for r in stale] == ["dead"]


class TestForeignKeys:
    def test_the_pragma_is_registered_on_the_engine_class(self, engine) -> None:
        # Registered per engine, the one engine somebody forgot is the one that
        # writes the orphan.
        with engine.connect() as connection:
            enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
        assert enabled == 1

    def test_registering_twice_does_not_stack_listeners(self, engine) -> None:
        enforce_sqlite_foreign_keys()
        enforce_sqlite_foreign_keys()
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1

    def test_a_declared_constraint_is_actually_enforced(self, tmp_path: Path) -> None:
        # The check that matters: a schema declaring FKs enforces nothing under
        # SQLite unless the PRAGMA is on, so a test writing an orphan passes and
        # proves the opposite of what it looks like it proves.
        enforce_sqlite_foreign_keys()
        engine = create_engine(f"sqlite:///{tmp_path / 'fk.db'}")
        with engine.connect() as connection:
            connection.exec_driver_sql("CREATE TABLE parent (id TEXT PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE child (id TEXT PRIMARY KEY, "
                "parent_id TEXT REFERENCES parent(id))"
            )
            connection.commit()
            with pytest.raises(Exception, match="FOREIGN KEY"):
                connection.exec_driver_sql(
                    "INSERT INTO child VALUES ('c1', 'no-such-parent')"
                )


class TestMigrations:
    def test_apply_to_an_empty_database(self, tmp_path: Path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
        with Session(engine) as session:
            applied = run_migrations(session)
        assert applied == [m.migration_id for m in MIGRATIONS]
        assert inspect(engine).has_table("lore_eden_runs")

    def test_are_safe_to_re_apply(self, tmp_path: Path) -> None:
        # Each guards its own changes, which is why no ledger table is needed —
        # a ledger is one more thing that can disagree with the schema.
        engine = create_engine(f"sqlite:///{tmp_path / 'twice.db'}")
        with Session(engine) as session:
            run_migrations(session)
            run_migrations(session)
        assert "attempt" in {
            column["name"] for column in inspect(engine).get_columns("lore_eden_runs")
        }

    def test_ids_are_unique_and_ordered(self) -> None:
        ids = [m.migration_id for m in MIGRATIONS]
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids)

    def test_backfill_adds_a_column_a_prior_schema_lacked(self, tmp_path: Path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE lore_eden_runs (run_id TEXT PRIMARY KEY, item_id TEXT, "
                "stage_key TEXT, heartbeat_at TEXT)"
            )
            connection.commit()
        with Session(engine) as session:
            run_migrations(session)
        assert "attempt" in {
            column["name"] for column in inspect(engine).get_columns("lore_eden_runs")
        }


class TestProtocolLayerIsDatabaseFree:
    """The seam only means something if it is checked.

    Asserted by importing the packages in a **subprocess** and inspecting its
    `sys.modules`, rather than this one's: by the time this file runs, its own
    imports of SQLModel have already polluted the picture.
    """

    PURE = ["lore_eden.workflow", "lore_eden.runner", "lore_eden.agents", "lore_eden.store"]

    def test_no_database_library_is_reachable(self) -> None:
        script = (
            "import sys;"
            + "".join(f"__import__({name!r});" for name in self.PURE)
            + "leaked = sorted(m for m in sys.modules "
            "if m.split('.')[0] in {'sqlmodel', 'sqlalchemy'});"
            "print(','.join(leaked))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        )
        assert result.stdout.strip() == "", (
            f"a database library reached the protocol layer: {result.stdout.strip()}"
        )

    def test_the_sql_module_does_import_one(self) -> None:
        # The control. Without it the test above would pass just as well if the
        # import machinery were broken.
        script = (
            "import sys, lore_eden.store.sql;"
            "print('sqlmodel' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        )
        assert result.stdout.strip() == "True"
