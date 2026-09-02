"""The vocabulary a workflow is described in.

A workflow here is a list of stages and a list of transitions between them, both
data. Nothing in this package knows what a stage *does* — an agent runs, a gate
evaluates, a human decides — only where the cursor goes next.

The stage shape carries a handful of fields this package never reads
(``agent_id``, ``model``, ``stage_brief``). They are kept because they travel in
the same template a host authors, and dropping them would mean every host
re-declaring the same envelope around them.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class StageStatus(str, Enum):
    """Where one stage stands within a run.

    ``(str, Enum)`` rather than ``StrEnum``: the latter is 3.11+, and this
    package installs on 3.10. Members compare equal to their string value
    either way, which is what callers and serialization rely on — but
    ``str(member)`` differs between the two, so format the ``.value``.
    """

    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    AWAITING = "awaiting"
    DONE = "done"
    WONT_DO = "wont_do"


class StageOutcome(str, Enum):
    """How a stage ended, which decides which transition edge is followed.

    ``DEFAULT`` is not a third result: it is a spelling a template may use for
    the unconditional edge, accepted alongside ``PASS`` because templates in the
    wild carry both.
    """

    PASS = "pass"
    REJECT = "reject"
    DEFAULT = "default"


class GateOutcome(str, Enum):
    """How a transition-gate evaluation ended.

    ``UNAVAILABLE`` is the one that earns its keep. A gate that timed out, or
    whose command is not on PATH, is not a gate that *failed* — it is one that
    never ran, and the difference decides who is asked to deal with it. Reporting
    it as ``FAILED`` sends it to the stage's own agent to fix, and no agent can
    install a toolchain it cannot see. "Could not run" is a fact about the
    machine and goes to a human.

    ``SKIPPED`` and ``DISABLED`` exist for the same reason on the passing side:
    a run that passed a real gate and a run where nothing was configured must not
    collapse into the same indistinguishable success.
    """

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class ClassifyRoute(BaseModel):
    """One branch a classify stage may take."""

    languages: list[str] = Field(default_factory=list)
    specialties: list[str] = Field(default_factory=list)
    agent_id: str
    skill_name: str = ""
    default: bool = False
    #: Stage this route branches to, so one template can carry several paths to
    #: completion.
    to_stage: str = ""


class ParallelAgentSpec(BaseModel):
    agent_id: str
    skill_name: str = ""


class WorkflowStageDef(BaseModel):
    """One stage of a workflow, as authored in a template."""

    key: str
    name: str
    agent_id: str = ""
    skill_name: str = ""
    optional: bool = False
    order: int = 0
    #: agent | classify | gate | parallel — interpreted by the host, not here.
    stage_type: str = "agent"
    classify_routes: list[ClassifyRoute] = Field(default_factory=list)
    parallel_agents: list[ParallelAgentSpec] = Field(default_factory=list)
    gate_commands: list[str] = Field(default_factory=list)
    gate_required: bool = False
    #: Evidence kinds this stage must produce before it can pass. Empty means
    #: unproven work advances.
    required_evidence: list[str] = Field(default_factory=list)
    #: Ends the workflow when reached. Falls back to ``key == "done"`` for
    #: templates authored before this flag existed.
    terminal: bool = False
    #: Condition under which this stage is passed over; interpreted by the host.
    skip_when: str = ""
    model: str = ""
    checklist: list[str] = Field(default_factory=list)
    #: Template-authored instruction for this stage specifically.
    stage_brief: str = ""
