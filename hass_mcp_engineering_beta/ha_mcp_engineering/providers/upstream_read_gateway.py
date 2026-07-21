"""Generic, policy-bound delegation for reviewed upstream pure-read tools."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import threading
import time
from typing import Any, Callable

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
    schema_fingerprint,
)


PROVIDER_ID = "upstream_read_gateway"
ALIAS_PREFIX = "ha_mcp__"
SUPPORTED_PROTOCOLS = frozenset({"2025-03-26"})
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

    @classmethod
    def build(
        cls,
        *,
        gateway: "UpstreamReadGateway",
        entry: UpstreamToolPolicyEntry,
        exposed_name: str,
        observed_tool: dict[str, Any],
    ) -> "ReviewedUpstreamReadTool":
        async def delegated_read(**arguments):
            del arguments
            raise RuntimeError("delegated_read_placeholder_must_not_execute")

        # The public annotation is binary-owned policy, not upstream metadata.
        # The exact schema is reviewed separately; descriptive or annotation
        # content advertised by the remote peer cannot weaken the read boundary.
        annotations = ToolAnnotations(
            title=entry.upstream_name,
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
        base = Tool.from_function(
            delegated_read,
            name=exposed_name,
            # Publish only the bounded reviewed description from the manifest.
            # Remote catalog prose is not part of the schema fingerprint and
            # must not become model-facing instructions.
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
        return tool

    async def run(self, arguments: dict[str, Any], context: Any = None) -> Any:
        del context
        return await self._gateway.execute(
            exposed_name=self.name,
            arguments=arguments,
            reviewed_schema=self._schema,
            policy_entry=self._entry,
        )


AdmissionValidator = Callable[[McpReadCatalog], None]


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
        self._exposed: dict[str, tuple[UpstreamToolPolicyEntry, dict[str, Any]]] = {}
        self._lock = threading.RLock()
        self._state = self._empty_state()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "configured": False,
            "initialized": False,
            "generic_delegation_available": False,
            "upstream_server_name": None,
            "upstream_server_version": None,
            "protocol_version": None,
            "catalog_fingerprint": None,
            "upstream_advertised_tool_count": 0,
            "reviewed_policy_entry_count": 0,
            "automatic_read_count": 0,
            "dynamically_exposed_count": 0,
            "collision_count": 0,
            "blocked_mixed_tool_count": 0,
            "blocked_write_count": 0,
            "blocked_physical_high_risk_count": 0,
            "prohibited_count": 0,
            "unsupported_count": 0,
            "schema_mismatch_count": 0,
            "missing_reviewed_read_count": 0,
            "unreviewed_tool_count": 0,
            "prohibited_delegation_attempts": 0,
            "fallback_count": 0,
            "last_failure_category": None,
            "failure_counts": Counter(),
            "last_catalog_refresh_at": None,
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
                "reviewed_policy_entry_count": len(self._policy.tools),
                "automatic_read_count": counts["automatic_read"],
                "blocked_mixed_tool_count": counts["mixed_or_requires_wrapper"],
                "blocked_write_count": counts["persistent_write"],
                "blocked_physical_high_risk_count": counts[
                    "physical_or_high_risk_action"
                ],
                "prohibited_count": counts["prohibited"],
                "unsupported_count": counts["unsupported"],
            }
        )

    async def initialize(self, server: Any) -> dict[str, Any]:
        """Discover once and atomically replace only this provider's dynamic tools."""

        self._remove_registered_tools()
        self._registered_server = server
        if not self._transport or not self._policy:
            self._record_failure("not_configured", disable_delegation=True)
            replace_dynamic_upstream_capabilities((), self.health_snapshot())
            return self.health_snapshot()
        try:
            catalog = await self._transport.discover()
            self._validate_identity(catalog.server_name, catalog.server_version, catalog.protocol_version)
            if self._admission_validator is not None:
                self._admission_validator(catalog)
            observed = self._validate_catalog(catalog)
            base_names = {
                tool.name for tool in server._tool_manager.list_tools()
            }
            exposed: dict[str, tuple[UpstreamToolPolicyEntry, dict[str, Any]]] = {}
            capabilities: list[dict[str, Any]] = []
            collisions: list[dict[str, str]] = []
            for entry, tool in observed:
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
                )
                server._tool_manager._tools[exposed_name] = dynamic_tool
                self._registered_names.add(exposed_name)
                exposed[exposed_name] = (entry, tool)
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
                        "collision": exposed_name != entry.upstream_name,
                    }
                )
            with self._lock:
                self._exposed = exposed
                self._state.update(
                    {
                        "initialized": True,
                        "generic_delegation_available": bool(exposed),
                        "upstream_server_name": catalog.server_name[:128],
                        "upstream_server_version": catalog.server_version[:128],
                        "protocol_version": catalog.protocol_version[:64],
                        "catalog_fingerprint": catalog_fingerprint(list(catalog.tools)),
                        "upstream_advertised_tool_count": len(catalog.tools),
                        "dynamically_exposed_count": len(exposed),
                        "collision_count": len(collisions),
                        "last_failure_category": None,
                        "last_catalog_refresh_at": _utc_now(),
                        "exposed_tools": sorted(exposed),
                        "collision_mappings": collisions,
                    }
                )
            replace_dynamic_upstream_capabilities(
                tuple(capabilities), self.health_snapshot()
            )
            return self.health_snapshot()
        except DashboardTransportError as exc:
            self._record_failure(exc.category, disable_delegation=True)
        except Exception:
            self._record_failure("internal_error", disable_delegation=True)
        self._remove_registered_tools()
        replace_dynamic_upstream_capabilities((), self.health_snapshot())
        return self.health_snapshot()

    def _validate_identity(self, server_name: str, server_version: str, protocol: str) -> None:
        if server_name != REVIEWED_UPSTREAM_SERVER:
            raise DashboardTransportError("server_identity_mismatch")
        if not self._policy or server_version != self._policy.reviewed_upstream_version:
            raise DashboardTransportError("upstream_version_mismatch")
        if protocol not in SUPPORTED_PROTOCOLS:
            raise DashboardTransportError("unsupported_protocol_version")

    def _validate_catalog(
        self, catalog: McpReadCatalog
    ) -> list[tuple[UpstreamToolPolicyEntry, dict[str, Any]]]:
        assert self._policy is not None
        policy = self._policy.by_name
        observed_by_name: dict[str, dict[str, Any]] = {}
        duplicate_names: set[str] = set()
        for item in catalog.tools:
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name:
                raise DashboardTransportError("invalid_response")
            if name in observed_by_name:
                duplicate_names.add(name)
            observed_by_name[name] = item
        if duplicate_names:
            raise DashboardTransportError("invalid_response")
        schema_mismatches: list[str] = []
        missing_reviewed_reads: list[str] = []
        matched: list[tuple[UpstreamToolPolicyEntry, dict[str, Any]]] = []
        blocked: list[dict[str, str]] = []
        for entry in self._policy.tools:
            tool = observed_by_name.get(entry.upstream_name)
            if tool is None:
                if entry.classification == "automatic_read":
                    missing_reviewed_reads.append(entry.upstream_name)
                continue
            schema = tool.get("inputSchema")
            try:
                Draft202012Validator.check_schema(schema)
                matches = schema_fingerprint(schema) == entry.input_schema_fingerprint
            except (SchemaError, TypeError, ValueError, OverflowError):
                matches = False
            if not matches:
                schema_mismatches.append(entry.upstream_name)
                continue
            if entry.classification == "automatic_read":
                matched.append((entry, tool))
            else:
                blocked.append(
                    {
                        "upstream_name": entry.upstream_name,
                        "classification": entry.classification,
                    }
                )
        unreviewed = sorted(set(observed_by_name) - set(policy))
        with self._lock:
            self._state["schema_mismatch_count"] = len(schema_mismatches)
            self._state["missing_reviewed_read_count"] = len(
                missing_reviewed_reads
            )
            self._state["unreviewed_tool_count"] = len(unreviewed)
            self._state["blocked_tools"] = blocked
        return matched

    async def execute(
        self,
        *,
        exposed_name: str,
        arguments: dict[str, Any],
        reviewed_schema: dict[str, Any],
        policy_entry: UpstreamToolPolicyEntry,
    ) -> str:
        started = time.perf_counter()
        response_limit = min(
            policy_entry.response_limit_bytes,
            self._settings.response_size_limit if self._settings else 60_000,
        )
        telemetry = current_telemetry()
        try:
            mapping = self._exposed.get(exposed_name)
            if (
                not mapping
                or mapping[0].classification != "automatic_read"
                or mapping[0].upstream_name != policy_entry.upstream_name
            ):
                with self._lock:
                    self._state["prohibited_delegation_attempts"] += 1
                raise _GatewayFailure("prohibited_delegation", dispatched=False)
            if not isinstance(arguments, dict):
                raise _GatewayFailure("argument_validation", dispatched=False)
            errors = sorted(
                Draft202012Validator(reviewed_schema).iter_errors(arguments),
                key=lambda error: tuple(str(item) for item in error.absolute_path),
            )
            if errors:
                raise _GatewayFailure("argument_validation", dispatched=False)
            if not self._transport:
                raise _GatewayFailure("not_configured", dispatched=False)

            attempt_started = time.perf_counter()
            if telemetry:
                telemetry.begin_upstream_attempt(attempt_started)
            try:
                exchange = await self._transport.execute_read(
                    policy_entry.upstream_name,
                    dict(arguments),
                    timeout_seconds=policy_entry.timeout_seconds,
                    identity_validator=self._validate_identity,
                )
            except DashboardTransportError as exc:
                raise _GatewayFailure(exc.category, dispatched=True) from None
            finally:
                finished = time.perf_counter()
                if telemetry:
                    telemetry.finish_upstream_attempt(
                        finished, (finished - attempt_started) * 1_000
                    )
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
            completeness = (
                "partial" if sanitation.truncated_field_count else "complete"
            )
            METRICS.record_provider_result(PROVIDER_ID, completeness, dispatched=True)
            with self._lock:
                # A completed call proves the discovered route remains usable. Historical
                # failure counts stay available, but a prior transient failure must not
                # leave an initialized gateway reported as unavailable forever.
                self._state["generic_delegation_available"] = bool(self._exposed)
                self._state["last_failure_category"] = None
            if telemetry:
                telemetry.result_status = "partial" if completeness == "partial" else "success"
                telemetry.completeness = completeness
            warnings = []
            if sanitation.truncated_field_count:
                warnings.append("The untrusted upstream response was safely bounded.")
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
            category = _normalize_category(exc.category)
            if exc.dispatched:
                METRICS.record_provider_result(PROVIDER_ID, "failed", dispatched=True)
            self._record_failure(category)
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
        if self._registered_server is not None:
            for name in tuple(self._registered_names):
                self._registered_server._tool_manager._tools.pop(name, None)
        self._registered_names.clear()
        self._exposed.clear()

    def _record_failure(
        self, category: str, *, disable_delegation: bool = False
    ) -> None:
        category = _normalize_category(category)
        with self._lock:
            self._state["last_failure_category"] = category
            self._state["failure_counts"][category] += 1
            if disable_delegation:
                self._state["generic_delegation_available"] = False

    def health_snapshot(self) -> dict[str, Any]:
        with self._lock:
            value = deepcopy(self._state)
            value["failure_counts"] = dict(value["failure_counts"])
            value["policy_classifications"] = (
                self._policy.classification_counts if self._policy else {}
            )
            value["advertised_is_callable"] = False
            value["callable_requires_exact_policy_and_schema_match"] = True
            value["writes_allowed"] = False
            value["direct_ha_fallback_allowed"] = False
            return value


class _GatewayFailure(RuntimeError):
    def __init__(self, category: str, *, dispatched: bool):
        super().__init__("The reviewed upstream read operation failed.")
        self.category = category
        self.dispatched = dispatched


def _normalize_category(category: str) -> str:
    value = str(category)
    return value if value in _FAILURE_CATEGORIES else "internal_error"


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


def _server_version() -> str:
    from ..version import SERVER_VERSION

    return SERVER_VERSION


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


UPSTREAM_READ_GATEWAY = UpstreamReadGateway()
