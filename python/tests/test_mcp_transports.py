"""One protocol suite, run against every transport.

The transports exist so a host can mount MCP on the web framework it already
has. That promise is only worth something if both answer identically, and the
way to keep them identical is to give them no separate tests to drift between.
Anything genuinely specific to one framework — how it is mounted, what it
raises when mounted wrongly — stays in that transport's own module.

A behavioural difference between FastAPI and Django fails here, on the
transport that differs, by name.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from lore_eden.mcp.protocol import PROTOCOL_VERSION
from mcp_transport_fixtures import django_client, fastapi_client, reset_context

BUILDERS = {"fastapi": fastapi_client, "django": django_client}


@pytest.fixture(params=sorted(BUILDERS), ids=sorted(BUILDERS))
def transport(request):
    reset_context()
    return BUILDERS[request.param]()


@pytest.fixture(params=sorted(BUILDERS), ids=sorted(BUILDERS))
def transport_with_context(request):
    reset_context()
    return BUILDERS[request.param](with_context=True)


def rpc(transport, method: str, params=None, request_id=1):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    status, payload = transport.post_json(body)
    assert status == 200, payload
    return payload


def test_full_handshake_round_trip(transport):
    """initialize -> initialized -> tools/list -> tools/call -> ping, the
    sequence a real client actually performs."""
    initialize = rpc(transport, "initialize")["result"]
    assert initialize["protocolVersion"] == PROTOCOL_VERSION
    assert initialize["serverInfo"]["name"] == "bare-app"

    status, notified = transport.post_json(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert status == 200
    assert notified == {}

    tools = rpc(transport, "tools/list")["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["shout"]

    called = rpc(transport, "tools/call", {"name": "shout", "arguments": {"text": "hi"}})
    assert called["result"]["content"] == [{"type": "text", "text": "HI"}]

    assert rpc(transport, "ping")["result"] == {}


def test_get_describes_the_endpoint(transport):
    status, payload = transport.get_info()

    assert status == 200
    assert payload["serverInfo"]["name"] == "bare-app"
    assert payload["transport"] == "streamable-http"
    assert "tools/call" in payload["usage"]


def test_a_batch_is_answered_as_an_array(transport):
    status, payload = transport.post_json(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
    )

    assert status == 200
    assert [item["id"] for item in payload] == [1, 2]


def test_a_body_that_is_not_json_is_a_parse_error_not_a_tool_failure(transport):
    """It never reached a tool, so reporting it as one would be a lie about
    where the failure was."""
    status, payload = transport.post_raw(b"{not json")

    assert status == 400
    assert payload["error"]["code"] == -32700


def test_context_is_resolved_per_request(transport_with_context):
    """A session opened for one call must not be shared with the next."""
    first = rpc(transport_with_context, "tools/call", {"name": "shout", "arguments": {"text": "a"}})
    second = rpc(
        transport_with_context, "tools/call", {"name": "shout", "arguments": {"text": "b"}}
    )

    assert first["result"]["content"][0]["text"] == "[ctx0] A"
    assert second["result"]["content"][0]["text"] == "[ctx1] B"


def test_no_context_reaches_a_tool_as_none(transport):
    called = rpc(transport, "tools/call", {"name": "shout", "arguments": {"text": "a"}})

    assert called["result"]["content"][0]["text"] == "A"


class TestTheProtocolNeedsNeitherFramework:
    """The reason `lore_eden.mcp.__init__` imports its transports lazily.

    Asserted in a subprocess against that interpreter's own ``sys.modules``,
    rather than this one's: by the time this file runs, both frameworks are
    long since imported here.
    """

    def _imported(self, script: str) -> str:
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    def test_importing_the_protocol_pulls_in_no_web_framework(self) -> None:
        script = (
            "import sys, lore_eden.mcp.protocol;"
            "leaked = sorted(m for m in sys.modules "
            "if m.split('.')[0] in {'fastapi', 'starlette', 'django'});"
            "print(leaked)"
        )
        assert self._imported(script) == "[]"

    def test_the_fastapi_transport_does_import_one(self) -> None:
        # The control. Without it the test above would pass just as well if the
        # import machinery were broken.
        script = (
            "import sys;"
            "from lore_eden.mcp import make_mcp_router;"
            "print('fastapi' in sys.modules)"
        )
        assert self._imported(script) == "True"

    def test_the_django_transport_does_import_one(self) -> None:
        script = (
            "import sys;"
            "from lore_eden.mcp import make_mcp_django_view;"
            "print('django' in sys.modules)"
        )
        assert self._imported(script) == "True"

    def test_an_unknown_attribute_is_still_an_attribute_error(self) -> None:
        import lore_eden.mcp

        with pytest.raises(AttributeError, match="no attribute 'make_mcp_carrier_pigeon'"):
            # Bound rather than left bare: the lookup is the whole test, and an
            # unbound attribute reads as a useless expression to a linter.
            _ = lore_eden.mcp.make_mcp_carrier_pigeon
