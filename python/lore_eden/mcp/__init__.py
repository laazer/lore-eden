"""MCP transport and the registry of servers a host makes reachable.

Two separate concerns that are easy to confuse by name:

- :mod:`lore_eden.mcp.tools` and :mod:`lore_eden.mcp.protocol` — *serving* MCP.
  A tool registry, and a JSON-RPC handler that dispatches to it.
- :mod:`lore_eden.mcp.servers` — *consuming* MCP. Which third-party servers this
  host knows about, whether they answer, and the client config that reaches them.

## Why the transports are imported lazily

There are two of them — :mod:`~lore_eden.mcp.router` for FastAPI and
:mod:`~lore_eden.mcp.django_router` for Django — and importing either from here
would put its web framework in the import path of the protocol handler, which
needs neither. A Django host would install FastAPI to reach ``McpServer``, and
once a second transport exists that trade only gets worse.

``__getattr__`` (PEP 562) defers each import to first use, so
``from lore_eden.mcp import make_mcp_router`` still reads as an ordinary import
while ``import lore_eden.mcp.protocol`` stays free of both frameworks. A test
asserts that, in a subprocess, with a control that would catch the import
machinery silently doing nothing.
"""

from typing import TYPE_CHECKING

from lore_eden.mcp.protocol import PROTOCOL_VERSION, McpServer, ServerInfo
from lore_eden.mcp.tools import (
    DuplicateToolError,
    ToolDefinition,
    ToolError,
    ToolHandler,
    ToolRegistry,
    UnknownToolError,
)

# PEP 562: bound rather than defined, because a package initialiser is for
# re-exporting, not for behaviour. What it does is in `transports`.
from lore_eden.mcp.transports import (
    load_transport as __getattr__,  # noqa: F401 - PEP 562 hook, not an unused import
)

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from lore_eden.mcp.django_router import make_mcp_django_view
    from lore_eden.mcp.router import make_mcp_router

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
    "make_mcp_django_view",
    "make_mcp_router",
]
