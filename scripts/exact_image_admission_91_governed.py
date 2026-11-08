"""HAMCP-319 #407 Part C (F2): governed dashboard and typed controls through the built image.

Section 5.1 cases ``91_governed_dashboard_verified_readback`` and
``91_typed_public_verified_readback`` (91_only). Unlike the in-process typed
case, every operation here goes through the BUILT Engineering image's public
MCP endpoint, the same one a client uses:

* the Engineering lane image (``engineering-admission-lane:test``, built from
  this commit) runs with host networking against
* a digest-pinned disposable Home Assistant Core from the reviewed support
  window (published on 127.0.0.1 only) and
* the digest-pinned ha-mcp 9.1.0 peer from the lane's reviewed registry row on
  the strict ``/synthetic-upstream-secret/mcp/catalog/full`` endpoint, reached
  through a loopback recording proxy that is this runner's peer request log.

The Core typed authority is a signed registry cache written by the image's own
``CoreReleaseRegistry`` from an envelope signed with an Ed25519 key generated
in memory for this run (never written, never logged). External approval goes
through the image's real Ingress approval page from the Supervisor Ingress peer
address (172.30.32.2) on a disposable Docker network.

Each required operation is recorded with the SHA-256 of its request, its
response and its readback plus the raw facts its verdict is derived from. The
same pure validator re-derives every verdict when the case file is written,
when the lane manifest is written and when the gate reads the manifest, so a
missing, failed or unverified operation can never be reported as a pass. The
script writes exactly two case files and never writes ``skipped``.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT / "scripts", ROOT / "hass_mcp_engineering_beta"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from exact_image_admission_lanes import (  # noqa: E402
    EXPECTED_PROTOCOL, LaneFailure, bounded, case_result, require, write_json,
)

CASE_DASHBOARD = "91_governed_dashboard_verified_readback"
CASE_TYPED = "91_typed_public_verified_readback"
CASES = (CASE_DASHBOARD, CASE_TYPED)
FORM = "strict_full"
JOB = "exact-image-admission-lanes"
UPSTREAM_VERSION = "9.1.0"
SECRET_PATH = "/synthetic-upstream-secret/mcp"
STRICT_PATH = SECRET_PATH + "/catalog/full"
ACCESS_SECRET = "synthetic-engineering-access-secret"
LANE_IMAGE = "engineering-admission-lane:test"
ENGINEERING_IMAGES = (LANE_IMAGE,)
ADDON_HOST = "abcdef12-ha-mcp"
CORE_PORT, PEER_PORT, PROXY_PORT, MCP_PORT, INGRESS_PORT = 18450, 18451, 18452, 18453, 18454
PREFIX = "governed91"
LABEL = "io.hass-mcp.governed91"
INGRESS_SUBNET, INGRESS_GATEWAY, INGRESS_PEER = "172.30.32.0/23", "172.30.32.1", "172.30.32.2"
INGRESS_PATH = "/api/hassio_ingress/hamcpGoverned91LaneToken"
DASHBOARD_URL_PATH = "hamcp-governed"
NEW_TITLE = "After governed apply"
GUIDE_TOOL, SETTER_TOOL, SERVICE_TOOL = "ha_get_skill_guide", "ha_config_set_dashboard", "ha_call_service"
GUIDE_ARGUMENTS = {"file": "references/dashboard-guide.md"}
GUIDE_RECEIPT = re.compile(r"I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-[0-9a-f]{8}")
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX32 = re.compile(r"[0-9a-f]{32}")
CONFIG_HASH = re.compile(r"[0-9a-f]{16}")
MAX_LOG_ENTRIES = 4000
MAX_CAPTURE = 4 * 1024 * 1024

SEED_CONFIG = {"title": "HAMCP governed lane", "views": [{"title": "Before", "path": "governed", "cards": []}]}
PATCHED_CONFIG = {"title": "HAMCP governed lane", "views": [{"title": NEW_TITLE, "path": "governed", "cards": []}]}
PATCH = [{"operation_id": "retitle-view", "operation": "replace", "path": "/views/0/title", "value": NEW_TITLE}]

DASHBOARD_OPERATIONS = (
    "dashboard_list",
    "dashboard_get",
    "dashboard_plan",
    "dashboard_apply_before_approval_refused",
    "dashboard_approval_request",
    "dashboard_ingress_non_supervisor_refused",
    "dashboard_ingress_approval",
    "dashboard_apply",
    "dashboard_bps_guide_read",
    "dashboard_verify",
    "dashboard_core_readback",
)
FAN = "fan.hamcp_contract_fan"
LIGHT, SWITCH = "light.hamcp_contract_light", "switch.hamcp_contract_switch"
PROVIDERS = {"control_power": "upstream_typed_power", "control_fan": "upstream_typed_fan",
             "control_helper": "upstream_typed_helper_value"}
# operation id -> (public tool, arguments without operation_id, expected Core state projection)
TYPED_TABLE: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {
    "power_light_turn_on": ("control_power", {"entity_id": LIGHT, "action": "turn_on"}, {"state": "on"}),
    "power_light_turn_off": ("control_power", {"entity_id": LIGHT, "action": "turn_off"}, {"state": "off"}),
    "power_switch_turn_on": ("control_power", {"entity_id": SWITCH, "action": "turn_on"}, {"state": "on"}),
    "power_switch_turn_off": ("control_power", {"entity_id": SWITCH, "action": "turn_off"}, {"state": "off"}),
    "fan_turn_on": ("control_fan", {"entity_id": FAN, "action": "turn_on", "percentage": 50},
                    {"state": "on", "percentage": 50}),
    "fan_set_percentage": ("control_fan", {"entity_id": FAN, "action": "set_percentage", "percentage": 75},
                           {"state": "on", "percentage": 75}),
    "fan_turn_off": ("control_fan", {"entity_id": FAN, "action": "turn_off"}, {"state": "off"}),
    "helper_input_boolean_turn_on": ("control_helper", {"entity_id": "input_boolean.hamcp_seq17",
                                                        "action": "turn_on"}, {"state": "on"}),
    "helper_input_boolean_turn_off": ("control_helper", {"entity_id": "input_boolean.hamcp_seq17",
                                                         "action": "turn_off"}, {"state": "off"}),
    "helper_input_number_set_value": ("control_helper", {"entity_id": "input_number.hamcp_seq17",
                                                         "action": "set_value", "value": 21.5}, {"state": "21.5"}),
    "helper_input_select_select_option": ("control_helper", {"entity_id": "input_select.hamcp_seq17",
                                                             "action": "select_option", "value": "Away"},
                                          {"state": "Away"}),
}
TYPED_OPERATIONS = tuple(TYPED_TABLE)
REQUIRED_OPERATIONS: dict[str, tuple[str, ...]] = {CASE_DASHBOARD: DASHBOARD_OPERATIONS,
                                                   CASE_TYPED: TYPED_OPERATIONS}
COMPACT_KEYS = ("id", "request_sha256", "response_sha256", "readback_sha256", "verified")


# --------------------------------------------------------------------------
# Pure binding and validation (shared by the runner, the manifest and the gate)
# --------------------------------------------------------------------------

def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def operation_record(op_id: str, *, request: Any, response: Any, readback: Any,
                     facts: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """One required operation bound to its request, response and readback digests."""

    record = {"id": op_id, "request_sha256": sha256(request), "response_sha256": sha256(response),
              "readback": readback, "readback_sha256": sha256(readback), "facts": facts}
    record["verified"] = operation_problem(record, context) is None
    return record


def _plan_status(readback: Any) -> Any:
    return readback.get("plan_status") if isinstance(readback, dict) else None


def _dashboard_problem(op: str, facts: dict[str, Any], readback: Any, context: dict[str, Any]) -> str | None:
    url_path = context.get("url_path")
    if op == "dashboard_list":
        if facts.get("success") is not True:
            return "list_dashboards did not succeed"
        if not isinstance(readback, list) or url_path not in readback:
            return "readback: the governed dashboard is not listed"
        return None
    if op == "dashboard_get":
        if facts.get("success") is not True or not CONFIG_HASH.fullmatch(str(facts.get("config_hash"))):
            return "get_dashboard_config did not return a bound configuration"
        if facts.get("expected") != context.get("seed_config") or readback != facts.get("expected"):
            return "readback mismatch: configuration is not the Core seed"
        return None
    if op == "dashboard_plan":
        if facts.get("success") is not True or facts.get("status") != "awaiting_approval":
            return "plan was not created awaiting approval"
        if not HEX32.fullmatch(str(facts.get("plan_id"))) or not facts.get("plan_hash") \
                or facts.get("plan_hash") != context.get("plan_hash"):
            return "plan identity is not bound"
        if _plan_status(readback) != "awaiting_approval":
            return "readback: get_change_plan is not awaiting_approval"
        return None
    if op == "dashboard_apply_before_approval_refused":
        if facts.get("success") is not False or not facts.get("error_code"):
            return "apply before approval was not refused"
        if not isinstance(readback, dict) or readback.get("setter_calls") != 0 \
                or readback.get("plan_status") != "awaiting_approval":
            return "readback: refused apply reached the setter or moved the plan"
        return None
    if op == "dashboard_approval_request":
        if facts.get("success") is not True or facts.get("status") != "approval_pending" \
                or facts.get("approval_action") != "plan_approval" or not facts.get("challenge_id"):
            return "approval request did not create a plan_approval challenge"
        if facts.get("bound_plan_hash") != context.get("plan_hash") or not context.get("plan_hash"):
            return "challenge is not bound to the exact plan hash"
        if _plan_status(readback) != "awaiting_approval":
            return "readback: plan left awaiting_approval before the Ingress decision"
        return None
    if op == "dashboard_ingress_non_supervisor_refused":
        if facts.get("http_status") != 403:
            return "non-Supervisor peer was not refused with 403"
        if _plan_status(readback) != "awaiting_approval":
            return "readback: refused peer changed the plan"
        return None
    if op == "dashboard_ingress_approval":
        rounds = facts.get("rounds")
        if facts.get("peer") != INGRESS_PEER or not isinstance(rounds, list) or not rounds:
            return "approval did not come from the Supervisor Ingress peer"
        if any(r.get("get_status") != 200 or r.get("post_status") != 200 for r in rounds if isinstance(r, dict)) \
                or not all(isinstance(r, dict) for r in rounds):
            return "Ingress review or decision did not answer 200"
        if rounds[-1].get("decision") != "approved":
            return "Ingress decision was not approved"
        if _plan_status(readback) != "approved":
            return "readback: plan is not approved after the Ingress decision"
        return None
    if op == "dashboard_apply":
        if facts.get("success") is not True or facts.get("status") != "applied" \
                or facts.get("task_state") != "succeeded_verified" \
                or facts.get("provider_dispatch_occurred") is not True \
                or facts.get("redispatch_performed") is not False \
                or not HEX32.fullmatch(str(facts.get("task_id"))):
            return "apply did not succeed verified with exactly one dispatch"
        if not isinstance(readback, dict) or readback.get("setter_calls") != 1:
            return "readback: peer request log does not show exactly one setter call"
        return None
    if op == "dashboard_bps_guide_read":
        return bps_problem(readback)
    if op == "dashboard_verify":
        if facts.get("task_state") != "succeeded_verified" or facts.get("terminal") is not True:
            return "execution task is not terminal succeeded_verified"
        if facts.get("expected") != context.get("patched_config") or readback != facts.get("expected"):
            return "readback mismatch: Engineering get_dashboard_config is not the patched configuration"
        return None
    if op == "dashboard_core_readback":
        if facts.get("expected") != context.get("patched_config") or readback != facts.get("expected"):
            return "readback mismatch: Core lovelace/config is not the patched configuration"
        return None
    return "unknown operation"


def bps_problem(log: Any) -> str | None:
    """The internal guide read precedes the single setter, whose key is that receipt."""

    if not isinstance(log, list) or not all(isinstance(item, dict) for item in log):
        return "readback: peer request log is unreadable"
    setters = [i for i, item in enumerate(log) if item.get("tool") == SETTER_TOOL]
    if len(setters) != 1:
        return "readback: peer request log does not show exactly one setter call"
    index = setters[0]
    guides = [item for item in log[:index] if item.get("tool") == GUIDE_TOOL]
    if not guides:
        return "missing guide read: no ha_get_skill_guide call precedes the setter"
    guide = guides[-1]
    if guide.get("arguments") != GUIDE_ARGUMENTS:
        return "guide read arguments are not exactly the dashboard guide"
    receipts = guide.get("receipt_sha256")
    if not isinstance(receipts, list) or not receipts or not all(HEX64.fullmatch(str(r)) for r in receipts):
        return "guide read returned no best-practice receipt"
    key = log[index].get("best_practice_key_sha256")
    if not isinstance(key, str) or key not in receipts:
        return "setter BestPracticeKey is not the guide receipt"
    if log[index].get("mandatory_bps") is not False:
        return "setter MandatoryBPS is not false"
    return None


def _typed_problem(op: str, facts: dict[str, Any], readback: Any, context: dict[str, Any]) -> str | None:
    tool, arguments, expected = TYPED_TABLE[op]
    if facts.get("tool") != tool or facts.get("arguments") != arguments:
        return "public tool or arguments differ from the required operation"
    if facts.get("success") is not True:
        return "public typed call did not succeed"
    if facts.get("outcome") != "succeeded_verified" or facts.get("terminal") is not True:
        return "typed task is not terminal succeeded_verified"
    if facts.get("provider") != PROVIDERS[tool] or facts.get("provider_contract_exact") is not True:
        return "typed provider or contract is not the reviewed 9.1.0 provider"
    if facts.get("provider_attempt_count") != 1 or facts.get("dispatch_intent_recorded") is not True:
        return "typed dispatch is not a single recorded attempt"
    if facts.get("core_binding_version") != context.get("core_version") or not context.get("core_version"):
        return "typed receipt is not bound to the disposable Core version"
    if facts.get("upstream_dispatches") != 1:
        return "peer request log does not show exactly one ha_call_service"
    if facts.get("duplicate_dispatches") != 0 or facts.get("duplicate_same") is not True:
        return "duplicate request dispatched again or returned a different receipt"
    want = dict(expected, **(facts.get("expected_extra") or {}))
    if facts.get("expected") != want or readback != want:
        return "readback mismatch: Core state differs from the expected state"
    return None


def operation_problem(record: Any, context: dict[str, Any]) -> str | None:
    if not isinstance(record, dict):
        return "operation record is malformed"
    op = record.get("id")
    for key in ("request_sha256", "response_sha256", "readback_sha256"):
        if not isinstance(record.get(key), str) or HEX64.fullmatch(record[key]) is None:
            return key + " is not a SHA-256 digest"
    if record["readback_sha256"] != sha256(record.get("readback")):
        return "readback digest does not bind the recorded readback"
    facts = record.get("facts")
    if not isinstance(facts, dict):
        return "operation facts are missing"
    if op in DASHBOARD_OPERATIONS:
        return _dashboard_problem(op, facts, record.get("readback"), context)
    if op in TYPED_TABLE:
        return _typed_problem(op, facts, record.get("readback"), context)
    return "unknown operation"


def case_context(case: str, observed: Any) -> dict[str, Any]:
    """Validation context: fixed expectations, never values the evidence chose for itself."""

    binding = observed.get("binding") if isinstance(observed, dict) and isinstance(observed.get("binding"), dict) \
        else {}
    if case == CASE_DASHBOARD:
        return {"plan_hash": binding.get("plan_hash"), "url_path": DASHBOARD_URL_PATH,
                "seed_config": SEED_CONFIG, "patched_config": PATCHED_CONFIG}
    core_version = observed.get("core_version") if isinstance(observed, dict) else None
    return {"core_version": core_version if binding.get("core_version") == core_version else None}


def case_problem(case: str, observed: Any) -> str | None:
    """The first reason this case's recorded evidence does not prove every required operation."""

    if case not in REQUIRED_OPERATIONS:
        return "not a governed case"
    if not isinstance(observed, dict):
        return "no observed evidence"
    context = case_context(case, observed)
    operations = observed.get("operations")
    if not isinstance(operations, list):
        return "no operation evidence"
    ids = [item.get("id") if isinstance(item, dict) else None for item in operations]
    for op in REQUIRED_OPERATIONS[case]:
        if ids.count(op) == 0:
            return f"operation {op} was not executed"
        if ids.count(op) > 1:
            return f"operation {op} was recorded more than once"
    extra = [op for op in ids if op not in REQUIRED_OPERATIONS[case]]
    if extra:
        return "unexpected operation recorded"
    for item in operations:
        problem = operation_problem(item, context)
        if problem is not None:
            return f"operation {item['id']}: {problem}"
    peer_log = observed.get("peer_log")
    if not isinstance(peer_log, dict) or not isinstance(peer_log.get("forwarded"), int) \
            or peer_log.get("forwarded", 0) <= 0 or peer_log.get("forwarded") != peer_log.get("peer_posts"):
        return "recording proxy and peer access log disagree"
    protocols = observed.get("protocols")
    if not isinstance(protocols, list) or not protocols or set(protocols) != {EXPECTED_PROTOCOL}:
        return "negotiated protocol was not exactly 2025-03-26 on every peer exchange"
    return None


