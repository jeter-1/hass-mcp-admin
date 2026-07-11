"""Tool registration boundary.

The compatibility module carries the unchanged v1.1.2 implementations. Importing
it registers the same 25 functions on the beta FastMCP instance. New v2 tools
should be added in focused modules and registered here instead of extending the
compatibility layer.
"""

from . import compatibility


def get_registered_server():
    return compatibility.mcp
