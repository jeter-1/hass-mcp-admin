"""Beta 52 target-scoped label and finite-entity-set regression tests."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.index import DependencyIndex
from ha_mcp_engineering.dependency.provider import (
    DirectHaDependencyProvider,
    MAX_LABEL_MEMBERSHIP,
    MAX_LABEL_REGISTRY_ENTRIES,
    _build_label_membership_evidence,
)
from ha_mcp_engineering.f3.operational_locks import (
    OperationalLockSetCalculator,
)
from ha_mcp_engineering.f3_configuration.locks import (
    unconstrained_helper_dependency_lock_key,
)
from ha_mcp_engineering.governance.helper_dependency import (
    HelperDependencyRiskService,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.storage import ChangePlanRepository

from tests.test_beta50_helper_production_target_scope import (
    CapturedBeta50ReplayRest,
    CapturedBeta50ReplayWebSocket,
)
from tests.test_beta37_exact_helper_state import (
    Clock,
    FakeHelperStateGateway,
    UnusedLegacyGateway,
)


REPLAY = (
    ROOT
    / "tests"
    / "fixtures"
    / "dependency"
    / "hamcp089_beta51_label_target_scope_replay_v1.json"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class Beta51LabelScopeReplayWebSocket(CapturedBeta50ReplayWebSocket):
    """Replay the captured selector-local registry boundary."""

    async def command(self, payload: dict):
        result = await super().command(payload)
        if payload == {"type": "config/entity_registry/list"}:
            result = list(result)
            result.append(
                copy.deepcopy(
                    self.fixture["registry_boundary_reproducer"]
                )
            )
        return result


class Beta52LabelMembershipEvidenceTests(unittest.TestCase):
    def test_unrelated_malformed_identity_does_not_poison_selector(self):
        evidence = _build_label_membership_evidence(
            ["reviewed_label"],
            label_registry=[
                {"label_id": "reviewed_label", "name": "Reviewed Label"}
            ],
            entity_registry=[
                {
                    "entity_id": "sensor.reviewed",
                    "labels": ["reviewed_label"],
                },
                {
                    "entity_id": "not-an-entity-id",
                    "labels": [],
                },
            ],
        )
        memberships, fingerprints, truncated, resolutions, complete = evidence
        self.assertEqual(
            ("sensor.reviewed",), memberships["reviewed_label"]
        )
        self.assertRegex(fingerprints["reviewed_label"], r"\A[0-9a-f]{64}\Z")
        self.assertEqual((), truncated)
        self.assertEqual(
            ("label_id", "reviewed_label"),
            resolutions["reviewed_label"],
        )
        self.assertEqual(
            {"reviewed_label": True}, evidence.selector_complete
        )
        self.assertTrue(complete)

    def test_malformed_potential_member_remains_incomplete(self):
        evidence = _build_label_membership_evidence(
            ["reviewed_label"],
            label_registry=[
                {"label_id": "reviewed_label", "name": "Reviewed Label"}
            ],
            entity_registry=[
                {
                    "entity_id": "not-an-entity-id",
                    "labels": ["reviewed_label"],
                }
            ],
        )
        memberships, _fingerprints, _truncated, _resolutions, complete = evidence
        self.assertEqual((), memberships["reviewed_label"])
        self.assertEqual(
            {"reviewed_label": False}, evidence.selector_complete
        )
        self.assertFalse(complete)

    def test_malformed_membership_shape_remains_incomplete(self):
        evidence = _build_label_membership_evidence(
            ["reviewed_label"],
            label_registry=[
                {"label_id": "reviewed_label", "name": "Reviewed Label"}
            ],
            entity_registry=[
                {
                    "entity_id": "sensor.reviewed",
                    "labels": "reviewed_label",
                }
            ],
        )
        self.assertEqual(
            {"reviewed_label": False}, evidence.selector_complete
        )
        self.assertFalse(evidence.complete)

    def test_id_first_lookup_is_selector_local(self):
        evidence = _build_label_membership_evidence(
            ["safety_controls"],
            label_registry=[
                {"label_id": "safety_controls", "name": "Other"},
                {"label_id": "different", "name": "Safety Controls"},
                {"label_id": None, "name": None},
            ],
            entity_registry=[
                {
                    "entity_id": "input_boolean.target",
                    "labels": ["safety_controls"],
                },
                {
                    "entity_id": "input_boolean.other",
                    "labels": ["different"],
                },
            ],
        )
        self.assertEqual(
            ("input_boolean.target",), evidence[0]["safety_controls"]
        )
        self.assertEqual(
            ("label_id", "safety_controls"),
            evidence[3]["safety_controls"],
        )
        self.assertTrue(evidence.selector_complete["safety_controls"])

    def test_unavailable_or_excessive_inventory_never_proves_completion(self):
        cases = (
            {
                "entity_registry": [],
                "label_registry": [
                    {"label_id": "reviewed_label", "name": "Reviewed"}
                ],
                "entity_inventory_available": False,
            },
            {
                "entity_registry": [],
                "label_registry": [
                    {"label_id": "reviewed_label", "name": "Reviewed"}
                ],
                "label_inventory_available": False,
            },
            {
                "entity_registry": [],
                "label_registry": [
                    {"label_id": "reviewed_label", "name": "Reviewed"}
                ]
                * (MAX_LABEL_REGISTRY_ENTRIES + 1),
            },
        )
        for kwargs in cases:
            with self.subTest(kwargs=tuple(sorted(kwargs))):
                evidence = _build_label_membership_evidence(
                    ["reviewed_label"],
                    **kwargs,
                )
                self.assertFalse(
                    evidence.selector_complete["reviewed_label"]
                )
                self.assertFalse(evidence.complete)

    def test_membership_bound_is_deterministic_and_fail_closed(self):
        members = [
            {
                "entity_id": f"sensor.member_{index:03d}",
                "labels": ["reviewed_label"],
            }
            for index in range(MAX_LABEL_MEMBERSHIP + 1)
        ]
        forward = _build_label_membership_evidence(
            ["reviewed_label"],
            label_registry=[
                {"label_id": "reviewed_label", "name": "Reviewed"}
            ],
            entity_registry=members,
        )
        reverse = _build_label_membership_evidence(
            ["reviewed_label"],
            label_registry=[
                {"label_id": "reviewed_label", "name": "Reviewed"}
            ],
            entity_registry=list(reversed(members)),
        )
        self.assertEqual(
            MAX_LABEL_MEMBERSHIP,
            len(forward.memberships["reviewed_label"]),
        )
        self.assertEqual(forward.memberships, reverse.memberships)
        self.assertEqual(forward.fingerprints, reverse.fingerprints)
        self.assertEqual(("reviewed_label",), forward.truncated)
        self.assertFalse(forward.selector_complete["reviewed_label"])
        self.assertFalse(forward.complete)

    def test_duplicate_membership_labels_are_semantically_deduplicated(self):
        single = _build_label_membership_evidence(
            ["reviewed_label"],
            label_registry=[
                {"label_id": "reviewed_label", "name": "Reviewed"}
            ],
            entity_registry=[
                {
                    "entity_id": "sensor.member",
                    "labels": ["reviewed_label"],
                }
            ],
        )
        duplicate = _build_label_membership_evidence(
            ["reviewed_label"],
            label_registry=[
                {"label_id": "reviewed_label", "name": "Reviewed"}
            ],
            entity_registry=[
                {
                    "entity_id": "sensor.member",
                    "labels": ["reviewed_label", "reviewed_label"],
                }
            ],
        )
        self.assertTrue(single.complete)
        self.assertTrue(duplicate.complete)
        self.assertEqual(single.memberships, duplicate.memberships)
        self.assertEqual(single.fingerprints, duplicate.fingerprints)

    def test_conflicting_relevant_identity_is_monotonically_incomplete(self):
        records = [
            {
                "entity_id": "sensor.member",
                "labels": ["reviewed_label"],
            },
            {"entity_id": "sensor.member", "labels": []},
        ]
        observed = []
        for entity_registry in (records, list(reversed(records))):
            evidence = _build_label_membership_evidence(
                ["reviewed_label"],
                label_registry=[
                    {"label_id": "reviewed_label", "name": "Reviewed"}
                ],
                entity_registry=entity_registry,
            )
            observed.append(evidence)
            self.assertFalse(
                evidence.selector_complete["reviewed_label"]
            )
            self.assertFalse(evidence.complete)
        self.assertEqual(observed[0].memberships, observed[1].memberships)
        self.assertEqual(observed[0].fingerprints, observed[1].fingerprints)


class Beta52CapturedProductionReplayTests(
    unittest.IsolatedAsyncioTestCase
):
    async def asyncSetUp(self) -> None:
        self.fixture = json.loads(REPLAY.read_text(encoding="utf-8"))
        self.rest = CapturedBeta50ReplayRest(self.fixture)
        self.websocket = Beta51LabelScopeReplayWebSocket(
            self.fixture, self.rest.ids
        )
        self.index = DependencyIndex(
            DirectHaDependencyProvider(
                self.rest,
                self.websocket,
                concurrency=4,
            )
        )
        self.snapshot, rebuilt, _lookup_ms = await self.index.get(
            refresh=True
        )
        self.assertTrue(rebuilt)
        self.target = self.fixture["target_entity_id"]

    @staticmethod
    def _lock_keys(target: str, binding: dict) -> set[str]:
        operation = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_boolean_state",
            target=SimpleNamespace(target_id=target),
            authoritative_provider_slug="direct_home_assistant_state",
            baseline={"dependency_risk": binding},
        )
        return {
            item.key
            for item in OperationalLockSetCalculator().calculate(operation)
        }

    @staticmethod
    def _traverse(
        governance: ChangeGovernanceService,
        plan_id: str,
        section: str,
    ) -> tuple[list[dict], str]:
        items: list[dict] = []
        fingerprint = ""
        cursor = ""
        while True:
            detail = governance.get_plan_observability(
                plan_id,
                detail_section=section,
                cursor=cursor,
                page_size=1,
            )["detail"]
            items.extend(detail["items"])
            fingerprint = fingerprint or detail["full_set_fingerprint"]
            if detail["full_set_fingerprint"] != fingerprint:
                raise AssertionError("collection fingerprint changed")
            if not detail["has_more"]:
                if detail["next_cursor"] is not None:
                    raise AssertionError("terminal cursor must be null")
                break
            cursor = detail["next_cursor"]
        return items, fingerprint

    def test_fixture_is_sanitized_and_binds_exact_capture_shape(self):
        provenance = self.fixture["provenance"]
        bound = {
            key: value
            for key, value in self.fixture.items()
            if key != "provenance"
        }
        self.assertEqual(
            provenance["sanitized_self_fingerprint"],
            hashlib.sha256(_canonical(bound)).hexdigest(),
        )
        self.assertTrue(provenance["sanitized_before_hashing"])
        self.assertFalse(
            provenance["current_configuration_snapshot_equivalence_proven"]
        )
        captured = self.fixture["captured_beta51_projection"]
        self.assertEqual(31, len(captured["obligations"]))
        self.assertEqual(31, captured["target_capable_opaque_obligation_count"])
        self.assertEqual(2, len(captured["downstream_profiles"]))
        self.assertEqual(
            {"source_01": 22, "source_02": 9},
            captured["source_obligation_counts"],
        )
        self.assertEqual(
            [16, 11, 5],
            [
                len(item["members"])
                for item in self.fixture["membership_evidence"]["labels"]
            ],
        )
        encoded = REPLAY.read_text(encoding="utf-8")
        for forbidden in (
            "fb757887f1c74aa1bd19198d454caebd",
            "input_boolean.mcp_f2_standard_admin_test_flag",
            "ha_tier_1",
            "ha_tier_2",
            "ha_monitor_unavailable",
        ):
            self.assertNotIn(forbidden, encoded)

    async def test_exact_beta51_replay_closes_before_risk_aggregation(self):
        expected = self.fixture["expected_corrected_projection"]
        target_capable = [
            item
            for item in self.snapshot.obligations
            if item.target_selector_scope == "target_capable"
        ]
        self.assertEqual([], target_capable)
        self.assertEqual(
            {key: True for key in self.snapshot.label_memberships},
            self.snapshot.label_membership_complete,
        )

        evidence = await HelperDependencyRiskService(self.index).assess(
            self.target, refresh=False
        )
        binding = evidence["binding"]
        assessment = helper_dependency_risk_assessment(evidence)
        self.assertEqual(
            expected["risk_model"], binding["model"]
        )
        self.assertEqual(
            expected["exact_dependency_count"],
            binding["exact_dependency_obligation_count"],
        )
        self.assertEqual(
            expected["target_capable_opaque_obligation_count"],
            binding["opaque_obligation_count"],
        )
        self.assertEqual(
            expected["downstream_profile_count"],
            len(binding["downstream_profiles"]),
        )
        self.assertEqual(
            expected["coverage_complete"], binding["coverage_complete"]
        )
        self.assertEqual(
            expected["evidence_complete"], binding["evidence_complete"]
        )
        self.assertEqual(
            expected["execution_eligible"], binding["execution_eligible"]
        )
        self.assertEqual(
            expected["physical_consequence"],
            binding["physical_consequence"],
        )
        self.assertEqual(expected["risk_level"], assessment.level.value)
        self.assertTrue(assessment.apply_allowed)
        self.assertFalse(
            binding["dependency_lock_projection"]
            ["conservative_helper_dependency"]
        )
        self.assertEqual(
            [],
            binding["dependency_lock_projection"]
            ["automation_resource_ids"],
        )

    async def test_standard_plan_persists_exact_snapshot_and_proportional_locks(
        self,
    ):
        calls_before = (len(self.rest.calls), len(self.websocket.calls))
        with tempfile.TemporaryDirectory() as temporary:
            helper = FakeHelperStateGateway()
            helper.entity_id = self.target
            governance = ChangeGovernanceService(
                ChangePlanRepository(Path(temporary) / "plans"),
                UnusedLegacyGateway(),
                now=Clock(),
                helper_state_gateway=helper,
                helper_dependency_risk_reader=(
                    HelperDependencyRiskService(self.index).assess
                ),
                plan_observability_cursor_key=b"beta52-label-replay" * 2,
            )
            created = await governance.create_helper_state_plan(
                entity_id=self.target,
                desired_state="on",
            )
            self.assertFalse(created["provider_dispatch_occurred"])
            plan = created["plan"]
            binding = plan["operational"]["baseline"]["dependency_risk"]

            self.assertEqual("helper-dependency-risk-v11", binding["model"])
            self.assertEqual(0, binding["exact_dependency_obligation_count"])
            self.assertEqual(0, binding["opaque_obligation_count"])
            self.assertEqual([], binding["downstream_profiles"])
            self.assertTrue(binding["coverage_complete"])
            self.assertTrue(binding["evidence_complete"])
            self.assertTrue(binding["execution_eligible"])
            self.assertTrue(plan["approval_actionable"])
            self.assertEqual("low", plan["risk"]["level"])
            self.assertEqual(
                "standard_admin",
                plan["policy_decision"]["policy_class"],
            )
            self.assertEqual(
                (
                    self.snapshot.generation,
                    self.snapshot.fingerprint,
                    self.snapshot.source_epoch,
                ),
                (
                    binding["dependency_index_generation"],
                    binding["dependency_index_fingerprint"],
                    binding["dependency_index_source_epoch"],
                ),
            )
            keys = self._lock_keys(self.target, binding)
            self.assertIn(f"helper_dependency:{self.target}", keys)
            self.assertIn(unconstrained_helper_dependency_lock_key(), keys)
            self.assertFalse(
                any(key.startswith("automation:") for key in keys)
            )
            self.assertFalse(
                binding["dependency_lock_projection"]
                ["conservative_helper_dependency"]
            )

            first_obligations = self._traverse(
                governance, plan["plan_id"], "obligation_evidence"
            )
            second_obligations = self._traverse(
                governance, plan["plan_id"], "obligation_evidence"
            )
            first_profiles = self._traverse(
                governance, plan["plan_id"], "downstream_profiles"
            )
            second_profiles = self._traverse(
                governance, plan["plan_id"], "downstream_profiles"
            )
            self.assertEqual(first_obligations, second_obligations)
            self.assertEqual(first_profiles, second_profiles)
            self.assertEqual([], first_obligations[0])
            self.assertEqual([], first_profiles[0])
            self.assertEqual(0, helper.dispatch_count)

        self.assertEqual(
            calls_before,
            (len(self.rest.calls), len(self.websocket.calls)),
        )

    async def test_different_input_boolean_member_is_not_domain_exclusion(self):
        different_helper = next(
            member
            for label in self.fixture["membership_evidence"]["labels"]
            for member in label["members"]
            if member.startswith("input_boolean.")
            and member != self.target
        )
        binding = (
            await HelperDependencyRiskService(self.index).assess(
                different_helper, refresh=False
            )
        )["binding"]
        self.assertGreater(
            binding["exact_dependency_obligation_count"], 0
        )
        self.assertEqual(0, binding["opaque_obligation_count"])
        self.assertGreater(len(binding["downstream_profiles"]), 0)
        keys = self._lock_keys(different_helper, binding)
        self.assertIn(f"helper_dependency:{different_helper}", keys)
        self.assertIn(unconstrained_helper_dependency_lock_key(), keys)
        self.assertTrue(
            any(key.startswith("automation:") for key in keys)
        )

    async def test_relevant_malformed_membership_remains_conservative(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["registry_boundary_reproducer"]["labels"] = [
            fixture["membership_evidence"]["labels"][0]["label_id"]
        ]
        rest = CapturedBeta50ReplayRest(fixture)
        index = DependencyIndex(
            DirectHaDependencyProvider(
                rest,
                Beta51LabelScopeReplayWebSocket(fixture, rest.ids),
            )
        )
        binding = (
            await HelperDependencyRiskService(index).assess(
                self.target, refresh=True
            )
        )["binding"]
        self.assertGreater(binding["opaque_obligation_count"], 0)
        self.assertFalse(binding["coverage_complete"])
        self.assertFalse(binding["evidence_complete"])
        self.assertFalse(binding["execution_eligible"])
        self.assertTrue(
            binding["dependency_lock_projection"]
            ["conservative_helper_dependency"]
        )
        self.assertIn(
            unconstrained_helper_dependency_lock_key(),
            self._lock_keys(self.target, binding),
        )


if __name__ == "__main__":
    unittest.main()
