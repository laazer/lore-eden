#!/usr/bin/env python3
"""Per-test profiling, recorded as named metrics rather than as durations.

A suite that reports only its total wall clock can tell you it got slower and
nothing else. Both suites here ran as a bare ``pytest -q`` — no ``--durations``,
no ``addopts`` in either ``pyproject.toml`` — so a regression was something you
felt on the next push and could not attribute. That matters more than usual
because pre-push runs the two suites in turn, deliberately (see ``lefthook.yml``),
so every slow test is billed to every push.

## Why metrics are named

The obvious shape for this is ``{nodeid: seconds}``, and it is a trap. The moment
a second dimension is wanted — peak memory, import time, query counts — that
schema has to be renamed, and every reader of the old file breaks. So a test's
measurements are a *mapping of metric name to value*, and time is simply the
first metric anyone implemented::

    "metrics": {"setup_seconds": 0.01, "call_seconds": 1.2, ...}

Adding a metric is adding a key. Nothing here is spelled "duration" outside the
metric names themselves, and `MetricUnit` exists so a later reader can tell
seconds from bytes without guessing from the suffix.

## Why this reports rather than fails

Wall clock on a developer laptop is noisy in a way line coverage is not: a cold
page cache, a busy machine, or another suite on the other cores moves these
numbers enough that any fixed per-test budget would flake, and a gate that flakes
teaches everyone to re-run it. So this writes a file and prints a table; it fails
nothing. The artifact is deliberately diffable so that a *delta* gate — this run
against a stored baseline — can be added later without changing the format. That
is the decision, not an oversight.

## Why opt-in, and why an environment variable

The default output of both suites is ``-q``, chosen so a pre-push stays readable.
Profiling every run would undo that and tax the thing it measures. So the plugin
is inert unless ``LORE_EDEN_PROFILE`` names an output path, which also means the
existing invocations did not have to change to gain it.

An environment variable rather than a command-line flag because ``pytest_addoption``
is only honoured from the rootdir conftest or an installed plugin, and both of this
repository's conftests sit a directory below their rootdir. An env var works
identically from either, and from CI, without an entry point.

Usage:
    LORE_EDEN_PROFILE=.profile/python.json python -m pytest -q
    LORE_EDEN_PROFILE_TOP=25 LORE_EDEN_PROFILE=... python -m pytest -q

Standard library only, on purpose: this module is imported by the ``gates`` suite,
whose whole point is to run under whatever ``python3`` is on PATH with nothing
installed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_TOP_N = 15

#: Output path. Unset means the plugin does not register at all.
ENV_OUTPUT = "LORE_EDEN_PROFILE"
#: How many rows the terminal summary prints. Unset means `DEFAULT_TOP_N`.
ENV_TOP_N = "LORE_EDEN_PROFILE_TOP"


class Phase(str, Enum):
    """The three phases pytest times separately for every test.

    Kept distinct in the artifact rather than summed, because a slow fixture and
    a slow assertion are different defects with different fixes, and a single
    total cannot tell them apart.
    """

    SETUP = "setup"
    CALL = "call"
    TEARDOWN = "teardown"


class ScopeKind(str, Enum):
    """Whether the run measured the whole suite or a selection of it.

    Recorded because `python-tests.sh` narrows to the tests the pushed commits can
    reach, via `select_pytest_targets.py`. A profile from a narrowed run covers a
    different set of tests each time, so comparing one to a full-suite baseline is
    meaningless — and silently meaningless, which is worse. The artifact says which
    it was so a later comparison can refuse.
    """

    FULL = "full"
    SELECTED = "selected"


class MetricUnit(str, Enum):
    """The unit a metric is measured in.

    Declared alongside the metric names so that a reader — or a future delta gate —
    does not have to infer "seconds" from a key suffix. A memory metric added later
    declares `BYTES` here and needs no other change.
    """

    SECONDS = "seconds"
    BYTES = "bytes"
    COUNT = "count"


def metric_name(phase: Phase) -> str:
    """The metric key for a phase, e.g. `Phase.CALL` -> ``call_seconds``."""
    return f"{phase.value}_{MetricUnit.SECONDS.value}"


TOTAL_METRIC = f"total_{MetricUnit.SECONDS.value}"


def resolve_top_n(raw: str | None) -> int:
    """How many rows to print, falling back on anything that is not a positive int.

    A malformed `LORE_EDEN_PROFILE_TOP` must not take the run down: the profile is
    an observation, and refusing to finish a suite over a bad display setting would
    make the tool more expensive than the problem it reports.
    """
    if raw is None:
        return DEFAULT_TOP_N
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_TOP_N
    return parsed if parsed > 0 else DEFAULT_TOP_N


def scope_kind(requested: list[str], testpaths: list[str]) -> ScopeKind:
    """Classify a run as whole-suite or narrowed.

    pytest resolves an argument-less invocation to the configured ``testpaths``, so
    those two cases are the same run and both count as `ScopeKind.FULL`. Anything
    else named explicitly is a selection.
    """
    if not requested or requested == testpaths:
        return ScopeKind.FULL
    return ScopeKind.SELECTED


class NodeProfile:
    """The accumulating metrics for one test node id.

    Named for the node rather than the test because pytest collects any class
    called ``Test*`` that it imports, and a profiler that surfaces in its own
    report as an uncollectable test class is its own small bug.
    """

    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid
        self.outcome = ""
        self.metrics: dict[str, float] = {}

    def record(self, phase: Phase, seconds: float, outcome: str) -> None:
        """Fold one phase report into this test's metrics.

        The test's own outcome is taken from the call phase when there is one, and
        otherwise from whichever phase did not pass — a test that errors in setup
        never reaches `Phase.CALL`, and reporting it as passed would be a lie the
        artifact then carries.
        """
        self.metrics[metric_name(phase)] = seconds
        if phase is Phase.CALL or not self.outcome or outcome != "passed":
            self.outcome = outcome

    @property
    def total(self) -> float:
        """Wall clock across every phase recorded so far."""
        return sum(self.metrics.values())

    def as_dict(self) -> dict[str, Any]:
        """The artifact's per-test row, with the total materialized as a metric."""
        metrics = dict(self.metrics)
        metrics[TOTAL_METRIC] = self.total
        return {"nodeid": self.nodeid, "outcome": self.outcome, "metrics": metrics}


