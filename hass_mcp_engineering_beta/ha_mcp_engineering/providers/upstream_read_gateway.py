"""Generic, policy-bound delegation for reviewed upstream pure-read tools."""

from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import json
import re
import threading
import time
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator, SchemaError
from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import PrivateAttr

from ..capabilities import replace_dynamic_upstream_capabilities
from ..clients.mcp import DashboardTransportError
from ..clients.rest import HomeAssistantRestClient
from ..clients.upstream_read import (
    BeforeDispatchFailure,
    CatalogValidationFailure,
    McpReadCatalog,
    McpReadGatewayTransport,
)
from ..clients.websocket import HomeAssistantWebSocketClient
from ..configuration import Settings, parse_upstream_dashboard_endpoint
from ..errors import (
    AuthorizationError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..mcp_sdk_compatibility import (
    McpSdkToolRegistry,
    current_listed_catalog_generation,
    install_catalog_generation_gate,
)
from ..models import FailureResponse, SuccessResponse
from ..ha_mcp_readmission import (
    CapabilityAdmissionCoordinator,
    DecisionGeneration,
    DispatchCommit,
    RouteLease,
    UpstreamSurface,
)
from ..ha_mcp_readmission.ha_mcp import (
    HaMcpAdmissionSelection,
    HaMcpAuthorityError,
    HaMcpAuthoritySelector,
    observation_for_catalog,
)
from ..ha_mcp_readmission.registry import (
    MISSING_RELEASE_REFRESH_INTERVAL_SECONDS,
    SignedReleaseRegistry,
)
from ..observability import METRICS
from ..request_context import current_request_id, current_telemetry
from ..sanitization import sanitize_untrusted_data
from ..tool_framework import timing_since
from ..upstream_tool_policy import (
    EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS,
    RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1,
    REVIEWED_UPSTREAM_SERVER,
    ReviewedUpstreamRelease,
    ReviewedUpstreamReleaseRegistry,
    UpstreamToolPolicy,
    UpstreamToolPolicyEntry,
    catalog_fingerprint,
    load_reviewed_upstream_release_registry,
    load_upstream_tool_policy,
    runtime_annotation_fingerprint,
    runtime_contract_field_fingerprints,
    runtime_contract_fingerprint,
    runtime_description_fingerprint,
    schema_fingerprint,
)
from .ha_2026_8_device_compatibility import (
    CompositeDeviceCompatibilityError,
    adapt_ha_get_device_composite_result,
)


PROVIDER_ID = "upstream_read_gateway"
ALIAS_PREFIX = "ha_mcp__"
REVIEWED_PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_PROTOCOLS = frozenset({REVIEWED_PROTOCOL_VERSION})
RECONCILIATION_RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
COMPATIBILITY_REPROBE_INTERVAL_SECONDS = 900.0
MAX_QUARANTINE_RECORDS = 26
MAX_RUNTIME_CONTRACT_DIFF_FIELDS = 16
MAX_STRUCTURED_UPSTREAM_ERROR_BYTES = 16_384
OPERATIONAL_CATALOG_FINGERPRINT_MODEL = "mcp-sorted-full-tool-catalog-v1"
HACS_INFO_RESPONSE_ENVELOPE_MODEL_V1 = (
    "ha-mcp-hacs-info-top-level-success-v1"
)
_REVIEWED_SUCCESS_ENVELOPE_MODELS = {
    (
        "8.1.0",
        REVIEWED_PROTOCOL_VERSION,
        "ha_get_hacs_info",
    ): HACS_INFO_RESPONSE_ENVELOPE_MODEL_V1,
    (
        "8.1.1",
        REVIEWED_PROTOCOL_VERSION,
        "ha_get_hacs_info",
    ): HACS_INFO_RESPONSE_ENVELOPE_MODEL_V1,
    (
        "8.2.0",
        REVIEWED_PROTOCOL_VERSION,
        "ha_get_hacs_info",
    ): HACS_INFO_RESPONSE_ENVELOPE_MODEL_V1,
    (
        "8.4.1",
        REVIEWED_PROTOCOL_VERSION,
        "ha_get_hacs_info",
    ): HACS_INFO_RESPONSE_ENVELOPE_MODEL_V1,
    (
        "8.4.3",
        REVIEWED_PROTOCOL_VERSION,
        "ha_get_hacs_info",
    ): HACS_INFO_RESPONSE_ENVELOPE_MODEL_V1,
}
_TRANSIENT_DISCOVERY_FAILURES = frozenset({"connection_failed", "timeout"})
_STARTUP_ORDERING_FAILURES = frozenset({"endpoint_rejected"})
STARTUP_ORDERING_GRACE_SECONDS = 600.0
_OBSERVED_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_UPSTREAM_VERSION_EVIDENCE = re.compile(
    r"^(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\."
    r"(?:0|[1-9][0-9]{0,3})(?:-[0-9A-Za-z.-]{1,64})?"
    r"(?:\+[0-9A-Za-z.-]{1,64})?$"
)
_ALLOWED_TOOL_DESCRIPTOR_FIELDS = frozenset(
    {
        "name",
        "title",
        "description",
        "inputSchema",
        "outputSchema",
        "annotations",
        "_meta",
    }
)
_ALLOWED_TOOL_META_FIELDS = {
    "fastmcp": frozenset({"tags"}),
    "ha_mcp": frozenset({"llm_api_exposed", "pinned", "policy"}),
}
_OBSERVED_IDENTITY_EVIDENCE = re.compile(r"^[A-Za-z0-9_.+\-]{1,128}$")
_FAILURE_CATEGORIES = frozenset(
    {
        "not_configured",
        "not_initialized",
        "connection_failed",
        "authentication_failed",
        "endpoint_rejected",
        "timeout",
        "protocol_error",
        "invalid_response",
        "response_too_large",
        "upstream_error",
        "server_identity_mismatch",
        "upstream_version_mismatch",
        "unsupported_protocol_version",
        "schema_mismatch",
        "argument_validation",
        "invalid_request",
        "capability_unavailable",
        "entity_not_found",
        "automation_not_found",
        "resource_not_found",
        "prohibited_delegation",
        "sanitization_failed",
        "internal_error",
    }
)
# Exact codes from the compiled, reviewed ha-mcp 7.14.1 profile. They select
# Engineering-owned categories only; upstream messages, metadata, suggestions,
# and retryability claims remain untrusted and are never reflected.
_UPSTREAM_VALIDATION_CODES = frozenset(
    {
        "VALIDATION_FAILED",
        "VALIDATION_INVALID_JSON",
        "VALIDATION_INVALID_PARAMETER",
        "VALIDATION_MISSING_PARAMETER",
    }
)
_UPSTREAM_CAPABILITY_CODES = frozenset({"COMPONENT_NOT_INSTALLED"})
_UPSTREAM_AUTHENTICATION_CODES = frozenset(
    {
        "AUTH_INVALID_TOKEN",
        "AUTH_EXPIRED",
        "AUTH_INSUFFICIENT_PERMISSIONS",
        "WEBSOCKET_NOT_AUTHENTICATED",
    }
)
_UPSTREAM_CONNECTION_CODES = frozenset(
    {
        "CONNECTION_FAILED",
        "WEBSOCKET_DISCONNECTED",
    }
)
_UPSTREAM_TIMEOUT_CODES = frozenset(
    {
        "CONNECTION_TIMEOUT",
        "TIMEOUT_OPERATION",
        "TIMEOUT_WEBSOCKET",
        "TIMEOUT_API_REQUEST",
    }
)
_UPSTREAM_INTERNAL_CODES = frozenset(
    {
        "INTERNAL_ERROR",
        "INTERNAL_UNEXPECTED",
    }
)
# Reviewed domain outcomes are keyed by both the exact admitted tool and the
# exact structured code emitted by pinned ha-mcp 7.14.1.  The same upstream
# code has different meanings for different tools, so codes are never promoted
# globally.  CONFIG_NOT_FOUND and ENTITY_INVALID_ID have no established
# automatic-read emitter in the pinned source and intentionally have no entry.
_UPSTREAM_DOMAIN_OUTCOMES = {
    ("ha_config_get_automation", "RESOURCE_NOT_FOUND"): "automation_not_found",
    ("ha_config_get_calendar_events", "ENTITY_NOT_FOUND"): "entity_not_found",
    ("ha_config_get_category", "RESOURCE_NOT_FOUND"): "resource_not_found",
    ("ha_config_get_label", "RESOURCE_NOT_FOUND"): "resource_not_found",
    ("ha_config_get_scene", "ENTITY_NOT_FOUND"): "entity_not_found",
    ("ha_config_get_script", "RESOURCE_NOT_FOUND"): "resource_not_found",
    ("ha_get_blueprint", "RESOURCE_NOT_FOUND"): "resource_not_found",
    ("ha_get_device", "ENTITY_NOT_FOUND"): "entity_not_found",
    ("ha_get_device", "RESOURCE_NOT_FOUND"): "resource_not_found",
    ("ha_get_entity", "ENTITY_NOT_FOUND"): "entity_not_found",
    ("ha_get_hacs_info", "RESOURCE_NOT_FOUND"): "resource_not_found",
    ("ha_get_skill_guide", "RESOURCE_NOT_FOUND"): "resource_not_found",
    ("ha_get_state", "ENTITY_NOT_FOUND"): "entity_not_found",
    ("ha_get_zone", "RESOURCE_NOT_FOUND"): "resource_not_found",
}
_UPSTREAM_DOMAIN_MESSAGES = {
    ("ha_config_get_automation", "automation_not_found"): (
        "The requested automation configuration was not found."
    ),
    ("ha_config_get_calendar_events", "entity_not_found"): (
        "The requested calendar entity was not found."
    ),
    ("ha_config_get_category", "resource_not_found"): (
        "The requested category configuration was not found."
    ),
    ("ha_config_get_label", "resource_not_found"): (
        "The requested label configuration was not found."
    ),
    ("ha_config_get_scene", "entity_not_found"): (
        "The requested scene configuration was not found."
    ),
    ("ha_config_get_script", "resource_not_found"): (
        "The requested script configuration was not found."
    ),
    ("ha_get_blueprint", "resource_not_found"): (
        "The requested blueprint was not found."
    ),
    ("ha_get_device", "entity_not_found"): (
        "The requested entity was not found or has no associated device."
    ),
    ("ha_get_device", "resource_not_found"): (
        "The requested device was not found."
    ),
    ("ha_get_entity", "entity_not_found"): (
        "The requested entity registry entry was not found."
    ),
    ("ha_get_hacs_info", "resource_not_found"): (
        "The requested HACS repository was not found."
    ),
    ("ha_get_skill_guide", "resource_not_found"): (
        "The requested skill guide resource was not found."
    ),
    ("ha_get_state", "entity_not_found"): (
        "The requested entity state was not found."
    ),
    ("ha_get_zone", "resource_not_found"): (
        "The requested zone configuration was not found."
    ),
}
_EXPECTED_PROVIDER_OUTCOMES = {
    "invalid_request": "invalid_request",
    "capability_unavailable": "unsupported_operation",
    "entity_not_found": "entity_not_found",
    "automation_not_found": "automation_not_found",
    "resource_not_found": "resource_not_found",
}


class ReviewedUpstreamReadTool(Tool):
    """FastMCP tool whose advertised and validated schema is the reviewed schema."""

    _gateway: "UpstreamReadGateway" = PrivateAttr()
    _entry: UpstreamToolPolicyEntry = PrivateAttr()
    _schema: dict[str, Any] = PrivateAttr()
    _admission_generation: int = PrivateAttr()
    _contract_fingerprint: str = PrivateAttr()

    @classmethod
    def build(
        cls,
        *,
        gateway: "UpstreamReadGateway",
        entry: UpstreamToolPolicyEntry,
        exposed_name: str,
        observed_tool: dict[str, Any],
        admission_generation: int,
        contract_fingerprint: str,
    ) -> "ReviewedUpstreamReadTool":
        async def delegated_read(**arguments):
            del arguments
            raise RuntimeError("delegated_read_placeholder_must_not_execute")

        # The public annotation is binary-owned policy, not upstream metadata.
        # The exact schema is reviewed separately; descriptive or annotation
        # content advertised by the remote peer cannot weaken the read boundary.
        annotations = ToolAnnotations(
            title=entry.upstream_name,
            readOnlyHint=entry.reviewed_annotations.read_only,
            destructiveHint=entry.reviewed_annotations.destructive,
            idempotentHint=entry.reviewed_annotations.idempotent,
            openWorldHint=entry.reviewed_annotations.open_world,
        )
        base = Tool.from_function(
            delegated_read,
            name=exposed_name,
            # Publish only the bounded reviewed description from the manifest.
            # The full remote runtime description is admission evidence only;
            # it must not become model-facing instructions.
            description=entry.description,
            annotations=annotations,
        )
        tool = cls(
            fn=base.fn,
            name=base.name,
            description=base.description,
            parameters=deepcopy(observed_tool["inputSchema"]),
            fn_metadata=base.fn_metadata,
            is_async=True,
            context_kwarg=None,
            annotations=annotations,
        )
        tool._gateway = gateway
        tool._entry = entry
        tool._schema = deepcopy(observed_tool["inputSchema"])
        tool._admission_generation = admission_generation
        tool._contract_fingerprint = contract_fingerprint
        return tool

    async def run(
        self,
        arguments: dict[str, Any],
        context: Any = None,
        convert_result: bool = False,
    ) -> Any:
        del context
        listed_generation = current_listed_catalog_generation()
        if (
            listed_generation is not None
            and listed_generation != self._admission_generation
        ):
            return FailureResponse(
                operation=self.name,
                error="UpstreamReadGatewayError",
                error_code="capability_unavailable",
                message=(
                    "The delegated read catalog changed; reconnect or list "
                    "tools again."
                ),
                details={"failure_category": "prohibited_delegation"},
                retryable=True,
                metadata={
                    "provider": PROVIDER_ID,
                    "upstream_dispatch_occurred": False,
                    "fallback": "none",
                    "fallback_occurred": False,
                },
                timing=timing_since(time.perf_counter()),
                request_id=current_request_id(),
            ).to_json(8_000)
        result = await self._gateway.execute(
            exposed_name=self.name,
            arguments=arguments,
            reviewed_schema=self._schema,
            policy_entry=self._entry,
            admission_generation=self._admission_generation,
            contract_fingerprint=self._contract_fingerprint,
        )
        if convert_result:
            return self.fn_metadata.convert_result(result)
        return result


AdmissionValidator = Callable[[McpReadCatalog], None]


@dataclass(frozen=True)
class _ContractDecision:
    entry: UpstreamToolPolicyEntry
    observed_tool: dict[str, Any]
    accepted: bool
    reason: str | None
    expected_fingerprint: str
    observed_fingerprint: str
    expected_runtime_contract_fingerprint: str
    observed_runtime_contract_fingerprint: str
    runtime_contract_fingerprint_model: str
    runtime_contract_diff_fields: tuple[str, ...]
    runtime_contract_diff_summary: str
    raw_runtime_contract_diff_fields: tuple[str, ...]


@dataclass(frozen=True)
class _CatalogEvaluation:
    matched: tuple[_ContractDecision, ...]
    missing: tuple[str, ...]
    quarantined: tuple[dict[str, Any], ...]
    quarantine_reason_counts: dict[str, int]
    blocked: tuple[dict[str, str], ...]
    unreviewed: tuple[str, ...]


def _apply_readmission_decisions(
    evaluation: _CatalogEvaluation,
    generation: DecisionGeneration,
) -> _CatalogEvaluation:
    """Project the production authority decision into gateway accounting.

    The existing contract comparison remains the descriptor authority.  This
    second projection can only remove a previously matched read when signed
    authority does not cover that exact binary-owned capability contract.
    """

    matched: list[_ContractDecision] = []
    quarantined = list(evaluation.quarantined)
    reasons = Counter(evaluation.quarantine_reason_counts)
    for item in evaluation.matched:
        decision = generation.decision_for(item.entry.upstream_name)
        if decision is not None and decision.disposition.admitted:
            matched.append(item)
            continue
        reason = (
            decision.reason_code
            if decision is not None
            else "readmission_decision_missing"
        )
        reasons[reason] += 1
        quarantined.append(_quarantine_record(item, reason=reason))
    return _CatalogEvaluation(
        matched=tuple(matched),
        missing=evaluation.missing,
        quarantined=tuple(
            sorted(quarantined, key=lambda item: item["upstream_name"])
        ),
        quarantine_reason_counts=dict(sorted(reasons.items())),
        blocked=evaluation.blocked,
        unreviewed=evaluation.unreviewed,
    )


@dataclass(frozen=True)
class _AdmittedRoute:
    entry: UpstreamToolPolicyEntry
    observed_tool: dict[str, Any]
    generation: int
    contract_fingerprint: str
    runtime_description_fingerprint: str
    runtime_annotation_fingerprint: str
    runtime_output_schema_fingerprint: str
    runtime_contract_fingerprint: str | None
    runtime_contract_field_fingerprints: tuple[tuple[str, str], ...]
    runtime_contract_fingerprint_model: str
    server_version: str
    adapter_version: str
    protocol_version: str
    profile_id: str | None = None
    adapter_id: str | None = None
    authority_source: str = "compiled_exact"
    authority_token: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class _HeldCanaryRoute:
    """Reviewed held-tool evidence captured by the active exact admission."""

    entry: UpstreamToolPolicyEntry
    observed_tool: dict[str, Any] | None
    generation: int
    compatibility_entry_id: str
    decision: _ContractDecision | None
    rejection_reason: str | None
    runtime_description_fingerprint: str
    runtime_annotation_fingerprint: str
    runtime_output_schema_fingerprint: str
    runtime_contract_fingerprint: str
    runtime_contract_field_fingerprints: tuple[tuple[str, str], ...]
    runtime_contract_fingerprint_model: str
    server_name: str
    server_version: str
    protocol_version: str


@dataclass
class _RouteLease:
    """One call's immutable route binding and dispatch linearization state."""

    route: _AdmittedRoute
    validator_ran: bool = False
    dispatch_committed: bool = False
    readmission_lease: RouteLease | None = None
    readmission_commit: DispatchCommit | None = None


class UpstreamReadGateway:
    """Discover and register exact policy-approved pure reads through one provider."""

    def __init__(self) -> None:
        self._transport: McpReadGatewayTransport | Any | None = None
        self._settings: Settings | None = None
        self._known_secrets: tuple[str, ...] = ()
        self._ha_rest_client: HomeAssistantRestClient | Any | None = None
        self._ha_websocket_client: HomeAssistantWebSocketClient | Any | None = None
        self._policy: UpstreamToolPolicy | None = None
        self._release_registry: (
            ReviewedUpstreamReleaseRegistry | None
        ) = None
        self._signed_release_registry: SignedReleaseRegistry | None = None
        self._readmission_selector: HaMcpAuthoritySelector | None = None
        self._readmission_coordinator: (
            CapabilityAdmissionCoordinator | None
        ) = None
        self._readmission_audit: tuple[dict[str, Any], ...] = ()
        self._active_release: ReviewedUpstreamRelease | None = None
        self._admission_validator: AdmissionValidator | None = None
        self._registered_server: Any = None
        self._registered_tool_registry: McpSdkToolRegistry | None = None
        self._registered_names: set[str] = set()
        self._exposed: dict[str, _AdmittedRoute] = {}
        self._held_canaries: dict[str, _HeldCanaryRoute] = {}
        self._dynamic_capabilities: tuple[dict[str, Any], ...] = ()
        self._admission_generation = 0
        self._live_observation_epoch = 0
        self._latest_live_contract_epoch = 0
        self._latest_live_contract_token: str | None = None
        self._stale_reprobe_retry_armed = False
        self._discovery_in_progress = False
        self._reprobe_event = asyncio.Event()
        self._missing_release_retry_handle: asyncio.TimerHandle | None = None
        self._initialize_lock = asyncio.Lock()
        self._reconciliation_lock = asyncio.Lock()
        self._lock = threading.RLock()
        self._state = self._empty_state()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "configured": False,
            "initialized": False,
            "generic_delegation_available": False,
            "admission_complete": False,
            "upstream_server_name": None,
            "upstream_server_version": None,
            "observed_upstream_server_name": None,
            "observed_upstream_server_version": None,
            "observed_protocol_version": None,
            "observed_identity_status": "not_observed",
            "reviewed_upstream_version": None,
            "reviewed_supported_versions": [],
            "reviewed_release_count": 0,
            "selected_compatibility_entry_id": None,
            "reviewed_source_commit": None,
            "reviewed_image_index_digest": None,
            "reviewed_architecture_image_digests": {},
            "reviewed_addon_artifact_digests": {},
            "reviewed_image_revision": None,
            "reviewed_image_revision_authoritative": False,
            "strict_full_contract_fingerprint": None,
            "strict_full_contract_fingerprint_model": None,
            "reviewed_strict_full_contract_fingerprint": None,
            "observed_strict_full_contract_fingerprint": None,
            "runtime_contract_fingerprint_model": None,
            "reviewed_allowed_protocol_versions": [],
            "runtime_artifact_provenance_observed": False,
            "runtime_source_commit_observed": None,
            "runtime_image_index_digest_observed": None,
            "runtime_architecture_image_digest_observed": None,
            "runtime_image_revision_observed": None,
            "runtime_artifact_provenance_status": (
                "unobserved_by_mcp_discovery"
            ),
            "catalog_comparison_status": "not_observed",
            "dashboard_attestation_status": "not_observed",
            "version_status": "not_observed",
            "admission_status": "unavailable",
            "protocol_version": None,
            "catalog_fingerprint": None,
            "observed_catalog_fingerprint": None,
            "reviewed_catalog_fingerprint": None,
            "operational_catalog_fingerprint_model": (
                OPERATIONAL_CATALOG_FINGERPRINT_MODEL
            ),
            "catalog_diff_field_counts": {},
            "upstream_advertised_tool_count": 0,
            "observed_advertised_tool_count": 0,
            "reviewed_policy_entry_count": 0,
            "reviewed_accounted_tool_count": 0,
            "reviewed_tool_accounting_valid": False,
            "automatic_read_count": 0,
            "held_read_count": 0,
            "held_tools": [],
            "live_canary_required_tools": [],
            "static_review_completed": False,
            "reviewed_automatic_read_count": 0,
            "exact_matched_automatic_read_count": 0,
            "dynamically_exposed_count": 0,
            "collision_count": 0,
            "blocked_mixed_tool_count": 0,
            "blocked_write_count": 0,
            "blocked_physical_high_risk_count": 0,
            "prohibited_count": 0,
            "unsupported_count": 0,
            "schema_mismatch_count": 0,
            "schema_mismatched_automatic_read_count": 0,
            "description_semantics_mismatch_count": 0,
            "annotation_mismatch_count": 0,
            "output_contract_mismatch_count": 0,
            "runtime_contract_mismatch_count": 0,
            "quarantined_automatic_read_count": 0,
            "quarantine_reason_counts": {},
            "quarantined_tools": [],
            "quarantine_truncated": False,
            "missing_reviewed_read_count": 0,
            "missing_automatic_read_count": 0,
            "missing_tools": [],
            "accounted_automatic_read_count": 0,
            "automatic_read_accounting_valid": False,
            "unreviewed_tool_count": 0,
            "unreviewed_observed_tool_count": 0,
            "unreviewed_tools": [],
            "unreviewed_tools_truncated": False,
            "blocked_classification_counts": {
                "mixed_or_requires_wrapper": 0,
                "persistent_write": 0,
                "physical_or_high_risk_action": 0,
                "prohibited": 0,
                "unsupported": 0,
                "held_for_canary": 0,
            },
            "reviewed_stock_catalog_tool_count": 0,
            "reviewed_stock_catalog_fingerprint": None,
            "observed_catalog_matches_reviewed_stock_fixture": False,
            "prohibited_delegation_attempts": 0,
            "fallback_count": 0,
            "last_failure_category": None,
            "last_discovery_failure_category": None,
            "last_call_failure_category": None,
            "failure_counts": Counter(),
            "last_catalog_refresh_at": None,
            "last_discovery_stable": False,
            "compatibility_status": "unavailable",
            "last_compatible_version": None,
            "compatibility_registry_status": "compiled_reviewed_release_registry",
            "recommended_action": "Wait for the configured upstream provider.",
            "reconciliation_active": False,
            "reconciliation_status": "idle",
            "discovery_attempt_count": 0,
            "retry_count": 0,
            "next_retry_delay_seconds": None,
            "last_discovery_attempt_at": None,
            "compatibility_reprobe_interval_seconds": (
                COMPATIBILITY_REPROBE_INTERVAL_SECONDS
            ),
            "last_compatibility_reprobe_at": None,
            "next_compatibility_reprobe_at": None,
            "compatibility_reprobe_status": "idle",
            "compatibility_reprobe_trigger_count": 0,
            "stale_reprobe_retry_armed": False,
            "exposed_tools": [],
            "collision_mappings": [],
            "blocked_tools": [],
        }

    def configure(
        self,
        settings: Settings,
        *,
        transport: McpReadGatewayTransport | Any | None = None,
        policy: UpstreamToolPolicy | None = None,
        release_registry: (
            ReviewedUpstreamReleaseRegistry | None
        ) = None,
        signed_release_registry: SignedReleaseRegistry | None = None,
        admission_validator: AdmissionValidator | None = None,
        ha_rest_client: HomeAssistantRestClient | Any | None = None,
        ha_websocket_client: HomeAssistantWebSocketClient | Any | None = None,
    ) -> None:
        self._remove_registered_tools()
        replace_dynamic_upstream_capabilities((), self._empty_state())
        endpoint = parse_upstream_dashboard_endpoint(settings.upstream_dashboard_mcp_url)
        self._settings = settings
        self._ha_rest_client = ha_rest_client or HomeAssistantRestClient(settings)
        self._ha_websocket_client = (
            ha_websocket_client or HomeAssistantWebSocketClient(settings)
        )
        self._known_secrets = tuple(
            dict.fromkeys(
                item
                for item in (
                    settings.access_secret,
                    settings.ha_token,
                    *(endpoint.secret_values if endpoint else ()),
                )
                if item
            )
        )
        if policy is not None and release_registry is not None:
            raise ValueError(
                "policy and release_registry are mutually exclusive"
            )
        self._release_registry = (
            None
            if policy is not None
            else release_registry
            or load_reviewed_upstream_release_registry()
        )
        self._policy = (
            policy
            if policy is not None
            else self._release_registry.default_release.policy
        )
        self._active_release = None
        self._signed_release_registry = None
        self._readmission_selector = None
        self._readmission_coordinator = None
        self._readmission_audit = ()
        if self._release_registry is not None:
            candidate_signed_registry = (
                signed_release_registry
                or SignedReleaseRegistry(
                    enabled=(
                        settings.ha_mcp_release_registry_enabled
                    ),
                    public_key=(
                        settings.ha_mcp_release_registry_public_key
                    ),
                )
            )
            if candidate_signed_registry.enabled:
                self._signed_release_registry = candidate_signed_registry
                self._readmission_selector = HaMcpAuthoritySelector(
                    self._release_registry,
                    candidate_signed_registry,
                )
        self._admission_validator = admission_validator
        self._admission_generation = 0
        self._live_observation_epoch = 0
        self._latest_live_contract_epoch = 0
        self._latest_live_contract_token = None
        self._stale_reprobe_retry_armed = False
        self._discovery_in_progress = False
        if self._missing_release_retry_handle is not None:
            self._missing_release_retry_handle.cancel()
            self._missing_release_retry_handle = None
        self._reprobe_event.clear()
        self._transport = (
            transport
            if endpoint and transport is not None
            else McpReadGatewayTransport(
                endpoint.url,
                timeout_seconds=settings.ha_timeout_seconds,
                client_version=_server_version(),
                retain_session=(
                    settings.ha_mcp_release_registry_enabled
                ),
            )
            if endpoint
            else None
        )
        counts = self._policy.classification_counts
        supported_versions = (
            list(self._release_registry.supported_versions)
            if self._release_registry is not None
            else [self._policy.reviewed_upstream_version]
        )
        self._state = self._empty_state()
        self._state.update(
            {
                "configured": bool(endpoint),
                "reviewed_upstream_version": self._policy.reviewed_upstream_version,
                "reviewed_supported_versions": supported_versions,
                "reviewed_release_count": len(supported_versions),
                "reviewed_policy_entry_count": len(self._policy.tools),
                "automatic_read_count": counts["automatic_read"],
                "held_read_count": counts.get("held_for_canary", 0),
                "held_tools": sorted(
                    entry.upstream_name
                    for entry in self._policy.tools
                    if entry.classification == "held_for_canary"
                ),
                "live_canary_required_tools": sorted(
                    entry.upstream_name
                    for entry in self._policy.tools
                    if entry.classification == "held_for_canary"
                ),
                "static_review_completed": True,
                "reviewed_automatic_read_count": counts["automatic_read"],
                "blocked_mixed_tool_count": counts["mixed_or_requires_wrapper"],
                "blocked_write_count": counts["persistent_write"],
                "blocked_physical_high_risk_count": counts[
                    "physical_or_high_risk_action"
                ],
                "prohibited_count": counts["prohibited"],
                "unsupported_count": counts["unsupported"],
                "blocked_classification_counts": {
                    name: counts.get(name, 0)
                    for name in (
                        "mixed_or_requires_wrapper",
                        "persistent_write",
                        "physical_or_high_risk_action",
                        "prohibited",
                        "unsupported",
                        "held_for_canary",
                    )
                    if name != "held_for_canary"
                    or counts.get(name, 0)
                },
                "reviewed_stock_catalog_tool_count": (
                    self._policy.reviewed_stock_catalog_tool_count
                ),
                "reviewed_stock_catalog_fingerprint": (
                    self._policy.reviewed_stock_catalog_fingerprint
                ),
            }
        )
        replace_dynamic_upstream_capabilities((), self.health_snapshot())

    async def initialize(self, server: Any) -> dict[str, Any]:
        """Run one admission attempt without overlapping registry mutation."""

        async with self._initialize_lock:
            with self._lock:
                self._discovery_in_progress = True
            try:
                return await self._initialize_once(server)
            finally:
                with self._lock:
                    self._discovery_in_progress = False

    async def _initialize_once(self, server: Any) -> dict[str, Any]:
        """Discover once and transactionally replace this provider's dynamic tools."""

        registry = McpSdkToolRegistry(server)
        if self._readmission_selector is not None:
            install_catalog_generation_gate(
                server,
                self._client_catalog_generation_snapshot,
            )
        self._registered_server = server
        self._registered_tool_registry = registry
        with self._lock:
            discovery_epoch = self._live_observation_epoch
            self._state.update(
                {
                    "reconciliation_status": (
                        "probing" if self._state["reconciliation_active"] else "idle"
                    ),
                    "next_retry_delay_seconds": None,
                    "last_discovery_attempt_at": _utc_now(),
                    "last_discovery_stable": False,
                }
            )
            self._state["discovery_attempt_count"] += 1
        replace_dynamic_upstream_capabilities(
            self._dynamic_capabilities, self.health_snapshot()
        )
        if not self._transport or not self._policy:
            self._record_failure(
                "not_configured", disable_delegation=True, discovery=True
            )
            self._remove_registered_tools()
            replace_dynamic_upstream_capabilities((), self.health_snapshot())
            return self.health_snapshot()

        catalog: McpReadCatalog | None = None
        identity_validated = False
        try:
            if self._signed_release_registry is not None:
                await self._signed_release_registry.refresh_if_due()
            catalog = await self._transport.discover()
            try:
                (
                    selected_policy,
                    selected_release,
                    readmission_selection,
                ) = self._validate_identity(
                    catalog.server_name,
                    catalog.server_version,
                    catalog.protocol_version,
                )
            except DashboardTransportError as initial_identity_error:
                signed_registry = self._signed_release_registry
                if (
                    signed_registry is None
                    or initial_identity_error.category
                    != "upstream_version_mismatch"
                ):
                    raise
                await signed_registry.refresh_for_missing_release(
                    server_name=catalog.server_name,
                    version=catalog.server_version,
                )
                try:
                    (
                        selected_policy,
                        selected_release,
                        readmission_selection,
                    ) = self._validate_identity(
                        catalog.server_name,
                        catalog.server_version,
                        catalog.protocol_version,
                    )
                except DashboardTransportError:
                    self._arm_missing_release_retry(
                        signed_registry.missing_release_retry_delay(
                            server_name=catalog.server_name,
                            version=catalog.server_version,
                        )
                    )
                    raise
            identity_validated = True
            if self._admission_validator is not None:
                self._admission_validator(catalog)
            reviewed_contracts = (
                selected_release.tool_contracts_by_name
                if selected_release is not None
                else None
            )
            evaluation = self._validate_catalog(
                catalog,
                policy=selected_policy,
                reviewed_contracts=reviewed_contracts,
                require_exact_order=(
                    readmission_selection is None
                    or readmission_selection.authority_source.value
                    == "compiled_exact"
                ),
                runtime_contract_fingerprint_model=(
                    selected_release.runtime_contract_fingerprint_model
                    if selected_release is not None
                    else RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1
                ),
            )
            if readmission_selection is not None:
                observation = observation_for_catalog(
                    catalog,
                    readmission_selection,
                )
                if self._readmission_coordinator is None:
                    self._readmission_coordinator = (
                        CapabilityAdmissionCoordinator(
                            (readmission_selection.profile,)
                        )
                    )
                else:
                    self._readmission_coordinator.replace_surface_profile(
                        readmission_selection.profile
                    )
                readmission_result = (
                    self._readmission_coordinator.reconcile(
                        observation,
                        readmission_selection.authority,
                    )
                )
                if (
                    not readmission_result.published
                    or readmission_result.generation is None
                ):
                    raise DashboardTransportError(
                        "upstream_version_mismatch"
                    )
                generation = (
                    readmission_result.generation.generation
                )
                evaluation = _apply_readmission_decisions(
                    evaluation,
                    readmission_result.generation,
                )
                audit = self._readmission_coordinator.audit_projection(
                    readmission_result
                )
                audit.update(
                    {
                        "authority_source": (
                            readmission_selection.authority_source.value
                        ),
                        "profile_id": (
                            readmission_selection.profile.profile_id
                        ),
                        "adapter_id": (
                            readmission_selection.profile.adapter_id
                        ),
                    }
                )
                self._readmission_audit = (
                    self._readmission_audit + (audit,)
                )[-8:]
            else:
                generation = self._admission_generation + 1
            candidate_contract_token = _catalog_contract_token(
                catalog, evaluation
            )
            if readmission_selection is not None:
                candidate_contract_token = schema_fingerprint(
                    {
                        "catalog_contract_token": (
                            candidate_contract_token
                        ),
                        "authority_token": (
                            readmission_selection.authority_token
                        ),
                        "generation": generation,
                    }
                )
            observed_fingerprint = _safe_catalog_fingerprint(
                list(catalog.tools)
            )
            observed_strict_fingerprint = _safe_strict_catalog_fingerprint(
                list(catalog.tools)
            )
            catalog_diff_field_counts = _catalog_diff_field_counts(
                list(catalog.tools), reviewed_contracts
            )
            base_names = set(registry.snapshot()).difference(
                self._registered_names
            )
            reviewed_descriptions = (
                selected_policy.reviewed_runtime_description_fingerprints_by_name
            )
            reviewed_annotations = (
                selected_policy.reviewed_runtime_annotation_fingerprints_by_name
            )
            reviewed_output_schemas = (
                selected_policy.reviewed_runtime_output_schema_fingerprints_by_name
            )
            exposed: dict[str, _AdmittedRoute] = {}
            dynamic_tools: dict[str, ReviewedUpstreamReadTool] = {}
            capabilities: list[dict[str, Any]] = []
            collisions: list[dict[str, str]] = []
            for decision in evaluation.matched:
                entry = decision.entry
                tool = decision.observed_tool
                exposed_name = entry.exposed_name
                if exposed_name in base_names:
                    exposed_name = f"{ALIAS_PREFIX}{entry.upstream_name}"
                    if exposed_name in base_names or exposed_name in exposed:
                        raise DashboardTransportError("schema_mismatch")
                    collisions.append(
                        {
                            "upstream_name": entry.upstream_name,
                            "exposed_name": exposed_name,
                        }
                    )
                dynamic_tool = ReviewedUpstreamReadTool.build(
                    gateway=self,
                    entry=entry,
                    exposed_name=exposed_name,
                    observed_tool=tool,
                    admission_generation=generation,
                    contract_fingerprint=decision.expected_fingerprint,
                )
                dynamic_tools[exposed_name] = dynamic_tool
                exposed[exposed_name] = _AdmittedRoute(
                    entry=entry,
                    observed_tool=tool,
                    generation=generation,
                    contract_fingerprint=decision.expected_fingerprint,
                    runtime_description_fingerprint=(
                        reviewed_descriptions[entry.upstream_name]
                    ),
                    runtime_annotation_fingerprint=(
                        reviewed_annotations[entry.upstream_name]
                    ),
                    runtime_output_schema_fingerprint=(
                        reviewed_output_schemas[entry.upstream_name]
                    ),
                    runtime_contract_fingerprint=(
                        reviewed_contracts[entry.upstream_name]
                        .runtime_contract_fingerprint
                        if reviewed_contracts is not None
                        else None
                    ),
                    runtime_contract_field_fingerprints=(
                        reviewed_contracts[entry.upstream_name]
                        .runtime_contract_field_fingerprints
                        if reviewed_contracts is not None
                        else ()
                    ),
                    runtime_contract_fingerprint_model=(
                        selected_release.runtime_contract_fingerprint_model
                        if selected_release is not None
                        else RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1
                    ),
                    server_version=catalog.server_version,
                    adapter_version=(
                        readmission_selection.binary_release.version
                        if readmission_selection is not None
                        else catalog.server_version
                    ),
                    protocol_version=catalog.protocol_version,
                    profile_id=(
                        readmission_selection.profile.profile_id
                        if readmission_selection is not None
                        else None
                    ),
                    adapter_id=(
                        readmission_selection.profile.adapter_id
                        if readmission_selection is not None
                        else None
                    ),
                    authority_source=(
                        readmission_selection.authority_source.value
                        if readmission_selection is not None
                        else "compiled_exact"
                    ),
                    authority_token=(
                        readmission_selection.authority_token
                        if readmission_selection is not None
                        else None
                    ),
                    session_id=(
                        observation.session_id
                        if readmission_selection is not None
                        else None
                    ),
                )
                capabilities.append(
                    {
                        "tool": exposed_name,
                        "upstream_tool": entry.upstream_name,
                        "status": "delegated",
                        "category": "upstream_read_gateway",
                        "risk": "read",
                        "operation_class": "automatic_read",
                        "provider": PROVIDER_ID,
                        "fallback": "none",
                        "schema_fingerprint": entry.input_schema_fingerprint,
                        "contract_fingerprint": decision.expected_fingerprint,
                        "admission_generation": generation,
                        "collision": exposed_name != entry.upstream_name,
                    }
                )
            held_canaries = self._build_held_canary_routes(
                catalog=catalog,
                policy=selected_policy,
                release=(
                    selected_release
                    if readmission_selection is None
                    or readmission_selection.authority_source.value
                    == "compiled_exact"
                    else None
                ),
                generation=generation,
            )
            full_admission = len(exposed) == selected_policy.classification_counts[
                "automatic_read"
            ]
            compatibility_status = (
                "exact"
                if full_admission
                else "partial"
                if exposed
                else "incompatible"
            )
            admission_status = (
                "admitted_exact"
                if compatibility_status == "exact"
                else "partially_admitted"
                if compatibility_status == "partial"
                else "blocked_incompatible_upstream"
            )
            # Publication is a short copy-on-write registry transaction. It
            # must never wait for delegated network I/O.
            with self._lock:
                epoch_changed = (
                    discovery_epoch != self._live_observation_epoch
                )
                newer_live_catalog_matches = (
                    self._latest_live_contract_epoch
                    > discovery_epoch
                    and self._latest_live_contract_token
                    == candidate_contract_token
                )
                stale_discovery = (
                    epoch_changed and not newer_live_catalog_matches
                )
                if stale_discovery:
                    immediate_retry = (
                        not self._stale_reprobe_retry_armed
                    )
                    self._stale_reprobe_retry_armed = True
                    self._state.update(
                        {
                            "last_discovery_stable": False,
                            "reconciliation_status": (
                                "reprobe_requested"
                            ),
                            "compatibility_reprobe_status": (
                                "triggered"
                                if immediate_retry
                                else "waiting"
                            ),
                            "next_compatibility_reprobe_at": None,
                            "stale_reprobe_retry_armed": True,
                            "recommended_action": (
                                "A newer live contract observation "
                                "superseded this discovery; reconcile "
                                "again before publishing it."
                            ),
                        }
                    )
                    if immediate_retry:
                        self._reprobe_event.set()
                    else:
                        # One immediate retry is sufficient. Continued catalog
                        # churn must fall back to the bounded slow cadence
                        # instead of creating a discovery loop.
                        self._reprobe_event.clear()
                    replace_dynamic_upstream_capabilities(
                        self._dynamic_capabilities,
                        self.health_snapshot(),
                    )
                    return self.health_snapshot()
                self._publish_discovery_generation(
                    server=server,
                    dynamic_tools=dynamic_tools,
                    exposed=exposed,
                    held_canaries=held_canaries,
                    capabilities=tuple(capabilities),
                    generation=generation,
                    catalog=catalog,
                    evaluation=evaluation,
                    observed_fingerprint=observed_fingerprint,
                    observed_strict_fingerprint=(
                        observed_strict_fingerprint
                    ),
                    catalog_diff_field_counts=catalog_diff_field_counts,
                    collisions=collisions,
                    full_admission=full_admission,
                    compatibility_status=compatibility_status,
                    admission_status=admission_status,
                    policy=selected_policy,
                    release=selected_release,
                    readmission_selection=readmission_selection,
                )
            replace_dynamic_upstream_capabilities(
                self._dynamic_capabilities, self.health_snapshot()
            )
            return self.health_snapshot()
        except DashboardTransportError as exc:
            transient = exc.category in (
                _TRANSIENT_DISCOVERY_FAILURES
                | _STARTUP_ORDERING_FAILURES
            )
            return await self._finish_discovery_failure(
                category=exc.category,
                transient=transient,
                catalog=catalog,
                identity_validated=identity_validated,
                discovery_epoch=discovery_epoch,
            )
        except Exception:
            return await self._finish_discovery_failure(
                category="internal_error",
                transient=False,
                catalog=catalog,
                identity_validated=identity_validated,
                discovery_epoch=discovery_epoch,
            )

    def _client_catalog_generation_snapshot(
        self,
    ) -> tuple[int | None, tuple[str, ...]]:
        with self._lock:
            generations = {
                route.generation for route in self._exposed.values()
            }
            generation = (
                next(iter(generations)) if len(generations) == 1 else None
            )
            return generation, tuple(sorted(self._exposed))

    def _arm_missing_release_retry(self, delay: float | None) -> None:
        if delay is None:
            return
        if self._missing_release_retry_handle is not None:
            self._missing_release_retry_handle.cancel()
        loop = asyncio.get_running_loop()

        def trigger() -> None:
            self._missing_release_retry_handle = None
            self._reprobe_event.set()

        self._missing_release_retry_handle = loop.call_later(
            max(0.01, min(MISSING_RELEASE_REFRESH_INTERVAL_SECONDS, delay)),
            trigger,
        )

    def _publish_discovery_generation(
        self,
        *,
        server: Any,
        dynamic_tools: dict[str, ReviewedUpstreamReadTool],
        exposed: dict[str, _AdmittedRoute],
        held_canaries: dict[str, _HeldCanaryRoute],
        capabilities: tuple[dict[str, Any], ...],
        generation: int,
        catalog: McpReadCatalog,
        evaluation: _CatalogEvaluation,
        observed_fingerprint: str | None,
        observed_strict_fingerprint: str | None,
        catalog_diff_field_counts: dict[str, int],
        collisions: list[dict[str, str]],
        full_admission: bool,
        compatibility_status: str,
        admission_status: str,
        policy: UpstreamToolPolicy,
        release: ReviewedUpstreamRelease | None,
        readmission_selection: HaMcpAdmissionSelection | None,
    ) -> None:
        """Publish one copy-on-write route generation under the state lock."""

        automatic_count = policy.classification_counts[
            "automatic_read"
        ]
        accounted = (
            len(evaluation.matched)
            + len(evaluation.missing)
            + len(evaluation.quarantined)
        )
        reason_counts = evaluation.quarantine_reason_counts
        self._replace_registered_tools(server, dynamic_tools)
        self._registered_names = set(dynamic_tools)
        self._exposed = dict(exposed)
        self._held_canaries = dict(held_canaries)
        self._dynamic_capabilities = capabilities
        self._admission_generation = generation
        self._policy = policy
        self._active_release = release
        readmission_state = (
            {
                "readmission_authority_source": (
                    readmission_selection.authority_source.value
                ),
                "readmission_profile_id": (
                    readmission_selection.profile.profile_id
                ),
                "readmission_adapter_id": (
                    readmission_selection.profile.adapter_id
                ),
                "readmission_generation": generation,
            }
            if readmission_selection is not None
            else {}
        )
        with self._lock:
            self._state.update(
                {
                    "initialized": True,
                    "generic_delegation_available": bool(exposed),
                    "admission_complete": full_admission,
                    "upstream_server_name": catalog.server_name[:128],
                    "upstream_server_version": catalog.server_version[:128],
                    "observed_upstream_server_name": (
                        self._safe_identity_evidence(
                            catalog.server_name
                        )
                    ),
                    "observed_upstream_server_version": (
                        self._safe_version_evidence(
                            catalog.server_version
                        )
                    ),
                    "observed_protocol_version": (
                        self._safe_identity_evidence(
                            catalog.protocol_version
                        )
                    ),
                    "observed_identity_status": "accepted",
                    "reviewed_upstream_version": (
                        policy.reviewed_upstream_version
                    ),
                    "selected_compatibility_entry_id": (
                        readmission_selection.compatibility_entry_id
                        if readmission_selection is not None
                        else release.entry_id
                        if release is not None
                        else None
                    ),
                    **readmission_state,
                    "reviewed_source_commit": (
                        readmission_selection.signed_entry.source_commit
                        if readmission_selection is not None
                        and readmission_selection.signed_entry is not None
                        else release.source_commit
                        if release is not None
                        else policy.reviewed_source_commit
                    ),
                    "reviewed_image_index_digest": (
                        readmission_selection.signed_entry.image_index_digest
                        if readmission_selection is not None
                        and readmission_selection.signed_entry is not None
                        else release.image_index_digest
                        if release is not None
                        else None
                    ),
                    "reviewed_architecture_image_digests": (
                        dict(
                            readmission_selection.signed_entry
                            .architecture_image_digests
                        )
                        if readmission_selection is not None
                        and readmission_selection.signed_entry is not None
                        else release.architecture_image_digests_by_platform
                        if release is not None
                        else {}
                    ),
                    "reviewed_addon_artifact_digests": (
                        {}
                        if readmission_selection is not None
                        and readmission_selection.signed_entry is not None
                        else release.addon_artifact_digests_by_platform
                        if release is not None
                        else {}
                    ),
                    "reviewed_image_revision": (
                        readmission_selection.signed_entry.image_revision
                        if readmission_selection is not None
                        and readmission_selection.signed_entry is not None
                        else release.image_revision
                        if release is not None
                        else None
                    ),
                    "reviewed_image_revision_authoritative": False,
                    "strict_full_contract_fingerprint": (
                        None
                        if readmission_selection is not None
                        and readmission_selection.signed_entry is not None
                        else release.strict_full_contract_fingerprint
                        if release is not None
                        else None
                    ),
                    "strict_full_contract_fingerprint_model": (
                        None
                        if readmission_selection is not None
                        and readmission_selection.signed_entry is not None
                        else release.strict_full_contract_fingerprint_model
                        if release is not None
                        else None
                    ),
                    "reviewed_strict_full_contract_fingerprint": (
                        None
                        if readmission_selection is not None
                        and readmission_selection.signed_entry is not None
                        else release.strict_full_contract_fingerprint
                        if release is not None
                        else None
                    ),
                    "observed_strict_full_contract_fingerprint": (
                        observed_strict_fingerprint
                    ),
                    "runtime_contract_fingerprint_model": (
                        release.runtime_contract_fingerprint_model
                        if release is not None
                        else RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1
                    ),
                    "reviewed_allowed_protocol_versions": (
                        list(release.allowed_protocol_versions)
                        if release is not None
                        else [REVIEWED_PROTOCOL_VERSION]
                    ),
                    "runtime_artifact_provenance_observed": False,
                    "runtime_source_commit_observed": None,
                    "runtime_image_index_digest_observed": None,
                    "runtime_architecture_image_digest_observed": None,
                    "runtime_image_revision_observed": None,
                    "runtime_artifact_provenance_status": (
                        "unobserved_by_mcp_discovery"
                    ),
                    "catalog_comparison_status": compatibility_status,
                    "dashboard_attestation_status": (
                        readmission_selection.signed_entry
                        .dashboard_attestation.status
                        if readmission_selection is not None
                        and readmission_selection.signed_entry is not None
                        else release.dashboard_attestation_status
                        if release is not None
                        else "not_evaluated"
                    ),
                    "version_status": "reviewed_exact",
                    "protocol_version": catalog.protocol_version[:64],
                    "catalog_fingerprint": observed_fingerprint,
                    "observed_catalog_fingerprint": observed_fingerprint,
                    "reviewed_catalog_fingerprint": (
                        policy.reviewed_stock_catalog_fingerprint
                    ),
                    "operational_catalog_fingerprint_model": (
                        OPERATIONAL_CATALOG_FINGERPRINT_MODEL
                    ),
                    "catalog_diff_field_counts": dict(
                        catalog_diff_field_counts
                    ),
                    "upstream_advertised_tool_count": len(catalog.tools),
                    "reviewed_accounted_tool_count": len(policy.tools),
                    "reviewed_policy_entry_count": len(policy.tools),
                    "reviewed_stock_catalog_tool_count": (
                        policy.reviewed_stock_catalog_tool_count
                    ),
                    "reviewed_stock_catalog_fingerprint": (
                        policy.reviewed_stock_catalog_fingerprint
                    ),
                    "reviewed_tool_accounting_valid": (
                        len(policy.tools) == len(catalog.tools)
                        and not evaluation.unreviewed
                    ),
                    "held_read_count": policy.classification_counts.get(
                        "held_for_canary", 0
                    ),
                    "automatic_read_count": policy.classification_counts[
                        "automatic_read"
                    ],
                    "reviewed_automatic_read_count": (
                        policy.classification_counts["automatic_read"]
                    ),
                    "blocked_mixed_tool_count": (
                        policy.classification_counts[
                            "mixed_or_requires_wrapper"
                        ]
                    ),
                    "blocked_write_count": policy.classification_counts[
                        "persistent_write"
                    ],
                    "blocked_physical_high_risk_count": (
                        policy.classification_counts[
                            "physical_or_high_risk_action"
                        ]
                    ),
                    "prohibited_count": policy.classification_counts[
                        "prohibited"
                    ],
                    "unsupported_count": policy.classification_counts[
                        "unsupported"
                    ],
                    "blocked_classification_counts": {
                        name: policy.classification_counts.get(name, 0)
                        for name in (
                            "held_for_canary",
                            "mixed_or_requires_wrapper",
                            "persistent_write",
                            "physical_or_high_risk_action",
                            "prohibited",
                            "unsupported",
                        )
                        if name != "held_for_canary"
                        or policy.classification_counts.get(name, 0)
                    },
                    "held_tools": sorted(
                        entry.upstream_name
                        for entry in policy.tools
                        if entry.classification == "held_for_canary"
                    ),
                    "live_canary_required_tools": sorted(
                        entry.upstream_name
                        for entry in policy.tools
                        if entry.classification == "held_for_canary"
                    ),
                    "static_review_completed": True,
                    "observed_advertised_tool_count": len(catalog.tools),
                    "exact_matched_automatic_read_count": len(
                        evaluation.matched
                    ),
                    "dynamically_exposed_count": len(exposed),
                    "collision_count": len(collisions),
                    "schema_mismatch_count": reason_counts.get(
                        "input_schema_mismatch", 0
                    ),
                    "schema_mismatched_automatic_read_count": (
                        reason_counts.get("input_schema_mismatch", 0)
                    ),
                    "description_semantics_mismatch_count": (
                        reason_counts.get(
                            "description_semantics_mismatch", 0
                        )
                    ),
                    "annotation_mismatch_count": reason_counts.get(
                        "annotation_mismatch", 0
                    ),
                    "output_contract_mismatch_count": reason_counts.get(
                        "output_contract_mismatch", 0
                    ),
                    "runtime_contract_mismatch_count": (
                        reason_counts.get(
                            "runtime_contract_mismatch", 0
                        )
                        + reason_counts.get(
                            "duplicate_tool_descriptor", 0
                        )
                    ),
                    "quarantined_automatic_read_count": len(
                        evaluation.quarantined
                    ),
                    "quarantine_reason_counts": dict(reason_counts),
                    "quarantined_tools": [
                        dict(item)
                        for item in evaluation.quarantined[
                            :MAX_QUARANTINE_RECORDS
                        ]
                    ],
                    "quarantine_truncated": (
                        len(evaluation.quarantined)
                        > MAX_QUARANTINE_RECORDS
                    ),
                    "missing_reviewed_read_count": len(evaluation.missing),
                    "missing_automatic_read_count": len(evaluation.missing),
                    "missing_tools": list(
                        evaluation.missing[:MAX_QUARANTINE_RECORDS]
                    ),
                    "accounted_automatic_read_count": accounted,
                    "automatic_read_accounting_valid": (
                        accounted == automatic_count
                    ),
                    "unreviewed_tool_count": len(evaluation.unreviewed),
                    "unreviewed_observed_tool_count": len(
                        evaluation.unreviewed
                    ),
                    "unreviewed_tools": list(
                        evaluation.unreviewed[:MAX_QUARANTINE_RECORDS]
                    ),
                    "unreviewed_tools_truncated": (
                        len(evaluation.unreviewed)
                        > MAX_QUARANTINE_RECORDS
                    ),
                    "last_failure_category": self._state[
                        "last_call_failure_category"
                    ],
                    "last_discovery_failure_category": None,
                    "last_discovery_stable": True,
                    "compatibility_status": compatibility_status,
                    "admission_status": admission_status,
                    "last_compatible_version": (
                        catalog.server_version[:128]
                        if full_admission
                        else self._state["last_compatible_version"]
                    ),
                    "recommended_action": _recommended_action(
                        compatibility_status
                    ),
                    "last_catalog_refresh_at": _utc_now(),
                    "reconciliation_status": (
                        "admitted" if full_admission else "degraded"
                    ),
                    "next_retry_delay_seconds": None,
                    "exposed_tools": sorted(exposed),
                    "collision_mappings": collisions,
                    "blocked_tools": [
                        dict(item) for item in evaluation.blocked
                    ],
                    "observed_catalog_matches_reviewed_stock_fixture": (
                        len(catalog.tools)
                        == policy.reviewed_stock_catalog_tool_count
                        and observed_fingerprint
                        == policy.reviewed_stock_catalog_fingerprint
                    ),
                    "stale_reprobe_retry_armed": False,
                }
            )
            if (
                readmission_selection is not None
                and readmission_selection.signed_entry is not None
            ):
                # The signed registry is authority input, not a raw health
                # surface. Preserve bounded status and fingerprints without
                # echoing registry identities, versions, commits, or images.
                self._state.update(
                    {
                        "upstream_server_name": "accepted_provider",
                        "upstream_server_version": (
                            "signed_compatible_release"
                        ),
                        "observed_upstream_server_name": "accepted",
                        "observed_upstream_server_version": "accepted",
                        "observed_protocol_version": "accepted",
                        "reviewed_upstream_version": "compiled_profile",
                        "selected_compatibility_entry_id": (
                            schema_fingerprint(
                                {
                                    "compatibility_entry_id": (
                                        readmission_selection
                                        .compatibility_entry_id
                                    )
                                }
                            )
                        ),
                        "reviewed_source_commit": None,
                        "reviewed_image_index_digest": None,
                        "reviewed_architecture_image_digests": {},
                        "reviewed_addon_artifact_digests": {},
                        "reviewed_image_revision": None,
                        "reviewed_allowed_protocol_versions": [
                            "accepted"
                        ],
                        "protocol_version": "accepted",
                        "last_compatible_version": (
                            "signed_compatible_release"
                        ),
                    }
                )
            self._latest_live_contract_epoch = 0
            self._latest_live_contract_token = None
            self._stale_reprobe_retry_armed = False
            self._reprobe_event.clear()

    async def _finish_discovery_failure(
        self,
        *,
        category: str,
        transient: bool,
        catalog: McpReadCatalog | None,
        identity_validated: bool,
        discovery_epoch: int,
    ) -> dict[str, Any]:
        """Publish only a failure observation from the current live epoch."""

        stale_discovery = False
        immediate_retry = False
        if not transient and self._readmission_coordinator is not None:
            self._readmission_coordinator.retire_surface_authority(
                UpstreamSurface.HA_MCP
            )
        # A discovery failure publishes immediately; it does not wait for
        # unrelated delegated calls that may be in flight.
        with self._lock:
            stale_discovery = (
                discovery_epoch != self._live_observation_epoch
            )
            if stale_discovery:
                immediate_retry = not self._stale_reprobe_retry_armed
                self._stale_reprobe_retry_armed = True
                self._state.update(
                    {
                        "last_discovery_stable": False,
                        "reconciliation_status": "reprobe_requested",
                        "compatibility_reprobe_status": (
                            "triggered"
                            if immediate_retry
                            else "waiting"
                        ),
                        "next_compatibility_reprobe_at": None,
                        "stale_reprobe_retry_armed": True,
                        "recommended_action": (
                            "A newer live contract observation "
                            "superseded this discovery failure."
                        ),
                    }
                )
            else:
                if catalog is not None:
                    self._record_observed_identity(
                        catalog.server_name,
                        catalog.server_version,
                        catalog.protocol_version,
                        accepted=identity_validated,
                    )
                self._record_failure(
                    category,
                    disable_delegation=not transient,
                    discovery=True,
                )
                if not transient:
                    self._remove_registered_tools()
        if stale_discovery:
            if immediate_retry:
                self._reprobe_event.set()
            else:
                self._reprobe_event.clear()
        replace_dynamic_upstream_capabilities(
            self._dynamic_capabilities, self.health_snapshot()
        )
        return self.health_snapshot()

    async def reconcile_until_initialized(
        self,
        server: Any,
        *,
        retry_delays: tuple[float, ...] = RECONCILIATION_RETRY_DELAYS_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> dict[str, Any]:
        """Retry transport failures until one stable compatibility state is known."""

        if not retry_delays or any(delay <= 0 for delay in retry_delays):
            raise ValueError("retry_delays must contain positive values")
        async with self._reconciliation_lock:
            snapshot = self.health_snapshot()
            if _stable_compatibility(snapshot):
                return snapshot
            return await self._reconcile_until_full_admission(
                server, retry_delays=retry_delays, sleep=sleep
            )

    async def _reconcile_until_full_admission(
        self,
        server: Any,
        *,
        retry_delays: tuple[float, ...],
        sleep: Callable[[float], Awaitable[None]],
    ) -> dict[str, Any]:
        with self._lock:
            self._state["reconciliation_active"] = True
            self._state["reconciliation_status"] = "probing"
        retry_index = 0
        startup_ordering_wait_seconds = 0.0
        try:
            while True:
                snapshot = await self.initialize(server)
                if not snapshot["configured"]:
                    with self._lock:
                        self._state["reconciliation_active"] = False
                        self._state["reconciliation_status"] = "idle"
                    replace_dynamic_upstream_capabilities((), self.health_snapshot())
                    return self.health_snapshot()
                if _stable_compatibility(snapshot):
                    with self._lock:
                        self._state["reconciliation_active"] = False
                        self._state["reconciliation_status"] = (
                            "admitted"
                            if snapshot["admission_complete"]
                            else "degraded"
                        )
                    replace_dynamic_upstream_capabilities(
                        self._dynamic_capabilities, self.health_snapshot()
                    )
                    return self.health_snapshot()
                failure = snapshot.get("last_discovery_failure_category")
                if failure in _STARTUP_ORDERING_FAILURES:
                    if (
                        startup_ordering_wait_seconds
                        >= STARTUP_ORDERING_GRACE_SECONDS
                    ):
                        with self._lock:
                            self._state["reconciliation_active"] = False
                            self._state["reconciliation_status"] = (
                                "startup_grace_exhausted"
                            )
                            self._state["next_retry_delay_seconds"] = None
                            self._state["recommended_action"] = (
                                "Verify the fixed upstream endpoint and wait "
                                "for upstream startup before the slow reprobe."
                            )
                        replace_dynamic_upstream_capabilities(
                            self._dynamic_capabilities,
                            self.health_snapshot(),
                        )
                        return self.health_snapshot()
                else:
                    startup_ordering_wait_seconds = 0.0
                if (
                    failure
                    and failure not in _TRANSIENT_DISCOVERY_FAILURES
                    and failure not in _STARTUP_ORDERING_FAILURES
                ):
                    with self._lock:
                        self._state["reconciliation_active"] = False
                        self._state["reconciliation_status"] = (
                            "blocked_incompatible_upstream"
                            if failure
                            in {
                                "server_identity_mismatch",
                                "upstream_version_mismatch",
                                "unsupported_protocol_version",
                                "invalid_response",
                                "schema_mismatch",
                            }
                            else "unavailable"
                        )
                    replace_dynamic_upstream_capabilities(
                        self._dynamic_capabilities, self.health_snapshot()
                    )
                    return self.health_snapshot()

                delay = retry_delays[min(retry_index, len(retry_delays) - 1)]
                if failure in _STARTUP_ORDERING_FAILURES:
                    delay = min(
                        delay,
                        STARTUP_ORDERING_GRACE_SECONDS
                        - startup_ordering_wait_seconds,
                    )
                    startup_ordering_wait_seconds += delay
                retry_index += 1
                with self._lock:
                    self._state["reconciliation_status"] = "waiting"
                    self._state["next_retry_delay_seconds"] = delay
                    self._state["retry_count"] += 1
                replace_dynamic_upstream_capabilities(
                    self._dynamic_capabilities, self.health_snapshot()
                )
                await sleep(delay)
                with self._lock:
                    self._state["reconciliation_status"] = "probing"
                    self._state["next_retry_delay_seconds"] = None
        finally:
            with self._lock:
                if self._state["reconciliation_active"]:
                    self._state["reconciliation_active"] = False
                    self._state["reconciliation_status"] = "stopped"
                    self._state["next_retry_delay_seconds"] = None
            replace_dynamic_upstream_capabilities(
                self._dynamic_capabilities, self.health_snapshot()
            )

    async def supervise_reconciliation(
        self,
        server: Any,
        *,
        retry_delays: tuple[float, ...] = RECONCILIATION_RETRY_DELAYS_SECONDS,
        reprobe_interval_seconds: float = COMPATIBILITY_REPROBE_INTERVAL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        initial_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Keep transport recovery fast and stable compatibility reprobes slow."""

        if reprobe_interval_seconds <= 0:
            raise ValueError("reprobe_interval_seconds must be positive")
        snapshot = (
            dict(initial_snapshot)
            if initial_snapshot is not None
            else await self.reconcile_until_initialized(
                server, retry_delays=retry_delays, sleep=sleep
            )
        )
        if not snapshot["configured"]:
            await asyncio.Future()
        while True:
            with self._lock:
                self._state["compatibility_reprobe_status"] = "waiting"
                self._state["compatibility_reprobe_interval_seconds"] = (
                    reprobe_interval_seconds
                )
                self._state["next_compatibility_reprobe_at"] = _utc_after(
                    reprobe_interval_seconds
                )
            replace_dynamic_upstream_capabilities(
                self._dynamic_capabilities, self.health_snapshot()
            )
            triggered = await self._wait_for_reprobe(
                reprobe_interval_seconds, sleep=sleep
            )
            with self._lock:
                self._state["compatibility_reprobe_status"] = (
                    "triggered" if triggered else "probing"
                )
                self._state["last_compatibility_reprobe_at"] = _utc_now()
                self._state["next_compatibility_reprobe_at"] = None
            replace_dynamic_upstream_capabilities(
                self._dynamic_capabilities, self.health_snapshot()
            )
            snapshot = await self.initialize(server)
            if (
                snapshot.get("last_discovery_failure_category")
                in (
                    _TRANSIENT_DISCOVERY_FAILURES
                    | _STARTUP_ORDERING_FAILURES
                )
            ):
                await self.reconcile_until_initialized(
                    server, retry_delays=retry_delays, sleep=sleep
                )

    async def _wait_for_reprobe(
        self,
        delay: float,
        *,
        sleep: Callable[[float], Awaitable[None]],
    ) -> bool:
        """Wait for the slow cadence or an admitted-identity movement signal."""

        sleep_task = asyncio.create_task(sleep(delay))
        event_task = asyncio.create_task(self._reprobe_event.wait())
        try:
            done, pending = await asyncio.wait(
                {sleep_task, event_task}, return_when=asyncio.FIRST_COMPLETED
            )
            triggered = event_task in done and event_task.result()
            if triggered:
                self._reprobe_event.clear()
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if sleep_task in done:
                sleep_task.result()
            return bool(triggered)
        finally:
            for task in (sleep_task, event_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleep_task, event_task, return_exceptions=True)

    def _validate_identity(
        self,
        server_name: str,
        server_version: str,
        protocol: str,
    ) -> tuple[
        UpstreamToolPolicy,
        ReviewedUpstreamRelease | None,
        HaMcpAdmissionSelection | None,
    ]:
        if server_name != REVIEWED_UPSTREAM_SERVER:
            raise DashboardTransportError("server_identity_mismatch")
        if (
            not isinstance(server_version, str)
            or not _UPSTREAM_VERSION_EVIDENCE.fullmatch(server_version)
            or any(
                secret and secret in server_version
                for secret in self._known_secrets
            )
        ):
            raise DashboardTransportError("upstream_version_mismatch")
        if protocol not in SUPPORTED_PROTOCOLS:
            raise DashboardTransportError("unsupported_protocol_version")
        if self._readmission_selector is not None:
            try:
                selection = self._readmission_selector.select(
                    server_name=server_name,
                    version=server_version,
                    protocol_version=protocol,
                )
            except HaMcpAuthorityError as exc:
                if exc.reason_code == "identity_or_protocol_disagreement":
                    raise DashboardTransportError(
                        "unsupported_protocol_version"
                    ) from None
                raise DashboardTransportError(
                    "upstream_version_mismatch"
                ) from None
            return (
                selection.policy,
                selection.binary_release,
                selection,
            )
        if self._release_registry is not None:
            release = self._release_registry.by_version.get(server_version)
            if release is None:
                # Matching a known catalog is not authority for an unknown
                # release. An exact source-controlled version entry is required.
                raise DashboardTransportError("upstream_version_mismatch")
            if (
                release.server_name != server_name
                or protocol not in release.allowed_protocol_versions
            ):
                raise DashboardTransportError(
                    "unsupported_protocol_version"
                )
            if release.provider_disposition("read_gateway") == "held":
                raise DashboardTransportError("upstream_version_mismatch")
            return release.policy, release, None
        if (
            self._policy is None
            or server_version != self._policy.reviewed_upstream_version
        ):
            raise DashboardTransportError("upstream_version_mismatch")
        return self._policy, None, None

    def _record_observed_identity(
        self,
        server_name: Any,
        server_version: Any,
        protocol: Any,
        *,
        accepted: bool,
    ) -> None:
        with self._lock:
            self._state.update(
                {
                    "observed_upstream_server_name": (
                        self._safe_identity_evidence(server_name)
                    ),
                    "observed_upstream_server_version": (
                        self._safe_version_evidence(server_version)
                    ),
                    "observed_protocol_version": (
                        self._safe_identity_evidence(protocol)
                    ),
                    "observed_identity_status": (
                        "accepted" if accepted else "rejected"
                    ),
                }
            )

    def _safe_identity_evidence(self, value: Any) -> str:
        if not isinstance(value, str):
            return "unknown"
        sanitation = sanitize_untrusted_data(
            value,
            known_secrets=self._known_secrets,
            max_string=128,
        )
        if sanitation.failed_closed or sanitation.redaction_applied:
            return "[REDACTED]"
        if (
            not isinstance(sanitation.value, str)
            or not _OBSERVED_IDENTITY_EVIDENCE.fullmatch(sanitation.value)
        ):
            return "unknown"
        return sanitation.value

    def _safe_version_evidence(self, value: Any) -> str:
        safe = self._safe_identity_evidence(value)
        if safe in {"unknown", "[REDACTED]"}:
            return safe
        return safe if _UPSTREAM_VERSION_EVIDENCE.fullmatch(safe) else "unknown"

    def _validate_catalog(
        self,
        catalog: McpReadCatalog,
        *,
        policy: UpstreamToolPolicy | None = None,
        reviewed_contracts: dict[str, Any] | None = None,
        require_exact_order: bool = True,
        runtime_contract_fingerprint_model: str = (
            RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1
        ),
    ) -> _CatalogEvaluation:
        selected_policy = policy or self._policy
        assert selected_policy is not None
        policy_by_name = selected_policy.by_name
        observed_reviewed: dict[str, list[dict[str, Any]]] = {}
        observed_reviewed_order: list[str] = []
        unreviewed: list[str] = []
        unreviewed_occurrences: Counter[str] = Counter()
        catalog_has_invalid_structure = False
        for item in catalog.tools:
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(item, dict):
                catalog_has_invalid_structure = True
                unreviewed.append("[INVALID_NAME]")
                continue
            if (
                not isinstance(name, str)
                or not _OBSERVED_TOOL_NAME.fullmatch(name)
            ):
                catalog_has_invalid_structure = True
                unreviewed.append(self._safe_observed_tool_name(name))
                continue
            if name in policy_by_name:
                if name not in observed_reviewed:
                    observed_reviewed_order.append(name)
                observed_reviewed.setdefault(name, []).append(item)
                continue
            unreviewed_occurrences[name] += 1
            safe_name = self._safe_observed_tool_name(name)
            if unreviewed_occurrences[name] > 1:
                safe_name = f"{safe_name} [duplicate]"
            unreviewed.append(safe_name)
        missing_reviewed_reads: list[str] = []
        matched: list[_ContractDecision] = []
        quarantined: list[dict[str, Any]] = []
        quarantine_reasons: Counter[str] = Counter()
        blocked: list[dict[str, str]] = []
        expected_order_fingerprint = (
            EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS.get(
                selected_policy.reviewed_upstream_version
            )
        )
        exact_catalog_order_required = (
            require_exact_order and expected_order_fingerprint is not None
        )
        catalog_structure_invalid = (
            exact_catalog_order_required and catalog_has_invalid_structure
        )
        catalog_order_invalid = (
            exact_catalog_order_required
            and schema_fingerprint(observed_reviewed_order)
            != expected_order_fingerprint
        )
        reviewed_descriptions = (
            selected_policy.reviewed_runtime_description_fingerprints_by_name
        )
        reviewed_annotations = (
            selected_policy.reviewed_runtime_annotation_fingerprints_by_name
        )
        reviewed_output_schemas = (
            selected_policy.reviewed_runtime_output_schema_fingerprints_by_name
        )
        for entry in selected_policy.tools:
            observed = observed_reviewed.get(entry.upstream_name, [])
            if not observed:
                if entry.classification == "automatic_read":
                    missing_reviewed_reads.append(entry.upstream_name)
                continue
            if entry.classification == "automatic_read":
                if catalog_structure_invalid or catalog_order_invalid:
                    reference = _compare_tool_contract(
                        entry,
                        observed[0],
                        protocol_version=catalog.protocol_version,
                        reviewed_runtime_description_fingerprint=(
                            reviewed_descriptions[entry.upstream_name]
                        ),
                        reviewed_runtime_annotation_fingerprint=(
                            reviewed_annotations[entry.upstream_name]
                        ),
                        reviewed_runtime_output_schema_fingerprint=(
                            reviewed_output_schemas[entry.upstream_name]
                        ),
                        reviewed_runtime_contract_fingerprint=(
                            reviewed_contracts[entry.upstream_name]
                            .runtime_contract_fingerprint
                            if reviewed_contracts is not None
                            else None
                        ),
                        reviewed_runtime_contract_field_fingerprints=(
                            dict(
                                reviewed_contracts[entry.upstream_name]
                                .runtime_contract_field_fingerprints
                            )
                            if reviewed_contracts is not None
                            else None
                        ),
                        runtime_contract_fingerprint_model=(
                            runtime_contract_fingerprint_model
                        ),
                    )
                    reason = (
                        "catalog_structure_invalid"
                        if catalog_structure_invalid
                        else "catalog_order_mismatch"
                    )
                    quarantine_reasons[reason] += 1
                    quarantined.append(
                        _quarantine_record(reference, reason=reason)
                    )
                    continue
                if len(observed) != 1:
                    reference = _compare_tool_contract(
                        entry,
                        observed[0],
                        protocol_version=catalog.protocol_version,
                        reviewed_runtime_description_fingerprint=(
                            reviewed_descriptions[entry.upstream_name]
                        ),
                        reviewed_runtime_annotation_fingerprint=(
                            reviewed_annotations[entry.upstream_name]
                        ),
                        reviewed_runtime_output_schema_fingerprint=(
                            reviewed_output_schemas[entry.upstream_name]
                        ),
                        reviewed_runtime_contract_fingerprint=(
                            reviewed_contracts[entry.upstream_name]
                            .runtime_contract_fingerprint
                            if reviewed_contracts is not None
                            else None
                        ),
                        reviewed_runtime_contract_field_fingerprints=(
                            dict(
                                reviewed_contracts[entry.upstream_name]
                                .runtime_contract_field_fingerprints
                            )
                            if reviewed_contracts is not None
                            else None
                        ),
                        runtime_contract_fingerprint_model=(
                            runtime_contract_fingerprint_model
                        ),
                    )
                    reason = "duplicate_tool_descriptor"
                    quarantine_reasons[reason] += 1
                    quarantined.append(
                        {
                            "upstream_name": entry.upstream_name,
                            "reason": reason,
                            "expected_fingerprint": (
                                reference.expected_fingerprint
                            ),
                            "observed_fingerprint": schema_fingerprint(
                                {"descriptor_count": len(observed)}
                            ),
                            "expected_contract_fingerprint": (
                                reference.expected_fingerprint
                            ),
                            "observed_contract_fingerprint": schema_fingerprint(
                                {"descriptor_count": len(observed)}
                            ),
                            "expected_runtime_contract_fingerprint": (
                                reference.expected_runtime_contract_fingerprint
                            ),
                            "observed_runtime_contract_fingerprint": (
                                reference.observed_runtime_contract_fingerprint
                            ),
                            "runtime_contract_fingerprint_model": (
                                reference.runtime_contract_fingerprint_model
                            ),
                            "runtime_contract_diff_fields": [
                                "/descriptor_count"
                            ],
                            "runtime_contract_diff_summary": (
                                "Duplicate runtime descriptors were observed."
                            ),
                        }
                    )
                    continue
                decision = _compare_tool_contract(
                    entry,
                    observed[0],
                    protocol_version=catalog.protocol_version,
                    reviewed_runtime_description_fingerprint=(
                        reviewed_descriptions[entry.upstream_name]
                    ),
                    reviewed_runtime_annotation_fingerprint=(
                        reviewed_annotations[entry.upstream_name]
                    ),
                    reviewed_runtime_output_schema_fingerprint=(
                        reviewed_output_schemas[entry.upstream_name]
                    ),
                    reviewed_runtime_contract_fingerprint=(
                        reviewed_contracts[entry.upstream_name]
                        .runtime_contract_fingerprint
                        if reviewed_contracts is not None
                        else None
                    ),
                    reviewed_runtime_contract_field_fingerprints=(
                        dict(
                            reviewed_contracts[entry.upstream_name]
                            .runtime_contract_field_fingerprints
                        )
                        if reviewed_contracts is not None
                        else None
                    ),
                    runtime_contract_fingerprint_model=(
                        runtime_contract_fingerprint_model
                    ),
                )
                if decision.accepted:
                    matched.append(decision)
                else:
                    reason = decision.reason or "contract_mismatch"
                    quarantine_reasons[reason] += 1
                    quarantined.append(
                        _quarantine_record(decision, reason=reason)
                    )
            else:
                blocked.append(
                    {
                        "upstream_name": entry.upstream_name,
                        "classification": entry.classification,
                    }
                )
        return _CatalogEvaluation(
            matched=tuple(matched),
            missing=tuple(sorted(missing_reviewed_reads)),
            quarantined=tuple(
                sorted(quarantined, key=lambda item: item["upstream_name"])
            ),
            quarantine_reason_counts=dict(sorted(quarantine_reasons.items())),
            blocked=tuple(
                sorted(blocked, key=lambda item: item["upstream_name"])
            ),
            unreviewed=tuple(sorted(unreviewed)),
        )

    def _safe_observed_tool_name(self, name: Any) -> str:
        sanitation = sanitize_untrusted_data(
            name,
            known_secrets=self._known_secrets,
            max_string=128,
        )
        if (
            sanitation.failed_closed
            or sanitation.redaction_applied
            or not isinstance(sanitation.value, str)
        ):
            return "[REDACTED]"
        if not _OBSERVED_TOOL_NAME.fullmatch(sanitation.value):
            return "[INVALID_NAME]"
        return sanitation.value

    def _build_held_canary_routes(
        self,
        *,
        catalog: McpReadCatalog,
        policy: UpstreamToolPolicy,
        release: ReviewedUpstreamRelease | None,
        generation: int,
    ) -> dict[str, _HeldCanaryRoute]:
        """Capture held-tool contracts without registering callable routes."""

        if release is None:
            return {}
        observed_by_name: dict[str, list[dict[str, Any]]] = {}
        for tool in catalog.tools:
            name = tool.get("name") if isinstance(tool, dict) else None
            if isinstance(name, str):
                observed_by_name.setdefault(name, []).append(tool)
        reviewed_contracts = release.tool_contracts_by_name
        routes: dict[str, _HeldCanaryRoute] = {}
        for entry in policy.tools:
            if entry.classification != "held_for_canary":
                continue
            observed = observed_by_name.get(entry.upstream_name, [])
            contract = reviewed_contracts[entry.upstream_name]
            decision = None
            rejection_reason = None
            observed_tool = observed[0] if len(observed) == 1 else None
            if len(observed) != 1:
                rejection_reason = (
                    "live_target_missing"
                    if not observed
                    else "live_target_duplicate"
                )
            else:
                decision = _compare_held_tool_contract(
                    entry,
                    observed[0],
                    protocol_version=catalog.protocol_version,
                    reviewed_contract=contract,
                    runtime_contract_fingerprint_model=(
                        release.runtime_contract_fingerprint_model
                    ),
                )
                rejection_reason = decision.reason
            routes[entry.upstream_name] = _HeldCanaryRoute(
                entry=entry,
                observed_tool=deepcopy(observed_tool),
                generation=generation,
                compatibility_entry_id=release.entry_id,
                decision=decision,
                rejection_reason=rejection_reason,
                runtime_description_fingerprint=(
                    contract.description_fingerprint
                ),
                runtime_annotation_fingerprint=(
                    contract.annotation_fingerprint
                ),
                runtime_output_schema_fingerprint=(
                    contract.output_contract_fingerprint
                ),
                runtime_contract_fingerprint=(
                    contract.runtime_contract_fingerprint
                ),
                runtime_contract_field_fingerprints=(
                    contract.runtime_contract_field_fingerprints
                ),
                runtime_contract_fingerprint_model=(
                    release.runtime_contract_fingerprint_model
                ),
                server_name=catalog.server_name,
                server_version=catalog.server_version,
                protocol_version=catalog.protocol_version,
            )
        return routes

    async def _dispatch_current_route(
        self,
        *,
        exposed_name: str,
        arguments: dict[str, Any],
        reviewed_schema: dict[str, Any],
        policy_entry: UpstreamToolPolicyEntry,
        admission_generation: int,
        contract_fingerprint: str,
        telemetry: Any,
        route_context: dict[str, Any],
        live_contract_failure: dict[str, Any],
    ) -> tuple[_AdmittedRoute, Any]:
        """Bind one call to the current route and same-session target contract."""

        with self._lock:
            mapping = self._exposed.get(exposed_name)
            if (
                not mapping
                or mapping.entry.classification != "automatic_read"
                or mapping.entry.upstream_name != policy_entry.upstream_name
                or mapping.generation != admission_generation
                or mapping.contract_fingerprint != contract_fingerprint
            ):
                self._state["prohibited_delegation_attempts"] += 1
                raise _GatewayFailure(
                    "prohibited_delegation", dispatched=False
                )
            transport = self._transport
            lease = _RouteLease(route=mapping)

        route_context.update(
            {"mapping": mapping, "lease": lease, "admitted": True}
        )
        if not isinstance(arguments, dict):
            raise _GatewayFailure("argument_validation", dispatched=False)
        errors = sorted(
            Draft202012Validator(reviewed_schema).iter_errors(arguments),
            key=lambda error: tuple(
                str(item) for item in error.absolute_path
            ),
        )
        if errors:
            raise _GatewayFailure("argument_validation", dispatched=False)
        if transport is None:
            raise _GatewayFailure("not_configured", dispatched=False)

        attempt_started = time.perf_counter()
        if telemetry:
            telemetry.begin_upstream_attempt(attempt_started)
        try:

            def validate_live_catalog(catalog: McpReadCatalog) -> None:
                lease.validator_ran = True
                with self._lock:
                    route_is_current = (
                        self._exposed.get(exposed_name) is mapping
                    )
                if not route_is_current:
                    raise DashboardTransportError(
                        "prohibited_delegation"
                    )
                if telemetry:
                    telemetry.audit_context[
                        "upstream_version_evidence"
                    ] = (
                        mapping.authority_source
                        if self._signed_release_registry is not None
                        else self._safe_version_evidence(catalog.server_version)
                    )
                    telemetry.audit_context[
                        "upstream_identity_status"
                    ] = "observed"
                try:
                    (
                        live_policy,
                        live_release,
                        live_selection,
                    ) = self._validate_identity(
                        catalog.server_name,
                        catalog.server_version,
                        catalog.protocol_version,
                    )
                except DashboardTransportError:
                    self._record_observed_identity(
                        catalog.server_name,
                        catalog.server_version,
                        catalog.protocol_version,
                        accepted=False,
                    )
                    if telemetry:
                        telemetry.audit_context[
                            "upstream_identity_status"
                        ] = "rejected"
                    self._advance_live_observation_epoch()
                    raise
                if catalog.protocol_version != mapping.protocol_version:
                    self._record_observed_identity(
                        catalog.server_name,
                        catalog.server_version,
                        catalog.protocol_version,
                        accepted=False,
                    )
                    if telemetry:
                        telemetry.audit_context[
                            "upstream_identity_status"
                        ] = "rejected"
                    self._advance_live_observation_epoch()
                    raise DashboardTransportError(
                        "unsupported_protocol_version"
                    )
                if (
                    catalog.server_version != mapping.server_version
                    or (
                        live_selection is None
                        and live_release is not None
                        and live_release.version
                        != mapping.server_version
                    )
                ):
                    self._advance_live_observation_epoch()
                    raise DashboardTransportError(
                        "upstream_version_mismatch"
                    )
                try:
                    live_evaluation = self._validate_catalog(
                        catalog,
                        policy=live_policy,
                        reviewed_contracts=(
                            live_release.tool_contracts_by_name
                            if live_release is not None
                            else None
                        ),
                        require_exact_order=(
                            live_selection is None
                            or live_selection.authority_source.value
                            == "compiled_exact"
                        ),
                        runtime_contract_fingerprint_model=(
                            live_release.runtime_contract_fingerprint_model
                            if live_release is not None
                            else RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1
                        ),
                    )
                    live_contract_token = _catalog_contract_token(
                        catalog, live_evaluation
                    )
                except DashboardTransportError:
                    self._advance_live_observation_epoch()
                    raise DashboardTransportError(
                        "schema_mismatch"
                    ) from None

                if mapping.profile_id is not None:
                    coordinator = self._readmission_coordinator
                    if (
                        coordinator is None
                        or live_selection is None
                        or live_selection.profile.profile_id
                        != mapping.profile_id
                        or live_selection.profile.adapter_id
                        != mapping.adapter_id
                        or live_selection.authority_source.value
                        != mapping.authority_source
                        or live_selection.authority_token
                        != mapping.authority_token
                    ):
                        self._advance_live_observation_epoch()
                        raise DashboardTransportError(
                            "upstream_version_mismatch"
                        )
                    live_observation = observation_for_catalog(
                        catalog,
                        live_selection,
                    )
                    live_generation = coordinator.generation_for(
                        UpstreamSurface.HA_MCP
                    )
                    live_decision = (
                        live_generation.decision_for(
                            policy_entry.upstream_name
                        )
                        if live_generation is not None
                        else None
                    )
                    if (
                        live_generation is None
                        or live_generation.generation
                        != mapping.generation
                        or live_generation.observation_fingerprint
                        != live_observation.fingerprint
                        or live_generation.authority_fingerprint
                        != live_selection.authority.fingerprint
                        or live_decision is None
                        or not live_decision.disposition.admitted
                    ):
                        self._advance_live_observation_epoch()
                        live_contract_failure.update(
                            {
                                "disposition": "surface_retired",
                                "reason": "readmission_generation_drift",
                            }
                        )
                        raise DashboardTransportError(
                            "schema_mismatch"
                        )

                targets = [
                    item
                    for item in catalog.tools
                    if isinstance(item, dict)
                    and item.get("name")
                    == policy_entry.upstream_name
                ]
                if len(targets) != 1:
                    self._advance_live_observation_epoch()
                    live_contract_failure.update(
                        {
                            "disposition": (
                                "missing"
                                if not targets
                                else "quarantine"
                            ),
                            "reason": (
                                "live_target_missing"
                                if not targets
                                else "live_target_duplicate"
                            ),
                            "expected_fingerprint": (
                                mapping.contract_fingerprint
                            ),
                            "observed_fingerprint": schema_fingerprint(
                                {"live_target_count": len(targets)}
                            ),
                        }
                    )
                    raise DashboardTransportError("schema_mismatch")

                decision = _compare_tool_contract(
                    policy_entry,
                    targets[0],
                    protocol_version=catalog.protocol_version,
                    reviewed_runtime_description_fingerprint=(
                        mapping.runtime_description_fingerprint
                    ),
                    reviewed_runtime_annotation_fingerprint=(
                        mapping.runtime_annotation_fingerprint
                    ),
                    reviewed_runtime_output_schema_fingerprint=(
                        mapping.runtime_output_schema_fingerprint
                    ),
                    reviewed_runtime_contract_fingerprint=(
                        mapping.runtime_contract_fingerprint
                    ),
                    reviewed_runtime_contract_field_fingerprints=dict(
                        mapping.runtime_contract_field_fingerprints
                    ),
                    runtime_contract_fingerprint_model=(
                        mapping.runtime_contract_fingerprint_model
                    ),
                )
                if (
                    not decision.accepted
                    or decision.expected_fingerprint
                    != mapping.contract_fingerprint
                ):
                    self._advance_live_observation_epoch()
                    live_contract_failure.update(
                        {
                            "disposition": "quarantine",
                            "reason": (
                                decision.reason
                                or "runtime_contract_mismatch"
                            ),
                            "expected_fingerprint": (
                                mapping.contract_fingerprint
                            ),
                            "observed_fingerprint": (
                                decision.observed_fingerprint
                            ),
                            "expected_contract_fingerprint": (
                                decision.expected_fingerprint
                            ),
                            "observed_contract_fingerprint": (
                                decision.observed_fingerprint
                            ),
                            "expected_runtime_contract_fingerprint": (
                                decision.expected_runtime_contract_fingerprint
                            ),
                            "observed_runtime_contract_fingerprint": (
                                decision.observed_runtime_contract_fingerprint
                            ),
                            "runtime_contract_fingerprint_model": (
                                decision.runtime_contract_fingerprint_model
                            ),
                            "runtime_contract_diff_fields": list(
                                decision.runtime_contract_diff_fields
                            ),
                            "runtime_contract_diff_summary": (
                                decision.runtime_contract_diff_summary
                            ),
                        }
                    )
                    raise DashboardTransportError("schema_mismatch")

                self._record_matching_version_observation(
                    exposed_name=exposed_name,
                    mapping=mapping,
                    catalog=catalog,
                    live_contract_token=live_contract_token,
                )
                if mapping.profile_id is not None:
                    coordinator = self._readmission_coordinator
                    if coordinator is None:
                        raise DashboardTransportError(
                            "prohibited_delegation"
                        )
                    lease.readmission_lease = coordinator.acquire_route(
                        policy_entry.upstream_name,
                        session_id=live_observation.session_id,
                    )
                    if lease.readmission_lease is None:
                        raise DashboardTransportError(
                            "prohibited_delegation"
                        )
                if telemetry:
                    telemetry.audit_context[
                        "upstream_identity_status"
                    ] = "accepted"

            def commit_live_route() -> None:
                # This is the dispatch linearization point. The registered
                # single-use lease is consumed after all same-session checks
                # and immediately before tools/call.
                if mapping.profile_id is not None:
                    coordinator = self._readmission_coordinator
                    if (
                        coordinator is None
                        or lease.readmission_lease is None
                    ):
                        raise DashboardTransportError(
                            "prohibited_delegation"
                        )
                    if not mapping.session_id:
                        raise DashboardTransportError(
                            "prohibited_delegation"
                        )
                    commit = coordinator.commit_route(
                        lease.readmission_lease,
                        session_id=mapping.session_id,
                    )
                    if commit is None:
                        raise DashboardTransportError(
                            "prohibited_delegation"
                        )
                    lease.readmission_commit = commit
                else:
                    with self._lock:
                        if self._exposed.get(exposed_name) is not mapping:
                            raise DashboardTransportError(
                                "prohibited_delegation"
                            )
                lease.dispatch_committed = True

            exchange = await transport.execute_read(
                policy_entry.upstream_name,
                dict(arguments),
                timeout_seconds=policy_entry.timeout_seconds,
                catalog_validator=validate_live_catalog,
                before_dispatch=commit_live_route,
            )
            if not lease.validator_ran or not lease.dispatch_committed:
                raise _GatewayFailure(
                    "prohibited_delegation", dispatched=False
                )
            return mapping, exchange
        except DashboardTransportError as exc:
            raise _GatewayFailure(
                exc.category,
                dispatched=lease.dispatch_committed,
            ) from None
        except (BeforeDispatchFailure, CatalogValidationFailure) as exc:
            category = (
                exc.cause.category
                if isinstance(exc.cause, DashboardTransportError)
                else "prohibited_delegation"
            )
            raise _GatewayFailure(
                category,
                dispatched=False,
            ) from None
        finally:
            coordinator = self._readmission_coordinator
            if coordinator is not None:
                if lease.readmission_commit is not None:
                    coordinator.finish_committed(
                        lease.readmission_commit
                    )
                elif lease.readmission_lease is not None:
                    coordinator.release_route(
                        lease.readmission_lease
                    )
            finished = time.perf_counter()
            if telemetry:
                telemetry.finish_upstream_attempt(
                    finished, (finished - attempt_started) * 1_000
                )

    async def execute(
        self,
        *,
        exposed_name: str,
        arguments: dict[str, Any],
        reviewed_schema: dict[str, Any],
        policy_entry: UpstreamToolPolicyEntry,
        admission_generation: int,
        contract_fingerprint: str,
    ) -> str:
        started = time.perf_counter()
        mapping: _AdmittedRoute | None = None
        route_was_admitted = False
        route_context: dict[str, Any] = {}
        live_contract_failure: dict[str, Any] = {}
        response_limit = min(
            policy_entry.response_limit_bytes,
            self._settings.response_size_limit if self._settings else 60_000,
        )
        telemetry = current_telemetry()
        try:
            mapping, exchange = await self._dispatch_current_route(
                exposed_name=exposed_name,
                arguments=arguments,
                reviewed_schema=reviewed_schema,
                policy_entry=policy_entry,
                admission_generation=admission_generation,
                contract_fingerprint=contract_fingerprint,
                telemetry=telemetry,
                route_context=route_context,
                live_contract_failure=live_contract_failure,
            )
            route_was_admitted = True
            if exchange.call_result.get("isError") is True:
                raise _GatewayFailure(
                    _classify_upstream_tool_error(
                        policy_entry.upstream_name,
                        exchange.call_result,
                        arguments,
                    ),
                    dispatched=True,
                )
            payload = _normalize_upstream_payload(
                exchange.call_result,
                server_version=mapping.adapter_version,
                protocol_version=mapping.protocol_version,
                upstream_tool=policy_entry.upstream_name,
            )
            response_adapter = None
            if policy_entry.upstream_name == "ha_get_device":
                try:
                    payload, response_adapter = (
                        await adapt_ha_get_device_composite_result(
                            payload,
                            arguments=arguments,
                            upstream_version=mapping.adapter_version,
                            rest_client=self._ha_rest_client,
                            websocket_client=self._ha_websocket_client,
                        )
                    )
                except HomeAssistantTimeoutError:
                    raise _GatewayFailure("timeout", dispatched=True) from None
                except HomeAssistantUnavailableError:
                    raise _GatewayFailure(
                        "connection_failed", dispatched=True
                    ) from None
                except AuthorizationError:
                    raise _GatewayFailure(
                        "authentication_failed", dispatched=True
                    ) from None
                except (
                    CompositeDeviceCompatibilityError,
                    HomeAssistantApiError,
                ):
                    raise _GatewayFailure(
                        "invalid_response", dispatched=True
                    ) from None
            sanitation = sanitize_untrusted_data(
                payload,
                known_secrets=self._known_secrets,
                max_string=max(2_000, min(response_limit // 2, 20_000)),
            )
            if sanitation.failed_closed:
                raise _GatewayFailure("sanitization_failed", dispatched=True)
            encoded_size = len(
                json.dumps(
                    sanitation.value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                    default=str,
                ).encode("utf-8")
            )
            if encoded_size + 8_000 > response_limit:
                raise _GatewayFailure("response_too_large", dispatched=True)
            upstream_partial, completeness_warnings = _upstream_completeness(
                policy_entry, sanitation.value
            )
            completeness = (
                "partial"
                if sanitation.truncated_field_count or upstream_partial
                else "complete"
            )
            METRICS.record_provider_result(PROVIDER_ID, completeness, dispatched=True)
            publish_current_route = False
            with self._lock:
                # A completed call proves the discovered route remains usable. Historical
                # failure counts stay available, but only the currently admitted route
                # may clear its own transient failure. A call from a removed generation
                # must not erase a newer discovery failure.
                if self._exposed.get(exposed_name) is mapping:
                    self._state["generic_delegation_available"] = bool(self._exposed)
                    self._state["last_call_failure_category"] = None
                    if self._state["last_discovery_failure_category"] is None:
                        self._state["last_failure_category"] = None
                    publish_current_route = True
            if publish_current_route:
                replace_dynamic_upstream_capabilities(
                    self._dynamic_capabilities, self.health_snapshot()
                )
            if telemetry:
                telemetry.result_status = "partial" if completeness == "partial" else "success"
                telemetry.completeness = completeness
            warnings = []
            if sanitation.truncated_field_count:
                warnings.append("The untrusted upstream response was safely bounded.")
            warnings.extend(completeness_warnings)
            if response_adapter is not None:
                warnings.append(
                    "A reviewed Home Assistant 2026.8 composite-device "
                    "compatibility adapter restored split entity membership."
                )
            metadata = {
                "provider": PROVIDER_ID,
                "upstream_tool": policy_entry.upstream_name,
                "upstream_server": exchange.server_name,
                "upstream_version": exchange.server_version,
                "classification": "automatic_read",
                "schema_fingerprint": policy_entry.input_schema_fingerprint,
                "untrusted_upstream_content": True,
                "fallback": "none",
                "fallback_occurred": False,
                "completeness": completeness,
            }
            if response_adapter is not None:
                metadata["response_adapter"] = response_adapter
            return SuccessResponse(
                operation=exposed_name,
                summary="Completed a reviewed pure-read operation through the upstream gateway.",
                data=sanitation.value,
                warnings=warnings,
                metadata=metadata,
                timing=timing_since(started),
                request_id=current_request_id(),
            ).to_json(response_limit)
        except _GatewayFailure as exc:
            mapping = route_context.get("mapping")
            route_was_admitted = bool(route_context.get("admitted"))
            category = _normalize_category(exc.category)
            if exc.dispatched:
                classified_outcome = _EXPECTED_PROVIDER_OUTCOMES.get(category)
                if classified_outcome:
                    METRICS.record_classified_outcome(classified_outcome)
                    METRICS.record_provider_result(
                        PROVIDER_ID, "complete", dispatched=True
                    )
                else:
                    METRICS.record_provider_result(
                        PROVIDER_ID, "failed", dispatched=True
                    )
            with self._lock:
                route_is_current = (
                    route_was_admitted
                    and mapping is not None
                    and self._exposed.get(exposed_name) is mapping
                )
            if not route_was_admitted or route_is_current:
                if category in _EXPECTED_PROVIDER_OUTCOMES:
                    self._record_expected_outcome(category)
                else:
                    self._record_failure(category, discovery=False)
            else:
                # Preserve the historical count without allowing a retired
                # in-flight generation to overwrite the newer generation's
                # live failure or availability state.
                with self._lock:
                    self._state["failure_counts"][category] += 1
            if category in _EXPECTED_PROVIDER_OUTCOMES and route_is_current:
                replace_dynamic_upstream_capabilities(
                    self._dynamic_capabilities, self.health_snapshot()
                )
            if category in {
                "server_identity_mismatch",
                "upstream_version_mismatch",
                "unsupported_protocol_version",
            } and mapping is not None:
                self._invalidate_for_identity_movement(
                    category,
                    exposed_name=exposed_name,
                    mapping=mapping,
                )
            elif (
                category == "schema_mismatch"
                and mapping is not None
                and live_contract_failure
            ):
                self._retire_live_contract_route(
                    exposed_name=exposed_name,
                    mapping=mapping,
                    failure=live_contract_failure,
                )
            elif (
                category == "protocol_error"
                and mapping is not None
                and mapping.profile_id is not None
            ):
                self._retire_live_contract_route(
                    exposed_name=exposed_name,
                    mapping=mapping,
                    failure={
                        "disposition": "surface_retired",
                        "reason": "upstream_session_drift",
                    },
                )
            code, retryable = _public_failure(category)
            if telemetry:
                telemetry.error_code = code
                telemetry.result_status = "failure"
                telemetry.completeness = "failed"
                if category == "timeout":
                    telemetry.timeout_occurred = True
            failure_metadata = {
                "provider": PROVIDER_ID,
                "upstream_tool": policy_entry.upstream_name,
                "classification": "automatic_read",
                "upstream_dispatch_occurred": exc.dispatched,
                "fallback": "none",
                "fallback_occurred": False,
            }
            if exc.dispatched and mapping is not None:
                failure_metadata.update(
                    {
                        "upstream_server": REVIEWED_UPSTREAM_SERVER,
                        "upstream_version": mapping.server_version,
                    }
                )
            return FailureResponse(
                operation=exposed_name,
                error="UpstreamReadGatewayError",
                error_code=code,
                message=_safe_failure_message(
                    category,
                    policy_entry.upstream_name,
                ),
                details={"failure_category": category},
                retryable=retryable,
                metadata=failure_metadata,
                timing=timing_since(started),
                request_id=current_request_id(),
            ).to_json(response_limit)

    async def run_held_read_canary(
        self,
        *,
        upstream_tool_name: str,
        expected_compatibility_entry_id: str,
        arguments: dict[str, Any] | None,
    ) -> str:
        """Execute one evidence-only call for an exactly reviewed held read."""

        started = time.perf_counter()
        arguments = {} if arguments is None else arguments
        telemetry = current_telemetry()
        route: _HeldCanaryRoute | None = None
        dispatched = False
        observed_identity: dict[str, str | None] = {
            "server": None,
            "version": None,
            "protocol": None,
        }

        def evidence(
            *,
            outcome: str,
            failure_category: str | None = None,
            completeness: str = "failed",
            truncation: bool = False,
            output_contract_match: bool | None = None,
            error_contract: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            observed_tool = (
                route.observed_tool if route is not None else None
            )
            active_id = (
                route.compatibility_entry_id
                if route is not None
                else self.health_snapshot().get(
                    "selected_compatibility_entry_id"
                )
            )
            observed_input_fingerprint = _safe_schema_component_fingerprint(
                observed_tool.get("inputSchema")
                if observed_tool is not None
                else None
            )
            observed_annotation_fingerprint = (
                _safe_schema_component_fingerprint(
                    {
                        "present": "annotations" in observed_tool,
                        "value": observed_tool.get("annotations"),
                    }
                )
                if observed_tool is not None
                else None
            )
            observed_output_fingerprint = (
                _safe_schema_component_fingerprint(
                    {
                        "present": "outputSchema" in observed_tool,
                        "value": observed_tool.get("outputSchema"),
                    }
                )
                if observed_tool is not None
                else None
            )
            observed_runtime_fingerprint = None
            if observed_tool is not None and route is not None:
                try:
                    observed_runtime_fingerprint = runtime_contract_fingerprint(
                        observed_tool,
                        model=route.runtime_contract_fingerprint_model,
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
            annotation_match = bool(
                route is not None
                and observed_annotation_fingerprint
                == route.runtime_annotation_fingerprint
            )
            security_match = bool(
                route is not None
                and route.entry.classification == "held_for_canary"
                and route.entry.reviewed_annotations.read_only
                and not route.entry.reviewed_annotations.destructive
                and not route.entry.reviewed_annotations.open_world
            )
            runtime_match = bool(
                route is not None
                and observed_runtime_fingerprint
                == route.runtime_contract_fingerprint
            )
            reviewed_output_match = bool(
                route is not None
                and observed_output_fingerprint
                == route.runtime_output_schema_fingerprint
            )
            return {
                "upstream_tool": upstream_tool_name[:128],
                "expected_compatibility_entry_id": (
                    expected_compatibility_entry_id[:160]
                    if isinstance(expected_compatibility_entry_id, str)
                    else "invalid"
                ),
                "active_compatibility_entry_id": active_id,
                "observed_upstream_server": observed_identity["server"],
                "observed_upstream_version": observed_identity["version"],
                "observed_upstream_protocol": observed_identity["protocol"],
                "reviewed_classification_before": (
                    route.entry.classification if route is not None else None
                ),
                "reviewed_classification_after": (
                    route.entry.classification if route is not None else None
                ),
                "reviewed_input_schema_fingerprint": (
                    route.entry.input_schema_fingerprint
                    if route is not None
                    else None
                ),
                "observed_input_schema_fingerprint": (
                    observed_input_fingerprint
                ),
                "input_schema_match": bool(
                    route is not None
                    and observed_input_fingerprint
                    == route.entry.input_schema_fingerprint
                ),
                "annotation_match": annotation_match,
                "security_match": security_match,
                "annotation_security_match": (
                    annotation_match and security_match
                ),
                "runtime_contract_match": runtime_match,
                "output_contract_match": (
                    output_contract_match
                    if output_contract_match is not None
                    else reviewed_output_match
                ),
                "error_contract": error_contract,
                "dispatch_occurred": dispatched,
                "provider": PROVIDER_ID,
                "fallback": "none",
                "fallback_occurred": False,
                "outcome": outcome,
                "failure_category": failure_category,
                "completeness": completeness,
                "truncated": truncation,
                "promotion_performed": False,
            }

        def set_audit_context(value: dict[str, Any]) -> None:
            if telemetry is None:
                return
            telemetry.audit_context.update(
                {
                    key: value[key]
                    for key in (
                        "upstream_tool",
                        "expected_compatibility_entry_id",
                        "active_compatibility_entry_id",
                        "observed_upstream_server",
                        "observed_upstream_version",
                        "observed_upstream_protocol",
                        "reviewed_classification_before",
                        "reviewed_classification_after",
                        "dispatch_occurred",
                        "provider",
                        "fallback_occurred",
                        "outcome",
                        "failure_category",
                        "completeness",
                        "truncated",
                        "promotion_performed",
                    )
                }
            )

        async def fail(
            category: str,
            *,
            reason: str | None = None,
            error_contract: dict[str, Any] | None = None,
        ) -> str:
            normalized = _normalize_category(category)
            code, retryable = _public_failure(normalized)
            report = evidence(
                outcome="error",
                failure_category=normalized,
                error_contract=error_contract,
            )
            set_audit_context(report)
            if telemetry is not None:
                telemetry.error_code = code
                telemetry.result_status = "failure"
                telemetry.completeness = "failed"
            if dispatched:
                METRICS.record_provider_result(
                    PROVIDER_ID,
                    "failed",
                    dispatched=True,
                )
            response_limit = min(
                route.entry.response_limit_bytes
                if route is not None
                else 60_000,
                self._settings.response_size_limit
                if self._settings is not None
                else 60_000,
            )
            return FailureResponse(
                operation="run_held_read_canary",
                error="HeldReadCanaryError",
                error_code=code,
                message=_safe_failure_message(
                    normalized,
                    upstream_tool_name if route is not None else None,
                ),
                details={
                    "failure_category": normalized,
                    **({"reason": reason[:128]} if reason else {}),
                    "canary_evidence": report,
                },
                retryable=retryable,
                metadata={
                    "provider": PROVIDER_ID,
                    "fallback": "none",
                    "fallback_occurred": False,
                    "promotion_performed": False,
                },
                timing=timing_since(started),
                request_id=current_request_id(),
            ).to_json(response_limit)

        with self._lock:
            active_release = self._active_release
            active_entry_id = self._state.get(
                "selected_compatibility_entry_id"
            )
            exact = (
                self._state.get("compatibility_status") == "exact"
                and self._state.get("admission_status") == "admitted_exact"
                and active_release is not None
            )
            policy_entry = (
                self._policy.by_name.get(upstream_tool_name)
                if self._policy is not None
                and isinstance(upstream_tool_name, str)
                else None
            )
            route = self._held_canaries.get(upstream_tool_name)
            transport = self._transport
        if route is not None:
            observed_identity.update(
                {
                    "server": self._safe_identity_evidence(route.server_name),
                    "version": self._safe_version_evidence(route.server_version),
                    "protocol": self._safe_identity_evidence(
                        route.protocol_version
                    ),
                }
            )
        if not exact:
            return await fail("not_initialized", reason="exact_admission_required")
        if (
            not isinstance(expected_compatibility_entry_id, str)
            or expected_compatibility_entry_id != active_entry_id
        ):
            return await fail(
                "prohibited_delegation",
                reason="compatibility_entry_mismatch",
            )
        if policy_entry is None or policy_entry.classification != "held_for_canary":
            return await fail(
                "prohibited_delegation",
                reason="tool_not_held_for_canary",
            )
        if route is None or route.compatibility_entry_id != active_entry_id:
            return await fail(
                "schema_mismatch",
                reason="held_contract_evidence_unavailable",
            )
        if route.rejection_reason is not None or route.decision is None:
            return await fail(
                "schema_mismatch",
                reason=route.rejection_reason or "held_contract_mismatch",
            )
        if not isinstance(arguments, dict):
            return await fail("argument_validation")
        errors = list(
            Draft202012Validator(
                route.observed_tool["inputSchema"]
            ).iter_errors(arguments)
        )
        if errors:
            return await fail("argument_validation")
        if transport is None:
            return await fail("not_configured")

        validator_ran = False

        def validate_live_catalog(catalog: McpReadCatalog) -> None:
            nonlocal validator_ran
            validator_ran = True
            observed_identity.update(
                {
                    "server": self._safe_identity_evidence(catalog.server_name),
                    "version": self._safe_version_evidence(catalog.server_version),
                    "protocol": self._safe_identity_evidence(catalog.protocol_version),
                }
            )
            live_policy, live_release, live_selection = self._validate_identity(
                catalog.server_name,
                catalog.server_version,
                catalog.protocol_version,
            )
            if (
                (
                    live_selection is not None
                    and live_selection.authority_source.value
                    != "compiled_exact"
                )
                or live_release is None
                or live_release.entry_id != expected_compatibility_entry_id
                or live_release.entry_id != route.compatibility_entry_id
                or live_release.version != route.server_version
                or catalog.protocol_version != route.protocol_version
            ):
                raise DashboardTransportError("upstream_version_mismatch")
            with self._lock:
                if (
                    self._active_release is not active_release
                    or self._held_canaries.get(upstream_tool_name) is not route
                    or self._admission_generation != route.generation
                ):
                    raise DashboardTransportError("prohibited_delegation")
            live_entry = live_policy.by_name.get(upstream_tool_name)
            if (
                live_entry is None
                or live_entry.classification != "held_for_canary"
            ):
                raise DashboardTransportError("prohibited_delegation")
            targets = [
                item
                for item in catalog.tools
                if isinstance(item, dict)
                and item.get("name") == upstream_tool_name
            ]
            if len(targets) != 1:
                raise DashboardTransportError("schema_mismatch")
            live_contract = live_release.tool_contracts_by_name[
                upstream_tool_name
            ]
            decision = _compare_held_tool_contract(
                live_entry,
                targets[0],
                protocol_version=catalog.protocol_version,
                reviewed_contract=live_contract,
                runtime_contract_fingerprint_model=(
                    route.runtime_contract_fingerprint_model
                ),
            )
            if not decision.accepted:
                raise DashboardTransportError("schema_mismatch")

        def before_dispatch() -> None:
            nonlocal dispatched
            with self._lock:
                if (
                    self._active_release is not active_release
                    or self._held_canaries.get(upstream_tool_name) is not route
                    or self._admission_generation != route.generation
                    or self._state.get("compatibility_status") != "exact"
                    or self._state.get("admission_status") != "admitted_exact"
                ):
                    raise DashboardTransportError("prohibited_delegation")
                dispatched = True

        try:
            exchange = await transport.execute_read(
                upstream_tool_name,
                dict(arguments),
                timeout_seconds=route.entry.timeout_seconds,
                catalog_validator=validate_live_catalog,
                before_dispatch=before_dispatch,
            )
        except DashboardTransportError as exc:
            return await fail(exc.category)
        except Exception as exc:
            category = getattr(getattr(exc, "cause", None), "category", None)
            return await fail(category or "internal_error")
        if not validator_ran or not dispatched:
            return await fail("prohibited_delegation")
        if exchange.call_result.get("isError") is True:
            error_contract = _upstream_error_evidence(exchange.call_result)
            return await fail(
                _classify_upstream_tool_error(
                    upstream_tool_name,
                    exchange.call_result,
                    arguments,
                ),
                error_contract=error_contract,
            )
        try:
            payload = _normalize_upstream_payload(
                exchange.call_result,
                server_version=route.server_version,
                protocol_version=route.protocol_version,
                upstream_tool=upstream_tool_name,
            )
            output_schema = route.observed_tool.get("outputSchema")
            output_match = True
            if isinstance(output_schema, dict):
                output_match = not any(
                    Draft202012Validator(output_schema).iter_errors(payload)
                )
            if not output_match:
                return await fail(
                    "invalid_response",
                    reason="output_contract_validation_failed",
                )
            response_limit = min(
                route.entry.response_limit_bytes,
                self._settings.response_size_limit
                if self._settings is not None
                else 60_000,
            )
            sanitation = sanitize_untrusted_data(
                payload,
                known_secrets=self._known_secrets,
                max_string=max(2_000, min(response_limit // 2, 20_000)),
            )
            if sanitation.failed_closed:
                return await fail("sanitization_failed")
            result = sanitation.value
            encoded_size = len(
                json.dumps(
                    result,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                    default=str,
                ).encode("utf-8")
            )
            summarized = encoded_size + 12_000 > response_limit
            if summarized:
                result = {
                    "result_omitted": True,
                    "reason": "bounded_result_summary",
                    "sanitized_result_type": type(sanitation.value).__name__,
                }
            upstream_partial, warnings = _upstream_completeness(
                route.entry, sanitation.value
            )
            truncated = bool(
                sanitation.truncated_field_count or summarized
            )
            completeness = (
                "partial" if truncated or upstream_partial else "complete"
            )
            report = evidence(
                outcome="partial" if completeness == "partial" else "success",
                completeness=completeness,
                truncation=truncated,
                output_contract_match=True,
            )
            set_audit_context(report)
            if telemetry is not None:
                telemetry.result_status = report["outcome"]
                telemetry.completeness = completeness
            METRICS.record_provider_result(
                PROVIDER_ID,
                completeness,
                dispatched=True,
            )
            return SuccessResponse(
                operation="run_held_read_canary",
                summary=(
                    "Executed one reviewed held read as evidence only; no "
                    "promotion was performed."
                ),
                data={"canary_evidence": report, "result": result},
                warnings=(
                    (["The untrusted upstream result was safely bounded."] if truncated else [])
                    + warnings
                    + ["A passing canary does not authorize promotion."]
                ),
                metadata={
                    "provider": PROVIDER_ID,
                    "untrusted_upstream_content": True,
                    "fallback": "none",
                    "fallback_occurred": False,
                    "promotion_performed": False,
                },
                timing=timing_since(started),
                request_id=current_request_id(),
            ).to_json(response_limit)
        except _GatewayFailure as exc:
            return await fail(exc.category)
        except (SchemaError, TypeError, ValueError, OverflowError):
            return await fail("invalid_response")

    def _remove_registered_tools(self) -> None:
        with self._lock:
            if self._registered_server is not None:
                self._replace_registered_tools(self._registered_server, {})
            self._registered_names = set()
            self._exposed = {}
            self._held_canaries = {}
            self._dynamic_capabilities = ()

    def _reset_contract_accounting_locked(self) -> None:
        """Clear current-catalog terms when no stable catalog is authoritative."""

        self._state.update(
            {
                "schema_mismatch_count": 0,
                "schema_mismatched_automatic_read_count": 0,
                "description_semantics_mismatch_count": 0,
                "annotation_mismatch_count": 0,
                "output_contract_mismatch_count": 0,
                "runtime_contract_mismatch_count": 0,
                "quarantined_automatic_read_count": 0,
                "quarantine_reason_counts": {},
                "quarantined_tools": [],
                "quarantine_truncated": False,
                "missing_reviewed_read_count": 0,
                "missing_automatic_read_count": 0,
                "missing_tools": [],
                "accounted_automatic_read_count": 0,
                "automatic_read_accounting_valid": False,
                "unreviewed_tool_count": 0,
                "unreviewed_observed_tool_count": 0,
                "unreviewed_tools": [],
                "unreviewed_tools_truncated": False,
                "observed_catalog_matches_reviewed_stock_fixture": False,
                "blocked_tools": [],
            }
        )

    def _retire_live_contract_route(
        self,
        *,
        exposed_name: str,
        mapping: _AdmittedRoute,
        failure: dict[str, Any],
    ) -> bool:
        """Retire drifted authority and request one atomic replacement."""

        if (
            mapping.profile_id is not None
            and failure.get("disposition") == "surface_retired"
        ):
            coordinator = self._readmission_coordinator
            if coordinator is not None:
                coordinator.retire_surface_authority(
                    UpstreamSurface.HA_MCP
                )
            with self._lock:
                if self._exposed.get(exposed_name) is not mapping:
                    return False
                self._remove_registered_tools()
                self._active_release = None
                self._reset_contract_accounting_locked()
                self._state.update(
                    {
                        "initialized": False,
                        "generic_delegation_available": False,
                        "admission_complete": False,
                        "exact_matched_automatic_read_count": 0,
                        "dynamically_exposed_count": 0,
                        "collision_count": 0,
                        "exposed_tools": [],
                        "collision_mappings": [],
                        "selected_compatibility_entry_id": None,
                        "readmission_authority_source": None,
                        "readmission_profile_id": None,
                        "readmission_adapter_id": None,
                        "readmission_generation": None,
                        "last_discovery_stable": False,
                        "compatibility_status": "reconciling",
                        "admission_status": "compatibility_reprobe_pending",
                        "reconciliation_status": "reprobe_requested",
                        "compatibility_reprobe_status": "triggered",
                        "next_compatibility_reprobe_at": None,
                        "recommended_action": (
                            "Reconnect or re-list after the changed upstream "
                            "catalog has been reconciled."
                        ),
                    }
                )
                self._state["compatibility_reprobe_trigger_count"] += 1
            self._reprobe_event.set()
            replace_dynamic_upstream_capabilities(
                (), self.health_snapshot()
            )
            return True

        with self._lock:
            if self._exposed.get(exposed_name) is not mapping:
                return False
            if self._registered_tool_registry is not None:
                replacement = dict(self._registered_tool_registry.snapshot())
                replacement.pop(exposed_name, None)
                self._registered_tool_registry.replace(replacement)
            registered_names = set(self._registered_names)
            registered_names.discard(exposed_name)
            self._registered_names = registered_names
            exposed = dict(self._exposed)
            exposed.pop(exposed_name, None)
            self._exposed = exposed
            self._dynamic_capabilities = tuple(
                item
                for item in self._dynamic_capabilities
                if item.get("tool") != exposed_name
            )

            disposition = failure.get("disposition")
            missing = set(self._state["missing_tools"])
            quarantined = [
                dict(item) for item in self._state["quarantined_tools"]
            ]
            reason_counts = Counter(self._state["quarantine_reason_counts"])
            if disposition == "missing":
                missing.add(mapping.entry.upstream_name)
            else:
                reason = failure.get(
                    "reason", "runtime_contract_mismatch"
                )
                quarantined = [
                    item
                    for item in quarantined
                    if item.get("upstream_name")
                    != mapping.entry.upstream_name
                ]
                quarantined.append(
                    {
                        "upstream_name": mapping.entry.upstream_name,
                        "reason": reason,
                        "expected_fingerprint": failure.get(
                            "expected_fingerprint",
                            mapping.contract_fingerprint,
                        ),
                        "observed_fingerprint": failure.get(
                            "observed_fingerprint",
                            schema_fingerprint(
                                {"live_contract": "unknown"}
                            ),
                        ),
                        "expected_contract_fingerprint": failure.get(
                            "expected_contract_fingerprint",
                            mapping.contract_fingerprint,
                        ),
                        "observed_contract_fingerprint": failure.get(
                            "observed_contract_fingerprint",
                            schema_fingerprint(
                                {"live_contract": "unknown"}
                            ),
                        ),
                        "expected_runtime_contract_fingerprint": failure.get(
                            "expected_runtime_contract_fingerprint",
                            mapping.runtime_contract_fingerprint or "unknown",
                        ),
                        "observed_runtime_contract_fingerprint": failure.get(
                            "observed_runtime_contract_fingerprint",
                            "unknown",
                        ),
                        "runtime_contract_fingerprint_model": failure.get(
                            "runtime_contract_fingerprint_model",
                            mapping.runtime_contract_fingerprint_model,
                        ),
                        "runtime_contract_diff_fields": list(
                            failure.get("runtime_contract_diff_fields", [])
                        )[:MAX_RUNTIME_CONTRACT_DIFF_FIELDS],
                        "runtime_contract_diff_summary": str(
                            failure.get(
                                "runtime_contract_diff_summary",
                                "The live runtime contract changed.",
                            )
                        )[:512],
                    }
                )
                quarantined.sort(
                    key=lambda item: str(item.get("upstream_name", ""))
                )
                reason_counts[reason] += 1

            matched_count = len(self._exposed)
            missing_count = len(missing)
            quarantined_count = len(quarantined)
            accounted = (
                matched_count + missing_count + quarantined_count
            )
            reviewed_count = self._state[
                "reviewed_automatic_read_count"
            ]
            compatibility_status = (
                "partial" if self._exposed else "incompatible"
            )
            collision_mappings = [
                item
                for item in self._state["collision_mappings"]
                if item.get("exposed_name") != exposed_name
            ]
            self._state.update(
                {
                    "initialized": True,
                    "generic_delegation_available": bool(self._exposed),
                    "admission_complete": False,
                    "exact_matched_automatic_read_count": matched_count,
                    "dynamically_exposed_count": matched_count,
                    "schema_mismatch_count": reason_counts.get(
                        "input_schema_mismatch", 0
                    ),
                    "schema_mismatched_automatic_read_count": (
                        reason_counts.get("input_schema_mismatch", 0)
                    ),
                    "description_semantics_mismatch_count": (
                        reason_counts.get(
                            "description_semantics_mismatch", 0
                        )
                    ),
                    "annotation_mismatch_count": reason_counts.get(
                        "annotation_mismatch", 0
                    ),
                    "output_contract_mismatch_count": reason_counts.get(
                        "output_contract_mismatch", 0
                    ),
                    "runtime_contract_mismatch_count": (
                        reason_counts.get("runtime_contract_mismatch", 0)
                        + reason_counts.get("live_target_duplicate", 0)
                        + reason_counts.get(
                            "duplicate_tool_descriptor", 0
                        )
                    ),
                    "quarantined_automatic_read_count": quarantined_count,
                    "quarantine_reason_counts": dict(reason_counts),
                    "quarantined_tools": quarantined[
                        :MAX_QUARANTINE_RECORDS
                    ],
                    "quarantine_truncated": (
                        quarantined_count > MAX_QUARANTINE_RECORDS
                    ),
                    "missing_reviewed_read_count": missing_count,
                    "missing_automatic_read_count": missing_count,
                    "missing_tools": sorted(missing)[
                        :MAX_QUARANTINE_RECORDS
                    ],
                    "accounted_automatic_read_count": accounted,
                    "automatic_read_accounting_valid": (
                        accounted == reviewed_count
                    ),
                    "exposed_tools": sorted(self._exposed),
                    "collision_count": len(collision_mappings),
                    "collision_mappings": collision_mappings,
                    "last_discovery_stable": False,
                    "observed_catalog_matches_reviewed_stock_fixture": False,
                    "compatibility_status": compatibility_status,
                    "admission_status": (
                        "partially_admitted"
                        if self._exposed
                        else "blocked_incompatible_upstream"
                    ),
                    "reconciliation_status": "reprobe_requested",
                    "compatibility_reprobe_status": "triggered",
                    "next_compatibility_reprobe_at": None,
                    "recommended_action": _recommended_action(
                        compatibility_status
                    ),
                }
            )
            self._state["compatibility_reprobe_trigger_count"] += 1
        self._reprobe_event.set()
        replace_dynamic_upstream_capabilities(
            self._dynamic_capabilities, self.health_snapshot()
        )
        return True

    def _advance_live_observation_epoch(self) -> None:
        """Make any in-progress discovery older than a call-time observation."""

        with self._lock:
            self._live_observation_epoch += 1
            self._latest_live_contract_epoch = (
                self._live_observation_epoch
            )
            self._latest_live_contract_token = None

    def _record_live_contract_observation_locked(
        self, live_contract_token: str
    ) -> None:
        """Record one reviewed automatic-read outcome projection."""

        self._live_observation_epoch += 1
        self._latest_live_contract_epoch = self._live_observation_epoch
        self._latest_live_contract_token = live_contract_token

    def _record_matching_version_observation(
        self,
        *,
        exposed_name: str,
        mapping: _AdmittedRoute,
        catalog: McpReadCatalog,
        live_contract_token: str,
    ) -> bool:
        """Record a mapped-version return without reviving stale discovery."""

        trigger = False
        publish = False
        with self._lock:
            if self._exposed.get(exposed_name) is not mapping:
                return False
            safe_name = self._safe_identity_evidence(catalog.server_name)
            safe_version = self._safe_version_evidence(
                catalog.server_version
            )
            safe_protocol = self._safe_identity_evidence(
                catalog.protocol_version
            )
            identity_changed = (
                self._state["observed_upstream_server_name"] != safe_name
                or self._state["observed_upstream_server_version"]
                != safe_version
                or self._state["observed_protocol_version"]
                != safe_protocol
                or self._state["observed_identity_status"] != "accepted"
            )
            if self._discovery_in_progress:
                self._record_live_contract_observation_locked(
                    live_contract_token
                )
            if not identity_changed:
                return True
            if not self._discovery_in_progress:
                self._record_live_contract_observation_locked(
                    live_contract_token
                )
            retry_is_armed = self._stale_reprobe_retry_armed
            self._state.update(
                {
                    "observed_upstream_server_name": safe_name,
                    "observed_upstream_server_version": safe_version,
                    "observed_protocol_version": safe_protocol,
                    "observed_identity_status": "accepted",
                    "version_status": "reviewed_exact",
                    "observed_advertised_tool_count": len(catalog.tools),
                    "observed_catalog_fingerprint": None,
                    "observed_strict_full_contract_fingerprint": None,
                    "catalog_diff_field_counts": {},
                    "last_discovery_stable": False,
                    "observed_catalog_matches_reviewed_stock_fixture": False,
                    "compatibility_status": "reconciling",
                    "admission_status": "compatibility_reprobe_pending",
                    "reconciliation_status": (
                        self._state["reconciliation_status"]
                        if retry_is_armed
                        else "reprobe_requested"
                    ),
                    "compatibility_reprobe_status": (
                        self._state["compatibility_reprobe_status"]
                        if retry_is_armed
                        else "triggered"
                    ),
                    "next_compatibility_reprobe_at": (
                        self._state["next_compatibility_reprobe_at"]
                        if retry_is_armed
                        else None
                    ),
                    "recommended_action": (
                        "Matching reviewed reads remain available while the "
                        "latest upstream version evidence is reconciled."
                    ),
                }
            )
            trigger = not retry_is_armed
            publish = True
        if trigger:
            self._reprobe_event.set()
        if publish:
            replace_dynamic_upstream_capabilities(
                self._dynamic_capabilities, self.health_snapshot()
            )
        return True

    def _invalidate_for_identity_movement(
        self,
        category: str,
        *,
        exposed_name: str,
        mapping: _AdmittedRoute,
    ) -> bool:
        """Retire all routes after a hard release/identity incompatibility."""

        coordinator = self._readmission_coordinator
        if coordinator is not None:
            coordinator.retire_surface_authority(
                UpstreamSurface.HA_MCP
            )
        with self._lock:
            if self._exposed.get(exposed_name) is not mapping:
                return False
            self._remove_registered_tools()
            self._reset_contract_accounting_locked()
            self._state.update(
                {
                    "initialized": False,
                    "generic_delegation_available": False,
                    "admission_complete": False,
                    "exact_matched_automatic_read_count": 0,
                    "dynamically_exposed_count": 0,
                    "collision_count": 0,
                    "exposed_tools": [],
                    "collision_mappings": [],
                    "last_discovery_stable": False,
                    "compatibility_status": "unavailable",
                    "admission_status": "blocked_incompatible_upstream",
                    "version_status": {
                        "upstream_version_mismatch": "rejected_unreviewed",
                        "server_identity_mismatch": "rejected_identity",
                        "unsupported_protocol_version": "rejected_protocol",
                    }.get(category, "rejected_identity"),
                    "reconciliation_status": "blocked_incompatible_upstream",
                    "compatibility_reprobe_status": "waiting",
                    "recommended_action": (
                        "Restore the reviewed upstream identity, exact "
                        "release profile, and supported protocol before "
                        "retrying delegated reads."
                    ),
                }
            )
        replace_dynamic_upstream_capabilities((), self.health_snapshot())
        return True

    def _replace_registered_tools(
        self, server: Any, dynamic_tools: dict[str, ReviewedUpstreamReadTool]
    ) -> None:
        """Publish one complete dynamic registry generation."""

        registry = McpSdkToolRegistry(server)
        replacement = dict(registry.snapshot())
        for name in self._registered_names:
            replacement.pop(name, None)
        replacement.update(dynamic_tools)
        registry.replace(replacement)
        self._registered_tool_registry = registry

    def _record_failure(
        self,
        category: str,
        *,
        disable_delegation: bool = False,
        discovery: bool = False,
    ) -> None:
        category = _normalize_category(category)
        with self._lock:
            self._state["last_failure_category"] = category
            self._state[
                (
                    "last_discovery_failure_category"
                    if discovery
                    else "last_call_failure_category"
                )
            ] = category
            self._state["failure_counts"][category] += 1
            if discovery:
                self._state["last_discovery_stable"] = False
            if disable_delegation:
                self._active_release = None
                blocked_incompatible = category in {
                    "server_identity_mismatch",
                    "upstream_version_mismatch",
                    "unsupported_protocol_version",
                    "invalid_response",
                    "schema_mismatch",
                }
                self._state["initialized"] = False
                self._state["generic_delegation_available"] = False
                self._state["admission_complete"] = False
                self._state["exact_matched_automatic_read_count"] = 0
                self._state["dynamically_exposed_count"] = 0
                self._state["collision_count"] = 0
                self._state["exposed_tools"] = []
                self._state["collision_mappings"] = []
                self._state["selected_compatibility_entry_id"] = None
                if self._readmission_selector is not None:
                    self._state["readmission_authority_source"] = None
                    self._state["readmission_profile_id"] = None
                    self._state["readmission_adapter_id"] = None
                    self._state["readmission_generation"] = None
                self._state["reviewed_source_commit"] = None
                self._state["reviewed_image_index_digest"] = None
                self._state["reviewed_architecture_image_digests"] = {}
                self._state["reviewed_addon_artifact_digests"] = {}
                self._state["reviewed_image_revision"] = None
                self._state["reviewed_image_revision_authoritative"] = False
                self._state["strict_full_contract_fingerprint"] = None
                self._state["strict_full_contract_fingerprint_model"] = None
                self._state["reviewed_strict_full_contract_fingerprint"] = None
                self._state["observed_strict_full_contract_fingerprint"] = None
                self._state["runtime_contract_fingerprint_model"] = None
                self._state["reviewed_catalog_fingerprint"] = None
                self._state["catalog_diff_field_counts"] = {}
                self._state["reviewed_allowed_protocol_versions"] = []
                self._state["catalog_comparison_status"] = {
                    "upstream_version_mismatch": "unknown_version",
                    "schema_mismatch": "reviewed_runtime_drift",
                }.get(category, "unavailable")
                self._state["dashboard_attestation_status"] = (
                    "unknown_version"
                    if category == "upstream_version_mismatch"
                    else "unavailable"
                )
                self._reset_contract_accounting_locked()
                self._state["compatibility_status"] = "unavailable"
                self._state["admission_status"] = (
                    "blocked_incompatible_upstream"
                    if blocked_incompatible
                    else "unavailable"
                )
                self._state["version_status"] = {
                    "upstream_version_mismatch": "rejected_unreviewed",
                    "server_identity_mismatch": "rejected_identity",
                    "unsupported_protocol_version": "rejected_protocol",
                }.get(category, self._state["version_status"])
                self._state["recommended_action"] = {
                    "upstream_version_mismatch": (
                        "The observed upstream version is not reviewed. "
                        "Capture and review it, or roll back to the last "
                        "compatible version."
                    ),
                    "schema_mismatch": (
                        "The reviewed upstream version has runtime contract "
                        "drift. Review quarantines or roll back upstream."
                    ),
                    "server_identity_mismatch": (
                        "Restore the reviewed ha-mcp server identity."
                    ),
                    "unsupported_protocol_version": (
                        "Restore the reviewed MCP protocol or roll back "
                        "upstream."
                    ),
                }.get(
                    category,
                    "Restore upstream connectivity or authentication.",
                )
                if not self._state["reconciliation_active"]:
                    self._state["reconciliation_status"] = "idle"

    def _record_expected_outcome(self, category: str) -> None:
        """Record a structured provider answer without degrading its health."""

        category = _normalize_category(category)
        with self._lock:
            self._state["failure_counts"][category] += 1
            self._state["generic_delegation_available"] = bool(self._exposed)
            self._state["last_call_failure_category"] = None
            if self._state["last_discovery_failure_category"] is None:
                self._state["last_failure_category"] = None

    def health_snapshot(self) -> dict[str, Any]:
        with self._lock:
            value = deepcopy(self._state)
            value["failure_counts"] = dict(value["failure_counts"])
            value["policy_classifications"] = (
                self._policy.classification_counts if self._policy else {}
            )
            value["advertised_is_callable"] = False
            value["callable_requires_exact_policy_and_contract_match"] = True
            value["catalog_admission_mode"] = "reviewed_per_tool_contract_subset"
            value["stock_catalog_match_is_informational"] = True
            value["writes_allowed"] = False
            value["direct_ha_fallback_allowed"] = False
            coordinator = self._readmission_coordinator
            registry = self._signed_release_registry
            audit_count = len(self._readmission_audit)
        if coordinator is not None:
            projection = coordinator.health_projection()
            surface = next(
                (
                    item
                    for item in projection["surfaces"]
                    if item["surface"] == UpstreamSurface.HA_MCP.value
                ),
                None,
            )
            value["automatic_readmission"] = {
                "model_version": projection["model_version"],
                "surface": surface,
                "authority_source": value[
                    "readmission_authority_source"
                ],
                "profile_id": value["readmission_profile_id"],
                "adapter_id": value["readmission_adapter_id"],
                "decision_generation": value[
                    "readmission_generation"
                ],
                "issued_lease_count": projection[
                    "issued_lease_count"
                ],
                "active_commit_count": projection[
                    "active_commit_count"
                ],
                "capacity_exhaustion_count": projection[
                    "capacity_exhaustion_count"
                ],
                "capacity_exhaustion_reason": projection[
                    "capacity_exhaustion_reason"
                ],
                "fallback_count": 0,
                "retained_audit_projection_count": audit_count,
            }
        if registry is not None:
            value["automatic_readmission_registry"] = registry.snapshot()
            self._sanitize_registry_enabled_health(value)
        return value

    @staticmethod
    def _sanitize_registry_enabled_health(value: dict[str, Any]) -> None:
        """Project signed-mode health without raw release or catalog data."""

        tool_fields = (
            "held_tools",
            "live_canary_required_tools",
            "quarantined_tools",
            "missing_tools",
            "unreviewed_tools",
            "exposed_tools",
            "collision_mappings",
            "blocked_tools",
        )
        raw_projection = {
            field: value.get(field, []) for field in tool_fields
        }
        try:
            value["catalog_name_projection_fingerprint"] = (
                schema_fingerprint(raw_projection)
            )
        except Exception:
            value["catalog_name_projection_fingerprint"] = None
        for field in tool_fields:
            value[field] = []
        reviewed_versions = value.get("reviewed_supported_versions")
        value["reviewed_supported_version_count"] = (
            len(reviewed_versions)
            if isinstance(reviewed_versions, list)
            else 0
        )
        value["reviewed_supported_versions"] = []
        value.update(
            {
                "upstream_server_name": (
                    "accepted_provider"
                    if value.get("observed_identity_status") == "accepted"
                    else "unavailable_provider"
                ),
                "upstream_server_version": (
                    "accepted_release"
                    if value.get("observed_identity_status") == "accepted"
                    else "unavailable_release"
                ),
                "observed_upstream_server_name": (
                    "accepted"
                    if value.get("observed_identity_status") == "accepted"
                    else "rejected"
                ),
                "observed_upstream_server_version": (
                    "accepted"
                    if value.get("observed_identity_status") == "accepted"
                    else "rejected"
                ),
                "observed_protocol_version": (
                    "accepted"
                    if value.get("observed_identity_status") == "accepted"
                    else "rejected"
                ),
                "reviewed_upstream_version": "compiled_profile",
                "protocol_version": (
                    "accepted"
                    if value.get("observed_identity_status") == "accepted"
                    else "unavailable"
                ),
                "last_compatible_version": (
                    "accepted_release"
                    if value.get("last_compatible_version")
                    else None
                ),
                "reviewed_source_commit": None,
                "reviewed_image_index_digest": None,
                "reviewed_architecture_image_digests": {},
                "reviewed_addon_artifact_digests": {},
                "reviewed_image_revision": None,
                "reviewed_allowed_protocol_versions": ["accepted"],
            }
        )
        selected = value.get("selected_compatibility_entry_id")
        if (
            selected is not None
            and not (
                isinstance(selected, str)
                and re.fullmatch(r"[0-9a-f]{64}", selected)
            )
        ):
            value["selected_compatibility_entry_id"] = schema_fingerprint(
                {"compatibility_entry_id": selected}
            )

    def readmission_audit_snapshot(self) -> tuple[dict[str, Any], ...]:
        """Return bounded sanitized internal reconciliation audit projections."""

        with self._lock:
            return deepcopy(self._readmission_audit)


class _GatewayFailure(RuntimeError):
    def __init__(self, category: str, *, dispatched: bool):
        super().__init__("The reviewed upstream read operation failed.")
        self.category = category
        self.dispatched = dispatched


def _safe_schema_component_fingerprint(value: Any) -> str | None:
    try:
        return schema_fingerprint(value)
    except (TypeError, ValueError, UnicodeError, OverflowError):
        return None


def _safe_catalog_fingerprint(
    tools: list[dict[str, Any]],
) -> str | None:
    """Keep whole-catalog diagnostics from becoming admission authority."""

    try:
        return catalog_fingerprint(tools)
    except (TypeError, ValueError, UnicodeError, OverflowError):
        return None


def _safe_strict_catalog_fingerprint(
    tools: list[dict[str, Any]],
) -> str | None:
    """Fingerprint the observed order-preserving strict evidence envelope."""

    try:
        return schema_fingerprint({"tools": tools})
    except (TypeError, ValueError, UnicodeError, OverflowError):
        return None


def _catalog_diff_field_counts(
    tools: list[dict[str, Any]],
    reviewed_contracts: dict[str, Any] | None,
) -> dict[str, int]:
    """Count bounded raw diagnostic-field drift across the reviewed catalog."""

    if reviewed_contracts is None:
        return {}
    observed_by_name: dict[str, list[dict[str, Any]]] = {}
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        if isinstance(name, str) and name in reviewed_contracts:
            observed_by_name.setdefault(name, []).append(tool)
    counts: Counter[str] = Counter()
    for name, contract in reviewed_contracts.items():
        observed = observed_by_name.get(name, [])
        if len(observed) != 1:
            counts["/tool_descriptor_count"] += 1
            continue
        expected_fields = dict(
            contract.runtime_contract_field_fingerprints
        )
        observed_fields = runtime_contract_field_fingerprints(observed[0])
        for pointer in expected_fields:
            if expected_fields[pointer] != observed_fields.get(pointer):
                counts[pointer] += 1
    return dict(sorted(counts.items()))


def _catalog_contract_token(
    catalog: McpReadCatalog, evaluation: _CatalogEvaluation
) -> str:
    """Fingerprint only admission-relevant reviewed catalog outcomes.

    The token is concurrency evidence, not admission authority. Unreviewed
    descriptor content and whole-catalog diagnostics are deliberately excluded
    so an unrelated new tool cannot gate a selected reviewed read.
    """

    outcomes: list[dict[str, str]] = []
    outcomes.extend(
        {
            "upstream_name": decision.entry.upstream_name,
            "status": "matched",
            "expected_fingerprint": decision.expected_fingerprint,
            "observed_fingerprint": decision.observed_fingerprint,
        }
        for decision in evaluation.matched
    )
    outcomes.extend(
        {
            "upstream_name": upstream_name,
            "status": "missing",
            "expected_fingerprint": "unknown",
            "observed_fingerprint": "unknown",
        }
        for upstream_name in evaluation.missing
    )
    outcomes.extend(
        {
            "upstream_name": item["upstream_name"],
            "status": f"quarantined:{item['reason']}",
            "expected_fingerprint": item["expected_fingerprint"],
            "observed_fingerprint": item["observed_fingerprint"],
        }
        for item in evaluation.quarantined
    )
    return schema_fingerprint(
        {
            "server_name": catalog.server_name,
            "server_version": catalog.server_version,
            "protocol_version": catalog.protocol_version,
            "reviewed_automatic_read_outcomes": sorted(
                outcomes, key=lambda item: item["upstream_name"]
            ),
        }
    )


def _compare_tool_contract(
    entry: UpstreamToolPolicyEntry,
    observed_tool: dict[str, Any],
    *,
    protocol_version: str,
    reviewed_runtime_description_fingerprint: str,
    reviewed_runtime_annotation_fingerprint: str,
    reviewed_runtime_output_schema_fingerprint: str,
    reviewed_runtime_contract_fingerprint: str | None = None,
    reviewed_runtime_contract_field_fingerprints: (
        dict[str, str] | None
    ) = None,
    runtime_contract_fingerprint_model: str = (
        RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1
    ),
) -> _ContractDecision:
    """Compare one advertised tool with binary-owned reviewed authority."""

    published_annotations = {
        "readOnlyHint": entry.reviewed_annotations.read_only,
        "destructiveHint": entry.reviewed_annotations.destructive,
        "idempotentHint": entry.reviewed_annotations.idempotent,
        "openWorldHint": entry.reviewed_annotations.open_world,
    }
    expected_annotations = {
        "runtime_fingerprint": reviewed_runtime_annotation_fingerprint,
        "published_policy": published_annotations,
    }
    observed_annotation_fingerprint = runtime_annotation_fingerprint(
        observed_tool.get("annotations")
    )
    observed_annotations = {
        "runtime_fingerprint": observed_annotation_fingerprint,
        "published_policy": published_annotations,
    }
    expected_description = reviewed_runtime_description_fingerprint
    observed_description = runtime_description_fingerprint(
        observed_tool.get("description")
    )
    behavior_adapter = (
        "ha_search_partial_v1"
        if entry.upstream_name == "ha_search"
        else "bounded_opaque_read_v1"
    )
    consumed_output_contract = {
        "behavior_adapter": behavior_adapter,
        "sanitized": True,
        "bounded": True,
        "fallback": "none",
    }
    expected_output = {
        "declared_output_schema": {
            "present": True,
            "schema_fingerprint": reviewed_runtime_output_schema_fingerprint,
        },
        "engineering_consumed_contract": consumed_output_contract,
    }
    observed_output = {
        "declared_output_schema": _observed_output_contract(observed_tool),
        "engineering_consumed_contract": consumed_output_contract,
    }
    observed_schema = observed_tool.get("inputSchema")
    try:
        observed_runtime_contract_fingerprint = runtime_contract_fingerprint(
            observed_tool,
            model=runtime_contract_fingerprint_model,
        )
        observed_runtime_fields = runtime_contract_field_fingerprints(
            observed_tool
        )
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError):
        observed_runtime_contract_fingerprint = schema_fingerprint(
            {"invalid_runtime_contract": True}
        )
        observed_runtime_fields = {}
    expected_runtime_contract_fingerprint = (
        reviewed_runtime_contract_fingerprint or "unknown"
    )
    raw_runtime_diff_fields = tuple(
        sorted(
            pointer
            for pointer in set(
                reviewed_runtime_contract_field_fingerprints or {}
            )
            | set(observed_runtime_fields)
            if (reviewed_runtime_contract_field_fingerprints or {}).get(
                pointer
            )
            != observed_runtime_fields.get(pointer)
        )
    )
    try:
        Draft202012Validator.check_schema(observed_schema)
        observed_schema_fingerprint = schema_fingerprint(observed_schema)
        input_matches = (
            observed_schema_fingerprint == entry.input_schema_fingerprint
        )
    except (SchemaError, TypeError, ValueError, OverflowError):
        observed_schema_fingerprint = schema_fingerprint(
            {"invalid_input_schema": True}
        )
        input_matches = False
    expected_static_contract = {
        "classification": entry.classification,
        "argument_restrictions": list(entry.argument_restrictions),
        "behavior_adapter": behavior_adapter,
        "protocol_version": REVIEWED_PROTOCOL_VERSION,
        "descriptor_fields_valid": True,
    }
    observed_static_contract = {
        "classification": entry.classification,
        "argument_restrictions": list(entry.argument_restrictions),
        "behavior_adapter": behavior_adapter,
        "protocol_version": protocol_version,
        "descriptor_fields_valid": _runtime_descriptor_fields_valid(
            observed_tool
        ),
    }
    expected_contract = {
        "name": entry.upstream_name,
        "input_schema_fingerprint": entry.input_schema_fingerprint,
        "runtime_description_fingerprint": expected_description,
        "annotations": expected_annotations,
        "output_contract": expected_output,
        **expected_static_contract,
    }
    observed_contract = {
        "name": observed_tool.get("name"),
        "input_schema_fingerprint": observed_schema_fingerprint,
        "runtime_description_fingerprint": observed_description,
        "annotations": observed_annotations,
        "output_contract": observed_output,
        **observed_static_contract,
    }
    reason = None
    if observed_tool.get("name") != entry.upstream_name:
        reason = "tool_name_mismatch"
    elif not input_matches:
        reason = "input_schema_mismatch"
    elif (
        observed_description is None
        or observed_description != expected_description
    ):
        reason = "description_semantics_mismatch"
    elif (
        observed_annotation_fingerprint is None
        or observed_annotations != expected_annotations
    ):
        reason = "annotation_mismatch"
    elif observed_output != expected_output:
        reason = "output_contract_mismatch"
    elif not observed_static_contract["descriptor_fields_valid"]:
        reason = "runtime_contract_mismatch"
    elif (
        reviewed_runtime_contract_fingerprint is not None
        and observed_runtime_contract_fingerprint
        != reviewed_runtime_contract_fingerprint
    ):
        reason = "runtime_contract_mismatch"
    elif entry.classification != "automatic_read":
        reason = "security_classification_mismatch"
    elif protocol_version not in SUPPORTED_PROTOCOLS:
        reason = "unsupported_protocol_version"
    runtime_diff_fields = raw_runtime_diff_fields
    if reason == "runtime_contract_mismatch" and not runtime_diff_fields:
        runtime_diff_fields = ("/",)
    runtime_diff_fields = runtime_diff_fields[
        :MAX_RUNTIME_CONTRACT_DIFF_FIELDS
    ]
    return _ContractDecision(
        entry=entry,
        observed_tool=observed_tool,
        accepted=reason is None,
        reason=reason,
        expected_fingerprint=schema_fingerprint(expected_contract),
        observed_fingerprint=schema_fingerprint(observed_contract),
        expected_runtime_contract_fingerprint=(
            expected_runtime_contract_fingerprint
        ),
        observed_runtime_contract_fingerprint=(
            observed_runtime_contract_fingerprint
        ),
        runtime_contract_fingerprint_model=(
            runtime_contract_fingerprint_model
        ),
        runtime_contract_diff_fields=runtime_diff_fields,
        runtime_contract_diff_summary=_runtime_contract_diff_summary(
            runtime_diff_fields,
            truncated=(
                len(raw_runtime_diff_fields)
                > MAX_RUNTIME_CONTRACT_DIFF_FIELDS
            ),
        ),
        raw_runtime_contract_diff_fields=raw_runtime_diff_fields,
    )


def _compare_held_tool_contract(
    entry: UpstreamToolPolicyEntry,
    observed_tool: dict[str, Any],
    *,
    protocol_version: str,
    reviewed_contract: Any,
    runtime_contract_fingerprint_model: str,
) -> _ContractDecision:
    """Compare a held read to release evidence without admitting it."""

    try:
        observed_components = {
            "input_schema_fingerprint": schema_fingerprint(
                observed_tool.get("inputSchema")
            ),
            "description_fingerprint": (
                runtime_description_fingerprint(
                    observed_tool.get("description")
                )
                or schema_fingerprint({"invalid_description": True})
            ),
            "annotation_fingerprint": schema_fingerprint(
                {
                    "present": "annotations" in observed_tool,
                    "value": observed_tool.get("annotations"),
                }
            ),
            "output_contract_fingerprint": schema_fingerprint(
                {
                    "present": "outputSchema" in observed_tool,
                    "value": observed_tool.get("outputSchema"),
                }
            ),
            "runtime_contract_fingerprint": runtime_contract_fingerprint(
                observed_tool,
                model=runtime_contract_fingerprint_model,
            ),
        }
        observed_runtime_fields = runtime_contract_field_fingerprints(
            observed_tool
        )
        Draft202012Validator.check_schema(observed_tool.get("inputSchema"))
        if "outputSchema" in observed_tool:
            Draft202012Validator.check_schema(
                observed_tool.get("outputSchema")
            )
    except (SchemaError, TypeError, ValueError, OverflowError):
        observed_components = {
            "input_schema_fingerprint": schema_fingerprint(
                {"invalid_input_schema": True}
            ),
            "description_fingerprint": schema_fingerprint(
                {"invalid_description": True}
            ),
            "annotation_fingerprint": schema_fingerprint(
                {"invalid_annotations": True}
            ),
            "output_contract_fingerprint": schema_fingerprint(
                {"invalid_output_schema": True}
            ),
            "runtime_contract_fingerprint": schema_fingerprint(
                {"invalid_runtime_contract": True}
            ),
        }
        observed_runtime_fields = {}
    expected_components = {
        "input_schema_fingerprint": reviewed_contract.input_schema_fingerprint,
        "description_fingerprint": reviewed_contract.description_fingerprint,
        "annotation_fingerprint": reviewed_contract.annotation_fingerprint,
        "output_contract_fingerprint": (
            reviewed_contract.output_contract_fingerprint
        ),
        "runtime_contract_fingerprint": (
            reviewed_contract.runtime_contract_fingerprint
        ),
    }
    reviewed_fields = dict(
        reviewed_contract.runtime_contract_field_fingerprints
    )
    raw_runtime_diff_fields = tuple(
        sorted(
            pointer
            for pointer in set(reviewed_fields) | set(observed_runtime_fields)
            if reviewed_fields.get(pointer)
            != observed_runtime_fields.get(pointer)
        )
    )
    annotations = entry.reviewed_annotations
    reason = None
    if observed_tool.get("name") != entry.upstream_name:
        reason = "tool_name_mismatch"
    elif observed_components["input_schema_fingerprint"] != expected_components[
        "input_schema_fingerprint"
    ]:
        reason = "input_schema_mismatch"
    elif observed_components["description_fingerprint"] != expected_components[
        "description_fingerprint"
    ]:
        reason = "description_semantics_mismatch"
    elif observed_components["annotation_fingerprint"] != expected_components[
        "annotation_fingerprint"
    ]:
        reason = "annotation_mismatch"
    elif (
        not annotations.read_only
        or annotations.destructive
        or annotations.open_world
        or entry.classification != "held_for_canary"
        or reviewed_contract.policy_classification != "held_for_canary"
        or reviewed_contract.reviewed_automatic_read
        or reviewed_contract.quarantine_reason != "policy:held_for_canary"
    ):
        reason = "security_classification_mismatch"
    elif observed_components["output_contract_fingerprint"] != expected_components[
        "output_contract_fingerprint"
    ]:
        reason = "output_contract_mismatch"
    elif (
        not _runtime_descriptor_fields_valid(observed_tool)
        or observed_components["runtime_contract_fingerprint"]
        != expected_components["runtime_contract_fingerprint"]
    ):
        reason = "runtime_contract_mismatch"
    elif protocol_version not in SUPPORTED_PROTOCOLS:
        reason = "unsupported_protocol_version"
    runtime_diff_fields = raw_runtime_diff_fields[
        :MAX_RUNTIME_CONTRACT_DIFF_FIELDS
    ]
    if reason == "runtime_contract_mismatch" and not runtime_diff_fields:
        runtime_diff_fields = ("/",)
    return _ContractDecision(
        entry=entry,
        observed_tool=observed_tool,
        accepted=reason is None,
        reason=reason,
        expected_fingerprint=schema_fingerprint(expected_components),
        observed_fingerprint=schema_fingerprint(observed_components),
        expected_runtime_contract_fingerprint=(
            reviewed_contract.runtime_contract_fingerprint
        ),
        observed_runtime_contract_fingerprint=observed_components[
            "runtime_contract_fingerprint"
        ],
        runtime_contract_fingerprint_model=runtime_contract_fingerprint_model,
        runtime_contract_diff_fields=runtime_diff_fields,
        runtime_contract_diff_summary=_runtime_contract_diff_summary(
            runtime_diff_fields,
            truncated=(
                len(raw_runtime_diff_fields)
                > MAX_RUNTIME_CONTRACT_DIFF_FIELDS
            ),
        ),
        raw_runtime_contract_diff_fields=raw_runtime_diff_fields,
    )


def _runtime_contract_diff_summary(
    fields: tuple[str, ...],
    *,
    truncated: bool,
) -> str:
    """Summarize only reviewed constant JSON pointers under a fixed bound."""

    if not fields:
        return "No admission-relevant runtime field difference was identified."
    suffix = "; additional fields omitted" if truncated else ""
    return (
        f"{len(fields)} runtime contract field(s) differ: "
        f"{', '.join(fields)}{suffix}."
    )[:512]


def _quarantine_record(
    decision: _ContractDecision,
    *,
    reason: str,
) -> dict[str, Any]:
    """Build one bounded quarantine diagnostic without descriptor values."""

    return {
        "upstream_name": decision.entry.upstream_name,
        "reason": reason,
        # Compatibility aliases retained for existing health consumers.
        "expected_fingerprint": decision.expected_fingerprint,
        "observed_fingerprint": decision.observed_fingerprint,
        "expected_contract_fingerprint": decision.expected_fingerprint,
        "observed_contract_fingerprint": decision.observed_fingerprint,
        "expected_runtime_contract_fingerprint": (
            decision.expected_runtime_contract_fingerprint
        ),
        "observed_runtime_contract_fingerprint": (
            decision.observed_runtime_contract_fingerprint
        ),
        "runtime_contract_fingerprint_model": (
            decision.runtime_contract_fingerprint_model
        ),
        "runtime_contract_diff_fields": list(
            decision.runtime_contract_diff_fields
        ),
        "runtime_contract_diff_summary": (
            decision.runtime_contract_diff_summary
        ),
    }


def _observed_output_contract(tool: dict[str, Any]) -> dict[str, Any]:
    if "outputSchema" not in tool:
        return {"present": False, "schema_fingerprint": None}
    try:
        output_schema = tool["outputSchema"]
        if not isinstance(output_schema, dict):
            raise TypeError("output schema must be an object")
        Draft202012Validator.check_schema(output_schema)
        fingerprint = schema_fingerprint(output_schema)
    except (SchemaError, TypeError, ValueError, OverflowError):
        fingerprint = schema_fingerprint({"invalid_output_schema": True})
    return {"present": True, "schema_fingerprint": fingerprint}


def _runtime_descriptor_fields_valid(tool: dict[str, Any]) -> bool:
    """Reject unreviewed top-level or namespaced descriptor semantics."""

    if set(tool) - _ALLOWED_TOOL_DESCRIPTOR_FIELDS:
        return False
    meta = tool.get("_meta")
    if meta is None:
        return True
    if not isinstance(meta, dict) or set(meta) - set(_ALLOWED_TOOL_META_FIELDS):
        return False
    return all(
        isinstance(value, dict)
        and not (set(value) - _ALLOWED_TOOL_META_FIELDS[namespace])
        for namespace, value in meta.items()
    )


def _normalize_category(category: str) -> str:
    value = str(category)
    return value if value in _FAILURE_CATEGORIES else "internal_error"


def _stable_compatibility(snapshot: dict[str, Any]) -> bool:
    return bool(
        snapshot.get("initialized")
        and snapshot.get("last_discovery_stable")
        and snapshot.get("compatibility_status")
        in {"exact", "partial", "incompatible"}
    )


def _recommended_action(compatibility_status: str) -> str:
    if compatibility_status == "exact":
        return (
            "The observed MCP contract matches reviewed evidence. Verify the "
            "running upstream image digest and revision independently during "
            "deployment; MCP discovery does not observe artifact provenance."
        )
    if compatibility_status == "partial":
        return (
            "Review quarantined, missing, and unreviewed tool contracts; "
            "matching reads remain available."
        )
    return (
        "Review the incompatible contracts or roll back to the last compatible "
        "upstream version."
    )


def _public_failure(category: str) -> tuple[str, bool]:
    if category in {"argument_validation", "invalid_request"}:
        return "invalid_request", False
    if category == "capability_unavailable":
        return "unsupported_operation", False
    if category == "entity_not_found":
        return "entity_not_found", False
    if category == "automation_not_found":
        return "automation_not_found", False
    if category == "resource_not_found":
        return "resource_not_found", False
    if category == "authentication_failed":
        return "authentication_failure", False
    if category == "prohibited_delegation":
        return "provider_prohibited", False
    if category == "timeout":
        return "provider_timeout", True
    if category in {
        "not_configured",
        "not_initialized",
        "connection_failed",
        "endpoint_rejected",
        "upstream_version_mismatch",
        "server_identity_mismatch",
        "unsupported_protocol_version",
        "schema_mismatch",
    }:
        return "provider_unavailable", category == "connection_failed"
    return "provider_error", category in {"upstream_error"}


def _safe_failure_message(
    category: str,
    upstream_tool: str | None = None,
) -> str:
    domain_message = _UPSTREAM_DOMAIN_MESSAGES.get(
        (upstream_tool, category)
    )
    if domain_message is not None:
        return domain_message
    return {
        "argument_validation": "The request does not match the reviewed upstream schema.",
        "invalid_request": "The reviewed upstream provider rejected the request arguments.",
        "capability_unavailable": (
            "The reviewed upstream capability is unavailable in this deployment."
        ),
        "prohibited_delegation": "The upstream tool is not approved for automatic read delegation.",
        "timeout": "The reviewed upstream read timed out.",
        "response_too_large": "The upstream response exceeded the safe response bound.",
        "connection_failed": "The reviewed upstream read provider is unavailable.",
        "authentication_failed": "The upstream provider rejected its configured authentication.",
        "protocol_error": "The upstream provider returned an incompatible MCP response.",
        "upstream_error": "The upstream read could not be completed.",
    }.get(category, "The reviewed upstream read provider could not complete the request.")


def _reject_duplicate_json_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    """Build one JSON object while rejecting ambiguity at every nesting level."""

    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object member")
        value[key] = item
    return value


def _reject_non_finite_json_constant(_value: str) -> None:
    """Reject Python's non-standard NaN and infinity JSON extensions."""

    raise ValueError("non-finite JSON constant")


def _classify_upstream_tool_error(
    upstream_tool: str,
    call_result: dict[str, Any],
    arguments: dict[str, Any] | None = None,
) -> str:
    """Classify only the reviewed 7.14.1 structured error discriminator."""

    content = call_result.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return "upstream_error"
    item = content[0]
    if (
        not isinstance(item, dict)
        or item.get("type") != "text"
        or not isinstance(item.get("text"), str)
    ):
        return "upstream_error"
    text = item["text"]
    try:
        if len(text.encode("utf-8")) > MAX_STRUCTURED_UPSTREAM_ERROR_BYTES:
            return "upstream_error"
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_non_finite_json_constant,
        )
    except (RecursionError, TypeError, UnicodeError, ValueError):
        return "upstream_error"
    if (
        not isinstance(payload, dict)
        or payload.get("success") is not False
        or not isinstance(payload.get("error"), dict)
    ):
        return "upstream_error"
    code = payload["error"].get("code")
    if not isinstance(code, str):
        return "upstream_error"
    if (
        upstream_tool == "ha_get_entity"
        and code == "SERVICE_CALL_FAILED"
        and _reviewed_single_entity_registry_lookup(arguments)
    ):
        # In both compiled releases this exact argument form reaches
        # _get_single_entity(), whose caught registry ValueError is encoded as
        # SERVICE_CALL_FAILED. Resolver, bulk, malformed, and future argument
        # forms remain on the generic fail-closed path.
        return "entity_not_found"
    domain_outcome = _UPSTREAM_DOMAIN_OUTCOMES.get(
        (upstream_tool, code)
    )
    if domain_outcome is not None:
        return domain_outcome
    if code in _UPSTREAM_VALIDATION_CODES:
        return "invalid_request"
    if code in _UPSTREAM_CAPABILITY_CODES:
        return "capability_unavailable"
    if code in _UPSTREAM_AUTHENTICATION_CODES:
        return "authentication_failed"
    if code in _UPSTREAM_CONNECTION_CODES:
        return "connection_failed"
    if code in _UPSTREAM_TIMEOUT_CODES:
        return "timeout"
    if code in _UPSTREAM_INTERNAL_CODES:
        return "upstream_error"
    return "upstream_error"


def _shape_projection(value: Any) -> Any:
    """Project an untrusted result to bounded structural evidence."""

    if isinstance(value, dict):
        return {
            str(name)[:128]: _shape_projection(item)
            for name, item in sorted(value.items(), key=lambda pair: str(pair[0]))[
                :64
            ]
        }
    if isinstance(value, list):
        return [_shape_projection(item) for item in value[:32]]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "unsupported"


def _upstream_error_evidence(call_result: dict[str, Any]) -> dict[str, Any]:
    """Return code-and-shape evidence without reflecting error payload data."""

    content = call_result.get("content")
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or content[0].get("type") != "text"
        or not isinstance(content[0].get("text"), str)
    ):
        return {
            "is_error": True,
            "structured_code": None,
            "shape_fingerprint": schema_fingerprint(
                _shape_projection(call_result)
            ),
        }
    text = content[0]["text"]
    try:
        if len(text.encode("utf-8")) > MAX_STRUCTURED_UPSTREAM_ERROR_BYTES:
            raise ValueError("error envelope exceeds evidence bound")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_non_finite_json_constant,
        )
    except (RecursionError, TypeError, UnicodeError, ValueError):
        return {
            "is_error": True,
            "structured_code": None,
            "shape_fingerprint": schema_fingerprint(
                {"unparseable_bounded_error": True}
            ),
        }
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return {
        "is_error": True,
        "structured_code": (
            code if isinstance(code, str) and len(code) <= 128 else None
        ),
        "shape_fingerprint": schema_fingerprint(_shape_projection(payload)),
    }


