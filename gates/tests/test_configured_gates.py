"""The two gates that are off until the repo names its helper, and the rewritten
defensive-normalization gate."""

from __future__ import annotations

import json

GIT_CALL = '''"""Shells out to git without scrubbing the environment."""

import subprocess


def status(repo):
    return subprocess.run(["git", "status"], cwd=repo, capture_output=True)
'''

GIT_CALL_WITH_ENV = '''"""Decides the child environment itself, so it is deliberate."""

import subprocess


def status(repo, env):
    return subprocess.run(["git", "status"], cwd=repo, env=env, capture_output=True)
'''

CONFIG = {
    "git_subprocess_helper": "myapp.gitwrap.run_git",
    "git_subprocess_helper_path": "myapp/gitwrap.py",
}


def configure(repo, **overrides):
    payload = {**CONFIG, **overrides}
    repo.write(".lore-eden-gates.json", json.dumps(payload))


def test_git_gate_is_silent_until_a_helper_is_named(nested_repo):
    """Flagging every git call in a repo with no designated wrapper would be an
    unactionable finding on each one."""
    nested_repo.write("server/myapp/git_user.py", GIT_CALL)

    result = nested_repo.gate(
        "py_git_subprocess_check.py", "--repo", str(nested_repo.root), "--scope", "worktree"
    )

    assert result.returncode == 0
    assert "skipped" in result.stdout


def test_the_skip_is_announced_in_the_pre_commit_form_too(nested_repo):
    """The form the installer writes, which is the one that matters.

    The managed lefthook block passes bare filenames, which the argument parser
    labels ``pre-commit`` rather than ``gate``. The skip notice was printed only
    for ``gate``, so a repo that installed five gates silently ran four — and
    the one it lost was the one it had most deliberately asked for.

    Found by installing the block into throwaway copies of the three cut-over
    targets and running the installed command lines, rather than by reading.
    """
    nested_repo.write("server/myapp/git_user.py", GIT_CALL)

    result = nested_repo.gate("py_git_subprocess_check.py", "server/myapp/git_user.py")

    assert result.returncode == 0
    assert "skipped" in result.stdout, (
        "the pre-commit form must say it did nothing, not exit 0 in silence"
    )
    assert "git_subprocess_helper" in result.stdout, "and must say how to enable it"


def test_git_gate_flags_an_unscrubbed_call_once_configured(nested_repo):
    configure(nested_repo)
    nested_repo.write("server/myapp/git_user.py", GIT_CALL)

    result = nested_repo.gate(
        "py_git_subprocess_check.py", "--repo", str(nested_repo.root), "--scope", "worktree"
    )

    assert result.returncode == 1, result.stdout
    assert "git_user.py" in result.stdout
    assert "myapp.gitwrap.run_git" in result.stdout, "remediation must name this repo's helper"


def test_git_gate_accepts_an_explicit_env(nested_repo):
    configure(nested_repo)
    nested_repo.write("server/myapp/git_user.py", GIT_CALL_WITH_ENV)

    result = nested_repo.gate(
        "py_git_subprocess_check.py", "--repo", str(nested_repo.root), "--scope", "worktree"
    )

    assert result.returncode == 0, result.stdout


def test_git_gate_exempts_the_configured_helper_itself(nested_repo):
    """The wrapper is the one file that must build a raw git argv."""
    configure(nested_repo, git_subprocess_helper_path="server/myapp/gitwrap.py")
    nested_repo.write("server/myapp/gitwrap.py", GIT_CALL)

    result = nested_repo.gate(
        "py_git_subprocess_check.py", "--repo", str(nested_repo.root), "--scope", "worktree"
    )

    assert result.returncode == 0, result.stdout


def test_malformed_config_fails_the_gate_rather_than_defaulting(nested_repo):
    nested_repo.write(".lore-eden-gates.json", "{oops")

    result = nested_repo.gate(
        "py_git_subprocess_check.py", "--repo", str(nested_repo.root), "--scope", "worktree"
    )

    assert result.returncode == 1
    assert "cannot read gate configuration" in result.stdout


DEFENSIVE = '''"""Re-normalizes on every read."""


def handle(payload):
    if str(payload).strip().lower() == "done":
        return 1
    return 0
'''

DEFENSIVE_WAIVED = '''"""Waived for a genuinely foreign value."""


def handle(payload):
    if str(payload).strip().lower() == "done":  # py-defensive: allow
        return 1
    return 0
'''

BOUNDARY_HELPER = '''"""The boundary helper is where normalizing belongs."""


def is_done(payload):
    return str(payload).strip().lower() == "done"
'''

BARE_LOWER = '''"""No str() coercion, so no claim the type was doubted."""


def handle(name):
    if name.lower() == "done":
        return 1
    return 0
'''


def test_defensive_normalization_is_flagged(nested_repo):
    nested_repo.write("server/myapp/handler.py", DEFENSIVE)

    result = nested_repo.gate(
        "py_defensive_normalization_check.py",
        "--repo",
        str(nested_repo.root),
        "--scope",
        "worktree",
    )

    assert result.returncode == 1, result.stdout
    assert "handler.py" in result.stdout


