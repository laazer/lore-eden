"""Tests for migration introspection and the table rebuild.

The rebuild is the reason this module is SQLite-specific, and the reason the
foreign-key guard exists: with enforcement on, `DROP TABLE` fails against any
table referencing the one being rebuilt.

`PRAGMA foreign_keys` is silently ignored inside a transaction, so a guard that
does nothing looks exactly like one that works. Every test here asserts the
pragma's *value* or the rebuild's *effect*, never that a statement was issued.
"""

from __future__ import annotations

import pytest
from lore_eden.store.migration_utils import (
    add_columns_if_missing,
    column_is_nullable,
    index_exists,
    relax_not_null,
    table_columns,
    table_exists,
)
from lore_eden.store.migrations import Migration, foreign_keys_disabled, run_migrations
from lore_eden.store.sql import enforce_sqlite_foreign_keys
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine


@pytest.fixture
def session() -> Session:
    enforce_sqlite_foreign_keys()
    engine = create_engine("sqlite://")
    with Session(engine) as open_session:
        yield open_session


@pytest.fixture
def linked(session: Session) -> Session:
    """A parent with a NOT NULL column, and a child referencing it."""
    session.execute(text("CREATE TABLE parent (id TEXT PRIMARY KEY, note TEXT NOT NULL)"))
    session.execute(
        text("CREATE TABLE child (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parent(id))")
    )
    session.execute(text("CREATE INDEX parent_note ON parent (note)"))
    session.execute(text("INSERT INTO parent VALUES ('p1', 'hello')"))
    session.execute(text("INSERT INTO child VALUES ('c1', 'p1')"))
    session.commit()
    return session


class TestIntrospection:
    def test_an_existing_table_is_found(self, linked: Session) -> None:
        assert table_exists(linked, "parent")

    def test_an_absent_table_is_not(self, session: Session) -> None:
        assert not table_exists(session, "nope")

    def test_columns_are_listed(self, linked: Session) -> None:
        assert table_columns(linked, "parent") == {"id", "note"}

    def test_an_absent_table_has_no_columns_rather_than_raising(self, session: Session) -> None:
        """A migration guarding its own change asks before the table exists."""
        assert table_columns(session, "nope") == set()

    def test_an_index_is_found_and_a_missing_one_is_not(self, linked: Session) -> None:
        assert index_exists(linked, "parent_note")
        assert not index_exists(linked, "absent_index")

    def test_nullability_is_reported(self, linked: Session) -> None:
        assert not column_is_nullable(linked, "parent", "note")
        assert column_is_nullable(linked, "child", "parent_id")

    def test_an_absent_column_reads_as_nullable(self, linked: Session) -> None:
        """Which is what keeps `relax_not_null` a no-op on an already-done table."""
        assert column_is_nullable(linked, "parent", "gone")
        assert column_is_nullable(linked, "nope", "any")

    def test_a_quoted_identifier_does_not_break_introspection(self, session: Session) -> None:
        session.execute(text('CREATE TABLE "odd name" (id TEXT PRIMARY KEY)'))
        session.commit()

        assert table_exists(session, "odd name")
        assert table_columns(session, "odd name") == {"id"}


class TestAddColumnsIfMissing:
    def test_a_missing_column_is_added(self, linked: Session) -> None:
        add_columns_if_missing(
            linked, "parent", {"extra": "ALTER TABLE parent ADD COLUMN extra TEXT"}
        )

        assert "extra" in table_columns(linked, "parent")

    def test_running_it_twice_is_harmless(self, linked: Session) -> None:
        statement = {"extra": "ALTER TABLE parent ADD COLUMN extra TEXT"}
        add_columns_if_missing(linked, "parent", statement)
        add_columns_if_missing(linked, "parent", statement)

        assert "extra" in table_columns(linked, "parent")

    def test_an_absent_table_is_skipped_rather_than_erroring(self, session: Session) -> None:
        add_columns_if_missing(session, "nope", {"x": "ALTER TABLE nope ADD COLUMN x TEXT"})


class TestForeignKeysDisabled:
    def test_enforcement_is_actually_off_inside_the_block(self, linked: Session) -> None:
        """Asserting the pragma's value, because issuing it inside a transaction
        succeeds and changes nothing."""
        with foreign_keys_disabled(linked):
            assert linked.execute(text("PRAGMA foreign_keys")).scalar() == 0

    def test_enforcement_is_restored_afterwards(self, linked: Session) -> None:
        with foreign_keys_disabled(linked):
            pass

        assert linked.execute(text("PRAGMA foreign_keys")).scalar() == 1

    def test_restoration_actually_rejects_a_bad_reference(self, linked: Session) -> None:
        """The pragma reading 1 is not proof; enforcement has to bite."""
        with foreign_keys_disabled(linked):
            pass

        with pytest.raises(IntegrityError):
            linked.execute(text("INSERT INTO child VALUES ('c2', 'absent')"))
            linked.commit()
        linked.rollback()

    def test_enforcement_is_restored_after_a_failure_inside_the_block(
        self, linked: Session
    ) -> None:
        with pytest.raises(RuntimeError):
            with foreign_keys_disabled(linked):
                raise RuntimeError("migration blew up")

        assert linked.execute(text("PRAGMA foreign_keys")).scalar() == 1

    def test_a_host_running_with_enforcement_off_keeps_it_off(self, session: Session) -> None:
        """Restoring the prior value, not hardcoding ON — applying a migration
        must not switch on a check the host chose to run without."""
        session.commit()
        session.execute(text("PRAGMA foreign_keys=OFF"))

        with foreign_keys_disabled(session):
            pass

        assert session.execute(text("PRAGMA foreign_keys")).scalar() == 0


