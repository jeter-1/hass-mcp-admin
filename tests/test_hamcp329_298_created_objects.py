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

from ha_mcp_engineering.f3_configuration import (  # noqa: E402
    created_objects as created_adapters,
    models as configuration_models,
)
from ha_mcp_engineering.f3_runtime import (  # noqa: E402
    pre_dispatch_reasons as record_bounds,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    created_object_identity as identity_model,
    created_object_rollback as rollback_model,
    entity_availability_exposure as availability_exposure,
    entity_metadata,
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


OBSERVED = "scene_create_observed_"
AMBIGUOUS = "scene_create_registry_entry_ambiguous"

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


class SceneRestoredRegistryMetadataTests(CreatedObjectServiceTestCase):
    """§3.2.1 and the §3.3 readback table: restored metadata is not a failure.

    Registering ``(scene, homeassistant, <config id>)`` again takes back the
    deleted entry's ``disabled_by``, ``hidden_by``, area and options.  The
    create is verified on the exact stored configuration and the one registry
    identity; what the entry carries is reported, and the governed undo can
    still delete the scene.
    """

    def restores(self, **fields: object) -> None:
        def settle(config_id: str) -> None:
            entity_id = self.config_rest.scene_entity(config_id)
            entry = self.ws.entries[entity_id]
            entry.update(deepcopy(fields))
            if entry["disabled_by"] is not None:
                # A disabled entity is never added, so it has no state.
                self.config_rest.states.discard(entity_id)

        self.config_rest.after_scene_post = settle

    async def assert_created_then_deleted(self, *reported: str) -> None:
        plan = await self.scene_plan()
        await self.approve(plan)
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.children(plan["plan_id"]),
            [("scene_create_1", "terminal", "succeeded_verified", 1)],
        )
        codes = self.codes(plan["plan_id"])
        self.assertEqual(
            {code for code in codes if code.startswith(OBSERVED)},
            {f"{OBSERVED}{name}" for name in reported},
        )
        self.assertIn("exact_readback_verified", codes)
        self.assertNotIn(AMBIGUOUS, codes)
        self.assertEqual(len(self.config_rest.scene_posts), 1)
        # The identity is the registry entry's own, so the undo can find it.
        (value,) = self.identity_values(plan["plan_id"])
        self.assertIsNotNone(value)
        self.assertEqual(value["outcome"], identity_model.CAPTURED, value)
        identity = value["identity"]
        self.assertEqual(identity["entity_id"], EVENING_ENTITY)
        self.assertEqual(identity["registry_unique_id"], EVENING)
        self.assertEqual(identity["registry_platform"], "homeassistant")
        self.assertEqual(
            identity["delete_argument"],
            {"method": "DELETE", "path": f"{SCENE_PREFIX}{EVENING}"},
        )
        rollback, delete_plan = await self.rollback_plan(plan)
        self.assertEqual(rollback["operations_to_delete"], ["scene_create_1"])
        self.assertEqual(rollback["operations_excluded"], [])
        self.assertEqual(self.config_rest.scene_deletes(), [])
        result = await self.apply(delete_plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.children(delete_plan["plan_id"]),
            [("delete_1", "terminal", "succeeded_verified", 1)],
        )
        self.assertEqual(
            self.config_rest.scene_deletes(), [("DELETE", f"{SCENE_PREFIX}{EVENING}")]
        )
        self.assertNotIn(EVENING, self.config_rest.scenes)
        self.assertNotIn(EVENING_ENTITY, self.ws.entries)
        self.assertNotIn(EVENING_ENTITY, self.config_rest.states)
        self.assertEqual(self.service_calls(), [])

    async def test_a_restored_disabled_scene_is_verified_reported_and_undoable(self):
        self.restores(disabled_by="user")
        await self.assert_created_then_deleted("disabled_by_user")

    async def test_restored_hidden_area_and_exposure_are_reported_not_failures(self):
        self.restores(
            hidden_by="user",
            area_id="synthetic_area",
            options={
                "conversation": {"should_expose": True},
                "cloud.alexa": {"should_expose": False},
                "cloud.google_assistant": {"synthetic_other": True},
            },
        )
        await self.assert_created_then_deleted(
            "hidden_by_user",
            "area_assigned",
            "exposed_conversation",
            "unexposed_cloud_alexa",
        )

    async def test_every_restored_field_at_once_is_still_one_verified_create(self):
        self.restores(
            disabled_by="integration",
            hidden_by="integration",
            area_id="synthetic_area",
            options={
                "cloud.alexa": {"should_expose": False},
                "cloud.google_assistant": {"should_expose": True},
                "conversation": {"should_expose": True},
            },
        )
        # The largest report there is: all six codes reach the child's
        # durable execution record whole.
        await self.assert_created_then_deleted(
            "disabled_by_integration",
            "hidden_by_integration",
            "area_assigned",
            "unexposed_cloud_alexa",
            "exposed_cloud_google_assistant",
            "exposed_conversation",
        )

    async def test_a_fresh_registry_entry_reports_no_restored_metadata(self):
        created = await self.created_scene()
        codes = self.codes(created["plan_id"])
        self.assertEqual({code for code in codes if code.startswith(OBSERVED)}, set())
        self.assertIn("exact_readback_verified", codes)

    async def created_then_undone(self, *reported: str) -> str:
        """Create the scene, undo it, and return the create's evidence hash."""

        created = await self.created_scene()
        self.assertEqual(
            {code for code in self.codes(created["plan_id"]) if code.startswith(OBSERVED)},
            {f"{OBSERVED}{name}" for name in reported},
        )
        (record,) = self.child_records(self.task_id(created["plan_id"]))
        self.assertEqual(record.normalized_outcome, "succeeded_verified")
        value = record.evidence["evidence_hash"]
        _rollback, delete_plan = await self.rollback_plan(created)
        result = await self.apply(delete_plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertNotIn(EVENING_ENTITY, self.ws.entries)
        return value

    async def test_restored_metadata_is_bound_in_the_verification_evidence_hash(self):
        # The same create of the same absent scene gives the same evidence,
        # so a different hash below can only come from the restored metadata.
        fresh = await self.created_then_undone()
        self.assertEqual(await self.created_then_undone(), fresh)
        self.restores(disabled_by="user")
        disabled = await self.created_then_undone("disabled_by_user")
        self.restores(hidden_by="user")
        hidden = await self.created_then_undone("hidden_by_user")
        self.assertEqual(len({fresh, disabled, hidden}), 3, (fresh, disabled, hidden))


class ObservedRegistryMetadataTests(unittest.TestCase):
    """The closed codes a verified scene create reports for its one entry."""

    def observed(self, **fields: object) -> tuple[str, ...]:
        entry = {
            "entity_id": EVENING_ENTITY,
            "platform": "homeassistant",
            "unique_id": EVENING,
            "disabled_by": None,
            "hidden_by": None,
            "area_id": None,
            **fields,
        }
        before = deepcopy(entry)
        codes = created_adapters.observed_registry_metadata(entry)
        self.assertEqual(entry, before, "the observed entry is never changed")
        return codes

    def test_nothing_set_reports_nothing(self):
        self.assertEqual(self.observed(), ())
        self.assertEqual(self.observed(options={}), ())
        self.assertEqual(created_adapters.observed_registry_metadata({}), ())

    def test_the_vocabularies_are_the_reviewed_registry_ones(self):
        self.assertEqual(
            set(created_adapters.OBSERVED_DISABLED_BY),
            availability_exposure._DISABLED_BY_VALUES - {None},
        )
        self.assertEqual(
            set(created_adapters.OBSERVED_HIDDEN_BY),
            entity_metadata._HIDDEN_BY_VALUES - {None},
        )
        self.assertEqual(
            created_adapters.OBSERVED_ASSISTANTS, availability_exposure.KNOWN_ASSISTANTS
        )

    def test_every_known_disabled_by_and_hidden_by_value_has_its_own_code(self):
        for value in sorted(created_adapters.OBSERVED_DISABLED_BY):
            with self.subTest(disabled_by=value):
                self.assertEqual(
                    self.observed(disabled_by=value), (f"{OBSERVED}disabled_by_{value}",)
                )
        for value in sorted(created_adapters.OBSERVED_HIDDEN_BY):
            with self.subTest(hidden_by=value):
                self.assertEqual(
                    self.observed(hidden_by=value), (f"{OBSERVED}hidden_by_{value}",)
                )

    def test_an_unknown_value_is_reported_as_unrecognized_never_echoed(self):
        for value in ("Synthetic Value", "user ", "x" * 200, 7, True, ["user"]):
            with self.subTest(value=value):
                self.assertEqual(
                    self.observed(disabled_by=value),
                    (f"{OBSERVED}disabled_by_unrecognized",),
                )
                self.assertEqual(
                    self.observed(hidden_by=value),
                    (f"{OBSERVED}hidden_by_unrecognized",),
                )

    def test_an_area_is_reported_by_presence_only(self):
        for value in ("synthetic_area", "Synthetic Area " * 40, 7):
            with self.subTest(area_id=value):
                self.assertEqual(
                    self.observed(area_id=value), (f"{OBSERVED}area_assigned",)
                )

    def test_only_a_null_area_is_no_area(self):
        # The code as written: presence is ``is not None``, not truthiness.
        # Whether Core can store such a value is not established here.
        for value in ("", 0, False):
            with self.subTest(area_id=value):
                self.assertEqual(
                    self.observed(area_id=value), (f"{OBSERVED}area_assigned",)
                )

    def test_only_a_stored_boolean_exposure_of_a_known_assistant_is_reported(self):
        self.assertEqual(
            self.observed(
                options={
                    "cloud.alexa": {"should_expose": True},
                    "cloud.google_assistant": {"should_expose": False},
                    "conversation": {"should_expose": True},
                    "synthetic.assistant": {"should_expose": True},
                }
            ),
            (
                f"{OBSERVED}exposed_cloud_alexa",
                f"{OBSERVED}unexposed_cloud_google_assistant",
                f"{OBSERVED}exposed_conversation",
            ),
        )
        for options in (
            None,
            "conversation",
            ["conversation"],
            {"conversation": None},
            {"conversation": True},
            {"conversation": {}},
            {"conversation": {"should_expose": "true"}},
            {"conversation": {"should_expose": 1}},
            {"conversation": {"should_expose": None}},
        ):
            with self.subTest(options=options):
                self.assertEqual(self.observed(options=options), ())

    def test_the_full_report_fits_the_durable_diagnostic_bounds(self):
        codes = self.observed(
            disabled_by="config_entry",
            hidden_by="integration",
            area_id="synthetic_area",
            options={
                name: {"should_expose": False}
                for name in created_adapters.OBSERVED_ASSISTANTS
            },
        )
        self.assertEqual(len(codes), 6)
        self.assertEqual(len(set(codes)), 6)
        recorded = ("exact_readback_completed", *codes)
        kept = sorted(recorded, key=lambda item: item.encode("utf-8"))
        # The observation's own canonical form keeps every code.
        self.assertEqual(list(configuration_models.bounded_codes(recorded)), kept)
        # The execution-record bound, read from its restatement in
        # ``f3_runtime/pre_dispatch_reasons.py``, which
        # ``test_hamcp249_pre_dispatch_reasons`` pins equal to ``f3/models.py``:
        # nothing is dropped and nothing is shortened.  The service test of
        # every restored field proves the same through the record itself.
        self.assertEqual(list(record_bounds.bounded_diagnostics(recorded)), kept)
        self.assertLessEqual(len(recorded), record_bounds.MAX_DIAGNOSTIC_ITEMS)
        for code in recorded:
            self.assertIsNotNone(record_bounds.EVIDENCE_PATTERN.fullmatch(code), code)
            self.assertLessEqual(len(code), record_bounds.MAX_DIAGNOSTIC_LENGTH)


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
