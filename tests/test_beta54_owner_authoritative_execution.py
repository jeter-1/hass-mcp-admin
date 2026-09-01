"""Beta 54 owner-authoritative exact-execution acceptance."""

from __future__ import annotations

import copy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
    PRODUCTION_LOCK_TIMING,
)
from ha_mcp_engineering.f3_runtime.repository import (  # noqa: E402
    canonical_hash,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HELPER_DEPENDENCY_RISK_MODEL,
    HelperDependencyRiskService,
)
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402
from ha_mcp_engineering.governance import policy as policy_module  # noqa: E402
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    POLICY_VERSION,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from tests import test_beta37_exact_helper_state as beta37  # noqa: E402
from tests import test_beta50_helper_production_target_scope as beta50  # noqa: E402
from tests import test_beta53_helper_registry_deduplication as beta53  # noqa: E402
from tests import test_dev14_configuration_plans as dev14  # noqa: E402
from tests import test_f3_runtime_integration as f3tests  # noqa: E402
from tests import test_governance as governance_tests  # noqa: E402


ACCEPTANCE = ROOT / "docs" / "V2_2_0_BETA54_ACCEPTANCE.md"
RELEASE_NOTES = ROOT / "docs" / "V2_2_0_BETA54_RELEASE_NOTES.md"


class _FrozenDependencyRiskReader:
    def __init__(self, evidence: dict) -> None:
        self.evidence = copy.deepcopy(evidence)
        self.read_count = 0
        self.fenced_read_count = 0

    async def __call__(
        self,
        _entity_id: str,
        *,
        refresh: bool = True,
        fenced: bool = False,
    ) -> dict:
        if refresh is not True:
            raise AssertionError("dependency evidence must be refreshed")
        self.read_count += 1
        self.fenced_read_count += int(fenced)
        return copy.deepcopy(self.evidence)


def _beta53_policy(plan):
    return policy_module._evaluate_change_policy_version(
        plan,
        policy_version="f2-v1",
        owner_authoritative=False,
    )


def _beta53_helper_plan_is_actionable(plan) -> bool:
    binding = ChangeGovernanceService._helper_dependency_binding(plan)
    return bool(
        binding is not None
        and binding.get("model") == "helper-dependency-risk-v12"
        and binding.get("execution_eligible") is True
        and plan.risk.apply_allowed
    )


