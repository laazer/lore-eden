"""Running the shell commands that gate a workflow transition.

Gates are configuration, not code: a list of command templates, run in a
directory, with placeholders filled from a context the host builds. Nothing here
knows what a linter is. The profile that ships with a host may well run one, but
that is data it supplied.

What this module deliberately does *not* do is decide **where** to run. The
version it came from resolved a ticket's worktree itself, and got the important
part right for a reason worth repeating: gates must run in the tree the stage
just wrote in. Run them in a shared checkout and every gate passes on work it
never saw. Since only the host knows where that tree is, ``repo_root`` is a
parameter — and getting it wrong is the failure mode to watch for.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydantic import BaseModel, Field

from lore_eden.workflow.models import GateOutcome, WorkflowStageDef

logger = logging.getLogger(__name__)

#: A gate that has not finished by now is a machine problem, not a code problem.
GATE_TIMEOUT_SECONDS = 300

#: Variables that bind git to a repository regardless of `cwd`.
_GIT_LOCATION_ENV_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """Drop ANSI colour/cursor codes so tool output stays readable when it is
    surfaced in a UI or fed back to an agent as context."""
    return _ANSI_RE.sub("", text)


def scrubbed_git_env() -> dict[str, str]:
    """The ambient environment without git's repo bindings.

    ``GIT_DIR`` overrides ``cwd``, and a gate invoked from a git hook inherits it
    pointing at another checkout. A gate aimed at the wrong repository examines
    nothing and exits 0 — a pass over unread work, which is worse than a
    failure.
    """
    return {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_ENV_VARS}


class GatesConfig(BaseModel):
    """What gates a transition, and what to try when one fails."""

    enabled: bool = False
    commands: list[str] = Field(default_factory=list)
    #: Repo-relative script run before the configured commands, if it exists.
    transition_script: str = ""
    #: Mechanical fixers run best-effort after a failure, before the gate is
    #: re-run. Their exit codes are ignored: a fixer legitimately exits non-zero
    #: when unfixable issues remain, and the re-run decides whether it helped.
    autofix_commands: list[str] = Field(default_factory=list)
    #: Whether a host should hand a still-failing gate back to the stage's agent
    #: rather than straight to a human. Recorded here; acted on by the host.
    autofix_agent_fallback: bool = True
    autofix_max_agent_attempts: int = 2


@dataclass(frozen=True)
class GateRunResult:
    ok: bool
    #: Explicit terminal outcome, so a gate that ran and passed is
    #: distinguishable from one that never ran. Callers must not infer this from
    #: `ok` alone — collapsing "passed" and "skipped" into one indistinguishable
    #: success is a bug that has shipped before.
    outcome: GateOutcome | None = None
    message: str = ""
    command: str = ""
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class GateAutofixResult:
    ran: bool
    commands: list[str] = field(default_factory=list)
    output: str = ""


@dataclass(frozen=True)
class GateCycleResult:
    """One gate evaluation, plus the repair attempt if there was one."""

    result: GateRunResult
    autofix: GateAutofixResult | None = None
    #: The evaluation before autofix ran, kept so a caller can report what was
    #: originally wrong rather than only what survived the fixer.
    first_failure: GateRunResult | None = None


def transition_name(from_stage: str, to_stage: str) -> str:
    return f"{from_stage}_to_{to_stage}"


def format_gate_command(template: str, context: Mapping[str, str]) -> str:
    """Fill a command template from the context.

    An unknown placeholder runs the command verbatim rather than failing the
    transition: the command may well be valid and the brace meant literally, and
    a warning names it either way.
    """
    try:
        return template.format(**context)
    except (KeyError, IndexError) as exc:
        logger.warning(
            "gate command template references unknown placeholder %s; running it verbatim: %r",
            exc,
            template,
        )
        return template


def run_command(command: str, cwd: Path) -> GateRunResult:
    """Run one gate command and classify how it ended.

    A malformed or unrunnable entry degrades to a result, never an exception:
    one typo'd command must not take down the evaluation around it.
    """
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            message=f"malformed gate command: {exc}",
            command=command,
        )
    if not argv:
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            message="empty gate command",
            command=command,
        )
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=scrubbed_git_env(),
            capture_output=True,
            text=True,
            timeout=GATE_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError as exc:
        # Not on PATH, not executable, and other exec failures all land here —
        # the gate did not run, which is not the same as failing.
        return GateRunResult(
            ok=False, outcome=GateOutcome.UNAVAILABLE, message=str(exc), command=command
        )
    except subprocess.TimeoutExpired:
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            message=f"Gate command timed out after {GATE_TIMEOUT_SECONDS}s",
            command=command,
        )

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.FAILED,
            message=stderr or stdout or f"exit code {completed.returncode}",
            command=command,
            stdout=stdout,
            stderr=stderr,
        )
    return GateRunResult(
        ok=True, outcome=GateOutcome.PASSED, command=command, stdout=stdout, stderr=stderr
    )


def is_undefined_transition(result: GateRunResult) -> bool:
    """True when a transition script rejected the transition *name* rather than
    running a gate and failing it.

    A host emits one transition per stage edge, but a repo's script may model
    only some of them. An unmodeled edge means "no gate here", which is a pass —
    treating it as a rejection wedges the whole workflow. argparse's ``choices=``
    rejection exits non-zero with "invalid choice"; a hand-rolled check typically
    prints "unknown transition". A genuine gate failure carries neither, so it
    still blocks.
    """
    haystack = f"{result.stderr}\n{result.stdout}".lower()
    return "invalid choice" in haystack or "unknown transition" in haystack


def resolve_transition_script(gates: GatesConfig, repo_root: Path) -> Path | None:
    """The repo's own transition-gate script, if it has one."""
    candidate = gates.transition_script.strip()
    if not candidate:
        return None
    path = repo_root / candidate
    return path if path.is_file() else None


