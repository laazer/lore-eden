"""Mount an :class:`McpServer` on a FastAPI app.

Separate from the protocol so the handler can be driven without an HTTP stack —
by a test, a stdio transport, or a host that routes with something other than
FastAPI. The transport is thin on purpose: it decodes a body, resolves a
context, and hands both to the protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from lore_eden.mcp.protocol import McpServer


def make_mcp_router(
    server: McpServer,
    context_dependency: Callable[..., Any] | None = None,
    *,
    tags: list[str] | None = None,
) -> APIRouter:
    """A router serving ``server`` at the path it is mounted on.

    ``context_dependency`` is an ordinary FastAPI dependency whose value is
    passed to every tool handler — a database session, a request-scoped service,
    whatever the host's tools need. Omit it for tools that need no context; they
    receive ``None``.

    The dependency is resolved per request rather than captured here, so a
    session opened for one call is not shared with the next.
    """

    router = APIRouter(tags=tags or ["mcp"])
    dependency = context_dependency or (lambda: None)

    @router.get("")
    def mcp_info() -> dict[str, Any]:
        return server.info_payload()

    @router.post("")
    async def mcp_post(
        request: Request,
        context: Any = Depends(dependency),
    ) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError as exc:
            # A body that is not JSON never reached the protocol handler, so it
            # cannot be reported as a tool failure. Say what actually happened.
            return JSONResponse(
                status_code=400,
                content={
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                },
            )
        return JSONResponse(content=server.handle_message(body, context))

    return router
