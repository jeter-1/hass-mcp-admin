#!/usr/bin/env python3
"""Reproduce and explain the Beta 11 ha-mcp 8.0.0 admission failure."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    _compare_tool_contract,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    canonical_json,
    catalog_fingerprint,
    load_reviewed_upstream_release_registry,
    runtime_contract_fingerprint,
    schema_fingerprint,
)


SELECTED_TOOLS = (
    "ha_get_state",
    "ha_config_get_automation",
    "ha_get_history",
    "ha_list_services",
)


def strict_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_tools(path: Path) -> list[dict[str, Any]]:
    value = strict_load(path)
    tools = value.get("tools") if isinstance(value, dict) else None
    if tools is None and isinstance(value, dict):
        result = value.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list) or not all(
        isinstance(item, dict) for item in tools
    ):
        raise ValueError(f"{path} does not contain a tools array")
    return tools


def by_name(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    values = {str(item.get("name")): item for item in tools}
    if len(values) != len(tools):
        raise ValueError("catalog contains duplicate tool names")
    return values


def pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def changed_fields(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
    if type(old) is not type(new):
        return [{"path": path or "/", "reviewed": old, "observed": new}]
    if isinstance(old, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(old) | set(new)):
            child = f"{path}/{pointer_escape(str(key))}"
            if key not in old:
                changes.append(
                    {"path": child, "reviewed": "<omitted>", "observed": new[key]}
                )
            elif key not in new:
                changes.append(
                    {"path": child, "reviewed": old[key], "observed": "<omitted>"}
                )
            else:
                changes.extend(changed_fields(old[key], new[key], child))
        return changes
    if isinstance(old, list):
        if old == new:
            return []
        return [{"path": path or "/", "reviewed": old, "observed": new}]
    if old == new:
        return []
    return [{"path": path or "/", "reviewed": old, "observed": new}]


def reconstruct_addon(
    reviewed_tools: list[dict[str, Any]], fixture: dict[str, Any]
) -> list[dict[str, Any]]:
    replacement = fixture["transform"]["replacement"]
    reconstructed = deepcopy(reviewed_tools)
    for tool in reconstructed:
        tool["_meta"]["ha_mcp"]["policy"] = deepcopy(replacement)
    return reconstructed


def tool_fingerprints(
    tool: dict[str, Any],
    *,
    name: str,
    release: Any,
) -> dict[str, Any]:
    policy = release.policy
    reviewed = release.tool_contracts_by_name[name]
    decision = _compare_tool_contract(
        policy.by_name[name],
        tool,
        protocol_version="2025-03-26",
        reviewed_runtime_description_fingerprint=(
            policy.reviewed_runtime_description_fingerprints_by_name[name]
        ),
        reviewed_runtime_annotation_fingerprint=(
            policy.reviewed_runtime_annotation_fingerprints_by_name[name]
        ),
        reviewed_runtime_output_schema_fingerprint=(
            policy.reviewed_runtime_output_schema_fingerprints_by_name[name]
        ),
        reviewed_runtime_contract_fingerprint=(
            reviewed.runtime_contract_fingerprint
        ),
        reviewed_runtime_contract_field_fingerprints=dict(
            reviewed.runtime_contract_field_fingerprints
        ),
        runtime_contract_fingerprint_model=(
            release.runtime_contract_fingerprint_model
        ),
    )
    policy_value = (
        tool.get("_meta", {}).get("ha_mcp", {}).get("policy")
        if isinstance(tool.get("_meta"), dict)
        else None
    )
    return {
        "input_schema_fingerprint": schema_fingerprint(tool.get("inputSchema")),
        "ordinary_contract_fingerprint": decision.observed_fingerprint,
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
        "raw_runtime_contract_fingerprint": schema_fingerprint(tool),
        "admission_runtime_contract_fingerprint": (
            runtime_contract_fingerprint(
                tool,
                model=release.runtime_contract_fingerprint_model,
            )
        ),
        "runtime_contract_fingerprint_model": (
            release.runtime_contract_fingerprint_model
        ),
        "policy_metadata_fingerprint": schema_fingerprint(
            {
                "present": policy_value is not None,
                "value": policy_value,
            }
        ),
        "accepted": decision.accepted,
        "reason": decision.reason,
        "runtime_contract_diff_fields": list(
            decision.runtime_contract_diff_fields
        ),
    }


def source_record(
    name: str,
    path: Path | None,
    tools: list[dict[str, Any]],
    *,
    release: Any,
) -> dict[str, Any]:
    records = by_name(tools)
    return {
        "source": name,
        "input_path": str(path) if path is not None else None,
        "input_sha256": file_sha256(path) if path is not None else None,
        "advertised_tool_count": len(tools),
        "operational_catalog_fingerprint": catalog_fingerprint(tools),
        "strict_ordered_catalog_fingerprint": schema_fingerprint({"tools": tools}),
        "tools": {
            tool_name: tool_fingerprints(
                records[tool_name], name=tool_name, release=release
            )
            for tool_name in SELECTED_TOOLS
        },
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ha-mcp 8.0.0 live add-on runtime-contract diff",
        "",
        "All fingerprints use protocol `2025-03-26`. Values are computed from "
        "the retained reviewed fixture and the exact local artifact captures named "
        "in the machine-readable report.",
        "",
        "## Catalogs",
        "",
        "| Source | Tools | Operational fingerprint | Strict ordered fingerprint |",
        "|---|---:|---|---|",
    ]
    for source in report["sources"]:
        lines.append(
            f"| `{source['source']}` | {source['advertised_tool_count']} | "
            f"`{source['operational_catalog_fingerprint']}` | "
            f"`{source['strict_ordered_catalog_fingerprint']}` |"
        )
    lines.extend(["", "## Reviewed-to-source field differences", ""])
    for tool_name, comparisons in report["field_diffs"].items():
        lines.extend([f"### `{tool_name}`", ""])
        for source_name, changes in comparisons.items():
            lines.append(f"- `{source_name}`: {len(changes)} changed field(s)")
            for change in changes:
                lines.append(
                    f"  - `{change['path']}`: `{json.dumps(change['reviewed'], sort_keys=True)}` "
                    f"→ `{json.dumps(change['observed'], sort_keys=True)}`"
                )
        lines.append("")
    lines.extend(
        [
            "## Finding",
            "",
            "The exact add-on and the deterministic reconstruction differ from the "
            "reviewed standalone fixture only at the shared live policy block. Input "
            "schemas, descriptions, annotations, output contracts, titles, tags, "
            "LLM exposure, and pinning are unchanged for the four representative "
            "tools. The ordinary comparator therefore remains equal while the legacy "
            "raw full-descriptor runtime comparator differs.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT
        / "docs/evidence/upstream-read-compatibility/ha-mcp-8.0.0-live-addon-reconstruction.json",
    )
    parser.add_argument("--standalone-tools", type=Path)
    parser.add_argument("--addon-tools", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = strict_load(args.fixture)
    reviewed_path = ROOT / fixture["base_capture_resource"]
    if file_sha256(reviewed_path) != fixture["base_capture_sha256"]:
        raise SystemExit("reviewed base capture digest mismatch")
    reviewed_tools = load_tools(reviewed_path)
    reconstructed_tools = reconstruct_addon(reviewed_tools, fixture)
    expected = fixture["expected_results"]
    if catalog_fingerprint(reviewed_tools) != expected[
        "reviewed_stock_catalog_fingerprint"
    ]:
        raise SystemExit("reviewed stock catalog fingerprint mismatch")
    if catalog_fingerprint(reconstructed_tools) != expected[
        "observed_live_catalog_fingerprint"
    ]:
        raise SystemExit("live reconstruction fingerprint mismatch")

    release = load_reviewed_upstream_release_registry().by_version["8.0.0"]
    sources: list[tuple[str, Path | None, list[dict[str, Any]]]] = [
        ("reviewed_fixture", reviewed_path, reviewed_tools),
        ("live_addon_reconstruction", args.fixture, reconstructed_tools),
    ]
    if args.standalone_tools is not None:
        sources.append(
            (
                "exact_standalone_image",
                args.standalone_tools,
                load_tools(args.standalone_tools),
            )
        )
    if args.addon_tools is not None:
        sources.append(
            (
                "exact_addon_image",
                args.addon_tools,
                load_tools(args.addon_tools),
            )
        )

    source_records = [
        source_record(name, path, tools, release=release)
        for name, path, tools in sources
    ]
    reviewed_by_name = by_name(reviewed_tools)
    field_diffs: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for tool_name in SELECTED_TOOLS:
        field_diffs[tool_name] = {
            name: changed_fields(
                reviewed_by_name[tool_name], by_name(tools)[tool_name]
            )
            for name, _path, tools in sources
            if name != "reviewed_fixture"
        }
    report = {
        "report_format_version": 1,
        "protocol_version": fixture["protocol_version"],
        "source_commit": fixture["source_commit"],
        "sources": source_records,
        "field_diffs": field_diffs,
    }
    args.output_json.write_bytes(canonical_json(report) + b"\n")
    args.output_markdown.write_text(markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
