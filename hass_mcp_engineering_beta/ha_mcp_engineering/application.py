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
from .governance.approval_notifications import validate_notification_service
from .dependency import DEPENDENCY_ANALYSIS
from .reliability import RELIABILITY_ANALYSIS
from .impact import CHANGE_IMPACT_ANALYSIS
from .integrity import CONFIGURATION_INTEGRITY_ANALYSIS
from .incident import INCIDENT_CORRELATION
from .handoff import HANDOFF_GENERATION
from .routing import AuthenticatedMcpGateway
from .providers.upstream_dashboard import UPSTREAM_DASHBOARD
from .providers.upstream_read_gateway import UPSTREAM_READ_GATEWAY
from .providers.operational_backup import UPSTREAM_OPERATIONAL_BACKUP
from .providers.operational_lifecycle import (
    UPSTREAM_OPERATIONAL_LIFECYCLE,
)
from .providers.upstream_registry import RegistryValidationError, UpstreamTrustRegistry
from .ha_mcp_readmission.registry import TRUST_ANCHOR_KEY_ID
from .signed_registry import (
    RegistryValidationError as SignedRegistryValidationError,
    TrustAnchorStore,
)
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
    if not validate_notification_service(settings.approval_notification_service):
        errors.append(
            "approval_notification_service must be empty or match "
            "notify.mobile_app_<device>"
        )
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
    if settings.ha_mcp_release_registry_enabled:
        try:
            TrustAnchorStore.from_base64(
                {
                    TRUST_ANCHOR_KEY_ID: (
                        settings.ha_mcp_release_registry_public_key
                    )
                }
            )
        except SignedRegistryValidationError:
            errors.append(
                "ha_mcp_release_registry_public_key must be a base64 Ed25519 public key when the registry is enabled"
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
    gateway = AuthenticatedMcpGateway(
        server.streamable_http_app(),
        settings,
        audit,
        require_initial_catalog_reconciliation=bool(
            parse_upstream_dashboard_endpoint(
                settings.upstream_dashboard_mcp_url
            )
        ),
        execution_readiness=lambda: bool(
            GOVERNANCE.require().f3_runtime
            and GOVERNANCE.require().f3_runtime.health().get(
                "execution_ready"
            )
        ),
    )
    UPSTREAM_OPERATIONAL_BACKUP.configure(settings)
    UPSTREAM_OPERATIONAL_LIFECYCLE.configure(settings)

    def operational_runtime_snapshot():
        from .capabilities import (
            BETA_NATIVE_CAPABILITIES,
            CAPABILITIES,
            dynamic_upstream_capabilities,
        )
        from .version import BUILD_SHA, SERVER_VERSION

        upstream = UPSTREAM_READ_GATEWAY.health_snapshot()
        governance = GOVERNANCE.health_summary()
        audit_state = audit.state()
        dependency = DEPENDENCY_ANALYSIS.health()
        return {
            "server_version": SERVER_VERSION,
            "build_sha": BUILD_SHA,
            "registered_tool_count": (
                len(CAPABILITIES)
                + len(BETA_NATIVE_CAPABILITIES)
                + len(dynamic_upstream_capabilities())
            ),
            "engineering_tool_count": (
                len(CAPABILITIES) + len(BETA_NATIVE_CAPABILITIES)
            ),
            "delegated_tool_count": len(dynamic_upstream_capabilities()),
            "governance_storage_status": (
                "healthy"
                if governance.get("storage_status") == "healthy"
                else str(governance.get("storage_status") or "unavailable")
            ),
            "governance_plan_count": governance.get("total_plans", 0),
            "audit_storage_status": (
                "healthy"
                if audit_state.get("write_failures") == 0
                and audit_state.get("target_configured")
                else "unavailable"
            ),
            "audit_write_failures": audit_state.get("write_failures", 0),
            "dependency_index_state": dependency.get("build_state"),
            "dependency_prewarm_state": dependency.get("prewarm_state"),
            "upstream_version": upstream.get(
                "observed_upstream_server_version"
            ),
            "upstream_protocol": upstream.get("observed_protocol_version"),
            "upstream_catalog_fingerprint": upstream.get(
                "observed_catalog_fingerprint"
            ),
            "upstream_admission_status": upstream.get("admission_status"),
            "fallback_count": upstream.get("fallback_count", 0),
        }

    UPSTREAM_DASHBOARD.configure(settings)
    GOVERNANCE.configure(
        settings,
        audit,
        HomeAssistantRestClient(settings),
        HomeAssistantWebSocketClient(settings),
        UPSTREAM_OPERATIONAL_BACKUP,
        UPSTREAM_OPERATIONAL_LIFECYCLE,
        runtime_snapshot=operational_runtime_snapshot,
        dashboard_provider=UPSTREAM_DASHBOARD,
    )
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
    # Generic pure reads are admitted independently per tool. The dashboard
    # provider retains its own stricter mixed-tool contract and cannot disable
    # unrelated generic reads when only that contract is unavailable.
    UPSTREAM_READ_GATEWAY.configure(settings)
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


async def _supervise_upstream_reconciliation(
    gateway: AuthenticatedMcpGateway,
) -> None:
    """Publish the first stable catalog before accepting MCP sessions."""

    server = get_registered_server()
    initial_snapshot = None
    if getattr(gateway, "initial_catalog_reconciliation_required", False):
        initial_snapshot = (
            await UPSTREAM_READ_GATEWAY.reconcile_until_initialized(server)
        )
        gateway.mark_initial_catalog_reconciled()
    await UPSTREAM_READ_GATEWAY.supervise_reconciliation(
        server,
        initial_snapshot=initial_snapshot,
    )


async def _run_f3_recovery_pass(trigger: str, *, strict: bool = False) -> None:
    """Run the one bounded F3 scheduler plus legacy read-only migration input."""

    logger = get_logger("operational_reconciliation")
    service = GOVERNANCE.service
    if service is None:
        return
    try:
        if service.f3_runtime is not None:
            await service.f3_runtime.recover_once(trigger)
        elif strict:
            raise RuntimeError("F3 runtime is not initialized")
        # Task rehydration validates durable authority and deadlines first.
        # Historical Beta 19 tasks remain under their original read-only
        # authority; this compatibility input never claims an F3 child.
        await service.reconcile_execution_tasks(trigger=trigger)
        await service.reconcile_operational_plans(trigger=trigger)
    except Exception as exc:
        if strict:
            raise
        # A failed readback pass remains represented by the persisted
        # verification-required plan and is retried on the next pass.
        log_event(
            logger,
            logging.WARNING,
            "operational_reconciliation_pass_failed",
            "Operational readback reconciliation will retry.",
            context={
                "trigger": trigger,
                "error_type": type(exc).__name__,
            },
        )


async def _supervise_f3_recovery(*, perform_startup: bool = True) -> None:
    """Own the sole startup and 30-second periodic recovery cadence."""

    if perform_startup:
        await _run_f3_recovery_pass("startup")
    while True:
        await asyncio.sleep(30)
        await _run_f3_recovery_pass("periodic")


# Compatibility call points for tests and external process supervisors. They
# delegate into the same authority and never create another loop.
async def _run_operational_reconciliation_pass(trigger: str) -> None:
    await _run_f3_recovery_pass(trigger)


async def _supervise_operational_reconciliation() -> None:
    await _supervise_f3_recovery()


async def _serve(settings: Settings) -> None:
    """Run distinct MCP and Ingress listeners in one supervised process."""

    gateway = create_application(settings)
    # No public listener exists until stores, registry, ownership, and the
    # initial cheap recovery pass have all succeeded.
    if isinstance(gateway, AuthenticatedMcpGateway):
        await _run_f3_recovery_pass("startup", strict=True)

    notification_manager = (
        GOVERNANCE.service.approval_notifications
        if GOVERNANCE.service is not None
        else None
    )
    notification_task = None
    if notification_manager is not None and notification_manager.configured:
        notification_manager.reconcile_pending(
            GOVERNANCE.require().pending_external_reviews()
        )
        notification_task = asyncio.create_task(
            notification_manager.run(),
            name="approval-notification-worker",
        )

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
    upstream_reconciliation_task = asyncio.create_task(
        _supervise_upstream_reconciliation(gateway),
        name="upstream-read-gateway-reconciliation",
    )
    operational_reconciliation_task = asyncio.create_task(
        _supervise_f3_recovery(perform_startup=False),
        name="f3-central-recovery-coordinator",
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
            {
                mcp_task,
                approval_task,
                upstream_reconciliation_task,
                operational_reconciliation_task,
            },
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
        operational_reconciliation_task.cancel()
        if notification_task is not None:
            notification_task.cancel()
        await asyncio.gather(mcp_task, approval_task, return_exceptions=True)
        await asyncio.gather(upstream_reconciliation_task, return_exceptions=True)
        await asyncio.gather(
            operational_reconciliation_task, return_exceptions=True
        )
        if notification_task is not None:
            await asyncio.gather(notification_task, return_exceptions=True)
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
            "approval_notifications": {
                "configured": bool(settings.approval_notification_service),
                "authority": "none",
            },
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
