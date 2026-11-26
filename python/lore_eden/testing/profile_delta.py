"""The delta gate `pytest_profile` left for later: this run against a stored baseline.

`pytest_profile` writes what each test cost and fails nothing, because one run's
wall clock on a busy machine is noise. Noise averages out across runs, though, and
a suite that grew by a fifth is not noise. loregarden's server suite went from
about nine minutes to over an hour without one run saying so, because nothing
compared a run with the runs before it. This module is that comparison.

## What is compared

A test *file*'s seconds: the sum of `total_seconds` over its tests, and over every
profile of the run, since a sharded suite writes one profile per shard. A suite's
size is the sum over its files, not the job's wall clock, which moves with how many
runners or workers there were. Files are the unit because that is what a person
acts on: "tests/test_symbol_map.py grew 300s" names the place to look, where a
single total does not and four thousand test ids are too many.

## The baseline

The median, per suite and per file, of the newest `DEFAULT_RUNS` runs that are
*complete*: every test measured that was collected, and none failed. A failed or
interrupted run's times describe a different suite. A median of ten is not moved
by one slow runner, or by one odd run that got in.

Which runs are eligible (a trunk branch's, never a pull request's own) is the
consumer's decision, made before calling `baseline`; this module only refuses
incomplete ones.

## Jest

`from_jest` turns `jest --json` output into this same schema, one row per test
file (jest's per-file start to end, which includes the module imports and the
setup a per-test sum would miss), so a frontend suite is baselined and compared by
exactly the same code. Its node ids are file paths with no `::` part.

Usage::

    python -m lore_eden.testing.profile_delta from-jest results.json --root client --out p.json
    python -m lore_eden.testing.profile_delta baseline --run DIR [--run DIR ...] --out base.json
    python -m lore_eden.testing.profile_delta compare --baseline base.json --current DIR

`--run` directories are newest first; each holds one run's profiles (any `*.json`
below it). `compare` exits 1 when a suite grew more than the threshold.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lore_eden.testing.pytest_profile import SCHEMA_VERSION as PROFILE_SCHEMA_VERSION
from lore_eden.testing.pytest_profile import (
    TOTAL_METRIC,
    MetricUnit,
    Phase,
    ScopeKind,
    metric_name,
)

BASELINE_SCHEMA_VERSION = 1
BASELINE_KIND = "profile-baseline"
DEFAULT_RUNS = 10
DEFAULT_THRESHOLD = 0.20
DEFAULT_TOP = 10
#: Outcomes that mean the run did not finish the suite it was asked to.
FAILED_OUTCOMES = frozenset({"failed", "error"})


class ProfileError(Exception):
    """A document is not a profile or a baseline in the shape this package writes."""


@dataclass(frozen=True)
class Measurement:
    """One suite's seconds in one run, per file."""

    suite: str
    files: dict[str, float]
    #: Every collected test measured, and none failed.
    complete: bool

    @property
    def total_seconds(self) -> float:
        return sum(self.files.values())


@dataclass(frozen=True)
class Verdict:
    suite: str
    passed: bool
    message: str
    #: (file, baseline seconds, current seconds), most grown first.
    grown: tuple[tuple[str, float, float], ...] = ()


def file_of(nodeid: str) -> str:
    """The file part of a pytest node id; a jest row's id is already a file."""
    return nodeid.split("::", 1)[0]


def measure(profiles: list[dict[str, Any]]) -> dict[str, Measurement]:
    """Each suite's per-file seconds over every profile of one run (one per shard)."""
    files: dict[str, dict[str, float]] = {}
    collected: dict[str, int] = {}
    measured: dict[str, int] = {}
    failed: set[str] = set()
    try:
        for profile in profiles:
            suite = profile["suite"]
            seconds = files.setdefault(suite, {})
            collected[suite] = collected.get(suite, 0) + int(
                profile["scope"]["collected"]
            )
            measured[suite] = measured.get(suite, 0) + int(profile["scope"]["measured"])
            for test in profile["tests"]:
                path = file_of(test["nodeid"])
                seconds[path] = seconds.get(path, 0.0) + float(
                    test["metrics"][TOTAL_METRIC]
                )
                if test["outcome"] in FAILED_OUTCOMES:
                    failed.add(suite)
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileError(f"not a profile: {exc!r}") from exc
    return {
        suite: Measurement(
            suite, seconds, suite not in failed and measured[suite] >= collected[suite]
        )
        for suite, seconds in files.items()
    }


