"""Synthetic, offline evidence for the HAMCP-319 governed 9.1 cases.

Every value here is unmistakably synthetic. The records are built with the
runner's own ``operation_record`` so the digests bind exactly what the
validator reads; tests then break one operation at a time.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / "scripts", ROOT / "hass_mcp_engineering_beta"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

governed = importlib.import_module("exact_image_admission_91_governed")
runners = importlib.import_module("exact_image_admission_91_runners")

PROTOCOL = "2025-03-26"
CORE_VERSION = "2099.1.0-synthetic"
ENGINEERING_VERSION = "9.9.9-synthetic"
ARCHITECTURE = "amd64"
PLAN_ID = "a" * 32
TASK_ID = "b" * 32
PLAN_HASH = "c" * 64
RECEIPT = "I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-0123abcd"
RECEIPT_SHA256 = hashlib.sha256(RECEIPT.encode()).hexdigest()


def version_of(case: str) -> str:
    return governed.CASE_STAGE[case].upstream_version


def bps_log(*, guide: bool = True, arguments: dict | None = None, key: str | None = RECEIPT_SHA256,
            setters: int = 1, version: str = governed.UPSTREAM_VERSION) -> list[dict[str, Any]]:
    log: list[dict[str, Any]] = [{"tool": "ha_get_dashboard_config", "status": 200}]
    if guide:
        log.append({"tool": governed.GUIDE_TOOL, "status": 200,
                    "arguments": dict(governed.GUIDE_ARGUMENTS_BY_VERSION[version] if arguments is None
                                      else arguments),
                    "receipt_sha256": [RECEIPT_SHA256]})
    for _ in range(setters):
        log.append({"tool": governed.SETTER_TOOL, "status": 200, "best_practice_key_sha256": key,
                    "mandatory_bps": False})
    return log


def dashboard_specs(version: str = governed.UPSTREAM_VERSION) -> dict[str, dict[str, Any]]:
    """Operation id -> {readback, facts} for a fully successful governed dashboard run."""

    awaiting = {"plan_status": "awaiting_approval"}
    return {
        "dashboard_list": {"readback": ["energy", governed.DASHBOARD_URL_PATH], "facts": {"success": True}},
        "dashboard_get": {"readback": copy.deepcopy(governed.SEED_CONFIG),
                          "facts": {"success": True, "config_hash": "0123456789abcdef",
                                    "expected": copy.deepcopy(governed.SEED_CONFIG)}},
        "dashboard_plan": {"readback": dict(awaiting),
                           "facts": {"success": True, "status": "awaiting_approval", "plan_id": PLAN_ID,
                                     "plan_hash": PLAN_HASH}},
        "dashboard_apply_before_approval_refused": {
            "readback": {"plan_status": "awaiting_approval", "setter_calls": 0},
            "facts": {"success": False, "error_code": "approval_required"}},
        "dashboard_approval_request": {
            "readback": dict(awaiting),
            "facts": {"success": True, "status": "approval_pending", "approval_action": "plan_approval",
                      "challenge_id": True, "bound_plan_hash": PLAN_HASH}},
        "dashboard_ingress_non_supervisor_refused": {"readback": dict(awaiting), "facts": {"http_status": 403}},
        "dashboard_ingress_approval": {
            "readback": {"plan_status": "approved"},
            "facts": {"peer": governed.INGRESS_PEER,
                      "rounds": [{"get_status": 200, "post_status": 200, "decision": "approved"}]}},
        "dashboard_apply": {
            "readback": {"setter_calls": 1},
            "facts": {"success": True, "status": "applied", "task_state": "succeeded_verified",
                      "provider_dispatch_occurred": True, "redispatch_performed": False, "task_id": TASK_ID}},
        "dashboard_bps_guide_read": {"readback": bps_log(version=version), "facts": {}},
        "dashboard_verify": {"readback": copy.deepcopy(governed.PATCHED_CONFIG),
                             "facts": {"task_state": "succeeded_verified", "terminal": True,
                                       "expected": copy.deepcopy(governed.PATCHED_CONFIG)}},
        "dashboard_core_readback": {"readback": copy.deepcopy(governed.PATCHED_CONFIG),
                                    "facts": {"expected": copy.deepcopy(governed.PATCHED_CONFIG)}},
    }


def typed_specs(core_version: str = CORE_VERSION,
                version: str = governed.UPSTREAM_VERSION) -> dict[str, dict[str, Any]]:
    specs = {}
    counters: dict[str, int] = {}
    for op, (tool, arguments, expected) in governed.TYPED_TABLE.items():
        extra = {}
        if tool == "control_power":
            counters[arguments["entity_id"]] = counters.get(arguments["entity_id"], 0) + 1
            extra = {"synthetic_service_calls": counters[arguments["entity_id"]]}
        want = dict(expected, **extra)
        specs[op] = {"readback": dict(want), "facts": {
            "tool": tool, "arguments": dict(arguments), "success": True, "outcome": "succeeded_verified",
            "terminal": True, "provider": governed.PROVIDERS[tool],
            "provider_contract": governed.PROVIDER_CONTRACTS[version][tool],
            "provider_attempt_count": 1, "dispatch_intent_recorded": True, "core_binding_version": core_version,
            "upstream_dispatches": 1, "duplicate_dispatches": 0, "duplicate_same": True,
            "expected_extra": extra, "expected": dict(want), "confirmation_reason": None}}
    return specs


def specs_for(case: str, core_version: str = CORE_VERSION) -> dict[str, dict[str, Any]]:
    if case in governed.DASHBOARD_CASES:
        return dashboard_specs(version_of(case))
    return typed_specs(core_version, version_of(case))


def binding_for(case: str, core_version: str = CORE_VERSION) -> dict[str, Any]:
    if case in governed.DASHBOARD_CASES:
        return {"plan_hash": PLAN_HASH, "url_path": governed.DASHBOARD_URL_PATH,
                "seed_config": copy.deepcopy(governed.SEED_CONFIG),
                "patched_config": copy.deepcopy(governed.PATCHED_CONFIG)}
    return {"core_version": core_version}


def records(case: str, specs: dict[str, dict[str, Any]] | None = None,
            core_version: str = CORE_VERSION) -> list[dict[str, Any]]:
    """Digest-bound records in the required order (each verified against the case context)."""

    specs = specs_for(case, core_version) if specs is None else specs
    context = governed.case_context(case, {"binding": binding_for(case, core_version),
                                           "core_version": core_version})
    return [governed.operation_record(op, request={"synthetic_request": op}, response={"synthetic_response": op},
                                      readback=spec["readback"], facts=spec["facts"], context=context)
            for op, spec in specs.items()]


def rollback_identity(architecture: str = ARCHITECTURE) -> dict[str, Any]:
    """The rollback-identity file the digest verification step writes when it passes."""

    return {"status": "passed", "image": runners.ROLLBACK_ENGINEERING_IMAGE,
            "image_digest": runners.ROLLBACK_ENGINEERING_INDEX_DIGEST,
            "version": runners.ROLLBACK_ENGINEERING_VERSION, "revision": runners.ROLLBACK_ENGINEERING_REVISION,
            "architecture": architecture,
            "manifest_digest": runners.ROLLBACK_ENGINEERING_PLATFORM_DIGESTS[architecture]}


def identity_for(stage, architecture: str = ARCHITECTURE,
                 engineering_version: str = ENGINEERING_VERSION) -> dict[str, Any]:
    """The stage identity ``run_stage`` records for a correct deployment."""

    ident = {"key": stage.key, "upstream_version": stage.upstream_version, "endpoint_path": stage.endpoint_path,
             "architecture": architecture, "engineering_image_id": "sha256:" + "9" * 64}
    if stage.rollback:
        ident.update(engineering_image=governed.ROLLBACK_IMAGE, engineering_version=governed.ROLLBACK_VERSION,
                     rollback_identity=rollback_identity(architecture))
    else:
        ident.update(engineering_image=governed.LANE_IMAGE, engineering_version=engineering_version)
    return ident


def evidence(configuration: str = "91_only", operations: dict[str, list] | None = None, *,
             dashboard: list | None = None, typed: list | None = None, protocols=None, peer_log=None,
             identities: dict[str, Any] | None = None, core_version: str = CORE_VERSION,
             architecture: str = ARCHITECTURE, engineering_version: str = ENGINEERING_VERSION) -> dict[str, Any]:
    """A runner evidence dict (as ``run_lane`` fills it) for every stage of ``configuration``.

    ``operations`` maps a case to replacement records; ``dashboard``/``typed``
    are the 91_only shorthands. ``identities`` maps a stage key to a
    replacement identity (``None`` removes it).
    """

    operations = dict(operations or {})
    if dashboard is not None:
        operations[governed.CASE_DASHBOARD] = dashboard
    if typed is not None:
        operations[governed.CASE_TYPED] = typed
    identities = identities or {}
    out: dict[str, Any] = {"stages": {}}
    for stage in governed.STAGES_BY_CONFIGURATION[configuration]:
        for case in stage.cases:
            out[case] = {"operations": records(case, core_version=core_version) if case not in operations
                         else operations[case], "binding": binding_for(case, core_version), "diagnostics": []}
        ident = identities[stage.key] if stage.key in identities \
            else identity_for(stage, architecture, engineering_version)
        out["stages"][stage.key] = {
            "protocols": [PROTOCOL, PROTOCOL] if protocols is None else list(protocols),
            "peer_log": {"forwarded": 40, "peer_posts": 40} if peer_log is None else dict(peer_log),
            "identity": {} if ident is None else ident}
    return out


def passing_observed(case: str) -> dict[str, Any]:
    """The ``observed`` block of a passing governed case file (no plan: lane fields added here)."""

    configuration = governed.CASE_STAGE[case].configuration
    observed = governed.build_observed(None, evidence(configuration), case)
    observed.update(core_version=CORE_VERSION, architecture=ARCHITECTURE, engineering_version=ENGINEERING_VERSION)
    return observed
