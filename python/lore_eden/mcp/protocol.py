"""MCP JSON-RPC protocol handling, over a tool registry the host supplies.

Transport only. This module knows about ``initialize``, ``tools/list``,
``tools/call``, ``ping`` and the notification the handshake requires — and
nothing whatsoever about what any tool does. What it dispatches to is a
:class:`~lore_eden.mcp.tools.ToolRegistry`, and the per-request ``context`` it
threads through is opaque.

That separation is what makes the transport reusable. The version this was
extracted from imported one application's tool table at module scope, so serving
a different set of tools meant editing the protocol handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lore_eden.mcp.tools import ToolRegistry

#: The MCP revision this handler implements.
PROTOCOL_VERSION = "2024-11-05"

#: JSON-RPC's own code for a method the server does not implement.
METHOD_NOT_FOUND = -32601


@dataclass(frozen=True)
class ServerInfo:
    """How the server identifies itself during the handshake."""

    name: str
    version: str = "0.1.0"

    def as_payload(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}


class McpServer:
    """A JSON-RPC endpoint serving one tool registry.

    Holds no connection and no state beyond its registry: a request carries its
    own context, so one instance safely serves concurrent requests belonging to
    different callers.
    """

    def __init__(self, server_info: ServerInfo, tools: ToolRegistry) -> None:
        self.server_info = server_info
        self.tools = tools

    def info_payload(self) -> dict[str, Any]:
        """A human-readable description, for a GET on the endpoint."""
        return {
            "transport": "streamable-http",
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": self.server_info.as_payload(),
            "usage": (
                "POST JSON-RPC messages to this URL "
                "(initialize, tools/list, tools/call, ping)."
            ),
        }

    def handle_request(self, request: dict[str, Any], context: Any) -> dict[str, Any] | None:
        """One JSON-RPC request. ``None`` means the message takes no response."""
        method = request.get("method")
        request_id = request.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": self.server_info.as_payload(),
                },
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": self.tools.definitions()},
            }

        if method == "tools/call":
            return self._call_tool(request, request_id, context)

        # The client announces it finished initializing. Acknowledging it with a
        # response is a protocol error, so this is the one method that returns
        # nothing.
        if method == "notifications/initialized":
            return None

        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": METHOD_NOT_FOUND, "message": f"Method not found: {method}"},
        }

    def _call_tool(
        self, request: dict[str, Any], request_id: Any, context: Any
    ) -> dict[str, Any]:
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = self.tools.call(name, arguments, context)
        except Exception as exc:
            # Broad on purpose, and not a swallow: a tool that raises must reach
            # the client as a failed *call*, not as a dead connection, and the
            # message travels with it. MCP models tool failure in the result —
            # `isError` — rather than as a JSON-RPC error, because the model on
            # the other end is expected to read it and try something else.
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": result}]},
        }

    def handle_message(self, body: Any, context: Any) -> Any:
        """A single JSON-RPC object, or a batch array of them."""
        if isinstance(body, list):  # py-org: allow-isinstance (JSON-RPC wire shape)
            responses = []
            for item in body:
                if not isinstance(item, dict):  # py-org: allow-isinstance (same)
                    continue
                response = self.handle_request(item, context)
                if response is not None:
                    responses.append(response)
            return responses
        if isinstance(body, dict):  # py-org: allow-isinstance (same)
            response = self.handle_request(body, context)
            # A notification produces no response, but the transport still owes
            # the caller a JSON body.
            return response if response is not None else {}
        raise ValueError("Invalid MCP message body: expected a JSON object or array")
