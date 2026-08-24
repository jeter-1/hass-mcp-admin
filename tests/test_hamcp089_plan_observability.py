"""HAMCP-089.R3 bounded persisted-plan observability regressions."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import GOVERNANCE  # noqa: E402
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.tools.governance import get_change_plan  # noqa: E402
from tests.test_beta37_exact_helper_state import (  # noqa: E402
    Clock,
    FakeDependencyRiskReader,
    FakeHelperStateGateway,
    UnusedLegacyGateway,
)


def _obligation(index: int, entity_id: str, *, padding: int = 0) -> dict:
    automation = f"automation.synthetic_observer_{index:04d}"
    return {
        "source_object_id": automation,
        "source_entity_id": automation,
        "configuration_path": f"action[{index}].target.entity_id",
        "obligation_kind": "entity_selector",
        "ledger_outcome": "exact",
        "target_outcome": "exact_dependency",
        "reason_code": "literal_entity_selector",
        "exact_candidates": [entity_id],
        "possible_entity_domains": ["input_boolean"],
        "literal_selectors": [entity_id],
        "limit_exceeded": False,
        "action_consequence": "none",
        "obligation_fingerprint": stable_hash(
            {"automation": automation, "index": index}
        ),
        "target_projection_fingerprint": stable_hash(
            {"automation": automation, "target": entity_id}
        ),
        "bounded_diagnostic": "x" * padding,
    }


def _profile(index: int, *, padding: int = 0, truncated: bool = False) -> dict:
    automation = f"automation.synthetic_profile_{index:04d}"
    resource_id = automation.removeprefix("automation.")
    return {
        "automation_id": automation,
        "automation_resource_id": resource_id,
        "relationships": ["trigger"],
        "physical_consequence": "none",
        "complete": not truncated,
        "truncated": truncated,
        "action_domains": ["notify"],
        "services": ["notify.notify"],
        "reason_codes": (
            ["action_profile_truncated"]
            if truncated
            else ["proven_benign_action_family"]
        ),
        "effect_projection_model": "automation-action-effect-v2",
        "effect_targets": [],
        "effect_data": [],
        "effect_structure_fingerprint": stable_hash(
            {"automation": automation, "structure": index}
        ),
        "effect_projection_fingerprint": stable_hash(
            {"automation": automation, "effect": index}
        ),
        "effect_projection_clipped": truncated,
        "profile_fingerprint": stable_hash(
            {"automation": automation, "profile": index}
        ),
        "bounded_diagnostic": "y" * padding,
    }


class SyntheticDependencyRiskReader(FakeDependencyRiskReader):
    def __init__(self, entity_id: str) -> None:
        super().__init__(entity_id)
        self.obligations: list[dict] = []
        self.profiles: list[dict] = []
        self.coverage_failure_reason_codes: list[str] = []
        self.read_count = 0

    async def __call__(self, *args, **kwargs):
        self.read_count += 1
        result = await super().__call__(*args, **kwargs)
        material = deepcopy(result["binding"])
        material.pop("evidence_fingerprint", None)
        material["obligation_evidence"] = deepcopy(self.obligations)
        material["retained_obligation_count"] = len(self.obligations)
        material["non_relevant_obligation_count"] = 0
        material["proven_target_exclusion_obligation_count"] = 0
        material["proven_dependency_neutral_obligation_count"] = 0
        material["obligation_overflow_count"] = 0
        material["obligation_overflow_fingerprint"] = None
        material["exact_dependency_obligation_count"] = len(self.obligations)
        material["downstream_profiles"] = deepcopy(self.profiles)
        material["relevant_downstream_object_ids"] = [
            item["automation_id"] for item in self.profiles
        ]
        material["downstream_automation_resource_ids"] = [
            item["automation_resource_id"] for item in self.profiles
        ]
        material["coverage_failure_reason_codes"] = sorted(
            self.coverage_failure_reason_codes
        )
        material["coverage_failure_count"] = len(
            self.coverage_failure_reason_codes
        )
        if self.coverage_failure_reason_codes:
            material["coverage_complete"] = False
            material["evidence_complete"] = False
            material["execution_eligible"] = False
            material["semantic_precision"] = "coverage_failure"
            material["completeness"] = "failed"
            material["truncated"] = True
        binding = {
            **material,
            "evidence_fingerprint": stable_hash(material),
        }
        result["binding"] = binding
        return result


class _Unreachable:
    def __getattr__(self, name):
        raise AssertionError(f"provider method must remain unreachable: {name}")


class PlanObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.helper = FakeHelperStateGateway()
        self.reader = SyntheticDependencyRiskReader(self.helper.entity_id)
        self.service = ChangeGovernanceService(
            ChangePlanRepository(self.root / "plans"),
            UnusedLegacyGateway(),
            now=self.clock,
            helper_state_gateway=self.helper,
            helper_dependency_risk_reader=self.reader,
            plan_observability_cursor_key=b"synthetic-r3-cursor-key" * 2,
        )

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def _create_plan(
        self,
        *,
        obligations: int = 0,
        obligation_padding: int = 0,
        profiles: int = 0,
        profile_padding: int = 0,
        profile_truncated: bool = False,
        expiration_minutes: int = 120,
    ) -> str:
        self.reader.obligations = [
            _obligation(
                index,
                self.helper.entity_id,
                padding=obligation_padding,
            )
            for index in range(obligations)
        ]
        self.reader.profiles = [
            _profile(
                index,
                padding=profile_padding,
                truncated=profile_truncated,
            )
            for index in range(profiles)
        ]
        created = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id,
            desired_state="on",
            expiration_minutes=expiration_minutes,
        )
        self.assertFalse(created["provider_dispatch_occurred"])
        return created["plan"]["plan_id"]

    def _persisted_files(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def _disable_authority_paths(self) -> None:
        async def forbidden_reader(*_args, **_kwargs):
            raise AssertionError("dependency refresh must remain unreachable")

        self.service.helper_dependency_risk_reader = forbidden_reader
        self.service.helper_state_gateway = _Unreachable()
        self.service.gateway = _Unreachable()

    async def test_small_plan_summary_is_canonical_and_detail_is_single_page(self):
        plan_id = await self._create_plan(obligations=2, profiles=1)
        self._disable_authority_paths()

        summary_result = self.service.get_plan_observability(plan_id)
        self.assertEqual(next(iter(summary_result)), "canonical_summary")
        summary = summary_result["canonical_summary"]
        self.assertEqual(summary["plan_id"], plan_id)
        self.assertEqual(summary["dependency_risk_binding_model"], "helper-dependency-risk-v4")
        self.assertEqual(summary["exact_dependency_count"], 2)
        self.assertEqual(summary["retained_obligation_count"], 2)
        self.assertEqual(summary["total_obligation_count"], 2)
        self.assertEqual(summary["downstream_profile_total_count"], 1)
        binding = summary_result["operational"]["baseline"]["dependency_risk"]
        self.assertNotIn("obligation_evidence", binding)
        self.assertNotIn("downstream_profiles", binding)

        page = self.service.get_plan_observability(
            plan_id,
            detail_section="obligation_evidence",
        )
        self.assertEqual(page["detail"]["returned_count"], 2)
        self.assertEqual(page["detail"]["total_count"], 2)
        self.assertFalse(page["detail"]["has_more"])
        self.assertIsNone(page["detail"]["next_cursor"])

    async def test_large_profiles_are_valid_json_below_gateway_limit(self):
        plan_id = await self._create_plan(
            profiles=30,
            profile_padding=3_000,
        )
        legacy = self.service.get_plan(plan_id)
        self.assertGreater(len(json.dumps(legacy, indent=2)), 60_000)
        self._disable_authority_paths()
        prior = GOVERNANCE.service
        GOVERNANCE.service = self.service
        try:
            encoded = await get_change_plan(
                plan_id,
                detail_section="downstream_profiles",
                page_size=100,
            )
        finally:
            GOVERNANCE.service = prior
        parsed = json.loads(encoded)
        self.assertTrue(parsed["success"])
        self.assertLess(len(encoded), 60_000)
        detail = parsed["data"]["detail"]
        self.assertGreater(detail["returned_count"], 0)
        self.assertLess(detail["returned_count"], detail["total_count"])
        self.assertTrue(detail["has_more"])
        self.assertTrue(detail["next_cursor"])
        self.assertEqual(
            parsed["data"]["canonical_summary"]["downstream_profile_total_count"],
            30,
        )

    async def test_response_budget_exact_boundary_and_above_boundary(self):
        plan_id = await self._create_plan(obligations=3, profiles=2)
        self._disable_authority_paths()
        unbounded = self.service.get_plan_observability(
            plan_id, response_limit=200_000
        )
        encoded_chars = self.service._plan_observability_encoded_chars(unbounded)

        exact = self.service.get_plan_observability(
            plan_id,
            response_limit=encoded_chars + 8_000,
        )
        self.assertFalse(
            exact["canonical_summary"]["base_plan_projection_compacted"]
        )
        above = self.service.get_plan_observability(
            plan_id,
            response_limit=encoded_chars + 7_999,
        )
        self.assertTrue(
            above["canonical_summary"]["base_plan_projection_compacted"]
        )
        self.assertLessEqual(
            self.service._plan_observability_encoded_chars(above),
            encoded_chars - 1,
        )

    async def test_every_obligation_is_returned_once_across_stable_pages(self):
        plan_id = await self._create_plan(
            obligations=55,
            obligation_padding=300,
        )
        self._disable_authority_paths()

        async def traverse():
            cursor = ""
            items = []
            fingerprints = set()
            while True:
                page = self.service.get_plan_observability(
                    plan_id,
                    detail_section="obligation_evidence",
                    cursor=cursor,
                    page_size=7,
                )
                detail = page["detail"]
                fingerprints.add(detail["full_set_fingerprint"])
                items.extend(detail["items"])
                cursor = detail["next_cursor"] or ""
                if not detail["has_more"]:
                    return items, fingerprints

        first, first_fingerprints = await traverse()
        second, second_fingerprints = await traverse()
        first_ids = [item["obligation_fingerprint"] for item in first]
        self.assertEqual(len(first_ids), 55)
        self.assertEqual(len(set(first_ids)), 55)
        self.assertEqual(first, second)
        self.assertEqual(len(first_fingerprints), 1)
        self.assertEqual(first_fingerprints, second_fingerprints)

    async def test_every_downstream_profile_is_returned_once_across_pages(self):
        plan_id = await self._create_plan(
            profiles=37,
            profile_padding=600,
        )
        self._disable_authority_paths()
        cursor = ""
        profiles = []
        fingerprints = set()
        while True:
            page = self.service.get_plan_observability(
                plan_id,
                detail_section="downstream_profiles",
                cursor=cursor,
                page_size=6,
            )
            detail = page["detail"]
            profiles.extend(detail["items"])
            fingerprints.add(detail["full_set_fingerprint"])
            cursor = detail["next_cursor"] or ""
            if not detail["has_more"]:
                break
        profile_ids = [item["profile_fingerprint"] for item in profiles]
        self.assertEqual(len(profile_ids), 37)
        self.assertEqual(len(set(profile_ids)), 37)
        self.assertEqual(len(fingerprints), 1)
        self.assertEqual(
            profiles,
            sorted(
                profiles,
                key=lambda item: self.service._plan_observability_sort_key(
                    "downstream_profiles", item
                ),
            ),
        )

    async def test_cursors_reject_malformed_tampered_wrong_scope_and_version(self):
        plan_id = await self._create_plan(obligations=3, profiles=3)
        first = self.service.get_plan_observability(
            plan_id,
            detail_section="obligation_evidence",
            page_size=1,
        )
        cursor = first["detail"]["next_cursor"]
        self.assertIsInstance(cursor, str)

        for invalid in ("malformed", cursor[:-1] + ("A" if cursor[-1] != "A" else "B")):
            with self.subTest(invalid=invalid[:12]):
                with self.assertRaises(GovernanceError) as caught:
                    self.service.get_plan_observability(
                        plan_id,
                        detail_section="obligation_evidence",
                        cursor=invalid,
                        page_size=1,
                    )
                self.assertEqual(caught.exception.code, ErrorCode.INVALID_CURSOR)

        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="downstream_profiles",
                cursor=cursor,
                page_size=1,
            )
        self.assertEqual(caught.exception.code, ErrorCode.STALE_CURSOR)

        unsupported = self.service._encode_plan_observability_cursor(
            {
                "version": 99,
                "plan_id": plan_id,
                "plan_hash": first["canonical_summary"]["plan_hash"],
                "evidence_fingerprint": first["canonical_summary"][
                    "evidence_fingerprint"
                ],
                "section": "obligation_evidence",
                "ordering_version": 1,
                "full_set_fingerprint": first["detail"][
                    "full_set_fingerprint"
                ],
                "offset": 1,
                "page_size": 1,
            }
        )
        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="obligation_evidence",
                cursor=unsupported,
                page_size=1,
            )
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_CURSOR)

        other_id = await self._create_plan(obligations=3)
        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                other_id,
                detail_section="obligation_evidence",
                cursor=cursor,
                page_size=1,
            )
        self.assertEqual(caught.exception.code, ErrorCode.STALE_CURSOR)

    async def test_cursor_rejects_persisted_authority_change(self):
        plan_id = await self._create_plan(obligations=3)
        first = self.service.get_plan_observability(
            plan_id,
            detail_section="obligation_evidence",
            page_size=1,
        )
        persisted = self.service._load(plan_id)
        binding = persisted.operational.baseline["dependency_risk"]
        binding["evidence_fingerprint"] = stable_hash(
            {"changed": binding["evidence_fingerprint"]}
        )
        self.service._save(persisted)

        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="obligation_evidence",
                cursor=first["detail"]["next_cursor"],
                page_size=1,
            )
        self.assertEqual(caught.exception.code, ErrorCode.STALE_CURSOR)

    async def test_expired_v3_and_v4_failure_summary_are_truthful(self):
        self.reader.model = "helper-dependency-risk-v3"
        v3_plan_id = await self._create_plan(
            obligations=1,
            expiration_minutes=5,
        )
        self.clock.advance(seconds=301)
        self.service.get_plan(v3_plan_id)
        before = self._persisted_files()
        v3 = self.service.get_plan_observability(v3_plan_id)
        self.assertEqual(v3["canonical_summary"]["status"], "expired")
        self.assertEqual(
            v3["canonical_summary"]["dependency_risk_binding_model"],
            "helper-dependency-risk-v3",
        )
        self.assertTrue(
            v3["canonical_summary"]["helper_dependency_replan_required"]
        )
        self.assertFalse(v3["approval_actionable"])
        self.assertEqual(before, self._persisted_files())

        self.reader.model = "helper-dependency-risk-v4"
        self.reader.coverage_failure_reason_codes = [
            "action_profile_truncated",
            "obligation_projection_limit",
            "stale_dependency_evidence",
        ]
        v4_plan_id = await self._create_plan(
            profiles=2,
            profile_truncated=True,
        )
        v4 = self.service.get_plan_observability(v4_plan_id)
        summary = v4["canonical_summary"]
        self.assertFalse(summary["coverage_complete"])
        self.assertFalse(summary["execution_eligible"])
        self.assertEqual(summary["coverage_failure_count"], 3)
        self.assertEqual(
            summary["coverage_failure_reason_codes"],
            [
                "action_profile_truncated",
                "obligation_projection_limit",
                "stale_dependency_evidence",
            ],
        )
        profile_page = self.service.get_plan_observability(
            v4_plan_id,
            detail_section="downstream_profiles",
        )
        self.assertEqual(profile_page["detail"]["returned_count"], 2)
        self.assertTrue(
            all(
                item["reason_codes"] == ["action_profile_truncated"]
                for item in profile_page["detail"]["items"]
            )
        )

    async def test_pages_do_not_refresh_write_approve_lock_dispatch_or_leak(self):
        plan_id = await self._create_plan(
            obligations=4,
            profiles=4,
            profile_padding=100,
        )
        self.service.get_plan_observability(plan_id)
        before = self._persisted_files()
        approval_before = deepcopy(
            self.service._load(plan_id).to_dict()["approval"]
        )
        self._disable_authority_paths()

        obligations = self.service.get_plan_observability(
            plan_id,
            detail_section="obligation_evidence",
            page_size=2,
        )
        profiles = self.service.get_plan_observability(
            plan_id,
            detail_section="downstream_profiles",
            page_size=2,
        )
        combined = {"obligations": obligations, "profiles": profiles}
        serialized = json.dumps(combined)

        def all_keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield key
                    yield from all_keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from all_keys(item)

        keys = set(all_keys(combined))
        self.assertFalse(
            {
                "proposed_config",
                "current_config",
                "normalized_proposed_config",
                "normalized_current_config",
                "credentials",
                "access_token",
                "secret",
            }
            & keys
        )
        self.assertNotIn("synthetic-r3-cursor-key", serialized)
        self.assertEqual(
            obligations["operational"]["provider_capability_evidence"][
                "fallback"
            ],
            "none",
        )
        self.assertEqual(self.reader.read_count, 1)
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertEqual(self.service._plan_locks, {})
        self.assertEqual(self.service._target_locks, {})
        self.assertEqual(
            approval_before,
            self.service._load(plan_id).to_dict()["approval"],
        )
        self.assertEqual(before, self._persisted_files())


if __name__ == "__main__":
    unittest.main()
