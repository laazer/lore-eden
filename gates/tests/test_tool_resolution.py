"""Tests for finding the linters a gate drives.

The bug these cover: the installed lefthook block runs the gates with a bare
``python3``, but the linters are usually installed in the project's virtualenv.
On a checkout with pylint in ``python/.venv`` the gate reported "pylint did not
run" — a gate not running, wearing the costume of a gate failing.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from precommit_git_diff import (
    _repo_root_for,
    tool_executable,
    tool_interpreter,
    venv_bin_dirs,
)


def _make_venv(root: Path, relpath: str, *, executable: str = "python") -> Path:
    """A virtualenv-shaped directory with one runnable stand-in inside it."""
    bin_dir = root / relpath / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / executable
    shim.write_text("#!/bin/sh\nexit 0\n")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return bin_dir


# -- locating the checkout -------------------------------------------------


def test_a_repository_is_found_by_its_git_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert _repo_root_for(nested) == tmp_path


def test_a_worktree_is_found_although_its_git_is_a_file(tmp_path):
    """`.git` is a file in a linked worktree, which is how this repo is worked."""
    (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    nested = tmp_path / "python"
    nested.mkdir()
    assert _repo_root_for(nested) == tmp_path


def test_a_directory_in_no_repository_is_its_own_root(tmp_path):
    assert _repo_root_for(tmp_path) == tmp_path


# -- finding virtualenvs ---------------------------------------------------


def test_a_venv_at_the_root_is_found(tmp_path):
    (tmp_path / ".git").mkdir()
    expected = _make_venv(tmp_path, ".venv")
    assert expected in venv_bin_dirs(tmp_path)


def test_a_venv_one_level_down_is_found(tmp_path):
    """lore-eden keeps its venv in `python/`, loregarden in `server/`."""
    (tmp_path / ".git").mkdir()
    expected = _make_venv(tmp_path, "python/.venv")
    assert expected in venv_bin_dirs(tmp_path)


def test_a_venv_two_levels_down_is_not_searched_for(tmp_path):
    """Bounded on purpose: this runs on every gate invocation, not just failures."""
    (tmp_path / ".git").mkdir()
    buried = _make_venv(tmp_path, "a/b/.venv")
    assert buried not in venv_bin_dirs(tmp_path)


def test_a_checkout_with_no_venv_yields_nothing(tmp_path):
    (tmp_path / ".git").mkdir()
    assert venv_bin_dirs(tmp_path) == []


# -- choosing an interpreter -----------------------------------------------


def test_this_interpreter_is_used_when_it_has_the_tool():
    """No subprocess probing when the answer is already in hand."""
    assert tool_interpreter("json") == sys.executable


def test_a_venv_interpreter_is_used_when_this_one_lacks_the_tool(tmp_path):
    (tmp_path / ".git").mkdir()
    bin_dir = _make_venv(tmp_path, "python/.venv")
    chosen = tool_interpreter("a_module_that_does_not_exist", start=tmp_path)
    assert chosen == str(bin_dir / "python")


def test_an_unrunnable_candidate_is_skipped_rather_than_raising(tmp_path):
    """A stale venv must not take the gate down before it can report."""
    (tmp_path / ".git").mkdir()
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text("not executable\n")
    assert tool_interpreter("a_module_that_does_not_exist", start=tmp_path) == sys.executable


def test_nothing_found_falls_back_so_the_caller_reports_the_real_failure(tmp_path):
    (tmp_path / ".git").mkdir()
    assert tool_interpreter("a_module_that_does_not_exist", start=tmp_path) == sys.executable


def test_a_name_that_is_not_a_module_is_refused():
    """The name is interpolated into `-c "import ..."`, so it is checked."""
    with pytest.raises(ValueError):
        tool_interpreter("pylint; rm -rf /")


# -- choosing an executable ------------------------------------------------


def test_an_executable_on_path_wins(tmp_path):
    found = tool_executable("sh")
    assert Path(found).name == "sh"
    assert os.access(found, os.X_OK)


def test_an_executable_in_a_venv_is_found_when_path_lacks_it(tmp_path, monkeypatch):
    """shellcheck ships as a pip wheel, so it lands in the venv, not on PATH.

    PATH is emptied rather than left alone, so the assertion is about the
    fallback and not about whichever shellcheck the machine happens to have on
    PATH — which, on any checkout that installed `shellcheck-py`, it does.
    """
    monkeypatch.setenv("PATH", "")
    (tmp_path / ".git").mkdir()
    bin_dir = _make_venv(tmp_path, "python/.venv", executable="shellcheck")
    assert tool_executable("shellcheck", start=tmp_path) == str(bin_dir / "shellcheck")


def test_an_unfindable_executable_returns_its_bare_name(tmp_path, monkeypatch):
    """So the caller still reports "not installed" in its own words."""
    monkeypatch.setenv("PATH", "")
    (tmp_path / ".git").mkdir()
    assert tool_executable("definitely-not-a-real-tool", start=tmp_path) == (
        "definitely-not-a-real-tool"
    )
