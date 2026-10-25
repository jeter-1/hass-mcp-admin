"""HAMCP-222 older-runtime probe: what a runtime that predates a seq-13 profile admits.

  --source <git archive v2.4.1-beta.34 = fff324bb>  (compiles 25 of the 26 profiles:
                                                     no core.configuration_delete_operation)
Boots a fresh runtime (trigger "startup") on each given journal and Core version
and records the admitted profiles, so a rollback below beta.35 is described by
observation, not assumption: does an entry carrying a reference the runtime does
not compile lose the whole entry, or only that reference?

Synthetic I/O only, ephemeral rehearsal key: no network, no Home Assistant, no credentials.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--public-key", type=Path, required=True)
parser.add_argument("--journal", type=Path, action="append", required=True)
parser.add_argument("--now", default="2026-10-25T05:00:00Z")
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()

ROOT = args.source
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]
from ha_mcp_engineering.version import SERVER_VERSION  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import CoreRuntime, CORE_IDENTITY  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.source import capability_evidence_for_probes  # noqa: E402
from test_core_2026_10_device_registry import probe_inputs  # noqa: E402

NOW = datetime.fromisoformat(args.now.replace("Z", "+00:00")).astimezone(timezone.utc)
PUBLIC = args.public_key.read_text().strip()
VERSIONS = ("2026.10.0", "2026.10.1", "2026.10.3")
COMPILED = sorted(p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES)


async def admitted(raw: bytes, version: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        async def fetch(url, maximum):
            return raw

        registry = CoreReleaseRegistry(enabled=True, public_key=PUBLIC, cache_path=Path(tmp) / "c.json",
                                       fetcher=fetch, now=lambda: NOW)
        assert await registry.refresh()
        entry = registry.authority().entry_for(CORE_IDENTITY, version)

        class Source:
            async def capture_core_snapshot(self):
                evidence = capability_evidence_for_probes(
                    **probe_inputs(registry.probe_profile_for(version), None, version))
                return {"identity": CORE_IDENTITY, "connected": True, "authenticated": True,
                        "session_id": f"synthetic-{version}", "rest_config": {"version": version},
                        "websocket_auth_ok": {"type": "auth_ok", "ha_version": version},
                        "websocket_get_config": {"version": version}, "capability_evidence": evidence}

        runtime = CoreRuntime()
        runtime.configure(object(), source=Source(), release_registry=registry)
        health = await runtime.reconcile_once("startup")
        names = sorted(p["capability_id"] for p in runtime.health_snapshot()["authority_profiles"]
                       if p["disposition"].startswith("admitted_"))
        referenced = [] if entry is None else sorted(c.capability_id for c in entry.capabilities)
        return {"sequence": registry.authority().sequence,
                "entry_reference_count": len(referenced),
                "recovery_state": health["recovery"]["recovery_state"],
                "admitted_count": len(names),
                "referenced_not_compiled": sorted(set(referenced) - set(COMPILED)),
                "referenced_not_admitted": sorted(set(referenced) - set(names))}


async def main() -> None:
    report = {"source": str(ROOT), "engineering_version": SERVER_VERSION, "compiled_count": len(COMPILED),
              "journals": {}}
    for path in args.journal:
        raw = path.read_bytes()
        report["journals"][path.name] = {"sha256": hashlib.sha256(raw).hexdigest(),
                                         **{v: await admitted(raw, v) for v in VERSIONS}}
    args.report.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps(report, indent=1, sort_keys=True))


asyncio.run(main())
