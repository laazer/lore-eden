"""Fixtures for driving the gates against real, disposable git repositories.

The gates read git, so the tests build git repos rather than mocking one. Each
is a real checkout in ``tmp_path`` with a real commit, because half of what
these gates do is decide *what to examine* from a diff, and a fake diff would
test the half that was never in question.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

GATES_DIR = Path(__file__).resolve().parent.parent / "lore_eden_gates"
sys.path.insert(0, str(GATES_DIR))


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Git with the repo-binding variables scrubbed.

    Not optional hygiene: pytest may itself be running inside a hook or a
    worktree, where an inherited GIT_DIR beats ``cwd`` and every fixture repo
    silently becomes the outer checkout.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    env.setdefault("GIT_AUTHOR_NAME", "gate tests")
    env.setdefault("GIT_AUTHOR_EMAIL", "gates@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "gate tests")
    env.setdefault("GIT_COMMITTER_EMAIL", "gates@example.invalid")
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, env=env
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr}")
    return proc


class Repo:
    """A disposable git repository a gate can be pointed at."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, relpath: str, content: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def commit(self, message: str = "wip") -> None:
        run_git(["add", "-A"], self.root)
        run_git(["commit", "-m", message], self.root)

    def stage(self, *relpaths: str) -> None:
        run_git(["add", *relpaths], self.root)

    def gate(
        self, script: str, *args: str, env_overlay: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        """Run a gate as a subprocess, the way lefthook and orchestration do.

        ``env_overlay`` is applied *after* the scrub, so a test can inject a
        hostile ``GIT_DIR`` on purpose — which is the one thing this harness
        otherwise makes impossible, and the thing the shared diff module exists
        to survive.
        """
        runner = ["node"] if script.endswith(".cjs") else [sys.executable]
        env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
        env.update(env_overlay or {})
        return subprocess.run(
            [*runner, str(GATES_DIR / script), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )


def make_repo(root: Path) -> Repo:
    root.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-q", "-b", "main"], root)
    repo = Repo(root)
    repo.write(".gitkeep", "")
    repo.commit("initial")
    return repo


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """An empty repo with one commit, no particular layout."""
    return make_repo(tmp_path / "repo")


@pytest.fixture
def nested_repo(tmp_path: Path) -> Repo:
    """Python under ``server/`` — the nested layout."""
    made = make_repo(tmp_path / "nested")
    made.write("server/pyproject.toml", "[project]\nname='x'\n")
    made.write("server/myapp/__init__.py", "")
    made.commit("layout")
    return made


@pytest.fixture
def asset_repo(tmp_path: Path) -> Repo:
    """Python under ``asset_generation/`` — a layout the gates must detect, not assume."""
    made = make_repo(tmp_path / "asset")
    made.write("asset_generation/python/pyproject.toml", "[project]\nname='x'\n")
    made.write("asset_generation/python/myapp/__init__.py", "")
    made.commit("layout")
    return made


@pytest.fixture
def flat_repo(tmp_path: Path) -> Repo:
    """Python at the repo root."""
    made = make_repo(tmp_path / "flat")
    made.write("pyproject.toml", "[project]\nname='x'\n")
    made.write("myapp/__init__.py", "")
    made.commit("layout")
    return made