def collect_gate_commands(
    gates: GatesConfig, *, stage_def: WorkflowStageDef | None = None
) -> list[str]:
    """Profile commands, plus any this stage adds."""
    if not gates.enabled:
        return []
    commands = list(gates.commands)
    if stage_def and stage_def.gate_commands:
        commands.extend(stage_def.gate_commands)
    return commands


def gates_can_run(gates: GatesConfig, repo_root: Path) -> bool:
    """Whether this config would actually execute something.

    ``enabled`` with nothing runnable gates nothing, and reporting that as
    "enabled" shows green for a config that lets every transition through.
    """
    if not gates.enabled:
        return False
    if any(command.strip() for command in gates.commands):
        return True
    return resolve_transition_script(gates, repo_root) is not None


def _blocking(result: GateRunResult) -> GateRunResult:
    """Stamp a failure with the outcome that decides who handles it.

    A command that could not run keeps ``UNAVAILABLE``; anything else that
    failed is a real gate failure. Collapsing the two is what sends a missing
    toolchain to an agent to "fix".
    """
    if result.outcome is GateOutcome.UNAVAILABLE:
        return result
    return replace(result, outcome=GateOutcome.FAILED)


def run_gates(
    gates: GatesConfig,
    *,
    repo_root: Path,
    context: Mapping[str, str],
    stage_def: WorkflowStageDef | None = None,
    transition_script_argv: Sequence[str] = (),
) -> GateRunResult:
    """Evaluate every configured gate for one transition.

    ``repo_root`` must be the tree the stage just wrote in — see the module
    docstring. ``context`` fills the command templates.

    ``transition_script_argv`` is appended to the resolved transition script, so
    a host passes whatever that script expects without this module inventing an
    interface for it.
    """
    if not gates.enabled:
        return GateRunResult(ok=True, outcome=GateOutcome.DISABLED, message="gates disabled")

    if not repo_root.is_dir():
        return GateRunResult(
            ok=False,
            outcome=GateOutcome.FAILED,
            message=f"Gate repo path does not exist: {repo_root}",
        )

    ran = 0

    script = resolve_transition_script(gates, repo_root)
    if script is not None:
        argv = " ".join(shlex.quote(part) for part in transition_script_argv)
        command = format_gate_command(
            f"{shlex.quote(str(script))} {argv}".strip(), dict(context)
        )
        result = run_command(command, repo_root)
        if result.ok:
            ran += 1
        elif is_undefined_transition(result):
            logger.info(
                "transition script does not model %r; treating as no gate for this edge",
                context.get("transition", ""),
            )
        else:
            return _blocking(result)

    for template in collect_gate_commands(gates, stage_def=stage_def):
        # A blank entry gates nothing. Skipping it keeps it from inflating the
        # count an operator uses to tell real gates apart.
        if not template.strip():
            continue
        result = run_command(format_gate_command(template, dict(context)), repo_root)
        if not result.ok:
            return _blocking(result)
        ran += 1

    if ran == 0:
        return GateRunResult(
            ok=True, outcome=GateOutcome.SKIPPED, message="no gate commands configured"
        )
    return GateRunResult(
        ok=True, outcome=GateOutcome.PASSED, message=f"passed {ran} gate command(s)"
    )


