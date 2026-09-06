"""The shared diff harness, tested directly.

Every gate imports this module, so a bug in it mis-scopes all of them at once —
and the symptom is `examined 0 file(s)` followed by a pass, which reads exactly
like a clean run. It was exercised transitively by every other test in this
directory and directly by none, which left its most dangerous behaviour
guarded only by CI's positive control, at the job level.

The three things tested here are the three its own docstrings name as having
produced silent passes: an inherited `GIT_DIR`, a `core.quotePath` literal, and
an unresolvable base ref.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lore_eden_gates"))

from precommit_git_diff import (  # noqa: E402
    WORKTREE,
    DiffNumstat,
    GitScopeError,
    UnexaminableError,
    UnexaminableFileError,
    decode_git_path,
    decoded_git_paths,
    git_added_paths,
    git_diff_cached,
    git_diff_numstat,
    git_gitlink_paths,
    read_source_text,
    resolve_scope,
    scrubbed_git_env,
    suppressed_diff_paths,
    unborn_worktree,
)

OFFENDER = '''"""A file with a getattr the organization gate refuses."""


def reach(obj, name):
    return getattr(obj, name)
'''


class TestScrubbedGitEnv:
    def test_removes_the_repo_bindings(self, monkeypatch) -> None:
        monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
        monkeypatch.setenv("GIT_WORK_TREE", "/elsewhere")
        env = scrubbed_git_env()
        assert "GIT_DIR" not in env
        assert "GIT_WORK_TREE" not in env

    def test_removes_injected_config(self, monkeypatch) -> None:
        # `GIT_CONFIG_COUNT`/`_KEY`/`_VALUE` inject config into the child, which
        # can turn quotePath back on — or off — underneath a decoder that was
        # written for one of those.
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.quotePath")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
        env = scrubbed_git_env()
        assert not [name for name in env if name.startswith("GIT_CONFIG")]

    def test_keeps_everything_else(self, monkeypatch) -> None:
        # It is an overlay on the real environment, not a replacement: a child
        # still needs PATH, HOME and whatever else the host set.
        monkeypatch.setenv("SOME_HOST_SETTING", "kept")
        env = scrubbed_git_env()
        assert env["SOME_HOST_SETTING"] == "kept"
        assert "PATH" in env


class TestAnInheritedGitDirDoesNotRedirectTheGate:
    """The failure the module's own docstring names.

    `GIT_DIR` beats `cwd`. A gate invoked from inside a git hook, or from a
    worktree, inherits one pointing at another checkout — reads *that*
    repository, finds none of the staged files, and reports a pass over a real
    violation. Pushing from a worktree is the case that actually happened.
    """

    def test_the_gate_examines_the_repo_it_was_pointed_at(self, nested_repo, tmp_path) -> None:
        nested_repo.write("server/myapp/reach.py", OFFENDER)
        nested_repo.stage("server/myapp/reach.py")

        decoy = tmp_path / "decoy"
        decoy.mkdir()
        os.system(f"git init -q -b main {decoy} 2>/dev/null")

        result = nested_repo.gate(
            "py_organization_check.py",
            "--repo",
            str(nested_repo.root),
            "--scope",
            "staged",
            env_overlay={"GIT_DIR": str(decoy / ".git"), "GIT_WORK_TREE": str(decoy)},
        )

        # Without the scrub the decoy has nothing staged, so this would be
        # "examined 0 file(s)" and exit 0 — a pass over the getattr below.
        assert "examined 1 file(s)" in result.stdout, result.stdout
        assert result.returncode == 1, "the staged getattr must still be caught"
        assert "getattr" in result.stdout

    def test_the_same_run_without_the_hostile_env_agrees(self, nested_repo) -> None:
        # The control: the assertion above only means something if the clean
        # run reaches the same conclusion.
        nested_repo.write("server/myapp/reach.py", OFFENDER)
        nested_repo.stage("server/myapp/reach.py")
        result = nested_repo.gate(
            "py_organization_check.py", "--repo", str(nested_repo.root), "--scope", "staged"
        )
        assert "examined 1 file(s)" in result.stdout
        assert result.returncode == 1


class TestQuotePathDecoding:
    """`core.quotePath` is on by default, so a non-ASCII path arrives C-quoted.

    Consumed raw, `"src/b\\303\\244d.py"` has the suffix `.py"` — the language
    filter drops it, and the gate reports `examined 0 file(s)`, exit 0, over a
    committed violation.
    """

    def test_decodes_octal_utf8(self) -> None:
        assert decode_git_path('"src/pkg/b\\303\\244d.py"') == "src/pkg/bäd.py"

    def test_decodes_the_c_escapes(self) -> None:
        assert decode_git_path('"a\\tb.py"') == "a\tb.py"
        assert decode_git_path('"a\\\\b.py"') == "a\\b.py"
        assert decode_git_path('"say\\"hi.py"') == 'say"hi.py'

    def test_keeps_a_newline_escaped_so_line_splitting_stays_correct(self) -> None:
        # The quoting is doing a job, not getting in the way: a literal newline
        # inside a path would otherwise split one path into two.
        assert decode_git_path('"a\\nb.py"') == "a\nb.py"

    def test_an_unquoted_token_passes_through(self) -> None:
        assert decode_git_path("src/plain.py") == "src/plain.py"
        assert decode_git_path("") == ""

    def test_a_malformed_quote_raises_rather_than_guessing(self) -> None:
        # Half a decode is a path that is neither the real one nor obviously
        # wrong, which is the shape that reports a pass.
        with pytest.raises(GitScopeError, match="malformed"):
            decode_git_path('"bad\\9.py"')
        with pytest.raises(GitScopeError, match="malformed"):
            decode_git_path('"trailing\\"')

    def test_decodes_a_whole_listing(self) -> None:
        listing = 'plain.py\n"src/b\\303\\244d.py"\n'
        assert decoded_git_paths(listing) == ["plain.py", "src/bäd.py"]

    def test_a_gate_examines_a_non_ascii_path(self, nested_repo) -> None:
        # End to end, because the decoder existing is not the same as every
        # call site using it.
        nested_repo.write("server/myapp/bäd.py", OFFENDER)
        nested_repo.stage("server/myapp/bäd.py")
        result = nested_repo.gate(
            "py_organization_check.py", "--repo", str(nested_repo.root), "--scope", "staged"
        )
        assert "examined 1 file(s)" in result.stdout, result.stdout
        assert result.returncode == 1


class TestAnUnresolvableScopeFailsLoudly:
    """`git diff` against a ref that does not exist exits 128 with empty stdout.

    Byte-identical to a clean diff. Returning it let a gate pass over a scope it
    never resolved.
    """

    def test_a_missing_base_ref_raises(self, nested_repo) -> None:
        with pytest.raises(GitScopeError):
            resolve_scope(nested_repo.root, "branch", "no-such-ref-anywhere")

    def test_the_error_is_an_unexaminable_error(self, nested_repo) -> None:
        # One type for every way of not-reading, so a third way inherits the
        # loud exit instead of becoming the next silent pass.
        assert issubclass(GitScopeError, UnexaminableError)
        assert issubclass(UnexaminableFileError, UnexaminableError)

    def test_the_gate_exits_non_zero_rather_than_reporting_zero_files(
        self, nested_repo
    ) -> None:
        result = nested_repo.gate(
            "py_organization_check.py",
            "--repo",
            str(nested_repo.root),
            "--scope",
            "branch",
            "--base",
            "no-such-ref-anywhere",
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "examined 0 file(s)" not in result.stdout

    def test_a_resolvable_scope_still_works(self, nested_repo) -> None:
        # The control for the two above.
        scope = resolve_scope(nested_repo.root, "staged", "main")
        assert scope.description


class TestAFileThatCannotBeReadIsNotClean:
    """Missing, unreadable or not UTF-8 all used to read as `None` and be
    handled exactly like a file that parsed clean."""

    def test_a_missing_file_raises(self, nested_repo) -> None:
        with pytest.raises(UnexaminableFileError):
            read_source_text(nested_repo.root / "server/myapp/gone.py", repo=nested_repo.root)

    def test_a_non_utf8_file_raises(self, nested_repo) -> None:
        path = nested_repo.root / "server/myapp/binary.py"
        path.write_bytes(b"\xff\xfe not text at all")
        with pytest.raises(UnexaminableFileError):
            read_source_text(path, repo=nested_repo.root)

    def test_a_readable_file_comes_back(self, nested_repo) -> None:
        path = nested_repo.write("server/myapp/fine.py", "x = 1\n")
        assert read_source_text(path, repo=nested_repo.root) == "x = 1\n"


class TestUnbornRepository:
    """A repository with no commits is gradable, not unexaminable.

    `git_changed_paths` has always answered without a ref; the four ref-based
    queries beside it did not. The visible failure was a gate that printed
    `examined 1 file(s)` over a brand-new workspace and then died on `git diff
    HEAD` with "cannot determine what to examine" — it announced it had read the
    file and then refused to grade it.
    """

    def test_only_the_worktree_scope_is_covered(self, unborn_repo) -> None:
        # `--cached` resolves fine against an unborn HEAD, and a named base is a
        # ref the caller said has to exist. Substituting emptiness there would
        # grade against a base nobody chose.
        assert unborn_worktree(unborn_repo.root, WORKTREE) is True
        assert unborn_worktree(unborn_repo.root, "staged") is False
        assert unborn_worktree(unborn_repo.root, "branch") is False

    def test_the_ref_based_queries_answer_without_a_ref(self, unborn_repo) -> None:
        # Each of the four died with GitScopeError before the guard.
        assert git_diff_cached(unborn_repo.root, WORKTREE) == ""
        assert git_added_paths(unborn_repo.root, WORKTREE) == []
        assert git_gitlink_paths(unborn_repo.root, WORKTREE, "main") == []
        assert git_diff_numstat(unborn_repo.root, WORKTREE) == DiffNumstat({}, frozenset())

    def test_a_committed_repository_still_reaches_git(self, nested_repo) -> None:
        # The control for the four above: the guard must not be swallowing a
        # real failure, so the same calls on a repo with a HEAD still answer
        # from git rather than from the empty branch.
        nested_repo.write("server/myapp/new.py", "VALUE = 1\n")
        nested_repo.stage("server/myapp/new.py")
        assert "server/myapp/new.py" in git_added_paths(nested_repo.root, WORKTREE)
        assert "server/myapp/new.py" in git_diff_cached(nested_repo.root, WORKTREE)
        assert git_diff_numstat(nested_repo.root, WORKTREE).counts

    def test_the_gate_grades_the_new_file_rather_than_refusing(self, unborn_repo) -> None:
        unborn_repo.write("myapp/reach.py", OFFENDER)
        result = unborn_repo.gate(
            "py_organization_check.py", "--repo", str(unborn_repo.root), "--scope", "worktree"
        )
        combined = result.stdout + result.stderr
        assert "cannot determine what to examine" not in combined, combined
        assert "examined 0 file(s)" not in combined, combined
        # Untracked, so graded whole: the getattr is found.
        assert result.returncode == 1, combined
        assert "getattr" in combined, combined


class TestSuppressedDiffPaths:
    """git counted N added lines; the parser found M. M < N is a diff that lied.

    The previous rule asked whether the diff emitted a `+++ ` header for the
    path. A `diff=<driver>` that prints the three header lines and exits walks
    straight through that — real counts in `--numstat`, a header in the diff,
    and no hunk for any gate to scope against, so the touched-line set came back
    empty and every violation in the file passed.
    """

    HEADER_ONLY = (
        "diff --git a/src/x.ts b/src/x.ts\n"
        "--- a/src/x.ts\n"
        "+++ b/src/x.ts\n"
    )
    REAL = (
        "diff --git a/src/x.ts b/src/x.ts\n"
        "--- a/src/x.ts\n"
        "+++ b/src/x.ts\n"
        "@@ -0,0 +1,2 @@\n"
        "+one\n"
        "+two\n"
    )

    def test_a_header_with_no_hunk_is_suppressed(self) -> None:
        numstat = DiffNumstat({"src/x.ts": (2, 0)}, frozenset())
        assert suppressed_diff_paths(self.HEADER_ONLY, numstat, ["src/x.ts"]) == frozenset(
            {"src/x.ts"}
        )

    def test_a_partially_described_diff_is_suppressed(self) -> None:
        # The case a "found nothing" test would pass: one hunk line where git
        # counted two. Half a description is not a description.
        partial = self.REAL.replace("+two\n", "")
        numstat = DiffNumstat({"src/x.ts": (2, 0)}, frozenset())
        assert suppressed_diff_paths(partial, numstat, ["src/x.ts"]) == frozenset({"src/x.ts"})

    def test_a_diff_that_describes_its_own_change_is_not(self) -> None:
        numstat = DiffNumstat({"src/x.ts": (2, 0)}, frozenset())
        assert suppressed_diff_paths(self.REAL, numstat, ["src/x.ts"]) == frozenset()

    def test_a_mode_only_change_is_not(self) -> None:
        # `0 < 0` is false: a chmod reports 0\t0 and legitimately has no hunk.
        numstat = DiffNumstat({"src/x.ts": (0, 0)}, frozenset())
        assert suppressed_diff_paths("", numstat, ["src/x.ts"]) == frozenset()

    def test_a_deletion_only_change_is_not(self) -> None:
        # Deletions are not added lines, so they cannot make M fall short of N.
        numstat = DiffNumstat({"src/x.ts": (0, 9)}, frozenset())
        assert suppressed_diff_paths("", numstat, ["src/x.ts"]) == frozenset()


class TestEmitScopeJson:
    """The scope, resolved once, for a gate written in another language.

    `ts_organization_check.cjs` carried a hand-port of this module's scope
    policy. This entry point is what deletes it: the `.cjs` passes in the only
    two things that are genuinely TypeScript's — which suffixes it grades and
    which source root confines discovery — and gets back everything else.
    """

    def _emit(self, repo, *args: str):
        result = repo.gate("precommit_git_diff.py", "--emit-scope-json", *args)
        return result

    def test_it_answers_with_the_files_that_survived_the_callers_filter(
        self, nested_repo
    ) -> None:
        nested_repo.write("server/myapp/kept.ts", "export const a = 1;\n")
        nested_repo.write("server/myapp/ignored.py", "A = 1\n")
        nested_repo.stage("server/myapp/kept.ts", "server/myapp/ignored.py")
        result = self._emit(
            nested_repo,
            "--repo",
            str(nested_repo.root),
            "--label",
            "ts-gate",
            "--suffix",
            ".ts",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert [Path(f).name for f in payload["files"]] == ["kept.ts"]
        # The count the caller prints is computed after that filter, by this
        # code, so it cannot disagree with the list beside it.
        assert "examined 1 file(s)" in "\n".join(payload["notices"])
        assert payload["scope"]["diff_scope"] == "staged"

    def test_the_human_lines_come_back_rather_than_going_to_the_terminal(
        self, nested_repo
    ) -> None:
        # stdout is the JSON channel. A notice printed straight through would
        # make the payload unparseable, which the caller must read as "could not
        # examine" — so a gate that only ever prints on success would break the
        # gate that never does.
        nested_repo.write("server/myapp/a.ts", "export const a = 1;\n")
        nested_repo.stage("server/myapp/a.ts")
        result = self._emit(nested_repo, "--repo", str(nested_repo.root), "--suffix", ".ts")
        json.loads(result.stdout)  # parses: nothing leaked into it

    def test_an_unresolvable_scope_exits_three_with_the_reason(self, nested_repo) -> None:
        # Not 1: the caller has to tell "could not determine what to examine"
        # apart from "graded files and found violations".
        result = self._emit(
            nested_repo,
            "--repo",
            str(nested_repo.root),
            "--scope",
            "branch",
            "--base",
            "no-such-ref-anywhere",
            "--suffix",
            ".ts",
        )
        assert result.returncode == 3, result.stdout + result.stderr
        assert json.loads(result.stdout)["error"]

    def test_discovered_candidates_are_confined_to_the_select_root(
        self, nested_repo
    ) -> None:
        nested_repo.write("server/myapp/inside.ts", "export const a = 1;\n")
        nested_repo.write("elsewhere/outside.ts", "export const b = 2;\n")
        nested_repo.stage("server/myapp/inside.ts", "elsewhere/outside.ts")
        result = self._emit(
            nested_repo,
            "--repo",
            str(nested_repo.root),
            "--suffix",
            ".ts",
            "--select-root",
            str(nested_repo.root / "server"),
        )
        payload = json.loads(result.stdout)
        assert [Path(f).name for f in payload["files"]] == ["inside.ts"]

    def test_an_explicitly_named_file_is_not_second_guessed(self, nested_repo) -> None:
        # The caller scoped the run; narrowing it again would drop files that
        # caller meant to have graded. Same rule the Python gates follow.
        outside = nested_repo.write("elsewhere/outside.ts", "export const b = 2;\n")
        nested_repo.stage("elsewhere/outside.ts")
        result = self._emit(
            nested_repo,
            "--repo",
            str(nested_repo.root),
            "--suffix",
            ".ts",
            "--select-root",
            str(nested_repo.root / "server"),
            str(outside),
        )
        payload = json.loads(result.stdout)
        assert [Path(f).name for f in payload["files"]] == ["outside.ts"]

    def test_it_reports_the_touched_lines_and_counts(self, nested_repo) -> None:
        nested_repo.write("server/myapp/a.ts", "export const a = 1;\n")
        nested_repo.commit("base")
        nested_repo.write("server/myapp/a.ts", "export const a = 1;\nexport const b = 2;\n")
        nested_repo.stage("server/myapp/a.ts")
        payload = json.loads(
            self._emit(nested_repo, "--repo", str(nested_repo.root), "--suffix", ".ts").stdout
        )
        assert payload["additions"]["server/myapp/a.ts"] == [2]
        assert payload["counts"]["server/myapp/a.ts"] == [1, 0]

    def test_an_untracked_file_is_named_as_such(self, nested_repo) -> None:
        # The caller grades these whole; an empty touched-line set is what let
        # a brand-new file pass.
        nested_repo.write("server/myapp/new.ts", "export const a = 1;\n")
        payload = json.loads(
            self._emit(
                nested_repo,
                "--repo",
                str(nested_repo.root),
                "--scope",
                "worktree",
                "--suffix",
                ".ts",
            ).stdout
        )
        assert "server/myapp/new.ts" in payload["untracked"]

    def test_the_library_refuses_any_other_cli_use(self, nested_repo) -> None:
        result = nested_repo.gate("precommit_git_diff.py", "--repo", str(nested_repo.root))
        assert result.returncode == 2
        assert "library" in result.stderr

    def test_a_symlinked_source_stays_in_scope_for_the_read_guard_to_refuse(
        self, nested_repo, tmp_path
    ) -> None:
        # `located_path`, not `resolve()`, in the select-root filter. Resolving
        # the whole path follows the link out of the tree, drops the file, and
        # reports `examined 0` over the one path the read guard exists to
        # refuse — a vacuous pass dressed as a clean run.
        outside = tmp_path / "outside.ts"
        outside.write_text("export const a = 1;\n", encoding="utf-8")
        link = nested_repo.root / "server" / "myapp" / "linked.ts"
        link.symlink_to(outside)
        nested_repo.stage("server/myapp/linked.ts")
        payload = json.loads(
            self._emit(
                nested_repo,
                "--repo",
                str(nested_repo.root),
                "--suffix",
                ".ts",
                "--select-root",
                str(nested_repo.root / "server"),
            ).stdout
        )
        assert [Path(f).name for f in payload["files"]] == ["linked.ts"]
