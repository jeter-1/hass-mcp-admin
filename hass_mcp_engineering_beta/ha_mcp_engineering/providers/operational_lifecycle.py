"""Exact reviewed providers for governed reload and restart operations."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import inspect
import json
import re
import threading
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from ..clients.mcp import DashboardTransportError
from ..clients.upstream_read import (
    BeforeDispatchFailure,
    CatalogValidationFailure,
    McpReadCatalog,
    McpReadGatewayTransport,
)
from ..configuration import Settings, parse_upstream_dashboard_endpoint
from ..request_context import current_telemetry
from ..sanitization import sanitize_untrusted_data
from ..upstream_tool_policy import (
    load_reviewed_upstream_release_registry,
    runtime_contract_field_fingerprints,
    runtime_contract_fingerprint,
    runtime_description_fingerprint,
    schema_fingerprint,
    validate_reviewed_release_catalog,
)
from ..version import SERVER_VERSION


PROVIDER_ID = "upstream_operational_lifecycle"
MAX_RESULT_BYTES = 60_000
MAX_LIFECYCLE_ADDON_STRUCTURED_TEXT_BYTES = 250_000
LIFECYCLE_ADDON_RESPONSE_MODEL_TEXT_V1 = (
    "ha-mcp-lifecycle-addon-text-json-v1"
)
LIFECYCLE_ADDON_RESPONSE_MODEL_STRUCTURED_V1 = (
    "ha-mcp-lifecycle-addon-structured-content-v1"
)
LIFECYCLE_ADDON_RESPONSE_ENVELOPE_TEXT = "mcp-text-content-v1"
LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED = (
    "mcp-direct-structured-content-v1"
)
RELOAD_TOOL = "ha_reload_core"
ADDON_ACTION_TOOL = "ha_manage_addon"
ADDON_READ_TOOL = "ha_get_addon"
HA_RESTART_TOOL = "ha_restart"
RELOAD_TARGETS = {
    "automation": "automations",
    "script": "scripts",
    "input_boolean": "input_booleans",
    "input_number": "input_numbers",
}
RELOAD_SERVICES = {
    "automations": "automation.reload",
    "scripts": "script.reload",
    "input_booleans": "input_boolean.reload",
    "input_numbers": "input_number.reload",
}
_SAFE_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_SAFE_REPOSITORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")
_SAFE_VERSION = re.compile(
    r"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}"
    r"(?:[-+][A-Za-z0-9.-]{1,32})?$"
)
_SENSITIVE_TOKEN_FRAGMENT = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,})"
)
_CREDENTIAL_URL_FRAGMENT = re.compile(
    r"(?:(?:[A-Za-z][A-Za-z0-9+.-]*:)?//)"
    r"[^\s/@]+(?::[^\s/@]*)?@[^\s]+"
)
_SAFE_ENDPOINT_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$"
)
_REVIEWED_ADDON_STATES = frozenset({"started", "stopped"})
_INSTALLED_SUMMARY_FIELDS = frozenset(
    {"total_installed", "running", "stopped", "updates_available"}
)
_TOOL_POLICY = {
    RELOAD_TOOL: "physical_or_high_risk_action",
    ADDON_ACTION_TOOL: "mixed_or_requires_wrapper",
    ADDON_READ_TOOL: "mixed_or_requires_wrapper",
    HA_RESTART_TOOL: "physical_or_high_risk_action",
}


@dataclass(frozen=True)
class LifecycleAddonResponseContract:
    """Exact reviewed response envelope for one admitted upstream release."""

    model: str
    envelope_variant: str


_LIFECYCLE_ADDON_RESPONSE_CONTRACTS = {
    (
        "ha-mcp-v7.14.1-68f386d9",
        "7.14.1",
        "2025-03-26",
    ): LifecycleAddonResponseContract(
        model=LIFECYCLE_ADDON_RESPONSE_MODEL_TEXT_V1,
        envelope_variant=LIFECYCLE_ADDON_RESPONSE_ENVELOPE_TEXT,
    ),
    (
        "ha-mcp-v7.14.2-7917b2d3",
        "7.14.2",
        "2025-03-26",
    ): LifecycleAddonResponseContract(
        model=LIFECYCLE_ADDON_RESPONSE_MODEL_TEXT_V1,
        envelope_variant=LIFECYCLE_ADDON_RESPONSE_ENVELOPE_TEXT,
    ),
    (
        "ha-mcp-v8.0.0-d65630f6",
        "8.0.0",
        "2025-03-26",
    ): LifecycleAddonResponseContract(
        model=LIFECYCLE_ADDON_RESPONSE_MODEL_STRUCTURED_V1,
        envelope_variant=LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED,
    ),
}


class OperationalLifecycleProviderError(RuntimeError):
    """Bounded provider failure carrying authoritative dispatch evidence."""

    def __init__(self, category: str, *, dispatched: bool) -> None:
        super().__init__("The governed operational provider could not complete.")
        self.category = category
        self.dispatched = dispatched


@dataclass(frozen=True)
class OperationalProviderEvidence:
    provider: str
    server_name: str
    server_version: str
    protocol_version: str
    compatibility_entry_id: str
    source_commit: str
    image_index_digest: str
    catalog_fingerprint: str
    normalized_catalog_fingerprint: str
    aggregate_fingerprint_model: str
    runtime_contract_fingerprint_model: str
    lifecycle_addon_response_contract_model: str
    lifecycle_addon_response_envelope_variant: str
    tool_contract_fingerprints: dict[str, str]
    argument_constraints: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "server_name": self.server_name,
            "server_version": self.server_version,
            "protocol_version": self.protocol_version,
            "compatibility_entry_id": self.compatibility_entry_id,
            "reviewed_source_commit": self.source_commit,
            "reviewed_image_index_digest": self.image_index_digest,
            "catalog_fingerprint": self.catalog_fingerprint,
            "normalized_catalog_fingerprint": (
                self.normalized_catalog_fingerprint
            ),
            "aggregate_fingerprint_model": (
                self.aggregate_fingerprint_model
            ),
            "runtime_contract_fingerprint_model": (
                self.runtime_contract_fingerprint_model
            ),
            "lifecycle_addon_response_contract_model": (
                self.lifecycle_addon_response_contract_model
            ),
            "lifecycle_addon_response_envelope_variant": (
                self.lifecycle_addon_response_envelope_variant
            ),
            "tool_contract_fingerprints": dict(
                self.tool_contract_fingerprints
            ),
            "argument_constraints": dict(self.argument_constraints),
            "runtime_artifact_observed": False,
            "fallback": "none",
            "fallback_occurred": False,
        }


@dataclass(frozen=True)
class OperationalDispatchResult:
    provider_evidence: OperationalProviderEvidence
    provider_response_received: bool
    response: dict[str, Any]


@dataclass
class OperationalProviderState:
    configured: bool = False
    operational_status: str = "unconfigured"
    probe_counts: Counter[str] = field(default_factory=Counter)
    request_counts: Counter[str] = field(default_factory=Counter)
    dispatch_counts: Counter[str] = field(default_factory=Counter)
    dispatch_success_counts: Counter[str] = field(default_factory=Counter)
    failure_counts: Counter[str] = field(default_factory=Counter)
    domain_outcome_counts: Counter[str] = field(default_factory=Counter)
    last_failure_category: str | None = None
    last_success_at: str | None = None
    selected_compatibility_entry_id: str | None = None
    observed_upstream_version: str | None = None
    runtime_contract_fingerprint_model: str | None = None
    lifecycle_addon_response_contract_model: str | None = None
    lifecycle_addon_response_envelope_variant: str | None = None
    lifecycle_addon_response_diagnostics: dict[str, Any] | None = None
    runtime_contract_mismatch_diagnostics: dict[str, Any] | None = None
    catalog_validation: dict[str, Any] | None = None
    fallback_count: int = 0


BeforeDispatch = Callable[[], None | Awaitable[None]]


class ReviewedOperationalLifecycleProvider:
    """Expose only exact reload/restart forms over reviewed upstream tools."""

    def __init__(self) -> None:
        self._transport: McpReadGatewayTransport | Any | None = None
        self._state = OperationalProviderState()
        self._lock = threading.Lock()
        self._configured_endpoint_host: str | None = None

    def configure(
        self,
        settings: Settings,
        *,
        transport: McpReadGatewayTransport | Any | None = None,
    ) -> None:
        endpoint = parse_upstream_dashboard_endpoint(
            settings.upstream_dashboard_mcp_url
        )
        endpoint_host = urlsplit(endpoint.url).hostname if endpoint else None
        self._configured_endpoint_host = (
            endpoint_host
            if isinstance(endpoint_host, str)
            and _SAFE_ENDPOINT_HOST.fullmatch(endpoint_host)
            else None
        )
        self._transport = (
            transport
            if endpoint and transport is not None
            else McpReadGatewayTransport(
                endpoint.url,
                timeout_seconds=settings.ha_timeout_seconds,
                client_version=SERVER_VERSION,
            )
            if endpoint
            else None
        )
        self._state = OperationalProviderState(
            configured=bool(endpoint),
            operational_status="unknown" if endpoint else "unconfigured",
        )

    async def probe(
        self, operation: str
    ) -> OperationalProviderEvidence:
        required = self._required_tools(operation)
        with self._lock:
            self._state.probe_counts[operation] += 1
        if self._transport is None:
            self._fail("provider_unavailable", dispatched=False)
        try:
            catalog = await self._transport.discover()
            evidence = self._validate_catalog(
                catalog, required, operation=operation
            )
            self._record_success(evidence)
            return evidence
        except OperationalLifecycleProviderError:
            raise
        except DashboardTransportError as exc:
            self._fail(_transport_category(exc.category), dispatched=False)
        except Exception:
            self._fail("provider_error", dispatched=False)

    async def reload(
        self,
        target: str,
        *,
        before_dispatch: BeforeDispatch,
    ) -> OperationalDispatchResult:
        upstream_target = RELOAD_TARGETS.get(target)
        if upstream_target is None:
            self._fail("invalid_request", dispatched=False)
        return await self._execute(
            "controlled_reload",
            RELOAD_TOOL,
            {"target": upstream_target},
            before_dispatch=before_dispatch,
            timeout_seconds=90.0,
        )

    async def restart_addon(
        self,
        slug: str,
        *,
        before_dispatch: BeforeDispatch,
    ) -> OperationalDispatchResult:
        if not isinstance(slug, str) or not _SAFE_SLUG.fullmatch(slug):
            self._fail("invalid_request", dispatched=False)
        return await self._execute(
            "restart_addon",
            ADDON_ACTION_TOOL,
            {"slug": slug, "action": "restart"},
            before_dispatch=before_dispatch,
            timeout_seconds=150.0,
        )

    async def restart_home_assistant(
        self,
        *,
        before_dispatch: BeforeDispatch,
    ) -> OperationalDispatchResult:
        return await self._execute(
            "restart_home_assistant",
            HA_RESTART_TOOL,
            {"confirm": True},
            before_dispatch=before_dispatch,
            timeout_seconds=90.0,
        )

    async def get_addon(self, slug: str) -> dict[str, Any]:
        """Read and project one exact add-on without exposing options/secrets."""
        if not isinstance(slug, str) or not _SAFE_SLUG.fullmatch(slug):
            self._fail("invalid_request", dispatched=False)
        if self._transport is None:
            self._fail("provider_unavailable", dispatched=False)
        evidence: OperationalProviderEvidence | None = None

        def validate(catalog: McpReadCatalog) -> None:
            nonlocal evidence
            candidate = self._validate_catalog(
                catalog,
                (ADDON_READ_TOOL,),
                operation="restart_addon",
            )
            if evidence is None:
                evidence = candidate
                return
            if (
                _provider_contract_identity(candidate)
                != _provider_contract_identity(evidence)
            ):
                self._record_addon_response_diagnostics(
                    identity_mismatch_fields=("provider_contract",),
                )
                self._fail(
                    "addon_response_contract_mismatch",
                    dispatched=False,
                )

        try:
            inventory_exchange = await self._execute_observed_read(
                ADDON_READ_TOOL,
                {"source": "installed", "include_stats": False},
                timeout_seconds=60.0,
                catalog_validator=validate,
            )
            assert evidence is not None
            inventory = self._decode_addon_response(
                inventory_exchange.call_result,
                evidence=evidence,
                dispatched=False,
                error_category=_upstream_error_category,
            )
            addons = inventory.get("addons")
            summary = inventory.get("summary")
            missing_paths = tuple(
                f"/{name}"
                for name in ("addons", "summary")
                if name not in inventory
            )
            invalid_paths = tuple(
                f"/{name}"
                for name, value, expected_type in (
                    ("addons", addons, list),
                    ("summary", summary, dict),
                )
                if name in inventory and not isinstance(value, expected_type)
            )
            if missing_paths or invalid_paths:
                self._record_addon_response_diagnostics(
                    missing_paths=missing_paths,
                    invalid_paths=invalid_paths,
                )
                self._fail(
                    "addon_response_contract_mismatch",
                    dispatched=False,
                )
            (
                missing_inventory_paths,
                invalid_inventory_paths,
            ) = _installed_inventory_diagnostic_paths(
                addons,
                summary,
            )
            incomplete_inventory_paths = _incomplete_response_paths(
                inventory
            )
            incomplete_summary_paths = _incomplete_response_paths(
                summary,
                prefix="/summary",
            )
            if (
                missing_inventory_paths
                or invalid_inventory_paths
                or incomplete_inventory_paths
                or incomplete_summary_paths
            ):
                self._record_addon_response_diagnostics(
                    observed_cardinality=min(len(addons), 2),
                    missing_paths=missing_inventory_paths,
                    invalid_paths=tuple(
                        dict.fromkeys(
                            (
                                *invalid_inventory_paths,
                                *incomplete_inventory_paths,
                                *incomplete_summary_paths,
                            )
                        )
                    ),
                )
                self._fail(
                    "addon_response_contract_mismatch",
                    dispatched=False,
                )
            matches = [
                item
                for item in addons
                if isinstance(item, dict) and item.get("slug") == slug
            ]
            if not matches:
                assert evidence is not None
                self._record_addon_response_diagnostics(
                    expected_cardinality=1,
                    observed_cardinality=0,
                )
                self._fail("addon_not_found", dispatched=False)
            if len(matches) != 1:
                self._record_addon_response_diagnostics(
                    expected_cardinality=1,
                    observed_cardinality=min(len(matches), 2),
                    invalid_paths=("/addons",),
                )
                self._fail(
                    "addon_response_contract_mismatch",
                    dispatched=False,
                )
            assert evidence is not None
            inventory_addon = _project_addon_identity(
                matches[0],
                required_slug=slug,
                require_installed=True,
            )
            if inventory_addon is None:
                self._record_addon_response_diagnostics(
                    expected_cardinality=1,
                    observed_cardinality=1,
                    invalid_paths=("/addons/0",),
                )
                self._fail(
                    "addon_response_contract_mismatch",
                    dispatched=False,
                )
            upstream_identity = _bind_upstream_addon_identity(
                addons,
                endpoint_host=self._configured_endpoint_host,
                evidence=evidence,
            )

            exchange = await self._execute_observed_read(
                ADDON_READ_TOOL,
                {"slug": slug},
                timeout_seconds=60.0,
                catalog_validator=validate,
            )
            payload = self._decode_addon_response(
                exchange.call_result,
                evidence=evidence,
                dispatched=False,
                error_category=_addon_error_category,
            )
            addon = payload.get("addon")
            detail_addon = _project_addon_identity(
                addon,
                required_slug=slug,
            )
            if detail_addon is None:
                self._record_addon_response_diagnostics(
                    expected_cardinality=1,
                    observed_cardinality=(
                        1 if isinstance(addon, dict) else 0
                    ),
                    missing_paths=(
                        ("/addon",) if "addon" not in payload else ()
                    ),
                    invalid_paths=(
                        ("/addon",) if "addon" in payload else ()
                    ),
                )
                self._fail(
                    "addon_response_contract_mismatch",
                    dispatched=False,
                )
            identity_mismatches = tuple(
                name
                for name in (
                    "slug",
                    "name",
                    "version",
                    "state",
                    "repository",
                    "update_available",
                )
                if inventory_addon[name] != detail_addon[name]
            )
            if identity_mismatches:
                self._record_addon_response_diagnostics(
                    expected_cardinality=1,
                    observed_cardinality=1,
                    identity_mismatch_fields=identity_mismatches,
                )
                self._fail(
                    "addon_response_contract_mismatch",
                    dispatched=False,
                )
            self._record_success(evidence)
            return {
                **detail_addon,
                "provider": evidence.as_dict(),
                "upstream_addon_identity": upstream_identity,
            }
        except OperationalLifecycleProviderError as exc:
            if exc.category == "addon_not_found" and evidence is not None:
                self._record_success(evidence)
            raise
        except CatalogValidationFailure as exc:
            if isinstance(exc.cause, OperationalLifecycleProviderError):
                raise exc.cause
            self._fail("provider_error", dispatched=False)
        except DashboardTransportError as exc:
            self._fail(_transport_category(exc.category), dispatched=False)
        except Exception:
            self._fail("provider_error", dispatched=False)

    async def authoritative_provider_identity(self) -> dict[str, str]:
        """Return one installed slug bound to this exact admitted MCP endpoint.

        The candidate is accepted only after the existing full installed
        inventory plus exact detail read prove the reviewed Supervisor-DNS
        binding.  The returned hash contains no endpoint or credentials.
        """

        host = self._configured_endpoint_host
        if not isinstance(host, str) or not host:
            self._fail("upstream_addon_identity_unavailable", dispatched=False)
        candidate = host.replace("-", "_")
        detail = await self.get_addon(candidate)
        identity = detail.get("upstream_addon_identity")
        if (
            not isinstance(identity, dict)
            or identity.get("status") != "bound"
            or identity.get("slug") != detail.get("slug")
        ):
            self._fail("upstream_addon_identity_unavailable", dispatched=False)
        evidence = {
            "slug": identity["slug"],
            "name": identity.get("name"),
            "installed_version": identity.get("installed_version"),
            "repository": identity.get("repository"),
            "identity_source": identity.get("identity_source"),
            "admission_evidence": identity.get("admission_evidence"),
        }
        return {
            "slug": str(identity["slug"]),
            "evidence_hash": hashlib.sha256(
                json.dumps(
                    evidence,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest(),
        }

    async def _execute_observed_read(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float,
        catalog_validator: Callable[[McpReadCatalog], None],
    ) -> Any:
        """Execute one reviewed read with truthful per-request attribution."""

        telemetry = current_telemetry()
        started = time.perf_counter()
        if telemetry:
            telemetry.begin_upstream_attempt(started)
        try:
            return await self._transport.execute_read(
                tool_name,
                arguments,
                timeout_seconds=timeout_seconds,
                catalog_validator=catalog_validator,
            )
        finally:
            if telemetry:
                finished = time.perf_counter()
                telemetry.finish_upstream_attempt(
                    finished,
                    (finished - started) * 1000,
                )

    async def _execute(
        self,
        operation: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        before_dispatch: BeforeDispatch,
        timeout_seconds: float,
    ) -> OperationalDispatchResult:
        if self._transport is None:
            self._fail("provider_unavailable", dispatched=False)
        dispatched = False
        evidence: OperationalProviderEvidence | None = None

        async def persist_dispatch() -> None:
            nonlocal dispatched
            prepared = before_dispatch()
            if inspect.isawaitable(prepared):
                await prepared
            dispatched = True
            with self._lock:
                self._state.dispatch_counts[operation] += 1

        def validate(catalog: McpReadCatalog) -> None:
            nonlocal evidence
            evidence = self._validate_catalog(
                catalog, (tool_name,), operation=operation
            )

        with self._lock:
            self._state.request_counts[operation] += 1
        try:
            exchange = await self._transport.execute_read(
                tool_name,
                arguments,
                timeout_seconds=timeout_seconds,
                catalog_validator=validate,
                before_dispatch=persist_dispatch,
            )
            if evidence is None:
                self._fail(
                    "internal_invariant_violation", dispatched=dispatched
                )
            payload = self._decode(
                exchange.call_result, dispatched=dispatched
            )
            normalized = self._normalize_success(
                operation, payload, arguments
            )
            assert evidence is not None
            self._record_success(evidence)
            with self._lock:
                self._state.dispatch_success_counts[operation] += 1
            return OperationalDispatchResult(
                provider_evidence=evidence,
                provider_response_received=True,
                response=normalized,
            )
        except OperationalLifecycleProviderError:
            raise
        except BeforeDispatchFailure as exc:
            raise exc.cause
        except CatalogValidationFailure as exc:
            if isinstance(exc.cause, OperationalLifecycleProviderError):
                raise exc.cause
            self._fail("provider_error", dispatched=False)
        except DashboardTransportError as exc:
            category = _transport_category(exc.category)
            if dispatched and category in {
                "provider_timeout",
                "provider_unavailable",
            }:
                category = "indeterminate_dispatch"
            self._fail(category, dispatched=dispatched)
        except Exception:
            self._fail(
                (
                    "indeterminate_dispatch"
                    if dispatched
                    else "provider_error"
                ),
                dispatched=dispatched,
            )

    def _validate_catalog(
        self,
        catalog: McpReadCatalog,
        required_tools: tuple[str, ...],
        *,
        operation: str,
    ) -> OperationalProviderEvidence:
        registry = load_reviewed_upstream_release_registry()
        with self._lock:
            self._state.selected_compatibility_entry_id = None
            self._state.observed_upstream_version = (
                catalog.server_version
                if isinstance(catalog.server_version, str)
                and _SAFE_VERSION.fullmatch(catalog.server_version)
                else None
            )
            self._state.runtime_contract_fingerprint_model = None
            self._state.lifecycle_addon_response_contract_model = None
            self._state.lifecycle_addon_response_envelope_variant = None
            self._state.lifecycle_addon_response_diagnostics = None
            self._state.catalog_validation = None
        if catalog.server_name != "ha-mcp":
            self._fail("server_identity_mismatch", dispatched=False)
        release = registry.by_version.get(catalog.server_version)
        if release is None:
            self._fail("upstream_version_mismatch", dispatched=False)
        runtime_model = release.runtime_contract_fingerprint_model
        with self._lock:
            self._state.selected_compatibility_entry_id = release.entry_id
            self._state.observed_upstream_version = catalog.server_version
            self._state.runtime_contract_fingerprint_model = runtime_model
        if catalog.protocol_version not in release.allowed_protocol_versions:
            self._fail("unsupported_protocol_version", dispatched=False)
        response_contract = _lifecycle_addon_response_contract(
            entry_id=release.entry_id,
            upstream_version=catalog.server_version,
            protocol_version=catalog.protocol_version,
        )
        if response_contract is None:
            self._fail(
                "unsupported_response_contract_model",
                dispatched=False,
            )
        with self._lock:
            self._state.lifecycle_addon_response_contract_model = (
                response_contract.model
            )
            self._state.lifecycle_addon_response_envelope_variant = (
                response_contract.envelope_variant
            )
        catalog_validation = validate_reviewed_release_catalog(
            release,
            observed_server_name=catalog.server_name,
            observed_upstream_version=catalog.server_version,
            observed_protocol_version=catalog.protocol_version,
            tools=catalog.tools,
        )
        with self._lock:
            self._state.catalog_validation = catalog_validation.as_dict()
        if not catalog_validation.valid:
            self._fail("catalog_mismatch", dispatched=False)
        observed_catalog = (
            catalog_validation.observed_raw_catalog_fingerprint
        )
        normalized_catalog_fingerprint = (
            catalog_validation.normalized_catalog_fingerprint
        )
        if (
            observed_catalog is None
            or normalized_catalog_fingerprint is None
        ):
            self._fail("internal_invariant_violation", dispatched=False)

        fingerprints: dict[str, str] = {}
        for tool_name in required_tools:
            tools = [
                item
                for item in catalog.tools
                if isinstance(item, dict) and item.get("name") == tool_name
            ]
            if len(tools) != 1:
                self._fail("required_tool_missing", dispatched=False)
            tool = tools[0]
            expected = release.tool_contracts_by_name[tool_name]
            try:
                observed = {
                    "input_schema_fingerprint": schema_fingerprint(
                        tool.get("inputSchema")
                    ),
                    "description_fingerprint": (
                        runtime_description_fingerprint(
                            tool.get("description")
                        )
                        or schema_fingerprint(
                            {"invalid_description": True}
                        )
                    ),
                    "annotation_fingerprint": schema_fingerprint(
                        {
                            "present": "annotations" in tool,
                            "value": tool.get("annotations"),
                        }
                    ),
                    "output_contract_fingerprint": schema_fingerprint(
                        {
                            "present": "outputSchema" in tool,
                            "value": tool.get("outputSchema"),
                        }
                    ),
                    "runtime_contract_fingerprint": (
                        runtime_contract_fingerprint(
                            tool,
                            model=runtime_model,
                        )
                    ),
                }
            except (TypeError, ValueError, OverflowError):
                self._fail("invalid_response", dispatched=False)
            if (
                observed["runtime_contract_fingerprint"]
                != expected.runtime_contract_fingerprint
            ):
                expected_fields = dict(
                    expected.runtime_contract_field_fingerprints
                )
                observed_fields = runtime_contract_field_fingerprints(tool)
                diff_fields = sorted(
                    pointer
                    for pointer in expected_fields.keys()
                    | observed_fields.keys()
                    if expected_fields.get(pointer)
                    != observed_fields.get(pointer)
                )[:16]
                with self._lock:
                    self._state.runtime_contract_mismatch_diagnostics = {
                        "tool": tool_name,
                        "expected_runtime_contract_fingerprint": (
                            expected.runtime_contract_fingerprint
                        ),
                        "observed_runtime_contract_fingerprint": observed[
                            "runtime_contract_fingerprint"
                        ],
                        "runtime_contract_fingerprint_model": runtime_model,
                        "runtime_contract_diff_fields": diff_fields or ["/"],
                    }
            if (
                expected.policy_classification
                != _TOOL_POLICY[tool_name]
                or expected.reviewed_automatic_read
                or any(
                    observed[name] != getattr(expected, name)
                    for name in observed
                )
            ):
                self._fail(
                    "reviewed_contract_mismatch", dispatched=False
                )
            fingerprints[tool_name] = (
                expected.runtime_contract_fingerprint
            )

        return OperationalProviderEvidence(
            provider=PROVIDER_ID,
            server_name=catalog.server_name,
            server_version=catalog.server_version,
            protocol_version=catalog.protocol_version,
            compatibility_entry_id=release.entry_id,
            source_commit=release.source_commit,
            image_index_digest=release.image_index_digest,
            catalog_fingerprint=observed_catalog,
            normalized_catalog_fingerprint=(
                normalized_catalog_fingerprint
            ),
            aggregate_fingerprint_model=(
                catalog_validation.aggregate_fingerprint_model
            ),
            runtime_contract_fingerprint_model=runtime_model,
            lifecycle_addon_response_contract_model=(
                response_contract.model
            ),
            lifecycle_addon_response_envelope_variant=(
                response_contract.envelope_variant
            ),
            tool_contract_fingerprints=fingerprints,
            argument_constraints=self._constraints(operation),
        )

    @staticmethod
    def _required_tools(operation: str) -> tuple[str, ...]:
        return {
            "controlled_reload": (RELOAD_TOOL,),
            "restart_addon": (ADDON_ACTION_TOOL, ADDON_READ_TOOL),
            "restart_home_assistant": (HA_RESTART_TOOL,),
        }.get(operation, ())

    @staticmethod
    def _constraints(operation: str) -> dict[str, Any]:
        if operation == "controlled_reload":
            return {
                "target_allowlist": sorted(RELOAD_TARGETS),
                "entry_id_allowed": False,
                "reload_all_allowed": False,
                "arbitrary_arguments_allowed": False,
            }
        if operation == "restart_addon":
            return {
                "action": "restart",
                "slug": "exact_planned_value",
                "other_actions_allowed": False,
                "configuration_mutation_allowed": False,
                "proxy_allowed": False,
                "arbitrary_arguments_allowed": False,
            }
        return {
            "confirm": True,
            "variants_allowed": False,
            "arbitrary_arguments_allowed": False,
        }

    def _decode(
        self,
        result: dict[str, Any],
        *,
        dispatched: bool,
        error_category: Callable[[Any], str] | None = None,
    ) -> dict[str, Any]:
        content = result.get("content")
        if not isinstance(content, list) or len(content) != 1:
            self._fail("invalid_response", dispatched=dispatched)
        item = content[0]
        if (
            not isinstance(item, dict)
            or item.get("type") != "text"
            or not isinstance(item.get("text"), str)
        ):
            self._fail("invalid_response", dispatched=dispatched)
        text = item["text"]
        try:
            if len(text.encode("utf-8")) > MAX_RESULT_BYTES:
                self._fail("invalid_response", dispatched=dispatched)
            payload = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_members,
                parse_constant=_reject_nonfinite,
            )
        except (TypeError, ValueError, UnicodeError, RecursionError):
            self._fail("invalid_response", dispatched=dispatched)
        if not isinstance(payload, dict):
            self._fail("invalid_response", dispatched=dispatched)
        if result.get("isError") or payload.get("success") is False:
            code = (
                payload.get("error", {}).get("code")
                if isinstance(payload.get("error"), dict)
                else None
            )
            self._fail(
                (
                    error_category(code)
                    if error_category is not None
                    else _upstream_error_category(code)
                ),
                dispatched=dispatched,
            )
        return payload

    def _decode_addon_response(
        self,
        result: dict[str, Any],
        *,
        evidence: OperationalProviderEvidence,
        dispatched: bool,
        error_category: Callable[[Any], str],
    ) -> dict[str, Any]:
        """Decode one exact-release add-on detail envelope.

        The exact 8.0.0 add-on returns the same result in a large text item and
        direct structured content.  The reviewed structured model keeps the
        global one-megabyte MCP exchange bound, validates the wire envelope,
        and projects only lifecycle identity fields at the call site.  It does
        not retain or expose the secret-bearing Supervisor detail object.
        """

        if not isinstance(result, dict):
            self._record_addon_response_diagnostics(
                invalid_paths=("/",),
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        model = evidence.lifecycle_addon_response_contract_model
        if model == LIFECYCLE_ADDON_RESPONSE_MODEL_TEXT_V1:
            payload = self._decode(
                result,
                dispatched=dispatched,
                error_category=error_category,
            )
            if "success" not in payload:
                self._record_addon_response_diagnostics(
                    missing_paths=("/success",),
                )
                self._fail(
                    "addon_response_contract_mismatch",
                    dispatched=dispatched,
                )
            if payload.get("success") is not True:
                self._record_addon_response_diagnostics(
                    invalid_paths=("/success",),
                )
                self._fail(
                    "addon_response_contract_mismatch",
                    dispatched=dispatched,
                )
            incomplete_paths = _incomplete_response_paths(payload)
            if incomplete_paths:
                self._record_addon_response_diagnostics(
                    invalid_paths=incomplete_paths,
                )
                self._fail(
                    "addon_response_contract_mismatch",
                    dispatched=dispatched,
                )
            return payload
        if model != LIFECYCLE_ADDON_RESPONSE_MODEL_STRUCTURED_V1:
            self._record_addon_response_diagnostics(
                invalid_paths=("/response_contract_model",),
            )
            self._fail(
                "unsupported_response_contract_model",
                dispatched=dispatched,
            )
        content = result.get("content")
        if "content" not in result:
            self._record_addon_response_diagnostics(
                missing_paths=("/content",),
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        if not isinstance(content, list) or len(content) != 1:
            self._record_addon_response_diagnostics(
                invalid_paths=("/content",),
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        item = content[0]
        if not isinstance(item, dict):
            self._record_addon_response_diagnostics(
                invalid_paths=("/content/0",),
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        missing_item_paths = tuple(
            f"/content/0/{name}"
            for name in ("type", "text")
            if name not in item
        )
        invalid_item_paths = tuple(
            path
            for path, invalid in (
                ("/content/0/type", item.get("type") != "text"),
                (
                    "/content/0/text",
                    not isinstance(item.get("text"), str),
                ),
            )
            if path.rsplit("/", 1)[-1] in item and invalid
        )
        if missing_item_paths or invalid_item_paths:
            self._record_addon_response_diagnostics(
                missing_paths=missing_item_paths,
                invalid_paths=invalid_item_paths,
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        text = item["text"]
        try:
            if (
                len(text.encode("utf-8"))
                > MAX_LIFECYCLE_ADDON_STRUCTURED_TEXT_BYTES
            ):
                raise ValueError("bounded lifecycle detail exceeded")
            text_payload = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_members,
                parse_constant=_reject_nonfinite,
            )
        except (TypeError, ValueError, UnicodeError, RecursionError):
            self._record_addon_response_diagnostics(
                invalid_paths=("/content/0/text",),
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        payload = result.get("structuredContent")
        if "structuredContent" not in result:
            self._record_addon_response_diagnostics(
                missing_paths=("/structuredContent",),
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        if not isinstance(payload, dict):
            self._record_addon_response_diagnostics(
                invalid_paths=("/structuredContent",),
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        if not _json_values_exact(text_payload, payload):
            self._record_addon_response_diagnostics(
                invalid_paths=(
                    "/content/0/text",
                    "/structuredContent",
                ),
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        if result.get("isError") or payload.get("success") is False:
            code = (
                payload.get("error", {}).get("code")
                if isinstance(payload.get("error"), dict)
                else None
            )
            self._fail(
                error_category(code),
                dispatched=dispatched,
            )
        if "success" not in payload:
            self._record_addon_response_diagnostics(
                missing_paths=("/structuredContent/success",),
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        if payload.get("success") is not True:
            self._record_addon_response_diagnostics(
                invalid_paths=("/structuredContent/success",),
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        incomplete_paths = _incomplete_response_paths(
            payload,
            prefix="/structuredContent",
        )
        if incomplete_paths:
            self._record_addon_response_diagnostics(
                invalid_paths=incomplete_paths,
            )
            self._fail(
                "addon_response_contract_mismatch",
                dispatched=dispatched,
            )
        return payload

    def _record_addon_response_diagnostics(
        self,
        *,
        expected_cardinality: int = 1,
        observed_cardinality: int | None = None,
        missing_paths: tuple[str, ...] = (),
        invalid_paths: tuple[str, ...] = (),
        identity_mismatch_fields: tuple[str, ...] = (),
    ) -> None:
        diagnostics = {
            "response_contract_model": (
                self._state.lifecycle_addon_response_contract_model
            ),
            "envelope_variant": (
                self._state.lifecycle_addon_response_envelope_variant
            ),
            "expected_collection_cardinality": expected_cardinality,
            "observed_collection_cardinality": observed_cardinality,
            "missing_semantic_field_paths": list(missing_paths[:8]),
            "invalid_semantic_field_paths": list(invalid_paths[:8]),
            "identity_mismatch_fields": list(
                identity_mismatch_fields[:8]
            ),
            "diagnostics_truncated": (
                len(missing_paths) > 8
                or len(invalid_paths) > 8
                or len(identity_mismatch_fields) > 8
            ),
        }
        with self._lock:
            self._state.lifecycle_addon_response_diagnostics = diagnostics

    def _normalize_success(
        self,
        operation: str,
        payload: dict[str, Any],
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if payload.get("success") is not True:
            self._fail("invalid_response", dispatched=True)
        if operation == "controlled_reload":
            expected_target = arguments["target"]
            expected_service = RELOAD_SERVICES[expected_target]
            if (
                payload.get("target") != expected_target
                or payload.get("service") != expected_service
            ):
                self._fail("invalid_response", dispatched=True)
            return {
                "success": True,
                "target": expected_target,
                "service": expected_service,
            }
        if operation == "restart_addon":
            if (
                payload.get("action") != "restart"
                or payload.get("slug") != arguments["slug"]
            ):
                self._fail("invalid_response", dispatched=True)
            return {
                "success": True,
                "action": "restart",
                "slug": arguments["slug"],
            }
        return {
            "success": True,
            "restart_initiated": True,
        }

    def _record_success(
        self, evidence: OperationalProviderEvidence
    ) -> None:
        with self._lock:
            self._state.operational_status = "available"
            self._state.last_failure_category = None
            self._state.last_success_at = (
                datetime.now(timezone.utc).isoformat()
            )
            self._state.selected_compatibility_entry_id = (
                evidence.compatibility_entry_id
            )
            self._state.observed_upstream_version = (
                evidence.server_version
            )
            self._state.runtime_contract_mismatch_diagnostics = None
            self._state.lifecycle_addon_response_diagnostics = None

    def _fail(self, category: str, *, dispatched: bool) -> None:
        with self._lock:
            if category == "addon_not_found":
                self._state.domain_outcome_counts[category] += 1
            else:
                self._state.failure_counts[category] += 1
                self._state.last_failure_category = category
            if category not in {
                "addon_not_found",
                "invalid_request",
                "resource_not_found",
                "operation_rejected",
            }:
                self._state.operational_status = "unavailable"
        raise OperationalLifecycleProviderError(
            category, dispatched=dispatched
        )

    def health_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "provider": PROVIDER_ID,
                "configured": self._state.configured,
                "operational_status": self._state.operational_status,
                "probe_counts": dict(self._state.probe_counts),
                "request_counts": dict(self._state.request_counts),
                "dispatch_counts": dict(self._state.dispatch_counts),
                "dispatch_success_counts": dict(
                    self._state.dispatch_success_counts
                ),
                "failure_counts": dict(self._state.failure_counts),
                "domain_outcome_counts": dict(
                    self._state.domain_outcome_counts
                ),
                "last_failure_category": (
                    self._state.last_failure_category
                ),
                "last_success_at": self._state.last_success_at,
                "selected_compatibility_entry_id": (
                    self._state.selected_compatibility_entry_id
                ),
                "observed_upstream_version": (
                    self._state.observed_upstream_version
                ),
                "runtime_contract_fingerprint_model": (
                    self._state.runtime_contract_fingerprint_model
                ),
                "lifecycle_addon_response_contract_model": (
                    self._state.lifecycle_addon_response_contract_model
                ),
                "lifecycle_addon_response_envelope_variant": (
                    self._state.lifecycle_addon_response_envelope_variant
                ),
                "lifecycle_addon_response_diagnostics": (
                    deepcopy(
                        self._state.lifecycle_addon_response_diagnostics
                    )
                    if self._state.lifecycle_addon_response_diagnostics
                    is not None
                    else None
                ),
                "runtime_contract_mismatch_diagnostics": (
                    dict(self._state.runtime_contract_mismatch_diagnostics)
                    if self._state.runtime_contract_mismatch_diagnostics
                    is not None
                    else None
                ),
                "catalog_validation": (
                    deepcopy(self._state.catalog_validation)
                    if self._state.catalog_validation is not None
                    else None
                ),
                "fallback_count": self._state.fallback_count,
                "fallback_policy": "none",
            }


def _lifecycle_addon_response_contract(
    *,
    entry_id: str,
    upstream_version: str,
    protocol_version: str,
) -> LifecycleAddonResponseContract | None:
    contract = _LIFECYCLE_ADDON_RESPONSE_CONTRACTS.get(
        (entry_id, upstream_version, protocol_version)
    )
    if contract is None:
        return None
    if (
        contract.model == LIFECYCLE_ADDON_RESPONSE_MODEL_TEXT_V1
        and contract.envelope_variant
        == LIFECYCLE_ADDON_RESPONSE_ENVELOPE_TEXT
    ):
        return contract
    if (
        contract.model
        == LIFECYCLE_ADDON_RESPONSE_MODEL_STRUCTURED_V1
        and contract.envelope_variant
        == LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED
    ):
        return contract
    return None


def _provider_contract_identity(
    evidence: OperationalProviderEvidence,
) -> tuple[Any, ...]:
    """Return exact release contract identity without raw catalog diagnostics."""

    return (
        evidence.provider,
        evidence.server_name,
        evidence.server_version,
        evidence.protocol_version,
        evidence.compatibility_entry_id,
        evidence.source_commit,
        evidence.image_index_digest,
        evidence.normalized_catalog_fingerprint,
        evidence.aggregate_fingerprint_model,
        evidence.runtime_contract_fingerprint_model,
        evidence.lifecycle_addon_response_contract_model,
        evidence.lifecycle_addon_response_envelope_variant,
        tuple(sorted(evidence.tool_contract_fingerprints.items())),
        json.dumps(
            evidence.argument_constraints,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _project_addon_identity(
    value: Any,
    *,
    required_slug: str,
    require_installed: bool = False,
) -> dict[str, Any] | None:
    """Project only the reviewed lifecycle identity surface."""

    if not isinstance(value, dict):
        return None
    slug = value.get("slug")
    name = _safe_text(value.get("name"))
    version = _safe_text(value.get("version"))
    state = _safe_text(value.get("state"))
    repository_value = value.get("repository")
    repository_prefix = (
        _repository_from_slug(slug) if isinstance(slug, str) else None
    )
    repository: str | None = None
    if repository_value is not None:
        if (
            not isinstance(repository_value, str)
            or not _SAFE_REPOSITORY.fullmatch(repository_value)
            or repository_value != repository_prefix
        ):
            return None
        repository = repository_value
    if (
        not isinstance(slug, str)
        or not _SAFE_SLUG.fullmatch(slug)
        or slug != required_slug
        or repository_prefix is None
        or state not in _REVIEWED_ADDON_STATES
        or None in {name, version, state}
    ):
        return None
    if require_installed and value.get("installed") is not True:
        return None
    if "installed" in value and value.get("installed") is not True:
        return None
    update_available = value.get("update_available")
    if (
        "update_available" in value
        and update_available is not None
        and not isinstance(update_available, bool)
    ):
        return None
    return {
        "slug": slug,
        "name": name,
        "version": version,
        "state": state,
        "repository": repository,
        "update_available": (
            update_available if isinstance(update_available, bool) else None
        ),
    }


def _repository_from_slug(slug: str) -> str | None:
    """Return the exact Supervisor repository prefix for one installed slug."""

    repository, separator, addon_slug = slug.partition("_")
    if (
        separator != "_"
        or not addon_slug
        or not _SAFE_REPOSITORY.fullmatch(repository)
    ):
        return None
    return repository


def _installed_inventory_diagnostic_paths(
    addons: list[Any],
    summary: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Validate the exact installed-list projection emitted by ha-mcp."""

    missing_paths: list[str] = []
    invalid_paths: list[str] = []
    if set(summary).difference(_INSTALLED_SUMMARY_FIELDS):
        invalid_paths.append("/summary")

    counts: dict[str, int] = {}
    for name in _INSTALLED_SUMMARY_FIELDS:
        if name not in summary:
            missing_paths.append(f"/summary/{name}")
            continue
        observed = summary.get(name)
        if (
            not isinstance(observed, int)
            or isinstance(observed, bool)
            or observed < 0
        ):
            invalid_paths.append(f"/summary/{name}")
        else:
            counts[name] = observed

    running = 0
    updates_available = 0
    for item in addons:
        if not isinstance(item, dict):
            invalid_paths.append("/addons")
            continue
        slug = item.get("slug")
        if (
            not isinstance(slug, str)
            or _project_addon_identity(
                item,
                required_slug=slug,
                require_installed=True,
            )
            is None
        ):
            invalid_paths.append("/addons")
            continue
        if item["state"] == "started":
            running += 1
        if item.get("update_available") is True:
            updates_available += 1

    expected = {
        "total_installed": len(addons),
        "running": running,
        "stopped": len(addons) - running,
        "updates_available": updates_available,
    }
    for name, value in expected.items():
        if name in counts and counts[name] != value:
            invalid_paths.append(f"/summary/{name}")
    return (
        tuple(dict.fromkeys(missing_paths)),
        tuple(dict.fromkeys(invalid_paths)),
    )


