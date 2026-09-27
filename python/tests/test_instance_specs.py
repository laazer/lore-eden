"""Templates declared as data, launched from a real worktree."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from lore_eden.instances import (
    FileInstanceRegistry,
    InstanceKind,
    InstanceManager,
    InstanceState,
    LaunchRequest,
    SpecTemplate,
    TemplateCatalog,
    TemplateFileError,
    TemplateParamError,
    TemplateSpec,
    list_worktrees,
    load_template_file,
    parse_template_file,
    register_self,
    validate_spec,
)

FAKE_SERVER = Path(__file__).resolve().parent / "fake_instance_server.py"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with an `app/` directory and one linked worktree, `feat-x`."""
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "README").write_text("app\n", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@e"}
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")
    git("worktree", "add", "-q", "-b", "feat-x", str(tmp_path / "feat-x"))
    return root


@pytest.fixture
def registry(tmp_path: Path) -> FileInstanceRegistry:
    return FileInstanceRegistry(tmp_path / "registry")


def _spec(**overrides) -> TemplateSpec:
    fields = {
        "name": "api",
        "kind": InstanceKind.SERVER,
        "command": [sys.executable, str(FAKE_SERVER), "{param:mode}"],
        "cwd": "app",
        "health_path": "/health",
        "params": [{"key": "mode", "label": "Mode", "default": "--ok", "choices": ["--ok", "--exit=3"]}],
    }
    return TemplateSpec.model_validate({**fields, **overrides})


@pytest.fixture
def manager(repo: Path, registry: FileInstanceRegistry) -> Iterator[InstanceManager]:
    catalog = TemplateCatalog()
    catalog.register(
        SpecTemplate(_spec(), name="demo/api", project="demo", worktrees=lambda: list_worktrees(repo),
                     registry=registry)
    )
    catalog.register(
        SpecTemplate(
            _spec(name="web", kind=InstanceKind.CLIENT, target={"env": "API_URL"}),
            name="demo/web", project="demo", worktrees=lambda: list_worktrees(repo), registry=registry,
        )
    )
    built = InstanceManager(registry, catalog)
    yield built
    for view in built.list().instances:
        if view.managed:
            built.stop(view.id)


# -- worktrees ---------------------------------------------------------------


def test_worktrees_list_the_primary_first_with_branches(repo: Path, tmp_path: Path) -> None:
    assert [(t.path.resolve(), t.branch) for t in list_worktrees(repo)] == [
        (repo.resolve(), "main"),
        ((tmp_path / "feat-x").resolve(), "feat-x"),
    ]


def test_a_directory_that_is_not_a_repo_still_offers_itself(tmp_path: Path) -> None:
    assert [t.path for t in list_worktrees(tmp_path)] == [tmp_path]


# -- parsing -----------------------------------------------------------------

GOOD = """
version: 1
templates:
  - name: api
    kind: server
    cwd: server
    command: ["uvicorn", "app:main", "--port", "{port}"]
"""


def test_a_file_parses_to_specs() -> None:
    [spec] = parse_template_file(GOOD, source="instances.yaml")
    assert (spec.name, spec.cwd, spec.health_path) == ("api", "server", "/")


def test_a_missing_file_is_no_templates(tmp_path: Path) -> None:
    assert load_template_file(tmp_path / "absent.yaml") == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("version: 1\ntemplates: [{", "not valid YAML"),
        ("version: 2\ntemplates: []", "version"),
        ("version: 1\ntemplates: [{name: API, kind: server, command: [x]}]", "lowercase"),
        ("version: 1\ntemplates: [{name: a, kind: server, command: [x], cwd: ../up}]", "stay inside"),
        ("version: 1\ntemplates: [{name: a, kind: server, command: [x], cwd: /etc}]", "stay inside"),
        ("version: 1\ntemplates: [{name: a, kind: server, command: []}]", "command"),
        ("version: 1\ntemplates: [{name: a, kind: server, command: [x], shell: true}]", "shell"),
        ("version: 1\ntemplates: [{name: a, kind: server, command: [x], port_range: [9000, 80]}]", "port_range"),
        (
            "version: 1\ntemplates: [{name: a, kind: server, command: [x], params: [{key: worktree, label: W}]}]",
            "may not be named worktree",
        ),
        (
            "version: 1\ntemplates: [{name: a, kind: server, command: [x]}, {name: a, kind: client, command: [y]}]",
            "duplicate template names",
        ),
    ],
)
def test_a_bad_file_says_what_is_wrong_and_where(text: str, message: str) -> None:
    with pytest.raises(TemplateFileError, match=message) as caught:
        parse_template_file(text, source="shop/instances.yaml")
    assert "instances.yaml" in str(caught.value)


def test_a_stored_spec_is_validated_the_same_way() -> None:
    with pytest.raises(TemplateFileError, match="stay inside"):
        validate_spec({"name": "a", "kind": "server", "command": ["x"], "cwd": "../.."})


# -- launching -----------------------------------------------------------------


def test_launches_in_the_chosen_worktree(manager: InstanceManager, tmp_path: Path) -> None:
    feat = str((tmp_path / "feat-x").resolve())
    info = next(t for t in manager.templates.describe_all() if t.name == "demo/api")
    assert [p.key for p in info.params] == ["worktree", "mode"]
    assert feat in (info.params[0].choices or [])

    view = manager.launch(LaunchRequest(template="demo/api", params={"worktree": feat}))
    assert view.cwd == str(Path(feat) / "app")
    assert view.labels["branch"] == "feat-x"
    assert view.name == "api-feat-x"
    assert manager.wait_ready(view.id).state == InstanceState.READY


def test_a_path_that_is_not_a_worktree_is_refused(manager: InstanceManager) -> None:
    with pytest.raises(TemplateParamError, match="must be one of"):
        manager.launch(LaunchRequest(template="demo/api", params={"worktree": "/etc"}))


def test_a_symlink_out_of_the_worktree_is_refused(
    repo: Path, registry: FileInstanceRegistry, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "escape").symlink_to(outside)
    template = SpecTemplate(_spec(cwd="escape"), name="x", project="demo",
                            worktrees=lambda: list_worktrees(repo)[:1], registry=registry)
    catalog = TemplateCatalog()
    catalog.register(template)
    with pytest.raises(TemplateParamError, match="outside"):
        InstanceManager(registry, catalog).launch(LaunchRequest(template="x"))


def test_a_client_gets_the_chosen_servers_url(manager: InstanceManager, registry: FileInstanceRegistry) -> None:
    main = register_self(registry, project="demo", name="main", kind=InstanceKind.SERVER,
                         host="127.0.0.1", port=8000)
    assert main is not None
    server = manager.launch(LaunchRequest(template="demo/api"))

    info = next(t for t in manager.templates.describe_all() if t.name == "demo/web")
    target = next(p for p in info.params if p.key == "target")
    assert target.choices == ["main", server.id]

    client = manager.launch(LaunchRequest(template="demo/web", params={"target": server.id}))
    assert client.target_instance_id == server.id
    assert client.labels["api"] == server.url
    main.release()


def test_a_client_pointed_at_a_missing_main_is_told_so(manager: InstanceManager) -> None:
    with pytest.raises(TemplateParamError, match="no main server of demo"):
        manager.launch(LaunchRequest(template="demo/web", params={"target": "main"}))
