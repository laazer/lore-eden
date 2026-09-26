"""Copy-on-write row history over plain sqlite3."""

from __future__ import annotations

import sqlite3

import pytest
from lore_eden.store.row_history import RowHistory

HISTORY = RowHistory(table="note_versions", key_column="note_id", columns=("title", "body"), keep=3)


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    HISTORY.ensure_schema(connection)
    yield connection
    connection.close()


def _update(conn, before, after, stamp="t"):
    return HISTORY.record_superseded(
        conn,
        key="n1",
        prior={"title": "T", "body": before},
        incoming={"title": "T", "body": after},
        superseded_at=stamp,
    )


def test_an_update_keeps_the_prior_version(conn):
    assert _update(conn, "one", "two", stamp="s1") == 1
    (row,) = HISTORY.versions(conn, "n1")
    assert (row["version"], row["body"], row["superseded_at"]) == (1, "one", "s1")


def test_an_unchanged_write_records_nothing(conn):
    assert _update(conn, "same", "same") is None
    assert HISTORY.versions(conn, "n1") == []


def test_unknown_attribution_is_null(conn):
    _update(conn, "one", "two")
    (row,) = HISTORY.versions(conn, "n1")
    assert (row["superseded_by"], row["change_note"], row["became_current_at"]) == (
        None,
        None,
        None,
    )


def test_growth_is_bounded_and_numbering_continues(conn):
    for index in range(7):
        _update(conn, f"v{index}", f"v{index + 1}")
    versions = HISTORY.versions(conn, "n1")
    assert [v["version"] for v in versions] == [5, 6, 7]
    assert [v["body"] for v in versions] == ["v4", "v5", "v6"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"table": "x; DROP TABLE y", "key_column": "k", "columns": ("a",)},
        {"table": "t", "key_column": "k", "columns": ("version",)},
        {"table": "t", "key_column": "k", "columns": ("a",), "keep": 0},
    ],
)
def test_unsafe_or_colliding_definitions_are_refused(kwargs):
    with pytest.raises(ValueError):
        RowHistory(**kwargs)
