"""Import-graph test selection, against a package that is not the one it came from.

The algorithm was written for a package called `loregarden` nested under
`server/`. These tests use neither, because "it still works for loregarden" was
never the question.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from select_pytest_targets import Layout, build_graph, full_suite_reason, select

NESTED = Layout(package="myapp", project_prefix="server", ignore_prefixes=("client",))
FLAT = Layout(package="myapp")


def build_project(repo, prefix: str) -> None:
    """A package where `api` imports `core`, plus a test per module."""
    base = f"{prefix}/" if prefix else ""
    repo.write(f"{base}myapp/__init__.py", "")
    repo.write(f"{base}myapp/core.py", "VALUE = 1\n")
    repo.write(f"{base}myapp/api.py", "from myapp.core import VALUE\n")
    repo.write(f"{base}myapp/lonely.py", "OTHER = 2\n")
    repo.write(f"{base}tests/test_core.py", "from myapp import core\n\n\ndef test_x():\n    pass\n")
    repo.write(f"{base}tests/test_api.py", "from myapp import api\n\n\ndef test_y():\n    pass\n")
    repo.commit("project")


def test_selects_tests_that_reach_a_changed_module_transitively(nested_repo):
    """test_api imports api, api imports core: changing core must select both."""
    build_project(nested_repo, "server")

    selected = select(nested_repo.root, ["server/myapp/core.py"], NESTED)
    names = {Path(p).name for p in selected}

    assert names == {"test_core.py", "test_api.py"}


def test_works_with_a_flat_layout_and_no_project_prefix(flat_repo):
    build_project(flat_repo, "")

    selected = select(flat_repo.root, ["myapp/core.py"], FLAT)
    names = {Path(p).name for p in selected}

    assert names == {"test_core.py", "test_api.py"}


def test_string_constants_count_as_imports(nested_repo):
    """`import_module("myapp.lonely")` is an edge no AST import records, and
    missing it means a test that does reach the changed code is not run."""
    build_project(nested_repo, "server")
    nested_repo.write(
        "server/tests/test_dynamic.py",
        'from importlib import import_module\n\n\ndef test_z():\n'
        '    import_module("myapp.lonely")\n',
    )
    nested_repo.commit("dynamic")

    selected = select(nested_repo.root, ["server/myapp/lonely.py"], NESTED)

    assert {Path(p).name for p in selected} == {"test_dynamic.py"}


def test_unmappable_change_falls_back_to_the_full_suite():
    reason = full_suite_reason(["server/pyproject.toml"], NESTED)
    assert reason and "not Python" in reason


def test_file_outside_the_project_falls_back(nested_repo):
    reason = full_suite_reason(["docs/readme.md"], NESTED)
    assert reason and "outside server/" in reason


def test_ignored_prefix_does_not_force_a_full_run():
    """A frontend change implies nothing about pytest."""
    assert full_suite_reason(["client/src/App.tsx"], NESTED) is None


def test_shared_test_file_forces_a_full_run():
    reason = full_suite_reason(["server/tests/conftest.py"], NESTED)
    assert reason and "how every test runs" in reason


def test_flat_layout_treats_root_files_as_in_project():
    """With no project prefix everything is in the project, so a root-level
    non-Python file is unmappable rather than out of scope."""
    reason = full_suite_reason(["pyproject.toml"], FLAT)
    assert reason and "not Python" in reason


def test_graph_is_built_for_the_configured_package_only(nested_repo):
    build_project(nested_repo, "server")
    layout = NESTED

    module_edges, test_edges = build_graph(
        layout.package_root(nested_repo.root),
        layout.test_root(nested_repo.root),
        layout.package,
    )

    assert "myapp.api" in module_edges
    assert module_edges["myapp.api"] == {"myapp.core"}
    assert {Path(p).name for p in test_edges} == {"test_core.py", "test_api.py"}


def test_wrong_package_name_finds_no_edges(nested_repo):
    """Guards the parameterization itself: if `package` were ignored and the
    old constant still used, this would still find edges."""
    build_project(nested_repo, "server")
    layout = Layout(package="notmyapp", project_prefix="server")

    module_edges, _ = build_graph(
        NESTED.package_root(nested_repo.root),
        NESTED.test_root(nested_repo.root),
        layout.package,
    )

    assert all(edges == set() for edges in module_edges.values())
