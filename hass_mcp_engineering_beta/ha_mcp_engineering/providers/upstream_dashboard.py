"""Read-only provider for one explicitly configured upstream dashboard tool."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import statistics
import threading
import time
from typing import Any, Callable

from ..clients.mcp import (
    DashboardTransportError,
    McpDashboardHandshake,
    McpDashboardRead,
    McpDashboardTransport,
    REQUIRED_DASHBOARD_TOOL,
    validate_dashboard_read_arguments,
)
from ..configuration import Settings, parse_upstream_dashboard_endpoint
from ..errors import DashboardProviderError, ErrorCode, GovernanceError
from ..observability import METRICS
from ..request_context import current_telemetry
from ..sanitization import sanitize_untrusted_data


PROVIDER_ID = "upstream_dashboard"
TRUST_MODE_CONTRACT_READ_ONLY = "contract_read_only"
TRUST_MODE_REVIEWED_ARGUMENT_CONSTRAINED = "reviewed_argument_constrained"
REVIEWED_TRUST_PROFILE = "ha_mcp_7_13_dashboard_read_v1"
REVIEWED_SERVER_NAME = "ha-mcp"
REVIEWED_SERVER_VERSION = "7.13.0"
REVIEWED_PROTOCOL_VERSION = "2025-03-26"
REVIEWED_UPSTREAM_COMMIT = "f4eb53621ccb814cb7123d2811e06eda3577129c"
REVIEWED_SCHEMA_FINGERPRINT = (
    "7f2b6a086faec129c182fe6f791722beda9fffc659a507f55a3b20d72e2155a6"
)
REVIEWED_FIXTURE_RUNTIME_DESCRIPTOR_FINGERPRINT = (
    "170c2aac1d6437d5c42b7f1d48f5322fef4736c414654c4cc4f7830138e959ca"
)
REVIEWED_PUBLISHED_RUNTIME_DESCRIPTOR_FINGERPRINT = (
    "dd12cba02e59bf98e5b251ddf516c5a7fbea5fbd5f37d053cd8a9cc549827157"
)
REVIEWED_SECURITY_CONTRACT_FINGERPRINT = (
    "c4395cfa63e9de34a672cfdfe34f93541b407766c81b9dcbe82bf4f82c3e7b86"
)
REVIEWED_ANNOTATIONS = {
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
    "title": "Get Dashboard",
}
REVIEWED_SAFETY_ANNOTATIONS = {
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
ALLOWED_UPSTREAM_TOOLS = frozenset({REQUIRED_DASHBOARD_TOOL})
PROHIBITED_UPSTREAM_TOOLS = frozenset(
    {
        "ha_config_set_dashboard",
        "ha_config_delete_dashboard",
        "ha_manage_backup",
        "call_service",
        "reload_domain",
        "upsert_automation",
    }
)
FAILURE_CATEGORIES = (
    "not_configured",
    "authentication_failed",
    "endpoint_rejected",
    "connection_failed",
    "timeout",
    "protocol_error",
    "invalid_response",
    "required_tool_missing",
    "schema_incompatible",
    "server_identity_mismatch",
    "upstream_version_mismatch",
    "reviewed_contract_mismatch",
    "reviewed_annotation_mismatch",
    "input_schema_mismatch",
    "security_contract_mismatch",
    "runtime_descriptor_semantic_drift",
    "annotation_mismatch",
    "output_contract_mismatch",
    "unsupported_trust_profile",
    "prohibited_argument",
    "hash_contract_mismatch",
    "upstream_error",
    "response_too_large",
    "dashboard_not_found",
    "internal_error",
)
CANONICAL_DASHBOARD_PATH = re.compile(r"^[a-z0-9_-]{1,256}$")
MAX_IDENTITY_CHARS = 128
MAX_WARNING_CHARS = 512
RESPONSE_ENVELOPE_RESERVE = 16_000
REACHABILITY_FRESHNESS_SECONDS = 120.0

_ERROR_CODES = {
    "not_configured": ErrorCode.UPSTREAM_DASHBOARD_NOT_CONFIGURED,
    "authentication_failed": ErrorCode.UPSTREAM_DASHBOARD_AUTHENTICATION_FAILED,
    "endpoint_rejected": ErrorCode.UPSTREAM_DASHBOARD_ENDPOINT_REJECTED,
    "connection_failed": ErrorCode.UPSTREAM_DASHBOARD_CONNECTION_FAILED,
    "timeout": ErrorCode.UPSTREAM_DASHBOARD_TIMEOUT,
    "protocol_error": ErrorCode.UPSTREAM_DASHBOARD_PROTOCOL_ERROR,
    "invalid_response": ErrorCode.UPSTREAM_DASHBOARD_INVALID_RESPONSE,
    "required_tool_missing": ErrorCode.UPSTREAM_DASHBOARD_REQUIRED_TOOL_MISSING,
    "schema_incompatible": ErrorCode.UPSTREAM_DASHBOARD_SCHEMA_INCOMPATIBLE,
    "server_identity_mismatch": (
        ErrorCode.UPSTREAM_DASHBOARD_SERVER_IDENTITY_MISMATCH
    ),
    "upstream_version_mismatch": ErrorCode.UPSTREAM_DASHBOARD_VERSION_MISMATCH,
    "reviewed_contract_mismatch": (
        ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH
    ),
    "reviewed_annotation_mismatch": (
        ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_ANNOTATION_MISMATCH
    ),
    "input_schema_mismatch": (
        ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH
    ),
    "security_contract_mismatch": (
        ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH
    ),
    "runtime_descriptor_semantic_drift": (
        ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH
    ),
    "annotation_mismatch": (
        ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_ANNOTATION_MISMATCH
    ),
    "output_contract_mismatch": (
        ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH
    ),
    "unsupported_trust_profile": (
        ErrorCode.UPSTREAM_DASHBOARD_UNSUPPORTED_TRUST_PROFILE
    ),
    "prohibited_argument": ErrorCode.UPSTREAM_DASHBOARD_PROHIBITED_ARGUMENT,
    "hash_contract_mismatch": (
        ErrorCode.UPSTREAM_DASHBOARD_HASH_CONTRACT_MISMATCH
    ),
    "upstream_error": ErrorCode.UPSTREAM_DASHBOARD_UPSTREAM_ERROR,
    "response_too_large": ErrorCode.UPSTREAM_DASHBOARD_RESPONSE_TOO_LARGE,
    "dashboard_not_found": ErrorCode.DASHBOARD_NOT_FOUND,
    "internal_error": ErrorCode.UPSTREAM_DASHBOARD_INTERNAL_ERROR,
}


@dataclass(frozen=True)
class DashboardProviderResult:
    data: dict[str, Any]
    warnings: list[str]
    metadata: dict[str, Any]
    completeness: str


@dataclass(frozen=True)
class DashboardTrustDecision:
    mode: str
    profile: str | None
    reviewed_contract_match: bool
    force_reload_supported: bool


@dataclass
class DashboardProviderState:
    configured: bool = False
    credential_present: bool = False
    reachable: bool = False
    capability_status: str = "unconfigured"
    upstream_server_name: str | None = None
    upstream_server_version: str | None = None
    mcp_protocol_version: str | None = None
    upstream_tool_count: int = 0
    required_tool_present: bool = False
    required_schema_compatible: bool = False
    required_schema_fingerprint: str | None = None
    required_contract_fingerprint: str | None = None
    reviewed_security_contract_fingerprint: str | None = None
    runtime_descriptor_fingerprint: str | None = None
    catalog_fingerprint: str | None = None
    trust_mode: str | None = None
    trust_profile: str | None = None
    reviewed_contract_match: bool = False
    input_schema_match: bool = False
    reviewed_security_contract_match: bool = False
    runtime_descriptor_match: bool = False
    published_runtime_descriptor_match: bool = False
    runtime_descriptor_drift: str = "not_observed"
    validation_reason: str | None = None
    force_reload_supported: bool = False
    last_successful_handshake_timestamp: str | None = None
    last_successful_dashboard_call_timestamp: str | None = None
    reachability_checked_at: str | None = None
    reachability_source: str | None = None
    last_failed_call_at: str | None = None
    last_failure_category: str | None = None
    connection_latencies: deque[float] = field(
        default_factory=lambda: deque(maxlen=100)
    )
    tool_call_latencies: deque[float] = field(
        default_factory=lambda: deque(maxlen=100)
    )
    request_count: int = 0
    success_count: int = 0
    failure_counts: Counter = field(default_factory=Counter)
    timeout_count: int = 0
    reconnect_count: int = 0
    session_state: str = "unconfigured"
    connection_lost: bool = False


def ensure_dashboard_tool_allowed(tool_name: str) -> None:
    """Reject every upstream tool except the single RC3A read operation."""

    if tool_name not in ALLOWED_UPSTREAM_TOOLS:
        raise GovernanceError(
            ErrorCode.PROVIDER_PROHIBITED,
            details={
                "reason": "upstream_dashboard_allowlist",
                "provider": PROVIDER_ID,
                "upstream_dispatch_occurred": False,
            },
        )


class UpstreamDashboardProvider:
    """Typed dashboard adapter over fixed non-screenshot MCP invocations."""

    def __init__(self) -> None:
        self._transport: McpDashboardTransport | Any | None = None
        self._known_secrets: tuple[str, ...] = ()
        self._state = DashboardProviderState()
        self._lock = threading.Lock()

    def configure(
        self,
        settings: Settings,
        *,
        transport: McpDashboardTransport | Any | None = None,
    ) -> None:
        endpoint = parse_upstream_dashboard_endpoint(
            settings.upstream_dashboard_mcp_url
        )
        self._known_secrets = tuple(
            dict.fromkeys(
                secret
                for secret in (
                    settings.access_secret,
                    settings.ha_token,
                    *(endpoint.secret_values if endpoint else ()),
                )
                if secret
            )
        )
        self._transport = (
            transport
            if endpoint and transport is not None
            else McpDashboardTransport(
                endpoint.url,
                timeout_seconds=settings.ha_timeout_seconds,
                client_version=_server_version(),
            )
            if endpoint
            else None
        )
        self._state = DashboardProviderState(
            configured=bool(endpoint),
            credential_present=bool(endpoint and endpoint.credential_present),
            capability_status="unknown" if endpoint else "unconfigured",
            session_state="idle" if endpoint else "unconfigured",
        )

    @property
    def configured(self) -> bool:
        return self._state.configured

    async def refresh_capabilities(self) -> dict[str, Any]:
        """Perform a read-only initialize/tools-list probe."""

        if not self._transport:
            self._record_failure("not_configured", dispatched=False)
            self._raise("not_configured", dispatched=False)
        started = self._begin_request()
        try:
            handshake = await self._dispatch_discovery()
            self._validate_handshake(handshake)
            self._record_success(
                tool_call_latency_ms=None,
                dashboard_call=False,
            )
            METRICS.record_provider_result(
                PROVIDER_ID, "complete", dispatched=True
            )
            return self.health_snapshot()
        except DashboardProviderError:
            raise
        except DashboardTransportError as exc:
            category = _normalized_category(exc.category)
            self._record_failure(category, dispatched=True)
            METRICS.record_provider_result(PROVIDER_ID, "failed", dispatched=True)
            self._raise(category, dispatched=True)
        finally:
            self._finish_telemetry(started)

    async def list_dashboards(
        self, *, limit: int, response_limit: int
    ) -> DashboardProviderResult:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer from 1 through 200")

        def normalize(
            payload: dict[str, Any], exchange: McpDashboardRead
        ) -> DashboardProviderResult:
            dashboards = payload.get("dashboards")
            if not isinstance(dashboards, list):
                self._raise("invalid_response", dispatched=True)
            warnings = self._upstream_warnings(payload)
            normalized: list[dict[str, Any]] = []
            malformed = 0
            for item in dashboards:
                safe = self._dashboard_metadata(item)
                if safe is None:
                    malformed += 1
                    continue
                normalized.append(safe)
            normalized.sort(key=lambda item: item["url_path"])
            source_count = len(normalized)
            returned = normalized[:limit]
            truncated = source_count > len(returned)
            while returned and (
                len(
                    json.dumps(
                        {
                            "count": len(returned),
                            "dashboards": returned,
                            "truncated": truncated,
                        },
                        default=str,
                        indent=2,
                    )
                )
                + RESPONSE_ENVELOPE_RESERVE
                > response_limit
            ):
                returned.pop()
                truncated = True
            if malformed:
                warnings.append(
                    f"{malformed} malformed dashboard metadata item(s) were omitted."
                )
            if truncated:
                warnings.append(
                    "The dashboard inventory was bounded by the requested limit."
                )
            completeness = "partial" if truncated or malformed else "complete"
            return DashboardProviderResult(
                data={
                    "count": len(returned),
                    "dashboards": returned,
                    "truncated": truncated,
                },
                warnings=warnings[:20],
                metadata=self._metadata(exchange.handshake, completeness),
                completeness=completeness,
            )

        return await self._execute(
            operation="list_dashboards",
            arguments={
                "list_only": True,
                "include_screenshot": False,
            },
            normalizer=normalize,
        )

    async def get_dashboard_config(
        self,
        *,
        url_path: str,
        force_reload: bool,
        response_limit: int,
    ) -> DashboardProviderResult:
        if (
            not isinstance(url_path, str)
            or url_path != url_path.strip()
            or not CANONICAL_DASHBOARD_PATH.fullmatch(url_path)
        ):
            raise ValueError(
                "url_path must be an exact non-empty canonical dashboard URL path"
            )
        if not isinstance(force_reload, bool):
            raise ValueError("force_reload must be a boolean")

        def normalize(
            payload: dict[str, Any], exchange: McpDashboardRead
        ) -> DashboardProviderResult:
            config = payload.get("config")
            if not isinstance(config, dict):
                self._raise("invalid_response", dispatched=True)
            try:
                upstream_serialized = _canonical_json(
                    config,
                    ensure_ascii=True,
                )
                expected_config_hash = _upstream_config_hash(config)
                engineering_config_hash = _engineering_config_hash(config)
            except (TypeError, ValueError, OverflowError):
                self._raise(
                    "invalid_response",
                    dispatched=True,
                    details={"hash_validation": "configuration_not_json"},
                )
            supplied_config_hash = payload.get("config_hash")
            if (
                not isinstance(supplied_config_hash, str)
                or not re.fullmatch(r"[0-9a-f]{16}", supplied_config_hash)
            ):
                self._raise(
                    "hash_contract_mismatch",
                    dispatched=True,
                    details={"hash_validation": "missing_or_malformed"},
                )
            if not hmac.compare_digest(
                supplied_config_hash,
                expected_config_hash,
            ):
                self._raise(
                    "hash_contract_mismatch",
                    dispatched=True,
                    details={"hash_validation": "mismatch"},
                )
            serialized_size = len(upstream_serialized.encode("utf-8"))
            canonical_path = payload.get("url_path")
            if not isinstance(canonical_path, str) or not CANONICAL_DASHBOARD_PATH.fullmatch(
                canonical_path
            ):
                canonical_path = None

            sanitation = sanitize_untrusted_data(
                config,
                known_secrets=self._known_secrets,
                max_string=max(2_000, min(response_limit // 2, 20_000)),
            )
            safe_config = sanitation.value
            safe_size = len(json.dumps(safe_config, default=str, indent=2))
            estimated_envelope_size = safe_size + RESPONSE_ENVELOPE_RESERVE
            if estimated_envelope_size > response_limit:
                self._raise(
                    "response_too_large",
                    dispatched=True,
                    details={
                        "config_hash": supplied_config_hash,
                        "engineering_config_hash": engineering_config_hash,
                        "estimated_serialized_size": serialized_size,
                        "response_limit": response_limit,
                        "configuration_returned": False,
                    },
                )

            warnings = self._upstream_warnings(payload)
            completeness = "complete"
            if sanitation.failed_closed or sanitation.truncated_field_count:
                completeness = "partial"
                warnings.append(
                    "Dashboard content was sanitized or bounded and is not complete."
                )
            data = {
                "url_path": canonical_path or url_path,
                "configuration": safe_config,
                "config_hash": supplied_config_hash,
                "engineering_config_hash": engineering_config_hash,
                "estimated_serialized_size": serialized_size,
                "configuration_returned": True,
            }
            if (
                canonical_path
                and canonical_path != url_path
            ):
                data["requested_url_path"] = url_path
            return DashboardProviderResult(
                data=data,
                warnings=warnings[:20],
                metadata=self._metadata(exchange.handshake, completeness),
                completeness=completeness,
            )

        arguments: dict[str, Any] = {
            "url_path": url_path,
            "list_only": False,
            "force_reload": force_reload,
            "include_screenshot": False,
        }

        return await self._execute(
            operation="get_dashboard_config",
            arguments=arguments,
            normalizer=normalize,
        )

    async def _execute(
        self,
        *,
        operation: str,
        arguments: dict[str, Any],
        normalizer: Callable[
            [dict[str, Any], McpDashboardRead], DashboardProviderResult
        ],
    ) -> DashboardProviderResult:
        if not self._transport:
            self._record_failure("not_configured", dispatched=False)
            self._raise("not_configured", dispatched=False)
        ensure_dashboard_tool_allowed(REQUIRED_DASHBOARD_TOOL)
        try:
            validate_dashboard_read_arguments(arguments)
        except DashboardTransportError:
            self._raise("prohibited_argument", dispatched=False)
        started = self._begin_request()
        failure_recorded = False
        try:
            def validate(handshake: McpDashboardHandshake) -> None:
                self._validate_handshake(handshake)

            exchange = await self._transport.execute_dashboard_read(
                arguments, validate
            )
            payload = self._decode_call_result(
                exchange.call_result,
                expected_url_path=(
                    str(arguments.get("url_path"))
                    if isinstance(arguments.get("url_path"), str)
                    else None
                ),
            )
            result = normalizer(payload, exchange)
            self._record_success(
                tool_call_latency_ms=exchange.tool_call_latency_ms,
                dashboard_call=True,
            )
            METRICS.record_provider_result(
                PROVIDER_ID, result.completeness, dispatched=True
            )
            return result
        except DashboardProviderError as exc:
            category = _category_for_code(exc.code)
            if category == "dashboard_not_found":
                self._record_domain_outcome(category)
                METRICS.record_classified_outcome(category)
                METRICS.record_provider_result(PROVIDER_ID, "complete", dispatched=True)
            else:
                self._record_failure(category, dispatched=True)
                failure_recorded = True
                METRICS.record_provider_result(PROVIDER_ID, "failed", dispatched=True)
            raise
        except DashboardTransportError as exc:
            category = _normalized_category(exc.category)
            self._record_failure(category, dispatched=True)
            failure_recorded = True
            METRICS.record_provider_result(PROVIDER_ID, "failed", dispatched=True)
            self._raise(category, dispatched=True)
        except Exception:
            self._record_failure("internal_error", dispatched=True)
            failure_recorded = True
            METRICS.record_provider_result(PROVIDER_ID, "failed", dispatched=True)
            self._raise("internal_error", dispatched=True)
        finally:
            if not failure_recorded and self._state.session_state == "connecting":
                with self._lock:
                    self._state.session_state = "idle"
            self._finish_telemetry(started)

    async def _dispatch_discovery(self) -> McpDashboardHandshake:
        if not self._transport:
            self._raise("not_configured", dispatched=False)
        return await self._transport.discover()

    def _validate_handshake(self, handshake: McpDashboardHandshake) -> None:
        tools = list(handshake.tools)
        tool = next(
            (item for item in tools if item.get("name") == REQUIRED_DASHBOARD_TOOL),
            None,
        )
        catalog_fingerprint = _stable_hash(
            sorted(
                (
                    {
                        "name": item.get("name"),
                        "inputSchema": item.get("inputSchema"),
                        "annotations": item.get("annotations"),
                    }
                    for item in tools
                ),
                key=lambda item: str(item["name"]),
            )
        )
        server_name = self._safe_string(
            handshake.server_name, max_chars=MAX_IDENTITY_CHARS
        )
        server_version = self._safe_string(
            handshake.server_version, max_chars=MAX_IDENTITY_CHARS
        )
        with self._lock:
            state = self._state
            if state.connection_lost:
                state.reconnect_count += 1
                state.connection_lost = False
            state.reachable = True
            state.upstream_server_name = server_name
            state.upstream_server_version = server_version
            state.mcp_protocol_version = self._safe_string(
                handshake.protocol_version, max_chars=64
            )
            state.upstream_tool_count = len(tools)
            state.catalog_fingerprint = catalog_fingerprint
            state.required_tool_present = tool is not None
            state.last_successful_handshake_timestamp = _utc_now()
            state.reachability_checked_at = _utc_now()
            state.reachability_source = "active_probe"
            state.connection_latencies.append(handshake.connection_latency_ms)

        if tool is None:
            with self._lock:
                self._state.required_schema_compatible = False
                self._state.capability_status = "unavailable"
                self._state.validation_reason = "required_tool_missing"
            raise DashboardTransportError("required_tool_missing")

        schema = tool.get("inputSchema")
        try:
            schema_fingerprint = _stable_hash(schema)
            runtime_descriptor_fingerprint = _stable_hash(tool)
            security_contract_fingerprint = _stable_hash(
                _reviewed_security_contract_projection(tool)
            )
        except (TypeError, ValueError, OverflowError):
            with self._lock:
                self._state.required_schema_compatible = False
                self._state.capability_status = "unavailable"
                self._state.validation_reason = "reviewed_contract_mismatch"
            raise DashboardTransportError("reviewed_contract_mismatch") from None
        input_schema_match = (
            schema_fingerprint == REVIEWED_SCHEMA_FINGERPRINT
        )
        security_contract_match = (
            security_contract_fingerprint
            == REVIEWED_SECURITY_CONTRACT_FINGERPRINT
        )
        runtime_descriptor_match = (
            runtime_descriptor_fingerprint
            == REVIEWED_FIXTURE_RUNTIME_DESCRIPTOR_FINGERPRINT
        )
        published_runtime_descriptor_match = (
            runtime_descriptor_fingerprint
            == REVIEWED_PUBLISHED_RUNTIME_DESCRIPTOR_FINGERPRINT
        )
        runtime_descriptor_drift = _runtime_descriptor_drift(
            runtime_descriptor_match=runtime_descriptor_match,
            security_contract_match=security_contract_match,
        )
        try:
            decision = _select_trust_profile(handshake, tool)
        except DashboardTransportError as exc:
            with self._lock:
                self._state.required_schema_fingerprint = schema_fingerprint
                self._state.required_contract_fingerprint = (
                    runtime_descriptor_fingerprint
                )
                self._state.reviewed_security_contract_fingerprint = (
                    security_contract_fingerprint
                )
                self._state.runtime_descriptor_fingerprint = (
                    runtime_descriptor_fingerprint
                )
                self._state.required_schema_compatible = False
                self._state.reviewed_contract_match = False
                self._state.input_schema_match = input_schema_match
                self._state.reviewed_security_contract_match = (
                    security_contract_match
                )
                self._state.runtime_descriptor_match = runtime_descriptor_match
                self._state.published_runtime_descriptor_match = (
                    published_runtime_descriptor_match
                )
                self._state.runtime_descriptor_drift = runtime_descriptor_drift
                self._state.validation_reason = exc.category
                self._state.capability_status = "unavailable"
            raise
        with self._lock:
            self._state.required_schema_fingerprint = schema_fingerprint
            self._state.required_contract_fingerprint = (
                runtime_descriptor_fingerprint
            )
            self._state.reviewed_security_contract_fingerprint = (
                security_contract_fingerprint
            )
            self._state.runtime_descriptor_fingerprint = (
                runtime_descriptor_fingerprint
            )
            self._state.required_schema_compatible = True
            self._state.force_reload_supported = decision.force_reload_supported
            self._state.trust_mode = decision.mode
            self._state.trust_profile = decision.profile
            self._state.reviewed_contract_match = (
                decision.reviewed_contract_match
            )
            self._state.input_schema_match = (
                input_schema_match
                if decision.mode == TRUST_MODE_REVIEWED_ARGUMENT_CONSTRAINED
                else False
            )
            self._state.reviewed_security_contract_match = (
                security_contract_match
                if decision.mode == TRUST_MODE_REVIEWED_ARGUMENT_CONSTRAINED
                else False
            )
            self._state.runtime_descriptor_match = (
                runtime_descriptor_match
                if decision.mode == TRUST_MODE_REVIEWED_ARGUMENT_CONSTRAINED
                else False
            )
            self._state.published_runtime_descriptor_match = (
                published_runtime_descriptor_match
                if decision.mode == TRUST_MODE_REVIEWED_ARGUMENT_CONSTRAINED
                else False
            )
            self._state.runtime_descriptor_drift = (
                runtime_descriptor_drift
                if decision.mode == TRUST_MODE_REVIEWED_ARGUMENT_CONSTRAINED
                else "not_applicable"
            )
            self._state.validation_reason = None
            self._state.capability_status = "available"

    def _decode_call_result(
        self,
        result: dict[str, Any],
        *,
        expected_url_path: str | None = None,
    ) -> dict[str, Any]:
        content = result.get("content")
        if not isinstance(content, list):
            self._raise("invalid_response", dispatched=True)
        text_parts = [
            item.get("text")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if not text_parts:
            self._raise("invalid_response", dispatched=True)
        text = "\n".join(text_parts)
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            self._raise("invalid_response", dispatched=True)
        if not isinstance(payload, dict):
            self._raise("invalid_response", dispatched=True)
        if result.get("isError") or payload.get("success") is False:
            if _upstream_error_code(payload) in {
                "RESOURCE_NOT_FOUND",
                "DASHBOARD_NOT_FOUND",
            } or _is_reviewed_dashboard_not_found(
                payload,
                expected_url_path=expected_url_path,
            ):
                self._raise("dashboard_not_found", dispatched=True)
            self._raise("upstream_error", dispatched=True)
        return payload

    def _dashboard_metadata(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        path = value.get("url_path")
        if not isinstance(path, str) or not CANONICAL_DASHBOARD_PATH.fullmatch(path):
            return None
        safe_path = self._safe_string(path, max_chars=256)
        if safe_path != path:
            return None
        output: dict[str, Any] = {"url_path": safe_path}
        for key in (
            "id",
            "title",
            "icon",
            "show_in_sidebar",
            "require_admin",
            "mode",
            "storage_mode",
        ):
            if key not in value:
                continue
            safe = sanitize_untrusted_data(
                value[key],
                known_secrets=self._known_secrets,
                max_string=256,
            ).value
            if safe is None or isinstance(safe, (str, bool, int, float)):
                output[key] = safe
        return output

    def _upstream_warnings(self, payload: dict[str, Any]) -> list[str]:
        values: list[Any] = []
        warnings = payload.get("warnings")
        if isinstance(warnings, list):
            values.extend(warnings[:20])
        elif isinstance(warnings, str):
            values.append(warnings)
        if isinstance(payload.get("hint"), str):
            values.append(payload["hint"])
        safe: list[str] = []
        for value in values:
            sanitized = self._safe_string(value, max_chars=MAX_WARNING_CHARS)
            if sanitized:
                safe.append(sanitized)
        return safe

    def _safe_string(self, value: Any, *, max_chars: int) -> str:
        result = sanitize_untrusted_data(
            str(value),
            known_secrets=self._known_secrets,
            max_string=max_chars,
        )
        return str(result.value)

    def _metadata(
        self, handshake: McpDashboardHandshake, completeness: str
    ) -> dict[str, Any]:
        return {
            "provider": PROVIDER_ID,
            "routing": PROVIDER_ID,
            "classification": PROVIDER_ID,
            "source_timestamp": _utc_now(),
            "upstream_server": {
                "name": self._safe_string(
                    handshake.server_name, max_chars=MAX_IDENTITY_CHARS
                ),
                "version": self._safe_string(
                    handshake.server_version, max_chars=MAX_IDENTITY_CHARS
                ),
            },
            "mcp_protocol_version": self._safe_string(
                handshake.protocol_version, max_chars=64
            ),
            "schema_fingerprint": self._state.required_schema_fingerprint,
            "contract_fingerprint": self._state.required_contract_fingerprint,
            "reviewed_security_contract_fingerprint": (
                self._state.reviewed_security_contract_fingerprint
            ),
            "runtime_descriptor_fingerprint": (
                self._state.runtime_descriptor_fingerprint
            ),
            "catalog_fingerprint": self._state.catalog_fingerprint,
            "trust_mode": self._state.trust_mode,
            "trust_profile": self._state.trust_profile,
            "argument_constraints_active": True,
            "screenshots_allowed": False,
            "preference_writes_allowed": False,
            "completeness": completeness,
            "upstream_dispatch_occurred": True,
            "content_is_untrusted_data": True,
        }

    def _begin_request(self) -> float:
        started = time.perf_counter()
        with self._lock:
            self._state.request_count += 1
            self._state.session_state = "connecting"
        telemetry = current_telemetry()
        if telemetry:
            telemetry.begin_upstream_attempt(started)
        return started

    def _finish_telemetry(self, started: float) -> None:
        finished = time.perf_counter()
        telemetry = current_telemetry()
        if telemetry:
            telemetry.finish_upstream_attempt(
                finished, (finished - started) * 1000
            )

    def _record_success(
        self,
        *,
        tool_call_latency_ms: float | None,
        dashboard_call: bool,
    ) -> None:
        with self._lock:
            self._state.reachable = True
            self._state.capability_status = "available"
            self._state.success_count += 1
            if tool_call_latency_ms is not None:
                self._state.tool_call_latencies.append(tool_call_latency_ms)
            if dashboard_call:
                self._state.last_successful_dashboard_call_timestamp = _utc_now()
            self._state.reachability_checked_at = _utc_now()
            self._state.reachability_source = "tool_call" if dashboard_call else "active_probe"
            self._state.last_failure_category = None
            self._state.session_state = "idle"

    def _record_domain_outcome(self, category: str) -> None:
        """Record an upstream domain answer without degrading provider health."""
        with self._lock:
            self._state.failure_counts[category] += 1
            self._state.reachable = True
            self._state.reachability_checked_at = _utc_now()
            self._state.reachability_source = "tool_call"
            self._state.session_state = "idle"

    def _record_failure(self, category: str, *, dispatched: bool) -> None:
        category = _normalized_category(category)
        with self._lock:
            self._state.failure_counts[category] += 1
            self._state.last_failed_call_at = _utc_now()
            self._state.last_failure_category = category
            self._state.reachability_checked_at = _utc_now()
            self._state.reachability_source = "tool_call"
            self._state.session_state = "failed"
            if category == "timeout":
                self._state.timeout_count += 1
            if category in {
                "authentication_failed",
                "endpoint_rejected",
                "connection_failed",
                "timeout",
                "protocol_error",
                "invalid_response",
                "internal_error",
            }:
                self._state.reachable = False
            if category in {
                "required_tool_missing",
                "schema_incompatible",
                "server_identity_mismatch",
                "upstream_version_mismatch",
                "reviewed_contract_mismatch",
                "reviewed_annotation_mismatch",
                "input_schema_mismatch",
                "security_contract_mismatch",
                "runtime_descriptor_semantic_drift",
                "annotation_mismatch",
                "output_contract_mismatch",
                "unsupported_trust_profile",
                "hash_contract_mismatch",
                "not_configured",
            }:
                self._state.validation_reason = category
                self._state.capability_status = (
                    "unconfigured" if category == "not_configured" else "unavailable"
                )
            if dispatched and category in {"connection_failed", "timeout"}:
                self._state.connection_lost = True
        telemetry = current_telemetry()
        if telemetry and category == "timeout":
            telemetry.timeout_occurred = True

    def _raise(
        self,
        category: str,
        *,
        dispatched: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        category = _normalized_category(category)
        safe_details = {
            "provider": PROVIDER_ID,
            "failure_category": category,
            "completeness": "unavailable",
            "upstream_dispatch_occurred": dispatched,
        }
        if details:
            safe_details.update(details)
        raise DashboardProviderError(_ERROR_CODES[category], details=safe_details)

    def health_snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
            reachability_age = _timestamp_age_seconds(state.reachability_checked_at)
            fresh = bool(
                reachability_age is not None
                and reachability_age <= REACHABILITY_FRESHNESS_SECONDS
            )
            operational_status = (
                "unavailable"
                if not state.configured or (fresh and state.reachable is False)
                else "available"
                if fresh and state.reachable
                else "unknown"
            )
            contract_status = (
                "valid"
                if state.required_schema_compatible
                else "invalid"
                if state.validation_reason and state.validation_reason != "not_configured"
                else "unknown"
            )
            return {
                "configured": state.configured,
                "credential_present": state.credential_present,
                "reachable": state.reachable if fresh else None,
                "operational_status": operational_status,
                "contract_status": contract_status,
                "reachability_source": state.reachability_source or "cached",
                "reachability_checked_at": state.reachability_checked_at,
                "reachability_age_seconds": reachability_age,
                "reachability_freshness_seconds": REACHABILITY_FRESHNESS_SECONDS,
                "last_successful_handshake_at": state.last_successful_handshake_timestamp,
                "last_successful_call_at": state.last_successful_dashboard_call_timestamp,
                "last_failed_call_at": state.last_failed_call_at,
                "last_failure_category": state.last_failure_category,
                "capability_status": state.capability_status,
                "upstream_server_name": state.upstream_server_name,
                "upstream_server_version": state.upstream_server_version,
                "mcp_protocol_version": state.mcp_protocol_version,
                "upstream_tool_count": state.upstream_tool_count,
                "required_tool_present": state.required_tool_present,
                "required_schema_compatible": state.required_schema_compatible,
                "required_schema_fingerprint": state.required_schema_fingerprint,
                "required_contract_fingerprint": (
                    state.required_contract_fingerprint
                ),
                "expected_input_schema_fingerprint": (
                    REVIEWED_SCHEMA_FINGERPRINT
                ),
                "observed_input_schema_fingerprint": (
                    state.required_schema_fingerprint
                ),
                "input_schema_match": state.input_schema_match,
                "expected_reviewed_security_contract_fingerprint": (
                    REVIEWED_SECURITY_CONTRACT_FINGERPRINT
                ),
                "observed_reviewed_security_contract_fingerprint": (
                    state.reviewed_security_contract_fingerprint
                ),
                "reviewed_security_contract_match": (
                    state.reviewed_security_contract_match
                ),
                "expected_fixture_runtime_descriptor_fingerprint": (
                    REVIEWED_FIXTURE_RUNTIME_DESCRIPTOR_FINGERPRINT
                ),
                "expected_published_runtime_descriptor_fingerprint": (
                    REVIEWED_PUBLISHED_RUNTIME_DESCRIPTOR_FINGERPRINT
                ),
                "observed_runtime_descriptor_fingerprint": (
                    state.runtime_descriptor_fingerprint
                ),
                "runtime_descriptor_match": state.runtime_descriptor_match,
                "published_runtime_descriptor_match": (
                    state.published_runtime_descriptor_match
                ),
                "runtime_descriptor_drift": state.runtime_descriptor_drift,
                "catalog_fingerprint": state.catalog_fingerprint,
                "trust_mode": state.trust_mode,
                "trust_profile": state.trust_profile,
                "pinned_server_name": REVIEWED_SERVER_NAME,
                "pinned_server_version": REVIEWED_SERVER_VERSION,
                "reviewed_contract_match": state.reviewed_contract_match,
                "validation_reason": state.validation_reason,
                "argument_constraints_active": True,
                "screenshots_allowed": False,
                "preference_writes_allowed": False,
                "last_successful_handshake_timestamp": (
                    state.last_successful_handshake_timestamp
                ),
                "last_successful_dashboard_call_timestamp": (
                    state.last_successful_dashboard_call_timestamp
                ),
                "connection_latency": _latency_summary(
                    state.connection_latencies
                ),
                "tool_call_latency": _latency_summary(
                    state.tool_call_latencies
                ),
                "request_count": state.request_count,
                "success_count": state.success_count,
                "failure_counts": {
                    category: state.failure_counts.get(category, 0)
                    for category in FAILURE_CATEGORIES
                },
                "timeout_count": state.timeout_count,
                "reconnect_count": state.reconnect_count,
                "session_state": state.session_state,
                "required_tool": REQUIRED_DASHBOARD_TOOL,
                "allowlisted_tool_count": len(ALLOWED_UPSTREAM_TOOLS),
                "writes_allowed": False,
            }


def _select_trust_profile(
    handshake: McpDashboardHandshake,
    tool: dict[str, Any],
) -> DashboardTrustDecision:
    strict_compatible, force_reload_supported = (
        _strict_contract_read_only_schema(tool)
    )
    annotations = tool.get("annotations")
    declares_contract_read_only = bool(
        isinstance(annotations, dict)
        and annotations.get("readOnlyHint") is True
    )
    if strict_compatible:
        return DashboardTrustDecision(
            mode=TRUST_MODE_CONTRACT_READ_ONLY,
            profile=None,
            reviewed_contract_match=False,
            force_reload_supported=force_reload_supported,
        )
    if declares_contract_read_only:
        raise DashboardTransportError("schema_incompatible")
    return _reviewed_argument_constrained_profile(handshake, tool)


def _reviewed_argument_constrained_profile(
    handshake: McpDashboardHandshake,
    tool: dict[str, Any],
) -> DashboardTrustDecision:
    if handshake.server_name != REVIEWED_SERVER_NAME:
        raise DashboardTransportError("server_identity_mismatch")
    if handshake.server_version != REVIEWED_SERVER_VERSION:
        raise DashboardTransportError("upstream_version_mismatch")
    if handshake.protocol_version != REVIEWED_PROTOCOL_VERSION:
        raise DashboardTransportError("unsupported_trust_profile")
    if tool.get("name") != REQUIRED_DASHBOARD_TOOL:
        raise DashboardTransportError("required_tool_missing")
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        raise DashboardTransportError("annotation_mismatch")
    if any(
        key not in annotations or annotations.get(key) is not expected
        for key, expected in REVIEWED_SAFETY_ANNOTATIONS.items()
    ):
        raise DashboardTransportError("annotation_mismatch")
    if "readOnlyHint" in annotations:
        # The reviewed exception is specifically for the mixed-operation
        # descriptor. A future pure-read declaration must re-enter the strict
        # contract validator rather than silently matching this profile.
        raise DashboardTransportError("annotation_mismatch")
    if set(annotations) - {*REVIEWED_SAFETY_ANNOTATIONS, "title"}:
        raise DashboardTransportError("annotation_mismatch")
    try:
        schema_fingerprint = _stable_hash(tool.get("inputSchema"))
        security_contract_fingerprint = _stable_hash(
            _reviewed_security_contract_projection(tool)
        )
    except (TypeError, ValueError, OverflowError):
        raise DashboardTransportError("security_contract_mismatch") from None
    if schema_fingerprint != REVIEWED_SCHEMA_FINGERPRINT:
        raise DashboardTransportError("input_schema_mismatch")
    if "outputSchema" in tool:
        # The reviewed 7.13.0 runtime omits an output schema. Adding one can
        # change the structured-result contract Engineering relies on, so it
        # requires a new review even if it appears additive.
        raise DashboardTransportError("output_contract_mismatch")
    if (
        security_contract_fingerprint
        != REVIEWED_SECURITY_CONTRACT_FINGERPRINT
    ):
        raise DashboardTransportError("security_contract_mismatch")
    return DashboardTrustDecision(
        mode=TRUST_MODE_REVIEWED_ARGUMENT_CONSTRAINED,
        profile=REVIEWED_TRUST_PROFILE,
        reviewed_contract_match=True,
        force_reload_supported=True,
    )


def _strict_contract_read_only_schema(
    tool: dict[str, Any],
) -> tuple[bool, bool]:
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        return False, False
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return False, False
    url_path = properties.get("url_path")
    list_only = properties.get("list_only")
    if not isinstance(url_path, dict) or "string" not in _schema_types(url_path):
        return False, False
    if not isinstance(list_only, dict) or "boolean" not in _schema_types(list_only):
        return False, False
    required = schema.get("required", [])
    if not isinstance(required, list) or any(
        name
        not in {
            "url_path",
            "list_only",
            "force_reload",
            "include_screenshot",
        }
        for name in required
    ):
        return False, False
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        return False, False
    if annotations.get("readOnlyHint") is not True:
        return False, False
    if annotations.get("destructiveHint") is True:
        return False, False
    force_reload = properties.get("force_reload")
    force_reload_supported = bool(
        isinstance(force_reload, dict)
        and "boolean" in _schema_types(force_reload)
    )
    include_screenshot = properties.get("include_screenshot")
    screenshot_false_supported = bool(
        isinstance(include_screenshot, dict)
        and "boolean" in _schema_types(include_screenshot)
    )
    compatible = force_reload_supported and screenshot_false_supported
    return compatible, force_reload_supported


def _compatible_dashboard_schema(tool: dict[str, Any]) -> tuple[bool, bool]:
    """Backward-compatible alias for strict contract-level validation."""

    return _strict_contract_read_only_schema(tool)


def _schema_types(schema: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    declared = schema.get("type")
    if isinstance(declared, str):
        values.add(declared)
    elif isinstance(declared, list):
        values.update(item for item in declared if isinstance(item, str))
    if schema.get("nullable") is True:
        values.add("null")
    for key in ("anyOf", "oneOf"):
        branches = schema.get(key)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    values.update(_schema_types(branch))
    return values


def _canonical_json(value: Any, *, ensure_ascii: bool) -> str:
    """Serialize JSON data canonically without coercing unsupported values."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=ensure_ascii,
        allow_nan=False,
    )


