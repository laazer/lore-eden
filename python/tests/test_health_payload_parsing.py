"""Reading a third-party server's answers.

These payloads arrive from a server this package does not control, so the
parsing is a boundary and gets modelled rather than poked at. The cases below
pin the judgements that modelling had to preserve — each one is a way a server
can answer that must not be mistaken for health.
"""

from __future__ import annotations

import pytest
from lore_eden.mcp.servers.health import (
    _handshake_name,
    _looks_like_initialize_result,
    _tool_names,
)

INITIALIZE_OK = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "docs-server", "version": "2.0"},
    },
}


def test_a_real_initialize_result_is_recognised():
    assert _looks_like_initialize_result(INITIALIZE_OK) is True
    assert _handshake_name(INITIALIZE_OK) == "docs-server"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "no"}},
                     id="jsonrpc-error"),
        pytest.param({"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}},
                     id="200-from-something-else"),
        pytest.param({"jsonrpc": "2.0", "id": 1, "result": "fine"}, id="result-not-an-object"),
        pytest.param({"jsonrpc": "2.0", "id": 1}, id="no-result-at-all"),
        pytest.param({}, id="empty"),
    ],
)
def test_things_that_are_not_an_initialize_result(payload):
    """A URL that returns 200 for anything is not a working MCP server."""
    assert _looks_like_initialize_result(payload) is False


def test_a_present_but_null_handshake_field_still_counts_as_an_answer():
    """Presence, not truthiness — the key-membership test this replaced treated
    an explicit null as an answer, and a server sending one has still responded
    to the question."""
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": None}}

    assert _looks_like_initialize_result(payload) is True


def test_a_handshake_without_server_info_yields_an_empty_name():
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}

    assert _looks_like_initialize_result(payload) is True
    assert _handshake_name(payload) == ""


def test_tool_names_are_read_from_a_listing():
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"tools": [{"name": "search"}, {"name": "fetch", "description": "x"}]},
    }

    assert _tool_names(payload) == ["search", "fetch"]


def test_one_malformed_tool_does_not_discard_the_others():
    """A server that emits one odd entry still exposes the tools around it."""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"tools": [{"name": "search"}, "not-a-tool", {"no": "name"}, {"name": "fetch"}]},
    }

    assert _tool_names(payload) == ["search", "fetch"]


def test_an_empty_listing_is_an_empty_list_not_unknown():
    """"This server exposes nothing" is a real answer."""
    assert _tool_names({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}) == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="never-asked"),
        pytest.param({"error": {"code": -1, "message": "refused"}}, id="refused"),
        pytest.param({"result": {"tools": "lots"}}, id="tools-not-a-list"),
        pytest.param({"result": {}}, id="no-tools-key"),
        pytest.param({"result": None}, id="null-result"),
    ],
)
def test_an_unusable_listing_is_unknown_rather_than_zero(payload):
    """None and [] are different answers. A gateway shows a tool count, and "0
    tools" claimed from a failed call is a number an operator would act on."""
    assert _tool_names(payload) is None
