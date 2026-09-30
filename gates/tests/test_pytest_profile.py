"""Tests for the per-test profiler.

The end-to-end case drives a real pytest in a subprocess against a throwaway
suite, in the same spirit as the rest of this directory: half of what this plugin
does is observe pytest's own phase reporting, and a hand-built fake report would
test the half that was never in question.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_profile
from pytest_profile import (
    DEFAULT_TOP_N,
    SCHEMA_VERSION,
    TOTAL_METRIC,
    MetricUnit,
    NodeProfile,
    Phase,
    Profiler,
    ScopeKind,
    metric_name,
    resolve_top_n,
    scope_kind,
)

GATES_DIR = Path(__file__).resolve().parent.parent / "lore_eden_gates"


class FakePluginManager:
    """Records what a conftest would have registered."""

    def __init__(self) -> None:
        self.registered: list[tuple[object, str]] = []

    def register(self, plugin: object, name: str) -> None:
        self.registered.append((plugin, name))


class FakeConfig:
    """Just enough of pytest's config for `register`."""

    def __init__(self) -> None:
        self.pluginmanager = FakePluginManager()


class FakeReport:
    """One phase report, as `pytest_runtest_logreport` receives it."""

    def __init__(self, nodeid: str, when: str, duration: float, outcome: str) -> None:
        self.nodeid = nodeid
        self.when = when
        self.duration = duration
        self.outcome = outcome


# -- metric naming ---------------------------------------------------------


def test_metric_names_carry_their_unit():
    assert metric_name(Phase.CALL) == "call_seconds"
    assert TOTAL_METRIC == "total_seconds"


def test_every_phase_has_a_distinct_metric_name():
    names = {metric_name(phase) for phase in Phase}
    assert len(names) == len(list(Phase))


# -- top-n resolution ------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "nonsense", "0", "-4"])
def test_a_bad_top_n_falls_back_rather_than_failing_the_run(raw):
    assert resolve_top_n(raw) == DEFAULT_TOP_N


def test_a_valid_top_n_is_honoured():
    assert resolve_top_n("3") == 3


# -- scope classification --------------------------------------------------


def test_no_arguments_is_a_full_run():
    assert scope_kind([], ["tests"]) is ScopeKind.FULL


def test_arguments_equal_to_testpaths_is_still_a_full_run():
    assert scope_kind(["tests"], ["tests"]) is ScopeKind.FULL


def test_a_narrowed_run_is_marked_selected():
    assert scope_kind(["tests/test_one.py"], ["tests"]) is ScopeKind.SELECTED


# -- per-test accumulation -------------------------------------------------


def test_phases_accumulate_into_a_total():
    profile = NodeProfile("t::a")
    profile.record(Phase.SETUP, 0.5, "passed")
    profile.record(Phase.CALL, 1.5, "passed")
    profile.record(Phase.TEARDOWN, 0.25, "passed")
    assert profile.total == pytest.approx(2.25)
    assert profile.as_dict()["metrics"][TOTAL_METRIC] == pytest.approx(2.25)


def test_a_setup_error_is_not_reported_as_a_pass():
    """A test that errors in setup never reaches the call phase."""
    profile = NodeProfile("t::a")
    profile.record(Phase.SETUP, 0.1, "failed")
    profile.record(Phase.TEARDOWN, 0.1, "passed")
    assert profile.outcome == "failed"


def test_a_call_failure_survives_a_passing_teardown():
    profile = NodeProfile("t::a")
    profile.record(Phase.SETUP, 0.1, "passed")
    profile.record(Phase.CALL, 0.1, "failed")
    profile.record(Phase.TEARDOWN, 0.1, "passed")
    assert profile.outcome == "failed"


# -- the artifact ----------------------------------------------------------


def _profiler(tmp_path: Path) -> Profiler:
    return Profiler(suite="demo", output=tmp_path / "out" / "profile.json")


def test_unknown_phases_are_ignored(tmp_path):
    """pytest emits reports this plugin has no metric for; they must not crash it."""
    profiler = _profiler(tmp_path)
    profiler.pytest_runtest_logreport(FakeReport("t::a", "collect", 0.1, "passed"))
    assert profiler.profiles == {}