class Beta54HistoricalHelperRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """Post-intent Beta 53 authority remains observation-only after expiry."""

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.clock = beta37.Clock()
        self.helper = beta37.FakeHelperStateGateway()
        self.dependency = beta37.FakeDependencyRiskReader(
            self.helper.entity_id
        )
        self.dependency.model = "helper-dependency-risk-v12"
        root = Path(self.temp.name)
        self.service = ChangeGovernanceService(
            ChangePlanRepository(root / "plans"),
            beta37.UnusedLegacyGateway(),
            AuditLogger(
                str(root / "audit.jsonl"),
                "beta54-historical-recovery-secret",
            ),
            now=self.clock,
            helper_state_gateway=self.helper,
            helper_dependency_risk_reader=self.dependency,
        )
        self.telemetry, self.context = begin_request(
            "beta54-historical-recovery"
        )
        self.telemetry.caller_id = "beta54-historical-requester"
        self.root = root
        self.runtime = self._runtime()
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def asyncTearDown(self) -> None:
        end_request(self.context)
        self.temp.cleanup()

    def _runtime(self) -> F3RuntimeIntegration:
        return F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=beta37.UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            helper_state_gateway=self.helper,
            provider_identity_reader=beta37.forbidden_upstream_identity,
            retention_days=90,
        )

    async def _grant(self, created: dict) -> None:
        plan = created["plan"]
        pending = self.service.approve(
            plan["plan_id"], plan["plan_hash"]
        )
        _review, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=(
                "home_assistant_admin_ingress:beta54-historical-owner"
            ),
        )

    def _beta53_authority(self):
        """Generate the durable record through the v12/f2-v1 writer path."""

        execution_models = frozenset(
            {"helper-dependency-risk-v12"}
        )
        return (
            patch(
                "ha_mcp_engineering.governance.policy."
                "evaluate_change_policy",
                _beta53_policy,
            ),
            patch(
                "ha_mcp_engineering.governance.service.POLICY_VERSION",
                "f2-v1",
            ),
            patch(
                "ha_mcp_engineering.governance.service."
                "evaluate_change_policy",
                _beta53_policy,
            ),
            patch(
                "ha_mcp_engineering.f3_runtime.runtime.POLICY_VERSION",
                "f2-v1",
            ),
            patch(
                "ha_mcp_engineering.governance.service."
                "HELPER_DEPENDENCY_RISK_EXECUTION_MODELS",
                execution_models,
            ),
            patch.object(
                ChangeGovernanceService,
                "_helper_dependency_plan_is_actionable",
                new=staticmethod(_beta53_helper_plan_is_actionable),
            ),
            patch(
                "ha_mcp_engineering.f3.operational_locks."
                "HELPER_DEPENDENCY_RISK_EXECUTION_MODELS",
                execution_models,
            ),
            patch(
                "ha_mcp_engineering.f3_runtime.runtime."
                "PRODUCTION_LOCK_TIMING",
                replace(
                    PRODUCTION_LOCK_TIMING,
                    lease_seconds=30,
                    renewal_interval_seconds=5,
                    poll_interval_seconds=0.01,
                ),
            ),
        )

    async def _historical_crash(self, stage: str):
        patches = self._beta53_authority()
        for item in patches:
            item.start()
        try:
            created = await self.service.create_helper_state_plan(
                entity_id=self.helper.entity_id,
                desired_state="on",
                expiration_minutes=5,
            )
            plan = created["plan"]
            self.assertEqual(
                "helper-dependency-risk-v12",
                self.service._load(plan["plan_id"])
                .operational.baseline["dependency_risk"]["model"],
            )
            self.assertEqual(
                "f2-v1", plan["policy_decision"]["policy_version"]
            )
            self.clock.advance(seconds=299)
            await self._grant(created)
            if stage == "pre_intent_initialized":
                await self.runtime._initialize(
                    self.service._load(plan["plan_id"]),
                    plan["plan_hash"],
                )
            else:
                def crash(point):
                    if point == stage:
                        raise SystemExit(
                            f"simulated process loss at {stage}"
                        )

                self.runtime.children._fault_hook = crash
                try:
                    with self.assertRaises(SystemExit):
                        await self.service.apply(
                            plan["plan_id"], plan["plan_hash"]
                        )
                finally:
                    self.runtime.children._fault_hook = None
        finally:
            for item in reversed(patches):
                item.stop()

        task = self.service.task_repository.get_for_plan(plan["plan_id"])
        declaration = self.runtime.children.declarations_for_task(
            task.task_id
        )[0]
        return plan, task, declaration

    def _mutate_crashed_record(self, declaration, mutator) -> None:
        record = self.runtime.children.get(declaration["child_id"])
        assert record is not None
        identity = record.execution_identity()
        self.runtime.children.mutate_claimed(
            declaration["child_id"],
            owner_id=identity.owner_id,
            claim_generation=record.claim_generation,
            mutator=mutator,
        )

    def _mutate_declaration_lock_hash(
        self, task, declaration, replacement_hash: str
    ) -> None:
        repository = self.runtime.children
        with repository._exclusive_transaction():
            manifest_path = (
                repository.root / f"{task.task_id}.manifest.json"
            )
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            replacement = copy.deepcopy(declaration)
            replacement["complete_lock_request_hash"] = replacement_hash
            replacement.pop("declaration_hash")
            replacement["declaration_hash"] = canonical_hash(replacement)
            manifest["declarations"] = [replacement]
            manifest.pop("manifest_hash")
            manifest["manifest_hash"] = canonical_hash(manifest)
            envelope = repository._raw_envelope(declaration["child_id"])
            assert envelope is not None
            envelope["declaration"] = replacement
            repository._atomic_write(
                repository._path(declaration["child_id"]), envelope
            )
            repository._atomic_write(manifest_path, manifest)

    def _expire_post_intent(self, plan, declaration) -> None:
        record = self.runtime.children.get(declaration["child_id"])
        assert record is not None and record.dispatch_intent is not None
        self.helper.state = "on"
        self.helper.last_changed = self.clock().isoformat()
        self.clock.advance(seconds=31)
        self.assertGreater(
            self.clock().isoformat(),
            self.service._load_for_projection(plan["plan_id"]).expires_at,
        )
        identity = record.execution_identity()
        self.runtime.children.mutate_claimed(
            declaration["child_id"],
            owner_id=identity.owner_id,
            claim_generation=record.claim_generation,
            mutator=lambda value: setattr(
                value, "claim_expires_at", self.clock().isoformat()
            ),
        )

    async def _assert_binding_tamper_refused(
        self, mutator, *, declaration_tamper: bool = False
    ) -> None:
        plan, task, declaration = await self._historical_crash(
            "after_durable_intent_persistence"
        )
        if declaration_tamper:
            mutator(task, declaration)
        else:
            self._mutate_crashed_record(declaration, mutator)
        self._expire_post_intent(plan, declaration)
        restarted = self._runtime()
        self.service.f3_runtime = restarted
        self.runtime = restarted
        with patch.object(
            self.helper,
            "read_state",
            wraps=self.helper.read_state,
        ) as readback:
            await restarted.recover_once("startup")
        record = restarted.children.get(declaration["child_id"])
        assert record is not None
        self.assertFalse(record.terminal)
        self.assertEqual(1, record.dispatch_count)
        self.assertEqual(0, self.helper.dispatch_count)
        self.assertEqual(0, readback.await_count)
        self.assertEqual(
            "bounded_retry",
            restarted.children.runtime(declaration["child_id"])[
                "reconciliation_result"
            ],
        )

    async def _reconcile(
        self, declaration, *, action: str = "rerun_observation"
    ) -> dict:
        runtime = self.runtime.children.runtime(declaration["child_id"])
        hold_binding = ",".join(
            f"{item['key']}:{item['generation']}"
            for item in runtime["selective_hold_tokens"]
        )
        return await self.runtime.reconcile_child(
            child_id=declaration["child_id"],
            action=action,
            record_generation=runtime["record_generation"],
            prepared_hash=declaration["prepared_operation_hash"],
            hold_generation_binding=hold_binding,
            authorized_principal=(
                "home_assistant_admin_ingress:beta54-reconciler"
            ),
        )

    async def test_expired_v12_f2_v1_post_intent_recovers_by_readback(self):
        plan, _task, declaration = await self._historical_crash(
            "after_durable_intent_persistence"
        )
        before = self.runtime.children.get(declaration["child_id"])
        assert before is not None and before.dispatch_intent is not None
        historical_before = self.service._load_for_projection(plan["plan_id"])
        decision_before = copy.deepcopy(historical_before.policy_decision)
        binding_before = copy.deepcopy(
            historical_before.operational.baseline["dependency_risk"]
        )
        self.assertEqual(1, before.dispatch_count)
        self.assertEqual(0, self.helper.dispatch_count)

        # Simulate the provider having accepted the durable intent before the
        # process was lost.  The test clock crosses plan expiry while the
        # short test lock lease expires and post-dispatch evidence remains
        # within its independent deadline.
        self._expire_post_intent(plan, declaration)

        restarted = self._runtime()
        self.service.f3_runtime = restarted
        self.runtime = restarted
        with patch.object(
            self.helper,
            "read_state",
            wraps=self.helper.read_state,
        ) as readback:
            result = await restarted.recover_once("startup")

        recovered = restarted.children.get(declaration["child_id"])
        assert recovered is not None
        self.assertGreaterEqual(result["active_recovery_transitions"], 1)
        self.assertEqual("succeeded_verified", recovered.normalized_outcome)
        self.assertTrue(recovered.terminal)
        self.assertEqual(1, recovered.dispatch_count)
        self.assertEqual(0, self.helper.dispatch_count)
        self.assertGreaterEqual(readback.await_count, 1)
        historical_after = self.service._load_for_projection(plan["plan_id"])
        self.assertEqual(decision_before, historical_after.policy_decision)
        self.assertEqual(
            binding_before,
            historical_after.operational.baseline["dependency_risk"],
        )

    async def test_historical_readback_rejects_request_id_mismatch(self):
        await self._assert_binding_tamper_refused(
            lambda record: record.dispatch_intent.__setitem__(
                "request_id", "tampered-request"
            )
        )

    async def test_historical_readback_rejects_provider_operation_mismatch(self):
        await self._assert_binding_tamper_refused(
            lambda record: record.dispatch_intent.__setitem__(
                "provider_operation", "tampered_operation"
            )
        )

    async def test_historical_readback_rejects_provider_arguments_hash_mismatch(self):
        await self._assert_binding_tamper_refused(
            lambda record: record.dispatch_intent.__setitem__(
                "provider_arguments_hash", stable_hash("tampered")
            )
        )

    async def test_historical_readback_rejects_declaration_lock_hash_mismatch(self):
        await self._assert_binding_tamper_refused(
            lambda task, declaration: self._mutate_declaration_lock_hash(
                task, declaration, stable_hash("tampered-lock-graph")
            ),
            declaration_tamper=True,
        )

    async def test_historical_readback_rejects_lock_key_mismatch(self):
        def mutate(record):
            record.lock_tokens[0]["key"] = "helper_dependency:tampered"
            record.lock_tokens.sort(key=lambda item: item["key"].encode())
            record.dispatch_intent["lock_tokens"] = copy.deepcopy(
                record.lock_tokens
            )

        await self._assert_binding_tamper_refused(mutate)

    async def test_historical_readback_rejects_lock_mode_mismatch(self):
        def mutate(record):
            record.lock_tokens[0]["mode"] = (
                "exclusive"
                if record.lock_tokens[0]["mode"] == "shared"
                else "shared"
            )
            record.dispatch_intent["lock_tokens"] = copy.deepcopy(
                record.lock_tokens
            )

        await self._assert_binding_tamper_refused(mutate)

    async def test_manual_historical_reconciliation_is_readback_only(self):
        plan, _task, declaration = await self._historical_crash(
            "after_durable_intent_persistence"
        )
        self._expire_post_intent(plan, declaration)
        restarted = self._runtime()
        self.service.f3_runtime = restarted
        self.runtime = restarted
        adapter = restarted.registry.adapter(declaration["capability_id"])
        with (
            patch.object(
                adapter,
                "preflight",
                side_effect=AssertionError("preflight must be unreachable"),
            ),
            patch.object(
                adapter,
                "dispatch",
                side_effect=AssertionError("dispatch must be unreachable"),
            ),
            patch.object(
                self.helper,
                "read_state",
                wraps=self.helper.read_state,
            ) as readback,
        ):
            result = await self._reconcile(declaration)
        record = restarted.children.get(declaration["child_id"])
        assert record is not None
        self.assertEqual("read_only_reconciliation_completed", result["status"])
        self.assertEqual("succeeded_verified", record.normalized_outcome)
        self.assertEqual(1, record.dispatch_count)
        self.assertEqual(0, self.helper.dispatch_count)
        self.assertGreaterEqual(readback.await_count, 1)

    async def test_terminal_historical_reconciliation_is_readback_only(self):
        plan, _task, declaration = await self._historical_crash(
            "after_durable_intent_persistence"
        )
        self._expire_post_intent(plan, declaration)
        restarted = self._runtime()
        self.service.f3_runtime = restarted
        self.runtime = restarted
        await restarted.recover_once("startup")
        record = restarted.children.get(declaration["child_id"])
        assert record is not None and record.terminal
        adapter = restarted.registry.adapter(declaration["capability_id"])
        with (
            patch.object(
                adapter,
                "preflight",
                side_effect=AssertionError("preflight must be unreachable"),
            ),
            patch.object(
                adapter,
                "dispatch",
                side_effect=AssertionError("dispatch must be unreachable"),
            ),
            patch.object(
                self.helper,
                "read_state",
                wraps=self.helper.read_state,
            ) as readback,
        ):
            result = await self._reconcile(declaration)
        after = restarted.children.get(declaration["child_id"])
        assert after is not None
        self.assertEqual("read_only_reconciliation_completed", result["status"])
        self.assertEqual("succeeded_verified", after.normalized_outcome)
        self.assertEqual(1, after.dispatch_count)
        self.assertEqual(0, self.helper.dispatch_count)
        self.assertGreaterEqual(readback.await_count, 1)

    async def test_v12_f2_v1_pre_intent_restart_cannot_prepare_or_dispatch(self):
        plan, _task, declaration = await self._historical_crash(
            "pre_intent_initialized"
        )
        before = self.runtime.children.get(declaration["child_id"])
        self.assertIsNone(before)
        self.assertEqual(0, self.helper.dispatch_count)

        self.clock.advance(seconds=31)
        restarted = self._runtime()
        self.service.f3_runtime = restarted
        self.runtime = restarted
        await restarted.recover_once("startup")

        after = restarted.children.get(declaration["child_id"])
        self.assertIsNone(after)
        self.assertEqual(0, self.helper.dispatch_count)
        self.assertEqual(
            "f2-v1",
            self.service._load_for_projection(plan["plan_id"])
            .policy_decision.policy_version,
        )


