"""Local instances, against real processes on real ports.

The failures worth catching are at the process boundary — a child that exits
before listening, a group that ignores SIGTERM, a zombie that still answers
``kill(pid, 0)`` — so the launcher drives a real server rather than a mock.
"""

from __future__ import annotations

import os
import select
import signal
import socket
import subprocess
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lore_eden.instances import (
    ENV_INSTANCE_ID,
    CommandTemplate,
    FileInstanceRegistry,
    InstanceKind,
    InstanceManager,
    InstanceRecord,
    InstanceRole,
    InstanceState,
    LaunchRequest,
    NoFreePortError,
    NotManagedError,
    TemplateCatalog,
    TemplateInfo,
    TemplateParam,
    TemplateParamError,
    allocate_port,
    make_instances_router,
    register_self,
    resolve_params,
)
from lore_eden.instances import __main__ as cli
from lore_eden.instances import manager as manager_module
from lore_eden.instances.registry import derive_state


@pytest.fixture
def registry(instance_registry: FileInstanceRegistry) -> FileInstanceRegistry:
    return instance_registry


@pytest.fixture
def manager(instance_manager: InstanceManager) -> InstanceManager:
    return instance_manager


def launch(manager: InstanceManager, mode: str = "--ok"):
    return manager.launch(LaunchRequest(template="api", params={"mode": mode}))


# -- ports -----------------------------------------------------------------


def test_allocate_port_skips_claimed_and_bound_ports() -> None:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        bound = held.getsockname()[1]
        chosen = allocate_port("127.0.0.1", port_range=(bound, bound + 5), claimed={bound + 1})
    assert chosen == bound + 2


def test_allocate_port_raises_when_the_range_is_exhausted() -> None:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        bound = held.getsockname()[1]
        with pytest.raises(NoFreePortError):
            allocate_port("127.0.0.1", port_range=(bound, bound))


# -- registry --------------------------------------------------------------


def _record(**overrides) -> InstanceRecord:
    fields = {
        "id": "demo-x",
        "project": "demo",
        "name": "x",
        "kind": InstanceKind.SERVER,
        "role": InstanceRole.BRANCH,
        "pid": os.getpid(),
        "host": "127.0.0.1",
        "port": 1,
        "url": "http://127.0.0.1:1",
        "started_at": datetime.now(timezone.utc),
    }
    return InstanceRecord(**{**fields, **overrides})


def test_unreadable_record_is_reported_not_dropped(registry: FileInstanceRegistry) -> None:
    registry.put(_record())
    (registry.records_dir / "broken.json").write_text("{not json", encoding="utf-8")
    scan = registry.scan()
    assert [r.id for r in scan.records] == ["demo-x"]
    assert [Path(u.path).name for u in scan.unreadable] == ["broken.json"]


def test_an_id_that_is_not_a_slug_cannot_reach_outside_the_registry(
    registry: FileInstanceRegistry,
) -> None:
    assert registry.get("../../etc/passwd") is None


def test_a_dead_pid_reads_as_exited(registry: FileInstanceRegistry) -> None:
    view = registry.view(_record(pid=2**22 + 7))
    assert view.state == InstanceState.EXITED


def test_a_recycled_pid_in_another_group_reads_as_exited(registry: FileInstanceRegistry) -> None:
    # Alive pid, but not the group the launcher created: someone else's process.
    view = registry.view(_record(pid=os.getpid(), pgid=os.getpgid(os.getpid()) + 1))
    assert view.state == InstanceState.EXITED


def test_never_ready_inside_its_budget_is_stalled_not_starting() -> None:
    started = datetime.now(timezone.utc) - timedelta(seconds=30)
    record = _record(started_at=started, ready_timeout_seconds=10)
    assert derive_state(record, alive=True, now=datetime.now(timezone.utc)) == InstanceState.STALLED


def test_register_self_is_found_as_main_and_released(registry: FileInstanceRegistry) -> None:
    handle = register_self(registry, project="demo", name="main", kind=InstanceKind.SERVER,
                           host="127.0.0.1", port=8000)
    assert handle is not None
    found = registry.find_main("demo")
    assert found is not None and found.url == "http://127.0.0.1:8000" and not found.managed
    handle.release()
    assert registry.find_main("demo") is None


def test_register_self_defers_to_the_launcher_record(registry: FileInstanceRegistry) -> None:
    with mock.patch.dict(os.environ, {ENV_INSTANCE_ID: "demo-launched"}):
        assert register_self(registry, project="demo", name="main", kind=InstanceKind.SERVER,
                             host="127.0.0.1", port=8000) is None
    assert registry.scan().records == []


def test_release_leaves_a_newer_main_alone(registry: FileInstanceRegistry) -> None:
    handle = register_self(registry, project="demo", name="main", kind=InstanceKind.SERVER,
                           host="127.0.0.1", port=8000)
    assert handle is not None
    registry.put(handle.record.model_copy(update={"pid": os.getppid()}))
    handle.release()
    assert registry.get(handle.record.id) is not None


