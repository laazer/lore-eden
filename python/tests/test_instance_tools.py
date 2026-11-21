"""The instance MCP tools, through a real ToolRegistry, against real processes."""

from __future__ import annotations

import errno
import json
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from lore_eden.instances import (
    INSTANCE_TOOL_NAMES,
    CommandTemplate,
    FileInstanceRegistry,
    InstanceKind,
    InstanceManager,
    TemplateCatalog,
    TemplateParam,
    register_instance_tools,
    register_self,
)
from lore_eden.instances import tools as tools_module
from lore_eden.mcp.tools import DuplicateToolError, ToolRegistry


@pytest.fixture
def tools(instance_manager: InstanceManager) -> ToolRegistry:
    registry = ToolRegistry()
    register_instance_tools(registry, lambda: instance_manager, prefix="demo_")
    return registry


def call(tools: ToolRegistry, name: str, **arguments: Any) -> dict[str, Any]:
    return json.loads(tools.call(f"demo_{name}", arguments, context=None))


def test_registers_every_tool_under_the_prefix(tools: ToolRegistry) -> None:
    assert tools.names() == [f"demo_{name}" for name in INSTANCE_TOOL_NAMES]
    for definition in tools.definitions():
        assert definition["inputSchema"]["additionalProperties"] is False


def test_registering_twice_is_refused(tools: ToolRegistry, instance_manager: InstanceManager) -> None:
    with pytest.raises(DuplicateToolError):
        register_instance_tools(tools, lambda: instance_manager, prefix="demo_")


def test_list_offers_templates_with_their_parameters(tools: ToolRegistry) -> None:
    listing = call(tools, "list_instances")
    assert listing["ok"] and listing["instances"] == []
    [template] = listing["templates"]
    assert template["name"] == "api"
    assert template["params"][0]["choices"] == ["--ok", "--exit=3", "--never-ready", "--ignore-term"]


def test_launch_wait_status_stop(tools: ToolRegistry, instance_registry: FileInstanceRegistry) -> None:
    launched = call(tools, "launch_instance", template="api", wait_seconds=15)
    assert launched["ok"], launched
    instance = launched["instance"]
    assert instance["state"] == "ready"
    assert instance["url"].startswith("http://127.0.0.1:")
    assert "stop_instance" in launched["next_step"]

    status = call(tools, "instance_status", instance_id=instance["id"])
    assert status["health"]["ok"] is True
    assert any("fake instance starting" in line for line in status["log_tail"])

    assert call(tools, "stop_instance", instance_id=instance["id"]) == {"ok": True, "stopped": instance["id"]}
    assert instance_registry.get(instance["id"]) is None


def test_launch_without_waiting_says_to_poll(tools: ToolRegistry) -> None:
    launched = call(tools, "launch_instance", template="api")
    assert launched["instance"]["state"] == "starting"
    assert "Poll instance_status" in launched["next_step"]


def test_wait_is_clamped_both_ways(tools: ToolRegistry) -> None:
    # A never-ready server asked to wait forever must come back, not hold the turn.
    with mock.patch.object(tools_module, "MAX_WAIT_SECONDS", 1):
        started = time.monotonic()
        launched = call(tools, "launch_instance", template="api", params={"mode": "--never-ready"},
                        wait_seconds=10_000)
        assert time.monotonic() - started < 5
    assert launched["instance"]["state"] == "starting"
    negative = call(tools, "launch_instance", template="api", wait_seconds=-5)
    assert negative["instance"]["state"] == "starting"


def test_a_crash_is_reported_with_its_log(tools: ToolRegistry, instance_manager: InstanceManager) -> None:
    launched = call(tools, "launch_instance", template="api", params={"mode": "--exit=3"})
    instance_manager.wait_ready(launched["instance"]["id"])
    status = call(tools, "instance_status", instance_id=launched["instance"]["id"])
    assert status["instance"]["state"] == "exited"
    assert status["exit_code"] == 3
    assert status["health"] is None
    assert status["log_tail"]


@pytest.mark.parametrize(
    ("name", "arguments", "kind"),
    [
        ("launch_instance", {"template": "nope"}, "unknown_template"),
        ("launch_instance", {"template": "api", "params": {"mode": "--rm-rf"}}, "invalid_params"),
        ("launch_instance", {"template": "api", "params": {"shell": "sh"}}, "invalid_params"),
        ("instance_status", {"instance_id": "missing"}, "not_found"),
        ("stop_instance", {"instance_id": "missing"}, "not_found"),
    ],
)
def test_failures_are_classified_not_raised(tools: ToolRegistry, name: str, arguments: dict, kind: str) -> None:
    result = call(tools, name, **arguments)
    assert result["ok"] is False
    assert result["error_kind"] == kind
    assert result["retryable"] is False
    assert result["error"]