class Beta54CapturedHelperAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        capture = json.loads(beta53.REPLAY.read_text(encoding="utf-8"))
        fixture = beta53._transport_fixture(capture)
        self.target = fixture["target_entity_id"]
        self.rest = beta53.CapturedBeta50ReplayRest(fixture)
        self.websocket = beta53.VariantReplayWebSocket(
            fixture,
            self.rest.ids,
            "malformed_relevant",
        )
        index = DependencyIndex(
            DirectHaDependencyProvider(
                self.rest,
                self.websocket,
                concurrency=4,
            )
        )
        _snapshot, rebuilt, _lookup_ms = await index.get(refresh=True)
        self.assertTrue(rebuilt)
        evidence = await HelperDependencyRiskService(index).assess(
            self.target,
            refresh=False,
        )
        self.dependency = _FrozenDependencyRiskReader(evidence)

        self.temp = tempfile.TemporaryDirectory()
        self.clock = beta37.Clock()
        self.helper = beta37.FakeHelperStateGateway()
        self.helper.entity_id = self.target
        root = Path(self.temp.name)
        self.service = ChangeGovernanceService(
            ChangePlanRepository(root / "plans"),
            beta37.UnusedLegacyGateway(),
            AuditLogger(str(root / "audit.jsonl"), "beta54-test-secret"),
            now=self.clock,
            helper_state_gateway=self.helper,
            helper_dependency_risk_reader=self.dependency,
        )
        self.telemetry, self.context = begin_request(
            "beta54-owner-authority"
        )
        self.telemetry.caller_id = "beta54-mcp-requester"
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(root / "plans"),
            configuration_gateway=beta37.UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            helper_state_gateway=self.helper,
            provider_identity_reader=beta37.forbidden_upstream_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def asyncTearDown(self) -> None:
        end_request(self.context)
        self.temp.cleanup()

    async def test_captured_consequence_uncertainty_is_one_step_actionable(self):
        binding = self.dependency.evidence["binding"]
        self.assertEqual("helper-dependency-risk-v13", HELPER_DEPENDENCY_RISK_MODEL)
        self.assertEqual(0, binding["exact_dependency_obligation_count"])
        self.assertEqual(24, binding["opaque_obligation_count"])
        self.assertEqual(2, len(binding["downstream_profiles"]))
        self.assertFalse(binding["coverage_complete"])
        self.assertFalse(binding["consequence_evidence_complete"])
        self.assertTrue(binding["execution_contract_complete"])
        self.assertEqual([], binding["execution_block_reason_codes"])
        self.assertTrue(binding["execution_eligible"])
        lock_projection = binding["dependency_lock_projection"]
        self.assertTrue(lock_projection["exact_helper_dependency"])
        self.assertTrue(
            lock_projection["conservative_helper_dependency"]
        )
        self.assertTrue(lock_projection["custom_template_reload"])
        self.assertEqual(
            2,
            len(lock_projection["automation_resource_ids"]),
        )

        created = await self.service.create_helper_state_plan(
            entity_id=self.target,
            desired_state="on",
        )
        plan = created["plan"]
        decision = plan["policy_decision"]
        self.assertEqual("f2-v2", POLICY_VERSION)
        self.assertEqual(POLICY_VERSION, decision["policy_version"])
        self.assertEqual("high", plan["risk"]["level"])
        self.assertTrue(plan["risk"]["apply_allowed"])
        self.assertEqual("unknown", decision["physical_consequence"])
        self.assertEqual("elevated_admin", decision["policy_class"])
        self.assertEqual(["plan_approval"], decision["required_acknowledgements"])
        self.assertIn(
            "helper_dependency_coverage_failure",
            decision["reason_codes"],
        )
        self.assertNotIn(
            "helper_dependency_evidence_complete",
            decision["reason_codes"],
        )
        self.assertTrue(plan["approval_actionable"])
        self.assertEqual(0, self.helper.dispatch_count)

        pending = self.service.approve(plan["plan_id"], plan["plan_hash"])
        review, csrf = await self.service.issue_external_csrf(
            plan["plan_id"],
            pending["challenge_id"],
        )
        helper_review = review["helper_dependency_review"]
        self.assertTrue(helper_review["execution_contract_complete"])
        self.assertFalse(helper_review["consequence_evidence_complete"])
        self.assertEqual(24, helper_review["opaque_obligation_count"])
        self.assertEqual(
            binding["evidence_fingerprint"],
            helper_review["evidence_fingerprint"],
        )
        granted = await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:beta54-owner",
        )
        self.assertEqual("approved", granted["status"])
        self.assertEqual(0, self.helper.dispatch_count)

        applied = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        declaration = self.runtime.children.declarations_for_task(
            applied["task_id"]
        )[0]
        child = self.runtime.children.get(declaration["child_id"])
        assert child is not None
        lock_keys = {item["key"] for item in child.lock_tokens}
        self.assertIn(f"helper:{self.target}", lock_keys)
        self.assertIn("home_assistant:core", lock_keys)
        self.assertIn("reload:input_boolean", lock_keys)
        self.assertIn("reload:custom_templates", lock_keys)
        self.assertIn(
            f"helper_dependency:{self.target}",
            lock_keys,
        )
        self.assertIn(
            "helper_dependency:input_boolean_dynamic",
            lock_keys,
        )
        self.assertEqual(
            "succeeded_verified",
            applied["task_state"],
            child.to_dict(),
        )
        self.assertEqual(1, self.helper.dispatch_count)
        self.assertGreaterEqual(self.dependency.fenced_read_count, 1)
        repeated = await self.service.apply(
            plan["plan_id"],
            plan["plan_hash"],
        )
        self.assertEqual("already_applied", repeated["status"])
        self.assertEqual(1, self.helper.dispatch_count)
        self.assertFalse(
            any(method != "GET" for method, _path in self.rest.calls)
        )

    async def test_execution_contract_failure_cannot_be_acknowledged(self):
        evidence = copy.deepcopy(self.dependency.evidence)
        binding = evidence["binding"]
        binding["execution_contract_complete"] = False
        binding["execution_eligible"] = False
        binding["execution_block_reason_codes"] = [
            "automation_lock_identity_unavailable"
        ]
        material = dict(binding)
        material.pop("evidence_fingerprint", None)
        binding["evidence_fingerprint"] = stable_hash(material)
        self.service.helper_dependency_risk_reader = (
            _FrozenDependencyRiskReader(evidence)
        )
        self.runtime.operational_adapter.strategies[
            "set_input_boolean_state"
        ].dependency_risk_reader = self.service.helper_dependency_risk_reader

        created = await self.service.create_helper_state_plan(
            entity_id=self.target,
            desired_state="on",
        )
        plan = created["plan"]
        self.assertFalse(plan["risk"]["apply_allowed"])
        self.assertEqual(
            "prohibited",
            plan["policy_decision"]["policy_class"],
        )
        self.assertFalse(plan["approval_actionable"])
        with self.assertRaises(GovernanceError) as caught:
            self.service.approve(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(
            ErrorCode.APPROVAL_SEQUENCE_FAILURE,
            caught.exception.code,
        )
        self.assertEqual(0, self.helper.dispatch_count)

    async def test_already_desired_still_rejects_consequence_evidence_drift(self):
        created = await self.service.create_helper_state_plan(
            entity_id=self.target,
            desired_state="on",
        )
        plan = created["plan"]
        pending = self.service.approve(plan["plan_id"], plan["plan_hash"])
        _review, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:beta54-owner",
        )

        drifted = copy.deepcopy(self.dependency.evidence)
        binding = drifted["binding"]
        binding["consequence_uncertainty_reason_codes"] = sorted(
            {
                *binding["consequence_uncertainty_reason_codes"],
                "post_approval_consequence_change",
            }
        )
        fingerprint_material = dict(binding)
        fingerprint_material.pop("evidence_fingerprint", None)
        binding["evidence_fingerprint"] = stable_hash(fingerprint_material)
        drifted_reader = _FrozenDependencyRiskReader(drifted)
        self.service.helper_dependency_risk_reader = drifted_reader
        self.runtime.operational_adapter.strategies[
            "set_input_boolean_state"
        ].dependency_risk_reader = drifted_reader
        self.helper.set_observed_state("on", self.clock)

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        declaration = self.runtime.children.declarations_for_task(
            result["task_id"]
        )[0]
        child = self.runtime.children.get(declaration["child_id"])
        assert child is not None
        self.assertEqual("failed_pre_dispatch", result["task_state"])
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(0, self.helper.dispatch_count)
        self.assertTrue(
            any(
                "dependency_risk_drift" in event["diagnostic_codes"]
                for event in child.events
            )
        )

    async def test_guest_mode_safety_critical_dependencies_are_actionable(self):
        index = DependencyIndex(
            DirectHaDependencyProvider(
                beta50.SyntheticBeta50Rest(),
                beta50.SyntheticBeta50WebSocket(),
            )
        )
        evidence = await HelperDependencyRiskService(index).assess(
            beta50.CONSEQUENTIAL_TARGET,
            refresh=True,
        )
        binding = evidence["binding"]
        self.assertEqual(7, binding["exact_dependency_obligation_count"])
        self.assertEqual(7, len(binding["downstream_profiles"]))
        self.assertEqual("safety_critical", binding["physical_consequence"])
        self.assertTrue(binding["consequence_evidence_complete"])
        self.assertTrue(binding["execution_contract_complete"])

        reader = _FrozenDependencyRiskReader(evidence)
        self.helper.entity_id = beta50.CONSEQUENTIAL_TARGET
        self.service.helper_dependency_risk_reader = reader
        self.runtime.operational_adapter.strategies[
            "set_input_boolean_state"
        ].dependency_risk_reader = reader
        created = await self.service.create_helper_state_plan(
            entity_id=beta50.CONSEQUENTIAL_TARGET,
            desired_state="on",
        )
        plan = created["plan"]
        self.assertEqual("high", plan["risk"]["level"])
        self.assertEqual(
            "safety_critical",
            plan["policy_decision"]["physical_consequence"],
        )
        self.assertEqual(
            "elevated_admin",
            plan["policy_decision"]["policy_class"],
        )
        self.assertEqual(
            ["plan_approval"],
            plan["policy_decision"]["required_acknowledgements"],
        )
        self.assertIn(
            "helper_dependency_evidence_complete",
            plan["policy_decision"]["reason_codes"],
        )
        self.assertTrue(plan["approval_actionable"])
        self.assertEqual(0, self.helper.dispatch_count)


