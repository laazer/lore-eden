"""What is specific to the Django mount.

The protocol behaviour lives in `test_mcp_transports.py`, run against every
transport. What stays here is what Django does and FastAPI does not: CSRF
middleware standing in front of the view, and method handling on a single view
function rather than a route per method.
"""

from __future__ import annotations

from mcp_transport_fixtures import configure_django


def test_a_post_survives_csrf_middleware() -> None:
    """Without ``csrf_exempt`` this is a 403 that never reaches the handler.

    ``CsrfViewMiddleware`` is in the default MIDDLEWARE of every Django project,
    and an MCP client carries no CSRF token, so the exemption is what makes the
    endpoint reachable at all. Asserted through the real middleware stack — a
    ``RequestFactory`` call would pass whether the exemption were there or not.
    """
    configure_django()
    from django.test import Client

    response = Client().post(
        "/mcp",
        data='{"jsonrpc": "2.0", "id": 1, "method": "ping"}',
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["result"] == {}


def test_a_method_that_is_neither_get_nor_post_is_refused() -> None:
    """One view serves both verbs, so nothing else rejects the others for it."""
    configure_django()
    from django.test import Client

    response = Client().put(
        "/mcp",
        data='{"jsonrpc": "2.0", "id": 1, "method": "ping"}',
        content_type="application/json",
    )

    assert response.status_code == 405
    assert sorted(response.headers["Allow"].split(", ")) == ["GET", "POST"]
