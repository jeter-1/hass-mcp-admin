"""Correlated secret-path authentication, routing, rate limiting, and audit."""

from __future__ import annotations

import hashlib
import json
import logging
import time

from .audit import AuditLogger, AuditRecord
from .capabilities import capability_for_tool
from .configuration import Settings
from .errors import ErrorCode, error_definition
from .logging_config import get_logger, log_event
from .models import FailureResponse, Timing
from .observability import METRICS
from .request_context import begin_request, current_telemetry, end_request


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
    def __init__(self, app, settings: Settings, audit: AuditLogger):
        self.app = app
        self.settings = settings
        self.audit = audit
        self.prefix = f"/{settings.access_secret}"
        self.clients: dict[str, TokenBucket] = {}
        self.auth_failures: dict[str, TokenBucket] = {}
        self.global_bucket = TokenBucket(
            settings.rate_limit_per_minute * 2, settings.rate_limit_burst * 2
        )
        self.logger = get_logger("gateway")

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

    @staticmethod
    def _client_ip(scope) -> str:
        forwarded = AuthenticatedMcpGateway._header(scope, b"cf-connecting-ip")
        if forwarded:
            return forwarded
        client = scope.get("client")
        return client[0] if client else "unknown"

    @staticmethod
    def _caller_id(client_ip: str) -> str:
        return hashlib.sha256(client_ip.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _bucket(store: dict, key: str, rate: float, burst: float) -> TokenBucket:
        if key not in store:
            if len(store) > 1000:
                store.clear()
            store[key] = TokenBucket(rate, burst)
        return store[key]

    def _audit_path(self, path: str) -> str:
        secret = self.settings.access_secret
        return (path.replace(secret, "<access_secret>") if secret else path)[:64]

    def rate_limiter_state(self) -> dict:
        return {
            "tracked_clients": len(self.clients),
            "tracked_auth_failures": len(self.auth_failures),
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
        operation_started = None
        parameters = {}
        capability = {}
        try:
            if path == "/health":
                return await self._respond(send, 200, b"ok", request_id)

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
                    "event": "auth_failure",
                    "request_id": request_id,
                    "authenticated": False,
                    "caller_id": caller_id,
                    "path": self._audit_path(path),
                    "result_status": "rejected",
                    "error_code": code.value,
                })
                body = b"not found" if status == 404 else b"too many requests"
                return await self._respond(send, status, body, request_id)

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
                return await self._respond(
                    send,
                    429,
                    failure.to_json(self.settings.response_size_limit).encode(),
                    request_id,
                    b"application/json",
                )

            new_receive = receive
            if scope.get("method") == "POST":
                chunks, more, total = [], True, 0
                while more:
                    message = await receive()
                    chunks.append(message)
                    total += len(message.get("body", b""))
                    more = message.get("more_body", False)
                    if total > 2_000_000:
                        break
                body = b"".join(message.get("body", b"") for message in chunks)
                try:
                    rpc = json.loads(body)
                    if isinstance(rpc, dict) and isinstance(rpc.get("method"), str):
                        rpc_method = rpc["method"]
                        operation_started = time.perf_counter()
                    if rpc_method == "tools/call":
                        params = rpc.get("params", {}) or {}
                        tool_name = params.get("name")
                        parameters = params.get("arguments", {}) or {}
                        capability = capability_for_tool(tool_name)
                        telemetry.tool_name = tool_name
                        telemetry.tool_started = time.perf_counter()
                        METRICS.record_tool_call()
                except (json.JSONDecodeError, UnicodeDecodeError):
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
                await send(message)

            log_event(
                self.logger,
                logging.INFO,
                "request_started",
                "Authenticated MCP request started.",
                context={"tool": tool_name},
                secret=self.settings.access_secret,
            )
            await self.app(forwarded, new_receive, correlated_send)
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
                if capability.get("category") == "governance":
                    audit_parameters = {
                        key: parameters[key]
                        for key in ("plan_id", "automation_id", "operation", "expiration_minutes")
                        if key in parameters
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
                ))
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
