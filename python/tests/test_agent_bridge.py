"""The permission bridge, against a real agent subprocess.

Mocked, none of this would prove anything: every way a bridge breaks is at the
process boundary — whether the response reaches stdin while the child is still
reading it, whether a child that exited early takes the supervisor down, whether
a denial actually stops the agent rather than leaving it waiting forever.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from lore_eden.agents import (
    PermissionBridge,
    PermissionDecision,
    TimeoutKind,
    allow_all,
    deny_all,
)

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"


def bridge(*args: str, **kwargs) -> PermissionBridge:
    return PermissionBridge(argv=[sys.executable, str(FAKE_AGENT), *args], **kwargs)


class RecordingPolicy:
    """Answers a fixed way, and remembers what it was asked."""

    def __init__(self, approved: bool = True, updated_input=None, message: str = "") -> None:
        self.approved = approved
        self.updated_input = updated_input
        self.message = message
        self.seen: list[str] = []

    def decide(self, request, *, cancelled):
        self.seen.append(request.tool_name)
        return PermissionDecision(
            approved=self.approved,
            message=self.message,
            updated_input=self.updated_input,
        )


class TestPermissionHandshake:
    def test_an_approval_reaches_the_agent(self):
        policy = RecordingPolicy(approved=True)

        outcome = bridge('ask', 'Bash', policy=policy).run()

        assert policy.seen == ['Bash']
        assert 'permission was allow' in outcome.result.stdout
        assert outcome.ok

    def test_a_denial_reaches_the_agent(self):
        # Sent, not withheld: an agent waiting on an answer that never comes
        # hangs rather than exits.
        policy = RecordingPolicy(approved=False, message='Not on this run')

        outcome = bridge('ask', 'Bash', policy=policy).run()

        assert 'permission was deny' in outcome.result.stdout

    def test_the_request_carries_what_the_tool_asked_for(self):
        seen = []

        class Inspecting:
            def decide(self, request, *, cancelled):
                seen.append((request.tool_name, request.tool_input, request.request_id))
                return PermissionDecision(approved=True)

        bridge('ask', 'Write', policy=Inspecting()).run()

        name, tool_input, request_id = seen[0]
        assert name == 'Write'
        assert tool_input == {'command': 'echo hi'}
        assert request_id == 'req-1'

    def test_several_requests_are_each_answered(self):
        policy = RecordingPolicy(approved=True)

        outcome = bridge('ask-many', '3', policy=policy).run()

        assert policy.seen == ['Tool0', 'Tool1', 'Tool2']
        assert 'allow,allow,allow' in outcome.result.stdout

    def test_each_decision_is_reported_back_to_the_caller(self):
        outcome = bridge('ask-many', '2', policy=RecordingPolicy()).run()

        assert [request.tool_name for request, _ in outcome.decisions] == ['Tool0', 'Tool1']
        assert all(decision.approved for _, decision in outcome.decisions)

    def test_the_default_policy_refuses(self):
        # An unconfigured bridge that approved would run tools nobody decided to
        # allow, with nothing saying so.
        outcome = bridge('ask', 'Bash').run()

        assert 'permission was deny' in outcome.result.stdout

    def test_allow_all_is_available_but_must_be_asked_for(self):
        outcome = bridge('ask', 'Bash', policy=allow_all()).run()

        assert 'permission was allow' in outcome.result.stdout

    def test_deny_all_says_why(self):
        outcome = bridge('ask', 'Bash', policy=deny_all('house rules')).run()

        assert outcome.decisions[0][1].message == 'house rules'


class TestOutcome:
    def test_the_session_id_is_captured_for_a_later_resume(self):
        outcome = bridge('ok').run()

        assert outcome.session_id == 'sess-42'

    def test_a_reported_failure_is_not_a_pass(self):
        # The process exits 0; the agent says in-band that it failed. Reading
        # only the exit code would call this a success.
        outcome = bridge('fail').run()

        assert outcome.result.returncode == 0
        assert outcome.reported_failure is True
        assert outcome.ok is False

    def test_a_crash_is_not_a_pass_either(self):
        outcome = bridge('crash').run()

        assert outcome.ok is False
        assert outcome.result.returncode == 3
        assert 'something went wrong' in outcome.result.stderr

    def test_a_clean_run_is_a_pass(self):
        outcome = bridge('ok').run()

        assert outcome.ok is True
        assert outcome.timed_out is TimeoutKind.NONE

    def test_a_command_that_does_not_exist_never_ran(self):
        # Distinct from a run that failed: there is no exit code because there
        # was no process.
        outcome = PermissionBridge(argv=['/nonexistent/agent']).run()

        assert outcome.result.returncode is None
        assert outcome.ok is False


class TestTimeouts:
    def test_silence_trips_the_idle_budget(self):
        outcome = bridge('quiet', '10', idle_timeout=0.5).run()

        assert outcome.timed_out is TimeoutKind.IDLE
        assert outcome.ok is False

    def test_output_resets_the_idle_budget(self):
        # An agent talking constantly is working, not stuck — the whole reason
        # the idle budget is not a wall clock.
        outcome = bridge('chatty', '1.5', idle_timeout=0.6).run()

        assert outcome.timed_out is TimeoutKind.NONE
        assert outcome.ok is True

    # It failed again, and the note left last time named the fix: "that margin
    # is the first thing to widen further."
    #
    # Failure two was a full-suite run of 567s against the usual ~230s, with a
    # Postgres container alongside it. Same shape as failure one (123s against
    # 63s), which makes the quantity that separates pass from fail no longer
    # unproven: it is wall-clock load. 5/5 in isolation at 2.6s says nothing
    # either way, which is why isolation was never the evidence.
    #
    # The previous fix widened the *interval* — 0.05s to 0.02s, more gaps. That
    # cannot help: a single scheduler stall longer than the budget trips it
    # however often the agent emits. The budget is the quantity, and it was
    # capped by the ceiling: idle x 6 had to stay under the agent's 10s runtime,
    # so the budget could not go past ~1.5s.
    #
    # So the multiplier is now the thing that gives. `PermissionBridge` never
    # forwarded it, though the supervisor always had it — with it forwarded, a
    # 1.5s budget at a multiplier of 1.5 is a 2.25s ceiling: the same runtime as
    # the old 0.3 x 6, with five times the tolerance for a stalled subprocess.
    def test_the_hard_cap_catches_a_run_that_never_goes_quiet(self):
        # Chatty forever would never trip the idle budget, which is exactly the
        # loop the second deadline exists for.
        outcome = bridge(
            'chatty', '10', idle_timeout=1.5, hard_cap_multiplier=1.5
        ).run()

        assert outcome.timed_out is TimeoutKind.HARD

    def test_the_multiplier_actually_reaches_the_supervisor(self):
        """A parameter the bridge accepts and drops looks exactly like one that
        works — the run still ends, just at the wrong ceiling.

        Asserted on the constructor call rather than on elapsed time, because a
        timing assertion is what this whole class of failure came from. The
        patch targets the name `bridge` bound at import, which is the one `run`
        actually calls.
        """
        from unittest import mock

        from lore_eden.agents.process import ProcessResult

        with mock.patch("lore_eden.agents.bridge.ProcessSupervisor") as supervisor:
            supervisor.return_value.run.return_value = ProcessResult(returncode=0, stdout="", stderr="")
            bridge("ok", idle_timeout=2.0, hard_cap_multiplier=1.5).run()

        assert supervisor.call_args.kwargs["idle_timeout"] == 2.0
        assert supervisor.call_args.kwargs["hard_cap_multiplier"] == 1.5

    def test_the_default_multiplier_is_still_the_supervisors(self):
        """The control: a host that says nothing must get what it got before."""
        from unittest import mock

        from lore_eden.agents.process import DEFAULT_HARD_CAP_MULTIPLIER, ProcessResult

        with mock.patch("lore_eden.agents.bridge.ProcessSupervisor") as supervisor:
            supervisor.return_value.run.return_value = ProcessResult(returncode=0, stdout="", stderr="")
            bridge("ok").run()

        assert supervisor.call_args.kwargs["hard_cap_multiplier"] == DEFAULT_HARD_CAP_MULTIPLIER

    def test_the_two_timeouts_are_distinguishable(self):
        # They diagnose different faults: idle is usually a wedged tool, hard is
        # usually a loop, and a caller routes them differently.
        idle = bridge('quiet', '10', idle_timeout=0.4).run()
        hard = bridge('chatty', '10', idle_timeout=1.5, hard_cap_multiplier=1.5).run()

        assert idle.timed_out is not hard.timed_out


class TestCancellation:
    def test_a_cancelled_run_stops(self):
        calls = {'n': 0}

        def cancelled() -> bool:
            calls['n'] += 1
            return calls['n'] > 2

        outcome = bridge('chatty', '10', cancelled=cancelled).run()

        assert outcome.result.cancelled is True
        assert outcome.ok is False

    def test_a_run_cancelled_before_the_question_never_asks_it(self):
        # Stopping first is the right order: there is no point putting a
        # question to a policy for a run that is already over.
        asked = []

        class Counting:
            def decide(self, request, *, cancelled):
                asked.append(request.tool_name)
                return PermissionDecision(approved=True)

        outcome = bridge('ask', 'Bash', policy=Counting(), cancelled=lambda: True).run()

        assert asked == []
        assert outcome.result.cancelled is True

    def test_a_blocking_policy_can_see_the_cancellation(self):
        # The contract that matters for an approval inbox: a policy waiting on a
        # human must be able to find out nobody is coming.
        stopped = {'value': False}
        seen = []

        class Blocking:
            def decide(self, request, *, cancelled):
                # Stands in for a wait loop over an inbox.
                for _ in range(3):
                    if cancelled():
                        seen.append('cancelled')
                        return PermissionDecision(approved=False, message='Run cancelled')
                    stopped['value'] = True
                return PermissionDecision(approved=True)

        policy = Blocking()
        # Cancelled only once the policy has started waiting, so the run reaches
        # the question before it is stopped.
        outcome = bridge(
            'ask', 'Bash', policy=policy, cancelled=lambda: stopped['value']
        ).run()

        assert seen == ['cancelled']
        # Asserted on the decision rather than on the agent echoing it: the run
        # is cancelled the moment the policy returns, so the agent is stopped
        # before it gets to say what it was told. The decision is the part the
        # caller acts on.
        _, decision = outcome.decisions[0]
        assert decision.approved is False
        assert decision.message == 'Run cancelled'
        assert outcome.result.cancelled is True


class TestObservation:
    def test_every_message_can_be_observed(self):
        seen: list[str] = []

        bridge(
            'ask', 'Bash', policy=allow_all(), on_message=lambda m: seen.append(m.get('type', ''))
        ).run()

        assert 'system' in seen
        assert 'control_request' in seen
        assert 'result' in seen

    def test_a_non_json_line_is_ignored_rather_than_fatal(self):
        # CLIs emit progress noise and partial flushes; failing a run over one
        # would make the bridge unusable against a real agent.
        outcome = PermissionBridge(
            argv=[sys.executable, '-c', 'print("not json"); print(\'{"type":"result"}\')'],
        ).run()

        assert outcome.ok is True


class TestSilentSuccess:
    """A run that exits 0 having reported nothing did not succeed.

    This is the shape an expired `claude` OAuth session takes: it prints a login
    message and exits 0, producing no stream. Every signal the bridge had before
    this — exit code, no reported failure, no timeout — says the run was fine.
    A stage would advance on work nobody did.
    """

    def test_exit_zero_with_no_result_event_is_not_ok(self) -> None:
        outcome = bridge("expired").run()
        assert outcome.result.returncode == 0
        assert outcome.result.ok, "the process itself exited cleanly"
        assert not outcome.reported_failure, "nothing said it failed"
        assert not outcome.saw_result
        assert not outcome.ok, "but the run reported nothing, so it did not succeed"

    def test_the_silent_case_is_distinguishable_from_a_reported_failure(self) -> None:
        # The remedy differs: this is an authentication problem on the host, and
        # a caller that retries it gets the same silence.
        assert bridge("expired").run().ended_silently
        assert not bridge("fail").run().ended_silently
        assert not bridge("crash").run().ended_silently

    def test_the_login_message_survives_for_a_caller_to_show(self) -> None:
        assert "login" in bridge("expired").run().result.stderr

    def test_a_normal_run_still_passes(self) -> None:
        outcome = bridge("ok").run()
        assert outcome.saw_result
        assert outcome.ok
        assert not outcome.ended_silently
