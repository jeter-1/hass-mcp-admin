"""Bounded disconnect and exact-readmission probe for disposable CI images."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.tools import (  # noqa: E402
    ENGINEERING_STATIC_TOOL_COUNT,
)


MAX_ATTEMPTS = 30
RETRY_SECONDS = 2.0
EXPECTED_PROTOCOL = "2025-03-26"
EXPECTED_ENTRY_BY_VERSION = {
    "8.1.0": "ha-mcp-v8.1.0-4c07e625",
    "8.1.1": "ha-mcp-v8.1.1-e1d76a6e",
    "8.2.0": "ha-mcp-v8.2.0-dbcfc0ee",
    "8.4.1": "ha-mcp-v8.4.1-7823b365",
}
EXPECTED_UPSTREAM_TOOL_COUNT = 78
EXPECTED_ENGINEERING_LOCAL_TOOL_COUNT = ENGINEERING_STATIC_TOOL_COUNT
EXPECTED_ACCOUNTING_BY_VERSION = {
    "8.1.0": {
        "delegated_read_count": 24,
        "held_tools": {"ha_search", "ha_get_operation_status"},
        "engineering_total_tool_count": (
            ENGINEERING_STATIC_TOOL_COUNT + 24
        ),
    },
    "8.1.1": {
        "delegated_read_count": 25,
        "held_tools": {"ha_get_operation_status"},
        "engineering_total_tool_count": (
            ENGINEERING_STATIC_TOOL_COUNT + 25
        ),
    },
    "8.2.0": {
        "delegated_read_count": 25,
        "held_tools": {"ha_get_operation_status"},
        "engineering_total_tool_count": (
            ENGINEERING_STATIC_TOOL_COUNT + 25
        ),
    },
    "8.4.1": {
        "delegated_read_count": 25,
        "held_tools": {"ha_get_operation_status"},
        "engineering_total_tool_count": (
            ENGINEERING_STATIC_TOOL_COUNT + 25
        ),
    },
}
ZERO_ADMISSION_COUNTERS = (
    "schema_mismatch_count",
    "description_semantics_mismatch_count",
    "annotation_mismatch_count",
    "output_contract_mismatch_count",
    "runtime_contract_mismatch_count",
    "quarantined_automatic_read_count",
    "missing_reviewed_read_count",
    "missing_automatic_read_count",
    "unreviewed_tool_count",
    "unreviewed_observed_tool_count",
    "fallback_count",
)


class ReadmissionFailure(RuntimeError):
    """One bounded lifecycle assertion failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReadmissionFailure(message)


