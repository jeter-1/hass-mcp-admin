"""FastMCP construction boundary."""

from mcp.server.fastmcp import FastMCP

from .configuration import Settings

INSTRUCTIONS = """Operating procedure for this Home Assistant admin server:
1. Debug with evidence, not hypothesis.
2. Read blueprint source before reasoning about blueprint behavior.
3. Test Jinja templates against live state before configuration writes.
4. Automation, script, input_boolean, and input_number configuration writes
   require an immutable change plan, exact-hash external approval, ordered
   governed apply, and exact read-back verification.
5. Multi-operation plans are non-atomic, stop on the first failure, and never
   roll back automatically. Inspect every per-step result and remaining risk.
6. Legacy execution, deletion, reload, and ungoverned upsert tools fail closed;
   generated evidence or recommendations are never authorization.
7. Prefer narrow queries over broad dumps."""


def create_mcp_server(settings: Settings) -> FastMCP:
    return FastMCP(
        "ha-engineering-beta",
        instructions=INSTRUCTIONS,
        host="0.0.0.0",
        port=settings.port,
        streamable_http_path="/mcp",
        stateless_http=True,
    )
