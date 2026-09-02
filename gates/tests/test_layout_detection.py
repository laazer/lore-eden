"""Every gate, against three repo layouts it was not written in.

The gates came out of a codebase whose Python lives under ``server/``. The claim
that they detect layout rather than assume it is the load-bearing claim of this
whole package, so it is tested against a nested layout, a differently-nested one,
and a flat one — and a clean repo of each shape must exit 0.
"""

from __future__ import annotations

import pytest

PY_GATES = [
    "py_organization_check.py",
    "py_silent_except_check.py",
    "py_git_subprocess_check.py",
    "py_defensive_normalization_check.py",
]

CLEAN_MODULE = '''"""A module with nothing wrong with it."""


def add(left: int, right: int) -> int:
    return left + right
'''

SILENT_EXCEPT_MODULE = '''"""A module that swallows an exception."""

import json


def load(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        return None
'''


_PACKAGE_DIRS = {
    "nested_repo": "server/myapp",
    "asset_repo": "asset_generation/python/myapp",
    "flat_repo": "myapp",
}


@pytest.fixture(params=sorted(_PACKAGE_DIRS))
def layout_repo(request):
    """One layout per param, with the repo-relative path of its package.

    Resolved lazily: naming all three repo fixtures as parameters would build
    all three for every test, which is three git repos of setup to use one.
    """
    return request.getfixturevalue(request.param), _PACKAGE_DIRS[request.param]


@pytest.mark.parametrize("gate", PY_GATES)
def test_clean_repo_passes_in_every_layout(layout_repo, gate):
    repo, package = layout_repo
    repo.write(f"{package}/clean.py", CLEAN_MODULE)
    repo.stage(f"{package}/clean.py")

    result = repo.gate(gate, "--repo", str(repo.root), "--scope", "worktree")

    assert result.returncode == 0, f"{gate} failed on a clean {package}:\n{result.stdout}"


def test_silent_except_is_found_in_every_layout(layout_repo):
    """The gate has to locate the source root before it can grade anything in
    it; a layout it cannot detect looks exactly like a repo with no findings."""
    repo, package = layout_repo
    repo.write(f"{package}/loader.py", SILENT_EXCEPT_MODULE)
    repo.stage(f"{package}/loader.py")

    result = repo.gate(
        "py_silent_except_check.py", "--repo", str(repo.root), "--scope", "worktree"
    )

    assert result.returncode == 1, f"expected a finding in {package}:\n{result.stdout}"
    assert "loader.py" in result.stdout


def test_worktree_scope_includes_untracked_files(nested_repo):
    """An agent's brand-new module is the least-reviewed code in a run, and it
    is untracked — a scope that skipped it would grade everything but the thing
    most worth grading."""
    nested_repo.write("server/myapp/fresh.py", SILENT_EXCEPT_MODULE)
    # deliberately not staged, not committed

    result = nested_repo.gate(
        "py_silent_except_check.py", "--repo", str(nested_repo.root), "--scope", "worktree"
    )

    assert result.returncode == 1, result.stdout
    assert "fresh.py" in result.stdout


def test_staged_scope_ignores_unstaged_changes(nested_repo):
    nested_repo.write("server/myapp/fresh.py", SILENT_EXCEPT_MODULE)

    result = nested_repo.gate(
        "py_silent_except_check.py", "--repo", str(nested_repo.root), "--scope", "staged"
    )

    assert result.returncode == 0, result.stdout


def test_unrecognized_scope_fails_rather_than_defaulting(nested_repo):
    """A scope the run could not resolve is not an empty scope."""
    result = nested_repo.gate(
        "py_silent_except_check.py", "--repo", str(nested_repo.root), "--scope", "nonsense"
    )

    assert result.returncode == 1
    assert "cannot determine what to examine" in result.stdout


def test_repo_with_no_python_is_a_pass_not_an_error(repo):
    """"No Python here" must exit 0 — these gates run against every workspace,
    including ones with no Python at all."""
    repo.write("README.md", "# nothing to see\n")
    repo.stage("README.md")

    for gate in PY_GATES:
        result = repo.gate(gate, "--repo", str(repo.root), "--scope", "worktree")
        assert result.returncode == 0, f"{gate}:\n{result.stdout}"
