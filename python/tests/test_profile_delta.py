"""Tests for the profile delta gate: a run against the median of the runs before it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from lore_eden.testing import profile_delta
from lore_eden.testing.profile_delta import (
    DEFAULT_RUNS,
    DEFAULT_TOP,
    Measurement,
    ProfileError,
    baseline,
    compare,
    from_jest,
    measure,
)
from lore_eden.testing.pytest_profile import TOTAL_METRIC


def _profile(
    files: dict[str, float], suite: str = "server", outcome: str = "passed"
) -> dict:
    """A profile holding one test per file, taking that file's seconds."""
    return {
        "schema_version": 1,
        "suite": suite,
        "scope": {
            "kind": "full",
            "requested": [],
            "collected": len(files),
            "measured": len(files),
        },
        "tests": [
            {
                "nodeid": f"{path}::test_it",
                "outcome": outcome,
                "metrics": {TOTAL_METRIC: s},
            }
            for path, s in files.items()
        ],
    }


def _files(scale: float = 1.0) -> dict[str, float]:
    return {f"tests/test_{n}.py": 10.0 * (n + 1) * scale for n in range(12)}


def _run(scale: float = 1.0, complete: bool = True) -> dict[str, Measurement]:
    return {"server": Measurement("server", _files(scale), complete)}


def _slowed(fraction: float, path: str = "tests/test_0.py") -> dict[str, Measurement]:
    files = _files()
    files[path] += sum(files.values()) * fraction
    return {"server": Measurement("server", files, True)}


# -- the threshold --------------------------------------------------------------


def test_a_25_percent_slowdown_fails_and_a_10_percent_one_passes():
    base = baseline([_run() for _ in range(DEFAULT_RUNS)])
    [slow] = compare(base, _slowed(0.25))
    [fine] = compare(base, _slowed(0.10))
    assert not slow.passed
    assert fine.passed
    assert slow.grown[0][0] == "tests/test_0.py"


def test_exactly_the_threshold_passes():
    [verdict] = compare(baseline([_run()]), _slowed(0.20))
    assert verdict.passed


def test_the_failure_names_the_top_files_by_growth_not_by_size():
    base = baseline([_run()])
    files = _files(1.5)
    files["tests/test_new.py"] = 500.0
    # The slowest file kept its time: slow is not the same as grown.
    files["tests/test_11.py"] = _files()["tests/test_11.py"]
    [verdict] = compare(base, {"server": Measurement("server", files, True)})
    assert not verdict.passed
    assert len(verdict.grown) == DEFAULT_TOP
    assert verdict.grown[0] == ("tests/test_new.py", 0.0, 500.0)
    growth = [new - old for _path, old, new in verdict.grown]
    assert growth == sorted(growth, reverse=True)
    named = {path for path, _old, _new in verdict.grown}
    assert named.isdisjoint({f"tests/test_{n}.py" for n in (0, 1, 11)})


def test_each_suite_is_judged_on_its_own():
    base = baseline(
        [{**_run(), "client": Measurement("client", {"a.test.ts": 10.0}, True)}]
    )
    current = {**_run(), "client": Measurement("client", {"a.test.ts": 13.0}, True)}
    assert {v.suite: v.passed for v in compare(base, current)} == {
        "server": True,
        "client": False,
    }


def test_a_suite_only_one_side_has_is_said_and_passed():
    base = baseline([{"client": Measurement("client", {"a.test.ts": 1.0}, True)}])
    verdicts = {v.suite: v for v in compare(base, _run(9))}
    assert verdicts["server"].passed
    assert "not compared, the baseline has no runs of it" in verdicts["server"].message
    assert "not compared, this run has none" in verdicts["client"].message


def test_the_report_lists_the_grown_files():
    text = profile_delta.report(compare(baseline([_run()]), _slowed(0.25)))
    assert "❌ server:" in text
    assert "| `tests/test_0.py` |" in text


# -- the baseline ---------------------------------------------------------------


def test_the_baseline_is_the_median_of_the_newest_complete_runs():
    newest_first = [_run(1.0 + n / 100) for n in range(DEFAULT_RUNS)]
    runs = (
        newest_first[:4] + [_run(0.1, complete=False)] + newest_first[4:] + [_run(0.1)]
    )
    labels = [f"r{n}" for n in range(len(runs))]
    stored = baseline(runs, labels)["suites"]["server"]
    assert len(stored["runs"]) == DEFAULT_RUNS
    assert "r4" not in stored["runs"]
    assert stored["runs"][-1] == "r10"
    assert stored["total_seconds"] == pytest.approx(
        sum(_files().values()) * 1.045, rel=1e-3
    )
    assert stored["files"]["tests/test_0.py"] == pytest.approx(10.45, rel=1e-3)


def test_a_run_that_measured_only_one_suite_counts_for_that_suite():
    client_only = {"client": Measurement("client", {"a.test.ts": 1.0}, True)}
    stored = baseline([client_only, _run()], ["c", "both"])["suites"]
    assert stored["server"]["runs"] == ["both"]
    assert stored["client"]["runs"] == ["c"]


def test_a_failed_or_interrupted_run_is_incomplete():
    assert measure([_profile({"a.py": 1.0})])["server"].complete
    assert not measure([_profile({"a.py": 1.0}, outcome="failed")])["server"].complete
    interrupted = _profile({"a.py": 1.0})
    interrupted["scope"]["collected"] = 5
    assert not measure([interrupted])["server"].complete


