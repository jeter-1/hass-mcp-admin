"""Beta 34 terminal-history policy projection compatibility."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.historical_policy import (  # noqa: E402
    BETA32_POLICY_SOURCE_COMMIT,
    BETA33_INITIAL_POLICY_SOURCE_COMMIT,
    HISTORICAL_POLICY_PROJECTION_MODEL,
    HISTORICAL_POLICY_PROJECTION_PROFILES,
    historical_policy_projection_match,
    persisted_historical_approval_integrity_matches,
    persisted_policy_snapshot_integrity_matches,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    ChangePlan,
    PlanStatus,
)
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    evaluate_change_policy,
    policy_snapshot_matches,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
    is_terminal_plan,
)


FIXTURE_ROOT = ROOT / "tests" / "fixtures"
FIXTURE_PATHS = (
    FIXTURE_ROOT / "beta32_retained_effect_prohibited_plan.json",
    FIXTURE_ROOT / "beta33_initial_retained_effect_reason_plan.json",
)
CONSUMED_FIXTURE_PATH = (
    FIXTURE_ROOT / "beta33_initial_retained_effect_consumed_plan.json"
)
ALL_FIXTURE_PATHS = (*FIXTURE_PATHS, CONSUMED_FIXTURE_PATH)
PROVENANCE_PATH = (
    FIXTURE_ROOT / "beta34_historical_policy_provenance.json"
)
GENERATOR_PATH = (
    ROOT / "scripts" / "generate_beta34_historical_policy_fixtures.py"
)
LEGACY_RETAINED_FIXTURE_PATHS = (
    FIXTURE_ROOT / "beta32_legacy_retained_effect_prohibited_plan.json",
    FIXTURE_ROOT / "beta6_legacy_retained_effect_expired_plan.json",
)
LEGACY_RETAINED_PROVENANCE_PATH = (
    FIXTURE_ROOT / "beta34_legacy_retained_effect_provenance.json"
)
LEGACY_RETAINED_GENERATOR_PATH = (
    ROOT
    / "scripts"
    / "generate_beta34_legacy_retained_effect_fixtures.py"
)
BETA6_LEGACY_SOURCE_COMMIT = (
    "5c7eebf962837f85f2309b1b5099401fb075cd6e"
)
EXPECTED_PROFILES = (
    "beta32_retained_effect_prohibited",
    "beta33_initial_retained_effect_reason",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _NoWriteGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.writes = 0

    async def read(self, *args: object) -> None:
        self.calls.append(("read", *args))
        return None

    async def get(self, *args: object) -> None:
        self.calls.append(("get", *args))
        return None

    async def write(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("write", *args, kwargs))
        self.writes += 1
        raise AssertionError("historical projection must never write")

    async def validate(self) -> dict[str, object]:
        self.calls.append(("validate",))
        return {"result": "valid", "errors": None}


class HistoricalPolicyFixtureProvenanceTests(unittest.TestCase):
    def test_fixtures_are_bound_to_exact_historical_writers(self):
        provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            provenance["compatibility_model"],
            HISTORICAL_POLICY_PROJECTION_MODEL,
        )
        self.assertEqual(
            provenance["generator_sha256"], _sha256(GENERATOR_PATH)
        )
        self.assertEqual(
            provenance["writer_path"],
            "ChangeGovernanceService.create_configuration_plan",
        )
        sources = {
            item["profile"]: item for item in provenance["sources"]
        }
        self.assertEqual(
            sources["beta32"]["commit"], BETA32_POLICY_SOURCE_COMMIT
        )
        self.assertEqual(
            sources["beta33-initial"]["commit"],
            BETA33_INITIAL_POLICY_SOURCE_COMMIT,
        )
        by_path = {item["path"]: item for item in provenance["fixtures"]}
        for path in ALL_FIXTURE_PATHS:
            with self.subTest(fixture=path.name):
                relative = path.relative_to(ROOT).as_posix()
                evidence = by_path[relative]
                self.assertEqual(evidence["sha256"], _sha256(path))
                expected_mutations = int(
                    path == CONSUMED_FIXTURE_PATH
                )
                self.assertEqual(
                    evidence["provider_write_count"], expected_mutations
                )
                self.assertEqual(
                    evidence["execution_task_count"], expected_mutations
                )
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(value["contract_version"], 2)
                self.assertEqual(len(value["operations"]), 1)

    def test_legacy_retained_fixtures_use_exact_reviewed_writers(self):
        provenance = json.loads(
            LEGACY_RETAINED_PROVENANCE_PATH.read_text(encoding="utf-8")
        )
        self.assertEqual(
            provenance["compatibility_model"],
            HISTORICAL_POLICY_PROJECTION_MODEL,
        )
        self.assertEqual(
            provenance["generator_sha256"],
            _sha256(LEGACY_RETAINED_GENERATOR_PATH),
        )
        self.assertEqual(
            provenance["writer_path"],
            "ChangeGovernanceService.create_plan",
        )
        sources = {
            item["profile"]: item for item in provenance["sources"]
        }
        self.assertEqual(
            sources["beta32-prohibited"]["commit"],
            BETA32_POLICY_SOURCE_COMMIT,
        )
        self.assertEqual(
            sources["beta6-expired"]["commit"],
            BETA6_LEGACY_SOURCE_COMMIT,
        )
        by_path = {item["path"]: item for item in provenance["fixtures"]}
        for path in LEGACY_RETAINED_FIXTURE_PATHS:
            with self.subTest(fixture=path.name):
                evidence = by_path[path.relative_to(ROOT).as_posix()]
                self.assertEqual(evidence["sha256"], _sha256(path))
                self.assertEqual(evidence["provider_write_count"], 0)
                self.assertEqual(evidence["execution_task_count"], 0)
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("contract_version", value)
                self.assertNotIn("operations", value)
                plan = ChangePlan.from_dict(value)
                self.assertEqual(plan.contract_version, 1)
                self.assertEqual(plan.plan_version, 1)
                self.assertEqual(plan.operation.value, "update_automation")
                self.assertEqual(plan.target_type, "automation")
                self.assertNotEqual(plan.target_id, plan.plan_id)
                self.assertEqual(plan.operations, [])
                self.assertEqual(
                    plan.policy_decision.reason_codes,
                    (
                        "safety_critical_effect_not_reviewed",
                        "supported_configuration_change",
                    ),
                )


class HistoricalPolicyProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plan_root = self.root / "plans"
        self.plan_root.mkdir(parents=True)
        self.persisted_paths: dict[str, Path] = {}
        self.persisted_before: dict[str, bytes] = {}
        for fixture in FIXTURE_PATHS:
            raw = fixture.read_bytes()
            plan_id = json.loads(raw)["plan_id"]
            persisted = self.plan_root / f"{plan_id}.json"
            persisted.write_bytes(raw)
            self.persisted_paths[plan_id] = persisted
            self.persisted_before[plan_id] = raw
        self.gateway = _NoWriteGateway()
        self.repository = ChangePlanRepository(self.plan_root)
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            now=lambda: datetime(
                2026, 8, 11, 12, 0, tzinfo=timezone.utc
            ),
        )

    async def asyncTearDown(self):
        self.temporary.cleanup()

    def _plans(self) -> list[ChangePlan]:
        plans = []
        for fixture in FIXTURE_PATHS:
            plan_id = json.loads(fixture.read_bytes())["plan_id"]
            plan = self.repository.get(plan_id)
            self.assertIsNotNone(plan)
            assert plan is not None
            plans.append(plan)
        return plans

    def _assert_persisted_bytes_unchanged(self) -> None:
        for plan_id, path in self.persisted_paths.items():
            self.assertEqual(path.read_bytes(), self.persisted_before[plan_id])

    def _write_adversarial(self, value: dict[str, object]) -> str:
        plan_id = str(value["plan_id"])
        path = self.plan_root / f"{plan_id}.json"
        path.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return plan_id

    @staticmethod
    def _rebind_snapshot_hashes(value: dict[str, object]) -> None:
        # ``from_dict`` intentionally normalizes nested dictionaries in
        # place, so use a deep copy when retaining a raw JSON test record.
        plan = ChangePlan.from_dict(copy.deepcopy(value))
        value["policy_decision"]["policy_subject_hash"] = (
            evaluate_change_policy(plan).policy_subject_hash
        )
        decision_payload = {
            key: value["policy_decision"][key]
            for key in (
                "policy_version",
                "policy_class",
                "risk_delta",
                "physical_consequence",
                "reason_codes",
                "required_acknowledgements",
                "policy_subject_hash",
            )
        }
        value["policy_decision"]["policy_decision_hash"] = stable_hash(
            decision_payload
        )
        value["approval"]["policy_decision_hash"] = value[
            "policy_decision"
        ]["policy_decision_hash"]

    def test_exact_terminal_snapshots_match_only_historical_projection(self):
        for plan, expected_profile in zip(
            self._plans(), EXPECTED_PROFILES, strict=True
        ):
            with self.subTest(profile=expected_profile):
                self.assertTrue(is_terminal_plan(plan))
                self.assertFalse(policy_snapshot_matches(plan))
                self.assertTrue(
                    persisted_policy_snapshot_integrity_matches(plan)
                )
                match = historical_policy_projection_match(plan)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(match.model, HISTORICAL_POLICY_PROJECTION_MODEL)
                self.assertEqual(match.profile, expected_profile)
                current = evaluate_change_policy(plan)
                self.assertEqual(
                    current.policy_class.value, "elevated_admin"
                )
                self.assertEqual(current.risk_delta.value, "moderate")
                self.assertEqual(
                    current.physical_consequence.value,
                    "safety_critical",
                )
                self.assertIn(
                    "non_risk_increasing_condition_guard_added",
                    current.reason_codes,
                )

    def test_matcher_rejects_contradictory_historical_approval_bundles(self):
        cases = (
            (
                FIXTURE_PATHS[0],
                lambda value: value["approval"].__setitem__(
                    "policy_decision_hash", "f" * 64
                ),
            ),
            (
                FIXTURE_PATHS[1],
                lambda value: value["approval"].__setitem__(
                    "policy_class", "standard_admin"
                ),
            ),
            (
                CONSUMED_FIXTURE_PATH,
                lambda value: value["approval"].__setitem__(
                    "bound_plan_hash", "e" * 64
                ),
            ),
            (
                CONSUMED_FIXTURE_PATH,
                lambda value: value["approval"][
                    "elevated_risk_acknowledgement"
                ].__setitem__("policy_decision_hash", "d" * 64),
            ),
        )
        for fixture, mutate in cases:
            with self.subTest(fixture=fixture.name):
                value = json.loads(fixture.read_text(encoding="utf-8"))
                mutate(value)
                plan = ChangePlan.from_dict(copy.deepcopy(value))
                self.assertTrue(
                    persisted_policy_snapshot_integrity_matches(plan)
                )
                self.assertFalse(
                    persisted_historical_approval_integrity_matches(plan)
                )
                self.assertIsNone(historical_policy_projection_match(plan))

    def test_matcher_locally_rejects_incompatible_execution_evidence(self):
        cases = (
            (
                "applied_at",
                lambda value: value.__setitem__(
                    "applied_at", "2026-08-10T12:00:00+00:00"
                ),
            ),
            (
                "verification_passed",
                lambda value: value["verification"].__setitem__(
                    "status", "passed"
                ),
            ),
            (
                "rollback_requested",
                lambda value: value["rollback"].__setitem__(
                    "requested_at", "2026-08-10T12:00:00+00:00"
                ),
            ),
            (
                "apply_request_id",
                lambda value: value.__setitem__(
                    "apply_request_id", "unexpected-execution"
                ),
            ),
            (
                "execution_outcome",
                lambda value: value.__setitem__(
                    "execution_outcome", "applied"
                ),
            ),
        )
        for fixture in FIXTURE_PATHS:
            for label, mutate in cases:
                with self.subTest(fixture=fixture.name, evidence=label):
                    value = json.loads(fixture.read_text(encoding="utf-8"))
                    mutate(value)
                    plan = ChangePlan.from_dict(copy.deepcopy(value))
                    self.assertTrue(
                        persisted_policy_snapshot_integrity_matches(plan)
                    )
                    self.assertTrue(
                        persisted_historical_approval_integrity_matches(plan)
                    )
                    self.assertIsNone(
                        historical_policy_projection_match(plan)
                    )

    def test_exact_history_projects_without_false_health_failures(self):
        plans = self._plans()
        public = [self.service.get_plan(plan.plan_id) for plan in plans]
        self.assertEqual(public[0]["status"], "prohibited")
        self.assertEqual(public[0]["approval_lifecycle"], "prohibited")
        self.assertFalse(public[0]["approval_actionable"])
        self.assertEqual(public[1]["status"], "superseded")
        self.assertEqual(
            public[1]["approval_lifecycle"], "approval_invalidated"
        )
        self.assertFalse(public[1]["approval_actionable"])

        listed = self.service.list_plans(limit=100)
        self.assertEqual(listed["count"], 2)
        self.assertFalse(listed["partial"])
        self.assertEqual(listed["projection_failure_count"], 0)
        self.assertEqual(
            self.service.list_plans(status="prohibited", limit=100)[
                "count"
            ],
            1,
        )
        self.assertEqual(
            self.service.list_plans(status="superseded", limit=100)[
                "count"
            ],
            1,
        )

        health = self.service.health_summary()
        self.assertEqual(health["total_plans"], 2)
        self.assertEqual(health["projection_failure_count"], 0)
        self.assertEqual(health["policy_snapshot_mismatches"], 0)
        self.assertIsNone(health["projection_failure_warning"])
        compatibility = health[
            "historical_policy_snapshot_compatibility"
        ]
        self.assertEqual(
            compatibility,
            {
                "model": HISTORICAL_POLICY_PROJECTION_MODEL,
                "compatible_count": 2,
                "profile_counts": {
                    HISTORICAL_POLICY_PROJECTION_PROFILES[0]: 1,
                    HISTORICAL_POLICY_PROJECTION_PROFILES[1]: 1,
                },
                "authorization_effect": "none_projection_only",
            },
        )
        self.assertEqual(
            health["plans_by_policy_class"]["prohibited"], 1
        )
        self.assertEqual(
            health["plans_by_policy_class"]["elevated_admin"], 1
        )
        self.assertEqual(
            health["plans_by_policy_class"]["projection_failed"], 0
        )
        self.assertTrue(health["policy_class_accounting_valid"])
        for field in (
            "plans_awaiting_approval",
            "plans_requiring_approval",
            "pending_plan_approvals",
            "pending_elevated_acknowledgements",
            "pending_challenge_count",
        ):
            self.assertEqual(health[field], 0)
        self.assertEqual(self.service.pending_external_reviews(), [])
        self._assert_persisted_bytes_unchanged()

    def test_restart_and_deep_audit_remain_read_only_and_deterministic(self):
        audit = self.service.deep_audit_plan_store()
        self.assertEqual(audit["projection_failure_count"], 0)
        self.assertEqual(
            audit["historical_policy_snapshot_compatibility"],
            self.service.health_summary()[
                "historical_policy_snapshot_compatibility"
            ],
        )
        recovered = ChangeGovernanceService(
            ChangePlanRepository(self.plan_root),
            self.gateway,
            now=lambda: datetime(
                2026, 8, 11, 12, 0, tzinfo=timezone.utc
            ),
        )
        self.assertEqual(
            recovered.health_summary()["projection_failure_count"], 0
        )
        self.assertEqual(
            recovered.health_summary()["policy_snapshot_mismatches"], 0
        )
        self.assertEqual(
            recovered.health_summary()[
                "historical_policy_snapshot_compatibility"
            ],
            self.service.health_summary()[
                "historical_policy_snapshot_compatibility"
            ],
        )
        self.assertEqual(len(recovered.resolved_plans()), 2)
        self._assert_persisted_bytes_unchanged()

    async def test_historical_projection_never_grants_execution_authority(self):
        for plan in self._plans():
            with self.subTest(plan_id=plan.plan_id):
                with self.assertRaises(GovernanceError) as approval_error:
                    self.service.approve(
                        plan.plan_id, self.service.plan_hash(plan)
                    )
                self.assertEqual(
                    approval_error.exception.code,
                    ErrorCode.POLICY_SNAPSHOT_MISMATCH,
                )
                with self.assertRaises(GovernanceError) as apply_error:
                    await self.service.apply(
                        plan.plan_id, self.service.plan_hash(plan)
                    )
                self.assertEqual(
                    apply_error.exception.code,
                    ErrorCode.POLICY_SNAPSHOT_MISMATCH,
                )
                self.assertIsNone(
                    self.service.task_repository.get_for_plan(plan.plan_id)
                )
        self.assertEqual(self.gateway.writes, 0)
        self.assertFalse(
            any(call and call[0] == "write" for call in self.gateway.calls)
        )
        self._assert_persisted_bytes_unchanged()

    async def test_consumed_historical_approval_is_immutable_and_inert(self):
        raw = CONSUMED_FIXTURE_PATH.read_bytes()
        value = json.loads(raw)
        plan_id = str(value["plan_id"])
        persisted = self.plan_root / f"{plan_id}.json"
        persisted.write_bytes(raw)
        before_approval = copy.deepcopy(value["approval"])

        plan = self.repository.get(plan_id)
        self.assertIsNotNone(plan)
        assert plan is not None
        match = historical_policy_projection_match(plan)
        self.assertIsNotNone(match)
        self.assertEqual(plan.approval.state, ApprovalState.CONSUMED)
        self.assertIsNotNone(plan.approval.consumed_at)
        self.assertEqual(self.service.get_plan(plan_id)["status"], "applied")
        self.assertEqual(
            self.service.health_summary()[
                "historical_policy_snapshot_compatibility"
            ]["compatible_count"],
            3,
        )

        with self.assertRaises(GovernanceError) as approval_error:
            self.service.approve(plan_id, self.service.plan_hash(plan))
        self.assertEqual(
            approval_error.exception.code,
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        with self.assertRaises(GovernanceError) as apply_error:
            await self.service.apply(plan_id, self.service.plan_hash(plan))
        self.assertEqual(
            apply_error.exception.code,
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        after = json.loads(persisted.read_bytes())
        self.assertEqual(after["approval"], before_approval)
        self.assertEqual(persisted.read_bytes(), raw)
        self.assertIsNone(self.service.task_repository.get_for_plan(plan_id))
        self.assertEqual(self.gateway.writes, 0)

    def test_current_and_legacy_snapshots_remain_distinct_categories(self):
        current = json.loads(
            FIXTURE_PATHS[1].read_text(encoding="utf-8")
        )
        current["plan_id"] = "b3440000000000000000000000000002"
        current["target"]["target_id"] = current["plan_id"]
        current_plan = ChangePlan.from_dict(copy.deepcopy(current))
        current_decision = evaluate_change_policy(current_plan)
        current["policy_decision"] = current_decision.to_dict()
        current["approval"]["policy_decision_hash"] = (
            current_decision.policy_decision_hash
        )
        current["approval"]["policy_class"] = (
            current_decision.policy_class.value
        )
        current_id = self._write_adversarial(current)
        loaded_current = self.repository.get(current_id)
        self.assertIsNotNone(loaded_current)
        assert loaded_current is not None
        self.assertTrue(policy_snapshot_matches(loaded_current))
        self.assertIsNone(
            historical_policy_projection_match(loaded_current)
        )
        self.assertEqual(
            self.service.get_plan(current_id)["plan_id"], current_id
        )

        legacy = json.loads(
            FIXTURE_PATHS[1].read_text(encoding="utf-8")
        )
        legacy["plan_id"] = "b3440000000000000000000000000003"
        legacy["target"]["target_id"] = legacy["plan_id"]
        legacy["policy_decision"] = None
        legacy["approval"]["policy_decision_hash"] = None
        legacy["approval"]["policy_class"] = None
        legacy_id = self._write_adversarial(legacy)
        loaded_legacy = self.repository.get(legacy_id)
        self.assertIsNotNone(loaded_legacy)
        assert loaded_legacy is not None
        self.assertIsNone(loaded_legacy.policy_decision)
        self.assertIsNone(
            historical_policy_projection_match(loaded_legacy)
        )
        self.assertEqual(
            self.service.get_plan(legacy_id)["plan_id"], legacy_id
        )

        health = self.service.health_summary()
        self.assertEqual(health["projection_failure_count"], 0)
        self.assertEqual(
            health["plans_by_policy_class"][
                "legacy_without_policy_snapshot"
            ],
            1,
        )
        self.assertEqual(
            health["historical_policy_snapshot_compatibility"][
                "compatible_count"
            ],
            2,
        )


class LegacyRetainedEffectProjectionTests(
    unittest.IsolatedAsyncioTestCase
):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.plan_root = Path(self.temporary.name) / "plans"
        self.plan_root.mkdir(parents=True)
        self.before: dict[str, bytes] = {}
        for fixture in LEGACY_RETAINED_FIXTURE_PATHS:
            raw = fixture.read_bytes()
            plan_id = str(json.loads(raw)["plan_id"])
            (self.plan_root / f"{plan_id}.json").write_bytes(raw)
            self.before[plan_id] = raw
        self.gateway = _NoWriteGateway()
        self.repository = ChangePlanRepository(self.plan_root)
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            now=lambda: datetime(
                2026, 8, 12, 12, 0, tzinfo=timezone.utc
            ),
        )

    async def asyncTearDown(self):
        self.temporary.cleanup()

    def _plans(self) -> list[ChangePlan]:
        plans: list[ChangePlan] = []
        for plan_id in self.before:
            plan = self.repository.get(plan_id)
            self.assertIsNotNone(plan)
            assert plan is not None
            plans.append(plan)
        return plans

    def _assert_bytes_unchanged(self) -> None:
        for plan_id, raw in self.before.items():
            self.assertEqual(
                (self.plan_root / f"{plan_id}.json").read_bytes(),
                raw,
            )

    def _write_adversarial(self, value: dict[str, object]) -> str:
        plan_id = str(value["plan_id"])
        (self.plan_root / f"{plan_id}.json").write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return plan_id

    @staticmethod
    def _rebind_snapshot_hashes(value: dict[str, object]) -> None:
        plan = ChangePlan.from_dict(copy.deepcopy(value))
        value["policy_decision"]["policy_subject_hash"] = (
            evaluate_change_policy(plan).policy_subject_hash
        )
        LegacyRetainedEffectProjectionTests._rebind_decision_hash(value)

    @staticmethod
    def _rebind_decision_hash(value: dict[str, object]) -> None:
        decision = value["policy_decision"]
        decision_payload = {
            key: decision[key]
            for key in (
                "policy_version",
                "policy_class",
                "risk_delta",
                "physical_consequence",
                "reason_codes",
                "required_acknowledgements",
                "policy_subject_hash",
            )
        }
        decision["policy_decision_hash"] = stable_hash(decision_payload)
        value["approval"]["policy_decision_hash"] = decision[
            "policy_decision_hash"
        ]

    def test_exact_legacy_shapes_resolve_live_health_failures(self):
        plans = self._plans()
        self.assertEqual(len(plans), 2)
        for plan in plans:
            with self.subTest(plan_id=plan.plan_id):
                self.assertEqual(plan.contract_version, 1)
                self.assertTrue(is_terminal_plan(plan))
                self.assertFalse(policy_snapshot_matches(plan))
                self.assertTrue(
                    persisted_policy_snapshot_integrity_matches(plan)
                )
                match = historical_policy_projection_match(plan)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(
                    match.profile,
                    "beta32_retained_effect_prohibited",
                )
                self.assertIsNone(
                    self.service._approval_bundle_integrity_error(plan)
                )
                public = self.service.get_plan(plan.plan_id)
                self.assertEqual(public["status"], "prohibited")
                self.assertFalse(public["approval_actionable"])
                self.assertFalse(public["approval_challenge_created"])
                self.assertFalse(public["apply_allowed"])
                self.assertIsNone(public["next_required_operation"])
                self.assertIsNone(public["execution_task"]["task_id"])

        health = self.service.health_summary()
        self.assertEqual(health["projection_failure_count"], 0)
        self.assertEqual(health["policy_snapshot_mismatches"], 0)
        self.assertEqual(
            health["plans_by_policy_class"]["prohibited"], 2
        )
        self.assertEqual(
            health["plans_by_policy_class"]["projection_failed"], 0
        )
        self.assertEqual(
            health["historical_policy_snapshot_compatibility"],
            {
                "model": HISTORICAL_POLICY_PROJECTION_MODEL,
                "compatible_count": 2,
                "profile_counts": {
                    "beta32_retained_effect_prohibited": 2,
                    "beta33_initial_retained_effect_reason": 0,
                },
                "authorization_effect": "none_projection_only",
            },
        )

        before_restart = health[
            "historical_policy_snapshot_compatibility"
        ]
        audit = self.service.deep_audit_plan_store()
        self.assertEqual(
            audit["historical_policy_snapshot_compatibility"],
            before_restart,
        )
        restarted = ChangeGovernanceService(
            ChangePlanRepository(self.plan_root),
            self.gateway,
            now=lambda: datetime(
                2026, 8, 12, 12, 0, tzinfo=timezone.utc
            ),
        )
        self.assertEqual(
            restarted.health_summary()[
                "historical_policy_snapshot_compatibility"
            ],
            before_restart,
        )
        self.assertEqual(
            restarted.health_summary()["projection_failure_count"], 0
        )
        self._assert_bytes_unchanged()

    async def test_legacy_history_never_becomes_new_authority(self):
        for plan in self._plans():
            with self.subTest(plan_id=plan.plan_id):
                with self.assertRaises(GovernanceError) as approval_error:
                    self.service.approve(
                        plan.plan_id,
                        self.service.plan_hash(plan),
                    )
                self.assertEqual(
                    approval_error.exception.code,
                    ErrorCode.POLICY_SNAPSHOT_MISMATCH,
                )
                with self.assertRaises(GovernanceError) as apply_error:
                    await self.service.apply(
                        plan.plan_id,
                        self.service.plan_hash(plan),
                    )
                self.assertEqual(
                    apply_error.exception.code,
                    ErrorCode.POLICY_SNAPSHOT_MISMATCH,
                )
                self.assertIsNone(
                    self.service.task_repository.get_for_plan(plan.plan_id)
                )
        self.assertEqual(self.gateway.writes, 0)
        self._assert_bytes_unchanged()

    def test_legacy_unknown_tampered_or_actionable_shapes_fail_closed(self):
        fixture = LEGACY_RETAINED_FIXTURE_PATHS[0]
        cases: list[tuple[str, dict[str, object]]] = []

        unknown = json.loads(fixture.read_text(encoding="utf-8"))
        unknown["policy_decision"]["reason_codes"] = [
            "safety_critical_effect_not_reviewed",
            "supported_configuration_change",
            "unreviewed_historical_reason",
        ]
        self._rebind_decision_hash(unknown)
        cases.append(("unknown_reason", unknown))

        decision_hash = json.loads(fixture.read_text(encoding="utf-8"))
        decision_hash["policy_decision"]["policy_decision_hash"] = "0" * 64
        decision_hash["approval"]["policy_decision_hash"] = "0" * 64
        cases.append(("decision_hash", decision_hash))

        challenge = json.loads(fixture.read_text(encoding="utf-8"))
        challenge["approval"]["challenge_id"] = (
            "synthetic-untrusted-challenge"
        )
        cases.append(("authority_evidence", challenge))

        execution = json.loads(fixture.read_text(encoding="utf-8"))
        execution["applied_at"] = "2026-08-11T18:01:00+00:00"
        cases.append(("execution_evidence", execution))

        for name, value in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "plans"
                root.mkdir(parents=True)
                plan_id = str(value["plan_id"])
                path = root / f"{plan_id}.json"
                path.write_text(
                    json.dumps(value, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                before = path.read_bytes()
                service = ChangeGovernanceService(
                    ChangePlanRepository(root),
                    _NoWriteGateway(),
                )
                loaded = service.repository.get(plan_id)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertIsNone(
                    historical_policy_projection_match(loaded)
                )
                with self.assertRaises(GovernanceError) as raised:
                    service.get_plan(plan_id)
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.POLICY_SNAPSHOT_MISMATCH,
                )
                self.assertEqual(path.read_bytes(), before)

    def test_nonterminal_old_snapshot_remains_a_policy_mismatch(self):
        value = json.loads(FIXTURE_PATHS[1].read_text(encoding="utf-8"))
        value["plan_id"] = "b3440000000000000000000000000001"
        value["target"]["target_id"] = value["plan_id"]
        value["status"] = PlanStatus.AWAITING_APPROVAL.value
        value["approval"]["state"] = ApprovalState.REQUIRED.value
        value["approval"]["bundle_state"] = "pending_plan_approval"
        value["approval"]["elevated_risk_acknowledgement"]["state"] = (
            ApprovalState.REQUIRED.value
        )
        value["events"] = [
            event
            for event in value["events"]
            if event["event"] != "change_plan_superseded"
        ]
        # The plan ID participates in immutable policy authority. Rebind both
        # hashes to create a self-consistent but still obsolete active record.
        self._rebind_snapshot_hashes(value)
        plan_id = self._write_adversarial(value)
        loaded = self.repository.get(plan_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertFalse(is_terminal_plan(loaded))
        self.assertTrue(persisted_policy_snapshot_integrity_matches(loaded))
        self.assertIsNone(historical_policy_projection_match(loaded))
        with self.assertRaises(GovernanceError) as raised:
            self.service.get_plan(plan_id)
        self.assertEqual(
            raised.exception.code, ErrorCode.POLICY_SNAPSHOT_MISMATCH
        )
        listed = self.service.list_plans(limit=100)
        self.assertTrue(listed["partial"])
        self.assertEqual(listed["projection_failure_count"], 1)
        health = self.service.health_summary()
        self.assertEqual(health["projection_failure_count"], 1)
        self.assertEqual(health["policy_snapshot_mismatches"], 1)
        self.assertEqual(
            health["historical_policy_snapshot_compatibility"][
                "compatible_count"
            ],
            2,
        )
        self.assertEqual(self.gateway.writes, 0)

    def test_tampered_subject_decision_and_bundle_remain_fail_closed(self):
        cases: list[tuple[str, dict[str, object], ErrorCode]] = []

        subject = json.loads(FIXTURE_PATHS[1].read_text(encoding="utf-8"))
        subject["plan_id"] = "b3450000000000000000000000000001"
        subject["target"]["target_id"] = subject["plan_id"]
        subject["title"] = "Tampered immutable title"
        cases.append(("subject", subject, ErrorCode.POLICY_SNAPSHOT_MISMATCH))

        decision = json.loads(FIXTURE_PATHS[1].read_text(encoding="utf-8"))
        decision["plan_id"] = "b3450000000000000000000000000002"
        decision["target"]["target_id"] = decision["plan_id"]
        decision["policy_decision"]["policy_decision_hash"] = "0" * 64
        decision["approval"]["policy_decision_hash"] = "0" * 64
        cases.append(("decision", decision, ErrorCode.POLICY_SNAPSHOT_MISMATCH))

        unknown = json.loads(FIXTURE_PATHS[1].read_text(encoding="utf-8"))
        unknown["plan_id"] = "b3450000000000000000000000000004"
        unknown["target"]["target_id"] = unknown["plan_id"]
        unknown["policy_decision"]["reason_codes"] = sorted(
            [
                "retained_safety_critical_effect",
                "safety_critical_effect_requires_elevated_review",
                "supported_configuration_change",
                "unreviewed_transition_reason",
            ]
        )
        self._rebind_snapshot_hashes(unknown)
        cases.append(
            (
                "unreviewed_profile",
                unknown,
                ErrorCode.POLICY_SNAPSHOT_MISMATCH,
            )
        )

        bundle = json.loads(FIXTURE_PATHS[1].read_text(encoding="utf-8"))
        bundle["plan_id"] = "b3450000000000000000000000000003"
        bundle["target"]["target_id"] = bundle["plan_id"]
        bundle["approval"]["elevated_risk_acknowledgement"]["state"] = (
            ApprovalState.REQUIRED.value
        )
        self._rebind_snapshot_hashes(bundle)
        cases.append(("bundle", bundle, ErrorCode.APPROVAL_SEQUENCE_FAILURE))

        for name, value, expected in cases:
            with self.subTest(name=name):
                plan_id = self._write_adversarial(value)
                with self.assertRaises(GovernanceError) as raised:
                    self.service.get_plan(plan_id)
                self.assertEqual(raised.exception.code, expected)
                self.assertEqual(self.gateway.writes, 0)
        health = self.service.health_summary()
        self.assertEqual(health["projection_failure_count"], 4)
        self.assertEqual(health["policy_snapshot_mismatches"], 3)
        self.assertEqual(health["approval_sequence_failures"], 1)
        self.assertEqual(
            health["historical_policy_snapshot_compatibility"][
                "compatible_count"
            ],
            2,
        )