_CURRENT_VANITY_AUTOMATION = {
    "id": "bathroom_vanity_restart_reconciliation",
    "alias": "Bathroom vanity restart reconciliation",
    "description": "Existing exact restart reconciliation",
    "mode": "single",
    "trigger": [{"platform": "homeassistant", "event": "start"}],
    "condition": [
        {
            "condition": "state",
            "entity_id": "binary_sensor.bathroom_presence",
            "state": "off",
        }
    ],
    "action": [
        {"delay": "00:00:20"},
        {
            "service": "switch.turn_off",
            "target": {"entity_id": "switch.bathroom_vanity"},
        },
    ],
}

_PROPOSED_VANITY_AUTOMATION = {
    **copy.deepcopy(_CURRENT_VANITY_AUTOMATION),
    "description": "Recheck presence after a bounded startup delay",
    "action": [
        {"delay": "00:00:30"},
        {
            "condition": "state",
            "entity_id": "binary_sensor.bathroom_presence",
            "state": "off",
        },
        {
            "service": "switch.turn_off",
            "target": {"entity_id": "switch.bathroom_vanity"},
        },
    ],
}


class Beta54ExactAutomationOwnerAuthorityTests(
    dev14.ConfigurationPlanTestCase
):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.gateway.configs[
            ("automation", "bathroom_vanity_restart_reconciliation")
        ] = copy.deepcopy(_CURRENT_VANITY_AUTOMATION)
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=f3tests._ExactFakeConfigurationGateway(
                self.gateway
            ),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=f3tests._provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def _create_vanity_plan(self, proposed: dict) -> dict:
        return await self.service.create_configuration_plan(
            title="Bathroom vanity restart reconciliation",
            description="Exact existing-automation update",
            operations=[
                {
                    "operation_id": "update_bathroom_vanity_reconciliation",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": "bathroom_vanity_restart_reconciliation",
                    "depends_on": [],
                    "proposed_config": copy.deepcopy(proposed),
                }
            ],
        )

    async def test_direct_consequence_update_uses_one_owner_decision(self):
        created = await self._create_vanity_plan(
            _PROPOSED_VANITY_AUTOMATION
        )
        decision = created["policy_decision"]
        operation = created["operations"][0]
        self.assertEqual("f2-v2", decision["policy_version"])
        self.assertEqual("elevated_admin", decision["policy_class"])
        self.assertEqual("direct", decision["physical_consequence"])
        self.assertEqual(["plan_approval"], decision["required_acknowledgements"])
        self.assertTrue(created["risk"]["apply_allowed"])
        self.assertTrue(created["approval_actionable"])
        self.assertTrue(operation["semantic_projection"]["projection_complete"])
        self.assertEqual(64, len(operation["semantic_projection_hash"]))
        self.assertEqual(0, sum(call[0] == "write" for call in self.gateway.calls))

        pending, review, granted = await self.approve(created)
        self.assertEqual("plan_approval", pending["approval_action"])
        self.assertEqual("approved", granted["status"])
        self.assertFalse(review["same_principal_requirement"])
        self.assertEqual(0, sum(call[0] == "write" for call in self.gateway.calls))

        applied = await self.service.apply(
            created["plan_id"],
            created["plan_hash"],
        )
        self.assertEqual("succeeded_verified", applied["task_state"])
        self.assertEqual(1, sum(call[0] == "write" for call in self.gateway.calls))
        stored = self.gateway.configs[
            ("automation", "bathroom_vanity_restart_reconciliation")
        ]
        self.assertEqual(
            _PROPOSED_VANITY_AUTOMATION["action"],
            stored["action"],
        )
        repeated = await self.service.apply(
            created["plan_id"],
            created["plan_hash"],
        )
        self.assertEqual("already_applied", repeated["status"])
        self.assertEqual(1, sum(call[0] == "write" for call in self.gateway.calls))

    async def test_unresolved_future_effect_is_disclosed_but_actionable(self):
        proposed = copy.deepcopy(_PROPOSED_VANITY_AUTOMATION)
        proposed["action"][-1] = {
            "service": "{{ states('input_text.future_service') }}",
            "target": {"entity_id": "switch.bathroom_vanity"},
        }
        created = await self._create_vanity_plan(proposed)
        decision = created["policy_decision"]
        self.assertEqual("elevated_admin", decision["policy_class"])
        self.assertEqual("unknown", decision["physical_consequence"])
        self.assertEqual(["plan_approval"], decision["required_acknowledgements"])
        self.assertIn(
            "automation_consequence_semantics_incomplete",
            decision["reason_codes"],
        )
        self.assertTrue(created["risk"]["apply_allowed"])
        self.assertTrue(created["approval_actionable"])
        self.assertEqual(0, sum(call[0] == "write" for call in self.gateway.calls))


