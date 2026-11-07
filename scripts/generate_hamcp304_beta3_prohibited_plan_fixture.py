#!/usr/bin/env python3
"""Generate exact shipped v2.4.2-beta.3 plan and acknowledgement records for HAMCP-304.

Every record is written by the exact v2.4.2-beta.3 governance service through
that revision's own test harnesses, in the live shapes the architect reported:

- ``prohibited_input_boolean_acknowledged`` and
  ``prohibited_input_boolean_unacknowledged``: never-approvable prohibited
  ``set_input_boolean_state`` plans (live ``2ab4764f`` and ``faba2fbd``). The
  first is acknowledged through the shipped acknowledgement path against the
  shipped inspection view.
- ``prohibited_input_select_acknowledged``: a never-approvable prohibited
  ``set_input_select_option`` plan (live ``4cf748d1``), acknowledged the same
  way.
- ``expired_elevated_input_boolean``: an elevated ``set_input_boolean_state``
  plan whose approval challenge expired unapproved (live ``d3af624a``).

The persisted bytes are copied verbatim; they are never reconstructed or
edited. The shipped inspection view of each record is recorded beside it.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


SOURCE_TAG = "v2.4.2-beta.3"
SOURCE_COMMIT = "446a0dd79629a93165fe68e897ff5a119c42f25e"
FIXTURE_DIR = "hamcp304_beta3_prohibited_plans"
PROVENANCE_NAME = "hamcp304_beta3_prohibited_plans_provenance.json"
PLAN_STORE = "plans/operational-administration-v3"
LEDGER = "plans/hamcp253-record-acknowledgement-v1/acknowledgements.json"
PRINCIPAL = "home_assistant_admin_ingress:hamcp304-fixture-admin"
REASON = "Reviewed: never-approvable prohibited plan before HAMCP-304"


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


def _shipped_view(service: Any, plan_id: str) -> dict[str, Any]:
    view = service.inspect_governance_record(
        record_kind="change_plan", record_id=plan_id
    )
    return {
        "status": view["status"],
        "policy_class": view["policy_class"],
        "approval": view["approval"],
        "failure": view["failure"],
        "events": [row["event"] for row in view["events"]],
        "acknowledgeable": view["acknowledgeable"],
        "acknowledgement_state": view["acknowledgement"]["state"],
        "record_fingerprint": view["record_fingerprint"],
    }


def _acknowledge(service: Any, plan_id: str, classification: str) -> dict[str, Any]:
    fingerprint = service.inspect_governance_record(
        record_kind="change_plan", record_id=plan_id
    )["record_fingerprint"]
    outcome = service.acknowledge_governance_record(
        record_kind="change_plan",
        record_id=plan_id,
        action="acknowledge",
        classification=classification,
        reason=REASON,
        record_fingerprint=fingerprint,
        principal=PRINCIPAL,
    )
    if outcome.get("status") != "acknowledged" or not outcome.get("audit_recorded"):
        raise SystemExit(f"historical acknowledgement was not recorded: {outcome}")
    return {
        "classification": classification,
        "record_fingerprint": fingerprint,
        "principal": PRINCIPAL,
    }


def _copy(root: Path, plan_id: str, destination: Path, *, ledger: bool) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    (destination / PLAN_STORE).mkdir(parents=True)
    record = root / PLAN_STORE / f"{plan_id}.json"
    shutil.copyfile(record, destination / PLAN_STORE / record.name)
    if ledger:
        (destination / LEDGER).parent.mkdir(parents=True)
        shutil.copyfile(root / LEDGER, destination / LEDGER)


def _prohibited_boolean_evidence(case: Any, beta54: Any) -> None:
    """``test_execution_contract_failure_cannot_be_acknowledged``'s evidence."""

    import copy  # noqa: PLC0415

    from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: PLC0415

    evidence = copy.deepcopy(case.dependency.evidence)
    binding = evidence["binding"]
    binding["execution_contract_complete"] = False
    binding["execution_eligible"] = False
    binding["execution_block_reason_codes"] = ["automation_lock_identity_unavailable"]
    material = dict(binding)
    material.pop("evidence_fingerprint", None)
    binding["evidence_fingerprint"] = stable_hash(material)
    reader = beta54._FrozenDependencyRiskReader(evidence)
    case.service.helper_dependency_risk_reader = reader
    case.runtime.operational_adapter.strategies[
        "set_input_boolean_state"
    ].dependency_risk_reader = reader


async def _boolean_record(
    beta54: Any, kind: str, destination: Path
) -> dict[str, Any]:
    case = beta54.Beta54CapturedHelperAuthorityTests()
    await case.asyncSetUp()
    try:
        if kind != "expired_elevated_input_boolean":
            _prohibited_boolean_evidence(case, beta54)
        created = await case.service.create_helper_state_plan(
            entity_id=case.target, desired_state="on"
        )
        plan = created["plan"]
        policy_class = plan["policy_decision"]["policy_class"]
        acknowledgement = None
        if kind == "expired_elevated_input_boolean":
            if policy_class != "elevated_admin":
                raise SystemExit("historical writer did not create an elevated plan")
            pending = case.service.approve(plan["plan_id"], plan["plan_hash"])
            if pending.get("approval_kind") != "apply":
                raise SystemExit("historical writer did not issue an apply challenge")
            case.clock.value += timedelta(days=2)
            if case.service.get_plan(plan["plan_id"])["status"] != "expired":
                raise SystemExit("historical writer did not expire the plan")
        else:
            if policy_class != "prohibited" or plan["approval_actionable"]:
                raise SystemExit("historical writer did not create a prohibited plan")
            if kind == "prohibited_input_boolean_acknowledged":
                acknowledgement = _acknowledge(
                    case.service, plan["plan_id"], "historical_record"
                )
        if case.helper.dispatch_count:
            raise SystemExit("historical writer dispatched to the provider")
        view = _shipped_view(case.service, plan["plan_id"])
        root = Path(case.temp.name)
        _copy(root, plan["plan_id"], destination, ledger=acknowledgement is not None)
        return {
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "operation": "set_input_boolean_state",
            "policy_class": policy_class,
            "clock": case.clock.value.isoformat(),
            "provider_dispatch_count": case.helper.dispatch_count,
            "shipped_view": view,
            "acknowledgement": acknowledgement,
        }
    finally:
        await case.asyncTearDown()
        case.doCleanups()


