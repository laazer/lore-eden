"""A real MCP server on stdio, built from this package's own protocol handler.

Used by the health-check tests. Serving with `McpServer` rather than a
hand-written script means the test exercises both halves of this package against
each other — the transport answering, and the health checker dialling — instead
of asserting that a fixture agrees with itself.
"""

from __future__ import annotations

import json
import sys

from lore_eden.mcp import McpServer, ServerInfo, ToolRegistry


def main() -> int:
    tools = ToolRegistry()

    @tools.tool("lookup", "Look something up.")
    def lookup(context, arguments):
        return "found it"

    @tools.tool("summarize", "Summarize some text.")
    def summarize(context, arguments):
        return "summary"

    server = McpServer(ServerInfo(name="fake-stdio", version="1.2.3"), tools)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = server.handle_request(json.loads(line), None)
        # A notification gets no reply; writing one would be a protocol error.
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
