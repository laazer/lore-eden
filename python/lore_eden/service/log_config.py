"""structlog configuration: JSON in production, readable in development.

Named ``log_config`` rather than ``logging``. A module called ``logging.py``
inside a package shadows the stdlib module for anything that resolves it by a
path including this directory — and the failure is an ``AttributeError`` about
a partially initialized module, from a sibling that only did ``import
logging``. Renaming removes the ambiguity instead of relying on every consumer
having tidy path hygiene.

From `corpocoin`, which had it as a module; `bridgepath` does the same thing
inline in `main.py`. Extracting the factored one is the whole reason a shared
library helps here — the inline version cannot be reused even by its own tests.

Call :func:`configure_logging` once, at startup, **before any logger is
created**. Configuring afterwards leaves already-bound loggers on the old
processor chain, so some lines are JSON and some are not, which is worse than
either.
"""

from __future__ import annotations

import logging
import sys
from typing import Any


def shared_processors() -> list[Any]:
    """The chain both the structlog path and the stdlib formatter must share.

    Shared deliberately: without it, a log line from a third-party library
    routed through the stdlib handler comes out in a different shape from the
    application's own, and the log becomes two formats in one stream.
    """
    import structlog

    return [
        # First, so a request id bound by the middleware reaches every line.
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        # Renders an exception into the event rather than losing it: a
        # structured logger that drops tracebacks is worse than print.
        structlog.processors.format_exc_info,
    ]


def configure_logging(*, json_output: bool = True, level: int = logging.INFO) -> None:
    """Point structlog and the stdlib root logger at one processor chain."""
    import structlog

    processors = shared_processors()
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        foreign_pre_chain=processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replaced rather than appended: called twice — a reload, a test — appending
    # would emit every line once per call.
    root.handlers = [handler]
    root.setLevel(level)
