"""This suite can be profiled too, and says so in the artifact.

The profiler itself is tested in `python/tests/test_pytest_profile.py`. What is
tested here is the wiring: that `gates` — which installs no part of the
`lore_eden` package — can still reach the plugin, and that a profiled run of
*this* suite lands an artifact labelled as this suite.

Driven as a real pytest subprocess over a narrow slice of the suite. A profiled
run is the only thing that proves the conftest hook fires, and the import it
performs is exactly the part that a dependency-free package could get wrong.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GATES_ROOT = Path(__file__).resolve().parent.parent
SLICE = "tests/test_interpreter.py"


def _run(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", SLICE],
        cwd=GATES_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", **env},
    )


def test_a_profiled_run_of_this_suite_is_labelled_as_this_suite(tmp_path):
    out = tmp_path / "gates.json"
    proc = _run({"LORE_EDEN_PROFILE": str(out)})
    assert proc.returncode == 0, proc.stdout + proc.stderr

    document = json.loads(out.read_text())
    assert document["suite"] == "gates"
    assert document["scope"]["measured"] == document["scope"]["collected"]
    assert document["scope"]["measured"] > 0
    assert document["scope"]["kind"] == "selected"

    slowest = document["tests"][0]
    assert slowest["nodeid"].startswith("tests/test_interpreter.py")
    assert "total_seconds" in slowest["metrics"]


def test_an_unprofiled_run_writes_nothing(tmp_path):
    """The default path must not import or emit anything."""
    out = tmp_path / "gates.json"
    proc = _run({})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not out.exists()
    assert "profile:" not in proc.stdout
