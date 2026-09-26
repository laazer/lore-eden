"""Local instances: run a project's servers and clients on free ports, and find them.

A feature that only touches the UI runs a branch *client* against the shared
*main* server. One that needs a server change runs a branch *server* on a port
of its own, and a client pointed at it. Both are launched from templates a host
registers, recorded in a registry every process on the machine can read, and
managed over HTTP by the router a host mounts:

.. code-block:: python

    from lore_eden.instances import (
        CommandTemplate, FileInstanceRegistry, InstanceKind, InstanceManager,
        TemplateCatalog, TemplateParam, make_instances_router, register_self,
    )

    templates = TemplateCatalog()
    templates.register(CommandTemplate(
        project="shop", name="api", kind=InstanceKind.SERVER,
        command=["uvicorn", "shop.main:app", "--port", "{port}"],
        cwd="{param:checkout}/server",
        params=[TemplateParam(key="checkout", label="Checkout", required=True)],
    ))
    manager = InstanceManager(FileInstanceRegistry(), templates)
    app.include_router(make_instances_router(manager), prefix="/api/instances")

The main instance advertises itself with :func:`register_self` at startup.
Anything else finds it with ``python -m lore_eden.instances url main``.
"""

from lore_eden.instances.manager import (
    InstanceLaunchError,
    InstanceManager,
    InstanceNotFoundError,
    NotManagedError,
    probe,
)
from lore_eden.instances.models import (
    InstanceHealth,
    InstanceKind,
    InstanceListing,
    InstanceLogs,
    InstanceRecord,
    InstanceRole,
    InstanceState,
    InstanceView,
    UnreadableRecord,
)
from lore_eden.instances.ports import NoFreePortError, allocate_port, port_is_free
from lore_eden.instances.registry import (
    ENV_INSTANCE_ID,
    ENV_REGISTRY_DIR,
    FileInstanceRegistry,
    SelfRegistration,
    register_self,
)
from lore_eden.instances.router import make_instances_router
from lore_eden.instances.templates import (
    CommandTemplate,
    InstanceTemplate,
    LaunchContext,
    LaunchRequest,
    LaunchSpec,
    TemplateCatalog,
    TemplateInfo,
    TemplateParam,
    TemplateParamError,
    UnknownTemplateError,
    resolve_params,
)

__all__ = [
    "ENV_INSTANCE_ID",
    "ENV_REGISTRY_DIR",
    "CommandTemplate",
    "FileInstanceRegistry",
    "InstanceHealth",
    "InstanceKind",
    "InstanceLaunchError",
    "InstanceListing",
    "InstanceLogs",
    "InstanceManager",
    "InstanceNotFoundError",
    "InstanceRecord",
    "InstanceRole",
    "InstanceState",
    "InstanceTemplate",
    "InstanceView",
    "LaunchContext",
    "LaunchRequest",
    "LaunchSpec",
    "NoFreePortError",
    "NotManagedError",
    "SelfRegistration",
    "TemplateCatalog",
    "TemplateInfo",
    "TemplateParam",
    "TemplateParamError",
    "UnknownTemplateError",
    "UnreadableRecord",
    "allocate_port",
    "make_instances_router",
    "port_is_free",
    "probe",
    "register_self",
    "resolve_params",
]
