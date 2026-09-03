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
