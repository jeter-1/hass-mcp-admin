"""Exact, argument-constrained upstream provider for governed backup creation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import inspect
import re
import threading
from typing import Any, Awaitable, Callable

from ..clients.mcp import DashboardTransportError
from ..clients.upstream_read import (
    BeforeDispatchFailure,
    CatalogValidationFailure,
    McpReadCatalog,
    McpReadGatewayTransport,
)
from ..configuration import Settings, parse_upstream_dashboard_endpoint
from ..upstream_tool_policy import (
    catalog_fingerprint,
    load_reviewed_upstream_release_registry,
    runtime_contract_fingerprint,
    runtime_description_fingerprint,
    schema_fingerprint,
)
from ..version import SERVER_VERSION


PROVIDER_ID = "upstream_operational_backup"
REQUIRED_TOOL = "ha_manage_backup"
MAX_ERROR_BYTES = 16_384
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SAFE_BACKUP_NAME = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,95}$"
)


class OperationalBackupProviderError(RuntimeError):
    """Bounded provider failure with explicit dispatch evidence."""

    def __init__(
        self,
        category: str,
        *,
        dispatched: bool,
        operation_id: str | None = None,
    ) -> None:
        super().__init__("The governed backup provider could not complete the request.")
        self.category = category
        self.dispatched = dispatched
        self.operation_id = operation_id


@dataclass(frozen=True)
class BackupProviderEvidence:
    provider: str
    server_name: str
    server_version: str
    protocol_version: str
    compatibility_entry_id: str
    source_commit: str
    image_index_digest: str
    catalog_fingerprint: str
    tool_contract_fingerprint: str
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
            "tool_contract_fingerprint": self.tool_contract_fingerprint,
            "argument_constraints": dict(self.argument_constraints),
            "runtime_artifact_observed": False,
            "fallback": "none",
        }


@dataclass(frozen=True)
class BackupDispatchResult:
    backup_id: str | None
    operation_id: str | None
    name: str
    date: str | None
    size_bytes: int | None
    provider_evidence: BackupProviderEvidence


@dataclass
class BackupProviderState:
    configured: bool = False
    operational_status: str = "unconfigured"
    probe_count: int = 0
    request_count: int = 0
    dispatch_count: int = 0
    probe_success_count: int = 0
    dispatch_success_count: int = 0
    failure_counts: Counter[str] = field(default_factory=Counter)
    indeterminate_count: int = 0
    last_failure_category: str | None = None
    last_success_at: str | None = None
    selected_compatibility_entry_id: str | None = None
    observed_upstream_version: str | None = None
    fallback_count: int = 0


BeforeDispatch = Callable[[], None | Awaitable[None]]


class ReviewedOperationalBackupProvider:
    """Permit only ``snapshot/create/name`` over one exact reviewed tool."""

    def __init__(self) -> None:
        self._transport: McpReadGatewayTransport | Any | None = None
        self._known_secrets: tuple[str, ...] = ()
        self._state = BackupProviderState()
        self._lock = threading.Lock()

    def configure(
        self,
        settings: Settings,
        *,
        transport: McpReadGatewayTransport | Any | None = None,
    ) -> None:
        endpoint = parse_upstream_dashboard_endpoint(
            settings.upstream_dashboard_mcp_url
        )
        self._known_secrets = tuple(
            value
            for value in (
                settings.access_secret,
                settings.ha_token,
                *(endpoint.secret_values if endpoint else ()),
            )
            if value
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
        self._state = BackupProviderState(
            configured=bool(endpoint),
            operational_status="unknown" if endpoint else "unconfigured",
        )

    async def probe(self) -> BackupProviderEvidence:
        with self._lock:
            self._state.probe_count += 1
        if self._transport is None:
            self._fail("provider_unavailable", dispatched=False)
        try:
            catalog = await self._transport.discover()
            evidence = self._validate_catalog(catalog)
            self._record_success(evidence, dispatched=False)
            return evidence
        except OperationalBackupProviderError:
            raise
        except DashboardTransportError as exc:
            category = _transport_category(exc.category)
            self._fail(category, dispatched=False)
        except Exception:
            self._fail("provider_error", dispatched=False)

    async def create_full_backup(
        self,
        name: str,
        *,
        before_dispatch: BeforeDispatch,
    ) -> BackupDispatchResult:
        if (
            not isinstance(name, str)
            or name != name.strip()
            or not _SAFE_BACKUP_NAME.fullmatch(name)
            or ".." in name
        ):
            self._fail("invalid_request", dispatched=False)
        if self._transport is None:
            self._fail("provider_unavailable", dispatched=False)
        arguments = {"scope": "snapshot", "action": "create", "name": name}
        dispatched = False
        evidence: BackupProviderEvidence | None = None

        async def persist_dispatch() -> None:
            nonlocal dispatched
            prepared = before_dispatch()
            if inspect.isawaitable(prepared):
                await prepared
            dispatched = True
            with self._lock:
                self._state.dispatch_count += 1

        def validate(catalog: McpReadCatalog) -> None:
            nonlocal evidence
            evidence = self._validate_catalog(catalog)

        with self._lock:
            self._state.request_count += 1
        try:
            exchange = await self._transport.execute_read(
                REQUIRED_TOOL,
                arguments,
                timeout_seconds=1_860.0,
                catalog_validator=validate,
                before_dispatch=persist_dispatch,
            )
            if evidence is None:
                self._fail("internal_invariant_violation", dispatched=dispatched)
            payload = self._decode_result(exchange.call_result)
            result = self._normalize_success(payload, name, evidence)
            self._record_success(evidence, dispatched=True)
            return result
        except OperationalBackupProviderError:
            raise
        except BeforeDispatchFailure as exc:
            raise exc.cause
        except CatalogValidationFailure as exc:
            if isinstance(exc.cause, OperationalBackupProviderError):
                raise exc.cause
            self._fail("provider_error", dispatched=False)
        except DashboardTransportError as exc:
            category = _transport_category(exc.category)
            if dispatched and category in {"provider_timeout", "provider_unavailable"}:
                category = "indeterminate_dispatch"
            self._fail(category, dispatched=dispatched)
        except Exception:
            self._fail(
                "indeterminate_dispatch" if dispatched else "provider_error",
                dispatched=dispatched,
            )

    def _validate_catalog(
        self, catalog: McpReadCatalog
    ) -> BackupProviderEvidence:
        registry = load_reviewed_upstream_release_registry()
        if catalog.server_name != "ha-mcp":
            self._fail("server_identity_mismatch", dispatched=False)
        release = registry.by_version.get(catalog.server_version)
        if release is None:
            self._fail("upstream_version_mismatch", dispatched=False)
        if catalog.protocol_version not in release.allowed_protocol_versions:
            self._fail("unsupported_protocol_version", dispatched=False)
        tools = [
            item
            for item in catalog.tools
            if isinstance(item, dict) and item.get("name") == REQUIRED_TOOL
        ]
        if len(tools) != 1:
            self._fail("required_tool_missing", dispatched=False)
        try:
            observed_catalog_fingerprint = catalog_fingerprint(
                [dict(item) for item in catalog.tools]
            )
            tool = tools[0]
            expected = release.tool_contracts_by_name[REQUIRED_TOOL]
            observed = {
                "input_schema_fingerprint": schema_fingerprint(
                    tool.get("inputSchema")
                ),
                "description_fingerprint": (
                    runtime_description_fingerprint(tool.get("description"))
                    or schema_fingerprint({"invalid_description": True})
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
                "runtime_contract_fingerprint": runtime_contract_fingerprint(
                    tool,
                    model=release.runtime_contract_fingerprint_model,
                ),
            }
        except (TypeError, ValueError, OverflowError):
            self._fail("invalid_response", dispatched=False)
        if (
            expected.policy_classification != "mixed_or_requires_wrapper"
            or expected.reviewed_automatic_read
            or any(
                observed[name] != getattr(expected, name)
                for name in observed
            )
        ):
            self._fail("reviewed_contract_mismatch", dispatched=False)
        if observed_catalog_fingerprint != release.catalog_fingerprint:
            self._fail("catalog_mismatch", dispatched=False)
        return BackupProviderEvidence(
            provider=PROVIDER_ID,
            server_name=catalog.server_name,
            server_version=catalog.server_version,
            protocol_version=catalog.protocol_version,
            compatibility_entry_id=release.entry_id,
            source_commit=release.source_commit,
            image_index_digest=release.image_index_digest,
            catalog_fingerprint=observed_catalog_fingerprint,
            tool_contract_fingerprint=expected.runtime_contract_fingerprint,
            argument_constraints={
                "scope": "snapshot",
                "action": "create",
                "name": "bounded_engineering_value",
                "restore_allowed": False,
                "delete_allowed": False,
                "arbitrary_arguments_allowed": False,
            },
        )

    def _decode_result(self, result: dict[str, Any]) -> dict[str, Any]:
        content = result.get("content")
        if not isinstance(content, list) or len(content) != 1:
            self._fail("invalid_response", dispatched=True)
        item = content[0]
        if (
            not isinstance(item, dict)
            or item.get("type") != "text"
            or not isinstance(item.get("text"), str)
        ):
            self._fail("invalid_response", dispatched=True)
        text = item["text"]
        try:
            if len(text.encode("utf-8")) > MAX_ERROR_BYTES:
                self._fail("invalid_response", dispatched=True)
            payload = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_members,
                parse_constant=_reject_nonfinite,
            )
        except (TypeError, ValueError, UnicodeError, RecursionError):
            self._fail("invalid_response", dispatched=True)
        if not isinstance(payload, dict):
            self._fail("invalid_response", dispatched=True)
        if result.get("isError") or payload.get("success") is False:
            code = (
                payload.get("error", {}).get("code")
                if isinstance(payload.get("error"), dict)
                else None
            )
            self._fail(_upstream_error_category(code), dispatched=True)
        return payload

    def _normalize_success(
        self,
        payload: dict[str, Any],
        expected_name: str,
        evidence: BackupProviderEvidence,
    ) -> BackupDispatchResult:
        if payload.get("success") is not True or payload.get("name") != expected_name:
            self._fail("invalid_response", dispatched=True)
        backup_id = _safe_identifier(payload.get("backup_id"))
        operation_id = _safe_identifier(payload.get("backup_job_id"))
        if backup_id is None and operation_id is None:
            self._fail("invalid_response", dispatched=True)
        date = payload.get("date")
        if date is not None and (
            not isinstance(date, str) or len(date) > 64
        ):
            self._fail("invalid_response", dispatched=True)
        size = payload.get("size_bytes")
        if size is not None and (
            isinstance(size, bool) or not isinstance(size, int) or size < 0
        ):
            self._fail("invalid_response", dispatched=True)
        return BackupDispatchResult(
            backup_id=backup_id,
            operation_id=operation_id,
            name=expected_name,
            date=date,
            size_bytes=size,
            provider_evidence=evidence,
        )

    def _record_success(
        self, evidence: BackupProviderEvidence, *, dispatched: bool
    ) -> None:
        with self._lock:
            self._state.operational_status = "available"
            if dispatched:
                self._state.dispatch_success_count += 1
            else:
                self._state.probe_success_count += 1
            self._state.last_failure_category = None
            self._state.last_success_at = datetime.now(timezone.utc).isoformat()
            self._state.selected_compatibility_entry_id = (
                evidence.compatibility_entry_id
            )
            self._state.observed_upstream_version = evidence.server_version

    def _fail(self, category: str, *, dispatched: bool) -> None:
        with self._lock:
            self._state.failure_counts[category] += 1
            self._state.last_failure_category = category
            if category == "indeterminate_dispatch":
                self._state.indeterminate_count += 1
            if category not in {
                "invalid_request",
                "backup_rejected",
                "backup_failed",
            }:
                self._state.operational_status = "unavailable"
        raise OperationalBackupProviderError(category, dispatched=dispatched)

    def health_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "provider": PROVIDER_ID,
                "configured": self._state.configured,
                "operational_status": self._state.operational_status,
                "probe_count": self._state.probe_count,
                "request_count": self._state.request_count,
                "dispatch_count": self._state.dispatch_count,
                "probe_success_count": self._state.probe_success_count,
                "dispatch_success_count": (
                    self._state.dispatch_success_count
                ),
                "failure_counts": dict(self._state.failure_counts),
                "indeterminate_count": self._state.indeterminate_count,
                "last_failure_category": self._state.last_failure_category,
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


def _safe_identifier(value: Any) -> str | None:
    return value if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) else None


def _transport_category(category: str) -> str:
    return {
        "authentication_failed": "permission_failure",
        "connection_failed": "provider_unavailable",
        "endpoint_rejected": "provider_unavailable",
        "timeout": "provider_timeout",
        "response_too_large": "invalid_response",
    }.get(category, category if category in {
        "protocol_error",
        "invalid_response",
    } else "provider_error")


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
        return "backup_rejected"
    if code == "SERVICE_CALL_FAILED":
        return "backup_failed"
    return "provider_error"


UPSTREAM_OPERATIONAL_BACKUP = ReviewedOperationalBackupProvider()
