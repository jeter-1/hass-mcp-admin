"""Beta 54 owner-authoritative exact-execution acceptance."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HELPER_DEPENDENCY_RISK_MODEL,
    HelperDependencyRiskService,
)
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402
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


class Beta54ReleaseAuthorityTests(unittest.TestCase):
    def test_staged_documents_resolve_exactly_without_advertising_beta54(self):
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
            "does not materialize",
        ):
            self.assertIn(required.lower(), text.lower())


if __name__ == "__main__":
    unittest.main()
