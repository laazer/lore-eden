"""Third-party MCP servers this host knows about, and whether they answer."""

from lore_eden.mcp.servers.health import HealthResult, check_server, record_health
from lore_eden.mcp.servers.models import (
    POLICY_AUTO,
    POLICY_PROMPT,
    TOOL_POLICIES,
    TRANSPORTS,
    McpServerCreate,
    McpServerRecord,
    McpServerUpdate,
    McpServerView,
)
from lore_eden.mcp.servers.registry import (
    McpRegistryError,
    client_server_entries,
    create_server,
    delete_server,
    enabled_server_names,
    list_servers,
    to_view,
    update_server,
)

__all__ = [
    "POLICY_AUTO",
    "POLICY_PROMPT",
    "TOOL_POLICIES",
    "TRANSPORTS",
    "HealthResult",
    "McpRegistryError",
    "McpServerCreate",
    "McpServerRecord",
    "McpServerUpdate",
    "McpServerView",
    "check_server",
    "client_server_entries",
    "create_server",
    "delete_server",
    "enabled_server_names",
    "list_servers",
    "record_health",
    "to_view",
    "update_server",
]
