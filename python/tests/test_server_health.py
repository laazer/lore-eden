"""Health checks: does a registered server actually answer MCP?

The check is a real `initialize` handshake rather than a ping. A URL that
returns 200 for anything, or a command that starts and does nothing, is not a
working MCP server, and a check that called either healthy would be worse than
no check at all — so these tests assert on that distinction specifically.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from lore_eden.mcp.servers import (
    McpServerCreate,
    check_server,
    create_server,
    record_health,
    to_view,
)
from lore_eden.mcp.servers.models import McpServerRecord

FAKE_SERVER = Path(__file__).resolve().parent / "fake_stdio_server.py"


def stdio_record(**overrides) -> McpServerRecord:
    fields = {
        "name": "fake",
        "transport": "stdio",
        "command": sys.executable,
        "args_json": json.dumps([str(FAKE_SERVER)]),
    }
    fields.update(overrides)
    return McpServerRecord(**fields)


def test_a_real_handshake_reports_healthy_and_lists_tools():
    """End to end against this package's own server, over a real subprocess."""
    result = check_server(stdio_record())

    assert result.ok, result.error
    assert result.error == ""
    assert result.server_name == "fake-stdio"
    assert result.tools == ["lookup", "summarize"]


def test_a_command_that_is_not_an_mcp_server_is_not_healthy():
    """`true` starts and exits cleanly. A check that called that healthy would
    be worse than no check."""
    result = check_server(stdio_record(command="/usr/bin/true", args_json="[]"))

    assert result.ok is False
    assert result.error


def test_a_command_that_prints_non_json_is_not_healthy():
    result = check_server(
        stdio_record(command="/bin/echo", args_json=json.dumps(["definitely not json"]))
    )

    assert result.ok is False


def test_a_missing_command_is_reported_as_such():
    result = check_server(stdio_record(command="/nonexistent/mcp-server", args_json="[]"))

    assert result.ok is False
    assert "not found" in result.error.lower()


def test_no_command_configured_is_reported_without_spawning():
    result = check_server(stdio_record(command="", args_json="[]"))

    assert result.ok is False
    assert "No command configured" in result.error


def test_a_missing_credential_is_named_before_dialling(monkeypatch):
    """The operator needs to know which variable to export."""
    monkeypatch.delenv("FAKE_TOKEN", raising=False)

    result = check_server(stdio_record(auth_env_var="FAKE_TOKEN"))

    assert result.ok is False
    assert "FAKE_TOKEN" in result.error


def test_an_unreachable_http_server_is_not_healthy():
    """Port 1 on localhost refuses immediately — no network egress needed."""
    result = check_server(
        McpServerRecord(name="dead", transport="http", url="http://127.0.0.1:1/mcp")
    )

    assert result.ok is False
    assert result.error


def test_check_server_never_raises():
    """A check that blew up would be indistinguishable from a server that is
    down, and the operator needs to know which."""
    result = check_server(McpServerRecord(name="bad", transport="http", url="not-a-url"))

    assert result.ok is False
    assert result.error


def test_recording_health_updates_the_record_and_the_view(session):
    created = create_server(
        session,
        McpServerCreate(
            name="fake", transport="stdio", command=sys.executable, args=[str(FAKE_SERVER)]
        ),
    )
    result = check_server(created)

    record_health(session, created, result)
    session.commit()

    view = to_view(created)
    assert view.last_health_ok is True
    assert view.last_checked_at != ""
    assert view.tools == ["lookup", "summarize"]
    assert view.tools_listed_at != ""


def test_recording_health_does_not_touch_updated_at(session):
    """A check observes the server, it does not change how it is configured.
    Bumping updated_at would make every check look like an edit."""
    created = create_server(
        session,
        McpServerCreate(name="fake", transport="http", url="http://127.0.0.1:1/mcp"),
    )
    before = created.updated_at

    record_health(session, created, check_server(created))
    session.commit()

    assert created.updated_at == before


def test_a_failed_check_records_why(session):
    created = create_server(
        session,
        McpServerCreate(name="dead", transport="http", url="http://127.0.0.1:1/mcp"),
    )

    record_health(session, created, check_server(created))
    session.commit()

    view = to_view(created)
    assert view.last_health_ok is False
    assert view.last_health_error != ""
    # Checked-and-failing, not never-checked. Those are different facts.
    assert view.last_checked_at != ""