def case_evidence_problem(case: str, result: Any) -> str | None:
    """For the manifest writer: a ``passed`` case file must still prove every operation."""

    if not isinstance(result, dict) or result.get("status") != "passed":
        return None
    return case_problem(case, result.get("observed"))


def compact_operations(case: str, observed: Any) -> list[dict[str, Any]]:
    """Manifest projection; ``verified`` is re-derived, never copied from the case file."""

    if not isinstance(observed, dict) or not isinstance(observed.get("operations"), list):
        return []
    context = case_context(case, observed)
    compact = []
    for item in observed["operations"]:
        if not isinstance(item, dict):
            continue
        entry = {key: item.get(key) for key in COMPACT_KEYS[:-1]}
        entry["verified"] = operation_problem(item, context) is None
        compact.append(entry)
    return compact


def manifest_operations_problem(case: str, operations: Any) -> str | None:
    """For the gate: the manifest must carry every required operation, verified and bound."""

    if case not in REQUIRED_OPERATIONS:
        return None
    if not isinstance(operations, list) or not all(isinstance(item, dict) for item in operations):
        return f"{case}: no operation evidence in the manifest"
    ids = [item.get("id") for item in operations]
    if sorted(ids) != sorted(REQUIRED_OPERATIONS[case]):
        missing = [op for op in REQUIRED_OPERATIONS[case] if op not in ids]
        return f"{case}: required operations missing or duplicated: " + ", ".join(missing or ["duplicate"])
    for item in operations:
        if set(item) != set(COMPACT_KEYS):
            return f"{case}: operation {item.get('id')} evidence is malformed"
        if not all(isinstance(item.get(k), str) and HEX64.fullmatch(item[k])
                   for k in ("request_sha256", "response_sha256", "readback_sha256")):
            return f"{case}: operation {item.get('id')} is not digest-bound"
        if item.get("verified") is not True:
            return f"{case}: operation {item.get('id')} was not verified"
    return None


