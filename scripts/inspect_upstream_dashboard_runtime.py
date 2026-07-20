"""Extract and exercise one exact upstream dashboard descriptor."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    REQUIRED_DASHBOARD_TOOL,
    validate_dashboard_read_arguments,
)
from ha_mcp_engineering.providers.upstream_contracts import (  # noqa: E402
    canonical_json,
    normalize_runtime_contract,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    _reviewed_security_contract_projection,
    ensure_dashboard_tool_allowed,
)


async def inspect(endpoint: str, version: str) -> dict:
    async with streamablehttp_client(endpoint) as (read_stream, write_stream, _session_id):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            tools = [
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in listed.tools
            ]
            tool = next((item for item in tools if item.get("name") == REQUIRED_DASHBOARD_TOOL), None)
            if tool is None:
                raise SystemExit("required_tool_missing")
            if initialized.serverInfo.name != "ha-mcp" or initialized.serverInfo.version != version:
                raise SystemExit("upstream_identity_mismatch")
            contract = normalize_runtime_contract(
                tool,
                protocol_version=str(initialized.protocolVersion),
            )
            raw_input_schema_fingerprint = hashlib.sha256(
                canonical_json(tool.get("inputSchema"))
            ).hexdigest()
            reviewed_security_descriptor_fingerprint = hashlib.sha256(
                canonical_json(_reviewed_security_contract_projection(tool))
            ).hexdigest()
            published_runtime_descriptor_fingerprint = hashlib.sha256(
                canonical_json(tool)
            ).hexdigest()
            list_arguments = {"list_only": True, "include_screenshot": False}
            get_arguments = {
                "url_path": "compatibility-fixture",
                "list_only": False,
                "force_reload": True,
                "include_screenshot": False,
            }
            validate_dashboard_read_arguments(list_arguments)
            validate_dashboard_read_arguments(get_arguments)
            rejected_tools = []
            for prohibited_tool in (
                "ha_set_entity",
                "ha_set_device",
                "ha_call_service",
                "ha_bulk_control",
                "ha_config_set_dashboard",
                "ha_config_delete_dashboard",
            ):
                try:
                    ensure_dashboard_tool_allowed(prohibited_tool)
                except Exception:
                    rejected_tools.append(prohibited_tool)
                else:
                    raise SystemExit("prohibited_upstream_tool_was_allowlisted")
            try:
                validate_dashboard_read_arguments(
                    {"list_only": True, "include_screenshot": True}
                )
            except Exception:
                screenshot_rejected = True
            else:
                raise SystemExit("screenshot_argument_was_accepted")
            listed_dashboards = await session.call_tool(REQUIRED_DASHBOARD_TOOL, list_arguments)
            dashboard = await session.call_tool(REQUIRED_DASHBOARD_TOOL, get_arguments)
            list_payload = _payload(listed_dashboards)
            get_payload = _payload(dashboard)
            if not list_payload.get("success") or not isinstance(list_payload.get("dashboards"), list):
                raise SystemExit("dashboard_list_contract_failed")
            if not get_payload.get("success") or not isinstance(get_payload.get("config"), dict):
                raise SystemExit("dashboard_get_contract_failed")
            supplied_hash = get_payload.get("config_hash")
            expected_hash = hashlib.sha256(
                json.dumps(
                    get_payload["config"], sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()[:16]
            if supplied_hash != expected_hash:
                raise SystemExit("dashboard_hash_contract_failed")
            return {
                "server_name": initialized.serverInfo.name,
                "server_version": initialized.serverInfo.version,
                "protocol_version": str(initialized.protocolVersion),
                "tool_count": len(tools),
                "catalog_fingerprint": hashlib.sha256(canonical_json(tools)).hexdigest(),
                "required_tool": tool,
                "contract_fingerprints": {
                    "input": contract.input_fingerprint,
                    "security": contract.security_fingerprint,
                    "output": contract.output_fingerprint,
                    "runtime": contract.runtime_fingerprint,
                },
                "informational_fingerprints": {
                    "raw_input_schema": raw_input_schema_fingerprint,
                    "reviewed_security_descriptor": (
                        reviewed_security_descriptor_fingerprint
                    ),
                    "fixture_runtime_descriptor": (
                        published_runtime_descriptor_fingerprint
                    ),
                    "published_runtime_descriptor": (
                        published_runtime_descriptor_fingerprint
                    ),
                },
                "positive_tests": {
                    "list_dashboards": True,
                    "get_dashboard_config": True,
                    "config_hash_verified": True,
                },
                "dispatched_arguments": [list_arguments, get_arguments],
                "write_dispatches": 0,
                "negative_reachability": {
                    "rejected_before_dispatch": rejected_tools,
                    "include_screenshot_true_rejected": screenshot_rejected,
                    "generic_forwarder_present": False,
                },
            }


def _payload(result) -> dict:
    dumped = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    text = "\n".join(
        item["text"]
        for item in dumped.get("content", [])
        if item.get("type") == "text" and isinstance(item.get("text"), str)
    )
    value = json.loads(text)
    if not isinstance(value, dict):
        raise SystemExit("upstream_payload_invalid")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = asyncio.run(inspect(args.endpoint, args.version))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json(value) + b"\n")


if __name__ == "__main__":
    main()
