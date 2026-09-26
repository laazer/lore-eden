"""What a locally running instance is, as the registry records it.

An *instance* is one process serving one port: a backend a feature branch
needs, or a dev client pointed at some backend. The record is what any other
process — another project's tooling, a UI, a shell script — needs to find it
and connect to it, and nothing more.

State is derived when a record is read, never trusted from the file. A record
says a process was started; only the process table says it is still there, and
a registry that believed its own files would report a crashed server as
running for as long as nobody looked.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class InstanceKind(str, Enum):
    """What the process serves. A client usually targets a server instance."""

    SERVER = "server"
    CLIENT = "client"


class InstanceRole(str, Enum):
    """Why the instance exists.

    ``MAIN`` is the long-lived shared instance a project normally runs, which
    registers itself; ``BRANCH`` is one spun up to exercise a change and torn
    down after. A UI-only change runs a branch client against the main server;
    a server change runs a branch server of its own.
    """

    MAIN = "main"
    BRANCH = "branch"


class InstanceState(str, Enum):
    """Derived at read time from the process table and the readiness probe."""

    #: Alive, and has not yet answered its readiness probe.
    STARTING = "starting"
    #: Alive and answered its readiness probe at least once.
    READY = "ready"
    #: Alive, but never became ready inside its budget. Still running, so it
    #: still holds its port — it is not the same answer as exited.
    STALLED = "stalled"
    #: The process is gone. Kept until someone dismisses it, so a crash is
    #: visible with its log rather than silently vanishing from the list.
    EXITED = "exited"


class InstanceRecord(BaseModel):
    """One instance, as written to the registry."""

    id: str
    #: Namespaces instances in a registry several projects share.
    project: str
    #: Human label, unique only within intent: "main", "feat-login".
    name: str
    kind: InstanceKind
    role: InstanceRole
    #: The template it was launched from; None for a self-registered process.
    template: str | None = None
    pid: int
    #: The process group the launcher created. Stopping signals the group, so
    #: a server that forked workers does not leave them holding the port. It is
    #: also the guard against PID reuse: a recycled pid is a different group.
    pgid: int | None = None
    #: True when the launcher started it and may therefore stop it. A process
    #: that registered itself is someone else's to stop.
    managed: bool = True
    host: str
    port: int
    url: str
    #: Path probed for readiness, relative to ``url``. None means "listening is
    #: ready enough" — the probe then only checks the port accepts connections.
    health_path: str | None = None
    ready_timeout_seconds: float = 120.0
    cwd: str | None = None
    command: list[str] = Field(default_factory=list)
    log_path: str | None = None
    #: The instance's private directory — a database snapshot, caches.
    data_dir: str | None = None
    #: For a client: the server instance it talks to.
    target_instance_id: str | None = None
    #: Free-form facts a host wants shown: branch, worktree, database mode.
    labels: dict[str, str] = Field(default_factory=dict)
    started_at: datetime
    ready_at: datetime | None = None
    exit_code: int | None = None
    #: Why the instance failed, in words an operator can act on.
    last_error: str | None = None


class InstanceView(InstanceRecord):
    """A record with the state derived from the live process table."""

    state: InstanceState


class UnreadableRecord(BaseModel):
    """A registry file that could not be parsed.

    Reported rather than skipped: a listing that quietly drops a file is a
    running server nobody can find, still holding its port.
    """

    path: str
    error: str


class InstanceListing(BaseModel):
    instances: list[InstanceView]
    unreadable: list[UnreadableRecord] = Field(default_factory=list)


class InstanceHealth(BaseModel):
    ok: bool
    latency_ms: int
    #: Empty when ok; otherwise what failed.
    error: str = ""
    status_code: int | None = None


class InstanceLogs(BaseModel):
    path: str | None
    lines: list[str]
    #: True when the file held more lines than were returned.
    truncated: bool = False
