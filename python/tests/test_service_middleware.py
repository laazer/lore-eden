"""The three middleware, and the contextvars failure that decided one of them."""

from __future__ import annotations

import pytest
import structlog.contextvars
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

from lore_eden.service import (
    HSTS_VALUE,
    REQUEST_ID_HEADER,
    SECURITY_HEADERS,
    RateLimitMiddleware,
    RateLimitRule,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)


class TestRequestId:
    @staticmethod
    def app(**kwargs) -> FastAPI:
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware, **kwargs)

        @app.get("/")
        def root() -> dict:
            return {"bound": structlog.contextvars.get_contextvars().get("request_id", "")}

        return app

    def test_every_response_carries_an_id(self) -> None:
        response = TestClient(self.app()).get("/")
        assert response.headers[REQUEST_ID_HEADER]

    def test_the_id_is_bound_where_the_logger_will_find_it(self) -> None:
        response = TestClient(self.app()).get("/")
        assert response.json()["bound"] == response.headers[REQUEST_ID_HEADER]

    def test_an_incoming_id_is_honoured_so_a_trace_survives_a_hop(self) -> None:
        response = TestClient(self.app()).get("/", headers={REQUEST_ID_HEADER: "from-upstream"})
        assert response.headers[REQUEST_ID_HEADER] == "from-upstream"

    def test_it_can_be_told_not_to_trust_the_client(self) -> None:
        response = TestClient(self.app(trust_incoming=False)).get(
            "/", headers={REQUEST_ID_HEADER: "spoofed"}
        )
        assert response.headers[REQUEST_ID_HEADER] != "spoofed"

    def test_an_absurd_incoming_id_is_bounded(self) -> None:
        # It is echoed into a header and into every log line, so an unbounded
        # one is a log-flooding primitive.
        response = TestClient(self.app()).get("/", headers={REQUEST_ID_HEADER: "x" * 5000})
        assert len(response.headers[REQUEST_ID_HEADER]) <= 200

    def test_two_requests_get_different_ids(self) -> None:
        client = TestClient(self.app())
        first = client.get("/").headers[REQUEST_ID_HEADER]
        second = client.get("/").headers[REQUEST_ID_HEADER]
        assert first != second


class TestTheSourcesJustificationIsStale:
    """The claim corpocoin's docstring makes, executed — and it no longer holds.

    It says pure ASGI "avoids the contextvars propagation issue that can occur
    when BaseHTTPMiddleware spawns a new task for the response body". That
    described a real Starlette bug which has since been fixed.

    These two tests are kept together so the record is the measurement rather
    than the folklore: pure ASGI keeps the id through the body, *and so does
    BaseHTTPMiddleware* on current Starlette. If the second ever starts
    failing, the historical justification has come back and this comment is the
    thing to re-read.
    """

    @staticmethod
    def streaming_app(middleware_class) -> FastAPI:
        app = FastAPI()
        app.add_middleware(middleware_class)

        @app.get("/stream")
        def stream() -> StreamingResponse:
            def body():
                # Read *during body production* — the phase BaseHTTPMiddleware
                # moves into another task.
                seen = structlog.contextvars.get_contextvars().get("request_id", "")
                yield (seen or "LOST").encode()

            return StreamingResponse(body())

        return app

    def test_the_pure_asgi_version_keeps_the_id_through_the_body(self) -> None:
        response = TestClient(self.streaming_app(RequestIDMiddleware)).get("/stream")
        assert response.text != "LOST"
        assert response.text == response.headers[REQUEST_ID_HEADER]

    def test_basehttpmiddleware_keeps_it_too_on_current_starlette(self) -> None:
        # Written expecting "LOST", which is what the source's docstring
        # predicts. It came back "legacy-id" — so the bug is fixed, the
        # justification is stale, and bridgepath is not carrying a live defect.
        # Recorded as a measurement rather than deleted.
        class LegacyRequestId(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                structlog.contextvars.clear_contextvars()
                structlog.contextvars.bind_contextvars(request_id="legacy-id")
                return await call_next(request)

        response = TestClient(self.streaming_app(LegacyRequestId)).get("/stream")
        assert response.text == "legacy-id", (
            "BaseHTTPMiddleware has started losing contextvars again; the "
            "pure-ASGI choice now has its original justification back"
        )


class TestSecurityHeaders:
    @staticmethod
    def app(**kwargs) -> FastAPI:
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, **kwargs)

        @app.get("/")
        def root() -> dict:
            return {}

        @app.get("/own-frame-policy")
        def own() -> dict:
            from fastapi.responses import JSONResponse

            return JSONResponse({}, headers={"X-Frame-Options": "SAMEORIGIN"})

        return app

    def test_the_documented_headers_are_all_set(self) -> None:
        response = TestClient(self.app()).get("/")
        for header, value in SECURITY_HEADERS.items():
            assert response.headers[header] == value

    def test_a_content_security_policy_is_among_them(self) -> None:
        # bridgepath's version has no CSP; corpocoin's does. That is the
        # difference that decided which was taken.
        assert "Content-Security-Policy" in SECURITY_HEADERS
        assert TestClient(self.app()).get("/").headers["Content-Security-Policy"]

    def test_hsts_only_in_production(self) -> None:
        # Sent over plain HTTP in development it pins a browser to
        # https://localhost, which is a bad afternoon.
        assert "strict-transport-security" not in TestClient(self.app()).get("/").headers
        production = TestClient(self.app(is_production=True)).get("/")
        assert production.headers["strict-transport-security"] == HSTS_VALUE

    def test_a_route_that_set_its_own_is_not_overridden(self) -> None:
        response = TestClient(self.app()).get("/own-frame-policy")
        assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