def test_shards_are_summed_per_file():
    measured = measure([_profile({"a.py": 1.0, "b.py": 2.0}), _profile({"a.py": 3.0})])
    assert measured["server"].files == {"a.py": 4.0, "b.py": 2.0}
    assert measured["server"].total_seconds == 6.0


def test_a_document_in_another_shape_is_an_error_not_a_zero():
    with pytest.raises(ProfileError):
        measure([{"suite": "server", "scope": {}, "tests": []}])
    with pytest.raises(ProfileError):
        compare({}, _run())
    with pytest.raises(ProfileError):
        from_jest({"testResults": [{"name": "x"}]}, "/w")


# -- jest -----------------------------------------------------------------------


JEST = {
    "numTotalTestSuites": 2,
    "testResults": [
        {
            "name": "/w/client/src/a.test.ts",
            "status": "passed",
            "startTime": 1000,
            "endTime": 3500,
        },
        {
            "name": "/w/client/src/b.test.tsx",
            "status": "failed",
            "startTime": 0,
            "endTime": 250,
        },
    ],
}


def test_jest_results_become_a_profile_of_one_row_per_file():
    profile = from_jest(JEST, "/w/client")
    measured = measure([profile])["client"]
    assert measured.files == {"src/a.test.ts": 2.5, "src/b.test.tsx": 0.25}
    assert not measured.complete
    assert profile["scope"] == {
        "kind": "full",
        "requested": [],
        "collected": 2,
        "measured": 2,
    }


def test_an_interrupted_jest_run_is_incomplete():
    partial = {**JEST, "numTotalTestSuites": 3}
    partial["testResults"] = JEST["testResults"][:1]
    assert not measure([from_jest(partial, "/w/client")])["client"].complete


# -- end to end -----------------------------------------------------------------


@pytest.fixture(name="real_profile", scope="module")
def real_profile_fixture(tmp_path_factory) -> dict:
    """A profile written by `pytest_profile` itself, under a real pytest."""
    root = tmp_path_factory.mktemp("suite")
    (root / "tests").mkdir()
    (root / "tests" / "conftest.py").write_text(
        "from lore_eden.testing import pytest_profile\n"
        "def pytest_configure(config):\n"
        "    pytest_profile.register(config, suite='server')\n"
    )
    (root / "tests" / "test_two.py").write_text(
        "def test_a():\n    pass\ndef test_b():\n    pass\n"
    )
    (root / "tests" / "test_one.py").write_text("def test_c():\n    pass\n")
    out = root / "profile.json"
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTEST_", "LORE_EDEN"))
    }
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=root,
        env={**env, "LORE_EDEN_PROFILE": str(out)},
        capture_output=True,
        check=True,
    )
    return json.loads(out.read_text())


def test_a_real_profile_is_measured_per_file(real_profile):
    measured = measure([real_profile])["server"]
    assert set(measured.files) == {"tests/test_one.py", "tests/test_two.py"}
    assert measured.complete


def _write_run(directory: Path, *profiles: dict) -> Path:
    directory.mkdir(parents=True)
    for n, profile in enumerate(profiles):
        (directory / f"shard-{n}.json").write_text(json.dumps(profile))
    return directory


def test_the_command_line_stores_a_baseline_and_compares_against_it(tmp_path, capsys):
    runs = [_write_run(tmp_path / f"main-{n}", _profile(_files())) for n in range(3)]
    base = tmp_path / "baseline.json"
    argv = ["baseline", "--out", str(base)]
    for run in runs:
        argv += ["--run", str(run)]
    assert profile_delta.main(argv) == 0
    assert json.loads(base.read_text())["suites"]["server"]["runs"] == [
        "main-0",
        "main-1",
        "main-2",
    ]

    slow = _files()
    slow["tests/test_0.py"] += sum(slow.values()) * 0.25
    fine = _files(1.1)
    summary = tmp_path / "summary.md"
    for name, files, code in (("slow", slow, 1), ("fine", fine, 0)):
        current = _write_run(tmp_path / name, _profile(files))
        argv = ["compare", "--baseline", str(base), "--current", str(current)]
        assert profile_delta.main([*argv, "--summary", str(summary)]) == code
    assert "| `tests/test_0.py` |" in summary.read_text()
    capsys.readouterr()


def test_the_command_line_converts_jest_output(tmp_path):
    results = tmp_path / "jest.json"
    results.write_text(json.dumps(JEST))
    out = tmp_path / "client.json"
    argv = ["from-jest", str(results), "--root", "/w/client", "--out", str(out)]
    assert profile_delta.main(argv) == 0
    assert json.loads(out.read_text())["suite"] == "client"


def test_the_command_line_says_what_is_wrong_and_exits_2(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    base = tmp_path / "b.json"
    assert (
        profile_delta.main(["baseline", "--run", str(empty), "--out", str(base)]) == 2
    )
    assert "no profiles under" in capsys.readouterr().err


def test_the_module_runs_as_a_command(tmp_path):
    results = tmp_path / "jest.json"
    results.write_text(json.dumps(JEST))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "lore_eden.testing.profile_delta",
            "from-jest",
            str(results),
            "--root",
            "/w/client",
            "--out",
            str(tmp_path / "p.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
