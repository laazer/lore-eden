#!/usr/bin/env python3
"""Keep git subprocess calls routed through the shared env-scrubbing helper.

GIT_DIR overrides `cwd`. A `subprocess.run(["git", ...], cwd=repo)` that passes
the ambient environment through therefore operates on whatever repository the
parent was bound to — not `repo`. That is not hypothetical: it was the root
cause of the pre-push worktree failures, and seven services carried the defect.

A repo designates one wrapper that scrubs the repo-binding variables and is the
one place allowed to build a raw git argv. This gate keeps it that way, because
the invariant is otherwise invisible — the broken call looks exactly like the
correct one at the call site.

`gh` is covered too: it resolves the repo by shelling out to git, so it inherits
the same binding.

Escape hatch: a call that passes an explicit `env=` is deliberate about the
child environment and is left alone. That is how a scrubbed `gh` call passes
(`env=scrubbed_git_env()`), and it keeps the gate from blocking a genuinely
custom environment.

**Off unless the repo names its wrapper** in `.lore-eden-gates.json` (see
`house_rules`). Flagging every git call in a repo that has no designated helper
would be an unactionable finding on every one of them.

Usage:
    py_git_subprocess_check.py [staged files...]
    py_git_subprocess_check.py --repo PATH --scope worktree|staged|branch
"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

_GATE_SCRIPTS = Path(__file__).resolve().parent
if str(_GATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_GATE_SCRIPTS))

from house_rules import (  # noqa: E402 - sys.path is set up just above
    HouseRules,
    HouseRulesError,
    load_house_rules,
)
from precommit_git_diff import (  # noqa: E402 - same
    DEFAULT_BASE_REF,
    STAGED,
    UnexaminableError,
    UnexaminableFileError,
    git_repo_root,
    read_source_text,
    resolve_gate_scope,
)
from py_organization_check import python_files_in_scope  # noqa: E402 - same

# Commands that resolve a repository through git's environment.
_GUARDED_COMMANDS: frozenset[str] = frozenset({"git", "gh"})

# subprocess entry points that spawn a child process.
_SPAWN_FUNCTIONS: frozenset[str] = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
    }
)


def _help_text(house_rules: HouseRules) -> str:
    """The remediation, naming the wrapper this repo actually has."""
    return f"""
💡 Fix: route it through the shared helper.

    {house_rules.git_subprocess_helper}

That wrapper is a thin subprocess.run passthrough — check/text/capture_output/timeout
all still mean what they mean — but it strips GIT_DIR, GIT_WORK_TREE and the other
repo-binding variables from the child, so `cwd` actually decides the repository.

