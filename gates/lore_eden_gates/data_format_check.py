#!/usr/bin/env python3
"""Configuration and lockfiles, which nothing parsed until something needed them.

The coverage check recorded `*.json`, `*.yml` and `*.toml` as exempt, with the
reason "malformed JSON fails the tool that reads it, which is a faster and more
specific signal than a gate". That is true of a file something reads on every
run — `package.json`, `pyproject.toml` — and false of the rest. A malformed
workflow file is not read until a push, a broken `.lore-eden-gates.json` is not
read until a gate runs in that repo, and a lockfile nothing touches is not read
at all. "Some other tool would notice eventually" is not a check.

So: parse them. A file that does not parse is a file nobody can rely on, and
this says so at the commit rather than at the deploy.

## What this can promise, and what it cannot

JSON is `json`, in the standard library, on every interpreter these gates
support. It is checked always.

YAML and TOML are not. `tomllib` arrives in 3.11 and these gates floor at 3.10
— the same reason `house_rules` is JSON and not TOML — and PyYAML is a
third-party package. The installed lefthook block runs a bare `python3`, so
neither can be assumed.

Rather than skip in silence, this reports on every run which formats it
checked and which it could not, by name. A repo that wants the other two
installs a parser; a repo that does not at least knows what it is getting. The
alternative — refusing outright — would make the gate unusable on the bare
interpreter this library exists to run on.

Usage:
    data_format_check.py [staged files...]
    data_format_check.py --repo PATH --scope worktree|staged|branch
"""

from __future__ import annotations

import json
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
    git_repo_root,
    read_source_text,
    repo_relative_posix,
    resolve_gate_scope,
)

JSON_SUFFIXES = frozenset({".json"})
YAML_SUFFIXES = frozenset({".yml", ".yaml"})
TOML_SUFFIXES = frozenset({".toml"})
GRADED_SUFFIXES = JSON_SUFFIXES | YAML_SUFFIXES | TOML_SUFFIXES


def _yaml_loader():
    try:
        import yaml  # noqa: PLC0415 - optional, and absence is a reported state
    except ImportError:
        return None
    return yaml.safe_load


def _toml_loader():
    for module in ("tomllib", "tomli"):
        try:
            loaded = __import__(module)
        except ImportError:
            continue
        return loaded.loads
    return None


@dataclass(frozen=True)
class Invocation:
    files: list[Path]
    repo: Path | None
    diff_scope: str
    base_ref: str
    label: str


def data_files_in_scope(
    repo: Path | None, candidates: Sequence[Path], discovered: bool
) -> list[Path]:
    return [path for path in candidates if path.suffix in GRADED_SUFFIXES]


def parse_failure(path: Path, text: str, yaml_load, toml_load) -> str | None:
    """The reason this file does not parse, or None. Never a silent pass."""
    suffix = path.suffix
    try:
        if suffix in JSON_SUFFIXES:
            json.loads(text)
        elif suffix in YAML_SUFFIXES and yaml_load is not None:
            # `safe_load`, not `load`: a gate that executes what it is grading
            # is a worse problem than the one it was added to find.
            yaml_load(text)
        elif suffix in TOML_SUFFIXES and toml_load is not None:
            toml_load(text)
        else:
            return None
    except Exception as exc:  # noqa: BLE001 - every parser raises its own type
        # Deliberately broad, and deliberately not swallowed: the three parsers
        # raise three unrelated exception types, and narrowing to the ones seen
        # so far is how the fourth becomes a traceback in a commit hook.
        return f"{type(exc).__name__}: {exc}"
    return None


def main(argv: list[str]) -> int:
    invocation = parse_argv(argv)
    return guarded(invocation.label, lambda: _check(invocation))


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
            if Path(arg).suffix in GRADED_SUFFIXES:
                files.append(Path(arg))
            index += 1
    repo = Path(repo_arg).resolve() if repo_arg else git_repo_root()
    label = "pre-commit" if diff_scope == STAGED and repo_arg is None else "gate"
    return Invocation(files, repo, diff_scope, base_ref, label)


def _check(invocation: Invocation) -> int:
    run = resolve_gate_scope(
        label=invocation.label,
        repo=invocation.repo,
        diff_scope=invocation.diff_scope,
        base_ref=invocation.base_ref,
        explicit_files=invocation.files,
        select=data_files_in_scope,
    )
    yaml_load, toml_load = _yaml_loader(), _toml_loader()
    unavailable = [
        name
        for name, loader in (("YAML", yaml_load), ("TOML", toml_load))
        if loader is None
    ]
    if unavailable:
        # Named on every run, in both invocation forms. A format this cannot
        # read and does not mention is indistinguishable from one it read and
        # found clean.
        print(
            f"{invocation.label}: {' and '.join(unavailable)} not checked — no parser on "
            "this interpreter (`pip install pyyaml tomli`); JSON is checked always."
        )
    if not run.files:
        return 0

    # Whole-file, not diff-scoped, and it is the one gate here that should be:
    # a file either parses or it does not, and half a syntax error is not a
    # thing. A malformed line nobody touched still breaks every reader.
    failures = []
    for path in run.files:
        reason = parse_failure(
            path, read_source_text(path, repo=run.repo), yaml_load, toml_load
        )
        if reason is not None:
            failures.append(f"   {repo_relative_posix(path, run.repo)}: {reason}")

    if not failures:
        print(f"{invocation.label}: data formats parse.")
        return 0

    print(f"{invocation.label}: ❌ files that do not parse:")
    for failure in failures:
        print(failure)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
