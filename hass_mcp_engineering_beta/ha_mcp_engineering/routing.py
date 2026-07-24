"""Correlated secret-path authentication, routing, rate limiting, and audit."""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import ipaddress
import json
import logging
import re
import threading
import time

from .audit import (
    AUTH_FAILURE_EVENT,
    AUTH_FAILURE_THROTTLED_EVENT,
    RATE_LIMITED_EVENT,
    AuditLogger,
    AuditRecord,
)
from .capabilities import capability_for_tool
from .configuration import Settings
from .errors import ErrorCode, error_definition
from .logging_config import get_logger, log_event
from .models import FailureResponse, Timing
from .observability import METRICS
from .providers.dispatch import CANONICAL_DISPATCHER
from .providers.routing import requires_prevalidation_enforcement
from .request_context import begin_request, current_telemetry, end_request


MAX_BUCKET_STORE_SIZE = 1000
MAX_MCP_OUTCOME_CAPTURE_BYTES = 1_100_000
UNKNOWN_CLIENT_IDENTITY = "unknown"
UPSTREAM_VERSION_AUDIT_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\."
    r"(?:0|[1-9][0-9]{0,3})(?:-[0-9A-Za-z.-]{1,64})?"
    r"(?:\+[0-9A-Za-z.-]{1,64})?$"
)


def _jsonrpc_response_from_body(body: bytes) -> dict | None:
    """Return one bounded JSON-RPC response from JSON or SSE output."""

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    candidates = []
    if "data:" in text:
        candidates.extend(
            line.removeprefix("data:").strip()
            for line in text.replace("\r", "").splitlines()
            if line.startswith("data:")
        )
    else:
        candidates.append(text.strip())
    for candidate in reversed(candidates):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("jsonrpc") == "2.0":
            return payload
    return None


def _mcp_error_code(payload: dict) -> str | None:
    """Classify an MCP/JSON-RPC failure without retaining raw error text."""

    error = payload.get("error")
    if isinstance(error, dict):
        return (
            ErrorCode.INVALID_REQUEST.value
            if error.get("code") in {-32700, -32600, -32601, -32602}
            else ErrorCode.INTERNAL_SERVER_ERROR.value
        )
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("isError") is not True:
        return None
    text = " ".join(
        str(item.get("text", ""))
        for item in result.get("content", [])
        if isinstance(item, dict) and item.get("type") == "text"
    ).lower()
    if "validation error for" in text or "unknown tool" in text:
        return ErrorCode.INVALID_REQUEST.value
    return ErrorCode.INTERNAL_SERVER_ERROR.value


