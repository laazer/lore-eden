"""The tool registry an MCP server dispatches through.

The extraction this came from dispatched tools with a long if-chain over tool
names, with a side table bolted on once the chain passed its complexity cap.
That shape has a cost the chain hides: every tool appended makes the next one
harder to add, and a tool cannot be contributed from outside the module that
owns the chain.

Here the table is the only mechanism. A tool is a name, a JSON Schema the client
sees, and a handler — registered from anywhere, including from a host
application this package knows nothing about.

Handlers receive a ``context`` the host supplies per request. This package never
inspects it: for a database-backed host it is a session, for another it may be a
config object or nothing at all. That is the seam that lets the same transport
serve tools that have nothing to do with each other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: A tool implementation. Receives the host's per-request context and the
#: arguments the client sent, and returns the text the client gets back.
ToolHandler = Callable[[Any, dict[str, Any]], str]


class ToolError(Exception):
    """A tool could not run. The message reaches the MCP client verbatim."""


class UnknownToolError(ToolError):
    """No tool is registered under that name."""


class DuplicateToolError(ValueError):
    """A name was registered twice.

    Refused rather than overwritten: silently replacing a tool means the call
    that used to work now runs someone else's code, and nothing says so.
    """


@dataclass(frozen=True)
class ToolDefinition:
    """What a client is told about one tool.

    ``input_schema`` is JSON Schema and is surfaced under the protocol's
    ``inputSchema`` key; the rename is the only translation done here.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})

    def as_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class ToolRegistry:
    """Tools this server offers, keyed by name.

    Registration order is preserved so ``tools/list`` is stable between calls —
    a client diffing the catalogue should see a change only when one happened.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(
        self,
        definition: ToolDefinition,
        handler: ToolHandler,
    ) -> None:
        if definition.name in self._handlers:
            raise DuplicateToolError(f"A tool named {definition.name!r} is already registered")
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    def tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any] | None = None,
    ) -> Callable[[ToolHandler], ToolHandler]:
        """Decorator form of :meth:`register`, for tools defined inline."""

        def decorate(handler: ToolHandler) -> ToolHandler:
            self.register(
                ToolDefinition(
                    name=name,
                    description=description,
                    input_schema=input_schema or {"type": "object"},
                ),
                handler,
            )
            return handler

        return decorate

    def names(self) -> list[str]:
        return list(self._handlers)

    def definitions(self) -> list[dict[str, Any]]:
        """The ``tools/list`` payload."""
        return [definition.as_payload() for definition in self._definitions.values()]

    def names(self) -> list[str]:
        """What is registered, in registration order.

        Separate from :meth:`definitions`, which returns wire payloads. A host
        asking "what do I offer?" wanted the names, and without this it either
        digs them out of the payloads or reaches for a private attribute.
        """
        return list(self._definitions)

    def __contains__(self, name: object) -> bool:
        return name in self._handlers

    def __len__(self) -> int:
        return len(self._handlers)

    def call(self, name: str, arguments: dict[str, Any], context: Any) -> str:
        handler = self._handlers.get(name)
        if handler is None:
            known = ", ".join(sorted(self._handlers)) or "none"
            raise UnknownToolError(f"Unknown tool: {name}. Registered tools: {known}")
        return handler(context, arguments)
