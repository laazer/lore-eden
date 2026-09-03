"""Domain errors that know nothing about HTTP, and one place that maps them.

Extracted from `bridgepath`, which was the only live implementation of this
design. `lllm-charge` had the other: one base exception carrying its own
``status_code``. That version is not here, and the reason is worth stating —
an exception that carries an HTTP status has an opinion about a transport it
should not know exists, which is exactly what stops it being raised from a
worker, a CLI, a scheduled job or a test.

So the hierarchy is transport-free and the mapping lives at the boundary:

    # in a domain service, which imports no web framework
    raise NotFoundError("No deal with that id")

    # once, in create_app()
    install_domain_error_handlers(app)

The payoff bridgepath's own docstring names: no try/except in any router.
"""

from __future__ import annotations

from typing import Any, Mapping

#: HTTP status for each error class. The only place in this package that knows
#: an HTTP status exists, which is the point.
_STATUS: dict[type, int] = {}


class DomainError(Exception):
    """Something the domain refused, in terms the domain owns.

    ``details`` carries structured context for the caller — which field, which
    id — rather than being formatted into the message, so an API can render it
    and a log can index it.
    """

    def __init__(self, message: str = "", details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        self.details: dict[str, Any] = dict(details or {})


class NotFoundError(DomainError):
    """The thing asked for does not exist, or the caller may not see it.

    Deliberately one error for both. Distinguishing "absent" from "forbidden"
    to an unauthorized caller tells them the resource exists, which is the
    enumeration leak this collapse prevents.
    """


class ValidationError(DomainError):
    """The request was understood and is not acceptable."""


class ConflictError(DomainError):
    """The request contradicts the current state — a duplicate, a stale write."""


class PermissionError_(DomainError):
    """The caller is known and not allowed.

    Trailing underscore because `PermissionError` is a builtin, and shadowing
    it inside a package every service imports is a trap: `except
    PermissionError` at a call site would then catch or miss depending on
    import order.
    """


class UnauthenticatedError(DomainError):
    """The caller is not known."""


class RateLimitedError(DomainError):
    """The caller is doing this too often."""


class UnavailableError(DomainError):
    """A dependency this operation needs is not answering.

    Distinct from a failure of the operation itself: a caller may retry this
    and should not retry a ValidationError.
    """


_STATUS.update(
    {
        UnauthenticatedError: 401,
        PermissionError_: 403,
        NotFoundError: 404,
        ConflictError: 409,
        ValidationError: 422,
        RateLimitedError: 429,
        UnavailableError: 503,
        DomainError: 500,
    }
)


def status_for(error: BaseException) -> int:
    """The HTTP status for an error, walking its bases.

    Walked rather than looked up directly so a host's own subclass of
    ``NotFoundError`` maps to 404 without registering anything — which is the
    whole point of a hierarchy, and the thing a flat dict of exact types
    silently fails to do.
    """
    for klass in type(error).__mro__:
        if klass in _STATUS:
            return _STATUS[klass]
    return 500


def register_status(error_type: type, status: int) -> None:
    """Map a host's own error type to a status the hierarchy does not cover."""
    _STATUS[error_type] = status


def error_payload(error: DomainError) -> dict[str, Any]:
    """The response body. ``detail`` because that is what FastAPI's own errors use."""
    payload: dict[str, Any] = {"detail": error.message}
    if error.details:
        payload["details"] = error.details
    return payload


def install_domain_error_handlers(app: Any) -> None:
    """Turn every DomainError raised under ``app`` into its mapped response.

    One handler for the base class, since Starlette dispatches on the most
    specific registered type and every error here shares that base. Registering
    one per subclass would work and would then miss a host's own subclass.
    """
    from fastapi.responses import JSONResponse

    @app.exception_handler(DomainError)
    async def _handle(_request: Any, exc: DomainError) -> Any:
        return JSONResponse(status_code=status_for(exc), content=error_payload(exc))