# -- templates -------------------------------------------------------------

_INFO = TemplateInfo(
    name="t",
    kind=InstanceKind.SERVER,
    params=[
        TemplateParam(key="db", label="Database", default="snapshot", choices=["snapshot", "fresh"]),
        TemplateParam(key="tree", label="Worktree", required=True),
    ],
)


def test_params_resolve_with_defaults() -> None:
    assert resolve_params(_INFO, {"tree": "/w"}) == {"db": "snapshot", "tree": "/w"}


@pytest.mark.parametrize(
    ("supplied", "message"),
    [
        ({}, "Worktree is required"),
        ({"tree": "/w", "db": "shared"}, "must be one of"),
        ({"tree": "/w", "dbb": "fresh"}, "unknown parameter"),
    ],
)
def test_params_refuse_what_the_template_does_not_allow(supplied, message) -> None:
    with pytest.raises(TemplateParamError, match=message):
        resolve_params(_INFO, supplied)


def test_registering_a_template_name_twice_is_refused() -> None:
    catalog = TemplateCatalog()
    template = CommandTemplate(project="p", name="t", kind=InstanceKind.SERVER, command=["x"], cwd=".")
    catalog.register(template)
    with pytest.raises(ValueError, match="already registered"):
        catalog.register(template)


# -- launching real processes ----------------------------------------------


def test_launch_becomes_ready_and_stop_frees_everything(manager: InstanceManager) -> None:
    view = launch(manager)
    assert view.state == InstanceState.STARTING
    ready = manager.wait_ready(view.id)
    assert ready.state == InstanceState.READY
    assert manager.health(view.id).ok
    assert any("fake instance starting" in line for line in manager.logs(view.id).lines)
    manager.stop(view.id)
    assert manager.registry.get(view.id) is None
    assert not Path(view.data_dir or "").exists()
    assert not manager.registry.is_alive(ready)


def test_two_launches_get_different_ports(manager: InstanceManager) -> None:
    first, second = launch(manager), launch(manager)
    assert first.port != second.port


def test_a_crash_before_ready_is_exited_with_its_code(manager: InstanceManager) -> None:
    view = launch(manager, "--exit=3")
    settled = manager.wait_ready(view.id)
    assert settled.state == InstanceState.EXITED
    assert settled.exit_code == 3
    assert "code 3" in (settled.last_error or "")
    # Still listed, so the crash is visible until someone dismisses it.
    assert [v.id for v in manager.list().instances] == [view.id]


def test_never_ready_is_still_alive_and_not_ready(manager: InstanceManager) -> None:
    view = launch(manager, "--never-ready")
    settled = manager.wait_ready(view.id, timeout=1.5)
    assert settled.state == InstanceState.STARTING
    health = manager.health(view.id)
    assert not health.ok and health.status_code == 503


def test_stop_escalates_past_an_ignored_sigterm(manager: InstanceManager) -> None:
    view = manager.wait_ready(launch(manager, "--ignore-term").id)
    assert view.state == InstanceState.READY
    with mock.patch.object(manager_module, "STOP_GRACE_SECONDS", 0.5):
        manager.stop(view.id)
    assert not manager.registry.is_alive(view)


def test_a_self_registered_live_instance_cannot_be_stopped(manager: InstanceManager) -> None:
    handle = register_self(manager.registry, project="demo", name="main",
                           kind=InstanceKind.SERVER, host="127.0.0.1", port=8000)
    assert handle is not None
    with pytest.raises(NotManagedError):
        manager.stop(handle.record.id)
    handle.release()



# -- stopping a group whose leader already exited ---------------------------
#
# macOS answers ``killpg`` with EPERM, not ESRCH, while a group holds nothing
# but an exited leader nobody has reaped. The leader can exit between stop's
# ``poll()`` and its next signal, so stop has to recognise that answer as
# "gone" — for its own child only.


class _Leader:
    """A real group leader, and a pipe that reports its exit without reaping it."""

    def __init__(self) -> None:
        self._exit_read, write_end = os.pipe()
        self.child = subprocess.Popen(  # noqa: S603 - fixed argv
            ["sleep", "30"], start_new_session=True, pass_fds=(write_end,)
        )
        os.close(write_end)

    def wait_exited_unreaped(self) -> None:
        # The kernel closes the child's end as it exits; nothing waits on the
        # child, so it stays a zombie.
        ready, _, _ = select.select([self._exit_read], [], [], 10)
        assert ready, "the leader did not exit"
        assert os.read(self._exit_read, 1) == b""

    def close(self) -> None:
        if self.child.poll() is None:
            self.child.kill()
            self.child.wait()
        os.close(self._exit_read)


@pytest.fixture
def leader(manager: InstanceManager) -> Iterator[_Leader]:
    built = _Leader()
    manager._children["zombie-leader"] = built.child
    yield built
    manager._children.pop("zombie-leader", None)
    built.close()