def _upstream_config_hash(value: Any) -> str:
    """Match reviewed homeassistant-ai/ha-mcp 7.13.0 optimistic-lock hashing."""

    encoded = _canonical_json(value, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _engineering_config_hash(value: Any) -> str:
    """Return the full Engineering evidence hash for complete raw JSON data."""

    encoded = _canonical_json(value, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_hash(value: Any) -> str:
    """Canonical full fingerprint used for schemas and bounded catalogs."""

    return _engineering_config_hash(value)


def _reviewed_security_contract_projection(
    tool: dict[str, Any],
) -> dict[str, Any]:
    """Project only fields that can change reviewed dispatch safety.

    The complete input schema remains in the projection, so argument names,
    required status, types, defaults, ``additionalProperties``, and even
    schema-local descriptions remain exact. Output-schema presence is explicit
    because omitted and null are different runtime contracts. Safety
    annotations record both presence and value; unknown annotations and
    unknown top-level metadata fail closed.

    Exclusions are intentionally narrow:

    * top-level ``title`` and ``description`` are display/documentation text;
    * ``annotations.title`` is a display label, not a safety hint;
    * ``_meta.fastmcp.tags`` is tool grouping/search presentation metadata;
    * ``_meta.ha_mcp.llm_api_exposed`` and ``pinned`` control how the upstream
      exposes tools to its own conversation-agent integration. Engineering
      neither selects nor dispatches from those values.

    Every other unexpected field is retained in the projection and therefore
    changes the blocking security fingerprint.
    """

    if not isinstance(tool, dict):
        raise TypeError("tool descriptor must be an object")
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {"__malformed__": annotations}

    safety_keys = (
        "readOnlyHint",
        "destructiveHint",
        "idempotentHint",
        "openWorldHint",
    )
    annotation_projection = {
        "safety": {
            key: {
                "present": key in annotations,
                "value": annotations.get(key),
            }
            for key in safety_keys
        },
        "unreviewed": {
            key: value
            for key, value in annotations.items()
            if key not in {*safety_keys, "title"}
        },
    }

    meta = tool.get("_meta")
    unreviewed_meta: dict[str, Any] = {}
    if meta is not None:
        if not isinstance(meta, dict):
            unreviewed_meta["__malformed__"] = meta
        else:
            for namespace, value in meta.items():
                if not isinstance(value, dict):
                    unreviewed_meta[namespace] = value
                    continue
                excluded = (
                    {"tags"}
                    if namespace == "fastmcp"
                    else {"llm_api_exposed", "pinned"}
                    if namespace == "ha_mcp"
                    else set()
                )
                remaining = {
                    key: item for key, item in value.items() if key not in excluded
                }
                if remaining:
                    unreviewed_meta[namespace] = remaining

    known_top_level = {
        "name",
        "title",
        "description",
        "inputSchema",
        "outputSchema",
        "annotations",
        "_meta",
    }
    return {
        "name": tool.get("name"),
        "inputSchema": tool.get("inputSchema"),
        "outputSchema": {
            "present": "outputSchema" in tool,
            "value": tool.get("outputSchema"),
        },
        "annotations": annotation_projection,
        "unreviewedTopLevel": {
            key: value
            for key, value in tool.items()
            if key not in known_top_level
        },
        "unreviewedMetadata": unreviewed_meta,
    }


def _runtime_descriptor_drift(
    *,
    runtime_descriptor_match: bool,
    security_contract_match: bool,
) -> str:
    if runtime_descriptor_match:
        return "none"
    if security_contract_match:
        return "descriptive_metadata_only"
    return "runtime_descriptor_semantic_drift"


def _latency_summary(values: deque[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "average_ms": None, "maximum_ms": None}
    return {
        "count": len(values),
        "average_ms": round(statistics.fmean(values), 3),
        "maximum_ms": round(max(values), 3),
    }


def _normalized_category(category: str) -> str:
    return category if category in FAILURE_CATEGORIES else "internal_error"


def _category_for_code(code: ErrorCode) -> str:
    for category, candidate in _ERROR_CODES.items():
        if candidate == code:
            return category
    return "internal_error"


def _upstream_error_code(payload: dict[str, Any]) -> str | None:
    """Extract only bounded structured domain codes; never inspect messages."""
    candidates: list[Any] = [payload.get("error_code"), payload.get("code")]
    error = payload.get("error")
    if isinstance(error, dict):
        candidates.extend((error.get("code"), error.get("error_code")))
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        candidates.append(data["error"].get("code"))
    for value in candidates:
        if isinstance(value, str):
            return value.strip().upper()[:64]
    return None


def _is_reviewed_dashboard_not_found(
    payload: dict[str, Any], *, expected_url_path: str | None
) -> bool:
    """Recognize only the reviewed ha-mcp 7.13 exact-dashboard miss envelope."""

    if not expected_url_path or not CANONICAL_DASHBOARD_PATH.fullmatch(expected_url_path):
        return False
    if _upstream_error_code(payload) != "SERVICE_CALL_FAILED":
        return False
    if payload.get("action") != "get" or payload.get("url_path") != expected_url_path:
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    message = error.get("message")
    if not isinstance(message, str):
        return False
    accepted = {
        f"Unknown config specified: {expected_url_path}",
        f"Command failed: Unknown config specified: {expected_url_path}",
    }
    return message in accepted


def _timestamp_age_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return round(max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds()), 3)
    except (TypeError, ValueError):
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _server_version() -> str:
    from ..version import SERVER_VERSION

    return SERVER_VERSION


UPSTREAM_DASHBOARD = UpstreamDashboardProvider()