def test_tests_are_ordered_slowest_first(tmp_path):
    profiler = _profiler(tmp_path)
    profiler.pytest_runtest_logreport(FakeReport("t::fast", "call", 0.01, "passed"))
    profiler.pytest_runtest_logreport(FakeReport("t::slow", "call", 2.0, "passed"))
    document = profiler.document(requested=[], testpaths=["tests"])
    assert [row["nodeid"] for row in document["tests"]] == ["t::slow", "t::fast"]


def test_the_document_declares_its_metrics_and_units(tmp_path):
    profiler = _profiler(tmp_path)
    document = profiler.document(requested=[], testpaths=["tests"])
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["metrics"][metric_name(Phase.CALL)] == MetricUnit.SECONDS.value
    assert document["metrics"][TOTAL_METRIC] == MetricUnit.SECONDS.value


def test_a_narrowed_run_records_what_it_actually_ran(tmp_path):
    """Without this a narrowed profile would be silently comparable to a full one."""
    profiler = _profiler(tmp_path)
    document = profiler.document(requested=["tests/test_one.py"], testpaths=["tests"])
    assert document["scope"]["kind"] == ScopeKind.SELECTED.value
    assert document["scope"]["requested"] == ["tests/test_one.py"]


def test_writing_creates_missing_parent_directories(tmp_path):
    profiler = _profiler(tmp_path)
    profiler.write(profiler.document(requested=[], testpaths=[]))
    assert profiler.write_error == ""
    assert json.loads(profiler.output.read_text())["suite"] == "demo"


def test_a_failed_write_is_remembered_rather_than_swallowed(tmp_path):
    """A green run with no file must not look like a run that was never profiled."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    profiler = Profiler(suite="demo", output=blocker / "profile.json")
    profiler.write(profiler.document(requested=[], testpaths=[]))
    assert profiler.write_error != ""


# -- registration ----------------------------------------------------------


def test_an_ordinary_run_registers_nothing():
    config = FakeConfig()
    assert pytest_profile.register(config, suite="demo", environ={}) is None
    assert config.pluginmanager.registered == []


def test_a_blank_setting_is_treated_as_unset():
    config = FakeConfig()
    assert pytest_profile.register(config, suite="demo", environ={"LORE_EDEN_PROFILE": "  "}) is None


def test_requesting_a_profile_registers_the_plugin(tmp_path):
    config = FakeConfig()
    env = {"LORE_EDEN_PROFILE": str(tmp_path / "p.json"), "LORE_EDEN_PROFILE_TOP": "4"}
    profiler = pytest_profile.register(config, suite="demo", environ=env)
    assert isinstance(profiler, Profiler)
    assert profiler.top_n == 4
    assert config.pluginmanager.registered == [(profiler, "lore-eden-profile")]


# -- end to end ------------------------------------------------------------


def _write_demo_suite(root: Path) -> None:
    """A throwaway suite with one deliberately slow test."""
    (root / "conftest.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(GATES_DIR)!r})\n"
        "import pytest_profile\n"
        "def pytest_configure(config):\n"
        "    pytest_profile.register(config, suite='demo')\n"
    )
    (root / "test_demo.py").write_text(
        "import time\n"
        "def test_quick():\n"
        "    assert True\n"
        "def test_slow():\n"
        "    time.sleep(0.25)\n"
        "    assert True\n"
    )


def test_a_real_run_writes_a_profile_naming_the_slow_test(tmp_path):
    out = tmp_path / "artifacts" / "profile.json"
    _write_demo_suite(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LORE_EDEN_PROFILE": str(out)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    document = json.loads(out.read_text())
    assert document["suite"] == "demo"
    assert document["scope"]["collected"] == 2
    assert document["scope"]["measured"] == 2

    slowest = document["tests"][0]
    assert slowest["nodeid"].endswith("::test_slow")
    assert slowest["outcome"] == "passed"
    assert slowest["metrics"][metric_name(Phase.CALL)] >= 0.25
    assert slowest["metrics"][TOTAL_METRIC] >= slowest["metrics"][metric_name(Phase.CALL)]

    assert "profile: wrote" in proc.stdout
    assert "::test_slow" in proc.stdout


def test_an_unprofiled_run_writes_nothing(tmp_path):
    out = tmp_path / "artifacts" / "profile.json"
    _write_demo_suite(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not out.exists()
    assert "profile:" not in proc.stdout
