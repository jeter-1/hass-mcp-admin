"""Beta 26 regression and scale acceptance for bounded plan-store queries."""

from __future__ import annotations

import copy
from datetime import timedelta
import json
from pathlib import Path
import sys
from time import monotonic
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.storage import ChangePlanRepository
from ha_mcp_engineering.request_context import begin_request, end_request
from tests.test_governance import CURRENT, Clock, FakeGateway


SCALE_COUNTS = (130, 1_000, 10_000)


def _terminal_plan_payload(plan_id: str) -> dict:
    timestamp = "2026-01-01T00:00:00+00:00"
    return {
        "plan_id": plan_id,
        "plan_version": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
        "expires_at": "2026-01-02T00:00:00+00:00",
        "status": "applied",
        "title": "Beta 26 scale fixture",
        "description": "Synthetic terminal plan for bounded-work tests",
        "requested_by": "beta26-scale-fixture",
        "target": {
            "target_type": "automation",
            "target_id": "beta26-scale-fixture",
        },
        "operation": "update_automation",
        "proposed_config": {},
        "current_config": {},
        "normalized_proposed_config": {},
        "normalized_current_config": {},
        "current_state_fingerprint": "0" * 64,
        "proposed_config_hash": "1" * 64,
        "risk": {
            "level": "low",
            "reasons": [],
            "apply_allowed": True,
            "evidence": [],
            "warnings": [],
        },
        "normalization_version": 1,
        "warnings": [],
        "validation_results": {},
        "dry_run_results": {},
        "approval": {"state": "consumed", "authority_version": 1},
        "verification": {"status": "verified"},
        "rollback": {},
        "events": [],
    }


def _write_terminal_history(root: Path, count: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(1, count + 1):
        plan_id = f"{index:032x}"
        payload = _terminal_plan_payload(plan_id)
        (root / f"{plan_id}.json").write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )


def _metric_delta(before: dict[str, int], after: dict[str, int], key: str) -> int:
    return int(after[key]) - int(before[key])


