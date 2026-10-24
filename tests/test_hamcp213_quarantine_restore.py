"""HAMCP-213 PR 1 batch 4 (review H6): rollback quarantine and re-upgrade.

beta.27 rejects every f2-v3 plan record and moves it, byte for byte, to
``quarantine/<plan_id>.<seconds>.corrupt`` (or, when that move fails,
deletes it: released behavior accepted by the owner, protected by the
documented pre-rollback backup). On re-upgrade, startup restores each
quarantined record whose integrity is verified to normal repository access,
never over an existing record, idempotently, and reports every record it
keeps quarantined in health.

The beta.27 move is reproduced here exactly as that release performs it;
the reviewer's executed old-loader round trip is separate evidence. All
fixtures are synthetic and anonymized.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    OPERATIONAL_NAMESPACE,
    ChangePlanRepository,
)
from tests import test_dev14_configuration_plans as dev14  # noqa: E402
from tests import test_hamcp213_in_plan_callee as ipc  # noqa: E402
from tests import test_hamcp213_review_corrections as rc  # noqa: E402

# beta.27 ``ChangePlanRepository._quarantine`` destination name.
BETA27_NAME = re.compile(r"^[a-f0-9]{32}\.[0-9]+\.corrupt$")
SECRET = "dev14-test-ha-token"
LIGHT = {"action": "light.turn_on", "target": {"entity_id": "light.review_porch"}}


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


class QuarantineRestorationTests(rc._ReviewCorrectionChecks, dev14.ConfigurationPlanTestCase):
    # Only the shared helpers are reused.
    test_h1_unresolvable_effects_require_the_acknowledgement = None
    test_h2_reference_steps_before_an_effect_are_compared_in_full = None
    test_h3_unlock_and_disarm_always_require_the_acknowledgement = None
    test_controls_keep_plan_approval_only = None
    test_garage_retained_effect_exception_is_unchanged = None
    test_pr001_verdicts_are_unchanged_by_the_corrections = None

    seed = ipc._InPlanCalleeChecks.seed
    plan = ipc._InPlanCalleeChecks.plan

    @property
    def plans_root(self) -> Path:
        return self.root / "plans"

    def restart(self):
        """Start this version again over the same storage (re-upgrade)."""

        self.repository = ChangePlanRepository(self.plans_root)
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            AuditLogger(str(self.audit_path), "dev14-test-access-secret"),
            now=lambda: datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc),
            sensitive_values=("dev14-test-access-secret", SECRET),
        )
        return self.repository.health()["quarantine_restoration"]

    def live_records(self) -> dict[str, bytes]:
        return {
            path.stem: path.read_bytes()
            for path in sorted(self.plans_root.glob("*.json"))
        }

    def quarantined(self) -> dict[str, bytes]:
        return {
            path.name: path.read_bytes()
            for path in sorted((self.plans_root / "quarantine").iterdir())
        }

    def beta27_quarantine(self, stamp: int = 1792595054) -> dict[str, bytes]:
        """Move every live record exactly as beta.27 ``_quarantine`` does."""

        moved = {}
        for path in sorted(self.plans_root.glob("*.json")):
            destination = self.plans_root / "quarantine" / f"{path.stem}.{stamp}.corrupt"
            self.assertRegex(destination.name, BETA27_NAME)
            moved[path.stem] = path.read_bytes()
            os.replace(path, destination)
        self.assertEqual(self.live_records(), {})
        return moved

    def write_quarantined(self, plan_id: str, raw: bytes, stamp: int = 1792595100, namespace=None):
        directory = self.plans_root / (namespace or "") / "quarantine"
        path = directory / f"{plan_id}.{stamp}.corrupt"
        path.write_bytes(raw)
        return path

    async def f2_v3_store(self):
        """Pending, acknowledged-approved and applied f2-v3 records."""

        self.seed()
        pending = await self.plan(callee_first=True, callee_after=ipc.UNLOCK_CALLEE)
        approved = await self.create_automation_plan()
        self.gateway.configs[("automation", "review_applied")] = rc.automation(
            LIGHT, description="before"
        )
        applied = await self.service.create_configuration_plan(
            title="Applied control",
            description="HAMCP-213 synthetic rollback fixture",
            operations=[
                {
                    "operation_id": "op",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": "review_applied",
                    "depends_on": [],
                    "proposed_config": rc.automation(LIGHT),
                }
            ],
        )
        await self.approve_plan_only(approved)
        await self.approve_plan_only(applied)
        await self.service.apply(applied["plan_id"], applied["plan_hash"])
        plans = {
            "pending": pending["plan_id"],
            "approved": approved["plan_id"],
            "applied": applied["plan_id"],
        }
        for plan_id in plans.values():
            self.assertEqual(
                self.repository.get(plan_id).policy_decision.policy_version, "f2-v3"
            )
        self.assertEqual(self.repository.get(plans["applied"]).status.value, "applied")
        return plans, {"approved": approved}

    # -- restoration -------------------------------------------------------

    async def test_beta27_quarantined_f2_v3_records_are_restored_on_reupgrade(self):
        plans, created = await self.f2_v3_store()
        before = self.live_records()
        moved = self.beta27_quarantine()
        self.assertEqual(moved, before)
        report = self.restart()
        self.assertEqual(report["restored"], 3)
        self.assertEqual(report["retained"], 0)
        self.assertTrue(report["performed"])
        self.assertEqual(self.live_records(), before)
        self.assertEqual(self.quarantined(), {})
        listed = {plan.plan_id for plan in self.repository.list()}
        self.assertEqual(listed, set(plans.values()))
        self.assertEqual(
            self.service.get_plan(plans["applied"])["status"], "applied"
        )
        self.assertEqual(
            self.service.get_plan(plans["pending"])["status"], "awaiting_approval"
        )
        # Normal access: the restored approved plan applies as before.
        approved = created["approved"]
        self.gateway.calls.clear()
        await self.service.apply(approved["plan_id"], approved["plan_hash"])
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.repository.get(approved["plan_id"]).status.value, "applied")

    async def test_without_restoration_the_records_are_not_visible(self):
        # The re-upgrade defect the restoration corrects: a bare repository
        # (no startup restoration) lists none of the quarantined records.
        plans, _created = await self.f2_v3_store()
        self.beta27_quarantine()
        bare = ChangePlanRepository(self.plans_root)
        self.assertEqual(bare.list(), [])
        self.assertFalse(bare.health()["quarantine_restoration"]["performed"])
        self.restart()
        self.assertEqual(
            {plan.plan_id for plan in self.repository.list()}, set(plans.values())
        )

    async def test_restoration_is_idempotent(self):
        plans, _created = await self.f2_v3_store()
        before = self.live_records()
        self.beta27_quarantine()
        self.assertEqual(self.restart()["restored"], 3)
        second = self.restart()
        self.assertEqual((second["restored"], second["retained"]), (0, 0))
        self.assertEqual(self.live_records(), before)
        self.assertEqual(
            {plan.plan_id for plan in self.repository.list()}, set(plans.values())
        )

    async def test_an_interrupted_restoration_completes_on_the_next_startup(self):
        plans, _created = await self.f2_v3_store()
        before = self.live_records()
        self.beta27_quarantine()
        # Linked into place, stopped before the quarantined name was removed.
        quarantined = self.plans_root / "quarantine" / f"{plans['applied']}.1792595054.corrupt"
        os.link(quarantined, self.plans_root / f"{plans['applied']}.json")
        report = self.restart()
        self.assertEqual((report["restored"], report["retained"]), (2, 0))
        self.assertEqual(self.live_records(), before)
        self.assertEqual(self.quarantined(), {})

    async def test_a_newer_record_is_never_overwritten(self):
        plans, _created = await self.f2_v3_store()
        plan_id = plans["applied"]
        newer = self.live_records()[plan_id]
        older = json.loads(newer)
        older["updated_at"] = "2026-07-01T00:00:00+00:00"
        older_bytes = canonical(older)
        self.assertNotEqual(older_bytes, newer)
        path = self.write_quarantined(plan_id, older_bytes)
        report = self.restart()
        self.assertEqual(self.live_records()[plan_id], newer)
        self.assertEqual(path.read_bytes(), older_bytes)
        self.assertEqual(report["restored"], 0)
        self.assertIn(
            {"record": path.name, "reason": "conflicting_record"},
            report["retained_records"],
        )

    async def test_unverifiable_records_stay_quarantined_and_are_reported(self):
        plans, _created = await self.f2_v3_store()
        moved = self.beta27_quarantine()
        good = plans["approved"]
        damaged = {}
        # Truncated bytes.
        truncated_id = plans["pending"]
        truncated = self.plans_root / "quarantine" / f"{truncated_id}.1792595054.corrupt"
        truncated.write_bytes(moved[truncated_id][:-17])
        damaged[truncated.name] = "unsupported_or_corrupt"
        # Parseable, but not the bytes this version writes.
        applied_id = plans["applied"]
        pretty = self.plans_root / "quarantine" / f"{applied_id}.1792595054.corrupt"
        pretty.write_bytes(
            json.dumps(json.loads(moved[applied_id]), indent=2, sort_keys=True).encode()
        )
        damaged[pretty.name] = "not_canonical"
        # A canonical record under another identifier's name.
        renamed = self.write_quarantined("0" * 32, moved[good])
        damaged[renamed.name] = "identifier_mismatch"
        # Canonical, parseable, carrying a known secret.
        leaked = json.loads(moved[good])
        leaked["description"] = f"Synthetic {SECRET}"
        leaked_id = "1" * 32
        leaked["plan_id"] = leaked_id
        secret_path = self.write_quarantined(leaked_id, canonical(leaked))
        damaged[secret_path.name] = "content_refused"
        # A configuration record inside the operational quarantine.
        misplaced_id = "2" * 32
        misplaced = json.loads(moved[good])
        misplaced["plan_id"] = misplaced_id
        wrong_namespace = self.write_quarantined(
            misplaced_id, canonical(misplaced), namespace=OPERATIONAL_NAMESPACE
        )
        damaged[wrong_namespace.name] = "namespace_mismatch"
        # A symbolic link is never followed.
        link = self.plans_root / "quarantine" / f"{'3' * 32}.1792595100.corrupt"
        link.symlink_to(self.plans_root / "quarantine" / f"{good}.1792595054.corrupt")
        damaged[link.name] = "not_regular_file"
        damaged_bytes = {
            path.name: path.read_bytes()
            for path in (truncated, pretty, renamed, secret_path, wrong_namespace)
        }

        report = self.restart()
        self.assertEqual(report["restored"], 1)
        self.assertEqual(report["retained"], len(damaged))
        self.assertEqual(
            {item["record"]: item["reason"] for item in report["retained_records"]},
            damaged,
        )
        self.assertEqual(set(self.live_records()), {good})
        for name, raw in damaged_bytes.items():
            directory = (
                self.plans_root / OPERATIONAL_NAMESPACE / "quarantine"
                if name == wrong_namespace.name
                else self.plans_root / "quarantine"
            )
            self.assertEqual((directory / name).read_bytes(), raw)
        self.assertTrue(link.is_symlink())
        for plan_id in (truncated_id, applied_id, "0" * 32, leaked_id, misplaced_id):
            self.assertIsNone(self.repository.get(plan_id))
        storage = self.repository.health()
        self.assertEqual(storage["quarantine_restoration"], report)
        self.assertNotIn(SECRET, json.dumps(storage))
        # Unverifiable records stay quarantined on every later startup.
        again = self.restart()
        self.assertEqual((again["restored"], again["retained"]), (0, len(damaged)))

    async def test_differing_quarantined_copies_are_ambiguous(self):
        plans, _created = await self.f2_v3_store()
        moved = self.beta27_quarantine()
        plan_id = plans["applied"]
        variant = json.loads(moved[plan_id])
        variant["updated_at"] = "2026-07-01T00:00:00+00:00"
        second = self.write_quarantined(plan_id, canonical(variant))
        report = self.restart()
        self.assertEqual(report["restored"], 2)
        self.assertEqual(
            sorted(item["reason"] for item in report["retained_records"]),
            ["ambiguous_candidates", "ambiguous_candidates"],
        )
        self.assertIsNone(self.repository.get(plan_id))
        self.assertTrue(second.exists())

    async def test_restoration_runs_before_cleanup_and_recovery(self):
        order = []

        class Recording(ChangePlanRepository):
            def restore_quarantined_records(self):
                order.append("restore")
                return super().restore_quarantined_records()

            def cleanup(self, **kwargs):
                order.append("cleanup")
                return super().cleanup(**kwargs)

            def recover_incomplete(self, timestamp):
                order.append("recover")
                return super().recover_incomplete(timestamp)

        ChangeGovernanceService(
            Recording(self.plans_root),
            self.gateway,
            AuditLogger(str(self.audit_path), "dev14-test-access-secret"),
            now=lambda: datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc),
            sensitive_values=(SECRET,),
        )
        self.assertEqual(order, ["restore", "cleanup", "recover"])

    async def test_beta27_move_failure_leaves_nothing_to_restore(self):
        # Accepted released behavior: beta.27 deletes a record whose
        # quarantine move fails. Only the pre-rollback backup protects it;
        # re-upgrade reports nothing and invents nothing.
        plans, _created = await self.f2_v3_store()
        for path in self.plans_root.glob("*.json"):
            path.unlink()
        report = self.restart()
        self.assertEqual((report["restored"], report["retained"]), (0, 0))
        self.assertEqual(self.repository.list(), [])
        del plans


if __name__ == "__main__":
    unittest.main()
