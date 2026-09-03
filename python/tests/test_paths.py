"""Tests for path normalization.

Each of these failure modes produces a *plausible* path rather than an error,
which is why they are worth pinning: the process opens something, just not the
thing the operator named.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_eden.paths import (
    expand_path,
    resolve_sqlite_path,
    sqlite_url_for_path,
    unescape_shell_path,
)


class TestUnescapeShellPath:
    def test_an_escaped_space_is_restored(self) -> None:
        r"""A terminal writes `Mobile\ Documents` when you drag the folder in."""
        assert unescape_shell_path(r"~/Library/Mobile\ Documents") == "~/Library/Mobile Documents"

    def test_surrounding_whitespace_is_stripped(self) -> None:
        assert unescape_shell_path("  /tmp/x \n") == "/tmp/x"

    def test_an_escaped_backslash_becomes_one_backslash(self) -> None:
        assert unescape_shell_path(r"a\\b") == r"a\b"

    def test_an_unescaped_path_is_unchanged(self) -> None:
        assert unescape_shell_path("/tmp/plain") == "/tmp/plain"


class TestExpandPath:
    def test_a_tilde_is_expanded(self) -> None:
        assert expand_path("~/thing") == (Path.home() / "thing").resolve()

    def test_a_relative_path_is_taken_against_the_repo_root(self, tmp_path: Path) -> None:
        assert expand_path("data/db.sqlite", repo_root=tmp_path) == tmp_path / "data/db.sqlite"

    def test_an_absolute_path_ignores_the_repo_root(self, tmp_path: Path) -> None:
        assert expand_path("/tmp/elsewhere", repo_root=tmp_path) == Path("/tmp/elsewhere").resolve()

    def test_the_result_is_always_absolute(self, tmp_path: Path) -> None:
        assert expand_path("x", repo_root=tmp_path).is_absolute()

    def test_a_path_that_does_not_exist_yet_is_allowed(self, tmp_path: Path) -> None:
        """A database file about to be created is the normal input."""
        resolved = expand_path("not/created/yet.db", repo_root=tmp_path)

        assert not resolved.exists()
        assert resolved == tmp_path / "not/created/yet.db"

    def test_dot_segments_are_collapsed(self, tmp_path: Path) -> None:
        assert expand_path("data/../db.sqlite", repo_root=tmp_path) == tmp_path / "db.sqlite"

    def test_escapes_are_undone_before_resolving(self, tmp_path: Path) -> None:
        assert expand_path(r"my\ data/db", repo_root=tmp_path) == tmp_path / "my data/db"

    def test_a_path_object_is_accepted(self, tmp_path: Path) -> None:
        assert expand_path(Path("db.sqlite"), repo_root=tmp_path) == tmp_path / "db.sqlite"

    @pytest.mark.parametrize("empty", ["", "   ", "\n"])
    def test_empty_input_raises_rather_than_naming_the_working_directory(self, empty: str) -> None:
        """`Path("")` is `.`, so an unset setting would silently mean "here"."""
        with pytest.raises(ValueError, match="path is required"):
            expand_path(empty)


class TestSqliteUrls:
    def test_a_relative_url_resolves_against_the_repo_root(self, tmp_path: Path) -> None:
        resolved = resolve_sqlite_path("sqlite:///data/app.db", tmp_path)

        assert resolved == tmp_path / "data/app.db"

    def test_an_absolute_url_is_honoured(self, tmp_path: Path) -> None:
        target = tmp_path / "app.db"

        assert resolve_sqlite_path(f"sqlite:///{target}", Path("/elsewhere")) == target

    def test_a_tilde_in_a_url_is_expanded(self, tmp_path: Path) -> None:
        assert resolve_sqlite_path("sqlite:///~/app.db", tmp_path) == (Path.home() / "app.db")

    @pytest.mark.parametrize(
        "url",
        [
            "postgresql://localhost/app",
            "sqlite://",
            "mysql://localhost/app",
            "/plain/path.db",
        ],
    )
    def test_a_non_sqlite_url_raises_rather_than_guessing(self, url: str) -> None:
        """Guessing here opens the wrong database and looks like it worked."""
        with pytest.raises(ValueError, match="expected a sqlite"):
            resolve_sqlite_path(url, Path("/tmp"))

    def test_the_url_form_round_trips(self, tmp_path: Path) -> None:
        target = tmp_path / "app.db"

        assert resolve_sqlite_path(sqlite_url_for_path(target), tmp_path) == target

    def test_the_url_form_is_absolute(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)

        url = sqlite_url_for_path(Path("app.db"))

        assert url == f"sqlite:///{(tmp_path / 'app.db').resolve()}"
