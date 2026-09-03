"""The three middleware `corpocoin` and `bridgepath` each built independently.

Where they disagreed, the choice is recorded here rather than in a commit
message, because the losing version is still running in one of them.

## RequestIDMiddleware is pure ASGI — but not for the reason the source gives

`corpocoin` implements it as raw ASGI and its docstring explains why:

> Using a pure ASGI middleware (not BaseHTTPMiddleware) avoids the contextvars
> propagation issue that can occur when BaseHTTPMiddleware spawns a new task
> for the response body.

**That was true and is no longer.** It described a real Starlette bug, since
fixed. A test in `test_service_middleware.py` builds the `BaseHTTPMiddleware`
version and asserts a contextvar bound before the response body is still
readable *inside* it — and on Starlette 1.6 it is. Both backends floor at
`fastapi>=0.111.0`, which resolves well past the fix, so `bridgepath`'s
`BaseHTTPMiddleware` version is not carrying a live bug either.

The comment is left as a stale justification for a choice that is still fine on
its own weaker merits: one less task hop per request, no dependency on
`BaseHTTPMiddleware`'s streaming semantics, and it works on old Starlette too.
Had the extraction not tested the claim, this module would be repeating a
retired bug report as a current design reason.

## The rate limiter combines both

`bridgepath` allows a per-rule window — `("/auth/login", 5, 60)` — where
`corpocoin` hard-coded a minute. `corpocoin` counts only write methods and
**fails open** when Redis is unreachable. Both survive, but the fail-open now
logs at error level: a rate limiter that quietly stops limiting is a security
control an attacker can switch off by taking out one dependency, and the
silence is the part that makes that work.
"""

from __future__ import annotations

import logging
import time
import uuid
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable, Sequence

logger = logging.getLogger(__name__)


class ScopeType(str, Enum):
    """The ASGI connection types these middleware distinguish.

    The ASGI specification owns this vocabulary, not this package — but the
    strings were compared at three sites, and one typo there means a
    middleware silently passes a connection through ungraded. A type makes
    that a failure at import rather than a header quietly missing.
    """

    HTTP = "http"
    WEBSOCKET = "websocket"
    LIFESPAN = "lifespan"

#: Set on every response, on every app. A pure JSON API needs none of the
#: features these switch off, so a strict policy costs nothing and removes the
#: reflected-XSS surface entirely.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-XSS-Protection": "1; mode=block",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=()",
    "X-Permitted-Cross-Domain-Policies": "none",
}

#: Two years, subdomains included, preload-eligible. Production only: sent over
#: plain HTTP in development it would pin a browser to https://localhost.
HSTS_VALUE = "max-age=31536000; includeSubDomains; preload"

REQUEST_ID_HEADER = "x-request-id"

#: Only these are counted. A rate limit on reads throttles a dashboard polling
#: its own data; the abuse worth limiting writes.
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _bind_request_id(request_id: str) -> None:
    """Put the id where the logger will find it, if structlog is installed.

    Optional because the middleware is worth having without it: the response
    header alone lets a client quote an id back at you.
    """
    try:
        import structlog.contextvars
    except ImportError:
        return
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)


class RequestIDMiddleware:
    """Tag every request, echo it back, and bind it for the logger.

    Pure ASGI — see the module docstring. An incoming ``X-Request-ID`` is
    honoured so a trace survives a hop between services; absent, one is made.
    """

    def __init__(self, app: Any, *, trust_incoming: bool = True) -> None:
        self._app = app
        self._trust_incoming = trust_incoming

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") not in (ScopeType.HTTP, ScopeType.WEBSOCKET):
            await self._app(scope, receive, send)
            return

        request_id = self._incoming(scope) or str(uuid.uuid4())
        _bind_request_id(request_id)
        encoded = request_id.encode()

        async def send_with_header(message: dict) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), encoded))
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_header)

    def _incoming(self, scope: dict) -> str:
        if not self._trust_incoming:
            return ""
        for name, value in scope.get("headers") or []:
            if name.lower() == REQUEST_ID_HEADER.encode():
                # Bounded: an id is echoed into a response header and into every
                # log line, so an unbounded one is a log-flooding primitive.
                return value.decode("utf-8", errors="replace")[:200]
        return ""


