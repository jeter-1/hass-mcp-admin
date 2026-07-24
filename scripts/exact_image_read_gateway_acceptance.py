"""Exact ha-mcp 7.14.1 image acceptance for the read-only gateway.

This script is intentionally transport-level.  CI starts the reviewed image,
the current Engineering image, and the synthetic read-only HA fixture before
invoking it.
"""

from __future__ import annotations

import argparse
import asyncio
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

from ha_mcp_engineering.tools import get_registered_server  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    catalog_fingerprint,
    load_upstream_tool_policy,
    runtime_annotation_fingerprint,
    runtime_description_fingerprint,
    schema_fingerprint,
)


EXPECTED_UPSTREAM_VERSION = "7.14.1"
EXPECTED_ENGINEERING_BASELINE_COUNT = 41
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


def fixture_stats(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed CI fixture URL
        return json.load(response)


async def inspect_upstream(endpoint: str) -> tuple[list[dict[str, Any]], str]:
    async with streamablehttp_client(endpoint) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            require(initialized.serverInfo.name == "ha-mcp", "upstream name mismatch")
            require(
                initialized.serverInfo.version == EXPECTED_UPSTREAM_VERSION,
                "upstream version mismatch",
            )
            tools = await list_all_tools(session)
    return tools, catalog_fingerprint(tools)


async def inspect_engineering(
    endpoint: str, fixture_stats_url: str, upstream_names: set[str]
) -> dict[str, Any]:
    readiness = engineering_readiness(endpoint)
    if readiness["http_status"] != 200 or readiness["ready"] is not True:
        raise AcceptanceFailure(
            "Engineering did not publish a ready initial catalog.",
            diagnostics={"initial_catalog_readiness": readiness},
        )
    base_names = {
        tool.name for tool in get_registered_server()._tool_manager.list_tools()
    }
    require(
        len(base_names) == EXPECTED_ENGINEERING_BASELINE_COUNT,
        (
            "local Engineering baseline is not "
            f"{EXPECTED_ENGINEERING_BASELINE_COUNT} tools"
        ),
    )
    policy = load_upstream_tool_policy()
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
                require(metadata.get("upstream_version") == EXPECTED_UPSTREAM_VERSION, f"{name} version mismatch")
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

            unavailable = await session.call_tool(
                "ha_call_service", {"domain": "fixture", "service": "noop"}
            )
            require(bool(unavailable.isError), "write-classified upstream tool became callable")

            audit = decode_tool_result(
                await session.call_tool("get_audit_log", {"event": "tool_call", "lines": 200})
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
            expected_delegated_calls = len(REPRESENTATIVE_CALLS) + 1
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
                after_successes - before_successes == expected_delegated_calls,
                "successful upstream read-gateway accounting mismatch",
            )
            require(
                after_failures == before_failures,
                "successful delegated reads changed provider failure accounting",
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
            require(fallback_before == fallback_after, "fallback counters changed")
            require(gateway_state.get("fallback_count") == 0, "gateway fallback occurred")
            require(
                gateway_state.get("dynamically_exposed_count") == len(automatic),
                "dynamic exposure count mismatch",
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
        "upstream_name_count": len(upstream_names),
        "direct_provider_snapshots": {"before": direct_before, "after": direct_after},
        "fallback_snapshots": {"before": fallback_before, "after": fallback_after},
        "initial_catalog_readiness": readiness,
        "fixture_stats": stats,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    policy = load_upstream_tool_policy()
    upstream_tools, observed_fingerprint = await inspect_upstream(args.upstream_endpoint)
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
    )
    return {
        "result": "PASS",
        "upstream_version": EXPECTED_UPSTREAM_VERSION,
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
        "classification_counts": policy.classification_counts,
        **engineering,
    }


def main() -> None:
    for logger_name in ("mcp.client.streamable_http", "httpx", "httpcore"):
        logger = logging.getLogger(logger_name)
        logger.disabled = True
        logger.propagate = False
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-endpoint", required=True)
    parser.add_argument("--engineering-endpoint", required=True)
    parser.add_argument("--fixture-stats-url", required=True)
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
