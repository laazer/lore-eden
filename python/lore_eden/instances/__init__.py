"""Local instances: run a project's servers and clients on free ports, and find them.

Templates say what can run — in code (:class:`CommandTemplate`), or as data
(:mod:`lore_eden.instances.spec`). :class:`InstanceManager` launches them;
:func:`make_instances_router` and :func:`register_instance_tools` expose it to
people and agents. See the package README for a walkthrough.
"""

from lore_eden.instances.failures import Failure, FailureKind, classify
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
from lore_eden.instances.spec import (
    SpecTemplate,
    TargetSpec,
    TemplateFile,
    TemplateFileError,
    TemplateSpec,
    load_template_file,
    parse_template_file,
    validate_spec,
)
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
    TemplateSource,
    UnknownTemplateError,
    resolve_params,
)
from lore_eden.instances.tools import INSTANCE_TOOL_NAMES, instance_tools, register_instance_tools
from lore_eden.instances.worktrees import Worktree, list_worktrees

__all__ = [
    "ENV_INSTANCE_ID",
    "ENV_REGISTRY_DIR",
    "CommandTemplate",
    "FileInstanceRegistry",
    "INSTANCE_TOOL_NAMES",
    "Failure",
    "FailureKind",
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
    "Worktree",
    "TemplateSpec",
    "TemplateSource",
    "TemplateFileError",
    "TemplateFile",
    "TargetSpec",
    "SpecTemplate",
    "TemplateInfo",
    "TemplateParam",
    "TemplateParamError",
    "UnknownTemplateError",
    "UnreadableRecord",
    "allocate_port",
    "classify",
    "instance_tools",
    "make_instances_router",
    "validate_spec",
    "parse_template_file",
    "load_template_file",
    "list_worktrees",
    "port_is_free",
    "probe",
    "register_instance_tools",
    "register_self",
    "resolve_params",
]