class TestRelaxNotNull:
    def test_the_constraint_is_dropped(self, linked: Session) -> None:
        with foreign_keys_disabled(linked):
            relax_not_null(linked, "parent", "note")

        assert column_is_nullable(linked, "parent", "note")

    def test_a_null_can_then_be_written(self, linked: Session) -> None:
        """The point of the exercise, not just the schema reading differently."""
        with foreign_keys_disabled(linked):
            relax_not_null(linked, "parent", "note")

        linked.execute(text("INSERT INTO parent VALUES ('p2', NULL)"))
        linked.commit()

        assert linked.execute(text("SELECT note FROM parent WHERE id='p2'")).scalar() is None

    def test_existing_rows_survive(self, linked: Session) -> None:
        with foreign_keys_disabled(linked):
            relax_not_null(linked, "parent", "note")

        assert linked.execute(text("SELECT note FROM parent WHERE id='p1'")).scalar() == "hello"

    def test_the_primary_key_survives(self, linked: Session) -> None:
        with foreign_keys_disabled(linked):
            relax_not_null(linked, "parent", "note")

        with pytest.raises(IntegrityError):
            linked.execute(text("INSERT INTO parent VALUES ('p1', 'duplicate')"))
            linked.commit()
        linked.rollback()

    def test_indexes_are_replayed(self, linked: Session) -> None:
        """A dropped table takes its indexes; silently losing one turns a fast
        query slow with nothing broken to notice."""
        with foreign_keys_disabled(linked):
            relax_not_null(linked, "parent", "note")

        assert index_exists(linked, "parent_note")

    def test_other_columns_keep_their_constraints(self, session: Session) -> None:
        session.execute(
            text("CREATE TABLE t (id TEXT PRIMARY KEY, a TEXT NOT NULL, b TEXT NOT NULL)")
        )
        session.commit()

        with foreign_keys_disabled(session):
            relax_not_null(session, "t", "a")

        assert column_is_nullable(session, "t", "a")
        assert not column_is_nullable(session, "t", "b")

    def test_a_default_is_preserved(self, session: Session) -> None:
        session.execute(
            text("CREATE TABLE t (id TEXT PRIMARY KEY, a TEXT NOT NULL DEFAULT 'x')")
        )
        session.commit()

        with foreign_keys_disabled(session):
            relax_not_null(session, "t", "a")

        session.execute(text("INSERT INTO t (id) VALUES ('r1')"))
        session.commit()

        assert session.execute(text("SELECT a FROM t WHERE id='r1'")).scalar() == "x"

    def test_an_outgoing_foreign_key_is_restated(self, linked: Session) -> None:
        """A rebuild drops the table's own FKs unless they are put back."""
        with foreign_keys_disabled(linked):
            relax_not_null(linked, "child", "id")

        keys = linked.execute(text("PRAGMA foreign_key_list(child)")).fetchall()

        assert [(row[2], row[3], row[4]) for row in keys] == [("parent", "parent_id", "id")]

    def test_a_referencing_table_keeps_its_rows(self, linked: Session) -> None:
        with foreign_keys_disabled(linked):
            relax_not_null(linked, "parent", "note")

        assert linked.execute(text("SELECT id FROM child")).fetchall() == [("c1",)]
        assert linked.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    def test_it_is_a_no_op_when_already_nullable(self, linked: Session) -> None:
        with foreign_keys_disabled(linked):
            relax_not_null(linked, "child", "parent_id")
            relax_not_null(linked, "child", "parent_id")

        assert column_is_nullable(linked, "child", "parent_id")

    def test_it_is_a_no_op_on_an_absent_table(self, session: Session) -> None:
        with foreign_keys_disabled(session):
            relax_not_null(session, "nope", "column")

    def test_it_fails_loudly_with_enforcement_on(self, linked: Session) -> None:
        """Not a supported call, and it must not half-succeed silently.

        This is the state the library shipped in: the migrations docstring
        promised enforcement was off while nothing turned it off.
        """
        with pytest.raises(IntegrityError):
            relax_not_null(linked, "parent", "note")
        linked.rollback()


class TestRunMigrationsProvidesTheGuarantee:
    def test_a_rebuild_migration_can_run(self, linked: Session) -> None:
        """The end-to-end claim: this failed outright before the guard existed."""

        def relax(session: Session) -> None:
            relax_not_null(session, "parent", "note")

        applied = run_migrations(linked, [Migration("t1", "relax parent.note", relax)])

        assert applied == ["t1"]
        assert column_is_nullable(linked, "parent", "note")

    def test_enforcement_is_back_on_after_the_run(self, linked: Session) -> None:
        run_migrations(linked, [Migration("t1", "nothing", lambda _session: None)])

        assert linked.execute(text("PRAGMA foreign_keys")).scalar() == 1
