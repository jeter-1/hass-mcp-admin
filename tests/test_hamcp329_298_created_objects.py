"""HAMCP-329/298 governed scene create and created-object rollback.

Every path runs through the shipped service, the F3 runtime and the closed
scene-create and created-object delete gateways against the synthetic
offline Core store in ``tests/hamcp329_298_fixtures.py``.  The contract is
``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md`` §3-§8.

This module holds the authorized successes of §7 and the option E positives
of §6.  Refusals and negative reachability are in
``test_hamcp329_298_refusals.py``; failure preservation and recovery are in
``test_hamcp329_298_failure_recovery.py``.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.governance import (  # noqa: E402
    created_object_identity as identity_model,
    created_object_rollback as rollback_model,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    evaluate_change_policy,
)

from tests.hamcp329_298_fixtures import (  # noqa: E402
    CreatedObjectServiceTestCase,
    EVENING,
    EVENING_ENTITY,
    SCENE_PREFIX,
    automation_config,
    boolean_config,
    helper_operation,
    number_config,
    script_config,
)


HELPERS = (
    ("input_boolean", boolean_config(), "hvac_override"),
    ("input_number", number_config(), "hvac_target"),
)


def config_operation(resource_type: str, target_id: str) -> dict:
    config = (
        automation_config(target_id)
        if resource_type == "automation"
        else script_config(target_id)
    )
    return {
        "operation_id": "create_1",
        "resource_type": resource_type,
        "action": "create",
        "target_id": target_id,
        "depends_on": [],
        "proposed_config": config,
    }


def two_helpers() -> tuple[dict, dict]:
    return (
        helper_operation("input_boolean", boolean_config(), operation_id="create_a"),
        helper_operation(
            "input_number",
            number_config(),
            operation_id="create_b",
            depends_on=["create_a"],
        ),
    )


class SceneAcceptanceTests(CreatedObjectServiceTestCase):
    async def test_scene_create_is_verified_and_captured(self):
        plan = await self.scene_plan()
        before = self.service.get_plan(plan["plan_id"])["rollback"]
        self.assertIs(before["available"], False)
        self.assertEqual(before["status"], "unavailable_for_configuration_plan")
        await self.applied(plan)
        # Exactly one POST, to the caller's config id, with the body only.
        self.assertEqual(len(self.config_rest.scene_posts), 1)
        config_id, body = self.config_rest.scene_posts[0]
        self.assertEqual(config_id, EVENING)
        self.assertEqual(set(body), {"name", "entities"})
        self.assertEqual(
            self.config_rest.writes(), [("POST", f"{SCENE_PREFIX}{EVENING}")]
        )
        self.assertEqual(self.service_calls(), [])
        (value,) = self.identity_values(plan["plan_id"])
        self.assertEqual(value["outcome"], identity_model.CAPTURED, value)
        identity = value["identity"]
        self.assertEqual(identity["entity_id"], EVENING_ENTITY)
        self.assertEqual(identity["registry_unique_id"], EVENING)
        self.assertEqual(identity["registry_platform"], "homeassistant")
        self.assertEqual(
            identity["delete_argument"],
            {"method": "DELETE", "path": f"{SCENE_PREFIX}{EVENING}"},
        )
        projected = self.service.get_plan(plan["plan_id"])["rollback"]
        self.assertIs(projected["available"], True)
        self.assertEqual(projected["status"], "available_pending_current_state_check")

    async def test_scene_create_rollback_deletes_exactly_once(self):
        created = await self.created_scene()
        rollback, plan = await self.rollback_plan(created, approve=False)
        self.assertEqual(rollback["operations_to_delete"], ["scene_create_1"])
        self.assertEqual(rollback["operations_excluded"], [])
        self.assertIs(rollback["approval_required"], True)
        self.assertIs(rollback["provider_dispatch_occurred"], False)
        # Planning sends nothing; the delete needs its own approval.
        self.assertEqual(self.config_rest.scene_deletes(), [])
        (operation,) = self.operations(plan["plan_id"])
        self.assertEqual(
            (operation.operation_id, operation.resource_type, operation.action),
            ("delete_1", "scene_configuration", "delete"),
        )
        await self.approve(plan)
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.config_rest.scene_deletes(), [("DELETE", f"{SCENE_PREFIX}{EVENING}")]
        )
        self.assertNotIn(EVENING, self.config_rest.scenes)
        self.assertNotIn(EVENING_ENTITY, self.ws.entries)
        self.assertEqual(self.service_calls(), [])

    async def test_unchanged_created_scene_rolls_back_under_the_standard_policy(self):
        created = await self.created_scene()
        rollback, _plan = await self.rollback_plan(created, approve=False)
        (marker,) = self.markers(rollback["rollback_plan_id"])
        self.assertEqual(marker["source"], rollback_model.SOURCE)
        self.assertEqual(marker["ownership"], rollback_model.OWNED)
        self.assertEqual(marker["basis"], "")
        self.assertEqual(marker["captured_modified_at"], marker["observed_modified_at"])
        self.assertEqual(marker["source_plan_id"], created["plan_id"])
        self.assertEqual(marker["source_plan_hash"], created["plan_hash"])
        self.assertEqual(marker["source_operation_id"], "scene_create_1")
        decision = self.policy(rollback["rollback_plan_id"])
        # No E basis: the class and acknowledgements are the HAMCP-155 ones.
        self.assertEqual(decision.policy_class.value, "standard_admin")
        self.assertEqual(
            [item.value for item in decision.required_acknowledgements],
            ["plan_approval"],
        )
        self.assertNotIn(
            rollback_model.BASIS_MODIFIED_SINCE_CREATE, decision.reason_codes
        )
        (operation,) = self.operations(rollback["rollback_plan_id"])
        self.assertFalse(
            any(rollback_model.E_DISCLOSURE in reason for reason in operation.risk.reasons)
        )

    async def test_slow_scene_registration_is_polled_within_the_bound(self):
        self.config_rest.scene_registers_after_gets = 3
        created = await self.created_scene()
        self.assertTrue(self.poll_sleeps, "the create readback never polled")
        self.assertLessEqual(len(self.poll_sleeps), 5)
        self.assertEqual(set(self.poll_sleeps), {0.5})
        self.assertEqual(len(self.config_rest.scene_posts), 1)
        (value,) = self.identity_values(created["plan_id"])
        self.assertEqual(value["outcome"], identity_model.CAPTURED, value)

    async def test_recreate_restores_the_same_scene_config_id(self):
        created = await self.created_scene()
        rollback, delete_plan = await self.rollback_plan(created)
        result = await self.apply(delete_plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        projected = self.service.get_plan(rollback["rollback_plan_id"])["rollback"]
        self.assertIs(projected["available"], True)
        recreate = await self.service.rollback_change(rollback["rollback_plan_id"])
        self.assertEqual(recreate["status"], "rollback_plan_created", recreate)
        self.assertEqual(recreate["operations_to_restore"], ["scene_create_1"])
        (operation,) = self.operations(recreate["rollback_plan_id"])
        self.assertEqual(
            (operation.resource_type, operation.action, operation.target_id),
            ("scene_configuration", "create", EVENING),
        )
        # The recreate is a governed create of its own: nothing is sent
        # until it is separately approved.
        self.assertEqual(len(self.config_rest.scene_posts), 1)
        result = await self.apply_rollback(recreate)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            [config_id for config_id, _body in self.config_rest.scene_posts],
            [EVENING, EVENING],
        )
        self.assertEqual(
            self.config_rest.scene_posts[1][1], self.config_rest.scene_posts[0][1]
        )
        self.assertIn(EVENING, self.config_rest.scenes)
        self.assertEqual(len(self.config_rest.scene_deletes()), 1)


class HelperAcceptanceTests(CreatedObjectServiceTestCase):
    async def test_helper_create_rollback_uses_the_exact_object_id(self):
        for domain, config, object_id in HELPERS:
            with self.subTest(domain=domain):
                created = await self.created_helper(domain, config)
                (value,) = self.identity_values(created["plan_id"])
                self.assertEqual(value["outcome"], identity_model.CAPTURED, value)
                identity = value["identity"]
                self.assertEqual(identity["storage_object_id"], object_id)
                self.assertEqual(identity["registry_unique_id"], object_id)
                self.assertEqual(identity["registry_platform"], domain)
                self.assertEqual(identity["entity_id"], f"{domain}.{object_id}")
                self.assertEqual(
                    identity["delete_argument"],
                    {"type": f"{domain}/delete", f"{domain}_id": object_id},
                )
                rollback = await self.service.rollback_change(created["plan_id"])
                result = await self.apply_rollback(rollback)
                self.assertEqual(result["task_state"], "succeeded_verified", result)
                # The object id, never the full entity id, and only this
                # domain's own command.
                self.assertEqual(
                    [
                        command
                        for command in self.ws.helper_commands("delete")
                        if command["type"].startswith(domain)
                    ],
                    [{"type": f"{domain}/delete", f"{domain}_id": object_id}],
                )
                self.assertEqual(self.ws.helpers[domain], {})
                self.assertNotIn(f"{domain}.{object_id}", self.ws.entries)
        self.assertEqual(len(self.ws.helper_commands("delete")), 2)
        self.assertEqual(self.service_calls(), [])

    async def test_helper_absence_is_read_by_object_id_and_actual_entity_id(self):
        created = await self.created_helper("input_number")
        _rollback, plan = await self.rollback_plan(created)
        mark = self.mark()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        commands = self.ws.commands[mark[1] :]
        position = commands.index(
            {"type": "input_number/delete", "input_number_id": "hvac_target"}
        )
        after = [command["type"] for command in commands[position + 1 :]]
        # After the delete: the storage collection and the registry are read
        # again, and the state by the actual entity id.
        self.assertIn("input_number/list", after)
        self.assertIn("config/entity_registry/list", after)
        self.assertIn(
            ("GET", "/states/input_number.hvac_target"),
            self.config_rest.requests[mark[0] :],
        )

    async def test_two_created_helpers_roll_back_in_strict_reverse_order(self):
        created = await self.applied(await self.configuration_plan(*two_helpers()))
        rollback, plan = await self.rollback_plan(created)
        self.assertEqual(rollback["operations_to_delete"], ["create_b", "create_a"])
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.ws.helper_commands("delete"),
            [
                {"type": "input_number/delete", "input_number_id": "hvac_target"},
                {"type": "input_boolean/delete", "input_boolean_id": "hvac_override"},
            ],
        )


class MixedPlanAcceptanceTests(CreatedObjectServiceTestCase):
    async def test_rollback_restores_the_consumer_first_then_deletes_the_helper(self):
        self.add("automation", "consumer")
        original = deepcopy(self.config_rest.configs["automation"]["consumer"])
        proposed = deepcopy(original)
        proposed["description"] = "Uses input_boolean.hvac_override"
        created = await self.applied(
            await self.configuration_plan(
                helper_operation("input_boolean", boolean_config()),
                {
                    "operation_id": "update_consumer",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": "consumer",
                    "depends_on": ["create_helper"],
                    "proposed_config": proposed,
                },
            )
        )
        rollback, plan = await self.rollback_plan(created)
        self.assertEqual(rollback["operations_to_delete"], ["create_helper"])
        self.assertEqual(len(rollback["operations_to_restore"]), 1)
        self.assertEqual(
            [
                (item.resource_type, item.action)
                for item in self.operations(rollback["rollback_plan_id"])
            ],
            [("automation", "update"), ("helper", "delete")],
        )
        seen: list[str] = []
        self.ws.before_helper_delete = lambda *_: seen.append(
            self.config_rest.configs["automation"]["consumer"]["description"]
        )
        mark = self.mark()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        # The consumer no longer referenced the helper when it was deleted.
        self.assertEqual(seen, [original["description"]])
        self.assertEqual(self.config_rest.configs["automation"]["consumer"], original)
        self.assertEqual(
            self.writes_since(mark),
            [
                ("POST", "/config/automation/config/consumer"),
                {"type": "input_boolean/delete", "input_boolean_id": "hvac_override"},
            ],
        )
        self.assertEqual(self.ws.helpers["input_boolean"], {})


class OptionEPositiveTests(CreatedObjectServiceTestCase):
    """§6 E positives: only ``modified_at`` (and the entity id) moved."""

    async def assert_option_e(
        self, created: dict, captured_entity: str, renamed_entity: str, sent: object
    ) -> None:
        captured = json.dumps(self.ws.entries[captured_entity]["modified_at"])
        self.rename_entity(captured_entity, renamed_entity)
        observed = json.dumps(self.ws.entries[renamed_entity]["modified_at"])
        self.assertNotEqual(captured, observed)
        _rollback, plan = await self.rollback_plan(created, approve=False)
        (marker,) = self.markers(plan["plan_id"])
        self.assertEqual(marker["ownership"], rollback_model.MODIFIED_SINCE_CREATE)
        self.assertEqual(marker["basis"], rollback_model.BASIS_MODIFIED_SINCE_CREATE)
        self.assertEqual(marker["captured_modified_at"], captured)
        self.assertEqual(marker["observed_modified_at"], observed)
        self.assertEqual(marker["captured_entity_id"], captured_entity)
        self.assertEqual(marker["observed_entity_id"], renamed_entity)
        decision = self.policy(plan["plan_id"])
        self.assertEqual(decision.policy_class.value, "elevated_admin")
        (operation,) = self.operations(plan["plan_id"])
        self.assertTrue(
            any(rollback_model.E_DISCLOSURE in reason for reason in operation.risk.reasons),
            operation.risk.reasons,
        )
        child = plan["dry_run_results"]["operations"][0]["dry_run_results"]
        disclosed = child["configuration_delete"]["created_object_rollback"]
        self.assertEqual(disclosed["statement"], rollback_model.E_DISCLOSURE)
        # Plan approval alone leaves the elevated acknowledgement pending.
        first = await self.plan_approval_only(plan)
        self.assertEqual(first["status"], "approval_pending", first)
        self.assertEqual(first["approval_action"], "elevated_risk_acknowledgement")
        mark = self.mark()
        await self.approve(plan)
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.writes_since(mark), [sent])
        # Absence is read by the entity id observed at planning: never the
        # captured id.
        states = [
            path
            for method, path in self.config_rest.requests[mark[0] :]
            if method == "GET" and path.startswith("/states/")
        ]
        self.assertIn(f"/states/{renamed_entity}", states)
        self.assertNotIn(f"/states/{captured_entity}", states)
        self.assertNotIn(renamed_entity, self.ws.entries)

    async def test_renamed_created_automation(self):
        created = await self.applied(
            await self.configuration_plan(config_operation("automation", "porch_light"))
        )
        await self.assert_option_e(
            created,
            "automation.porch_light",
            "automation.renamed_porch",
            ("DELETE", "/config/automation/config/porch_light"),
        )

    async def test_renamed_created_script(self):
        created = await self.applied(
            await self.configuration_plan(config_operation("script", "evening"))
        )
        await self.assert_option_e(
            created,
            "script.evening",
            "script.renamed_evening",
            ("DELETE", "/config/script/config/evening"),
        )

    async def test_renamed_created_input_boolean(self):
        created = await self.created_helper("input_boolean")
        await self.assert_option_e(
            created,
            "input_boolean.hvac_override",
            "input_boolean.renamed_override",
            {"type": "input_boolean/delete", "input_boolean_id": "hvac_override"},
        )
        self.assertEqual(self.ws.helpers["input_boolean"], {})

    async def test_renamed_created_input_number(self):
        created = await self.created_helper("input_number")
        await self.assert_option_e(
            created,
            "input_number.hvac_target",
            "input_number.renamed_target",
            {"type": "input_number/delete", "input_number_id": "hvac_target"},
        )
        self.assertEqual(self.ws.helpers["input_number"], {})

    async def test_renamed_created_scene(self):
        created = await self.created_scene()
        await self.assert_option_e(
            created,
            EVENING_ENTITY,
            "scene.renamed_evening",
            ("DELETE", f"{SCENE_PREFIX}{EVENING}"),
        )
        self.assertNotIn(EVENING, self.config_rest.scenes)

    async def test_area_only_change_keeps_the_entity_id_and_is_still_option_e(self):
        created = await self.created_helper("input_boolean")
        entry = self.ws.entries["input_boolean.hvac_override"]
        entry["area_id"] = "synthetic_area"
        entry["modified_at"] = 6.5
        rollback, plan = await self.rollback_plan(created)
        (marker,) = self.markers(rollback["rollback_plan_id"])
        self.assertEqual(marker["basis"], rollback_model.BASIS_MODIFIED_SINCE_CREATE)
        self.assertEqual(marker["observed_modified_at"], "6.5")
        self.assertEqual(marker["observed_entity_id"], marker["captured_entity_id"])
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.ws.helper_commands("delete")), 1)

    async def test_same_registry_id_and_identical_body_replacement_is_option_e(self):
        # A delete and re-register that restores ``id`` and ``created_at`` is
        # indistinguishable from a rename: only ``modified_at`` moved.
        created = await self.created_scene()
        entry = deepcopy(self.ws.entries[EVENING_ENTITY])
        body = deepcopy(self.config_rest.scenes[EVENING])
        self.remove_scene()
        self.config_rest.scenes[EVENING] = body
        entry["modified_at"] = 41.0
        self.ws.entries[EVENING_ENTITY] = entry
        self.config_rest.states.add(EVENING_ENTITY)
        rollback, plan = await self.rollback_plan(created, approve=False)
        (marker,) = self.markers(rollback["rollback_plan_id"])
        # Disclosed and elevated; never reported as proven ownership.
        self.assertEqual(marker["ownership"], rollback_model.MODIFIED_SINCE_CREATE)
        self.assertNotEqual(marker["ownership"], rollback_model.OWNED)
        self.assertEqual(
            self.policy(rollback["rollback_plan_id"]).policy_class.value,
            "elevated_admin",
        )
        first = await self.plan_approval_only(plan)
        self.assertEqual(first["approval_action"], "elevated_risk_acknowledgement")
        self.assertEqual(self.config_rest.scene_deletes(), [])

    async def test_mixed_plan_with_one_e_child_and_one_unchanged_child(self):
        created = await self.applied(await self.configuration_plan(*two_helpers()))
        self.rename_entity("input_boolean.hvac_override", "input_boolean.renamed")
        rollback, plan = await self.rollback_plan(created, approve=False)
        self.assertEqual(rollback["operations_to_delete"], ["create_b", "create_a"])
        unchanged, modified = self.markers(rollback["rollback_plan_id"])
        self.assertEqual(
            (unchanged["source_operation_id"], unchanged["basis"]), ("create_b", "")
        )
        self.assertEqual(
            (modified["source_operation_id"], modified["basis"]),
            ("create_a", rollback_model.BASIS_MODIFIED_SINCE_CREATE),
        )
        decision = self.policy(rollback["rollback_plan_id"])
        self.assertEqual(decision.policy_class.value, "elevated_admin")
        # One plan-level acknowledgement covers the plan.
        first = await self.plan_approval_only(plan)
        self.assertEqual(first["status"], "approval_pending", first)
        self.assertEqual(first["approval_action"], "elevated_risk_acknowledgement")
        self.assertEqual(self.ws.helper_commands("delete"), [])
        await self.approve(plan)
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.ws.helper_commands("delete"),
            [
                {"type": "input_number/delete", "input_number_id": "hvac_target"},
                {"type": "input_boolean/delete", "input_boolean_id": "hvac_override"},
            ],
        )


class OptionEPolicyTests(CreatedObjectServiceTestCase):
    async def assert_e_is_not_safety_critical(self, created: dict, entity: str) -> None:
        self.rename_entity(entity)
        rollback, _plan = await self.rollback_plan(created, approve=False)
        plan = self.service._load(rollback["rollback_plan_id"])
        owned = deepcopy(plan)
        (marker,) = rollback_model.markers(owned.operations[0])
        marker["observed_modified_at"] = marker["captured_modified_at"]
        marker["observed_entity_id"] = marker["captured_entity_id"]
        marker["ownership"] = rollback_model.OWNED
        marker["basis"] = ""
        elevated = evaluate_change_policy(plan)
        baseline = evaluate_change_policy(owned)
        self.assertEqual(elevated.policy_class.value, "elevated_admin")
        self.assertEqual(baseline.policy_class.value, "standard_admin")
        # The ownership basis changes the class and the reasons only.
        self.assertEqual(elevated.physical_consequence, baseline.physical_consequence)
        self.assertNotEqual(elevated.physical_consequence.value, "safety_critical")
        self.assertIn(rollback_model.BASIS_MODIFIED_SINCE_CREATE, elevated.reason_codes)
        self.assertNotIn(
            rollback_model.BASIS_MODIFIED_SINCE_CREATE, baseline.reason_codes
        )
        self.assertFalse(
            any(
                item.value.startswith("safety")
                for item in elevated.required_acknowledgements
            )
        )

    async def test_option_e_alone_never_sets_safety_critical_for_a_scene(self):
        await self.assert_e_is_not_safety_critical(
            await self.created_scene(), EVENING_ENTITY
        )

    async def test_option_e_alone_never_sets_safety_critical_for_a_helper(self):
        await self.assert_e_is_not_safety_critical(
            await self.created_helper("input_number"), "input_number.hvac_target"
        )


if __name__ == "__main__":
    unittest.main()
