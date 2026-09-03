"""Service scaffolding two live backends each rebuilt independently.

`corpocoin` and `bridgepath` both grew the same layer around FastAPI: a request
id, hardening headers, a rate limiter, a domain error hierarchy, pagination and
structlog configuration. Where they disagreed, the module that owns each piece
records which won and why — because the losing version is still running in one
of them, and a note in a commit message would not reach whoever reads the code.

- :mod:`~lore_eden.service.errors` — domain errors that know no HTTP, and one
  boundary that maps them.
- :mod:`~lore_eden.service.middleware` — request id, security headers, rate
  limiting. All pure ASGI.
- :mod:`~lore_eden.service.pagination` — page parameters and a paged response.
- :mod:`~lore_eden.service.log_config` — structlog: JSON in production,
  readable in development.

`structlog` is an optional extra. Without it the request id still reaches the
response header; only the log binding is skipped.
"""

from lore_eden.service.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionError_,
    RateLimitedError,
    UnauthenticatedError,
    UnavailableError,
    ValidationError,
    error_payload,
    install_domain_error_handlers,
    register_status,
    status_for,
)
from lore_eden.service.middleware import (
    HSTS_VALUE,
    ScopeType,
    REQUEST_ID_HEADER,
    SECURITY_HEADERS,
    WRITE_METHODS,
    RateLimitMiddleware,
    RateLimitRule,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from lore_eden.service.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
    PaginationParams,
    paginate,
)

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "HSTS_VALUE",
    "MAX_PAGE_SIZE",
    "REQUEST_ID_HEADER",
    "SECURITY_HEADERS",
    "ScopeType",
    "WRITE_METHODS",
    "ConflictError",
    "DomainError",
    "NotFoundError",
    "Page",
    "PaginationParams",
    "PermissionError_",
    "RateLimitMiddleware",
    "RateLimitRule",
    "RateLimitedError",
    "RequestIDMiddleware",
    "SecurityHeadersMiddleware",
    "UnauthenticatedError",
    "UnavailableError",
    "ValidationError",
    "error_payload",
    "install_domain_error_handlers",
    "paginate",
    "register_status",
    "status_for",
]
