"""The managed-block installer: text surgery on a config file it does not own.

Every behaviour asserted here was load-bearing in the predecessor and is easy to
regress silently, because the failure mode of each is a gate that stops running
rather than an error anybody sees.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from install_workspace_hooks import (
    BEGIN_MARKER,
    END_MARKER,
    LEGACY_MARKERS,
    MANAGED_COMMAND_NAMES,
    PY_GLOB,
)

INSTALLER = Path(__file__).resolve().parent.parent / "lore_eden_gates" / "install_workspace_hooks.py"
GATES_ROOT = Path(__file__).resolve().parent.parent / "lore_eden_gates"

MINIMAL_CONFIG = """pre-commit:
  parallel: true
  commands:
"""

CONFIG_WITH_OWN_COMMAND = """pre-commit:
  parallel: true
  commands:
    my-own-check:
      name: Something the repo already had
      run: echo hi
"""

FOUR_SPACE_CONFIG = """pre-commit:
  parallel: true
  commands:
        deeply-indented:
          name: Unusual but legal
          run: echo hi
"""


def install(config: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--config",
            str(config),
            "--gates-root",
            str(GATES_ROOT),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "lefthook.yml"
    path.write_text(MINIMAL_CONFIG, encoding="utf-8")
    return path


def test_installs_every_managed_gate(config):
    assert install(config).returncode == 0
    text = config.read_text(encoding="utf-8")

    for name in MANAGED_COMMAND_NAMES:
        assert f"{name}:" in text, f"{name} missing from the managed block"
    assert BEGIN_MARKER in text and END_MARKER in text


def test_managed_set_includes_the_two_gates_that_were_never_promoted():
    """git-subprocess routing and defensive normalization enforce universal
    rules but shipped in neither the predecessor's managed set nor its default
    orchestration profile, so they only ever ran in one repo."""
    assert "lore-eden-py-git-subprocess" in MANAGED_COMMAND_NAMES
    assert "lore-eden-py-defensive-normalization" in MANAGED_COMMAND_NAMES


def test_is_idempotent(config):
    assert install(config).returncode == 0
    first = config.read_text(encoding="utf-8")
    assert install(config).returncode == 0
    assert config.read_text(encoding="utf-8") == first


def test_check_reports_missing_without_writing(config):
    before = config.read_text(encoding="utf-8")
    result = install(config, "--check")

    assert result.returncode == 1
    assert "missing" in result.stdout
    assert config.read_text(encoding="utf-8") == before


def test_check_passes_once_current(config):
    install(config)
    result = install(config, "--check")
    assert result.returncode == 0
    assert "already current" in result.stdout


def test_refuses_a_colliding_command_rather_than_writing_a_duplicate_key(config):
    """Two entries under one key in the same map is a duplicate YAML key: the
    parser either errors or keeps one, silently disabling the other gate."""
    collide = MANAGED_COMMAND_NAMES[0]
    config.write_text(
        MINIMAL_CONFIG + f"    {collide}:\n      run: echo mine\n", encoding="utf-8"
    )

    result = install(config)

    assert result.returncode == 1
    assert collide in result.stderr
    assert BEGIN_MARKER not in config.read_text(encoding="utf-8")


def test_preserves_the_targets_own_indentation(tmp_path):
    config = tmp_path / "lefthook.yml"
    config.write_text(FOUR_SPACE_CONFIG, encoding="utf-8")

    assert install(config).returncode == 0

    for line in config.read_text(encoding="utf-8").splitlines():
        if line.strip() == BEGIN_MARKER:
            assert line.startswith(" " * 8), f"indent not matched: {line!r}"
            break
    else:
        pytest.fail("managed block not written")


def test_leaves_the_repos_own_commands_alone(tmp_path):
    config = tmp_path / "lefthook.yml"
    config.write_text(CONFIG_WITH_OWN_COMMAND, encoding="utf-8")

    assert install(config).returncode == 0

    text = config.read_text(encoding="utf-8")
    assert "my-own-check:" in text
    assert "Something the repo already had" in text


def test_replaces_a_legacy_block_instead_of_stacking_beside_it(tmp_path):
    """A repo carrying the predecessor's block must end up with one block, not
    two — two means every gate runs twice, which is the defect this library was
    built to remove."""
    legacy_begin, legacy_end = LEGACY_MARKERS[0]
    config = tmp_path / "lefthook.yml"
    config.write_text(
        MINIMAL_CONFIG
        + f"    {legacy_begin}\n"
        + "    loregarden-py-organization:\n"
        + "      run: python3 /elsewhere/py_organization_check.py {staged_files}\n"
        + f"    {legacy_end}\n",
        encoding="utf-8",
    )

    assert install(config).returncode == 0

    text = config.read_text(encoding="utf-8")
    assert legacy_begin not in text
    assert "loregarden-py-organization:" not in text
    assert text.count(BEGIN_MARKER) == 1


def test_glob_alternation_covers_root_level_files():
    """`**/*.py` alone matches nested files only; lefthook then skipped a
    root-level foo.py reporting "no files for inspection", which reads exactly
    like a pass. Verified against lefthook v2.1.10."""
    assert PY_GLOB == "{*.py,**/*.py}"


def test_missing_commands_map_is_refused_not_guessed(tmp_path):
    config = tmp_path / "lefthook.yml"
    config.write_text("pre-push:\n  commands:\n    x:\n      run: echo hi\n", encoding="utf-8")

    result = install(config)

    assert result.returncode == 1
    assert "no pre-commit commands map" in result.stderr


def test_block_points_at_the_given_gates_root(config):
    install(config)
    text = config.read_text(encoding="utf-8")
    assert str(GATES_ROOT / "py_organization_check.py") in text
