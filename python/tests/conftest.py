"""A database containing nothing but this package's own table.

The registry is DB-backed, so the tests need a real one. They create the schema
from `lore_eden`'s metadata alone — if anything here depended on a host
application's tables, `create_all` would not be able to build it.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Imported for its side effect: registering the table on SQLModel.metadata.
from lore_eden.mcp.servers.models import McpServerRecord  # noqa: F401
from lore_eden.store.sql import enforce_sqlite_foreign_keys
from sqlmodel import Session, SQLModel, create_engine

# SQLite ignores foreign keys unless asked, per connection. The library's own
# registration is used rather than a copy here: this file had one, it fired on
# every engine of every dialect, and the Postgres conformance pass died on
# `syntax error at or near "PRAGMA"` in the test harness as well as in the
# library. Two copies of a rule is two places for it to be wrong.
enforce_sqlite_foreign_keys()


@pytest.fixture
def session(tmp_path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session


# The profiling plugin lives with the gate library: it is standard-library only
# and shared by both suites, and duplicating it here would be a second copy to
# keep in step. Reached by path rather than by install, the same way the gates
# themselves are run.
_GATES_DIR = Path(__file__).resolve().parents[2] / "gates" / "lore_eden_gates"
if str(_GATES_DIR) not in sys.path:
    sys.path.insert(0, str(_GATES_DIR))


def pytest_configure(config):
    """Profile this run when `LORE_EDEN_PROFILE` asks for it; otherwise do nothing."""
    import pytest_profile

    pytest_profile.register(config, suite="python")
