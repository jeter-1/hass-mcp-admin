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
from ..clients.upstream_read import McpReadCatalog, McpReadGatewayTransport
from ..configuration import Settings, parse_upstream_dashboard_endpoint
from ..models import FailureResponse, SuccessResponse
from ..observability import METRICS
from ..request_context import current_request_id, current_telemetry
from ..sanitization import sanitize_untrusted_data
from ..tool_framework import timing_since
from ..upstream_tool_policy import (
    REVIEWED_UPSTREAM_SERVER,
    UpstreamToolPolicy,
    UpstreamToolPolicyEntry,
    catalog_fingerprint,
    load_upstream_tool_policy,
    runtime_annotation_fingerprint,
    runtime_description_fingerprint,
    schema_fingerprint,
)


PROVIDER_ID = "upstream_read_gateway"
ALIAS_PREFIX = "ha_mcp__"
REVIEWED_PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_PROTOCOLS = frozenset({REVIEWED_PROTOCOL_VERSION})
RECONCILIATION_RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
COMPATIBILITY_REPROBE_INTERVAL_SECONDS = 900.0
MAX_QUARANTINE_RECORDS = 26
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
    "ha_mcp": frozenset({"llm_api_exposed", "pinned"}),
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
        "prohibited_delegation",
        "sanitization_failed",
        "internal_error",
    }
)


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

    async def run(self, arguments: dict[str, Any], context: Any = None) -> Any:
        del context
        return await self._gateway.execute(
            exposed_name=self.name,
            arguments=arguments,
            reviewed_schema=self._schema,
            policy_entry=self._entry,
            admission_generation=self._admission_generation,
            contract_fingerprint=self._contract_fingerprint,
        )


AdmissionValidator = Callable[[McpReadCatalog], None]


@dataclass(frozen=True)
class _ContractDecision:
    entry: UpstreamToolPolicyEntry
    observed_tool: dict[str, Any]
    accepted: bool
    reason: str | None
    expected_fingerprint: str
    observed_fingerprint: str


@dataclass(frozen=True)
class _CatalogEvaluation:
    matched: tuple[_ContractDecision, ...]
    missing: tuple[str, ...]
    quarantined: tuple[dict[str, str], ...]
    quarantine_reason_counts: dict[str, int]
    blocked: tuple[dict[str, str], ...]
    unreviewed: tuple[str, ...]


@dataclass(frozen=True)
class _AdmittedRoute:
    entry: UpstreamToolPolicyEntry
    observed_tool: dict[str, Any]
    generation: int
    contract_fingerprint: str
    runtime_description_fingerprint: str
    runtime_annotation_fingerprint: str
    runtime_output_schema_fingerprint: str
    server_version: str
    protocol_version: str


@dataclass
class _RouteLease:
    """One call's immutable route binding and dispatch linearization state."""

    route: _AdmittedRoute
    validator_ran: bool = False
    dispatch_committed: bool = False


