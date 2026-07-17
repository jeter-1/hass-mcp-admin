"""Tool registration and schema-preserving provider-routing boundary."""

from functools import wraps
import inspect

from mcp.types import ToolAnnotations

from . import compatibility
from .governance import GOVERNANCE_TOOLS
from .analysis import ANALYSIS_TOOLS
from .dashboard import DASHBOARD_TOOLS
from ..capabilities import CAPABILITIES
from ..providers.dispatch import CANONICAL_DISPATCHER
from ..providers.routing import CapabilityRoute, routing_for_tool

_SERVER = compatibility.mcp
if "get_server_health" not in {
    tool.name for tool in _SERVER._tool_manager.list_tools()
}:
    # Register the beta-native tool explicitly on the FastMCP instance used to
    # serve tools/list. This avoids relying on capability metadata or an import
    # side effect as proof that the tool is callable.
    _SERVER.tool()(compatibility.get_server_health)

_registered = {tool.name for tool in _SERVER._tool_manager.list_tools()}
for governance_tool in GOVERNANCE_TOOLS:
    if governance_tool.__name__ not in _registered:
        _SERVER.tool()(governance_tool)

_registered = {tool.name for tool in _SERVER._tool_manager.list_tools()}
for analysis_tool in ANALYSIS_TOOLS:
    if analysis_tool.__name__ not in _registered:
        _SERVER.tool()(analysis_tool)

_registered = {tool.name for tool in _SERVER._tool_manager.list_tools()}
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
    existing = _SERVER._tool_manager.get_tool(name)
    if not existing:
        continue
    wrapped = _routed_wrapper(name, existing.fn)
    del _SERVER._tool_manager._tools[name]
    _SERVER.tool(name=name)(wrapped)
    setattr(compatibility, name, wrapped)


def get_registered_server():
    return _SERVER