def _structured_tool_failure_code(payload: dict) -> str | None:
    """Extract a stable Engineering failure code from an MCP tool result."""

    result = payload.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        return None
    for item in result.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        try:
            rendered = json.loads(item.get("text", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(rendered, dict) or rendered.get("success") is not False:
            continue
        code = rendered.get("error_code")
        if isinstance(code, str) and code in {value.value for value in ErrorCode}:
            return code
    return None


class TokenBucket:
    def __init__(self, per_minute: float, burst: float):
        self.rate = per_minute / 60.0
        self.cap = burst
        self.tokens = burst
        self.last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.cap, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    def summary(self) -> dict[str, float]:
        return {"capacity": self.cap, "available_tokens": round(self.tokens, 2)}


class AuthenticatedMcpGateway:
    def __init__(
        self,
        app,
        settings: Settings,
        audit: AuditLogger,
        *,
        require_initial_catalog_reconciliation: bool = False,
    ):
        self.app = app
        self.settings = settings
        self.audit = audit
        self.prefix = f"/{settings.access_secret}"
        self._initial_catalog_reconciliation_required = bool(
            require_initial_catalog_reconciliation
        )
        self._initial_catalog_reconciliation_complete = not (
            self._initial_catalog_reconciliation_required
        )
        self._catalog_readiness_lock = threading.Lock()
        self.clients: OrderedDict[str, TokenBucket] = OrderedDict()
        self.auth_failures: OrderedDict[str, TokenBucket] = OrderedDict()
        self._bucket_lock = threading.Lock()
        self._trusted_proxy_networks = tuple(
            ipaddress.ip_network(value, strict=False)
            for value in settings.trusted_proxy_cidrs
        )
        self.global_bucket = TokenBucket(
            settings.rate_limit_per_minute * 2, settings.rate_limit_burst * 2
        )
        self.logger = get_logger("gateway")

    @property
    def initial_catalog_reconciliation_required(self) -> bool:
        return self._initial_catalog_reconciliation_required

    def mark_initial_catalog_reconciled(self) -> None:
        """Publish readiness only after the configured initial reconcile returns."""

        with self._catalog_readiness_lock:
            self._initial_catalog_reconciliation_complete = True

    def catalog_readiness_state(self) -> dict[str, bool | str]:
        with self._catalog_readiness_lock:
            complete = self._initial_catalog_reconciliation_complete
        return {
            "ready": complete,
            "initial_reconciliation_required": (
                self._initial_catalog_reconciliation_required
            ),
            "initial_reconciliation_complete": complete,
            "status": "ready" if complete else "initial_reconciliation_pending",
        }

    async def _respond_catalog_readiness(self, send, request_id: str) -> None:
        state = self.catalog_readiness_state()
        await self._respond(
            send,
            200 if state["ready"] else 503,
            json.dumps(state, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            request_id,
            b"application/json",
        )

    @staticmethod
    def _header(scope, wanted: bytes) -> str | None:
        for name, value in scope.get("headers", []):
            if name.lower() == wanted:
                return value.decode("latin-1")
        return None

    @staticmethod
    async def _respond(send, status: int, body: bytes, request_id: str, content_type=b"text/plain") -> None:
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", content_type),
                (b"x-request-id", request_id.encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    @classmethod
    async def _respond_mcp_tool_result(
        cls,
        send,
        *,
        rpc_id,
        rendered: str,
        request_id: str,
    ) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "content": [{"type": "text", "text": rendered}],
                "isError": False,
            },
        }
        body = (
            "event: message\r\n"
            f"data: {json.dumps(payload, separators=(',', ':'))}\r\n\r\n"
        ).encode("utf-8")
        await cls._respond(
            send,
            200,
            body,
            request_id,
            b"text/event-stream",
        )

    @staticmethod
    def _apply_mcp_outcome(telemetry, body: bytes) -> None:
        payload = _jsonrpc_response_from_body(body)
        if payload is None:
            return
        structured_code = _structured_tool_failure_code(payload)
        if structured_code:
            telemetry.error_code = structured_code
            telemetry.result_status = "failure"
            telemetry.completeness = telemetry.completeness or "failed"
            return
        mcp_code = _mcp_error_code(payload)
        if mcp_code:
            telemetry.error_code = telemetry.error_code or mcp_code
            telemetry.result_status = "failure"
            telemetry.completeness = telemetry.completeness or "failed"

    def _client_ip(self, scope) -> str:
        return resolve_client_address(
            scope,
            trust_cf_connecting_ip=self.settings.trust_cf_connecting_ip,
            trusted_proxy_networks=self._trusted_proxy_networks,
        )

    @staticmethod
    def _caller_id(client_ip: str) -> str:
        return hashlib.sha256(client_ip.encode("utf-8")).hexdigest()[:12]

    def _bucket(
        self,
        store: OrderedDict[str, TokenBucket],
        key: str,
        rate: float,
        burst: float,
    ) -> TokenBucket:
        # No await occurs while this lock is held. This keeps creation,
        # recency updates, and eviction atomic across concurrent ASGI tasks.
        with self._bucket_lock:
            existing = store.pop(key, None)
            if existing is not None:
                store[key] = existing
                return existing
            while len(store) >= MAX_BUCKET_STORE_SIZE:
                store.popitem(last=False)
            bucket = TokenBucket(rate, burst)
            store[key] = bucket
            return bucket

    def _audit_path(self, path: str) -> str:
        # The invalid path itself is credential material: it can contain either
        # the configured secret or an attacker-supplied candidate. Retain only a
        # fixed diagnostic shape so neither value enters the audit record.
        del path
        return "/<access_secret>/mcp"

    def rate_limiter_state(self) -> dict:
        return {
            "tracked_clients": len(self.clients),
            "tracked_auth_failures": len(self.auth_failures),
            "maximum_tracked_clients": MAX_BUCKET_STORE_SIZE,
            "forwarded_header_trust_enabled": self.settings.trust_cf_connecting_ip,
            "trusted_proxy_network_count": len(self._trusted_proxy_networks),
            "global": self.global_bucket.summary(),
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        telemetry, context_token = begin_request(self._header(scope, b"x-request-id"))
        request_id = telemetry.request_id
        client_ip = self._client_ip(scope)
        caller_id = self._caller_id(client_ip)
        telemetry.caller_id = caller_id
        tool_name = None
        rpc_method = None
        rpc = None
        operation_started = None
        parameters = {}
        capability = {}
        response_capture = bytearray()
        authenticated_request_accepted = False
        try:
            if path == "/health":
                return await self._respond(send, 200, b"ok", request_id)
            if path == "/ready":
                return await self._respond_catalog_readiness(send, request_id)

            if not path.startswith(self.prefix + "/") and path != self.prefix:
                bucket = self._bucket(self.auth_failures, client_ip, 0.5, 5)
                status = 404 if bucket.allow() else 429
                code = (
                    ErrorCode.AUTHENTICATION_FAILURE
                    if status == 404
                    else ErrorCode.RATE_LIMIT_EXCEEDED
                )
                METRICS.record_error(code.value)
                self.audit.write({
                    "event": (
                        AUTH_FAILURE_EVENT
                        if status == 404
                        else AUTH_FAILURE_THROTTLED_EVENT
                    ),
                    "request_id": request_id,
                    "authenticated": False,
                    "caller_id": caller_id,
                    "path": self._audit_path(path),
                    "result_status": "rejected",
                    "error_code": code.value,
                })
                body = b"not found" if status == 404 else b"too many requests"
                return await self._respond(send, status, body, request_id)

            if not self.catalog_readiness_state()["ready"]:
                telemetry.error_code = ErrorCode.PROVIDER_UNAVAILABLE.value
                telemetry.result_status = "failure"
                telemetry.completeness = "failed"
                telemetry.response_status = 503
                return await self._respond_catalog_readiness(send, request_id)

            client_bucket = self._bucket(
                self.clients,
                client_ip,
                self.settings.rate_limit_per_minute,
                self.settings.rate_limit_burst,
            )
            if not client_bucket.allow() or not self.global_bucket.allow():
                definition = error_definition(ErrorCode.RATE_LIMIT_EXCEEDED)
                telemetry.error_code = ErrorCode.RATE_LIMIT_EXCEEDED.value
                METRICS.record_error(telemetry.error_code)
                failure = FailureResponse(
                    operation="mcp_request",
                    error="RateLimitExceeded",
                    error_code=telemetry.error_code,
                    message=definition.message,
                    retryable=definition.retryable,
                    timing=Timing(total_ms=telemetry.total_duration_ms),
                    request_id=request_id,
                )
                self.audit.write({
                    "event": RATE_LIMITED_EVENT,
                    "request_id": request_id,
                    "authenticated": True,
                    "caller_id": caller_id,
                    "result_status": "rejected",
                    "error_code": telemetry.error_code,
                })
                return await self._respond(
                    send,
                    429,
                    failure.to_json(self.settings.response_size_limit).encode(),
                    request_id,
                    b"application/json",
                )

            authenticated_request_accepted = True
            new_receive = receive
            if scope.get("method") == "POST":
                chunks, more, total = [], True, 0
                request_oversized = False
                while more:
                    message = await receive()
                    chunks.append(message)
                    total += len(message.get("body", b""))
                    more = message.get("more_body", False)
                    if total > 2_000_000:
                        request_oversized = True
                        break
                body = b"".join(message.get("body", b"") for message in chunks)
                try:
                    rpc = json.loads(body)
                    if isinstance(rpc, dict) and isinstance(rpc.get("method"), str):
                        rpc_method = rpc["method"]
                        operation_started = time.perf_counter()
                    if rpc_method == "tools/call":
                        params = rpc.get("params", {}) or {}
                        if not isinstance(params, dict):
                            raise TypeError("tools/call params must be an object")
                        tool_name = params.get("name")
                        raw_parameters = params.get("arguments", {})
                        parameters = raw_parameters if isinstance(raw_parameters, dict) else {}
                        capability = capability_for_tool(tool_name)
                        telemetry.tool_name = tool_name
                        telemetry.tool_started = time.perf_counter()
                        METRICS.record_tool_call()
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                    telemetry.error_code = ErrorCode.INVALID_REQUEST.value
                queue = list(chunks)

                async def replay():
                    return queue.pop(0) if queue else await receive()

                new_receive = replay

            forwarded = dict(scope)
            forwarded["path"] = path[len(self.prefix):] or "/"
            if forwarded["path"] == "/mcp":
                forwarded["path"] = "/mcp/"
            if forwarded.get("raw_path"):
                raw_prefix = self.prefix.encode()
                raw_path = forwarded["raw_path"]
                if raw_path.startswith(raw_prefix):
                    forwarded["raw_path"] = raw_path[len(raw_prefix):] or b"/"
                    if forwarded["raw_path"] == b"/mcp":
                        forwarded["raw_path"] = b"/mcp/"

            async def correlated_send(message):
                if message["type"] == "http.response.start":
                    telemetry.response_status = message["status"]
                    headers = list(message.get("headers", []))
                    headers.append((b"x-request-id", request_id.encode("ascii")))
                    message = {**message, "headers": headers}
                elif message["type"] == "http.response.body":
                    available = MAX_MCP_OUTCOME_CAPTURE_BYTES - len(response_capture)
                    if available > 0:
                        response_capture.extend(message.get("body", b"")[:available])
                await send(message)

            log_event(
                self.logger,
                logging.INFO,
                "request_started",
                "Authenticated MCP request started.",
                context={"tool": tool_name},
                secret=self.settings.access_secret,
            )
            if scope.get("method") == "POST" and request_oversized:
                parameters = {}
                telemetry.error_code = ErrorCode.INVALID_REQUEST.value
                telemetry.result_status = "failure"
                telemetry.completeness = "failed"
                telemetry.response_status = 413
                definition = error_definition(ErrorCode.INVALID_REQUEST)
                failure = FailureResponse(
                    operation=tool_name or "mcp_request",
                    error="RequestTooLarge",
                    error_code=telemetry.error_code,
                    message=definition.message,
                    retryable=definition.retryable,
                    timing=Timing(total_ms=telemetry.total_duration_ms),
                    request_id=request_id,
                )
                await self._respond(
                    send,
                    413,
                    failure.to_json(self.settings.response_size_limit).encode(),
                    request_id,
                    b"application/json",
                )
                return
            if (
                rpc_method == "tools/call"
                and isinstance(rpc, dict)
                and not request_oversized
                and requires_prevalidation_enforcement(tool_name)
            ):
                rendered = await CANONICAL_DISPATCHER.enforce_prevalidation(
                    tool_name,
                    response_limit=self.settings.response_size_limit,
                )
                telemetry.response_status = 200
                await self._respond_mcp_tool_result(
                    send,
                    rpc_id=rpc.get("id"),
                    rendered=rendered,
                    request_id=request_id,
                )
                return
            await self.app(forwarded, new_receive, correlated_send)
            if tool_name and response_capture:
                self._apply_mcp_outcome(telemetry, bytes(response_capture))
        except Exception:
            telemetry.error_code = telemetry.error_code or ErrorCode.INTERNAL_SERVER_ERROR.value
            if not tool_name:
                METRICS.record_error(telemetry.error_code)
            log_event(
                self.logger,
                logging.ERROR,
                "request_failed",
                "MCP request failed.",
                context={"error_code": telemetry.error_code, "tool": tool_name},
                secret=self.settings.access_secret,
                exc_info=True,
            )
            raise
        finally:
            # recent_error_counts measures terminal public tool outcomes. The
            # transport, provider, and response-conversion layers may all see
            # the same exception, but one tools/call contributes one count.
            if tool_name and telemetry.error_code:
                METRICS.record_error(telemetry.error_code)
            if telemetry.tool_started is not None:
                telemetry.tool_duration_ms = round(
                    (time.perf_counter() - telemetry.tool_started) * 1000, 3
                )
                METRICS.record_tool_completion(telemetry.tool_duration_ms)
            transport_duration = telemetry.total_duration_ms
            operation_duration = (
                round((time.perf_counter() - operation_started) * 1000, 3)
                if operation_started is not None
                else None
            )
            METRICS.record_transport_completion()
            if operation_duration is not None and rpc_method is not None:
                METRICS.record_mcp_operation(operation_duration, rpc_method)
            if tool_name:
                risk = capability.get("risk")
                access = "write" if risk in {"behavioral_write", "physical_action", "destructive", "infrastructure"} else "read"
                resource_ids = {
                    key: str(value)[:128]
                    for key, value in parameters.items()
                    if key.endswith("_id") and isinstance(value, (str, int))
                }
                audit_parameters = parameters
                if telemetry.error_code in {
                    ErrorCode.INVALID_REQUEST.value,
                    ErrorCode.VALIDATION_FAILURE.value,
                }:
                    # Invalid caller values are untrusted.  Preserve only the
                    # bounded field names needed to diagnose a schema failure.
                    audit_parameters = {
                        "validation": "rejected",
                        "argument_fields": sorted(
                            str(key)[:64] for key in parameters
                        )[:32],
                    }
                    resource_ids = {}
                elif tool_name == "upsert_automation":
                    # Preserve only the bounded target and fail-closed reason;
                    # the caller-supplied configuration is never audited.
                    audit_parameters = {
                        "automation_id": (
                            str(parameters.get("automation_id"))[:128]
                            if isinstance(parameters.get("automation_id"), (str, int))
                            else ""
                        ),
                        "refusal_reason": "governance_required",
                    }
                elif tool_name == "delete_automation":
                    audit_parameters = {
                        "automation_id": (
                            str(parameters.get("automation_id"))[:128]
                            if isinstance(parameters.get("automation_id"), (str, int))
                            else ""
                        ),
                        "refusal_reason": "operation_prohibited",
                    }
                elif tool_name in {"call_service", "reload_domain"}:
                    audit_parameters = {
                        "refusal_reason": "provider_unavailable",
                    }
                elif capability.get("category") == "governance":
                    audit_parameters = {
                        key: parameters[key]
                        for key in ("plan_id", "automation_id", "operation", "expiration_minutes")
                        if key in parameters
                    }
                elif tool_name == "change_impact_analysis":
                    # Cursor material and result evidence never enter audit.
                    # Keep only bounded validated intent plus aggregate output.
                    audit_parameters = {
                        key: parameters[key]
                        for key in (
                            "entity_id",
                            "replacement_entity_id",
                            "operation",
                            "include_indirect",
                            "max_depth",
                            "source_types",
                            "detail_level",
                            "limit",
                            "refresh_index",
                        )
                        if key in parameters
                    }
                elif tool_name == "configuration_integrity_analysis":
                    # Raw signed cursors and finding evidence never enter audit.
                    audit_parameters = {
                        key: parameters[key]
                        for key in (
                            "source_types",
                            "finding_types",
                            "include_orphan_candidates",
                            "detail_level",
                            "limit",
                            "refresh_index",
                        )
                        if key in parameters
                    }
                    audit_parameters["cursor_present"] = bool(
                        parameters.get("cursor")
                    )
                elif tool_name == "incident_correlation":
                    # Record bounded intent and counts, never raw cursors or
                    # entity lists, evidence, traces, history, or log text.
                    audit_parameters = {
                        key: parameters[key]
                        for key in (
                            "lookback_hours",
                            "correlation_window_minutes",
                            "trace_limit",
                            "include_dependency_context",
                            "include_integrity_context",
                            "include_reliability_context",
                            "detail_level",
                            "limit",
                            "refresh_index",
                        )
                        if key in parameters
                    }
                    audit_parameters.update(
                        {
                            "focus_entity_supplied": bool(parameters.get("focus_entity_id")),
                            "automation_id_supplied": bool(parameters.get("automation_id")),
                            "related_entity_count": len(parameters.get("related_entity_ids") or [])
                            if isinstance(parameters.get("related_entity_ids"), list)
                            else 0,
                            "cursor_present": bool(parameters.get("cursor")),
                        }
                    )
                elif tool_name == "handoff_generation":
                    # Handoff source objects, Markdown, evidence and raw cursor
                    # are excluded. Retain only bounded validated intent.
                    audit_parameters = {
                        key: parameters[key]
                        for key in (
                            "handoff_type", "lookback_hours",
                            "include_runtime_health", "include_governance_context",
                            "include_dependency_context", "include_integrity_context",
                            "include_reliability_context", "include_incident_context",
                            "include_recommendations", "detail_level", "output_format",
                            "limit", "refresh_index",
                        )
                        if key in parameters
                    }
                    audit_parameters.update({
                        "focus_entity_count": len(parameters.get("focus_entity_ids") or []) if isinstance(parameters.get("focus_entity_ids"), list) else 0,
                        "automation_count": len(parameters.get("automation_ids") or []) if isinstance(parameters.get("automation_ids"), list) else 0,
                        "change_plan_count": len(parameters.get("change_plan_ids") or []) if isinstance(parameters.get("change_plan_ids"), list) else 0,
                        "cursor_present": bool(parameters.get("cursor")),
                    })
                elif tool_name == "list_dashboards":
                    audit_parameters = {
                        "limit": parameters.get("limit", 100),
                        "provider": "upstream_dashboard",
                    }
                elif tool_name == "get_dashboard_config":
                    audit_parameters = {
                        "url_path": str(parameters.get("url_path", ""))[:256],
                        "force_reload": bool(parameters.get("force_reload", True)),
                        "provider": "upstream_dashboard",
                    }
                elif capability.get("category") == "upstream_read_gateway":
                    # Generic reads may carry search text, templates, or other
                    # untrusted content.  Audit only the reviewed route,
                    # bounded field names, and already-bounded *_id resources.
                    audit_parameters = {
                        "provider": "upstream_read_gateway",
                        "classification": "automatic_read",
                        "argument_fields": sorted(
                            str(key)[:64] for key in parameters
                        )[:64],
                        "upstream_version_evidence": (
                            telemetry.audit_context.get(
                                "upstream_version_evidence"
                            )
                            if isinstance(
                                telemetry.audit_context.get(
                                    "upstream_version_evidence"
                                ),
                                str,
                            )
                            and UPSTREAM_VERSION_AUDIT_PATTERN.fullmatch(
                                telemetry.audit_context[
                                    "upstream_version_evidence"
                                ]
                            )
                            else "unknown"
                        ),
                        "upstream_identity_status": (
                            telemetry.audit_context.get(
                                "upstream_identity_status"
                            )
                            if telemetry.audit_context.get(
                                "upstream_identity_status"
                            )
                            in {"accepted", "rejected"}
                            else "unknown"
                        ),
                    }
                self.audit.write(AuditRecord(
                    request_id=request_id,
                    tool_name=tool_name,
                    capability_classification=capability.get("status"),
                    operation_category=capability.get("category"),
                    access=access,
                    authenticated=True,
                    caller_id=caller_id,
                    parameters=audit_parameters,
                    result_status=(
                        telemetry.result_status
                        if telemetry.result_status
                        else
                        "failure"
                        if telemetry.error_code
                        else "success" if (telemetry.response_status or 500) < 400 else "failure"
                    ),
                    error_code=telemetry.error_code,
                    duration_ms=telemetry.tool_duration_ms or operation_duration,
                    ha_endpoint_categories=sorted(telemetry.endpoint_categories),
                    resource_ids=resource_ids,
                    analysis_summary=(
                        dict(telemetry.audit_context)
                        if tool_name in {
                            "change_impact_analysis",
                            "configuration_integrity_analysis",
                            "incident_correlation",
                            "handoff_generation",
                        }
                        else {}
                    ),
                ))
            elif authenticated_request_accepted and telemetry.error_code:
                self.audit.write(
                    {
                        "event": "mcp_request",
                        "request_id": request_id,
                        "authenticated": True,
                        "caller_id": caller_id,
                        "mcp_method": rpc_method or "invalid",
                        "result_status": "failure",
                        "error_code": telemetry.error_code,
                    }
                )
            log_event(
                self.logger,
                logging.INFO,
                "request_completed",
                "MCP request completed.",
                context={
                    "tool": tool_name,
                    "mcp_method": rpc_method,
                    "operation_duration_ms": operation_duration,
                    "tool_duration_ms": telemetry.tool_duration_ms,
                    "transport_duration_ms": transport_duration,
                    "response_status": telemetry.response_status,
                    "error_code": telemetry.error_code,
                    "completeness": telemetry.completeness,
                },
                secret=self.settings.access_secret,
            )
            end_request(context_token)


def resolve_client_address(
    scope,
    *,
    trust_cf_connecting_ip: bool,
    trusted_proxy_networks: tuple,
) -> str:
    """Return a bounded canonical client identity from a trusted network path."""

    client = scope.get("client")
    direct = _canonical_ip(client[0] if client else None)
    if direct is None:
        return UNKNOWN_CLIENT_IDENTITY
    if not trust_cf_connecting_ip:
        return direct
    direct_address = ipaddress.ip_address(direct)
    if not any(direct_address in network for network in trusted_proxy_networks):
        return direct
    forwarded = AuthenticatedMcpGateway._header(scope, b"cf-connecting-ip")
    return _canonical_ip(forwarded) or direct


def _canonical_ip(value) -> str | None:
    try:
        return ipaddress.ip_address(str(value).strip()).compressed
    except (ValueError, TypeError):
        return None
