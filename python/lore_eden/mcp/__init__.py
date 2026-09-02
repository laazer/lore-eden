"""MCP transport and the registry of servers a host makes reachable.

Two separate concerns that are easy to confuse by name:

- :mod:`lore_eden.mcp.tools` and :mod:`lore_eden.mcp.protocol` — *serving* MCP.
  A tool registry, and a JSON-RPC handler that dispatches to it.
- :mod:`lore_eden.mcp.servers` — *consuming* MCP. Which third-party servers this
  host knows about, whether they answer, and the client config that reaches them.
"""

from lore_eden.mcp.protocol import PROTOCOL_VERSION, McpServer, ServerInfo
from lore_eden.mcp.router import make_mcp_router
from lore_eden.mcp.tools import (
    DuplicateToolError,
    ToolDefinition,
    ToolError,
    ToolHandler,
    ToolRegistry,
    UnknownToolError,
)

__all__ = [
    "PROTOCOL_VERSION",
    "DuplicateToolError",
    "McpServer",
    "ServerInfo",
    "ToolDefinition",
    "ToolError",
    "ToolHandler",
    "ToolRegistry",
    "UnknownToolError",
    "make_mcp_router",
]
