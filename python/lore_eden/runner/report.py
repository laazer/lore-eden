"""Reading an outcome out of what an agent said.

## Exit 0 is not a pass

The single most important rule in this package. A CLI agent exiting 0 means the
process ended; it says nothing about whether the work was done. An agent that
misread the task, ran out of context, or decided the request was impossible all
exit 0 after saying so politely.

A harness that maps ``returncode == 0`` to :attr:`StageOutcome.PASS` therefore
advances work on the strength of a process having terminated, which is not
evidence of anything. It is also the most tempting shortcut available, because
it works in every happy-path test anyone writes.

So the default here **requires an explicit report**. An agent that says nothing
recognisable gets :attr:`StageOutcome.REJECT` and a note saying why — never a
pass. A host that wants a different contract supplies its own reader; that is a
decision it makes deliberately rather than inherits.

## The contract

A line, anywhere in the agent's output:

    STAGE-OUTCOME: pass
    STAGE-OUTCOME: reject   Something specific about what is wrong

Chosen to be greppable, hard to emit by accident, and trivially explainable in a
prompt. A host with structured output of its own should read that instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from lore_eden.agents import BridgeOutcome, TimeoutKind
from lore_eden.workflow.models import StageOutcome

#: The default marker. Anchored to a line start so prose *about* the contract —
#: a prompt explaining it, an agent quoting it back — does not trip it.
REPORT_PATTERN = re.compile(
    r"^\s*STAGE-OUTCOME:\s*(pass|reject)\b[ \t]*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class StageReport:
    """What the agent said about how it went."""

    outcome: StageOutcome
    summary: str = ""
    #: Why the harness reached this conclusion, when the agent did not say.
    reason: str = ""

    @property
    def reported(self) -> bool:
        """False when this was inferred rather than read from the agent."""
        return not self.reason


class ReportReader(Protocol):
    """Turns a finished run into an outcome."""

    def read(self, outcome: BridgeOutcome) -> StageReport: ...


def parse_report(text: str) -> StageReport | None:
    """The last ``STAGE-OUTCOME:`` line in ``text``, or None.

    The *last*, not the first: an agent that reconsiders mid-run leaves both, and
    its final word is the one it meant. Taking the first would let a discarded
    early draft decide the stage.
    """
    matches = REPORT_PATTERN.findall(text)
    if not matches:
        return None
    verdict, summary = matches[-1]
    # The pattern only admits the two spellings, so this cannot raise — and
    # going through the enum keeps the vocabulary in one place rather than
    # repeating its values as literals here.
    return StageReport(outcome=StageOutcome(verdict.lower()), summary=summary.strip())


@dataclass(frozen=True)
class ExplicitReportReader:
    """The default. No report means no pass.

    Every path that is not an agent explicitly saying ``pass`` returns
    ``REJECT`` with a reason naming which path it was — a run that failed, one
    that ended in silence, and one that finished chattily without a verdict are
    three different problems and a caller has to be able to tell them apart.
    """

    def read(self, outcome: BridgeOutcome) -> StageReport:
        if outcome.ended_silently:
            # Exit 0, no stream at all. Almost always an expired agent session;
            # see `claude_oauth_env`.
            return StageReport(
                outcome=StageOutcome.REJECT,
                reason="The agent exited cleanly without producing any output, "
                "which usually means its session is not authenticated.",
            )
        if outcome.timed_out is not TimeoutKind.NONE:
            return StageReport(
                outcome=StageOutcome.REJECT,
                reason=f"The agent hit its {outcome.timed_out.value} timeout.",
            )
        if outcome.result.cancelled:
            return StageReport(outcome=StageOutcome.REJECT, reason="The run was cancelled.")
        if outcome.reported_failure:
            return StageReport(
                outcome=StageOutcome.REJECT, reason="The agent reported that it failed."
            )
        if not outcome.result.ok:
            return StageReport(
                outcome=StageOutcome.REJECT,
                reason=f"The agent exited {outcome.result.returncode}.",
            )

        report = parse_report(outcome.result.stdout)
        if report is None:
            # The case this class exists for. The process succeeded and the
            # agent talked, but nothing it said claimed the work was done.
            return StageReport(
                outcome=StageOutcome.REJECT,
                reason="The agent finished without reporting an outcome. "
                "Exiting 0 is not a pass.",
            )
        return report
