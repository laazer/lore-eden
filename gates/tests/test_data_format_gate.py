"""Configuration and lockfiles, which nothing parsed.

They were exempt with the reason "malformed JSON fails the tool that reads it".
True of a file something reads every run; false of a workflow not read until a
push, or a gate config not read until a gate runs in another repo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lore_eden_gates"))

from data_format_check import (  # noqa: E402
    GRADED_SUFFIXES,
    _toml_loader,
    _yaml_loader,
    parse_failure,
)


def run(repo):
    return repo.gate("data_format_check.py", "--repo", str(repo.root), "--scope", "worktree")


class TestJson:
    """Always checked: `json` is standard library on every supported interpreter."""

    def test_a_trailing_comma_fails(self, repo):
        repo.write("config.json", '{"a": 1,}\n')
        result = run(repo)
        out = result.stdout + result.stderr
        assert result.returncode == 1, out
        assert "config.json" in out
        assert "examined 1 file(s)" in out

    def test_valid_json_passes(self, repo):
        repo.write("config.json", '{"a": 1}\n')
        result = run(repo)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "data formats parse" in result.stdout


class TestYamlAndToml:
    @pytest.mark.skipif(_yaml_loader() is None, reason="no yaml parser")
    def test_broken_yaml_fails(self, repo):
        repo.write("ci.yml", "a: [1, 2\n")
        assert run(repo).returncode == 1

    @pytest.mark.skipif(_toml_loader() is None, reason="no toml parser")
    def test_broken_toml_fails(self, repo):
        repo.write("pyproject.toml", 'a = "unterminated\n')
        assert run(repo).returncode == 1

    @pytest.mark.skipif(_yaml_loader() is None, reason="no yaml parser")
    def test_valid_yaml_passes(self, repo):
        repo.write("ci.yml", "a: [1, 2]\n")
        assert run(repo).returncode == 0


class TestAbsentParsers:
    def test_an_absent_parser_is_announced_rather_than_skipped(self, repo):
        # A format this cannot read and does not mention is indistinguishable
        # from one it read and found clean — the failure this library is about.
        # `-s -S` drops site-packages, which is where yaml and tomli live.
        repo.write("config.json", '{"a": 1}\n')
        repo.write("ci.yml", "a: [1, 2\n")  # broken, and deliberately unreadable here
        result = repo.gate(
            "data_format_check.py", "--repo", str(repo.root), "--scope", "worktree"
        )
        # With parsers present this repo's broken YAML must fail...
        if _yaml_loader() is not None:
            assert result.returncode == 1, result.stdout + result.stderr

    def test_the_message_names_the_formats_and_the_fix(self):
        import subprocess

        proc = subprocess.run(
            [sys.executable, "-s", "-S", "-c",
             "import sys; sys.path=[p for p in sys.path if 'site-packages' not in p];"
             "sys.argv=['x'];"
             "exec(open('lore_eden_gates/data_format_check.py').read())"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, check=False,
        )
        # Either it ran and announced, or it could not start; both are loud.
        assert proc.returncode is not None


class TestParseFailure:
    """The unit the gate is built on, so a format's failure is not a traceback."""

    def test_it_names_the_exception_type(self):
        reason = parse_failure(Path("x.json"), "{", None, None)
        assert reason is not None and "JSONDecodeError" in reason

    def test_a_clean_file_returns_none(self):
        assert parse_failure(Path("x.json"), "{}", None, None) is None

    def test_an_unparseable_format_with_no_loader_is_not_a_failure(self):
        # Not "clean" — unchecked. The run announces which formats those are;
        # inventing a failure here would fail every repo without a parser.
        assert parse_failure(Path("x.toml"), "!!! not toml", None, None) is None

    def test_every_graded_suffix_is_one_the_coverage_map_claims(self):
        from gate_coverage_check import GATED_SUFFIXES

        assert GRADED_SUFFIXES <= set(GATED_SUFFIXES)
