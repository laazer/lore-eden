"""Which transport module serves which name, and importing it only when asked.

A transport drags its web framework in with it — FastAPI for
:mod:`~lore_eden.mcp.router`, Django for :mod:`~lore_eden.mcp.django_router` —
and the protocol handler needs neither. Importing both from
``lore_eden.mcp.__init__`` would put both frameworks in the import path of every
host that touches :class:`~lore_eden.mcp.protocol.McpServer`, so a Django
project would install FastAPI to reach it and vice versa.

:func:`load_transport` is bound as ``__getattr__`` in that package (PEP 562), so
``from lore_eden.mcp import make_mcp_router`` still reads as an ordinary import
and pays for one framework rather than both. It lives here rather than in
``__init__.py`` because behaviour in a package initialiser is the thing the
organization gate exists to stop.

The dispatch is written as explicit branches on purpose. A name-to-module table
resolved with ``getattr`` is shorter and states less: these branches are what
lets a reader — and a type checker — see that exactly two names are lazy, and
what each one costs.
"""

from __future__ import annotations

from typing import Any


def load_transport(name: str) -> Any:
    """Return a transport factory, importing its framework on the way."""
    if name == "make_mcp_router":
        from lore_eden.mcp.router import make_mcp_router

        return make_mcp_router
    if name == "make_mcp_django_view":
        from lore_eden.mcp.django_router import make_mcp_django_view

        return make_mcp_django_view
    raise AttributeError(f"module 'lore_eden.mcp' has no attribute {name!r}")
