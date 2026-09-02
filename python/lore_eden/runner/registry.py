"""Which agent runs a stage, decided by one pure function.

## Why this is a module and not two lines

The source project resolved this from three places at once — a `next_agent`
column on the work item, the stage definition's `agent_id`, and a scope-reroute
pin — with no stated precedence. The result is a filed bug: a classify stage
honours a **stale** `next_agent` and dispatches to the wrong agent, which then
routes back to classify, which reads the same stale value again. A loop that
costs a full agent run per iteration.

The failure is not that three sources is too many. It is that the precedence
lived in the order of a few `if` statements spread across a service, so no one
could see it, test it, or say what it was.

Here it is :func:`resolve_agent`: arguments in, agent id out, no I/O. What it
does is inspectable by reading twelve lines, and :meth:`explain` will tell you
which source won and what the others said.

## A pin is consumed

The override tier exists for "this one run goes elsewhere" — a retry on a
different model, a reroute after a scope denial. It is returned **with** an
instruction to clear it, because that is the loop above: a pin that outlives the
run it was set for is read again by the next one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

from lore_eden.agents.policy import PermissionPolicy, deny_all
from lore_eden.agents.prompts import PromptBuilder, StaticPrompt
from lore_eden.workflow.models import WorkflowStageDef


class UnknownAgentError(LookupError):
    """A stage named an agent nothing is bound to."""


@dataclass(frozen=True)
class AgentBinding:
    """Everything needed to run one agent.

    The policy defaults to refusing every tool, matching
    :class:`~lore_eden.agents.PermissionBridge`. A binding that approved by
    default would let an unconfigured stage run tools nobody decided to allow.
    """

    agent_id: str
    prompt: PromptBuilder = field(default_factory=lambda: StaticPrompt(""))
    policy: PermissionPolicy = field(default_factory=deny_all)
    model: str = ""
    effort: str = ""
    #: Passed to the invocation builder. Empty means the host's default.
    allowed_tools: Sequence[str] = ()
    disallowed_tools: Sequence[str] = ()
    idle_timeout: float = 300.0


@dataclass(frozen=True)
class AgentResolution:
    """Which agent won, and what the losing sources said."""

    agent_id: str
    #: "override" | "stage" | "default"
    source: str
    #: True when the caller must clear the override before the next run.
    consumes_override: bool = False
    considered: Mapping[str, str] = field(default_factory=dict)

    def explain(self) -> str:
        others = ", ".join(
            f"{name}={value!r}" for name, value in sorted(self.considered.items())
        )
        return f"{self.agent_id!r} from {self.source} (considered: {others or 'nothing else'})"


def resolve_agent(
    stage: WorkflowStageDef,
    *,
    override: str = "",
    default: str = "",
) -> AgentResolution:
    """Pick the agent for ``stage``.

    Precedence, highest first:

    1. **override** — a pin set for this one run. Consumed.
    2. **stage.agent_id** — what the template says. The normal answer.
    3. **default** — a host-wide fallback.

    The override wins because the cases it exists for (a retry elsewhere, a
    reroute) are always a deliberate departure from the template. It is
    consumed because a pin that survives its run is read again by the next one,
    which is exactly the dispatch loop this ordering was written to prevent.
    """
    considered = {"override": override, "stage": stage.agent_id, "default": default}
    if override:
        return AgentResolution(
            agent_id=override,
            source="override",
            consumes_override=True,
            considered=considered,
        )
    if stage.agent_id:
        return AgentResolution(agent_id=stage.agent_id, source="stage", considered=considered)
    if default:
        return AgentResolution(agent_id=default, source="default", considered=considered)
    raise UnknownAgentError(
        f"Stage {stage.key!r} names no agent, and there is no override or default."
    )


@dataclass
class AgentRegistry:
    """Agent id to binding.

    Deliberately not stage-keyed: a template says which agent a stage wants, and
    a second mapping from stage to agent would be a place for the two to
    disagree.
    """

    bindings: dict[str, AgentBinding] = field(default_factory=dict)
    #: Used when a stage names no agent of its own.
    default_agent_id: str = ""

    def register(self, binding: AgentBinding) -> AgentBinding:
        self.bindings[binding.agent_id] = binding
        return binding

    def get(self, agent_id: str) -> AgentBinding:
        try:
            return self.bindings[agent_id]
        except KeyError:
            known = ", ".join(sorted(self.bindings)) or "nothing"
            raise UnknownAgentError(
                f"No binding for agent {agent_id!r}. Registered: {known}."
            ) from None

    def resolve(self, stage: WorkflowStageDef, *, override: str = "") -> tuple[
        AgentBinding, AgentResolution
    ]:
        """Resolve and look up in one step, applying the stage's model pin.

        A template's ``model`` overrides the binding's, because the template is
        the more specific statement — a binding says what an agent usually runs
        on, a stage says what this step needs.
        """
        resolution = resolve_agent(stage, override=override, default=self.default_agent_id)
        binding = self.get(resolution.agent_id)
        if stage.model:
            binding = replace(binding, model=stage.model)
        return binding, resolution