class Beta54LegacyAutomationAuthorityBoundaryTests(
    governance_tests.GovernanceTestCase
):
    async def test_dynamic_legacy_update_cannot_gain_owner_authority(self):
        proposed = copy.deepcopy(governance_tests.CURRENT)
        proposed["description"] = "Legacy dynamic consequence boundary"
        proposed["action"] = [
            {
                "service": "{{ states('input_text.future_service') }}",
                "target": {"entity_id": "switch.bathroom_vanity"},
            }
        ]

        created = await self.service.create_plan(
            title="Legacy dynamic update",
            description="Contract-v1 must retain fail-closed policy",
            operation="update_automation",
            automation_id="porch",
            proposed_config=proposed,
        )

        persisted = self.repository.get(created["plan_id"])
        assert persisted is not None
        self.assertEqual(1, persisted.contract_version)
        self.assertEqual("prohibited", created["policy_decision"]["policy_class"])
        self.assertFalse(created["approval_actionable"])
        self.assertIsNone(
            self.service.task_repository.get_for_plan(created["plan_id"])
        )
        with self.assertRaises(GovernanceError):
            self.service.approve(created["plan_id"], created["plan_hash"])
        persisted = self.repository.get(created["plan_id"])
        assert persisted is not None
        self.assertIsNone(persisted.approval.challenge_id)
        self.assertEqual(0, self.gateway.write_calls)


