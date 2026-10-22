"""HAMCP-069B planning, execution, verification, policy and rollback tests.

Every path runs through the shipped service, F3 runtime and the closed
``EntityAvailabilityExposureGateway`` against the synthetic offline registry.
The Core admission gate is covered separately: these tests construct the F3
runtime without a Core runtime, exactly as the 069A execution tests do.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
)
from ha_mcp_engineering.governance.entity_availability_exposure import (  # noqa: E402
    AvailabilityExposureContractError,
    NEVER_SENT_COMMANDS,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalPolicyClass,
    ApprovalState,
    PlanStatus,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    evaluate_change_policy,
)

from tests.hamcp069b_fixtures import (  # noqa: E402
    CONFIG_ENTRY_GET,
    EXPOSE,
    EXPOSE_NEW_GET,
    REGISTRY_GET,
    REGISTRY_LIST,
    UPDATE,
    AvailabilityExposureServiceTestCase,
    automation_profile,
    exposure_snapshot,
    finding,
)


INCOMPLETE = "entity_availability_exposure_execution_contract_incomplete"
ELEVATED_ACKS = ["plan_approval", "elevated_risk_acknowledgement"]


def unauthorized() -> AuthorizationError:
    return AuthorizationError(details={"provider_response_received": True})


def policy(created: dict) -> tuple:
    decision = created["policy_decision"]
    return (
        decision["policy_class"],
        decision["physical_consequence"],
        decision["required_acknowledgements"],
    )


def child_disclosures(created: dict) -> list[dict]:
    return [
        item["dry_run_results"]["entity_availability_exposure"]
        for item in created["dry_run_results"]["operations"]
    ]


class ExecutionTestCase(AvailabilityExposureServiceTestCase):
    async def asyncTearDown(self) -> None:
        # The synthetic socket raises on a never-sent command; this also
        # proves no such command was ever recorded on any path.
        self.assert_never_sent()
        await super().asyncTearDown()

    async def refused(self, code: ErrorCode, reason: str, /, **arguments) -> dict:
        plans = len(self.repository.list())
        with self.assertRaises(GovernanceError) as raised:
            await self.create(**arguments)
        self.assertEqual(raised.exception.code, code, raised.exception.details)
        details = raised.exception.details
        self.assertEqual(details["reason_code"], reason, details)
        self.assertIs(details["plan_created"], False)
        self.assertIs(details["provider_dispatch_occurred"], False)
        self.assertEqual(len(self.repository.list()), plans)
        self.assertEqual(self.ws.writes(), [])
        return details

    async def assert_replay_refused(self, created: dict, task_id: str) -> None:
        writes = len(self.ws.writes())
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(created)
        self.assertEqual(raised.exception.code, ErrorCode.DUPLICATE_APPLY_ATTEMPT)
        self.assertEqual(raised.exception.details["task_id"], task_id)
        self.assertEqual(len(self.ws.writes()), writes)


class EndToEndTests(ExecutionTestCase):
    """Useful authorized success for each operation, with exact readback."""

    async def test_disable_is_standard_and_verified_by_registry_readback(self):
        self.add_entity("light.porch")
        created = await self.create(operation="disable", entity_ids=["light.porch"])
        self.assertEqual(policy(created), ("standard_admin", "none", ["plan_approval"]))
        (disclosure,) = child_disclosures(created)
        self.assertEqual(disclosure["final"]["disabled_by"], "user")
        self.assertEqual(disclosure["consumers"]["status"], "read")
        self.assertIn(
            "Disabling light.porch removes its state", disclosure["consequence_statements"][0]
        )
        # Planning only reads.
        self.assertEqual(self.ws.writes(), [])
        await self.approve(created)
        result = await self.apply(created)

        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(
            self.ws.writes(),
            [{"type": UPDATE, "entity_id": "light.porch", "disabled_by": "user"}],
        )
        self.assertEqual(self.ws.entries["light.porch"]["disabled_by"], "user")
        # Unfenced at planning, fenced again at preflight.
        self.assertEqual(self.snapshot_reads, [False, True])
        self.assertEqual(self.ws.command_types(), {REGISTRY_GET, UPDATE})
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.dispatch_count, 1)
        self.assertEqual(record.normalized_outcome, "succeeded_verified")

    async def test_enable_binds_one_config_entry_and_discloses_the_reload(self):
        self.ws.add_config_entry("entry_a")
        for entity_id in ("light.b", "light.a", "light.c"):
            self.add_entity(entity_id, disabled_by="user", config_entry_id="entry_a")
        created = await self.create(operation="enable", entity_ids=["light.b", "light.a"])
        self.assertEqual(policy(created), ("standard_admin", "none", ["plan_approval"]))
        disclosures = child_disclosures(created)
        # Ordered by (config entry, entity id).
        self.assertEqual([d["entity_id"] for d in disclosures], ["light.a", "light.b"])
        for disclosure in disclosures:
            self.assertEqual(disclosure["reload"], "config_entry_reload_after_30s")
            self.assertEqual(disclosure["config_entry"]["status"], "read")
            self.assertIs(disclosure["config_entry"]["supports_unload"], True)
            self.assertEqual(disclosure["other_entities_in_entry"], 2)
        self.assertEqual(self.snapshot_reads, [])
        await self.approve(created)
        result = await self.apply(created)

        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(
            self.ws.writes(),
            [
                {"type": UPDATE, "entity_id": "light.a", "disabled_by": None},
                {"type": UPDATE, "entity_id": "light.b", "disabled_by": None},
            ],
        )
        self.assertIsNone(self.ws.entries["light.a"]["disabled_by"])
        self.assertEqual(self.ws.entries["light.c"]["disabled_by"], "user")
        self.assertTrue({CONFIG_ENTRY_GET, REGISTRY_LIST} <= self.ws.command_types())

    async def test_expose_and_unexpose_each_writable_assistant(self):
        self.add_entity("light.porch")
        self.add_entity(
            "light.hall",
            options={"cloud.google_assistant": {"should_expose": True, "aliases": []}},
        )
        cases = (
            ("expose", "light.porch", "conversation", True, "stored_setting_is_effective"),
            ("unexpose", "light.hall", "cloud.google_assistant", False, "stored_setting_only"),
        )
        for operation, entity_id, assistant, value, proves in cases:
            with self.subTest(operation=operation, assistant=assistant):
                created = await self.create(
                    operation=operation, entity_ids=[entity_id], assistant=assistant
                )
                self.assertEqual(policy(created)[0], "standard_admin")
                (disclosure,) = child_disclosures(created)
                self.assertEqual(disclosure["readback_proves"], proves)
                self.assertIs(disclosure["expose_new_entities"], False)
                await self.approve(created)
                result = await self.apply(created)
                self.assertEqual(result["status"], "applied", result)
                self.assertEqual(
                    self.ws.writes()[-1],
                    {
                        "type": EXPOSE,
                        "assistants": [assistant],
                        "entity_ids": [entity_id],
                        "should_expose": value,
                    },
                )
                self.assertEqual(
                    self.ws.entries[entity_id]["options"][assistant]["should_expose"], value
                )
        # Other stored settings for the assistant are preserved by Core.
        self.assertEqual(
            self.ws.entries["light.hall"]["options"]["cloud.google_assistant"]["aliases"], []
        )
        self.assertEqual(
            self.ws.command_types(), {REGISTRY_GET, EXPOSE, EXPOSE_NEW_GET}
        )

    async def test_eight_entities_apply_in_order(self):
        entity_ids = [f"light.room_{index}" for index in range(8)]
        for entity_id in entity_ids:
            self.add_entity(entity_id)
        created = await self.create(operation="disable", entity_ids=list(reversed(entity_ids)))
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual([w["entity_id"] for w in self.ws.writes()], entity_ids)
        self.assertEqual(len(self.child_records(result["task_id"])), 8)


class ElevatedPolicyTests(ExecutionTestCase):
    """OD-5: protected cases are elevated, never prohibited, and apply."""

    async def elevated(self, basis: str, **arguments) -> dict:
        created = await self.create(**arguments)
        self.assertEqual(
            policy(created), ("elevated_admin", "safety_critical", ELEVATED_ACKS)
        )
        self.assertIn(basis, created["policy_decision"]["reason_codes"])
        for disclosure in child_disclosures(created):
            self.assertEqual(disclosure["policy_basis"]["basis"], [basis])
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["status"], "applied", result)
        return created

    async def test_expose_protected_object(self):
        self.add_entity("lock.front_door")
        await self.elevated(
            "expose_protected_object",
            operation="expose", entity_ids=["lock.front_door"], assistant="conversation",
        )
        self.assertIs(
            self.ws.entries["lock.front_door"]["options"]["conversation"]["should_expose"], True
        )

    async def test_unexpose_protected_object_is_standard(self):
        self.add_entity(
            "lock.front_door", options={"conversation": {"should_expose": True}}
        )
        created = await self.create(
            operation="unexpose", entity_ids=["lock.front_door"], assistant="conversation"
        )
        self.assertEqual(policy(created)[0], "standard_admin")

    async def test_disable_safety_sensor(self):
        self.add_entity("binary_sensor.kitchen_smoke", original_device_class="smoke")
        await self.elevated(
            "disable_protected_object",
            operation="disable", entity_ids=["binary_sensor.kitchen_smoke"],
        )
        self.assertEqual(self.ws.entries["binary_sensor.kitchen_smoke"]["disabled_by"], "user")

    async def test_disable_classless_cover_is_protected(self):
        self.add_entity("cover.side")
        created = await self.elevated(
            "disable_protected_object", operation="disable", entity_ids=["cover.side"]
        )
        (disclosure,) = child_disclosures(created)
        self.assertIn(
            "cover_device_class_unresolved", disclosure["protection"]["object_reasons"]
        )

    async def test_disable_feeding_a_safety_critical_automation(self):
        self.add_entity("sensor.hall_motion")
        self.snapshot = exposure_snapshot(
            findings=(finding("sensor.hall_motion", "unlock_on_motion"),),
            profiles=(automation_profile("unlock_on_motion", "safety_critical"),),
        )
        created = await self.elevated(
            "disable_protected_dependency",
            operation="disable", entity_ids=["sensor.hall_motion"],
        )
        (disclosure,) = child_disclosures(created)
        self.assertEqual(
            disclosure["protection"]["dependency_reasons"],
            ["feeds_protected_automation:unlock_on_motion"],
        )
        self.assertEqual(disclosure["consumers"]["count"], 1)

    async def test_disable_with_unresolved_dependency_evidence_is_elevated(self):
        self.add_entity("sensor.hall_motion")
        self.snapshot = exposure_snapshot(
            findings=(finding("sensor.hall_motion", "unknown_effect"),),
        )
        await self.elevated(
            "disable_protected_dependency",
            operation="disable", entity_ids=["sensor.hall_motion"],
        )

    async def test_enable_exposed_protected_object(self):
        self.add_entity(
            "lock.front_door",
            disabled_by="user",
            options={"cloud.google_assistant": {"should_expose": True}},
        )
        await self.elevated(
            "enable_exposed_protected_object",
            operation="enable", entity_ids=["lock.front_door"],
        )
        self.assertIsNone(self.ws.entries["lock.front_door"]["disabled_by"])

    async def test_enable_unexposed_protected_object_is_standard(self):
        self.add_entity("lock.front_door", disabled_by="user")
        created = await self.create(operation="enable", entity_ids=["lock.front_door"])
        self.assertEqual(policy(created)[0], "standard_admin")

    async def test_standard_approval_alone_cannot_apply_an_elevated_plan(self):
        self.add_entity("lock.front_door")
        created = await self.create(
            operation="expose", entity_ids=["lock.front_door"], assistant="conversation"
        )
        granted = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(granted.get("status"), "approval_pending", granted)
        with self.assertRaises(GovernanceError):
            await self.apply(created)
        self.assertEqual(self.ws.writes(), [])


class PlanningRefusalTests(ExecutionTestCase):
    async def test_alexa_is_refused_before_any_read(self):
        self.add_entity("light.porch")
        details = await self.refused(
            ErrorCode.INVALID_REQUEST, "assistant_not_supported",
            operation="expose", entity_ids=["light.porch"], assistant="cloud.alexa",
        )
        self.assertEqual(details["field"], "assistant")
        self.assertEqual(self.ws.commands, [])

    async def test_assistant_rules(self):
        self.add_entity("light.porch")
        await self.refused(
            ErrorCode.INVALID_REQUEST, "assistant_required",
            operation="expose", entity_ids=["light.porch"],
        )
        await self.refused(
            ErrorCode.INVALID_REQUEST, "assistant_not_applicable",
            operation="disable", entity_ids=["light.porch"], assistant="conversation",
        )
        self.assertEqual(self.ws.commands, [])

    async def test_eight_is_the_cap(self):
        entity_ids = [f"light.room_{index}" for index in range(9)]
        for entity_id in entity_ids:
            self.add_entity(entity_id)
        await self.refused(
            ErrorCode.INVALID_REQUEST, "entity_availability_exposure_too_many_entities",
            operation="disable", entity_ids=entity_ids,
        )
        self.assertEqual(self.ws.commands, [])
        created = await self.create(operation="disable", entity_ids=entity_ids[:8])
        self.assertEqual(len(created["dry_run_results"]["operations"]), 8)

    async def test_input_shape_refusals(self):
        self.add_entity("light.porch")
        for arguments, reason in (
            ({"operation": "disable", "entity_ids": ["light.porch"], "reason": "x"},
             "entity_availability_exposure_field_not_supported"),
            ({"operation": "rename", "entity_ids": ["light.porch"]},
             "entity_availability_exposure_invalid_value"),
            ({"operation": "disable", "entity_ids": []},
             "entity_availability_exposure_invalid_value"),
            ({"operation": "disable", "entity_ids": "light.porch"},
             "entity_availability_exposure_invalid_value"),
            ({"operation": "disable", "entity_ids": ["light.*"]},
             "entity_availability_exposure_invalid_value"),
            ({"operation": "disable", "entity_ids": ["light.porch", "light.porch"]},
             "entity_availability_exposure_duplicate_entity"),
            ({"operation": "disable", "entity_ids": ["light.porch"], "expiration_minutes": 4},
             "entity_availability_exposure_invalid_value"),
        ):
            with self.subTest(arguments=arguments):
                await self.refused(ErrorCode.INVALID_REQUEST, reason, **arguments)
        self.assertEqual(self.ws.commands, [])

    async def test_enable_spanning_config_entries_is_refused(self):
        self.add_entity("light.a", disabled_by="user", config_entry_id="entry_a")
        self.add_entity("light.b", disabled_by="user", config_entry_id="entry_b")
        self.add_entity("light.c", disabled_by="user")
        for pair in (["light.a", "light.b"], ["light.a", "light.c"]):
            with self.subTest(pair=pair):
                details = await self.refused(
                    ErrorCode.INVALID_REQUEST, "enable_spans_multiple_config_entries",
                    operation="enable", entity_ids=pair,
                )
                self.assertEqual(details["config_entry_count"], 2)
        # Disable has no single-entry rule.
        self.add_entity("light.d", config_entry_id="entry_a")
        self.add_entity("light.e", config_entry_id="entry_b")
        created = await self.create(operation="disable", entity_ids=["light.d", "light.e"])
        self.assertEqual(len(created["dry_run_results"]["operations"]), 2)

    async def test_unregistered_entity_is_refused(self):
        self.add_entity("light.porch")
        details = await self.refused(
            ErrorCode.ENTITY_NOT_FOUND, "entity_not_in_registry",
            operation="disable", entity_ids=["light.porch", "light.state_only"],
        )
        self.assertEqual(details["entity_id"], "light.state_only")

    async def test_state_refusals(self):
        self.add_entity("light.off", disabled_by="user")
        self.add_entity("light.on")
        self.add_entity("light.exposed", options={"conversation": {"should_expose": True}})
        self.add_entity("light.hidden", options={"conversation": {"should_expose": False}})
        for reason, arguments in (
            ("entity_already_disabled", {"operation": "disable", "entity_ids": ["light.off"]}),
            ("entity_already_enabled", {"operation": "enable", "entity_ids": ["light.on"]}),
            ("entity_already_exposed", {"operation": "expose", "entity_ids": ["light.exposed"],
                                        "assistant": "conversation"}),
            ("entity_already_unexposed", {"operation": "unexpose", "entity_ids": ["light.hidden"],
                                          "assistant": "conversation"}),
        ):
            with self.subTest(reason=reason):
                await self.refused(ErrorCode.INVALID_REQUEST, reason, **arguments)

    async def test_od6_disabled_by_reasons_are_refused(self):
        for reason in ("device", "config_entry", "hass"):
            with self.subTest(disabled_by=reason):
                self.add_entity(f"light.{reason}", disabled_by=reason)
                await self.refused(
                    ErrorCode.INVALID_REQUEST, f"entity_disabled_by_{reason}",
                    operation="enable", entity_ids=[f"light.{reason}"],
                )

    async def test_integration_disabled_entity_can_be_enabled(self):
        self.add_entity("sensor.diagnostic", disabled_by="integration")
        created = await self.create(operation="enable", entity_ids=["sensor.diagnostic"])
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["status"], "applied", result)
        self.assertIsNone(self.ws.entries["sensor.diagnostic"]["disabled_by"])

    async def test_absent_preference_is_always_an_explicit_write(self):
        self.add_entity("light.porch")
        for operation in ("expose", "unexpose"):
            with self.subTest(operation=operation):
                created = await self.create(
                    operation=operation, entity_ids=["light.porch"], assistant="conversation"
                )
                (disclosure,) = child_disclosures(created)
                self.assertIs(disclosure["current"]["exposure"]["conversation"]["present"], False)
                self.assertIn("No rollback", disclosure["rollback_disclosure"])

    async def test_planning_permission_failure(self):
        self.add_entity("light.porch")
        self.ws.failures[REGISTRY_GET] = unauthorized()
        await self.refused(
            ErrorCode.AUTHORIZATION_FAILURE, "entity_registry_permission_failure",
            operation="disable", entity_ids=["light.porch"],
        )

    async def test_public_configuration_route_refuses_both_types(self):
        self.add_entity("light.porch")
        for resource_type in ("entity_registry_availability", "entity_assistant_exposure"):
            with self.subTest(resource_type=resource_type):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_plan(
                        title="Generic",
                        description="Generic route",
                        operations=[{
                            "operation_id": "op",
                            "resource_type": resource_type,
                            "action": "update",
                            "target_id": "light.porch",
                            "proposed_config": {"disabled_by": "user"},
                        }],
                    )
                self.assertEqual(
                    raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED
                )
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.ws.writes(), [])


class StaleAndVerificationTests(ExecutionTestCase):
    async def assert_preflight_rejected(self, created: dict, code: str) -> dict:
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIs(result["provider_dispatch_occurred"], False)
        records = self.child_records(result["task_id"])
        self.assertEqual(records[0].normalized_outcome, "preflight_rejected")
        self.assertEqual(records[0].dispatch_count, 0)
        self.assertIn(code, self.child_codes(result["task_id"]))
        self.assertEqual(self.ws.writes(), [])
        return result

    async def test_external_disabled_by_change_is_stale(self):
        self.add_entity("light.porch")
        created = await self.approved(operation="disable", entity_ids=["light.porch"])
        self.ws.set_external("light.porch", disabled_by="integration")
        await self.assert_preflight_rejected(created, "stale_target_state")
        self.assertEqual(self.ws.entries["light.porch"]["disabled_by"], "integration")
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(plan.approval.state, ApprovalState.APPROVED)

    async def test_unrelated_registry_field_change_is_stale(self):
        self.add_entity("light.porch", options={"conversation": {"should_expose": False}})
        created = await self.approved(
            operation="expose", entity_ids=["light.porch"], assistant="conversation"
        )
        self.ws.set_external("light.porch", name="Renamed elsewhere")
        await self.assert_preflight_rejected(created, "stale_target_state")

    async def test_other_assistant_exposure_change_is_stale(self):
        self.add_entity("light.porch")
        created = await self.approved(
            operation="expose", entity_ids=["light.porch"], assistant="conversation"
        )
        self.ws.set_external(
            "light.porch", options={"cloud.google_assistant": {"should_expose": True}}
        )
        await self.assert_preflight_rejected(created, "stale_target_state")

    async def test_changed_config_entry_evidence_is_rejected(self):
        self.ws.add_config_entry("entry_a")
        self.add_entity("light.a", disabled_by="user", config_entry_id="entry_a")
        created = await self.approved(operation="enable", entity_ids=["light.a"])
        self.ws.config_entries["entry_a"]["supports_unload"] = False
        await self.assert_preflight_rejected(created, "config_entry_evidence_changed")

    async def test_changed_dependency_evidence_is_rejected(self):
        self.add_entity("sensor.hall_motion")
        created = await self.approved(operation="disable", entity_ids=["sensor.hall_motion"])
        self.snapshot = exposure_snapshot(
            findings=(finding("sensor.hall_motion", "new_consumer"),),
            profiles=(automation_profile("new_consumer", "none"),),
        )
        await self.assert_preflight_rejected(created, "dependency_evidence_changed")

    async def test_readback_mismatch_is_terminal_and_never_redispatched(self):
        self.add_entity("light.porch")
        created = await self.approved(operation="disable", entity_ids=["light.porch"])
        self.ws.ignore_writes = True
        result = await self.apply(created)

        self.assertIs(result["provider_dispatch_occurred"], True)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "verification_mismatch")
        self.assertEqual(record.dispatch_count, 1)
        self.assertIn("exact_readback_not_verified", self.child_codes(result["task_id"]))
        self.assertEqual(len(self.ws.writes()), 1)
        self.assertEqual(self.repository.get(created["plan_id"]).status, PlanStatus.FAILED)
        await self.assert_replay_refused(created, result["task_id"])

    async def test_empty_expose_response_is_not_proof(self):
        self.add_entity("light.porch")
        created = await self.approved(
            operation="expose", entity_ids=["light.porch"], assistant="conversation"
        )
        self.ws.ignore_writes = True
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "verification_mismatch")

    async def test_new_alexa_key_is_tolerated_and_reported(self):
        self.add_entity("light.porch")
        self.ws.alexa_default_on_update = False
        created = await self.approved(operation="disable", entity_ids=["light.porch"])
        result = await self.apply(created)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(
            self.ws.entries["light.porch"]["options"]["cloud.alexa"], {"should_expose": False}
        )
        self.assertIn(
            "tolerated_new_exposure_key_cloud_alexa", self.child_codes(result["task_id"])
        )

    async def test_any_other_exposure_side_effect_is_a_mismatch(self):
        self.add_entity("light.porch")
        created = await self.approved(operation="disable", entity_ids=["light.porch"])
        websocket = self.ws
        answer = websocket.command

        async def command(message):
            value = await answer(message)
            if message.get("type") == UPDATE:
                websocket.entries["light.porch"]["options"]["conversation"] = {
                    "should_expose": True
                }
            return value

        websocket.command = command
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "verification_mismatch")
        self.assertFalse(
            {c for c in self.child_codes(result["task_id"]) if c.startswith("tolerated")}
        )

    async def test_unauthorized_dispatch_is_confirmed_not_applied(self):
        self.add_entity("light.porch")
        created = await self.approved(
            operation="expose", entity_ids=["light.porch"], assistant="conversation"
        )
        self.ws.failures[EXPOSE] = unauthorized()
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "dispatch_failed_confirmed")
        self.assertEqual(record.dispatch_count, 1)
        self.assertEqual(len(self.ws.writes()), 1)
        self.assertNotIn("conversation", self.ws.entries["light.porch"]["options"])
        await self.assert_replay_refused(created, result["task_id"])

    async def test_error_result_is_settled_by_readback(self):
        self.add_entity("light.porch")
        created = await self.approved(operation="disable", entity_ids=["light.porch"])
        websocket = self.ws
        answer = websocket.command

        async def command(message):
            if message.get("type") == UPDATE:
                await answer(message)
                raise HomeAssistantApiError(
                    details={"status": 500, "provider_response_received": True}
                )
            return await answer(message)

        websocket.command = command
        result = await self.apply(created)
        # The write landed; only the registry readback proves it.
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.ws.writes()), 1)

    async def test_stop_on_first_failure(self):
        for entity_id in ("light.a", "light.b", "light.c"):
            self.add_entity(entity_id)
        created = await self.approved(
            operation="disable", entity_ids=["light.a", "light.b", "light.c"]
        )
        websocket = self.ws
        answer = websocket.command

        async def command(message):
            if message.get("type") == UPDATE and message["entity_id"] == "light.b":
                websocket.commands.append(dict(message))
                raise unauthorized()
            return await answer(message)

        websocket.command = command
        result = await self.apply(created)
        # A verified child followed by a failed one is the shipped partial
        # outcome for a non-atomic plan.
        self.assertEqual(result["task_state"], "manual_review_required", result)
        self.assertEqual([w["entity_id"] for w in self.ws.writes()], ["light.a", "light.b"])
        self.assertEqual(self.ws.entries["light.a"]["disabled_by"], "user")
        self.assertIsNone(self.ws.entries["light.c"]["disabled_by"])
        first, second, third = self.child_records(result["task_id"])
        self.assertEqual(
            [first.normalized_outcome, second.normalized_outcome],
            ["succeeded_verified", "dispatch_failed_confirmed"],
        )
        # The third child was never started, so it can never dispatch.
        self.assertIsNone(third)


class NeverSentTests(ExecutionTestCase):
    ENGINE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"

    def test_cloud_status_is_never_named_outside_the_never_sent_list(self):
        """Static check: no Engineering module can send ``cloud/status``."""

        import ast

        carriers = []
        for path in sorted(self.ENGINE.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            allowed: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "NEVER_SENT_COMMANDS"
                    for t in node.targets
                ):
                    allowed |= {id(item) for item in ast.walk(node.value)}
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value.strip() in {"cloud/status", *NEVER_SENT_COMMANDS}
                    and id(node) not in allowed
                ):
                    carriers.append((path.relative_to(self.ENGINE).as_posix(), node.value))
        self.assertEqual(carriers, [])

    async def test_gateway_refuses_every_never_sent_command_before_io(self):
        gateway = self.resource_gateway.availability_exposure
        for kind in (*NEVER_SENT_COMMANDS, "config/entity_registry/remove", "call_service"):
            with self.subTest(command=kind):
                with self.assertRaises(AvailabilityExposureContractError):
                    await gateway._send({"type": kind})
        self.assertEqual(self.ws.commands, [])

    async def test_every_flow_uses_only_the_closed_vocabulary(self):
        self.ws.add_config_entry("entry_a")
        self.add_entity("light.a", disabled_by="user", config_entry_id="entry_a")
        self.add_entity("light.b")
        for arguments in (
            {"operation": "enable", "entity_ids": ["light.a"]},
            {"operation": "disable", "entity_ids": ["light.b"]},
            {"operation": "expose", "entity_ids": ["light.b"], "assistant": "conversation"},
            {"operation": "unexpose", "entity_ids": ["light.b"],
             "assistant": "cloud.google_assistant"},
        ):
            created = await self.approved(**arguments)
            result = await self.apply(created)
            self.assertEqual(result["status"], "applied", result)
        self.assertEqual(
            self.ws.command_types(),
            {REGISTRY_GET, REGISTRY_LIST, CONFIG_ENTRY_GET, EXPOSE_NEW_GET, UPDATE, EXPOSE},
        )
        for write in self.ws.writes():
            if write["type"] == UPDATE:
                self.assertEqual(set(write), {"type", "entity_id", "disabled_by"})
        self.assertEqual(self.rest.requests, [])


class PolicyBoundaryTests(ExecutionTestCase):
    async def planned(self, *entity_ids: str, **arguments) -> object:
        created = await self.create(
            **{"operation": "disable", "entity_ids": list(entity_ids), **arguments}
        )
        return self.repository.get(created["plan_id"])

    def assert_prohibited(self, plan) -> None:
        decision = evaluate_change_policy(plan)
        self.assertEqual(decision.policy_class, ApprovalPolicyClass.PROHIBITED)
        self.assertIn(INCOMPLETE, decision.reason_codes)

    async def test_tampered_marker_or_basis_is_prohibited(self):
        self.add_entity("light.porch")
        plan = await self.planned("light.porch")
        operation = plan.operations[0]
        for change in (
            {"policy": "elevated"},
            {"physical_consequence": "safety_critical"},
            {"basis": "disable_protected_object"},
        ):
            with self.subTest(change=change):
                evidence = tuple(
                    {**item, **change} if isinstance(item, dict) and "trigger" in item else item
                    for item in operation.risk.evidence
                )
                tampered = deepcopy(plan)
                tampered.operations = [
                    replace(operation, risk=replace(operation.risk, evidence=evidence))
                ]
                self.assert_prohibited(tampered)
        for evidence in ((), tuple(operation.risk.evidence) * 2):
            tampered = deepcopy(plan)
            tampered.operations = [
                replace(operation, risk=replace(operation.risk, evidence=evidence))
            ]
            self.assert_prohibited(tampered)

    async def test_protected_object_relabelled_standard_is_prohibited(self):
        """A protected basis cannot be hidden: it is recomputed from bindings."""

        self.add_entity("binary_sensor.smoke", original_device_class="smoke")
        plan = await self.planned("binary_sensor.smoke")
        operation = plan.operations[0]
        evidence = tuple(
            {**item, "policy": "standard", "physical_consequence": "none", "basis": ""}
            if isinstance(item, dict) and "trigger" in item else item
            for item in operation.risk.evidence
        )
        from ha_mcp_engineering.governance.models import RiskLevel

        tampered = deepcopy(plan)
        tampered.operations = [
            replace(
                operation,
                risk=replace(operation.risk, evidence=evidence, level=RiskLevel.LOW),
            )
        ]
        self.assert_prohibited(tampered)

    async def test_plan_shape_rules(self):
        for entity_id in ("light.a", "light.b"):
            self.add_entity(entity_id)
        self.add_entity("light.c", disabled_by="user", config_entry_id="entry_a")
        self.add_entity("light.d", disabled_by="user", config_entry_id="entry_b")
        self.ws.add_config_entry("entry_a")
        self.ws.add_config_entry("entry_b")
        disable_a = (await self.planned("light.a")).operations[0]
        disable_b = (await self.planned("light.b")).operations[0]
        expose_a = (
            await self.planned("light.a", operation="expose", assistant="conversation")
        ).operations[0]
        enable_c = (await self.planned("light.c", operation="enable")).operations[0]
        enable_d = (await self.planned("light.d", operation="enable")).operations[0]
        base = await self.planned("light.a")
        for label, operations in (
            ("mixed_types", [disable_a, replace(expose_a, operation_id="second")]),
            ("duplicate_target", [disable_a, replace(disable_a, operation_id="second")]),
            ("two_config_entries", [enable_c, replace(enable_d, operation_id="second")]),
            ("nine_children", [
                replace(disable_b, operation_id=f"op_{index}", target_id=f"light.x{index}")
                for index in range(9)
            ]),
        ):
            with self.subTest(label=label):
                tampered = deepcopy(base)
                tampered.operations = operations
                self.assert_prohibited(tampered)

    async def test_non_update_action_is_prohibited(self):
        self.add_entity("light.porch")
        plan = await self.planned("light.porch")
        tampered = deepcopy(plan)
        tampered.operations = [replace(plan.operations[0], action="create")]
        self.assert_prohibited(tampered)


class RollbackTests(ExecutionTestCase):
    async def applied(self, **arguments) -> dict:
        created = await self.approved(**arguments)
        result = await self.apply(created)
        self.assertEqual(result["status"], "applied", result)
        return created

    async def rollback(self, created: dict) -> dict:
        return await self.service.rollback_change(created["plan_id"], created["plan_hash"])

    async def refused_rollback(self, created: dict, reason: str) -> dict:
        plans = len(self.repository.list())
        writes = len(self.ws.writes())
        with self.assertRaises(GovernanceError) as raised:
            await self.rollback(created)
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        details = raised.exception.details
        self.assertEqual(details["reason"], reason, details)
        self.assertIs(details["provider_dispatch_occurred"], False)
        self.assertEqual(len(self.repository.list()), plans)
        self.assertEqual(len(self.ws.writes()), writes)
        return details

    async def apply_reverse(self, rollback: dict) -> dict:
        reverse = {"plan_id": rollback["rollback_plan_id"], "plan_hash": rollback["plan_hash"]}
        await self.approve(reverse)
        return await self.apply(reverse)

    async def test_enable_rolls_back_as_a_disable_plan(self):
        self.ws.add_config_entry("entry_a")
        self.add_entity("light.a", disabled_by="user", config_entry_id="entry_a")
        self.add_entity("light.b", disabled_by="user", config_entry_id="entry_a")
        created = await self.applied(operation="enable", entity_ids=["light.a", "light.b"])
        rollback = await self.rollback(created)
        self.assertEqual(rollback["status"], "rollback_plan_created")
        self.assertEqual(rollback["rollback_operation"], "disable")
        self.assertEqual(rollback["entities_to_restore"], ["light.a", "light.b"])
        self.assertIs(rollback["provider_dispatch_occurred"], False)
        self.assertEqual(len(self.ws.writes()), 2)
        again = await self.rollback(created)
        self.assertEqual(again["rollback_plan_id"], rollback["rollback_plan_id"])
        result = await self.apply_reverse(rollback)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(self.ws.entries["light.a"]["disabled_by"], "user")
        self.assertEqual(self.ws.entries["light.b"]["disabled_by"], "user")

    async def test_exposure_with_a_stored_prior_is_restored(self):
        self.add_entity("light.porch", options={"conversation": {"should_expose": False}})
        created = await self.applied(
            operation="expose", entity_ids=["light.porch"], assistant="conversation"
        )
        rollback = await self.rollback(created)
        self.assertEqual(rollback["rollback_operation"], "unexpose")
        result = await self.apply_reverse(rollback)
        self.assertEqual(result["status"], "applied", result)
        self.assertIs(
            self.ws.entries["light.porch"]["options"]["conversation"]["should_expose"], False
        )

    async def test_unset_prior_preference_is_not_restorable(self):
        self.add_entity("light.porch")
        created = await self.applied(
            operation="expose", entity_ids=["light.porch"], assistant="conversation"
        )
        await self.refused_rollback(created, "prior_exposure_preference_unset_not_restorable")

    async def test_later_edit_refuses_rollback(self):
        self.add_entity("light.porch")
        created = await self.applied(operation="disable", entity_ids=["light.porch"])
        self.ws.set_external("light.porch", name="Edited elsewhere")
        details = await self.refused_rollback(created, "entry_changed_since_apply")
        self.assertEqual(details["entity_id"], "light.porch")

    async def test_tolerated_alexa_key_does_not_block_rollback(self):
        self.add_entity("light.porch")
        self.ws.alexa_default_on_update = False
        created = await self.applied(operation="disable", entity_ids=["light.porch"])
        rollback = await self.rollback(created)
        self.assertEqual(rollback["rollback_operation"], "enable")

    async def test_failed_or_unapplied_forward_is_refused(self):
        self.add_entity("light.porch")
        unapplied = await self.create(operation="disable", entity_ids=["light.porch"])
        await self.refused_rollback(unapplied, "forward_not_verified")
        created = await self.approved(operation="disable", entity_ids=["light.porch"])
        self.ws.failures[UPDATE] = unauthorized()
        await self.apply(created)
        await self.refused_rollback(created, "forward_not_verified")

    async def test_partial_forward_reverses_only_verified_children(self):
        for entity_id in ("light.a", "light.b"):
            self.add_entity(entity_id)
        created = await self.approved(operation="disable", entity_ids=["light.a", "light.b"])
        websocket = self.ws
        answer = websocket.command

        async def command(message):
            if message.get("type") == UPDATE and message["entity_id"] == "light.b":
                websocket.commands.append(dict(message))
                raise unauthorized()
            return await answer(message)

        websocket.command = command
        await self.apply(created)
        websocket.command = answer
        rollback = await self.rollback(created)
        self.assertEqual(rollback["status"], "rollback_partial_plan_created", rollback)
        self.assertEqual(rollback["entities_to_restore"], ["light.a"])
        self.assertEqual(len(rollback["operations_excluded"]), 1)
        result = await self.apply_reverse(rollback)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(self.ws.writes()[-1], {"type": UPDATE, "entity_id": "light.a",
                                                "disabled_by": None})


if __name__ == "__main__":
    unittest.main()
