"""Templates declared as data: a file in the project, or a row a host stores.

A project says how its servers and clients start without writing Python::

    # .loregarden/instances.yaml — or any path a host chooses
    version: 1
    templates:
      - name: api
        kind: server
        cwd: server                 # relative to the chosen worktree
        command: ["uvicorn", "shop.main:app", "--port", "{port}"]
        health_path: /health
      - name: web
        kind: client
        cwd: web
        command: ["npm", "run", "dev", "--", "--port", "{port}", "--strictPort"]
        health_path: /
        target: {env: API_URL}      # which server it talks to, picked at launch

Every template launches from a worktree of the project, picked at launch from
``git worktree list`` — the same closed choice the built-in templates offer.
One with a ``target`` also offers the project's live server instances (and its
main), and hands the chosen one's URL to the process in that variable. That is
the "run my UI change against main, or against my server change" story,
available to any project that writes the file.

A spec never makes a command reachable from a launch request: the request
still names a template and fills its parameters. What a spec changes is who
may define templates — whoever can edit the file or the stored row — which is
the host's decision to guard.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from lore_eden.instances.models import InstanceKind, InstanceRecord
from lore_eden.instances.ports import DEFAULT_PORT_RANGE
from lore_eden.instances.registry import FileInstanceRegistry, slugify
from lore_eden.instances.templates import (
    LaunchContext,
    LaunchRequest,
    LaunchSpec,
    TemplateInfo,
    TemplateParam,
    TemplateParamError,
    resolve_params,
)
from lore_eden.instances.worktrees import Worktree

#: Parameter keys every spec template offers; a spec may not declare its own.
WORKTREE_PARAM = "worktree"
TARGET_PARAM = "target"
MAIN_TARGET = "main"
_RESERVED = frozenset({WORKTREE_PARAM, TARGET_PARAM})


class TemplateFileError(ValueError):
    """A template file or stored spec that cannot be used, with the reason."""


class TargetSpec(BaseModel):
    """A template whose process talks to a server instance."""

    model_config = ConfigDict(extra="forbid")

    #: The variable the chosen server's URL is passed in.
    env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    #: Offer the project's main server as a choice (the default).
    allow_main: bool = True


class TemplateSpec(BaseModel):
    """One template, as a file or a stored row declares it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    kind: InstanceKind
    description: str = ""
    command: list[str] = Field(min_length=1)
    #: Relative to the chosen worktree. Must stay inside it.
    cwd: str = "."
    env: dict[str, str] = Field(default_factory=dict)
    #: None means "accepting connections is ready enough".
    health_path: str | None = "/"
    ready_timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    port_range: tuple[int, int] = DEFAULT_PORT_RANGE
    params: list[TemplateParam] = Field(default_factory=list)
    target: TargetSpec | None = None

    @field_validator("name")
    @classmethod
    def _name_is_a_slug(cls, value: str) -> str:
        if slugify(value) != value:
            raise ValueError(f"name must be lowercase letters, digits and dashes (got {value!r})")
        return value

    @field_validator("cwd")
    @classmethod
    def _cwd_stays_relative(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"cwd must be relative to the worktree and stay inside it (got {value!r})")
        return value

    @field_validator("health_path")
    @classmethod
    def _health_path_is_a_path(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("/"):
            raise ValueError(f"health_path must start with / (got {value!r})")
        return value

    @field_validator("port_range")
    @classmethod
    def _port_range_is_ordered(cls, value: tuple[int, int]) -> tuple[int, int]:
        low, high = value
        if not 1024 <= low <= high <= 65535:
            raise ValueError(f"port_range must be ascending within 1024-65535 (got {low}-{high})")
        return value

    @model_validator(mode="after")
    def _params_are_distinct_and_unreserved(self) -> TemplateSpec:
        keys = [param.key for param in self.params]
        clashes = sorted(set(keys) & _RESERVED)
        if clashes:
            raise ValueError(f"params may not be named {', '.join(clashes)}; every template provides them")
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate param keys: {', '.join(duplicates)}")
        return self


class TemplateFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    templates: list[TemplateSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _names_are_unique(self) -> TemplateFile:
        names = [spec.name for spec in self.templates]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate template names: {', '.join(duplicates)}")
        return self


def _describe_validation(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"]) or "file"
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts)


def parse_template_file(text: str, *, source: str) -> list[TemplateSpec]:
    """The specs a template file declares, or :class:`TemplateFileError` saying why not.

    ``source`` names the file in the message, so a person can find what to fix.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TemplateFileError(f"{source} is not valid YAML: {exc}") from exc
    if raw is None:
        return []
    try:
        return TemplateFile.model_validate(raw).templates
    except ValidationError as exc:
        raise TemplateFileError(f"{source}: {_describe_validation(exc)}") from exc


def load_template_file(path: Path) -> list[TemplateSpec]:
    """Parse ``path``. A missing file is no templates; an unreadable one is an error."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise TemplateFileError(f"cannot read {path}: {exc}") from exc
    return parse_template_file(text, source=str(path))


def validate_spec(raw: object) -> TemplateSpec:
    """One stored spec, validated the way a file entry is."""
    try:
        return TemplateSpec.model_validate(raw)
    except ValidationError as exc:
        raise TemplateFileError(_describe_validation(exc)) from exc


def _fill(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace(f"{{param:{key}}}", value)
    return text


class SpecTemplate:
    """An :class:`~lore_eden.instances.templates.InstanceTemplate` built from a spec.

    ``name`` is what launch requests use and may differ from ``spec.name`` —
    a host serving several projects qualifies it (``shop/api``) so two
    projects' ``api`` templates do not collide.
    """

    def __init__(
        self,
        spec: TemplateSpec,
        *,
        name: str,
        project: str,
        worktrees: Callable[[], list[Worktree]],
        registry: FileInstanceRegistry,
    ) -> None:
        self.spec = spec
        self.name = name
        self.project = project
        self._worktrees = worktrees
        self._registry = registry

    def _targets(self, registry: FileInstanceRegistry) -> list[InstanceRecord]:
        return [
            record
            for record in registry.scan(self.project).records
            if record.kind == InstanceKind.SERVER and registry.is_alive(record)
        ]

    def _info(self, registry: FileInstanceRegistry) -> tuple[TemplateInfo, dict[str, Worktree]]:
        trees = {str(tree.path): tree for tree in self._worktrees()}
        params = [
            TemplateParam(
                key=WORKTREE_PARAM,
                label="Worktree",
                description="The checkout to run.",
                required=True,
                default=next(iter(trees), None),
                choices=list(trees),
            ),
            *self.spec.params,
        ]
        if self.spec.target is not None:
            choices = [record.id for record in self._targets(registry) if record.managed]
            if self.spec.target.allow_main:
                choices.insert(0, MAIN_TARGET)
            params.append(
                TemplateParam(
                    key=TARGET_PARAM,
                    label="Server",
                    description="The server instance this one talks to.",
                    required=True,
                    default=choices[0] if choices else None,
                    choices=choices,
                )
            )
        info = TemplateInfo(
            name=self.name, kind=self.spec.kind, description=self.spec.description, params=params
        )
        return info, trees

    def describe(self) -> TemplateInfo:
        return self._info(self._registry)[0]

    def _resolve_target(self, registry: FileInstanceRegistry, ref: str) -> InstanceRecord:
        record = registry.find_main(self.project) if ref == MAIN_TARGET else registry.get(ref)
        if record is None or not registry.is_alive(record):
            if ref == MAIN_TARGET:
                raise TemplateParamError(f"no main server of {self.project} is registered and running")
            raise TemplateParamError(f"server {ref} is no longer running")
        return record

    def build(self, request: LaunchRequest, ctx: LaunchContext) -> LaunchSpec:
        info, trees = self._info(ctx.registry)
        values = resolve_params(info, request.params)
        tree = trees[values[WORKTREE_PARAM]]
        root = tree.path.resolve()
        cwd = (root / self.spec.cwd).resolve()
        if not cwd.is_relative_to(root):
            # The spec's own check rejects `..`; this catches a symlink that
            # points out of the worktree.
            raise TemplateParamError(f"{self.spec.cwd!r} resolves outside {tree.path}")
        if not cwd.is_dir():
            raise TemplateParamError(f"{self.spec.cwd!r} does not exist in {tree.path}")
        own = {key: value for key, value in values.items() if key not in _RESERVED}
        env = {key: _fill(value, own) for key, value in self.spec.env.items()}
        labels = {"worktree": str(tree.path), "branch": tree.branch, **own}
        target_id = None
        if self.spec.target is not None:
            target = self._resolve_target(ctx.registry, values[TARGET_PARAM])
            env[self.spec.target.env] = target.url
            target_id = target.id
            labels["api"] = target.url
        return LaunchSpec(
            project=self.project,
            name=request.name or f"{self.spec.name}-{slugify(tree.branch or tree.path.name)}",
            kind=self.spec.kind,
            command=[_fill(part, own) for part in self.spec.command],
            cwd=cwd,
            env=env,
            health_path=self.spec.health_path,
            ready_timeout_seconds=self.spec.ready_timeout_seconds,
            port_range=self.spec.port_range,
            target_instance_id=target_id,
            labels=labels,
        )