class UpstreamReadGateway:
    """Discover and register exact policy-approved pure reads through one provider."""

    def __init__(self) -> None:
        self._transport: McpReadGatewayTransport | Any | None = None
        self._settings: Settings | None = None
        self._known_secrets: tuple[str, ...] = ()
        self._policy: UpstreamToolPolicy | None = None
        self._admission_validator: AdmissionValidator | None = None
        self._registered_server: Any = None
        self._registered_names: set[str] = set()
        self._exposed: dict[str, _AdmittedRoute] = {}
        self._dynamic_capabilities: tuple[dict[str, Any], ...] = ()
        self._admission_generation = 0
        self._live_observation_epoch = 0
        self._latest_live_contract_epoch = 0
        self._latest_live_contract_token: str | None = None
        self._stale_reprobe_retry_armed = False
        self._discovery_in_progress = False
        self._reprobe_event = asyncio.Event()
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
            "version_status": "not_observed",
            "admission_status": "unavailable",
            "protocol_version": None,
            "catalog_fingerprint": None,
            "observed_catalog_fingerprint": None,
            "upstream_advertised_tool_count": 0,
            "observed_advertised_tool_count": 0,
            "reviewed_policy_entry_count": 0,
            "automatic_read_count": 0,
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
            "compatibility_registry_status": "binary_policy_only",
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
        admission_validator: AdmissionValidator | None = None,
    ) -> None:
        self._remove_registered_tools()
        replace_dynamic_upstream_capabilities((), self._empty_state())
        endpoint = parse_upstream_dashboard_endpoint(settings.upstream_dashboard_mcp_url)
        self._settings = settings
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
        self._policy = policy or load_upstream_tool_policy()
        self._admission_validator = admission_validator
        self._admission_generation = 0
        self._live_observation_epoch = 0
        self._latest_live_contract_epoch = 0
        self._latest_live_contract_token = None
        self._stale_reprobe_retry_armed = False
        self._discovery_in_progress = False
        self._reprobe_event.clear()
        self._transport = (
            transport
            if endpoint and transport is not None
            else McpReadGatewayTransport(
                endpoint.url,
                timeout_seconds=settings.ha_timeout_seconds,
                client_version=_server_version(),
            )
            if endpoint
            else None
        )
        counts = self._policy.classification_counts
        self._state = self._empty_state()
        self._state.update(
            {
                "configured": bool(endpoint),
                "reviewed_upstream_version": self._policy.reviewed_upstream_version,
                "reviewed_policy_entry_count": len(self._policy.tools),
                "automatic_read_count": counts["automatic_read"],
                "reviewed_automatic_read_count": counts["automatic_read"],
                "blocked_mixed_tool_count": counts["mixed_or_requires_wrapper"],
                "blocked_write_count": counts["persistent_write"],
                "blocked_physical_high_risk_count": counts[
                    "physical_or_high_risk_action"
                ],
                "prohibited_count": counts["prohibited"],
                "unsupported_count": counts["unsupported"],
                "blocked_classification_counts": {
                    name: counts[name]
                    for name in (
                        "mixed_or_requires_wrapper",
                        "persistent_write",
                        "physical_or_high_risk_action",
                        "prohibited",
                        "unsupported",
                    )
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

        self._registered_server = server
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
            catalog = await self._transport.discover()
            self._validate_identity(catalog.server_name, catalog.server_version, catalog.protocol_version)
            identity_validated = True
            if self._admission_validator is not None:
                self._admission_validator(catalog)
            evaluation = self._validate_catalog(catalog)
            candidate_contract_token = _catalog_contract_token(
                catalog, evaluation
            )
            observed_fingerprint = _safe_catalog_fingerprint(
                list(catalog.tools)
            )
            base_names = {
                tool.name for tool in server._tool_manager.list_tools()
                if tool.name not in self._registered_names
            }
            reviewed_descriptions = (
                self._policy.reviewed_runtime_description_fingerprints_by_name
            )
            reviewed_annotations = (
                self._policy.reviewed_runtime_annotation_fingerprints_by_name
            )
            reviewed_output_schemas = (
                self._policy.reviewed_runtime_output_schema_fingerprints_by_name
            )
            generation = self._admission_generation + 1
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
                    server_version=catalog.server_version,
                    protocol_version=catalog.protocol_version,
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
            full_admission = len(exposed) == self._policy.classification_counts[
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
                    capabilities=tuple(capabilities),
                    generation=generation,
                    catalog=catalog,
                    evaluation=evaluation,
                    observed_fingerprint=observed_fingerprint,
                    collisions=collisions,
                    full_admission=full_admission,
                    compatibility_status=compatibility_status,
                    admission_status=admission_status,
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

    def _publish_discovery_generation(
        self,
        *,
        server: Any,
        dynamic_tools: dict[str, ReviewedUpstreamReadTool],
        exposed: dict[str, _AdmittedRoute],
        capabilities: tuple[dict[str, Any], ...],
        generation: int,
        catalog: McpReadCatalog,
        evaluation: _CatalogEvaluation,
        observed_fingerprint: str | None,
        collisions: list[dict[str, str]],
        full_admission: bool,
        compatibility_status: str,
        admission_status: str,
    ) -> None:
        """Publish one copy-on-write route generation under the state lock."""

        assert self._policy is not None
        automatic_count = self._policy.classification_counts[
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
        self._dynamic_capabilities = capabilities
        self._admission_generation = generation
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
                        self._policy.reviewed_upstream_version
                    ),
                    "version_status": "reviewed_exact",
                    "protocol_version": catalog.protocol_version[:64],
                    "catalog_fingerprint": observed_fingerprint,
                    "observed_catalog_fingerprint": observed_fingerprint,
                    "upstream_advertised_tool_count": len(catalog.tools),
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
                        == self._policy.reviewed_stock_catalog_tool_count
                        and observed_fingerprint
                        == self._policy.reviewed_stock_catalog_fingerprint
                    ),
                    "stale_reprobe_retry_armed": False,
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

    def _validate_identity(self, server_name: str, server_version: str, protocol: str) -> None:
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
        if (
            self._policy is None
            or server_version != self._policy.reviewed_upstream_version
        ):
            # A self-advertised descriptor is observation, not release
            # authority. Contract-level reconciliation is permitted only
            # after the binary policy (or a future verified registry profile)
            # explicitly admits this exact release.
            raise DashboardTransportError("upstream_version_mismatch")
        if protocol not in SUPPORTED_PROTOCOLS:
            raise DashboardTransportError("unsupported_protocol_version")

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
        self, catalog: McpReadCatalog
    ) -> _CatalogEvaluation:
        assert self._policy is not None
        policy = self._policy.by_name
        observed_reviewed: dict[str, list[dict[str, Any]]] = {}
        unreviewed: list[str] = []
        unreviewed_occurrences: Counter[str] = Counter()
        for item in catalog.tools:
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(item, dict):
                raise DashboardTransportError("invalid_response")
            if (
                not isinstance(name, str)
                or not _OBSERVED_TOOL_NAME.fullmatch(name)
            ):
                unreviewed.append(self._safe_observed_tool_name(name))
                continue
            if name in policy:
                observed_reviewed.setdefault(name, []).append(item)
                continue
            unreviewed_occurrences[name] += 1
            safe_name = self._safe_observed_tool_name(name)
            if unreviewed_occurrences[name] > 1:
                safe_name = f"{safe_name} [duplicate]"
            unreviewed.append(safe_name)
        missing_reviewed_reads: list[str] = []
        matched: list[_ContractDecision] = []
        quarantined: list[dict[str, str]] = []
        quarantine_reasons: Counter[str] = Counter()
        blocked: list[dict[str, str]] = []
        reviewed_descriptions = (
            self._policy.reviewed_runtime_description_fingerprints_by_name
        )
        reviewed_annotations = (
            self._policy.reviewed_runtime_annotation_fingerprints_by_name
        )
        reviewed_output_schemas = (
            self._policy.reviewed_runtime_output_schema_fingerprints_by_name
        )
        for entry in self._policy.tools:
            observed = observed_reviewed.get(entry.upstream_name, [])
            if not observed:
                if entry.classification == "automatic_read":
                    missing_reviewed_reads.append(entry.upstream_name)
                continue
            if entry.classification == "automatic_read":
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
                )
                if decision.accepted:
                    matched.append(decision)
                else:
                    reason = decision.reason or "contract_mismatch"
                    quarantine_reasons[reason] += 1
                    quarantined.append(
                        {
                            "upstream_name": entry.upstream_name,
                            "reason": reason,
                            "expected_fingerprint": decision.expected_fingerprint,
                            "observed_fingerprint": decision.observed_fingerprint,
                        }
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
        live_contract_failure: dict[str, str],
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
                    ] = self._safe_version_evidence(
                        catalog.server_version
                    )
                    telemetry.audit_context[
                        "upstream_identity_status"
                    ] = "observed"
                try:
                    self._validate_identity(
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
                try:
                    live_evaluation = self._validate_catalog(catalog)
                    live_contract_token = _catalog_contract_token(
                        catalog, live_evaluation
                    )
                except DashboardTransportError:
                    self._advance_live_observation_epoch()
                    raise DashboardTransportError(
                        "schema_mismatch"
                    ) from None

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
                        }
                    )
                    raise DashboardTransportError("schema_mismatch")

                self._record_matching_version_observation(
                    exposed_name=exposed_name,
                    mapping=mapping,
                    catalog=catalog,
                    live_contract_token=live_contract_token,
                )
                # This is the dispatch linearization point. A route retired
                # before the same-session checks complete can never reach
                # tools/call. Publication after this point may proceed without
                # waiting; the immutable leased route may finish but cannot
                # update or revive a newer generation.
                with self._lock:
                    if self._exposed.get(exposed_name) is not mapping:
                        raise DashboardTransportError(
                            "prohibited_delegation"
                        )
                    lease.dispatch_committed = True
                if telemetry:
                    telemetry.audit_context[
                        "upstream_identity_status"
                    ] = "accepted"

            exchange = await transport.execute_read(
                policy_entry.upstream_name,
                dict(arguments),
                timeout_seconds=policy_entry.timeout_seconds,
                catalog_validator=validate_live_catalog,
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
        finally:
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
        live_contract_failure: dict[str, str] = {}
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
                raise _GatewayFailure("upstream_error", dispatched=True)
            payload = _normalize_upstream_payload(exchange.call_result)
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
            return SuccessResponse(
                operation=exposed_name,
                summary="Completed a reviewed pure-read operation through the upstream gateway.",
                data=sanitation.value,
                warnings=warnings,
                metadata={
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
                },
                timing=timing_since(started),
                request_id=current_request_id(),
            ).to_json(response_limit)
        except _GatewayFailure as exc:
            mapping = route_context.get("mapping")
            route_was_admitted = bool(route_context.get("admitted"))
            category = _normalize_category(exc.category)
            if exc.dispatched:
                METRICS.record_provider_result(PROVIDER_ID, "failed", dispatched=True)
            with self._lock:
                route_is_current = (
                    route_was_admitted
                    and mapping is not None
                    and self._exposed.get(exposed_name) is mapping
                )
            if not route_was_admitted or route_is_current:
                self._record_failure(category, discovery=False)
            else:
                # Preserve the historical count without allowing a retired
                # in-flight generation to overwrite the newer generation's
                # live failure or availability state.
                with self._lock:
                    self._state["failure_counts"][category] += 1
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
            code, retryable = _public_failure(category)
            if telemetry:
                telemetry.error_code = code
                telemetry.result_status = "failure"
                telemetry.completeness = "failed"
                if category == "timeout":
                    telemetry.timeout_occurred = True
            return FailureResponse(
                operation=exposed_name,
                error="UpstreamReadGatewayError",
                error_code=code,
                message=_safe_failure_message(category),
                details={"failure_category": category},
                retryable=retryable,
                metadata={
                    "provider": PROVIDER_ID,
                    "upstream_tool": policy_entry.upstream_name,
                    "classification": "automatic_read",
                    "upstream_dispatch_occurred": exc.dispatched,
                    "fallback": "none",
                    "fallback_occurred": False,
                },
                timing=timing_since(started),
                request_id=current_request_id(),
            ).to_json(response_limit)

    def _remove_registered_tools(self) -> None:
        with self._lock:
            if self._registered_server is not None:
                self._replace_registered_tools(self._registered_server, {})
            self._registered_names = set()
            self._exposed = {}
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
        failure: dict[str, str],
    ) -> bool:
        """Remove one live-drifted route and keep unrelated matches available."""

        with self._lock:
            if self._exposed.get(exposed_name) is not mapping:
                return False
            if self._registered_server is not None:
                replacement = dict(
                    self._registered_server._tool_manager._tools
                )
                replacement.pop(exposed_name, None)
                self._registered_server._tool_manager._tools = replacement
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

        replacement = dict(server._tool_manager._tools)
        for name in self._registered_names:
            replacement.pop(name, None)
        replacement.update(dynamic_tools)
        server._tool_manager._tools = replacement

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
                self._state["recommended_action"] = (
                    "Restore the reviewed upstream identity and protocol, or "
                    "roll back to the last compatible upstream version."
                    if self._state["admission_status"]
                    == "blocked_incompatible_upstream"
                    else "Restore upstream connectivity or authentication."
                )
                if not self._state["reconciliation_active"]:
                    self._state["reconciliation_status"] = "idle"

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
            return value


