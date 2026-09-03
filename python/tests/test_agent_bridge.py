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

    # Observed failing once, in a full-suite run that overlapped a Rust
    # compile and took 123s against the usual 63s. Not reproduced since: 427/427
    # on the next full run, 6/6 in isolation, 3/3 under eight-way CPU
    # contention. So the cause is unproven, and "it passed on retry" is not a
    # diagnosis. What is objectively true is that the margin was thin — the
    # fake agent emitted every 0.05s against a 0.3s idle budget, six gaps — so
    # the interval is now 0.02s. If this fails again, that margin is the first
    # thing to widen further, and the emit interval is the quantity to change.
    def test_the_hard_cap_catches_a_run_that_never_goes_quiet(self):
        # Chatty forever would never trip the idle budget, which is exactly the
        # loop the second deadline exists for.
        # idle 0.3 x the default multiplier of 6 is a 1.8s ceiling, well under
        # the agent's 10s — and the agent never goes quiet, so only the hard cap
        # can stop it.
        outcome = bridge('chatty', '10', idle_timeout=0.3).run()

        assert outcome.timed_out is TimeoutKind.HARD

    def test_the_two_timeouts_are_distinguishable(self):
        # They diagnose different faults: idle is usually a wedged tool, hard is
        # usually a loop, and a caller routes them differently.
        idle = bridge('quiet', '10', idle_timeout=0.4).run()
        hard = bridge('chatty', '10', idle_timeout=0.3).run()

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
