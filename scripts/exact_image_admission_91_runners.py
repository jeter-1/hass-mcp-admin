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
YAML_READER = "ha_config_get_yaml"
YAML_SIBLINGS = ("ha_delete_file", "ha_list_files", "ha_read_file", "ha_write_file")
OPTIONAL_NAMES = (YAML_READER, *YAML_SIBLINGS)
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
    ("91_only", "91_parity_dashboard_gateway_acceptance"): (CANDIDATE_VERSION, "admitted"),
    ("91_only", "91_filesystem_disabled_variant"): (CANDIDATE_VERSION, "absent"),
    ("side_by_side", "sbs_stage_a_85_legacy_useful"): (BASELINE_VERSION, None),
    ("side_by_side", "sbs_stage_b2_91_strict_full_useful"): (CANDIDATE_VERSION, "admitted"),
    ("side_by_side", "sbs_rollback_85_legacy_useful"): (BASELINE_VERSION, None),
    ("side_by_side", "sbs_stage_a_again_85_legacy_useful"): (BASELINE_VERSION, None),
}


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
                "dispatch_occurred": None, "provider": None, "upstream_version": None}
    metadata = probe.get("metadata") if isinstance(probe.get("metadata"), dict) else {}
    details = probe.get("details") if isinstance(probe.get("details"), dict) else {}
    return {
        "success": probe.get("success") is True,
        "error_code": probe.get("error_code"),
        "failure_category": details.get("failure_category"),
        "dispatch_occurred": metadata.get("upstream_dispatch_occurred"),
        "provider": metadata.get("provider"),
        "upstream_version": metadata.get("upstream_version"),
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


def evaluate_health_case(expect: str, *, configured_url: str, engineering_version: str,
                         container_image: str | None, rollback_identity: dict[str, Any] | None,
                         health_before: dict[str, Any],
                         health_after: dict[str, Any], tool_names: list[str],
                         probe: dict[str, Any] | None, fixture_before: dict[str, int] | None,
                         fixture_after: dict[str, int] | None,
                         peer_requests: list[tuple[str, int]] | None) -> tuple[str, str, dict[str, Any]]:
    """Return ``(status, reason, observed)`` for one Engineering health case."""

    configuration, name = EXPECTATIONS[expect]
    form = case_form(configuration, name)
    observed: dict[str, Any] = {}
    try:
        problem = endpoint_problem(configured_url, form)
        require(problem is None, problem or "")
        problem = deployment_problem(name, container_image=container_image,
                                     rollback_identity=rollback_identity)
        require(problem is None, problem or "")
        problem = engineering_version_problem(name, health_after, engineering_version)
        require(problem is None, problem or "")
        view = gateway_view(health_after)
        before = gateway_view(health_before)
        names = set(tool_names)
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
            require(view.get("runtime_contract_mismatch_count") == 0
                    and view.get("output_contract_mismatch_count") == 0, "contract_mismatch_observed")
            observed["reviewed_strict_full_contract_fingerprint"] = fingerprint
            observed["observed_strict_full_contract_fingerprint"] = view.get(
                "observed_strict_full_contract_fingerprint")
            require(view.get("reviewed_tool_accounting_valid") is True, "tool_accounting_invalid")
            require(isinstance(view.get("dynamically_exposed_count"), int)
                    and view["dynamically_exposed_count"] > 0, "no_delegated_reads_exposed")
            require(PROBE_TOOL in names, "probe_tool_not_listed")
            require(result["success"], "probe_read_not_useful")
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
            require(not result["success"], "rollback_served_a_read")
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
                          health: dict[str, Any] | None) -> tuple[str, str, dict[str, Any]]:
    """Return ``(status, reason, observed)`` for one gateway-acceptance case."""

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
        require(isinstance(health, dict), "health_missing")
        problem = engineering_version_problem(name, health, engineering_version)
        require(problem is None, problem or "")
        observed["engineering_version"] = engineering_version
        observed["engineering_image"] = container_image
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


def tool_text(result: Any) -> dict[str, Any] | None:
    text = "".join(getattr(item, "text", "") or "" for item in getattr(result, "content", []) or [])
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


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
                    probe_result = tool_text(await session.call_tool(PROBE_TOOL, PROBE_ARGUMENTS))
                except Exception:  # noqa: BLE001 - an unlisted tool is a refusal, recorded as such
                    probe_result = {"success": False, "error_code": "probe_call_rejected"}
            health = tool_text(await session.call_tool("get_server_health", {"check_ha": False}))
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
                        session: Callable[..., Any] = engineering_session) -> int:
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
        status, reason, observed = evaluate_health_case(
            args.expect, configured_url=args.configured_url,
            engineering_version=args.expected_engineering_version,
            container_image=container_image(args.engineering_container),
            rollback_identity=load_identity(args.rollback_identity), health_before=health_before,
            health_after=health_after, tool_names=names, probe=probe,
            fixture_before=mark.get("fixture"), fixture_after=fixture_after, peer_requests=requests)
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
        if isinstance(source, dict) and source.get("result") == "PASS":
            health, _, _ = asyncio.run(session(args.engineering_endpoint, probe=False))
        status, reason, observed = evaluate_gateway_case(
            args.configuration, args.case, source=source, configured_url=args.configured_url,
            upstream_endpoint=args.upstream_endpoint,
            engineering_version=args.expected_engineering_version,
            container_image=container_image(args.engineering_container),
            rollback_identity=load_identity(args.rollback_identity), health=health)
    except LaneFailure as error:
        status, reason, observed = "failed", str(error), {}
    except Exception as error:  # noqa: BLE001 - any observation failure fails the case
        status, reason, observed = "failed", "observation_failed: " + bounded(type(error).__name__, 64), {}
    write_json(Path(args.output), case_result(args.case, status, form=form, reason=reason, observed=observed))
    print(f"{args.configuration} {args.case}: {status}{(' (' + reason + ')') if reason else ''}")
    return 0 if status == "passed" else 1


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
    gateway.add_argument("--output", required=True)
    gateway.set_defaults(handler=command_gateway_case)

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