def evaluate(case: str, observed: dict[str, Any]) -> tuple[str, str]:
    problem = case_problem(case, observed)
    return ("failed", problem) if problem else ("passed", "")


# --------------------------------------------------------------------------
# Plan and entry point
# --------------------------------------------------------------------------

@dataclass
class Plan:
    architecture: str
    peer: dict[str, str]
    core: dict[str, str]
    engineering_image: str
    engineering_version: str
    work_root: Path
    identity: str
    evidence: dict[str, Any] = field(default_factory=dict)


def empty_evidence() -> dict[str, Any]:
    return {case: {"operations": [], "binding": {}, "diagnostics": []} for case in CASES} | {
        "protocols": [], "peer_log": {}}


def build_observed(plan: Plan | None, evidence: dict[str, Any], case: str) -> dict[str, Any]:
    section = evidence.get(case) or {}
    observed: dict[str, Any] = {
        "operations": section.get("operations", []), "binding": section.get("binding", {}),
        "protocols": evidence.get("protocols", []), "peer_log": evidence.get("peer_log", {}),
        "diagnostics": section.get("diagnostics", [])[:20],
    }
    protocols = evidence.get("protocols") or []
    observed["negotiated_protocol"] = protocols[0] if protocols and len(set(protocols)) == 1 else None
    observed["required_operations"] = list(REQUIRED_OPERATIONS[case])
    if plan is not None:
        observed.update(endpoint_path=STRICT_PATH, architecture=plan.architecture,
                        engineering_image=plan.engineering_image, engineering_version=plan.engineering_version,
                        peer_image_index_digest=plan.peer["image_index_digest"],
                        peer_platform_manifest_digest=plan.peer["platform_manifest_digest"],
                        core_version=plan.core["version"], core_image_index_digest=plan.core["image_index_digest"])
    for key in ("engineering_image_id", "peer_image_id", "core_compatible_count", "failure"):
        if key in evidence:
            observed[key] = evidence[key]
    return observed


def job_gate(environ: dict[str, str]) -> None:
    if environ.get("GITHUB_ACTIONS") == "true":
        require(environ.get("GITHUB_JOB") == JOB, "not_exact_image_admission_lanes_job")


Runner = Callable[[Plan], Awaitable[None]]


