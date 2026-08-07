"""Runtime-inert, compatibility, migration, and observability invariants."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.f3.models import NORMALIZED_OUTCOME_TO_TASK_STATE  # noqa: E402
from ha_mcp_engineering.f3.operational_models import (  # noqa: E402
    CAPABILITY_IDENTITIES,
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    OPERATIONAL_PLAN_CONTRACT_VERSION,
    OPERATIONAL_PREPARED_AUTHORITY_MODEL,
    OPERATIONAL_PROVIDER_CONTRACT_MODEL,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    SUPPORTED_OPERATIONS,
    canonical_json,
    operational_prepared_authority_payload,
    recompute_operational_prepared_hash,
    stable_hash,
)
from ha_mcp_engineering.f3.operational_observability import (  # noqa: E402
    COMMON_COUNTERS,
    OPERATION_COUNTERS,
    OperationalEventRecorder,
    OperationalMetrics,
)
from ha_mcp_engineering.governance.models import ChangeOperation  # noqa: E402
from ha_mcp_engineering.governance.task_models import TASK_SCHEMA_VERSION  # noqa: E402
from ha_mcp_engineering.tools import registered_tools  # noqa: E402
from ha_mcp_engineering.tools.registry import get_registered_server  # noqa: E402

from tests.f3_operational_fixtures import (  # noqa: E402
    PLAN_HASH,
    PUBLIC_TASK_ID,
    TASK_ID,
    make_context,
    prepare_context,
)


class RuntimeInertAndSchemaTests(unittest.TestCase):
    def test_application_and_current_service_do_not_import_or_instantiate_adapter(self):
        for relative in (
            "ha_mcp_engineering/application.py",
            "ha_mcp_engineering/governance/runtime.py",
            "ha_mcp_engineering/governance/service.py",
            "ha_mcp_engineering/f3/__init__.py",
        ):
            text = (BETA / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertNotIn("operational_adapter", text)
                self.assertNotIn("OperationalAdministrationAdapter", text)

    def test_current_operational_routes_remain_the_existing_service_methods(self):
        service_path = BETA / "ha_mcp_engineering/governance/service.py"
        tree = ast.parse(service_path.read_text(encoding="utf-8"))
        methods = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(
            {
                "create_backup_plan",
                "create_reload_plan",
                "create_addon_restart_plan",
                "create_home_assistant_restart_plan",
                "_apply_operational_backup",
                "_apply_operational_lifecycle",
                "_resume_operational_verification",
                "_resume_lifecycle_verification",
                "reconcile_operational_plans",
            }
            <= methods
        )

    def test_public_tools_and_persisted_schema_vocabulary_are_unchanged(self):
        self.assertEqual(len(registered_tools(get_registered_server())), 49)
        self.assertEqual(TASK_SCHEMA_VERSION, 1)
        service_tree = ast.parse(
            (BETA / "ha_mcp_engineering/governance/service.py").read_text(
                encoding="utf-8"
            )
        )
        constants = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in service_tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id
            in {
                "CONFIGURATION_PLAN_CONTRACT_VERSION",
                "OPERATIONAL_PLAN_CONTRACT_VERSION",
            }
        }
        self.assertEqual(
            constants,
            {
                "CONFIGURATION_PLAN_CONTRACT_VERSION": 2,
                "OPERATIONAL_PLAN_CONTRACT_VERSION": 3,
            },
        )
        self.assertEqual(
            {item.value for item in ChangeOperation},
            {
                "create_automation",
                "update_automation",
                "configuration_plan",
                "create_full_backup",
                "controlled_reload",
                "restart_addon",
                "restart_home_assistant",
            },
        )

    def test_current_provider_routing_does_not_import_f3_operational_modules(self):
        for relative in (
            "ha_mcp_engineering/providers/operational_backup.py",
            "ha_mcp_engineering/providers/operational_lifecycle.py",
            "ha_mcp_engineering/governance/operational.py",
            "ha_mcp_engineering/governance/operational_lifecycle.py",
        ):
            text = (BETA / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertNotIn("f3.operational", text)

    def test_stable_v1_has_no_f3_operational_import(self):
        stable = ROOT / "hass_mcp_admin"
        for path in stable.rglob("*.py"):
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn(
                    "f3.operational", path.read_text(encoding="utf-8")
                )


class ExactReleaseAndMigrationEquivalenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_exact_7_14_2_and_8_0_0_provider_models_are_preserved(self):
        expected = {
            "7.14.2": (
                "ha-mcp-lifecycle-addon-text-json-v1",
                "mcp-text-content-v1",
            ),
            "8.0.0": (
                "ha-mcp-lifecycle-addon-structured-content-v1",
                "mcp-direct-structured-content-v1",
            ),
        }
        for version, response_contract in expected.items():
            for operation in SUPPORTED_OPERATIONS:
                with self.subTest(version=version, operation=operation):
                    context = make_context(
                        self.root / version / operation,
                        operation,
                        version=version,
                    )
                    prepared = await prepare_context(context)
                    evidence = prepared.provider_evidence
                    self.assertEqual(evidence["server_version"], version)
                    self.assertEqual(evidence["protocol_version"], "2025-03-26")
                    self.assertEqual(
                        evidence["aggregate_fingerprint_model"],
                        "ha-mcp-reviewed-normalized-catalog-v1",
                    )
                    if operation != CREATE_FULL_BACKUP:
                        self.assertEqual(
                            (
                                evidence["lifecycle_addon_response_contract_model"],
                                evidence["lifecycle_addon_response_envelope_variant"],
                            ),
                            response_contract,
                        )

    async def test_migration_equivalence_preserves_policy_risk_effects_and_limits(self):
        for operation in SUPPORTED_OPERATIONS:
            context = make_context(self.root / operation, operation)
            prepared = await prepare_context(context)
            plan = context.plan
            operational = plan.operational
            assert operational is not None
            self.assertEqual(prepared.plan_id, plan.plan_id)
            self.assertEqual(prepared.plan_hash, PLAN_HASH)
            self.assertEqual(
                prepared.plan_contract_version,
                OPERATIONAL_PLAN_CONTRACT_VERSION,
            )
            self.assertEqual(prepared.public_task_id, PUBLIC_TASK_ID)
            self.assertEqual(prepared.child_execution_id, TASK_ID)
            self.assertEqual(prepared.target.target_type, plan.target_type)
            self.assertEqual(prepared.target.target_id, plan.target_id)
            self.assertEqual(prepared.operation, plan.operation.value)
            self.assertEqual(
                prepared.capability_id, CAPABILITY_IDENTITIES[operation]
            )
            self.assertEqual(
                prepared.current_state_fingerprint,
                plan.current_state_fingerprint,
            )
            self.assertEqual(
                prepared.normalized_proposed_hash,
                plan.proposed_config_hash,
            )
            self.assertEqual(
                prepared.policy_decision_hash,
                plan.policy_decision.policy_decision_hash,
            )
            self.assertEqual(prepared.policy_class, plan.policy_decision.policy_class.value)
            self.assertEqual(prepared.risk_delta, plan.policy_decision.risk_delta.value)
            self.assertEqual(
                prepared.physical_consequence,
                plan.policy_decision.physical_consequence.value,
            )
            self.assertEqual(prepared.risk_level, plan.risk.level.value)
            self.assertEqual(prepared.expected_effect_descriptions, tuple(operational.expected_effects))
            self.assertEqual(prepared.warnings, tuple(plan.warnings))
            self.assertEqual(prepared.limitations, tuple(operational.limitations))
            self.assertEqual(prepared.provider_id, operational.provider)
            self.assertEqual(
                prepared.provider_contract_model,
                OPERATIONAL_PROVIDER_CONTRACT_MODEL,
            )
            self.assertEqual(
                prepared.provider_evidence,
                operational.provider_capability_evidence,
            )
            self.assertEqual(prepared.baseline, operational.baseline)
            self.assertEqual(
                prepared.verification_contract_json,
                canonical_json(operational.verification_contract),
            )
            self.assertEqual(
                prepared.provider_arguments_hash,
                stable_hash(prepared.provider_arguments_json),
            )
            self.assertEqual(prepared.provider_operation, {
                CREATE_FULL_BACKUP: "ha_manage_backup",
                CONTROLLED_RELOAD: "ha_reload_core",
                RESTART_ADDON: "ha_manage_addon",
                RESTART_HOME_ASSISTANT: "ha_restart",
            }[operation])
            requests = context.adapter.lock_requests(prepared)
            lock_projection = [
                {
                    "key": request.key,
                    "scopes": [scope.value for scope in request.scopes],
                    "mode": request.mode.value,
                    "reason_codes": list(request.reason_codes),
                }
                for request in requests
            ]
            self.assertEqual(
                lock_projection,
                sorted(
                    lock_projection,
                    key=lambda item: item["key"].encode("utf-8"),
                ),
            )
            self.assertEqual(len(prepared.selective_hold_keys), 1)
            self.assertIn(
                prepared.selective_hold_keys[0],
                {item["key"] for item in lock_projection},
            )
            self.assertGreaterEqual(prepared.evidence_deadline_seconds, 60)
            self.assertFalse(prepared.rollback_available)

    async def test_prepared_authority_payload_binds_every_execution_surface(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        payload = operational_prepared_authority_payload(prepared)
        self.assertEqual(
            payload["authority_model"],
            OPERATIONAL_PREPARED_AUTHORITY_MODEL,
        )
        self.assertEqual(
            set(payload),
            {
                "authority_model",
                "adapter_contract",
                "adapter_id",
                "capability_id",
                "operation",
                "target",
                "plan",
                "state",
                "authorization",
                "provider",
                "requested_name",
                "baseline",
                "reporting",
                "verification",
                "rollback_available",
                "recovery",
            },
        )
        self.assertEqual(
            set(payload["plan"]),
            {
                "plan_id",
                "plan_hash",
                "plan_contract_version",
                "plan_expires_at",
                "public_task_id",
                "child_execution_id",
            },
        )
        self.assertEqual(
            set(payload["authorization"]),
            {
                "risk_level",
                "policy_class",
                "risk_delta",
                "physical_consequence",
                "policy_decision_hash",
                "approval_bundle_hash",
            },
        )
        self.assertEqual(
            set(payload["provider"]),
            {
                "provider_id",
                "provider_contract",
                "provider_operation",
                "provider_arguments",
                "provider_arguments_hash",
                "provider_evidence",
                "authoritative_provider_slug",
                "provider_identity_evidence_hash",
            },
        )
        self.assertEqual(
            set(payload["verification"]),
            {"model", "contract", "contract_hash"},
        )
        self.assertEqual(
            set(payload["recovery"]),
            {
                "evidence_deadline_class",
                "evidence_deadline_seconds",
                "selective_hold_keys",
            },
        )
        self.assertEqual(
            recompute_operational_prepared_hash(prepared),
            prepared.prepared_operation_hash,
        )

    def test_normalized_outcomes_map_to_existing_task_states(self):
        self.assertEqual(
            NORMALIZED_OUTCOME_TO_TASK_STATE,
            {
                "preflight_rejected": "failed_pre_dispatch",
                "lock_conflict": "failed_pre_dispatch",
                "provider_unavailable_pre_dispatch": "failed_pre_dispatch",
                "dispatch_failed_confirmed": "failed_post_dispatch",
                "dispatch_indeterminate": "observing",
                "observing": "observing",
                "verification_mismatch": "failed_post_dispatch",
                "succeeded_verified": "succeeded_verified",
                "failed_pre_dispatch": "failed_pre_dispatch",
                "failed_post_dispatch": "failed_post_dispatch",
                "manual_review_required": "manual_review_required",
                "cancelled_pre_dispatch": "cancelled_pre_dispatch",
            },
        )

    def test_capability_and_operation_sets_are_bijective(self):
        self.assertEqual(set(CAPABILITY_IDENTITIES), set(SUPPORTED_OPERATIONS))
        self.assertEqual(len(set(CAPABILITY_IDENTITIES.values())), 4)


class OperationalObservabilityTests(unittest.TestCase):
    def test_every_required_counter_is_closed_and_zero_initialized(self):
        metrics = OperationalMetrics()
        snapshot = metrics.snapshot()
        self.assertEqual(set(snapshot), set(SUPPORTED_OPERATIONS))
        for operation, counters in snapshot.items():
            with self.subTest(operation=operation):
                self.assertEqual(
                    set(counters),
                    COMMON_COUNTERS | OPERATION_COUNTERS[operation],
                )
                self.assertTrue(all(value == 0 for value in counters.values()))
                self.assertEqual(counters["fallbacks"], 0)

    def test_metrics_reject_unknown_operations_counters_and_negative_values(self):
        metrics = OperationalMetrics()
        with self.assertRaises(ValueError):
            metrics.increment("unknown", "preparations")
        with self.assertRaises(ValueError):
            metrics.increment(CREATE_FULL_BACKUP, "raw_provider_payload")
        with self.assertRaises(ValueError):
            metrics.increment(CREATE_FULL_BACKUP, "preparations", -1)

    def test_events_accept_only_bounded_classifications(self):
        events = OperationalEventRecorder(max_events=2)
        events.emit(
            {
                "event_type": "operation_prepared",
                "operation": CREATE_FULL_BACKUP,
                "capability_id": CAPABILITY_IDENTITIES[CREATE_FULL_BACKUP],
                "task_id": "task-1",
                "plan_id": "plan-1",
                "target_type": "backup",
            }
        )
        self.assertEqual(len(events.snapshot()), 1)
        for unsafe in (
            {"raw_provider_payload": "secret"},
            {"event_type": "contains spaces and arbitrary provider text"},
            {"dispatch_count": -1},
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    events.emit(unsafe)

    def test_events_are_bounded_by_count(self):
        events = OperationalEventRecorder(max_events=2)
        for index in range(3):
            events.emit(
                {
                    "event_type": "operation_observed",
                    "operation": CREATE_FULL_BACKUP,
                    "observation_count": index,
                }
            )
        snapshot = events.snapshot()
        self.assertEqual(len(snapshot), 2)
        self.assertEqual(snapshot[0]["observation_count"], 1)


if __name__ == "__main__":
    unittest.main()
