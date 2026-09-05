"""Running one stage: resolve, prompt, run, judge, gate, advance.

The piece that turns two libraries into a harness. Before it,
:mod:`lore_eden.workflow` could say which stage is next and
:mod:`lore_eden.agents` could run a subprocess, and nothing joined them — so
every host wrote this loop, and this loop is where all the decisions are.

## The order, and why

1. **Resolve** the agent (:func:`~lore_eden.runner.resolve_agent`).
2. **Build** the prompt and write it where the CLI can read it.
3. **Run** the agent through the permission bridge.
4. **Judge** the run — the agent's own report, not its exit code.
5. **Gate**, but only on a pass. Running gates over work the agent just
   disowned burns a test suite to confirm something already known.
6. **Advance** the cursor on the resulting outcome.

Step 4 before step 5 is the load-bearing ordering. Step 4 defaulting to *reject*
is the load-bearing default — see :mod:`lore_eden.runner.report`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from lore_eden.agents import (
    BridgeOutcome,
    CliAdapter,
    InvocationRequest,
    PermissionBridge,
    PromptContext,
    build_invocation,
    build_user_message,
    write_prompt_file,
)
from lore_eden.runner.registry import AgentBinding, AgentRegistry, AgentResolution
from lore_eden.runner.report import ExplicitReportReader, ReportReader, StageReport
from lore_eden.workflow.dispatch import DispatchResult, advance, block, start
from lore_eden.workflow.gates import GateRunResult, GatesConfig, run_gates
from lore_eden.workflow.models import StageOutcome, WorkflowStageDef
from lore_eden.workflow.terminal import is_terminal_stage


@dataclass(frozen=True)
class StageExecution:
    """Everything one stage run produced, for a caller to record."""

    stage_key: str
    agent_id: str
    resolution: AgentResolution
    prompt: str
    bridge: BridgeOutcome | None
    report: StageReport
    gate: GateRunResult | None
    dispatch: DispatchResult

    @property
    def outcome(self) -> StageOutcome:
        return self.report.outcome

    @property
    def already_finished(self) -> bool:
        """True when there was nothing to do: the item is already at its end."""
        return self.bridge is None and self.dispatch.finished

    @property
    def gate_blocked(self) -> bool:
        """True when a gate, not the agent, decided this stage did not pass."""
        return (
            self.gate is not None
            and not self.gate.ok
            and self.report.outcome is StageOutcome.PASS
        )


@dataclass
class StageRunner:
    """Runs a work item's current stage and moves it on."""

    registry: AgentRegistry
    stages: list[WorkflowStageDef]
    transitions: list[dict[str, str]]
    workspace_root: Path
    #: Where prompt files go. One per run, named by item and stage.
    prompt_dir: Path
    adapter: CliAdapter = CliAdapter.CLAUDE
    #: Bridged by default: it is the only mode that can answer a permission
    #: request, and an agent whose requests go unanswered hangs.
    interactive: bool = True
    gates: GatesConfig = field(default_factory=GatesConfig)
    reader: ReportReader = field(default_factory=ExplicitReportReader)
    #: Environment overlay for every run — where `claude_oauth_env` belongs.
    env: dict[str, str] = field(default_factory=dict)
    #: Builds the bridge. Injected so a test can run a fake agent.
    make_bridge: Callable[..., PermissionBridge] | None = None
    permission_mode: str = ""

    def stage(self, key: str) -> WorkflowStageDef:
        for definition in self.stages:
            if definition.key == key:
                return definition
        raise LookupError(f"No stage named {key!r} in this workflow.")

    def run_stage(
        self,
        cursor,
        *,
        agent_override: str = "",
        attempt: int = 1,
        values: dict | None = None,
    ) -> StageExecution:
        """Run the stage the cursor is on, and advance it.

        ``agent_override`` is consumed: check
        ``execution.resolution.consumes_override`` and clear the stored pin. A
        pin that outlives its run is read by the next one, which is a dispatch
        loop rather than a reroute.
        """
        if not cursor.stage_key:
            started = start(cursor, self.stages)
            cursor = started.cursor
            if started.blocked:
                return self._blocked(cursor, started, "This workflow has no stages.")

        definition = self.stage(cursor.stage_key)
        if is_terminal_stage(definition):
            # Nothing to run, and asking the registry would raise: a terminal
            # stage names no agent, by definition. Reachable whenever a caller
            # reads the cursor fresh rather than remembering it finished — a
            # cron that fires once more, a duplicate queue message, an operator
            # re-running a command. Crashing there reports a broken workflow
            # for what is only a repeated call.
            return self._at_the_end(cursor, definition)

        binding, resolution = self.registry.resolve(definition, override=agent_override)

        prompt = binding.prompt.build(
            PromptContext(
                item_id=cursor.item_id,
                stage_key=definition.key,
                attempt=attempt,
                workspace_root=self.workspace_root,
                values=values or {},
            )
        )
        prompt_file = write_prompt_file(
            prompt, self.prompt_dir / f"{cursor.item_id}-{definition.key}.md"
        )

        bridge_outcome = self._run_agent(binding, prompt, prompt_file)
        report = self.reader.read(bridge_outcome)

        gate_result: GateRunResult | None = None
        if report.outcome is StageOutcome.PASS:
            # Only on a pass. Gating work the agent already disowned spends a
            # test suite to confirm something known.
            gate_result = run_gates(
                self.gates,
                repo_root=self.workspace_root,
                context={"stage": definition.key, "item": cursor.item_id},
                stage_def=definition,
            )
            if not gate_result.ok:
                report = replace(
                    report,
                    outcome=StageOutcome.REJECT,
                    reason=f"A gate rejected the work: {gate_result.message}".strip(),
                )

        dispatch = advance(
            cursor,
            self.stages,
            self.transitions,
            outcome=report.outcome.value,
            blocking_issues=report.reason,
        )
        return StageExecution(
            stage_key=definition.key,
            agent_id=binding.agent_id,
            resolution=resolution,
            prompt=prompt,
            bridge=bridge_outcome,
            report=report,
            gate=gate_result,
            dispatch=dispatch,
        )

    def _run_agent(
        self, binding: AgentBinding, prompt: str, prompt_file: Path
    ) -> BridgeOutcome:
        invocation = build_invocation(
            InvocationRequest(
                adapter=self.adapter,
                workspace_root=self.workspace_root,
                prompt=prompt,
                prompt_file=prompt_file,
                model=binding.model,
                effort=binding.effort,
                interactive=self.interactive,
                permission_mode=self.permission_mode,
                allowed_tools=binding.allowed_tools,
                disallowed_tools=binding.disallowed_tools,
                env=self.env,
            )
        )
        factory = self.make_bridge or PermissionBridge
        bridge = factory(
            argv=invocation.argv,
            cwd=Path(invocation.cwd) if invocation.cwd else None,
            env_overlay=dict(invocation.env),
            policy=binding.policy,
            idle_timeout=binding.idle_timeout,
        )
        # A bridged session receives the instruction as a stream-json user
        # message over stdin; print mode already carries it as an argument. An
        # interactive run given no stdin sits waiting for a turn that never
        # comes, and dies on the idle timeout looking like a slow agent.
        stdin_text = (
            json.dumps(build_user_message(prompt)) + "\n" if self.interactive else None
        )
        return bridge.run(stdin_text=stdin_text)

    def _at_the_end(self, cursor, definition: WorkflowStageDef) -> StageExecution:
        return StageExecution(
            stage_key=definition.key,
            agent_id="",
            resolution=AgentResolution(agent_id="", source="none"),
            prompt="",
            bridge=None,
            report=StageReport(
                outcome=StageOutcome.PASS,
                reason=f"{definition.key!r} ends this workflow; nothing left to run.",
            ),
            gate=None,
            dispatch=DispatchResult(cursor=cursor, finished=True),
        )

    def _blocked(self, cursor, dispatch: DispatchResult, reason: str) -> StageExecution:
        return StageExecution(
            stage_key=cursor.stage_key,
            agent_id="",
            resolution=AgentResolution(agent_id="", source="none"),
            prompt="",
            bridge=None,
            report=StageReport(outcome=StageOutcome.REJECT, reason=reason),
            gate=None,
            dispatch=block(cursor, reason),
        )


__all__ = ["StageExecution", "StageRunner"]
