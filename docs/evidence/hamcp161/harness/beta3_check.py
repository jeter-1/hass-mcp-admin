"""HAMCP-161 Part 0 checker: run the deployed beta.3 runtime against rehearsal journals.

Run with PYTHONPATH=<beta.3 extract>/hass_mcp_engineering_beta:<beta.3 extract>/tests,
where the extract is `git archive 872685b` (deployed Engineering 2.4.1-beta.3).
Only beta.3 registry, runtime and test-fixture code is imported. The journals and
public key come from build_journals.py (ephemeral key); in Part 2 pass the owner's
signed journal with the production PUBLIC key instead.

Part 2 also runs it with --runtime beta4 against an extract of current main's
2.4.1-beta.4 source (a3aa4d1 or later). There the entity-metadata profile is
compiled in and its transport evidence is captured, but it must stay unavailable
because no signed reference selects it.

Synthetic I/O only: no network, no Home Assistant, no credentials.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--beta3", type=Path, required=True, help="source extract to import (beta.3 or beta.4)")
parser.add_argument("--runtime", choices=("beta3", "beta4"), default="beta3")
parser.add_argument("--public-key", type=Path, required=True)
parser.add_argument("--baseline", type=Path, required=True, help="journal currently served (seq 6)")
parser.add_argument("--final", type=Path, action="append", required=True,
                    help="LABEL=PATH of a final journal; repeatable")
parser.add_argument("--steps", type=Path, action="append", default=[],
                    help="LABEL=PATH intermediate journals applied in order before the final")
parser.add_argument("--now", default="2026-10-12T19:00:00Z")
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()

ROOT = args.beta3
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]
from ha_mcp_engineering.ha_core_readmission import CoreRuntime, CORE_IDENTITY  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.models import CoreAuthorityStatus  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_RUNTIME_CAPABILITY_PROFILES, compiled_exact_authority)
from ha_mcp_engineering.ha_core_readmission.registry import (  # noqa: E402
    CORE_REGISTRY_URL, CoreReleaseRegistry)
from ha_mcp_engineering.ha_core_readmission.source import capability_evidence_for_probes  # noqa: E402
from test_core_2026_10_device_registry import probe_inputs  # noqa: E402

ENTITY = "core.entity_registry_metadata_operation"
from ha_mcp_engineering.fan.contracts import CORE_REQUIREMENTS as LEGACY_REQUIREMENTS  # noqa: E402
VERSIONS = ("2026.9.2", "2026.9.3", "2026.9.4", "2026.10.0", "2026.10.1")
SEPTEMBER = json.loads((ROOT / "tests/fixtures/ha_core_2026_9_device_registry.json").read_text())
NOW = datetime.fromisoformat(args.now.replace("Z", "+00:00")).astimezone(timezone.utc)
PUBLIC = args.public_key.read_text().strip()
BETA3_CAPABILITIES = {p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES}
if args.runtime == "beta3":
    assert ENTITY not in BETA3_CAPABILITIES, "checker must run the beta.3 source"
else:
    assert ENTITY in BETA3_CAPABILITIES, "beta.4 source must compile the dormant entity profile"
ENTITY_TARGET = {"target_type": "entity_registry_metadata", "target_id": "light.synthetic"}
FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def labelled(value: Path) -> tuple[str, Path]:
    label, _, path = str(value).partition("=")
    return label, Path(path)


class Harness:
    def __init__(self, cache: Path, raw: bytes | Exception):
        self.raw = raw
        self.clock = NOW

        async def fetch(url, maximum):
            assert url == CORE_REGISTRY_URL
            if isinstance(self.raw, Exception):
                raise self.raw
            return self.raw

        self.registry = CoreReleaseRegistry(enabled=True, public_key=PUBLIC, cache_path=cache,
                                            fetcher=fetch, now=lambda: self.clock)

    def runtime(self, version: str) -> CoreRuntime:
        registry = self.registry

        class Source:
            async def capture_core_snapshot(self):
                profile = registry.probe_profile_for(version)
                records = SEPTEMBER if (profile is not None and "name-parts" not in profile.profile_id) else None
                inputs = probe_inputs(profile, records, version)
                evidence = capability_evidence_for_probes(**inputs)
                return {"identity": CORE_IDENTITY, "connected": True, "authenticated": True,
                        "session_id": f"synthetic-{version}", "rest_config": {"version": version},
                        "websocket_auth_ok": {"type": "auth_ok", "ha_version": version},
                        "websocket_get_config": {"version": version},
                        "capability_evidence": evidence}

        runtime = CoreRuntime()
        runtime.configure(object(), source=Source(), release_registry=self.registry)
        return runtime


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
        health = await runtime.reconcile_once("hamcp161-part0")
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
            "entity_acquire": runtime.acquire((ENTITY,)) is not None,
            "entity_acquire_exact_target": runtime.acquire((ENTITY,), target=ENTITY_TARGET) is not None,
            "entity_probe_evidence": any(
                e.get("capability_id") == ENTITY
                for e in (await runtime._source.capture_core_snapshot())["capability_evidence"]),
            "legacy_fan_power_2026_9_2_pin": legacy_pin(runtime),
        }
    return result


def legacy_pin(runtime: CoreRuntime) -> bool:
    """Shipped fan/power legacy receipts acquire only with expected Core 2026.9.2."""
    authority = runtime.acquire(LEGACY_REQUIREMENTS, expected_core_version="2026.9.2",
                                target={"target_type": "entity", "target_id": "fan.synthetic"})
    if authority is None:
        return False
    runtime.release(authority)
    return True


async def lease_transition(baseline: bytes, final: bytes, version: str) -> dict:
    """A lease acquired at the baseline, then the registry advances under it."""
    with tempfile.TemporaryDirectory() as tmp:
        h = Harness(Path(tmp) / "core.json", baseline)
        assert await h.registry.refresh()
        runtime = h.runtime(version)
        await runtime.reconcile_once("hamcp161-lease")
        required = ("core.f3_mutation_verification", "core.governed_configuration_operation")
        authority = runtime.acquire(required, target={"target_type": "automation", "target_id": "one"})
        commits = None if authority is None else runtime.consume(authority)
        h.raw = final
        refreshed = await h.registry.refresh()
        revalidated = None if commits is None else runtime.revalidate(authority, commits)
        finished = None if commits is None else runtime.finish(commits)
        await runtime.reconcile_once("hamcp161-lease-after")
        after = runtime.acquire(required, target={"target_type": "automation", "target_id": "two"})
        return {"acquired": authority is not None, "refreshed": refreshed,
                "in_flight_revalidated": revalidated, "finished": finished,
                "new_lease_after_refresh": after is not None}


async def scenario(label: str, baseline: bytes, steps: list[bytes], final: bytes) -> dict:
    out: dict = {"label": label, "final_sha256": hashlib.sha256(final).hexdigest()}
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "core.json"
        h = Harness(cache, baseline)
        check(await h.registry.refresh(), f"{label}: baseline refresh failed")
        out["baseline"] = await observe(h)
        # Production path: one merge moves main from seq 6 straight to the final tip.
        h.raw = final
        out["jump_refreshed"] = await h.registry.refresh()
        out["final"] = await observe(h)
        # Restart: cache only, upstream unavailable.
        restarted = Harness(cache, OSError("synthetic upstream unavailable"))
        out["restart_cache"] = {"sequence": restarted.registry.authority().sequence,
                                "content_digest": restarted.registry.authority().content_digest}
        # Rollback attempt: upstream re-serves the older baseline.
        h.raw = baseline
        try:
            out["rollback_refresh_result"] = await h.registry.refresh()
        except Exception as error:  # noqa: BLE001 - record any refusal shape
            out["rollback_refresh_result"] = f"raised {type(error).__name__}"
        out["after_rollback_sequence"] = h.registry.authority().sequence
    if steps:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp) / "core.json", baseline)
            await h.registry.refresh()
            out["stepwise"] = []
            for raw in [*steps, final]:
                h.raw = raw
                ok = await h.registry.refresh()
                out["stepwise"].append({"refreshed": ok, "sequence": h.registry.authority().sequence})
            out["stepwise_final_matches_jump"] = (
                h.registry.authority().content_digest == out["final"]["content_digest"])
    out["leases"] = {v: await lease_transition(baseline, final, v) for v in ("2026.10.0", "2026.9.4")}
    return out


def evaluate(result: dict) -> None:
    label, base, final = result["label"], result["baseline"], result["final"]
    check(result["jump_refreshed"] is True, f"{label}: final journal not accepted")
    check(final["sequence"] > base["sequence"], f"{label}: sequence did not advance")
    check(not final["surface_denied"] and final["positive_current"], f"{label}: surface denied or stale")
    check(final["revoked"] == ["2026.9.2", "2026.9.3"], f"{label}: revocations {final['revoked']}")
    check(result["restart_cache"]["content_digest"] == final["content_digest"],
          f"{label}: cache restart did not restore the final tip")
    check(result["after_rollback_sequence"] == final["sequence"], f"{label}: rollback accepted")
    check(result.get("stepwise_final_matches_jump", True), f"{label}: stepwise and jump differ")
    for version in ("2026.10.0", "2026.9.4"):
        b, f = base["versions"][version], final["versions"][version]
        check(f["admitted"] == b["admitted"], f"{label}: {version} admitted set changed")
        check(f["positive"] == b["positive"], f"{label}: {version} positive set changed")
        check(f["probe_profile"] == b["probe_profile"], f"{label}: {version} probe changed")
        check(f["compatible_count"] == b["compatible_count"] and f["compatible_count"] > 0,
              f"{label}: {version} compatible {b['compatible_count']}->{f['compatible_count']}")
        check(f["fallback_count"] == 0, f"{label}: {version} fallback")
        check(not b["legacy_fan_power_2026_9_2_pin"] and not f["legacy_fan_power_2026_9_2_pin"],
              f"{label}: {version} relies on the 2026.9.2 legacy fan/power pin")
    ten = base["versions"]["2026.10.0"]
    new = final["versions"]["2026.10.1"]
    check(base["versions"]["2026.10.1"]["compatible_count"] == 0, f"{label}: 2026.10.1 admitted before add")
    check(new["admitted"] == ten["admitted"] and new["compatible_count"] == ten["compatible_count"],
          f"{label}: 2026.10.1 not admitted with the 2026.10.0 capability set")
    check(new["probe_profile"] == ten["probe_profile"], f"{label}: 2026.10.1 probe differs")
    for version in ("2026.9.2", "2026.9.3"):
        f = final["versions"][version]
        check(f["revoked"] and f["probe_profile"] is None and f["positive"] == []
              and f["compatible_count"] == 0 and f["admitted"] == [],
              f"{label}: {version} revocation not honored")
        check(f["deny_only"] == len(CORE_RUNTIME_CAPABILITY_PROFILES), f"{label}: {version} deny_only")
    for version, f in final["versions"].items():
        check(ENTITY not in f["positive"] and ENTITY not in f["admitted"]
              and not f["entity_route_available"] and not f["entity_acquire"]
              and not f["entity_acquire_exact_target"],
              f"{label}: unknown entity profile became available on {version}")
    for version, lease in result["leases"].items():
        check(lease["acquired"] and lease["refreshed"] and lease["finished"]
              and lease["new_lease_after_refresh"], f"{label}: lease continuity on {version}: {lease}")


async def main() -> None:
    baseline = args.baseline.read_bytes()
    steps = [labelled(p) for p in args.steps]
    results = []
    for value in args.final:
        label, path = labelled(value)
        own_steps = [p.read_bytes() for step_label, p in steps if step_label == label]
        result = await scenario(label, baseline, own_steps, path.read_bytes())
        evaluate(result)
        results.append(result)
    report = {"beta3_source": str(ROOT), "runtime": args.runtime, "now": args.now,
              "public_key_sha256": hashlib.sha256(__import__("base64").b64decode(PUBLIC)).hexdigest(),
              "baseline_sha256": hashlib.sha256(baseline).hexdigest(),
              "results": results, "failures": FAILURES, "passed": not FAILURES}
    args.report.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": not FAILURES, "failures": FAILURES}, indent=1))
    sys.exit(0 if not FAILURES else 1)


asyncio.run(main())
