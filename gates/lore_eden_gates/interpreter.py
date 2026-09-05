#!/usr/bin/env python3
"""Refusing an interpreter these gates cannot run on, in words that name the fix.

The gates need Python 3.10: :mod:`py_string_vocab` reads ``match`` statements
through ``ast.match_case`` / ``ast.MatchOr`` / ``ast.MatchValue``, none of which
exist before then. That is a fine requirement — lore-eden's own
``requires-python`` is ``>=3.10`` — and the problem was never the requirement.

It was the report. On 3.9 the first gate died at *import* time with::

    AttributeError: module 'ast' has no attribute 'match_case'

which names neither the interpreter, nor the version needed, nor anything a
reader could act on. And it does not stay inside lore-eden:
``install-workspace-hooks.sh`` writes lefthook commands that run these gates
with a bare ``python3``, so a consuming workspace whose ``python3`` predates
3.10 got that traceback on every commit, from a repo it does not have checked
out.

## Why a bare `python3` in the installed hook, still

Pinning the hook to an absolute interpreter path was considered and rejected:
the path that is correct on the machine running the installer is wrong on
everyone else's, and a hook that hard-codes ``/opt/homebrew/bin/python3.12``
fails on a colleague's laptop in a way that is *harder* to read than this was.
``python3`` is the right thing to invoke. What was missing is this module —
being told, in one line, that the ``python3`` on PATH is too old and what to do
about it.

Nothing here may use syntax or attributes newer than the oldest interpreter it
must be able to report on, which is the whole point of it being a separate
module: it has to survive being imported by the interpreter it is about to
reject.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TextIO

MINIMUM_PYTHON = (3, 10)

# Nonzero so lefthook blocks the commit. Deliberately the same code an ordinary
# gate failure uses: "your interpreter is too old" and "your code broke a rule"
# are both reasons this commit must not proceed, and a hook runner that treated
# them differently would be inventing a distinction nobody asked for.
EXIT_UNSUPPORTED_INTERPRETER = 1


def unsupported_interpreter_message(
    minimum: tuple[int, int],
    actual: Sequence[int],
    executable: str,
) -> str:
    """What to print instead of the AttributeError."""
    needed = ".".join(str(part) for part in minimum)
    found = ".".join(str(part) for part in actual)
    return (
        f"lore-eden gates: these checks need Python {needed} or newer.\n"
        f"  found:  Python {found} at {executable}\n"
        f"  needed: Python {needed}+\n"
        "\n"
        "The gates read `match` statements through AST node types that do not\n"
        f"exist before {needed}.\n"
        "\n"
        "The lefthook block installed by install-workspace-hooks.sh runs these\n"
        "gates with a bare `python3`, so this is whichever `python3` is first on\n"
        f"PATH. Put a {needed}+ interpreter ahead of it and commit again."
    )


def require_python(
    minimum: tuple[int, int] = MINIMUM_PYTHON,
    actual: Sequence[int] | None = None,
    executable: str | None = None,
    stream: TextIO | None = None,
) -> None:
    """Stop with a readable message when the running interpreter is too old.

    Called by every gate entry point *before* it imports anything that needs the
    version, which is the only ordering that works: an import that fails takes
    the check down with it.

    The three arguments exist so a test can drive an interpreter it is not
    running on. Left alone they describe this one.
    """
    running = tuple(actual) if actual is not None else sys.version_info[:3]
    if tuple(running[: len(minimum)]) >= minimum:
        return

    where = executable if executable is not None else sys.executable
    out = stream if stream is not None else sys.stderr
    print(unsupported_interpreter_message(minimum, running, where), file=out)
    raise SystemExit(EXIT_UNSUPPORTED_INTERPRETER)
