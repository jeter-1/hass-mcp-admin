#!/usr/bin/env python3
"""HAMCP-319 section 5.1 case runners for the 91_only and side_by_side lanes.

Each subcommand observes a real Engineering container (and, where the case
needs it, the real ha-mcp peer's request log and the synthetic Home Assistant
fixture's counters) and writes exactly one bounded case result through the
lane module's ``case_result``. A result is ``passed`` only when every
assertion for that case was observed; any missing observation, transport
error or unexpected value writes ``failed`` and exits nonzero. No subcommand
ever writes ``skipped``.

All traffic is loopback. The only secrets are the synthetic values the lane
workflow already uses.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from exact_image_admission_lanes import (  # noqa: E402
    CASES,
    DIGEST,
    LaneFailure,
    Runner,
    bounded,
    case_form,
    case_names,
    case_result,
    classify_form,
    image_identity,
    require,
    run_command,
    write_json,
)

CANDIDATE_VERSION = "9.1.0"
BASELINE_VERSION = "8.5.0"
# The peer is launched with MCP_SECRET_PATH set to this value. The 9.1
# full-catalog selector is that whole path followed by /catalog/full; the
# shorter /synthetic-upstream-secret/catalog/full is not served by 9.1.
PEER_SECRET_PATH = "/synthetic-upstream-secret/mcp"
STRICT_FULL_PATH = PEER_SECRET_PATH + "/catalog/full"
PEER_HOSTS = frozenset({"abcdef12-ha-mcp:18086", "127.0.0.1:18086"})
CATALOG_MODE_UNADMITTED = "upstream_catalog_mode_unadmitted"
VERSION_MISMATCH = "upstream_version_mismatch"
PROBE_TOOL = "ha_get_state"
PROBE_ARGUMENTS = {"entity_id": "sun.sun"}
HEALTH_TOOL = "get_server_health"
HEALTH_ERROR_FIELDS = ("error", "error_code", "errors")
# Every ha-mcp tool name, and so every delegated read Engineering can expose,
# starts with this prefix; no Engineering-native tool does (2.4.2-beta.6 and
# the lane build). A listed name with it is part of the delegated surface.
DELEGATED_TOOL_PREFIX = "ha_"
# 2.4.2-beta.6 refuses a delegated read it does not publish before Core
# acquisition, provider dispatch or any upstream or Home Assistant request
# (routing.py at ROLLBACK_ENGINEERING_REVISION): an MCP error whose content is
# this structured refusal.
ROLLBACK_REFUSAL = {"error": "CoreRequirementUnmapped", "error_code": "unsupported_operation",
                    "reason_code": "core_requirement_unmapped"}
YAML_READER = "ha_config_get_yaml"
YAML_SIBLINGS = ("ha_delete_file", "ha_list_files", "ha_read_file", "ha_write_file")
OPTIONAL_NAMES = (YAML_READER, *YAML_SIBLINGS)
# Josh, 2026-11-07 (ruling 3): the strict 9.1 check compares every reviewed
# tool exactly and requires exact set equality with no duplicates. The runtime
# compares only its automatic-read routes per tool and tolerates unreviewed
# extras, so the case also lists the peer's raw strict-full catalog and
# compares it with the reviewed capture the runtime's fingerprint is bound to.
REVIEWED_91_CAPTURE = (Path(__file__).resolve().parent.parent / "docs" / "evidence"
                       / "upstream-read-compatibility" / "ha-mcp-9.1.0.json")
DESCRIPTOR_FIELDS = ("name", "title", "description", "inputSchema", "outputSchema", "annotations", "_meta")
MAX_PEER_CATALOG_PAGES = 32
FIXTURE_READ_KEYS = ("rest_reads", "websocket_reads")
FIXTURE_MUTATION_KEYS = ("http_mutations", "websocket_mutations")
# Engineering rollback stage (architect ruling 2026-11-07): the actual
# previous release, 2.4.2-beta.6, by its published digest. Provenance: the
# annotated tag v2.4.2-beta.6 (source af5ec837...), its GitHub Release
# "Immutable publication identity" section, and the registry index read
# read-only (OCI index, per-architecture manifests, OCI labels). Never a
# local build and never a mutable tag.
ROLLBACK_ENGINEERING_VERSION = "2.4.2-beta.6"
ROLLBACK_ENGINEERING_REPOSITORY = "ghcr.io/jeter-1/hass-mcp-engineering-beta"
ROLLBACK_ENGINEERING_INDEX_DIGEST = "sha256:9284290132d1d4f06195f5ee5bb27797238b86d8a250db13c6c78f2171059f6a"
ROLLBACK_ENGINEERING_IMAGE = f"{ROLLBACK_ENGINEERING_REPOSITORY}@{ROLLBACK_ENGINEERING_INDEX_DIGEST}"
ROLLBACK_ENGINEERING_REVISION = "af5ec8375401276f910804843c2ddb9180533c77"
ROLLBACK_ENGINEERING_PLATFORM_DIGESTS = {
    "amd64": "sha256:ca34ccae1fc9bb1b7d5809a82c0397862ce819bee28decdfa824eb908f2cad5c",
    "arm64": "sha256:b320edc4dfe9cc6f7534fcf01f1ed793a2f3ddfec46bd3466a22eca381e49dfb",
}
ROLLBACK_CASES = frozenset({"sbs_rollback_85_legacy_useful", "sbs_engineering_rollback_91_refused"})
LANE_ENGINEERING_IMAGE = "engineering-admission-lane:test"
ENGINEERING_IMAGES = frozenset({LANE_ENGINEERING_IMAGE, ROLLBACK_ENGINEERING_IMAGE})
PEER_CONTAINER = "exact-admission-upstream"
PEER_PORT = 18086
PEER_READY_URL = f"http://127.0.0.1:{PEER_PORT}{PEER_SECRET_PATH}"
FIXTURE_HA_URL = "http://127.0.0.1:18123"
SYNTHETIC_HA_TOKEN = "synthetic-read-gateway-token"
ENGINEERING_OPTIONS = {
    "access_secret": "synthetic-engineering-access-secret",
    "audit_enabled": True,
    "prewarm_enabled": False,
    "rate_limit_per_minute": 600,
    "rate_limit_burst": 100,
    "approval_notification_service": "notify.mobile_app_beta31_fixture",
    "upstream_trust_registry_enabled": False,
}
ENGINEERING_CONTAINER = re.compile(r"engineering-admission-[a-z0-9-]{1,40}")
PEER_REQUEST = re.compile(r'"POST (?P<path>/[^ "]*) HTTP/1\.[01]" (?P<status>\d{3})')

EXPECTATIONS = {
    "admitted_strict_91": ("91_only", "91_strict_admission_positive"),
    "catalog_mode_unadmitted_at_startup": ("91_only", "91_legacy_form_refused_before_list"),
    "catalog_mode_unadmitted_after_peer_change": ("side_by_side", "sbs_stage_b1_91_legacy_refused"),
    "unsupported_version_rollback": ("side_by_side", "sbs_engineering_rollback_91_refused"),
}
GATEWAY_CASES = {
    ("91_only", "91_parity_read_gateway_acceptance"): (CANDIDATE_VERSION, "admitted"),
    ("91_only", "91_filesystem_disabled_variant"): (CANDIDATE_VERSION, "absent"),
    ("side_by_side", "sbs_stage_a_85_legacy_useful"): (BASELINE_VERSION, None),
    ("side_by_side", "sbs_stage_b2_91_strict_full_useful"): (CANDIDATE_VERSION, "admitted"),
    ("side_by_side", "sbs_rollback_85_legacy_useful"): (BASELINE_VERSION, None),
    ("side_by_side", "sbs_stage_a_again_85_legacy_useful"): (BASELINE_VERSION, None),
}
# Roll-forward from the previous release (Josh, 2026-11-07): stage A again
# restarts the lane image on the SAME persisted data the beta.6 stages ran on,
# with no state reset, and fails closed unless everything beta.6 wrote or left
# (audit records, change plans, the trace store) is still present and
# readable by the restarted runtime.
ROLL_FORWARD_CASE = "sbs_stage_a_again_85_legacy_useful"
PERSISTED_ROOT = "/data"
PERSISTED_AUDIT = "audit.jsonl"
PERSISTED_PLAN_STORE = "governance/change_plans"
PERSISTED_TRACE_STORE = PERSISTED_PLAN_STORE + "/trace-snapshots-v1"
PERSISTED_TRACE_RECORDS = PERSISTED_TRACE_STORE + "/records"
PERSISTED_PLAN_FILE = r"governance/change_plans/[0-9a-f]{32}\.json"
# A plan's identity cannot change; its lifecycle (status, approval, events)
# legitimately moves on, for example to superseded when a newer plan for the
# same target is created by the restarted runtime.
PERSISTED_PLAN_IDENTITY = ("plan_id", "created_at", "operation", "target", "proposed_config_hash")
PERSISTED_ENTRY_LIMIT = 10_000
# Runs inside the Engineering container (python3 -I -c), so "readable" means
# readable by that runtime's own user and interpreter. Prints one JSON object.
INVENTORY_SCRIPT = r"""
import hashlib, json, os, re, sys
config = json.loads(sys.argv[1])
root, prefix = config["root"], config["prefix"]
plan_file = re.compile(config["plan_file"])
entries, walk_errors = {}, []
for current, dirs, files in os.walk(root, onerror=lambda error: walk_errors.append(error)):
    dirs.sort()
    rel = os.path.relpath(current, root)
    if rel != ".":
        entries[rel] = {"kind": "dir", "listable": os.access(current, os.R_OK | os.X_OK)}
    for name in sorted(files):
        path = os.path.join(current, name)
        key = os.path.relpath(path, root)
        entry = {"kind": "file"}
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            entry["size"] = len(data)
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            if key in prefix:
                head = data[:prefix[key]] if len(data) >= prefix[key] else None
                entry["prefix_sha256"] = None if head is None else hashlib.sha256(head).hexdigest()
            if key.endswith(".jsonl"):
                region = data[:prefix[key]] if key in prefix else data
                lines = [line for line in region.split(b"\n") if line.strip()]
                for line in lines:
                    if not isinstance(json.loads(line), dict):
                        raise ValueError("record_not_object")
                entry["records"] = len(lines)
            elif key.endswith(".json"):
                value = json.loads(data)
                if plan_file.fullmatch(key):
                    if not isinstance(value, dict):
                        raise ValueError("plan_not_object")
                    identity = {field: value.get(field) for field in config["plan_identity"]}
                    entry["plan"] = {
                        "plan_id": value.get("plan_id"),
                        "status": value.get("status"),
                        "identity_sha256": hashlib.sha256(
                            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                    }
        except Exception as error:
            entry["error"] = type(error).__name__
        entries[key] = entry
if walk_errors:
    entries["<walk>"] = {"kind": "dir", "listable": False, "error": type(walk_errors[0]).__name__}
print(json.dumps(entries, sort_keys=True))
"""


# --------------------------------------------------------------------------
# Pure checks
# --------------------------------------------------------------------------

def endpoint_problem(url: str, form: str) -> str | None:
    """Return a fixed category when ``url`` is not the exact lane value for ``form``.

    ``strict_full`` must be ``<peer>/synthetic-upstream-secret/mcp/catalog/full``;
    ``legacy`` must be ``<peer>/synthetic-upstream-secret/mcp``. Both must name
    the loopback peer and carry no query, fragment or encoding.
    """

    try:
        parts = urlsplit(url)
    except ValueError:
        return "endpoint_unparseable"
    if parts.scheme != "http" or parts.netloc not in PEER_HOSTS:
        return "endpoint_not_loopback_peer"
    if parts.query or parts.fragment or "%" in parts.path:
        return "endpoint_not_canonical"
    if classify_form(url) != form:
        return "endpoint_form_mismatch"
    expected = STRICT_FULL_PATH if form == "strict_full" else PEER_SECRET_PATH
    if parts.path != expected:
        return "endpoint_path_not_lane_value"
    return None


def fixture_totals(stats: dict[str, Any]) -> dict[str, int]:
    totals = {}
    for key in (*FIXTURE_READ_KEYS, *FIXTURE_MUTATION_KEYS):
        value = stats.get(key)
        require(isinstance(value, dict), "fixture_stats_malformed")
        require(all(isinstance(item, int) for item in value.values()), "fixture_stats_malformed")
        totals[key] = sum(value.values())
    return totals


def parse_peer_requests(log_text: str) -> list[tuple[str, int]]:
    return [(match["path"], int(match["status"])) for match in PEER_REQUEST.finditer(log_text)]


def gateway_view(health: dict[str, Any]) -> dict[str, Any]:
    data = health.get("data") if isinstance(health.get("data"), dict) else health
    view = data.get("upstream_read_gateway")
    require(isinstance(view, dict), "health_gateway_view_missing")
    return view


def health_problem(health: Any) -> str | None:
    """A health observation counts only as a present, successful, error-free ``get_server_health`` result."""

    if not isinstance(health, dict):
        return "health_missing"
    if health.get("success") is not True:
        return "health_not_successful"
    if any(health.get(key) not in (None, "", [], {}) for key in HEALTH_ERROR_FIELDS):
        return "health_reports_error"
    if health.get("operation") != HEALTH_TOOL:
        return "health_operation_unexpected"
    data = health.get("data")
    if not (isinstance(data, dict) and isinstance(data.get("server"), dict)
            and isinstance(data.get("upstream_read_gateway"), dict)):
        return "health_data_missing"
    return None


def delegated_surface(tool_names: Any) -> list[str] | None:
    """The delegated tools among Engineering's listed tools; ``None`` when the list was not observed."""

    if not isinstance(tool_names, (list, tuple)) or not all(isinstance(name, str) for name in tool_names):
        return None
    if HEALTH_TOOL not in tool_names:
        return None
    return sorted(name for name in tool_names if name.startswith(DELEGATED_TOOL_PREFIX))


def rollback_refusal_problem(probe: Any) -> str | None:
    """The previous release's own refusal of the probe read, with nothing dispatched anywhere."""

    details = probe.get("details") if isinstance(probe, dict) and isinstance(probe.get("details"), dict) else {}
    if (not isinstance(probe, dict) or probe.get("success") is not False or probe.get("mcp_is_error") is not True
            or probe.get("error") != ROLLBACK_REFUSAL["error"]
            or probe.get("error_code") != ROLLBACK_REFUSAL["error_code"]
            or details.get("reason_code") != ROLLBACK_REFUSAL["reason_code"]
            or details.get("core_authority_acquired") is not False
            or details.get("provider_dispatch_occurred") is not False
            or details.get("upstream_attempted") is not False
            or details.get("upstream_request_count") != 0
            or details.get("home_assistant_request_count") != 0):
        return "rollback_refusal_not_attributable"
    return None


def engineering_version_problem(name: str, health: dict[str, Any], expected: str) -> str | None:
    """Rollback cases must run the previous release; every other case the lane build."""

    if name in ROLLBACK_CASES and expected != ROLLBACK_ENGINEERING_VERSION:
        return "rollback_case_not_previous_release"
    if name not in ROLLBACK_CASES and expected == ROLLBACK_ENGINEERING_VERSION:
        return "previous_release_outside_rollback_case"
    data = health.get("data") if isinstance(health.get("data"), dict) else health
    server = data.get("server") if isinstance(data.get("server"), dict) else {}
    if server.get("version") != expected:
        return "engineering_version_mismatch"
    return None


def engineering_image_problem(name: str, container_image: str | None) -> str | None:
    """The observed container must run the image its case names, by exact reference."""

    expected = ROLLBACK_ENGINEERING_IMAGE if name in ROLLBACK_CASES else LANE_ENGINEERING_IMAGE
    if container_image != expected:
        return "engineering_image_not_case_image"
    return None


def rollback_identity_problem(identity: dict[str, Any] | None) -> str | None:
    """A rollback case counts only after the previous release's identity verified on this runner."""

    if not isinstance(identity, dict) or identity.get("status") != "passed":
        return "rollback_identity_not_verified"
    architecture = identity.get("architecture")
    if (identity.get("image") != ROLLBACK_ENGINEERING_IMAGE
            or identity.get("image_digest") != ROLLBACK_ENGINEERING_INDEX_DIGEST
            or identity.get("version") != ROLLBACK_ENGINEERING_VERSION
            or identity.get("revision") != ROLLBACK_ENGINEERING_REVISION
            or architecture not in ROLLBACK_ENGINEERING_PLATFORM_DIGESTS
            or identity.get("manifest_digest") != ROLLBACK_ENGINEERING_PLATFORM_DIGESTS[architecture]):
        return "rollback_identity_mismatch"
    return None


def deployment_problem(name: str, *, container_image: str | None,
                       rollback_identity: dict[str, Any] | None) -> str | None:
    problem = engineering_image_problem(name, container_image)
    if problem is None and name in ROLLBACK_CASES:
        problem = rollback_identity_problem(rollback_identity)
    return problem


def probe_view(probe: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(probe, dict):
        return {"success": False, "error_code": "probe_unreadable", "failure_category": None,
                "dispatch_occurred": None, "provider": None, "upstream_version": None, "mcp_is_error": None}
    metadata = probe.get("metadata") if isinstance(probe.get("metadata"), dict) else {}
    details = probe.get("details") if isinstance(probe.get("details"), dict) else {}
    return {
        "success": probe.get("success") is True,
        "error_code": probe.get("error_code"),
        "failure_category": details.get("failure_category"),
        "dispatch_occurred": metadata.get("upstream_dispatch_occurred"),
        "provider": metadata.get("provider"),
        "upstream_version": metadata.get("upstream_version"),
        "mcp_is_error": probe.get("mcp_is_error"),
    }


def failure_categories(view: dict[str, Any]) -> set[str]:
    return {value for key in ("last_failure_category", "last_call_failure_category",
                              "last_discovery_failure_category")
            if isinstance(value := view.get(key), str)}


def peer_delta_problem(requests: list[tuple[str, int]], *, refused_path: str, allowed: int) -> str | None:
    """A refused-before-list session costs the peer one initialize POST and nothing more.

    ``tools/list`` needs an established session: initialize (200), the
    ``notifications/initialized`` POST (202) and then the list POST. So any
    202, any request on another path, or more POSTs than refused sessions
    means the peer saw traffic past initialize.
    """

    if any(path != refused_path for path, _ in requests):
        return "peer_request_on_unexpected_path"
    if any(status == 202 for _, status in requests):
        return "peer_session_established"
    if len(requests) > allowed:
        return "peer_requests_exceed_refused_sessions"
    return None


def is_lock_file(path: str) -> bool:
    return path.rsplit("/", 1)[-1].endswith(".lock")


def persisted_snapshot_problem(entries: Any) -> str | None:
    """What the previous release left must be complete and readable before it counts."""

    if not isinstance(entries, dict) or not entries or len(entries) > PERSISTED_ENTRY_LIMIT:
        return "previous_release_data_unobserved"
    if not all(isinstance(entry, dict) for entry in entries.values()):
        return "previous_release_data_unobserved"
    if any("error" in entry or (entry.get("kind") == "dir" and entry.get("listable") is not True)
           for entry in entries.values()):
        return "previous_release_data_unreadable"
    audit = entries.get(PERSISTED_AUDIT, {})
    if audit.get("kind") != "file" or not isinstance(audit.get("records"), int) or audit["records"] < 1:
        return "previous_release_audit_absent"
    if entries.get(PERSISTED_PLAN_STORE, {}).get("kind") != "dir":
        return "previous_release_plan_store_absent"
    plans = {path: entry for path, entry in entries.items() if re.fullmatch(PERSISTED_PLAN_FILE, path)}
    if not plans:
        return "previous_release_plans_absent"
    for path, entry in plans.items():
        plan = entry.get("plan")
        if (not isinstance(plan, dict) or plan.get("plan_id") != path.rsplit("/", 1)[-1][:-len(".json")]
                or not isinstance(plan.get("identity_sha256"), str)):
            return "previous_release_plan_unparseable"
    if any(entries.get(path, {}).get("kind") != "dir" for path in (PERSISTED_TRACE_STORE, PERSISTED_TRACE_RECORDS)):
        return "previous_release_trace_store_absent"
    return None


def persisted_data_problem(snapshot: Any, current: Any, mount_source: str | None) -> str | None:
    """Stage A again must run on the previous release's data, all of it present and readable."""

    if (not isinstance(snapshot, dict) or snapshot.get("status") != "passed"
            or snapshot.get("image") != ROLLBACK_ENGINEERING_IMAGE):
        return "persisted_snapshot_not_previous_release"
    source = snapshot.get("mount_source")
    if not isinstance(source, str) or not source or source != mount_source:
        return "persisted_data_not_same_mount"
    entries = snapshot.get("entries")
    problem = persisted_snapshot_problem(entries)
    if problem is not None:
        return problem
    if not isinstance(current, dict) or "<walk>" in current:
        return "persisted_data_unreadable_after_restart"
    for path, before in sorted(entries.items()):
        if is_lock_file(path):
            continue  # lock files are coordination, not data
        after = current.get(path)
        if not isinstance(after, dict):
            return "persisted_entry_missing"
        if after.get("kind") != before.get("kind"):
            return "persisted_entry_kind_changed"
        if "error" in after or (after.get("kind") == "dir" and after.get("listable") is not True):
            return "persisted_entry_unreadable"
        if before.get("kind") != "file":
            continue
        if path.endswith(".jsonl"):
            # Append-only: every byte and record the previous release wrote is
            # the unchanged prefix of the file the restarted runtime holds.
            if (not isinstance(after.get("size"), int) or after["size"] < before["size"]
                    or after.get("prefix_sha256") != before.get("sha256")
                    or after.get("records") != before.get("records")):
                return "persisted_audit_not_preserved"
        elif re.fullmatch(PERSISTED_PLAN_FILE, path):
            plan, previous = after.get("plan"), before.get("plan")
            if not isinstance(plan, dict) or plan.get("identity_sha256") != previous.get("identity_sha256"):
                return "persisted_plan_identity_changed"
        elif path.startswith(PERSISTED_PLAN_STORE + "/"):
            if after.get("sha256") != before.get("sha256"):
                return "persisted_record_changed"
    return None


def persisted_summary(snapshot: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    entries = snapshot["entries"]
    plans = sorted(path for path in entries if re.fullmatch(PERSISTED_PLAN_FILE, path))
    return {
        "mount_source": snapshot.get("mount_source"),
        "previous_release_entries": len(entries),
        "previous_release_audit_records": entries[PERSISTED_AUDIT]["records"],
        "previous_release_plans": len(plans),
        "previous_release_trace_records": sum(1 for path, entry in entries.items()
                                              if path.startswith(PERSISTED_TRACE_RECORDS + "/")
                                              and entry.get("kind") == "file"),
        "plan_status_after_restart": {path.rsplit("/", 1)[-1][:-len(".json")]:
                                      [entries[path]["plan"].get("status"), current[path]["plan"].get("status")]
                                      for path in plans},
        "audit_records_after_restart": current[PERSISTED_AUDIT].get("records"),
    }


def canonical_fingerprint(value: Any) -> str:
    """The runtime's ``schema_fingerprint``: SHA-256 over canonical JSON."""

    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def load_reviewed_91_capture(path: Path = REVIEWED_91_CAPTURE) -> list[dict[str, Any]]:
    capture = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(capture, dict) and capture.get("server_version") == CANDIDATE_VERSION,
            "reviewed_capture_not_91")
    tools = capture.get("tools")
    require(isinstance(tools, list) and len(tools) == capture.get("tool_count")
            and all(isinstance(tool, dict) and isinstance(tool.get("name"), str) for tool in tools),
            "reviewed_capture_unreadable")
    names = [tool["name"] for tool in tools]
    require(len(set(names)) == len(names) and not set(names) & set(OPTIONAL_NAMES), "reviewed_capture_invalid")
    return tools


def strict_catalog_problem(observed: Any, reviewed: list[dict[str, Any]], *,
                           optional_names: tuple[str, ...]) -> str | None:
    """Exact, order-insensitive comparison of the peer's strict-full catalog with the reviewed capture.

    Every reviewed tool must be advertised exactly once with a descriptor equal
    to the reviewed one on every field (name, title, description, inputSchema,
    outputSchema, annotations, _meta, and no other key). The advertised set
    must equal the reviewed set plus exactly ``optional_names``. Advertised
    order is not compared, and no fingerprint gates: fingerprints are recorded
    as diagnostics only (Josh, 2026-11-07).
    """

    if not isinstance(observed, list) or not all(isinstance(tool, dict) and isinstance(tool.get("name"), str)
                                                  for tool in observed):
        return "peer_catalog_unreadable"
    names = [tool["name"] for tool in observed]
    if len(set(names)) != len(names):
        return "peer_catalog_duplicate_tool"
    by_name = {tool["name"]: tool for tool in reviewed}
    if set(by_name) - set(names):
        return "peer_catalog_missing_reviewed_tool"
    if set(names) - set(by_name) - set(optional_names):
        return "peer_catalog_extra_tool"
    if set(optional_names) - set(names):
        return "peer_catalog_optional_tool_missing"
    for tool in observed:
        expected = by_name.get(tool["name"])
        if expected is None:
            continue
        if set(tool) != set(expected):
            return "peer_catalog_descriptor_keys_mismatch"
        for field in DESCRIPTOR_FIELDS:
            if field in expected and canonical_fingerprint(tool[field]) != canonical_fingerprint(expected[field]):
                return f"peer_catalog_{field.strip('_')}_mismatch"
    return None


def evaluate_health_case(expect: str, *, configured_url: str, engineering_version: str,
                         container_image: str | None, rollback_identity: dict[str, Any] | None,
                         health_before: dict[str, Any],
                         health_after: dict[str, Any], tool_names: list[str],
                         probe: dict[str, Any] | None, fixture_before: dict[str, int] | None,
                         fixture_after: dict[str, int] | None,
                         peer_requests: list[tuple[str, int]] | None,
                         peer_tools: list[dict[str, Any]] | None = None,
                         reviewed_tools: list[dict[str, Any]] | None = None) -> tuple[str, str, dict[str, Any]]:
    """Return ``(status, reason, observed)`` for one Engineering health case.

    ``peer_tools`` is the peer's raw strict-full catalog and ``reviewed_tools``
    the reviewed capture; both are required for ``admitted_strict_91`` only.
    """

    configuration, name = EXPECTATIONS[expect]
    form = case_form(configuration, name)
    observed: dict[str, Any] = {}
    try:
        problem = endpoint_problem(configured_url, form)
        require(problem is None, problem or "")
        problem = deployment_problem(name, container_image=container_image,
                                     rollback_identity=rollback_identity)
        require(problem is None, problem or "")
        for observation in (health_before, health_after):
            problem = health_problem(observation)
            require(problem is None, problem or "")
        problem = engineering_version_problem(name, health_after, engineering_version)
        require(problem is None, problem or "")
        view = gateway_view(health_after)
        before = gateway_view(health_before)
        names = set(tool_names) if isinstance(tool_names, (list, tuple, set)) else set()
        result = probe_view(probe)
        observed.update(
            configured_path=urlsplit(configured_url).path,
            admission_status=view.get("admission_status"),
            initialized=view.get("initialized"),
            observed_upstream_server_version=view.get("observed_upstream_server_version"),
            last_failure_category=view.get("last_failure_category"),
            probe_tool_listed=PROBE_TOOL in names,
            probe_success=result["success"],
            probe_error_code=result["error_code"],
            probe_failure_category=result["failure_category"],
            probe_dispatch_occurred=result["dispatch_occurred"],
            engineering_version=engineering_version,
            engineering_image=container_image,
        )
        if fixture_before is not None and fixture_after is not None:
            observed["fixture_delta"] = {key: fixture_after[key] - fixture_before[key] for key in fixture_after}
            require(all(fixture_after[key] == fixture_before[key] for key in FIXTURE_MUTATION_KEYS),
                    "fixture_mutation_observed")
        if peer_requests is not None:
            observed["peer_requests"] = len(peer_requests)
        if expect == "admitted_strict_91":
            require(view.get("initialized") is True, "admission_not_initialized")
            require(view.get("admission_status") == "admitted_exact", "admission_not_exact")
            require(view.get("version_status") == "reviewed_exact", "version_not_reviewed_exact")
            require(view.get("observed_identity_status") == "accepted", "identity_not_accepted")
            require(view.get("observed_upstream_server_version") == CANDIDATE_VERSION, "observed_version_not_91")
            require(str(view.get("selected_compatibility_entry_id") or "").startswith(
                f"ha-mcp-v{CANDIDATE_VERSION}-"), "selected_entry_not_91")
            # Admission is per reviewed tool contract; the stock strict-full
            # fingerprint is informational in the runtime, so it is recorded,
            # not required to match (the 9.1 app options change the catalog).
            fingerprint = view.get("reviewed_strict_full_contract_fingerprint")
            require(isinstance(fingerprint, str) and len(fingerprint) == 64,
                    "reviewed_strict_full_contract_fingerprint_missing")
            require(view.get("catalog_admission_mode") == "reviewed_per_tool_contract_subset",
                    "catalog_admission_mode_unexpected")
            require(view.get("catalog_comparison_status") == "exact", "catalog_comparison_not_exact")
            require(all(view.get(key) == 0 for key in (
                "runtime_contract_mismatch_count", "output_contract_mismatch_count", "schema_mismatch_count",
                "schema_mismatched_automatic_read_count", "description_semantics_mismatch_count",
                "annotation_mismatch_count")), "contract_mismatch_observed")
            observed["reviewed_strict_full_contract_fingerprint"] = fingerprint
            observed["observed_strict_full_contract_fingerprint"] = view.get(
                "observed_strict_full_contract_fingerprint")
            require(view.get("reviewed_tool_accounting_valid") is True, "tool_accounting_invalid")
            # Every reviewed automatic read matched exactly; nothing quarantined
            # (catalog structure, order and duplicates surface only there),
            # missing, unreviewed, or different in the catalog diff.
            reads = view.get("reviewed_automatic_read_count")
            require(isinstance(reads, int) and reads > 0
                    and (view.get("policy_classifications") or {}).get("automatic_read") == reads
                    and view.get("exact_matched_automatic_read_count") == reads
                    and view.get("accounted_automatic_read_count") == reads
                    and view.get("automatic_read_accounting_valid") is True
                    and view.get("admission_complete") is True, "automatic_reads_not_all_exact")
            require(view.get("quarantined_automatic_read_count") == 0
                    and view.get("quarantine_reason_counts") == {}, "automatic_read_quarantined")
            require(view.get("missing_reviewed_read_count") == 0 and view.get("missing_automatic_read_count") == 0,
                    "reviewed_tool_missing")
            require(view.get("unreviewed_tool_count") == 0 and view.get("unreviewed_observed_tool_count") == 0,
                    "unreviewed_tool_observed")
            require(view.get("catalog_diff_field_counts") == {}, "catalog_diff_observed")
            # The optional filesystem tools are on the wire (enabled toggle):
            # the reader is admitted on its reviewed descriptor and every
            # sibling is withheld with no drift from its reviewed binding.
            states = view.get("optional_tools")
            require(isinstance(states, dict) and states.get(YAML_READER) == "admitted"
                    and all(states.get(key) == "prohibited_withheld" for key in YAML_SIBLINGS)
                    and view.get("optional_sibling_drift") == []
                    and view.get("optional_reviewed_accounting_valid") is True, "optional_tools_not_reviewed_exact")
            require(view.get("writes_allowed") is False and view.get("direct_ha_fallback_allowed") is False
                    and view.get("stock_catalog_match_is_informational") is True, "gateway_safety_flags_unexpected")
            require(isinstance(reviewed_tools, list), "reviewed_capture_missing")
            require(peer_tools is not None, "peer_catalog_unobserved")
            problem = strict_catalog_problem(peer_tools, reviewed_tools, optional_names=OPTIONAL_NAMES)
            require(problem is None, problem or "")
            require(view.get("reviewed_accounted_tool_count") == len(reviewed_tools)
                    and view.get("reviewed_policy_entry_count") == len(reviewed_tools)
                    and view.get("upstream_advertised_tool_count") == len(peer_tools),
                    "catalog_size_not_reviewed")
            # What the runner listed is what Engineering observed.
            wire = canonical_fingerprint({"tools": peer_tools})
            require(view.get("observed_strict_full_contract_fingerprint") == wire,
                    "peer_catalog_not_engineering_observation")
            observed["peer_catalog"] = {
                "advertised": len(peer_tools), "reviewed_exact": len(reviewed_tools),
                "optional_on_wire": sorted(name for name in (tool["name"] for tool in peer_tools)
                                           if name in OPTIONAL_NAMES),
                "duplicates": 0, "extras": 0, "missing": 0, "wire_fingerprint": wire,
                # Diagnostic only: whether the reviewed capture, in its own
                # order, reproduces the runtime's reviewed fingerprint.
                "reviewed_capture_fingerprint_matches_runtime":
                    canonical_fingerprint({"tools": reviewed_tools}) == fingerprint,
            }
            # One optional exposure: the admitted reader.
            require(view.get("exact_matched_automatic_read_count") is not None
                    and view.get("dynamically_exposed_count") == reads + 1
                    and view.get("core_withheld_read_count") == 0, "no_delegated_reads_exposed")
            require(PROBE_TOOL in names, "probe_tool_not_listed")
            require(result["success"], "probe_read_not_useful")
            require(result["mcp_is_error"] is not True, "probe_mcp_is_error")
            require(result["provider"] == "upstream_read_gateway", "probe_not_via_read_gateway")
            require(result["upstream_version"] == CANDIDATE_VERSION, "probe_not_served_by_91")
            protocol = view.get("observed_protocol_version")
            require(isinstance(protocol, str) and protocol != "", "protocol_not_observed")
            observed["negotiated_protocol"] = protocol
            observed["selected_compatibility_entry_id"] = view.get("selected_compatibility_entry_id")
            observed["dynamically_exposed_count"] = view.get("dynamically_exposed_count")
        elif expect in ("catalog_mode_unadmitted_at_startup", "catalog_mode_unadmitted_after_peer_change"):
            require(CATALOG_MODE_UNADMITTED in failure_categories(view), "catalog_mode_refusal_not_reported")
            require("/catalog/full" in str(view.get("operator_remediation") or ""), "remediation_missing")
            require(not result["success"], "refused_form_served_a_read")
            require(peer_requests is not None, "peer_requests_not_observed")
            if expect == "catalog_mode_unadmitted_at_startup":
                require(view.get("initialized") is False, "refused_form_initialized")
                require(not str(view.get("admission_status") or "").startswith("admitted"),
                        "refused_form_admitted")
                require(view.get("dynamically_exposed_count") == 0, "refused_form_exposed_reads")
                require(view.get("observed_upstream_server_version") is None, "refused_form_identity_observed")
                require(PROBE_TOOL not in names, "refused_form_listed_delegated_read")
                attempts = view.get("discovery_attempt_count")
                require(isinstance(attempts, int) and attempts >= 1, "refused_form_no_discovery_attempt")
                allowed = attempts + 1
            else:
                # The peer changed under a running Engineering; its next
                # upstream session observes 9.1 on the legacy value and is
                # refused before tools/list or tools/call.
                require(result["failure_category"] == CATALOG_MODE_UNADMITTED, "probe_not_refused_by_catalog_mode")
                require(result["dispatch_occurred"] is False, "probe_dispatched_upstream")
                require(fixture_before is not None and fixture_after is not None, "fixture_not_observed")
                require(all(fixture_after[key] == fixture_before[key] for key in FIXTURE_READ_KEYS),
                        "fixture_read_observed")
                delta = (view.get("discovery_attempt_count") or 0) - (before.get("discovery_attempt_count") or 0)
                allowed = max(delta, 0) + 1
            problem = peer_delta_problem(peer_requests, refused_path=PEER_SECRET_PATH, allowed=allowed)
            require(problem is None, problem or "")
        elif expect == "unsupported_version_rollback":
            require(view.get("initialized") is False, "rollback_initialized_91")
            require(view.get("admission_status") == "blocked_incompatible_upstream", "rollback_not_blocked")
            require(VERSION_MISMATCH in failure_categories(view), "rollback_not_version_mismatch")
            require(view.get("observed_upstream_server_version") == CANDIDATE_VERSION, "rollback_did_not_observe_91")
            require(view.get("observed_identity_status") == "rejected", "rollback_identity_not_rejected")
            require(view.get("version_status") == "rejected_unreviewed", "rollback_version_not_rejected")
            supported = view.get("reviewed_supported_versions")
            require(isinstance(supported, list) and CANDIDATE_VERSION not in supported,
                    "rollback_engineering_has_91_entry")
            require(view.get("dynamically_exposed_count") == 0, "rollback_exposed_reads")
            require(PROBE_TOOL not in names, "rollback_listed_delegated_read")
            surface = delegated_surface(tool_names)
            require(surface is not None, "engineering_tool_list_unobserved")
            observed["listed_tool_count"] = len(tool_names)
            observed["delegated_surface"] = surface
            require(surface == [], "rollback_delegated_surface_not_empty")
            require(not result["success"], "rollback_served_a_read")
            require(isinstance(probe, dict), "rollback_probe_evidence_missing")
            problem = rollback_refusal_problem(probe)
            require(problem is None, problem or "")
            observed["probe_refusal"] = {key: probe.get(key) for key in ("error", "error_code", "mcp_is_error")}
            require(fixture_before is not None and fixture_after is not None, "fixture_not_observed")
            require(all(fixture_after[key] == fixture_before[key] for key in FIXTURE_READ_KEYS),
                    "fixture_read_observed")
            observed["reviewed_supported_version_count"] = len(supported)
        else:
            raise LaneFailure("expectation_unknown")
    except LaneFailure as error:
        return "failed", str(error), observed
    return "passed", "", observed


def evaluate_gateway_case(configuration: str, name: str, *, source: dict[str, Any] | None,
                          configured_url: str, upstream_endpoint: str, engineering_version: str,
                          container_image: str | None, rollback_identity: dict[str, Any] | None,
                          health: dict[str, Any] | None,
                          persisted: tuple[Any, Any, str | None] | None = None) -> tuple[str, str, dict[str, Any]]:
    """Return ``(status, reason, observed)`` for one gateway-acceptance case.

    ``persisted`` is ``(snapshot, current_inventory, current_mount_source)``;
    it is required for the roll-forward case and refused for every other.
    """

    observed: dict[str, Any] = {}
    try:
        require((configuration, name) in GATEWAY_CASES, "gateway_case_unlisted")
        version, optional = GATEWAY_CASES[(configuration, name)]
        form = case_form(configuration, name)
        for url in (configured_url, upstream_endpoint):
            problem = endpoint_problem(url, form)
            require(problem is None, problem or "")
        require(isinstance(source, dict), "acceptance_result_missing")
        require(source.get("result") == "PASS", "acceptance_result_not_pass")
        require(source.get("upstream_version") == version, "acceptance_version_mismatch")
        observed.update(configured_path=urlsplit(configured_url).path,
                        upstream_version=source.get("upstream_version"),
                        dynamic_tool_count=source.get("dynamic_tool_count"),
                        observed_catalog_fingerprint=source.get("observed_catalog_fingerprint"))
        problem = deployment_problem(name, container_image=container_image,
                                     rollback_identity=rollback_identity)
        require(problem is None, problem or "")
        problem = health_problem(health)
        require(problem is None, problem or "")
        problem = engineering_version_problem(name, health, engineering_version)
        require(problem is None, problem or "")
        observed["engineering_version"] = engineering_version
        observed["engineering_image"] = container_image
        if name == ROLL_FORWARD_CASE:
            require(persisted is not None, "persisted_snapshot_missing")
            problem = persisted_data_problem(*persisted)
            require(problem is None, problem or "")
            observed["persisted_from_previous_release"] = persisted_summary(persisted[0], persisted[1])
        else:
            require(persisted is None, "persisted_check_outside_roll_forward")
        view = gateway_view(health)
        require(view.get("admission_status") == "admitted_exact", "admission_not_exact")
        require(view.get("observed_upstream_server_version") == version, "health_version_mismatch")
        require(str(view.get("selected_compatibility_entry_id") or "").startswith(f"ha-mcp-v{version}-"),
                "selected_entry_mismatch")
        protocol = view.get("observed_protocol_version")
        require(isinstance(protocol, str) and protocol != "", "protocol_not_observed")
        observed["negotiated_protocol"] = protocol
        if optional is not None:
            on_wire = source.get("observed_optional_tools")
            exposed = source.get("optional_exposed_tools")
            states = view.get("optional_tools")
            require(isinstance(on_wire, list) and isinstance(exposed, list) and isinstance(states, dict),
                    "optional_accounting_missing")
            observed.update(observed_optional_tools=sorted(on_wire), optional_exposed_tools=sorted(exposed),
                            optional_tools={key: states.get(key) for key in OPTIONAL_NAMES})
            if optional == "admitted":
                require(sorted(on_wire) == sorted(OPTIONAL_NAMES), "filesystem_tools_not_on_wire")
                require(exposed == [YAML_READER], "yaml_reader_not_admitted_alone")
                require(states.get(YAML_READER) == "admitted", "yaml_reader_state_not_admitted")
                require(all(states.get(key) == "prohibited_withheld" for key in YAML_SIBLINGS),
                        "filesystem_sibling_not_withheld")
            else:
                require(on_wire == [] and exposed == [], "filesystem_disabled_tools_on_wire")
                require(all(states.get(key) == "optional_absent" for key in OPTIONAL_NAMES),
                        "filesystem_disabled_state_not_absent")
    except LaneFailure as error:
        return "failed", str(error), observed
    return "passed", "", observed


# --------------------------------------------------------------------------
# Observation (I/O)
# --------------------------------------------------------------------------

def http_json(url: str) -> dict[str, Any]:
    require(urlsplit(url).hostname in ("127.0.0.1", "localhost"), "observation_not_loopback")
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - loopback only
        value = json.loads(response.read(1_048_576).decode("utf-8"))
    require(isinstance(value, dict), "observation_not_object")
    return value


def container_image(container: str) -> str | None:
    require(ENGINEERING_CONTAINER.fullmatch(container) is not None, "engineering_container_name_invalid")
    completed = subprocess.run(["docker", "inspect", "--format", "{{.Config.Image}}", container],
                               capture_output=True, text=True, timeout=60, check=False)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def container_inventory(container: str, prefix: dict[str, int]) -> dict[str, Any]:
    """Inventory ``/data`` from inside the running Engineering container."""

    require(ENGINEERING_CONTAINER.fullmatch(container) is not None, "engineering_container_name_invalid")
    config = {"root": PERSISTED_ROOT, "prefix": prefix, "plan_file": PERSISTED_PLAN_FILE,
              "plan_identity": list(PERSISTED_PLAN_IDENTITY)}
    completed = subprocess.run(["docker", "exec", container, "python3", "-I", "-c", INVENTORY_SCRIPT,
                                json.dumps(config)], capture_output=True, text=True, timeout=300, check=True)
    value = json.loads(completed.stdout)
    require(isinstance(value, dict), "persisted_inventory_not_object")
    return value


def container_data_mount(container: str) -> str | None:
    require(ENGINEERING_CONTAINER.fullmatch(container) is not None, "engineering_container_name_invalid")
    completed = subprocess.run(["docker", "inspect", "--format", "{{json .Mounts}}", container],
                               capture_output=True, text=True, timeout=60, check=True)
    mounts = json.loads(completed.stdout or "null")
    sources = [mount.get("Source") for mount in mounts or [] if isinstance(mount, dict)
               and mount.get("Destination") == PERSISTED_ROOT]
    return sources[0] if len(sources) == 1 and isinstance(sources[0], str) else None


def load_identity(path: str | None) -> dict[str, Any] | None:
    if not path or not Path(path).is_file():
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def peer_log(container: str) -> str:
    completed = subprocess.run(["docker", "logs", container], capture_output=True, text=True,
                               timeout=60, check=True)
    return completed.stdout + completed.stderr


def snapshot(fixture_stats_url: str | None, peer_container: str | None) -> dict[str, Any]:
    mark: dict[str, Any] = {}
    if fixture_stats_url:
        mark["fixture"] = fixture_totals(http_json(fixture_stats_url))
    if peer_container:
        mark["peer_requests"] = len(parse_peer_requests(peer_log(peer_container)))
    return mark


def decoded_content(result: Any) -> dict[str, Any] | None:
    text = "".join(getattr(item, "text", "") or "" for item in getattr(result, "content", []) or [])
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def tool_text(result: Any) -> dict[str, Any] | None:
    """The JSON object a successful tool call returned; an MCP error result (``isError``) is never one."""

    if getattr(result, "isError", False) is not False:
        return None
    return decoded_content(result)


def tool_evidence(result: Any) -> dict[str, Any] | None:
    """A call's JSON object with its MCP ``isError`` flag recorded as ``mcp_is_error``.

    For the probe read, whose refusal is itself evidence: the flag is kept so a
    case can require it (an MCP error for a refusal, never for a served read).
    """

    value = decoded_content(result)
    if value is None:
        return None
    return {**value, "mcp_is_error": getattr(result, "isError", None) is True}


async def peer_catalog(endpoint: str) -> list[dict[str, Any]]:
    """Every descriptor the loopback peer advertises on ``endpoint``, all pages."""

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    require(urlsplit(endpoint).hostname == "127.0.0.1", "peer_not_loopback")
    tools: list[dict[str, Any]] = []
    async with streamablehttp_client(endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            cursor = None
            for _ in range(MAX_PEER_CATALOG_PAGES):
                result = await session.list_tools(cursor)
                tools.extend(tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                             for tool in result.tools)
                cursor = result.nextCursor
                if not cursor:
                    return tools
    raise LaneFailure("peer_catalog_unbounded")


async def engineering_session(endpoint: str, *, probe: bool) -> tuple[dict[str, Any], list[str], dict[str, Any] | None]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    require(urlsplit(endpoint).hostname == "127.0.0.1", "engineering_not_loopback")
    async with streamablehttp_client(endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = sorted(tool.name for tool in (await session.list_tools()).tools)
            probe_result = None
            if probe:
                try:
                    probe_result = tool_evidence(await session.call_tool(PROBE_TOOL, PROBE_ARGUMENTS))
                except Exception:  # noqa: BLE001 - an unlisted tool is a refusal, recorded as such
                    probe_result = {"success": False, "error_code": "probe_call_rejected"}
            health = tool_text(await session.call_tool(HEALTH_TOOL, {"check_ha": False}))
            require(isinstance(health, dict), "health_unreadable")
            return health, names, probe_result


Session = Callable[..., Any]


def settled(expect: str, view: dict[str, Any]) -> bool:
    if expect == "admitted_strict_91":
        return view.get("initialized") is True and view.get("admission_status") == "admitted_exact"
    if expect == "unsupported_version_rollback":
        return VERSION_MISMATCH in failure_categories(view)
    if expect == "catalog_mode_unadmitted_at_startup":
        return CATALOG_MODE_UNADMITTED in failure_categories(view)
    return True


def command_health_case(args: argparse.Namespace,
                        session: Callable[..., Any] = engineering_session,
                        catalog: Callable[..., Any] = peer_catalog) -> int:
    configuration, name = EXPECTATIONS[args.expect]
    require(args.configuration == configuration and args.case == name, "health_case_mismatch")
    form = case_form(configuration, name)
    try:
        # An explicit --mark covers everything since it was taken (a peer
        # change, an Engineering start). Without one, the window opens after
        # admission has settled, immediately before the probing session.
        mark = json.loads(Path(args.mark).read_text(encoding="utf-8")) if args.mark else None
        health_before, _, _ = asyncio.run(session(args.engineering_endpoint, probe=False))
        deadline = time.monotonic() + args.wait_seconds
        while not settled(args.expect, gateway_view(health_before)) and time.monotonic() < deadline:
            time.sleep(2)
            health_before, _, _ = asyncio.run(session(args.engineering_endpoint, probe=False))
        if mark is None:
            mark = snapshot(args.fixture_stats_url, args.peer_container)
        health_after, names, probe = asyncio.run(session(args.engineering_endpoint, probe=True))
        fixture_after = fixture_totals(http_json(args.fixture_stats_url)) if args.fixture_stats_url else None
        requests = None
        if args.peer_container:
            requests = parse_peer_requests(peer_log(args.peer_container))[mark.get("peer_requests", 0):]
        peer_tools = reviewed_tools = None
        upstream = getattr(args, "upstream_endpoint", None)
        if args.expect == "admitted_strict_91":
            # Listed after the observation window closes, so it is not counted in it.
            require(upstream is not None, "peer_endpoint_missing")
            problem = endpoint_problem(upstream, form)
            require(problem is None, problem or "")
            reviewed_tools = load_reviewed_91_capture()
            peer_tools = asyncio.run(catalog(upstream))
        else:
            require(upstream is None, "peer_catalog_outside_strict_admission")
        status, reason, observed = evaluate_health_case(
            args.expect, configured_url=args.configured_url,
            engineering_version=args.expected_engineering_version,
            container_image=container_image(args.engineering_container),
            rollback_identity=load_identity(args.rollback_identity), health_before=health_before,
            health_after=health_after, tool_names=names, probe=probe,
            fixture_before=mark.get("fixture"), fixture_after=fixture_after, peer_requests=requests,
            peer_tools=peer_tools, reviewed_tools=reviewed_tools)
    except LaneFailure as error:
        status, reason, observed = "failed", str(error), {}
    except Exception as error:  # noqa: BLE001 - any observation failure fails the case
        status, reason, observed = "failed", "observation_failed: " + bounded(type(error).__name__, 64), {}
    write_json(Path(args.output), case_result(name, status, form=form, reason=reason, observed=observed))
    print(f"{configuration} {name}: {status}{(' (' + reason + ')') if reason else ''}")
    return 0 if status == "passed" else 1


def command_gateway_case(args: argparse.Namespace,
                         session: Callable[..., Any] = engineering_session) -> int:
    require(args.case in case_names(args.configuration), "gateway_case_unlisted")
    form = case_form(args.configuration, args.case)
    try:
        path = Path(args.source)
        source = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        health = None
        persisted = None
        if isinstance(source, dict) and source.get("result") == "PASS":
            health, _, _ = asyncio.run(session(args.engineering_endpoint, probe=False))
            snapshot_path = getattr(args, "persisted_snapshot", None)
            if snapshot_path:
                persisted = observe_persisted(snapshot_path, args.engineering_container)
        status, reason, observed = evaluate_gateway_case(
            args.configuration, args.case, source=source, configured_url=args.configured_url,
            upstream_endpoint=args.upstream_endpoint,
            engineering_version=args.expected_engineering_version,
            container_image=container_image(args.engineering_container),
            rollback_identity=load_identity(args.rollback_identity), health=health, persisted=persisted)
    except LaneFailure as error:
        status, reason, observed = "failed", str(error), {}
    except Exception as error:  # noqa: BLE001 - any observation failure fails the case
        status, reason, observed = "failed", "observation_failed: " + bounded(type(error).__name__, 64), {}
    write_json(Path(args.output), case_result(args.case, status, form=form, reason=reason, observed=observed))
    print(f"{args.configuration} {args.case}: {status}{(' (' + reason + ')') if reason else ''}")
    return 0 if status == "passed" else 1


def observe_persisted(snapshot_path: str, container: str,
                      inventory: Callable[[str, dict[str, int]], dict[str, Any]] | None = None,
                      mount: Callable[[str], str | None] | None = None) -> tuple[Any, Any, str | None]:
    """Re-inventory the restarted container against the previous release's snapshot."""

    inventory = inventory or container_inventory
    mount = mount or container_data_mount
    snapshot = load_identity(snapshot_path)
    entries = snapshot.get("entries") if isinstance(snapshot, dict) else None
    if not isinstance(entries, dict):
        return snapshot, None, None
    sizes = {path: entry["size"] for path, entry in entries.items()
             if isinstance(entry, dict) and entry.get("kind") == "file" and isinstance(entry.get("size"), int)}
    return snapshot, inventory(container, sizes), mount(container)


def command_persisted_snapshot(args: argparse.Namespace,
                               inventory: Callable[[str, dict[str, int]], dict[str, Any]] | None = None,
                               mount: Callable[[str], str | None] | None = None,
                               image: Callable[[str], str | None] | None = None) -> int:
    """Record what the running previous release wrote or left in its data directory."""

    inventory = inventory or container_inventory
    mount = mount or container_data_mount
    image = image or container_image
    result: dict[str, Any] = {"status": "failed", "container": args.engineering_container}
    try:
        observed_image = image(args.engineering_container)
        require(observed_image == ROLLBACK_ENGINEERING_IMAGE, "persisted_snapshot_not_previous_release")
        source = mount(args.engineering_container)
        require(isinstance(source, str) and source != "", "persisted_data_mount_missing")
        require(Path(source).resolve() == Path(args.data_dir).resolve(), "persisted_data_not_lane_data_dir")
        entries = inventory(args.engineering_container, {})
        problem = persisted_snapshot_problem(entries)
        require(problem is None, problem or "")
        result.update(status="passed", image=observed_image, mount_source=source, entries=entries)
    except LaneFailure as error:
        result["reason"] = str(error)
    except Exception as error:  # noqa: BLE001 - any observation failure fails closed
        result["reason"] = "observation_failed: " + bounded(type(error).__name__, 64)
    write_json(Path(args.output), result)
    print(f"previous release persisted data: {result['status']}"
          f"{(' (' + result['reason'] + ')') if result.get('reason') else ''}")
    return 0 if result["status"] == "passed" else 1


def rollback_identity(architecture: str, run: Runner = run_command,
                      raw_index: Callable[[], bytes] | None = None) -> dict[str, Any]:
    """Verify the pinned previous release on this runner (read-only registry, then pull)."""

    require(architecture in ROLLBACK_ENGINEERING_PLATFORM_DIGESTS, "rollback_architecture_unpinned")
    require(DIGEST.fullmatch(ROLLBACK_ENGINEERING_INDEX_DIGEST) is not None, "rollback_digest_malformed")
    if raw_index is None:
        def raw_index() -> bytes:
            return subprocess.run(["docker", "buildx", "imagetools", "inspect", ROLLBACK_ENGINEERING_IMAGE, "--raw"],
                                  capture_output=True, timeout=300, check=True).stdout
    # The registry's own index bytes must hash to the pinned digest.
    require("sha256:" + hashlib.sha256(raw_index()).hexdigest() == ROLLBACK_ENGINEERING_INDEX_DIGEST,
            "rollback_index_bytes_mismatch")
    row = {
        "upstream_image": ROLLBACK_ENGINEERING_IMAGE,
        "image_index_digest": ROLLBACK_ENGINEERING_INDEX_DIGEST,
        "architecture_image_digests": {f"linux/{arch}": digest
                                       for arch, digest in ROLLBACK_ENGINEERING_PLATFORM_DIGESTS.items()},
        "upstream_version": ROLLBACK_ENGINEERING_VERSION,
        "image_revision": ROLLBACK_ENGINEERING_REVISION,
    }
    verified = image_identity(row, architecture, run)
    return {"status": "passed", "architecture": architecture, "image": ROLLBACK_ENGINEERING_IMAGE,
            "image_digest": verified["image_digest"], "manifest_digest": verified["manifest_digest"],
            "config_digest": verified["config_digest"], "version": ROLLBACK_ENGINEERING_VERSION,
            "revision": ROLLBACK_ENGINEERING_REVISION}


def command_rollback_identity(args: argparse.Namespace, run: Runner = run_command) -> int:
    try:
        result = rollback_identity(args.architecture, run)
    except (LaneFailure, ValueError, subprocess.SubprocessError, OSError) as exc:
        category = str(exc) if isinstance(exc, LaneFailure) else type(exc).__name__
        result = {"status": "failed", "architecture": args.architecture, "reason": bounded(category)}
    write_json(Path(args.output), result)
    print(f"previous release {ROLLBACK_ENGINEERING_VERSION} identity on {args.architecture}: {result['status']}")
    return 0 if result["status"] == "passed" else 1


def peer_command(row: dict[str, Any], role: str, *, filesystem: str) -> list[str]:
    """Return the docker command for one digest-pinned peer from the reviewed registry row."""

    image = row.get("upstream_image")
    index = row.get("image_index_digest")
    version = row.get("upstream_version")
    require(isinstance(image, str) and isinstance(index, str) and DIGEST.fullmatch(index) is not None
            and image.endswith("@" + index), "peer_row_not_digest_pinned")
    require(version == (CANDIDATE_VERSION if role == "candidate" else BASELINE_VERSION), "peer_row_version_unexpected")
    require(filesystem in ("enabled", "disabled"), "peer_filesystem_mode_invalid")
    require(role == "candidate" or filesystem == "enabled", "peer_filesystem_toggle_candidate_only")
    command = ["docker", "run", "--detach", "--network", "host", "--name", PEER_CONTAINER,
               "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/home/mcpuser/.ha-mcp",
               "-e", f"HOMEASSISTANT_URL={FIXTURE_HA_URL}", "-e", f"HOMEASSISTANT_TOKEN={SYNTHETIC_HA_TOKEN}",
               "-e", "MCP_HOST=127.0.0.1", "-e", f"MCP_PORT={PEER_PORT}",
               "-e", f"MCP_SECRET_PATH={PEER_SECRET_PATH}", "-e", "HA_MCP_DISABLE_SETTINGS_UI=true"]
    if role == "candidate":
        # 9.1 app options from the lane contract; only the filesystem toggle varies.
        command += ["-e", "ENABLE_TOOL_SEARCH=true", "-e", "ENABLE_BETA_FEATURES=true",
                    "-e", "HAMCP_ENABLE_FILESYSTEM_TOOLS=" + ("true" if filesystem == "enabled" else "false"),
                    "-e", "READ_ONLY_MODE=false"]
    return command + [image, "ha-mcp-web"]


def engineering_command(*, name: str, image: str, data_dir: str, mcp_port: int, approval_port: int,
                        upstream_url: str) -> list[str]:
    require(ENGINEERING_CONTAINER.fullmatch(name) is not None, "engineering_container_name_invalid")
    require(image in ENGINEERING_IMAGES, "engineering_image_not_allowed")
    form = classify_form(upstream_url)
    require(form in ("legacy", "strict_full") and endpoint_problem(upstream_url, form) is None,
            "engineering_upstream_url_not_lane_value")
    require(all(18000 <= port < 18400 for port in (mcp_port, approval_port)), "engineering_port_out_of_range")
    return ["docker", "run", "--detach", "--rm", "--network", "host",
            "--add-host", "abcdef12-ha-mcp:127.0.0.1", "--add-host", "supervisor:127.0.0.1",
            "--name", name, "-v", f"{data_dir}:/data",
            "-e", "HA_URL=http://supervisor:18123/core", "-e", f"HA_TOKEN={SYNTHETIC_HA_TOKEN}",
            "-e", f"SUPERVISOR_TOKEN={SYNTHETIC_HA_TOKEN}",
            "-e", f"MCP_PORT={mcp_port}", "-e", f"APPROVAL_INGRESS_PORT={approval_port}",
            "-e", f"UPSTREAM_DASHBOARD_MCP_URL={upstream_url}", image]


def http_status(url: str) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - loopback only
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError):
        return None


def wait_for(url: str, accept: Callable[[int | None], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if accept(http_status(url)):
            return True
        time.sleep(1)
    return False


def command_start_peer(args: argparse.Namespace) -> int:
    peers = json.loads(args.peers)
    require(isinstance(peers, dict) and isinstance(peers.get(args.role), dict), "peer_role_absent")
    row = peers[args.role]
    command = peer_command(row, args.role, filesystem=args.filesystem)
    subprocess.run(["docker", "rm", "-f", PEER_CONTAINER], capture_output=True, timeout=120, check=False)
    subprocess.run(command, capture_output=True, timeout=300, check=True)
    require(wait_for(PEER_READY_URL, lambda status: status is not None, 90), "peer_not_listening")
    installed = subprocess.run(["docker", "exec", PEER_CONTAINER, "python", "-c",
                                'from importlib.metadata import version; print(version("ha-mcp"))'],
                               capture_output=True, text=True, timeout=120, check=True).stdout.strip()
    require(installed == row["upstream_version"], "peer_installed_version_mismatch")
    print(f"peer {args.role} {installed} ({row['image_index_digest']}) filesystem {args.filesystem}")
    return 0


def command_start_engineering(args: argparse.Namespace) -> int:
    command = engineering_command(name=args.name, image=args.image, data_dir=args.data_dir,
                                  mcp_port=args.mcp_port, approval_port=args.approval_port,
                                  upstream_url=args.upstream_url)
    data = Path(args.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    options = data / "options.json"
    if not options.exists():
        options.write_text(json.dumps(ENGINEERING_OPTIONS, indent=2) + "\n", encoding="utf-8")
    for container in args.stop:
        require(ENGINEERING_CONTAINER.fullmatch(container) is not None, "engineering_container_name_invalid")
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=120, check=False)
    subprocess.run(command, capture_output=True, timeout=300, check=True)
    require(wait_for(f"http://127.0.0.1:{args.mcp_port}/ready", lambda status: status == 200, 120),
            "engineering_not_ready")
    print(f"engineering {args.name} ({args.image}) ready on {classify_form(args.upstream_url)}")
    return 0


def command_mark(args: argparse.Namespace) -> int:
    write_json(Path(args.output), snapshot(args.fixture_stats_url, args.peer_container))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    mark = commands.add_parser("mark", help="record fixture counters and the peer request-log position")
    mark.add_argument("--fixture-stats-url")
    mark.add_argument("--peer-container")
    mark.add_argument("--output", required=True)
    mark.set_defaults(handler=command_mark)

    health = commands.add_parser("health-case", help="observe Engineering admission health for one case")
    health.add_argument("--configuration", required=True, choices=sorted(CASES))
    health.add_argument("--case", required=True)
    health.add_argument("--expect", required=True, choices=sorted(EXPECTATIONS))
    health.add_argument("--engineering-endpoint", required=True)
    health.add_argument("--configured-url", required=True)
    health.add_argument("--expected-engineering-version", required=True)
    health.add_argument("--engineering-container", required=True)
    health.add_argument("--rollback-identity")
    health.add_argument("--fixture-stats-url")
    health.add_argument("--peer-container")
    health.add_argument("--upstream-endpoint",
                        help="loopback strict-full peer endpoint; admitted_strict_91 only")
    health.add_argument("--mark")
    health.add_argument("--wait-seconds", type=float, default=90.0)
    health.add_argument("--output", required=True)
    health.set_defaults(handler=command_health_case)

    gateway = commands.add_parser("gateway-case", help="record one gateway acceptance case with admission health")
    gateway.add_argument("--configuration", required=True, choices=sorted(CASES))
    gateway.add_argument("--case", required=True)
    gateway.add_argument("--source", required=True)
    gateway.add_argument("--engineering-endpoint", required=True)
    gateway.add_argument("--configured-url", required=True)
    gateway.add_argument("--upstream-endpoint", required=True)
    gateway.add_argument("--expected-engineering-version", required=True)
    gateway.add_argument("--engineering-container", required=True)
    gateway.add_argument("--rollback-identity")
    gateway.add_argument("--persisted-snapshot", help="roll-forward case: the previous release's data snapshot")
    gateway.add_argument("--output", required=True)
    gateway.set_defaults(handler=command_gateway_case)

    persisted = commands.add_parser("persisted-snapshot",
                                    help="record the previous release's persisted data before rolling forward")
    persisted.add_argument("--engineering-container", required=True)
    persisted.add_argument("--data-dir", required=True)
    persisted.add_argument("--output", required=True)
    persisted.set_defaults(handler=command_persisted_snapshot)

    identity = commands.add_parser("rollback-identity", help="verify the pinned previous Engineering release")
    identity.add_argument("--architecture", required=True, choices=sorted(ROLLBACK_ENGINEERING_PLATFORM_DIGESTS))
    identity.add_argument("--output", required=True)
    identity.set_defaults(handler=command_rollback_identity)

    peer = commands.add_parser("start-peer", help="start one digest-pinned ha-mcp peer from the lane row")
    peer.add_argument("--peers", required=True)
    peer.add_argument("--role", required=True, choices=("baseline", "candidate"))
    peer.add_argument("--filesystem", default="enabled", choices=("enabled", "disabled"))
    peer.set_defaults(handler=command_start_peer)

    engineering = commands.add_parser("start-engineering", help="start one Engineering container and wait for /ready")
    engineering.add_argument("--name", required=True)
    engineering.add_argument("--image", required=True)
    engineering.add_argument("--data-dir", required=True)
    engineering.add_argument("--mcp-port", required=True, type=int)
    engineering.add_argument("--approval-port", required=True, type=int)
    engineering.add_argument("--upstream-url", required=True)
    engineering.add_argument("--stop", action="append", default=[])
    engineering.set_defaults(handler=command_start_engineering)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except LaneFailure as error:
        print(f"exact-image admission runner refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
