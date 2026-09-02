"""The FastAPI mount, on an app that is nothing but this router.

The point of the test is what is *absent*: no database, no settings module, no
application of any kind beyond `FastAPI()`. The version this was extracted from
could not do this — its router imported a specific app's session dependency at
module scope.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lore_eden.mcp import McpServer, ServerInfo, ToolRegistry, make_mcp_router
from lore_eden.mcp.protocol import PROTOCOL_VERSION


def build_app(context_dependency=None) -> FastAPI:
    tools = ToolRegistry()

    @tools.tool("shout", "Upper-case the text.")
    def shout(context, arguments):
        prefix = f"[{context}] " if context is not None else ""
        return prefix + str(arguments.get("text", "")).upper()

    server = McpServer(ServerInfo(name="bare-app"), tools)
    app = FastAPI()
    app.include_router(make_mcp_router(server, context_dependency), prefix="/mcp")
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app())


def rpc(client: TestClient, method: str, params=None, request_id=1):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    response = client.post("/mcp", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_full_handshake_round_trip(client):
    """initialize -> initialized -> tools/list -> tools/call -> ping, the
    sequence a real client actually performs."""
    initialize = rpc(client, "initialize")["result"]
    assert initialize["protocolVersion"] == PROTOCOL_VERSION
    assert initialize["serverInfo"]["name"] == "bare-app"

    notified = client.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert notified.status_code == 200
    assert notified.json() == {}

    tools = rpc(client, "tools/list")["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["shout"]

    called = rpc(client, "tools/call", {"name": "shout", "arguments": {"text": "hi"}})["result"]
    assert called["content"] == [{"type": "text", "text": "HI"}]

    assert rpc(client, "ping")["result"] == {}


def test_get_describes_the_endpoint(client):
    payload = client.get("/mcp").json()

    assert payload["serverInfo"]["name"] == "bare-app"
    assert payload["transport"] == "streamable-http"
    assert "tools/call" in payload["usage"]


def test_context_dependency_is_resolved_per_request():
    """A session opened for one call must not be shared with the next."""
    issued = []

    def context_dependency():
        issued.append(len(issued))
        return f"ctx{len(issued) - 1}"

    client = TestClient(build_app(context_dependency))

    first = rpc(client, "tools/call", {"name": "shout", "arguments": {"text": "a"}})
    second = rpc(client, "tools/call", {"name": "shout", "arguments": {"text": "b"}})

    assert first["result"]["content"][0]["text"] == "[ctx0] A"
    assert second["result"]["content"][0]["text"] == "[ctx1] B"


def test_a_batch_is_answered_as_an_array(client):
    response = client.post(
        "/mcp",
        json=[
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
    )

    payload = response.json()
    assert [item["id"] for item in payload] == [1, 2]


def test_a_body_that_is_not_json_is_a_parse_error_not_a_tool_failure(client):
    """It never reached a tool, so reporting it as one would be a lie about
    where the failure was."""
    response = client.post(
        "/mcp", content=b"{not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700