def baseline(
    runs: list[dict[str, Measurement]],
    labels: list[str] | None = None,
    keep: int = DEFAULT_RUNS,
) -> dict[str, Any]:
    """Per suite, the median total and per-file seconds of the newest `keep` complete runs.

    `runs` is newest first. A file missing from some runs takes the median of the
    runs it was in.
    """
    labels = labels if labels is not None else [str(n) for n in range(len(runs))]
    suites: dict[str, Any] = {}
    names = sorted({name for run in runs for name in run})
    for name in names:
        chosen = [
            (label, run[name])
            for label, run in zip(labels, runs, strict=True)
            if name in run and run[name].complete
        ][:keep]
        if not chosen:
            continue
        per_file: dict[str, list[float]] = {}
        for _label, measurement in chosen:
            for path, seconds in measurement.files.items():
                per_file.setdefault(path, []).append(seconds)
        suites[name] = {
            "runs": [label for label, _m in chosen],
            "total_seconds": round(
                statistics.median(m.total_seconds for _l, m in chosen), 3
            ),
            "files": {
                path: round(statistics.median(s), 3)
                for path, s in sorted(per_file.items())
            },
        }
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "kind": BASELINE_KIND,
        "metric": TOTAL_METRIC,
        "suites": suites,
    }


def compare(
    base: dict[str, Any],
    current: dict[str, Measurement],
    threshold: float = DEFAULT_THRESHOLD,
    top: int = DEFAULT_TOP,
) -> list[Verdict]:
    """A verdict per suite in either side; a suite fails when it grew more than `threshold`.

    A suite only one side has is reported and passed: there is nothing to compare,
    and saying so is the honest answer.
    """
    try:
        stored = base["suites"]
    except (KeyError, TypeError) as exc:
        raise ProfileError(f"not a baseline: {exc!r}") from exc
    verdicts = []
    for name in sorted(set(stored) | set(current)):
        before, now = stored.get(name), current.get(name)
        if before is None or now is None:
            missing = (
                "the baseline has no runs of it"
                if before is None
                else "this run has none"
            )
            verdicts.append(Verdict(name, True, f"{name}: not compared, {missing}"))
            continue
        old, new = before["total_seconds"], now.total_seconds
        growth = new / old - 1 if old else 0.0
        grown = sorted(
            (
                (path, before["files"].get(path, 0.0), s)
                for path, s in now.files.items()
            ),
            key=lambda row: row[2] - row[1],
            reverse=True,
        )[:top]
        passed = growth <= threshold
        verdicts.append(
            Verdict(
                name,
                passed,
                f"{name}: {new:.0f}s against a baseline of {old:.0f}s "
                f"(median of {len(before['runs'])} runs), {growth:+.0%}; "
                f"{'within' if passed else 'over'} the {threshold:.0%} allowance",
                tuple(grown),
            )
        )
    return verdicts


def report(verdicts: list[Verdict]) -> str:
    """Markdown: a line per suite, then each compared suite's most grown files."""
    lines = ["## Suite time against the baseline", ""]
    lines += [f"- {'✅' if v.passed else '❌'} {v.message}" for v in verdicts]
    for verdict in verdicts:
        if not verdict.grown:
            continue
        lines += [
            "",
            f"### {verdict.suite}: the {len(verdict.grown)} files whose time grew most",
            "",
            "| File | Baseline (s) | This run (s) | Change (s) |",
            "|---|---:|---:|---:|",
        ]
        lines += [
            f"| `{path}` | {old:.1f} | {new:.1f} | {new - old:+.1f} |"
            for path, old, new in verdict.grown
        ]
    return "\n".join(lines) + "\n"


