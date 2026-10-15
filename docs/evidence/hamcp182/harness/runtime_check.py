"""HAMCP-182 Part 0 checker: run a shipped Engineering runtime against rehearsal journals.

Run once per target with the source extract to import:
  --source <git archive 690a00b>  --runtime beta9   deployed Engineering 2.4.1-beta.9
  --source <git archive 7370ad25> --runtime beta8   rollback Engineering 2.4.1-beta.8
Only that extract's registry, runtime and test-fixture code is imported. Journals
and public key come from build_journals.py (ephemeral key); in Part 2 pass the
owner's signed journals with the production PUBLIC key instead.

Proves at the Core authority boundary (registry -> CoreRuntime admission -> route
acquisition); it does not execute create_entity_metadata_plan or contact Core.
Synthetic I/O only: no network, no Home Assistant, no credentials.
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
parser.add_argument("--runtime", choices=("beta9", "beta8"), required=True)
parser.add_argument("--public-key", type=Path, required=True)
parser.add_argument("--baseline", type=Path, required=True, help="journal currently served (seq 9)")
parser.add_argument("--final", type=Path, required=True, help="final journal (seq 12)")
parser.add_argument("--steps", type=Path, action="append", default=[],
                    help="intermediate journals applied in order before the final")
parser.add_argument("--now", default="2026-10-15T07:00:00Z")
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
from ha_mcp_engineering.ha_core_readmission.source import capability_evidence_for_probes  # noqa: E402
from ha_mcp_engineering.fan.contracts import CORE_REQUIREMENTS as LEGACY_REQUIREMENTS  # noqa: E402
from test_core_2026_10_device_registry import probe_inputs  # noqa: E402

EXPECTED_VERSION = {"beta9": "2.4.1-beta.9", "beta8": "2.4.1-beta.8"}[args.runtime]
assert ENGINEERING_VERSION == EXPECTED_VERSION, f"{args.runtime} source is {ENGINEERING_VERSION}"
ENTITY = "core.entity_registry_metadata_operation"
assert ENTITY in {p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES}, \
    "both targets compile the dormant entity-metadata profile"
VERSIONS = ("2026.9.2", "2026.9.3", "2026.9.4", "2026.10.0", "2026.10.1")
EXTENDED = ("2026.10.0", "2026.10.1")
SEPTEMBER = json.loads((ROOT / "tests/fixtures/ha_core_2026_9_device_registry.json").read_text())
NOW = datetime.fromisoformat(args.now.replace("Z", "+00:00")).astimezone(timezone.utc)
PUBLIC = args.public_key.read_text().strip()
ENTITY_TARGET = {"target_type": "entity_registry_metadata", "target_id": "light.synthetic"}
CONFIG_REQUIRED = ("core.f3_mutation_verification", "core.governed_configuration_operation")
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
        health = await runtime.reconcile_once("hamcp182-part0")
        admitted = sorted(p["capability_id"] for p in runtime.health_snapshot()["authority_profiles"]
                          if p["disposition"].startswith("admitted_"))
        entry = authority.entry_for(CORE_IDENTITY, version)
        result["versions"][version] = {
            "entry_present": entry is not None,
            "entry_reference_count": None if entry is None else len(entry.capabilities),
            "entry_has_entity_reference": entry is not None and any(
                c.capability_id == ENTITY for c in entry.capabilities),
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
            "entity_route_available": runtime.route_status((ENTITY,))["available"],
            "entity_acquire_exact_target": acquired(runtime, (ENTITY,), ENTITY_TARGET),
            "config_acquire": acquired(runtime, CONFIG_REQUIRED,
                                       {"target_type": "automation", "target_id": "one"}),
            "legacy_fan_power_2026_9_2_pin": legacy_pin(runtime),
        }
    return result


async def lease_transition(baseline: bytes, final: bytes, version: str, required) -> dict:
    """A lease committed at the baseline, then the registry advances under it."""
    with tempfile.TemporaryDirectory() as tmp:
        h = Harness(Path(tmp) / "core.json", baseline)
        assert await h.registry.refresh()
        runtime = h.runtime(version)
        await runtime.reconcile_once("hamcp182-lease")
        authority = runtime.acquire(required, target={"target_type": "automation", "target_id": "one"})
        commits = None if authority is None else runtime.consume(authority)
        h.raw = final
        refreshed = await h.registry.refresh()
        revalidated = None if commits is None else runtime.revalidate(authority, commits)
        finished = None if commits is None else runtime.finish(commits)
        await runtime.reconcile_once("hamcp182-lease-after")
        after = runtime.acquire(required, target={"target_type": "automation", "target_id": "two"})
        return {"acquired": authority is not None, "refreshed": refreshed,
                "in_flight_revalidated": revalidated, "finished": finished,
                "new_lease_after_refresh": after is not None}


async def scenario(baseline: bytes, steps: list[bytes], final: bytes) -> dict:
    out: dict = {"final_sha256": hashlib.sha256(final).hexdigest()}
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "core.json"
        h = Harness(cache, baseline)
        check(await h.registry.refresh(), "baseline refresh failed")
        out["baseline"] = await observe(h)
        # Production path: one merge moves main from seq 9 straight to the final tip.
        h.raw = final
        out["jump_refreshed"] = await h.registry.refresh()
        out["final"] = await observe(h)
        restarted = Harness(cache, OSError("synthetic upstream unavailable"))
        out["restart_cache"] = {"sequence": restarted.registry.authority().sequence,
                                "content_digest": restarted.registry.authority().content_digest}
        h.raw = baseline
        try:
            out["rollback_refresh_result"] = await h.registry.refresh()
        except Exception as error:  # noqa: BLE001 - record any refusal shape
            out["rollback_refresh_result"] = f"raised {type(error).__name__}"
        out["after_rollback_sequence"] = h.registry.authority().sequence
    with tempfile.TemporaryDirectory() as tmp:
        h = Harness(Path(tmp) / "core.json", baseline)
        await h.registry.refresh()
        out["stepwise"] = []
        for raw in [*steps, final]:
            h.raw = raw
            ok = await h.registry.refresh()
            view = await observe(h)
            out["stepwise"].append({
                "refreshed": ok, "sequence": view["sequence"], "revoked": view["revoked"],
                "admitted": {v: len(view["versions"][v]["admitted"]) for v in VERSIONS},
                "entity": {v: view["versions"][v]["entity_acquire_exact_target"] for v in VERSIONS}})
        out["stepwise_final_matches_jump"] = (
            h.registry.authority().content_digest == out["final"]["content_digest"])
    out["leases"] = {
        **{f"{v} configuration": await lease_transition(baseline, final, v, CONFIG_REQUIRED)
           for v in (*EXTENDED, "2026.9.4")},
    }
    return out


def evaluate(result: dict) -> None:
    base, final = result["baseline"], result["final"]
    check(result["jump_refreshed"] is True, "final journal not accepted")
    check(base["sequence"] == 9 and final["sequence"] == 12, "sequence is not 9 -> 12")
    check(not final["surface_denied"] and final["positive_current"], "surface denied or stale")
    check(base["revoked"] == ["2026.9.2", "2026.9.3"], f"baseline revocations {base['revoked']}")
    check(final["revoked"] == ["2026.9.2", "2026.9.3", "2026.9.4"], f"revocations {final['revoked']}")
    check(result["restart_cache"]["content_digest"] == final["content_digest"],
          "cache restart did not restore the final tip")
    check(result["after_rollback_sequence"] == final["sequence"], "rollback accepted")
    check(result["stepwise_final_matches_jump"], "stepwise and jump differ")
    for version in EXTENDED:
        b, f = base["versions"][version], final["versions"][version]
        check(b["entry_reference_count"] == 21 and f["entry_reference_count"] == 22,
              f"{version} references {b['entry_reference_count']}->{f['entry_reference_count']}")
        check(ENTITY not in b["admitted"] and not b["entity_route_available"]
              and not b["entity_acquire_exact_target"], f"{version} entity available before extension")
        check(f["admitted"] == sorted([*b["admitted"], ENTITY]), f"{version} admitted is not baseline + entity")
        check(f["positive"] == sorted([*b["positive"], ENTITY]), f"{version} positive is not baseline + entity")
        check(f["probe_profile"] == b["probe_profile"] is not None, f"{version} probe changed")
        check(f["compatible_count"] == b["compatible_count"] + 1 and b["compatible_count"] > 0,
              f"{version} compatible {b['compatible_count']}->{f['compatible_count']}")
        check(f["fallback_count"] == 0 == b["fallback_count"], f"{version} fallback")
        check(f["entity_route_available"] and f["entity_acquire_exact_target"],
              f"{version} entity authority not available after extension")
        check(b["config_acquire"] and f["config_acquire"], f"{version} configuration authority lost")
        check(f["selection_token"] != b["selection_token"], f"{version} selection token unchanged")
    b, f = base["versions"]["2026.9.4"], final["versions"]["2026.9.4"]
    check(not b["revoked"] and b["compatible_count"] > 0 and b["config_acquire"]
          and ENTITY not in b["admitted"], "2026.9.4 baseline is not the admitted 21-reference entry")
    check(f["revoked"] and not f["entry_present"] and f["probe_profile"] is None and f["positive"] == []
          and f["compatible_count"] == 0 and f["admitted"] == [] and not f["config_acquire"]
          and not f["entity_acquire_exact_target"], "2026.9.4 revocation not honored")
    check(f["deny_only"] == len(CORE_RUNTIME_CAPABILITY_PROFILES), "2026.9.4 deny_only")
    check(f["compiled_authority"] == 0 == b["compiled_authority"], "2026.9.4 compiled authority")
    for version in ("2026.9.2", "2026.9.3"):
        check(base["versions"][version] == final["versions"][version] | {
            "selection_token": base["versions"][version]["selection_token"]},
            f"{version} observation changed")
        check(final["versions"][version]["revoked"] and final["versions"][version]["admitted"] == [],
              f"{version} no longer revoked")
    for view in (base, final):
        for version, v in view["versions"].items():
            check(not v["legacy_fan_power_2026_9_2_pin"], f"{version} relies on the legacy 9.2 pin")
    for label, lease in result["leases"].items():
        version = label.split()[0]
        check(lease["acquired"] and lease["refreshed"] and lease["finished"],
              f"lease bookkeeping on {label}: {lease}")
        if version == "2026.9.4":
            check(lease["in_flight_revalidated"] is False and lease["new_lease_after_refresh"] is False,
                  f"revoked 2026.9.4 lease survived: {lease}")
        else:
            check(lease["new_lease_after_refresh"] is True, f"no new lease on {label}: {lease}")


async def main() -> None:
    baseline = args.baseline.read_bytes()
    result = await scenario(baseline, [p.read_bytes() for p in args.steps], args.final.read_bytes())
    evaluate(result)
    report = {"source": str(ROOT), "runtime": args.runtime, "engineering_version": ENGINEERING_VERSION,
              "now": args.now,
              "public_key_sha256": hashlib.sha256(base64.b64decode(PUBLIC)).hexdigest(),
              "baseline_sha256": hashlib.sha256(baseline).hexdigest(),
              "steps_sha256": [hashlib.sha256(p.read_bytes()).hexdigest() for p in args.steps],
              "result": result, "failures": FAILURES, "passed": not FAILURES}
    args.report.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"runtime": args.runtime, "passed": not FAILURES, "failures": FAILURES}, indent=1))
    sys.exit(0 if not FAILURES else 1)


asyncio.run(main())
