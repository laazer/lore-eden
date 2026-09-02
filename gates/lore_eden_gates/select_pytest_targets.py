#!/usr/bin/env python3
"""Which test files can the commits being pushed actually break?

A large suite costs minutes on an idle machine and considerably worse on a busy
one. Most pushes touch a handful of modules, and running
everything to learn that is the reason people reach for `--no-verify`. This
selects the test files that reach the changed modules through the import graph,
and runs only those — seconds, for a change to a leaf module.

Selection is not free of risk, so the design is biased hard toward running too
much:

* Anything it cannot map means the full suite. A changed `conftest.py`, a
  changed `pyproject.toml`, a non-Python file inside the project, a file
  outside it — all of it falls back, because tests routinely read repo files
  (config, fixtures on disk) that no import graph can see.
* Selecting *zero* tests means the full suite, not a fast pass. "Nothing reaches
  this module" is more often a hole in the graph than a fact about the code.
* String constants naming a package module count as imports, so the MCP tool
  registry and other `importlib.import_module("pkg.x")` dispatch does not slip
  through.

What can still be missed: a test that reaches changed code through a data file,
a subprocess, or a name assembled at runtime. CI still runs the whole suite on
every PR, so the cost of a miss is a slower signal, never an unguarded merge.

Usage: select_pytest_targets.py --repo PATH --base REF --package NAME
         [--project-prefix DIR] [--tests-dirname NAME] [--ignore-prefix DIR]
Prints one test path per line. Exit 2 means "run the full suite" and the reason
goes to stderr.
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

EXIT_SELECTED = 0
EXIT_RUN_EVERYTHING = 2

#: Changing one of these changes how every test runs, so nothing narrower is honest.
_SHARED_TEST_FILES = frozenset({"conftest.py", "factories.py", "worktree_helpers.py"})


@dataclass(frozen=True)
class Layout:
    """Where this repo keeps the package and tests being selected over.

    The selector walks one package's import graph. Which package, and where it
    sits in the repo, is the only thing that differs between repos — the
    algorithm and its bias toward over-running are not repo-specific.

    ``project_prefix`` is the repo-relative directory holding the Python
    project (``"server"`` for a nested layout, ``""`` when the project is the
    repo root). ``ignore_prefixes`` are repo-relative directories that belong to
    another toolchain entirely and so imply nothing about pytest — a frontend
    directory, say.
    """

    package: str
    project_prefix: str = ""
    tests_dirname: str = "tests"
    ignore_prefixes: tuple[str, ...] = ()

    def within_project(self, path: str) -> bool:
        return path.startswith(f"{self.project_prefix}/") if self.project_prefix else True

    def ignored(self, path: str) -> bool:
        return any(path.startswith(f"{prefix}/") for prefix in self.ignore_prefixes)

    def project_relative(self, path: str) -> str:
        if not self.project_prefix:
            return path
        return path[len(self.project_prefix) + 1 :]

    @property
    def project_label(self) -> str:
        return f"{self.project_prefix}/" if self.project_prefix else "the repo root"

    def package_root(self, repo: Path) -> Path:
        return repo / self.project_prefix / self.package if self.project_prefix else repo / self.package

    def test_root(self, repo: Path) -> Path:
        if self.project_prefix:
            return repo / self.project_prefix / self.tests_dirname
        return repo / self.tests_dirname

    def is_test_path(self, path: str) -> bool:
        prefix = (
            f"{self.project_prefix}/{self.tests_dirname}/"
            if self.project_prefix
            else f"{self.tests_dirname}/"
        )
        return path.startswith(prefix)


def _run_git(args: list[str], repo: Path) -> str:
    """Git through a scrubbed environment: GIT_DIR beats cwd, and a pre-push hook
    in a worktree inherits it pointing at the main checkout."""
    env_blocklist = ("GIT_DIR", "GIT_WORK_TREE")
    env = {k: v for k, v in os.environ.items() if k not in env_blocklist}
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False, env=env
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def changed_files(repo: Path, base: str) -> list[str]:
    out = _run_git(["diff", "--name-only", f"{base}...HEAD"], repo)
    return [line for line in out.splitlines() if line.strip()]


def full_suite_reason(paths: list[str], layout: Layout) -> str | None:
    """Why this change cannot be narrowed, if it cannot."""
    for path in paths:
        if layout.ignored(path):
            continue  # another toolchain's problem, not pytest's
        if not layout.within_project(path):
            return (
                f"{path} is outside {layout.project_label} "
                "(tests read repo files no import graph sees)"
            )
        if layout.is_test_path(path) and Path(path).name in _SHARED_TEST_FILES:
            return f"{path} changes how every test runs"
        if not path.endswith(".py"):
            return f"{path} is not Python (no import edge to follow)"
    return None


def module_name(path: Path, package_root: Path) -> str | None:
    """Dotted name for a file inside the package, e.g. pkg.services.doctor."""
    try:
        rel = path.relative_to(package_root.parent)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


def _imported_modules(tree: ast.AST, package: str) -> set[str]:
    """Package modules this file names — through imports and through strings.

    The string scan is what keeps `importlib.import_module("pkg.mcp.x")` and
    any name-keyed registry from being invisible to the graph.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{package}."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith(package):
                found.add(node.module)
                for alias in node.names:
                    # `from pkg.services import doctor` names a module too.
                    found.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):  # py-org: allow-isinstance (ast node)
            if node.value.startswith(f"{package}."):
                found.add(node.value)
    return found


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def build_graph(
    package_root: Path, test_root: Path, package: str
) -> tuple[dict[str, set[str]], dict[Path, set[str]]]:
    """(module -> modules it imports, test file -> modules it imports)."""
    known: dict[str, Path] = {}
    for py in package_root.rglob("*.py"):
        name = module_name(py, package_root)
        if name:
            known[name] = py

    module_edges: dict[str, set[str]] = {}
    for name, py in known.items():
        tree = _parse(py)
        module_edges[name] = {m for m in _imported_modules(tree, package) if m in known} if tree else set()

    test_edges: dict[Path, set[str]] = {}
    for py in sorted(test_root.rglob("test_*.py")):
        tree = _parse(py)
        test_edges[py] = {m for m in _imported_modules(tree, package) if m in known} if tree else set()

    return module_edges, test_edges


