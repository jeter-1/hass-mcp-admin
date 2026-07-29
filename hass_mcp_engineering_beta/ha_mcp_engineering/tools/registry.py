"""Tool registration and schema-preserving provider-routing boundary."""

from functools import wraps
import inspect

from mcp.types import ToolAnnotations

from . import compatibility
from .governance import GOVERNANCE_TOOLS
from .analysis import ANALYSIS_TOOLS
from .dashboard import DASHBOARD_TOOLS
from ..capabilities import CAPABILITIES
from ..mcp_sdk_compatibility import McpSdkToolRegistry
from ..providers.dispatch import CANONICAL_DISPATCHER
from ..providers.routing import CapabilityRoute, routing_for_tool

_SERVER = compatibility.mcp
_SDK_TOOLS = McpSdkToolRegistry(_SERVER)
if "get_server_health" not in _SDK_TOOLS.snapshot():
    # Register the beta-native tool explicitly on the FastMCP instance used to
    # serve tools/list. This avoids relying on capability metadata or an import
    # side effect as proof that the tool is callable.
    _SERVER.tool()(compatibility.get_server_health)

_registered = set(_SDK_TOOLS.snapshot())
_PROPOSAL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
_PROPOSAL_TOOLS = {
    "create_backup_plan",
    "create_reload_plan",
    "create_addon_restart_plan",
    "create_home_assistant_restart_plan",
    "create_change_plan",
    "create_configuration_plan",
}
_TASK_READ_TOOLS = {"get_execution_task", "list_execution_tasks"}
_TASK_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_TASK_CANCEL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
for governance_tool in GOVERNANCE_TOOLS:
    if governance_tool.__name__ not in _registered:
        _SERVER.tool(
            annotations=(
                _PROPOSAL_ANNOTATIONS
                if governance_tool.__name__ in _PROPOSAL_TOOLS
                else _TASK_READ_ANNOTATIONS
                if governance_tool.__name__ in _TASK_READ_TOOLS
                else _TASK_CANCEL_ANNOTATIONS
                if governance_tool.__name__ == "cancel_execution_task"
                else None
            )
        )(governance_tool)

_registered = set(_SDK_TOOLS.snapshot())
for analysis_tool in ANALYSIS_TOOLS:
    if analysis_tool.__name__ not in _registered:
        _SERVER.tool()(analysis_tool)

_registered = set(_SDK_TOOLS.snapshot())
_DASHBOARD_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
for dashboard_tool in DASHBOARD_TOOLS:
    if dashboard_tool.__name__ not in _registered:
        _SERVER.tool(
            annotations=_DASHBOARD_READ_ANNOTATIONS
        )(dashboard_tool)


def _routed_wrapper(tool_name, original):
    signature = inspect.signature(original)

    @wraps(original)
    async def routed(*args, **kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return await CANONICAL_DISPATCHER.execute(
            tool_name,
            lambda: original(*args, **kwargs),
            arguments=dict(bound.arguments),
            response_limit=compatibility.SETTINGS.response_size_limit,
        )

    return routed


# Compatibility functions were registered during module import in v1-style
# FastMCP decorators. Replace only the served canonical registrations whose
# routing policy selects a provider. functools.wraps preserves each original
# signature, so the public MCP schemas remain byte-for-byte compatible.
for capability in CAPABILITIES:
    name = capability["tool"]
    decision = routing_for_tool(name)
    if decision.route in {CapabilityRoute.ENGINEERING_NATIVE, CapabilityRoute.UNSUPPORTED}:
        continue
    existing = _SDK_TOOLS.get(name)
    if not existing:
        continue
    wrapped = _routed_wrapper(name, existing.fn)
    _SDK_TOOLS.remove_exact(name)
    _SERVER.tool(name=name)(wrapped)
    setattr(compatibility, name, wrapped)


def get_registered_server():
    return _SERVER
