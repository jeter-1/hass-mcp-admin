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

READMISSION_SESSION_IDLE_TIMEOUT_SECONDS = 1_800.0


def create_mcp_server(settings: Settings) -> FastMCP:
    server = FastMCP(
        "ha-engineering-beta",
        instructions=INSTRUCTIONS,
        host="0.0.0.0",
        port=settings.port,
        streamable_http_path="/mcp",
        # Catalog-generation authority is bound to the authenticated inbound
        # MCP session. Preserve Beta 54's stateless topology while the feature
        # is disabled; automatic readmission requires a stable stateful session
        # so a tools/list generation can authorize later delegated calls.
        stateless_http=not settings.ha_mcp_release_registry_enabled,
    )
    if settings.ha_mcp_release_registry_enabled:
        # The pinned SDK exposes the stateful session manager as a public
        # boundary but FastMCP 1.28.1 does not yet forward its idle timeout.
        # Instantiate it once and bound abandoned authenticated sessions.
        server.streamable_http_app()
        server.session_manager.session_idle_timeout = (
            READMISSION_SESSION_IDLE_TIMEOUT_SECONDS
        )
    return server
