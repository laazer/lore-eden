"""The git chokepoint, and the drift guard on its duplicated var list."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from lore_eden.git import (
    GIT_CONFIG_ENV_PREFIXES,
    GIT_LOCATION_ENV_VARS,
    run_git,
    scrubbed_env,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    run_git(["init", "-q", "-b", "main"], cwd=root, check=True)
    (root / "file.txt").write_text("hello\n")
    run_git(["add", "-A"], cwd=root, check=True)
    run_git(
        ["-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    return root


class TestScrubbedEnv:
    def test_it_removes_every_binding_variable(self, monkeypatch) -> None:
        for name in GIT_LOCATION_ENV_VARS:
            monkeypatch.setenv(name, "/elsewhere")
        env = scrubbed_env()
        assert not [name for name in GIT_LOCATION_ENV_VARS if name in env]

    def test_it_removes_injected_config_pairs(self, monkeypatch) -> None:
        # One pair setting core.attributesFile can mark sources -diff, which
        # empties a diff while --name-only still lists the file.
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.attributesFile")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/tmp/evil")
        env = scrubbed_env()
        assert not [key for key in env if key.startswith(GIT_CONFIG_ENV_PREFIXES)]

    def test_it_is_an_overlay_not_a_replacement(self, monkeypatch) -> None:
        monkeypatch.setenv("SOME_HOST_SETTING", "kept")
        env = scrubbed_env()
        assert env["SOME_HOST_SETTING"] == "kept"
        assert "PATH" in env

    def test_it_can_scrub_a_supplied_mapping(self) -> None:
        assert "GIT_DIR" not in scrubbed_env({"GIT_DIR": "/x", "KEEP": "1"})
        assert scrubbed_env({"GIT_DIR": "/x", "KEEP": "1"})["KEEP"] == "1"


class TestRunGitIgnoresAHostileEnvironment:
    """The failure the gate exists to prevent.

    `GIT_DIR` beats `cwd`, so an unscrubbed call from inside a hook operates on
    the repo the hook fired for. loregarden's pre-push suite hit exactly this:
    throwaway repos in `tmp_path` inherited a worktree's `GIT_DIR` and died on
    `git add .` with exit 128.
    """

    def test_cwd_decides_the_repository(self, repo: Path, tmp_path: Path, monkeypatch) -> None:
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        run_git(["init", "-q", "-b", "main"], cwd=decoy, check=True)

        monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(decoy))

        result = run_git(["log", "--oneline", "-1"], cwd=repo)
        assert result.returncode == 0, result.stderr
        assert "init" in result.stdout, "the call read the decoy repository"

    def test_the_control_shows_an_unscrubbed_call_would_be_redirected(
        self, repo: Path, tmp_path: Path, monkeypatch
    ) -> None:
        # Without this the test above proves only that run_git works, not that
        # the scrub is what makes it work.
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        run_git(["init", "-q", "-b", "main"], cwd=decoy, check=True)
        monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(decoy))

        bare = subprocess.run(  # noqa: S603 - the thing the gate forbids, on purpose
            ["git", "log", "--oneline", "-1"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        # The decoy has no commits, so a redirected call fails or says nothing —
        # either way it does not report this repository's commit.
        assert "init" not in bare.stdout

    def test_a_caller_cannot_pass_its_own_env_through(self) -> None:
        # A **kwargs passthrough would let `env=` reach subprocess.run and
        # defeat the whole function. The signature refuses it.
        with pytest.raises(TypeError):
            run_git(["status"], unexpected_kwarg=True)  # type: ignore[call-arg]


class TestRunGitBehaviour:
    def test_a_non_zero_exit_is_an_answer_by_default(self, tmp_path: Path) -> None:
        # "not a repository" and "no such ref" are answers, not failures, so
        # check defaults to False and call sites that want the raise ask.
        result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path)
        assert result.returncode != 0
        assert result.stderr

    def test_check_raises_when_asked(self, tmp_path: Path) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            run_git(["rev-parse", "HEAD"], cwd=tmp_path, check=True)

    def test_it_returns_text_by_default(self, repo: Path) -> None:
        assert isinstance(run_git(["status", "--short"], cwd=repo).stdout, str)  # py-org: allow-isinstance


class TestTheDuplicatedListDoesNotDrift:
    """The gate carries its own copy of these lists.

    It has to: the gates are stdlib-only and installed by filesystem path,
    this package is installed by pip, and neither can import the other. So the
    duplication is real and this is what keeps it honest — a variable added to
    one and not the other means a scrubbed child in one place and a redirected
    one in the other.
    """

    @staticmethod
    def gate_module():
        gates = Path(__file__).resolve().parents[2] / "gates" / "lore_eden_gates"
        sys.path.insert(0, str(gates))
        import precommit_git_diff

        return precommit_git_diff

    def test_the_location_lists_agree(self) -> None:
        gate = self.gate_module()
        assert tuple(gate.GIT_LOCATION_ENV_VARS) == GIT_LOCATION_ENV_VARS

    def test_the_config_prefixes_agree(self) -> None:
        gate = self.gate_module()
        assert tuple(gate.GIT_CONFIG_ENV_PREFIXES) == GIT_CONFIG_ENV_PREFIXES
