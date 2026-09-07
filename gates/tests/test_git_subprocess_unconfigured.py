"""An unconfigured repo is not an unexamined one.

The gate used to return before reading a single file when no
`git_subprocess_helper` was set, which collapsed two opposite facts into one
line. A repo that never shells out to git, and a repo that does it in twelve
places with no chokepoint, both reported `skipped`.

The first is the rule holding, verifiably, and a gate that can say so should.
The second is worth knowing even when the gate cannot name the fix. That
distinction is what `lor-extract-lore-11`'s third acceptance criterion asks for
— "reports examined-and-passed rather than skipped" — in a repo that turns out
to have no git calls to route at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lore_eden_gates"))

RAW_CALL = 'import subprocess\nsubprocess.run(["git", "status"])\n'
HELPER = "def run_git(argv):\n    return argv\n"
CONFIG = json.dumps(
    {
        "git_subprocess_helper": "app.git.run_git",
        "git_subprocess_helper_path": "app/git.py",
    }
) + "\n"


def run(repo):
    return repo.gate(
        "py_git_subprocess_check.py", "--repo", str(repo.root), "--scope", "worktree"
    )


def out(result) -> str:
    return result.stdout + result.stderr


class TestUnconfiguredAndClean:
    """The loremaker shape: no helper, and nothing to route."""

    def test_it_examines_and_passes_rather_than_skipping(self, repo):
        repo.write("app/service.py", "VALUE = 1\n")
        result = run(repo)
        assert result.returncode == 0, out(result)
        assert "skipped" not in out(result)
        assert "examined 1 file(s)" in out(result)
        assert "passed" in out(result)


class TestUnconfiguredWithCalls:
    """The fact the old behaviour hid: calls exist, and nowhere to put them."""

    def test_the_calls_are_reported(self, repo):
        repo.write("app/service.py", RAW_CALL)
        result = run(repo)
        assert "unscrubbed git subprocess call" in out(result)
        assert "service.py" in out(result)

    def test_it_does_not_block_the_commit(self, repo):
        # Reported, not enforced. Demanding these route through a wrapper the
        # repo has not got would fail every commit from the moment the gate is
        # installed — an outage, not a stricter gate.
        repo.write("app/service.py", RAW_CALL)
        assert run(repo).returncode == 0, out(run(repo))

    def test_it_says_how_to_make_the_rule_enforceable(self, repo):
        repo.write("app/service.py", RAW_CALL)
        assert "git_subprocess_helper" in out(run(repo))

    def test_this_is_distinguishable_from_having_no_calls(self, repo, tmp_path):
        # The whole point. Both were `skipped` before.
        repo.write("app/service.py", RAW_CALL)
        with_calls = out(run(repo))
        repo.write("app/service.py", "VALUE = 1\n")
        without = out(run(repo))
        assert with_calls != without
        assert "unscrubbed" in with_calls and "unscrubbed" not in without


class TestConfiguredStillEnforces:
    """The control for all of the above: configuration still blocks."""

    def test_a_configured_repo_fails_on_an_unrouted_call(self, repo):
        repo.write("app/git.py", HELPER)
        repo.write("app/service.py", RAW_CALL)
        repo.write(".lore-eden-gates.json", CONFIG)
        result = run(repo)
        assert result.returncode == 1, out(result)
        assert "❌" in out(result)

    def test_the_helper_itself_is_still_exempt(self, repo):
        repo.write("app/git.py", HELPER + RAW_CALL)
        repo.write(".lore-eden-gates.json", CONFIG)
        assert run(repo).returncode == 0, out(run(repo))
