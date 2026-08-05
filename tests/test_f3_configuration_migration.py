"""Migration-equivalence evidence for the runtime-inert F3-C1 adapters."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.clients.rest import ExpectedHttpStatus
from ha_mcp_engineering.f3_configuration.adapter import (
    ConfigurationOperationAdapter,
)
from ha_mcp_engineering.f3_configuration.gateway import (
    ExistingConfigurationGatewayBridge,
)
from ha_mcp_engineering.f3_configuration.locks import lock_set_hash
from ha_mcp_engineering.f3_configuration.migration import (
    proposal_from_configuration_operation,
)
from ha_mcp_engineering.governance.models import (
    ApprovalPolicyClass,
    ChangeOperation,
    ChangePlan,
    ChangePolicyDecision,
    ChangeRiskAssessment,
    ChangeTarget,
    ConfigurationOperation,
    PhysicalConsequence,
    PlanStatus,
    RiskDelta,
    RiskLevel,
)
from ha_mcp_engineering.governance.normalize import stable_hash
from ha_mcp_engineering.governance.resources import (
    ConfigurationResourceGateway,
    normalize_resource_config,
    resource_fingerprint,
    validate_resource,
)


AUTOMATION = {
    "alias": "F3 inert automation",
    "trigger": [{"platform": "event", "event_type": "f3_inert_event"}],
    "condition": [],
    "action": [{"service": "notify.f3_inert_sink"}],
    "mode": "single",
}
SCRIPT = {
    "alias": "F3 inert script",
    "sequence": [{"service": "notify.f3_inert_sink"}],
    "mode": "single",
}
INPUT_BOOLEAN = {
    "name": "F3 Inert Toggle",
    "icon": "mdi:toggle-switch",
    "initial": False,
}
INPUT_NUMBER = {
    "name": "F3 Inert Level",
    "min": 0,
    "max": 10,
    "step": 0.5,
    "initial": 2,
    "mode": "slider",
}

CASES = (
    ("automation", "f3_inert_automation", AUTOMATION),
    ("script", "f3_inert_script", SCRIPT),
    ("input_boolean", "input_boolean.f3_inert_toggle", INPUT_BOOLEAN),
    ("input_number", "input_number.f3_inert_level", INPUT_NUMBER),
)


class RecordingRestClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def request(
        self,
        method: str,
        path: str,
        body=None,
        raw: bool = False,
        expected_statuses: frozenset[int] = frozenset(),
    ):
        del raw, expected_statuses
        self.calls.append({"method": method, "path": path, "body": body})
        if method == "GET" and path.startswith("/states/"):
            return ExpectedHttpStatus(404)
        return {"result": "ok"}


class RecordingWebSocketClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def command(self, command: dict[str, object]):
        self.calls.append(copy.deepcopy(command))
        operation = str(command.get("type", ""))
        if operation.endswith("/list"):
            return []
        resource_type, action = operation.split("/", 1)
        if action == "update":
            object_id = command[f"{resource_type}_id"]
        else:
            object_id = (
                "f3_inert_toggle"
                if resource_type == "input_boolean"
                else "f3_inert_level"
            )
        return {"id": object_id}


def _risk() -> ChangeRiskAssessment:
    return ChangeRiskAssessment(
        level=RiskLevel.LOW,
        reasons=["Synthetic inert configuration fixture"],
        apply_allowed=True,
        evidence=[{"trigger": "synthetic_inert_fixture"}],
        warnings=[],
    )


def _policy() -> ChangePolicyDecision:
    return ChangePolicyDecision(
        policy_version="f2-v1",
        policy_class=ApprovalPolicyClass.STANDARD_ADMIN,
        risk_delta=RiskDelta.LOW,
        physical_consequence=PhysicalConsequence.INDIRECT,
        reason_codes=("supported_configuration_change",),
        required_acknowledgements=(),
        policy_subject_hash="a" * 64,
        policy_decision_hash="b" * 64,
    )


def _operation_and_plan(
    resource_type: str,
    action: str,
    target_id: str,
    proposed: dict[str, object],
) -> tuple[ConfigurationOperation, ChangePlan]:
    current = None if action == "create" else {**copy.deepcopy(proposed), "id": target_id.split(".", 1)[-1]}
    normalized_current = normalize_resource_config(resource_type, current)
    normalized_proposed = normalize_resource_config(resource_type, proposed)
    assert normalized_proposed is not None
    risk = _risk()
    operation = ConfigurationOperation(
        operation_id=f"{action}_{resource_type}",
        order=0,
        depends_on=[],
        resource_type=("helper" if resource_type.startswith("input_") else resource_type),
        action=action,
        target_id=target_id,
        helper_type=(resource_type if resource_type.startswith("input_") else None),
        proposed_config=copy.deepcopy(proposed),
        current_config=current,
        normalized_proposed_config=normalized_proposed,
        normalized_current_config=normalized_current,
        current_state_fingerprint=resource_fingerprint(resource_type, current),
        proposed_config_hash=stable_hash(normalized_proposed),
        normalization_version=1,
        risk=risk,
        validation_results={"valid": True, "errors": []},
    )
    timestamp = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc).isoformat()
    plan = ChangePlan(
        plan_id="f3c1migrationplan",
        plan_version=1,
        created_at=timestamp,
        updated_at=timestamp,
        expires_at="2026-08-04T14:00:00+00:00",
        status=PlanStatus.APPROVED,
        title="Synthetic F3-C1 migration fixture",
        description="Repository-local and inert",
        requested_by="f3-c1-test",
        target=ChangeTarget("configuration_plan", "f3c1migrationplan"),
        operation=ChangeOperation.CONFIGURATION_PLAN,
        proposed_config={},
        current_config=None,
        normalized_proposed_config={},
        normalized_current_config=None,
        current_state_fingerprint=resource_fingerprint(resource_type, None),
        proposed_config_hash=stable_hash({}),
        risk=risk,
        policy_decision=_policy(),
        contract_version=2,
        operations=[operation],
    )
    return operation, plan


class MigrationEquivalenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_eight_paths_preserve_planned_semantics(self):
        for resource_type, target_id, config in CASES:
            for action in ("create", "update"):
                with self.subTest(resource_type=resource_type, action=action):
                    operation, plan = _operation_and_plan(
                        resource_type, action, target_id, config
                    )
                    expected_plan_hash = stable_hash(plan.to_dict())
                    approval_bundle_hash = "c" * 64
                    proposal = proposal_from_configuration_operation(
                        plan,
                        operation,
                        task_id="f3c1migrationtask",
                        plan_hash=expected_plan_hash,
                        approval_bundle_hash=approval_bundle_hash,
                        provider_admitted=True,
                        policy_snapshot_valid=True,
                    )
                    adapter = ConfigurationOperationAdapter(
                        resource_type,
                        action,
                        gateway=object(),
                        now=lambda: datetime(
                            2026, 8, 4, 12, 30, tzinfo=timezone.utc
                        ),
                    )
                    prepared = await adapter.prepare(proposal)

                    self.assertEqual(prepared.plan_id, plan.plan_id)
                    self.assertEqual(prepared.plan_hash, expected_plan_hash)
                    self.assertEqual(
                        prepared.operation_id, operation.operation_id
                    )
                    self.assertEqual(prepared.resource_type, resource_type)
                    self.assertEqual(prepared.action, action)
                    self.assertEqual(prepared.target.target_id, target_id)
                    self.assertEqual(
                        normalize_resource_config(
                            resource_type, prepared.proposed_config()
                        ),
                        operation.normalized_proposed_config,
                    )
                    self.assertEqual(
                        prepared.current_state_fingerprint,
                        operation.current_state_fingerprint,
                    )
                    self.assertEqual(
                        prepared.normalized_proposed_hash,
                        operation.proposed_config_hash,
                    )
                    self.assertEqual(prepared.risk_level, operation.risk.level.value)
                    self.assertEqual(
                        prepared.policy_class,
                        plan.policy_decision.policy_class.value,
                    )
                    self.assertEqual(
                        prepared.policy_decision_hash,
                        plan.policy_decision.policy_decision_hash,
                    )
                    self.assertEqual(
                        prepared.approval_bundle_hash,
                        approval_bundle_hash,
                    )
                    self.assertEqual(
                        prepared.risk_evidence_hash,
                        proposal.risk_evidence_hash,
                    )
                    self.assertEqual(
                        prepared.expected_effects,
                        (
                            "configuration_resource_created"
                            if action == "create"
                            else "configuration_resource_updated",
                        ),
                    )
                    self.assertEqual(
                        prepared.provider_descriptor.arguments_hash,
                        adapter.strategy.provider_descriptor(
                            target_id, config
                        ).arguments_hash,
                    )
                    self.assertEqual(
                        prepared.provider_descriptor.operation,
                        adapter.strategy.provider_descriptor(
                            target_id, config
                        ).operation,
                    )
                    self.assertEqual(
                        adapter.capabilities.validation_contract,
                        "existing_configuration_validation_v1",
                    )
                    self.assertEqual(
                        prepared.verification_contract_model,
                        adapter.capabilities.verification_contract,
                    )
                    self.assertEqual(
                        len(lock_set_hash(adapter.lock_requests(prepared))),
                        64,
                    )
                    valid, errors, _warnings = validate_resource(
                        resource_type, target_id, config
                    )
                    self.assertTrue(valid, errors)
                    self.assertEqual(operation.validation_results["valid"], valid)
                    self.assertFalse(prepared.rollback_available)

    async def test_provider_descriptors_match_existing_gateway_mutations(self):
        for resource_type, target_id, config in CASES:
            for action in ("create", "update"):
                with self.subTest(resource_type=resource_type, action=action):
                    operation, plan = _operation_and_plan(
                        resource_type, action, target_id, config
                    )
                    proposal = proposal_from_configuration_operation(
                        plan,
                        operation,
                        task_id="f3c1providertask",
                        plan_hash=stable_hash(plan.to_dict()),
                        approval_bundle_hash="c" * 64,
                        provider_admitted=True,
                        policy_snapshot_valid=True,
                    )
                    adapter = ConfigurationOperationAdapter(
                        resource_type, action, gateway=object()
                    )
                    prepared = await adapter.prepare(proposal)

                    rest = RecordingRestClient()
                    websocket = RecordingWebSocketClient()
                    gateway = ConfigurationResourceGateway(rest, websocket)
                    bridge = ExistingConfigurationGatewayBridge(gateway)
                    await bridge.write(action, resource_type, target_id, config)

                    if resource_type in {"automation", "script"}:
                        actual = rest.calls[-1]
                    else:
                        actual = websocket.calls[-1]
                    self.assertEqual(
                        prepared.provider_descriptor.arguments_hash,
                        stable_hash(actual),
                    )
                    self.assertEqual(
                        prepared.provider_descriptor.argument_names,
                        tuple(
                            sorted(actual, key=lambda item: item.encode("utf-8"))
                        ),
                    )

    def test_unknown_migration_resource_fails_closed_like_current_validation(self):
        valid, errors, _warnings = validate_resource(
            "scene", "f3_inert_scene", {}
        )
        self.assertFalse(valid)
        self.assertTrue(errors)
        with self.assertRaises(ValueError):
            ConfigurationOperationAdapter("scene", "create", gateway=object())

    def test_incomplete_or_nonmember_historical_projection_requires_new_plan(self):
        operation, plan = _operation_and_plan(
            "automation", "update", "f3_inert_automation", AUTOMATION
        )
        kwargs = {
            "task_id": "f3c1incomplete",
            "plan_hash": stable_hash(plan.to_dict()),
            "approval_bundle_hash": "c" * 64,
            "provider_admitted": True,
            "policy_snapshot_valid": True,
        }
        copied_operation = copy.deepcopy(operation)
        with self.assertRaisesRegex(ValueError, "exact plan member"):
            proposal_from_configuration_operation(
                plan, copied_operation, **kwargs
            )
        plan.policy_decision = None
        with self.assertRaisesRegex(ValueError, "no F2 policy decision"):
            proposal_from_configuration_operation(plan, operation, **kwargs)


if __name__ == "__main__":
    unittest.main()
