#!/usr/bin/env python3
"""Generate exact shipped beta.59 input_boolean v13 plan records for HAMCP-285.

Both records are written by the exact v2.4.1-beta.59 governance service and
F3 runtime through that revision's own input_boolean test harness: one plan
left awaiting approval and one plan approved with an external approval.  The
persisted bytes are copied verbatim; they are never reconstructed or edited.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


SOURCE_TAG = "v2.4.1-beta.59"
SOURCE_COMMIT = "8615ad146a2cf239410708a3bd6254e46c9c8ed0"
FIXTURE_DIR = "hamcp285_beta59_input_boolean_v13"
PROVENANCE_NAME = "hamcp285_beta59_input_boolean_v13_provenance.json"
PLAN_STORE = "plans/operational-administration-v3"
V13_MODEL = "helper-dependency-risk-v13"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_commit(source_root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generated-at", required=True)
    return parser.parse_args()


def _shipped_lock_graph(stored: Any) -> list[list[str]]:
    """The lock graph the exact beta.59 calculator derives for this record."""

    from types import SimpleNamespace  # noqa: PLC0415

    from ha_mcp_engineering.f3.operational_locks import (  # noqa: PLC0415
        OperationalLockSetCalculator,
    )

    operation = SimpleNamespace(
        validate=lambda: None,
        operation=stored.operation.value,
        target=SimpleNamespace(target_id=stored.target_id),
        authoritative_provider_slug=stored.operational.provider,
        baseline=stored.operational.baseline,
    )
    return [
        [item.key, item.mode.value]
        for item in OperationalLockSetCalculator().calculate(operation)
    ]


async def _write_record(case_type: type, approved: bool, destination: Path) -> dict[str, Any]:
    case = case_type()
    await case.asyncSetUp()
    try:
        created = await case.service.create_helper_state_plan(
            entity_id=case.helper.entity_id,
            desired_state="on",
        )
        plan = created["plan"]
        if approved:
            await case.grant(created)
        if case.helper.dispatch_count:
            raise SystemExit("historical writer dispatched to the provider")
        stored = case.service._load(plan["plan_id"])
        binding = stored.operational.baseline["dependency_risk"]
        if binding.get("model") != V13_MODEL:
            raise SystemExit("historical writer did not emit v13")
        if "coarse_automation_fence" in binding["dependency_lock_projection"]:
            raise SystemExit("historical writer emitted a coarse fence")
        shipped_graph = _shipped_lock_graph(stored)
        root = Path(case.temp.name)
        record = root / PLAN_STORE / f"{plan['plan_id']}.json"
        audit = root / "audit.jsonl"
        destination.mkdir(parents=True, exist_ok=False)
        (destination / PLAN_STORE).mkdir(parents=True)
        shutil.copyfile(record, destination / PLAN_STORE / record.name)
        shutil.copyfile(audit, destination / "audit.jsonl")
        return {
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "status": stored.status.value,
            "approval_state": stored.approval.state.value,
            "dependency_model": binding["model"],
            "provider_write_count": case.helper.dispatch_count,
            "shipped_execution_lock_graph": shipped_graph,
        }
    finally:
        await case.asyncTearDown()
        case.doCleanups()


def main() -> int:
    args = _parse_args()
    source_root = args.source_root.resolve()
    output_dir = args.output_dir.resolve()
    if _source_commit(source_root) != SOURCE_COMMIT:
        raise SystemExit("source root is not exact shipped beta.59")
    target = output_dir / FIXTURE_DIR
    if target.exists():
        raise SystemExit(f"refusing to overwrite {target}")

    os.chdir(source_root)
    sys.path[:0] = [str(source_root), str(source_root / "hass_mcp_engineering_beta")]
    from tests.test_beta37_exact_helper_state import (  # noqa: PLC0415
        ExactHelperStateRuntimeTests,
    )

    for name in (
        "tests.test_beta37_exact_helper_state",
        "ha_mcp_engineering.governance.service",
        "ha_mcp_engineering.governance.helper_dependency",
        "ha_mcp_engineering.f3.operational_locks",
    ):
        imported = Path(sys.modules[name].__file__).resolve()
        if not imported.is_relative_to(source_root):
            raise SystemExit(f"{name} was not imported from the source root")

    records = {}
    for kind, approved in (("pending", False), ("approved", True)):
        records[kind] = asyncio.run(
            _write_record(ExactHelperStateRuntimeTests, approved, target / kind)
        )
    if records["pending"]["approval_state"] == records["approved"]["approval_state"]:
        raise SystemExit("historical writer did not approve the approved record")

    generator = Path(__file__).resolve()
    provenance = {
        "schema_version": 1,
        "compatibility_model": "hamcp285-beta59-input-boolean-v13-replan-v1",
        "generated_at": args.generated_at,
        "generator": "scripts/generate_hamcp285_beta59_input_boolean_v13_fixture.py",
        "generator_command": (
            "python scripts/generate_hamcp285_beta59_input_boolean_v13_fixture.py "
            "--source-root <exact-beta59-clone> --output-dir tests/fixtures "
            f"--generated-at {args.generated_at}"
        ),
        "generator_sha256": _sha256(generator),
        "source": {
            "commit": SOURCE_COMMIT,
            "tag": SOURCE_TAG,
            "writer": (
                "tests.test_beta37_exact_helper_state.ExactHelperStateRuntimeTests "
                "create_helper_state_plan(desired_state='on') [+ grant] on the "
                "exact source revision"
            ),
            "helper_dependency_source_sha256": _sha256(
                source_root
                / "hass_mcp_engineering_beta/ha_mcp_engineering/governance/helper_dependency.py"
            ),
            "operational_locks_source_sha256": _sha256(
                source_root
                / "hass_mcp_engineering_beta/ha_mcp_engineering/f3/operational_locks.py"
            ),
            "service_source_sha256": _sha256(
                source_root
                / "hass_mcp_engineering_beta/ha_mcp_engineering/governance/service.py"
            ),
        },
        "records": {
            kind: {
                **records[kind],
                "files": {
                    str(path.relative_to(target / kind)): _sha256(path)
                    for path in sorted((target / kind).rglob("*"))
                    if path.is_file()
                },
            }
            for kind in records
        },
        "boundary": (
            "Hash-bearing bytes are copied verbatim from the exact beta.59 "
            "writer and must not be edited.  Current code may read them and "
            "reconstruct the shipped v13 recovery lock graph, but must refuse "
            "fresh execution with a structured replan result."
        ),
    }
    (output_dir / PROVENANCE_NAME).write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(records, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