def _incomplete_response_paths(
    value: dict[str, Any],
    *,
    prefix: str = "",
) -> tuple[str, ...]:
    paths: list[str] = []

    def path(name: str) -> str:
        return f"{prefix}/{name}"

    for name in ("warning", "warnings"):
        if name not in value:
            continue
        observed = value.get(name)
        if not (
            observed is None
            or observed is False
            or (isinstance(observed, str) and observed == "")
            or (isinstance(observed, list) and not observed)
            or (isinstance(observed, dict) and not observed)
        ):
            paths.append(path(name))
    for name in ("truncated", "partial", "has_more"):
        if name in value and value.get(name) is not False:
            paths.append(path(name))
    for name in ("next", "next_cursor"):
        if name not in value:
            continue
        observed = value.get(name)
        if observed is not None and not (
            isinstance(observed, str) and observed == ""
        ):
            paths.append(path(name))
    pagination = value.get("pagination")
    if "pagination" not in value:
        return tuple(paths)
    if not isinstance(pagination, dict):
        paths.append(path("pagination"))
        return tuple(paths)
    if set(pagination).difference({"has_more", "next", "next_cursor"}):
        paths.append(path("pagination"))
    if (
        "has_more" in pagination
        and pagination.get("has_more") is not False
    ):
        paths.append(path("pagination/has_more"))
    for name in ("next", "next_cursor"):
        if name not in pagination:
            continue
        observed = pagination.get(name)
        if observed is not None and not (
            isinstance(observed, str) and observed == ""
        ):
            paths.append(path(f"pagination/{name}"))
    return tuple(dict.fromkeys(paths))