def run_autofix(
    gates: GatesConfig,
    *,
    repo_root: Path,
    context: Mapping[str, str],
) -> GateAutofixResult:
    """Run the mechanical fixers, best effort.

    Exit codes are ignored on purpose: a fixer exits non-zero when unfixable
    issues remain, which says nothing about whether it fixed the others. Only
    re-running the gate answers that.
    """
    if not gates.enabled or not gates.autofix_commands:
        return GateAutofixResult(ran=False)
    if not repo_root.is_dir():
        return GateAutofixResult(
            ran=False, output=f"Gate repo path does not exist: {repo_root}"
        )

    commands: list[str] = []
    chunks: list[str] = []
    for template in gates.autofix_commands:
        if not template.strip():
            continue
        command = format_gate_command(template, dict(context))
        commands.append(command)
        result = run_command(command, repo_root)
        body = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if not body and not result.ok:
            body = result.message
        if body:
            chunks.append(f"$ {command}\n{strip_ansi(body)}")

    return GateAutofixResult(ran=bool(commands), commands=commands, output="\n\n".join(chunks))


def run_gates_with_autofix(
    gates: GatesConfig,
    *,
    repo_root: Path,
    context: Mapping[str, str],
    stage_def: WorkflowStageDef | None = None,
    transition_script_argv: Sequence[str] = (),
) -> GateCycleResult:
    """Evaluate the gates; on failure, try the fixers once and evaluate again.

    One cycle, not a loop. If the fixers did not clear it, a second pass of the
    same fixers will not either — what happens next (hand it to the stage's
    agent, or to a human) is the host's escalation policy, and
    ``GatesConfig.autofix_agent_fallback`` records the intent for it.

    A gate that could not *run* is not retried: no fixer installs a missing
    toolchain, and re-running it only spends the timeout twice.
    """
    first = run_gates(
        gates,
        repo_root=repo_root,
        context=context,
        stage_def=stage_def,
        transition_script_argv=transition_script_argv,
    )
    if first.ok or first.outcome is GateOutcome.UNAVAILABLE:
        return GateCycleResult(result=first)

    autofix = run_autofix(gates, repo_root=repo_root, context=context)
    if not autofix.ran:
        return GateCycleResult(result=first, autofix=autofix)

    second = run_gates(
        gates,
        repo_root=repo_root,
        context=context,
        stage_def=stage_def,
        transition_script_argv=transition_script_argv,
    )
    return GateCycleResult(result=second, autofix=autofix, first_failure=first)