class Beta26PlanStoreScaleTests(unittest.IsolatedAsyncioTestCase):
    def test_beta26_is_staged_without_changing_published_versions(self):
        config = (BETA_DIR / "config.yaml").read_text(encoding="utf-8")
        marker = ROOT / ".release" / "next-version"
        if 'version: "2.2.0-beta.25"' in config:
            self.assertEqual(
                marker.read_text(encoding="utf-8").strip(),
                "2.2.0-beta.26",
            )
        else:
            self.assertIn('version: "2.2.0-beta.26"', config)
            self.assertFalse(marker.exists())
        stable_config = (
            ROOT / "hass_mcp_admin" / "config.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn('version: "1.1.2"', stable_config)

    def _service_with_history(
        self, count: int, active_payload: dict
    ) -> tuple[tempfile.TemporaryDirectory, ChangeGovernanceService, float]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "plans"
        _write_terminal_history(root, count)
        active_id = str(active_payload["plan_id"])
        (root / f"{active_id}.json").write_text(
            json.dumps(
                active_payload, sort_keys=True, separators=(",", ":")
            ),
            encoding="utf-8",
        )
        started = monotonic()
        service = ChangeGovernanceService(
            ChangePlanRepository(root),
            FakeGateway({"porch": CURRENT}),
            now=Clock(),
        )
        return temporary, service, monotonic() - started

    async def _active_review_payload(self) -> dict:
        temporary = tempfile.TemporaryDirectory()
        try:
            root = Path(temporary.name) / "plans"
            service = ChangeGovernanceService(
                ChangePlanRepository(root),
                FakeGateway({"porch": CURRENT}),
                now=Clock(),
            )
            proposed = copy.deepcopy(CURRENT)
            proposed["description"] = "Beta 26 scale review"
            created = await service.create_plan(
                title="Beta 26 scale review",
                description="Fixed active work across scale fixtures",
                operation="update_automation",
                automation_id="porch",
                proposed_config=proposed,
            )
            service.approve(created["plan_id"], created["plan_hash"])
            return json.loads(
                (root / f"{created['plan_id']}.json").read_text(
                    encoding="utf-8"
                )
            )
        finally:
            temporary.cleanup()

    async def test_hot_paths_are_flat_through_ten_thousand_terminal_plans(self):
        evidence: list[dict[str, float | int]] = []
        active_payload = await self._active_review_payload()
        active_id = str(active_payload["plan_id"])
        for count in SCALE_COUNTS:
            temporary, service, startup_seconds = self._service_with_history(
                count, active_payload
            )
            try:
                repository = service.repository

                before = repository.navigation_metrics()
                old_started = monotonic()
                plans, failures = (
                    service._resolved_plans_with_projection_failures()
                )
                old_seconds = monotonic() - old_started
                after = repository.navigation_metrics()
                self.assertEqual(len(plans), count + 1)
                self.assertEqual(failures, [])
                self.assertEqual(
                    _metric_delta(before, after, "records_deserialized"),
                    count + 1,
                )
                self.assertEqual(
                    _metric_delta(
                        before,
                        after,
                        "terminal_records_deserialized",
                    ),
                    count,
                )
                service._rebuild_projection_failure_index(
                    invalidate_health=False
                )

                before = repository.navigation_metrics()
                list_started = monotonic()
                listed = service.list_plans(limit=1)
                list_seconds = monotonic() - list_started
                after = repository.navigation_metrics()
                self.assertEqual(listed["count"], 1)
                self.assertEqual(
                    _metric_delta(before, after, "records_deserialized"),
                    1,
                )
                self.assertEqual(
                    _metric_delta(
                        before,
                        after,
                        "terminal_records_deserialized",
                    ),
                    1,
                )
                self.assertEqual(
                    service._hot_path_metrics["list_change_plans"][
                        "records_enumerated"
                    ],
                    1,
                )

                before = repository.navigation_metrics()
                pending_started = monotonic()
                pending = service.pending_external_reviews()
                pending_seconds = monotonic() - pending_started
                after = repository.navigation_metrics()
                self.assertEqual(
                    [item["plan_id"] for item in pending], [active_id]
                )
                self.assertEqual(
                    _metric_delta(before, after, "records_deserialized"),
                    1,
                )
                self.assertEqual(
                    _metric_delta(
                        before,
                        after,
                        "terminal_records_deserialized",
                    ),
                    0,
                )

                before = repository.navigation_metrics()
                detail_started = monotonic()
                detail = service.pending_external_review(active_id)
                detail_seconds = monotonic() - detail_started
                after = repository.navigation_metrics()
                self.assertEqual(detail["plan_id"], active_id)
                self.assertEqual(
                    _metric_delta(before, after, "records_deserialized"),
                    1,
                )

                before = repository.navigation_metrics()
                recovered_tasks = await service.reconcile_execution_tasks()
                recovered_plans = await service.reconcile_operational_plans()
                after = repository.navigation_metrics()
                self.assertEqual(recovered_tasks["checked"], 0)
                self.assertEqual(recovered_plans["checked"], 0)
                self.assertEqual(
                    _metric_delta(before, after, "records_deserialized"),
                    0,
                )

                before = repository.navigation_metrics()
                health = service.health_summary()
                after = repository.navigation_metrics()
                self.assertEqual(health["total_plans"], count + 1)
                self.assertEqual(
                    _metric_delta(before, after, "records_deserialized"),
                    0,
                )
                hot = health["plan_store_scaling"]["hot_paths"]
                self.assertEqual(
                    hot["pending_external_reviews"][
                        "terminal_plan_records_deserialized"
                    ],
                    0,
                )
                self.assertEqual(
                    hot["operational_plan_recovery"][
                        "recovery_candidates_examined"
                    ],
                    0,
                )

                evidence.append(
                    {
                        "terminal_plans": count,
                        "startup_seconds": round(startup_seconds, 6),
                        "old_full_scan_seconds": round(old_seconds, 6),
                        "bounded_list_seconds": round(list_seconds, 6),
                        "pending_review_seconds": round(
                            pending_seconds, 6
                        ),
                        "approval_detail_seconds": round(
                            detail_seconds, 6
                        ),
                        "old_terminal_touches": count,
                        "bounded_terminal_touches": 1,
                    }
                )
            finally:
                temporary.cleanup()
        print("BETA26_SCALE_EVIDENCE=" + json.dumps(evidence, sort_keys=True))


class Beta26PlanStoreCorrectnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "plans"
        _write_terminal_history(self.root, 130)
        self.clock = Clock()
        self.gateway = FakeGateway({"porch": CURRENT})
        self.repository = ChangePlanRepository(self.root)
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            now=self.clock,
        )
        self.telemetry, self.context = begin_request(
            "beta26-plan-store-scaling"
        )
        self.telemetry.caller_id = "beta26-test-caller"

    async def asyncTearDown(self) -> None:
        end_request(self.context)
        self.temporary.cleanup()

    async def _pending_review(self) -> tuple[dict, dict]:
        proposed = copy.deepcopy(CURRENT)
        proposed["description"] = "Beta 26 pending review"
        created = await self.service.create_plan(
            title="Beta 26 plan",
            description="Exercise active approval navigation",
            operation="update_automation",
            automation_id="porch",
            proposed_config=proposed,
        )
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        return created, pending

    async def test_approval_inventory_detail_rebuild_and_restart_are_bounded(self):
        created, _pending = await self._pending_review()
        plan_id = created["plan_id"]

        before = self.repository.navigation_metrics()
        reviews = self.service.pending_external_reviews()
        after = self.repository.navigation_metrics()
        self.assertEqual([item["plan_id"] for item in reviews], [plan_id])
        self.assertEqual(
            _metric_delta(before, after, "records_deserialized"), 1
        )
        self.assertEqual(
            _metric_delta(
                before, after, "terminal_records_deserialized"
            ),
            0,
        )

        before = self.repository.navigation_metrics()
        detail = self.service.pending_external_review(plan_id)
        after = self.repository.navigation_metrics()
        self.assertIsNotNone(detail)
        self.assertEqual(detail["plan_id"], plan_id)
        self.assertEqual(
            _metric_delta(before, after, "records_deserialized"), 1
        )
        self.assertIsNone(self.service.pending_external_review("f" * 32))

        rebuilds = self.repository.index_rebuild_count
        self.repository._approval_candidate_ids.clear()
        rebuilt_reviews = self.service.pending_external_reviews()
        self.assertEqual(
            [item["plan_id"] for item in rebuilt_reviews], [plan_id]
        )
        self.assertGreaterEqual(
            self.repository.index_rebuild_count, rebuilds + 1
        )
        self.assertGreaterEqual(self.repository.index_invalidation_count, 1)

        expected_candidates = self.repository.approval_candidate_ids()
        recovered = ChangeGovernanceService(
            ChangePlanRepository(self.root),
            self.gateway,
            now=self.clock,
        )
        self.assertEqual(
            recovered.repository.approval_candidate_ids(),
            expected_candidates,
        )
        self.assertEqual(
            [
                item["plan_id"]
                for item in recovered.pending_external_reviews()
            ],
            [plan_id],
        )

    async def test_expiry_updates_every_derived_view(self):
        created, _pending = await self._pending_review()
        plan_id = created["plan_id"]
        self.assertEqual(
            self.repository.navigation_metrics()[
                "approval_candidate_count"
            ],
            1,
        )

        self.clock.advance(hours=2)
        self.assertEqual(self.service.pending_external_reviews(), [])
        metrics = self.repository.navigation_metrics()
        self.assertEqual(metrics["approval_candidate_count"], 0)
        self.assertEqual(metrics["active_record_count"], 0)

        before = self.repository.navigation_metrics()
        self.assertEqual(self.service.pending_external_reviews(), [])
        after = self.repository.navigation_metrics()
        self.assertEqual(
            _metric_delta(before, after, "records_deserialized"), 0
        )
        persisted = self.repository.get(plan_id)
        self.assertEqual(persisted.status.value, "expired")

    async def test_derived_navigation_never_grants_authority(self):
        created, pending = await self._pending_review()
        plan_id = created["plan_id"]
        terminal_id = f"{1:032x}"
        self.repository._approval_candidate_ids.add(terminal_id)
        self.repository._expected_approval_candidate_count += 1
        self.repository._refresh_expected_active_signatures()
        reviews = self.service.pending_external_reviews()
        self.assertEqual([item["plan_id"] for item in reviews], [plan_id])

        review, csrf = await self.service.issue_external_csrf(
            plan_id, pending["challenge_id"]
        )
        self.assertEqual(review["plan_id"], plan_id)
        result = await self.service.decide_external_approval(
            plan_id=plan_id,
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind="apply",
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:beta26",
        )
        self.assertEqual(result["status"], "approved")

    async def test_explicit_deep_audit_detects_terminal_tampering(self):
        plan_id = f"{130:032x}"
        path = self.root / f"{plan_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "not-a-plan-status"
        path.write_text(json.dumps(payload), encoding="utf-8")

        before = self.repository.navigation_metrics()
        self.service.list_plans(limit=1)
        after = self.repository.navigation_metrics()
        self.assertLessEqual(
            _metric_delta(before, after, "records_deserialized"), 1
        )

        audit = self.service.deep_audit_plan_store()
        self.assertEqual(audit["plan_store"]["records_enumerated"], 130)
        self.assertEqual(audit["plan_store"]["indexed_records"], 129)
        self.assertEqual(self.repository.corruption_count, 1)
        self.assertFalse(path.exists())
        self.assertTrue(list((self.root / "quarantine").glob("*.corrupt")))