def _group_is_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    return False


def test_stop_treats_a_group_holding_only_its_exited_leader_as_gone(
    manager: InstanceManager, leader: _Leader
) -> None:
    leader.child.send_signal(signal.SIGTERM)
    leader.wait_exited_unreaped()
    manager._terminate(leader.child.pid, "zombie-leader")
    assert leader.child.returncode is not None
    assert _group_is_gone(leader.child.pid)


def test_stop_treats_a_leader_exiting_after_its_poll_as_gone(
    manager: InstanceManager, leader: _Leader
) -> None:
    real_poll = leader.child.poll
    calls = 0

    def exits_just_after_poll() -> int | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            # The race: SIGTERM has landed, the leader exits, and this poll
            # returned before the exit was there to reap.
            leader.wait_exited_unreaped()
            return None
        return real_poll()

    with mock.patch.object(leader.child, "poll", side_effect=exits_just_after_poll):
        manager._terminate(leader.child.pid, "zombie-leader")
    assert leader.child.returncode is not None
    assert _group_is_gone(leader.child.pid)



def test_stop_treats_a_leader_exiting_before_its_sigkill_as_gone(
    manager: InstanceManager, leader: _Leader
) -> None:
    real_killpg = os.killpg

    def exits_before_sigkill(pgid: int, sig: int) -> None:
        if sig == signal.SIGKILL:
            # It honoured SIGTERM just as the grace period ran out.
            leader.wait_exited_unreaped()
        real_killpg(pgid, sig)

    with (
        mock.patch.object(manager_module, "STOP_GRACE_SECONDS", 0),
        mock.patch.object(manager_module.os, "killpg", side_effect=exits_before_sigkill),
    ):
        manager._terminate(leader.child.pid, "zombie-leader")
    assert leader.child.returncode is not None
    assert _group_is_gone(leader.child.pid)

@pytest.mark.parametrize("instance_id", ["zombie-leader", "no-child-here"])
def test_stop_raises_eperm_it_cannot_explain_by_its_own_exited_child(
    manager: InstanceManager, leader: _Leader, instance_id: str
) -> None:
    # Our child still running, or no child of ours at all: EPERM is a real
    # permission failure on the group and must not read as "stopped".
    denied = PermissionError(1, "Operation not permitted")
    with mock.patch.object(manager_module.os, "killpg", side_effect=denied):
        with pytest.raises(PermissionError):
            manager._terminate(leader.child.pid, instance_id)
    assert leader.child.poll() is None


# -- HTTP ------------------------------------------------------------------


@pytest.fixture
def client(manager: InstanceManager) -> TestClient:
    app = FastAPI()
    app.include_router(make_instances_router(lambda: manager), prefix="/api/instances")
    return TestClient(app)


def test_http_round_trip(client: TestClient, manager: InstanceManager) -> None:
    templates = client.get("/api/instances/templates").json()
    assert [t["name"] for t in templates] == ["api"]
    created = client.post("/api/instances", json={"template": "api"})
    assert created.status_code == 201, created.text
    instance_id = created.json()["id"]
    manager.wait_ready(instance_id)
    listing = client.get("/api/instances", params={"project": "demo"}).json()
    assert [(i["id"], i["state"]) for i in listing["instances"]] == [(instance_id, "ready")]
    assert client.get(f"/api/instances/{instance_id}/health").json()["ok"] is True
    assert client.delete(f"/api/instances/{instance_id}").status_code == 204
    assert client.get(f"/api/instances/{instance_id}").status_code == 404


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"template": "nope"}, 404),
        ({"template": "api", "params": {"mode": "--rm-rf"}}, 422),
        ({"template": "api", "params": {"cmd": "sh"}}, 422),
    ],
)
def test_http_launch_refuses_what_no_template_allows(client: TestClient, body, code) -> None:
    assert client.post("/api/instances", json=body).status_code == code


def test_http_stop_of_a_self_registered_instance_is_a_conflict(
    client: TestClient, manager: InstanceManager
) -> None:
    handle = register_self(manager.registry, project="demo", name="main",
                           kind=InstanceKind.SERVER, host="127.0.0.1", port=8000)
    assert handle is not None
    assert client.delete(f"/api/instances/{handle.record.id}").status_code == 409
    handle.release()


# -- CLI -------------------------------------------------------------------


def test_cli_url_prints_the_main_url_and_fails_when_absent(
    registry: FileInstanceRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    with mock.patch.dict(os.environ, {"LORE_EDEN_INSTANCES_DIR": str(registry.root)}):
        assert cli.main(["--project", "demo", "url", "main"]) == 1
        assert capsys.readouterr().out == ""
        handle = register_self(registry, project="demo", name="main", kind=InstanceKind.SERVER,
                               host="127.0.0.1", port=8000)
        assert handle is not None
        assert cli.main(["--project", "demo", "url", "main"]) == 0
        assert capsys.readouterr().out.strip() == "http://127.0.0.1:8000"
        handle.release()
