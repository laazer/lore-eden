"""Running the commands that gate a transition.

Every command here is supplied by the test as configuration. That is the claim:
the runner knows nothing about linters, and nothing about this repo's toolchain.
"""

from __future__ import annotations

import sys

import pytest
from lore_eden.workflow import (
    GateOutcome,
    GatesConfig,
    WorkflowStageDef,
    gates_can_run,
    run_autofix,
    run_gates,
    run_gates_with_autofix,
    transition_name,
)
from lore_eden.workflow import gates as gates_module

CONTEXT = {"transition": "draft_to_review", "from_stage": "draft", "to_stage": "review"}


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    return tmp_path


def python_command(body: str) -> str:
    """A gate command that is just Python, so the tests carry no tool dependency."""
    return f"{sys.executable} -c {body!r}"


def test_a_passing_command_passes(repo):
    config = GatesConfig(enabled=True, commands=[python_command("print('all good')")])

    result = run_gates(config, repo_root=repo, context=CONTEXT)

    assert result.ok is True
    assert result.outcome is GateOutcome.PASSED
    assert "1 gate command" in result.message


def test_a_failing_command_blocks_and_reports_why(repo):
    config = GatesConfig(
        enabled=True,
        commands=[python_command("import sys; print('three problems'); sys.exit(1)")],
    )

    result = run_gates(config, repo_root=repo, context=CONTEXT)

    assert result.ok is False
    assert result.outcome is GateOutcome.FAILED
    assert "three problems" in result.message


def test_a_command_that_cannot_run_is_unavailable_not_failed(repo):
    """A missing toolchain is a fact about the machine. Reporting it as a gate
    failure sends it to the stage's agent to fix, and no agent can install
    something it cannot see."""
    config = GatesConfig(enabled=True, commands=["/nonexistent/linter --check"])

    result = run_gates(config, repo_root=repo, context=CONTEXT)

    assert result.ok is False
    assert result.outcome is GateOutcome.UNAVAILABLE


def test_a_malformed_command_does_not_take_down_the_evaluation(repo):
    config = GatesConfig(enabled=True, commands=['echo "unterminated'])

    result = run_gates(config, repo_root=repo, context=CONTEXT)

    assert result.outcome is GateOutcome.UNAVAILABLE
    assert "malformed" in result.message


def test_a_timeout_is_unavailable(repo, monkeypatch):
    monkeypatch.setattr(gates_module, "GATE_TIMEOUT_SECONDS", 1)
    config = GatesConfig(enabled=True, commands=[python_command("import time; time.sleep(30)")])

    result = run_gates(config, repo_root=repo, context=CONTEXT)

    assert result.outcome is GateOutcome.UNAVAILABLE
    assert "timed out" in result.message


def test_gates_off_reads_as_disabled_not_passed(repo):
    """A run that passed a real gate and one where gates are off must not
    collapse into the same success."""
    result = run_gates(GatesConfig(enabled=False, commands=["true"]), repo_root=repo, context=CONTEXT)

    assert result.ok is True
    assert result.outcome is GateOutcome.DISABLED


def test_gates_on_with_nothing_configured_reads_as_skipped(repo):
    result = run_gates(GatesConfig(enabled=True), repo_root=repo, context=CONTEXT)

    assert result.ok is True
    assert result.outcome is GateOutcome.SKIPPED


def test_a_blank_command_entry_gates_nothing(repo):
    """It must not inflate the count an operator uses to tell real gates apart."""
    result = run_gates(GatesConfig(enabled=True, commands=["", "   "]), repo_root=repo, context=CONTEXT)

    assert result.outcome is GateOutcome.SKIPPED


def test_a_stage_can_add_its_own_gate_commands(repo):
    config = GatesConfig(enabled=True, commands=[python_command("print('profile')")])
    stage = WorkflowStageDef(
        key="review", name="Review", gate_commands=[python_command("print('stage')")]
    )

    result = run_gates(config, repo_root=repo, context=CONTEXT, stage_def=stage)

    assert result.outcome is GateOutcome.PASSED
    assert "2 gate command" in result.message


def test_placeholders_are_filled_from_the_context(repo):
    config = GatesConfig(
        enabled=True,
        commands=[sys.executable + " -c \"import sys; assert sys.argv[1]=='draft_to_review'\" {transition}"],
    )

    result = run_gates(config, repo_root=repo, context=CONTEXT)

    assert result.ok is True, result.message


