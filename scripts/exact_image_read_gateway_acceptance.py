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
    schema_fingerprint,
)


EXPECTED_UPSTREAM_VERSION = "7.14.1"
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
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


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
    base_names = {
        tool.name for tool in get_registered_server()._tool_manager.list_tools()
    }
    require(len(base_names) == 40, "local Engineering baseline is not 40 tools")
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
            require(base_names <= names, "an existing Engineering tool is missing")
            require(automatic <= names, "an exact matched reviewed read is missing")
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

            health_before_result = await session.call_tool("get_server_health", {})
            health_before = decode_tool_result(health_before_result)
            direct_before = find_values(health_before, "requests_by_provider")
            fallback_before = find_values(health_before, "fallback_count")

            calls: dict[str, dict[str, Any]] = {}
            for name, arguments in REPRESENTATIVE_CALLS.items():
                result = await session.call_tool(name, arguments)
                value = decode_tool_result(result)
                require(value.get("success") is True, f"{name} did not succeed: {value.get('error_code')}")
                metadata = value.get("metadata") or {}
                require(metadata.get("provider") == "upstream_read_gateway", f"{name} provider mismatch")
                require(metadata.get("fallback") == "none", f"{name} fallback mismatch")
                require(metadata.get("upstream_version") == EXPECTED_UPSTREAM_VERSION, f"{name} version mismatch")
                calls[name] = {
                    "request_id": value.get("request_id"),
                    "provider": metadata.get("provider"),
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
                require(name in audit_text, f"audit missing {name} tool name")

            health_after = decode_tool_result(
                await session.call_tool("get_server_health", {})
            )
            direct_after = find_values(health_after, "requests_by_provider")
            fallback_after = find_values(health_after, "fallback_count")
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
    try:
        result = asyncio.run(run(args))
    except AcceptanceFailure as exc:
        raise SystemExit(f"exact-image read gateway acceptance failed: {exc}") from None
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "exact-image read gateway acceptance: PASS "
        f"({result['observed_catalog_count']} advertised, "
        f"{result['dynamic_tool_count']} delegated)"
    )


if __name__ == "__main__":
    main()
