"""Supervising an agent subprocess.

The part worth carrying over intact is the timeout model, because the obvious
one is wrong. A single wall-clock deadline kills a long run that is working
perfectly — an agent editing twenty files legitimately takes longer than one
editing two, and there is no number that is generous enough for the second
without being useless for the first.

So there are two budgets:

**Idle** — the longest the process may go saying *nothing*. Output is progress,
so any line resets it. This is what actually catches a hung agent, and it is
what a caller's configured timeout means.

**Hard** — an absolute ceiling, a multiple of the idle budget. A process that
emits a line every few seconds forever would otherwise never trip the idle
deadline. This catches the loop that is busy rather than stuck.

They are reported separately, because they mean different things to whoever
reads the failure: idle is usually a wedged tool, hard is usually a loop.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import IO, Any

#: How much longer than the idle budget a run may take in total.
DEFAULT_HARD_CAP_MULTIPLIER = 6.0

#: How often the loop wakes to check its deadlines while nothing is arriving.
#: Short enough that a timeout is reported promptly, long enough not to spin.
_POLL_INTERVAL_SECONDS = 0.05


class TimeoutKind(str, Enum):
    """Which budget ran out. Distinct because they diagnose different faults."""

    NONE = "none"
    #: Nothing was emitted for the whole idle budget — usually a wedged tool.
    IDLE = "idle"
    #: Still going past the absolute ceiling — usually a loop.
    HARD = "hard"


@dataclass
class ProcessResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: TimeoutKind = TimeoutKind.NONE
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return (
            self.returncode == 0
            and self.timed_out is TimeoutKind.NONE
            and not self.cancelled
        )


@dataclass
class ProcessSupervisor:
    """Runs one command, streaming its stdout a line at a time.

    ``on_line`` is called for every line as it arrives, which is what lets a
    caller answer a control request mid-run rather than after the process exits.
    """

    argv: list[str]
    cwd: Path | None = None
    #: Overlaid on the parent environment rather than replacing it — a run with
    #: no overlay must keep whatever the supervising process set after import.
    env_overlay: dict[str, str] = field(default_factory=dict)
    #: Seconds of silence tolerated. Output resets it.
    idle_timeout: float = 300.0
    hard_cap_multiplier: float = DEFAULT_HARD_CAP_MULTIPLIER
    #: Consulted between lines; a true answer terminates the process.
    cancelled: Callable[[], bool] = lambda: False

    def environment(self) -> dict[str, str] | None:
        """The child's environment, or None to inherit unchanged.

        None rather than a copy of ``os.environ``: inheriting is not the same as
        being handed a snapshot taken at import time, and the difference shows up
        as a credential the parent set later going missing.
        """
        if not self.env_overlay:
            return None
        return {**os.environ, **self.env_overlay}

    def run(
        self,
        *,
        stdin_text: str | None = None,
        on_line: Callable[[str], None] | None = None,
        on_started: Callable[[subprocess.Popen[str]], None] | None = None,
    ) -> ProcessResult:
        started = time.monotonic()
        idle_deadline = started + self.idle_timeout
        hard_deadline = started + self.idle_timeout * self.hard_cap_multiplier

        try:
            proc = subprocess.Popen(  # noqa: S603 - argv is operator-configured
                self.argv,
                cwd=str(self.cwd) if self.cwd else None,
                env=self.environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            # Not on PATH, not executable: the process never ran, which is not
            # the same as a run that failed.
            return ProcessResult(returncode=None, stdout="", stderr=str(exc))

        if on_started is not None:
            on_started(proc)

        if stdin_text is not None and proc.stdin is not None:
            proc.stdin.write(stdin_text)
            proc.stdin.flush()

        collected: list[str] = []
        timed_out = TimeoutKind.NONE
        cancelled = False

        # Read on a thread, decide on this one.
        #
        # Iterating the pipe directly blocks until a line arrives, so nothing
        # else runs while the process is silent — and silence is exactly what
        # the idle budget exists to catch. A deadline checked only when output
        # appears cannot fire on a process producing none, which makes it a
        # timeout that works for every case except the one it was written for.
        lines: queue.Queue[str | None] = queue.Queue()
        reader = threading.Thread(target=self._pump, args=(proc, lines), daemon=True)
        reader.start()

        while True:
            try:
                line = lines.get(timeout=_POLL_INTERVAL_SECONDS)
            except queue.Empty:
                line = ""
            if line is None:
                break  # the pipe closed: the process is done talking
            if line:
                collected.append(line)
                if on_line is not None:
                    on_line(line)
                # Output is progress. The idle budget resets; the hard cap never
                # moves, which is the whole reason there are two of them.
                idle_deadline = time.monotonic() + self.idle_timeout

            now = time.monotonic()
            if self.cancelled():
                cancelled = True
                break
            if now >= hard_deadline:
                timed_out = TimeoutKind.HARD
                break
            if now >= idle_deadline:
                timed_out = TimeoutKind.IDLE
                break

        if timed_out is not TimeoutKind.NONE or cancelled:
            self._terminate(proc)
            stderr = self._drain(proc.stderr)
            return ProcessResult(
                returncode=proc.returncode,
                stdout="".join(collected),
                stderr=stderr,
                timed_out=timed_out,
                cancelled=cancelled,
            )

        stderr = self._drain(proc.stderr)
        proc.wait()
        return ProcessResult(
            returncode=proc.returncode, stdout="".join(collected), stderr=stderr
        )

    @staticmethod
    def _pump(proc: subprocess.Popen[str], lines: queue.Queue[str | None]) -> None:
        """Move stdout onto the queue, then signal end with None."""
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    lines.put(line)
        except (OSError, ValueError):
            # The pipe was closed under us — by cancellation, or by the process
            # being killed. That is an ending, not an error to report twice.
            pass
        finally:
            lines.put(None)

    @staticmethod
    def _drain(stream: IO[str] | None) -> str:
        if stream is None:
            return ""
        try:
            return stream.read()
        except (OSError, ValueError) as exc:
            # A closed or already-consumed pipe. Say so rather than returning ""
            # — an empty stderr and an unreadable one look identical to whoever
            # is diagnosing the run.
            return f"<stderr unavailable: {exc}>"

    @staticmethod
    def _terminate(proc: subprocess.Popen[Any]) -> None:
        """Ask, then insist.

        A CLI agent has child processes of its own; terminate gives it a chance
        to take them down, and kill after a grace period stops a refusal to exit
        from hanging the supervisor too.
        """
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def close_stdin(proc: subprocess.Popen[Any]) -> None:
    """Signal end-of-input, tolerating a pipe that is already gone.

    An agent that has exited leaves a closed pipe behind, and failing the run
    because it could not be closed twice would report the wrong problem.
    """
    stdin = proc.stdin
    if stdin is None:
        return
    try:
        stdin.close()
    except OSError:
        # The child is gone; there is nothing to signal and nothing to fix.
        return