Passing an explicit `env=` also satisfies this gate, for calls that are
deliberate about the child environment (e.g. `env=scrubbed_git_env()`).
"""


def _is_exempt(path: Path, house_rules: HouseRules) -> bool:
    """The helper defines the chokepoint; tests legitimately drive git directly.

    Tests build throwaway repos in tmp_path and assert on real git behaviour —
    including guard tests that need an *unscrubbed* call to prove GIT_DIR really
    does hijack one.
    """
    parts = path.parts
    if "tests" in parts or path.name.startswith("test_"):
        return True
    helper = house_rules.git_subprocess_helper_path
    if not helper:
        return False
    helper_parts = tuple(Path(helper).parts)
    return parts[-len(helper_parts) :] == helper_parts


def _spawn_call_name(func: ast.expr) -> str | None:
    """Return the subprocess entry point this call targets, if any."""
    # subprocess.run(...) / sp.run(...)
    if isinstance(func, ast.Attribute) and func.attr in _SPAWN_FUNCTIONS:
        return func.attr
    # run(...) after `from subprocess import run`
    if isinstance(func, ast.Name) and func.id in _SPAWN_FUNCTIONS:
        return func.id
    return None


def _guarded_command(node: ast.Call) -> str | None:
    """The guarded command this call spawns, if it is statically knowable.

    Handles the list form (`["git", "status"]`) and the shell-string form
    (`"git status"`). A command built from a variable is not statically
    knowable; those are skipped rather than guessed at, to keep the gate free
    of false positives.
    """
    if not node.args:
        return None

    first = node.args[0]

    if isinstance(first, ast.List) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):  # py-org: allow-isinstance (ast node)
            name = Path(head.value).name
            return name if name in _GUARDED_COMMANDS else None
        return None

    if isinstance(first, ast.Constant) and isinstance(first.value, str):  # py-org: allow-isinstance (ast node)
        tokens = first.value.split()
        if tokens:
            name = Path(tokens[0]).name
            return name if name in _GUARDED_COMMANDS else None

    return None


def _has_explicit_env(node: ast.Call) -> bool:
    """True when the call decides the child environment itself."""
    return any(kw.arg == "env" for kw in node.keywords)


def violations_in(path: Path, *, repo: Optional[Path]) -> list[tuple[int, str, str]]:
    source = read_source_text(path, repo=repo)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        # A file this gate could not parse is not a file it cleared.
        raise UnexaminableFileError(
            f"{path}:{exc.lineno}: this run could not parse it, so it cannot be "
            f"reported clean ({exc.msg})"
        ) from exc

    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        spawn = _spawn_call_name(node.func)
        if spawn is None:
            continue
        command = _guarded_command(node)
        if command is None or _has_explicit_env(node):
            continue
        found.append((node.lineno, command, spawn))
    return found


@dataclass
class Invocation:
    """How this run was asked to scope itself.

    Two callers: lefthook passes staged file paths and nothing else; an
    orchestration gate passes ``--repo``/``--scope`` and no file list, because it
    is judging whatever an agent just did to a workspace it does not enumerate.
    """

    files: list[Path]
    repo: Path | None
    diff_scope: str
    base_ref: str
    label: str


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
            if arg.endswith(".py"):
                files.append(Path(arg))
            index += 1
    repo = Path(repo_arg).resolve() if repo_arg else git_repo_root()
    label = "pre-commit" if diff_scope == STAGED and repo_arg is None else "gate"
    return Invocation(files, repo, diff_scope, base_ref, label)


def main(argv: list[str]) -> int:
    invocation = parse_argv(argv)
    try:
        return _check(invocation)
    except HouseRulesError as exc:
        # A malformed config is not "nothing to report" — it is a gate that
        # never ran.
        print(f"{invocation.label}: {exc}")
        return 1
    except UnexaminableError as exc:
        print(f"{invocation.label}: cannot determine what to examine: {exc}")
        return 1


def _check(invocation: Invocation) -> int:
    house_rules = load_house_rules(invocation.repo)
    if not house_rules.git_subprocess_enabled:
        # No designated wrapper means no actionable finding. Say so rather than
        # printing a pass, so a repo that meant to enable this can tell the
        # difference between "clean" and "never ran".
        #
        # Printed for *every* invocation form. It was once suppressed unless the
        # label was "gate", which silenced it in the one form that matters most:
        # the pre-commit entry the installer writes passes bare filenames and
        # gets the label "pre-commit". So a repo that installed five gates
        # silently ran four, and the check it most wanted — this one — reported
        # nothing while doing nothing.
        print(
            f"{invocation.label}: git-subprocess check skipped — no "
            "git_subprocess_helper in .lore-eden-gates.json, so there is no "
            "wrapper to require calls to route through."
        )
        return 0

    def graded(repo: Path | None, candidates: Sequence[Path], discovered: bool) -> list[Path]:
        in_scope = python_files_in_scope(repo, candidates, discovered)
        return [path for path in in_scope if not _is_exempt(path, house_rules)]

    run = resolve_gate_scope(
        label=invocation.label,
        repo=invocation.repo,
        diff_scope=invocation.diff_scope,
        base_ref=invocation.base_ref,
        explicit_files=invocation.files,
        select=graded,
    )
    if not run.files:
        return 0

    failures: list[str] = []
    for path in run.files:
        touched = run.touched_lines(path)
        for lineno, command, spawn in violations_in(path, repo=run.repo):
            if touched is not None and lineno not in touched:
                continue
            failures.append(f"   {path}:{lineno}: subprocess.{spawn}([{command!r}, ...]) — no env=")

    if not failures:
        if invocation.label == "gate":
            print("gate: git-subprocess check passed.")
        return 0

    print(f"{invocation.label}: ❌ Unscrubbed git subprocess call (inherits GIT_DIR):")
    print("   GIT_DIR overrides cwd, so this can operate on the wrong repository.")
    print()
    for failure in failures:
        print(failure)
    print(_help_text(house_rules))
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
