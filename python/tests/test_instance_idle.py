"""Idle instances: who counts as using one, and what a sweep does to one nobody is.

Driven against real processes, like `test_instances.py`: what a sweep must get
right is whether the process group is really gone and whether the record that
says so survives. Time is passed in as ``now`` rather than slept through.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from lore_eden.instances import (
    InstanceKind,
    InstanceManager,
    InstanceRecord,
    InstanceState,
    LaunchRequest,
    register_self,
)
from lore_eden.instances import manager as manager_module
from lore_eden.instances.tools import instance_tools, launch_instance

IDLE = timedelta(minutes=30)


def _launch(manager: InstanceManager, *, owner: str | None = None, mode: str = "--ok") -> str:
    view = manager.launch(LaunchRequest(template="api", params={"mode": mode}, owner=owner))
    return manager.wait_ready(view.id).id


def _later(manager: InstanceManager, instance_id: str, by: timedelta) -> datetime:
    record = manager.registry.get(instance_id)
    assert record is not None and record.last_active_at is not None
    return record.last_active_at + by


def _never(_record: InstanceRecord) -> bool:
    return False


# -- activity ----------------------------------------------------------------


def test_launch_records_its_owner_and_counts_as_a_use(instance_manager: InstanceManager) -> None:
    instance_id = _launch(instance_manager, owner="run-1")
    record = instance_manager.registry.get(instance_id)
    assert record is not None
    assert record.owner == "run-1"
    assert record.last_active_at == record.started_at


def test_reading_one_instance_is_a_use_but_listing_is_not(instance_manager: InstanceManager) -> None:
    instance_id = _launch(instance_manager)
    later = _later(instance_manager, instance_id, timedelta(minutes=5))

    with mock.patch.object(manager_module, "datetime", wraps=datetime) as clock:
        clock.now.return_value = later
        instance_manager.list()
        listed = instance_manager.registry.get(instance_id)
        assert listed is not None and listed.last_active_at != later

        instance_manager.get(instance_id)
    touched = instance_manager.registry.get(instance_id)
    assert touched is not None and touched.last_active_at == later


def test_logs_count_as_a_use(instance_manager: InstanceManager) -> None:
    instance_id = _launch(instance_manager)
    later = _later(instance_manager, instance_id, timedelta(minutes=5))
    with mock.patch.object(manager_module, "datetime", wraps=datetime) as clock:
        clock.now.return_value = later
        instance_manager.logs(instance_id)
    record = instance_manager.registry.get(instance_id)
    assert record is not None and record.last_active_at == later


def test_a_use_inside_the_resolution_is_not_written_again(instance_manager: InstanceManager) -> None:
    instance_id = _launch(instance_manager)
    soon = _later(instance_manager, instance_id, timedelta(seconds=5))
    before = instance_manager.registry.get(instance_id)
    instance_manager.touch(instance_id, now=soon)
    assert instance_manager.registry.get(instance_id) == before


# -- the sweep ---------------------------------------------------------------


def test_an_idle_instance_is_stopped_and_its_record_kept_with_the_reason(
    instance_manager: InstanceManager,
) -> None:
    instance_id = _launch(instance_manager)
    record = instance_manager.registry.get(instance_id)
    assert record is not None
    now = _later(instance_manager, instance_id, IDLE + timedelta(seconds=1))

    sweep = instance_manager.stop_idle(idle=IDLE, keep=_never, now=now)

    assert [stop.id for stop in sweep.stopped] == [instance_id]
    assert sweep.stopped[0].idle_seconds == int((IDLE + timedelta(seconds=1)).total_seconds())
    assert sweep.failed == []
    assert not instance_manager.registry.is_alive(record)
    view = instance_manager.get(instance_id)
    assert view.state == InstanceState.EXITED
    assert view.idle_stopped_at == now
    assert view.last_error is not None and view.last_error.startswith("stopped after 30 min with no activity")
    # Kept for the reader: the log says what it was doing when it went idle.
    assert Path(view.log_path or "").exists()
    assert Path(view.data_dir or "").exists()


def test_dismissing_an_idle_stopped_instance_cleans_up_as_stop_always_did(
    instance_manager: InstanceManager,
) -> None:
    instance_id = _launch(instance_manager)
    now = _later(instance_manager, instance_id, IDLE)
    instance_manager.stop_idle(idle=IDLE, keep=_never, now=now)
    view = instance_manager.get(instance_id)

    instance_manager.stop(instance_id)

    assert instance_manager.registry.get(instance_id) is None
    assert not Path(view.data_dir or "").exists()
    assert not Path(view.log_path or "").exists()


def test_an_instance_used_inside_the_window_is_left_running(instance_manager: InstanceManager) -> None:
    instance_id = _launch(instance_manager)
    now = _later(instance_manager, instance_id, IDLE - timedelta(seconds=1))
    asked: list[str] = []

    def keep(record: InstanceRecord) -> bool:
        asked.append(record.id)
        return False

    sweep = instance_manager.stop_idle(idle=IDLE, keep=keep, now=now)

    assert sweep.stopped == []
    assert asked == []  # the host's veto is only asked about an instance already idle
    assert instance_manager.get(instance_id).state == InstanceState.READY


def test_the_hosts_veto_spares_an_idle_instance(instance_manager: InstanceManager) -> None:
    spared = _launch(instance_manager, owner="run-in-flight")
    idle = _launch(instance_manager, owner="run-finished")
    now = max(_later(instance_manager, spared, IDLE), _later(instance_manager, idle, IDLE))

    sweep = instance_manager.stop_idle(
        idle=IDLE, keep=lambda record: record.owner == "run-in-flight", now=now
    )

    assert [stop.id for stop in sweep.stopped] == [idle]
    assert instance_manager.get(spared).state == InstanceState.READY


def test_an_exception_from_the_veto_stops_nothing(instance_manager: InstanceManager) -> None:
    instance_id = _launch(instance_manager)
    now = _later(instance_manager, instance_id, IDLE)

    def broken(_record: InstanceRecord) -> bool:
        raise RuntimeError("database is locked")

    with pytest.raises(RuntimeError, match="database is locked"):
        instance_manager.stop_idle(idle=IDLE, keep=broken, now=now)
    assert instance_manager.get(instance_id).state == InstanceState.READY


def test_a_use_after_the_scan_spares_the_instance(instance_manager: InstanceManager) -> None:
    instance_id = _launch(instance_manager)
    now = _later(instance_manager, instance_id, IDLE)

    def used_meanwhile(record: InstanceRecord) -> bool:
        instance_manager.touch(record.id, now=now)
        return False

    sweep = instance_manager.stop_idle(idle=IDLE, keep=used_meanwhile, now=now)

    assert sweep.stopped == []
    assert instance_manager.get(instance_id).state == InstanceState.READY


def test_a_main_or_self_registered_instance_is_never_considered(
    instance_manager: InstanceManager,
) -> None:
    handle = register_self(
        instance_manager.registry, project="demo", name="main",
        kind=InstanceKind.SERVER, host="127.0.0.1", port=8000,
    )
    assert handle is not None
    try:
        asked: list[str] = []
        now = handle.record.started_at + IDLE * 10
        sweep = instance_manager.stop_idle(
            idle=IDLE, keep=lambda record: asked.append(record.id) or False, now=now
        )
        assert sweep.stopped == [] and sweep.failed == []
        assert asked == []
        assert instance_manager.registry.get(handle.record.id) is not None
    finally:
        handle.release()


def test_an_instance_that_cannot_be_signalled_is_reported_and_left_alone(
    instance_manager: InstanceManager,
) -> None:
    instance_id = _launch(instance_manager)
    before = instance_manager.registry.get(instance_id)
    now = _later(instance_manager, instance_id, IDLE)

    with mock.patch.object(
        InstanceManager, "_terminate", side_effect=PermissionError(1, "Operation not permitted")
    ):
        sweep = instance_manager.stop_idle(idle=IDLE, keep=_never, now=now)

    assert sweep.stopped == []
    assert [(f.id, f.error) for f in sweep.failed] == [(instance_id, "[Errno 1] Operation not permitted")]
    assert instance_manager.registry.get(instance_id) == before
    assert instance_manager.get(instance_id).state == InstanceState.READY


def test_a_group_that_survives_the_stop_is_reported_not_marked_stopped(
    instance_manager: InstanceManager,
) -> None:
    instance_id = _launch(instance_manager)
    now = _later(instance_manager, instance_id, IDLE)

    with mock.patch.object(InstanceManager, "_terminate"):
        sweep = instance_manager.stop_idle(idle=IDLE, keep=_never, now=now)

    assert sweep.stopped == []
    assert len(sweep.failed) == 1 and "still running after SIGKILL" in sweep.failed[0].error
    record = instance_manager.registry.get(instance_id)
    assert record is not None and record.idle_stopped_at is None and record.last_error is None


def test_a_host_that_cannot_signal_a_group_disables_the_sweep(
    instance_manager: InstanceManager,
) -> None:
    instance_id = _launch(instance_manager)
    now = _later(instance_manager, instance_id, IDLE)

    with mock.patch.object(manager_module, "os", spec=["environ", "getpid"]):
        sweep = instance_manager.stop_idle(idle=IDLE, keep=_never, now=now)

    assert "cannot signal a process group" in sweep.disabled_reason
    assert sweep.stopped == [] and sweep.failed == []
    assert instance_manager.get(instance_id).state == InstanceState.READY


@pytest.mark.parametrize("window", [timedelta(0), timedelta(seconds=-1)])
def test_a_window_that_is_not_positive_is_refused(
    instance_manager: InstanceManager, window: timedelta
) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        instance_manager.stop_idle(idle=window, keep=_never)


def test_restarting_an_idle_stopped_instance_clears_the_stop(instance_manager: InstanceManager) -> None:
    instance_id = _launch(instance_manager)
    now = _later(instance_manager, instance_id, IDLE)
    instance_manager.stop_idle(idle=IDLE, keep=_never, now=now)

    view = instance_manager.wait_ready(instance_manager.restart(instance_id).id)

    assert view.state == InstanceState.READY
    assert view.idle_stopped_at is None
    assert view.last_error is None
    assert view.last_active_at == view.started_at


# -- the agent's view --------------------------------------------------------


def test_the_launch_tool_body_records_its_callers_owner(instance_manager: InstanceManager) -> None:
    reply = json.loads(launch_instance(instance_manager, {"template": "api"}, owner="run-7"))
    assert reply["ok"]
    record = instance_manager.registry.get(reply["instance"]["id"])
    assert record is not None and record.owner == "run-7"


def test_an_idle_stop_shows_in_the_listing_summary(instance_manager: InstanceManager) -> None:
    instance_id = _launch(instance_manager)
    now = _later(instance_manager, instance_id, IDLE)
    instance_manager.stop_idle(idle=IDLE, keep=_never, now=now)
    tools = {definition.name: handler for definition, handler in instance_tools(lambda: instance_manager)}

    listed = json.loads(tools["list_instances"](None, {}))

    [row] = [row for row in listed["instances"] if row["id"] == instance_id]
    assert row["state"] == "exited"
    assert row["idle_stopped_at"] == now.astimezone(timezone.utc).isoformat()
    assert row["last_error"].startswith("stopped after 30 min")
