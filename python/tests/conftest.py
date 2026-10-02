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
from lore_eden.instances import (
    CommandTemplate,
    FileInstanceRegistry,
    InstanceKind,
    InstanceManager,
    TemplateCatalog,
    TemplateParam,
)

# Imported for its side effect: registering the table on SQLModel.metadata.
from lore_eden.mcp.servers.models import McpServerRecord  # noqa: F401
from lore_eden.store.sql import enforce_sqlite_foreign_keys
from lore_eden.testing import pytest_profile
from sqlmodel import Session, SQLModel, create_engine

# SQLite ignores foreign keys unless asked, per connection. The library's own
# registration is used rather than a copy here: this file had one, it fired on
# every engine of every dialect, and the Postgres conformance pass died on
# `syntax error at or near "PRAGMA"` in the test harness as well as in the
# library. Two copies of a rule is two places for it to be wrong.
enforce_sqlite_foreign_keys()


def pytest_configure(config):
    """Profile this run when `LORE_EDEN_PROFILE` asks for it; otherwise do nothing."""
    pytest_profile.register(config, suite="python")


@pytest.fixture
def session(tmp_path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session


FAKE_INSTANCE_SERVER = Path(__file__).resolve().parent / "fake_instance_server.py"


@pytest.fixture
def instance_registry(tmp_path: Path) -> FileInstanceRegistry:
    return FileInstanceRegistry(tmp_path / "registry")


@pytest.fixture
def instance_manager(instance_registry: FileInstanceRegistry, tmp_path: Path) -> Iterator[InstanceManager]:
    """A manager with one template, ``api``, launching `fake_instance_server.py`.

    Its ``mode`` parameter picks how the fake behaves. Everything launched is
    stopped afterwards, so a failing test cannot leave a server holding a port.
    """
    catalog = TemplateCatalog()
    catalog.register(
        CommandTemplate(
            project="demo",
            name="api",
            kind=InstanceKind.SERVER,
            command=[sys.executable, str(FAKE_INSTANCE_SERVER), "{param:mode}"],
            cwd=str(tmp_path),
            params=[
                TemplateParam(
                    key="mode",
                    label="Mode",
                    default="--ok",
                    choices=["--ok", "--exit=3", "--never-ready", "--ignore-term"],
                )
            ],
            ready_timeout_seconds=15,
        )
    )
    built = InstanceManager(instance_registry, catalog)
    yield built
    for view in built.list().instances:
        if view.managed:
            built.stop(view.id)
