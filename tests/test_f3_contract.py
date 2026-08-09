"""F3-0 declaration stability and non-behavioral boundary tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import ast
from collections.abc import Awaitable, Callable
import inspect
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    ApprovalConsumptionRecorder,
    F3_ADAPTER_CONTRACT_MODEL,
    F3_MAX_MUTATING_PROVIDER_INVOCATIONS_PER_OPERATION,
    AdapterCapabilityDescriptor,
    DispatchResult,
    LockMode,
    LockRequest,
    LockScope,
    NormalizedOperationOutcome,
    ObservationResult,
    OperationAdapter,
    OperationAdapterPhase,
    OperationTarget,
    PreflightResult,
    PreparedOperation,
    RecoveryContext,
    VerificationResult,
)
from ha_mcp_engineering.f3.executor import SharedOperationExecutor  # noqa: E402
import f3_contracts.operation_adapter as compatibility_contract  # noqa: E402
import ha_mcp_engineering.f3.contracts as canonical_contract  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ChangeOperation,
)
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    ExecutionTaskState,
    TASK_SCHEMA_VERSION,
)
from ha_mcp_engineering.tools import registered_tools  # noqa: E402
from ha_mcp_engineering.tools.registry import (  # noqa: E402
    get_registered_server,
)


class F3ContractDeclarationTests(unittest.TestCase):
    def test_contract_models_and_enum_values_are_stable(self):
        self.assertEqual(
            F3_ADAPTER_CONTRACT_MODEL,
            "f3-operation-adapter-v1",
        )
        self.assertEqual(
            F3_MAX_MUTATING_PROVIDER_INVOCATIONS_PER_OPERATION,
            1,
        )
        self.assertEqual(
            {value.value for value in OperationAdapterPhase},
            {
                "planning",
                "preflight",
                "dispatch",
                "observation",
                "verification",
                "recovery",
                "rollback",
            },
        )
        self.assertEqual(
            {value.value for value in NormalizedOperationOutcome},
            {
                "preflight_rejected",
                "lock_conflict",
                "provider_unavailable_pre_dispatch",
                "dispatch_failed_confirmed",
                "dispatch_indeterminate",
                "observing",
                "verification_mismatch",
                "succeeded_verified",
                "failed_pre_dispatch",
                "failed_post_dispatch",
                "manual_review_required",
                "cancelled_pre_dispatch",
            },
        )
        self.assertEqual(
            {value.value for value in LockScope}, {"resource", "provider"}
        )
        self.assertEqual(
            {value.value for value in LockMode}, {"shared", "exclusive"}
        )

    def test_adapter_protocol_methods_are_explicit(self):
        methods = {
            name
            for name, value in inspect.getmembers(
                OperationAdapter,
                predicate=inspect.isfunction,
            )
            if not name.startswith("_")
        }
        self.assertEqual(
            methods,
            {
                "prepare",
                "lock_requests",
                "preflight",
                "dispatch",
                "observe",
                "verify",
                "recover",
                "prepare_rollback",
            },
        )
        capabilities = inspect.getattr_static(
            OperationAdapter,
            "capabilities",
        )
        self.assertIsInstance(capabilities, property)
        dispatch = inspect.signature(OperationAdapter.dispatch)
        self.assertIn("before_dispatch", dispatch.parameters)
        self.assertEqual(
            dispatch.parameters["before_dispatch"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        recovery = inspect.signature(OperationAdapter.recover)
        self.assertNotIn("before_dispatch", recovery.parameters)
        self.assertIn("context", recovery.parameters)
        self.assertEqual(
            recovery.parameters["context"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        preflight = inspect.signature(OperationAdapter.preflight)
        self.assertEqual(
            preflight.parameters["acquired_locks"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        rollback = inspect.signature(OperationAdapter.prepare_rollback)
        self.assertEqual(
            rollback.parameters["expected_current_fingerprint"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )

    def test_approval_consumption_callback_is_caller_owned_and_keyword_only(self):
        self.assertEqual(
            ApprovalConsumptionRecorder,
            Callable[[], Awaitable[None]],
        )
        self.assertIn(
            "ApprovalConsumptionRecorder", canonical_contract.__all__
        )
        execute = inspect.signature(SharedOperationExecutor.execute)
        self.assertIn("approval_consumption", execute.parameters)
        self.assertEqual(
            execute.parameters["approval_consumption"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertNotIn(
            "approval_consumption",
            inspect.signature(OperationAdapter.dispatch).parameters,
        )

    def test_declarations_are_frozen_value_objects(self):
        target = OperationTarget("dashboard", "overview")
        lock = LockRequest(
            key="dashboard:overview",
            scopes=(LockScope.RESOURCE,),
            mode=LockMode.EXCLUSIVE,
            reason_codes=("dashboard_target_mutation",),
        )
        capabilities = AdapterCapabilityDescriptor(
            adapter_id="dashboard",
            contract_model=F3_ADAPTER_CONTRACT_MODEL,
            operation_family="dashboard_configuration",
            supported_operations=("update_dashboard",),
            rollback_supported=False,
            readback_recovery_supported=True,
            exact_provider_contract_required=True,
        )
        prepared = PreparedOperation(
            contract_model=F3_ADAPTER_CONTRACT_MODEL,
            adapter_id=capabilities.adapter_id,
            operation=capabilities.supported_operations[0],
            target=target,
            current_state_fingerprint="a" * 64,
            normalized_proposed_hash="b" * 64,
            prepared_operation_hash="e" * 64,
            risk_level="high",
            policy_decision_hash="c" * 64,
            approval_bundle_hash="d" * 64,
            expected_effects=("dashboard_configuration_changed",),
            verification_contract_model="dashboard-exact-reread-v1",
            verification_contract_hash="f" * 64,
            rollback_available=capabilities.rollback_supported,
        )
        recovery = RecoveryContext(
            dispatch_intent_recorded=True,
            provider_invocation_may_have_occurred=True,
            provider_response_received=False,
            prior_observation_attempts=0,
            prior_verification_attempts=0,
            post_dispatch_deadline="2026-08-05T00:00:00+00:00",
        )
        self.assertEqual(lock.key, "dashboard:overview")
        self.assertEqual(lock.scopes, (LockScope.RESOURCE,))
        self.assertEqual(lock.mode, LockMode.EXCLUSIVE)
        self.assertEqual(
            lock.reason_codes,
            ("dashboard_target_mutation",),
        )
        self.assertTrue(recovery.provider_invocation_may_have_occurred)
        with self.assertRaises(FrozenInstanceError):
            prepared.operation = "unexpected"  # type: ignore[misc]

    def test_prepared_and_recovery_fields_are_stable(self):
        expected_fields = {
            OperationTarget: ["target_type", "target_id"],
            LockRequest: ["key", "scopes", "mode", "reason_codes"],
            AdapterCapabilityDescriptor: [
                "adapter_id",
                "contract_model",
                "operation_family",
                "supported_operations",
                "rollback_supported",
                "readback_recovery_supported",
                "exact_provider_contract_required",
            ],
            PreparedOperation: [
                "contract_model",
                "adapter_id",
                "operation",
                "target",
                "current_state_fingerprint",
                "normalized_proposed_hash",
                "prepared_operation_hash",
                "risk_level",
                "policy_decision_hash",
                "approval_bundle_hash",
                "expected_effects",
                "verification_contract_model",
                "verification_contract_hash",
                "rollback_available",
            ],
            PreflightResult: [
                "eligible",
                "outcome",
                "confirmed_target",
                "observed_state_fingerprint",
                "provider_contract",
                "provider_operation",
                "provider_arguments_hash",
                "evidence_hash",
                "diagnostic_codes",
                "mismatch_fields",
            ],
            DispatchResult: [
                "outcome",
                "dispatch_intent_recorded",
                "mutating_invocation_count",
                "may_have_dispatched",
                "provider_response_received",
                "provider_operation_id",
                "response_evidence_hash",
                "diagnostic_codes",
            ],
            ObservationResult: [
                "outcome",
                "attempt_count",
                "observation_complete",
                "provider_reachable",
                "target_reachable",
                "readback_state_fingerprint",
                "intended_result_observed",
                "mismatch_fields",
                "evidence_hash",
                "diagnostic_codes",
            ],
            VerificationResult: [
                "outcome",
                "attempt_count",
                "verified",
                "resulting_state_fingerprint",
                "mismatch_fields",
                "evidence_hash",
                "manual_review_reason_code",
            ],
            RecoveryContext: [
                "dispatch_intent_recorded",
                "provider_invocation_may_have_occurred",
                "provider_response_received",
                "prior_observation_attempts",
                "prior_verification_attempts",
                "post_dispatch_deadline",
            ],
        }
        for model, expected in expected_fields.items():
            with self.subTest(model=model.__name__):
                self.assertEqual([field.name for field in fields(model)], expected)
                self.assertTrue(model.__dataclass_params__.frozen)

    def test_persisted_vocabulary_and_public_registration_are_unchanged(self):
        self.assertEqual(TASK_SCHEMA_VERSION, 1)
        self.assertEqual(
            {value.value for value in ChangeOperation},
            {
                "create_automation",
                "update_automation",
                "configuration_plan",
                "update_dashboard",
                "create_full_backup",
                "controlled_reload",
                "restart_addon",
                "restart_home_assistant",
            },
        )
        self.assertEqual(
            {value.value for value in ExecutionTaskState},
            {
                "created",
                "preflight",
                "dispatching",
                "observing",
                "verifying",
                "succeeded_verified",
                "failed_pre_dispatch",
                "failed_post_dispatch",
                "manual_review_required",
                "cancelled_pre_dispatch",
                "waiting_for_lock",
                "compensating",
                "partial_application",
                "compensated",
                "superseded",
            },
        )
        self.assertEqual(len(registered_tools(get_registered_server())), 50)

    def test_canonical_contract_has_only_standard_library_imports(self):
        contract = (
            BETA_DIR / "ha_mcp_engineering" / "f3" / "contracts.py"
        )
        tree = ast.parse(contract.read_text(encoding="utf-8"))
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        relative_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level
        ]
        self.assertEqual(relative_imports, [])
        self.assertLessEqual(
            imported_roots,
            set(sys.stdlib_module_names) | {"__future__"},
        )

    def test_repository_root_facade_exports_canonical_objects_by_identity(self):
        self.assertEqual(
            compatibility_contract.__all__, canonical_contract.__all__
        )
        for name in canonical_contract.__all__:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_contract, name),
                    getattr(canonical_contract, name),
                )

    def test_contract_model_has_one_runtime_definition(self):
        models_source = (
            BETA_DIR / "ha_mcp_engineering" / "f3" / "models.py"
        ).read_text(encoding="utf-8")
        facade_source = (
            ROOT / "f3_contracts" / "operation_adapter.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            'F3_ADAPTER_CONTRACT_MODEL = "f3-operation-adapter-v1"',
            models_source,
        )
        self.assertNotIn("class OperationAdapter", facade_source)
        self.assertNotIn("class PreparedOperation", facade_source)
        definitions = []
        for path in ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name in {
                    "OperationAdapter",
                    "PreparedOperation",
                }:
                    definitions.append((path.relative_to(ROOT), node.name))
        self.assertEqual(
            definitions,
            [
                (
                    Path(
                        "hass_mcp_engineering_beta/ha_mcp_engineering/"
                        "f3/contracts.py"
                    ),
                    "PreparedOperation",
                ),
                (
                    Path(
                        "hass_mcp_engineering_beta/ha_mcp_engineering/"
                        "f3/contracts.py"
                    ),
                    "OperationAdapter",
                ),
            ],
        )

    def test_runtime_does_not_import_the_repository_root_facade(self):
        runtime = BETA_DIR / "ha_mcp_engineering"
        for path in sorted(runtime.rglob("*.py")):
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn(
                    "f3_contracts",
                    path.read_text(encoding="utf-8"),
                )

    def test_runtime_inert_f3_configuration_uses_canonical_contract(self):
        runtime = BETA_DIR / "ha_mcp_engineering"
        importers = {
            path.relative_to(runtime).as_posix()
            for path in sorted(runtime.rglob("*.py"))
            if "ha_mcp_engineering.f3.contracts"
            in path.read_text(encoding="utf-8")
        }
        self.assertGreaterEqual(
            importers,
            {
                "f3_configuration/adapter.py",
                "f3_configuration/locks.py",
                "f3_configuration/models.py",
                "f3_configuration/outcomes.py",
                "f3_configuration/sequence.py",
                "f3_configuration/strategies.py",
            },
        )

    def test_required_contract_documents_have_distinct_boundaries(self):
        documents = {
            "F3_CURRENT_STATE_INVENTORY.md": [
                "## Configuration adapters",
                "## Operational adapters",
                "## Current lock implementation",
                "## Dashboard reads and reviewed write evidence",
            ],
            "F3_DASHBOARD_WRITE_CONTRACT.md": [
                "## Required completion boundary",
                "## Transformation representation",
                "## Exact provider evidence boundary",
                "## Explicit exclusions",
            ],
            "F3_PARALLEL_DEVELOPMENT_PLAN.md": [
                "## F3-A — adapter and lock core",
                "## F3-B — canonical contracts and governed dashboard planning",
                "## F3-C1 — configuration-adapter conformance",
                "## F3-C2 — operational-adapter conformance",
                "## F3-D — recovery, observability, and acceptance",
            ],
            "F3_COMPLETION_ACCEPTANCE.md": [
                "## Shared adapter execution",
                "## Governed dashboard update",
                "## Exact `ha-mcp` 7.14.2 compatibility",
                "## Exact `ha-mcp` 8.0.0 compatibility",
            ],
        }
        for name, headings in documents.items():
            text = (ROOT / "docs" / name).read_text(encoding="utf-8")
            with self.subTest(document=name):
                for heading in headings:
                    self.assertIn(heading, text)
        adr = (
            ROOT
            / "docs"
            / "architecture"
            / "ADR-013-F3-OPERATION-ADAPTER-AND-LOCK-CONTRACT.md"
        ).read_text(encoding="utf-8")
        self.assertIn("## Decision", adr)
        self.assertIn("### Dispatch boundary", adr)
        self.assertIn("### Lock model", adr)


if __name__ == "__main__":
    unittest.main()
