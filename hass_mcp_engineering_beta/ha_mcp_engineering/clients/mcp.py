"""Bounded streamable-HTTP MCP client for the dashboard read provider."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import json
import logging
import re
import socket
import time
from typing import Any, Callable

from mcp import types
from mcp.client.streamable_http import streamablehttp_client

from ..mcp_sdk_compatibility import ReviewedProtocolClientSession as ClientSession


REQUIRED_DASHBOARD_TOOL = "ha_config_get_dashboard"
REQUIRED_DASHBOARD_WRITE_TOOL = "ha_config_set_dashboard"
REQUIRED_BEST_PRACTICES_TOOL = "ha_get_skill_guide"
ALLOWED_DASHBOARD_ARGUMENTS = frozenset(
    {"url_path", "list_only", "force_reload", "include_screenshot"}
)
MAX_TOOL_CATALOG_SIZE = 500
MAX_TOOL_CATALOG_PAGES = 20
MAX_UPSTREAM_CONTENT_CHARS = 2_000_000
MAX_GROUPED_ERROR_CATEGORIES = 8
TRANSPORT_FAILURE_KINDS = frozenset(
    {
        "transport_silence_or_response_loss",
        "provider_5xx_ambiguous",
        "protocol_or_transport_failure",
    }
)
_RECOGNIZED_TYPED_ERROR_CATEGORIES = frozenset(
    {
        "annotation_mismatch",
        "authentication_failed",
        "connection_failed",
        "dashboard_not_found",
        "endpoint_rejected",
        "hash_contract_mismatch",
        "input_schema_mismatch",
        "internal_error",
        "invalid_response",
        "not_configured",
        "output_contract_mismatch",
        "prohibited_argument",
        "protocol_error",
        "required_tool_missing",
        "response_too_large",
        "reviewed_annotation_mismatch",
        "reviewed_contract_mismatch",
        "runtime_descriptor_semantic_drift",
        "schema_incompatible",
        "schema_mismatch",
        "security_contract_mismatch",
        "server_identity_mismatch",
        "timeout",
        "unsupported_protocol_version",
        "unsupported_trust_profile",
        "upstream_attestation_missing",
        "upstream_attestation_revoked",
        "upstream_contract_family_unknown",
        "upstream_error",
        "upstream_input_contract_mismatch",
        "upstream_output_contract_mismatch",
        "upstream_registry_expired",
        "upstream_registry_invalid_signature",
        "upstream_registry_replay_conflict",
        "upstream_registry_rollback",
        "upstream_registry_unavailable",
        "upstream_runtime_contract_mismatch",
        "upstream_security_contract_mismatch",
        "upstream_version_mismatch",
    }
)
_TYPED_ERROR_PRECEDENCE = {
    category: priority
    for priority, categories in enumerate(
        (
            {
                "prohibited_argument",
                "hash_contract_mismatch",
                "reviewed_annotation_mismatch",
                "reviewed_contract_mismatch",
                "security_contract_mismatch",
                "runtime_descriptor_semantic_drift",
                "upstream_input_contract_mismatch",
                "upstream_output_contract_mismatch",
                "upstream_runtime_contract_mismatch",
                "upstream_security_contract_mismatch",
            },
            {
                "annotation_mismatch",
                "input_schema_mismatch",
                "output_contract_mismatch",
                "required_tool_missing",
                "schema_incompatible",
                "schema_mismatch",
                "server_identity_mismatch",
                "unsupported_protocol_version",
                "unsupported_trust_profile",
                "upstream_attestation_missing",
                "upstream_attestation_revoked",
                "upstream_contract_family_unknown",
                "upstream_registry_expired",
                "upstream_registry_invalid_signature",
                "upstream_registry_replay_conflict",
                "upstream_registry_rollback",
                "upstream_registry_unavailable",
                "upstream_version_mismatch",
            },
            {"authentication_failed", "endpoint_rejected"},
            {
                "dashboard_not_found",
                "invalid_response",
                "protocol_error",
                "response_too_large",
                "upstream_error",
            },
            {"connection_failed", "not_configured", "timeout"},
            {"internal_error"},
        )
    )
    for category in categories
}


class DashboardTransportError(RuntimeError):
    """Secret-free transport failure classified at the MCP boundary."""

    def __init__(
        self,
        category: str,
        *,
        retryable: bool | None = None,
        grouped_categories: tuple[str, ...] = (),
        provider_response_received: bool = False,
        http_response_received: bool = False,
        failure_kind: str = "protocol_or_transport_failure",
        http_status_class: str | None = None,
    ):
        super().__init__("The upstream dashboard MCP transport failed.")
        self.category = category
        self.retryable = retryable
        self.grouped_categories = grouped_categories[
            :MAX_GROUPED_ERROR_CATEGORIES
        ]
        self.provider_response_received = provider_response_received is True
        self.http_response_received = http_response_received is True
        self.failure_kind = (
            failure_kind
            if failure_kind in TRANSPORT_FAILURE_KINDS
            else "protocol_or_transport_failure"
        )
        self.http_status_class = (
            http_status_class
            if http_status_class in {"4xx", "5xx"}
            else None
        )

    def evidence_details(self) -> dict[str, Any]:
        """Return only bounded classification evidence, never exception text."""

        details: dict[str, Any] = {
            "provider_response_received": self.provider_response_received,
            "http_response_received": self.http_response_received,
            "provider_failure_kind": self.failure_kind,
        }
        if self.http_status_class is not None:
            details["http_status_class"] = self.http_status_class
        if self.grouped_categories:
            details["transport_failure_categories"] = list(
                self.grouped_categories
            )
        return details


@dataclass(frozen=True)
class McpDashboardHandshake:
    protocol_version: str
    server_name: str
    server_version: str
    tools: tuple[dict[str, Any], ...]
    connection_latency_ms: float


@dataclass(frozen=True)
class McpDashboardRead:
    handshake: McpDashboardHandshake
    call_result: dict[str, Any]
    tool_call_latency_ms: float


CapabilityValidator = Callable[[McpDashboardHandshake], None]


class McpDashboardTransport:
    """Open one bounded MCP session and call only the dashboard read tool.

    The endpoint is never included in representations, logs, or exceptions.
    Each operation creates and closes a session, so a subsequent call naturally
    reconnects after an upstream restart.
    """

    def __init__(self, url: str, *, timeout_seconds: float, client_version: str):
        self._url = url
        # Preserve the reviewed Engineering minimum timeout even though the
        # pinned SDK handles fractional timedelta values correctly.
        self._timeout = timedelta(seconds=max(1.0, float(timeout_seconds)))
        self._client_info = types.Implementation(
            name="hass-mcp-engineering-dashboard",
            version=client_version,
        )
        self._silence_url_bearing_library_logs()

    def __repr__(self) -> str:
        return (
            "McpDashboardTransport("
            f"configured={bool(self._url)}, timeout_seconds={self._timeout.total_seconds()}"
            ")"
        )

    @staticmethod
    def _silence_url_bearing_library_logs() -> None:
        # Keep SDK endpoint-bearing transport diagnostics disabled. Engineering
        # exposes its own bounded category metrics instead.
        for name in ("mcp.client.streamable_http", "httpx", "httpcore"):
            logger = logging.getLogger(name)
            logger.disabled = True
            logger.propagate = False

    async def discover(self) -> McpDashboardHandshake:
        result = await self._run(
            tool_name=None, arguments=None, capability_validator=None
        )
        if not isinstance(result, McpDashboardHandshake):
            raise DashboardTransportError("internal_error")
        return result

    async def execute_dashboard_read(
        self,
        arguments: dict[str, Any],
        capability_validator: CapabilityValidator,
    ) -> McpDashboardRead:
        validate_dashboard_read_arguments(arguments)
        result = await self._run(
            tool_name=REQUIRED_DASHBOARD_TOOL,
            arguments=dict(arguments),
            capability_validator=capability_validator,
        )
        if not isinstance(result, McpDashboardRead):
            raise DashboardTransportError("internal_error")
        return result

    async def execute_dashboard_write(
        self,
        arguments: dict[str, Any],
        capability_validator: CapabilityValidator,
    ) -> McpDashboardRead:
        """Call only the exact governed dashboard setter."""

        validate_dashboard_write_arguments(arguments)
        result = await self._run(
            tool_name=REQUIRED_DASHBOARD_WRITE_TOOL,
            arguments=dict(arguments),
            capability_validator=capability_validator,
        )
        if not isinstance(result, McpDashboardRead):
            raise DashboardTransportError("internal_error")
        return result

    async def execute_best_practices_read(
        self,
        capability_validator: CapabilityValidator,
    ) -> McpDashboardRead:
        """Fetch only the reviewed dashboard guide used by strict BPS."""

        arguments = {
            "skill": "home-assistant-best-practices",
            "file": "references/dashboard-guide.md",
        }
        result = await self._run(
            tool_name=REQUIRED_BEST_PRACTICES_TOOL,
            arguments=arguments,
            capability_validator=capability_validator,
        )
        if not isinstance(result, McpDashboardRead):
            raise DashboardTransportError("internal_error")
        return result

    async def _run(
        self,
        *,
        tool_name: str | None,
        arguments: dict[str, Any] | None,
        capability_validator: CapabilityValidator | None,
    ) -> McpDashboardHandshake | McpDashboardRead:
        started = time.perf_counter()
        try:
            async with streamablehttp_client(
                self._url,
                timeout=self._timeout,
                sse_read_timeout=self._timeout,
                terminate_on_close=True,
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=self._timeout,
                    client_info=self._client_info,
                ) as session:
                    initialize = await session.initialize()
                    tools = await self._list_all_tools(session)
                    handshake = McpDashboardHandshake(
                        protocol_version=str(initialize.protocolVersion),
                        server_name=str(initialize.serverInfo.name),
                        server_version=str(initialize.serverInfo.version),
                        tools=tuple(tools),
                        connection_latency_ms=round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                    )
                    if arguments is None:
                        return handshake
                    if tool_name is None:
                        raise DashboardTransportError("internal_error")
                    capability_validator(handshake)
                    call_started = time.perf_counter()
                    call_result = await session.call_tool(
                        tool_name,
                        arguments,
                        read_timeout_seconds=self._timeout,
                    )
                    encoded = call_result.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    )
                    if len(json.dumps(encoded, default=str)) > MAX_UPSTREAM_CONTENT_CHARS:
                        raise DashboardTransportError("response_too_large")
                    return McpDashboardRead(
                        handshake=handshake,
                        call_result=encoded,
                        tool_call_latency_ms=round(
                            (time.perf_counter() - call_started) * 1000, 3
                        ),
                    )
        except DashboardTransportError:
            raise
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise _classified_transport_error(exc) from None

    async def _list_all_tools(self, session: ClientSession) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _page in range(MAX_TOOL_CATALOG_PAGES):
            result = await session.list_tools(cursor)
            tools.extend(
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in result.tools
            )
            if len(tools) > MAX_TOOL_CATALOG_SIZE:
                raise DashboardTransportError("invalid_response")
            cursor = result.nextCursor
            if not cursor:
                return tools
            if cursor in seen_cursors:
                raise DashboardTransportError("protocol_error")
            seen_cursors.add(cursor)
        raise DashboardTransportError("invalid_response")


def validate_dashboard_read_arguments(arguments: dict[str, Any]) -> None:
    """Accept only the two reviewed non-screenshot dashboard call forms."""

    if not isinstance(arguments, dict):
        raise DashboardTransportError("prohibited_argument")
    if set(arguments) - ALLOWED_DASHBOARD_ARGUMENTS:
        raise DashboardTransportError("prohibited_argument")
    if arguments == {"list_only": True, "include_screenshot": False}:
        return
    if set(arguments) != {
        "url_path",
        "list_only",
        "force_reload",
        "include_screenshot",
    }:
        raise DashboardTransportError("prohibited_argument")
    if (
        not isinstance(arguments.get("url_path"), str)
        or not arguments["url_path"]
        or arguments.get("list_only") is not False
        or not isinstance(arguments.get("force_reload"), bool)
        or arguments.get("include_screenshot") is not False
    ):
        raise DashboardTransportError("prohibited_argument")


def validate_dashboard_write_arguments(arguments: dict[str, Any]) -> None:
    """Accept only one exact full-result replacement of an existing target."""

    required = {
        "url_path",
        "config",
        "config_hash",
        "MandatoryBPS",
        "return_screenshot",
    }
    allowed = required | {"BestPracticeKey"}
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise DashboardTransportError("prohibited_argument")
    if not required.issubset(arguments):
        raise DashboardTransportError("prohibited_argument")
    if (
        not isinstance(arguments.get("url_path"), str)
        or not re.fullmatch(r"[a-z0-9_-]{1,256}", arguments["url_path"])
        or not isinstance(arguments.get("config"), dict)
        or not isinstance(arguments.get("config_hash"), str)
        or not re.fullmatch(r"[0-9a-f]{16}", arguments["config_hash"])
        or arguments.get("MandatoryBPS") is not False
        or arguments.get("return_screenshot") is not False
        or (
            "BestPracticeKey" in arguments
            and (
                not isinstance(arguments["BestPracticeKey"], str)
                or not re.fullmatch(
                    r"I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-[0-9a-f]{8}",
                    arguments["BestPracticeKey"],
                )
            )
        )
    ):
        raise DashboardTransportError("prohibited_argument")


def _iter_exceptions(exc: BaseException):
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            yield from _iter_exceptions(child)
    else:
        yield exc


def _recognized_typed_error_details(
    leaves: tuple[BaseException, ...],
) -> tuple[str, bool | None, tuple[str, ...]] | None:
    """Select one bounded typed category using security-first precedence."""

    recognized = [
        (index, leaf)
        for index, leaf in enumerate(leaves)
        if isinstance(leaf, DashboardTransportError)
        and leaf.category in _RECOGNIZED_TYPED_ERROR_CATEGORIES
    ]
    if not recognized:
        return None
    selected_index, selected = min(
        recognized,
        key=lambda item: (
            _TYPED_ERROR_PRECEDENCE.get(item[1].category, 999),
            item[0],
        ),
    )
    del selected_index
    same_category = [
        leaf for _index, leaf in recognized if leaf.category == selected.category
    ]
    retryability = [
        leaf.retryable
        for leaf in same_category
        if leaf.retryable is not None
    ]
    retryable = (
        False
        if False in retryability
        else True
        if retryability and all(retryability)
        else None
    )
    categories = tuple(
        dict.fromkeys(leaf.category for _index, leaf in recognized)
    )[:MAX_GROUPED_ERROR_CATEGORIES]
    return selected.category, retryable, categories


def _classified_transport_error(
    exc: BaseException,
) -> DashboardTransportError:
    leaves = tuple(_iter_exceptions(exc))
    typed = _recognized_typed_error_details(leaves)
    if typed is not None:
        category, retryable, categories = typed
        selected = next(
            (
                leaf
                for leaf in leaves
                if isinstance(leaf, DashboardTransportError)
                and leaf.category == category
            ),
            None,
        )
        return DashboardTransportError(
            category,
            retryable=retryable,
            grouped_categories=categories,
            provider_response_received=(
                selected.provider_response_received
                if selected is not None
                else False
            ),
            http_response_received=(
                selected.http_response_received
                if selected is not None
                else False
            ),
            failure_kind=(
                selected.failure_kind
                if selected is not None
                else "protocol_or_transport_failure"
            ),
            http_status_class=(
                selected.http_status_class
                if selected is not None
                else None
            ),
        )
    category = _classify_transport_exception(exc)
    http_status_class = _http_status_class(leaves)
    return DashboardTransportError(
        category,
        http_response_received=http_status_class is not None,
        failure_kind=(
            "provider_5xx_ambiguous"
            if http_status_class == "5xx"
            else "transport_silence_or_response_loss"
            if category in {"connection_failed", "timeout"}
            else "protocol_or_transport_failure"
        ),
        http_status_class=http_status_class,
    )


def _http_status_class(leaves: tuple[BaseException, ...]) -> str | None:
    """Return a bounded HTTP status family without retaining response data."""

    for leaf in leaves:
        response = getattr(leaf, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            if 500 <= status <= 599:
                return "5xx"
            if 400 <= status <= 499:
                return "4xx"
    return None


def _classify_transport_exception(exc: BaseException) -> str:
    leaves = tuple(_iter_exceptions(exc))
    typed = _recognized_typed_error_details(leaves)
    if typed is not None:
        return typed[0]
    for leaf in leaves:
        response = getattr(leaf, "response", None)
        status = getattr(response, "status_code", None)
        if status in {401, 403}:
            return "authentication_failed"
        if status == 404:
            return "endpoint_rejected"
    for leaf in leaves:
        error = getattr(leaf, "error", None)
        if (
            getattr(error, "code", None) == 32600
            and getattr(error, "message", None) == "Session terminated"
        ):
            # Some streamable-HTTP implementations convert a fresh-request 404
            # into this synthetic MCP error and discard the HTTP status.
            return "endpoint_rejected"
    for leaf in leaves:
        name = type(leaf).__name__.lower()
        if isinstance(leaf, (asyncio.TimeoutError, TimeoutError)):
            continue
        if isinstance(leaf, (ConnectionError, socket.gaierror, OSError)) or any(
            term in name
            for term in (
                "connecterror",
                "connectionrefused",
                "networkerror",
                "gaierror",
                "nameorservice",
                "noroutetohost",
            )
        ):
            return "connection_failed"
    for leaf in leaves:
        if isinstance(leaf, (asyncio.TimeoutError, TimeoutError)):
            return "timeout"
        if "timeout" in type(leaf).__name__.lower():
            return "timeout"
        error = getattr(leaf, "error", None)
        if getattr(error, "code", None) == 408:
            return "timeout"
    for leaf in leaves:
        name = type(leaf).__name__.lower()
        if isinstance(leaf, (json.JSONDecodeError, UnicodeDecodeError)):
            return "invalid_response"
        if any(term in name for term in ("validationerror", "decodeerror")):
            return "invalid_response"
        if any(
            term in name
            for term in ("mcperror", "protocolerror", "remoteprotocolerror")
        ):
            return "protocol_error"
    for leaf in leaves:
        response = getattr(leaf, "response", None)
        if isinstance(getattr(response, "status_code", None), int):
            return "upstream_error"
        if "httperror" in type(leaf).__name__.lower():
            return "upstream_error"
    return "internal_error"
