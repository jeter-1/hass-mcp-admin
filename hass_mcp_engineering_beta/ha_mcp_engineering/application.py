"""Beta application composition, validation, and structured startup."""

import asyncio
import logging
import os
import sys
import ipaddress

import uvicorn

from .approval_web import create_approval_application as create_ingress_web_application
from .audit import AuditLogger
from .configuration import (
    MAX_TRUSTED_PROXY_CIDRS,
    MIN_ACCESS_SECRET_LENGTH,
    Settings,
    load_settings,
    parse_upstream_dashboard_endpoint,
)
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
from .incident import INCIDENT_CORRELATION
from .handoff import HANDOFF_GENERATION
from .routing import AuthenticatedMcpGateway
from .providers.upstream_dashboard import UPSTREAM_DASHBOARD
from .providers.upstream_read_gateway import UPSTREAM_READ_GATEWAY
from .providers.upstream_registry import RegistryValidationError, UpstreamTrustRegistry
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
    if not 1 <= settings.ingress_port <= 65535:
        errors.append("ingress_port must be between 1 and 65535")
    if settings.ingress_port == settings.port:
        errors.append("ingress_port must be separate from the MCP port")
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
    if settings.prewarm_startup_delay_seconds < 0:
        errors.append("prewarm_startup_delay_seconds must not be negative")
    if settings.prewarm_retry_delay_seconds < 300:
        errors.append("prewarm_retry_delay_seconds must be at least 300")
    if settings.dependency_index_soft_ttl_seconds <= 0:
        errors.append("dependency_index_soft_ttl_seconds must be positive")
    if (
        settings.dependency_index_hard_ttl_seconds
        <= settings.dependency_index_soft_ttl_seconds
    ):
        errors.append(
            "dependency_index_hard_ttl_seconds must be greater than dependency_index_soft_ttl_seconds"
        )
    if not settings.redaction_enabled:
        errors.append("redaction_enabled must remain true")
    if not settings.governance_path.strip():
        errors.append("governance_path is required")
    if not 1 <= settings.governance_retention_days <= 365:
        errors.append("governance_retention_days must be between 1 and 365")
    if len(settings.trusted_proxy_cidrs) > MAX_TRUSTED_PROXY_CIDRS:
        errors.append(
            f"trusted_proxy_cidrs must contain at most {MAX_TRUSTED_PROXY_CIDRS} entries"
        )
    for value in settings.trusted_proxy_cidrs:
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError:
            errors.append("trusted_proxy_cidrs contains an invalid IP address or CIDR")
    try:
        parse_upstream_dashboard_endpoint(settings.upstream_dashboard_mcp_url)
    except ValueError:
        errors.append(
            "upstream_dashboard_mcp_url is malformed or lacks a secret-bearing credential"
        )
    if settings.upstream_trust_registry_enabled:
        try:
            UpstreamTrustRegistry(
                enabled=True,
                public_key=settings.upstream_trust_registry_public_key,
            )
        except RegistryValidationError:
            errors.append(
                "upstream_trust_registry_public_key must be a base64 Ed25519 public key when the registry is enabled"
            )
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
        soft_ttl_seconds=settings.dependency_index_soft_ttl_seconds,
        hard_ttl_seconds=settings.dependency_index_hard_ttl_seconds,
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
    INCIDENT_CORRELATION.configure(
        DEPENDENCY_ANALYSIS.require().index,
        HomeAssistantRestClient(settings),
        HomeAssistantWebSocketClient(settings),
        RELIABILITY_ANALYSIS.require().provider,
        secret=settings.access_secret,
        ha_token=settings.ha_token,
        timeout=settings.ha_timeout_seconds,
    )
    HANDOFF_GENERATION.configure(
        governance=GOVERNANCE,
        incident=INCIDENT_CORRELATION.require(),
        dependency_index=DEPENDENCY_ANALYSIS.require().index,
        rest_client=HomeAssistantRestClient(settings),
        health=HEALTH,
        secret=settings.access_secret,
        ha_token=settings.ha_token,
        timeout=settings.ha_timeout_seconds,
    )
    UPSTREAM_DASHBOARD.configure(settings)
    UPSTREAM_READ_GATEWAY.configure(
        settings,
        admission_validator=UPSTREAM_DASHBOARD.validate_read_gateway_catalog,
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
        INCIDENT_CORRELATION,
        HANDOFF_GENERATION,
        UPSTREAM_DASHBOARD,
        UPSTREAM_READ_GATEWAY,
    )
    return gateway


