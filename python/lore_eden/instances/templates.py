"""Templates: the only way an instance can be launched.

A launch request names a template and fills in its parameters; it never
carries a command. The endpoint that launches instances runs processes, and an
endpoint that ran whatever command it was sent would be a remote shell. The
host decides what can run, in code, by registering templates.

A template builds a :class:`LaunchSpec` from the request. Commands, working
directory and environment may use placeholders the launcher fills once the
port is known: ``{host}``, ``{port}``, ``{url}``, ``{instance_id}`` and
``{instance_dir}``. They are substituted as literal tokens, not through
``str.format``, so a command that contains other braces — a JSON argument —
passes through untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from lore_eden.instances.models import InstanceKind, InstanceRole
from lore_eden.instances.ports import DEFAULT_PORT_RANGE
from lore_eden.instances.registry import FileInstanceRegistry


class UnknownTemplateError(KeyError):
    pass


class TemplateParamError(ValueError):
    """A request's parameters do not satisfy the template. Shown to the user."""


class TemplateParam(BaseModel):
    key: str
    label: str
    description: str = ""
    required: bool = False
    default: str | None = None
    #: When set, the only accepted values — a UI renders a picker.
    choices: list[str] | None = None


class TemplateInfo(BaseModel):
    name: str
    kind: InstanceKind
    description: str = ""
    params: list[TemplateParam] = Field(default_factory=list)


class LaunchRequest(BaseModel):
    template: str
    #: Label for the instance; the template picks one when omitted.
    name: str | None = None
    params: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class LaunchContext:
    """What a template may use while building a spec."""

    instance_id: str
    #: Private to this instance, created before ``build`` runs.
    instance_dir: Path
    registry: FileInstanceRegistry


@dataclass
class LaunchSpec:
    project: str
    name: str
    kind: InstanceKind
    command: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    role: InstanceRole = InstanceRole.BRANCH
    host: str = "127.0.0.1"
    #: None allocates one from ``port_range``.
    port: int | None = None
    port_range: tuple[int, int] = DEFAULT_PORT_RANGE
    health_path: str | None = "/health"
    ready_timeout_seconds: float = 120.0
    target_instance_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


class InstanceTemplate(Protocol):
    def describe(self) -> TemplateInfo:
        """Called per request, so choices can reflect the current machine."""

    def build(self, request: LaunchRequest, ctx: LaunchContext) -> LaunchSpec: ...


def resolve_params(info: TemplateInfo, supplied: dict[str, str]) -> dict[str, str]:
    """Defaults applied, required checked, choices enforced, unknowns refused.

    An unknown key is refused rather than ignored: a misspelt parameter that is
    dropped launches an instance configured differently from what was asked.
    """
    known = {p.key: p for p in info.params}
    unknown = sorted(set(supplied) - set(known))
    if unknown:
        raise TemplateParamError(f"unknown parameter(s) for {info.name}: {', '.join(unknown)}")
    resolved: dict[str, str] = {}
    for param in info.params:
        value = supplied.get(param.key) or param.default
        if value is None:
            if param.required:
                raise TemplateParamError(f"{param.label} is required")
            continue
        if param.choices is not None and value not in param.choices:
            raise TemplateParamError(
                f"{param.label} must be one of: {', '.join(param.choices)} (got {value!r})"
            )
        resolved[param.key] = value
    return resolved


@dataclass
class CommandTemplate:
    """A template that is just a command, for hosts with nothing to prepare.

    Parameter values are available to ``command``, ``cwd`` and ``env`` as
    ``{param:<key>}``, alongside the launcher's placeholders::

        CommandTemplate(
            project="shop", name="api", kind=InstanceKind.SERVER,
            command=["uvicorn", "shop.main:app", "--port", "{port}"],
            cwd="{param:checkout}/server",
            params=[TemplateParam(key="checkout", label="Checkout", required=True)],
        )
    """

    project: str
    name: str
    kind: InstanceKind
    command: list[str]
    cwd: str
    description: str = ""
    env: dict[str, str] = field(default_factory=dict)
    params: list[TemplateParam] = field(default_factory=list)
    health_path: str | None = "/health"
    ready_timeout_seconds: float = 120.0
    port_range: tuple[int, int] = DEFAULT_PORT_RANGE

    def describe(self) -> TemplateInfo:
        return TemplateInfo(
            name=self.name, kind=self.kind, description=self.description, params=self.params
        )

    def build(self, request: LaunchRequest, ctx: LaunchContext) -> LaunchSpec:
        values = resolve_params(self.describe(), request.params)

        def fill(text: str) -> str:
            for key, value in values.items():
                text = text.replace(f"{{param:{key}}}", value)
            return text

        return LaunchSpec(
            project=self.project,
            name=request.name or self.name,
            kind=self.kind,
            command=[fill(part) for part in self.command],
            cwd=Path(fill(self.cwd)),
            env={key: fill(value) for key, value in self.env.items()},
            health_path=self.health_path,
            ready_timeout_seconds=self.ready_timeout_seconds,
            port_range=self.port_range,
            labels=values,
        )


class TemplateSource(Protocol):
    """Where a manager finds templates. :class:`TemplateCatalog` is the fixed one.

    A host whose templates change at runtime — read from files, stored in a
    database — implements this instead, and answers from the current state
    on every call.
    """

    def get(self, name: str) -> InstanceTemplate:
        """The template, or :class:`UnknownTemplateError`."""

    def describe_all(self) -> list[TemplateInfo]: ...


class TemplateCatalog:
    """The templates a host allows. Registering a name twice is an error."""

    def __init__(self) -> None:
        self._templates: dict[str, InstanceTemplate] = {}

    def register(self, template: InstanceTemplate) -> InstanceTemplate:
        name = template.describe().name
        if name in self._templates:
            raise ValueError(f"template {name!r} is already registered")
        self._templates[name] = template
        return template

    def get(self, name: str) -> InstanceTemplate:
        try:
            return self._templates[name]
        except KeyError:
            raise UnknownTemplateError(name) from None

    def describe_all(self) -> list[TemplateInfo]:
        return [template.describe() for template in self._templates.values()]

