"""The two isinstance/getattr rule changes made during extraction.

Both came out of the gates failing on their own source. That is worth being
precise about: the rules are diff-scoped, so in the repo they were written in
these lines were old and never fired. Extraction made every line new, which is
the first time the rules were ever pointed at an AST walker.
"""

from __future__ import annotations

AST_VISITOR = '''"""Walks an AST, which is the only way to walk an AST."""

import ast


def count_calls(tree: ast.AST) -> int:
    total = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            total += 1
    return total
'''

PAYLOAD_CHECK = '''"""Hand-rolled schema check on an untyped payload."""


def handle(payload) -> int:
    if isinstance(payload, dict):
        return len(payload)
    return 0
'''

WAIVED_PAYLOAD_CHECK = '''"""Hand-rolled schema check, waived."""


def handle(payload) -> int:
    if isinstance(payload, dict):  # py-org: allow-isinstance
        return len(payload)
    return 0
'''

GETATTR_USE = '''"""Reaches for an attribute dynamically."""


def end_of(node):
    return getattr(node, "end_lineno", None)
'''

WAIVED_GETATTR_USE = '''"""Reaches for an attribute dynamically, waived."""


def end_of(node):
    return getattr(node, "end_lineno", None)  # py-org: allow-dynamic
'''


def run(repo):
    return repo.gate("py_organization_check.py", "--repo", str(repo.root), "--scope", "worktree")


def test_ast_node_type_tests_are_exempt(nested_repo):
    """`isinstance(node, ast.Call)` has no fix: you cannot model an ast.Call
    with Pydantic, nor add a method to it to dispatch on. A finding with no
    possible response is noise on every AST walker in every repo."""
    nested_repo.write("server/myapp/visitor.py", AST_VISITOR)

    result = run(nested_repo)

    assert result.returncode == 0, result.stdout


def test_payload_shape_checks_are_still_flagged(nested_repo):
    """The exemption must not have blunted the rule it lives in."""
    nested_repo.write("server/myapp/handler.py", PAYLOAD_CHECK)

    result = run(nested_repo)

    assert result.returncode == 1, result.stdout
    assert "isinstance" in result.stdout


def test_isinstance_waiver_still_works(nested_repo):
    nested_repo.write("server/myapp/handler.py", WAIVED_PAYLOAD_CHECK)

    result = run(nested_repo)

    assert result.returncode == 0, result.stdout


def test_getattr_is_flagged_by_default(nested_repo):
    nested_repo.write("server/myapp/reader.py", GETATTR_USE)

    result = run(nested_repo)

    assert result.returncode == 1, result.stdout
    assert "getattr" in result.stdout


def test_getattr_waiver_is_honoured(nested_repo):
    """The dynamic-access rule shipped with no escape hatch at all, which left
    reaching for an optional attribute on a foreign object with no answer short
    of disabling the gate."""
    nested_repo.write("server/myapp/reader.py", WAIVED_GETATTR_USE)

    result = run(nested_repo)

    assert result.returncode == 0, result.stdout


LOCAL_PROTOCOL_DISPATCH = '''"""Dispatches on a protocol declared right here."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Segments(Protocol):
    def parts(self) -> tuple[str, ...]:
        ...


def render(value: object) -> str:
    if isinstance(value, Segments):
        return " · ".join(value.parts())
    return str(value)
'''

QUALIFIED_PROTOCOL_DISPATCH = LOCAL_PROTOCOL_DISPATCH.replace(
    "from typing import Protocol, runtime_checkable", "import typing"
).replace("@runtime_checkable", "@typing.runtime_checkable").replace(
    "class Segments(Protocol):", "class Segments(typing.Protocol):"
)

UNCHECKABLE_PROTOCOL_DISPATCH = LOCAL_PROTOCOL_DISPATCH.replace("@runtime_checkable\n", "")

IMPORTED_PROTOCOL_DISPATCH = '''"""Dispatches on a protocol imported from elsewhere."""

from myapp.contracts import Segments


def render(value: object) -> str:
    if isinstance(value, Segments):
        return " · ".join(value.parts())
    return str(value)
'''

PLAIN_CLASS_DISPATCH = '''"""A type switch on a concrete class we own — the rule's actual target."""


class Segments:
    def parts(self) -> tuple[str, ...]:
        return ()


def render(value: object) -> str:
    if isinstance(value, Segments):
        return " · ".join(value.parts())
    return str(value)
'''


def test_a_locally_declared_runtime_checkable_protocol_is_exempt(nested_repo):
    """The rule's own message recommends `a typing.Protocol`, then flagged it.

    Taking the advice returned the same finding, leaving the waiver comment as
    the only possible response — so the advice was unfollowable.
    """
    nested_repo.write("server/myapp/render.py", LOCAL_PROTOCOL_DISPATCH)

    result = run(nested_repo)

    assert result.returncode == 0, result.stdout


def test_the_exemption_sees_a_typing_qualified_protocol(nested_repo):
    """`@typing.runtime_checkable` on `typing.Protocol` is the same declaration."""
    nested_repo.write("server/myapp/render.py", QUALIFIED_PROTOCOL_DISPATCH)

    result = run(nested_repo)

    assert result.returncode == 0, result.stdout


def test_a_protocol_that_is_not_runtime_checkable_is_still_flagged(nested_repo):
    """Without the decorator that `isinstance` raises TypeError at runtime.

    Exempting it would have the gate bless a line that cannot execute.
    """
    nested_repo.write("server/myapp/render.py", UNCHECKABLE_PROTOCOL_DISPATCH)

    result = run(nested_repo)

    assert result.returncode == 1, result.stdout
    assert "isinstance" in result.stdout


def test_a_protocol_imported_from_another_module_is_still_flagged(nested_repo):
    """One file's AST cannot tell a protocol from a class, and guessing by name
    would let a real type switch through on a badly named class. The waiver
    comment stays the answer for these — a known, documented limit."""
    nested_repo.write("server/myapp/render.py", IMPORTED_PROTOCOL_DISPATCH)

    result = run(nested_repo)

    assert result.returncode == 1, result.stdout
    assert "isinstance" in result.stdout


def test_a_type_switch_on_a_concrete_class_is_still_flagged(nested_repo):
    """The exemption must not have blunted the rule: same call shape, same
    method name, no protocol — and this is what the rule is for."""
    nested_repo.write("server/myapp/render.py", PLAIN_CLASS_DISPATCH)

    result = run(nested_repo)

    assert result.returncode == 1, result.stdout
    assert "isinstance" in result.stdout
