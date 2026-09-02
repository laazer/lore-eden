"""The TypeScript gate, run against repos that have no node_modules of their own.

This is the trap that made the gate un-extractable: it resolved its parser from
a sibling `client/node_modules` by relative path. That worked only while the
gate lived inside the repo it graded — invisible until the moment the library is
installed somewhere else, which is the entire point of the library.
"""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

CLEAN_TSX = """export function Greeting({ name }: { name: string }) {
  return <div>{name}</div>;
}
"""

FETCH_IN_COMPONENT = """export function Widget() {
  const load = async () => {
    const res = await fetch("/api/thing");
    return res.json();
  };
  return <button onClick={load}>go</button>;
}
"""

INSTANCEOF_TERNARY = """export function describe(error: unknown): string {
  const message = error instanceof Error ? error.message : "unknown";
  return message;
}
"""

WAIVED_INSTANCEOF = """export function describe(error: unknown): string {
  const message = error instanceof Error ? error.message : "x"; // ts-org: allow-instanceof
  return message;
}
"""


def ts_repo(repo, relpath: str, content: str):
    """A repo with TypeScript under src/ and deliberately no node_modules.

    The source file is left uncommitted so its lines are *additions* in the
    diff: several of these rules fire only on newly added lines, and committing
    the file first makes the gate examine nothing and pass vacuously.
    """
    repo.write("package.json", '{"name":"graded","private":true}\n')
    repo.commit("layout")
    repo.write(relpath, content)
    return repo


def output(result) -> str:
    """Everything the gate said. Findings go to stderr; the examined-count line
    goes to stdout, and a test that reads only one of them can assert a pass
    the gate never gave."""
    return f"{result.stdout}\n{result.stderr}"


def assert_examined(result) -> None:
    """A pass that examined nothing is not evidence the gate works."""
    assert "examined 0 file(s)" not in output(result), (
        f"gate examined nothing, so this proves nothing:\n{output(result)}"
    )


def run_gate(repo):
    return repo.gate(
        "ts_organization_check.cjs", "--repo", str(repo.root), "--scope", "worktree"
    )


def test_clean_typescript_passes_without_a_parser_in_the_graded_repo(repo):
    """The load-bearing case: the graded repo has no node_modules at all."""
    ts_repo(repo, "src/Greeting.tsx", CLEAN_TSX)
    assert not (repo.root / "node_modules").exists()

    result = run_gate(repo)

    assert result.returncode == 0, output(result)
    assert_examined(result)


def test_finds_a_violation_without_a_parser_in_the_graded_repo(repo):
    """Passing could also mean "parsed nothing", so prove it actually reads the
    AST from a repo that supplies no parser."""
    ts_repo(repo, "src/Widget.tsx", FETCH_IN_COMPONENT)

    result = run_gate(repo)

    assert result.returncode == 1, output(result)
    assert "Widget.tsx" in output(result)


def test_instanceof_ternary_is_flagged(repo):
    ts_repo(repo, "src/errors.ts", INSTANCEOF_TERNARY)

    result = run_gate(repo)

    assert result.returncode == 1, output(result)


def test_instanceof_waiver_is_honoured(repo):
    ts_repo(repo, "src/errors.ts", WAIVED_INSTANCEOF)

    result = run_gate(repo)

    assert result.returncode == 0, output(result)
    assert_examined(result)


def test_repo_with_no_typescript_passes(repo):
    repo.write("README.md", "# nothing\n")
    repo.commit("docs")

    result = run_gate(repo)

    assert result.returncode == 0, output(result)
