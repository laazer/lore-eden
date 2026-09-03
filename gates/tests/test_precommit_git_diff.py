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

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lore_eden_gates"))

from precommit_git_diff import (  # noqa: E402
    GitScopeError,
    UnexaminableError,
    UnexaminableFileError,
    decode_git_path,
    decoded_git_paths,
    read_source_text,
    resolve_scope,
    scrubbed_git_env,
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
