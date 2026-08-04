"""Immutable artifact, stale-preflight, and reread verification tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from f3_dashboard.artifact_store import (  # noqa: E402
    DashboardArtifactStore,
    artifact_resulting_configuration,
)
from f3_dashboard.errors import ArtifactStorageError  # noqa: E402
from f3_dashboard.json_codec import engineering_sha256, upstream_config_hash  # noqa: E402
from f3_dashboard.models import VerificationOutcome  # noqa: E402
from f3_dashboard.observability import DashboardWriteObservability  # noqa: E402
from f3_dashboard.serialization import proposal_hash  # noqa: E402
from f3_dashboard.verification import (  # noqa: E402
    assess_dashboard_preflight,
    verify_dashboard_observation,
)
from f3_dashboard_support import make_preread, make_proposal  # noqa: E402


class DashboardArtifactStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_read_result_hash_and_immutability(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DashboardArtifactStore(directory)
            proposal, _ = await make_proposal(
                artifact_store=store, plan_id="artifact00000001"
            )
            record = store.get(proposal.plan_id)
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.proposal_sha256, proposal.proposal_sha256)
            self.assertEqual(
                artifact_resulting_configuration(record),
                proposal.compilation.resulting_configuration,
            )
            with self.assertRaises(ArtifactStorageError):
                store.create(proposal)

    async def test_payload_corruption_and_self_consistent_rehash_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DashboardArtifactStore(directory)
            proposal, _ = await make_proposal(
                artifact_store=store, plan_id="artifact00000002"
            )
            path = Path(directory) / f"{proposal.plan_id}.json"
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["title"] = "tampered"
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(ArtifactStorageError, "payload hash"):
                store.get(proposal.plan_id)

            envelope["payload_sha256"] = engineering_sha256(envelope["payload"])
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(ArtifactStorageError, "proposal binding"):
                store.get(proposal.plan_id)

    async def test_unknown_schema_wrong_plan_binding_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DashboardArtifactStore(directory)
            proposal, _ = await make_proposal(
                artifact_store=store, plan_id="artifact00000003"
            )
            path = Path(directory) / f"{proposal.plan_id}.json"
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["schema"] = "future-schema"
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(ArtifactStorageError, "Unknown"):
                store.get(proposal.plan_id)

            target = Path(directory) / "outside.json"
            target.write_text("{}", encoding="utf-8")
            link_id = "artifact00000004"
            os.symlink(target, Path(directory) / f"{link_id}.json")
            with self.assertRaises(ArtifactStorageError):
                store.get(link_id)

    async def test_artifact_serialization_is_stable_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DashboardArtifactStore(directory)
            proposal, _ = await make_proposal(
                artifact_store=store, plan_id="artifact00000005"
            )
            record = store.get(proposal.plan_id)
            assert record is not None
            self.assertEqual(record.payload["proposal_sha256"], proposal_hash(proposal))
            round_trip = json.loads(
                json.dumps(record.payload, sort_keys=True, separators=(",", ":"))
            )
            self.assertEqual(round_trip, record.payload)

    async def test_expired_artifacts_are_removed_only_after_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DashboardArtifactStore(directory, retention_days=30)
            proposal, _ = await make_proposal(
                artifact_store=store, plan_id="artifact00000006"
            )
            self.assertEqual(
                store.prune_expired(
                    now=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
                ),
                0,
            )
            self.assertIsNotNone(store.get(proposal.plan_id))
            self.assertEqual(
                store.prune_expired(
                    now=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
                ),
                1,
            )
            self.assertIsNone(store.get(proposal.plan_id))


class DashboardPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.proposal, _ = await make_proposal(plan_id="preflight00000001")
        self.current = make_preread()
        self.now = datetime(2026, 8, 4, 12, 10, tzinfo=timezone.utc)

    def preflight(self, current=None, **overrides):
        values = {
            "now": self.now,
            "approval_bundle_validated_by_caller_layer": True,
            "acquired_lock_keys": self.proposal.lock_keys,
            "fencing_validated_by_f3a": True,
        }
        values.update(overrides)
        return assess_dashboard_preflight(
            self.proposal, current or self.current, **values
        )

    async def test_even_fresh_approved_fenced_complete_lock_preflight_is_atomicity_blocked(self):
        result = self.preflight()
        self.assertFalse(result.eligible)
        self.assertFalse(result.stale)
        self.assertTrue(result.approval_bundle_validated)
        self.assertTrue(result.complete_lock_keys_present)
        self.assertTrue(result.fencing_validated)
        self.assertFalse(result.atomicity_validated)
        self.assertIn("atomicity_gate_rejected", result.diagnostic_codes)
        self.assertIn("proposal_is_planning_only", result.diagnostic_codes)

    async def test_external_change_before_intent_is_stale_and_cannot_dispatch(self):
        changed = make_preread({"title": "external writer"})
        result = self.preflight(changed)
        self.assertTrue(result.stale)
        self.assertFalse(result.eligible)
        self.assertIn("stale_dashboard_state", result.diagnostic_codes)

    async def test_expiration_approval_lock_and_fencing_fail_independently(self):
        cases = (
            {"now": datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)},
            {"approval_bundle_validated_by_caller_layer": False},
            {"acquired_lock_keys": ("dashboard:synthetic-dashboard",)},
            {"fencing_validated_by_f3a": False},
        )
        for case in cases:
            with self.subTest(case=case):
                result = self.preflight(**case)
                self.assertFalse(result.eligible)

    async def test_same_dashboard_lock_conflicts_by_identity_and_separate_dashboard_is_distinct(self):
        same, _ = await make_proposal(plan_id="preflight00000002")
        other_preread = make_preread(url_path="other-dashboard")
        other, _ = await make_proposal(
            preread=other_preread,
            url_path="other-dashboard",
            plan_id="preflight00000003",
        )
        self.assertIn("dashboard:synthetic-dashboard", self.proposal.lock_keys)
        self.assertIn("dashboard:synthetic-dashboard", same.lock_keys)
        self.assertIn("dashboard:other-dashboard", other.lock_keys)
        self.assertNotIn("dashboard:synthetic-dashboard", other.lock_keys)
        self.assertIn("home_assistant:core", other.lock_keys)


class DashboardVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.proposal, _ = await make_proposal(plan_id="verify00000000001")

    def result_preread(self):
        result = self.proposal.compilation.resulting_configuration
        return make_preread(
            result,
            config_hash=upstream_config_hash(result),
        )

    async def test_exact_full_reread_is_succeeded_verified(self):
        outcome = verify_dashboard_observation(self.proposal, self.result_preread())
        self.assertEqual(outcome.outcome, VerificationOutcome.SUCCEEDED_VERIFIED)
        self.assertTrue(outcome.verified)
        self.assertTrue(outcome.untouched_fields_preserved)
        self.assertEqual(outcome.mismatch_paths, ())

    async def test_mismatch_reports_paths_without_values(self):
        result = self.proposal.compilation.resulting_configuration
        changed = json.loads(json.dumps(result))
        changed["unknown_root_extension"]["nested"]["preserve"] = "external value"
        outcome = verify_dashboard_observation(self.proposal, make_preread(changed))
        self.assertEqual(outcome.outcome, VerificationOutcome.VERIFICATION_MISMATCH)
        self.assertFalse(outcome.untouched_fields_preserved)
        self.assertIn(
            "/unknown_root_extension/nested/preserve", outcome.mismatch_paths
        )
        self.assertNotIn("external value", json.dumps(outcome.diagnostic_codes))

    async def test_missing_or_partial_reread_requires_manual_review(self):
        missing = verify_dashboard_observation(self.proposal, None)
        partial = verify_dashboard_observation(
            self.proposal, replace(self.result_preread(), completeness="partial")
        )
        for outcome in (missing, partial):
            self.assertEqual(outcome.outcome, VerificationOutcome.MANUAL_REVIEW_REQUIRED)
            self.assertIsNone(outcome.verified)

    async def test_authoritative_no_write_evidence_is_the_only_confirmed_failure(self):
        outcome = verify_dashboard_observation(
            self.proposal, None, authoritative_no_write=True
        )
        self.assertEqual(outcome.outcome, VerificationOutcome.FAILED_CONFIRMED_NO_WRITE)
        self.assertFalse(outcome.verified)

    async def test_lost_response_and_process_reconstruction_use_reread_only(self):
        metrics = DashboardWriteObservability()
        recovered = verify_dashboard_observation(
            self.proposal, self.result_preread(), observability=metrics
        )
        snapshot = metrics.snapshot()
        self.assertEqual(recovered.outcome, VerificationOutcome.SUCCEEDED_VERIFIED)
        self.assertEqual(snapshot["counts"]["verification.rereads"], 1)
        self.assertEqual(snapshot["counts"]["provider.dispatch_attempts"], 0)
        self.assertEqual(snapshot["counts"]["provider.responses_received"], 0)
        self.assertEqual(snapshot["counts"]["provider.fallback_count"], 0)

    async def test_ambiguous_recovery_never_redispatches(self):
        metrics = DashboardWriteObservability()
        outcome = verify_dashboard_observation(
            self.proposal, None, observability=metrics
        )
        snapshot = metrics.snapshot()
        self.assertEqual(outcome.outcome, VerificationOutcome.MANUAL_REVIEW_REQUIRED)
        self.assertEqual(snapshot["counts"]["verification.rereads"], 1)
        self.assertEqual(snapshot["counts"]["provider.dispatch_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