class SecurityHeadersMiddleware:
    """Set the hardening headers on every response.

    Pure ASGI for the same reason as the request id: one mechanism, and no
    surprise about which middleware can see contextvars.
    """

    def __init__(self, app: Any, *, is_production: bool = False) -> None:
        self._app = app
        self._is_production = is_production

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != ScopeType.HTTP:
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                for header, value in SECURITY_HEADERS.items():
                    # A route that set its own is not overridden: a download
                    # endpoint legitimately needs a different frame policy.
                    if header.lower().encode() not in present:
                        headers.append((header.lower().encode(), value.encode()))
                if self._is_production:
                    headers.append((b"strict-transport-security", HSTS_VALUE.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_headers)


class RateLimitRule:
    """A path prefix, a request count, and the window it applies over."""

    __slots__ = ("prefix", "limit", "window_seconds")

    def __init__(self, prefix: str, limit: int, window_seconds: int = 60) -> None:
        self.prefix = prefix
        self.limit = limit
        self.window_seconds = window_seconds


class RateLimitMiddleware:
    """A fixed-window limiter over a counter the host supplies.

    The counter is injected rather than a Redis client being constructed here:
    a host with Redis passes one backed by it, a test passes a dict, and this
    package gains no Redis dependency for a feature not every consumer wants.

    ``fail_open`` keeps the source's availability choice — a limiter that cannot
    reach its counter lets traffic through rather than refusing everyone. It
    **logs at error level** when it does, because the silent version is a
    control an attacker disables by taking out one dependency.
    """

    def __init__(
        self,
        app: Any,
        *,
        rules: Sequence[RateLimitRule],
        count: Callable[[str, int], Awaitable[int]],
        enabled: bool = True,
        fail_open: bool = True,
        methods: Iterable[str] = WRITE_METHODS,
    ) -> None:
        self._app = app
        self._rules = list(rules)
        self._count = count
        self._enabled = enabled
        self._fail_open = fail_open
        self._methods = frozenset(methods)

    def rule_for(self, path: str) -> RateLimitRule | None:
        """The first matching rule, in the order given — so a specific path can
        precede a broader prefix without the broader one shadowing it."""
        for rule in self._rules:
            if path.startswith(rule.prefix):
                return rule
        return None

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if not self._enabled or scope.get("type") != ScopeType.HTTP:
            await self._app(scope, receive, send)
            return
        if scope.get("method", "").upper() not in self._methods:
            await self._app(scope, receive, send)
            return

        rule = self.rule_for(scope.get("path", ""))
        if rule is None:
            await self._app(scope, receive, send)
            return

        if await self._over_limit(rule, scope):
            await self._refuse(send, rule)
            return
        await self._app(scope, receive, send)

    async def _over_limit(self, rule: RateLimitRule, scope: dict) -> bool:
        window = int(time.time()) // rule.window_seconds
        key = f"ratelimit:{rule.prefix}:{self._client(scope)}:{window}"
        try:
            return await self._count(key, rule.window_seconds) > rule.limit
        except Exception as exc:  # noqa: BLE001 - any counter failure, see below
            # Deliberately broad and deliberately loud. The counter is a network
            # dependency and every way it can fail leads to the same decision;
            # what matters is that the decision is visible, because an
            # unlimited window that nobody was told about is the failure mode.
            logger.error(
                "rate limit counter unavailable, %s: %s",
                "allowing the request" if self._fail_open else "refusing the request",
                exc,
            )
            return not self._fail_open

    @staticmethod
    def _client(scope: dict) -> str:
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def _refuse(self, send: Any, rule: RateLimitRule) -> None:
        body = b'{"detail":"Too many requests. Please try again later."}'
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    # So a client can back off correctly rather than retrying
                    # immediately and burning its next window too.
                    (b"retry-after", str(rule.window_seconds).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