def _response_reports_incomplete(value: dict[str, Any]) -> bool:
    return bool(_incomplete_response_paths(value))


def _reject_duplicate_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _json_values_exact(left: Any, right: Any) -> bool:
    """Compare decoded JSON without Python's bool/int coercion."""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_values_exact(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_values_exact(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _safe_text(value: Any) -> str | None:
    if not isinstance(value, str) or not _SAFE_TEXT.fullmatch(value):
        return None
    parsed = urlsplit(value)
    if parsed.netloc and (
        parsed.username is not None or parsed.password is not None
    ):
        return None
    sanitized = sanitize_untrusted_data(value, max_string=160)
    if (
        sanitized.failed_closed
        or sanitized.redaction_applied
        or sanitized.truncated_field_count
        or sanitized.value != value
        or _SENSITIVE_TOKEN_FRAGMENT.search(value)
        or _CREDENTIAL_URL_FRAGMENT.search(value)
    ):
        return None
    return value


def _bind_upstream_addon_identity(
    addons: list[Any],
    *,
    endpoint_host: str | None,
    evidence: OperationalProviderEvidence,
) -> dict[str, Any]:
    """Bind one installed Supervisor slug to the admitted MCP endpoint.

    Supervisor defines an add-on's internal DNS name as its complete installed
    slug with every underscore replaced by a hyphen. Matching that documented
    full-slug transform is not repository-prefix inference: the candidate slug
    still comes from the exact installed inventory and the MCP identity comes
    from catalog discovery over the configured endpoint.
    """

    unbound_evidence = {
        "endpoint_host": endpoint_host,
        "identity_source": (
            "configured_endpoint_supervisor_dns_and_reviewed_admission"
        ),
        "inventory_arguments": {
            "source": "installed",
            "include_stats": False,
        },
    }
    if endpoint_host is None:
        return {"status": "unavailable", **unbound_evidence}
    candidates = []
    for item in addons:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if (
            isinstance(slug, str)
            and _SAFE_SLUG.fullmatch(slug)
            and slug.replace("_", "-") == endpoint_host
        ):
            candidates.append(item)
    if len(candidates) != 1:
        return {
            "status": (
                "ambiguous" if len(candidates) > 1 else "unavailable"
            ),
            **unbound_evidence,
        }

    candidate = candidates[0]
    slug = candidate.get("slug")
    projected = _project_addon_identity(
        candidate,
        required_slug=slug if isinstance(slug, str) else "",
        require_installed=True,
    )
    if (
        projected is None
        or projected["version"] != evidence.server_version
    ):
        return {"status": "conflicting", **unbound_evidence}
    return {
        "status": "bound",
        "slug": projected["slug"],
        "name": projected["name"],
        "installed_version": projected["version"],
        "repository": projected["repository"],
        "endpoint_host": endpoint_host,
        "identity_source": (
            "configured_endpoint_supervisor_dns_and_reviewed_admission"
        ),
        "inventory_arguments": {
            "source": "installed",
            "include_stats": False,
        },
        "admission_evidence": evidence.as_dict(),
    }


def _transport_category(category: str) -> str:
    return {
        "authentication_failed": "permission_failure",
        "connection_failed": "provider_unavailable",
        "endpoint_rejected": "provider_unavailable",
        "timeout": "provider_timeout",
        "response_too_large": "invalid_response",
    }.get(
        category,
        (
            category
            if category in {"protocol_error", "invalid_response"}
            else "provider_error"
        ),
    )


def _upstream_error_category(code: Any) -> str:
    if code in {
        "AUTH_INVALID_TOKEN",
        "AUTH_EXPIRED",
        "AUTH_INSUFFICIENT_PERMISSIONS",
        "WEBSOCKET_NOT_AUTHENTICATED",
    }:
        return "permission_failure"
    if code in {"CONNECTION_FAILED", "WEBSOCKET_DISCONNECTED"}:
        return "provider_unavailable"
    if code in {"TIMEOUT", "TIMEOUT_OPERATION", "WEBSOCKET_TIMEOUT"}:
        return "indeterminate_dispatch"
    if code in {
        "VALIDATION_FAILED",
        "VALIDATION_INVALID_PARAMETER",
        "VALIDATION_MISSING_PARAMETER",
    }:
        return "operation_rejected"
    if code in {"RESOURCE_NOT_FOUND", "ENTITY_NOT_FOUND"}:
        return "resource_not_found"
    if code == "CONFIG_INVALID":
        return "configuration_invalid"
    if code == "SERVICE_CALL_FAILED":
        return "operation_failed"
    return "provider_error"


def _addon_error_category(code: Any) -> str:
    if code == "RESOURCE_NOT_FOUND":
        return "addon_not_found"
    return _upstream_error_category(code)


UPSTREAM_OPERATIONAL_LIFECYCLE = ReviewedOperationalLifecycleProvider()
