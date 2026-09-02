"""The JSON-RPC handler, driven directly — no HTTP, no database.

If any of this needed either, the transport would not be reusable, which is the
whole claim being made.
"""

from __future__ import annotations

import pytest
from lore_eden.mcp import (
    DuplicateToolError,
    McpServer,
    ServerInfo,
    ToolDefinition,
    ToolRegistry,
)
from lore_eden.mcp.protocol import METHOD_NOT_FOUND, PROTOCOL_VERSION


@pytest.fixture
def registry() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.tool(
        "greet",
        "Greet somebody by name.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    )
    def greet(context, arguments):
        return f"hello {arguments['name']}"

    @tools.tool("boom", "Always fails.")
    def boom(context, arguments):
        raise RuntimeError("the tool broke")

    @tools.tool("echo_context", "Return whatever context the host supplied.")
    def echo_context(context, arguments):
        return str(context)

    return tools


@pytest.fixture
def server(registry: ToolRegistry) -> McpServer:
    return McpServer(ServerInfo(name="test-harness", version="9.9.9"), registry)


def call(server: McpServer, method: str, params=None, request_id=1, context=None):
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    return server.handle_request(request, context)


def test_initialize_reports_protocol_and_server_identity(server):
    result = call(server, "initialize")["result"]

    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"] == {"name": "test-harness", "version": "9.9.9"}
    assert "tools" in result["capabilities"]


def test_tools_list_returns_the_registered_catalogue(server):
    tools = call(server, "tools/list")["result"]["tools"]

    assert [tool["name"] for tool in tools] == ["greet", "boom", "echo_context"]
    greet = tools[0]
    assert greet["description"] == "Greet somebody by name."
    # The protocol spells it inputSchema; the registry stores input_schema. That
    # rename is the only translation the definition does, so it is worth pinning.
    assert greet["inputSchema"]["required"] == ["name"]


def test_tools_call_returns_the_handler_result(server):
    result = call(server, "tools/call", {"name": "greet", "arguments": {"name": "ada"}})["result"]

    assert result["content"] == [{"type": "text", "text": "hello ada"}]
    assert "isError" not in result


def test_a_raising_tool_is_a_failed_call_not_a_dead_connection(server):
    """MCP models tool failure in the result rather than as a JSON-RPC error,
    because the model on the other end is expected to read it and try again."""
    result = call(server, "tools/call", {"name": "boom"})["result"]

    assert result["isError"] is True
    assert "the tool broke" in result["content"][0]["text"]


def test_unknown_tool_says_what_is_registered(server):
    result = call(server, "tools/call", {"name": "nope"})["result"]

    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "Unknown tool: nope" in text
    assert "greet" in text


def test_context_reaches_the_handler_untouched(server):
    """The context is the seam that lets one transport serve unrelated tools, so
    this package must pass it through without inspecting it."""
    sentinel = object()

    result = call(server, "tools/call", {"name": "echo_context"}, context=sentinel)["result"]

    assert str(sentinel) in result["content"][0]["text"]


def test_ping_is_answered(server):
    assert call(server, "ping")["result"] == {}


def test_initialized_notification_takes_no_response(server):
    """Answering a notification is a protocol error."""
    assert server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}, None) is None


def test_unknown_method_is_a_jsonrpc_error(server):
    error = call(server, "does/not/exist")["error"]

    assert error["code"] == METHOD_NOT_FOUND
    assert "does/not/exist" in error["message"]


def test_batch_messages_answer_in_order_and_drop_notifications(server):
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "greet", "arguments": {"name": "grace"}}},
    ]

    responses = server.handle_message(batch, None)

    assert [r["id"] for r in responses] == [1, 2]
    assert responses[1]["result"]["content"][0]["text"] == "hello grace"


def test_a_notification_alone_still_returns_a_body(server):
    """The transport owes the caller JSON even when the protocol owes nothing."""
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, None) == {}


def test_a_non_object_body_is_refused(server):
    with pytest.raises(ValueError, match="Invalid MCP message body"):
        server.handle_message("not a message", None)


def test_registering_a_duplicate_name_is_refused(registry):
    """Silently replacing a tool means the call that used to work now runs
    someone else's code, and nothing says so."""
    with pytest.raises(DuplicateToolError, match="greet"):
        registry.register(ToolDefinition("greet", "a different greet"), lambda ctx, args: "")
