"""The TypeScript gate, run against repos that have no node_modules of their own.

This is the trap that made the gate un-extractable: it resolved its parser from
a sibling `client/node_modules` by relative path. That worked only while the
gate lived inside the repo it graded — invisible until the moment the library is
installed somewhere else, which is the entire point of the library.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest
from conftest import GATES_DIR, Repo, make_repo, run_git

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

    def test_a_graded_file_is_not_reported_as_duplicating_itself(self, repo, tmp_path):
        # The DRY catalog skips files the run is grading, via
        # `changedSet.has(full)`. `changedSet` holds the resolver's paths, which
        # are under the *resolved* root; `full` was built from the string the
        # caller passed. Behind a symlinked prefix the two never compared equal,
        # so every graded file went into the catalog and matched itself:
        #
        #   ChatComposer.tsx:19: `ChatComposer` duplicates existing code
        #     (../../../../var/folders/.../ChatComposer.tsx:ChatComposer@19)
        #
        # pytest's tmp_path is already resolved, so reproducing it needs a link.
        # Long enough to reach MIN_DUPLICATE_BODY_LINES; the catalog ignores
        # bodies shorter than that, so a small fixture cannot reproduce this.
        body = "\n".join(f"  const v{i} = {i};" for i in range(12))
        ts_repo(repo, "src/Big.tsx", f"export function Big() {{\n{body}\n  return null;\n}}\n")
        link = tmp_path / "linked-checkout"
        link.symlink_to(repo.root)
        result = repo.gate(
            "ts_organization_check.cjs", "--repo", str(link), "--scope", "worktree"
        )
        assert "duplicates existing code" not in output(result), output(result)
        assert result.returncode == 0, output(result)
        assert "examined 1 file(s)" in output(result)


class TestJsxIsDecidedByExtension:
    """TypeScript's own rule, not a preference: `<T>` is a type parameter in a
    `.ts` file and a JSX element in a `.tsx` one. The two readings are mutually
    exclusive, which is why the language splits them by suffix.

    The gate passed `jsx: true` for everything. A `.ts` file with a generic —
    `useQuery<DataPage<Record>>(...)` — failed to parse, became an
    UnexaminableFileError, and took the whole run down. Nothing in this package
    has a generic in a `.ts` file, so it was invisible here and surfaced the
    first time the gate was pointed at a real consuming repo.
    """

    # A generic *arrow function*, which is the ambiguous case and the one that
    # actually broke. `function first<T>(…)` is unambiguous even under
    # `jsx: true` — a first version of this fixture used that and passed with
    # the bug still in place. This is the construct found by bisecting the file
    # that took the real run down:
    #     export const usePostStageLaunch = <C>(id: string, changeset: C) => {
    GENERIC_TS = (
        "export const firstOf = <T>(items: T[]): T | undefined => {\n"
        "  return items[0];\n"
        "};\n"
    )

    def test_a_generic_in_a_ts_file_parses(self, repo):
        ts_repo(repo, "src/generic.ts", self.GENERIC_TS)
        result = run_gate(repo)
        assert result.returncode == 0, output(result)
        assert "could not parse" not in output(result)
        assert "examined 1 file(s)" in output(result)

    def test_jsx_in_a_tsx_file_still_parses(self, repo):
        # The control for the above: narrowing jsx to `.tsx` must not stop
        # `.tsx` files being read as JSX.
        ts_repo(repo, "src/Greeting.tsx", CLEAN_TSX)
        result = run_gate(repo)
        assert result.returncode == 0, output(result)
        assert "could not parse" not in output(result)
        assert "examined 1 file(s)" in output(result)

    def test_a_genuinely_unparseable_file_is_still_refused(self, repo):
        # The other control: this must not have become "parse errors are fine".
        ts_repo(repo, "src/broken.ts", "export function ( { { {\n")
        result = run_gate(repo)
        assert result.returncode == 1, output(result)
        assert "could not parse" in output(result)


class TestAWorktreeBorrowsThePrimaryCheckoutsParser:
    """A linked worktree has no `gates/node_modules` until it is bootstrapped,
    and git hooks are shared across worktrees — so every TypeScript commit in a
    fresh one was refused with "cannot load @typescript-eslint/typescript-estree"
    while the primary checkout, one `.git` file away, had the parser installed.

    These copy the gate package into a primary repo, give only the primary a
    `gates/node_modules`, and run the *worktree's* copy of the gate, laid out
    the way agents work: under the primary's `.claude/worktrees/`.
    """

    @staticmethod
    def make_primary(tmp_path, *, with_parser: bool):
        primary = make_repo(tmp_path / "primary")
        shutil.copytree(
            GATES_DIR,
            primary.root / "gates" / "lore_eden_gates",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        shutil.copy(GATES_DIR.parent / "package.json", primary.root / "gates" / "package.json")
        primary.write(".gitignore", "node_modules/\n")
        primary.commit("gates")
        if with_parser:
            (primary.root / "gates" / "node_modules").symlink_to(GATES_DIR.parent / "node_modules")
        return primary

    @staticmethod
    def add_worktree(primary, *extra: str):
        worktree = primary.root / ".claude" / "worktrees" / "agent"
        run_git(["worktree", "add", "-q", *extra, "-b", "agent", str(worktree)], primary.root)
        assert not (worktree / "gates" / "node_modules").exists()
        tree = Repo(worktree)
        ts_repo(tree, "src/Widget.tsx", FETCH_IN_COMPONENT)
        return tree

    @staticmethod
    def run_worktree_gate(tree):
        # The worktree's own copy, as the shared hook's relative `run:` line
        # reaches it — not GATES_DIR, which has a parser beside it.
        env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
        return subprocess.run(
            [
                "node",
                str(tree.root / "gates" / "lore_eden_gates" / "ts_organization_check.cjs"),
                "--repo",
                str(tree.root),
                "--scope",
                "worktree",
            ],
            cwd=tree.root,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_the_worktree_grades_with_the_primarys_parser(self, tmp_path):
        tree = self.add_worktree(self.make_primary(tmp_path, with_parser=True))

        result = self.run_worktree_gate(tree)

        # A finding, not a pass: proof the file was parsed and read.
        assert "cannot load" not in output(result), output(result)
        assert result.returncode == 1, output(result)
        assert "Widget.tsx" in output(result)
        assert "examined 1 file(s)" in output(result)

    def test_relative_worktree_paths_resolve_too(self, tmp_path):
        # `--relative-paths` writes both `.git`'s gitdir and the admin dir's
        # back-pointer relative; `commondir` is relative either way.
        primary = self.make_primary(tmp_path, with_parser=True)
        probe = subprocess.run(
            ["git", "worktree", "add", "-h"],
            cwd=primary.root,
            capture_output=True,
            text=True,
            check=False,
        )
        # Listed as `--[no-]relative-paths`.
        if "relative-paths" not in probe.stdout + probe.stderr:
            pytest.skip("git predates `worktree add --relative-paths` (2.48)")
        tree = self.add_worktree(primary, "--relative-paths")
        assert not (tree.root / ".git").read_text().split(":", 1)[1].strip().startswith("/")

        result = self.run_worktree_gate(tree)

        assert "cannot load" not in output(result), output(result)
        assert result.returncode == 1, output(result)
        assert "Widget.tsx" in output(result)

    def test_no_parser_anywhere_is_still_a_loud_refusal(self, tmp_path):
        # The fallback must not turn "nothing resolved" into a skip. The refusal
        # names the primary's copy, so a reader sees where else it looked.
        primary = self.make_primary(tmp_path, with_parser=False)
        tree = self.add_worktree(primary)

        result = self.run_worktree_gate(tree)

        assert result.returncode != 0, output(result)
        assert "cannot load @typescript-eslint/typescript-estree" in output(result)
        assert str(primary.root / "gates") in output(result)
        assert "checks passed" not in output(result)
