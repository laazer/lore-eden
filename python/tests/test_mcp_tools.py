"""Regressions on the tool registry itself.

The registry's behaviour is covered through the protocol and router tests; what
lives here is the structure of the class, which those cannot see.
"""

from __future__ import annotations

import ast
import inspect

from lore_eden.mcp import tools
from lore_eden.mcp.tools import ToolDefinition, ToolRegistry


def _registry_class_def() -> ast.ClassDef:
    tree = ast.parse(inspect.getsource(tools))
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "ToolRegistry"
    ]
    assert len(found) == 1, f"expected one ToolRegistry, found {len(found)}"
    return found[0]


def test_no_method_on_the_registry_shadows_another() -> None:
    """`names` was defined twice, and the second definition won silently.

    `register` writes `_definitions` and `_handlers` together and raises on a
    duplicate, so both bodies returned the same list — which is exactly why
    nobody noticed, and why editing the dead one would have changed nothing.
    """
    defined = [
        node.name
        for node in _registry_class_def().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    duplicated = sorted({name for name in defined if defined.count(name) > 1})

    assert not duplicated, f"shadowed method(s): {duplicated}"


def test_names_and_definitions_agree_on_what_is_registered() -> None:
    """The invariant that made the shadowing harmless — now asserted rather than assumed."""
    registry = ToolRegistry()
    for name in ("beta", "alpha", "gamma"):
        registry.register(ToolDefinition(name=name, description=name), lambda **_: None)

    assert registry.names() == ["beta", "alpha", "gamma"], "registration order is the contract"
    assert [payload["name"] for payload in registry.definitions()] == registry.names()


def test_a_registered_name_is_reported_as_contained() -> None:
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="alpha", description="a"), lambda **_: None)

    assert "alpha" in registry
    assert "absent" not in registry
