"""HTTP endpoints for managing instances, mountable in any FastAPI host.

::

    app.include_router(make_instances_router(lambda: manager), prefix="/api/instances")

The router takes a *provider* rather than a manager, and resolves it as a
FastAPI dependency: a host can build its manager lazily — the registry
creates its directory on construction — and a test can swap it with
``app.dependency_overrides[provider]``.

Launch takes a template name and parameters, never a command — see
:mod:`lore_eden.instances.templates` for why. Endpoints are synchronous
functions, so FastAPI runs them in its threadpool and a stop's grace period
does not block the event loop.
"""

# No `from __future__ import annotations` here: the endpoint signatures use a
# dependency alias local to the factory, and FastAPI resolves a string
# annotation against module globals, where it does not exist.

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status

from lore_eden.instances.manager import (
    InstanceLaunchError,
    InstanceManager,
    InstanceNotFoundError,
    NotManagedError,
)
from lore_eden.instances.models import InstanceHealth, InstanceListing, InstanceLogs, InstanceView
from lore_eden.instances.ports import NoFreePortError
from lore_eden.instances.templates import (
    LaunchRequest,
    TemplateInfo,
    TemplateParamError,
    UnknownTemplateError,
)

_T = TypeVar("_T")

#: Each failure a manager call can raise, and the status it answers with.
#: One table rather than a try block per endpoint, so every endpoint maps the
#: same failure to the same status.
_STATUS_FOR: dict[type[Exception], int] = {
    InstanceNotFoundError: status.HTTP_404_NOT_FOUND,
    UnknownTemplateError: status.HTTP_404_NOT_FOUND,
    TemplateParamError: 422,
    NotManagedError: status.HTTP_409_CONFLICT,
    NoFreePortError: status.HTTP_503_SERVICE_UNAVAILABLE,
    InstanceLaunchError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}
#: KeyError's str() is the repr of its key; these say what was missing instead.
_MISSING_NOUN: dict[type[Exception], str] = {
    InstanceNotFoundError: "instance",
    UnknownTemplateError: "template",
}


def _nearest(table: dict[type[Exception], _T], exc: Exception) -> _T | None:
    """The entry for the closest class in ``exc``'s MRO, so a subclass maps too."""
    return next((table[k] for k in type(exc).__mro__ if k in table), None)


@contextmanager
def _as_http() -> Iterator[None]:
    try:
        yield
    except tuple(_STATUS_FOR) as exc:
        noun = _nearest(_MISSING_NOUN, exc)
        detail = f"no {noun} {exc.args[0]!r}" if noun else str(exc)
        code = _nearest(_STATUS_FOR, exc) or status.HTTP_500_INTERNAL_SERVER_ERROR
        raise HTTPException(code, detail) from exc


def make_instances_router(provider: Callable[[], InstanceManager]) -> APIRouter:
    router = APIRouter(tags=["instances"])
    Manager = Annotated[InstanceManager, Depends(provider)]  # noqa: N806 - a type alias

    @router.get("", response_model=InstanceListing)
    def list_instances(manager: Manager, project: str | None = None) -> InstanceListing:
        return manager.list(project)

    @router.get("/templates", response_model=list[TemplateInfo])
    def list_templates(manager: Manager) -> list[TemplateInfo]:
        return manager.templates.describe_all()

    @router.post("", response_model=InstanceView, status_code=status.HTTP_201_CREATED)
    def launch_instance(manager: Manager, request: LaunchRequest) -> InstanceView:
        with _as_http():
            return manager.launch(request)

    @router.get("/{instance_id}", response_model=InstanceView)
    def get_instance(manager: Manager, instance_id: str) -> InstanceView:
        with _as_http():
            return manager.get(instance_id)

    @router.get("/{instance_id}/health", response_model=InstanceHealth)
    def instance_health(manager: Manager, instance_id: str) -> InstanceHealth:
        with _as_http():
            return manager.health(instance_id)

    @router.get("/{instance_id}/logs", response_model=InstanceLogs)
    def instance_logs(
        manager: Manager, instance_id: str, tail: int = Query(200, ge=1, le=5000)
    ) -> InstanceLogs:
        with _as_http():
            return manager.logs(instance_id, tail=tail)

    @router.delete("/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
    def stop_instance(manager: Manager, instance_id: str, keep_data: bool = False) -> None:
        with _as_http():
            manager.stop(instance_id, keep_data=keep_data)

    return router
