#!/usr/bin/env python3
"""Generate a v1 automation baseline with the exact shipped beta.37 capture code.

HAMCP-228 changes the capture schema to ``automation-audit-baseline-v2``. The
compatibility fixture for the earlier v1 shape is written by the exact
shipped 2.4.1-beta.37 capture provider and service (merge commit
06a28134), driven by a synthetic in-process Home Assistant peer. It is not a
reconstruction of the historical schema. The script imports the capture code
only from ``--source-root`` and refuses any other checkout.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any


SOURCE_VERSION = "2.4.1-beta.37"
SOURCE_COMMIT = "06a28134f219244f258ab26307c1dfcb9e5d18cb"
FIXTURE_NAME = "hamcp228_beta37_v1_baseline.json"
INPUT_NAME = "hamcp228_beta37_v1_baseline_input.json"
PROVENANCE_NAME = "hamcp228_beta37_v1_baseline_provenance.json"
CALLER = "synthetic-hamcp228-fixture-owner"


def synthetic_input() -> dict[str, Any]:
    """Synthetic Home Assistant reads: two readable automations, one unreadable."""

    ids = ("beta37_alpha", "beta37_bravo", "beta37_charlie")
    return {
        "principal": {"is_admin": True},
        "entries": [{"entry_id": "synthetic_entry", "domain": "hassio", "state": "loaded"}],
        "devices": [{"id": "synthetic_core_device", "config_entry_id": "synthetic_entry",
                     "identifiers": [["hassio", "core"]], "parent_device_id": None}],
        "states": [{"entity_id": "automation." + item, "state": "off" if index == 1 else "on",
                    "attributes": {"id": item}} for index, item in enumerate(ids)],
        "registry": [{"entity_id": "automation." + item, "unique_id": item, "platform": "automation",
                      "disabled_by": None} for item in ids],
        "configs": {
            "beta37_alpha": {"id": "beta37_alpha", "mode": "single",
                             "triggers": [{"trigger": "time", "at": "07:00:00"}],
                             "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.synthetic"}}]},
            "beta37_bravo": {"id": "beta37_bravo", "mode": "restart", "description": "Küche 20 °C",
                             "actions": [{"delay": {"seconds": 1.5}}]},
            "beta37_charlie": None,
        },
    }


class SyntheticCore:
    def __init__(self, requirements):
        self.requirements = requirements
        self.current_observation = SimpleNamespace(version="2026.9.4")

    def route_status(self, requirements):
        if tuple(requirements) != tuple(self.requirements):
            raise SystemExit("unexpected Core requirements")
        return {"available": True, "generation": 1}

    def health_projection(self):
        return {"compatible_count": 21, "release_registry": {"sequence": 5},
                "counters": {"verification_failures": 0, "retirements": 0}}


class SyntheticClient:
    """The beta.37 ``BaselineReadClient`` surface, answering from synthetic data."""

    def __init__(self, data, error):
        self.data, self.error = data, error
        self.requests = self.consumed = 0

    @asynccontextmanager
    async def capture(self, version, authorize):
        self.authorize = authorize
        yield self

    async def read(self, kind):
        self.authorize()
        self.requests += 1
        return copy.deepcopy(self.data[kind])

    async def configuration(self, identifier):
        self.authorize()
        self.requests += 1
        value = self.data["configs"].get(identifier)
        if value is None:
            raise self.error("configuration_unavailable")
        return copy.deepcopy(value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _source_commit(source_root: Path) -> str:
    return subprocess.check_output(["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True).strip()


async def _capture(source_root: Path, data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    package_root = source_root / "hass_mcp_engineering_beta"
    sys.path.insert(0, str(package_root))
    import ha_mcp_engineering
    if Path(ha_mcp_engineering.__file__).resolve().parents[1] != package_root:
        raise SystemExit("ha_mcp_engineering was not imported from the source root")
    from ha_mcp_engineering.audit_baseline import capture_contracts as c
    from ha_mcp_engineering.audit_baseline.capture_provider import BaselineCaptureProvider
    from ha_mcp_engineering.audit_baseline.capture_service import BaselineCaptureService
    from ha_mcp_engineering.request_context import begin_request, end_request

    telemetry, token = begin_request()
    telemetry.caller_id = CALLER
    telemetry.core_dispatch_authorizer = lambda: True
    try:
        service = BaselineCaptureService(BaselineCaptureProvider(
            SyntheticClient(data, c.CaptureError), SyntheticCore(c.REQUIREMENTS)))
        page = await service.capture(limit=2)
        baseline = copy.deepcopy(page["baseline_header"])
        baseline["records"] = list(page["records"])
        artifact = page["artifact_sha256"]
        while page["pagination"]["next_cursor"]:
            page = await service.capture(limit=2, cursor=page["pagination"]["next_cursor"])
            if page["artifact_sha256"] != artifact:
                raise SystemExit("artifact digest changed between pages")
            baseline["records"].extend(page["records"])
        if c.digest(baseline) != artifact:
            raise SystemExit("assembled baseline does not match the exported digest")
        return baseline, artifact
    finally:
        end_request(token)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generated-at", required=True)
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    output_dir = args.output_dir.resolve()
    if _source_commit(source_root) != SOURCE_COMMIT:
        raise SystemExit("source root is not the exact shipped beta.37 merge commit")
    data = synthetic_input()
    baseline, artifact = asyncio.run(_capture(source_root, copy.deepcopy(data)))
    if baseline.get("schema") != "automation-audit-baseline-v1":
        raise SystemExit("beta.37 did not write a v1 baseline")
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = output_dir / FIXTURE_NAME
    input_path = output_dir / INPUT_NAME
    # The exported canonical bytes, exactly as the shipped code hashed them.
    fixture_path.write_bytes(json.dumps(baseline, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=True, allow_nan=False).encode("utf-8"))
    _write_json(input_path, data)
    package = source_root / "hass_mcp_engineering_beta" / "ha_mcp_engineering" / "audit_baseline"
    generator = Path(__file__).resolve()
    _write_json(output_dir / PROVENANCE_NAME, {
        "schema_version": 1,
        "compatibility_model": "hamcp228-shipped-beta37-v1-baseline-v1",
        "generated_at": args.generated_at,
        "generator": "scripts/" + generator.name,
        "generator_sha256": _sha256(generator),
        "generator_command": ("python3 scripts/" + generator.name + " --source-root <exact-beta37-worktree>"
                              " --output-dir tests/fixtures --generated-at " + args.generated_at),
        "source": {"commit": SOURCE_COMMIT, "version": SOURCE_VERSION,
                   "audit_baseline_sources_sha256": {path.name: _sha256(path)
                                                     for path in sorted(package.glob("*.py"))}},
        "writer": "BaselineCaptureService.capture (exported pages assembled; artifact digest verified)",
        "fixture": {"path": "tests/fixtures/" + FIXTURE_NAME, "sha256": _sha256(fixture_path),
                    "artifact_sha256": artifact, "schema": baseline["schema"],
                    "record_count": len(baseline["records"]),
                    "statuses": sorted({row["configuration"]["status"] for row in baseline["records"]})},
        "input": {"path": "tests/fixtures/" + INPUT_NAME, "sha256": _sha256(input_path),
                  "description": "synthetic Home Assistant reads replayed by the in-process peer"},
        "boundary": "Synthetic data only; no live Home Assistant. The fixture bytes are the shipped "
                    "code's canonical export and must not be edited.",
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
