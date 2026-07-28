"""Exact reviewed providers for governed reload and restart operations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from ..upstream_tool_policy import (
    catalog_fingerprint,
    load_reviewed_upstream_release_registry,
    runtime_description_fingerprint,
    schema_fingerprint,
)
from ..version import SERVER_VERSION


PROVIDER_ID = "upstream_operational_lifecycle"
MAX_RESULT_BYTES = 60_000
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
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")
_SAFE_ENDPOINT_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$"
)
_TOOL_POLICY = {
    RELOAD_TOOL: "physical_or_high_risk_action",
    ADDON_ACTION_TOOL: "mixed_or_requires_wrapper",
    ADDON_READ_TOOL: "mixed_or_requires_wrapper",
    HA_RESTART_TOOL: "physical_or_high_risk_action",
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
            evidence = self._validate_catalog(
                catalog,
                (ADDON_READ_TOOL,),
                operation="restart_addon",
            )

        try:
            inventory_exchange = await self._execute_observed_read(
                ADDON_READ_TOOL,
                {"source": "installed", "include_stats": False},
                timeout_seconds=60.0,
                catalog_validator=validate,
            )
            inventory = self._decode(
                inventory_exchange.call_result,
                dispatched=False,
            )
            addons = inventory.get("addons")
            if inventory.get("success") is not True or not isinstance(
                addons, list
            ):
                self._fail("invalid_response", dispatched=False)
            matches = [
                item
                for item in addons
                if isinstance(item, dict) and item.get("slug") == slug
            ]
            if not matches:
                assert evidence is not None
                self._fail("addon_not_found", dispatched=False)
            if len(matches) != 1:
                self._fail("invalid_response", dispatched=False)
            assert evidence is not None
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
            payload = self._decode(
                exchange.call_result,
                dispatched=False,
                error_category=_addon_error_category,
            )
            addon = payload.get("addon")
            if payload.get("success") is not True or not isinstance(addon, dict):
                self._fail("invalid_response", dispatched=False)
            observed_slug = _safe_text(addon.get("slug"))
            name = _safe_text(addon.get("name"))
            version = _safe_text(addon.get("version"))
            state = _safe_text(addon.get("state"))
            if observed_slug != slug or None in {name, version, state}:
                self._fail("invalid_response", dispatched=False)
            self._record_success(evidence)
            return {
                "slug": observed_slug,
                "name": name,
                "version": version,
                "state": state,
                "repository": _safe_text(addon.get("repository")),
                "update_available": (
                    addon.get("update_available")
                    if isinstance(addon.get("update_available"), bool)
                    else None
                ),
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
        if catalog.server_name != "ha-mcp":
            self._fail("server_identity_mismatch", dispatched=False)
        release = registry.by_version.get(catalog.server_version)
        if release is None:
            self._fail("upstream_version_mismatch", dispatched=False)
        if catalog.protocol_version not in release.allowed_protocol_versions:
            self._fail("unsupported_protocol_version", dispatched=False)
        try:
            observed_catalog = catalog_fingerprint(
                [dict(item) for item in catalog.tools]
            )
        except (TypeError, ValueError, OverflowError):
            self._fail("invalid_response", dispatched=False)
        if observed_catalog != release.catalog_fingerprint:
            self._fail("catalog_mismatch", dispatched=False)

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
                    "runtime_contract_fingerprint": schema_fingerprint(tool),
                }
            except (TypeError, ValueError, OverflowError):
                self._fail("invalid_response", dispatched=False)
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
                "fallback_count": self._state.fallback_count,
                "fallback_policy": "none",
            }


def _reject_duplicate_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _safe_text(value: Any) -> str | None:
    return (
        value
        if isinstance(value, str) and _SAFE_TEXT.fullmatch(value)
        else None
    )


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

    if endpoint_host is None:
        return {"status": "unavailable"}
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
            )
        }

    candidate = candidates[0]
    slug = candidate.get("slug")
    name = _safe_text(candidate.get("name"))
    installed_version = _safe_text(candidate.get("version"))
    repository = _safe_text(candidate.get("repository"))
    if (
        not isinstance(slug, str)
        or name is None
        or installed_version is None
        or installed_version != evidence.server_version
    ):
        return {"status": "conflicting"}
    return {
        "status": "bound",
        "slug": slug,
        "name": name,
        "installed_version": installed_version,
        "repository": repository,
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
