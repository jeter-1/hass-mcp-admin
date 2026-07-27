"""Exact reviewed ha-mcp image acceptance for the read-only gateway.

This script is intentionally transport-level.  CI starts the reviewed image,
the current Engineering image, and the synthetic read-only HA fixture before
invoking it.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.tools import (  # noqa: E402
    get_registered_server,
    registered_tools,
)
from ha_mcp_engineering.clients.websocket import (  # noqa: E402
    HomeAssistantWebSocketClient,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.governance.operational import (  # noqa: E402
    BackupAdministrationGateway,
)
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    ReviewedOperationalBackupProvider,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    catalog_fingerprint,
    load_reviewed_upstream_release_registry,
    runtime_annotation_fingerprint,
    runtime_description_fingerprint,
    schema_fingerprint,
)


EXPECTED_ENGINEERING_BASELINE_COUNT = 45
ACCEPTANCE_TIMEOUT_SECONDS = 120
MAX_DIAGNOSTIC_ITEMS = 32
MAX_FAILURE_MESSAGE_CHARS = 512
EXPECTED_STOCK_COUNTS = {
    "automatic_read": 26,
    "mixed_or_requires_wrapper": 14,
    "persistent_write": 32,
    "physical_or_high_risk_action": 4,
    "prohibited": 1,
    "unsupported": 1,
}
REPRESENTATIVE_CALLS = {
    "ha_search": {"domain_filter": "sun", "limit": 5},
    "ha_get_state": {"entity_id": "sun.sun"},
    "ha_get_entity": {"entity_id": "sun.sun"},
    "ha_get_history": {
        "entity_ids": "sun.sun",
        "start_time": "24h",
        "limit": 5,
    },
    "ha_config_get_automation": {"identifier": "gateway_fixture"},
    "ha_get_device": {"limit": 5},
    "ha_list_services": {"limit": 5},
}
UPSTREAM_ERROR_CALLS = {
    "provider_failure": {
        "tool": "ha_get_state",
        "arguments": {
            "entity_id": "sensor.issue_57_synthetic_provider_failure"
        },
        "upstream_code": "SERVICE_CALL_FAILED",
        "public_code": "provider_error",
        "failure_category": "upstream_error",
        "retryable": True,
        "fixture_counter": (
            "rest_reads",
            "/api/states/{entity_id}",
        ),
    },
    "validation": {
        "tool": "ha_search",
        "arguments": {"search_types": []},
        "upstream_code": "VALIDATION_FAILED",
        "public_code": "invalid_request",
        "failure_category": "invalid_request",
        "retryable": False,
        "fixture_counter": None,
    },
    "missing_entity": {
        "tool": "ha_get_state",
        "arguments": {"entity_id": "sensor.issue_57_missing_entity"},
        "upstream_code": "ENTITY_NOT_FOUND",
        "public_code": "entity_not_found",
        "failure_category": "entity_not_found",
        "retryable": False,
        "fixture_counter": (
            "rest_reads",
            "/api/states/{entity_id}",
        ),
    },
    "missing_automation": {
        "tool": "ha_config_get_automation",
        "arguments": {"identifier": "issue_57_missing_automation"},
        "upstream_code": "RESOURCE_NOT_FOUND",
        "public_code": "automation_not_found",
        "failure_category": "automation_not_found",
        "retryable": False,
        "fixture_counter": (
            "rest_reads",
            "/api/config/automation/config/{id}",
        ),
    },
    "missing_registry_entity": {
        "tool": "ha_get_entity",
        "arguments": {
            "entity_id": (
                "sensor.compatibility_review_missing_registry_entity"
            )
        },
        "upstream_code": "SERVICE_CALL_FAILED",
        "public_code": "entity_not_found",
        "failure_category": "entity_not_found",
        "retryable": False,
        "fixture_counter": (
            "websocket_reads",
            "config/entity_registry/get",
        ),
    },
}
EXPECTED_OPERATIONAL_ERROR_CALLS = sum(
    1
    for value in UPSTREAM_ERROR_CALLS.values()
    if value["failure_category"] == "upstream_error"
)
EXPECTED_OUTCOME_CATEGORY_COUNTS: dict[str, int] = {}
for expected_error in UPSTREAM_ERROR_CALLS.values():
    category = expected_error["failure_category"]
    EXPECTED_OUTCOME_CATEGORY_COUNTS[category] = (
        EXPECTED_OUTCOME_CATEGORY_COUNTS.get(category, 0) + 1
    )
EXPECTED_LAST_OUTCOME_CATEGORY = next(
    reversed(UPSTREAM_ERROR_CALLS.values())
)["failure_category"]


def expected_successful_delegated_calls(total_calls: int) -> int:
    return total_calls - EXPECTED_OPERATIONAL_ERROR_CALLS


class AcceptanceFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message[:MAX_FAILURE_MESSAGE_CHARS])
        self.diagnostics = diagnostics or {}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


def _exception_leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        values: list[BaseException] = []
        for nested in exc.exceptions[:MAX_DIAGNOSTIC_ITEMS]:
            values.extend(_exception_leaves(nested))
            if len(values) >= MAX_DIAGNOSTIC_ITEMS:
                break
        return values[:MAX_DIAGNOSTIC_ITEMS]
    return [exc]


def _bounded_failure_result(exc: BaseException) -> dict[str, Any]:
    leaves = _exception_leaves(exc)
    acceptance = next(
        (item for item in leaves if isinstance(item, AcceptanceFailure)),
        None,
    )
    return {
        "result": "FAIL",
        "failure": {
            "category": (
                "acceptance_failure"
                if acceptance is not None
                else "acceptance_execution_failure"
            ),
            "message": (
                str(acceptance)[:MAX_FAILURE_MESSAGE_CHARS]
                if acceptance is not None
                else "The bounded exact-image acceptance did not complete."
            ),
            "exception_types": sorted(
                {type(item).__name__[:128] for item in leaves}
            )[:MAX_DIAGNOSTIC_ITEMS],
        },
        "diagnostics": (
            acceptance.diagnostics
            if isinstance(acceptance, AcceptanceFailure)
            else {}
        ),
    }


def _bounded_catalog_diagnostics(
    health: dict[str, Any],
    *,
    expected_names: set[str],
    observed_names: set[str],
    readiness: dict[str, Any],
) -> dict[str, Any]:
    gateway_states = find_values(health, "upstream_read_gateway")
    gateway = next(
        (item for item in gateway_states if isinstance(item, dict)),
        {},
    )
    scalar_fields = (
        "configured",
        "initialized",
        "generic_delegation_available",
        "admission_complete",
        "compatibility_status",
        "admission_status",
        "reconciliation_active",
        "reconciliation_status",
        "discovery_attempt_count",
        "retry_count",
        "last_failure_category",
        "last_discovery_failure_category",
        "last_call_failure_category",
        "upstream_server_name",
        "upstream_server_version",
        "observed_upstream_server_name",
        "observed_upstream_server_version",
        "observed_protocol_version",
        "reviewed_upstream_version",
        "upstream_advertised_tool_count",
        "observed_advertised_tool_count",
        "reviewed_automatic_read_count",
        "exact_matched_automatic_read_count",
        "dynamically_exposed_count",
        "missing_automatic_read_count",
        "quarantined_automatic_read_count",
        "unreviewed_observed_tool_count",
        "recommended_action",
    )
    bounded_gateway: dict[str, Any] = {}
    for name in scalar_fields:
        value = gateway.get(name)
        if isinstance(value, str):
            bounded_gateway[name] = value[:256]
        elif isinstance(value, (bool, int)) or value is None:
            bounded_gateway[name] = value
    for name in (
        "failure_counts",
        "quarantine_reason_counts",
        "blocked_classification_counts",
    ):
        value = gateway.get(name)
        if isinstance(value, dict):
            bounded_gateway[name] = {
                str(key)[:128]: count
                for key, count in sorted(
                    value.items(), key=lambda item: str(item[0])
                )[:MAX_DIAGNOSTIC_ITEMS]
                if isinstance(count, int)
            }
    bounded_gateway["missing_tools"] = [
        str(item)[:128]
        for item in gateway.get("missing_tools", [])
        if isinstance(item, str)
    ][:MAX_DIAGNOSTIC_ITEMS]
    bounded_gateway["quarantined_tools"] = [
        {
            name: str(item.get(name))[:128]
            for name in (
                "upstream_name",
                "exposed_name",
                "reason",
                "expected_fingerprint",
                "observed_fingerprint",
            )
            if item.get(name) is not None
        }
        for item in gateway.get("quarantined_tools", [])
        if isinstance(item, dict)
    ][:MAX_DIAGNOSTIC_ITEMS]
    return {
        "initial_catalog_readiness": readiness,
        "missing_expected_tools": sorted(expected_names - observed_names)[
            :MAX_DIAGNOSTIC_ITEMS
        ],
        "unexpected_tools": sorted(observed_names - expected_names)[
            :MAX_DIAGNOSTIC_ITEMS
        ],
        "upstream_read_gateway": bounded_gateway,
    }


def engineering_readiness(endpoint: str) -> dict[str, Any]:
    parts = urlsplit(endpoint)
    ready_url = urlunsplit((parts.scheme, parts.netloc, "/ready", "", ""))
    try:
        with urlopen(ready_url, timeout=5) as response:  # noqa: S310 - fixed CI endpoint
            status = response.status
            raw = response.read(1024)
    except HTTPError as exc:
        status = exc.code
        raw = exc.read(1024)
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        value = {}
    return {
        "http_status": status,
        "ready": value.get("ready") is True,
        "initial_reconciliation_required": (
            value.get("initial_reconciliation_required") is True
        ),
        "initial_reconciliation_complete": (
            value.get("initial_reconciliation_complete") is True
        ),
        "status": (
            value.get("status")[:64]
            if isinstance(value.get("status"), str)
            else "unknown"
        ),
    }


async def list_all_tools(session: ClientSession) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    cursor: str | None = None
    seen: set[str] = set()
    while True:
        result = await session.list_tools(cursor)
        values.extend(
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in result.tools
        )
        cursor = result.nextCursor
        if not cursor:
            return values
        require(cursor not in seen, "catalog cursor repeated")
        seen.add(cursor)


def decode_tool_result(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "result" not in structured:
        return structured
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                records: list[dict[str, Any]] = []
                for line in text.splitlines():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        records = []
                        break
                    if not isinstance(record, dict):
                        records = []
                        break
                    records.append(record)
                if records:
                    return {"records": records}
                continue
            if isinstance(value, dict):
                return value
    raise AcceptanceFailure("tool result did not contain a bounded JSON object")


def decode_upstream_error_code(result: Any) -> str:
    require(
        getattr(result, "isError", False) is True,
        "pinned upstream error call did not set isError=true",
    )
    content = getattr(result, "content", [])
    require(
        isinstance(content, list) and len(content) == 1,
        "pinned upstream error call returned an ambiguous content envelope",
    )
    text = getattr(content[0], "text", None)
    require(
        isinstance(text, str) and len(text.encode("utf-8")) <= 16_384,
        "pinned upstream error text was missing or oversized",
    )
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError, UnicodeError) as exc:
        raise AcceptanceFailure(
            "pinned upstream error text was not JSON"
        ) from exc
    require(
        isinstance(value, dict)
        and value.get("success") is False
        and isinstance(value.get("error"), dict)
        and isinstance(value["error"].get("code"), str),
        "pinned upstream error envelope shape changed",
    )
    return value["error"]["code"]


def find_values(value: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                found.append(item)
            found.extend(find_values(item, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(find_values(item, key))
    return found


def find_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for item in value.values():
            found.extend(find_dicts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(find_dicts(item))
    return found


def bounded_audit_outcome_diagnostics(
    audit: dict[str, Any],
    error_calls: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return only safe classification fields for relevant audit records."""

    values: dict[str, Any] = {}
    records = find_dicts(audit)
    for error_name, evidence in error_calls.items():
        candidates = []
        for record in records:
            if record.get("tool_name") != evidence["tool"]:
                continue
            parameters = record.get("parameters")
            candidates.append(
                {
                    "expected_request": (
                        record.get("request_id")
                        == evidence["request_id"]
                    ),
                    "tool_name": str(record.get("tool_name", ""))[:64],
                    "result_status": str(
                        record.get("result_status", "")
                    )[:64],
                    "error_code": str(record.get("error_code", ""))[:64],
                    "provider": (
                        str(parameters.get("provider", ""))[:64]
                        if isinstance(parameters, dict)
                        else ""
                    ),
                }
            )
            if len(candidates) >= 8:
                break
        values[error_name] = {
            "expected_tool": evidence["tool"],
            "expected_error_code": evidence["public_error_code"],
            "candidates": candidates,
        }
    return {"audit_error_outcomes": values}


