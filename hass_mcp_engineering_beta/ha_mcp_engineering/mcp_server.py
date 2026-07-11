"""FastMCP construction boundary."""

from mcp.server.fastmcp import FastMCP

from .configuration import Settings

INSTRUCTIONS = """Operating procedure for this Home Assistant admin server:
1. Debug with evidence, not hypothesis.
2. Read blueprint source before reasoning about blueprint behavior.
3. Test Jinja templates against live state before configuration writes.
4. Verify writes and run configuration checks.
5. Require explicit confirmation for destructive physical actions.
6. Prefer narrow queries over broad dumps."""


def create_mcp_server(settings: Settings) -> FastMCP:
    return FastMCP(
        "ha-engineering-beta",
        instructions=INSTRUCTIONS,
        host="0.0.0.0",
        port=settings.port,
        streamable_http_path="/mcp",
        stateless_http=True,
    )
