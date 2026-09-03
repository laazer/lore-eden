"""The URLconf a Django host would write, executed by the conformance suite.

Two routes because the suite needs a server with a request-scoped context and
one without, and a real host wires each view at its own path.

The first route is the README's Django snippet. Keeping it executed here is the
closing audit's lesson from the FastAPI side, where a README line that had never
been run showed a mount that raises.
"""

from __future__ import annotations

from django.urls import path
from mcp_transport_fixtures import build_server, next_context

from lore_eden.mcp import make_mcp_django_view

urlpatterns = [
    path("mcp", make_mcp_django_view(build_server())),
    path("mcp-ctx", make_mcp_django_view(build_server(), next_context)),
]
