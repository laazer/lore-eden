"""Restarting an instance in place, against real processes on real ports.

A restart is how a launched server picks up a code change: it runs no reloader.
What makes it worth more than stop-and-launch is everything it keeps — the id,
the port, the URL a client was pointed at — and the environment it runs with,
which for a sandbox is what keeps it off the main database.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lore_eden.instances import (
    CommandTemplate,
    FileInstanceRegistry,
    InstanceKind,
    InstanceLaunchError,
    InstanceManager,
    InstanceNotFoundError,
    InstanceState,
    LaunchRequest,
    NotManagedError,
    TemplateCatalog,
    make_instances_router,
    register_self,
)

FAKE_INSTANCE_SERVER = Path(__file__).with_name("fake_instance_server.py")


@pytest.fixture
def manager(instance_registry: FileInstanceRegistry, tmp_path: Path) -> Iterator[InstanceManager]:
    """One template, ``env-api``: prints its template env var, then serves."""
    catalog = TemplateCatalog()
    catalog.register(
        CommandTemplate(
            project="demo",
            name="env-api",
            kind=InstanceKind.SERVER,
            command=[
                "sh", "-c", 'echo "marker=$LE_MARKER"; exec "$0" "$1" "$2"',
                sys.executable, str(FAKE_INSTANCE_SERVER), "--ok",
            ],
            cwd=str(tmp_path),
            env={"LE_MARKER": "from-template-{instance_id}"},
            params=[],
            ready_timeout_seconds=15,
        )
    )
    built = InstanceManager(instance_registry, catalog)
    yield built
    for view in built.list().instances:
        if view.managed:
            built.stop(view.id)


def _launch_ready(manager: InstanceManager):
    view = manager.launch(LaunchRequest(template="env-api"))
    ready = manager.wait_ready(view.id)
    assert ready.state == InstanceState.READY, manager.logs(view.id).lines
    return ready


def test_restart_keeps_identity_and_runs_a_new_process(manager: InstanceManager) -> None:
    before = _launch_ready(manager)
    restarted = manager.restart(before.id)
    assert (restarted.id, restarted.port, restarted.url, restarted.data_dir) == (
        before.id, before.port, before.url, before.data_dir,
    )
    assert restarted.pid != before.pid
    assert restarted.state == InstanceState.STARTING
    assert restarted.ready_at is None
    assert not manager.registry.is_alive(before)

    ready = manager.wait_ready(before.id)
    assert ready.state == InstanceState.READY
    assert manager.health(before.id).ok
    lines = manager.logs(before.id).lines
    # One log, both runs, with the restart marked between them.
    assert sum("fake instance starting" in line for line in lines) == 2
    assert any(line.startswith("--- restarted ") for line in lines)


def test_restart_runs_with_the_template_environment(manager: InstanceManager) -> None:
    before = _launch_ready(manager)
    manager.wait_ready(manager.restart(before.id).id)
    markers = [line for line in manager.logs(before.id).lines if line.startswith("marker=")]
    assert markers == [f"marker=from-template-{before.id}"] * 2


def test_restart_retries_an_exited_instance(manager: InstanceManager) -> None:
    before = _launch_ready(manager)
    manager._terminate(before.pgid or before.pid, before.id)
    assert manager.get(before.id).state == InstanceState.EXITED
    ready = manager.wait_ready(manager.restart(before.id).id)
    assert ready.state == InstanceState.READY
    assert ready.exit_code is None and ready.last_error is None


def test_restart_refuses_a_record_without_its_launch_environment(manager: InstanceManager) -> None:
    """A record written before restart existed has no template env. Running its
    command anyway would start it on the launcher's inherited environment —
    for a sandboxed server, the main database — so it is refused, and the
    running process is left alone."""
    before = _launch_ready(manager)
    record = manager.registry.get(before.id)
    assert record is not None
    manager.registry.put(record.model_copy(update={"launch_env": None}))
    with pytest.raises(InstanceLaunchError, match="stop it and launch it again"):
        manager.restart(before.id)
    assert manager.registry.is_alive(before)


def test_restart_refuses_a_self_registered_instance(manager: InstanceManager) -> None:
    handle = register_self(manager.registry, project="demo", name="main",
                           kind=InstanceKind.SERVER, host="127.0.0.1", port=8000)
    assert handle is not None
    with pytest.raises(NotManagedError):
        manager.restart(handle.record.id)
    handle.release()


def test_restart_of_a_missing_instance_is_not_found(manager: InstanceManager) -> None:
    with pytest.raises(InstanceNotFoundError):
        manager.restart("missing")


def test_http_restart_and_the_environment_stays_off_responses(manager: InstanceManager) -> None:
    app = FastAPI()
    app.include_router(make_instances_router(lambda: manager), prefix="/api/instances")
    client = TestClient(app)
    before = _launch_ready(manager)

    response = client.post(f"/api/instances/{before.id}/restart")
    assert response.status_code == 200, response.text
    assert (response.json()["id"], response.json()["port"]) == (before.id, before.port)
    assert "launch_env" not in response.json()
    listing = client.get("/api/instances").json()
    assert all("launch_env" not in item for item in listing["instances"])
    assert "launch_env" not in client.get(f"/api/instances/{before.id}").json()
    # ...while the registry file keeps it, which is what the restart used.
    record = manager.registry.get(before.id)
    assert record is not None and record.launch_env == {"LE_MARKER": f"from-template-{before.id}"}

    assert client.post("/api/instances/missing/restart").status_code == 404
