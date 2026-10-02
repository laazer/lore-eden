"""Launching, observing and stopping instances.

Launch returns as soon as the process is started. Readiness is not awaited in
the request: a branch server may spend minutes syncing dependencies first, and
a request held open that long times out while the server comes up fine behind
it. Instead the record is written in ``starting`` and every read advances it —
:meth:`InstanceManager.refresh` probes an instance that is still starting and
stamps ``ready_at`` the first time it answers. A UI polling the list is what
drives it, and so is :meth:`wait_ready` for a caller that does want to block.

Nothing depends on the launching process surviving. The watcher thread a
launcher might have used dies with a reloading server and leaves its instance
``starting`` forever; a probe on read has no such owner to lose.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import signal
import socket
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import httpx

from lore_eden.instances.models import (
    InstanceHealth,
    InstanceListing,
    InstanceLogs,
    InstanceRecord,
    InstanceRole,
    InstanceState,
    InstanceView,
)
from lore_eden.instances.ports import allocate_port
from lore_eden.instances.registry import (
    ENV_INSTANCE_ID,
    ENV_REGISTRY_DIR,
    FileInstanceRegistry,
    slugify,
)
from lore_eden.instances.templates import LaunchContext, LaunchRequest, LaunchSpec, TemplateSource

logger = logging.getLogger(__name__)

#: Probe budget while listing. Short: it runs once per starting instance on
#: every poll, and a server still booting refuses the connection immediately.
LIST_PROBE_TIMEOUT_SECONDS = 0.5
#: Budget for an explicit health check, which an operator asked for.
HEALTH_TIMEOUT_SECONDS = 5.0
#: How long a stop waits after SIGTERM before SIGKILL.
STOP_GRACE_SECONDS = 10.0

_POLL_SECONDS = 0.25
#: How long a signal answered with EPERM waits for our own child to finish
#: exiting. Milliseconds normally; seconds only on a badly overloaded host.
_EXIT_SETTLE_SECONDS = 2.0


class InstanceNotFoundError(KeyError):
    pass


class NotManagedError(RuntimeError):
    """The instance registered itself; whoever started it must stop it."""


class InstanceLaunchError(RuntimeError):
    """The process could not be started at all."""


def _fill(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace(f"{{{key}}}", value)
    return text


def probe(record: InstanceRecord, *, timeout: float) -> InstanceHealth:
    """GET the health path, or — with none — check the port accepts a connection."""
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    if record.health_path is None:
        try:
            with socket.create_connection((record.host, record.port), timeout=timeout):
                return InstanceHealth(ok=True, latency_ms=elapsed())
        except OSError as exc:
            return InstanceHealth(ok=False, latency_ms=elapsed(), error=str(exc))
    url = record.url.rstrip("/") + record.health_path
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.HTTPError as exc:
        return InstanceHealth(ok=False, latency_ms=elapsed(), error=f"{type(exc).__name__}: {exc}")
    ok = response.is_success
    return InstanceHealth(
        ok=ok,
        latency_ms=elapsed(),
        status_code=response.status_code,
        error="" if ok else f"GET {record.health_path} returned {response.status_code}",
    )


class InstanceManager:
    """Everything a host needs to run instances from its templates."""

    def __init__(self, registry: FileInstanceRegistry, templates: TemplateSource) -> None:
        self.registry = registry
        self.templates = templates
        #: Children this process started. Polling them is what reaps a child
        #: that exited: an unreaped zombie still answers `kill(pid, 0)`, so
        #: without it a crashed instance reads as alive to its own launcher.
        self._children: dict[str, subprocess.Popen[bytes]] = {}

    # -- launch ----------------------------------------------------------

    def launch(self, request: LaunchRequest) -> InstanceView:
        template = self.templates.get(request.template)
        info = template.describe()
        label = request.name or info.name
        instance_id = slugify(f"{label}-{secrets.token_hex(3)}")
        instance_dir = self.registry.data_root / instance_id
        instance_dir.mkdir(parents=True)
        try:
            spec = template.build(request, LaunchContext(instance_id, instance_dir, self.registry))
            return self._start(instance_id, instance_dir, request.template, spec)
        except BaseException:
            # Nothing was registered, so nothing will ever show this directory
            # to anyone; it is ours to remove.
            shutil.rmtree(instance_dir, ignore_errors=True)
            raise

    def _start(
        self, instance_id: str, instance_dir: Path, template: str, spec: LaunchSpec
    ) -> InstanceView:
        log_path = self.registry.logs_dir / f"{instance_id}.log"
        with self.registry.lock():
            port = spec.port or allocate_port(
                spec.host, port_range=spec.port_range, claimed=self.registry.claimed_ports()
            )
            url = f"http://{spec.host}:{port}"
            values = {
                "host": spec.host,
                "port": str(port),
                "url": url,
                "instance_id": instance_id,
                "instance_dir": str(instance_dir),
            }
            command = [_fill(part, values) for part in spec.command]
            cwd = Path(_fill(str(spec.cwd), values))
            env = {
                **os.environ,
                **{key: _fill(value, values) for key, value in spec.env.items()},
                "PORT": str(port),
                ENV_INSTANCE_ID: instance_id,
                ENV_REGISTRY_DIR: str(self.registry.root),
            }
            with open(log_path, "ab") as log:
                try:
                    child = subprocess.Popen(  # noqa: S603 - argv from a host-registered template
                        command,
                        cwd=cwd,
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        # Its own group, so stop reaches the workers it forks.
                        start_new_session=True,
                    )
                except OSError as exc:
                    raise InstanceLaunchError(f"could not start {command[0]!r} in {cwd}: {exc}") from exc
            record = InstanceRecord(
                id=instance_id,
                project=spec.project,
                name=spec.name,
                kind=spec.kind,
                role=spec.role,
                template=template,
                pid=child.pid,
                pgid=child.pid,
                host=spec.host,
                port=port,
                url=url,
                health_path=spec.health_path,
                ready_timeout_seconds=spec.ready_timeout_seconds,
                cwd=str(cwd),
                command=command,
                log_path=str(log_path),
                data_dir=str(instance_dir),
                target_instance_id=spec.target_instance_id,
                labels=spec.labels,
                started_at=datetime.now(timezone.utc),
            )
            self.registry.put(record)
        self._children[instance_id] = child
        logger.info("launched %s (%s) pid=%s on %s", instance_id, template, child.pid, url)
        return self.registry.view(record)

    # -- observe ---------------------------------------------------------

    def _reap(self, record: InstanceRecord) -> InstanceRecord:
        child = self._children.get(record.id)
        if child is None or child.poll() is None:
            return record
        del self._children[record.id]
        if record.exit_code is not None:
            return record
        updated = record.model_copy(
            update={
                "exit_code": child.returncode,
                "last_error": record.last_error or f"exited with code {child.returncode}",
            }
        )
        self.registry.put(updated)
        return updated

    def refresh(self, record: InstanceRecord) -> InstanceView:
        """The record's current view, advancing ``starting`` to ``ready`` if it now answers."""
        record = self._reap(record)
        view = self.registry.view(record)
        if view.state == InstanceState.EXITED and record.last_error is None:
            # Exited without this process seeing it — no exit code to report,
            # but "it is gone" still has to be said, not left for the reader
            # to infer from a state name.
            record = record.model_copy(update={"last_error": "process is no longer running"})
            self.registry.put(record)
            return self.registry.view(record)
        if view.state not in (InstanceState.STARTING, InstanceState.STALLED):
            return view
        health = probe(record, timeout=LIST_PROBE_TIMEOUT_SECONDS)
        if not health.ok:
            return view
        ready = record.model_copy(update={"ready_at": datetime.now(timezone.utc), "last_error": None})
        self.registry.put(ready)
        return self.registry.view(ready)

    def list(self, project: str | None = None) -> InstanceListing:
        scan = self.registry.scan(project)
        views = [self.refresh(record) for record in scan.records]
        views.sort(key=lambda v: (v.project, v.role != InstanceRole.MAIN, v.started_at))
        return InstanceListing(instances=views, unreadable=scan.unreadable)

    def get(self, instance_id: str) -> InstanceView:
        record = self.registry.get(instance_id)
        if record is None:
            raise InstanceNotFoundError(instance_id)
        return self.refresh(record)

    def health(self, instance_id: str) -> InstanceHealth:
        view = self.get(instance_id)
        if view.state == InstanceState.EXITED:
            return InstanceHealth(ok=False, latency_ms=0, error=view.last_error or "not running")
        return probe(view, timeout=HEALTH_TIMEOUT_SECONDS)

    def wait_ready(self, instance_id: str, *, timeout: float | None = None) -> InstanceView:
        """Block until ready, exited, or out of budget; return whichever it was."""
        view = self.get(instance_id)
        deadline = time.monotonic() + (timeout if timeout is not None else view.ready_timeout_seconds)
        while view.state == InstanceState.STARTING and time.monotonic() < deadline:
            time.sleep(_POLL_SECONDS)
            view = self.get(instance_id)
        return view

    def logs(self, instance_id: str, *, tail: int = 200) -> InstanceLogs:
        record = self.registry.get(instance_id)
        if record is None:
            raise InstanceNotFoundError(instance_id)
        if record.log_path is None:
            return InstanceLogs(path=None, lines=[])
        path = Path(record.log_path)
        if not path.exists():
            return InstanceLogs(path=str(path), lines=[])
        kept: deque[str] = deque(maxlen=max(tail, 1))
        total = 0
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                kept.append(line.rstrip("\n"))
                total += 1
        return InstanceLogs(path=str(path), lines=list(kept), truncated=total > len(kept))

    # -- stop ------------------------------------------------------------

    def stop(self, instance_id: str, *, keep_data: bool = False) -> None:
        """Stop the process group if it is running, then forget the instance.

        Also the way to dismiss an instance that already exited: its record
        stays in the list, with its log, until someone does this.
        """
        record = self.registry.get(instance_id)
        if record is None:
            raise InstanceNotFoundError(instance_id)
        if not record.managed:
            if self.registry.is_alive(record):
                raise NotManagedError(
                    f"{record.name} registered itself (pid {record.pid}); stop it where it was started"
                )
            # A dead self-registration is only a stale file.
            self.registry.remove(instance_id)
            return
        if self.registry.is_alive(record) and record.pgid is not None:
            self._terminate(record.pgid, instance_id)
        self._children.pop(instance_id, None)
        self.registry.remove(instance_id)
        if record.data_dir and not keep_data:
            try:
                shutil.rmtree(record.data_dir)
            except FileNotFoundError:  # silent-ok: already gone is the outcome stop wants
                pass
            except OSError:
                # The instance is stopped and forgotten either way; what is
                # left is disk, and the operator needs the path to reclaim it.
                logger.exception("stopped %s but could not remove %s", instance_id, record.data_dir)
        if record.log_path:
            Path(record.log_path).unlink(missing_ok=True)

    def _terminate(self, pgid: int, instance_id: str) -> None:
        child = self._children.get(instance_id)
        if not _signal_group(pgid, signal.SIGTERM, child):
            return
        deadline = time.monotonic() + STOP_GRACE_SECONDS
        while time.monotonic() < deadline:
            if child is not None:
                child.poll()
            if not _signal_group(pgid, 0, child):
                return
            time.sleep(_POLL_SECONDS)
        logger.warning("instance %s ignored SIGTERM for %ss; killing", instance_id, STOP_GRACE_SECONDS)
        if not _signal_group(pgid, signal.SIGKILL, child):
            return
        if child is not None:
            child.wait(timeout=STOP_GRACE_SECONDS)


def _signal_group(pgid: int, sig: int, child: subprocess.Popen[bytes] | None) -> bool:
    """Send ``sig`` to the group; ``False`` when it has no live member left.

    macOS answers ``EPERM``, not ``ESRCH``, for a group whose only member is
    a leader that has exited — whether it is an unreaped zombie or still
    tearing down after SIGTERM, before ``poll()`` can see it go. A loaded
    host stretches the second window long enough for a stop's probe to land
    in it. That answer is ours to interpret only when the leader is our own
    child: wait briefly for it to finish exiting and reap it, then ask again,
    so what decides is the group's real membership. An ``EPERM`` on any
    other group, from a child that outlives the wait, or one that survives
    the reap, is a real permission failure and raises.
    """
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        if child is None or not _reap(child):
            raise
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return False
    return True


def _reap(child: subprocess.Popen[bytes]) -> bool:
    """Reap ``child``, giving one still exiting time to finish; ``False`` if it does not."""
    try:
        child.wait(timeout=_EXIT_SETTLE_SECONDS)
    except subprocess.TimeoutExpired:
        return False
    return True