def _reviewed_single_entity_registry_lookup(
    arguments: dict[str, Any] | None,
) -> bool:
    if not isinstance(arguments, dict):
        return False
    entity_id = arguments.get("entity_id")
    if (
        not isinstance(entity_id, str)
        or re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", entity_id) is None
    ):
        return False
    return all(
        arguments.get(field) is None
        for field in ("unique_id", "domain", "platform")
    )


def _normalize_upstream_payload(
    call_result: dict[str, Any],
    *,
    server_version: str,
    protocol_version: str,
    upstream_tool: str,
) -> Any:
    response_model = _REVIEWED_SUCCESS_ENVELOPE_MODELS.get(
        (server_version, protocol_version, upstream_tool)
    )
    if response_model == HACS_INFO_RESPONSE_ENVELOPE_MODEL_V1:
        payload = _reviewed_hacs_info_payload(call_result)
        return _normalize_hacs_info_top_level_success(payload)

    structured = call_result.get("structuredContent")
    if structured is not None:
        payload = structured
    else:
        content = call_result.get("content")
        if not isinstance(content, list):
            raise _GatewayFailure("invalid_response", dispatched=True)
        if len(content) == 1 and isinstance(content[0], dict):
            item = content[0]
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                try:
                    payload = json.loads(item["text"])
                except json.JSONDecodeError:
                    payload = item["text"]
            else:
                payload = content
        else:
            payload = content
    return payload