def test_an_unknown_placeholder_runs_verbatim_rather_than_failing(repo):
    """The brace may be meant literally; a warning names it either way."""
    config = GatesConfig(enabled=True, commands=[python_command("print('{not_a_placeholder}')")])

    result = run_gates(config, repo_root=repo, context=CONTEXT)

    assert result.ok is True


def test_a_missing_repo_root_fails_rather_than_passing_over_nothing(tmp_path):
    config = GatesConfig(enabled=True, commands=[python_command("print('hi')")])

    result = run_gates(config, repo_root=tmp_path / "gone", context=CONTEXT)

    assert result.ok is False
    assert result.outcome is GateOutcome.FAILED


def test_commands_run_in_the_given_repo_root(repo):
    """Gates must run in the tree the stage wrote in. Run them elsewhere and
    every gate passes on work it never saw."""
    (repo / "marker.txt").write_text("here", encoding="utf-8")
    config = GatesConfig(
        enabled=True,
        commands=[python_command("import pathlib,sys; sys.exit(0 if pathlib.Path('marker.txt').exists() else 1)")],
    )

    assert run_gates(config, repo_root=repo, context=CONTEXT).ok is True


def test_the_git_environment_is_scrubbed(repo, monkeypatch):
    """GIT_DIR overrides cwd. A gate aimed at the wrong repository examines
    nothing and exits 0 — a pass over unread work."""
    monkeypatch.setenv("GIT_DIR", "/somewhere/else/.git")
    config = GatesConfig(
        enabled=True,
        commands=[python_command("import os,sys; sys.exit(1 if 'GIT_DIR' in os.environ else 0)")],
    )

    assert run_gates(config, repo_root=repo, context=CONTEXT).ok is True


def test_gates_can_run_is_honest_about_doing_nothing(repo):
    """`enabled` with nothing runnable gates nothing, and reporting that as
    enabled shows green for a config that lets everything through."""
    assert gates_can_run(GatesConfig(enabled=False, commands=["x"]), repo) is False
    assert gates_can_run(GatesConfig(enabled=True), repo) is False
    assert gates_can_run(GatesConfig(enabled=True, commands=["  "]), repo) is False
    assert gates_can_run(GatesConfig(enabled=True, commands=["x"]), repo) is True


def test_transition_name_is_the_edge():
    assert transition_name("draft", "review") == "draft_to_review"


# --- autofix ---------------------------------------------------------------


def marker_gate(repo) -> str:
    """Fails until `fixed.txt` exists."""
    return python_command(
        "import pathlib,sys; sys.exit(0 if pathlib.Path('fixed.txt').exists() else 1)"
    )


def test_autofix_then_retry_clears_a_failure(repo):
    """The whole point of the cycle: a mechanical fixer repairs it and the
    re-run passes, without a human."""
    config = GatesConfig(
        enabled=True,
        commands=[marker_gate(repo)],
        autofix_commands=[python_command("import pathlib; pathlib.Path('fixed.txt').write_text('x')")],
    )

    cycle = run_gates_with_autofix(config, repo_root=repo, context=CONTEXT)

    assert cycle.first_failure is not None, "the gate must have failed first"
    assert cycle.first_failure.outcome is GateOutcome.FAILED
    assert cycle.autofix.ran is True
    assert cycle.result.ok is True
    assert cycle.result.outcome is GateOutcome.PASSED


def test_autofix_that_does_not_help_leaves_the_failure(repo):
    config = GatesConfig(
        enabled=True,
        commands=[marker_gate(repo)],
        autofix_commands=[python_command("print('tried nothing')")],
    )

    cycle = run_gates_with_autofix(config, repo_root=repo, context=CONTEXT)

    assert cycle.autofix.ran is True
    assert cycle.result.ok is False
    assert cycle.result.outcome is GateOutcome.FAILED


def test_a_passing_gate_never_runs_the_fixers(repo):
    config = GatesConfig(
        enabled=True,
        commands=[python_command("print('fine')")],
        autofix_commands=[python_command("import pathlib; pathlib.Path('ran.txt').write_text('x')")],
    )

    cycle = run_gates_with_autofix(config, repo_root=repo, context=CONTEXT)

    assert cycle.autofix is None
    assert not (repo / "ran.txt").exists()


