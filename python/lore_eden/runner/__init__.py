"""Running a work item's stages: the loop that joins a workflow to its agents.

:mod:`lore_eden.workflow` decides where a work item goes next.
:mod:`lore_eden.agents` runs a CLI agent and answers what it asks. Nothing
joined them, so every host wrote that loop — and that loop is where the
decisions live: which agent runs this stage, what prompt it gets, and what its
exit means.

Three rules this package exists to hold:

- **Exit 0 is not a pass.** The outcome comes from what the agent reported, and
  the default reader rejects anything it cannot read a verdict from. See
  :mod:`lore_eden.runner.report`.
- **Agent resolution is one pure function** with a stated precedence, because
  the alternative — precedence spread across a service's `if` statements — is a
  dispatch loop nobody can see.
- **An override is consumed.** A pin that outlives the run it was set for is
  read again by the next one.
"""

from lore_eden.runner.registry import (
    AgentBinding,
    AgentRegistry,
    AgentResolution,
    UnknownAgentError,
    resolve_agent,
)
from lore_eden.runner.report import (
    REPORT_PATTERN,
    ExplicitReportReader,
    ReportReader,
    StageReport,
    parse_report,
)
from lore_eden.runner.stage_runner import StageExecution, StageRunner

__all__ = [
    "REPORT_PATTERN",
    "AgentBinding",
    "AgentRegistry",
    "AgentResolution",
    "ExplicitReportReader",
    "ReportReader",
    "StageExecution",
    "StageReport",
    "StageRunner",
    "UnknownAgentError",
    "parse_report",
    "resolve_agent",
]
