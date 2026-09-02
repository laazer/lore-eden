"""The loop that joins a workflow to its agents.

Against a real agent subprocess, for the reason the bridge tests give: what
breaks a harness is at the process boundary. The workflow here is deliberately
not software work — a draft/critique pair over a text file — so that anything
SDLC-shaped leaking into the harness shows up as a test that cannot be written.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from lore_eden.agents import PermissionBridge, PromptContext, StaticPrompt, allow_all
from lore_eden.runner import (
    AgentBinding,
    AgentRegistry,
    ExplicitReportReader,
    StageRunner,
    UnknownAgentError,
    parse_report,
    resolve_agent,
)
from lore_eden.workflow import WorkflowCursor
from lore_eden.workflow.gates import GatesConfig
from lore_eden.workflow.models import StageOutcome, WorkflowStageDef

REPORTER = Path(__file__).resolve().parent / "fake_reporting_agent.py"

STAGES = [
    WorkflowStageDef(key="draft", name="Draft", agent_id="writer", order=1),
    WorkflowStageDef(key="critique", name="Critique", agent_id="critic", order=2),
    WorkflowStageDef(key="done", name="Done", order=3, terminal=True),
]
TRANSITIONS = [
    {"from": "draft", "to": "critique", "when": "pass"},
    {"from": "critique", "to": "done", "when": "pass"},
    {"from": "critique", "to": "draft", "when": "reject"},
]


def runner(tmp_path: Path, *, script_args: dict[str, list[str]], **kwargs) -> StageRunner:
    """A runner whose bridge runs the fake agent with per-agent arguments."""
    registry = AgentRegistry()
    for agent_id in script_args:
        registry.register(
            AgentBinding(
                agent_id=agent_id,
                prompt=StaticPrompt(f"Instructions for {agent_id}."),
                policy=allow_all(),
                idle_timeout=10.0,
            )
        )

    def make_bridge(*, argv, **bridge_kwargs) -> PermissionBridge:
        # argv[0] is the resolved CLI binary; the prompt file names the stage,
        # which is how the fake agent knows which script to play.
        stage = next(a for a in argv if a.endswith(".md")).rsplit("-", 1)[-1][:-3]
        agent = next(a for a, _ in script_args.items() if a == _AGENT_BY_STAGE[stage])
        bridge_kwargs.pop("cwd", None)
        return PermissionBridge(
            argv=[sys.executable, str(REPORTER), *script_args[agent]], **bridge_kwargs
        )

    return StageRunner(
        registry=registry,
        stages=STAGES,
        transitions=TRANSITIONS,
        workspace_root=tmp_path,
        prompt_dir=tmp_path / "prompts",
        make_bridge=make_bridge,
        **kwargs,
    )


_AGENT_BY_STAGE = {"draft": "writer", "critique": "critic"}


class TestEndToEnd:
    def test_two_stages_reach_the_terminal(self, tmp_path: Path) -> None:
        run = runner(tmp_path, script_args={"writer": ["pass"], "critic": ["pass"]})
        cursor = WorkflowCursor(item_id="doc-1")

        first = run.run_stage(cursor)
        assert first.stage_key == "draft"
        assert first.outcome is StageOutcome.PASS
        assert first.dispatch.cursor.stage_key == "critique"

        second = run.run_stage(first.dispatch.cursor)
        assert second.stage_key == "critique"
        assert second.dispatch.finished

    def test_a_reject_sends_the_item_back(self, tmp_path: Path) -> None:
        run = runner(tmp_path, script_args={"writer": ["pass"], "critic": ["reject"]})
        cursor = run.run_stage(WorkflowCursor(item_id="doc-1")).dispatch.cursor
        second = run.run_stage(cursor)
        assert second.outcome is StageOutcome.REJECT
        assert second.dispatch.cursor.stage_key == "draft"
        assert second.report.summary

    def test_the_prompt_mentions_no_domain(self, tmp_path: Path) -> None:
        # The whole point of the prompt seam. If a ticket, an acceptance
        # criterion or a review ever leaks into the harness, this fails.
        run = runner(tmp_path, script_args={"writer": ["pass"], "critic": ["pass"]})
        execution = run.run_stage(WorkflowCursor(item_id="doc-1"))
        lowered = execution.prompt.lower()
        for word in ("ticket", "acceptance criteria", "pull request", "review"):
            assert word not in lowered

    def test_the_prompt_file_is_written_where_the_cli_can_read_it(
        self, tmp_path: Path
    ) -> None:
        run = runner(tmp_path, script_args={"writer": ["pass"], "critic": ["pass"]})
        run.run_stage(WorkflowCursor(item_id="doc-1"))
        assert (tmp_path / "prompts" / "doc-1-draft.md").is_file()


class TestExitZeroIsNotAPass:
    """The rule the whole package exists to hold."""

    def test_a_chatty_run_with_no_verdict_does_not_pass(self, tmp_path: Path) -> None:
        # Exit 0, a full stream, plenty of assistant messages — and nothing
        # claiming the work was done.
        run = runner(tmp_path, script_args={"writer": ["silent-success"], "critic": ["pass"]})
        execution = run.run_stage(WorkflowCursor(item_id="doc-1"))
        assert execution.bridge is not None
        assert execution.bridge.result.returncode == 0
        assert execution.bridge.ok, "the run itself was clean"
        assert execution.outcome is StageOutcome.REJECT
        assert "Exiting 0 is not a pass" in execution.report.reason

    def test_it_does_not_advance(self, tmp_path: Path) -> None:
        run = runner(tmp_path, script_args={"writer": ["silent-success"], "critic": ["pass"]})
        execution = run.run_stage(WorkflowCursor(item_id="doc-1"))
        # First stage, rejected, nowhere earlier to go: blocked in place rather
        # than advanced.
        assert execution.dispatch.blocked
        assert execution.dispatch.cursor.stage_key == "draft"

    def test_an_unauthenticated_agent_is_named_as_such(self, tmp_path: Path) -> None:
        run = runner(tmp_path, script_args={"writer": ["expired"], "critic": ["pass"]})
        execution = run.run_stage(WorkflowCursor(item_id="doc-1"))
        assert execution.outcome is StageOutcome.REJECT
        assert "authenticated" in execution.report.reason

    def test_a_crash_is_distinguished_from_silence(self, tmp_path: Path) -> None:
        run = runner(tmp_path, script_args={"writer": ["crash"], "critic": ["pass"]})
        execution = run.run_stage(WorkflowCursor(item_id="doc-1"))
        assert execution.outcome is StageOutcome.REJECT
        assert "exited 3" in execution.report.reason


class TestGates:
    def test_a_failing_gate_turns_a_pass_into_a_reject(self, tmp_path: Path) -> None:
        run = runner(
            tmp_path,
            script_args={"writer": ["pass"], "critic": ["pass"]},
            gates=GatesConfig(enabled=True, commands=["false"]),
        )
        cursor = run.run_stage(WorkflowCursor(item_id="doc-1")).dispatch.cursor
        second = run.run_stage(cursor)
        assert second.outcome is StageOutcome.REJECT
        assert second.dispatch.cursor.stage_key == "draft"
        assert "gate rejected" in second.report.reason

    def test_gates_do_not_run_on_a_rejected_stage(self, tmp_path: Path) -> None:
        # Spending a test suite to confirm work the agent already disowned.
        run = runner(
            tmp_path,
            script_args={"writer": ["reject"], "critic": ["pass"]},
            gates=GatesConfig(enabled=True, commands=["exit 1"]),
        )
        execution = run.run_stage(WorkflowCursor(item_id="doc-1"))
        assert execution.gate is None

    def test_a_passing_gate_leaves_the_pass_alone(self, tmp_path: Path) -> None:
        run = runner(
            tmp_path,
            script_args={"writer": ["pass"], "critic": ["pass"]},
            gates=GatesConfig(enabled=True, commands=["true"]),
        )
        execution = run.run_stage(WorkflowCursor(item_id="doc-1"))
        assert execution.outcome is StageOutcome.PASS
        assert execution.gate is not None and execution.gate.ok

    def test_a_gate_block_is_distinguishable_from_an_agent_reject(
        self, tmp_path: Path
    ) -> None:
        run = runner(
            tmp_path,
            script_args={"writer": ["reject"], "critic": ["pass"]},
            gates=GatesConfig(enabled=True, commands=["true"]),
        )
        assert not run.run_stage(WorkflowCursor(item_id="doc-1")).gate_blocked


class TestAgentResolution:
    """One pure function with a stated precedence, and an override that is consumed."""

    stage = WorkflowStageDef(key="draft", name="Draft", agent_id="writer")

    def test_the_stage_is_the_normal_answer(self) -> None:
        assert resolve_agent(self.stage).agent_id == "writer"
        assert resolve_agent(self.stage).source == "stage"

    def test_an_override_beats_the_stage(self) -> None:
        resolution = resolve_agent(self.stage, override="reviewer")
        assert resolution.agent_id == "reviewer"
        assert resolution.source == "override"

    def test_two_sources_disagreeing_is_answerable_by_reading_one_function(self) -> None:
        # The source project resolved this from three places with no stated
        # order, and the resulting stale-pin loop cost an agent run per
        # iteration. `explain` names the winner and what lost.
        resolution = resolve_agent(self.stage, override="reviewer", default="fallback")
        assert "reviewer" in resolution.explain()
        assert "stage='writer'" in resolution.explain()

    def test_the_default_is_the_last_resort(self) -> None:
        bare = WorkflowStageDef(key="x", name="X")
        assert resolve_agent(bare, default="fallback").source == "default"

    def test_no_agent_anywhere_raises_rather_than_guessing(self) -> None:
        with pytest.raises(UnknownAgentError, match="names no agent"):
            resolve_agent(WorkflowStageDef(key="x", name="X"))

    def test_an_override_says_it_must_be_consumed(self) -> None:
        # A pin that outlives its run is read by the next one, which is the
        # dispatch loop rather than a reroute.
        assert resolve_agent(self.stage, override="reviewer").consumes_override
        assert not resolve_agent(self.stage).consumes_override

    def test_the_runner_reports_the_consumption(self, tmp_path: Path) -> None:
        run = runner(tmp_path, script_args={"writer": ["pass"], "critic": ["pass"]})
        run.registry.register(
            AgentBinding(agent_id="critic", prompt=StaticPrompt("x"), policy=allow_all())
        )
        execution = run.run_stage(WorkflowCursor(item_id="doc-1"))
        assert not execution.resolution.consumes_override

    def test_an_unregistered_agent_names_what_is_registered(self) -> None:
        registry = AgentRegistry()
        registry.register(AgentBinding(agent_id="writer"))
        with pytest.raises(UnknownAgentError, match="Registered: writer"):
            registry.get("nobody")

    def test_a_stage_model_pin_beats_the_bindings(self) -> None:
        registry = AgentRegistry()
        registry.register(AgentBinding(agent_id="writer", model="haiku"))
        stage = WorkflowStageDef(key="draft", name="Draft", agent_id="writer", model="opus")
        binding, _ = registry.resolve(stage)
        assert binding.model == "opus"


class TestReportParsing:
    def test_reads_a_verdict_and_its_summary(self) -> None:
        report = parse_report("blah\nSTAGE-OUTCOME: reject  needs a stronger opening\n")
        assert report is not None
        assert report.outcome is StageOutcome.REJECT
        assert report.summary == "needs a stronger opening"

    def test_the_last_word_wins(self) -> None:
        # An agent that reconsiders leaves both; its final word is the one it
        # meant. Taking the first lets a discarded draft decide the stage.
        report = parse_report("STAGE-OUTCOME: reject\nwait\nSTAGE-OUTCOME: pass\n")
        assert report is not None and report.outcome is StageOutcome.PASS

    def test_prose_about_the_contract_does_not_trip_it(self) -> None:
        assert parse_report("Please end with a line saying STAGE-OUTCOME: pass") is None

    def test_nothing_recognizable_is_none(self) -> None:
        assert parse_report("I have finished the work successfully.") is None

    def test_a_report_read_from_the_agent_is_marked_as_such(self) -> None:
        report = parse_report("STAGE-OUTCOME: pass")
        assert report is not None and report.reported

    def test_an_inferred_rejection_is_not(self) -> None:
        reader = ExplicitReportReader()
        assert not reader.read(_silent_outcome()).reported


def _silent_outcome():
    from lore_eden.agents import BridgeOutcome, ProcessResult

    return BridgeOutcome(result=ProcessResult(returncode=0, stdout="", stderr=""))