def test_the_self_registered_main_cannot_be_stopped(
    tools: ToolRegistry, instance_registry: FileInstanceRegistry
) -> None:
    main = register_self(instance_registry, project="demo", name="main", kind=InstanceKind.SERVER,
                         host="127.0.0.1", port=8000)
    assert main is not None
    result = call(tools, "stop_instance", instance_id=main.record.id)
    assert result["error_kind"] == "not_managed"
    main.release()


_OUT_OF_DESCRIPTORS = OSError(errno.EMFILE, "Too many open files")


@pytest.mark.parametrize(
    "fail",
    [
        # Before the process starts, under the registry lock.
        lambda manager: mock.patch.object(manager.registry, "lock", side_effect=_OUT_OF_DESCRIPTORS),
        # Before anything exists at all.
        lambda manager: mock.patch.object(Path, "mkdir", side_effect=_OUT_OF_DESCRIPTORS),
    ],
    ids=["registry-lock", "instance-dir"],
)
def test_an_os_error_during_launch_is_a_classified_failure(
    tools: ToolRegistry, instance_manager: InstanceManager, fail
) -> None:
    """An OSError outside the one Popen call escaped EXPECTED_FAILURES, so an
    MCP client got bare "[Errno 24] ..." text with no error_kind. It is a
    launch failure like any other, and leaves nothing behind."""
    with fail(instance_manager):
        result = call(tools, "launch_instance", template="api")
    assert result["ok"] is False
    assert result["error_kind"] == "launch_failed"
    assert "Too many open files" in result["error"]
    assert instance_manager.registry.scan().records == []
    assert list(instance_manager.registry.data_root.iterdir()) == []


# -- listing size ------------------------------------------------------------

_MANY = [f"/repo/.claude/worktrees/tree-{n:03}" for n in range(40)]


@pytest.fixture
def wide_tools(instance_registry: FileInstanceRegistry, tmp_path: Path) -> ToolRegistry:
    """Two templates offering 40 worktrees each, like a busy repository."""
    catalog = TemplateCatalog()
    for name in ("client", "server"):
        catalog.register(
            CommandTemplate(
                project="demo", name=name, kind=InstanceKind.SERVER, command=["true"], cwd=str(tmp_path),
                params=[
                    TemplateParam(key="worktree", label="Worktree", required=True, default=_MANY[7],
                                  choices=_MANY),
                    TemplateParam(key="db", label="Database", default="snapshot", choices=["snapshot", "fresh"]),
                ],
            )
        )
    registry = ToolRegistry()
    register_instance_tools(registry, lambda: InstanceManager(instance_registry, catalog), prefix="demo_")
    return registry


def test_a_long_choice_list_is_summarised_in_the_listing(wide_tools: ToolRegistry) -> None:
    listing = call(wide_tools, "list_instances")
    assert [t["name"] for t in listing["templates"]] == ["client", "server"]
    for template in listing["templates"]:
        worktree, db = template["params"]
        assert "choices" not in worktree
        assert worktree["choice_count"] == 40
        assert worktree["choice_examples"] == [_MANY[7], _MANY[0], _MANY[1]]
        assert f'template="{template["name"]}"' in worktree["all_choices"]
        # A short list is still given whole.
        assert db["choices"] == ["snapshot", "fresh"]
    raw = wide_tools.call("demo_list_instances", {}, context=None)
    assert all(path not in raw for path in _MANY[8:])


def test_naming_a_template_returns_every_choice(wide_tools: ToolRegistry) -> None:
    listing = call(wide_tools, "list_instances", template="server")
    [template] = listing["templates"]
    assert template["name"] == "server"
    assert template["params"][0]["choices"] == _MANY
    assert "choice_count" not in template["params"][0]


def test_naming_an_unknown_template_is_classified(wide_tools: ToolRegistry) -> None:
    result = call(wide_tools, "list_instances", template="nope")
    assert (result["ok"], result["error_kind"]) == (False, "unknown_template")


# -- restart -------------------------------------------------------------------


def test_restart_keeps_the_url_and_comes_back_ready(tools: ToolRegistry) -> None:
    launched = call(tools, "launch_instance", template="api", wait_seconds=15)["instance"]
    restarted = call(tools, "restart_instance", instance_id=launched["id"], wait_seconds=15)
    assert restarted["ok"], restarted
    assert (restarted["instance"]["id"], restarted["instance"]["url"]) == (launched["id"], launched["url"])
    assert restarted["instance"]["state"] == "ready"


def test_restart_failures_are_classified(tools: ToolRegistry, instance_registry: FileInstanceRegistry) -> None:
    assert call(tools, "restart_instance", instance_id="missing")["error_kind"] == "not_found"
    main = register_self(instance_registry, project="demo", name="main", kind=InstanceKind.SERVER,
                         host="127.0.0.1", port=8000)
    assert main is not None
    assert call(tools, "restart_instance", instance_id=main.record.id)["error_kind"] == "not_managed"
    main.release()
