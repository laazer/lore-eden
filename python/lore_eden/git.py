"""The single chokepoint for shelling out to git — the one the gate demands.

`py_git_subprocess_check` refuses a bare `subprocess.run(["git", ...])` and
tells the repo to route it through "the helper we already have". lore-eden
shipped that gate and no helper, so the rule was inert in every adopting repo
until each wrote its own wrapper. This is the wrapper.

## Why the rule exists

Git exports `GIT_DIR` — and, depending on the command, `GIT_WORK_TREE` and
`GIT_INDEX_FILE` — into the environment of hooks and of anything they spawn.
Those variables bind the child to *that* repository and **override `cwd`**. So
`git -C /some/workspace status` run from inside a hook silently operates on the
repo the hook fired for.

That is not hypothetical: loregarden's pre-push suite hit it. Tests building
throwaway repos in `tmp_path` inherited a worktree's `GIT_DIR` and died on
`git add .` with exit 128 — an error naming neither the variable nor the
repository it had been redirected to.

`GIT_CONFIG_COUNT` with its `GIT_CONFIG_KEY_<n>`/`VALUE_<n>` pairs is scrubbed
for a subtler reason: one pair setting `core.attributesFile` can mark sources
`-diff`, which empties a diff while `--name-only` still lists the file. A
scrubbed child cannot be handed a config that makes its own output lie.

## `gh` too

`gh` resolves its repository through git, so it inherits the same bindings and
needs the same treatment. :func:`scrubbed_env` is public for that.

## The same list lives in the gate

`lore_eden_gates.precommit_git_diff` carries its own copy, because the gates are
stdlib-only and installed by filesystem path while this package is installed by
pip — neither can import the other. `tests/test_git.py` asserts the two lists
agree, so the duplication is guarded rather than merely regretted.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

#: Variables that rebind git to a different repository, index, object store or
#: pathspec root. `GIT_DIR`/`GIT_WORK_TREE` are the ones that caused the
#: worktree breakage; the rest travel with them out of a hook and would point an
#: otherwise scrubbed child back at the wrong repo state.
GIT_LOCATION_ENV_VARS: tuple[str, ...] = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG_COUNT",
)

#: Ad-hoc config git reads from numbered pairs, counted by `GIT_CONFIG_COUNT`.
GIT_CONFIG_ENV_PREFIXES: tuple[str, ...] = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def scrubbed_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """``env`` (default: the ambient environment) minus git's bindings and config.

    An overlay on the real environment, not a replacement: a child still needs
    `PATH`, `HOME`, and whatever else the host set. Only the variables that
    redirect git are removed.
    """
    base = dict(os.environ if env is None else env)
    for name in GIT_LOCATION_ENV_VARS:
        base.pop(name, None)
    for name in [key for key in base if key.startswith(GIT_CONFIG_ENV_PREFIXES)]:
        base.pop(name, None)
    return base


def run_git(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
    timeout: float | None = None,
) -> "subprocess.CompletedProcess[str]":
    """Run ``git *args`` with the repo-binding variables removed.

    The parameters are named rather than a ``**kwargs`` passthrough. The source
    passed one, and it reads as a thin wrapper — but a bag documents nothing,
    hides a typo'd keyword as silence, and would let a caller pass its own
    ``env=`` straight through, defeating the entire purpose of the function.
    These four are what call sites actually vary; anything else should use
    :func:`scrubbed_env` and ``subprocess.run`` directly, deliberately.

    ``check`` defaults to False because a non-zero exit from git is usually an
    answer — "not a repository", "no such ref" — rather than a failure, and
    call sites that want the raise ask for it.
    """
    return subprocess.run(  # noqa: S603 - argv is caller-supplied, never a shell string
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        env=scrubbed_env(env),
        check=check,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
    )


def run_gh(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
    timeout: float | None = None,
) -> "subprocess.CompletedProcess[str]":
    """Run ``gh *args`` with the same scrub.

    Separate from :func:`run_git` rather than a ``binary`` parameter, because
    the gate names two commands and a caller reading ``run_git(["pr", "view"])``
    would rightly be confused.
    """
    return subprocess.run(  # noqa: S603 - argv is caller-supplied, never a shell string
        ["gh", *args],
        cwd=str(cwd) if cwd is not None else None,
        env=scrubbed_env(env),
        check=check,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
    )