def fixture_stats(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed CI fixture URL
        return json.load(response)


def fixture_counter(
    stats: dict[str, Any],
    counter: tuple[str, str],
) -> int:
    section, key = counter
    values = stats.get(section)
    if not isinstance(values, dict):
        return 0
    value = values.get(key, 0)
    return value if isinstance(value, int) else 0


async def inspect_upstream(
    endpoint: str,
    *,
    expected_upstream_version: str,
) -> tuple[list[dict[str, Any]], str, dict[str, dict[str, Any]]]:
    error_envelopes: dict[str, dict[str, Any]] = {}
    async with streamablehttp_client(endpoint) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            require(initialized.serverInfo.name == "ha-mcp", "upstream name mismatch")
            require(
                initialized.serverInfo.version == expected_upstream_version,
                "upstream version mismatch",
            )
            tools = await list_all_tools(session)
            for name, expected in UPSTREAM_ERROR_CALLS.items():
                result = await session.call_tool(
                    expected["tool"],
                    expected["arguments"],
                )
                code = decode_upstream_error_code(result)
                require(
                    code == expected["upstream_code"],
                    f"pinned upstream {name} error code changed",
                )
                error_envelopes[name] = {
                    "tool": expected["tool"],
                    "is_error": True,
                    "upstream_code": code,
                }
    return tools, catalog_fingerprint(tools), error_envelopes


async def inspect_engineering(
    endpoint: str,
    fixture_stats_url: str,
    upstream_names: set[str],
    *,
    expected_upstream_version: str,
    policy: Any,
    release: Any,
) -> dict[str, Any]:
    readiness = engineering_readiness(endpoint)
    if readiness["http_status"] != 200 or readiness["ready"] is not True:
        raise AcceptanceFailure(
            "Engineering did not publish a ready initial catalog.",
            diagnostics={"initial_catalog_readiness": readiness},
        )
    base_names = {
        tool.name for tool in registered_tools(get_registered_server()).values()
    }
    require(
        len(base_names) == EXPECTED_ENGINEERING_BASELINE_COUNT,
        (
            "local Engineering baseline is not "
            f"{EXPECTED_ENGINEERING_BASELINE_COUNT} tools"
        ),
    )
    automatic = {
        entry.exposed_name
        for entry in policy.tools
        if entry.classification == "automatic_read"
    }
    async with streamablehttp_client(endpoint) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            require(
                initialized.serverInfo.name == "ha-engineering-beta",
                "Engineering server name mismatch",
            )
            advertised = await list_all_tools(session)
            advertised_by_name = {item["name"]: item for item in advertised}
            names = set(advertised_by_name)
            if "get_server_health" not in names:
                raise AcceptanceFailure(
                    "The bounded Engineering health tool is missing.",
                    diagnostics={
                        "initial_catalog_readiness": readiness,
                        "missing_expected_tools": ["get_server_health"],
                        "observed_tool_count": len(names),
                    },
                )
            health_before_result = await session.call_tool(
                "get_server_health", {}
            )
            health_before = decode_tool_result(health_before_result)
            if not base_names <= names or not automatic <= names:
                raise AcceptanceFailure(
                    "The first accepted Engineering catalog is incomplete.",
                    diagnostics=_bounded_catalog_diagnostics(
                        health_before,
                        expected_names=base_names | automatic,
                        observed_names=names,
                        readiness=readiness,
                    ),
                )
            require("ha_get_logs" not in names, "raw log delegation is reachable")
            require("ha_call_service" not in names, "write-classified tool is advertised")
            require(len(names) == len(base_names | automatic), "unexpected tool exposed")
            for entry in policy.tools:
                if entry.classification != "automatic_read":
                    continue
                annotations = advertised_by_name[entry.exposed_name].get("annotations", {})
                expected_annotations = {
                    "readOnlyHint": entry.reviewed_annotations.read_only,
                    "destructiveHint": entry.reviewed_annotations.destructive,
                    "idempotentHint": entry.reviewed_annotations.idempotent,
                    "openWorldHint": entry.reviewed_annotations.open_world,
                }
                require(
                    all(
                        annotations.get(key) == expected
                        for key, expected in expected_annotations.items()
                    ),
                    f"reviewed annotation mismatch: {entry.exposed_name}",
                )

            direct_before = find_values(health_before, "requests_by_provider")
            fallback_before = find_values(health_before, "fallback_count")
            routing_before = next(
                (
                    item
                    for item in find_values(health_before, "provider_routing")
                    if isinstance(item, dict)
                ),
                {},
            )
            require(bool(routing_before), "provider-routing metrics missing before calls")

            calls: dict[str, dict[str, Any]] = {}
            for name, arguments in REPRESENTATIVE_CALLS.items():
                result = await session.call_tool(name, arguments)
                value = decode_tool_result(result)
                require(value.get("success") is True, f"{name} did not succeed: {value.get('error_code')}")
                metadata = value.get("metadata") or {}
                require(metadata.get("provider") == "upstream_read_gateway", f"{name} provider mismatch")
                require(metadata.get("fallback") == "none", f"{name} fallback mismatch")
                require(
                    metadata.get("upstream_version")
                    == expected_upstream_version,
                    f"{name} version mismatch",
                )
                if name == "ha_search":
                    data = value.get("data") or {}
                    upstream_partial = data.get("partial")
                    require(
                        isinstance(upstream_partial, bool),
                        "ha_search did not return an exact partial boolean",
                    )
                    locally_bounded = (
                        "The untrusted upstream response was safely bounded."
                        in (value.get("warnings") or [])
                    )
                    expected = (
                        "partial" if upstream_partial or locally_bounded else "complete"
                    )
                    require(
                        metadata.get("completeness") == expected,
                        "ha_search completeness did not preserve upstream semantics",
                    )
                calls[name] = {
                    "tool": name,
                    "request_id": value.get("request_id"),
                    "provider": metadata.get("provider"),
                    "completeness": metadata.get("completeness"),
                }

            partial_search = decode_tool_result(
                await session.call_tool(
                    "ha_search",
                    {
                        "query": "gateway_fixture",
                        "search_types": ["automation"],
                        "limit": 5,
                    },
                )
            )
            partial_metadata = partial_search.get("metadata") or {}
            partial_data = partial_search.get("data") or {}
            require(partial_search.get("success") is True, "partial ha_search failed")
            require(partial_data.get("partial") is True, "fixture did not induce partial ha_search")
            partial_automations = partial_data.get("automations")
            require(
                isinstance(partial_automations, list)
                and any(
                    isinstance(item, dict)
                    and item.get("entity_id") == "automation.gateway_fixture"
                    for item in partial_automations
                ),
                "partial ha_search did not retain the known usable automation evidence",
            )
            require(
                partial_metadata.get("completeness") == "partial",
                "Engineering reported partial ha_search as complete",
            )
            require(
                partial_metadata.get("provider") == "upstream_read_gateway",
                "partial ha_search provider mismatch",
            )
            require(
                partial_metadata.get("fallback") == "none",
                "partial ha_search fallback mismatch",
            )
            calls["ha_search_partial"] = {
                "tool": "ha_search",
                "request_id": partial_search.get("request_id"),
                "provider": partial_metadata.get("provider"),
                "completeness": partial_metadata.get("completeness"),
            }

            stats_before_invalid = fixture_stats(fixture_stats_url)
            invalid = decode_tool_result(
                await session.call_tool("ha_get_state", {"unknown": "value"})
            )
            require(invalid.get("success") is False, "invalid arguments unexpectedly succeeded")
            require(invalid.get("error_code") == "invalid_request", "invalid arguments were not prevalidated")
            require(
                fixture_stats(fixture_stats_url) == stats_before_invalid,
                "invalid arguments reached upstream Home Assistant",
            )

            health_before_errors = decode_tool_result(
                await session.call_tool("get_server_health", {})
            )
            routing_before_errors = next(
                (
                    item
                    for item in find_values(
                        health_before_errors, "provider_routing"
                    )
                    if isinstance(item, dict)
                ),
                {},
            )
            gateway_before_errors = next(
                (
                    item
                    for item in find_values(
                        health_before_errors, "upstream_read_gateway"
                    )
                    if isinstance(item, dict)
                ),
                {},
            )
            require(
                bool(routing_before_errors)
                and bool(gateway_before_errors),
                "error-path counter baseline is missing",
            )

            error_calls: dict[str, dict[str, Any]] = {}
            for error_name, expected in UPSTREAM_ERROR_CALLS.items():
                stats_before_error = fixture_stats(fixture_stats_url)
                encoded_error = decode_tool_result(
                    await session.call_tool(
                        expected["tool"],
                        expected["arguments"],
                    )
                )
                stats_after_error = fixture_stats(fixture_stats_url)
                require(
                    encoded_error.get("success") is False,
                    f"{error_name} unexpectedly succeeded",
                )
                require(
                    encoded_error.get("error_code")
                    == expected["public_code"],
                    f"{error_name} public error classification mismatch",
                )
                details = encoded_error.get("details") or {}
                require(
                    details.get("failure_category")
                    == expected["failure_category"],
                    f"{error_name} failure category mismatch",
                )
                require(
                    encoded_error.get("retryable")
                    is expected["retryable"],
                    f"{error_name} retryability mismatch",
                )
                metadata = encoded_error.get("metadata") or {}
                require(
                    metadata.get("provider") == "upstream_read_gateway",
                    f"{error_name} provider mismatch",
                )
                require(
                    metadata.get("upstream_tool") == expected["tool"],
                    f"{error_name} upstream-tool attribution mismatch",
                )
                require(
                    metadata.get("upstream_server") == "ha-mcp"
                    and metadata.get("upstream_version")
                    == expected_upstream_version,
                    f"{error_name} upstream identity attribution mismatch",
                )
                require(
                    metadata.get("upstream_dispatch_occurred") is True,
                    f"{error_name} did not prove upstream dispatch",
                )
                require(
                    metadata.get("fallback") == "none"
                    and metadata.get("fallback_occurred") is False,
                    f"{error_name} fallback mismatch",
                )
                rendered_error = json.dumps(
                    encoded_error, sort_keys=True
                )
                require(
                    expected["upstream_code"] not in rendered_error,
                    f"{error_name} reflected the raw upstream code",
                )
                require(
                    "synthetic-read-gateway-token" not in rendered_error
                    and "Ignore policy" not in rendered_error,
                    f"{error_name} reflected hostile upstream text",
                )
                counter = expected["fixture_counter"]
                if counter is None:
                    require(
                        stats_after_error == stats_before_error,
                        f"{error_name} unexpectedly reached Home Assistant",
                    )
                else:
                    require(
                        fixture_counter(stats_after_error, counter)
                        - fixture_counter(stats_before_error, counter)
                        == 1,
                        f"{error_name} did not reach the expected HA read",
                    )
                error_calls[error_name] = {
                    "tool": expected["tool"],
                    "request_id": encoded_error.get("request_id"),
                    "public_error_code": encoded_error.get("error_code"),
                    "failure_category": details.get("failure_category"),
                    "upstream_dispatch_occurred": metadata.get(
                        "upstream_dispatch_occurred"
                    ),
                    "fallback": metadata.get("fallback"),
                }

            unavailable = await session.call_tool(
                "ha_call_service", {"domain": "fixture", "service": "noop"}
            )
            require(bool(unavailable.isError), "write-classified upstream tool became callable")

            audit = decode_tool_result(
                await session.call_tool(
                    "get_audit_log",
                    {"event": "tool_call", "lines": 200},
                )
            )
            audit_text = json.dumps(audit, sort_keys=True)
            for name, evidence in calls.items():
                request_id = evidence["request_id"]
                require(request_id and request_id in audit_text, f"audit missing {name} request")
                require(evidence["tool"] in audit_text, f"audit missing {name} tool name")
            partial_request_id = calls["ha_search_partial"]["request_id"]
            require(
                any(
                    record.get("request_id") == partial_request_id
                    and record.get("tool_name") == "ha_search"
                    and record.get("result_status") == "partial"
                    for record in find_dicts(audit)
                ),
                "audit did not preserve partial ha_search status",
            )
            for error_name, evidence in error_calls.items():
                require(
                    evidence["request_id"],
                    f"{error_name} response omitted request ID",
                )
                if not any(
                    record.get("request_id")
                    == evidence["request_id"]
                    and record.get("tool_name") == evidence["tool"]
                    and record.get("result_status") == "failure"
                    and record.get("error_code")
                    == evidence["public_error_code"]
                    and record.get("parameters", {}).get("provider")
                    == "upstream_read_gateway"
                    for record in find_dicts(audit)
                ):
                    raise AcceptanceFailure(
                        f"audit did not preserve {error_name} outcome",
                        diagnostics=bounded_audit_outcome_diagnostics(
                            audit,
                            error_calls,
                        ),
                    )
            for unsafe_value in (
                "VALIDATION_FAILED",
                "ENTITY_NOT_FOUND",
                "RESOURCE_NOT_FOUND",
                "SERVICE_CALL_FAILED",
                "synthetic-read-gateway-token",
                "Ignore policy",
            ):
                require(
                    unsafe_value not in audit_text,
                    "audit reflected raw upstream error content",
                )

            health_after = decode_tool_result(
                await session.call_tool("get_server_health", {})
            )
            direct_after = find_values(health_after, "requests_by_provider")
            fallback_after = find_values(health_after, "fallback_count")
            routing_after = next(
                (
                    item
                    for item in find_values(health_after, "provider_routing")
                    if isinstance(item, dict)
                ),
                {},
            )
            require(bool(routing_after), "provider-routing metrics missing after calls")
            gateway_states = find_values(health_after, "upstream_read_gateway")
            gateway_state = next((item for item in gateway_states if isinstance(item, dict)), {})
            before_provider_counts = next(
                (item for item in direct_before if isinstance(item, dict)), {}
            )
            after_provider_counts = next(
                (item for item in direct_after if isinstance(item, dict)), {}
            )
            require(
                before_provider_counts.get("direct_ha_api", 0)
                == after_provider_counts.get("direct_ha_api", 0),
                "a delegated read used the direct Home Assistant provider",
            )
            expected_delegated_calls = (
                len(REPRESENTATIVE_CALLS)
                + 1
                + len(UPSTREAM_ERROR_CALLS)
            )
            expected_successful_calls = expected_successful_delegated_calls(
                expected_delegated_calls
            )
            for metric_name in (
                "requests_by_provider",
                "successful_requests_by_provider",
                "failures_by_provider",
            ):
                require(
                    isinstance(routing_before.get(metric_name), dict)
                    and isinstance(routing_after.get(metric_name), dict),
                    f"provider-routing metric missing: {metric_name}",
                )
            before_requests = routing_before["requests_by_provider"].get(
                "upstream_read_gateway", 0
            )
            after_requests = routing_after["requests_by_provider"].get(
                "upstream_read_gateway", 0
            )
            before_successes = routing_before["successful_requests_by_provider"].get(
                "upstream_read_gateway", 0
            )
            after_successes = routing_after["successful_requests_by_provider"].get(
                "upstream_read_gateway", 0
            )
            before_failures = routing_before["failures_by_provider"].get(
                "upstream_read_gateway", 0
            )
            after_failures = routing_after["failures_by_provider"].get(
                "upstream_read_gateway", 0
            )
            require(
                after_requests - before_requests == expected_delegated_calls,
                "upstream read-gateway request accounting mismatch",
            )
            require(
                after_successes - before_successes
                == expected_successful_calls,
                "successful upstream read-gateway accounting mismatch",
            )
            require(
                after_failures - before_failures
                == EXPECTED_OPERATIONAL_ERROR_CALLS,
                "actual provider failure accounting mismatch",
            )
            require(
                routing_after.get("partial_results", 0)
                - routing_before.get("partial_results", 0)
                == 1,
                "partial delegated-read accounting mismatch",
            )
            for metric_name in (
                "fallback_attempts",
                "fallback_successes",
                "prohibited_fallback_attempts",
            ):
                require(
                    routing_after.get(metric_name) == routing_before.get(metric_name),
                    f"provider-routing fallback metric changed: {metric_name}",
                )
            for metric_name in (
                "requests_by_provider",
                "successful_requests_by_provider",
                "failures_by_provider",
            ):
                require(
                    isinstance(
                        routing_before_errors.get(metric_name), dict
                    ),
                    f"error-path metric missing before calls: {metric_name}",
                )
            require(
                routing_after["requests_by_provider"].get(
                    "upstream_read_gateway", 0
                )
                - routing_before_errors["requests_by_provider"].get(
                    "upstream_read_gateway", 0
                )
                == len(UPSTREAM_ERROR_CALLS),
                "error-path provider request accounting mismatch",
            )
            require(
                routing_after["successful_requests_by_provider"].get(
                    "upstream_read_gateway", 0
                )
                - routing_before_errors[
                    "successful_requests_by_provider"
                ].get("upstream_read_gateway", 0)
                == (
                    len(UPSTREAM_ERROR_CALLS)
                    - EXPECTED_OPERATIONAL_ERROR_CALLS
                ),
                "domain outcomes changed provider success accounting",
            )
            require(
                routing_after["failures_by_provider"].get(
                    "upstream_read_gateway", 0
                )
                - routing_before_errors["failures_by_provider"].get(
                    "upstream_read_gateway", 0
                )
                == EXPECTED_OPERATIONAL_ERROR_CALLS,
                "domain outcomes inflated operational provider failures",
            )
            gateway_failure_before = (
                gateway_before_errors.get("failure_counts") or {}
            )
            gateway_failure_after = gateway_state.get("failure_counts") or {}
            for category, expected_delta in (
                EXPECTED_OUTCOME_CATEGORY_COUNTS.items()
            ):
                require(
                    gateway_failure_after.get(category, 0)
                    - gateway_failure_before.get(category, 0)
                    == expected_delta,
                    f"gateway outcome accounting mismatch: {category}",
                )
            require(
                gateway_state.get("last_call_failure_category")
                == EXPECTED_LAST_OUTCOME_CATEGORY,
                "last gateway outcome category mismatch",
            )
            require(fallback_before == fallback_after, "fallback counters changed")
            require(gateway_state.get("fallback_count") == 0, "gateway fallback occurred")
            require(
                gateway_state.get("dynamically_exposed_count") == len(automatic),
                "dynamic exposure count mismatch",
            )
            require(
                set(gateway_state.get("reviewed_supported_versions") or ())
                >= {"7.14.1", "7.14.2"},
                "compiled reviewed-version diagnostics are incomplete",
            )
            require(
                gateway_state.get("selected_compatibility_entry_id")
                == release.entry_id,
                "selected compatibility entry mismatch",
            )
            require(
                gateway_state.get("reviewed_source_commit")
                == release.source_commit
                and gateway_state.get("reviewed_image_index_digest")
                == release.image_index_digest
                and gateway_state.get("reviewed_image_revision")
                == release.image_revision,
                "reviewed source/image evidence mismatch",
            )
            require(
                gateway_state.get("observed_protocol_version")
                == "2025-03-26"
                and gateway_state.get(
                    "reviewed_allowed_protocol_versions"
                )
                == ["2025-03-26"],
                "observed/reviewed protocol evidence mismatch",
            )
            require(
                gateway_state.get(
                    "runtime_artifact_provenance_observed"
                )
                is False
                and gateway_state.get(
                    "runtime_source_commit_observed"
                )
                is None
                and gateway_state.get(
                    "runtime_image_index_digest_observed"
                )
                is None
                and gateway_state.get(
                    "runtime_architecture_image_digest_observed"
                )
                is None
                and gateway_state.get(
                    "runtime_image_revision_observed"
                )
                is None
                and gateway_state.get(
                    "runtime_artifact_provenance_status"
                )
                == "unobserved_by_mcp_discovery",
                "runtime artifact provenance was falsely claimed",
            )
            require(
                gateway_state.get("catalog_comparison_status") == "exact"
                and gateway_state.get("dashboard_attestation_status")
                == "reviewed",
                "active compatibility diagnostics are not exact",
            )
            require(
                gateway_state.get("observed_catalog_matches_reviewed_stock_fixture") is True,
                "exact image was not recognized as the stock reviewed fixture",
            )

    stats = fixture_stats(fixture_stats_url)
    require(not stats["http_mutations"], "an HTTP mutation reached the HA fixture")
    require(not stats["websocket_mutations"], "a WebSocket mutation reached the HA fixture")
    return {
        "engineering_tool_count": len(base_names | automatic),
        "base_engineering_tool_count": len(base_names),
        "dynamic_tool_count": len(automatic),
        "representative_calls": calls,
        "error_calls": error_calls,
        "error_counter_snapshots": {
            "provider_routing_before": routing_before_errors,
            "provider_routing_after": routing_after,
            "gateway_failures_before": gateway_failure_before,
            "gateway_failures_after": gateway_failure_after,
        },
        "upstream_name_count": len(upstream_names),
        "direct_provider_snapshots": {"before": direct_before, "after": direct_after},
        "fallback_snapshots": {"before": fallback_before, "after": fallback_after},
        "initial_catalog_readiness": readiness,
        "fixture_stats": stats,
    }


async def inspect_operational_backup(
    *,
    upstream_endpoint: str,
    engineering_endpoint: str,
    fixture_stats_url: str,
    ha_url: str,
    ha_token: str,
    expected_upstream_version: str,
    release: Any,
) -> dict[str, Any]:
    """Exercise the public proposal and exact runtime provider against the image."""

    backup_name = (
        f"Exact image governed backup {expected_upstream_version}"
    )
    before = fixture_stats(fixture_stats_url)
    creates_before = len(before.get("operational_backup_creates") or [])
    async with streamablehttp_client(engineering_endpoint) as (
        read,
        write,
        _session_id,
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            proposal = decode_tool_result(
                await session.call_tool(
                    "create_backup_plan",
                    {"backup_name": backup_name},
                )
            )
    require(
        proposal.get("success") is True,
        "governed backup proposal failed in the exact image",
    )
    proposal_data = proposal.get("data") or {}
    require(
        proposal_data.get("proposal_only") is True
        and proposal_data.get("provider_dispatch_occurred") is False,
        "governed backup planning did not remain proposal-only",
    )
    after_proposal = fixture_stats(fixture_stats_url)
    require(
        len(after_proposal.get("operational_backup_creates") or [])
        == creates_before,
        "backup planning dispatched an operational write",
    )

    settings = Settings(
        ha_url=ha_url,
        ha_token=ha_token,
        access_secret="synthetic-exact-image-engineering-secret",
        port=0,
        audit_path="/tmp/synthetic-exact-image-audit.jsonl",
        rate_limit_per_minute=1,
        rate_limit_burst=1,
        destructive_services=frozenset(),
        upstream_dashboard_mcp_url=upstream_endpoint,
    )
    provider = ReviewedOperationalBackupProvider()
    provider.configure(settings)
    gateway = BackupAdministrationGateway(
        provider,
        HomeAssistantWebSocketClient(settings),
    )
    planning = await gateway.planning_evidence()
    provider_evidence = planning.get("provider") or {}
    require(
        provider_evidence.get("server_version")
        == expected_upstream_version
        and provider_evidence.get("compatibility_entry_id")
        == release.entry_id,
        "operational provider selected the wrong reviewed release",
    )
    dispatch_persisted = False

    async def before_dispatch() -> None:
        nonlocal dispatch_persisted
        require(
            not dispatch_persisted,
            "operational dispatch callback ran more than once",
        )
        dispatch_persisted = True

    dispatched = await gateway.create_full_backup(
        backup_name,
        before_dispatch=before_dispatch,
    )
    require(
        dispatch_persisted,
        "operational dispatch did not persist evidence before provider call",
    )
    verification = await gateway.verify_full_backup(
        requested_name=backup_name,
        baseline_ids=list(
            (planning.get("baseline") or {}).get("backup_ids") or []
        ),
        apply_started_at=datetime.now(timezone.utc).isoformat(),
        backup_id=dispatched.backup_id,
        operation_id=dispatched.operation_id,
    )
    require(
        verification.get("status") == "verified",
        "independent backup/info verification did not pass",
    )
    after = fixture_stats(fixture_stats_url)
    creates = after.get("operational_backup_creates") or []
    require(
        len(creates) - creates_before == 1,
        "exactly one governed backup creation was not observed",
    )
    reached = creates[-1]
    require(
        reached
        == {
            "name": backup_name,
            "agent_ids": ["hassio.local"],
            "include_homeassistant": True,
            "include_database": False,
            "include_all_addons": True,
            "password_present": True,
        },
        "the pinned image received arguments outside the reviewed contract",
    )
    health = provider.health_snapshot()
    require(
        health.get("dispatch_count") == 1
        and health.get("fallback_count") == 0,
        "operational provider accounting or fallback policy changed",
    )
    require(
        not after.get("http_mutations")
        and not after.get("websocket_mutations"),
        "an unreviewed mutation reached the HA fixture",
    )
    return {
        "proposal_only": True,
        "provider": provider_evidence.get("provider"),
        "compatibility_entry_id": provider_evidence.get(
            "compatibility_entry_id"
        ),
        "dispatch_count": health.get("dispatch_count"),
        "verified_backup_id": verification.get("evidence", {}).get(
            "backup_id"
        ),
        "archive_integrity_validated": verification.get(
            "evidence", {}
        ).get("archive_integrity_validated"),
        "fallback_count": health.get("fallback_count"),
        "exact_arguments": reached,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_reviewed_upstream_release_registry()
    release = registry.by_version.get(args.expected_upstream_version)
    require(
        release is not None,
        "requested exact-image version has no compiled review entry",
    )
    assert release is not None
    policy = release.policy
    (
        upstream_tools,
        observed_fingerprint,
        upstream_error_envelopes,
    ) = await inspect_upstream(
        args.upstream_endpoint,
        expected_upstream_version=args.expected_upstream_version,
    )
    require(len(upstream_tools) == policy.reviewed_stock_catalog_tool_count, "stock catalog count mismatch")
    observed_by_name = {tool["name"]: tool for tool in upstream_tools}
    missing_names = sorted(set(policy.by_name) - set(observed_by_name))
    extra_names = sorted(set(observed_by_name) - set(policy.by_name))
    schema_mismatches = sorted(
        name
        for name in set(observed_by_name) & set(policy.by_name)
        if schema_fingerprint(observed_by_name[name]["inputSchema"])
        != policy.by_name[name].input_schema_fingerprint
    )
    require(
        not missing_names and not extra_names and not schema_mismatches,
        "stock policy mismatch: "
        f"missing={missing_names[:20]} extra={extra_names[:20]} "
        f"schema={schema_mismatches[:20]}",
    )
    require(
        observed_fingerprint == policy.reviewed_stock_catalog_fingerprint,
        "stock catalog fingerprint mismatch: "
        f"observed={observed_fingerprint} "
        f"expected={policy.reviewed_stock_catalog_fingerprint}",
    )
    reviewed_descriptions = (
        policy.reviewed_runtime_description_fingerprints_by_name
    )
    description_mismatches = sorted(
        name
        for name, expected in reviewed_descriptions.items()
        if runtime_description_fingerprint(
            observed_by_name[name].get("description")
        )
        != expected
    )
    require(
        not description_mismatches,
        "reviewed runtime description mismatch: "
        f"tools={description_mismatches[:MAX_DIAGNOSTIC_ITEMS]}",
    )
    reviewed_annotations = (
        policy.reviewed_runtime_annotation_fingerprints_by_name
    )
    annotation_mismatches = sorted(
        name
        for name, expected in reviewed_annotations.items()
        if runtime_annotation_fingerprint(
            observed_by_name[name].get("annotations")
        )
        != expected
    )
    require(
        not annotation_mismatches,
        "reviewed runtime annotation mismatch: "
        f"tools={annotation_mismatches[:MAX_DIAGNOSTIC_ITEMS]}",
    )
    reviewed_output_schemas = (
        policy.reviewed_runtime_output_schema_fingerprints_by_name
    )
    output_schema_mismatches: list[str] = []
    for name, expected in reviewed_output_schemas.items():
        observed_schema = observed_by_name[name].get("outputSchema")
        try:
            actual = (
                schema_fingerprint(observed_schema)
                if isinstance(observed_schema, dict)
                else None
            )
        except (TypeError, ValueError, OverflowError):
            actual = None
        if actual != expected:
            output_schema_mismatches.append(name)
    require(
        not output_schema_mismatches,
        "reviewed runtime output-schema mismatch: "
        f"tools={output_schema_mismatches[:MAX_DIAGNOSTIC_ITEMS]}",
    )
    require(policy.classification_counts == EXPECTED_STOCK_COUNTS, "stock classification counts mismatch")
    engineering = await inspect_engineering(
        args.engineering_endpoint,
        args.fixture_stats_url,
        set(observed_by_name),
        expected_upstream_version=args.expected_upstream_version,
        policy=policy,
        release=release,
    )
    operational_backup = await inspect_operational_backup(
        upstream_endpoint=args.upstream_endpoint,
        engineering_endpoint=args.engineering_endpoint,
        fixture_stats_url=args.fixture_stats_url,
        ha_url=args.ha_url,
        ha_token=args.ha_token,
        expected_upstream_version=args.expected_upstream_version,
        release=release,
    )
    return {
        "result": "PASS",
        "upstream_version": args.expected_upstream_version,
        "observed_catalog_count": len(upstream_tools),
        "observed_catalog_fingerprint": observed_fingerprint,
        "reviewed_runtime_description_fingerprint_count": len(
            reviewed_descriptions
        ),
        "reviewed_runtime_annotation_fingerprint_count": len(
            reviewed_annotations
        ),
        "reviewed_runtime_output_schema_fingerprint_count": len(
            reviewed_output_schemas
        ),
        "upstream_error_envelopes": upstream_error_envelopes,
        "classification_counts": policy.classification_counts,
        "operational_backup": operational_backup,
        **engineering,
    }


def main() -> None:
    for logger_name in ("mcp.client.streamable_http", "httpx", "httpcore"):
        logger = logging.getLogger(logger_name)
        logger.disabled = True
        logger.propagate = False
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-endpoint", required=True)
    parser.add_argument("--expected-upstream-version", required=True)
    parser.add_argument("--engineering-endpoint", required=True)
    parser.add_argument("--fixture-stats-url", required=True)
    parser.add_argument("--ha-url", required=True)
    parser.add_argument("--ha-token", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(
            asyncio.wait_for(run(args), timeout=ACCEPTANCE_TIMEOUT_SECONDS)
        )
    except Exception as exc:
        failure = _bounded_failure_result(exc)
        args.output.write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise SystemExit(
            "exact-image read gateway acceptance failed; "
            "see the bounded result artifact"
        ) from None
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "exact-image read gateway acceptance: PASS "
        f"({result['observed_catalog_count']} advertised, "
        f"{result['dynamic_tool_count']} delegated)"
    )


if __name__ == "__main__":
    main()