class TestRateLimit:
    """Driven by a stopped clock unless a test says otherwise.

    Not tidiness. The window number is part of the counter key, so a suite that
    used the real clock had its counter reset under it whenever three requests
    happened to straddle a minute boundary — and the request that should have
    been refused was allowed. It failed on CI at 19:38:00.2, roughly one run in
    however many land there, and looked exactly like a flake.
    """

    @staticmethod
    def app(counter, **kwargs) -> FastAPI:
        app = FastAPI()
        rules = kwargs.pop("rules", [RateLimitRule("/write", limit=2, window_seconds=60)])
        kwargs.setdefault("now", lambda: 1_000_000.0)
        app.add_middleware(RateLimitMiddleware, rules=rules, count=counter, **kwargs)

        @app.post("/write")
        def write() -> dict:
            return {"ok": True}

        @app.get("/write")
        def read() -> dict:
            return {"ok": True}

        @app.post("/other")
        def other() -> dict:
            return {"ok": True}

        return app

    @staticmethod
    def counting():
        seen: dict[str, int] = {}

        async def count(key: str, _window: int) -> int:
            seen[key] = seen.get(key, 0) + 1
            return seen[key]

        return count

    def test_it_refuses_past_the_limit(self) -> None:
        client = TestClient(self.app(self.counting()))
        assert client.post("/write").status_code == 200
        assert client.post("/write").status_code == 200
        assert client.post("/write").status_code == 429

    def test_the_window_boundary_resets_the_count(self) -> None:
        """The behaviour that made the flake, pinned as behaviour.

        A fixed-window limiter is *supposed* to forget at the boundary. Now that
        the clock is injected the reset can be asserted deliberately instead of
        being discovered by a test that happened to run at the wrong second.
        """
        clock = {"seconds": 1_000_000.0}
        client = TestClient(self.app(self.counting(), now=lambda: clock["seconds"]))

        assert client.post("/write").status_code == 200
        assert client.post("/write").status_code == 200
        assert client.post("/write").status_code == 429

        clock["seconds"] += 60
        assert client.post("/write").status_code == 200

    def test_the_refusal_says_when_to_come_back(self) -> None:
        client = TestClient(self.app(self.counting()))
        for _ in range(3):
            response = client.post("/write")
        assert response.headers["retry-after"] == "60"

    def test_reads_are_not_counted(self) -> None:
        # A limit on reads throttles a dashboard polling its own data.
        client = TestClient(self.app(self.counting()))
        for _ in range(10):
            assert client.get("/write").status_code == 200

    def test_a_path_with_no_rule_is_untouched(self) -> None:
        client = TestClient(self.app(self.counting()))
        for _ in range(10):
            assert client.post("/other").status_code == 200

    def test_the_first_matching_rule_wins_so_specific_can_precede_broad(self) -> None:
        middleware = RateLimitMiddleware(
            app=None,
            rules=[RateLimitRule("/write/rare", 1), RateLimitRule("/write", 100)],
            count=self.counting(),
        )
        assert middleware.rule_for("/write/rare").limit == 1
        assert middleware.rule_for("/write/other").limit == 100
        assert middleware.rule_for("/elsewhere") is None

    def test_a_broken_counter_fails_open_and_says_so(self, caplog) -> None:
        async def broken(_key: str, _window: int) -> int:
            raise ConnectionError("redis is gone")

        with caplog.at_level("ERROR"):
            assert TestClient(self.app(broken)).post("/write").status_code == 200
        # The silence is the part that makes a knocked-out Redis a way to
        # disable the control.
        assert any("rate limit counter unavailable" in r.message for r in caplog.records)

    def test_it_can_be_told_to_fail_closed(self, caplog) -> None:
        async def broken(_key: str, _window: int) -> int:
            raise ConnectionError("redis is gone")

        with caplog.at_level("ERROR"):
            response = TestClient(self.app(broken, fail_open=False)).post("/write")
        assert response.status_code == 429

    def test_disabled_lets_everything_through(self) -> None:
        client = TestClient(self.app(self.counting(), enabled=False))
        for _ in range(10):
            assert client.post("/write").status_code == 200


class TestTheyTravelToADjangoHost:
    """Asked by the MCP transport work: does `lore_eden.service` drag FastAPI
    into a Django project along with the Django MCP view?

    It does not, and the reason is stronger than "no import". These three are
    pure ASGI — ``__call__(scope, receive, send)`` — so they wrap *any* ASGI
    application, Django's included. A Django host wraps
    ``get_asgi_application()`` rather than adding them to ``MIDDLEWARE``, which
    is a different wiring point, not a missing capability.

    Proven by running one against a real Django ASGI app rather than by reading
    the signatures, because "it imports nothing framework-specific" and "it
    works there" are different claims.
    """

    @staticmethod
    def _get(app, path: str):
        """Drive the ASGI app over a real client.

        Hand-rolling ``receive``/``send`` looks simpler and is not: Django's
        handler listens for a disconnect alongside the response, so a fake
        ``receive`` either repeats the body — which it rejects — or reports a
        disconnect that cancels the request before it answers.
        """
        import httpx
        from asgiref.sync import async_to_sync

        async def call():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.get(path)

        return async_to_sync(call)()

    def test_request_id_middleware_wraps_a_django_asgi_app(self) -> None:
        from mcp_transport_fixtures import configure_django

        configure_django()
        from django.core.asgi import get_asgi_application

        response = self._get(RequestIDMiddleware(get_asgi_application()), "/mcp")

        assert response.status_code == 200
        assert response.headers[REQUEST_ID_HEADER]