def create_approval_application():
    """Create the private Ingress application after governance is configured."""

    return create_ingress_web_application(GOVERNANCE)


async def _serve(settings: Settings) -> None:
    """Run distinct MCP and Ingress listeners in one supervised process."""

    gateway = create_application(settings)

    mcp_server = uvicorn.Server(
        uvicorn.Config(
            gateway,
            host="0.0.0.0",
            port=settings.port,
            log_level=settings.log_level.lower(),
            access_log=False,
        )
    )
    approval_server = uvicorn.Server(
        uvicorn.Config(
            create_approval_application(),
            host="0.0.0.0",
            port=settings.ingress_port,
            log_level=settings.log_level.lower(),
            access_log=False,
        )
    )
    # Let the MCP server own process signals. The private listener follows its
    # lifecycle so container shutdown cannot leave an independent authority
    # process running.
    approval_server.install_signal_handlers = lambda: None
    mcp_task = asyncio.create_task(mcp_server.serve())
    approval_task = asyncio.create_task(approval_server.serve())
    # Start with the 40 native tools, then keep exact, fail-closed upstream
    # admission under supervision until ha-mcp becomes ready after host boot.
    async def supervise_upstream_reconciliation() -> None:
        await UPSTREAM_READ_GATEWAY.reconcile_until_initialized(
            get_registered_server()
        )
        # Normal admission completes the reconciliation loop. Keep its
        # supervisor pending so only an unexpected exception stops the process.
        await asyncio.Future()

    upstream_reconciliation_task = asyncio.create_task(
        supervise_upstream_reconciliation(),
        name="upstream-read-gateway-reconciliation",
    )
    registry_refresh_task = (
        asyncio.create_task(UPSTREAM_DASHBOARD.refresh_registry_at_startup())
        if settings.upstream_trust_registry_enabled
        else None
    )
    prewarm_task = (
        DEPENDENCY_ANALYSIS.start_prewarm(
            startup_delay_seconds=settings.prewarm_startup_delay_seconds,
            retry_delay_seconds=settings.prewarm_retry_delay_seconds,
        )
        if settings.prewarm_enabled and DEPENDENCY_ANALYSIS.service is not None
        else None
    )
    try:
        done, _ = await asyncio.wait(
            {mcp_task, approval_task, upstream_reconciliation_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        # Either listener ending is a process-level event. A failed private
        # authority listener must never leave a seemingly healthy MCP listener
        # running without its required approval channel.
        for task in done:
            exception = task.exception()
            if exception is not None:
                raise exception
    finally:
        mcp_server.should_exit = True
        approval_server.should_exit = True
        upstream_reconciliation_task.cancel()
        await asyncio.gather(mcp_task, approval_task, return_exceptions=True)
        await asyncio.gather(upstream_reconciliation_task, return_exceptions=True)
        if registry_refresh_task is not None:
            await asyncio.gather(registry_refresh_task, return_exceptions=True)
        await DEPENDENCY_ANALYSIS.shutdown()


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
            "ingress_port": settings.ingress_port,
            "runtime": "home_assistant_addon" if os.environ.get("SUPERVISOR_TOKEN") else "standalone",
            "redaction_enabled": settings.redaction_enabled,
            "upstream_dashboard": {
                "configured": bool(settings.upstream_dashboard_mcp_url),
                "credential_present": bool(
                    parse_upstream_dashboard_endpoint(
                        settings.upstream_dashboard_mcp_url
                    )
                ),
                "trust_registry_enabled": settings.upstream_trust_registry_enabled,
            },
        },
        secret=settings.access_secret,
    )
    asyncio.run(_serve(settings))