def main(argv: list[str] | None = None, runner: Runner | None = None,
         environ: dict[str, str] | None = None,
         release_for: Callable[[str], Any] | None = None,
         window_loader: Callable[[], dict] | None = None) -> int:
    import exact_image_admission_91_typed as typed
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--peers", required=True)
    parser.add_argument("--architecture", required=True, choices=("amd64", "arm64"))
    parser.add_argument("--cases-dir", required=True, type=Path)
    parser.add_argument("--engineering-image", default=LANE_IMAGE, choices=ENGINEERING_IMAGES)
    parser.add_argument("--expected-engineering-version", required=True)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args(argv)
    environ = dict(os.environ) if environ is None else environ
    runner = run_lane if runner is None else runner
    plan: Plan | None = None
    evidence = empty_evidence()
    failure = ""
    try:
        job_gate(environ)
        require(re.fullmatch(r"[0-9A-Za-z.+-]{1,64}", args.expected_engineering_version) is not None,
                "engineering_version_malformed")
        peers = json.loads(args.peers)
        peer = typed.select_peer_image(peers, args.architecture, release_for or typed.reviewed_release)
        core = typed.core_image(window_loader)
        work_root = args.work_dir or Path(environ.get("RUNNER_TEMP") or tempfile.gettempdir())
        identity = f"{PREFIX}-{os.getpid()}-{args.architecture}"
        plan = Plan(args.architecture, peer, core, args.engineering_image, args.expected_engineering_version,
                    Path(work_root), identity, evidence)
        previous = signal.getsignal(signal.SIGTERM)

        def terminated(signum, frame):
            raise LaneFailure("lane_terminated")

        signal.signal(signal.SIGTERM, terminated)
        try:
            asyncio.run(runner(plan))
        finally:
            signal.signal(signal.SIGTERM, previous)
    except LaneFailure as exc:
        failure = "lane refused: " + bounded(str(exc), 120)
    except json.JSONDecodeError:
        failure = "lane refused: peers_not_json"
    except Exception as exc:  # bounded category only; never exception text
        failure = "lane error: " + type(exc).__name__[:64]
    exit_code = 0
    summary = []
    for case in CASES:
        try:
            observed = build_observed(plan, evidence, case)
            status, reason = evaluate(case, observed)
        except Exception:
            observed, status, reason = {"negotiated_protocol": None}, "failed", "evidence_unbuildable"
        if failure:
            observed["failure"] = failure
            if status == "passed":
                status, reason = "failed", failure
            else:
                reason = bounded(failure + "; " + reason)
        write_json(args.cases_dir / (case + ".json"),
                   case_result(case, status, form=FORM, reason=reason if status != "passed" else "",
                               observed=observed))
        summary.append({"case": case, "status": status, "reason": reason})
        exit_code |= 0 if status == "passed" else 1
    print(json.dumps(summary), flush=True)
    return exit_code


# --------------------------------------------------------------------------
# Recording proxy: the peer request log
# --------------------------------------------------------------------------

def parse_messages(content_type: str, body: bytes) -> list[dict[str, Any]]:
    """JSON-RPC messages from a JSON or SSE body (bounded, best effort)."""

    text = body.decode("utf-8", "replace")
    chunks = []
    if "text/event-stream" in content_type:
        chunks = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    else:
        chunks = [text]
    messages = []
    for chunk in chunks:
        try:
            value = json.loads(chunk)
        except ValueError:
            continue
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict):
                messages.append(item)
    return messages


def log_entry(request: dict[str, Any], status: int, content_type: str, body: bytes) -> dict[str, Any]:
    """One bounded peer-log entry; receipts and keys are recorded only as SHA-256."""

    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    method = request.get("method")
    entry: dict[str, Any] = {"method": method if isinstance(method, str) else None, "status": status,
                             "response_sha256": hashlib.sha256(body).hexdigest()}
    if method == "tools/call":
        tool = params.get("name")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        entry.update(tool=tool if isinstance(tool, str) else None, arguments_sha256=sha256(arguments))
        if tool == GUIDE_TOOL:
            entry["arguments"] = {k: v for k, v in arguments.items() if isinstance(v, (str, int, bool))}
            receipts = sorted(set(GUIDE_RECEIPT.findall(body.decode("utf-8", "replace"))))
            entry["receipt_sha256"] = [hashlib.sha256(r.encode()).hexdigest() for r in receipts]
        if tool == SETTER_TOOL:
            key = arguments.get("BestPracticeKey")
            entry["best_practice_key_sha256"] = (hashlib.sha256(key.encode()).hexdigest()
                                                 if isinstance(key, str) else None)
            entry["mandatory_bps"] = arguments.get("MandatoryBPS")
            entry["url_path"] = arguments.get("url_path")
    if method == "initialize":
        for message in parse_messages(content_type, body):
            result = message.get("result") if isinstance(message.get("result"), dict) else {}
            if isinstance(result.get("protocolVersion"), str):
                entry["protocol"] = result["protocolVersion"]
    return entry


HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding", "upgrade", "te", "trailer", "host",
       "content-length"}


