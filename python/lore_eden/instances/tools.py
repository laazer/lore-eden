"""MCP tools for agents to launch, inspect and stop local instances.

::

    from lore_eden.instances import register_instance_tools

    register_instance_tools(registry, lambda: manager, prefix="shop_")

Four tools — ``list_instances``, ``launch_instance``, ``instance_status`` and
``stop_instance`` — over the same manager the HTTP router uses, so an agent and
a person at the UI see and act on the same instances.

Handlers take the ``(context, arguments) -> str`` shape of
:class:`~lore_eden.mcp.ToolRegistry`. A host with its own dispatch table uses
:func:`instance_tools` and registers each pair itself.

**Every answer classifies itself.** A failure returns ``ok: false`` with an
``error_kind`` and ``retryable`` instead of raising: an agent that gets an
exception's text cannot tell "that template does not exist" from "no port is
free yet", and will retry the first and give up on the second.

**Nothing blocks for long.** A tool call runs inside the agent's turn. Launch
returns as soon as the process starts; ``wait_seconds`` covers the common
case of a server that is up in a few seconds, and is clamped because a branch
server syncing its dependencies can take minutes. After that the agent polls
``instance_status``, which is also what moves an instance to ``ready``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from lore_eden.instances.failures import EXPECTED_FAILURES, classify
from lore_eden.instances.manager import InstanceManager
from lore_eden.instances.models import InstanceState, InstanceView
from lore_eden.instances.templates import LaunchRequest
from lore_eden.mcp.tools import ToolDefinition, ToolHandler, ToolRegistry

#: Longest a launch may wait for readiness inside one tool call.
MAX_WAIT_SECONDS = 30
#: Log lines a status call returns by default, and at most.
DEFAULT_LOG_LINES = 40
MAX_LOG_LINES = 500

ManagerProvider = Callable[[], InstanceManager]


def _summary(view: InstanceView) -> dict[str, Any]:
    """What an agent needs to use an instance: where it is and whether it is up."""
    return {
        "id": view.id,
        "name": view.name,
        "project": view.project,
        "kind": view.kind.value,
        "role": view.role.value,
        "state": view.state.value,
        "url": view.url,
        "managed": view.managed,
        "target_instance_id": view.target_instance_id,
        "labels": view.labels,
        "last_error": view.last_error,
    }


def _ok(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, indent=2)


def _failed(exc: Exception) -> str:
    failure, detail = classify(exc)
    return json.dumps(
        {
            "ok": False,
            "error_kind": failure.kind.value,
            "retryable": failure.retryable,
            "error": detail,
        },
        indent=2,
    )


def _clamp(value: Any, default: int, high: int) -> int:
    if value is None:
        return default
    return max(0, min(int(value), high))


def _next_step(view: InstanceView) -> str:
    if view.state == InstanceState.READY:
        return f"Ready at {view.url}. Stop it with stop_instance when you are done."
    if view.state == InstanceState.EXITED:
        return "It exited. Read its log with instance_status, then stop_instance to dismiss it."
    if view.state == InstanceState.STALLED:
        return "It is running but never answered its readiness check. Read its log with instance_status."
    return "Still starting. Poll instance_status until state is ready — each poll re-checks it."


def _list(manager: InstanceManager, arguments: dict[str, Any]) -> str:
    listing = manager.list(arguments.get("project") or None)
    return _ok(
        instances=[_summary(view) for view in listing.instances],
        unreadable=[bad.model_dump() for bad in listing.unreadable],
        templates=[info.model_dump(mode="json") for info in manager.templates.describe_all()],
    )


def _launch(manager: InstanceManager, arguments: dict[str, Any]) -> str:
    request = LaunchRequest(
        template=arguments["template"],
        name=arguments.get("name") or None,
        params={key: str(value) for key, value in (arguments.get("params") or {}).items()},
    )
    wait = _clamp(arguments.get("wait_seconds"), 0, MAX_WAIT_SECONDS)
    try:
        view = manager.launch(request)
        if wait:
            view = manager.wait_ready(view.id, timeout=wait)
    except EXPECTED_FAILURES as exc:
        return _failed(exc)
    return _ok(instance=_summary(view), next_step=_next_step(view))


def _inspect(manager: InstanceManager, arguments: dict[str, Any]) -> str:
    instance_id = arguments["instance_id"]
    lines = _clamp(arguments.get("log_lines"), DEFAULT_LOG_LINES, MAX_LOG_LINES)
    try:
        view = manager.get(instance_id)
        health = manager.health(instance_id) if view.state != InstanceState.EXITED else None
        logs = manager.logs(instance_id, tail=lines) if lines else None
    except EXPECTED_FAILURES as exc:
        return _failed(exc)
    return _ok(
        instance=_summary(view),
        exit_code=view.exit_code,
        health=health.model_dump() if health else None,
        log_tail=logs.lines if logs else [],
        next_step=_next_step(view),
    )


def _stop(manager: InstanceManager, arguments: dict[str, Any]) -> str:
    instance_id = arguments["instance_id"]
    try:
        manager.stop(instance_id)
    except EXPECTED_FAILURES as exc:
        return _failed(exc)
    return _ok(stopped=instance_id)


_SPECS: list[tuple[str, str, dict[str, Any], Callable[[InstanceManager, dict[str, Any]], str]]] = [
    (
        "list_instances",
        "List local server and client instances — including the main server, which "
        "registers itself — with their state and URL, and the templates you can "
        "launch, with each template's parameters and allowed values.",
        {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Only this project's instances."},
            },
            "additionalProperties": False,
        },
        _list,
    ),
    (
        "launch_instance",
        "Start a server or client from one of the templates list_instances returns, "
        "on a free port. Returns immediately with its URL, in state `starting` "
        "unless `wait_seconds` saw it become ready; poll instance_status after that. "
        "Stop it with stop_instance when you are done — it keeps running otherwise.",
        {
            "type": "object",
            "properties": {
                "template": {"type": "string", "description": "Template name from list_instances."},
                "params": {
                    "type": "object",
                    "description": "Template parameters, by key. Omitted ones take their defaults.",
                    "additionalProperties": {"type": "string"},
                },
                "name": {"type": "string", "description": "Optional label for the instance."},
                "wait_seconds": {
                    "type": "integer",
                    "description": f"Wait up to this long for it to be ready (0-{MAX_WAIT_SECONDS}, default 0).",
                },
            },
            "required": ["template"],
            "additionalProperties": False,
        },
        _launch,
    ),
    (
        "instance_status",
        "One instance's state, a fresh health check, and the tail of its log. "
        "Polling this is what moves a starting instance to ready. Read the log when "
        "an instance exited or is stalled — it says why.",
        {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "description": "Instance id."},
                "log_lines": {
                    "type": "integer",
                    "description": f"Log lines to return (0-{MAX_LOG_LINES}, default {DEFAULT_LOG_LINES}).",
                },
            },
            "required": ["instance_id"],
            "additionalProperties": False,
        },
        _inspect,
    ),
    (
        "stop_instance",
        "Stop an instance you launched, with every process it started, and remove "
        "it from the list; also dismisses one that already exited. A main server "
        "that registered itself cannot be stopped here.",
        {
            "type": "object",
            "properties": {"instance_id": {"type": "string", "description": "Instance id."}},
            "required": ["instance_id"],
            "additionalProperties": False,
        },
        _stop,
    ),
]

#: The tool names, unprefixed, in registration order.
INSTANCE_TOOL_NAMES: tuple[str, ...] = tuple(name for name, *_ in _SPECS)


def instance_tools(provider: ManagerProvider, *, prefix: str = "") -> list[tuple[ToolDefinition, ToolHandler]]:
    """Each tool's definition and a ``(context, arguments) -> str`` handler.

    ``provider`` is called on every call rather than once, so a host can build
    its manager lazily and a test can swap it.
    """

    def bind(run: Callable[[InstanceManager, dict[str, Any]], str]) -> ToolHandler:
        def handler(_context: Any, arguments: dict[str, Any]) -> str:
            return run(provider(), arguments)

        return handler

    return [
        (ToolDefinition(name=f"{prefix}{name}", description=description, input_schema=schema), bind(run))
        for name, description, schema, run in _SPECS
    ]


def register_instance_tools(registry: ToolRegistry, provider: ManagerProvider, *, prefix: str = "") -> None:
    for definition, handler in instance_tools(provider, prefix=prefix):
        registry.register(definition, handler)
