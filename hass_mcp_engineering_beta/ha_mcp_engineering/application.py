"""Beta application composition, validation, and structured startup."""

import logging
import os
import sys

import uvicorn

from .audit import AuditLogger
from .configuration import Settings, load_settings
from .errors import ConfigurationError
from .logging_config import configure_logging, get_logger, log_event
from .health import HEALTH
from .routing import AuthenticatedMcpGateway
from .tools import get_registered_server

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


def validate_settings(settings: Settings) -> None:
    errors = []
    if not settings.ha_token:
        errors.append("Home Assistant API token is unavailable")
    if not settings.ha_url.startswith(("http://", "https://")):
        errors.append("Home Assistant URL must use http or https")
    if not settings.access_secret or len(settings.access_secret) < 24:
        errors.append("access_secret is unset or shorter than 24 characters")
    if not 1 <= settings.port <= 65535:
        errors.append("port must be between 1 and 65535")
    if settings.audit_enabled and not settings.audit_path.strip():
        errors.append("audit output path is required when auditing is enabled")
    if settings.audit_max_payload_chars < 512:
        errors.append("audit_max_payload_chars must be at least 512")
    if settings.log_level not in VALID_LOG_LEVELS:
        errors.append("log_level must be DEBUG, INFO, WARNING, or ERROR")
    if not 0 < settings.ha_timeout_seconds <= 300:
        errors.append("ha_timeout_seconds must be greater than 0 and at most 300")
    if not 1024 <= settings.response_size_limit <= 1_000_000:
        errors.append("response_size_limit must be between 1024 and 1000000")
    if not settings.redaction_enabled:
        errors.append("redaction_enabled must remain true")
    if errors:
        raise ConfigurationError(
            "Beta configuration validation failed.", details={"issues": errors}
        )


def create_application(settings: Settings | None = None):
    settings = settings or load_settings()
    server = get_registered_server()
    audit = AuditLogger(
        settings.audit_path,
        settings.access_secret,
        enabled=settings.audit_enabled,
        max_payload_chars=settings.audit_max_payload_chars,
    )
    gateway = AuthenticatedMcpGateway(server.streamable_http_app(), settings, audit)
    HEALTH.configure(settings, audit, gateway)
    return gateway


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    logger = get_logger("application")
    try:
        validate_settings(settings)
    except ConfigurationError as exc:
        log_event(
            logger,
            logging.ERROR,
            "startup_validation_failed",
            exc.safe_message,
            context=exc.details,
            secret=settings.access_secret,
        )
        sys.exit("FATAL: beta configuration validation failed; review structured logs")
    log_event(
        logger,
        logging.INFO,
        "server_starting",
        "HA MCP Engineering Server Beta is starting.",
        context={
            "port": settings.port,
            "runtime": "home_assistant_addon" if os.environ.get("SUPERVISOR_TOKEN") else "standalone",
            "redaction_enabled": settings.redaction_enabled,
        },
        secret=settings.access_secret,
    )
    uvicorn.run(
        create_application(settings),
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