def from_jest(
    results: dict[str, Any], root: str, suite: str = "client", created_at: str = ""
) -> dict[str, Any]:
    """`jest --json` output as a profile, one row per test file, paths relative to `root`."""
    prefix = root.rstrip("/") + "/"
    try:
        rows = [
            {
                "nodeid": r["name"].removeprefix(prefix),
                "outcome": r["status"],
                "metrics": {
                    metric_name(Phase.CALL): (
                        seconds := (r["endTime"] - r["startTime"]) / 1000
                    ),
                    TOTAL_METRIC: seconds,
                },
            }
            for r in results["testResults"]
        ]
        collected = int(results["numTotalTestSuites"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileError(f"not jest --json output: {exc!r}") from exc
    rows.sort(key=lambda row: row["metrics"][TOTAL_METRIC], reverse=True)
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "suite": suite,
        "created_at": created_at,
        "scope": {
            "kind": ScopeKind.FULL.value,
            "requested": [],
            "collected": collected,
            "measured": len(rows),
        },
        "metrics": {
            metric_name(Phase.CALL): MetricUnit.SECONDS.value,
            TOTAL_METRIC: MetricUnit.SECONDS.value,
        },
        "tests": rows,
    }


# -- command line ---------------------------------------------------------------


def load_run(directory: Path) -> list[dict[str, Any]]:
    """Every profile below `directory`: one run's, however many shards wrote it."""
    paths = sorted(directory.rglob("*.json"))
    if not paths:
        raise ProfileError(f"no profiles under {directory}")
    try:
        return [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"{directory}: {exc}") from exc


def _from_jest(args: argparse.Namespace) -> int:
    results = json.loads(args.results.read_text(encoding="utf-8"))
    profile = from_jest(results, str(args.root.resolve()), args.suite)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return 0


def _baseline(args: argparse.Namespace) -> int:
    runs = [measure(load_run(directory)) for directory in args.run]
    stored = baseline(runs, [directory.name for directory in args.run], args.keep)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
    for name, suite in stored["suites"].items():
        print(
            f"{name}: {suite['total_seconds']:.0f}s, median of {len(suite['runs'])} runs"
        )
    return 0


def _compare(args: argparse.Namespace) -> int:
    base = json.loads(args.baseline.read_text(encoding="utf-8"))
    verdicts = compare(base, measure(load_run(args.current)), args.threshold, args.top)
    text = report(verdicts)
    print(text)
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(text)
    return 0 if all(verdict.passed for verdict in verdicts) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lore_eden.testing.profile_delta")
    commands = parser.add_subparsers(dest="command", required=True)

    jest = commands.add_parser(
        "from-jest", help="convert jest --json output to a profile"
    )
    jest.add_argument("results", type=Path)
    jest.add_argument("--root", type=Path, required=True, help="the jest rootDir")
    jest.add_argument("--suite", default="client")
    jest.add_argument("--out", type=Path, required=True)
    jest.set_defaults(handler=_from_jest)

    base = commands.add_parser("baseline", help="median of the newest complete runs")
    base.add_argument(
        "--run", type=Path, action="append", required=True, help="newest first"
    )
    base.add_argument("--keep", type=int, default=DEFAULT_RUNS)
    base.add_argument("--out", type=Path, required=True)
    base.set_defaults(handler=_baseline)

    check = commands.add_parser(
        "compare", help="exit 1 when a suite grew past the threshold"
    )
    check.add_argument("--baseline", type=Path, required=True)
    check.add_argument("--current", type=Path, required=True)
    check.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    check.add_argument("--top", type=int, default=DEFAULT_TOP)
    check.add_argument("--summary", type=Path)
    check.set_defaults(handler=_compare)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (ProfileError, OSError, json.JSONDecodeError) as exc:
        print(f"profile_delta: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
