"""A database containing nothing but this package's own table.

The registry is DB-backed, so the tests need a real one. They create the schema
from `lore_eden`'s metadata alone — if anything here depended on a host
application's tables, `create_all` would not be able to build it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

# Imported for its side effect: registering the table on SQLModel.metadata.
from lore_eden.mcp.servers.models import McpServerRecord  # noqa: F401


@event.listens_for(Engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """SQLite ignores foreign keys unless asked, per connection.

    Registered on the Engine class so every engine a test builds gets it, rather
    than each test remembering to.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture
def session(tmp_path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