def reachable(seeds: set[str], module_edges: dict[str, set[str]]) -> set[str]:
    """Every package module reachable from these, following imports forward."""
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(module_edges.get(current, ()))
    return seen


def select(repo: Path, paths: list[str], layout: Layout) -> list[Path]:
    """Test files that import a changed module, plus changed tests themselves."""
    package_root = layout.package_root(repo)
    test_root = layout.test_root(repo)

    changed_tests: list[Path] = []
    changed_modules: set[str] = set()
    for rel in paths:
        if not layout.within_project(rel) or not rel.endswith(".py"):
            continue
        path = repo / rel
        if layout.is_test_path(rel):
            # A deleted test file cannot be run; its absence is not a gap.
            if path.exists():
                changed_tests.append(path)
            continue
        name = module_name(path, package_root)
        if name:
            changed_modules.add(name)

    if not changed_modules:
        return sorted(set(changed_tests))

    module_edges, test_edges = build_graph(package_root, test_root, layout.package)
    selected = set(changed_tests)
    for test_file, imports in test_edges.items():
        if reachable(imports, module_edges) & changed_modules:
            selected.add(test_file)
    return sorted(selected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--base", required=True)
    parser.add_argument(
        "--package",
        required=True,
        help="Import package whose graph is walked, e.g. myapp.",
    )
    parser.add_argument(
        "--project-prefix",
        default="",
        help="Repo-relative directory holding the Python project (e.g. server). "
        "Omit when the project is the repo root.",
    )
    parser.add_argument(
        "--tests-dirname",
        default="tests",
        help="Test directory name inside the project (default: tests).",
    )
    parser.add_argument(
        "--ignore-prefix",
        action="append",
        default=[],
        dest="ignore_prefixes",
        help="Repo-relative directory belonging to another toolchain, which "
        "therefore implies nothing about pytest (e.g. client). Repeatable.",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    layout = Layout(
        package=args.package,
        project_prefix=args.project_prefix.strip("/"),
        tests_dirname=args.tests_dirname,
        ignore_prefixes=tuple(p.strip("/") for p in args.ignore_prefixes),
    )

    paths = changed_files(repo, args.base)
    if not paths:
        print(f"no diff against {args.base}", file=sys.stderr)
        return EXIT_RUN_EVERYTHING

    reason = full_suite_reason(paths, layout)
    if reason:
        print(reason, file=sys.stderr)
        return EXIT_RUN_EVERYTHING

    selected = select(repo, paths, layout)
    if not selected:
        # More often a hole in the graph than a module nothing tests.
        print("no test file reaches the changed modules", file=sys.stderr)
        return EXIT_RUN_EVERYTHING

    for path in selected:
        print(path.relative_to(repo).as_posix())
    return EXIT_SELECTED


if __name__ == "__main__":
    raise SystemExit(main())
