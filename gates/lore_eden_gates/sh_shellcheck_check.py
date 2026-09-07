#!/usr/bin/env python3
"""Shell scripts, which nothing here graded.

The coverage check recorded `*.sh` as a KNOWN GAP: nine tracked scripts, two of
them the ones git executes on every commit and push, and no gate that had ever
read one. Recording a gap is better than not knowing about it, and worse than
closing it — an exemption is a decision, and "we cannot check this" was not the
decision anyone wanted.

shellcheck arrives as a pip wheel (`shellcheck-py`), so this needs no system
package and no new toolchain: the two diff filters already drive `ruff` and
`pylint` the same way, and this repo's own dev extra installs all three.

Run with `-x --source-path=SCRIPTDIR`, both load-bearing. Without `-x`,
shellcheck does not follow a `source`d file and reports SC1091 on every script
that has one — three of ours. Without `--source-path=SCRIPTDIR` it resolves the
`# shellcheck source=` directive against the *current directory* rather than the
script's own, so it still cannot find it and reports SC1091 anyway. The pair is
what makes the difference between three notes nobody can act on and a clean run.

Diff-scoped like every other gate: a finding is reported when the change touched
its line. A script full of pre-existing warnings does not block an unrelated
one-line edit, which is the policy that keeps a gate installed.

Usage:
    sh_shellcheck_check.py [staged files...]
    sh_shellcheck_check.py --repo PATH --scope worktree|staged|branch
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_GATE_SCRIPTS = Path(__file__).resolve().parent
if str(_GATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_GATE_SCRIPTS))

from interpreter import require_python  # noqa: E402 - sys.path is set up just above

require_python()

from gate_cli import guarded  # noqa: E402 - sys.path is set up above
from precommit_git_diff import (  # noqa: E402 - same
    DEFAULT_BASE_REF,
    STAGED,
    UnexaminableError,
    git_repo_root,
    repo_relative_posix,
    resolve_gate_scope,
)

SH_SUFFIX = ".sh"

#: Levels that fail the gate. `style` is the advisory tier and is excluded; a
#: gate that fails on advice is one people turn off rather than argue with.
#:
#: `info` is *in*, and that is the whole reason this reads JSON rather than
#: shellcheck's gcc format. gcc collapses `info` and `style` into one label,
#: `note` — and SC2086, an unquoted variable that word-splits, is `info`. A
#: first draft here excluded notes and passed a planted `rm $UNQUOTED`, which
#: is the single most common shell bug there is. The control caught it.
FAILING_LEVELS = frozenset({"error", "warning", "info"})


@dataclass(frozen=True)
class Invocation:
    files: list[Path]
    repo: Path | None
    diff_scope: str
    base_ref: str
    label: str


def shell_files_in_scope(
    repo: Path | None, candidates: Sequence[Path], discovered: bool
) -> list[Path]:
    return [path for path in candidates if path.suffix == SH_SUFFIX]


def run_shellcheck(paths: Sequence[Path], repo: Path | None) -> list[tuple[str, int, str, str]]:
    """(relpath, line, severity, message) for every finding, or a loud failure.

    Absent shellcheck raises rather than returning nothing. "The linter is not
    installed" and "the scripts are clean" produce the same empty output, and a
    machine missing the tool would otherwise report a pass on every commit —
    which is exactly what `require_tool_ran` exists to refuse for ruff and
    pylint.
    """
    cmd = [
        "shellcheck",
        "-x",
        "--source-path=SCRIPTDIR",
        "-f",
        "json1",
        *[str(p) for p in paths],
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise UnexaminableError(
            "shellcheck is not installed, so no shell script was examined. "
            "Install it (`pip install shellcheck-py`), or remove the shell gate "
            "from this repo's hooks."
        ) from exc
    if proc.returncode not in (0, 1):
        detail = (proc.stderr.strip() or proc.stdout.strip()).splitlines()
        hint = detail[-1] if detail else f"exit {proc.returncode}"
        raise UnexaminableError(f"shellcheck did not run: {hint}")

    try:
        payload = json.loads(proc.stdout or '{"comments": []}')
    except ValueError as exc:
        # Unreadable output is not "no findings". Treating it as clean is a
        # pass over unread scripts.
        raise UnexaminableError(
            f"shellcheck produced output this gate could not read: {exc}"
        ) from exc
    return [
        (
            repo_relative_posix(Path(c["file"]), repo),
            int(c["line"]),
            c["level"],
            f"SC{c['code']}: {c['message']}",
        )
        for c in payload.get("comments", [])
    ]


def parse_argv(argv: list[str]) -> Invocation:
    files: list[Path] = []
    repo_arg: str | None = None
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
            if arg.endswith(SH_SUFFIX):
                files.append(Path(arg))
            index += 1
    repo = Path(repo_arg).resolve() if repo_arg else git_repo_root()
    label = "pre-commit" if diff_scope == STAGED and repo_arg is None else "gate"
    return Invocation(files, repo, diff_scope, base_ref, label)


def main(argv: list[str]) -> int:
    invocation = parse_argv(argv)
    return guarded(invocation.label, lambda: _check(invocation))


def _check(invocation: Invocation) -> int:
    run = resolve_gate_scope(
        label=invocation.label,
        repo=invocation.repo,
        diff_scope=invocation.diff_scope,
        base_ref=invocation.base_ref,
        explicit_files=invocation.files,
        select=shell_files_in_scope,
    )
    if not run.files:
        return 0

    touched_by_rel = {
        repo_relative_posix(path, run.repo): run.touched_lines(path) for path in run.files
    }
    failures = []
    for rel, lineno, severity, message in run_shellcheck(run.files, run.repo):
        if severity not in FAILING_LEVELS:
            continue
        touched = touched_by_rel.get(rel)
        if touched is not None and lineno not in touched:
            continue
        failures.append(f"   {rel}:{lineno}: {severity}: {message}")

    if not failures:
        print(f"{invocation.label}: shellcheck passed.")
        return 0

    print(f"{invocation.label}: ❌ shellcheck:")
    for failure in failures:
        print(failure)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
