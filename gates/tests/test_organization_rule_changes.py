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
