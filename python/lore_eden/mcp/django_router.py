"""Serve an :class:`McpServer` from a Django view.

The sibling of :mod:`lore_eden.mcp.router`, which does the same for FastAPI.
Both are thin for the same reason: the protocol handler in
:mod:`lore_eden.mcp.protocol` knows the whole of MCP and nothing about HTTP, so
a transport only has to decode a body, resolve a context, and hand both over.
Everything a transport adds beyond that is a place the two can disagree.

## Plain Django, not DRF

The ticket that asked for this called it a DRF transport, and this is a plain
``django.http`` view instead. DRF's value is content negotiation, serializers,
authentication classes and browsable output — a layer that decides what a
request means before the body is read. JSON-RPC has already decided: one
content type, one envelope, and errors that must come back inside that envelope
rather than as DRF's ``{"detail": ...}``. Putting DRF between an MCP client and
the protocol handler adds a second opinion about failures without adding a
capability.

A plain view mounts identically in a DRF project, so nothing is lost. A host
that wants DRF's authentication or throttling in front of it wraps this view
the way it would wrap any other.

## CSRF

The view is ``csrf_exempt``. An MCP client is not a browser session and carries
no CSRF token, so with Django's ``CsrfViewMiddleware`` installed — which it is,
in the default ``MIDDLEWARE`` every ``startproject`` writes — every POST would
be rejected with 403 before reaching the handler. The exemption is what makes
the endpoint reachable at all, not a relaxation of the host's posture: the view
performs no session-authenticated state change of its own, and a host that
needs authentication puts it in front (see above).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from lore_eden.mcp.protocol import McpServer

PARSE_ERROR_CODE = -32700


def make_mcp_django_view(
    server: McpServer,
    context_factory: Callable[[HttpRequest], Any] | None = None,
) -> Callable[[HttpRequest], HttpResponse]:
    """A Django view serving ``server`` at whatever path it is wired to::

        urlpatterns = [
            path("mcp", make_mcp_django_view(server)),
        ]

    ``context_factory`` is called with the request and its value is passed to
    every tool handler — a database session, a request-scoped service, whatever
    the host's tools need. Omit it for tools that need no context; they receive
    ``None``.

    It is called per request rather than once here, so a session opened for one
    call is not shared with the next. That mirrors the FastAPI transport, where
    the same guarantee comes from ``Depends`` resolving per request.
    """

    factory = context_factory or (lambda request: None)

    @csrf_exempt
    def mcp_view(request: HttpRequest) -> HttpResponse:
        if request.method == "GET":
            return JsonResponse(server.info_payload())
        if request.method != "POST":
            return HttpResponseNotAllowed(["GET", "POST"])

        try:
            body = json.loads(request.body)
        except ValueError as exc:
            # A body that is not JSON never reached the protocol handler, so it
            # cannot be reported as a tool failure. Say what actually happened.
            return JsonResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": PARSE_ERROR_CODE, "message": f"Parse error: {exc}"},
                },
                status=400,
            )

        # safe=False because a JSON-RPC batch is answered with an array, and
        # JsonResponse refuses a non-dict without it.
        return JsonResponse(server.handle_message(body, factory(request)), safe=False)

    return mcp_view