def _reviewed_hacs_info_payload(call_result: dict[str, Any]) -> Any:
    """Decode the exact 8.1 MCP success envelope without ambiguity."""

    content = call_result.get("content")
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or content[0].get("type") != "text"
        or not isinstance(content[0].get("text"), str)
    ):
        raise _GatewayFailure("invalid_response", dispatched=True)
    try:
        text_payload = json.loads(
            content[0]["text"],
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_non_finite_json_constant,
        )
    except (RecursionError, TypeError, UnicodeError, ValueError):
        raise _GatewayFailure("invalid_response", dispatched=True) from None

    structured = call_result.get("structuredContent")
    if structured is not None:
        try:
            structured_canonical = json.dumps(
                structured,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            text_canonical = json.dumps(
                text_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (RecursionError, TypeError, UnicodeError, ValueError):
            raise _GatewayFailure(
                "invalid_response", dispatched=True
            ) from None
        if structured_canonical != text_canonical:
            raise _GatewayFailure("invalid_response", dispatched=True)
    return structured if structured is not None else text_payload


def _normalize_hacs_info_top_level_success(payload: Any) -> dict[str, Any]:
    """Restore the exact 8.0 HACS read shape for reviewed 8.1 success data."""

    if not isinstance(payload, dict) or set(payload) != {
        "success",
        "data",
        "metadata",
    }:
        raise _GatewayFailure("invalid_response", dispatched=True)
    data = payload.get("data")
    metadata = payload.get("metadata")
    if (
        payload.get("success") is not True
        or not isinstance(data, dict)
        or not isinstance(metadata, dict)
        or "success" in data
    ):
        raise _GatewayFailure("invalid_response", dispatched=True)
    return {
        "data": {"success": True, **data},
        "metadata": metadata,
    }


def _upstream_completeness(
    policy_entry: UpstreamToolPolicyEntry, payload: Any
) -> tuple[bool, list[str]]:
    """Preserve ha_search's reviewed top-level semantic completeness signal."""

    if policy_entry.upstream_name != "ha_search":
        return False, []
    if not isinstance(payload, dict) or not isinstance(payload.get("partial"), bool):
        return True, ["The upstream search completeness could not be verified."]
    if payload["partial"]:
        return True, ["The upstream search reported partial coverage."]
    return False, []


def _server_version() -> str:
    from ..version import SERVER_VERSION

    return SERVER_VERSION


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_after(seconds: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (
        (datetime.now(timezone.utc) + timedelta(seconds=seconds))
        .isoformat()
        .replace("+00:00", "Z")
    )


UPSTREAM_READ_GATEWAY = UpstreamReadGateway()
