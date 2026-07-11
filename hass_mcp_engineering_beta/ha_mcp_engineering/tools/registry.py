"""Tool registration boundary.

The compatibility module carries the unchanged v1.1.2 implementations. Importing
it registers the same 25 functions on the beta FastMCP instance. New v2 tools
should be added in focused modules and registered here instead of extending the
compatibility layer.
"""

from . import compatibility

_SERVER = compatibility.mcp
if "get_server_health" not in {
    tool.name for tool in _SERVER._tool_manager.list_tools()
}:
    # Register the beta-native tool explicitly on the FastMCP instance used to
    # serve tools/list. This avoids relying on capability metadata or an import
    # side effect as proof that the tool is callable.
    _SERVER.tool()(compatibility.get_server_health)


def get_registered_server():
    return _SERVER