class _GatewayFailure(RuntimeError):
    def __init__(self, category: str, *, dispatched: bool):
        super().__init__("The reviewed upstream read operation failed.")
        self.category = category
        self.dispatched = dispatched


def _safe_catalog_fingerprint(
    tools: list[dict[str, Any]],
) -> str | None:
    """Keep whole-catalog diagnostics from becoming admission authority."""

    try:
        return catalog_fingerprint(tools)
    except (TypeError, ValueError, UnicodeError, OverflowError):
        return None


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
    elif entry.classification != "automatic_read":
        reason = "security_classification_mismatch"
    elif protocol_version not in SUPPORTED_PROTOCOLS:
        reason = "unsupported_protocol_version"
    return _ContractDecision(
        entry=entry,
        observed_tool=observed_tool,
        accepted=reason is None,
        reason=reason,
        expected_fingerprint=schema_fingerprint(expected_contract),
        observed_fingerprint=schema_fingerprint(observed_contract),
    )


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
        return "No compatibility action is required."
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
    if category == "argument_validation":
        return "invalid_request", False
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


def _safe_failure_message(category: str) -> str:
    return {
        "argument_validation": "The request does not match the reviewed upstream schema.",
        "prohibited_delegation": "The upstream tool is not approved for automatic read delegation.",
        "timeout": "The reviewed upstream read timed out.",
        "response_too_large": "The upstream response exceeded the safe response bound.",
        "connection_failed": "The reviewed upstream read provider is unavailable.",
        "authentication_failed": "The upstream provider rejected its configured authentication.",
        "protocol_error": "The upstream provider returned an incompatible MCP response.",
        "upstream_error": "The upstream read could not be completed.",
    }.get(category, "The reviewed upstream read provider could not complete the request.")


def _normalize_upstream_payload(call_result: dict[str, Any]) -> Any:
    structured = call_result.get("structuredContent")
    if structured is not None:
        return structured
    content = call_result.get("content")
    if not isinstance(content, list):
        raise _GatewayFailure("invalid_response", dispatched=True)
    if len(content) == 1 and isinstance(content[0], dict):
        item = content[0]
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            try:
                return json.loads(item["text"])
            except json.JSONDecodeError:
                return item["text"]
    return content


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
