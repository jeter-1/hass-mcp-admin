"""HAMCP-242 round-1 correction probe (offline).

Replays the live-case fixture (Core 2026.10.3, signed sequence-14 entry)
through the runtime twice, once with the coordinator from the reviewed head
f5f83c4d and once with the working tree, under a fixed clock, and compares the
public ``home_assistant_core_authority`` block byte for byte.  It also records
the public envelope for each injected consistency fault.  Run from the
repository root with the CI test environment:

    python docs/evidence/hamcp242/r1_nominal_identity.py
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
from unittest.mock import patch

ROOT = pathlib.Path.cwd()
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "hass_mcp_engineering_beta")]
REVIEWED = "f5f83c4dd91f01ad1cf8b8c74144e050885ac208"
SCRATCH = ROOT / ".artifacts/hamcp242"
RESULT = ROOT / "docs/evidence/hamcp242/r1_nominal_identity.json"
MODULE = "hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/coordinator.py"

import tests.test_hamcp242_core_authority_itemization as fixture  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import runtime  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.models import CoreDisposition  # noqa: E402
from ha_mcp_engineering.health import HealthRegistry  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def reviewed_coordinator():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    path = SCRATCH / "reviewed_coordinator.py"
    path.write_bytes(subprocess.check_output(["git", "show", f"{REVIEWED}:{MODULE}"]))
    name = "ha_mcp_engineering.ha_core_readmission._reviewed_coordinator"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.CoreReadmissionCoordinator


def wire(value) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()


async def public_block(cls):
    case = fixture.LiveCaseTests()
    with patch.object(runtime, "CoreReadmissionCoordinator", cls), patch.object(
        runtime, "datetime", FixedDateTime
    ):
        await case.asyncSetUp()
        await case.admit(fixture.signed_sequence_14_entry())
    with patch.object(compatibility, "HEALTH", HealthRegistry(core_readmission=case.core)):
        envelope = json.loads(await compatibility.get_server_health(check_ha=False))
    return case, envelope


async def main() -> int:
    reviewed_case, reviewed = await public_block(reviewed_coordinator())
    current_case, current = await public_block(runtime.CoreReadmissionCoordinator)
    before = reviewed["data"]["home_assistant_core_authority"]
    after = current["data"]["home_assistant_core_authority"]
    faults = {}
    coordinator = current_case.core._coordinator  # noqa: SLF001
    changes = {
        "held_count": lambda a: a.update(held_count=a["held_count"] + 1),
        "quarantined_count": lambda a: a.update(quarantined_count=a["quarantined_count"] + 1),
        "unavailable_count": lambda a: a.update(unavailable_count=a["unavailable_count"] - 1),
        "withheld_total": lambda a: a.update(
            withheld_count=a["withheld_count"] + 1, compatible_count=a["compatible_count"] - 1
        ),
        "duplicate_capability": lambda a: a.update(
            held_capabilities=[dict(a["unavailable_capabilities"][0])],
            held_count=1,
            withheld_count=a["withheld_count"] + 1,
            compatible_count=a["compatible_count"] - 1,
        ),
        "partition_count": lambda a: a.update(compatible_count=a["compatible_count"] + 1),
    }
    with patch.object(compatibility, "HEALTH", HealthRegistry(core_readmission=current_case.core)):
        for name, change in changes.items():
            assessment = coordinator.update_assessment()
            change(assessment)
            with patch.object(coordinator, "update_assessment", return_value=assessment):
                faults[name] = json.loads(await compatibility.get_server_health(check_ha=False))
        published = coordinator._published  # noqa: SLF001
        first, *rest = published.decisions
        with patch.object(
            coordinator,
            "_published",
            replace(published, decisions=(replace(first, disposition=CoreDisposition.VERIFYING), *rest)),
        ):
            faults["unclassified_decision"] = json.loads(
                await compatibility.get_server_health(check_ha=False)
            )
    others = sorted(k for k in current["data"] if k not in ("home_assistant_core_authority", "uptime_seconds"))
    result = {
        "reviewed_head": REVIEWED,
        "nominal_block_byte_identical": wire(before) == wire(after),
        "nominal_block_sha256": hashlib.sha256(wire(after)).hexdigest(),
        "nominal_block_bytes": len(wire(after)),
        "faults": {
            name: {
                "success": envelope["success"],
                "block": envelope["data"]["home_assistant_core_authority"],
                "other_sections_equal_nominal": all(
                    envelope["data"][key] == current["data"][key] for key in others
                ),
            }
            for name, envelope in faults.items()
        },
        "other_sections_compared": others,
    }
    RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    reviewed_case.doCleanups()
    current_case.doCleanups()
    ok = result["nominal_block_byte_identical"] and all(
        item["success"] and item["other_sections_equal_nominal"]
        and item["block"]["projection_degraded"] is True
        for item in result["faults"].values()
    )
    print(json.dumps({k: v for k, v in result.items() if k != "faults"} | {"faults_contained": ok}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
