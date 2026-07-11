"""Beta application composition and process startup."""

import sys

import uvicorn

from .audit import AuditLogger
from .configuration import Settings, load_settings
from .errors import ConfigurationError
from .routing import AuthenticatedMcpGateway
from .tools import get_registered_server


def validate_settings(settings: Settings) -> None:
    if not settings.ha_token:
        raise ConfigurationError(
            "no SUPERVISOR_TOKEN/HA_TOKEN available — cannot reach Home Assistant"
        )
    if not settings.access_secret or len(settings.access_secret) < 24:
        raise ConfigurationError(
            "access_secret is unset or too short (minimum 24 characters)"
        )


def create_application(settings: Settings | None = None):
    settings = settings or load_settings()
    server = get_registered_server()
    audit = AuditLogger(settings.audit_path, settings.access_secret)
    return AuthenticatedMcpGateway(server.streamable_http_app(), settings, audit)


def main() -> None:
    settings = load_settings()
    try:
        validate_settings(settings)
    except ConfigurationError as exc:
        sys.exit(f"FATAL: {exc}")
    print(
        f"HA MCP Engineering Server Beta starting on :{settings.port} "
        f"(HA at {settings.ha_url})",
        flush=True,
    )
    print("Beta MCP endpoint configured with a redacted secret path", flush=True)
    app = create_application(settings)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.port,
        log_level="info",
        access_log=False,
    )
