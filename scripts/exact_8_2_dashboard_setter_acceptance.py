"""Probe an exact reviewed ha-mcp dashboard setter against a refusing fixture.

The fixture rejects every save/create frame, so this proves which requests reach
the authoritative Home Assistant mutation boundary without mutating a dashboard.
It is intentionally a direct upstream contract probe, not an Engineering route.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
import sys
from typing import Any
from urllib.request import urlopen

from mcp import types
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.mcp_sdk_compatibility import (  # noqa: E402
    ReviewedProtocolClientSession,
)


SUPPORTED_VERSIONS = frozenset({"8.2.0", "8.4.1", "8.4.3"})
EXPECTED_PROTOCOL = "2025-03-26"
MAX_RESPONSE_BYTES = 32_768
ACK_KEY_PATTERN = re.compile(
    r"\bI-HAVE-READ-THE-BEST-PRACTICES-GUIDE-[0-9a-f]{8}\b"
)


class DashboardSetterAcceptanceFailure(RuntimeError):
    """One exact resolver or mutation-boundary assertion failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DashboardSetterAcceptanceFailure(message)


def fixture_stats(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed CI fixture
        value = json.load(response)
    require(isinstance(value, dict), "fixture statistics are malformed")
    return value


def mutation_attempt_count(stats: dict[str, Any]) -> int:
    values = stats.get("websocket_mutations")
    require(isinstance(values, dict), "fixture mutation counters are missing")
    require(
        all(type(value) is int and value >= 0 for value in values.values()),
        "fixture mutation counters are malformed",
    )
    return sum(values.values())


def decode_error_code(result: Any) -> str | None:
    for item in getattr(result, "content", ()):
        text = getattr(item, "text", None)
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        error = value.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
        if isinstance(value.get("code"), str):
            return value["code"]
    return None


def bounded_text(result: Any) -> str:
    parts: list[str] = []
    total = 0
    for item in getattr(result, "content", ()):
        value = getattr(item, "text", None)
        if not isinstance(value, str):
            continue
        total += len(value.encode("utf-8"))
        require(total <= MAX_RESPONSE_BYTES, "upstream skill response is oversized")
        parts.append(value)
    return "\n".join(parts)


async def probe(
    *,
    upstream_endpoint: str,
    fixture_stats_url: str,
    expected_version: str,
) -> dict[str, Any]:
    cases = (
        ("existing_map", "map", True),
        ("existing_hyphenated", "compatibility-fixture", True),
        ("new_hyphenless", "newdashboard", False),
        ("new_hyphenated", "new-dashboard", True),
    )
    results: dict[str, Any] = {}
    async with streamablehttp_client(upstream_endpoint) as (
        read,
        write,
        _session_id,
    ):
        async with ReviewedProtocolClientSession(
            read,
            write,
            client_info=types.Implementation(
                name="hass-mcp-engineering-exact-dashboard-review",
                version="1",
            ),
        ) as session:
            initialized = await session.initialize()
            require(
                initialized.serverInfo.name == "ha-mcp"
                and initialized.serverInfo.version == expected_version,
                "exact upstream identity changed",
            )
            require(
                str(initialized.protocolVersion) == EXPECTED_PROTOCOL,
                "exact upstream protocol changed",
            )
            skill_result = await session.call_tool(
                "ha_get_skill_guide",
                {
                    "skill": "home-assistant-best-practices",
                    "file": "references/dashboard-guide.md",
                },
            )
            require(
                getattr(skill_result, "isError", False) is not True,
                "strict-BPS dashboard guide could not be read",
            )
            ack_match = ACK_KEY_PATTERN.search(bounded_text(skill_result))
            require(ack_match is not None, "strict-BPS acknowledgment key is missing")
            best_practice_key = ack_match.group(0)
            for name, url_path, should_reach_mutation_boundary in cases:
                before = mutation_attempt_count(fixture_stats(fixture_stats_url))
                result = await session.call_tool(
                    "ha_config_set_dashboard",
                    {
                        "url_path": url_path,
                        "config": {"title": "Synthetic contract probe", "views": []},
                        "MandatoryBPS": True,
                        "BestPracticeKey": best_practice_key,
                        "return_screenshot": False,
                    },
                )
                after = mutation_attempt_count(fixture_stats(fixture_stats_url))
                delta = after - before
                require(delta >= 0, "fixture mutation counter moved backwards")
                error_code = decode_error_code(result)
                require(
                    getattr(result, "isError", False) is True,
                    "refusing fixture unexpectedly accepted a dashboard mutation",
                )
                if should_reach_mutation_boundary:
                    require(
                        delta >= 1,
                        f"{name} was rejected before the reviewed mutation boundary",
                    )
                else:
                    require(delta == 0, "new hyphenless target reached mutation boundary")
                    require(
                        error_code == "VALIDATION_INVALID_PARAMETER",
                        "new hyphenless target did not retain exact validation failure",
                    )
                results[name] = {
                    "url_path": url_path,
                    "setter_invocations": 1,
                    "mutation_boundary_reached": delta >= 1,
                    "fixture_mutation_attempt_delta": delta,
                    "upstream_error_code": error_code,
                }
    model = f"ha-mcp-{expected_version}-dashboard-setter-runtime-acceptance-v1"
    return {
        "model": model,
        "upstream_version": expected_version,
        "protocol_version": EXPECTED_PROTOCOL,
        "cases": results,
        "successful_fixture_mutations": 0,
        "engineering_dashboard_fallback_used": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-endpoint", required=True)
    parser.add_argument("--fixture-stats-url", required=True)
    parser.add_argument(
        "--expected-version",
        choices=sorted(SUPPORTED_VERSIONS),
        default="8.2.0",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        probe(
            upstream_endpoint=args.upstream_endpoint,
            fixture_stats_url=args.fixture_stats_url,
            expected_version=args.expected_version,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
