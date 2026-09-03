"""The two diff filters: complexity growth and statement growth.

Both were written, installed nowhere and tested by nothing — 423 lines the
README described as "supporting, not installed as hooks", which is a decision
nobody had actually made. They are in `MANAGED_GATES` now, so they need
coverage of the thing that kept them out of it: what they do when their tool is
absent from the machine.

They *reported a pass*. `python -m ruff` with ruff uninstalled exits non-zero
with nothing on stdout, and `json.loads(stdout or "[]")` turns that into an
empty finding list, which every caller reads as clean. So a machine missing the
tool printed "no complexity growth on touched lines" on every commit,
indefinitely.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lore_eden_gates"))

from install_workspace_hooks import MANAGED_COMMAND_NAMES  # noqa: E402
from precommit_git_diff import UnexaminableError, require_tool_ran  # noqa: E402


def installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


class TestRequireToolRan:
    """The guard both filters now go through."""

    def test_a_clean_exit_is_fine(self) -> None:
        require_tool_ran("ruff", completed(0, stdout="[]"))

    def test_non_zero_with_findings_is_the_normal_linter_case(self) -> None:
        # A linter exits non-zero *because* it found something. Only silence is
        # suspicious, so this must not raise.
        require_tool_ran("ruff", completed(1, stdout='[{"code": "C901"}]'))

    def test_non_zero_with_no_output_is_a_tool_that_never_ran(self) -> None:
        with pytest.raises(UnexaminableError, match="did not run"):
            require_tool_ran("ruff", completed(1, stderr="No module named ruff"))

    def test_the_message_names_the_tool_and_the_way_out(self) -> None:
        with pytest.raises(UnexaminableError) as caught:
            require_tool_ran("pylint", completed(1, stderr="No module named pylint"))
        message = str(caught.value)
        assert "pylint" in message
        assert "No module named pylint" in message
        assert "Install it" in message, "a gate that blocks must say how to unblock"

    def test_it_raises_the_shared_unexaminable_type(self) -> None:
        # So it inherits the loud exit every other not-read failure gets,
        # rather than becoming a third way of passing quietly.
        with pytest.raises(UnexaminableError):
            require_tool_ran("ruff", completed(2))


class TestTheFiltersRefuseWhenTheirToolIsMissing:
    """End to end, through a real interpreter that lacks the module."""

    @staticmethod
    def run_filter(script: str, cwd: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "lore_eden_gates" / script), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )

    @pytest.mark.skipif(installed("pylint"), reason="pylint is installed here")
    def test_the_pylint_filter_refuses(self, flat_repo) -> None:
        flat_repo.write("myapp/mod.py", "def f():\n    return 1\n")
        flat_repo.stage("myapp/mod.py")
        result = self.run_filter("pylint_diff_filter.py", flat_repo.root, "myapp/mod.py")
        assert result.returncode == 1, result.stdout
        assert "nothing was checked" in result.stdout
        assert "no growth" not in result.stdout, "it must not claim a pass it did not earn"

    @pytest.mark.skipif(installed("ruff"), reason="ruff is installed here")
    def test_the_ruff_filter_refuses(self, flat_repo) -> None:
        flat_repo.write("myapp/mod.py", "def f():\n    return 1\n")
        flat_repo.stage("myapp/mod.py")
        result = self.run_filter("ruff_complexity_diff_filter.py", flat_repo.root, "myapp/mod.py")
        assert result.returncode == 1, result.stdout
        assert "nothing was checked" in result.stdout

    @pytest.mark.skipif(not installed("ruff"), reason="needs ruff")
    def test_the_ruff_filter_passes_simple_code_when_it_can_run(self, flat_repo) -> None:
        # The control for the refusals above: with the tool present the filter
        # reaches a real verdict rather than refusing on principle.
        flat_repo.write("myapp/mod.py", "def f():\n    return 1\n")
        flat_repo.stage("myapp/mod.py")
        result = self.run_filter(
            "ruff_complexity_diff_filter.py", flat_repo.root, "myapp/mod.py"
        )
        assert result.returncode == 0, result.stdout
        assert "no complexity growth" in result.stdout


class TestTheyAreActuallyInstalled:
    def test_both_are_managed_gates_now(self) -> None:
        # The gap this closes: they existed, and nothing invoked them.
        assert "lore-eden-py-complexity" in MANAGED_COMMAND_NAMES
        assert "lore-eden-py-statements" in MANAGED_COMMAND_NAMES

    def test_the_managed_set_is_seven(self) -> None:
        # Pinned so a gate cannot be dropped from the installer without a test
        # saying so — which is how these two came to be unwired.
        assert len(MANAGED_COMMAND_NAMES) == 7

    def test_ci_runs_every_managed_gate(self) -> None:
        """CI's self-grade job must name every gate the installer installs.

        The same drift, one layer up: adding these two to MANAGED_GATES left
        CI's loop still listing five, so the repo would have installed gates it
        never ran against itself. The loop is written out by hand because the
        diff filters take positional paths rather than --repo/--scope and
        cannot join it — so a check is the only thing keeping the two lists
        honest.
        """
        from install_workspace_hooks import MANAGED_GATES

        ci = (
            Path(__file__).resolve().parent.parent.parent / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        missing = [gate.script for gate in MANAGED_GATES if gate.script not in ci]
        assert missing == [], f"managed gates CI never runs against this repo: {missing}"
