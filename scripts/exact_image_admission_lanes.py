"""HAMCP-319 exact-image admission lanes: case runners, manifest writer and gate.

Design section 5.1 (``docs/HAMCP_319_PHASE2_DESIGN.md``) accepts three
configurations separately (8.5-only, 9.1-only, side by side). Each needs an
exact-image lane on amd64 and on arm64, and each lane writes an acceptance
manifest: image, manifest and config digests, Engineering commit, endpoint
form, negotiated protocol, the case list, executed count and every skip with
its reason. A missing lane, or a listed case that did not execute, leaves that
configuration unaccepted and is a completion blocker [R5]; it is never
downgraded.

This module owns:

* ``CASES``: the fixed case list of every configuration. The manifest writer and
  the gate both use it, so a lane cannot shorten its own list.
* small case runners for the checks that are not already an existing acceptance
  script (image identity, a raw no-redirect ``initialize``, Engineering failing
  closed on a value the peer does not serve) and ``record`` to wrap the bounded
  result of an existing acceptance script;
* ``write-manifest``: one manifest per lane. A dormant lane (``support_window``
  admission state) writes ``NOT_EXECUTED`` with its reason and executes nothing.
  An active lane is ``PASS`` only when every listed case executed and passed;
  a case with no result is recorded as not executed and fails the lane;
* ``gate``: verifies all six manifests together. A missing or duplicate lane, a
  listed-but-unexecuted or skipped case in an active lane, a dormant lane that
  reports a result, or a state that differs from the derived matrix fails.

Nothing here reaches a live Home Assistant or a deployed MCP endpoint. Runners
only talk to disposable loopback services that the CI job started, and every
recorded value is bounded and secret-free (URLs are never written; only their
classified form).
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.parse import urlsplit


SCHEMA = "hamcp319-exact-image-acceptance-manifest/v1"
EXPECTED_PROTOCOL = "2025-03-26"
CONFIGURATIONS = ("85_only", "91_only", "side_by_side")
ARCHITECTURES = ("amd64", "arm64")
REQUIRED_LANES = frozenset((c, a) for c in CONFIGURATIONS for a in ARCHITECTURES)
ROLES = {"85_only": ("baseline",), "91_only": ("candidate",), "side_by_side": ("baseline", "candidate")}
FORMS = ("legacy", "strict_full")
STATES = ("active", "dormant")
STATUSES = ("passed", "failed", "skipped", "not_executed")
RESULTS = ("PASS", "FAIL", "NOT_EXECUTED")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
CASE_NAME = re.compile(r"[a-z0-9_]{1,80}")
MAX_REASON = 300
MAX_BODY = 65_536
# Selector keywords of the 9.1 grammar (design section 3.1/3.2).
SELECTOR_KEYWORDS = frozenset({"catalog", "full", "search", "readonly", "default"})
REDIRECT_CATEGORY = "upstream_redirect_refused"


@dataclass(frozen=True)
class Case:
    name: str
    endpoint_form: str | None


# The section 5.1 case list per configuration. Order is the manifest order.
CASES: dict[str, tuple[Case, ...]] = {
    "85_only": (
        Case("image_identity_baseline", None),
        Case("85_legacy_direct_initialize_no_redirect", "legacy"),
        Case("85_legacy_gateway_acceptance", "legacy"),
        Case("85_legacy_exact_admission_health", "legacy"),
        Case("85_strict_full_direct_initialize_refused", "strict_full"),
        Case("85_strict_full_engineering_fails_closed", "strict_full"),
    ),
    "91_only": (
        Case("image_identity_candidate", None),
        Case("91_strict_full_direct_initialize_no_redirect", "strict_full"),
        Case("91_strict_admission_positive", "strict_full"),
        Case("91_parity_dashboard_gateway_acceptance", "strict_full"),
        Case("91_typed_controls_verified_readback", "strict_full"),
        Case("91_legacy_form_refused_before_list", "legacy"),
        Case("91_filesystem_disabled_variant", "strict_full"),
    ),
    "side_by_side": (
        Case("image_identity_baseline", None),
        Case("image_identity_candidate", None),
        Case("sbs_stage_a_85_legacy_useful", "legacy"),
        Case("sbs_stage_b1_91_legacy_refused", "legacy"),
        Case("sbs_stage_b2_91_strict_full_useful", "strict_full"),
        Case("sbs_rollback_85_legacy_useful", "legacy"),
        Case("sbs_stage_a_again_85_legacy_useful", "legacy"),
        Case("sbs_engineering_rollback_91_refused", "strict_full"),
    ),
}
MANIFEST_KEYS = frozenset({
    "schema", "configuration", "architecture", "runner", "state", "result", "dormant_reason",
    "engineering_commit", "images", "endpoint_forms", "negotiated_protocol", "cases",
    "case_count", "executed_count", "skips",
})
IMAGE_KEYS = frozenset({"role", "version", "image_digest", "manifest_digest", "config_digest"})


class LaneFailure(RuntimeError):
    """One bounded lane assertion failed; the message is a fixed category."""


def require(condition: bool, category: str) -> None:
    if not condition:
        raise LaneFailure(category)


def bounded(text: Any, limit: int = MAX_REASON) -> str:
    value = " ".join(str(text).split())
    return value[:limit]


def classify_form(url: str) -> str:
    """Label a value ``strict_full`` or ``legacy`` per design section 3.2 Step 1.

    Pure label: ``strict_full`` needs a path ending exactly in ``/catalog/full``
    after at least one base segment that is not a selector keyword, with no
    percent-encoding, no dot segment, no empty segment and no trailing slash.
    Anything else is ``legacy``. It never refuses.
    """

    try:
        parts = urlsplit(url)
    except ValueError:
        return "legacy"
    path = parts.path
    if parts.query or parts.fragment or "%" in path or not path.endswith("/catalog/full"):
        return "legacy"
    segments = path.split("/")[1:]
    base = segments[:-2]
    if not base or any(s in ("", ".", "..") for s in segments):
        return "legacy"
    if any(s in SELECTOR_KEYWORDS for s in base):
        return "legacy"
    return "strict_full"


def case_names(configuration: str) -> tuple[str, ...]:
    return tuple(case.name for case in CASES[configuration])


def case_form(configuration: str, name: str) -> str | None:
    return next(case.endpoint_form for case in CASES[configuration] if case.name == name)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def case_result(name: str, status: str, *, form: str | None, reason: str = "",
                observed: dict[str, Any] | None = None) -> dict[str, Any]:
    require(CASE_NAME.fullmatch(name) is not None, "case_name_invalid")
    require(status in ("passed", "failed", "skipped"), "case_status_invalid")
    return {"case": name, "status": status, "endpoint_form": form,
            "reason": bounded(reason), "observed": observed or {}}


# --------------------------------------------------------------------------
# Image identity (index digest, platform manifest, config digest, labels)
# --------------------------------------------------------------------------

Runner = Callable[[list[str]], str]


def run_command(argv: list[str]) -> str:
    completed = subprocess.run(argv, check=True, capture_output=True, text=True, timeout=300)
    return completed.stdout


def image_identity(row: dict[str, Any], architecture: str, run: Runner = run_command) -> dict[str, Any]:
    """Verify one reviewed registry row on this runner and return its digests."""

    image = row.get("upstream_image")
    index = row.get("image_index_digest")
    expected_platforms = row.get("architecture_image_digests")
    require(isinstance(image, str) and isinstance(index, str) and DIGEST.fullmatch(index)
            and image.endswith("@" + index), "identity_row_malformed")
    require(isinstance(expected_platforms, dict), "identity_row_malformed")
    expected_manifest = expected_platforms.get("linux/" + architecture)
    require(isinstance(expected_manifest, str) and DIGEST.fullmatch(expected_manifest), "identity_platform_unreviewed")
    resolved = re.findall(r"^Digest:\s*(sha256:[0-9a-f]{64})$",
                          run(["docker", "buildx", "imagetools", "inspect", image]), re.M)
    require(resolved[:1] == [index], "identity_index_digest_mismatch")
    raw_index = json.loads(run(["docker", "buildx", "imagetools", "inspect", image, "--raw"]))
    observed = {}
    for item in raw_index.get("manifests", []) if isinstance(raw_index, dict) else []:
        platform = item.get("platform") or {}
        os_name, arch = platform.get("os"), platform.get("architecture")
        if not isinstance(os_name, str) or not isinstance(arch, str) or (os_name, arch) == ("unknown", "unknown"):
            continue
        name = f"{os_name}/{arch}"
        if isinstance(platform.get("variant"), str) and platform["variant"]:
            name += "/" + platform["variant"]
        observed[name] = item.get("digest")
    require(observed == expected_platforms, "identity_platform_map_mismatch")
    repository = image.rsplit("@", 1)[0]
    raw_manifest = json.loads(run(["docker", "buildx", "imagetools", "inspect",
                                   f"{repository}@{expected_manifest}", "--raw"]))
    config = raw_manifest.get("config") if isinstance(raw_manifest, dict) else None
    config_digest = config.get("digest") if isinstance(config, dict) else None
    require(isinstance(config_digest, str) and DIGEST.fullmatch(config_digest) is not None, "identity_config_missing")
    run(["docker", "pull", "--platform", "linux/" + architecture, image])
    labels = json.loads(run(["docker", "image", "inspect", image, "--format",
                             "{{json .Config.Labels}}"]) or "null") or {}
    require(labels.get("org.opencontainers.image.version") == row.get("upstream_version"), "identity_version_label_mismatch")
    require(labels.get("org.opencontainers.image.revision") == row.get("image_revision"), "identity_revision_label_mismatch")
    local_arch = run(["docker", "image", "inspect", image, "--format", "{{.Architecture}}"]).strip()
    require(local_arch == architecture, "identity_local_architecture_mismatch")
    return {"version": row["upstream_version"], "image_digest": index,
            "manifest_digest": expected_manifest, "config_digest": config_digest}


def command_identity(args: argparse.Namespace, run: Runner = run_command) -> int:
    peers = json.loads(args.peers)
    status = 0
    for role in ROLES[args.configuration]:
        name = "image_identity_" + role
        try:
            require(isinstance(peers, dict)
                    and isinstance(peers.get(role), dict), "identity_peer_absent")
            observed = {"role": role, **image_identity(peers[role], args.architecture, run)}
            result = case_result(name, "passed", form=None, observed=observed)
        except (LaneFailure, ValueError, subprocess.SubprocessError, OSError) as exc:
            category = str(exc) if isinstance(exc, LaneFailure) else type(exc).__name__
            result = case_result(name, "failed", form=None, reason=category)
            status = 1
        write_json(Path(args.cases_dir) / f"{name}.json", result)
    return status


# --------------------------------------------------------------------------
# Raw initialize with redirects disabled
# --------------------------------------------------------------------------

def initialize_payload() -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": EXPECTED_PROTOCOL, "capabilities": {},
                       "clientInfo": {"name": "hamcp319-admission-lane", "version": "1"}}}


def parse_rpc_body(content_type: str, body: str) -> dict[str, Any]:
    """Return the JSON-RPC message from a JSON or a single-event SSE body."""

    if "text/event-stream" in content_type:
        for line in body.splitlines():
            if line.startswith("data:"):
                value = json.loads(line[5:].strip())
                if isinstance(value, dict):
                    return value
        raise LaneFailure("initialize_sse_without_message")
    value = json.loads(body)
    require(isinstance(value, dict), "initialize_body_not_object")
    return value


def evaluate_initialize(status: int, content_type: str, body: str, *, expect: str,
                        expected_version: str | None) -> dict[str, Any]:
    require(not 300 <= status < 400, "initialize_redirect_observed")
    observed: dict[str, Any] = {"http_status": status}
    if expect == "refused":
        require(400 <= status < 500, "initialize_not_refused_with_client_error")
        require(REDIRECT_CATEGORY not in body[:MAX_BODY], "initialize_redirect_category_present")
        return observed
    require(status == 200, "initialize_not_served")
    message = parse_rpc_body(content_type, body[:MAX_BODY])
    result = message.get("result")
    require(isinstance(result, dict), "initialize_without_result")
    server = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {}
    observed["upstream_protocol"] = result.get("protocolVersion")
    # Counts toward the manifest's single negotiated protocol (section 5.1).
    observed["negotiated_protocol"] = result.get("protocolVersion")
    observed["server_version"] = server.get("version")
    require(result.get("protocolVersion") == EXPECTED_PROTOCOL, "initialize_protocol_mismatch")
    require(expected_version is None or server.get("version") == expected_version, "initialize_server_version_mismatch")
    return observed


def post_initialize(url: str) -> tuple[int, str, str]:
    import httpx  # Engineering requirements.lock pins it; imported lazily for unit tests.

    with httpx.Client(follow_redirects=False, timeout=15.0) as client:
        response = client.post(url, json=initialize_payload(), headers={
            "Accept": "application/json, text/event-stream", "Content-Type": "application/json"})
        return response.status_code, response.headers.get("content-type", ""), response.text[:MAX_BODY]


def command_initialize(args: argparse.Namespace, post: Callable[[str], tuple[int, str, str]] = post_initialize) -> int:
    form = classify_form(args.url)
    try:
        require(form == args.form, "endpoint_form_mismatch")
        status, content_type, body = post(args.url)
        observed = {"classified_form": form, **evaluate_initialize(
            status, content_type, body, expect=args.expect, expected_version=args.expected_version)}
        result = case_result(args.case, "passed", form=form, observed=observed)
    except (LaneFailure, ValueError, OSError) as exc:
        category = str(exc) if isinstance(exc, LaneFailure) else type(exc).__name__
        result = case_result(args.case, "failed", form=form, reason=category)
    write_json(Path(args.output), result)
    return 0 if result["status"] == "passed" else 1


# --------------------------------------------------------------------------
# Engineering stays fail closed on a value its peer does not serve
# --------------------------------------------------------------------------

def http_get(url: str) -> tuple[int, str]:
    """Return ``(status, body)``; ``(0, "")`` while nothing is listening yet."""

    import httpx

    try:
        with httpx.Client(follow_redirects=False, timeout=5.0) as client:
            response = client.get(url)
            return response.status_code, response.text[:MAX_BODY]
    except httpx.TransportError:
        return 0, ""


def http_post_initialize(url: str) -> tuple[int, str]:
    status, _content_type, body = post_initialize(url)
    return status, body


# Bounds on the complete Engineering catalog read by the ready path. A catalog
# that is still paginating at the page bound, or larger than the tool bound, is
# incomplete and fails: an unread later page could hold a delegated read.
MAX_CATALOG_PAGES = 32
MAX_CATALOG_TOOLS = 4096
# The rollback state (design section 4: an 8.5.0 app with the ``/catalog/full``
# suffix left in place) keeps the delegated surface fail-closed, so the exact
# expected delegated-read set is empty. Every name the Engineering catalog
# lists outside the frozen static catalog (``ENGINEERING_STATIC_TOOL_NAMES``,
# frozen in ``tools/registry.py`` "before any admitted upstream reads are
# added") is a delegated upstream read.
ROLLBACK_EXPECTED_DELEGATED_READS: frozenset[str] = frozenset()
# ``upstream_read_gateway.admission_status`` values that the gateway sets only
# together with zero exposed delegated reads (``providers/upstream_read_gateway.py``:
# the initial state, the disable-delegation failure path and the reprobe reset).
UNADMITTED_STATUSES = frozenset({"unavailable", "blocked_incompatible_upstream", "compatibility_reprobe_pending"})
ADMITTED_STATUSES = frozenset({"admitted_exact", "partially_admitted"})


def engineering_static_tool_names() -> frozenset[str]:
    """Return the frozen static Engineering catalog of the checked-out source.

    The lane builds the image under test from the same checkout, so this is the
    image's own static catalog. Imported lazily: only the ready path needs it.
    """

    beta = str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta")
    if beta not in sys.path:
        sys.path.insert(0, beta)
    from ha_mcp_engineering.tools import ENGINEERING_STATIC_TOOL_NAMES

    return frozenset(ENGINEERING_STATIC_TOOL_NAMES)


async def collect_catalog(session: Any) -> dict[str, Any]:
    """Read every ``tools/list`` page, bounded; report whether the read completed."""

    from mcp.types import PaginatedRequestParams

    pages: list[list[str]] = []
    cursor: str | None = None
    seen: set[str] = set()
    complete = False
    for _ in range(MAX_CATALOG_PAGES):
        listed = await (session.list_tools(params=PaginatedRequestParams(cursor=cursor))
                        if cursor is not None else session.list_tools())
        pages.append([tool.name for tool in listed.tools])
        cursor = getattr(listed, "nextCursor", None)
        if cursor is None:
            complete = True
            break
        if cursor in seen:
            break
        seen.add(cursor)
    return {"pages": pages, "catalog_complete": complete}


async def engineering_catalog(url: str) -> dict[str, Any]:
    """Read the complete Engineering catalog and the gateway admission through MCP."""

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            catalog = await collect_catalog(session)
            try:
                health = await session.call_tool("get_server_health", {"check_ha": False})
            except Exception:  # bounded: an MCP error is health evidence that failed
                return {**catalog, "health_is_error": True, "health_text": ""}
            text = "".join(getattr(item, "text", "") or "" for item in getattr(health, "content", ()))
            return {**catalog, "health_is_error": getattr(health, "isError", False) is not False,
                    "health_text": text[:MAX_BODY]}


def unadmitted_gateway(is_error: Any, text: Any) -> dict[str, Any]:
    """Return the gateway block of a valid health result that is positively unadmitted.

    Missing, errored or malformed health fails, and so does any admission state
    other than an explicit unadmitted status with zero exposed delegated reads.
    The absence of an admitted marker is never evidence.
    """

    require(is_error is False, "fail_closed_health_error")
    require(isinstance(text, str) and text.strip() != "", "fail_closed_health_missing")
    try:
        envelope = json.loads(text)
    except ValueError:
        raise LaneFailure("fail_closed_health_malformed") from None
    require(isinstance(envelope, dict), "fail_closed_health_malformed")
    require(envelope.get("success") is True and "error_code" not in envelope, "fail_closed_health_error")
    data = envelope.get("data")
    gateway = data.get("upstream_read_gateway") if isinstance(data, dict) else None
    require(envelope.get("operation") == "get_server_health" and isinstance(gateway, dict),
            "fail_closed_health_malformed")
    status = gateway.get("admission_status")
    require(status not in ADMITTED_STATUSES, "fail_closed_gateway_admitted")
    require(status in UNADMITTED_STATUSES, "fail_closed_gateway_unadmission_unproven")
    exposed = (gateway.get("dynamically_exposed_count"), data.get("dynamic_upstream_tool_count"))
    require(all(type(count) is int for count in exposed), "fail_closed_health_malformed")
    require(exposed == (0, 0) and gateway.get("generic_delegation_available") is False,
            "fail_closed_gateway_exposes_reads")
    return gateway


def delegated_reads(catalog: dict[str, Any], static_names: frozenset[str] | None) -> set[str]:
    """Return the complete delegated-read set of a complete, bounded catalog."""

    require(isinstance(static_names, frozenset) and bool(static_names), "fail_closed_static_catalog_unknown")
    pages = catalog.get("pages")
    require(isinstance(pages, list) and bool(pages) and all(isinstance(page, list) for page in pages),
            "fail_closed_catalog_malformed")
    require(catalog.get("catalog_complete") is True and len(pages) <= MAX_CATALOG_PAGES,
            "fail_closed_catalog_incomplete")
    names = [name for page in pages for name in page]
    require(all(isinstance(name, str) for name in names), "fail_closed_catalog_malformed")
    require(len(names) <= MAX_CATALOG_TOOLS, "fail_closed_catalog_incomplete")
    require(len(names) == len(set(names)), "fail_closed_catalog_duplicate_name")
    return set(names) - static_names


def evaluate_fail_closed(samples: list[tuple[int, str]], mcp_attempt: tuple[int, str] | None,
                         catalog: dict[str, Any] | None,
                         static_names: frozenset[str] | None = None) -> dict[str, Any]:
    """Accept only a delegated surface that never became usable.

    Either readiness never reached 200 and the authenticated endpoint refused a
    session (no ``tools/call`` is possible), or Engineering became ready and
    positively shows the rollback state: the complete delegated-read set equals
    the exact expected set (empty), and valid health reports the gateway
    explicitly unadmitted with nothing exposed. A redirect category anywhere
    fails: the peer answers 404, not a 3xx.
    """

    require(samples, "fail_closed_no_samples")
    texts = [body for _, body in samples] + ([mcp_attempt[1]] if mcp_attempt else [])
    texts += [str(catalog.get("health_text") or "")] if isinstance(catalog, dict) else []
    require(not any(REDIRECT_CATEGORY in text for text in texts), "fail_closed_redirect_category_present")
    ready = [status for status, _ in samples]
    # Status 0 means nothing was listening yet. Engineering must have answered
    # readiness at least once, so a container that never started cannot pass.
    require(any(status != 0 for status in ready), "fail_closed_engineering_never_answered")
    if all(status != 200 for status in ready):
        require(mcp_attempt is not None and mcp_attempt[0] >= 400 and not 300 <= mcp_attempt[0] < 400,
                "fail_closed_session_not_refused")
        return {"path": "never_ready", "ready_statuses": sorted(set(ready)), "session_status": mcp_attempt[0]}
    require(isinstance(catalog, dict), "fail_closed_ready_without_catalog")
    delegated = delegated_reads(catalog, static_names)
    require(not delegated - ROLLBACK_EXPECTED_DELEGATED_READS, "fail_closed_delegated_read_exposed")
    require(not ROLLBACK_EXPECTED_DELEGATED_READS - delegated, "fail_closed_expected_delegated_read_missing")
    gateway = unadmitted_gateway(catalog.get("health_is_error"), catalog.get("health_text"))
    return {"path": "ready_without_delegated_reads", "ready_statuses": sorted(set(ready)),
            "engineering_tool_count": sum(len(page) for page in catalog["pages"]),
            "catalog_pages": len(catalog["pages"]), "delegated_read_count": len(delegated),
            "admission_status": gateway.get("admission_status")}


def command_fail_closed(args: argparse.Namespace) -> int:
    form = classify_form(args.configured_url)
    try:
        require(form == args.form, "endpoint_form_mismatch")
        samples = []
        deadline = time.monotonic() + args.observe_seconds
        while True:
            try:
                samples.append(http_get(args.ready_url))
            except OSError:
                samples.append((0, ""))
            if time.monotonic() >= deadline:
                break
            time.sleep(3)
        catalog = None
        mcp_attempt = None
        static_names = None
        if all(status != 200 for status, _ in samples):
            mcp_attempt = http_post_initialize(args.engineering_endpoint)
        else:
            catalog = asyncio.run(engineering_catalog(args.engineering_endpoint))
            static_names = engineering_static_tool_names()
        observed = {"classified_form": form, **evaluate_fail_closed(samples, mcp_attempt, catalog, static_names)}
        result = case_result(args.case, "passed", form=form, observed=observed)
    except (LaneFailure, ValueError, OSError) as exc:
        category = str(exc) if isinstance(exc, LaneFailure) else type(exc).__name__
        result = case_result(args.case, "failed", form=form, reason=category)
    except Exception as exc:  # bounded: no exception text is retained
        result = case_result(args.case, "failed", form=form, reason=type(exc).__name__)
    write_json(Path(args.output), result)
    return 0 if result["status"] == "passed" else 1


# --------------------------------------------------------------------------
# Wrap an existing acceptance script's bounded result
# --------------------------------------------------------------------------

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


def record_source(name: str, form: str | None, source: Path) -> dict[str, Any]:
    if not source.is_file():
        return case_result(name, "failed", form=form, reason="acceptance_result_missing")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return case_result(name, "failed", form=form, reason="acceptance_result_unreadable")
    if not isinstance(value, dict) or value.get("result") != "PASS":
        reason = value.get("failure_type") if isinstance(value, dict) else None
        return case_result(name, "failed", form=form, reason="acceptance_result_not_pass"
                           + (": " + bounded(reason, 96) if isinstance(reason, str) else ""))
    observed: dict[str, Any] = {}
    protocols = {item for item in find_values(value, "observed_protocol_version") if isinstance(item, str)}
    if len(protocols) == 1:
        observed["negotiated_protocol"] = protocols.pop()
    elif protocols:
        return case_result(name, "failed", form=form, reason="acceptance_protocol_inconsistent")
    return case_result(name, "passed", form=form, observed=observed)


def command_record(args: argparse.Namespace) -> int:
    require(args.configuration in CASES and args.case in case_names(args.configuration), "record_case_unlisted")
    result = record_source(args.case, case_form(args.configuration, args.case), Path(args.source))
    write_json(Path(args.output), result)
    return 0 if result["status"] == "passed" else 1


# --------------------------------------------------------------------------
# Manifest writer
# --------------------------------------------------------------------------

def load_case_results(cases_dir: Path) -> dict[str, dict[str, Any]]:
    results = {}
    if cases_dir.is_dir():
        for path in sorted(cases_dir.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            require(isinstance(value, dict) and value.get("case") == path.stem, "case_result_malformed")
            results[path.stem] = value
    return results


def build_manifest(row: dict[str, Any], engineering_commit: str,
                   results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    configuration, architecture, state = row.get("configuration"), row.get("architecture"), row.get("state")
    require(configuration in CASES and architecture in ARCHITECTURES and state in STATES, "manifest_row_invalid")
    require(COMMIT.fullmatch(engineering_commit or "") is not None, "manifest_engineering_commit_invalid")
    listed = case_names(configuration)
    require(set(results) <= set(listed), "manifest_unlisted_case_result")
    manifest: dict[str, Any] = {
        "schema": SCHEMA, "configuration": configuration, "architecture": architecture,
        "runner": row.get("runner"), "state": state, "engineering_commit": engineering_commit,
    }
    if state == "dormant":
        reason = bounded(row.get("dormant_reason") or "")
        require(reason != "", "manifest_dormant_reason_missing")
        # A dormant lane never executes anything; a case result here means a
        # step ran that must not have, so the lane refuses instead of reporting.
        require(not results, "manifest_dormant_lane_has_case_results")
        manifest.update(result="NOT_EXECUTED", dormant_reason=reason, images=[], endpoint_forms=[],
                        negotiated_protocol=None, skips=[], case_count=len(listed), executed_count=0,
                        cases=[{"name": case.name, "endpoint_form": case.endpoint_form,
                                "status": "not_executed", "reason": reason} for case in CASES[configuration]])
        return manifest
    cases, images, protocols, forms, skips = [], [], set(), set(), []
    for case in CASES[configuration]:
        value = results.get(case.name)
        if value is None:
            cases.append({"name": case.name, "endpoint_form": case.endpoint_form, "status": "not_executed",
                          "reason": "no result: this listed case was not executed in this lane"})
            continue
        status = value.get("status")
        require(status in ("passed", "failed", "skipped"), "manifest_case_status_invalid")
        require(value.get("endpoint_form") == case.endpoint_form, "manifest_case_form_mismatch")
        reason = bounded(value.get("reason") or "")
        if status == "skipped":
            require(reason != "", "manifest_skip_without_reason")
            skips.append({"case": case.name, "reason": reason})
        else:
            if case.endpoint_form:
                forms.add(case.endpoint_form)
            observed = value.get("observed") or {}
            if isinstance(observed.get("negotiated_protocol"), str):
                protocols.add(observed["negotiated_protocol"])
            if status == "passed" and case.name.startswith("image_identity_"):
                images.append({key: observed.get(key) for key in sorted(IMAGE_KEYS)})
        cases.append({"name": case.name, "endpoint_form": case.endpoint_form, "status": status, "reason": reason})
    executed = sum(case["status"] in ("passed", "failed") for case in cases)
    passed = all(case["status"] == "passed" for case in cases) and len(protocols) == 1
    manifest.update(
        result="PASS" if passed else "FAIL", dormant_reason=None, images=images,
        endpoint_forms=sorted(forms),
        negotiated_protocol=next(iter(protocols)) if len(protocols) == 1 else None,
        skips=skips, case_count=len(listed), executed_count=executed, cases=cases)
    return manifest


def command_write_manifest(args: argparse.Namespace) -> int:
    row = json.loads(args.row)
    manifest = build_manifest(row, args.engineering_commit, load_case_results(Path(args.cases_dir)))
    write_json(Path(args.output), manifest)
    print(f"exact-image admission {manifest['configuration']} {manifest['architecture']}: "
          f"{manifest['result']} ({manifest['executed_count']}/{manifest['case_count']} executed)")
    if manifest["state"] == "dormant":
        print(f"::notice::{manifest['configuration']} {manifest['architecture']} dormant, "
              f"{manifest['dormant_reason']}")
        return 0
    return 0 if manifest["result"] == "PASS" else 1


# --------------------------------------------------------------------------
# Gate over all lanes
# --------------------------------------------------------------------------

def verify_manifests(manifests: list[dict[str, Any]], matrix: dict[str, Any],
                     engineering_commit: str) -> tuple[dict[str, str], list[str]]:
    """Return (per-configuration acceptance, errors). Any error fails the gate."""

    errors: list[str] = []
    rows = matrix.get("include") if isinstance(matrix, dict) else None
    expected: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        key = (row.get("configuration"), row.get("architecture")) if isinstance(row, dict) else None
        if key in expected:
            errors.append(f"matrix lane {key} is duplicated")
        expected[key] = row
    for key in sorted(REQUIRED_LANES - set(expected)):
        errors.append(f"required lane {key[0]} {key[1]} is missing from the derived matrix")
    for key in sorted(set(expected) - REQUIRED_LANES, key=str):
        errors.append(f"matrix lane {key} is not a section 5.1 lane")
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for manifest in manifests:
        key = (manifest.get("configuration"), manifest.get("architecture")) if isinstance(manifest, dict) else None
        if key not in REQUIRED_LANES:
            errors.append(f"manifest {key} is not a section 5.1 lane")
            continue
        if key in seen:
            errors.append(f"lane {key[0]} {key[1]} wrote more than one manifest")
            continue
        seen[key] = manifest
    for key in sorted(REQUIRED_LANES - set(seen)):
        errors.append(f"lane {key[0]} {key[1]} wrote no manifest (missing lane)")
    lane_ok: dict[tuple[str, str], str] = {}
    for key, manifest in sorted(seen.items()):
        label = f"lane {key[0]} {key[1]}"
        problems = manifest_problems(manifest, expected.get(key), engineering_commit)
        errors.extend(f"{label}: {problem}" for problem in problems)
        lane_ok[key] = "error" if problems else manifest.get("result")
    acceptance = {}
    for configuration in CONFIGURATIONS:
        states = [lane_ok.get((configuration, arch)) for arch in ARCHITECTURES]
        if all(state == "PASS" for state in states):
            acceptance[configuration] = "ACCEPTED"
        elif all(state == "NOT_EXECUTED" for state in states):
            acceptance[configuration] = "NOT ACCEPTED (dormant: not executed)"
        else:
            acceptance[configuration] = "NOT ACCEPTED"
    return acceptance, errors


def manifest_problems(manifest: dict[str, Any], row: dict[str, Any] | None, engineering_commit: str) -> list[str]:
    problems: list[str] = []
    if set(manifest) != MANIFEST_KEYS or manifest.get("schema") != SCHEMA:
        return ["manifest schema is not " + SCHEMA]
    configuration = manifest["configuration"]
    if row is None:
        return ["no derived matrix row for this lane"]
    if manifest["state"] != row.get("state"):
        problems.append(f"state {manifest['state']!r} differs from the derived state {row.get('state')!r}")
    if manifest["engineering_commit"] != engineering_commit:
        problems.append("Engineering commit is not this run's commit")
    cases = manifest["cases"]
    names = tuple(case.get("name") for case in cases) if isinstance(cases, list) else ()
    if names != case_names(configuration):
        problems.append("case list differs from the section 5.1 list")
        return problems
    statuses = [case.get("status") for case in cases]
    executed = sum(status in ("passed", "failed") for status in statuses)
    if manifest["case_count"] != len(names) or manifest["executed_count"] != executed:
        problems.append("case or executed count does not match the case list")
    if manifest["skips"] != [{"case": c["name"], "reason": c.get("reason")} for c in cases if c["status"] == "skipped"]:
        problems.append("skip list does not match the skipped cases")
    if manifest["state"] == "dormant":
        if manifest["result"] != "NOT_EXECUTED":
            problems.append(f"dormant lane reported {manifest['result']!r}; a dormant lane is NOT_EXECUTED")
        if executed or any(status != "not_executed" for status in statuses):
            problems.append("dormant lane reports executed cases")
        if manifest["images"] or manifest["negotiated_protocol"] is not None or not manifest["dormant_reason"]:
            problems.append("dormant lane carries execution evidence or no reason")
        return problems
    if manifest["result"] != "PASS":
        problems.append(f"result is {manifest['result']!r}")
    unexecuted = [c["name"] for c in cases if c["status"] == "not_executed"]
    if unexecuted:
        problems.append("listed but not executed: " + ", ".join(unexecuted))
    skipped = [c["name"] for c in cases if c["status"] == "skipped"]
    if skipped:
        problems.append("skipped (completion blocker, never downgraded): " + ", ".join(skipped))
    failed = [c["name"] for c in cases if c["status"] == "failed"]
    if failed:
        problems.append("failed: " + ", ".join(failed))
    if executed != len(names):
        problems.append(f"executed {executed} of {len(names)} listed cases")
    if manifest["negotiated_protocol"] != EXPECTED_PROTOCOL:
        problems.append(f"negotiated protocol {manifest['negotiated_protocol']!r} is not {EXPECTED_PROTOCOL}")
    expected_forms = sorted({c.endpoint_form for c in CASES[configuration] if c.endpoint_form})
    if manifest["endpoint_forms"] != expected_forms:
        problems.append("endpoint forms differ from the case list")
    problems.extend(image_problems(manifest, row))
    return problems


def image_problems(manifest: dict[str, Any], row: dict[str, Any]) -> list[str]:
    peers = row.get("peers") if isinstance(row.get("peers"), dict) else {}
    images = manifest["images"] if isinstance(manifest["images"], list) else []
    by_role = {image.get("role"): image for image in images if isinstance(image, dict)}
    problems = []
    if len(by_role) != len(images) or set(by_role) != set(ROLES[manifest["configuration"]]):
        return ["image identities do not cover exactly the configuration's peers"]
    for role, image in sorted(by_role.items()):
        peer = peers.get(role) if isinstance(peers.get(role), dict) else {}
        platform = (peer.get("architecture_image_digests") or {}).get("linux/" + manifest["architecture"])
        if set(image) != IMAGE_KEYS or not all(DIGEST.fullmatch(str(image[k])) for k in
                                                ("image_digest", "manifest_digest", "config_digest")):
            problems.append(f"{role} image identity is incomplete")
        elif (image["image_digest"] != peer.get("image_index_digest") or image["manifest_digest"] != platform
              or image["version"] != peer.get("upstream_version")):
            problems.append(f"{role} image identity differs from the reviewed registry row")
    return problems


def command_gate(args: argparse.Namespace) -> int:
    manifests = []
    for path in sorted(Path(args.manifests_dir).rglob("*.json")):
        try:
            manifests.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            manifests.append({"configuration": None, "architecture": path.name})
    acceptance, errors = verify_manifests(manifests, json.loads(args.admission_matrix), args.engineering_commit)
    summary = {"schema": SCHEMA + "#gate", "acceptance": acceptance, "errors": errors,
               "result": "FAIL" if errors else "PASS",
               "lanes": [{"configuration": m.get("configuration"), "architecture": m.get("architecture"),
                          "state": m.get("state"), "result": m.get("result"),
                          "executed_count": m.get("executed_count"), "case_count": m.get("case_count")}
                         for m in manifests if isinstance(m, dict)]}
    write_json(Path(args.output), summary)
    lines = ["## HAMCP-319 exact-image admission lanes (design section 5.1)", "",
             "| Configuration | Acceptance |", "|---|---|"]
    lines += [f"| {name} | {value} |" for name, value in acceptance.items()]
    lines += ["", "| Lane | State | Result | Executed |", "|---|---|---|---|"]
    lines += [f"| {lane['configuration']} {lane['architecture']} | {lane['state']} | {lane['result']} | "
              f"{lane['executed_count']}/{lane['case_count']} |" for lane in summary["lanes"]]
    if errors:
        lines += ["", "**Gate errors:**", *[f"- {error}" for error in errors]]
    text = "\n".join(lines) + "\n"
    print(text)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as stream:
            stream.write(text)
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("identity")
    p.add_argument("--peers", required=True)
    p.add_argument("--configuration", choices=CONFIGURATIONS, required=True)
    p.add_argument("--architecture", choices=ARCHITECTURES, required=True)
    p.add_argument("--cases-dir", required=True)
    p = sub.add_parser("initialize")
    p.add_argument("--case", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--form", choices=FORMS, required=True)
    p.add_argument("--expect", choices=("served", "refused"), required=True)
    p.add_argument("--expected-version")
    p.add_argument("--output", required=True)
    p = sub.add_parser("fail-closed")
    p.add_argument("--case", required=True)
    p.add_argument("--configured-url", required=True)
    p.add_argument("--form", choices=FORMS, required=True)
    p.add_argument("--ready-url", required=True)
    p.add_argument("--engineering-endpoint", required=True)
    p.add_argument("--observe-seconds", type=float, default=40.0)
    p.add_argument("--output", required=True)
    p = sub.add_parser("record")
    p.add_argument("--configuration", choices=CONFIGURATIONS, required=True)
    p.add_argument("--case", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("write-manifest")
    p.add_argument("--row", required=True)
    p.add_argument("--engineering-commit", required=True)
    p.add_argument("--cases-dir", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("gate")
    p.add_argument("--manifests-dir", required=True)
    p.add_argument("--admission-matrix", required=True)
    p.add_argument("--engineering-commit", required=True)
    p.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        return {"identity": command_identity, "initialize": command_initialize,
                "fail-closed": command_fail_closed, "record": command_record,
                "write-manifest": command_write_manifest, "gate": command_gate}[args.command](args)
    except LaneFailure as exc:
        print(f"exact-image admission lane refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