class Profiler:
    """Collects per-test metrics and writes them out at the end of the session.

    Registered as a plugin object rather than as module-level hook functions,
    because pytest only discovers hooks declared in a conftest's own namespace or
    on a registered plugin — and both conftests in this repository sit below their
    rootdir, where a `pytest_plugins` declaration is an error.
    """

    def __init__(self, suite: str, output: Path, top_n: int = DEFAULT_TOP_N) -> None:
        self.suite = suite
        self.output = output
        self.top_n = top_n
        self.profiles: dict[str, NodeProfile] = {}
        self.collected = 0
        self.write_error = ""

    # -- collection -------------------------------------------------------

    def pytest_collection_finish(self, session: Any) -> None:
        """Record how many tests this run actually collected."""
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report: Any) -> None:
        """Fold every phase report of every test into its profile."""
        try:
            phase = Phase(report.when)
        except ValueError:
            return
        profile = self.profiles.setdefault(report.nodeid, NodeProfile(report.nodeid))
        profile.record(phase, float(report.duration), str(report.outcome))

    # -- output -----------------------------------------------------------

    def slowest(self) -> list[NodeProfile]:
        """Profiles ordered by total wall clock, slowest first."""
        return sorted(self.profiles.values(), key=lambda p: p.total, reverse=True)

    def document(self, requested: list[str], testpaths: list[str]) -> dict[str, Any]:
        """The whole artifact, ready to serialize."""
        ordered = self.slowest()
        return {
            "schema_version": SCHEMA_VERSION,
            "suite": self.suite,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "scope": {
                "kind": scope_kind(requested, testpaths).value,
                "requested": requested,
                "collected": self.collected,
                "measured": len(ordered),
            },
            "metrics": {
                metric_name(phase): MetricUnit.SECONDS.value for phase in Phase
            }
            | {TOTAL_METRIC: MetricUnit.SECONDS.value},
            "tests": [profile.as_dict() for profile in ordered],
        }

    def write(self, document: dict[str, Any]) -> None:
        """Write the artifact, remembering any failure so the summary can report it.

        A profile that could not be written must say so out loud. Swallowing the
        error would leave a green run and no file, which reads exactly like a run
        that was never profiled.
        """
        try:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            self.write_error = str(exc)

    def pytest_sessionfinish(self, session: Any) -> None:
        """Serialize the profile once the run is over."""
        config = session.config
        requested = [str(arg) for arg in config.args]
        testpaths = [str(path) for path in (config.getini("testpaths") or [])]
        self.write(self.document(requested, testpaths))

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        """Print the slowest tests, and where the artifact landed."""
        write_line = terminalreporter.write_line
        write_line("")
        if self.write_error:
            write_line(f"profile: could not write {self.output}: {self.write_error}")
        else:
            write_line(f"profile: wrote {self.output} ({len(self.profiles)} tests)")
        for profile in self.slowest()[: self.top_n]:
            write_line(f"  {profile.total:8.3f}s  {profile.nodeid}")


def register(config: Any, suite: str, environ: dict[str, str] | None = None) -> Profiler | None:
    """Attach the profiler to a run, if this one was asked to be profiled.

    Called from each suite's ``pytest_configure``. Returns the registered profiler
    so a test can drive it, or None when `ENV_OUTPUT` is unset — which is every
    ordinary run.
    """
    env = os.environ if environ is None else environ
    destination = env.get(ENV_OUTPUT, "").strip()
    if not destination:
        return None
    profiler = Profiler(
        suite=suite,
        output=Path(destination),
        top_n=resolve_top_n(env.get(ENV_TOP_N)),
    )
    config.pluginmanager.register(profiler, "lore-eden-profile")
    return profiler