class RecordingProxy:
    """Loopback pass-through to the peer that logs every JSON-RPC request it forwards."""

    def __init__(self, listen_port: int, peer_port: int):
        self.peer_port = peer_port
        self.entries: list[dict[str, Any]] = []
        self.forwarded = 0
        self.lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):  # never echo paths or headers
                return

            def _forward(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
                headers["Host"] = f"127.0.0.1:{proxy.peer_port}"
                connection = http.client.HTTPConnection("127.0.0.1", proxy.peer_port, timeout=120)
                try:
                    connection.request(method, self.path, body=body or None, headers=headers)
                    response = connection.getresponse()
                    with proxy.lock:
                        if method == "POST":
                            proxy.forwarded += 1
                    content_type = response.getheader("Content-Type") or ""
                    self.send_response(response.status)
                    for key, value in response.getheaders():
                        if key.lower() not in HOP:
                            self.send_header(key, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    captured = bytearray()
                    while True:
                        chunk = response.read1(65536) if hasattr(response, "read1") else response.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        if len(captured) < MAX_CAPTURE:
                            captured.extend(chunk[:MAX_CAPTURE - len(captured)])
                    if method == "POST":
                        try:
                            request = json.loads(body or b"null")
                        except ValueError:
                            request = None
                        for item in request if isinstance(request, list) else [request]:
                            if isinstance(item, dict) and isinstance(item.get("method"), str):
                                entry = log_entry(item, response.status, content_type, bytes(captured))
                                with proxy.lock:
                                    if len(proxy.entries) < MAX_LOG_ENTRIES:
                                        proxy.entries.append(entry)
                except (OSError, http.client.HTTPException):
                    try:
                        self.send_error(502)
                    except OSError:
                        pass
                finally:
                    connection.close()

            def do_POST(self):
                self._forward("POST")

            def do_GET(self):
                self._forward("GET")

            def do_DELETE(self):
                self._forward("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", listen_port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def mark(self) -> int:
        with self.lock:
            return len(self.entries)

    def since(self, mark: int) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(item) for item in self.entries[mark:]]

    def protocols(self) -> list[str]:
        with self.lock:
            return [item["protocol"] for item in self.entries if "protocol" in item]


def calls(entries: list[dict[str, Any]], tool: str) -> int:
    return sum(1 for item in entries if item.get("tool") == tool and item.get("method") == "tools/call")


def bps_projection(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = ("tool", "arguments", "receipt_sha256", "best_practice_key_sha256", "mandatory_bps", "status")
    return [{k: item[k] for k in keep if k in item} for item in entries
            if item.get("method") == "tools/call" and item.get("tool") in (GUIDE_TOOL, SETTER_TOOL)]


# --------------------------------------------------------------------------
# Real lane (Docker; disposable Core, exact peer, BUILT Engineering image)
# --------------------------------------------------------------------------

APPROVAL_CLIENT = r'''
import json, os, re, sys, urllib.error, urllib.parse, urllib.request
base, plan, path, user = (os.environ[k] for k in ("BASE", "PLAN_ID", "INGRESS_PATH", "USER_ID"))
headers = {"X-Ingress-Path": path, "X-Remote-User-Id": user}
def call(method, url, data=None, extra=None):
    request = urllib.request.Request(url, data=data, method=method, headers=dict(headers, **(extra or {})))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read(1 << 20).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read(1 << 20).decode("utf-8", "replace")
rounds = []
for _ in range(3):
    status, page = call("GET", f"{base}/plans/{plan}")
    item = {"get_status": status}
    rounds.append(item)
    if status != 200:
        break
    fields = []
    seen = set()
    for name, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page):
        if name not in seen and name != "action":
            seen.add(name)
            fields.append((name, value.replace("&quot;", '"').replace("&#x27;", "'").replace("&amp;", "&")))
    fields += [("ack_object", v) for v in re.findall(r'name="ack_object" value="([^"]*)"', page)]
    item["fields"] = sorted(seen)
    body = urllib.parse.urlencode(fields).encode()
    status, page = call("POST", f"{base}/plans/{plan}/approve", body,
                        {"Content-Type": "application/x-www-form-urlencoded"})
    item["post_status"] = status
    match = re.search(r"The exact governed plan was <strong>([a-z_]{1,32})</strong>", page)
    item["decision"] = match.group(1) if match else None
    if item["decision"] != "approval_pending":
        break
print(json.dumps({"rounds": rounds}))
'''

SEED_SCRIPT = r'''
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, "/app")
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry, CORE_REGISTRY_CACHE_PATH
signed = Path("/seed/envelope.json").read_bytes()
public_key = Path("/seed/public_key").read_text().strip()
async def fetch(_url, _maximum):
    return signed
async def main():
    registry = CoreReleaseRegistry(enabled=True, public_key=public_key, fetcher=fetch)
    ok = await registry.refresh()
    print(json.dumps({"refreshed": bool(ok), "cache": CORE_REGISTRY_CACHE_PATH.exists()}))
asyncio.run(main())
'''


def docker(*args: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *args], text=True, capture_output=True, timeout=timeout)
    if check:
        require(result.returncode == 0, "docker_" + (args[0] if args else "command") + "_failed")
    return result


def signed_core_authority(core_version: str) -> tuple[bytes, str]:
    """A Core registry envelope signed by a key that exists only in this process."""

    import base64
    from datetime import datetime, timezone
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import core_registry_contract_lane as lane
    from prepare_core_release_registry import prepare_candidate, sign_candidate
    from ha_mcp_engineering.ha_core_readmission.profiles import (
        CORE_CAPABILITY_PROFILES, CORE_TYPED_OPERATION_PROFILES)
    from ha_mcp_engineering.signed_registry import canonical_json
    entry = lane.lane_entry(core_version)
    require(lane._lane_binding(core_version).typed_operations is True, "core_typed_operations_unbound")
    evidence = b"Disposable test authority only; actual contract result follows."
    profiles = CORE_CAPABILITY_PROFILES + CORE_TYPED_OPERATION_PROFILES
    probe = lane.probe_profile_for(core_version)
    entry.update({"evidence_sha256": hashlib.sha256(evidence).hexdigest(),
                  "probe_profile_id": probe.profile_id, "probe_profile_sha256": probe.contract_fingerprint,
                  "capabilities": [{n: p.to_mapping()[n] for n in (
                      "capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")}
                      for p in profiles]})
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    candidate = canonical_json(prepare_candidate(entry=entry, evidence=evidence, previous=None,
                                                 public_key=key.public_key(), now=now))
    signed = sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                            previous=None, key=key, now=now)
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    del key
    return (signed if isinstance(signed, bytes) else canonical_json(signed)), public


def engineering_options(public_key: str) -> dict[str, Any]:
    return {"access_secret": ACCESS_SECRET, "audit_enabled": True, "prewarm_enabled": False,
            "rate_limit_per_minute": 600, "rate_limit_burst": 100, "approval_notification_service": "",
            "upstream_trust_registry_enabled": False, "ha_core_release_registry_enabled": True,
            "ha_core_release_registry_public_key": public_key}


def engineering_command(plan: Plan, data: Path, env_file: Path) -> list[str]:
    require(plan.engineering_image in ENGINEERING_IMAGES, "engineering_image_not_allowed")
    return ["run", "-d", "--name", plan.identity + "-engineering", "--network", "host",
            "--label", f"{LABEL}={plan.identity}", "--security-opt", "no-new-privileges:true",
            "--add-host", f"{ADDON_HOST}:127.0.0.1",
            # The real registry host resolves to loopback: refresh fails closed and keeps the seeded cache.
            "--add-host", "raw.githubusercontent.com:127.0.0.1",
            "--memory", "1g", "-v", f"{data}:/data", "--env-file", str(env_file), plan.engineering_image]


async def mcp_call(session, tool: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(request, decoded response); an MCP error result is a failed response, not an exception."""

    request = {"tool": tool, "arguments": arguments}
    result = await session.call_tool(tool, arguments)
    text = "".join(getattr(item, "text", "") or "" for item in getattr(result, "content", []) or [])
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        value = {"success": False, "error_code": "undecodable_response"}
    if getattr(result, "isError", False):
        value = dict(value, success=False, mcp_is_error=True)
    return request, value


def data_of(response: dict[str, Any]) -> dict[str, Any]:
    return response.get("data") if isinstance(response.get("data"), dict) else {}


def find_key(value: Any, key: str, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for item in value.values():
            found = find_key(item, key, depth + 1)
            if found is not None:
                return found
    return None


def shape(response: dict[str, Any]) -> dict[str, Any]:
    """Bounded diagnostic shape (keys and fixed codes only, never values)."""

    data = data_of(response)
    return {"success": response.get("success"), "error_code": response.get("error_code"),
            "keys": sorted(response)[:20], "data_keys": sorted(data)[:30],
            "reason": bounded(str(find_key(response.get("details"), "reason") or ""), 80)}


class CoreSocket:
    """Minimal authenticated Core websocket for fixtures and independent readback."""

    def __init__(self, url: str, token: str):
        self.url, self.token, self.next_id = url, token, 1

    async def __aenter__(self):
        import aiohttp
        self.client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        self.ws = await self.client.ws_connect(self.url)
        require((await self.ws.receive_json()).get("type") == "auth_required", "core_ws_no_auth_required")
        await self.ws.send_json({"type": "auth", "access_token": self.token})
        require((await self.ws.receive_json()).get("type") == "auth_ok", "core_ws_auth_failed")
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()
        await self.client.close()

    async def command(self, message: dict[str, Any]) -> Any:
        ident, self.next_id = self.next_id, self.next_id + 1
        await self.ws.send_json(dict(message, id=ident))
        while True:
            reply = await self.ws.receive_json()
            if reply.get("id") == ident and reply.get("type") == "result":
                require(reply.get("success") is True, "core_ws_" + str(message.get("type")).replace("/", "_"))
                return reply.get("result")


async def core_state(client, core_url: str, token: str, entity: str) -> dict[str, Any]:
    async with client.get(f"{core_url}/api/states/{entity}", headers={"Authorization": f"Bearer {token}"}) as r:
        require(r.status == 200, "core_state_unreadable")
        return await r.json()


def project(state: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    attributes = state.get("attributes") or {}
    return {key: (state.get("state") if key == "state" else attributes.get(key)) for key in expected}


def durable(receipt: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in receipt.items() if key != "safety"}


async def plan_status(session, plan_id: str) -> Any:
    _, response = await mcp_call(session, "get_change_plan", {"plan_id": plan_id, "detail_section": "summary"})
    return find_key(data_of(response), "status")


async def dashboard_flow(plan: Plan, session, proxy: RecordingProxy, socket: CoreSocket,
                         ingress: Callable[[str], Awaitable[dict[str, Any]]],
                         host_probe: Callable[[str], Awaitable[int]]) -> None:
    section = plan.evidence[CASE_DASHBOARD]
    context = section["binding"]
    context.update(url_path=DASHBOARD_URL_PATH, seed_config=SEED_CONFIG, patched_config=PATCHED_CONFIG)
    ops = section["operations"]

    def record(op, request, response, readback, facts):
        ops.append(operation_record(op, request=request, response=response, readback=readback,
                                    facts=facts, context=context))
        if not ops[-1]["verified"]:
            section["diagnostics"].append({"operation": op, "shape": shape(response)
                                           if isinstance(response, dict) and "success" in response else None})
            raise LaneFailure(op + "_not_verified")

    request, response = await mcp_call(session, "list_dashboards", {"limit": 100})
    listed = sorted({str(item.get("url_path")) for item in (find_key(data_of(response), "dashboards") or [])
                     if isinstance(item, dict) and item.get("url_path")})
    record("dashboard_list", request, response, listed, {"success": response.get("success")})

    request, response = await mcp_call(session, "get_dashboard_config",
                                       {"url_path": DASHBOARD_URL_PATH, "force_reload": True})
    data = data_of(response)
    record("dashboard_get", request, response, data.get("configuration"),
           {"success": response.get("success"), "config_hash": data.get("config_hash"), "expected": SEED_CONFIG})

    request, response = await mcp_call(session, "create_dashboard_update_plan", {
        "title": "HAMCP-319 governed lane retitle", "description": "Disposable lane dashboard update.",
        "url_path": DASHBOARD_URL_PATH, "patch_operations": PATCH, "expiration_minutes": 30})
    data = data_of(response)
    plan_id, plan_hash = str(data.get("plan_id") or ""), str(data.get("plan_hash") or "")
    context["plan_hash"] = plan_hash
    record("dashboard_plan", request, response, {"plan_status": await plan_status(session, plan_id)},
           {"success": response.get("success"), "status": data.get("status"), "plan_id": plan_id,
            "plan_hash": plan_hash, "next_required_operation": data.get("next_required_operation")})

    mark = proxy.mark()
    request, response = await mcp_call(session, "apply_change_plan",
                                       {"plan_id": plan_id, "expected_plan_hash": plan_hash})
    record("dashboard_apply_before_approval_refused", request, response,
           {"plan_status": await plan_status(session, plan_id), "setter_calls": calls(proxy.since(mark), SETTER_TOOL)},
           {"success": response.get("success"), "error_code": response.get("error_code")})

    request, response = await mcp_call(session, "approve_change_plan",
                                       {"plan_id": plan_id, "expected_plan_hash": plan_hash,
                                        "approval_note": "HAMCP-319 disposable lane"})
    data = data_of(response)
    record("dashboard_approval_request", request, response, {"plan_status": await plan_status(session, plan_id)},
           {"success": response.get("success"), "status": data.get("status"),
            "approval_action": data.get("approval_action"), "challenge_id": bool(data.get("challenge_id")),
            "bound_plan_hash": data.get("bound_plan_hash")})

    status = await host_probe(plan_id)
    record("dashboard_ingress_non_supervisor_refused", {"peer": "127.0.0.1", "plan_id": plan_id},
           {"http_status": status}, {"plan_status": await plan_status(session, plan_id)}, {"http_status": status})

    outcome = await ingress(plan_id)
    record("dashboard_ingress_approval", {"peer": INGRESS_PEER, "plan_id": plan_id}, outcome,
           {"plan_status": await plan_status(session, plan_id)},
           {"peer": INGRESS_PEER, "rounds": outcome.get("rounds")})

    mark = proxy.mark()
    request, response = await mcp_call(session, "apply_change_plan",
                                       {"plan_id": plan_id, "expected_plan_hash": plan_hash})
    data = data_of(response)
    task_id = str(data.get("task_id") or "")
    for _ in range(30):
        if data.get("status") != "accepted":
            break
        await asyncio.sleep(2)
        _, polled = await mcp_call(session, "get_execution_task", {"task_id": task_id})
        state = find_key(data_of(polled), "state")
        if state in ("succeeded_verified", "failed", "verification_failed"):
            data = dict(data, status="applied" if state == "succeeded_verified" else state, task_state=state)
    window = proxy.since(mark)
    record("dashboard_apply", request, response, {"setter_calls": calls(window, SETTER_TOOL)},
           {"success": response.get("success"), "status": data.get("status"), "task_state": data.get("task_state"),
            "provider_dispatch_occurred": data.get("provider_dispatch_occurred"),
            "redispatch_performed": data.get("redispatch_performed"), "task_id": task_id})

    guide = [item for item in window if item.get("tool") == GUIDE_TOOL]
    record("dashboard_bps_guide_read", {"log_window": [item.get("arguments_sha256") for item in guide]},
           {"responses": [item.get("response_sha256") for item in guide]}, bps_projection(window), {})

    request, task = await mcp_call(session, "get_execution_task", {"task_id": task_id})
    _, after = await mcp_call(session, "get_dashboard_config", {"url_path": DASHBOARD_URL_PATH, "force_reload": True})
    task_data = data_of(task)
    record("dashboard_verify", request, {"task": task, "config": after}, data_of(after).get("configuration"),
           {"task_state": find_key(task_data, "state"), "terminal": find_key(task_data, "terminal")
            if find_key(task_data, "terminal") is not None else find_key(task_data, "state") == "succeeded_verified",
            "expected": PATCHED_CONFIG})

    core_request = {"type": "lovelace/config", "url_path": DASHBOARD_URL_PATH, "force": True}
    config = await socket.command(core_request)
    record("dashboard_core_readback", core_request, config, config, {"expected": PATCHED_CONFIG})


async def typed_flow(plan: Plan, session, proxy: RecordingProxy, core_url: str, token: str) -> None:
    import aiohttp
    from ha_mcp_engineering.fan.contracts import FAN_SEMANTIC_CONTRACTS
    from ha_mcp_engineering.power.contracts import POWER_SEMANTIC_CONTRACTS
    from ha_mcp_engineering.helper_value.contracts import HELPER_SEMANTIC_CONTRACTS
    from exact_image_admission_91_typed import operation_id
    contracts = {"control_power": POWER_SEMANTIC_CONTRACTS[UPSTREAM_VERSION],
                 "control_fan": FAN_SEMANTIC_CONTRACTS[UPSTREAM_VERSION],
                 "control_helper": HELPER_SEMANTIC_CONTRACTS[UPSTREAM_VERSION]}
    section = plan.evidence[CASE_TYPED]
    context = section["binding"]
    context["core_version"] = plan.core["version"]
    calls_seen: dict[str, int] = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
        for entity in (LIGHT, SWITCH):
            initial = await core_state(client, core_url, token, entity)
            require(initial.get("state") == "off", "power_fixture_not_off")
            calls_seen[entity] = int((initial.get("attributes") or {}).get("synthetic_service_calls") or 0)
        for op, (tool, base, expected) in TYPED_TABLE.items():
            arguments = dict(base, operation_id=operation_id())
            mark = proxy.mark()
            request, response = await mcp_call(session, tool, arguments)
            receipt = data_of(response)
            confirmation = None
            if receipt.get("status") == "confirmation_required":
                confirmation = receipt.get("reason")
                require(receipt.get("dispatch") == "no_dispatch" and receipt.get("task_created") is False
                        and calls(proxy.since(mark), SERVICE_TOOL) == 0, "helper_confirmation_dispatched")
                arguments = dict(arguments, acknowledge_safety_critical=True)
                request, response = await mcp_call(session, tool, arguments)
                receipt = data_of(response)
            task_id = str(receipt.get("task_id") or (response.get("metadata") or {}).get("task_id") or "")
            for _ in range(15):
                if receipt.get("terminal") is True or not HEX32.fullmatch(task_id):
                    break
                await asyncio.sleep(2)
                _, polled = await mcp_call(session, "get_execution_task", {"task_id": task_id})
                if data_of(polled):
                    receipt = data_of(polled)
            first = calls(proxy.since(mark), SERVICE_TOOL)
            _, duplicate = await mcp_call(session, tool, arguments)
            repeated = calls(proxy.since(mark), SERVICE_TOOL) - first
            state = await core_state(client, core_url, token, base["entity_id"])
            extra = {}
            if tool == "control_power":
                calls_seen[base["entity_id"]] += 1
                extra = {"synthetic_service_calls": calls_seen[base["entity_id"]]}
            want = dict(expected, **extra)
            facts = {"tool": tool, "arguments": base, "success": response.get("success"),
                     "outcome": receipt.get("state") if tool != "control_helper" else receipt.get("status"),
                     "terminal": receipt.get("terminal"), "provider": receipt.get("provider"),
                     "provider_contract_exact": receipt.get("provider_contract") == contracts[tool],
                     "provider_attempt_count": receipt.get("provider_attempt_count"),
                     "dispatch_intent_recorded": receipt.get("dispatch_intent_recorded"),
                     "core_binding_version": (receipt.get("core_binding") or {}).get("version"),
                     "upstream_dispatches": first, "duplicate_dispatches": repeated,
                     "duplicate_same": durable(data_of(duplicate)) == durable(receipt),
                     "expected_extra": extra, "expected": want, "confirmation_reason": confirmation}
            section["operations"].append(operation_record(op, request=request, response=response,
                                                          readback=project(state, want), facts=facts,
                                                          context=context))
            if not section["operations"][-1]["verified"]:
                section["diagnostics"].append({"operation": op, "shape": shape(response),
                                               "receipt_keys": sorted(receipt)[:40]})
                raise LaneFailure(op + "_not_verified")


async def guarded(plan: Plan, case: str, step: Awaitable[None]) -> None:
    """One case's failure is recorded and never hides the other case."""

    try:
        await step
    except LaneFailure as exc:
        plan.evidence[case]["diagnostics"].append({"failure": bounded(str(exc), 80)})
    except Exception as exc:
        plan.evidence[case]["diagnostics"].append({"failure": type(exc).__name__[:64]})


def peer_posts(container: str) -> int:
    logs = docker("logs", container, check=False, timeout=60)
    text = (logs.stdout or "") + (logs.stderr or "")
    return len(re.findall(r'"POST ' + re.escape(STRICT_PATH) + r'[^ "]* HTTP/1\.[01]" \d{3}', text))


def cleanup(plan: Plan, work: Path | None, core_image_ref: str) -> None:
    listed = docker("ps", "-aq", "--filter", f"label={LABEL}={plan.identity}", check=False).stdout.split()
    if listed:
        docker("rm", "-f", *listed, check=False, timeout=120)
    for network in (plan.identity, plan.identity + "-ingress"):
        docker("network", "rm", network, check=False)
    if work is not None and work.exists():
        # Core and Engineering write root-owned files; delete them with pinned images.
        for image, sub in ((core_image_ref, "core"), (plan.engineering_image, "data")):
            if (work / sub).exists():
                docker("run", "--rm", "--network", "none", "--label", f"{LABEL}={plan.identity}",
                       "-v", f"{work / sub}:/scratch", "--entrypoint", "find", image,
                       "/scratch", "-mindepth", "1", "-delete", check=False, timeout=180)
        shutil.rmtree(work, ignore_errors=True)


async def run_lane(plan: Plan) -> None:
    logging.disable(logging.CRITICAL)
    import aiohttp
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    import real_ha_contract_tests as existing
    import exact_image_admission_91_typed as typed
    from ha_mcp_engineering.clients import HomeAssistantRestClient

    plan.work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=PREFIX + "-", dir=plan.work_root))
    os.chmod(work, 0o700)
    config, data, seed = work / "core", work / "data", work / "seed"
    common = ["--label", f"{LABEL}={plan.identity}", "--security-opt", "no-new-privileges:true",
              "--pids-limit", "512"]
    proxy: RecordingProxy | None = None
    try:
        typed.docker_disk_preflight()
        arch = plan.architecture
        require(docker("image", "inspect", "--format", "{{.Architecture}}", plan.engineering_image).stdout.strip()
                == arch, "engineering_architecture_mismatch")
        plan.evidence["engineering_image_id"] = docker("image", "inspect", "--format", "{{.Id}}",
                                                       plan.engineering_image).stdout.strip()
        docker("pull", "--platform", "linux/" + arch, plan.core["image"], timeout=900)
        require(docker("image", "inspect", "--format", "{{.Architecture}}", plan.core["image"]).stdout.strip()
                == arch, "core_architecture_mismatch")
        typed.write_core_config(config)
        docker("network", "create", "--label", f"{LABEL}={plan.identity}", plan.identity)
        docker("run", "-d", "--name", plan.identity + "-core", "--network", plan.identity, *common,
               "--memory", "2g", "-p", f"127.0.0.1:{CORE_PORT}:8123", "-v", f"{config}:/config",
               plan.core["image"], timeout=120)
        core_url = f"http://127.0.0.1:{CORE_PORT}"
        existing.HA_URL, existing.CLIENT_ID = core_url, core_url + "/"
        token = await existing.bootstrap_disposable_admin()
        rest = HomeAssistantRestClient(existing.settings(token))
        observed_core = await existing.wait_for_runtime_ready(rest)
        require(observed_core.get("version") == plan.core["version"], "core_version_mismatch")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
            await existing._advance_config_flow(client, token, "beta23_device_fixture", [{"slot": "a"}])
            await existing._advance_config_flow(client, token, "power_contract_fixture", [{}])
        await typed.wait_states(rest, {FAN: "off", LIGHT: "off", SWITCH: "off",
                                       "input_boolean.hamcp_seq17": "off", "input_number.hamcp_seq17": "20.0",
                                       "input_select.hamcp_seq17": "Home"})
        async with CoreSocket(f"ws://127.0.0.1:{CORE_PORT}/api/websocket", token) as socket:
            user = await socket.command({"type": "auth/current_user"})
            require(isinstance(user, dict) and user.get("is_owner") is True
                    and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(user.get("id"))) is not None, "core_owner_unknown")
            owner = str(user["id"])
            await socket.command({"type": "lovelace/dashboards/create", "url_path": DASHBOARD_URL_PATH,
                                  "title": "HAMCP governed lane", "icon": "mdi:view-dashboard",
                                  "show_in_sidebar": True, "require_admin": False})
            await socket.command({"type": "lovelace/config/save", "url_path": DASHBOARD_URL_PATH,
                                  "config": SEED_CONFIG})
            require(await socket.command({"type": "lovelace/config", "url_path": DASHBOARD_URL_PATH,
                                          "force": True}) == SEED_CONFIG, "core_dashboard_seed_mismatch")

            # Core typed authority: signed in memory, cached by the image's own registry writer.
            envelope, public_key = signed_core_authority(plan.core["version"])
            seed.mkdir()
            data.mkdir()
            (seed / "envelope.json").write_bytes(envelope)
            (seed / "public_key").write_text(public_key + "\n", encoding="utf-8")
            (seed / "seed.py").write_text(SEED_SCRIPT, encoding="utf-8")
            seeded = docker("run", "--rm", "--network", "none", *common, "-v", f"{data}:/data",
                            "-v", f"{seed}:/seed:ro", "--entrypoint", "python3", plan.engineering_image,
                            "-I", "/seed/seed.py", timeout=180)
            require(json.loads(seeded.stdout.strip().splitlines()[-1]) == {"refreshed": True, "cache": True},
                    "core_registry_seed_failed")
            (data / "options.json").write_text(json.dumps(engineering_options(public_key)) + "\n", encoding="utf-8")

            # Exact ha-mcp 9.1.0 peer from the reviewed registry row.
            image = plan.peer["image"]
            docker("pull", "--platform", plan.peer["platform"], image, timeout=600)
            require(docker("image", "inspect", "--format", "{{.Architecture}}", image).stdout.strip() == arch,
                    "peer_architecture_mismatch")
            labels = json.loads(docker("image", "inspect", "--format", "{{json .Config.Labels}}", image).stdout
                                or "null") or {}
            require(labels.get("org.opencontainers.image.version") == UPSTREAM_VERSION
                    and labels.get("org.opencontainers.image.revision") == plan.peer["image_revision"],
                    "peer_label_mismatch")
            plan.evidence["peer_image_id"] = docker("image", "inspect", "--format", "{{.Id}}", image).stdout.strip()
            env_file = work / "peer.env"
            with env_file.open("x", encoding="utf-8") as stream:
                os.chmod(env_file, 0o600)
                stream.write("\n".join((
                    f"HOMEASSISTANT_URL={core_url}", f"HOMEASSISTANT_TOKEN={token}",
                    "MCP_HOST=127.0.0.1", f"MCP_PORT={PEER_PORT}", f"MCP_SECRET_PATH={SECRET_PATH}",
                    "HA_MCP_DISABLE_SETTINGS_UI=true", "ENABLE_TOOL_SEARCH=true", "ENABLE_BETA_FEATURES=true",
                    "HAMCP_ENABLE_FILESYSTEM_TOOLS=true", "READ_ONLY_MODE=false")) + "\n")
            try:
                docker("run", "-d", "--name", plan.identity + "-peer", "--network", "host", *common,
                       "--memory", "1g", "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/home/mcpuser/.ha-mcp",
                       "--env-file", str(env_file), image, "ha-mcp-web", timeout=120)
            finally:
                env_file.unlink(missing_ok=True)
            require(docker("inspect", "--format", "{{.Image}}", plan.identity + "-peer").stdout.strip()
                    == plan.evidence["peer_image_id"], "peer_running_image_mismatch")
            await typed.wait_http(f"http://127.0.0.1:{PEER_PORT}{STRICT_PATH}")
            package = docker("exec", plan.identity + "-peer", "python", "-c",
                             "from importlib.metadata import version; print(version('ha-mcp'))").stdout.strip()
            require(package == UPSTREAM_VERSION, "peer_package_version_mismatch")
            proxy = RecordingProxy(PROXY_PORT, PEER_PORT)
            proxy.start()

            # The BUILT Engineering image, with the Supervisor Ingress peer network beside it.
            docker("network", "create", "--subnet", INGRESS_SUBNET, "--gateway", INGRESS_GATEWAY,
                   "--label", f"{LABEL}={plan.identity}", plan.identity + "-ingress")
            env_file = work / "engineering.env"
            with env_file.open("x", encoding="utf-8") as stream:
                os.chmod(env_file, 0o600)
                stream.write("\n".join((
                    f"HA_URL={core_url}", f"HA_TOKEN={token}", f"SUPERVISOR_TOKEN={token}",
                    f"MCP_PORT={MCP_PORT}", f"APPROVAL_INGRESS_PORT={INGRESS_PORT}",
                    f"UPSTREAM_DASHBOARD_MCP_URL=http://{ADDON_HOST}:{PROXY_PORT}{STRICT_PATH}")) + "\n")
            try:
                docker(*engineering_command(plan, data, env_file), timeout=120)
            finally:
                env_file.unlink(missing_ok=True)
            ready = await asyncio.to_thread(_wait_ready, f"http://127.0.0.1:{MCP_PORT}/ready", 240)
            if not ready:
                logs = docker("logs", "--tail", "40", plan.identity + "-engineering", check=False)
                plan.evidence[CASE_DASHBOARD]["diagnostics"].append(
                    {"engineering_log_tail_sha256": hashlib.sha256((logs.stderr or "").encode()).hexdigest()})
                raise LaneFailure("engineering_not_ready")
            client_script = work / "client.py"
            client_script.write_text(APPROVAL_CLIENT, encoding="utf-8")

            async def ingress(plan_id: str) -> dict[str, Any]:
                result = await asyncio.to_thread(docker, "run", "--rm", "--network", plan.identity + "-ingress",
                                                 "--ip", INGRESS_PEER, *common, "-v", f"{client_script}:/client.py:ro",
                                                 "-e", f"BASE=http://{INGRESS_GATEWAY}:{INGRESS_PORT}",
                                                 "-e", f"PLAN_ID={plan_id}", "-e", f"INGRESS_PATH={INGRESS_PATH}",
                                                 "-e", f"USER_ID={owner}", "--entrypoint", "python3",
                                                 plan.engineering_image, "-I", "/client.py", timeout=120)
                return json.loads(result.stdout.strip().splitlines()[-1])

            async def host_probe(plan_id: str) -> int:
                import urllib.error
                import urllib.request
                request = urllib.request.Request(f"http://127.0.0.1:{INGRESS_PORT}/plans/{plan_id}",
                                                 headers={"X-Ingress-Path": INGRESS_PATH, "X-Remote-User-Id": owner})

                def fetch() -> int:
                    try:
                        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - loopback
                            return response.status
                    except urllib.error.HTTPError as error:
                        return error.code
                return await asyncio.to_thread(fetch)

            endpoint = f"http://127.0.0.1:{MCP_PORT}/{ACCESS_SECRET}/mcp"
            async with streamablehttp_client(endpoint, timeout=60, sse_read_timeout=300) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    _, health = await mcp_call(session, "get_server_health", {"check_ha": False})
                    server = find_key(health, "server") or {}
                    require(isinstance(server, dict) and server.get("version") == plan.engineering_version,
                            "engineering_version_mismatch")
                    await guarded(plan, CASE_DASHBOARD,
                                  dashboard_flow(plan, session, proxy, socket, ingress, host_probe))
                    await guarded(plan, CASE_TYPED, typed_flow(plan, session, proxy, core_url, token))
            plan.evidence["protocols"] = proxy.protocols()
            plan.evidence["peer_log"] = {"forwarded": proxy.forwarded,
                                         "peer_posts": peer_posts(plan.identity + "-peer")}
    finally:
        if proxy is not None:
            proxy.stop()
        cleanup(plan, work, plan.core["image"])


def _wait_ready(url: str, seconds: float) -> bool:
    import time
    import urllib.error
    import urllib.request
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - loopback
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    return False


if __name__ == "__main__":
    raise SystemExit(main())
