"""What is specific to the FastAPI mount.

The protocol behaviour this file used to assert now lives in
`test_mcp_transports.py`, run against every transport, so FastAPI and Django
cannot drift apart. What stays here is the part Django has no equivalent of:
how the router is included, and what FastAPI raises when it is included wrongly.
"""

from __future__ import annotations


class TestTheDocumentedMounting:
    """The README's snippet, executed.

    Added by the closing audit, which found the root README showing
    ``include_router(router)`` with no prefix — a form that raises
    ``Prefix and path cannot be both empty``. The claim that the example had
    been "executed rather than eyeballed" was true of `make_mcp_router` alone,
    not of the line that mounted it.
    """

    def test_mounting_under_a_prefix_works(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from lore_eden.mcp import McpServer, ServerInfo, ToolRegistry, make_mcp_router

        registry = ToolRegistry()

        @registry.tool("summarize", "Summarize a document")
        async def summarize(document_id: str) -> dict:
            return {"summary": document_id}

        app = FastAPI()
        app.include_router(
            make_mcp_router(McpServer(ServerInfo("my-host", "1.0"), registry)),
            prefix="/mcp",
        )
        assert TestClient(app).get("/mcp").status_code == 200

    def test_mounting_with_no_prefix_is_the_error_the_docs_warn_about(self) -> None:
        # Pinned so the docstring stays true. FastAPI's message names neither
        # this router nor the fix, which is why the router says so itself.
        import pytest
        from fastapi import FastAPI
        from fastapi.exceptions import FastAPIError
        from lore_eden.mcp import McpServer, ServerInfo, ToolRegistry, make_mcp_router

        app = FastAPI()
        router = make_mcp_router(McpServer(ServerInfo("x", "1"), ToolRegistry()))
        with pytest.raises(FastAPIError, match="both empty"):
            app.include_router(router)