class Beta54ReleaseAuthorityTests(unittest.TestCase):
    def test_documents_resolve_exactly_when_staged_or_materialized(self):
        context_path = ROOT / "scripts" / "codex-context.py"
        spec = importlib.util.spec_from_file_location(
            "_beta54_context_authority",
            context_path,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        context = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(context)
        resolution = context.resolve_documents(ROOT, "2.2.0-beta.54")
        self.assertEqual("exact", resolution["resolution_status"])
        self.assertEqual(
            "docs/V2_2_0_BETA54_ACCEPTANCE.md",
            resolution["active_acceptance_document"],
        )
        self.assertEqual(
            "docs/V2_2_0_BETA54_RELEASE_NOTES.md",
            resolution["active_release_notes"],
        )

        marker = ROOT / ".release" / "next-version"
        config = (
            ROOT / "hass_mcp_engineering_beta" / "config.yaml"
        ).read_text(encoding="utf-8")
        if marker.exists():
            self.assertEqual(
                "2.2.0-beta.54",
                marker.read_text(encoding="utf-8").strip(),
            )
            self.assertIn('version: "2.2.0-beta.53"', config)
            self.assertIn(
                "Beta 54 stages",
                ACCEPTANCE.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Engineering remains advertised as 2.2.0-beta.53",
                RELEASE_NOTES.read_text(encoding="utf-8"),
            )
            return

        self.assertIn('version: "2.2.0-beta.54"', config)
        for path in (ACCEPTANCE, RELEASE_NOTES):
            text = path.read_text(encoding="utf-8")
            self.assertIn("Beta 54 is materialized", text)
            self.assertIn("Engineering now advertises 2.2.0-beta.54", text)

    def test_acceptance_binds_new_authority_and_non_actions(self):
        text = ACCEPTANCE.read_text(encoding="utf-8")
        for required in (
            "helper-dependency-risk-v13",
            "f2-v2",
            "execution_contract_complete",
            "consequence_evidence_complete",
            "policy_replan_required",
            "plan_approval",
            "approval authority remains version 3",
            "task schema remains 1",
            "public engineering tools remain 51",
            "provider fallback remains absent",
            "does not independently prohibit",
            "at most one dispatch",
            "does not publish",
        ):
            self.assertIn(required.lower(), text.lower())


if __name__ == "__main__":
    unittest.main()
