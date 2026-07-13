"""Beta application composition, validation, and structured startup."""

import logging
import os
import sys

import uvicorn

from .audit import AuditLogger
from .configuration import MIN_ACCESS_SECRET_LENGTH, Settings, load_settings
from .errors import ConfigurationError
from .logging_config import configure_logging, get_logger, log_event
from .health import HEALTH
from .clients import HomeAssistantRestClient
from .clients import HomeAssistantWebSocketClient
from .governance import GOVERNANCE
from .dependency import DEPENDENCY_ANALYSIS
from .reliability import RELIABILITY_ANALYSIS
from .impact import CHANGE_IMPACT_ANALYSIS
from .integrity import CONFIGURATION_INTEGRITY_ANALYSIS
from .routing import AuthenticatedMcpGateway
from .tools import get_registered_server

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


def validate_settings(settings: Settings) -> None:
    errors = []
    if not settings.ha_token:
        errors.append("Home Assistant API token is unavailable")
    if not settings.ha_url.startswith(("http://", "https://")):
        errors.append("Home Assistant URL must use http or https")
    if not settings.access_secret or len(settings.access_secret) < MIN_ACCESS_SECRET_LENGTH:
        errors.append(
            f"access_secret is unset or shorter than {MIN_ACCESS_SECRET_LENGTH} characters"
        )
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
    if not settings.governance_path.strip():
        errors.append("governance_path is required")
    if not 1 <= settings.governance_retention_days <= 365:
        errors.append("governance_retention_days must be between 1 and 365")
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
    GOVERNANCE.configure(settings, audit, HomeAssistantRestClient(settings))
    DEPENDENCY_ANALYSIS.configure(
        HomeAssistantRestClient(settings),
        HomeAssistantWebSocketClient(settings),
        secret=settings.access_secret,
        timeout=settings.ha_timeout_seconds,
    )
    RELIABILITY_ANALYSIS.configure(
        HomeAssistantRestClient(settings),
        HomeAssistantWebSocketClient(settings),
        secret=settings.access_secret,
        ha_token=settings.ha_token,
        timeout=settings.ha_timeout_seconds,
    )
    CHANGE_IMPACT_ANALYSIS.configure(
        DEPENDENCY_ANALYSIS.require().index,
        HomeAssistantRestClient(settings),
        HomeAssistantWebSocketClient(settings),
        secret=settings.access_secret,
        ha_token=settings.ha_token,
        timeout=settings.ha_timeout_seconds,
    )
    CONFIGURATION_INTEGRITY_ANALYSIS.configure(
        DEPENDENCY_ANALYSIS.require().index,
        HomeAssistantRestClient(settings),
        HomeAssistantWebSocketClient(settings),
        secret=settings.access_secret,
        ha_token=settings.ha_token,
        timeout=settings.ha_timeout_seconds,
    )
    HEALTH.configure(
        settings,
        audit,
        gateway,
        GOVERNANCE,
        DEPENDENCY_ANALYSIS,
        RELIABILITY_ANALYSIS,
        CHANGE_IMPACT_ANALYSIS,
        CONFIGURATION_INTEGRITY_ANALYSIS,
    )
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
