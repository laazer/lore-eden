"""Registering third-party MCP servers, and the client config that reaches them."""

from __future__ import annotations

import pytest
from lore_eden.mcp.servers import (
    McpRegistryError,
    McpServerCreate,
    McpServerUpdate,
    client_server_entries,
    create_server,
    delete_server,
    enabled_server_names,
    list_servers,
    to_view,
    update_server,
)

HTTP = McpServerCreate(name="docs", transport="http", url="https://example.invalid/mcp")
STDIO = McpServerCreate(
    name="local", transport="stdio", command="/usr/bin/thing", args=["--serve", "--quiet"]
)


def test_registering_a_server_makes_it_listable(session):
    created = create_server(session, HTTP)

    assert created.id
    assert [server.name for server in list_servers(session)] == ["docs"]


def test_servers_list_in_name_order(session):
    create_server(session, STDIO)
    create_server(session, HTTP)

    assert [server.name for server in list_servers(session)] == ["docs", "local"]


def test_http_entry_carries_the_url(session):
    create_server(session, HTTP)

    assert client_server_entries(session) == {
        "docs": {"type": "http", "url": "https://example.invalid/mcp"}
    }


def test_stdio_entry_carries_command_and_args(session):
    create_server(session, STDIO)

    assert client_server_entries(session)["local"] == {
        "type": "stdio",
        "command": "/usr/bin/thing",
        "args": ["--serve", "--quiet"],
    }


def test_a_credential_is_read_from_the_environment_not_the_database(session, monkeypatch):
    """The database gets copied into scratch dirs and worktrees. A token at rest
    in it would travel with every copy."""
    monkeypatch.setenv("DOCS_TOKEN", "s3cret")
    create_server(
        session,
        McpServerCreate(
            name="docs", transport="http", url="https://example.invalid/mcp",
            auth_env_var="DOCS_TOKEN",
        ),
    )

    stored = list_servers(session)[0]
    assert stored.auth_env_var == "DOCS_TOKEN"
    assert "s3cret" not in repr(stored.__dict__)

    entry = client_server_entries(session)["docs"]
    assert entry["headers"] == {"Authorization": "Bearer s3cret"}


def test_an_unset_credential_yields_no_auth_header(session, monkeypatch):
    monkeypatch.delenv("DOCS_TOKEN", raising=False)
    create_server(
        session,
        McpServerCreate(
            name="docs", transport="http", url="https://example.invalid/mcp",
            auth_env_var="DOCS_TOKEN",
        ),
    )

    assert "headers" not in client_server_entries(session)["docs"]


def test_stdio_passes_the_variable_by_name(session, monkeypatch):
    """The child reads the value itself; the config names the variable."""
    monkeypatch.setenv("LOCAL_TOKEN", "abc")
    create_server(
        session,
        McpServerCreate(
            name="local", transport="stdio", command="/usr/bin/thing",
            auth_env_var="LOCAL_TOKEN",
        ),
    )

    assert client_server_entries(session)["local"]["env"] == {"LOCAL_TOKEN": "abc"}


def test_a_disabled_server_is_withheld_not_forgotten(session):
    """Parking a misbehaving server must not lose how it was configured."""
    created = create_server(session, HTTP)
    update_server(session, created.id, McpServerUpdate(enabled=False))

    assert client_server_entries(session) == {}
    assert enabled_server_names(session) == frozenset()
    stored = list_servers(session)[0]
    assert stored.url == "https://example.invalid/mcp"


def test_enabled_names_match_what_the_config_offers(session):
    create_server(session, HTTP)
    disabled = create_server(session, STDIO)
    update_server(session, disabled.id, McpServerUpdate(enabled=False))

    assert enabled_server_names(session) == frozenset({"docs"})
    assert set(client_server_entries(session)) == {"docs"}


@pytest.mark.parametrize(
    "body, message",
    [
        (McpServerCreate(name="  "), "name is required"),
        (McpServerCreate(name="x", transport="carrier-pigeon"), "Unknown transport"),
        (McpServerCreate(name="x", transport="http", url=""), "http server needs a url"),
        (McpServerCreate(name="x", transport="stdio", command=""), "stdio server needs a command"),
        (
            McpServerCreate(name="x", transport="http", url="u", tool_policy="whatever"),
            "Unknown tool policy",
        ),
    ],
)
def test_a_server_missing_what_its_transport_needs_is_refused(session, body, message):
    """It would register cleanly and then fail inside an agent subprocess, where
    the cause is hard to see."""
    with pytest.raises(McpRegistryError, match=message):
        create_server(session, body)


def test_duplicate_names_are_refused(session):
    """The name is the key under `mcpServers`, so a clash would shadow one."""
    create_server(session, HTTP)

    with pytest.raises(McpRegistryError, match="already registered"):
        create_server(session, HTTP)


def test_renaming_onto_another_server_is_refused(session):
    create_server(session, HTTP)
    other = create_server(session, STDIO)

    with pytest.raises(McpRegistryError, match="already registered"):
        update_server(session, other.id, McpServerUpdate(name="docs"))


def test_updating_an_unknown_server_is_refused(session):
    with pytest.raises(McpRegistryError, match="not found"):
        update_server(session, "no-such-id", McpServerUpdate(enabled=False))


def test_deleting_removes_it(session):
    created = create_server(session, HTTP)

    delete_server(session, created.id)

    assert list_servers(session) == []


def test_deleting_an_unknown_server_is_refused(session):
    with pytest.raises(McpRegistryError, match="not found"):
        delete_server(session, "no-such-id")


def test_an_update_cannot_leave_the_record_invalid(session):
    """Validation runs on the merged result, not just the incoming fields."""
    created = create_server(session, HTTP)

    with pytest.raises(McpRegistryError, match="stdio server needs a command"):
        update_server(session, created.id, McpServerUpdate(transport="stdio"))


def test_a_negative_rate_limit_is_floored_at_zero(session):
    created = create_server(session, HTTP)

    updated = update_server(session, created.id, McpServerUpdate(rate_limit_per_min=-5))

    assert updated.rate_limit_per_min == 0


def test_the_view_reports_credential_presence_without_the_value(session, monkeypatch):
    monkeypatch.setenv("DOCS_TOKEN", "s3cret")
    created = create_server(
        session,
        McpServerCreate(
            name="docs", transport="http", url="https://example.invalid/mcp",
            auth_env_var="DOCS_TOKEN",
        ),
    )

    view = to_view(created)

    assert view.auth_present is True
    assert "s3cret" not in view.model_dump_json()


def test_the_view_reports_a_missing_credential(session, monkeypatch):
    monkeypatch.delenv("DOCS_TOKEN", raising=False)
    created = create_server(
        session,
        McpServerCreate(
            name="docs", transport="http", url="https://example.invalid/mcp",
            auth_env_var="DOCS_TOKEN",
        ),
    )

    assert to_view(created).auth_present is False


def test_never_checked_is_distinct_from_checked_and_failing(session):
    """A UI that renders them the same way is lying about one of them."""
    created = create_server(session, HTTP)

    view = to_view(created)

    assert view.last_checked_at == ""
    assert view.last_health_ok is False
    assert view.tools_listed_at == ""


def test_a_corrupt_args_column_reads_as_empty_rather_than_raising(session):
    """An older build or an operator writing the column badly should not turn
    every read of the table into a 500."""
    created = create_server(session, STDIO)
    created.args_json = "{not json"
    session.add(created)
    session.commit()

    assert to_view(created).args == []
    assert client_server_entries(session)["local"]["args"] == []
