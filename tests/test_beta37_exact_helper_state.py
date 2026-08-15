"""HAMCP-089 exact governed input-boolean runtime-action acceptance."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.f3_configuration.gateway import (  # noqa: E402
    ExistingConfigurationGatewayBridge,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
)
from ha_mcp_engineering.governance.helper_state import (  # noqa: E402
    HELPER_STATE_PROVIDER,
    HelperStateGateway,
    HelperStateGatewayError,
    helper_state_provider_evidence,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    PlanStatus,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationResourceGateway,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int = 1) -> None:
        self.value += timedelta(seconds=seconds)


class UnusedLegacyGateway:
    async def get(self, *_args):
        raise AssertionError("legacy configuration gateway must not be used")

    async def write(self, *_args):
        raise AssertionError("legacy configuration gateway must not be used")

    async def validate(self):
        raise AssertionError("legacy configuration gateway must not be used")


class AbsentStateRest:
    async def request(self, *_args, **_kwargs):
        from ha_mcp_engineering.clients.rest import ExpectedHttpStatus

        return ExpectedHttpStatus(404)


class UnusedConfigurationGateway(ConfigurationResourceGateway):
    def __init__(self) -> None:
        self.rest_client = AbsentStateRest()
        self.websocket_client = object()

    async def get(self, *_args):
        raise AssertionError("configuration gateway must not be used")

    async def read(self, *_args):
        raise AssertionError("configuration gateway must not be used")

    async def validate_all(self):
        raise AssertionError("configuration gateway must not be used")

    async def write(self, *_args):
        raise AssertionError("configuration gateway must not be used")


class FakeHelperStateGateway:
    def __init__(self) -> None:
        self.entity_id = "input_boolean.beta37_exact_action"
        self.state = "off"
        self.last_changed = "2026-08-13T12:00:00+00:00"
        self.dispatch_count = 0
        self.mode = "success"

    def set_observed_state(self, state: str, clock: Clock) -> None:
        self.state = state
        clock.advance()
        self.last_changed = clock().isoformat()

    async def planning_evidence(self, entity_id: str):
        if entity_id != self.entity_id:
            raise HelperStateGatewayError("entity_not_found")
        return {
            "provider": helper_state_provider_evidence(),
            "baseline": await self.read_state(entity_id),
        }

    async def read_state(self, entity_id: str):
        if entity_id != self.entity_id:
            raise HelperStateGatewayError("entity_not_found")
        return {
            "entity_id": self.entity_id,
            "state": self.state,
            "last_changed": self.last_changed,
        }

    async def set_state(
        self, entity_id: str, desired_state: str, *, before_dispatch
    ):
        if entity_id != self.entity_id or desired_state not in {"on", "off"}:
            raise AssertionError("unreviewed helper-state arguments reached dispatch")
        await before_dispatch()
        self.dispatch_count += 1
        if self.mode == "confirmed_failure":
            raise HelperStateGatewayError(
                "provider_rejected", dispatched=True
            )
        if self.mode != "verification_mismatch":
            self.state = desired_state
            self.last_changed = "2026-08-13T12:00:05+00:00"
        if self.mode == "response_lost":
            raise HelperStateGatewayError(
                "dispatch_indeterminate", dispatched=True
            )
        return type(
            "DispatchResult",
            (),
            {"provider_response_received": True},
        )()


class FakeDependencyRiskReader:
    def __init__(self, entity_id: str) -> None:
        self.entity_id = entity_id
        self.generation = 1
        self.automation_ids: list[str] = []
        self.consequence = "none"
        self.complete = True
        self.effect_revision = "v1"
        self.selector_revision: str | None = None

    async def __call__(self, entity_id: str, *, refresh: bool = True):
        if entity_id != self.entity_id or refresh is not True:
            raise AssertionError("dependency risk was not refreshed exactly")
        resource_ids = [
            automation_id.removeprefix("automation.")
            for automation_id in self.automation_ids
        ]
        profiles = [
            {
                "automation_id": automation_id,
                "automation_resource_id": resource_id,
                "relationships": ["trigger"],
                "physical_consequence": self.consequence,
                "complete": self.complete,
                "truncated": False,
                "action_domains": (
                    ["cover"] if self.consequence != "none" else ["notify"]
                ),
                "services": (
                    ["cover.open_cover"]
                    if self.consequence != "none"
                    else ["notify.notify"]
                ),
                "reason_codes": [
                    "consequential_action_family"
                    if self.consequence != "none"
                    else "proven_benign_action_family"
                ],
                "effect_projection_model": "automation-action-effect-v2",
                "effect_targets": [],
                "effect_data": [],
                "effect_structure_fingerprint": stable_hash(
                    {
                        "automation": automation_id,
                        "structure": self.effect_revision,
                    }
                ),
                "effect_projection_fingerprint": stable_hash(
                    {
                        "automation": automation_id,
                        "effect": self.effect_revision,
                    }
                ),
                "effect_projection_clipped": False,
                "profile_fingerprint": stable_hash(
                    {
                        "automation": automation_id,
                        "profile": self.effect_revision,
                    }
                ),
            }
            for automation_id, resource_id in zip(
                self.automation_ids, resource_ids
            )
        ]
        material = {
            "model": "helper-dependency-risk-v2",
            "entity_id": entity_id,
            "completeness": "complete" if self.complete else "partial",
            "evidence_complete": self.complete,
            "execution_eligible": self.complete,
            "physical_consequence": self.consequence,
            "relevant_downstream_object_ids": list(self.automation_ids),
            "downstream_automation_resource_ids": resource_ids,
            "consequential_downstream_object_ids": (
                list(self.automation_ids)
                if self.consequence in {"direct", "safety_critical"}
                else []
            ),
            "downstream_profiles": profiles,
            "target_relevant_dynamic_reference_count": 0,
            "target_relevant_dynamic_reference_fingerprints": [],
            "unresolved_dynamic_reference_count": 0,
            "selector_classification_fixture": self.selector_revision,
            "truncated": False,
        }
        binding = {
            **material,
            "evidence_fingerprint": stable_hash(material),
        }
        return {
            "binding": binding,
            "provenance": {
                "provider": "dependency_index",
                "completeness": binding["completeness"],
                "generation": self.generation,
                "fingerprint": f"{self.generation:064x}",
                "freshness": "current",
                "fallback": "none",
                "fallback_occurred": False,
            },
        }


async def forbidden_upstream_identity():
    raise AssertionError("direct helper state must not depend on ha-mcp identity")


class ExactHelperStateRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.helper = FakeHelperStateGateway()
        self.dependency = FakeDependencyRiskReader(
            self.helper.entity_id
        )
        root = Path(self.temp.name)
        self.service = ChangeGovernanceService(
            ChangePlanRepository(root / "plans"),
            UnusedLegacyGateway(),
            AuditLogger(str(root / "audit.jsonl"), "synthetic-beta37-secret"),
            now=self.clock,
            helper_state_gateway=self.helper,
            helper_dependency_risk_reader=self.dependency,
        )
        self.telemetry, self.context = begin_request("beta37-helper-state")
        self.telemetry.caller_id = "mcp-requester"
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(root / "plans"),
            configuration_gateway=UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            helper_state_gateway=self.helper,
            provider_identity_reader=forbidden_upstream_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    async def grant(self, created: dict) -> dict:
        plan = created["plan"]
        pending = self.service.approve(plan["plan_id"], plan["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind="apply",
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:beta37",
        )
        return plan

    async def create_and_grant(self, desired_state: str = "on") -> dict:
        created = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id,
            desired_state=desired_state,
        )
        self.assertEqual(created["provider"], HELPER_STATE_PROVIDER)
        self.assertEqual(created["fallback"], "none")
        return await self.grant(created)

    async def test_planning_returns_verified_no_change_without_plan(self):
        self.helper.set_observed_state("on", self.clock)

        result = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id,
            desired_state="on",
        )

        self.assertEqual(result["outcome"], "already_in_desired_state")
        self.assertFalse(result["plan_created"])
        self.assertTrue(result["verified"])
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertEqual(self.service.repository.list(), [])

    async def test_approved_exact_state_dispatches_once_and_verifies(self):
        plan = await self.create_and_grant()

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        child = self.runtime.children.get(
            self.runtime.children.declarations_for_task(result["task_id"])[0][
                "child_id"
            ]
        )

        self.assertEqual(
            result["task_state"], "succeeded_verified", child.to_dict()
        )
        self.assertTrue(result["provider_dispatch_occurred"])
        self.assertEqual(self.helper.dispatch_count, 1)
        self.assertEqual(self.helper.state, "on")
        self.assertEqual(child.dispatch_count, 1)
        self.assertEqual(child.normalized_outcome, "succeeded_verified")
        saved = self.service._load(plan["plan_id"])
        self.assertEqual(saved.status, PlanStatus.APPLIED)
        self.assertEqual(saved.approval.state, ApprovalState.CONSUMED)

        repeated = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(repeated["status"], "already_applied")
        self.assertFalse(repeated["redispatch_performed"])
        self.assertEqual(self.helper.dispatch_count, 1)

    async def test_harmless_helper_plan_uses_standard_low_no_consequence_policy(self):
        created = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id,
            desired_state="on",
        )
        plan = created["plan"]

        self.assertEqual(plan["risk"]["level"], "low")
        self.assertEqual(
            plan["policy_decision"]["policy_class"], "standard_admin"
        )
        self.assertEqual(plan["policy_decision"]["risk_delta"], "low")
        self.assertEqual(
            plan["policy_decision"]["physical_consequence"], "none"
        )
        self.assertTrue(plan["approval_actionable"])
        self.assertEqual(
            plan["next_required_operation"], "approve_change_plan"
        )

    async def test_consequential_dependency_elevates_governance(self):
        self.dependency.automation_ids = ["automation.opens_cover"]
        self.dependency.consequence = "direct"

        created = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id,
            desired_state="on",
        )
        plan = created["plan"]

        self.assertEqual(plan["risk"]["level"], "high")
        self.assertEqual(
            plan["policy_decision"]["policy_class"], "elevated_admin"
        )
        self.assertEqual(
            plan["policy_decision"]["physical_consequence"], "direct"
        )
        self.assertEqual(
            plan["policy_decision"]["required_acknowledgements"],
            ["plan_approval", "elevated_risk_acknowledgement"],
        )
        self.assertTrue(plan["approval_actionable"])
        self.assertEqual(
            plan["next_required_operation"], "approve_change_plan"
        )

    async def test_incomplete_dependency_evidence_is_reviewable_but_not_low(self):
        self.dependency.complete = False

        created = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id,
            desired_state="on",
        )
        plan = created["plan"]

        self.assertEqual(plan["risk"]["level"], "high")
        self.assertFalse(plan["risk"]["apply_allowed"])
        self.assertEqual(
            plan["policy_decision"]["policy_class"], "elevated_admin"
        )
        self.assertEqual(
            plan["policy_decision"]["physical_consequence"], "indirect"
        )
        self.assertFalse(plan["approval_actionable"])
        self.assertIsNone(plan["next_required_operation"])
        self.assertFalse(plan["approval_challenge_created"])

        with self.assertRaises(GovernanceError) as captured:
            self.service.approve(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(
            captured.exception.code,
            ErrorCode.OPERATIONAL_VALIDATION_FAILED,
        )
        saved = self.service._load(plan["plan_id"])
        self.assertEqual(saved.approval.state, ApprovalState.REQUIRED)
        self.assertIsNone(saved.approval.challenge_id)
        self.assertEqual(self.helper.dispatch_count, 0)

    async def test_approval_request_never_dispatches_helper_state(self):
        created = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id,
            desired_state="on",
        )
        plan = created["plan"]

        pending = self.service.approve(
            plan["plan_id"], plan["plan_hash"]
        )

        self.assertEqual(pending["approval_lifecycle"], "approval_pending_external")
        self.assertEqual(self.helper.dispatch_count, 0)

    async def test_selector_classification_drift_rejects_before_dispatch(self):
        self.dependency.selector_revision = "ordinary_dynamic_template"
        plan = await self.create_and_grant()
        self.dependency.selector_revision = "dynamic_entity_selector"

        result = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.helper.dispatch_count, 0)

    async def test_dependency_fingerprint_change_rejects_before_dispatch(self):
        plan = await self.create_and_grant()
        self.dependency.automation_ids = ["automation.new_cover_path"]
        self.dependency.consequence = "direct"

        result = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        child = self.runtime.children.get(
            self.runtime.children.declarations_for_task(result["task_id"])[0][
                "child_id"
            ]
        )

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertTrue(
            any(
                "dependency_risk_drift" in event["diagnostic_codes"]
                for event in child.events
            )
        )

    async def test_irrelevant_dependency_generation_change_does_not_reject(self):
        plan = await self.create_and_grant()
        self.dependency.generation += 1

        result = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )

        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(self.helper.dispatch_count, 1)

    async def test_effect_detail_change_rejects_before_dispatch(self):
        self.dependency.automation_ids = ["automation.benign_notify"]
        plan = await self.create_and_grant()
        self.dependency.effect_revision = "v2-target-or-data-changed"

        result = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.helper.dispatch_count, 0)

    async def test_preflight_already_desired_succeeds_without_consuming_approval(self):
        plan = await self.create_and_grant()
        self.helper.set_observed_state("on", self.clock)

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        child = self.runtime.children.get(
            self.runtime.children.declarations_for_task(result["task_id"])[0][
                "child_id"
            ]
        )

        self.assertEqual(
            result["task_state"], "succeeded_verified", child.to_dict()
        )
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertIsNone(child.dispatch_intent)
        self.assertEqual(child.dispatch_count, 0)
        self.assertTrue(child.preflight_completed)
        self.assertTrue(
            any(
                event["diagnostic_codes"]
                == ["desired_state_already_reached"]
                for event in child.events
            )
        )
        self.assertTrue(
            any(
                event["event_type"] == "preflight_noop_verified"
                for event in child.events
            )
        )
        self.assertEqual(
            self.service._load(plan["plan_id"]).approval.state,
            ApprovalState.APPROVED,
        )

    async def test_changed_fingerprint_fails_pre_dispatch(self):
        plan = await self.create_and_grant()
        self.helper.set_observed_state("off", self.clock)

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.helper.dispatch_count, 0)

    async def test_lost_response_is_verified_by_readback_without_redispatch(self):
        self.helper.mode = "response_lost"
        plan = await self.create_and_grant()

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])

        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(self.helper.dispatch_count, 1)
        repeated = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(repeated["status"], "already_applied")
        self.assertEqual(self.helper.dispatch_count, 1)

    async def test_success_response_with_wrong_readback_fails_truthfully(self):
        self.helper.mode = "verification_mismatch"
        plan = await self.create_and_grant()

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])

        self.assertEqual(result["task_state"], "failed_post_dispatch")
        self.assertTrue(result["provider_dispatch_occurred"])
        self.assertEqual(self.helper.dispatch_count, 1)
        self.assertEqual(
            self.service._load(plan["plan_id"]).status,
            PlanStatus.FAILED,
        )

    async def test_non_input_boolean_target_is_rejected_before_read_or_write(self):
        with self.assertRaises(GovernanceError):
            await self.service.create_helper_state_plan(
                entity_id="switch.not_allowed",
                desired_state="on",
            )
        self.assertEqual(self.helper.dispatch_count, 0)


class ExactHelperStateGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_emits_only_the_fixed_call_service_shape(self):
        class Rest:
            async def request(self, method, path):
                self.observed = (method, path)
                return {
                    "entity_id": "input_boolean.exact_target",
                    "state": "off",
                    "last_changed": "2026-08-13T12:00:00+00:00",
                }

        class WebSocket:
            def __init__(self):
                self.payloads = []

            async def command(self, payload):
                self.payloads.append(deepcopy(payload))
                return None

        rest = Rest()
        websocket = WebSocket()
        gateway = HelperStateGateway(rest, websocket)
        trace = []

        await gateway.read_state("input_boolean.exact_target")
        await gateway.set_state(
            "input_boolean.exact_target",
            "on",
            before_dispatch=lambda: self._record_boundary(trace),
        )

        self.assertEqual(
            rest.observed,
            ("GET", "/states/input_boolean.exact_target"),
        )
        self.assertEqual(trace, ["durable_intent"])
        self.assertEqual(
            websocket.payloads,
            [
                {
                    "type": "call_service",
                    "domain": "input_boolean",
                    "service": "turn_on",
                    "target": {
                        "entity_id": "input_boolean.exact_target"
                    },
                }
            ],
        )

    @staticmethod
    async def _record_boundary(trace):
        trace.append("durable_intent")

    async def test_toggle_and_physical_domains_never_reach_transport(self):
        class Unused:
            async def request(self, *_args, **_kwargs):
                raise AssertionError("read transport must not be reached")

            async def command(self, *_args, **_kwargs):
                raise AssertionError("write transport must not be reached")

        gateway = HelperStateGateway(Unused(), Unused())

        with self.assertRaises(ValueError):
            await gateway.set_state(
                "input_boolean.exact_target",
                "toggle",
                before_dispatch=lambda: self._record_boundary([]),
            )
        with self.assertRaises(ValueError):
            await gateway.set_state(
                "switch.physical_target",
                "on",
                before_dispatch=lambda: self._record_boundary([]),
            )

    async def test_rest_permission_failure_is_classified_without_content(self):
        class ForbiddenRest:
            async def request(self, *_args, **_kwargs):
                raise HomeAssistantApiError(
                    details={"status": 403, "untrusted": "must-not-surface"}
                )

        gateway = HelperStateGateway(ForbiddenRest(), object())

        with self.assertRaises(HelperStateGatewayError) as raised:
            await gateway.read_state("input_boolean.exact_target")

        self.assertEqual(raised.exception.category, "permission_failure")
        self.assertNotIn("must-not-surface", str(raised.exception))


class ExactHelperStateRegistryTests(unittest.TestCase):
    def test_configuration_bridge_remains_the_existing_exact_type_boundary(self):
        gateway = UnusedConfigurationGateway()
        self.assertIsInstance(
            ExistingConfigurationGatewayBridge(gateway),
            ExistingConfigurationGatewayBridge,
        )


if __name__ == "__main__":
    unittest.main()
