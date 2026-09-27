"""The worktrees of a repository, as launch targets.

A template launches *a checkout* of a project, and the checkout is a choice
the person launching makes: main, or the worktree holding the change they want
to see. Offered as a closed list rather than a free-text path, because the
answer becomes a working directory for a process — a path outside the
project's own worktrees has no business being one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from lore_eden.git import run_git

logger = logging.getLogger(__name__)

DETACHED = "(detached)"


@dataclass(frozen=True)
class Worktree:
    path: Path
    #: The checked-out branch, `(detached)`, or empty when git could not say.
    branch: str


def list_worktrees(repo_root: Path) -> list[Worktree]:
    """``repo_root`` and every worktree linked to it, the primary first.

    When git cannot answer — not a repository, git missing — the root alone is
    returned and the reason logged at warning: the root is still launchable,
    so a picker that offers only it is degraded, not wrong.
    """
    try:
        result = run_git(["worktree", "list", "--porcelain"], cwd=repo_root)
    except OSError as exc:
        logger.warning("could not list worktrees of %s: %s", repo_root, exc)
        return [Worktree(repo_root, "")]
    if result.returncode != 0:
        logger.warning("git worktree list failed in %s: %s", repo_root, result.stderr.strip())
        return [Worktree(repo_root, "")]
    trees: list[Worktree] = []
    path: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif path is not None and line.startswith("branch "):
            trees.append(Worktree(path, line.removeprefix("branch ").removeprefix("refs/heads/")))
            path = None
        elif path is not None and line == "detached":
            trees.append(Worktree(path, DETACHED))
            path = None
    return trees or [Worktree(repo_root, "")]
