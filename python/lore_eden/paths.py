"""Turning a path a person typed into one the process can use.

A path reaching a control plane has usually been through a shell and a text
field: it may be `~`-relative, repo-relative, or carry the backslash escapes a
terminal adds when you drag a folder into it (``Mobile\\ Documents``). Passed
straight to :class:`~pathlib.Path` those produce a directory that does not
exist, named almost like the one that does.

The SQLite helpers are here rather than in :mod:`lore_eden.store` because the
question they answer — *which file on disk does this URL mean* — is a path
question, and the store should not have to know how a URL was spelled.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SQLITE_URL_PREFIX = "sqlite:///"


def unescape_shell_path(raw: str) -> str:
    r"""Undo shell-style backslash escapes, and strip surrounding whitespace.

    A terminal escapes spaces when a folder is dragged into it, and that text
    is what gets pasted into a settings field: ``Mobile\ Documents`` names a
    directory that does not exist.
    """
    return re.sub(r"\\(.)", r"\1", raw.strip())


def expand_path(raw: str | Path, *, repo_root: Path | None = None) -> Path:
    """Resolve ``raw`` to an absolute path: unescaped, `~`-expanded, resolved.

    A relative path is taken against ``repo_root`` when one is given, and
    against the process's working directory otherwise — which is rarely what a
    configured path means, so pass ``repo_root`` wherever there is one.

    Resolution is non-strict: the path need not exist yet. A database file and
    a directory about to be created are both legitimate inputs, and refusing
    them would make this unusable for the case it was written for.

    Raises :class:`ValueError` on empty input rather than returning the working
    directory, which is what ``Path("")`` does — an empty setting would
    otherwise silently name the wrong place.
    """
    text = unescape_shell_path(str(raw))
    if not text:
        raise ValueError("path is required")
    path = Path(os.path.expanduser(text))
    if not path.is_absolute() and repo_root is not None:
        path = repo_root / path
    return path.resolve(strict=False)


def resolve_sqlite_path(database_url: str, repo_root: Path) -> Path:
    """The file a ``sqlite:///…`` URL refers to.

    Raises :class:`ValueError` for any other scheme rather than guessing: a
    Postgres URL reaching this function means the caller is about to open the
    wrong database, and a plausible-looking path is the worst way to find out.
    """
    if not database_url.startswith(SQLITE_URL_PREFIX):
        raise ValueError(f"expected a {SQLITE_URL_PREFIX} URL, got: {database_url}")
    return expand_path(database_url.removeprefix(SQLITE_URL_PREFIX), repo_root=repo_root)


def sqlite_url_for_path(db_path: Path) -> str:
    """The ``sqlite:///…`` URL for a file — the inverse of :func:`resolve_sqlite_path`."""
    return f"{SQLITE_URL_PREFIX}{db_path.resolve()}"
