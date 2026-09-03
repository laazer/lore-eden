#!/usr/bin/env python3
"""Flag defensive string normalization: re-normalizing a value on every read.

``if str(value).strip().lower() == "done":`` says the author did not trust the
value to arrive normalized. Usually it already is — normalized at construction,
or constrained by the type — and the call is noise that hides where the real
invariant lives. Where it is *not* already normalized, the fix is to normalize
at the boundary once, not at every comparison.

Whitelisted: a function whose *body* is a single line and whose name starts with
``is``/``to``/``sanitize`` (with or without a leading underscore). That function
*is* the boundary — normalizing is its whole job. (The predecessor required the
whole ``def`` to occupy one physical line, which excluded the ordinary two-line
spelling of exactly the helper it meant to allow.)

Waiver: ``# py-defensive: allow`` on the offending line.

Usage:
    py_defensive_normalization_check.py [staged files...]
    py_defensive_normalization_check.py --repo PATH --scope worktree|staged|branch

This replaces a bash/heredoc predecessor that matched with a regex, interpolated
its file list into a Python program as text, and swallowed every exception per
file — so a file it could not read was reported exactly like a file with nothing
wrong. The rule survived that implementation; none of the implementation did.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

_GATE_SCRIPTS = Path(__file__).resolve().parent
if str(_GATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_GATE_SCRIPTS))

from interpreter import require_python  # noqa: E402 - sys.path is set up just above

# Before the imports below, not inside main(): the version this gate needs is
# needed to *import* them, so a check that ran later would never run at all.
require_python()

from precommit_git_diff import (  # noqa: E402 - sys.path is set up just above
    DEFAULT_BASE_REF,
    STAGED,
    UnexaminableError,
    git_repo_root,
    parse_python_source,
    read_source_text,
    resolve_gate_scope,
)
from py_organization_check import python_files_in_scope  # noqa: E402 - same

#: Methods that normalize a string in place of trusting its source.
_NORMALIZERS = frozenset({"strip", "lstrip", "rstrip", "lower", "upper", "casefold"})

#: A one-line function with one of these prefixes is the normalization boundary.
_BOUNDARY_PREFIXES = ("sanitize", "_sanitize", "is", "_is", "to", "_to")

ALLOW_MARKER = "py-defensive: allow"

_HELP = """
💡 Fix: normalize once, at the boundary.

Give the value a normalized form where it is constructed — a Pydantic validator,
a factory, an enum — and compare it directly:

    if status is Status.DONE:

A one-line `is_*` / `to_*` / `sanitize_*` helper is the exception: normalizing is
what it is for, and it is whitelisted.

Waiver for a genuinely foreign value: `# py-defensive: allow` on the line.
"""


def _normalizing_chain_over_str(node: ast.expr) -> bool:
    """True for ``str(x).strip().lower()`` and similar normalizing chains.

    Walks down the attribute-call chain collecting normalizer names; the smell
    is a chain of them bottoming out in a ``str(...)`` coercion. A bare
    ``value.lower()`` is not flagged — without the ``str()`` there is no claim
    that the type was doubted.
    """
    saw_normalizer = False
    current = node
    while (
        isinstance(current, ast.Call)
        and isinstance(current.func, ast.Attribute)
        and current.func.attr in _NORMALIZERS
    ):
        saw_normalizer = True
        current = current.func.value

    if not saw_normalizer:
        return False
    return (
        isinstance(current, ast.Call)
        and isinstance(current.func, ast.Name)
        and current.func.id == "str"
    )


def _boundary_functions(tree: ast.AST) -> Set[int]:
    """Line numbers inside a one-line ``is_``/``to_``/``sanitize_`` function."""
    lines: Set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = node.end_lineno or node.lineno
        body_start = node.body[0].lineno if node.body else node.lineno
        body_end = node.body[-1].end_lineno or body_start if node.body else node.lineno
        if body_end - body_start > 0:
            continue  # more than one line of body: not a thin boundary helper
        if not node.name.startswith(_BOUNDARY_PREFIXES):
            continue
        lines.update(range(node.lineno, end + 1))
    return lines


def violations_in(path: Path, *, repo: Optional[Path]) -> List[Tuple[int, str]]:
    """(line, source) for each defensive comparison in ``path``.

    Raises rather than returning empty when the file cannot be read or parsed:
    "found nothing" and "never looked" are the same value to a caller, and the
    predecessor returned the second while meaning the first.
    """
    content = read_source_text(path, repo=repo)
    tree = parse_python_source(content, path)
    source_lines = content.splitlines()
    exempt = _boundary_functions(tree)

    found: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        operands = [node.left, *node.comparators]
        if not any(_normalizing_chain_over_str(operand) for operand in operands):
            continue
        lineno = node.lineno
        if lineno in exempt:
            continue
        text = source_lines[lineno - 1] if 0 < lineno <= len(source_lines) else ""
        if ALLOW_MARKER in text:
            continue
        found.append((lineno, text.strip()))
    return found


@dataclass
class Invocation:
    """How this run was asked to scope itself."""

    files: List[Path]
    repo: Optional[Path]
    diff_scope: str
    base_ref: str
    label: str


def parse_argv(argv: List[str]) -> Invocation:
    files: List[Path] = []
    repo_arg: Optional[str] = None
    diff_scope = STAGED
    base_ref = DEFAULT_BASE_REF
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--repo" and index + 1 < len(argv):
            repo_arg, index = argv[index + 1], index + 2
        elif arg == "--scope" and index + 1 < len(argv):
            diff_scope, index = argv[index + 1], index + 2
        elif arg == "--base" and index + 1 < len(argv):
            base_ref, index = argv[index + 1], index + 2
        else:
            if arg.endswith(".py"):
                files.append(Path(arg))
            index += 1
    repo = Path(repo_arg).resolve() if repo_arg else git_repo_root()
    label = "pre-commit" if diff_scope == STAGED and repo_arg is None else "gate"
    return Invocation(files, repo, diff_scope, base_ref, label)


def _graded(repo: Optional[Path], candidates: Sequence[Path], discovered: bool) -> List[Path]:
    in_scope = python_files_in_scope(repo, candidates, discovered)
    return [path for path in in_scope if "tests" not in path.parts and not path.name.startswith("test_")]


def main(argv: List[str]) -> int:
    invocation = parse_argv(argv)
    try:
        return _check(invocation)
    except UnexaminableError as exc:
        print(f"{invocation.label}: cannot determine what to examine: {exc}")
        return 1


def _check(invocation: Invocation) -> int:
    run = resolve_gate_scope(
        label=invocation.label,
        repo=invocation.repo,
        diff_scope=invocation.diff_scope,
        base_ref=invocation.base_ref,
        explicit_files=invocation.files,
        select=_graded,
    )
    if not run.files:
        return 0

    failures: List[str] = []
    for path in run.files:
        touched = run.touched_lines(path)
        for lineno, text in violations_in(path, repo=run.repo):
            if touched is not None and lineno not in touched:
                continue
            failures.append(f"   {path}:{lineno}: {text}")

    if not failures:
        if invocation.label == "gate":
            print("gate: defensive-normalization check passed.")
        return 0

    print(f"{invocation.label}: ❌ Defensive string normalization:")
    print("   Re-normalizing on read hides where the value is actually constrained.")
    print()
    for failure in failures:
        print(failure)
    print(_HELP)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
