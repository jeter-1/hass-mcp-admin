"""Capture, compare, and validate reviewed ha-mcp read-release contracts.

Generated candidate data is deliberately marked ``candidate_unapproved``.
Runtime admission requires a separate human-reviewed source change that changes
the status to ``reviewed`` and passes the compiled registry validator.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import difflib
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    ReviewedUpstreamReleaseRegistry,
    canonical_json,
    catalog_fingerprint,
    generated_reviewed_release_registry,
    load_reviewed_upstream_release_registry,
    load_upstream_tool_policy,
    reviewed_tool_contracts_from_capture,
    runtime_annotation_fingerprint,
    runtime_description_fingerprint,
    schema_fingerprint,
    validate_reviewed_release_evidence,
)


MAX_CATALOG_PAGES = 16
MAX_CATALOG_TOOLS = 512
MAX_ERROR_BYTES = 16_384
ERROR_PROBES = {
    "invalid_search": (
        "ha_search",
        {"search_types": []},
    ),
    "missing_state": (
        "ha_get_state",
        {"entity_id": "sensor.compatibility_review_missing_state"},
    ),
    "missing_automation": (
        "ha_config_get_automation",
        {"identifier": "compatibility_review_missing_automation"},
    ),
    "missing_registry_entity": (
        "ha_get_entity",
        {"entity_id": "sensor.compatibility_review_missing_registry_entity"},
    ),
}


def strict_json(value: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for name, item in pairs:
            if name in value:
                raise ValueError("duplicate JSON member")
            value[name] = item
        return value

    def finite(_value: str) -> None:
        raise ValueError("non-finite JSON constant")

    return json.loads(
        value,
        object_pairs_hook=unique,
        parse_constant=finite,
    )


def strict_load(path: Path) -> Any:
    return strict_json(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value) + b"\n")


def shape_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(name): shape_projection(item)
            for name, item in sorted(value.items())
        }
    if isinstance(value, list):
        return [shape_projection(item) for item in value[:32]]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "unsupported"


def error_evidence(result: Any) -> dict[str, Any]:
    encoded = result.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    content = encoded.get("content")
    if (
        encoded.get("isError") is not True
        or not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or not isinstance(content[0].get("text"), str)
    ):
        return {
            "is_error": encoded.get("isError") is True,
            "structured_code": None,
            "shape_fingerprint": schema_fingerprint(
                shape_projection(encoded)
            ),
        }
    raw = content[0]["text"].encode("utf-8")
    if len(raw) > MAX_ERROR_BYTES:
        raise ValueError("error envelope exceeds the review bound")
    payload = strict_json(raw.decode("utf-8"))
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return {
        "is_error": True,
        "structured_code": (
            code if isinstance(code, str) and len(code) <= 128 else None
        ),
        "shape_fingerprint": schema_fingerprint(
            shape_projection(payload)
        ),
    }


async def list_tools(
    session: ReviewedProtocolClientSession,
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _page in range(MAX_CATALOG_PAGES):
        result = await session.list_tools(cursor)
        tools.extend(
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in result.tools
        )
        if len(tools) > MAX_CATALOG_TOOLS:
            raise ValueError("catalog exceeds the review bound")
        cursor = result.nextCursor
        if not cursor:
            return sorted(tools, key=lambda item: item["name"])
        if cursor in seen:
            raise ValueError("catalog cursor repeated")
        seen.add(cursor)
    raise ValueError("catalog pagination exceeded the review bound")


async def capture(endpoint: str) -> dict[str, Any]:
    from mcp import types
    from mcp.client.streamable_http import streamablehttp_client

    from ha_mcp_engineering.mcp_sdk_compatibility import (
        ReviewedProtocolClientSession,
    )

    async with streamablehttp_client(endpoint) as (
        read_stream,
        write_stream,
        _session_id,
    ):
        async with ReviewedProtocolClientSession(
            read_stream,
            write_stream,
            client_info=types.Implementation(
                name="hass-mcp-engineering-upstream-review",
                version="1",
            ),
        ) as session:
            initialized = await session.initialize()
            tools = await list_tools(session)
            errors = {}
            by_name = {item["name"] for item in tools}
            for name, (tool, arguments) in ERROR_PROBES.items():
                if tool not in by_name:
                    errors[name] = {
                        "is_error": False,
                        "structured_code": None,
                        "shape_fingerprint": schema_fingerprint(
                            {"tool_absent": tool}
                        ),
                    }
                    continue
                errors[name] = error_evidence(
                    await session.call_tool(tool, arguments)
                )
            return {
                "capture_format_version": 1,
                "server_name": str(initialized.serverInfo.name),
                "server_version": str(initialized.serverInfo.version),
                "protocol_version": str(initialized.protocolVersion),
                "tool_count": len(tools),
                "catalog_fingerprint": catalog_fingerprint(tools),
                "tools": tools,
                "error_shapes": errors,
            }


def comparison(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    old_tools = {item["name"]: item for item in old["tools"]}
    new_tools = {item["name"]: item for item in new["tools"]}
    policy_by_name = (
        {
            item["upstream_name"]: item["classification"]
            for item in policy["tools"]
        }
        if policy is not None
        else {}
    )
    results = []
    for name in sorted(set(old_tools) | set(new_tools)):
        before = old_tools.get(name)
        after = new_tools.get(name)
        if before is None:
            status = "new_unreviewed"
        elif after is None:
            status = "removed"
        elif before == after:
            status = "unchanged_exact"
        else:
            input_changed = before.get("inputSchema") != after.get(
                "inputSchema"
            )
            description_changed = before.get("description") != after.get(
                "description"
            )
            annotation_changed = before.get("annotations") != after.get(
                "annotations"
            )
            output_changed = (
                ("outputSchema" in before) != ("outputSchema" in after)
                or before.get("outputSchema") != after.get("outputSchema")
            )
            other_changed = {
                key: value
                for key, value in before.items()
                if key
                not in {
                    "description",
                    "inputSchema",
                    "annotations",
                    "outputSchema",
                }
            } != {
                key: value
                for key, value in after.items()
                if key
                not in {
                    "description",
                    "inputSchema",
                    "annotations",
                    "outputSchema",
                }
            }
            status = (
                "metadata_only_review_required"
                if description_changed
                and not (
                    input_changed
                    or annotation_changed
                    or output_changed
                    or other_changed
                )
                else "classification_review_required"
            )
        results.append(
            {
                "tool": name,
                "classification": policy_by_name.get(name, "unreviewed"),
                "comparison": status,
                "input_schema_change": (
                    before is not None
                    and after is not None
                    and before.get("inputSchema")
                    != after.get("inputSchema")
                ),
                "description_change": (
                    before is not None
                    and after is not None
                    and before.get("description")
                    != after.get("description")
                ),
                "annotation_change": (
                    before is not None
                    and after is not None
                    and before.get("annotations")
                    != after.get("annotations")
                ),
                "output_contract_change": (
                    before is not None
                    and after is not None
                    and (
                        ("outputSchema" in before)
                        != ("outputSchema" in after)
                        or before.get("outputSchema")
                        != after.get("outputSchema")
                    )
                ),
                "runtime_contract_change": (
                    before is not None
                    and after is not None
                    and before != after
                ),
                "policy_classification_impact": (
                    "none"
                    if status == "unchanged_exact"
                    else "human_review_required"
                ),
                "delegation_impact": (
                    "none"
                    if status == "unchanged_exact"
                    else "quarantine_until_reviewed"
                ),
                "dashboard_provider_impact": (
                    "separate_attestation_review_required"
                    if name == "ha_config_get_dashboard"
                    and status != "unchanged_exact"
                    else "none"
                ),
            }
        )
    counts: dict[str, int] = {}
    for item in results:
        status = item["comparison"]
        counts[status] = counts.get(status, 0) + 1
    return {
        "comparison_format_version": 1,
        "old_version": old["server_version"],
        "new_version": new["server_version"],
        "old_tool_count": old["tool_count"],
        "new_tool_count": new["tool_count"],
        "old_catalog_fingerprint": old["catalog_fingerprint"],
        "new_catalog_fingerprint": new["catalog_fingerprint"],
        "classification_counts": dict(sorted(counts.items())),
        "tools": results,
    }


def candidate_entry(args: argparse.Namespace) -> None:
    capture_value = strict_load(args.capture)
    policy = strict_load(args.base_policy)
    if args.dashboard_status == "reviewed" and (
        not args.dashboard_entry_id
        or not args.dashboard_attestation_fingerprint
        or not args.dashboard_constraints_fingerprint
    ):
        raise SystemExit(
            "reviewed dashboard candidates require exact attestation and "
            "compiled-constraint fingerprints"
        )
    if args.dashboard_status == "quarantined" and any(
        (
            args.dashboard_entry_id,
            args.dashboard_attestation_fingerprint,
            args.dashboard_constraints_fingerprint,
        )
    ):
        raise SystemExit(
            "quarantined dashboard candidates cannot carry trusted evidence"
        )
    if capture_value["server_version"] != args.version:
        raise SystemExit("capture version does not match candidate version")
    if capture_value["server_name"] != "ha-mcp":
        raise SystemExit("capture server identity is not ha-mcp")
    policy = deepcopy(policy)
    policy["reviewed_upstream_version"] = args.version
    policy["reviewed_source_tag"] = f"v{args.version}"
    policy["reviewed_source_commit"] = args.source_commit
    policy["reviewed_stock_catalog_tool_count"] = capture_value[
        "tool_count"
    ]
    policy["reviewed_stock_catalog_fingerprint"] = capture_value[
        "catalog_fingerprint"
    ]
    captured_by_name = {
        item["name"]: item for item in capture_value["tools"]
    }
    policy_by_name = {
        item["upstream_name"]: item for item in policy["tools"]
    }
    if set(captured_by_name) != set(policy_by_name):
        raise SystemExit(
            "candidate catalog and base policy tool names differ; "
            "classify additions and removals before generation"
        )
    for name, item in policy_by_name.items():
        item["input_schema_fingerprint"] = schema_fingerprint(
            captured_by_name[name]["inputSchema"]
        )
    automatic_names = {
        name
        for name, item in policy_by_name.items()
        if item["classification"] == "automatic_read"
    }
    policy["reviewed_runtime_description_fingerprints"] = {
        name: (
            runtime_description_fingerprint(
                captured_by_name[name].get("description")
            )
            or ""
        )
        for name in sorted(automatic_names)
    }
    policy["reviewed_runtime_annotation_fingerprints"] = {
        name: (
            runtime_annotation_fingerprint(
                captured_by_name[name].get("annotations")
            )
            or ""
        )
        for name in sorted(automatic_names)
    }
    policy["reviewed_runtime_output_schema_fingerprints"] = {
        name: schema_fingerprint(
            captured_by_name[name].get("outputSchema")
        )
        for name in sorted(automatic_names)
    }
    write_json(args.output_policy, policy)
    policy_digest = (
        "sha256:"
        + hashlib.sha256(args.output_policy.read_bytes()).hexdigest()
    )
    reviewed_policy = load_upstream_tool_policy(
        args.output_policy,
        expected_version=args.version,
        expected_source_tag=f"v{args.version}",
        expected_source_commit=args.source_commit,
    )
    contracts = reviewed_tool_contracts_from_capture(
        capture_value, reviewed_policy
    )
    error_contract = schema_fingerprint(capture_value["error_shapes"])
    try:
        capture_resource = args.capture.resolve().relative_to(
            ROOT.resolve()
        ).as_posix()
    except ValueError as exc:
        raise SystemExit(
            "candidate capture must be committed below the repository root"
        ) from exc
    capture_digest = (
        "sha256:" + hashlib.sha256(args.capture.read_bytes()).hexdigest()
    )
    entry = {
        "entry_id": (
            f"ha-mcp-v{args.version}-"
            f"{args.image_index_digest.removeprefix('sha256:')[:8]}"
        ),
        "approval_status": "candidate_unapproved",
        "server_name": "ha-mcp",
        "version": args.version,
        "allowed_protocol_versions": ["2025-03-26"],
        "source_repository": "https://github.com/homeassistant-ai/ha-mcp",
        "release_tag": f"v{args.version}",
        "source_commit": args.source_commit,
        "image_index_digest": args.image_index_digest,
        "architecture_image_digests": {
            "linux/amd64": args.amd64_digest,
            "linux/arm64": args.arm64_digest,
        },
        "image_revision": args.image_revision,
        "advertised_tool_count": capture_value["tool_count"],
        "catalog_fingerprint": capture_value[
            "catalog_fingerprint"
        ],
        "capture_resource": capture_resource,
        "capture_sha256": capture_digest,
        "capture_format_version": capture_value[
            "capture_format_version"
        ],
        "policy_resource": args.output_policy.name,
        "policy_sha256": policy_digest,
        "review_provenance": [
            "Exact official image capture against the repository synthetic read-only fixture.",
            "Per-tool source and wire-contract review against ha-mcp 7.14.1.",
        ],
        "review_date": args.review_date,
        "dashboard_attestation": {
            "status": args.dashboard_status,
            "entry_id": args.dashboard_entry_id,
            "attestation_fingerprint": (
                args.dashboard_attestation_fingerprint
            ),
            "compiled_constraints_fingerprint": (
                args.dashboard_constraints_fingerprint
            ),
        },
        "error_contract_fingerprint": error_contract,
        "entity_lookup_missing_resource_status": (
            "ambiguous_upstream_service_call_failed"
            if capture_value["error_shapes"]
            .get("missing_registry_entity", {})
            .get("structured_code")
            == "SERVICE_CALL_FAILED"
            else "deterministic_entity_not_found"
        ),
        "tool_contracts": contracts,
    }
    write_json(args.output_entry, entry)


def report_markdown(value: dict[str, Any]) -> str:
    lines = [
        "# Reviewed upstream catalog comparison",
        "",
        f"- Old version: `{value['old_version']}`",
        f"- New version: `{value['new_version']}`",
        f"- Old tools: `{value['old_tool_count']}`",
        f"- New tools: `{value['new_tool_count']}`",
        f"- Old fingerprint: `{value['old_catalog_fingerprint']}`",
        f"- New fingerprint: `{value['new_catalog_fingerprint']}`",
        "",
        "| Tool | Comparison | Policy | Delegation impact |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| `{item['tool']}` | `{item['comparison']}` | "
        f"`{item['classification']}` | `{item['delegation_impact']}` |"
        for item in value["tools"]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    capture_command = commands.add_parser("capture")
    capture_command.add_argument("--endpoint", required=True)
    capture_command.add_argument("--output", type=Path, required=True)

    normalize = commands.add_parser("normalize")
    normalize.add_argument("--input", type=Path, required=True)
    normalize.add_argument("--output", type=Path, required=True)

    fingerprint = commands.add_parser("fingerprint")
    fingerprint.add_argument("--input", type=Path, required=True)

    diff = commands.add_parser("diff")
    diff.add_argument("--old", type=Path, required=True)
    diff.add_argument("--new", type=Path, required=True)
    diff.add_argument("--policy", type=Path)
    diff.add_argument("--output", type=Path, required=True)

    validate = commands.add_parser("validate")
    validate.add_argument(
        "--registry",
        type=Path,
        default=(
            BETA
            / "ha_mcp_engineering"
            / "upstream_release_registry.json"
        ),
    )
    validate.add_argument(
        "--repository-root",
        type=Path,
        default=ROOT,
    )

    generate = commands.add_parser("generate")
    generate.add_argument(
        "--registry",
        type=Path,
        default=(
            BETA
            / "ha_mcp_engineering"
            / "upstream_release_registry.json"
        ),
    )
    generate.add_argument(
        "--repository-root",
        type=Path,
        default=ROOT,
    )
    generate.add_argument("--output", type=Path, required=True)

    registry_diff = commands.add_parser("registry-diff")
    registry_diff.add_argument("--expected", type=Path, required=True)
    registry_diff.add_argument("--actual", type=Path, required=True)

    matrix = commands.add_parser("ci-matrix")
    matrix.add_argument(
        "--registry",
        type=Path,
        default=(
            BETA
            / "ha_mcp_engineering"
            / "upstream_release_registry.json"
        ),
    )

    report = commands.add_parser("report")
    report.add_argument("--comparison", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)

    candidate = commands.add_parser("candidate")
    candidate.add_argument("--capture", type=Path, required=True)
    candidate.add_argument("--base-policy", type=Path, required=True)
    candidate.add_argument("--version", required=True)
    candidate.add_argument("--source-commit", required=True)
    candidate.add_argument("--image-index-digest", required=True)
    candidate.add_argument("--amd64-digest", required=True)
    candidate.add_argument("--arm64-digest", required=True)
    candidate.add_argument("--image-revision", required=True)
    candidate.add_argument("--review-date", required=True)
    candidate.add_argument(
        "--dashboard-status",
        choices=("reviewed", "quarantined"),
        required=True,
    )
    candidate.add_argument("--dashboard-entry-id")
    candidate.add_argument("--dashboard-attestation-fingerprint")
    candidate.add_argument("--dashboard-constraints-fingerprint")
    candidate.add_argument("--output-policy", type=Path, required=True)
    candidate.add_argument("--output-entry", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "capture":
        write_json(args.output, asyncio.run(capture(args.endpoint)))
    elif args.command == "normalize":
        write_json(args.output, strict_load(args.input))
    elif args.command == "fingerprint":
        value = strict_load(args.input)
        print(
            json.dumps(
                {
                    "server_name": value["server_name"],
                    "server_version": value["server_version"],
                    "protocol_version": value["protocol_version"],
                    "tool_count": value["tool_count"],
                    "catalog_fingerprint": value[
                        "catalog_fingerprint"
                    ],
                    "artifact_fingerprint": hashlib.sha256(
                        canonical_json(value)
                    ).hexdigest(),
                },
                sort_keys=True,
            )
        )
    elif args.command == "diff":
        write_json(
            args.output,
            comparison(
                strict_load(args.old),
                strict_load(args.new),
                policy=(
                    strict_load(args.policy)
                    if args.policy is not None
                    else None
                ),
            ),
        )
    elif args.command == "validate":
        registry: ReviewedUpstreamReleaseRegistry = (
            validate_reviewed_release_evidence(
                args.registry,
                repository_root=args.repository_root,
            )
        )
        print(
            json.dumps(
                {
                    "registry_format_version": (
                        registry.registry_format_version
                    ),
                    "default_version": registry.default_version,
                    "supported_versions": list(
                        registry.supported_versions
                    ),
                    "release_count": len(registry.releases),
                },
                sort_keys=True,
            )
        )
    elif args.command == "generate":
        write_json(
            args.output,
            generated_reviewed_release_registry(
                args.registry,
                repository_root=args.repository_root,
            ),
        )
    elif args.command == "registry-diff":
        expected = canonical_json(strict_load(args.expected)) + b"\n"
        actual = canonical_json(strict_load(args.actual)) + b"\n"
        if expected != actual:
            print(
                "".join(
                    difflib.unified_diff(
                        expected.decode("utf-8").splitlines(True),
                        actual.decode("utf-8").splitlines(True),
                        fromfile=str(args.expected),
                        tofile=str(args.actual),
                    )
                ),
                end="",
            )
            raise SystemExit(1)
    elif args.command == "ci-matrix":
        registry = load_reviewed_upstream_release_registry(
            args.registry
        )
        print(
            json.dumps(
                {
                    "include": [
                        {
                            "upstream_version": release.version,
                            "upstream_image": (
                                "ghcr.io/homeassistant-ai/ha-mcp@"
                                f"{release.image_index_digest}"
                            ),
                            "image_index_digest": (
                                release.image_index_digest
                            ),
                            "image_revision": release.image_revision,
                            "architecture_image_digests": (
                                release
                                .architecture_image_digests_by_platform
                            ),
                        }
                        for release in registry.releases
                    ]
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    elif args.command == "report":
        args.output.write_text(
            report_markdown(strict_load(args.comparison)),
            encoding="utf-8",
        )
    elif args.command == "candidate":
        candidate_entry(args)
    else:
        raise SystemExit("unsupported command")


if __name__ == "__main__":
    main()
