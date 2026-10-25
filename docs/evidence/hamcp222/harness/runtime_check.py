"""HAMCP-222 runtime checker: run a shipped Engineering runtime against the rehearsed journals.

Run once per target with the source extract to import:
  --source <git archive v2.4.1-beta.36 = 16b724ec> --runtime beta36   deployed Engineering
  --source <git archive v2.4.1-beta.35 = 90495027> --runtime beta35   previous Engineering
Only that extract's registry, runtime and test-fixture code is imported. Journals
and public key come from build_journals.py (ephemeral key): the sequence-12 twin
(baseline), planned step 1 (sequence 13, extend 2026.10.1) and planned step 2
(sequence 14, add 2026.10.3). After signing, pass the owner's signed journals with
the production PUBLIC key instead.

Proves at the Core authority boundary (registry -> CoreRuntime admission -> route
acquisition) that each sequence admits exactly the four seq-13 profiles on the
intended Core release and changes nothing else. It does not execute a planner or
contact Core. Synthetic I/O only: no network, no Home Assistant, no credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True, help="source extract to import")
parser.add_argument("--runtime", choices=("beta36", "beta35"), required=True)
parser.add_argument("--public-key", type=Path, required=True)
parser.add_argument("--baseline", type=Path, required=True, help="journal currently served (seq 12)")
parser.add_argument("--final", type=Path, required=True, help="final journal (seq 14)")
parser.add_argument("--steps", type=Path, action="append", default=[],
                    help="intermediate journals applied in order before the final (seq 13)")
parser.add_argument("--now", default="2026-10-25T05:00:00Z")
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()

ROOT = args.source
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]
from ha_mcp_engineering.version import SERVER_VERSION as ENGINEERING_VERSION  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import CoreRuntime, CORE_IDENTITY  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.models import CoreAuthorityStatus  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_RUNTIME_CAPABILITY_PROFILES, compiled_exact_authority)
from ha_mcp_engineering.ha_core_readmission.registry import (  # noqa: E402
    CORE_REGISTRY_URL, CoreReleaseRegistry)
from ha_mcp_engineering.ha_core_readmission.runtime import CORE_RECOVERY_FLAP_CONFIRMATIONS  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.source import capability_evidence_for_probes  # noqa: E402
from ha_mcp_engineering.fan.contracts import CORE_REQUIREMENTS as LEGACY_REQUIREMENTS  # noqa: E402
from test_core_2026_10_device_registry import probe_inputs  # noqa: E402

EXPECTED_VERSION = {"beta36": "2.4.1-beta.36", "beta35": "2.4.1-beta.35"}[args.runtime]
assert ENGINEERING_VERSION == EXPECTED_VERSION, f"{args.runtime} source is {ENGINEERING_VERSION}"
PLANNING = "core.dependency_helper_planning"
F3 = "core.f3_mutation_verification"
ENTITY = "core.entity_registry_metadata_operation"
# The four seq-13 profiles and the target each one's F3 identity carries.
NEW = {
    "core.entity_registry_availability_operation":
        {"target_type": "entity_registry_availability", "target_id": "light.synthetic"},
    "core.entity_assistant_exposure_operation":
        {"target_type": "entity_assistant_exposure", "target_id": "light.synthetic"},
    "core.label_registry_operation":
        {"target_type": "label_registry_entry", "target_id": "synthetic"},
    "core.configuration_delete_operation":
        {"target_type": "configuration_deletion", "target_id": "automation.synthetic"},
}
COMPILED = {p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES}
assert len(COMPILED) == 26 and set(NEW) | {ENTITY} <= COMPILED, \
    "both targets compile the 26 profiles including the four dormant seq-13 profiles"
# 2026.10.2 is never listed: a control that no sequence admits it.
VERSIONS = ("2026.9.2", "2026.9.3", "2026.9.4", "2026.10.0", "2026.10.1", "2026.10.2", "2026.10.3")
SEPTEMBER = json.loads((ROOT / "tests/fixtures/ha_core_2026_9_device_registry.json").read_text())
NOW = datetime.fromisoformat(args.now.replace("Z", "+00:00")).astimezone(timezone.utc)
PUBLIC = args.public_key.read_text().strip()
CONFIG_REQUIRED = (F3, "core.governed_configuration_operation")
FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


class Harness:
    def __init__(self, cache: Path, raw: bytes | Exception):
        self.raw = raw

        async def fetch(url, maximum):
            assert url == CORE_REGISTRY_URL
            if isinstance(self.raw, Exception):
                raise self.raw
            return self.raw

        self.registry = CoreReleaseRegistry(enabled=True, public_key=PUBLIC, cache_path=cache,
                                            fetcher=fetch, now=lambda: NOW)

    def runtime(self, version: str) -> CoreRuntime:
        registry = self.registry

        class Source:
            async def capture_core_snapshot(self):
                profile = registry.probe_profile_for(version)
                records = SEPTEMBER if (profile is not None and "name-parts" not in profile.profile_id) else None
                evidence = capability_evidence_for_probes(**probe_inputs(profile, records, version))
                return {"identity": CORE_IDENTITY, "connected": True, "authenticated": True,
                        "session_id": f"synthetic-{version}", "rest_config": {"version": version},
                        "websocket_auth_ok": {"type": "auth_ok", "ha_version": version},
                        "websocket_get_config": {"version": version},
                        "capability_evidence": evidence}

        runtime = CoreRuntime()
        runtime.configure(object(), source=Source(), release_registry=self.registry)
        return runtime


async def settle(runtime: CoreRuntime) -> tuple[dict, int]:
    """Background verifications after a registry refresh, as the running add-on does.

    Mirrors tests/support/core_gate.py: stop at the first snapshot that is not
    confirming, bounded by the flap confirmation count. Returns the snapshot and
    the number of verifications that ran.
    """
    snapshot = await runtime.reconcile_once("periodic")
    runs = 1
    while runs < CORE_RECOVERY_FLAP_CONFIRMATIONS and snapshot["recovery"]["recovery_state"] == "confirming":
        snapshot = await runtime.reconcile_once("periodic")
        runs += 1
    return snapshot, runs


def admitted_of(runtime: CoreRuntime) -> list[str]:
    return sorted(p["capability_id"] for p in runtime.health_snapshot()["authority_profiles"]
                  if p["disposition"].startswith("admitted_"))


def legacy_pin(runtime: CoreRuntime) -> bool:
    """Shipped fan/power legacy receipts acquire only with expected Core 2026.9.2."""
    authority = runtime.acquire(LEGACY_REQUIREMENTS, expected_core_version="2026.9.2",
                                target={"target_type": "entity", "target_id": "fan.synthetic"})
    if authority is None:
        return False
    runtime.release(authority)
    return True


def acquired(runtime: CoreRuntime, capabilities: tuple[str, ...], target=None) -> bool:
    authority = runtime.acquire(capabilities, target=target)
    if authority is None:
        return False
    runtime.release(authority)
    return True


async def observe(h: Harness) -> dict:
    authority = h.registry.authority()
    result = {"sequence": authority.sequence, "content_digest": authority.content_digest,
              "positive_current": authority.positive_authority_current,
              "surface_denied": authority.surface_denied,
              "revoked": sorted(r.version for r in authority.revocations),
              "snapshot": {k: v for k, v in h.registry.snapshot().items()
                           if k in {"freshness_status", "cache_status", "refresh_status",
                                    "last_failure_reason", "failure_reason_counts", "sequence",
                                    "retained_revocation_count"}},
              "versions": {}}
    for version in VERSIONS:
        selections = h.registry.selections(version)
        probe = h.registry.probe_profile_for(version)
        runtime = h.runtime(version)
        health = await runtime.reconcile_once("startup")
        admitted = admitted_of(runtime)
        entry = authority.entry_for(CORE_IDENTITY, version)
        result["versions"][version] = {
            "entry_present": entry is not None,
            "entry_reference_count": None if entry is None else len(entry.capabilities),
            "revoked": authority.revoked(CORE_IDENTITY, version),
            "compiled_authority": len(compiled_exact_authority(version)),
            "probe_profile": None if probe is None else probe.profile_id,
            "positive": sorted(c for s in selections if s.status is CoreAuthorityStatus.POSITIVE
                               for c in s.capability_ids),
            "deny_only": sum(s.status is CoreAuthorityStatus.DENY_ONLY for s in selections),
            "selection_token": h.registry.selection_token(version),
            "compatible_count": health["compatible_count"],
            "fallback_count": health["fallback_count"],
            "admitted": admitted,
            "new_profiles": {
                capability: {
                    "route_available": runtime.route_status((capability,))["available"],
                    "f3_acquire_exact_target": acquired(runtime, (F3, capability), target),
                    "planner_acquire": acquired(runtime, (PLANNING, capability), target),
                } for capability, target in NEW.items()},
            "entity_metadata_acquire": acquired(
                runtime, (PLANNING, ENTITY),
                {"target_type": "entity_registry_metadata", "target_id": "light.synthetic"}),
            "config_acquire": acquired(runtime, CONFIG_REQUIRED,
                                       {"target_type": "automation", "target_id": "one"}),
            "legacy_fan_power_2026_9_2_pin": legacy_pin(runtime),
        }
    return result


async def lease_transition(start: bytes, end: bytes, version: str, required, target) -> dict:
    """A lease committed on one journal, then the registry advances under it."""
    with tempfile.TemporaryDirectory() as tmp:
        h = Harness(Path(tmp) / "core.json", start)
        assert await h.registry.refresh()
        runtime = h.runtime(version)
        await runtime.reconcile_once("startup")
        authority = runtime.acquire(required, target=target)
        commits = None if authority is None else runtime.consume(authority)
        h.raw = end
        refreshed = await h.registry.refresh()
        revalidated = None if commits is None else runtime.revalidate(authority, commits)
        finished = None if commits is None else runtime.finish(commits)
        settled, runs = await settle(runtime)
        after = runtime.acquire(required, target={**target, "target_id": f"{target['target_id']}.two"})
        return {"acquired": authority is not None, "refreshed": refreshed,
                "in_flight_revalidated": revalidated, "finished": finished,
                "new_lease_after_refresh": after is not None,
                "verifications_after_refresh": runs,
                "recovery_state_after_refresh": settled["recovery"]["recovery_state"]}


async def live_advance(journals: list[bytes], version: str) -> list[dict]:
    """One running runtime (no restart) while the registry advances journal by journal."""
    with tempfile.TemporaryDirectory() as tmp:
        h = Harness(Path(tmp) / "core.json", journals[0])
        assert await h.registry.refresh()
        runtime = h.runtime(version)
        boot = await runtime.reconcile_once("startup")
        out = [{"sequence": h.registry.authority().sequence, "trigger": "startup",
                "recovery_state": boot["recovery"]["recovery_state"],
                "admitted": admitted_of(runtime)}]
        for raw in journals[1:]:
            h.raw = raw
            refreshed = await h.registry.refresh()
            first = await runtime.reconcile_once("periodic")
            first_admitted = admitted_of(runtime)
            settled, runs = await settle(runtime)
            out.append({"sequence": h.registry.authority().sequence, "refreshed": refreshed,
                        "first_verification": {"recovery_state": first["recovery"]["recovery_state"],
                                               "admitted": first_admitted},
                        "verifications_to_settle": 1 + runs,
                        "recovery_state": settled["recovery"]["recovery_state"],
                        "admitted": admitted_of(runtime),
                        "new_profiles_routable": {c: runtime.route_status((c,))["available"] for c in NEW}})
        return out


async def refresh_result(h: Harness, raw: bytes):
    h.raw = raw
    try:
        return await h.registry.refresh()
    except Exception as error:  # noqa: BLE001 - record any refusal shape
        return f"raised {type(error).__name__}"


async def scenario(baseline: bytes, steps: list[bytes], final: bytes) -> dict:
    out: dict = {"final_sha256": hashlib.sha256(final).hexdigest()}
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "core.json"
        h = Harness(cache, baseline)
        check(await h.registry.refresh(), "baseline refresh failed")
        out["baseline"] = await observe(h)
        # One publication may move main from seq 12 straight to the final tip.
        h.raw = final
        out["jump_refreshed"] = await h.registry.refresh()
        out["final"] = await observe(h)
        restarted = Harness(cache, OSError("synthetic upstream unavailable"))
        out["restart_cache"] = {"sequence": restarted.registry.authority().sequence,
                                "content_digest": restarted.registry.authority().content_digest}
        out["rollback_refresh_result"] = {
            "to_baseline": await refresh_result(h, baseline),
            **{f"to_step{i}": await refresh_result(h, raw) for i, raw in enumerate(steps, 1)}}
        out["after_rollback_sequence"] = h.registry.authority().sequence
    with tempfile.TemporaryDirectory() as tmp:
        h = Harness(Path(tmp) / "core.json", baseline)
        await h.registry.refresh()
        out["stepwise"] = []
        for raw in [*steps, final]:
            h.raw = raw
            ok = await h.registry.refresh()
            out["stepwise"].append({"refreshed": ok, **await observe(h)})
        out["stepwise_final_matches_jump"] = (
            h.registry.authority().content_digest == out["final"]["content_digest"])
    step1 = steps[0] if steps else final
    label = next(iter(NEW))
    out["leases"] = {
        "2026.10.1 configuration seq12->seq14": await lease_transition(
            baseline, final, "2026.10.1", CONFIG_REQUIRED, {"target_type": "automation", "target_id": "one"}),
        "2026.10.0 configuration seq12->seq14": await lease_transition(
            baseline, final, "2026.10.0", CONFIG_REQUIRED, {"target_type": "automation", "target_id": "one"}),
        **{f"2026.10.1 {c} seq13->seq14": await lease_transition(step1, final, "2026.10.1", (F3, c), t)
           for c, t in NEW.items()},
    }
    out["live_advance"] = {v: await live_advance([baseline, *steps, final], v)
                           for v in ("2026.10.0", "2026.10.1", "2026.10.3")}
    # Control: no lease on 2026.10.3 before seq 14; a new one once seq 14 is refreshed.
    out["lease_control_2026_10_3"] = await lease_transition(
        baseline, final, "2026.10.3", (F3, label), NEW[label])
    return out


def unchanged(a: dict, b: dict) -> bool:
    """Equal apart from the registry-wide selection token."""
    return {k: v for k, v in a.items() if k != "selection_token"} == \
           {k: v for k, v in b.items() if k != "selection_token"}


def new_state(view: dict) -> set[bool]:
    return {flag for profile in view["new_profiles"].values() for flag in profile.values()}


def evaluate(result: dict) -> None:
    base, final, stepwise = result["baseline"], result["final"], result["stepwise"]
    check(result["jump_refreshed"] is True, "final journal not accepted")
    check(len(stepwise) == 2 and all(s["refreshed"] is True for s in stepwise), "a step was not accepted")
    check([base["sequence"], *(s["sequence"] for s in stepwise)] == [12, 13, 14] and final["sequence"] == 14,
          "sequence is not 12 -> 13 -> 14")
    check(result["stepwise_final_matches_jump"], "stepwise and jump differ")
    check(result["restart_cache"]["content_digest"] == final["content_digest"],
          "cache restart did not restore the final tip")
    check(result["after_rollback_sequence"] == 14 and all(
        v is not True for v in result["rollback_refresh_result"].values()), "rollback accepted")
    views = [base, *stepwise, final]
    for view in views:
        check(not view["surface_denied"] and view["positive_current"], f"seq {view['sequence']} denied or stale")
        check(view["revoked"] == ["2026.9.2", "2026.9.3", "2026.9.4"], f"seq {view['sequence']} revocations")
        for version, v in view["versions"].items():
            check(not v["legacy_fan_power_2026_9_2_pin"], f"{version} relies on the legacy 9.2 pin")
            check(v["fallback_count"] == 0, f"seq {view['sequence']} {version} fallback")
    s13 = stepwise[0]
    check({k: v for k, v in stepwise[1].items() if k != "refreshed"} == final,
          "stepwise seq 14 differs from the jump")

    # 2026.10.0: never changes and never admits the four.
    for view in views[1:]:
        check(unchanged(view["versions"]["2026.10.0"], base["versions"]["2026.10.0"]),
              f"2026.10.0 changed at seq {view['sequence']}")
    b0 = base["versions"]["2026.10.0"]
    check(b0["entry_reference_count"] == 22 and b0["config_acquire"] and b0["entity_metadata_acquire"]
          and new_state(b0) == {False} and not set(NEW) & set(b0["admitted"]),
          "2026.10.0 baseline is not the admitted 22-reference entry without the four")

    # 2026.10.1: seq 13 adds exactly the four; seq 14 leaves it unchanged.
    b1, e1 = base["versions"]["2026.10.1"], s13["versions"]["2026.10.1"]
    check(b1["entry_reference_count"] == 22 and e1["entry_reference_count"] == 26,
          f"2026.10.1 references {b1['entry_reference_count']}->{e1['entry_reference_count']}")
    check(new_state(b1) == {False} and not set(NEW) & set(b1["admitted"]), "2026.10.1 has a new profile at seq 12")
    check(e1["admitted"] == sorted([*b1["admitted"], *NEW]), "2026.10.1 admitted is not baseline + the four")
    check(e1["positive"] == sorted([*b1["positive"], *NEW]), "2026.10.1 positive is not baseline + the four")
    check(e1["probe_profile"] == b1["probe_profile"] is not None, "2026.10.1 probe changed")
    check(e1["compatible_count"] == b1["compatible_count"] + 4 and b1["compatible_count"] > 0,
          f"2026.10.1 compatible {b1['compatible_count']}->{e1['compatible_count']}")
    check(new_state(e1) == {True}, "2026.10.1 a new profile is not routable/acquirable at seq 13")
    check(b1["config_acquire"] and e1["config_acquire"] and b1["entity_metadata_acquire"]
          and e1["entity_metadata_acquire"], "2026.10.1 existing authority lost")
    check(e1["selection_token"] != b1["selection_token"], "2026.10.1 selection token unchanged")
    check(unchanged(final["versions"]["2026.10.1"], e1), "2026.10.1 changed at seq 14")

    # 2026.10.3: nothing until seq 14, then the same 26 references as 2026.10.1.
    for view in (base, s13):
        v = view["versions"]["2026.10.3"]
        check(not v["entry_present"] and v["probe_profile"] is None and v["positive"] == []
              and v["admitted"] == [] and v["compatible_count"] == 0 and not v["config_acquire"]
              and not v["entity_metadata_acquire"] and new_state(v) == {False},
              f"2026.10.3 has authority at seq {view['sequence']}")
    f3, f1 = final["versions"]["2026.10.3"], final["versions"]["2026.10.1"]
    check(f3["entry_present"] and f3["entry_reference_count"] == 26 and not f3["revoked"],
          "2026.10.3 entry not present with 26 references")
    check(f3["probe_profile"] == "core-probes-child-device-name-parts-v1", f"2026.10.3 probe {f3['probe_profile']}")
    check(f3["admitted"] == f1["admitted"] and f3["positive"] == f1["positive"]
          and f3["compatible_count"] == f1["compatible_count"], "2026.10.3 admission differs from 2026.10.1")
    check(new_state(f3) == {True} and f3["config_acquire"] and f3["entity_metadata_acquire"],
          "2026.10.3 authority not available at seq 14")

    # Revoked and never-listed releases never change and never admit anything.
    for version in ("2026.9.2", "2026.9.3", "2026.9.4", "2026.10.2"):
        for view in views[1:]:
            check(unchanged(view["versions"][version], base["versions"][version]),
                  f"{version} changed at seq {view['sequence']}")
        v = base["versions"][version]
        check(not v["entry_present"] and v["admitted"] == [] and v["positive"] == []
              and v["compatible_count"] == 0 and not v["config_acquire"] and new_state(v) == {False}
              and v["compiled_authority"] == 0, f"{version} has authority")
        check(v["revoked"] == (version != "2026.10.2"), f"{version} revocation state")

    for label, lease in result["leases"].items():
        check(lease["acquired"] and lease["refreshed"] and lease["finished"]
              and lease["new_lease_after_refresh"], f"lease on {label}: {lease}")
    # A running runtime (no restart) settles on the same admission as a fresh one.
    live = result["live_advance"]
    for version in ("2026.10.0", "2026.10.1", "2026.10.3"):
        seq = [s["sequence"] for s in live[version]]
        check(seq == [12, 13, 14], f"{version} live sequences {seq}")
        for s, view in zip(live[version], (base, s13, final)):
            check(s["admitted"] == view["versions"][version]["admitted"],
                  f"{version} live seq {s['sequence']} admitted differs from a fresh runtime")
            if "new_profiles_routable" in s:
                check(set(s["new_profiles_routable"].values()) == new_state(view["versions"][version]),
                      f"{version} live seq {s['sequence']} routability")
    for s in live["2026.10.0"][1:]:
        check(s["first_verification"]["admitted"] == base["versions"]["2026.10.0"]["admitted"]
              and s["recovery_state"] == "stable", f"2026.10.0 interrupted at seq {s['sequence']}")
    s14 = live["2026.10.1"][2]
    check(s14["first_verification"]["admitted"] == s14["admitted"], "2026.10.1 interrupted at seq 14")
    # Disclosed (not a defect): adding profiles to the running release's entry changes its
    # signed authority, so the HAMCP-215 restoration gate withholds that release for one
    # confirmation (15 s nominal in production) before admitting baseline + the four.
    for version, sequence in (("2026.10.1", 13), ("2026.10.3", 14)):
        s = live[version][[12, 13, 14].index(sequence)]
        check(s["recovery_state"] == "restored" and s["first_verification"]["recovery_state"] in {"confirming", "restored"},
              f"{version} seq {sequence} did not restore: {s}")

    lease = result["lease_control_2026_10_3"]
    check(lease["acquired"] is False and lease["refreshed"] is True and lease["new_lease_after_refresh"] is True,
          f"2026.10.3 lease control: {lease}")


async def main() -> None:
    baseline = args.baseline.read_bytes()
    steps = [p.read_bytes() for p in args.steps]
    final = args.final.read_bytes()
    result = await scenario(baseline, steps, final)
    evaluate(result)
    report = {"source": str(ROOT), "runtime": args.runtime, "engineering_version": ENGINEERING_VERSION,
              "now": args.now,
              "public_key_sha256": hashlib.sha256(base64.b64decode(PUBLIC)).hexdigest(),
              "baseline_sha256": hashlib.sha256(baseline).hexdigest(),
              "steps_sha256": [hashlib.sha256(raw).hexdigest() for raw in steps],
              "result": result, "failures": FAILURES, "passed": not FAILURES}
    args.report.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"runtime": args.runtime, "passed": not FAILURES, "failures": FAILURES}, indent=1))
    sys.exit(0 if not FAILURES else 1)


asyncio.run(main())