def test_one_line_boundary_helper_is_whitelisted(nested_repo):
    nested_repo.write("server/myapp/predicates.py", BOUNDARY_HELPER)

    result = nested_repo.gate(
        "py_defensive_normalization_check.py",
        "--repo",
        str(nested_repo.root),
        "--scope",
        "worktree",
    )

    assert result.returncode == 0, result.stdout


def test_waiver_comment_is_honoured(nested_repo):
    nested_repo.write("server/myapp/handler.py", DEFENSIVE_WAIVED)

    result = nested_repo.gate(
        "py_defensive_normalization_check.py",
        "--repo",
        str(nested_repo.root),
        "--scope",
        "worktree",
    )

    assert result.returncode == 0, result.stdout


def test_plain_lower_without_str_coercion_is_not_flagged(nested_repo):
    """The smell is doubting the type, not calling .lower()."""
    nested_repo.write("server/myapp/handler.py", BARE_LOWER)

    result = nested_repo.gate(
        "py_defensive_normalization_check.py",
        "--repo",
        str(nested_repo.root),
        "--scope",
        "worktree",
    )

    assert result.returncode == 0, result.stdout


def test_unreadable_file_is_not_reported_clean(nested_repo):
    """The predecessor caught every exception per file and continued, so a file
    it could not parse exited exactly like a file with nothing wrong."""
    nested_repo.write("server/myapp/broken.py", "def oops(\n")

    result = nested_repo.gate(
        "py_defensive_normalization_check.py",
        "--repo",
        str(nested_repo.root),
        "--scope",
        "worktree",
    )

    assert result.returncode == 1, result.stdout


def test_grading_nothing_says_why_when_everything_was_exempt(nested_repo):
    """"examined 0 file(s)" must not be the whole story.

    The helper module and the tests are legitimately exempt, so a change that
    touches only those grades nothing — and a bare zero there is
    indistinguishable from a gate whose scope never resolved, which is the shape
    this library exists to remove. Found by pointing lore-eden's own house rules
    at its own new helper and seeing a silent zero.
    """
    configure(nested_repo)
    nested_repo.write("server/myapp/gitwrap.py", GIT_CALL)
    nested_repo.write("server/tests/test_thing.py", GIT_CALL)

    result = nested_repo.gate(
        "py_git_subprocess_check.py", "--repo", str(nested_repo.root), "--scope", "worktree"
    )

    assert result.returncode == 0
    assert "exempt from this gate" in result.stdout, result.stdout
    assert "2 file(s) exempt" in result.stdout


class TestEveryHookCommandNamesSomethingThatExists:
    """lefthook.yml is configuration, and configuration rots quietly.

    A command whose script was renamed or deleted does not fail loudly: lefthook
    reports the non-zero exit of a missing file, which reads like a failing check
    rather than a broken one — and a `glob` that matches nothing skips silently,
    which reads like a passing one.

    This repository has already had the strongest version of that problem: the
    whole `pre-commit` block was committed and never installed, so every command
    in it ran on nothing for months.
    """

    @staticmethod
    def hook_commands() -> list[tuple[str, str, str]]:
        """Every (stage, name, run-line) in the committed config.

        Parsed by hand rather than with a YAML library: the gates package takes
        no dependencies, which is the point of it being installable into any
        workspace.
        """
        import re
        from pathlib import Path

        text = (Path(__file__).resolve().parents[2] / "lefthook.yml").read_text(encoding="utf-8")
        found: list[tuple[str, str, str]] = []
        stage = ""
        name = ""
        for line in text.splitlines():
            stripped = line.strip()
            if re.fullmatch(r"[a-z-]+:", stripped) and not line.startswith(" "):
                stage = stripped[:-1]
            elif re.fullmatch(r"[a-z0-9-]+:", stripped) and line.startswith("    ") and stage:
                name = stripped[:-1]
            elif stripped.startswith("run:") and stage and name:
                found.append((stage, name, stripped[len("run:") :].strip()))
        return found

    def test_the_config_declares_both_stages(self) -> None:
        stages = {stage for stage, _, _ in self.hook_commands()}

        assert stages == {"pre-commit", "pre-push"}

    def test_every_run_line_points_at_a_file_that_exists(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        missing = []
        for stage, name, run in self.hook_commands():
            for token in run.split():
                if "/" not in token or token.startswith("{"):
                    continue
                if not (root / token).exists():
                    missing.append(f"{stage}.{name} -> {token}")

        assert missing == []

    def test_the_pre_push_scripts_are_executable(self) -> None:
        """A script committed without its bit set fails at push time with a
        permission error, which names the file but not the cause."""
        import os
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        not_executable = [
            path.name
            for path in (root / ".lefthook" / "scripts").glob("*.sh")
            if not os.access(path, os.X_OK)
        ]

        assert not_executable == []