def test_an_unavailable_gate_is_not_retried(repo):
    """No fixer installs a missing toolchain; re-running only spends the
    timeout twice."""
    config = GatesConfig(
        enabled=True,
        commands=["/nonexistent/linter"],
        autofix_commands=[python_command("import pathlib; pathlib.Path('ran.txt').write_text('x')")],
    )

    cycle = run_gates_with_autofix(config, repo_root=repo, context=CONTEXT)

    assert cycle.result.outcome is GateOutcome.UNAVAILABLE
    assert cycle.autofix is None
    assert not (repo / "ran.txt").exists()


def test_a_fixers_exit_code_is_ignored(repo):
    """A fixer exits non-zero when unfixable issues remain, which says nothing
    about whether it fixed the others. Only the re-run answers that."""
    config = GatesConfig(
        enabled=True,
        commands=[marker_gate(repo)],
        autofix_commands=[
            python_command(
                "import pathlib,sys; pathlib.Path('fixed.txt').write_text('x'); sys.exit(2)"
            )
        ],
    )

    cycle = run_gates_with_autofix(config, repo_root=repo, context=CONTEXT)

    assert cycle.result.ok is True


def test_autofix_output_is_captured_and_ansi_stripped(repo):
    config = GatesConfig(
        enabled=True,
        autofix_commands=[python_command(r"print('\x1b[31mred\x1b[0m fixed 2 files')")],
    )

    result = run_autofix(config, repo_root=repo, context=CONTEXT)

    assert result.ran is True
    assert "red fixed 2 files" in result.output
    assert "\x1b[" not in result.output


def test_no_autofix_configured_reports_that_it_did_not_run(repo):
    result = run_autofix(GatesConfig(enabled=True), repo_root=repo, context=CONTEXT)

    assert result.ran is False


# --- transition script -----------------------------------------------------


def test_a_repo_transition_script_runs_and_receives_its_arguments(repo):
    script = repo / "gate.sh"
    script.write_text(
        "#!/bin/sh\n"
        "printf '%s' \"$1\" > script_saw.txt\n"
        "touch script_ran.txt\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    config = GatesConfig(enabled=True, transition_script="gate.sh")

    result = run_gates(
        config,
        repo_root=repo,
        context=CONTEXT,
        transition_script_argv=["--transition", "{transition}"],
    )

    assert result.ok is True, result.message
    assert result.outcome is GateOutcome.PASSED
    assert (repo / "script_ran.txt").exists(), "the script did not run"
    assert (repo / "script_saw.txt").read_text() == "--transition"


def test_an_unmodeled_transition_is_not_a_rejection(repo):
    """A repo may gate only some edges. An unmodeled one means "no gate here" —
    treating it as a rejection wedges the whole workflow."""
    script = repo / "gate.sh"
    script.write_text("#!/bin/sh\necho 'error: argument --transition: invalid choice' >&2\nexit 2\n", encoding="utf-8")
    script.chmod(0o755)
    config = GatesConfig(enabled=True, transition_script="gate.sh")

    result = run_gates(config, repo_root=repo, context=CONTEXT)

    assert result.ok is True
    assert result.outcome is GateOutcome.SKIPPED


def test_a_real_script_failure_still_blocks(repo):
    script = repo / "gate.sh"
    script.write_text("#!/bin/sh\necho 'the diff is not signed off' >&2\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    config = GatesConfig(enabled=True, transition_script="gate.sh")

    result = run_gates(config, repo_root=repo, context=CONTEXT)

    assert result.ok is False
    assert result.outcome is GateOutcome.FAILED
    assert "not signed off" in result.message


def test_a_missing_transition_script_is_simply_absent(repo):
    config = GatesConfig(enabled=True, transition_script="not_there.py")

    result = run_gates(config, repo_root=repo, context=CONTEXT)

    assert result.outcome is GateOutcome.SKIPPED


def test_the_package_names_no_specific_toolchain():
    """Gates are configuration. A linter named in the engine would be a default
    every host inherits whether or not it has that tool."""
    from pathlib import Path

    source = Path(gates_module.__file__).read_text(encoding="utf-8")
    for tool in ("ruff", "oxlint", "eslint", "pylint", "mypy"):
        assert tool not in source.lower(), f"{tool} is named in the gate runner"
