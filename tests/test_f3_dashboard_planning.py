"""Exact raw-evidence and zero-dispatch dashboard planning tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

from ha_mcp_engineering.f3_dashboard.artifact_store import DashboardArtifactStore  # noqa: E402
from ha_mcp_engineering.f3_dashboard.errors import PlanningError, RawEvidenceError  # noqa: E402
from ha_mcp_engineering.f3_dashboard.models import AtomicityStatus  # noqa: E402
from ha_mcp_engineering.f3_dashboard.observability import DashboardWriteObservability  # noqa: E402
from ha_mcp_engineering.f3_dashboard.planning import (  # noqa: E402
    create_dashboard_update_plan,
    create_dashboard_update_plan_projection,
)
from ha_mcp_engineering.f3_dashboard.provider import EXACT_CONTRACTS  # noqa: E402
from ha_mcp_engineering.f3_dashboard.raw_evidence import build_raw_dashboard_evidence  # noqa: E402
from f3_dashboard_support import FakeExactReader, load_dashboard, make_preread, make_proposal  # noqa: E402


class RawDashboardEvidenceTests(unittest.TestCase):
    def test_exact_storage_prereads_are_accepted_for_reviewed_releases(self):
        for version in ("7.14.2", "8.0.0", "8.1.1", "8.2.0", "8.4.1"):
            with self.subTest(version=version):
                source = make_preread(version=version)
                evidence = build_raw_dashboard_evidence(
                    source, requested_url_path="synthetic-dashboard"
                )
                self.assertTrue(evidence.storage_mode_confirmed)
                self.assertEqual(evidence.upstream_version, version)
                self.assertRegex(evidence.engineering_config_sha256, r"^[0-9a-f]{64}$")
                source.configuration["title"] = "mutated after validation"
                self.assertNotEqual(evidence.configuration["title"], source.configuration["title"])

    def test_explicit_lovelace_inventory_identity_is_accepted_but_default_alias_is_not(self):
        evidence = build_raw_dashboard_evidence(
            make_preread(url_path="lovelace"), requested_url_path="lovelace"
        )
        self.assertEqual(evidence.canonical_url_path, "lovelace")
        with self.assertRaises(RawEvidenceError):
            build_raw_dashboard_evidence(
                make_preread(url_path="default"), requested_url_path="default"
            )

    def test_yaml_missing_partial_sanitized_and_truncated_prereads_are_rejected(self):
        base = make_preread()
        cases = (
            replace(base, inventory=(replace(base.inventory[0], mode="yaml"),)),
            replace(base, inventory=()),
            replace(base, completeness="partial"),
            replace(base, configuration_returned=False),
            replace(base, sanitized=True),
            replace(base, truncated=True),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(RawEvidenceError):
                build_raw_dashboard_evidence(
                    candidate, requested_url_path="synthetic-dashboard"
                )

    def test_stale_or_malformed_hash_and_malformed_configuration_are_rejected(self):
        base = make_preread()
        for config_hash in ("0" * 16, "not-a-hash", "A" * 16, ""):
            with self.subTest(config_hash=config_hash), self.assertRaises(RawEvidenceError):
                build_raw_dashboard_evidence(
                    replace(base, config_hash=config_hash),
                    requested_url_path="synthetic-dashboard",
                )
        malformed = replace(base, configuration={"invalid": object()})
        with self.assertRaises(RawEvidenceError):
            build_raw_dashboard_evidence(
                malformed, requested_url_path="synthetic-dashboard"
            )

    def test_default_alias_target_mismatch_unknown_release_and_wrong_protocol_fail_closed(self):
        base = make_preread()
        candidates = (
            (replace(base, canonical_url_path="default"), "default"),
            (base, "other-dashboard"),
            (replace(base, upstream_version="8.0.1"), "synthetic-dashboard"),
            (replace(base, protocol_version="2026-01-01"), "synthetic-dashboard"),
        )
        for candidate, requested in candidates:
            with self.subTest(requested=requested), self.assertRaises(RawEvidenceError):
                build_raw_dashboard_evidence(candidate, requested_url_path=requested)

    def test_oversized_raw_configuration_is_rejected(self):
        config = {"title": "x" * 40_100}
        with self.assertRaises(RawEvidenceError):
            build_raw_dashboard_evidence(
                make_preread(config), requested_url_path="synthetic-dashboard"
            )


class DashboardPlanningTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_is_immutable_exactly_bound_and_zero_dispatch(self):
        proposal, reader = await make_proposal()
        self.assertEqual(reader.preread_count, 1)
        self.assertEqual(reader.mutation_count, 0)
        self.assertTrue(proposal.executable)
        self.assertTrue(proposal.provider_projection.executable)
        self.assertEqual(
            proposal.atomicity.status,
            AtomicityStatus.OPERATOR_ACCEPTED_NON_ATOMIC,
        )
        self.assertEqual(proposal.required_approval, "external_administrator")
        self.assertFalse(proposal.rollback_available)
        self.assertEqual(
            proposal.lock_keys,
            (
                "addon:ha_mcp",
                "dashboard:synthetic-dashboard",
                "home_assistant:core",
            ),
        )
        self.assertEqual(
            proposal.compilation.preread_sha256,
            proposal.raw_evidence.engineering_config_sha256,
        )
        with self.assertRaises(FrozenInstanceError):
            proposal.executable = True  # type: ignore[misc]

    async def test_public_projection_excludes_raw_config_and_generated_python(self):
        reader = FakeExactReader(make_preread())
        projection = await create_dashboard_update_plan_projection(
            reader=reader,
            url_path="synthetic-dashboard",
            operations=[
                {
                    "operation_id": "rename-title",
                    "operation": "replace",
                    "path": "/title",
                    "value": "Public projection test",
                }
            ],
            title="Projection",
            description="No raw data.",
            expiration_minutes=30,
            requested_by="test.operator",
            provider_evidence=EXACT_CONTRACTS["8.1.1"],
            authoritative_provider_slug="ha_mcp",
            now=datetime(2026, 8, 4, 12, 5, tzinfo=timezone.utc),
            plan_id="plan000000000002",
        )
        encoded = json.dumps(projection, sort_keys=True)
        self.assertNotIn("resulting_configuration", encoded)
        self.assertNotIn("generated_transform\"", encoded)
        self.assertNotIn("Synthetic inert text", encoded)
        self.assertFalse(projection["data_handling"]["raw_configuration_exposed"])
        self.assertFalse(projection["data_handling"]["instructions_authoritative"])
        self.assertTrue(projection["risk"]["findings"])
        for finding in projection["risk"]["findings"]:
            self.assertRegex(
                finding["semantic_binding_sha256"], r"^[0-9a-f]{64}$"
            )
            self.assertNotIn("target", finding)
            self.assertNotIn("service_data", finding)
            self.assertNotIn("payload", finding)
            self.assertNotIn("path", finding)
            self.assertNotIn("action", finding)
            self.assertNotIn("service", finding)
            self.assertRegex(finding["path_sha256"], r"^[0-9a-f]{64}$")

        proposal, _ = await make_proposal(plan_id="plan000000000004")
        self.assertNotIn(
            "generated_transform",
            json.dumps(proposal.compilation, default=str, sort_keys=True),
        )

    async def test_artifact_is_created_without_execution_task_or_approval_challenge(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DashboardArtifactStore(directory)
            proposal, reader = await make_proposal(
                artifact_store=store, plan_id="plan000000000003"
            )
            self.assertIsNotNone(store.get(proposal.plan_id))
            self.assertEqual(reader.mutation_count, 0)
            self.assertFalse(hasattr(proposal, "execution_task"))
            self.assertFalse(hasattr(proposal, "approval_challenge"))

    async def test_planning_rejections_leave_mutation_count_zero(self):
        cases = (
            make_preread(mode="yaml"),
            make_preread(completeness="partial"),
            make_preread(config_hash="0" * 16),
        )
        for index, preread in enumerate(cases):
            reader = FakeExactReader(preread)
            with self.subTest(index=index), self.assertRaises(RawEvidenceError):
                await create_dashboard_update_plan(
                    reader=reader,
                    url_path="synthetic-dashboard",
                    operations=[
                        {
                            "operation_id": "rename",
                            "operation": "replace",
                            "path": "/title",
                            "value": "Rejected",
                        }
                    ],
                    title="Rejected",
                    description="Synthetic",
                    expiration_minutes=30,
                    requested_by="test.operator",
                    provider_evidence=EXACT_CONTRACTS["8.1.1"],
                    authoritative_provider_slug="ha_mcp",
                    plan_id=f"plan00000000{index + 10:04d}",
                )
            self.assertEqual(reader.mutation_count, 0)

    async def test_plan_input_bounds_fail_before_preread(self):
        reader = FakeExactReader(make_preread())
        with self.assertRaises(PlanningError):
            await create_dashboard_update_plan(
                reader=reader,
                url_path="synthetic-dashboard",
                operations=[],
                title="",
                description="Synthetic",
                expiration_minutes=30,
                requested_by="test.operator",
                provider_evidence=EXACT_CONTRACTS["8.1.1"],
                authoritative_provider_slug="ha_mcp",
            )
        self.assertEqual(reader.preread_count, 0)
        self.assertEqual(reader.mutation_count, 0)

    async def test_observability_is_bounded_and_contains_no_target_or_content(self):
        metrics = DashboardWriteObservability()
        reader = FakeExactReader(make_preread())
        proposal = await create_dashboard_update_plan(
            reader=reader,
            url_path="synthetic-dashboard",
            operations=[
                {
                    "operation_id": "rename",
                    "operation": "replace",
                    "path": "/title",
                    "value": "Observed",
                }
            ],
            title="Observed",
            description="Synthetic",
            expiration_minutes=30,
            requested_by="test.operator",
            provider_evidence=EXACT_CONTRACTS["8.1.1"],
            authoritative_provider_slug="ha_mcp",
            observability=metrics,
            now=datetime(2026, 8, 4, 12, 5, tzinfo=timezone.utc),
            plan_id="plan000000000004",
        )
        snapshot = metrics.snapshot()
        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertEqual(snapshot["counts"]["planning.plans_created"], 1)
        self.assertEqual(snapshot["counts"]["provider.dispatch_attempts"], 0)
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertNotIn(proposal.target_id, encoded)
        self.assertNotIn(load_dashboard()["title"], encoded)
        self.assertFalse(snapshot["raw_dashboard_content_exposed"])


if __name__ == "__main__":
    unittest.main()
