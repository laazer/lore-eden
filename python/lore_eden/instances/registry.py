"""The on-disk registry of local instances.

One JSON file per instance under a directory every process on the machine can
read, so discovery needs no running control plane: a shell script, a Vite
config, or another project's tooling finds a server by reading a file. The
directory is ``$LORE_EDEN_INSTANCES_DIR``, or ``~/.lore-eden/instances``.

Writes go through a temp file and ``os.replace``, so a reader never sees half a
record. Port allocation holds an ``flock`` on the directory, so two launchers
cannot claim the same port. Both are POSIX; this module is not for Windows.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from lore_eden.instances.models import (
    InstanceKind,
    InstanceRecord,
    InstanceRole,
    InstanceState,
    InstanceView,
    UnreadableRecord,
)

logger = logging.getLogger(__name__)

ENV_REGISTRY_DIR = "LORE_EDEN_INSTANCES_DIR"
#: Set in a launched child's environment. A process that finds it knows the
#: launcher has already registered it, and must not register itself again.
ENV_INSTANCE_ID = "LORE_EDEN_INSTANCE_ID"

_ID_UNSAFE = re.compile(r"[^a-z0-9-]+")


def default_registry_dir() -> Path:
    configured = os.environ.get(ENV_REGISTRY_DIR)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".lore-eden" / "instances"


def slugify(text: str) -> str:
    """A lowercase, dash-separated form safe for a filename and a URL segment."""
    slug = _ID_UNSAFE.sub("-", text.lower()).strip("-")
    return slug or "instance"


def pid_alive(pid: int, pgid: int | None = None) -> bool:
    """Whether ``pid`` is a live process — and, given ``pgid``, still in it.

    ``EPERM`` means the process exists under another user, which is alive.
    The group check is what stops a recycled pid from reading as the instance
    it replaced.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if pgid is None:
        return True
    try:
        return os.getpgid(pid) == pgid
    except ProcessLookupError:
        return False


def derive_state(record: InstanceRecord, *, alive: bool, now: datetime) -> InstanceState:
    if not alive:
        return InstanceState.EXITED
    if record.ready_at is not None:
        return InstanceState.READY
    elapsed = (now - record.started_at).total_seconds()
    if elapsed > record.ready_timeout_seconds:
        return InstanceState.STALLED
    return InstanceState.STARTING


@dataclass
class RegistryScan:
    records: list[InstanceRecord] = field(default_factory=list)
    unreadable: list[UnreadableRecord] = field(default_factory=list)


class FileInstanceRegistry:
    """Instance records as JSON files under ``root``."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_registry_dir()).resolve()
        self.records_dir = self.root / "records"
        self.logs_dir = self.root / "logs"
        self.data_root = self.root / "data"
        for directory in (self.records_dir, self.logs_dir, self.data_root):
            directory.mkdir(parents=True, exist_ok=True)

    # -- storage ---------------------------------------------------------

    def _path(self, instance_id: str) -> Path:
        if slugify(instance_id) != instance_id:
            # An id reaches here from a URL. Refusing anything that is not
            # already a slug is what keeps `../` out of the path.
            raise KeyError(instance_id)
        return self.records_dir / f"{instance_id}.json"

    def put(self, record: InstanceRecord) -> None:
        path = self._path(record.id)
        tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
        tmp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def get(self, instance_id: str) -> InstanceRecord | None:
        try:
            path = self._path(instance_id)
        except KeyError:
            return None
        if not path.exists():
            return None
        return InstanceRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def remove(self, instance_id: str) -> bool:
        try:
            self._path(instance_id).unlink()
        except FileNotFoundError:
            return False
        return True

    def scan(self, project: str | None = None) -> RegistryScan:
        scan = RegistryScan()
        for path in sorted(self.records_dir.glob("*.json")):
            try:
                record = InstanceRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, ValidationError) as exc:
                logger.warning("unreadable instance record %s: %s", path, exc)
                scan.unreadable.append(UnreadableRecord(path=str(path), error=str(exc)))
                continue
            if project is None or record.project == project:
                scan.records.append(record)
        return scan

    # -- derived views ---------------------------------------------------

    def is_alive(self, record: InstanceRecord) -> bool:
        return pid_alive(record.pid, record.pgid)

    def view(self, record: InstanceRecord, *, now: datetime | None = None) -> InstanceView:
        state = derive_state(
            record, alive=self.is_alive(record), now=now or datetime.now(timezone.utc)
        )
        return InstanceView(**record.model_dump(), state=state)

    def claimed_ports(self) -> set[int]:
        """Ports held by any live instance, in any project."""
        return {r.port for r in self.scan().records if self.is_alive(r)}

    def find_main(self, project: str, kind: InstanceKind = InstanceKind.SERVER) -> InstanceRecord | None:
        """The live main instance of ``project``, if one is registered."""
        for record in self.scan(project).records:
            if record.role == InstanceRole.MAIN and record.kind == kind and self.is_alive(record):
                return record
        return None

    def resolve(self, project: str | None, ref: str) -> InstanceRecord | None:
        """An instance by id, or by name within ``project``; live ones win."""
        direct = self.get(ref) if slugify(ref) == ref else None
        if direct is not None:
            return direct
        named = [r for r in self.scan(project).records if r.name == ref]
        named.sort(key=lambda r: (not self.is_alive(r), -r.started_at.timestamp()))
        return named[0] if named else None

    # -- locking ---------------------------------------------------------

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Exclusive across processes, for check-then-claim of a port."""
        with open(self.root / ".lock", "a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass
class SelfRegistration:
    """Handle for a process that registered itself; release it on shutdown."""

    registry: FileInstanceRegistry
    record: InstanceRecord

    def release(self) -> None:
        """Remove the record, but only if it still describes this process.

        A second main that started after this one overwrote the record; its
        claim is newer, and removing it would hide a live server.
        """
        current = self.registry.get(self.record.id)
        if current is not None and current.pid == self.record.pid:
            self.registry.remove(self.record.id)


def register_self(
    registry: FileInstanceRegistry,
    *,
    project: str,
    name: str,
    kind: InstanceKind,
    host: str,
    port: int,
    role: InstanceRole = InstanceRole.MAIN,
    health_path: str | None = "/health",
    labels: dict[str, str] | None = None,
) -> SelfRegistration | None:
    """Advertise the running process so other tools can find it.

    Returns None — registering nothing — when this process was started by a
    launcher, which has already written the authoritative record for it. The
    id is stable (``project-name``), so a restarted main replaces its own
    record rather than accumulating dead ones.
    """
    if os.environ.get(ENV_INSTANCE_ID):
        return None
    now = datetime.now(timezone.utc)
    record = InstanceRecord(
        id=slugify(f"{project}-{name}"),
        project=project,
        name=name,
        kind=kind,
        role=role,
        pid=os.getpid(),
        managed=False,
        host=host,
        port=port,
        url=f"http://{host}:{port}",
        health_path=health_path,
        started_at=now,
        # It is serving requests by the time it can register itself.
        ready_at=now,
        labels=labels or {},
    )
    registry.put(record)
    return SelfRegistration(registry, record)

