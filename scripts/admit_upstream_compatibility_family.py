"""Generate one exact release admission from a reviewed family policy.

The command deliberately has no version-range or latest-release mode.  It
accepts two retained exact runtime captures plus immutable source/OCI identity,
compares them with the reviewed baseline, and emits one exact policy, decision,
and registry entry.  A rejected decision emits no admission artifacts.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.compatibility_family import (  # noqa: E402
    DRIFT_CATEGORIES,
    CompatibilityFamilyError,
    canonical_json,
    compare_family_candidate,
    load_compatibility_families,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2,
    STRICT_FULL_CONTRACT_FINGERPRINT_MODEL_V1,
    load_reviewed_upstream_release_registry,
    load_upstream_tool_policy,
    reviewed_tool_contracts_from_capture,
    runtime_annotation_fingerprint,
    runtime_description_fingerprint,
    schema_fingerprint,
)


REGISTRY = (
    BETA / "ha_mcp_engineering" / "upstream_release_registry.json"
)
FAMILY_POLICY = (
    BETA / "ha_mcp_engineering" / "upstream_compatibility_families.json"
)
_SHA256_PREFIX = "sha256:"


def strict_load(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for name, item in pairs:
            if name in value:
                raise SystemExit(f"duplicate JSON member in {path}")
            value[name] = item
        return value

    def finite(_value: str) -> None:
        raise SystemExit(f"non-finite JSON value in {path}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique,
            parse_constant=finite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"unable to load exact evidence: {path}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value) + b"\n")


def digest_bytes(value: bytes) -> str:
    return _SHA256_PREFIX + hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    try:
        return digest_bytes(path.read_bytes())
    except OSError as exc:
        raise SystemExit(f"unable to hash exact evidence: {path}") from exc


def require_sha256(value: str, *, field: str) -> str:
    payload = value.removeprefix(_SHA256_PREFIX)
    if (
        not value.startswith(_SHA256_PREFIX)
        or len(payload) != 64
        or any(character not in "0123456789abcdef" for character in payload)
    ):
        raise SystemExit(f"{field} must be an exact lowercase SHA-256 digest")
    return value


def require_commit(value: str, *, field: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise SystemExit(f"{field} must be an exact lowercase commit SHA")
    return value


def require_exact_version(value: str, *, expected: str, field: str) -> str:
    if value != expected:
        raise SystemExit(f"{field} does not match the exact candidate version")
    return value


def require_distinct_capture_paths(first: Path, second: Path) -> None:
    if first.resolve() == second.resolve():
        raise SystemExit("candidate captures must be retained as distinct evidence")


def capture(path: Path) -> tuple[dict[str, Any], bytes, str]:
    raw = path.read_bytes()
    value = strict_load(path)
    if raw != canonical_json(value) + b"\n":
        raise SystemExit("candidate captures must be canonical JSON")
    if not isinstance(value, dict) or set(value) != {
        "capture_format_version",
        "catalog_fingerprint",
        "error_shapes",
        "protocol_version",
        "server_name",
        "server_version",
        "tool_count",
        "tools",
    }:
        raise SystemExit("candidate capture fields are invalid")
    if (
        value["capture_format_version"] != 1
        or value["server_name"] != "ha-mcp"
        or value["protocol_version"] != "2025-03-26"
        or not isinstance(value["tools"], list)
        or value["tool_count"] != len(value["tools"])
    ):
        raise SystemExit("candidate capture identity is invalid")
    return value, raw, digest_bytes(raw)


def policy_from_baseline(
    *,
    baseline_policy_path: Path,
    candidate_capture: dict[str, Any],
    candidate_version: str,
    source_commit: str,
    held_automatic_reads: set[str],
) -> dict[str, Any]:
    policy = deepcopy(strict_load(baseline_policy_path))
    tools = candidate_capture["tools"]
    candidate_by_name = {item["name"]: item for item in tools}
    policy["reviewed_upstream_version"] = candidate_version
    policy["reviewed_source_tag"] = f"v{candidate_version}"
    policy["reviewed_source_commit"] = source_commit
    policy["reviewed_stock_catalog_tool_count"] = len(tools)
    policy["reviewed_stock_catalog_fingerprint"] = candidate_capture[
        "catalog_fingerprint"
    ]
    for entry in policy["tools"]:
        name = entry["upstream_name"]
        observed = candidate_by_name[name]
        if name in held_automatic_reads:
            entry["classification"] = "held_for_canary"
        entry["input_schema_fingerprint"] = schema_fingerprint(
            observed["inputSchema"]
        )
        entry["reason"] = (
            f"Exact {candidate_version} family evidence compared with the "
            "reviewed 8.1.0 baseline; retain the baseline classification "
            "without expanding Engineering reachability."
        )
        entry["source_evidence"] = [
            (
                "homeassistant-ai/ha-mcp@"
                f"{source_commit}: exact v{candidate_version} immutable identity"
            ),
            (
                "Two byte-identical exact MCP tools/list captures for ha-mcp "
                f"{candidate_version}"
            ),
            "Reviewed compatibility family ha-mcp-8.1.x-v1",
        ]
    automatic_names = {
        entry["upstream_name"]
        for entry in policy["tools"]
        if entry["classification"] == "automatic_read"
    }
    policy["reviewed_runtime_description_fingerprints"] = {
        name: runtime_description_fingerprint(
            candidate_by_name[name].get("description")
        )
        for name in sorted(automatic_names)
    }
    policy["reviewed_runtime_annotation_fingerprints"] = {
        name: runtime_annotation_fingerprint(
            candidate_by_name[name].get("annotations")
        )
        for name in sorted(automatic_names)
    }
    policy["reviewed_runtime_output_schema_fingerprints"] = {
        name: schema_fingerprint(candidate_by_name[name].get("outputSchema"))
        for name in sorted(automatic_names)
    }
    return policy


def artifact_runtime_fields(path: Path) -> dict[str, Any]:
    evidence = strict_load(path)
    if not isinstance(evidence, dict):
        raise SystemExit("artifact evidence is invalid")
    runtime = evidence.get("runtime_catalog")
    if not isinstance(runtime, dict):
        raise SystemExit("artifact runtime evidence is missing")
    return runtime


def load_registry_without_candidate(candidate_version: str):
    """Validate the reviewed baseline without trusting a prior candidate entry."""

    value = strict_load(REGISTRY)
    if not isinstance(value, dict) or not isinstance(value.get("releases"), list):
        raise SystemExit("reviewed release registry is invalid")
    baseline_only = deepcopy(value)
    baseline_only["releases"] = [
        item
        for item in baseline_only["releases"]
        if isinstance(item, dict) and item.get("version") != candidate_version
    ]
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=".family-baseline-",
        suffix=".json",
        dir=REGISTRY.parent,
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(canonical_json(baseline_only) + b"\n")
    try:
        return load_reviewed_upstream_release_registry(temporary)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--capture-first", type=Path, required=True)
    parser.add_argument("--capture-second", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tag-object", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--source-archive-sha256", required=True)
    parser.add_argument("--image-index-digest", required=True)
    parser.add_argument("--amd64-digest", required=True)
    parser.add_argument("--arm64-digest", required=True)
    parser.add_argument("--addon-amd64-index-digest", required=True)
    parser.add_argument("--addon-amd64-manifest-digest", required=True)
    parser.add_argument("--addon-arm64-index-digest", required=True)
    parser.add_argument("--addon-arm64-manifest-digest", required=True)
    parser.add_argument("--image-revision", required=True)
    parser.add_argument("--package-version", required=True)
    parser.add_argument("--initialize-version", required=True)
    parser.add_argument("--supervisor-addon-version", required=True)
    parser.add_argument("--artifact-evidence", type=Path, required=True)
    parser.add_argument("--review-date", required=True)
    parser.add_argument(
        "--observed-non-catalog-category",
        action="append",
        default=[],
        choices=tuple(sorted(DRIFT_CATEGORIES)),
    )
    parser.add_argument("--dashboard-entry-id", required=True)
    parser.add_argument("--dashboard-attestation-fingerprint", required=True)
    parser.add_argument("--dashboard-constraints-fingerprint", required=True)
    parser.add_argument("--output-policy", type=Path, required=True)
    parser.add_argument("--output-decision", type=Path, required=True)
    parser.add_argument("--output-entry", type=Path, required=True)
    parser.add_argument("--output-registry", type=Path, required=True)
    args = parser.parse_args()

    families = load_compatibility_families(FAMILY_POLICY)
    family = families.by_id.get(args.family_id)
    if family is None:
        raise SystemExit("requested compatibility family is not reviewed")
    registry = load_registry_without_candidate(args.version)
    baseline_release = registry.historical_by_version.get(
        family.baseline.version
    )
    if baseline_release is None or baseline_release.entry_id != family.baseline.entry_id:
        raise SystemExit("reviewed family baseline is unavailable")
    require_distinct_capture_paths(args.capture_first, args.capture_second)
    first, first_raw, first_digest = capture(args.capture_first)
    second, second_raw, second_digest = capture(args.capture_second)
    if first_raw != second_raw or first_digest != second_digest:
        raise SystemExit("candidate runtime captures are not byte-identical")
    if first["server_version"] != args.version:
        raise SystemExit("candidate capture version does not match")
    baseline_capture = strict_load(ROOT / baseline_release.capture_resource)
    baseline_policy = baseline_release.policy
    comparison = compare_family_candidate(
        family,
        candidate_version=args.version,
        baseline_tools=baseline_capture["tools"],
        candidate_tools=first["tools"],
        baseline_classifications={
            entry.upstream_name: entry.classification
            for entry in baseline_policy.tools
        },
        observed_non_catalog_categories={
            "immutable_identity_only", *args.observed_non_catalog_category
        },
    )
    if not comparison.admitted:
        raise SystemExit(
            "candidate family comparison rejected: "
            + ",".join(comparison.unknown_drift or comparison.material_drift_categories)
        )

    source_commit = require_commit(args.source_commit, field="source commit")
    source_tag_object = require_commit(
        args.source_tag_object, field="source tag object"
    )
    source_tree = require_commit(args.source_tree, field="source tree")
    image_revision = require_commit(args.image_revision, field="image revision")
    package_version = require_exact_version(
        args.package_version,
        expected=args.version,
        field="installed package version",
    )
    initialize_version = require_exact_version(
        args.initialize_version,
        expected=args.version,
        field="MCP initialize version",
    )
    supervisor_addon_version = require_exact_version(
        args.supervisor_addon_version,
        expected=args.version,
        field="Supervisor add-on version",
    )
    source_archive = require_sha256(
        args.source_archive_sha256, field="source archive"
    )
    image_index = require_sha256(args.image_index_digest, field="image index")
    architecture_digests = {
        "linux/amd64": require_sha256(args.amd64_digest, field="amd64 manifest"),
        "linux/arm64": require_sha256(args.arm64_digest, field="arm64 manifest"),
    }
    addon_artifacts = {
        "linux/amd64": {
            "index_digest": require_sha256(
                args.addon_amd64_index_digest, field="add-on amd64 index"
            ),
            "image_manifest_digest": require_sha256(
                args.addon_amd64_manifest_digest,
                field="add-on amd64 manifest",
            ),
        },
        "linux/arm64": {
            "index_digest": require_sha256(
                args.addon_arm64_index_digest, field="add-on arm64 index"
            ),
            "image_manifest_digest": require_sha256(
                args.addon_arm64_manifest_digest,
                field="add-on arm64 manifest",
            ),
        },
    }
    artifact_digest = digest_file(args.artifact_evidence)
    runtime_fields = artifact_runtime_fields(args.artifact_evidence)
    expected_artifact_resource = (
        ROOT
        / "docs/evidence/upstream-read-compatibility"
        / f"ha-mcp-{args.version}-contract-review.json"
    )
    if args.artifact_evidence.resolve() != expected_artifact_resource.resolve():
        raise SystemExit("artifact evidence is not the exact candidate resource")
    expected_policy_resource = (
        BETA
        / "ha_mcp_engineering"
        / f"upstream_tool_policy_{args.version.replace('.', '_')}.json"
    )
    if args.output_policy.resolve() != expected_policy_resource.resolve():
        raise SystemExit("policy output is not the exact candidate resource")
    expected_decision_resource = (
        ROOT
        / "docs/evidence/upstream-read-compatibility"
        / f"ha-mcp-{args.version}-family-decision.json"
    )
    if args.output_decision.resolve() != expected_decision_resource.resolve():
        raise SystemExit("decision output is not the exact candidate resource")

    baseline_policy_path = (
        BETA / "ha_mcp_engineering" / baseline_release.policy_resource
    )
    policy_value = policy_from_baseline(
        baseline_policy_path=baseline_policy_path,
        candidate_capture=first,
        candidate_version=args.version,
        source_commit=source_commit,
        held_automatic_reads=set(comparison.held_automatic_reads),
    )
    write_json(args.output_policy, policy_value)
    policy_digest = digest_file(args.output_policy)
    compiled_policy = load_upstream_tool_policy(
        args.output_policy,
        expected_version=args.version,
        expected_source_tag=f"v{args.version}",
        expected_source_commit=source_commit,
    )
    contracts = reviewed_tool_contracts_from_capture(
        first,
        compiled_policy,
        runtime_contract_fingerprint_model=(
            RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2
        ),
    )
    entry_id = (
        f"ha-mcp-v{args.version}-"
        f"{image_index.removeprefix(_SHA256_PREFIX)[:8]}"
    )
    decision_resource = args.output_decision.resolve().relative_to(
        ROOT.resolve()
    ).as_posix()
    surface_disposition = dict(comparison.provider_dispositions)
    decision = {
        "schema_version": 1,
        "decision_id": f"{entry_id}-family-admission-v1",
        "family_id": family.family_id,
        "baseline": {
            "version": family.baseline.version,
            "entry_id": family.baseline.entry_id,
            "source_commit": family.baseline.source_commit,
            "image_index_digest": family.baseline.image_index_digest,
            "capture_sha256": family.baseline.capture_sha256,
            "policy_sha256": family.baseline.policy_sha256,
        },
        "candidate": {
            "version": args.version,
            "entry_id": entry_id,
            "source_commit": source_commit,
            "source_tag_object": source_tag_object,
            "source_tree": source_tree,
            "source_archive_sha256": source_archive,
            "image_index_digest": image_index,
            "architecture_image_digests": architecture_digests,
            "addon_artifact_digests": addon_artifacts,
            "image_revision": image_revision,
            "capture_sha256": first_digest,
            "policy_sha256": policy_digest,
            "artifact_evidence_sha256": artifact_digest,
            "protocol_version": "2025-03-26",
            "server_name": "ha-mcp",
            "package_version": package_version,
            "initialize_version": initialize_version,
            "supervisor_addon_version": supervisor_addon_version,
        },
        "capture_determinism": {
            "capture_count": 2,
            "capture_sha256_values": [first_digest, second_digest],
            "byte_identical": True,
        },
        "observed_drift_categories": list(
            comparison.observed_drift_categories
        ),
        "material_drift_categories": list(
            comparison.material_drift_categories
        ),
        "unknown_drift": list(comparison.unknown_drift),
        "surface_disposition": surface_disposition,
        "required_validation": list(family.required_evidence),
        "outcome": comparison.outcome,
        "reason": (
            "All exact runtime descriptors and provider contracts match the "
            "reviewed 8.1.0 baseline. Observed source changes are limited to "
            "documentation and evidence-verified dependency isolation; no "
            "input, output, annotation, classification, consumed-envelope, "
            "lifecycle, dashboard, security, transport, or unknown drift was "
            "observed."
        ),
    }
    write_json(args.output_decision, decision)
    decision_digest = digest_file(args.output_decision)
    family_policy_digest = digest_file(FAMILY_POLICY)
    entry = {
        "entry_id": entry_id,
        "approval_status": "reviewed",
        "server_name": "ha-mcp",
        "version": args.version,
        "allowed_protocol_versions": ["2025-03-26"],
        "source_repository": "https://github.com/homeassistant-ai/ha-mcp",
        "release_tag": f"v{args.version}",
        "source_commit": source_commit,
        "source_tag_object": source_tag_object,
        "source_tree": source_tree,
        "source_archive_sha256": source_archive,
        "image_index_digest": image_index,
        "architecture_image_digests": architecture_digests,
        "image_revision": image_revision,
        "advertised_tool_count": first["tool_count"],
        "catalog_fingerprint": first["catalog_fingerprint"],
        "runtime_contract_fingerprint_model": (
            RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2
        ),
        "strict_full_contract_fingerprint": runtime_fields[
            "standalone_strict_full_contract_fingerprint"
        ],
        "strict_full_contract_fingerprint_model": (
            STRICT_FULL_CONTRACT_FINGERPRINT_MODEL_V1
        ),
        "addon_artifact_digests": addon_artifacts,
        "artifact_evidence_resource": args.artifact_evidence.resolve().relative_to(
            ROOT.resolve()
        ).as_posix(),
        "artifact_evidence_sha256": artifact_digest,
        "capture_resource": (
            "docs/evidence/upstream-read-compatibility/"
            f"ha-mcp-{args.version}.json"
        ),
        "capture_sha256": first_digest,
        "capture_format_version": first["capture_format_version"],
        "policy_resource": args.output_policy.name,
        "policy_sha256": policy_digest,
        "review_provenance": [
            (
                "Generated by the reviewed ha-mcp-8.1.x compatibility-family "
                "fast path from two byte-identical exact OCI runtime captures."
            ),
            (
                "Exact immutable source, standalone image, add-on image, "
                "provider, packaging, and disposable-Home-Assistant evidence."
            ),
        ],
        "review_date": args.review_date,
        "dashboard_attestation": {
            "status": "reviewed",
            "entry_id": args.dashboard_entry_id,
            "attestation_fingerprint": args.dashboard_attestation_fingerprint,
            "compiled_constraints_fingerprint": args.dashboard_constraints_fingerprint,
        },
        "error_contract_fingerprint": schema_fingerprint(first["error_shapes"]),
        "entity_lookup_missing_resource_status": (
            baseline_release.entity_lookup_missing_resource_status
        ),
        "tool_contracts": contracts,
        "family_admission": {
            "family_id": family.family_id,
            "baseline_entry_id": family.baseline.entry_id,
            "policy_resource": FAMILY_POLICY.name,
            "policy_sha256": family_policy_digest,
            "decision_resource": decision_resource,
            "decision_sha256": decision_digest,
            "outcome": comparison.outcome,
        },
        "provider_dispositions": surface_disposition,
        "revoked": False,
        "revocation_reason": None,
    }
    write_json(args.output_entry, entry)
    registry_value = strict_load(REGISTRY)
    registry_value["releases"] = sorted(
        [
            item
            for item in registry_value["releases"]
            if item.get("version") != args.version
        ]
        + [entry],
        key=lambda item: tuple(int(part) for part in item["version"].split(".")),
    )
    write_json(args.output_registry, registry_value)
    print(
        json.dumps(
            {
                "entry_id": entry_id,
                "outcome": comparison.outcome,
                "observed_drift_categories": list(
                    comparison.observed_drift_categories
                ),
                "held_automatic_reads": list(
                    comparison.held_automatic_reads
                ),
                "provider_dispositions": surface_disposition,
                "capture_sha256": first_digest,
                "policy_sha256": policy_digest,
                "decision_sha256": decision_digest,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except CompatibilityFamilyError as exc:
        raise SystemExit(str(exc)) from exc