def decode_tool_result(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "result" not in structured:
        return structured
    for item in getattr(result, "content", ()):
        text = getattr(item, "text", None)
        if not isinstance(text, str) or len(text.encode("utf-8")) > 100_000:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ReadmissionFailure("Engineering returned no bounded JSON result")


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


def bounded_gateway_health(value: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        item
        for item in find_values(value, "upstream_read_gateway")
        if isinstance(item, dict)
    ]
    require(candidates, "Engineering health omitted upstream read admission")
    gateway = candidates[0]
    fields = (
        "admission_status",
        "selected_compatibility_entry_id",
        "observed_protocol_version",
        "observed_advertised_tool_count",
        "reviewed_accounted_tool_count",
        "reviewed_tool_accounting_valid",
        "exact_matched_automatic_read_count",
        "dynamically_exposed_count",
        "held_read_count",
        "held_tools",
        *ZERO_ADMISSION_COUNTERS,
    )
    return {name: gateway.get(name) for name in fields}


def exact_readmission_observed(
    observed: dict[str, Any], *, expected_upstream_version: str
) -> bool:
    expected_entry = EXPECTED_ENTRY_BY_VERSION.get(expected_upstream_version)
    expected = EXPECTED_ACCOUNTING_BY_VERSION.get(expected_upstream_version)
    if expected_entry is None or expected is None:
        return False
    expected_delegated = expected["delegated_read_count"]
    expected_held_tools = expected["held_tools"]
    health = observed.get("gateway_health")
    return bool(
        observed.get("success") is True
        and observed.get("provider") == "upstream_read_gateway"
        and observed.get("upstream_version") == expected_upstream_version
        and observed.get("fallback") == "none"
        and observed.get("fallback_occurred") is False
        and observed.get("engineering_tool_count")
        == expected["engineering_total_tool_count"]
        and observed.get("engineering_local_tool_count")
        == EXPECTED_ENGINEERING_LOCAL_TOOL_COUNT
        and isinstance(health, dict)
        and health.get("admission_status") == "admitted_exact"
        and health.get("selected_compatibility_entry_id") == expected_entry
        and health.get("observed_protocol_version") == EXPECTED_PROTOCOL
        and health.get("observed_advertised_tool_count")
        == EXPECTED_UPSTREAM_TOOL_COUNT
        and health.get("reviewed_accounted_tool_count")
        == EXPECTED_UPSTREAM_TOOL_COUNT
        and health.get("reviewed_tool_accounting_valid") is True
        and health.get("exact_matched_automatic_read_count")
        == expected_delegated
        and health.get("dynamically_exposed_count")
        == expected_delegated
        and health.get("held_read_count") == len(expected_held_tools)
        and set(health.get("held_tools") or ()) == expected_held_tools
        and all(health.get(name) == 0 for name in ZERO_ADMISSION_COUNTERS)
    )


async def probe(endpoint: str) -> dict[str, Any]:
    async with streamablehttp_client(endpoint) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            require(
                initialized.serverInfo.name == "ha-engineering-beta",
                "Engineering server identity changed",
            )
            catalog = await session.list_tools()
            names = {tool.name for tool in catalog.tools}
            health = bounded_gateway_health(
                decode_tool_result(
                    await session.call_tool(
                        "get_server_health", {"check_ha": False}
                    )
                )
            )
            held_tools = set(health.get("held_tools") or ())
            require("ha_get_state" in names, "admitted representative read disappeared")
            require(
                not held_tools.intersection(names),
                "held read became reachable through Engineering",
            )
            result = decode_tool_result(
                await session.call_tool(
                    "ha_get_state", {"entity_id": "sun.sun"}
                )
            )
            metadata = result.get("metadata")
            return {
                "success": result.get("success") is True,
                "error_code": (
                    str(result.get("error_code"))[:96]
                    if isinstance(result.get("error_code"), str)
                    else None
                ),
                "provider": (
                    metadata.get("provider")
                    if isinstance(metadata, dict)
                    else None
                ),
                "upstream_version": (
                    metadata.get("upstream_version")
                    if isinstance(metadata, dict)
                    else None
                ),
                "fallback": (
                    metadata.get("fallback")
                    if isinstance(metadata, dict)
                    else None
                ),
                "fallback_occurred": (
                    metadata.get("fallback_occurred")
                    if isinstance(metadata, dict)
                    else None
                ),
                "engineering_tool_count": len(names),
                "held_tools_absent": True,
                "engineering_local_tool_count": (
                    len(names)
                    - health.get("dynamically_exposed_count", 0)
                    if isinstance(
                        health.get("dynamically_exposed_count"), int
                    )
                    else None
                ),
                "gateway_health": health,
            }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.phase == "disconnected":
        observed = await probe(args.engineering_endpoint)
        require(not observed["success"], "read succeeded while upstream was stopped")
        require(
            observed["provider"] == "upstream_read_gateway",
            "disconnect failure lost provider attribution",
        )
        require(
            observed["fallback"] == "none"
            and observed["fallback_occurred"] is False,
            "disconnect attempted fallback",
        )
        return {"result": "PASS", "phase": args.phase, "probe": observed}

    last: dict[str, Any] | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            last = await probe(args.engineering_endpoint)
        except Exception:  # bounded retry; no exception content is retained
            last = None
        if isinstance(last, dict) and exact_readmission_observed(
            last, expected_upstream_version=args.expected_upstream_version
        ):
            return {
                "result": "PASS",
                "phase": args.phase,
                "attempt": attempt,
                "probe": last,
            }
        await asyncio.sleep(RETRY_SECONDS)
    raise ReadmissionFailure("Engineering did not exactly readmit the restarted release")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engineering-endpoint", required=True)
    parser.add_argument("--expected-upstream-version", required=True)
    parser.add_argument(
        "--phase", choices=("disconnected", "readmitted"), required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        args.output.write_text(
            json.dumps(
                {
                    "result": "FAIL",
                    "phase": args.phase,
                    "failure_type": type(exc).__name__[:96],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise SystemExit(
            "exact-image lifecycle acceptance failed; see bounded evidence"
        ) from None
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"exact-image lifecycle {args.phase}: PASS")


if __name__ == "__main__":
    main()
