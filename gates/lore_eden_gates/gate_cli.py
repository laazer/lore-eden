"""The entry-point guard every configurable gate was writing out for itself.

Three gates had the same `main`: parse argv, run the check, and turn the two
ways of *not having run* into a readable line and exit 1. The duplication check
caught the third copy going in, which is the rule working — the bodies were
identical, so a fix to one would have reached one.

Both exceptions mean the same thing and are kept distinct only in wording. A
malformed `.lore-eden-gates.json` and a scope that would not resolve are both a
gate that never ran, and neither may leave by the success exit. That is the
invariant this whole library exists to hold; this is the one place it is spelled
for a command line.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

_GATE_SCRIPTS = Path(__file__).resolve().parent
if str(_GATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_GATE_SCRIPTS))

from house_rules import HouseRulesError  # noqa: E402 - sys.path is set up just above
from precommit_git_diff import UnexaminableError  # noqa: E402 - same


def guarded(label: str, check: Callable[[], int]) -> int:
    """Run ``check``, reporting either failure-to-run as a loud, readable exit.

    ``label`` is the gate's own prefix — ``pre-commit`` or ``gate`` — so the
    line reads the same as every finding that gate prints.
    """
    try:
        return check()
    except HouseRulesError as exc:
        # A malformed config is not "nothing to report" — it is a gate that
        # never ran. Fail rather than proceed with defaults.
        print(f"{label}: {exc}")
        return 1
    except UnexaminableError as exc:
        # One handler for the one invariant: a scope this run could not resolve
        # and a file it could not read are both things it did not examine.
        print(f"{label}: cannot determine what to examine: {exc}")
        return 1
