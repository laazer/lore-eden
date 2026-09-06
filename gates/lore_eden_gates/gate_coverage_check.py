#!/usr/bin/env python3
"""Every tracked file is graded by something, or says in writing why it is not.

The other gates answer "is this file correct". This one answers the question
nobody was asking: **is anything looking at this file at all.**

It exists because of what a sweep of this repository turned up. Six gates, all
green, and 45 tracked files that no gate had ever opened — among them every
stylesheet, every shell script including the ones the git hooks execute, and
every CI workflow. Nothing was wrong with the gates. The globs simply named
``.py``, ``.ts`` and ``.tsx``, and a commit touching only the other files ran
the whole pre-commit stage in 0.08 seconds and printed "no files for
inspection" seven times. A clean run over nothing at all, which is the exact
shape this library exists to refuse — one level up, at the file type rather
than the file.

Coverage is a whole-repository property, so this is not diff-scoped. It is also
cheap: it reads the index, not the files.

An uncovered path is not automatically a defect. Plenty of file types genuinely
need no gate. What they need is for somebody to have *decided* that, which is
what the configuration records::

    "ungated_globs": {
      "*.md": "prose; reviewed by reading, and no linter this repo agrees with",
      "LICENSE": "verbatim licence text"
    }

A reason, not a bare list. An exemption nobody had to justify is how a file type
goes ungraded for a year without anyone choosing it — and the reason is the part
a reader needs when the answer changes.

Usage:
    gate_coverage_check.py --repo PATH
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

_GATE_SCRIPTS = Path(__file__).resolve().parent
if str(_GATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_GATE_SCRIPTS))

from interpreter import require_python  # noqa: E402 - sys.path is set up just above

require_python()

from house_rules import HouseRulesError, load_house_rules  # noqa: E402
from precommit_git_diff import (  # noqa: E402
    UnexaminableError,
    git_repo_root,
    scrubbed_git_env,
)

#: Which shipped gate grades which suffix. The map is here rather than derived,
#: because "what this library covers" is a fact about the library and a gate
#: that guessed it from its own filename would answer differently after a
#: rename.
GATED_SUFFIXES: dict[str, str] = {
    ".py": "py_organization_check, py_silent_except_check, py_git_subprocess_check, "
    "py_defensive_normalization_check",
    ".ts": "ts_organization_check",
    ".tsx": "ts_organization_check",
    ".css": "css_organization_check",
}


def tracked_paths(repo: Path) -> list[str]:
    """Every path in the index, decoded.

    The index rather than a walk: a file that is not tracked is not something
    this repository ships, and walking would grade `node_modules` and every
    build artifact into the bargain.
    """
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--"],
        cwd=repo,
        env=scrubbed_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit {proc.returncode}"
        raise UnexaminableError(f"`git ls-files` failed in {repo}: {detail}")
    # -z, not the quoted form: this is the one caller that wants raw bytes back
    # rather than git's C-quoting, and splitting on NUL cannot be confused by a
    # newline in a path.
    return [name for name in proc.stdout.split("\0") if name]


def uncovered(paths: list[str], ungated: dict[str, str]) -> list[str]:
    """Tracked paths no gate grades and no exemption names."""
    out = []
    for path in paths:
        if Path(path).suffix in GATED_SUFFIXES:
            continue
        if any(
            fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(Path(path).name, glob)
            for glob in ungated
        ):
            continue
        out.append(path)
    return sorted(out)


def unused_globs(paths: list[str], ungated: dict[str, str]) -> list[str]:
    """Exemptions that match nothing any more.

    A stale exemption is a decision still being applied to a file type the repo
    no longer has — and, worse, one that would silently cover a *new* file that
    happens to match. Reported so the list stays a description of the repo
    rather than a history of it.
    """
    return sorted(
        glob
        for glob in ungated
        if not any(
            fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(Path(path).name, glob)
            for path in paths
        )
    )


def main(argv: list[str]) -> int:
    repo_arg = None
    index = 0
    while index < len(argv):
        if argv[index] == "--repo" and index + 1 < len(argv):
            repo_arg, index = argv[index + 1], index + 2
        else:
            index += 1
    repo = Path(repo_arg).resolve() if repo_arg else git_repo_root()
    if repo is None:
        print("gate: coverage check needs a repository; pass --repo")
        return 1

    try:
        rules = load_house_rules(repo)
        paths = tracked_paths(repo)
    except (HouseRulesError, UnexaminableError) as exc:
        print(f"gate: {exc}")
        return 1

    ungated = rules.ungated_globs
    missing = uncovered(paths, ungated)
    stale = unused_globs(paths, ungated)
    graded = sum(1 for p in paths if Path(p).suffix in GATED_SUFFIXES)

    # The counts, always. "Coverage passed" over an empty index is the failure
    # this rule is about, so the numbers are the finding even on success.
    print(
        f"gate: examined {len(paths)} tracked file(s) — {graded} graded by a gate, "
        f"{len(paths) - graded - len(missing)} exempted by name, {len(missing)} uncovered"
    )

    if stale:
        print("gate: file coverage — these exemptions match nothing:")
        for glob in stale:
            print(f" - {glob}: {ungated[glob]}")
    if missing:
        print("gate: file coverage — no gate grades these, and nothing says why:")
        for path in missing:
            print(f" - {path}")
        print()
        print("Add a gate for the type, or record the decision in .lore-eden-gates.json:")
        print('  "ungated_globs": { "*.ext": "why this needs no gate" }')
    if stale or missing:
        return 1

    print("gate: file coverage passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
