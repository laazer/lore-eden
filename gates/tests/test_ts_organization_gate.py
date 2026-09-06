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


class TestScopeComesFromTheResolver:
    """The scope policy this gate used to hand-port, asserted through the gate.

    About 560 lines of `precommit_git_diff.py` were reimplemented in the `.cjs`
    and nothing here exercised them, which is how the two copies drifted a
    version apart without a red build. These run the gate end to end and assert
    the behaviour the resolver is now responsible for, so the next divergence
    is a failure rather than a discovery.
    """

    def test_an_untracked_file_is_graded_whole(self, repo):
        # No diff to scope against. An empty touched-line set here is a pass
        # over the only new file in the run.
        ts_repo(repo, "src/Widget.tsx", FETCH_IN_COMPONENT)
        result = run_gate(repo)
        assert result.returncode == 1, output(result)
        assert "examined 1 file(s)" in output(result)

    def test_the_examined_count_is_taken_after_this_gates_own_filter(self, repo):
        ts_repo(repo, "src/Greeting.tsx", CLEAN_TSX)
        repo.write("src/notes.md", "# not typescript\n")
        repo.write("src/thing.py", "A = 1\n")
        result = run_gate(repo)
        assert result.returncode == 0, output(result)
        assert "examined 1 file(s)" in output(result)

    def test_discovery_is_confined_to_the_source_root(self, repo):
        # `scripts/` is build tooling, not application code, and the lefthook
        # glob never offered it. A stray match outside src/ must not be graded.
        ts_repo(repo, "src/Greeting.tsx", CLEAN_TSX)
        repo.write("scripts/build.ts", INSTANCEOF_TERNARY)
        result = run_gate(repo)
        assert result.returncode == 0, output(result)
        assert "examined 1 file(s)" in output(result)

    def test_an_explicitly_named_file_outside_the_source_root_is_still_graded(self, repo):
        # The caller scoped the run; narrowing it again would silently drop
        # files that caller meant to have graded.
        ts_repo(repo, "src/Greeting.tsx", CLEAN_TSX)
        outside = repo.write("scripts/build.ts", INSTANCEOF_TERNARY)
        result = repo.gate(
            "ts_organization_check.cjs",
            "--repo",
            str(repo.root),
            "--scope",
            "worktree",
            str(outside),
        )
        assert result.returncode == 1, output(result)

    def test_an_unresolvable_base_ref_refuses_rather_than_examining_nothing(self, repo):
        # 128-with-empty-stdout is indistinguishable from a clean diff. The
        # resolver refuses; this gate has to carry that out rather than pass.
        ts_repo(repo, "src/Greeting.tsx", CLEAN_TSX)
        result = repo.gate(
            "ts_organization_check.cjs",
            "--repo",
            str(repo.root),
            "--scope",
            "branch",
            "--base",
            "no-such-ref-anywhere",
        )
        assert result.returncode == 1, output(result)
        assert "cannot determine what to examine" in output(result)
        assert "examined 0 file(s)" not in output(result)

    def test_a_non_ascii_path_is_decoded_rather_than_dropped(self, repo):
        # `core.quotePath` is git's default: the path arrives as
        # `"src/b\303\244d.tsx"`. Consumed raw it fails the suffix filter and
        # the count silently loses a file.
        ts_repo(repo, "src/bäd.tsx", FETCH_IN_COMPONENT)
        result = run_gate(repo)
        assert result.returncode == 1, output(result)
        assert "examined 1 file(s)" in output(result)

    def test_a_symlink_out_of_the_repository_is_refused_not_read(self, repo, tmp_path):
        # It stays in scope so the read guard can refuse it. Dropping it from
        # the filter instead reports `examined 0` and a pass.
        ts_repo(repo, "src/Greeting.tsx", CLEAN_TSX)
        outside = tmp_path / "outside.ts"
        outside.write_text(INSTANCEOF_TERNARY, encoding="utf-8")
        (repo.root / "src" / "linked.ts").symlink_to(outside)
        result = run_gate(repo)
        assert result.returncode == 1, output(result)
        assert "outside the repository" in output(result)

    def test_a_suppressed_diff_is_graded_whole(self, repo):
        # `-diff` in .gitattributes: git reports the change and refuses to
        # describe it. An empty touched-line set is a pass over the violation.
        ts_repo(repo, "src/errors.ts", "export const a = 1;\n")
        repo.write(".gitattributes", "*.ts -diff\n")
        repo.commit("attributes")
        repo.write("src/errors.ts", INSTANCEOF_TERNARY)
        repo.stage("src/errors.ts")
        result = repo.gate("ts_organization_check.cjs", "--repo", str(repo.root))
        assert result.returncode == 1, output(result)

    def test_a_resolver_that_cannot_run_is_not_a_gate_that_passed(self, repo, tmp_path):
        # No `python3` on PATH — the shape of an interpreter this gate refuses,
        # and of one that is missing entirely. The resolver never answers, and a
        # gate that reads that as "nothing to examine" passes over everything.
        # `node` is kept, or the test proves only that node is required.
        only_node = tmp_path / "path"
        only_node.mkdir()
        (only_node / "node").symlink_to(shutil.which("node"))
        ts_repo(repo, "src/Widget.tsx", FETCH_IN_COMPONENT)
        result = repo.gate(
            "ts_organization_check.cjs",
            "--repo",
            str(repo.root),
            "--scope",
            "worktree",
            env_overlay={"PATH": str(only_node)},
        )
        assert result.returncode == 1, output(result)
        assert "cannot determine what to examine" in output(result)
        assert "checks passed" not in output(result)

    def test_a_checkout_behind_a_symlinked_prefix_still_maps_its_own_files(
        self, repo, tmp_path
    ):
        # macOS `/tmp` -> `/private/tmp`, and every agent worktree under a
        # linked home. The resolver answers with repo-relative keys against the
        # *resolved* root; a gate comparing against the unresolved one gets
        # `../../private/...`, which matches no key. Every lookup then misses,
        # the touched-line set comes back empty, and the gate prints a credible
        # file count and a pass. pytest's own tmp_path is already resolved,
        # which is why this needs a link of its own to reproduce.
        ts_repo(repo, "src/Widget.tsx", FETCH_IN_COMPONENT)
        link = tmp_path / "linked-checkout"
        link.symlink_to(repo.root)
        result = repo.gate(
            "ts_organization_check.cjs", "--repo", str(link), "--scope", "worktree"
        )
        assert "examined 1 file(s)" in output(result)
        assert result.returncode == 1, output(result)

    def test_a_resolver_that_answers_with_nothing_usable_is_not_a_pass(
        self, repo, tmp_path
    ):
        # The other half of "could not run": an interpreter that exists and
        # answers with something this cannot read — too old to import the
        # module, a crash, a stray print. Non-JSON is not "nothing to examine".
        fake = tmp_path / "path"
        fake.mkdir()
        (fake / "node").symlink_to(shutil.which("node"))
        (fake / "git").symlink_to(shutil.which("git"))
        stub = fake / "python3"
        stub.write_text("#!/bin/sh\necho 'these checks need Python 3.10' >&2\nexit 1\n")
        stub.chmod(0o755)
        ts_repo(repo, "src/Widget.tsx", FETCH_IN_COMPONENT)
        result = repo.gate(
            "ts_organization_check.cjs",
            "--repo",
            str(repo.root),
            "--scope",
            "worktree",
            env_overlay={"PATH": str(fake)},
        )
        assert result.returncode == 1, output(result)
        assert "cannot determine what to examine" in output(result)
        # The reason the interpreter gave, carried through rather than swallowed.
        assert "Python 3.10" in output(result)
