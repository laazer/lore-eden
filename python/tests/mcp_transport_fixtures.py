"""One MCP server, reachable over both transports, for one conformance suite.

Kept out of a ``test_`` module so the Django URLconf can import it without
pytest collecting the URLconf as a test file.

The context counter lives here rather than in either transport's builder so
both count the same way. That is the point of the shared suite: if FastAPI's
``Depends`` resolved per request and Django's factory did not, the two would
produce different sequences from identical assertions.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lore_eden.mcp import McpServer, ServerInfo, ToolRegistry

_context_calls: list[None] = []


def reset_context() -> None:
    _context_calls.clear()


def next_context(request: Any = None) -> str:
    """A fresh context per call, so a shared one is visible as a repeat."""
    _context_calls.append(None)
    return f"ctx{len(_context_calls) - 1}"


def build_server() -> McpServer:
    tools = ToolRegistry()

    @tools.tool("shout", "Upper-case the text.")
    def shout(context, arguments):
        prefix = f"[{context}] " if context is not None else ""
        return prefix + str(arguments.get("text", "")).upper()

    return McpServer(ServerInfo(name="bare-app"), tools)


@dataclass(frozen=True)
class TransportClient:
    """What the conformance suite needs from a transport, and nothing else."""

    name: str
    get_info: Callable[[], tuple[int, Any]]
    post_json: Callable[[Any], tuple[int, Any]]
    post_raw: Callable[[bytes], tuple[int, Any]]


def _decode(status: int, content: bytes) -> tuple[int, Any]:
    try:
        return status, json.loads(content)
    except ValueError:
        return status, content.decode("utf-8", "replace")


def fastapi_client(*, with_context: bool = False) -> TransportClient:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lore_eden.mcp import make_mcp_router

    app = FastAPI()
    dependency = (lambda: next_context()) if with_context else None
    app.include_router(make_mcp_router(build_server(), dependency), prefix="/mcp")
    client = TestClient(app)

    return TransportClient(
        name="fastapi",
        get_info=lambda: _decode(*_httpx(client.get("/mcp"))),
        post_json=lambda body: _decode(*_httpx(client.post("/mcp", json=body))),
        post_raw=lambda raw: _decode(
            *_httpx(
                client.post("/mcp", content=raw, headers={"Content-Type": "application/json"})
            )
        ),
    )


def _httpx(response: Any) -> tuple[int, bytes]:
    return response.status_code, response.content


def django_client(*, with_context: bool = False) -> TransportClient:
    configure_django()
    from django.test import Client

    client = Client()
    # Two routes rather than two views built here: the URLconf is what a real
    # Django host writes, so the test exercises the wiring it documents.
    path = "/mcp-ctx" if with_context else "/mcp"

    def post(body: Any) -> tuple[int, Any]:
        response = client.post(path, data=json.dumps(body), content_type="application/json")
        return _decode(response.status_code, response.content)

    def post_raw(raw: bytes) -> tuple[int, Any]:
        response = client.post(path, data=raw, content_type="application/json")
        return _decode(response.status_code, response.content)

    def get_info() -> tuple[int, Any]:
        response = client.get(path)
        return _decode(response.status_code, response.content)

    return TransportClient(
        name="django", get_info=get_info, post_json=post, post_raw=post_raw
    )


def configure_django() -> None:
    """Settings for a Django project that is nothing but this URLconf.

    ``CsrfViewMiddleware`` is present deliberately. It is in the default
    ``MIDDLEWARE`` of every ``startproject``, and without the view's
    ``csrf_exempt`` it rejects every POST with 403 before the handler runs —
    so a test suite that omitted it would pass while real hosts got 403s.
    """
    import django
    from django.conf import settings

    if settings.configured:
        return
    settings.configure(
        DEBUG=False,
        SECRET_KEY="lore-eden-conformance-tests",
        ALLOWED_HOSTS=["testserver"],
        ROOT_URLCONF="django_urlconf",
        MIDDLEWARE=["django.middleware.csrf.CsrfViewMiddleware"],
        DATABASES={},
        USE_TZ=True,
    )
    django.setup()