async def _select_record(hamcp274: Any, destination: Path) -> dict[str, Any]:
    case = hamcp274.InstallationTestCase()
    await case.asyncSetUp()
    try:
        # ``test_unreadable_dependency_evidence_stays_prohibited``.
        reader = await case.install()

        async def broken(*_: Any, **__: Any) -> Any:
            raise RuntimeError("synthetic index failure")

        reader.index.get = broken
        created = await case.create_select("Away")
        plan = created["plan"]
        if (
            plan["policy_decision"]["policy_class"] != "prohibited"
            or plan["approval_actionable"]
        ):
            raise SystemExit("historical writer did not create a prohibited plan")
        acknowledgement = _acknowledge(case.service, plan["plan_id"], "current_defect")
        if case.sent():
            raise SystemExit("historical writer dispatched to the provider")
        view = _shipped_view(case.service, plan["plan_id"])
        _copy(Path(case.root), plan["plan_id"], destination, ledger=True)
        return {
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "operation": "set_input_select_option",
            "policy_class": "prohibited",
            "clock": case.clock[0].isoformat(),
            "provider_dispatch_count": len(case.sent()),
            "shipped_view": view,
            "acknowledgement": acknowledgement,
        }
    finally:
        await case.asyncTearDown()
        case.doCleanups()


def main() -> int:
    args = _parse_args()
    source_root = args.source_root.resolve()
    output_dir = args.output_dir.resolve()
    if _source_commit(source_root) != SOURCE_COMMIT:
        raise SystemExit("source root is not exact shipped v2.4.2-beta.3")
    target = output_dir / FIXTURE_DIR
    if target.exists():
        raise SystemExit(f"refusing to overwrite {target}")

    os.chdir(source_root)
    sys.path[:0] = [str(source_root), str(source_root / "hass_mcp_engineering_beta")]
    from tests import test_beta54_owner_authoritative_execution as beta54  # noqa: PLC0415
    from tests import test_hamcp274_input_select_relevance as hamcp274  # noqa: PLC0415

    for name in (
        "tests.test_beta54_owner_authoritative_execution",
        "tests.test_hamcp274_input_select_relevance",
        "ha_mcp_engineering.governance.service",
        "ha_mcp_engineering.governance.record_inspection",
        "ha_mcp_engineering.governance.record_acknowledgement",
    ):
        imported = Path(sys.modules[name].__file__).resolve()
        if not imported.is_relative_to(source_root):
            raise SystemExit(f"{name} was not imported from the source root")

    records: dict[str, dict[str, Any]] = {}
    for kind in (
        "prohibited_input_boolean_acknowledged",
        "prohibited_input_boolean_unacknowledged",
        "expired_elevated_input_boolean",
    ):
        records[kind] = asyncio.run(_boolean_record(beta54, kind, target / kind))
    records["prohibited_input_select_acknowledged"] = asyncio.run(
        _select_record(hamcp274, target / "prohibited_input_select_acknowledged")
    )

    generator = Path(__file__).resolve()
    governance = source_root / "hass_mcp_engineering_beta/ha_mcp_engineering/governance"
    provenance = {
        "schema_version": 1,
        "compatibility_model": "hamcp304-beta3-prohibited-plan-projection-v1",
        "generated_at": args.generated_at,
        "generator": "scripts/generate_hamcp304_beta3_prohibited_plan_fixture.py",
        "generator_command": (
            "python scripts/generate_hamcp304_beta3_prohibited_plan_fixture.py "
            "--source-root <exact-v2.4.2-beta.3-clone> --output-dir tests/fixtures "
            f"--generated-at {args.generated_at}"
        ),
        "generator_sha256": _sha256(generator),
        "source": {
            "commit": SOURCE_COMMIT,
            "tag": SOURCE_TAG,
            "writer": (
                "tests.test_beta54_owner_authoritative_execution."
                "Beta54CapturedHelperAuthorityTests and "
                "tests.test_hamcp274_input_select_relevance.InstallationTestCase "
                "on the exact source revision; acknowledgements through "
                "ChangeGovernanceService.acknowledge_governance_record"
            ),
            "service_source_sha256": _sha256(governance / "service.py"),
            "record_inspection_source_sha256": _sha256(
                governance / "record_inspection.py"
            ),
            "record_acknowledgement_source_sha256": _sha256(
                governance / "record_acknowledgement.py"
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
            "Hash-bearing bytes are copied verbatim from the exact "
            "v2.4.2-beta.3 writer and must not be edited.  Current code reads "
            "them without rewriting them; shipped_view is the inspection view "
            "the shipped code returned for each record."
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
