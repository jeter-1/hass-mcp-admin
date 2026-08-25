"""HAMCP-089.R3 bounded persisted-plan observability regressions."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import GOVERNANCE  # noqa: E402
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402
from ha_mcp_engineering.governance.models import PlanStatus  # noqa: E402
from ha_mcp_engineering.governance.risk import (  # noqa: E402
    automation_action_consequence_profile,
)
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


def _production_profile(
    index: int,
    *,
    action_count: int = 256,
    entity_id_chars: int = 80,
) -> dict:
    """Build one persisted profile through the production action projector."""

    actions = []
    for action_index in range(action_count):
        suffix = f"_{action_index:04d}"
        entity_id = "cover." + "x" * (
            entity_id_chars - len("cover.") - len(suffix)
        ) + suffix
        actions.append(
            {
                "action": "cover.set_cover_position",
                "target": {"entity_id": entity_id},
                "data": {"position": (50 + index) % 100},
            }
        )
    consequence = automation_action_consequence_profile(
        {"action": actions}
    )
    automation = f"automation.synthetic_profile_{index:04d}"
    return {
        "automation_id": automation,
        "automation_resource_id": automation.removeprefix("automation."),
        "relationships": ["trigger"],
        "physical_consequence": consequence["physical_consequence"],
        "complete": consequence["complete"],
        "truncated": consequence["truncated"],
        "action_domains": consequence["action_domains"],
        "services": consequence["services"],
        "reason_codes": consequence["reason_codes"],
        "effect_projection_model": consequence[
            "effect_projection_model"
        ],
        "effect_targets": consequence["effect_targets"],
        "effect_data": consequence["effect_data"],
        "effect_structure_fingerprint": consequence[
            "effect_structure_fingerprint"
        ],
        "effect_projection_fingerprint": consequence[
            "effect_projection_fingerprint"
        ],
        "effect_projection_clipped": consequence[
            "effect_projection_clipped"
        ],
        "profile_fingerprint": consequence["evidence_fingerprint"],
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


class SyntheticLegacyGateway:
    def __init__(self, automation_id: str, current: dict) -> None:
        self.automation_id = automation_id
        self.current = deepcopy(current)
        self.read_count = 0
        self.write_count = 0

    async def get(self, automation_id: str):
        self.read_count += 1
        if automation_id != self.automation_id:
            raise AssertionError("unexpected legacy automation read")
        return deepcopy(self.current)

    async def write(self, *_args, **_kwargs):
        self.write_count += 1
        raise AssertionError("legacy automation write must remain unreachable")

    async def validate(self):
        raise AssertionError("legacy validation must remain unreachable")


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

    async def _create_plan_with_profiles(
        self, profiles: list[dict]
    ) -> str:
        self.reader.obligations = []
        self.reader.profiles = deepcopy(profiles)
        created = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id,
            desired_state="on",
            expiration_minutes=120,
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

    def _traverse_downstream_profiles(
        self,
        plan_id: str,
        *,
        page_size: int = 100,
    ) -> tuple[list[dict], list[dict]]:
        cursor = ""
        profiles = []
        pages = []
        fragment_bytes = bytearray()
        fragment_record_index = None
        fragment_fingerprint = None
        while True:
            page = self.service.get_plan_observability(
                plan_id,
                detail_section="downstream_profiles",
                cursor=cursor,
                page_size=page_size,
            )
            pages.append(page)
            detail = page["detail"]
            profiles.extend(detail["items"])
            for fragment in detail["fragments"]:
                if fragment_record_index is None:
                    fragment_record_index = fragment[
                        "logical_record_index"
                    ]
                    fragment_fingerprint = fragment[
                        "logical_record_fingerprint"
                    ]
                self.assertEqual(
                    fragment["logical_record_index"],
                    fragment_record_index,
                )
                self.assertEqual(
                    fragment["logical_record_fingerprint"],
                    fragment_fingerprint,
                )
                self.assertEqual(fragment["byte_start"], len(fragment_bytes))
                encoded = fragment["payload"]
                payload = base64.b64decode(
                    encoded + "=" * (-len(encoded) % 4),
                    altchars=b"-_",
                    validate=True,
                )
                self.assertEqual(
                    base64.urlsafe_b64encode(payload)
                    .decode("ascii")
                    .rstrip("="),
                    encoded,
                )
                fragment_bytes.extend(payload)
                self.assertEqual(fragment["byte_end"], len(fragment_bytes))
                if fragment["is_final"]:
                    profile = json.loads(fragment_bytes.decode("utf-8"))
                    self.assertEqual(stable_hash(profile), fragment_fingerprint)
                    profiles.append(profile)
                    fragment_bytes = bytearray()
                    fragment_record_index = None
                    fragment_fingerprint = None
            cursor = detail["next_cursor"] or ""
            if not detail["has_more"]:
                self.assertFalse(fragment_bytes)
                return profiles, pages

    def _assert_complete_profile_traversal(
        self,
        plan_id: str,
        expected: list[dict],
        *,
        page_size: int = 100,
    ) -> tuple[list[dict], list[dict]]:
        profiles, pages = self._traverse_downstream_profiles(
            plan_id,
            page_size=page_size,
        )
        ordered = sorted(
            expected,
            key=lambda item: self.service._plan_observability_sort_key(
                "downstream_profiles", item
            ),
        )
        self.assertEqual(profiles, ordered)
        self.assertEqual(len(profiles), len(expected))
        self.assertEqual(
            len({item["profile_fingerprint"] for item in profiles}),
            len(expected),
        )
        self.assertEqual(
            {page["detail"]["total_count"] for page in pages},
            {len(expected)},
        )
        self.assertEqual(
            len(
                {
                    page["detail"]["full_set_fingerprint"]
                    for page in pages
                }
            ),
            1,
        )
        self.assertEqual(
            pages[0]["detail"]["full_set_fingerprint"],
            stable_hash(ordered),
        )
        self.assertEqual(
            {
                page["canonical_summary"][
                    "downstream_profile_full_set_fingerprint"
                ]
                for page in pages
            },
            {stable_hash(ordered)},
        )
        self.assertTrue(
            all(
                self.service._plan_observability_encoded_chars(page)
                <= 52_000
                for page in pages
            )
        )
        self.assertFalse(pages[-1]["detail"]["has_more"])
        self.assertIsNone(pages[-1]["detail"]["next_cursor"])
        self.assertEqual(
            sum(not page["detail"]["has_more"] for page in pages),
            1,
        )
        return profiles, pages

    async def _create_contract_v1_plan(
        self,
        *,
        sentinel: str,
        title: str = "Synthetic R4 contract-v1 observability",
    ) -> tuple[str, SyntheticLegacyGateway]:
        automation_id = "synthetic_r4_contract_v1"
        current = {
            "id": automation_id,
            "alias": "Synthetic R4 legacy source",
            "description": "Before",
            "trigger": [
                {
                    "platform": "event",
                    "event_type": "synthetic_r4_contract_v1",
                }
            ],
            "condition": [],
            "action": [
                {
                    "service": "notify.notify",
                    "data": {"message": sentinel},
                }
            ],
            "mode": "single",
        }
        proposed = deepcopy(current)
        proposed["description"] = f"After {sentinel}"
        gateway = SyntheticLegacyGateway(automation_id, current)
        self.service.gateway = gateway
        created = await self.service.create_plan(
            title=title,
            description="Synthetic contract-v1 writer regression",
            operation="update_automation",
            automation_id=automation_id,
            proposed_config=proposed,
        )
        persisted = self.service.repository.get(created["plan_id"])
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.contract_version, 1)
        return created["plan_id"], gateway

    async def _grant_helper_plan(
        self,
        plan_id: str,
        plan_hash: str,
    ) -> None:
        pending = self.service.approve(plan_id, plan_hash)
        while pending["status"] == "approval_pending":
            _, csrf = await self.service.issue_external_csrf(
                plan_id,
                pending["challenge_id"],
            )
            pending = await self.service.decide_external_approval(
                plan_id=plan_id,
                challenge_id=pending["challenge_id"],
                expected_plan_hash=plan_hash,
                approval_kind=pending["approval_kind"],
                approval_action=pending["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=(
                    "home_assistant_admin_ingress:synthetic-r4-reviewer"
                ),
            )

    def _assert_canonical_lifecycle_matches_public(self, result: dict) -> None:
        summary = result["canonical_summary"]
        expected = {
            "status": result["status"],
            "approval_state": result["approval"]["state"],
            "approval_lifecycle": result["approval_lifecycle"],
            "approval_bundle_state": result["approval_bundle_state"],
            "approval_actionable": result["approval_actionable"],
            "apply_allowed": result["apply_allowed"],
            "next_required_operation": result["next_required_operation"],
        }
        for field, value in expected.items():
            self.assertEqual(summary[field], value, field)

    @staticmethod
    def _decode_public_cursor_bytes(cursor: str) -> bytes:
        return base64.b64decode(
            cursor + "=" * (-len(cursor) % 4),
            altchars=b"-_",
            validate=True,
        )

    @staticmethod
    def _encode_public_cursor_bytes(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    def _synthetic_cursor_claims(self) -> dict:
        return {
            "version": 3,
            "plan_id": "plan_beta44_opaque_cursor_sentinel",
            "plan_hash": "1f" * 32,
            "evidence_fingerprint": "2e" * 32,
            "section": "obligation_evidence",
            "ordering_version": 2,
            "full_set_fingerprint": "3d" * 32,
            "offset": 123456789,
            "fragment_offset": 0,
            "fragment_index": 0,
            "record_fingerprint": "4c" * 32,
            "page_size": 97,
        }

    def _assert_invalid_cursor_token(self, cursor: str) -> GovernanceError:
        with self.assertRaises(GovernanceError) as caught:
            self.service._decode_plan_observability_cursor(cursor)
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_CURSOR)
        self.assertEqual(
            caught.exception.details,
            {
                "field": "cursor",
                "reason": "integrity_or_format_invalid",
                "operation": "get_change_plan",
            },
        )
        return caught.exception

    async def test_contract_v1_observability_excludes_raw_configuration(self):
        sentinel = "SYNTHETIC_R4_LEGACY_CONFIGURATION_SENTINEL"
        plan_id, gateway = await self._create_contract_v1_plan(
            sentinel=sentinel
        )
        reads_after_writer = gateway.read_count
        before = self._persisted_files()
        self._disable_authority_paths()

        results = [
            self.service.get_plan_observability(plan_id),
            self.service.get_plan_observability(
                plan_id,
                detail_section="obligation_evidence",
            ),
            self.service.get_plan_observability(
                plan_id,
                detail_section="downstream_profiles",
            ),
        ]

        prohibited_keys = {
            "proposed_config",
            "current_config",
            "normalized_proposed_config",
            "normalized_current_config",
            "snapshot",
            "events",
            "dry_run_results",
        }

        def all_keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield key
                    yield from all_keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from all_keys(item)

        for result in results:
            with self.subTest(section=result["detail"]["section"]):
                encoded = json.dumps(result, sort_keys=True)
                self.assertEqual(json.loads(encoded), result)
                self.assertLess(len(encoded), 60_000)
                self.assertNotIn(sentinel, encoded)
                self.assertTrue(prohibited_keys.isdisjoint(all_keys(result)))

        self.assertEqual(gateway.read_count, reads_after_writer)
        self.assertEqual(gateway.write_count, 0)
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertEqual(self.service._plan_locks, {})
        self.assertEqual(self.service._target_locks, {})
        self.assertEqual(before, self._persisted_files())

    async def test_contract_v1_observability_fails_closed_when_projection_is_unsafe(self):
        sentinel = "SYNTHETIC_R4_NEWLY_KNOWN_SECRET"
        plan_id, gateway = await self._create_contract_v1_plan(
            sentinel="synthetic-safe-configuration-value",
            title=f"Legacy title {sentinel}",
        )
        before = self._persisted_files()
        reads_after_writer = gateway.read_count
        self.service.sensitive_values = (sentinel,)
        self._disable_authority_paths()

        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(plan_id)

        self.assertEqual(
            caught.exception.code,
            ErrorCode.CHANGE_PLAN_STORAGE_ERROR,
        )
        self.assertEqual(gateway.read_count, reads_after_writer)
        self.assertEqual(gateway.write_count, 0)
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertEqual(before, self._persisted_files())

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
        self.assertEqual(page["detail"]["completed_logical_record_count"], 2)
        self.assertEqual(page["detail"]["returned_fragment_count"], 0)
        self.assertEqual(page["detail"]["fragments"], [])
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

    async def test_production_profile_after_eight_records_is_fully_traversable(self):
        # Released Beta 43 measured 14,165 formatted base characters and
        # 42,332 for this profile: 59,743 together versus its 52,000 pager
        # budget. The first page returned eight records; the next call failed.
        profiles = [_profile(index) for index in range(31)]
        profiles[8] = _production_profile(8)
        plan_id = await self._create_plan_with_profiles(profiles)
        blocking_profile = profiles[8]
        self.assertEqual(blocking_profile["physical_consequence"], "direct")
        self.assertEqual(len(blocking_profile["effect_targets"]), 256)
        self.assertEqual(len(blocking_profile["effect_data"]), 256)
        self.assertEqual(
            len(
                self.service._plan_observability_record_bytes(
                    blocking_profile
                )
            ),
            39_669,
        )
        self.assertEqual(
            self.service._plan_observability_encoded_chars(blocking_profile),
            42_332,
        )
        self._disable_authority_paths()

        returned, pages = self._assert_complete_profile_traversal(
            plan_id, profiles
        )
        repeated, repeated_pages = self._assert_complete_profile_traversal(
            plan_id, profiles
        )
        self.assertEqual(returned, repeated)
        self.assertEqual(
            pages[0]["detail"]["full_set_fingerprint"],
            repeated_pages[0]["detail"]["full_set_fingerprint"],
        )
        fragment_fields = (
            "logical_record_index",
            "logical_record_fingerprint",
            "fragment_index",
            "byte_start",
            "byte_end",
            "total_bytes",
            "is_final",
            "fragment_fingerprint",
        )
        self.assertEqual(
            [
                tuple(fragment[field] for field in fragment_fields)
                for page in pages
                for fragment in page["detail"]["fragments"]
            ],
            [
                tuple(fragment[field] for field in fragment_fields)
                for page in repeated_pages
                for fragment in page["detail"]["fragments"]
            ],
        )
        self.assertTrue(any(page["detail"]["fragments"] for page in pages))

    async def test_oversized_profile_positions_and_page_sizes_make_progress(self):
        first = [_production_profile(0), *[_profile(i) for i in range(1, 7)]]
        last = [*[_profile(i) for i in range(6)], _production_profile(6)]
        consecutive = [_profile(i) for i in range(8)]
        consecutive[3] = _production_profile(3)
        consecutive[4] = _production_profile(4)
        plans = []
        for name, profiles in (
            ("first", first),
            ("last", last),
            ("consecutive", consecutive),
        ):
            plans.append(
                (name, profiles, await self._create_plan_with_profiles(profiles))
            )
        self._disable_authority_paths()

        for name, profiles, plan_id in plans:
            for page_size in (1, 20, 100):
                with self.subTest(position=name, page_size=page_size):
                    _, pages = self._assert_complete_profile_traversal(
                        plan_id,
                        profiles,
                        page_size=page_size,
                    )
                    self.assertLess(len(pages), 100)
                    self.assertTrue(
                        any(page["detail"]["fragments"] for page in pages)
                    )

    async def test_profile_larger_than_gateway_limit_is_reconstructed(self):
        profiles = [_production_profile(0, entity_id_chars=150)]
        self.assertGreater(
            self.service._plan_observability_encoded_chars(profiles[0]),
            60_000,
        )
        plan_id = await self._create_plan_with_profiles(profiles)
        self._disable_authority_paths()

        _, pages = self._assert_complete_profile_traversal(plan_id, profiles)

        self.assertGreater(len(pages), 1)
        self.assertTrue(
            all(page["detail"]["returned_fragment_count"] == 1 for page in pages)
        )
        self.assertEqual(
            sum(
                page["detail"]["completed_logical_record_count"]
                for page in pages
            ),
            1,
        )

    async def test_fragment_response_exact_budget_and_one_character_less(self):
        profiles = [_production_profile(0)]
        plan_id = await self._create_plan_with_profiles(profiles)
        self._disable_authority_paths()
        ordinary = self.service.get_plan_observability(
            plan_id,
            detail_section="downstream_profiles",
            page_size=100,
        )
        ordinary_size = self.service._plan_observability_encoded_chars(ordinary)

        exact = self.service.get_plan_observability(
            plan_id,
            detail_section="downstream_profiles",
            page_size=100,
            response_limit=ordinary_size + 8_000,
        )
        one_less = self.service.get_plan_observability(
            plan_id,
            detail_section="downstream_profiles",
            page_size=100,
            response_limit=ordinary_size + 7_999,
        )

        self.assertEqual(
            self.service._plan_observability_encoded_chars(exact),
            ordinary_size,
        )
        self.assertLessEqual(
            self.service._plan_observability_encoded_chars(one_less),
            ordinary_size - 1,
        )
        self.assertLess(
            one_less["detail"]["fragments"][0]["byte_end"],
            exact["detail"]["fragments"][0]["byte_end"],
        )

    async def test_tool_framing_remains_below_gateway_limit_for_fragments(self):
        profiles = [_profile(index) for index in range(31)]
        profiles[8] = _production_profile(8)
        plan_id = await self._create_plan_with_profiles(profiles)
        self._disable_authority_paths()
        prior = GOVERNANCE.service
        GOVERNANCE.service = self.service
        try:
            cursor = ""
            completed = 0
            response_count = 0
            while True:
                encoded = await get_change_plan(
                    plan_id,
                    detail_section="downstream_profiles",
                    cursor=cursor,
                    page_size=100,
                )
                self.assertLess(len(encoded), 60_000)
                parsed = json.loads(encoded)
                self.assertTrue(parsed["success"])
                detail = parsed["data"]["detail"]
                completed += detail["completed_logical_record_count"]
                response_count += 1
                cursor = detail["next_cursor"] or ""
                if not detail["has_more"]:
                    break
        finally:
            GOVERNANCE.service = prior

        self.assertEqual(completed, 31)
        self.assertGreater(response_count, 2)

    async def test_fragment_cursor_binds_record_collection_and_byte_position(self):
        profiles = [_production_profile(0), _profile(1)]
        plan_id = await self._create_plan_with_profiles(profiles)
        self._disable_authority_paths()
        first = self.service.get_plan_observability(
            plan_id,
            detail_section="downstream_profiles",
            page_size=100,
        )
        fragment = first["detail"]["fragments"][0]
        cursor = first["detail"]["next_cursor"]
        claims = self.service._decode_plan_observability_cursor(cursor)
        self.assertGreater(claims["fragment_offset"], 0)
        self.assertEqual(claims["fragment_index"], 1)
        self.assertEqual(
            claims["record_fingerprint"], stable_hash(profiles[0])
        )

        fragment_material = dict(fragment)
        supplied_fingerprint = fragment_material.pop("fragment_fingerprint")
        self.assertEqual(stable_hash(fragment_material), supplied_fingerprint)
        fragment_material["payload"] += "A"
        self.assertNotEqual(stable_hash(fragment_material), supplied_fingerprint)

        changed_record = dict(claims)
        changed_record["record_fingerprint"] = "a" * 64
        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="downstream_profiles",
                cursor=self.service._encode_plan_observability_cursor(
                    changed_record
                ),
                page_size=100,
            )
        self.assertEqual(caught.exception.code, ErrorCode.STALE_CURSOR)

        out_of_range = dict(claims)
        out_of_range["fragment_offset"] = len(
            self.service._plan_observability_record_bytes(profiles[0])
        )
        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="downstream_profiles",
                cursor=self.service._encode_plan_observability_cursor(
                    out_of_range
                ),
                page_size=100,
            )
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_CURSOR)
        self.assertEqual(
            caught.exception.details["reason"],
            "fragment_offset_out_of_range",
        )

        completed = dict(claims)
        completed.update(
            {
                "offset": len(profiles),
                "fragment_offset": 0,
                "fragment_index": 0,
                "record_fingerprint": "b" * 64,
            }
        )
        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="downstream_profiles",
                cursor=self.service._encode_plan_observability_cursor(completed),
                page_size=100,
            )
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_CURSOR)
        self.assertEqual(caught.exception.details["reason"], "offset_out_of_range")

        restarted = ChangeGovernanceService(
            ChangePlanRepository(self.root / "plans"),
            UnusedLegacyGateway(),
            now=self.clock,
            helper_state_gateway=_Unreachable(),
            helper_dependency_risk_reader=_Unreachable(),
        )
        with self.assertRaises(GovernanceError) as caught:
            restarted.get_plan_observability(
                plan_id,
                detail_section="downstream_profiles",
                cursor=cursor,
                page_size=100,
            )
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_CURSOR)

    async def test_fragment_cursor_rejects_changed_persisted_profile_material(self):
        profiles = [_production_profile(0), _profile(1)]
        plan_id = await self._create_plan_with_profiles(profiles)
        first = self.service.get_plan_observability(
            plan_id,
            detail_section="downstream_profiles",
            page_size=100,
        )
        cursor = first["detail"]["next_cursor"]
        persisted = self.service._load(plan_id)
        binding = persisted.operational.baseline["dependency_risk"]
        binding["downstream_profiles"][0]["effect_data"][0] += "-changed"
        self.service._save(persisted)
        self._disable_authority_paths()

        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="downstream_profiles",
                cursor=cursor,
                page_size=100,
            )
        self.assertEqual(caught.exception.code, ErrorCode.STALE_CURSOR)

    async def test_malformed_oversized_persisted_profile_fails_closed(self):
        profiles = [_production_profile(0)]
        profiles[0]["effect_data"][0] = float("nan")
        plan_id = await self._create_plan_with_profiles(profiles)
        self._disable_authority_paths()

        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="downstream_profiles",
                page_size=100,
            )
        self.assertEqual(
            caught.exception.code,
            ErrorCode.CHANGE_PLAN_STORAGE_ERROR,
        )

    async def test_fragment_success_interruption_and_refusal_are_read_only(self):
        profiles = [_production_profile(0), _profile(1)]
        plan_id = await self._create_plan_with_profiles(profiles)
        reads_before = self.reader.read_count
        before = self._persisted_files()
        self._disable_authority_paths()

        first = self.service.get_plan_observability(
            plan_id,
            detail_section="downstream_profiles",
            page_size=100,
        )
        continuation = self.service.get_plan_observability(
            plan_id,
            detail_section="downstream_profiles",
            cursor=first["detail"]["next_cursor"],
            page_size=100,
        )
        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="downstream_profiles",
                cursor=first["detail"]["next_cursor"] + "!",
                page_size=100,
            )

        self.assertEqual(caught.exception.code, ErrorCode.INVALID_CURSOR)
        self.assertEqual(first["detail"]["returned_fragment_count"], 1)
        self.assertEqual(
            continuation["detail"]["returned_fragment_count"], 1
        )
        self.assertEqual(self.reader.read_count, reads_before)
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertEqual(self.service._plan_locks, {})
        self.assertEqual(self.service._target_locks, {})
        self.assertEqual(before, self._persisted_files())

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

    def test_cursor_tokens_are_randomized_opaque_and_bounded(self):
        claims = self._synthetic_cursor_claims()

        first = self.service._encode_plan_observability_cursor(claims)
        second = self.service._encode_plan_observability_cursor(claims)

        self.assertNotEqual(first, second)
        self.assertEqual(
            self.service._decode_plan_observability_cursor(first),
            claims,
        )
        self.assertEqual(
            self.service._decode_plan_observability_cursor(second),
            claims,
        )
        self.assertLess(len(first), 2_048)
        self.assertNotIn(".", first)
        self.assertEqual(len(first.split(".")), 1)

        decoded = self._decode_public_cursor_bytes(first)
        with self.assertRaises((UnicodeDecodeError, json.JSONDecodeError)):
            json.loads(decoded.decode("utf-8"))

        canonical_claims = json.dumps(
            claims,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertNotIn(canonical_claims, decoded)
        for field in (
            "plan_id",
            "plan_hash",
            "evidence_fingerprint",
            "full_set_fingerprint",
            "record_fingerprint",
            "section",
        ):
            recognizable = str(claims[field]).encode("utf-8")
            self.assertNotIn(recognizable, decoded, field)
            self.assertNotIn(str(claims[field]), first, field)
        for fragment in (
            b'"offset":123456789',
            b'"fragment_offset":0',
            b'"fragment_index":0',
            b'"page_size":97',
        ):
            self.assertNotIn(fragment, decoded)

        largest_valid_claims = dict(claims)
        largest_valid_claims["plan_id"] = "p" * 256
        for field, marker in (
            ("plan_hash", "a"),
            ("evidence_fingerprint", "b"),
            ("full_set_fingerprint", "c"),
            ("record_fingerprint", "d"),
        ):
            largest_valid_claims[field] = marker * 64
        largest_valid = self.service._encode_plan_observability_cursor(
            largest_valid_claims
        )
        self.assertLess(len(largest_valid), 2_048)
        self.assertEqual(
            self.service._decode_plan_observability_cursor(largest_valid),
            largest_valid_claims,
        )

    def test_cursor_aead_rejects_tampering_and_noncanonical_encodings(self):
        claims = self._synthetic_cursor_claims()
        token = self.service._encode_plan_observability_cursor(claims)
        raw = self._decode_public_cursor_bytes(token)
        nonce_size = 12
        tag_size = 16
        ciphertext_end = len(raw) - tag_size
        self.assertGreater(ciphertext_end, nonce_size)

        mutation_positions = {
            "nonce": 0,
            "ciphertext_beginning": nonce_size,
            "ciphertext_middle": nonce_size
            + (ciphertext_end - nonce_size) // 2,
            "ciphertext_end": ciphertext_end - 1,
            "authentication_tag": len(raw) - 1,
        }
        for label, position in mutation_positions.items():
            mutated = bytearray(raw)
            mutated[position] ^= 0x01
            with self.subTest(label=label):
                self._assert_invalid_cursor_token(
                    self._encode_public_cursor_bytes(bytes(mutated))
                )

        invalid_tokens = {
            "truncated": self._encode_public_cursor_bytes(raw[:-1]),
            "extra_bytes": self._encode_public_cursor_bytes(raw + b"\x00"),
            "invalid_characters": token + "!",
            "padding_added": token + "=",
        }
        for label, invalid in invalid_tokens.items():
            with self.subTest(label=label):
                self._assert_invalid_cursor_token(invalid)

        noncanonical = None
        alphabet = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "abcdefghijklmnopqrstuvwxyz"
            "0123456789-_"
        )
        for _ in range(3):
            token = self.service._encode_plan_observability_cursor(claims)
            raw = self._decode_public_cursor_bytes(token)
            if len(token) % 4 in {2, 3}:
                for replacement in alphabet:
                    candidate = token[:-1] + replacement
                    if candidate == token:
                        continue
                    try:
                        candidate_raw = self._decode_public_cursor_bytes(
                            candidate
                        )
                    except ValueError:
                        continue
                    if candidate_raw == raw:
                        noncanonical = candidate
                        break
            if noncanonical is not None:
                break
            claims["plan_id"] += "x"
        if noncanonical is None:
            self.fail("could not construct a noncanonical base64url token")
        self._assert_invalid_cursor_token(noncanonical)

    def test_beta42_signed_plaintext_cursor_is_rejected_without_leakage(self):
        claims = self._synthetic_cursor_claims()
        payload = json.dumps(
            claims,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(
            self.service._plan_observability_cursor_key,
            payload,
            hashlib.sha256,
        ).digest()
        legacy = (
            self._encode_public_cursor_bytes(payload)
            + "."
            + self._encode_public_cursor_bytes(signature)
        )

        error = self._assert_invalid_cursor_token(legacy)
        bounded_error = json.dumps(
            {
                "code": error.code.value,
                "details": error.details,
            },
            sort_keys=True,
        )
        self.assertNotIn(legacy, bounded_error)
        self.assertNotIn("synthetic-r3-cursor-key", bounded_error)
        for field in (
            "plan_id",
            "plan_hash",
            "evidence_fingerprint",
            "full_set_fingerprint",
        ):
            self.assertNotIn(str(claims[field]), bounded_error)

    def test_beta43_encrypted_cursor_is_rejected_under_beta44_domain(self):
        claims = {
            "version": 2,
            "plan_id": "plan_beta43_cursor",
            "plan_hash": "1f" * 32,
            "evidence_fingerprint": "2e" * 32,
            "section": "downstream_profiles",
            "ordering_version": 1,
            "full_set_fingerprint": "3d" * 32,
            "offset": 8,
            "page_size": 100,
        }
        payload = json.dumps(
            claims,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        old_key = hmac.new(
            self.service._plan_observability_cursor_key,
            b"ha-mcp-engineering:plan-observability:cursor-encryption-key:v2",
            hashlib.sha256,
        ).digest()
        nonce = b"\x01" * 12
        encrypted = AESGCM(old_key).encrypt(
            nonce,
            payload,
            b"ha-mcp-engineering:plan-observability:cursor:v2",
        )
        cursor = self._encode_public_cursor_bytes(nonce + encrypted)

        self._assert_invalid_cursor_token(cursor)

    async def test_every_obligation_is_returned_once_across_stable_pages(self):
        plan_id = await self._create_plan(
            obligations=100,
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
        self.assertEqual(len(first_ids), 100)
        self.assertEqual(len(set(first_ids)), 100)
        self.assertEqual(first, second)
        self.assertEqual(len(first_fingerprints), 1)
        self.assertEqual(first_fingerprints, second_fingerprints)

    async def test_every_downstream_profile_is_returned_once_across_pages(self):
        plan_id = await self._create_plan(
            profiles=37,
            profile_padding=600,
        )
        self._disable_authority_paths()

        def traverse():
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
                    return profiles, fingerprints

        first, first_fingerprints = traverse()
        second, second_fingerprints = traverse()
        profile_ids = [item["profile_fingerprint"] for item in first]
        self.assertEqual(len(profile_ids), 37)
        self.assertEqual(len(set(profile_ids)), 37)
        self.assertEqual(first, second)
        self.assertEqual(len(first_fingerprints), 1)
        self.assertEqual(first_fingerprints, second_fingerprints)
        self.assertEqual(
            first,
            sorted(
                first,
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
        self.assertEqual(
            caught.exception.details["reason"],
            "plan_or_section_binding_changed",
        )

        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="obligation_evidence",
                cursor=cursor,
                page_size=2,
            )
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_CURSOR)
        self.assertEqual(
            caught.exception.details["reason"],
            "page_size_mismatch",
        )

        unsupported = self.service._encode_plan_observability_cursor(
            {
                "version": 2,
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
                "fragment_offset": 0,
                "fragment_index": 0,
                "record_fingerprint": "4c" * 32,
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
        self.assertEqual(
            caught.exception.details["reason"],
            "unsupported_version",
        )

        out_of_range = self.service._encode_plan_observability_cursor(
            {
                "version": 3,
                "plan_id": plan_id,
                "plan_hash": first["canonical_summary"]["plan_hash"],
                "evidence_fingerprint": first["canonical_summary"][
                    "evidence_fingerprint"
                ],
                "section": "obligation_evidence",
                "ordering_version": 2,
                "full_set_fingerprint": first["detail"][
                    "full_set_fingerprint"
                ],
                "offset": 4,
                "fragment_offset": 0,
                "fragment_index": 0,
                "record_fingerprint": "4c" * 32,
                "page_size": 1,
            }
        )
        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="obligation_evidence",
                cursor=out_of_range,
                page_size=1,
            )
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_CURSOR)
        self.assertEqual(
            caught.exception.details["reason"],
            "offset_out_of_range",
        )

        other_id = await self._create_plan(obligations=3)
        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                other_id,
                detail_section="obligation_evidence",
                cursor=cursor,
                page_size=1,
            )
        self.assertEqual(caught.exception.code, ErrorCode.STALE_CURSOR)
        self.assertEqual(
            caught.exception.details["reason"],
            "plan_or_section_binding_changed",
        )

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
        self.assertEqual(
            caught.exception.details["reason"],
            "persisted_authority_changed",
        )

    async def test_expired_v3_and_v4_failure_summary_are_truthful(self):
        self.reader.model = "helper-dependency-risk-v3"
        v3_plan_id = await self._create_plan(
            obligations=1,
            expiration_minutes=5,
        )
        persisted_v3 = self.service._load_for_projection(v3_plan_id)
        persisted_v3_hash = self.service.plan_hash(persisted_v3)
        self.clock.advance(seconds=301)
        before = self._persisted_files()
        reads_before = self.reader.read_count
        with (
            patch.object(
                self.service,
                "_resolve_lifecycle",
                side_effect=AssertionError(
                    "mutating lifecycle resolver must remain unreachable"
                ),
            ),
            patch.object(
                self.service,
                "_record",
                side_effect=AssertionError(
                    "observability must not persist lifecycle events"
                ),
            ),
            patch.object(
                self.service,
                "_save",
                side_effect=AssertionError(
                    "observability must not save plans"
                ),
            ),
            patch.object(
                self.service,
                "_project_plan_event_to_task",
                side_effect=AssertionError(
                    "observability must not project task events"
                ),
            ),
        ):
            v3 = self.service.get_plan_observability(v3_plan_id)
        self.assertEqual(v3["canonical_summary"]["status"], "expired")
        self.assertEqual(v3["status"], "expired")
        self.assertEqual(v3["approval"]["state"], "invalidated")
        self.assertEqual(v3["approval_lifecycle"], "approval_invalidated")
        self.assertEqual(v3["approval_bundle_state"], "invalidated")
        self.assertEqual(
            v3["canonical_summary"]["dependency_risk_binding_model"],
            "helper-dependency-risk-v3",
        )
        self.assertTrue(
            v3["canonical_summary"]["helper_dependency_replan_required"]
        )
        self.assertFalse(v3["approval_actionable"])
        self.assertFalse(v3["apply_allowed"])
        self.assertNotEqual(
            v3["approval_lifecycle"],
            "approval_not_requested",
        )
        self.assertEqual(v3["plan_hash"], persisted_v3_hash)
        self._assert_canonical_lifecycle_matches_public(v3)
        self.assertEqual(self.reader.read_count, reads_before)
        self.assertEqual(self.helper.dispatch_count, 0)
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

    async def test_challenge_expiration_projects_truth_without_persistence(self):
        plan_id = await self._create_plan(expiration_minutes=120)
        persisted = self.service._load_for_projection(plan_id)
        plan_hash = self.service.plan_hash(persisted)
        pending = self.service.approve(plan_id, plan_hash)
        self.assertEqual(pending["approval_lifecycle"], "approval_pending_external")
        before = self._persisted_files()
        reads_before = self.reader.read_count
        self.clock.advance(seconds=3_601)

        with (
            patch.object(
                self.service,
                "_resolve_lifecycle",
                side_effect=AssertionError(
                    "mutating lifecycle resolver must remain unreachable"
                ),
            ),
            patch.object(
                self.service,
                "_record",
                side_effect=AssertionError(
                    "observability must not persist lifecycle events"
                ),
            ),
            patch.object(
                self.service,
                "_save",
                side_effect=AssertionError(
                    "observability must not save plans"
                ),
            ),
            patch.object(
                self.service,
                "_project_plan_event_to_task",
                side_effect=AssertionError(
                    "observability must not project task events"
                ),
            ),
        ):
            result = self.service.get_plan_observability(plan_id)

        self.assertEqual(result["status"], "awaiting_approval")
        self.assertEqual(result["approval"]["state"], "expired")
        self.assertEqual(result["approval_lifecycle"], "approval_expired")
        self.assertEqual(result["approval_bundle_state"], "expired")
        self.assertFalse(result["approval_actionable"])
        self.assertFalse(result["apply_allowed"])
        self.assertIsNone(result["next_required_operation"])
        self.assertEqual(result["plan_hash"], plan_hash)
        self._assert_canonical_lifecycle_matches_public(result)
        self.assertEqual(self.reader.read_count, reads_before)
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertEqual(before, self._persisted_files())

    async def test_terminal_plan_observability_remains_terminal_and_inert(self):
        plan_id = await self._create_plan(expiration_minutes=5)
        self.clock.advance(seconds=301)
        persisted = self.service._load_for_projection(plan_id)
        self.service._resolve_lifecycle(persisted)
        terminal = self.service._load_for_projection(plan_id)
        self.assertEqual(terminal.status, PlanStatus.EXPIRED)
        terminal_hash = self.service.plan_hash(terminal)
        before = self._persisted_files()
        self.clock.advance(seconds=600)
        self._disable_authority_paths()

        with (
            patch.object(
                self.service,
                "_resolve_lifecycle",
                side_effect=AssertionError(
                    "mutating lifecycle resolver must remain unreachable"
                ),
            ),
            patch.object(
                self.service,
                "_record",
                side_effect=AssertionError(
                    "terminal observability must not append events"
                ),
            ),
            patch.object(
                self.service,
                "_save",
                side_effect=AssertionError(
                    "terminal observability must not save"
                ),
            ),
        ):
            result = self.service.get_plan_observability(plan_id)

        self.assertEqual(result["status"], "expired")
        self.assertEqual(result["approval_lifecycle"], "approval_invalidated")
        self.assertFalse(result["approval_actionable"])
        self.assertFalse(result["apply_allowed"])
        self.assertEqual(result["plan_hash"], terminal_hash)
        self._assert_canonical_lifecycle_matches_public(result)
        self.assertEqual(before, self._persisted_files())

    async def test_dispatched_operational_plan_does_not_project_expired(self):
        plan_id = await self._create_plan(expiration_minutes=5)
        persisted = self.service._load_for_projection(plan_id)
        plan_hash = self.service.plan_hash(persisted)
        await self._grant_helper_plan(plan_id, plan_hash)
        dispatched = self.service._load_for_projection(plan_id)
        self.service._consume_approval_bundle(dispatched)
        dispatched.status = PlanStatus.VERIFICATION_REQUIRED
        dispatched.operational.dispatch.update(
            {
                "attempt_count": 1,
                "dispatched": True,
                "attempted_at": self.clock().isoformat(),
                "provider_response_received": True,
            }
        )
        self.service._save(dispatched)
        before = self._persisted_files()
        reads_before = self.reader.read_count
        self.clock.advance(seconds=301)
        self._disable_authority_paths()

        with (
            patch.object(
                self.service,
                "_resolve_lifecycle",
                side_effect=AssertionError(
                    "mutating lifecycle resolver must remain unreachable"
                ),
            ),
            patch.object(
                self.service,
                "_record",
                side_effect=AssertionError(
                    "dispatched observability must not append events"
                ),
            ),
            patch.object(
                self.service,
                "_save",
                side_effect=AssertionError(
                    "dispatched observability must not save"
                ),
            ),
            patch.object(
                self.service,
                "_project_plan_event_to_task",
                side_effect=AssertionError(
                    "dispatched observability must not project task events"
                ),
            ),
        ):
            result = self.service.get_plan_observability(plan_id)

        self.assertEqual(result["status"], "verification_required")
        self.assertEqual(result["approval"]["state"], "consumed")
        self.assertEqual(result["approval_lifecycle"], "approval_consumed")
        self.assertEqual(result["approval_bundle_state"], "consumed")
        self.assertFalse(result["approval_actionable"])
        self.assertFalse(result["apply_allowed"])
        self.assertIsNone(result["next_required_operation"])
        self.assertEqual(result["plan_hash"], plan_hash)
        self.assertEqual(
            result["operational"]["dispatch"]["attempt_count"],
            1,
        )
        self._assert_canonical_lifecycle_matches_public(result)
        self.assertEqual(self.reader.read_count, reads_before)
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertEqual(before, self._persisted_files())

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
        obligation_continuation = self.service.get_plan_observability(
            plan_id,
            detail_section="obligation_evidence",
            cursor=obligations["detail"]["next_cursor"],
            page_size=2,
        )
        profile_continuation = self.service.get_plan_observability(
            plan_id,
            detail_section="downstream_profiles",
            cursor=profiles["detail"]["next_cursor"],
            page_size=2,
        )
        invalid_cursor = obligations["detail"]["next_cursor"] + "!"
        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan_observability(
                plan_id,
                detail_section="obligation_evidence",
                cursor=invalid_cursor,
                page_size=2,
            )
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_CURSOR)
        combined = {
            "obligations": obligations,
            "obligation_continuation": obligation_continuation,
            "profiles": profiles,
            "profile_continuation": profile_continuation,
            "bounded_error": caught.exception.details,
        }
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
