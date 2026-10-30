"""HAMCP-071: governed scene configuration edit, end to end and offline.

Real ``ChangeGovernanceService``, real F3 runtime, real
``SceneConfigurationGateway`` over a Core-faithful synthetic scene store,
entity registry and state machine shaped from the architect's read-only
observation.  Positive edits, inactive refusal, planning refusals, stale
state, failure truthfulness, rollback, disclosure and redaction.  Saving a
scene never activates it: every scene service, DELETE and reload fails the
fixture.  No live Home Assistant.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering import untrusted_text  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
)
from ha_mcp_engineering.f3_runtime import dispatch_facts as df  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import _core_call_window  # noqa: E402
from ha_mcp_engineering.governance import scene_configuration as contract  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    scene_configuration_planning as planning,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationMutationNotDispatchedError,
)
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    ExecutionTaskState,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402

from tests.hamcp071_fixtures import (  # noqa: E402
    CONSUMER,
    FAN,
    FRONT_LOCK,
    GARAGE,
    INTEGRATION_ENTITY,
    LAMP,
    LEAVING,
    LEAVING_ENTITY,
    LOUNGE,
    LOUNGE_ENTITY,
    REGISTRY_LIST,
    STATE_ONLY,
    STRIP,
    YAML_ID,
    SceneServiceTestCase,
    lamp_snapshot,
    leaving_config,
    lounge_config,
    scene_registry_entry,
    strip_snapshot,
)

# Screened as credential text by untrusted_text.screen.
CREDENTIAL_TEXT = "-----BEGIN RSA " + "PRIVATE KEY-----"
INACTIVE_DETAILS = {
    "reason": "pending_signed_registry_admission",
    "reason_code": "pending_signed_registry_admission",
    "resource_type": "scene_configuration",
    "core_capability": "core.scene_configuration_operation",
    "plan_created": False,
    "provider_dispatch_occurred": False,
}


def body(config: dict) -> dict:
    """The exact POST body Core receives for ``config``."""

    return {key: value for key, value in config.items() if key != "id"}


def raw_operation(proposed: dict | None = None) -> list[dict]:
    return [{
        "operation_id": planning.OPERATION_ID,
        "resource_type": contract.RESOURCE_TYPE,
        "action": contract.ACTION,
        "target_id": LOUNGE,
        "proposed_config": proposed if proposed is not None else {"name": "x"},
    }]


def store_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class _Base(SceneServiceTestCase):
    def assert_refused(self, raised, reason_code: str, code=None) -> None:
        error = raised.exception
        self.assertEqual(error.details.get("reason_code"), reason_code, error.details)
        self.assertIs(error.details.get("plan_created"), False)
        self.assertIs(error.details.get("provider_dispatch_occurred"), False)
        if code is not None:
            self.assertEqual(error.code, code)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.writes(), [])
        self.assert_never_sent()

    async def assert_create_refused(self, reason_code: str, code=None, **arguments):
        with self.assertRaises(GovernanceError) as raised:
            await self.create(**arguments)
        self.assert_refused(raised, reason_code, code)
        return raised.exception

    def assert_outcome(self, result, task_state, outcomes, codes=()) -> None:
        self.assertEqual(result["task_state"], task_state, result)
        self.assertEqual(self.outcomes(result["task_id"]), list(outcomes))
        self.assertTrue(
            set(codes) <= self.child_codes(result["task_id"]),
            self.child_codes(result["task_id"]),
        )
        self.assert_never_sent()

    async def applied(self, **arguments) -> tuple[dict, dict]:
        created = await self.approved(**arguments)
        return created, await self.apply(created)

    def disclosure(self, created: dict) -> dict:
        plan = self.service._load(created["plan_id"])
        return plan.operations[0].dry_run_results["scene_configuration"]

    def stored(self, config_id: str = LOUNGE) -> dict:
        return deepcopy(self.scene_rest.config(config_id))

    async def approve_rollback(self, rollback: dict) -> dict:
        created = {"plan_id": rollback["rollback_plan_id"],
                   "plan_hash": rollback["plan_hash"]}
        await self.approve(created)
        return created

    async def restore(self, created: dict) -> dict:
        rollback = await self.service.rollback_change(created["plan_id"])
        return await self.apply(await self.approve_rollback(rollback))


class PositiveTests(_Base):
    async def test_each_field_alone_is_saved_and_verified_exactly(self):
        changed_lamp = lamp_snapshot(brightness=200)
        cases = (
            ({"name": "Synthetic Lounge bright"}, {"name": "Synthetic Lounge bright"}),
            ({"icon": "mdi:lightbulb-on"}, {"icon": "mdi:lightbulb-on"}),
            ({"icon": None}, {"icon": None}),
            ({"entities": {LAMP: changed_lamp}},
             {"entities": {LAMP: changed_lamp, STRIP: strip_snapshot()}}),
        )
        for arguments, change in cases:
            with self.subTest(arguments=arguments):
                self.scene_rest.set_external(LOUNGE, **lounge_config())
                writes = len(self.writes())
                _created, result = await self.applied(config_id=LOUNGE, **arguments)
                self.assert_outcome(
                    result, "succeeded_verified", ["succeeded_verified"],
                    ["exact_readback_verified"],
                )
                expected = lounge_config()
                for key, value in change.items():
                    if value is None:
                        expected.pop(key)
                    else:
                        expected[key] = value
                self.assertEqual(self.writes()[writes:], [(LOUNGE, body(expected))])
                self.assertEqual(self.stored(), expected)

    async def test_all_fields_together(self):
        entities = {LAMP: lamp_snapshot(brightness=10), STRIP: None,
                    FAN: {"state": "on", "percentage": 33}}
        _created, result = await self.applied(
            config_id=LOUNGE, entity_id=LOUNGE_ENTITY, name="Synthetic Lounge night",
            icon="mdi:weather-night", entities=entities,
        )
        self.assert_outcome(result, "succeeded_verified", ["succeeded_verified"],
                            ["exact_readback_verified"])
        expected = {
            "id": LOUNGE,
            "name": "Synthetic Lounge night",
            "icon": "mdi:weather-night",
            "entities": {LAMP: lamp_snapshot(brightness=10),
                         FAN: {"state": "on", "percentage": 33}},
            "metadata": {},
        }
        self.assertEqual(self.writes(), [(LOUNGE, body(expected))])
        self.assertEqual(self.stored(), expected)
        # The reload updated the loaded scene.
        loaded = self.scene_rest.states[LOUNGE_ENTITY]["attributes"]
        self.assertEqual(sorted(loaded["entity_id"]), sorted([LAMP, FAN]))
        self.assertEqual(self.ws.entries[LOUNGE_ENTITY]["original_name"],
                         "Synthetic Lounge night")

    async def test_entity_add_remove_and_change(self):
        cases = (
            ({FAN: {"state": "off"}}, "added"),
            ({STRIP: None}, "removed"),
            ({STRIP: strip_snapshot(state="on")}, "changed"),
        )
        for patch, change in cases:
            with self.subTest(change=change):
                self.scene_rest.set_external(LOUNGE, **lounge_config())
                created, result = await self.applied(config_id=LOUNGE, entities=patch)
                self.assert_outcome(result, "succeeded_verified", ["succeeded_verified"])
                expected = lounge_config()["entities"]
                for member, value in patch.items():
                    if value is None:
                        expected.pop(member)
                    else:
                        expected[member] = value
                self.assertEqual(self.stored()["entities"], expected)
                rows = self.disclosure(created)["entities"]
                self.assertEqual([(row["entity_id"], row["change"]) for row in rows],
                                 [(next(iter(patch)), change)])

    async def test_full_attribute_snapshot_round_trips_exactly(self):
        snapshot = lamp_snapshot(
            brightness=255, hs_color=[0.0, 0.0], rgb_color=[255, 255, 255],
            xy_color=[0.323, 0.329], effect=None, effect_list=["none", "Rainbow"],
            nested={"a": [1, 2.5, True, None, "x"]},
        )
        _created, result = await self.applied(config_id=LOUNGE,
                                              entities={LAMP: snapshot})
        self.assert_outcome(result, "succeeded_verified", ["succeeded_verified"],
                            ["exact_readback_verified"])
        stored = self.stored()["entities"]
        self.assertEqual(stored[LAMP], snapshot)
        self.assertEqual(contract.canonical(stored[LAMP]), contract.canonical(snapshot))
        # The unrequested member is re-sent exactly as stored.
        self.assertEqual(stored[STRIP], strip_snapshot())
        self.assertEqual(self.writes()[0][1]["entities"][STRIP], strip_snapshot())

    async def test_name_is_stored_exactly(self):
        for name in ("Lounge  dim ", " Leading", "Ünïcødé ✓", "x" * contract.MAX_NAME_LENGTH):
            with self.subTest(name=name):
                _created, result = await self.applied(config_id=LOUNGE, name=name)
                self.assert_outcome(result, "succeeded_verified", ["succeeded_verified"])
                self.assertEqual(self.writes()[-1][1]["name"], name)
                self.assertEqual(self.stored()["name"], name)

    async def test_state_only_member_is_known(self):
        _created, result = await self.applied(
            config_id=LOUNGE, entities={STATE_ONLY: {"state": "12"}}
        )
        self.assert_outcome(result, "succeeded_verified", ["succeeded_verified"])
        self.assertEqual(self.stored()["entities"][STATE_ONLY], {"state": "12"})

    async def test_body_never_carries_the_id_and_metadata_is_kept(self):
        self.scene_rest.set_external(LOUNGE, metadata={LAMP: {"entity_only": True}})
        await self.applied(config_id=LOUNGE, name="Renamed")
        sent = self.writes()[0][1]
        self.assertNotIn("id", sent)
        self.assertEqual(sent["metadata"], {LAMP: {"entity_only": True}})
        self.assertEqual(self.stored()["metadata"], {LAMP: {"entity_only": True}})
        self.assertEqual(self.stored()["id"], LOUNGE)

    async def test_planning_reads_only_and_sends_nothing(self):
        created = await self.create(config_id=LOUNGE, name="Synthetic Lounge bright")
        self.assertEqual(created["status"], "awaiting_approval", created)
        self.assertEqual(self.writes(), [])
        self.assertEqual(
            {method for method, _path in self.scene_rest.requests}, {"GET"}
        )
        self.assert_never_sent()
        plan = self.service._load(created["plan_id"])
        operation = plan.operations[0]
        self.assertEqual(operation.resource_type, contract.RESOURCE_TYPE)
        self.assertEqual(operation.target_id, LOUNGE)
        self.assertEqual(operation.current_config["config"], lounge_config())
        self.assertEqual(operation.current_config["entity_id"], LOUNGE_ENTITY)
        self.assertIs(operation.current_config["loaded_matches_config"], True)
        self.assertEqual(operation.proposed_config["config"]["name"],
                         "Synthetic Lounge bright")

    async def test_other_scenes_are_never_changed(self):
        before = self.stored(LEAVING)
        await self.applied(config_id=LOUNGE, name="Renamed")
        self.assertEqual(self.stored(LEAVING), before)
        self.assertEqual([config_id for config_id, _ in self.writes()], [LOUNGE])

    async def test_elevated_scene_remains_actionable(self):
        # The consequence is high; the execution contract is exact.
        created, result = await self.applied(config_id=LEAVING, name="Leaving now")
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        self.assert_outcome(result, "succeeded_verified", ["succeeded_verified"],
                            ["exact_readback_verified"])
        self.assertEqual(self.stored(LEAVING)["entities"], leaving_config()["entities"])
        # Saving never activated the scene: members keep their states.
        self.assertEqual(self.scene_rest.states[GARAGE]["state"], "closed")
        self.assertEqual(self.scene_rest.states[FRONT_LOCK]["state"], "locked")


class DisclosureTests(_Base):
    async def test_before_after_for_each_field_and_member(self):
        created = await self.create(
            config_id=LOUNGE, name="Synthetic Lounge night", icon=None,
            entities={STRIP: None, FAN: {"state": "on"}},
        )
        disclosure = self.disclosure(created)
        self.assertEqual(disclosure["model"], planning.DISCLOSURE_MODEL)
        self.assertEqual(disclosure["config_id"], LOUNGE)
        self.assertEqual(disclosure["entity_id"], LOUNGE_ENTITY)
        self.assertEqual(
            [(row["field"], row["before"], row["after"], row["requested"], row["changed"])
             for row in disclosure["fields"]],
            [
                ("name", "Synthetic Lounge dim", "Synthetic Lounge night", True, True),
                ("icon", "mdi:floor-lamp", None, True, True),
            ],
        )
        self.assertEqual(
            [(row["entity_id"], row["change"], row["before"], row["after"])
             for row in disclosure["entities"]],
            [
                (STRIP, "removed", strip_snapshot(), None),
                (FAN, "added", None, {"state": "on"}),
            ],
        )
        self.assertEqual(disclosure["unchanged_entity_count"], 1)
        self.assertEqual(
            (disclosure["entity_count_before"], disclosure["entity_count_after"]), (2, 2)
        )
        self.assertEqual(disclosure["changed_fields"], ["name", "icon", "entities"])
        self.assertEqual(disclosure["unrequested_fields"],
                         "sent_exactly_as_stored_and_verified_unchanged")
        self.assertIs(disclosure["configuration_fingerprint_bound"], True)
        self.assertEqual(disclosure["provider"], contract.PROVIDER)
        self.assertEqual(disclosure["fallback"], "none")

    async def test_saving_does_not_activate_and_reload_is_disclosed(self):
        created = await self.create(config_id=LOUNGE, name="Renamed")
        disclosure = self.disclosure(created)
        self.assertIs(disclosure["activated"], False)
        self.assertEqual(disclosure["activation"], planning.NOT_ACTIVATED_DISCLOSURE)
        self.assertIn("does not activate", planning.NOT_ACTIVATED_DISCLOSURE)
        self.assertEqual(disclosure["reload"], planning.RELOAD_DISCLOSURE)
        self.assertIn("every UI and YAML scene", planning.RELOAD_DISCLOSURE)
        self.assertEqual(disclosure["id_assignment"], planning.ID_ASSIGNMENT_DISCLOSURE)
        self.assertEqual(disclosure["member_order"], planning.MEMBER_ORDER_DISCLOSURE)
        self.assertEqual(disclosure["metadata"], planning.METADATA_DISCLOSURE)
        plan = self.service._load(created["plan_id"])
        plan_level = plan.dry_run_results["scene_configuration"]
        self.assertEqual(plan_level["activation"], planning.NOT_ACTIVATED_DISCLOSURE)
        self.assertEqual(plan_level["never_sent"], list(contract.NEVER_SENT))
        for never in ("scene.turn_on", "scene.apply", "scene.create",
                      "DELETE /api/config/scene/config/{id}"):
            self.assertIn(never, plan_level["never_sent"])
        self.assertEqual(plan_level["execution"], "single_update_at_most_once")
        self.assertEqual(plan_level["residual_concurrency_window"],
                         planning.CONCURRENCY_DISCLOSURE)
        self.assertEqual(plan_level["rollback"], planning.ROLLBACK_DISCLOSURE)

    async def test_activating_automations_and_scripts_are_listed(self):
        created = await self.create(config_id=LOUNGE, name="Renamed")
        consumers = self.disclosure(created)["consumers"]
        self.assertEqual(consumers["statement"], planning.CONSUMER_DISCLOSURE)
        self.assertIn(CONSUMER, str(consumers["consumers"]))
        self.assertEqual(consumers["consumer_count"], 1)
        self.assertEqual(self.exposure_reads, [False])

    async def test_unavailable_consumer_evidence_is_a_disclosed_gap(self):
        async def failing(*, fenced: bool):
            raise HomeAssistantApiError("synthetic index failure")

        self.resource_gateway.availability_exposure_snapshot_reader = failing
        created = await self.create(config_id=LOUNGE, name="Renamed")
        consumers = self.disclosure(created)["consumers"]
        self.assertIs(consumers["evidence_complete"], False)
        self.assertNotEqual(consumers.get("status"), "complete")
        self.assertEqual(consumers["consumers"], [])
        self.assertTrue(consumers["evidence_gaps"])
        self.resource_gateway.availability_exposure_snapshot_reader = None
        created = await self.create(config_id=LOUNGE, name="Renamed again")
        consumers = self.disclosure(created)["consumers"]
        self.assertEqual(consumers["status"], "unavailable")
        self.assertIsNone(consumers["consumer_count"])
        self.assertEqual(consumers["evidence_gaps"], ["dependency_index_unavailable"])

    async def test_standard_policy_for_harmless_members(self):
        created = await self.create(config_id=LOUNGE, name="Renamed")
        decision = created["policy_decision"]
        self.assertEqual(decision["policy_class"], "standard_admin")
        self.assertEqual(decision["physical_consequence"], "none")
        basis = self.service._load(created["plan_id"]).operations[0].dry_run_results[
            "scene_configuration"]
        self.assertEqual(self.disclosure(created)["consequence_statements"], [])
        self.assertIsNotNone(basis)
        granted = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(granted["status"], "approval_pending")
        self.assertIs(granted["external_approval_required"], True)

    async def test_lock_or_garage_cover_elevates_before_or_after(self):
        self.scene_rest.add_member("cover.synthetic_blind", "open", device_class="blind")
        cases = (
            # Already in the scene (garage cover and lock).
            (LEAVING, {"name": "Leaving now"}),
            # Removing the protected members still elevates (before).
            (LEAVING, {"entities": {GARAGE: None}}),
            # Adding one elevates (after).
            (LOUNGE, {"entities": {FRONT_LOCK: {"state": "unlocked"}}}),
            (LOUNGE, {"entities": {GARAGE: {"state": "open"}}}),
        )
        for config_id, arguments in cases:
            with self.subTest(config_id=config_id, arguments=arguments):
                created = await self.create(config_id=config_id, **arguments)
                decision = created["policy_decision"]
                self.assertEqual(decision["policy_class"], "elevated_admin")
                self.assertEqual(decision["physical_consequence"], "indirect")
                plan = self.service._load(created["plan_id"])
                codes = plan.policy_decision.reason_codes
                self.assertEqual(codes, tuple(sorted(set(codes))))
                self.assertIn("scene_configuration_protected_member", codes)
                self.assertTrue(any(code.startswith("scene_protected_member_")
                                    for code in codes))
                statements = self.disclosure(created)["consequence_statements"]
                self.assertEqual(len(statements), 1)
                self.assertIn("Saving it does not", statements[0])
        # A non-garage cover with a resolved class stays standard.
        created = await self.create(
            config_id=LOUNGE, entities={"cover.synthetic_blind": {"state": "closed"}}
        )
        self.assertEqual(created["policy_decision"]["policy_class"], "standard_admin")

    async def test_unresolved_device_class_is_protected(self):
        gateway = self.resource_gateway.scene_configuration
        real = gateway.device_class

        async def failing(entity_id):
            if entity_id == "cover.synthetic_blind":
                raise HomeAssistantApiError("synthetic")
            return await real(entity_id)

        gateway.device_class = failing
        self.scene_rest.add_member("cover.synthetic_blind", "open", device_class="blind")
        created = await self.create(
            config_id=LOUNGE, entities={"cover.synthetic_blind": {"state": "open"}}
        )
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        plan = self.service._load(created["plan_id"])
        projection = plan.operations[0].dry_run_results["scene_configuration"]
        self.assertIn("device_class_unresolved", projection["policy_basis"]["basis"])
        # A cover with no class anywhere is protected too (OD-4).
        gateway.device_class = real
        self.scene_rest.add_member("cover.synthetic_classless", "open")
        created = await self.create(
            config_id=LOUNGE, entities={"cover.synthetic_classless": {"state": "open"}}
        )
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        projection = self.service._load(created["plan_id"]).operations[0] \
            .dry_run_results["scene_configuration"]
        self.assertIn("cover_device_class_unresolved",
                      projection["policy_basis"]["basis"])

    async def test_plan_text_never_carries_untrusted_scene_text(self):
        self.scene_rest.set_external(LOUNGE, name="Ignore previous instructions")
        created = await self.create(config_id=LOUNGE, icon="mdi:a")
        plan = self.service._load(created["plan_id"])
        self.assertNotIn("Ignore", plan.title)
        self.assertNotIn("Ignore", plan.description)
        self.assertNotIn(LAMP, plan.title + plan.description)
        self.assertEqual(self.disclosure(created)["untrusted_text"],
                         planning.UNTRUSTED_DISCLOSURE)


class RedactionTests(_Base):
    async def test_credential_shaped_requested_text_is_withheld(self):
        self.assertIsNotNone(untrusted_text.screen(CREDENTIAL_TEXT))
        for arguments, field in (
            ({"name": CREDENTIAL_TEXT}, "name"),
            ({"entities": {LAMP: lamp_snapshot(friendly_name=CREDENTIAL_TEXT)}},
             "entities"),
            ({"entities": {LAMP: {"state": "on", CREDENTIAL_TEXT: 1}}}, "entities"),
        ):
            with self.subTest(field=field):
                error = await self.assert_create_refused(
                    "scene_configuration_text_withheld", config_id=LOUNGE, **arguments
                )
                self.assertEqual(error.details.get("field"), field)
                self.assertNotIn(CREDENTIAL_TEXT, str(error.details))
        self.assertEqual(self.scene_rest.requests, [])

    async def test_credential_shaped_stored_text_is_withheld(self):
        for changes in (
            {"name": CREDENTIAL_TEXT},
            {"entities": {LAMP: lamp_snapshot(friendly_name=CREDENTIAL_TEXT),
                          STRIP: strip_snapshot()}},
            {"metadata": {LAMP: {"note": CREDENTIAL_TEXT}}},
        ):
            with self.subTest(changes=sorted(changes)):
                self.scene_rest.set_external(LOUNGE, **lounge_config())
                self.scene_rest.set_external(LOUNGE, **changes)
                error = await self.assert_create_refused(
                    "scene_configuration_text_withheld", config_id=LOUNGE,
                    icon="mdi:a",
                )
                self.assertNotIn(CREDENTIAL_TEXT, str(error.details))
        audit = self.audit_path.read_text() if self.audit_path.exists() else ""
        self.assertNotIn(CREDENTIAL_TEXT, audit)


class PlanningRefusalTests(_Base):
    async def test_unknown_config_id(self):
        await self.assert_create_refused(
            "scene_configuration_target_not_found", ErrorCode.ENTITY_NOT_FOUND,
            config_id="1799999999999", name="x",
        )

    async def test_integration_scene_is_refused(self):
        await self.assert_create_refused(
            "scene_configuration_not_ui_managed", config_id="presence_sim_1", name="x"
        )
        self.assertEqual(self.ws.entries[INTEGRATION_ENTITY]["platform"],
                         "synthetic_integration")

    async def test_yaml_scene_outside_the_config_store_is_refused(self):
        await self.assert_create_refused(
            "scene_configuration_not_ui_managed", config_id=YAML_ID, name="x"
        )

    async def test_entity_id_must_match_the_config_id(self):
        await self.assert_create_refused(
            "scene_configuration_target_mismatch", config_id=LOUNGE,
            entity_id=LEAVING_ENTITY, name="x",
        )

    async def test_config_id_is_not_an_entity_id(self):
        for config_id in (LOUNGE_ENTITY, "scene/1", "", "a" * 65, 1700000000001, None):
            with self.subTest(config_id=config_id):
                error = await self.assert_create_refused(
                    planning.INVALID_VALUE, config_id=config_id, name="x"
                )
                self.assertEqual(error.details.get("field"), "config_id")
        self.assertEqual(self.scene_rest.requests, [])

    async def test_unloaded_scene_is_refused(self):
        # Stored but not yet reloaded: the loaded scene does not match.
        self.scene_rest.set_external(LOUNGE, reload=False, name="Saved elsewhere")
        await self.assert_create_refused(
            "scene_configuration_not_loaded", config_id=LOUNGE, icon="mdi:a"
        )

    async def test_scene_with_no_registry_entry_is_not_loaded(self):
        del self.ws.entries[LOUNGE_ENTITY]
        await self.assert_create_refused(
            "scene_configuration_not_loaded", config_id=LOUNGE, icon="mdi:a"
        )

    async def test_disabled_scene_is_refused(self):
        self.ws.entries[LOUNGE_ENTITY]["disabled_by"] = "user"
        await self.assert_create_refused(
            "scene_configuration_target_disabled", config_id=LOUNGE, icon="mdi:a"
        )

    async def test_ambiguous_registry_identity_is_refused(self):
        self.ws.entries["scene.synthetic_lounge_copy"] = scene_registry_entry(
            "scene.synthetic_lounge_copy", LOUNGE, lounge_config()
        )
        await self.assert_create_refused(
            "scene_configuration_target_ambiguous", config_id=LOUNGE, icon="mdi:a"
        )

    async def test_unreviewable_stored_configuration_is_refused(self):
        self.scene_rest.config(LOUNGE)["unreviewed_key"] = True
        await self.assert_create_refused(
            "scene_configuration_target_unreviewable", config_id=LOUNGE, icon="mdi:a"
        )

    async def test_unknown_member_entities_are_refused(self):
        error = await self.assert_create_refused(
            planning.UNKNOWN_ENTITIES, config_id=LOUNGE,
            entities={"light.synthetic_missing": {"state": "on"},
                      FAN: {"state": "on"}},
        )
        self.assertEqual(error.details.get("fields"), ["light.synthetic_missing"])

    async def test_removing_a_non_member_or_every_member(self):
        await self.assert_create_refused(
            "scene_configuration_entity_not_in_scene", config_id=LOUNGE,
            entities={FAN: None},
        )
        await self.assert_create_refused(
            "scene_configuration_entities_empty", config_id=LOUNGE,
            entities={LAMP: None, STRIP: None},
        )

    async def test_values_outside_the_contract_are_refused_before_any_read(self):
        deep = {"state": "on", "a": {"b": {"c": {"d": {"e": 1}}}}}
        many = {"state": "on", **{f"k{index}": index for index in range(70)}}
        cases = [
            ({"name": ""}, "name"),
            ({"name": "   "}, "name"),
            ({"name": "x" * (contract.MAX_NAME_LENGTH + 1)}, "name"),
            ({"name": 5}, "name"),
            ({"name": None}, "name"),
            ({"icon": "floor-lamp"}, "icon"),
            ({"icon": "mdi:" + "a" * 64}, "icon"),
            ({"icon": "MDI:Upper"}, "icon"),
            ({"icon": ""}, "icon"),
            ({"icon": 3}, "icon"),
            ({"entities": {}}, "entities"),
            ({"entities": []}, "entities"),
            ({"entities": {f"light.synthetic_{i}": {"state": "on"}
                           for i in range(contract.MAX_ENTITIES + 1)}}, "entities"),
            ({"entities": {"Light.Bad": {"state": "on"}}}, "entities"),
            ({"entities": {"scene.synthetic_other": {"state": "on"}}}, "entities"),
            ({"entities": {LAMP: {"brightness": 5}}}, "entities"),
            ({"entities": {LAMP: "on"}}, "entities"),
            ({"entities": {LAMP: {"state": 1}}}, "entities"),
            ({"entities": {LAMP: deep}}, "entities"),
            ({"entities": {LAMP: many}}, "entities"),
            ({"entities": {LAMP: {"state": "on", "x": "y" * 3000}}}, "entities"),
            ({"entities": {LAMP: {"state": "on", "x": 2**60}}}, "entities"),
            ({"entities": {LAMP: {"state": "on", "x": float("nan")}}}, "entities"),
            ({"name": "x", "expiration_minutes": 1}, "expiration_minutes"),
            ({"name": "x", "expiration_minutes": True}, "expiration_minutes"),
        ]
        for arguments, field in cases:
            with self.subTest(arguments=str(arguments)[:80]):
                error = await self.assert_create_refused(
                    planning.INVALID_VALUE, config_id=LOUNGE, **arguments
                )
                self.assertEqual(error.details.get("field"), field)
        self.assertEqual(self.scene_rest.requests, [])
        self.assertEqual(self.ws.commands, [])

    async def test_oversize_final_configuration_is_refused(self):
        entities = {
            f"light.synthetic_bulk_{index}": {
                "state": "on", "a": "x" * 2000, "b": "y" * 2000,
            }
            for index in range(contract.MAX_ENTITIES - 2)
        }
        error = await self.assert_create_refused(
            planning.INVALID_VALUE, config_id=LOUNGE, entities=entities
        )
        self.assertEqual(error.details.get("field"), "proposed_state")

    async def test_out_of_scope_fields_are_refused(self):
        for extra in ({"metadata": {}}, {"id": LOUNGE}, {"config": {}},
                      {"activate": True}, {"new_entity_id": "scene.x"},
                      {"area_id": "lounge"}):
            with self.subTest(extra=extra):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_scene_configuration_plan(
                        {"config_id": LOUNGE, "name": "x", **extra}
                    )
                self.assert_refused(raised, planning.FIELD_NOT_SUPPORTED)
                self.assertEqual(raised.exception.details["fields"], sorted(extra))
        await self.assert_create_refused(
            "scene_configuration_no_fields_requested", config_id=LOUNGE
        )
        await self.assert_create_refused(
            "scene_configuration_no_fields_requested", config_id=LOUNGE,
            entity_id=LOUNGE_ENTITY,
        )
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_scene_configuration_plan(["config_id"])
        self.assert_refused(raised, "scene_configuration_invalid_request")
        self.assertEqual(self.scene_rest.requests, [])

    async def test_no_op_is_truthful_and_creates_no_plan(self):
        for arguments in (
            {"name": "Synthetic Lounge dim"},
            {"icon": "mdi:floor-lamp"},
            {"entities": {LAMP: lamp_snapshot()}},
            {"name": "Synthetic Lounge dim", "icon": "mdi:floor-lamp",
             "entities": {STRIP: strip_snapshot()}},
        ):
            with self.subTest(arguments=sorted(arguments)):
                error = await self.assert_create_refused(
                    planning.NO_CHANGE, config_id=LOUNGE, **arguments
                )
                self.assertIs(error.details.get("already_satisfied"), True)
                self.assertEqual(error.details.get("fields"), sorted(
                    arguments, key=contract.FIELDS.index))
        await self.assert_create_refused(planning.NO_CHANGE, config_id=LEAVING,
                                         icon=None)

    async def test_member_type_change_is_a_change_not_a_no_op(self):
        # 1 and 1.0 (and True) are distinct stored values.
        self.scene_rest.set_external(
            LOUNGE, entities={LAMP: lamp_snapshot(brightness=1), STRIP: strip_snapshot()}
        )
        created = await self.create(config_id=LOUNGE,
                                    entities={LAMP: lamp_snapshot(brightness=1.0)})
        self.assertEqual([row["change"] for row in self.disclosure(created)["entities"]],
                         ["changed"])

    async def test_unsendable_and_unrestorable_baselines(self):
        self.scene_rest.set_external(LOUNGE, name="")
        error = await self.assert_create_refused(
            planning.BASELINE_NOT_SENDABLE, config_id=LOUNGE, icon="mdi:a"
        )
        self.assertEqual(error.details.get("field"), "config")
        self.scene_rest.set_external(LOUNGE, **lounge_config())
        # Core's cv.icon only needs a colon; the write contract is stricter.
        self.scene_rest.set_external(LOUNGE, icon="MDI:Floor_Lamp")
        await self.assert_create_refused(
            planning.BASELINE_NOT_SENDABLE, config_id=LOUNGE, name="Renamed"
        )
        await self.assert_create_refused(
            planning.BASELINE_NOT_RESTORABLE, config_id=LOUNGE, icon="mdi:a"
        )

    async def test_unrestorable_icon_cleared_is_refused(self):
        self.scene_rest.set_external(LOUNGE, icon="mdi:" + "a" * 70)
        await self.assert_create_refused(
            planning.BASELINE_NOT_RESTORABLE, config_id=LOUNGE, icon=None
        )

    async def test_change_during_planning_read_is_refused(self):
        self.scene_rest.after_config_read = (
            lambda: self.scene_rest.set_external(LOUNGE, name="Changed elsewhere")
        )
        await self.assert_create_refused(
            "scene_configuration_provider_unavailable", config_id=LOUNGE, icon="mdi:a"
        )

    async def test_provider_failures_during_planning(self):
        self.scene_rest.failures["GET"] = AuthorizationError("synthetic")
        await self.assert_create_refused(
            "scene_configuration_permission_failure", ErrorCode.AUTHORIZATION_FAILURE,
            config_id=LOUNGE, name="x",
        )
        self.scene_rest.failures["GET"] = HomeAssistantApiError("synthetic")
        await self.assert_create_refused(
            "scene_configuration_provider_unavailable", ErrorCode.HA_UNAVAILABLE,
            config_id=LOUNGE, name="x",
        )
        del self.scene_rest.failures["GET"]
        self.ws.failures[REGISTRY_LIST] = HomeAssistantApiError("synthetic")
        await self.assert_create_refused(
            "scene_configuration_provider_unavailable", config_id=LOUNGE, name="x"
        )

    async def test_retryable_not_found_gap_is_not_masked(self):
        # Report-only (Part 1, finding 7): the refusal stays not_found here.
        error = await self.assert_create_refused(
            "scene_configuration_target_not_found", config_id="1799999999999",
            name="x",
        )
        self.assertNotEqual(error.code, ErrorCode.HA_UNAVAILABLE)


class InactiveTests(_Base):
    core_admits = False

    async def test_unadmitted_planning_reads_nothing_and_stores_nothing(self):
        for arguments in (
            {"config_id": LOUNGE, "name": "x"},
            {"config_id": "presence_sim_1", "name": "x"},
            {"config_id": LOUNGE, "name": 5},
            {"config_id": LOUNGE, "entities": {LAMP: None}},
            {},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_scene_configuration_plan(arguments)
                self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
                self.assertEqual(raised.exception.details, INACTIVE_DETAILS)
        self.assertEqual(self.scene_rest.requests, [])
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.core.planning_leases, [])
        self.assertEqual(self.exposure_reads, [])

    async def test_generic_builder_refuses_a_raw_operation_first(self):
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_configuration_plan(
                title="t", description="d", operations=raw_operation(),
                _before_persist=lambda: None,
            )
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"],
                         "pending_signed_registry_admission")
        self.assertEqual(self.scene_rest.requests, [])
        self.assertEqual(self.repository.list(), [])

    async def test_inactive_rollback_creates_nothing(self):
        with self.assertRaises(GovernanceError):
            await self.service.rollback_change("plan_does_not_exist")
        self.assertEqual(self.scene_rest.requests, [])
        self.assertEqual(self.repository.list(), [])

    def test_pending_text_names_sequence_16(self):
        self.assertIn("sequence 16", contract.PENDING_ADMISSION_TEXT)
        self.assertIn("No plan is created", contract.PENDING_ADMISSION_TEXT)


class GenericRouteTests(_Base):
    async def test_generic_configuration_plan_cannot_carry_scene_configuration(self):
        for before_persist in (None, lambda: None):
            with self.subTest(before_persist=before_persist):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service._create_configuration_plan(
                        title="t", description="d", operations=raw_operation(),
                        _before_persist=before_persist,
                    )
                self.assertEqual(raised.exception.code,
                                 ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.writes(), [])

    async def test_create_configuration_plan_tool_rejects_the_scene_type(self):
        with self.assertRaises(GovernanceError):
            await self.service.create_configuration_plan(
                title="t", description="d", operations=raw_operation()
            )
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.writes(), [])


class IsolatedControlTests(_Base):
    """HAMCP-106 R1-M3: each check refuses on its own."""

    async def test_generic_builder_refuses_a_complete_valid_admitted_operation(self):
        current = await self.resource_gateway.scene_configuration.read_state(LOUNGE)
        self.assertEqual(contract.state_errors(LOUNGE, current), [])
        proposed = deepcopy(current)
        proposed["config"]["name"] = "Synthetic review name"
        self.assertEqual(contract.proposed_errors(LOUNGE, proposed), [])
        requests, commands = len(self.scene_rest.requests), len(self.ws.commands)
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_configuration_plan(
                title="t", description="d", operations=raw_operation(proposed),
                _before_persist=lambda: None,
            )
        self.assertEqual(raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        self.assertEqual(raised.exception.details, {
            "validation_errors": [
                "scene_configuration_1: resource_type must be automation, "
                "script, or helper"
            ],
        })
        self.assertEqual(len(self.scene_rest.requests), requests)
        self.assertEqual(len(self.ws.commands), commands)
        self.assertEqual(self.repository.list(), [])

    async def test_verified_child_under_an_unverified_task_creates_no_restore(self):
        created, result = await self.applied(config_id=LOUNGE, name="New")
        self.assertEqual(result["task_state"], "succeeded_verified")
        repository = self.service.task_repository
        task = repository.get_for_plan(created["plan_id"])
        unverified = replace(
            task,
            state=ExecutionTaskState.MANUAL_REVIEW_REQUIRED,
            terminal_outcome="manual_review_required",
        )
        repository.get_for_plan = lambda plan_id: unverified
        plans, writes = len(self.repository.list()), len(self.writes())
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"], "forward_not_verified")
        self.assertIs(raised.exception.details["plan_created"], False)
        self.assertEqual(len(self.repository.list()), plans)
        self.assertEqual(len(self.writes()), writes)

    async def test_final_persistence_check_refuses_without_the_lease_check(self):
        service = self.service
        real_lease = service._scene_configuration_planning_lease

        class LeaseWithoutCheck:
            def __init__(self, inner):
                self.inner = inner

            async def __aenter__(self):
                await self.inner.__aenter__()
                return self

            async def __aexit__(self, *exc):
                try:
                    await self.inner.__aexit__(*exc)
                except GovernanceError:
                    pass
                return False

            def require_current(self):
                return None

        service._scene_configuration_planning_lease = (
            lambda target_id: LeaseWithoutCheck(real_lease(target_id))
        )
        real_bind = service._bind_new_plan_policy

        def bind_then_withdraw(plan):
            real_bind(plan)
            self.core.withdraw()

        service._bind_new_plan_policy = bind_then_withdraw
        with self.assertRaises(GovernanceError) as raised:
            await self.create(config_id=LOUNGE, name="New")
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details, INACTIVE_DETAILS)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.writes(), [])

    async def test_strategy_readback_alone_rejects_a_mismatch(self):
        from ha_mcp_engineering.f3_configuration.strategies import (
            SceneConfigurationConfigurationStrategy,
        )

        from ha_mcp_engineering.governance.resources import (
            compare_resource_verification,
        )

        strategy = SceneConfigurationConfigurationStrategy(contract.ACTION)
        current = await self.resource_gateway.scene_configuration.read_state(LOUNGE)
        proposed = deepcopy(current)
        proposed["config"]["name"] = "Proposed"
        self.assertTrue(strategy.already_reached(deepcopy(proposed), proposed))
        self.assertIs(compare_resource_verification(
            contract.RESOURCE_TYPE, proposed, deepcopy(proposed)).semantic_match, True)
        for mutate in (
            lambda s: s["config"].__setitem__("name", "Other"),
            lambda s: s["config"]["entities"][LAMP].__setitem__("brightness", 78),
            lambda s: s["config"]["entities"][LAMP].__setitem__("brightness", 77.0),
            lambda s: s["config"]["entities"][STRIP].__setitem__("x", True),
            lambda s: s["config"]["entities"][STRIP].__setitem__("x", 1),
            lambda s: s["config"].pop("icon"),
            lambda s: s["config"]["metadata"].__setitem__("x", 1),
            lambda s: s.__setitem__("loaded_matches_config", False),
            lambda s: s.__setitem__("registry_entry_id", "scnother"),
        ):
            observed = deepcopy(proposed)
            mutate(observed)
            # Python equality would call 77 == 77.0 == True; both checks
            # are type-exact.
            self.assertFalse(strategy.already_reached(observed, proposed))
            self.assertIsNot(compare_resource_verification(
                contract.RESOURCE_TYPE, proposed, observed).semantic_match, True)
        self.assertFalse(strategy.already_reached(None, proposed))


class _ResourceWriteAuthority:
    """HAMCP-106 R1-H1: a valid command is not authority at the boundary."""

    def valid_command(self) -> dict:
        proposed = lounge_config()
        proposed["name"] = "Synthetic review name"
        return {"method": "POST", "config_id": LOUNGE, "body": body(proposed)}

    async def assert_write_refused(self, reason_code: str, write=None) -> None:
        before = store_bytes(self.root)
        self.scene_rest.requests.clear()
        self.ws.commands.clear()
        command = self.valid_command()
        self.assertEqual(contract.command_errors(command), [])
        if write is None:
            write = lambda: self.resource_gateway.write(  # noqa: E731
                "update", contract.RESOURCE_TYPE, LOUNGE, command
            )
        with self.assertRaises(ConfigurationMutationNotDispatchedError) as raised:
            await write()
        error = raised.exception
        self.assertEqual(error.details["reason_code"], reason_code, error.details)
        self.assertEqual(error.details["reason"], reason_code)
        self.assertIs(error.details["provider_dispatch_occurred"], False)
        self.assertIs(error.call_not_sent, True)
        self.assertEqual(self.scene_rest.requests, [])
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(store_bytes(self.root), before)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.stored(), lounge_config())

    async def assert_both_write_layers_refused(self, reason_code: str) -> None:
        await self.assert_write_refused(reason_code)
        gateway = self.resource_gateway.scene_configuration
        await self.assert_write_refused(
            reason_code, lambda: gateway.write(LOUNGE, self.valid_command())
        )


class InactiveResourceWriteTests(_ResourceWriteAuthority, _Base):
    core_admits = False

    async def test_valid_complete_command_refuses_pending_admission(self):
        await self.assert_both_write_layers_refused("pending_signed_registry_admission")

    async def test_refused_even_inside_an_authorizing_call_window(self):
        async with _core_call_window(lambda: True):
            await self.assert_both_write_layers_refused(
                "pending_signed_registry_admission"
            )

    async def test_no_core_runtime_refuses_pending_admission(self):
        self.resource_gateway.scene_configuration.core_runtime = None
        async with _core_call_window(lambda: True):
            await self.assert_both_write_layers_refused(
                "pending_signed_registry_admission"
            )

    async def test_core_runtime_error_refuses_pending_admission(self):
        class Broken:
            def route_status(self, profiles):
                raise RuntimeError("synthetic")

        self.resource_gateway.scene_configuration.core_runtime = Broken()
        async with _core_call_window(lambda: True):
            await self.assert_both_write_layers_refused(
                "pending_signed_registry_admission"
            )


class AdmittedResourceWriteTests(_ResourceWriteAuthority, _Base):
    async def test_admitted_without_f3_context_refuses(self):
        await self.assert_both_write_layers_refused("active_f3_execution_required")

    async def test_admitted_request_without_dispatch_authorizer_refuses(self):
        telemetry, token = begin_request()
        try:
            self.assertIsNone(telemetry.core_dispatch_authorizer)
            await self.assert_both_write_layers_refused("active_f3_execution_required")
        finally:
            end_request(token)

    async def test_admitted_with_a_refusing_dispatch_window_refuses(self):
        async with _core_call_window(lambda: False):
            await self.assert_both_write_layers_refused("active_f3_execution_required")

    async def test_admitted_with_f3_context_sends_exactly_once(self):
        async with _core_call_window(lambda: True):
            result = await self.resource_gateway.write(
                "update", contract.RESOURCE_TYPE, LOUNGE, self.valid_command()
            )
        self.assertEqual(result, {"core_response": {"result": "ok"}})
        self.assertEqual(self.writes(), [(LOUNGE, self.valid_command()["body"])])
        self.assertEqual(self.stored()["name"], "Synthetic review name")

    async def test_closed_payload_violations_are_never_sent(self):
        valid = self.valid_command()
        cases = (
            {**valid, "body": {**valid["body"], "id": LOUNGE}},
            {**valid, "body": {**valid["body"], "extra": 1}},
            {**valid, "method": "DELETE"},
            {**valid, "method": "PUT"},
            {**valid, "config_id": LEAVING},
            {**valid, "body": {**valid["body"], "name": ""}},
            {**valid, "body": {**valid["body"], "icon": "MDI:x"}},
            {key: value for key, value in valid.items() if key != "body"},
            {**valid, "service": "scene.turn_on"},
        )
        async with _core_call_window(lambda: True):
            for command in cases:
                with self.subTest(command=str(command)[:80]):
                    with self.assertRaises(ConfigurationMutationNotDispatchedError) as raised:
                        await self.resource_gateway.scene_configuration.write(
                            LOUNGE, command
                        )
                    self.assertIs(raised.exception.call_not_sent, True)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.stored(), lounge_config())

    async def test_retained_approved_plan_without_f3_refuses_before_any_read(self):
        created = await self.approved(config_id=LOUNGE, name="Retained")
        self.service.f3_runtime = None
        before = store_bytes(self.root)
        self.scene_rest.requests.clear()
        self.ws.commands.clear()
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(created)
        error = raised.exception
        self.assertEqual(error.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(error.details["reason_code"], "core_authority_unavailable")
        self.assertEqual(error.details["resource_types"], [contract.RESOURCE_TYPE])
        self.assertIs(error.details["provider_dispatch_occurred"], False)
        self.assertEqual(self.scene_rest.requests, [])
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(store_bytes(self.root), before)
        self.assertEqual(self.stored(), lounge_config())

    async def test_withdrawn_admission_refuses_apply_before_any_read(self):
        created = await self.approved(config_id=LOUNGE, name="Requested")
        self.core.withdraw()
        self.scene_rest.requests.clear()
        try:
            result = await self.apply(created)
        except GovernanceError as exc:
            self.assertIs(exc.details.get("provider_dispatch_occurred", False), False)
        else:
            self.assertNotEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.stored(), lounge_config())

    def test_scene_configuration_is_in_the_no_f3_refusal_inventory(self):
        from ha_mcp_engineering.governance.service import (
            DORMANT_PROFILE_RESOURCE_TYPES,
        )

        self.assertIn(contract.RESOURCE_TYPE, DORMANT_PROFILE_RESOURCE_TYPES)


class RuntimeCompositionTests(unittest.TestCase):
    """The production composition hands the Core runtime to the write boundary."""

    def configured_gateway(self, core_runtime):
        import dataclasses
        import shutil
        import tempfile

        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
        from ha_mcp_engineering.governance.runtime import GovernanceRuntime
        from tests.test_readonly_upstream_gateway import settings

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        configured = dataclasses.replace(
            settings(),
            audit_path=str(root / "audit.jsonl"),
            governance_path=str(root / "governance"),
        )
        runtime = GovernanceRuntime()
        runtime.configure(
            configured,
            AuditLogger(configured.audit_path, "hamcp071-synthetic-audit"),
            HomeAssistantRestClient(configured),
            object(),
            core_runtime=core_runtime,
        )
        self.assertIsNone(runtime.storage_error)
        return runtime.service.scene_configuration_gateway

    def test_configured_write_boundary_consults_the_given_core_runtime(self):
        class Core:
            def __init__(self, available):
                self.available = available
                self.asked = []

            def route_status(self, profiles):
                self.asked.append(tuple(profiles))
                return {"available": self.available}

        admitting = Core(True)
        gateway = self.configured_gateway(admitting)
        self.assertIs(gateway.core_runtime, admitting)
        self.assertEqual(gateway.write_refusal(), contract.F3_CONTEXT_REQUIRED_REASON)
        self.assertEqual(admitting.asked, [(contract.PROFILE,)])
        withheld = Core(False)
        self.assertEqual(
            self.configured_gateway(withheld).write_refusal(),
            "pending_signed_registry_admission",
        )
        self.assertEqual(withheld.asked, [(contract.PROFILE,)])


class ApplyBoundaryTests(_Base):
    async def test_stale_state_for_each_field_is_refused_before_dispatch(self):
        external = (
            {"name": "Renamed elsewhere"},
            {"icon": "mdi:other"},
            {"icon": None},
            {"entities": {LAMP: lamp_snapshot(brightness=78), STRIP: strip_snapshot()}},
            {"entities": {LAMP: lamp_snapshot()}},
            {"metadata": {LAMP: {"entity_only": True}}},
            {"metadata": None},
        )
        for change in external:
            with self.subTest(change=str(change)[:60]):
                self.scene_rest.set_external(LOUNGE, **lounge_config())
                writes = len(self.writes())
                created = await self.approved(config_id=LOUNGE, name="Requested")
                self.scene_rest.set_external(LOUNGE, **change)
                observed = self.stored()
                result = await self.apply(created)
                self.assert_outcome(result, "failed_pre_dispatch", ["preflight_rejected"],
                                    ["stale_target_state"])
                self.assertEqual(len(self.writes()), writes)
                self.assertEqual(self.stored(), observed)

    async def test_registry_change_before_dispatch_is_stale(self):
        created = await self.approved(config_id=LOUNGE, name="Requested")
        self.ws.entries[LOUNGE_ENTITY]["id"] = "scnreplaced"
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertEqual(self.outcomes(result["task_id"]), ["preflight_rejected"])
        self.assertEqual(self.writes(), [])

    async def test_unloaded_change_before_dispatch_is_stale(self):
        created = await self.approved(config_id=LOUNGE, name="Requested")
        self.scene_rest.set_external(LOUNGE, reload=False, icon="mdi:x")
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertEqual(self.writes(), [])

    async def test_scene_deleted_before_dispatch_is_refused(self):
        created = await self.approved(config_id=LOUNGE, name="Requested")
        self.scene_rest.scenes.remove(self.scene_rest.config(LOUNGE))
        del self.ws.entries[LOUNGE_ENTITY]
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertEqual(self.outcomes(result["task_id"]), ["preflight_rejected"])
        # Never recreated by the upsert.
        self.assertEqual(self.writes(), [])
        self.assertIsNone(self.scene_rest.config(LOUNGE))

    async def test_expired_plan_is_never_applied(self):
        created = await self.approved(config_id=LOUNGE, name="Requested",
                                      expiration_minutes=5)
        self.clock[0] += timedelta(minutes=6)
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(created)
        self.assertIs(raised.exception.details.get("provider_dispatch_occurred", False),
                      False)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.stored(), lounge_config())

    async def test_no_self_approval_and_unapproved_apply(self):
        created = await self.create(config_id=LOUNGE, name="Requested")
        with self.assertRaises(GovernanceError):
            await self.apply(created)
        granted = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(granted["status"], "approval_pending", granted)
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(created)
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH)
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"], "sha256:" + "0" * 64)
        self.assertEqual(self.writes(), [])

    async def test_target_already_reached_is_not_dispatched(self):
        created = await self.approved(config_id=LOUNGE, name="Reached")
        self.scene_rest.set_external(LOUNGE, name="Reached")
        result = await self.apply(created)
        self.assertEqual(self.writes(), [])
        self.assertNotEqual(result["task_state"], "succeeded_verified", result) \
            if result["execution_task"].get("terminal_outcome") != (
                "desired_state_already_present_not_dispatched") else None
        plan = self.service._load(created["plan_id"])
        self.assertIs(plan.rollback.available, False)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)


class FailureTruthfulnessTests(_Base):
    async def test_core_validation_rejection_is_confirmed_not_dispatched(self):
        created = await self.approved(config_id=LOUNGE, name="Requested")
        self.scene_rest.failures["POST"] = HomeAssistantApiError(
            "Message malformed", details={"status": 400, "provider_response_received": True}
        )
        result = await self.apply(created)
        self.assertEqual(self.outcomes(result["task_id"]), ["dispatch_failed_confirmed"])
        self.assertNotEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.stored(), lounge_config())

    async def test_authorization_rejection_is_confirmed_not_dispatched(self):
        for failure in (
            AuthorizationError("synthetic", details={"provider_response_received": True}),
            HomeAssistantApiError(
                "forbidden", details={"status": 403, "provider_response_received": True}
            ),
        ):
            with self.subTest(failure=type(failure).__name__):
                writes = len(self.writes())
                created = await self.approved(config_id=LOUNGE, name=f"R {writes}")
                self.scene_rest.failures["POST"] = failure
                result = await self.apply(created)
                del self.scene_rest.failures["POST"]
                self.assertEqual(self.outcomes(result["task_id"]),
                                 ["dispatch_failed_confirmed"])
                self.assertIn("provider_permission_denied",
                              self.child_codes(result["task_id"]))
                self.assertEqual(len(self.writes()), writes + 1)

    async def test_server_error_after_send_is_indeterminate(self):
        created = await self.approved(config_id=LOUNGE, name="Requested")
        self.scene_rest.failures["POST"] = HomeAssistantApiError(
            "internal", details={"status": 500, "provider_response_received": True}
        )
        result = await self.apply(created)
        self.assert_outcome(result, "failed_post_dispatch", ["verification_mismatch"],
                            ["provider_error_after_send"])
        self.assertEqual(len(self.writes()), 1)

    async def test_lost_response_is_indeterminate_and_never_redispatched(self):
        created = await self.approved(config_id=LOUNGE, name="Requested")
        self.scene_rest.failures["POST"] = HomeAssistantApiError("connection lost")
        result = await self.apply(created)
        self.assertNotEqual(result["task_state"], "succeeded_verified", result)
        self.assertNotIn("succeeded_verified", self.outcomes(result["task_id"]))
        self.assertEqual(len(self.writes()), 1)
        del self.scene_rest.failures["POST"]
        try:
            await self.apply(created)
        except GovernanceError:
            pass
        await self.restart_runtime()
        self.assertEqual(len(self.writes()), 1)

    async def test_readback_mismatch_when_nothing_was_stored(self):
        created = await self.approved(config_id=LOUNGE, name="Requested")
        self.scene_rest.ignore_writes = True
        result = await self.apply(created)
        self.assert_outcome(result, "failed_post_dispatch", ["verification_mismatch"],
                            ["exact_readback_not_verified"])
        self.assertNotIn("exact_readback_verified", self.child_codes(result["task_id"]))
        self.assertEqual(len(self.writes()), 1)

    async def test_unrequested_drift_is_a_verification_failure(self):
        for drift in (
            {"icon": "mdi:drift"},
            {"metadata": {"x": 1}},
            {"entities": {LAMP: lamp_snapshot()}},
            {"entities": {LAMP: lamp_snapshot(), STRIP: strip_snapshot(state="on")}},
        ):
            with self.subTest(drift=sorted(drift)):
                self.scene_rest.set_external(LOUNGE, **lounge_config())
                writes = len(self.writes())
                created = await self.approved(config_id=LOUNGE, name=f"T {writes}")
                self.scene_rest.drift = drift
                result = await self.apply(created)
                self.scene_rest.drift = {}
                self.assert_outcome(result, "failed_post_dispatch",
                                    ["verification_mismatch"],
                                    ["exact_readback_not_verified"])
                self.assertEqual(len(self.writes()), writes + 1)

    async def test_failed_reload_is_a_verification_failure(self):
        created = await self.approved(config_id=LOUNGE, name="Requested")
        self.scene_rest.reload_fails = True
        result = await self.apply(created)
        self.assert_outcome(result, "failed_post_dispatch", ["verification_mismatch"],
                            ["exact_readback_not_verified"])
        # Stored, but the loaded scene never reflected it.
        self.assertEqual(self.stored()["name"], "Requested")
        self.assertEqual(len(self.writes()), 1)

    async def test_verified_plan_is_never_applied_twice(self):
        created, result = await self.applied(config_id=LOUNGE, name="Requested")
        self.assertEqual(result["task_state"], "succeeded_verified")
        try:
            await self.apply(created)
        except GovernanceError:
            pass
        await self.restart_runtime()
        try:
            await self.apply(created)
        except GovernanceError:
            pass
        self.assertEqual(len(self.writes()), 1)

    async def test_attribution_names_the_scene_provider(self):
        created, result = await self.applied(config_id=LOUNGE, name="Requested")
        self.assertEqual(result["task_state"], "succeeded_verified")
        records = self.child_records(result["task_id"])
        self.assertEqual([record.dispatch_count for record in records], [1])
        self.assertEqual(self.disclosure(created)["provider"],
                         "direct_home_assistant_scene_configuration")
        self.assertEqual(contract.provider_evidence()["contract"]
                         if "contract" in contract.provider_evidence()
                         else contract.PROVIDER_CONTRACT,
                         contract.PROVIDER_CONTRACT)


class RollbackTests(_Base):
    async def test_rollback_restores_the_exact_prior_configuration(self):
        cases = (
            {"name": "Renamed"},
            {"icon": None},
            {"icon": "mdi:lightbulb"},
            {"entities": {FAN: {"state": "on"}}},
            {"entities": {STRIP: None}},
            {"entities": {LAMP: lamp_snapshot(brightness=1, hs_color=[1.0, 2.0])}},
            {"name": "All", "icon": None,
             "entities": {LAMP: None, FAN: {"state": "on", "percentage": 10}}},
        )
        for arguments in cases:
            with self.subTest(arguments=str(arguments)[:80]):
                self.scene_rest.set_external(LOUNGE, **lounge_config())
                created, result = await self.applied(config_id=LOUNGE, **arguments)
                self.assertEqual(result["task_state"], "succeeded_verified", result)
                writes = len(self.writes())
                rollback = await self.service.rollback_change(created["plan_id"])
                self.assertEqual(rollback["status"], "rollback_plan_created")
                self.assertIs(rollback["approval_required"], True)
                self.assertIs(rollback["provider_dispatch_occurred"], False)
                self.assertEqual(len(self.writes()), writes)
                restored = await self.apply(await self.approve_rollback(rollback))
                self.assertEqual(restored["task_state"], "succeeded_verified", restored)
                self.assertEqual(self.writes()[-1], (LOUNGE, body(lounge_config())))
                self.assertEqual(self.stored(), lounge_config())
                self.assertEqual(
                    contract.canonical(self.stored()), contract.canonical(lounge_config())
                )

    async def test_rollback_plan_discloses_and_needs_its_own_approval(self):
        created, _result = await self.applied(config_id=LOUNGE, name="Renamed")
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertIn("name", rollback["restored_fields"])
        reverse = {"plan_id": rollback["rollback_plan_id"],
                   "plan_hash": rollback["plan_hash"]}
        with self.assertRaises(GovernanceError):
            await self.apply(reverse)
        plan = self.service._load(rollback["rollback_plan_id"])
        self.assertEqual(plan.dry_run_results["scene_configuration"]["rollback"],
                         "this_plan_restores_the_captured_configuration")
        disclosure = plan.operations[0].dry_run_results["scene_configuration"]
        self.assertIs(disclosure["activated"], False)
        self.assertEqual(len(self.writes()), 1)

    async def test_rollback_of_an_elevated_scene_is_elevated(self):
        created, _result = await self.applied(config_id=LEAVING,
                                              entities={FRONT_LOCK: None})
        rollback = await self.service.rollback_change(created["plan_id"])
        plan = self.service._load(rollback["rollback_plan_id"])
        self.assertEqual(plan.policy_decision.policy_class.value, "elevated_admin")
        restored = await self.apply(await self.approve_rollback(rollback))
        self.assertEqual(restored["task_state"], "succeeded_verified", restored)
        self.assertEqual(self.stored(LEAVING), leaving_config())

    async def test_rollback_refused_after_drift(self):
        created, _result = await self.applied(config_id=LOUNGE, name="New")
        self.scene_rest.set_external(LOUNGE, icon="mdi:elsewhere")
        plans = len(self.repository.list())
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"],
                         "scene_configuration_rollback_baseline_drift")
        self.assertIs(raised.exception.details["plan_created"], False)
        self.assertEqual(len(self.repository.list()), plans)
        self.assertEqual(len(self.writes()), 1)

    async def test_rollback_drift_is_type_exact(self):
        created, _result = await self.applied(
            config_id=LOUNGE, entities={FAN: {"state": "on", "percentage": 1}}
        )
        drifted = self.stored()["entities"]
        drifted[FAN]["percentage"] = True
        self.scene_rest.set_external(LOUNGE, entities=drifted)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.details["reason_code"],
                         "scene_configuration_rollback_baseline_drift")
        self.assertEqual(len(self.writes()), 1)

    async def test_rollback_refused_when_already_restored(self):
        created, _result = await self.applied(config_id=LOUNGE, name="New")
        self.scene_rest.set_external(LOUNGE, name="Synthetic Lounge dim")
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(len(self.writes()), 1)

    async def test_rollback_refused_when_forward_was_not_verified(self):
        created = await self.approved(config_id=LOUNGE, name="New")
        self.scene_rest.ignore_writes = True
        await self.apply(created)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertIs(raised.exception.details["plan_created"], False)
        self.assertEqual(raised.exception.details["reason_code"], "forward_not_verified")

    async def test_rollback_refused_when_forward_was_rejected(self):
        created = await self.approved(config_id=LOUNGE, name="New")
        self.scene_rest.failures["POST"] = HomeAssistantApiError(
            "Message malformed", details={"status": 400, "provider_response_received": True}
        )
        await self.apply(created)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertIs(raised.exception.details["plan_created"], False)

    async def test_rollback_after_the_scene_was_deleted_is_refused(self):
        created, _result = await self.applied(config_id=LOUNGE, name="New")
        self.scene_rest.scenes.remove(self.scene_rest.config(LOUNGE))
        del self.ws.entries[LOUNGE_ENTITY]
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(len(self.writes()), 1)
        self.assertIsNone(self.scene_rest.config(LOUNGE))

    async def test_rollback_availability_tracks_the_verified_forward(self):
        created, result = await self.applied(config_id=LOUNGE, name="New")
        self.assertEqual(result["task_state"], "succeeded_verified")
        plan = self.service._load(created["plan_id"])
        self.assertIs(plan.rollback.available, True)
        self.assertEqual(plan.rollback.status, "available_pending_current_state_check")


class ContractBoundaryTests(_Base):
    """Provider-boundary rechecks below the planner."""

    def test_command_shape_is_closed(self):
        config = lounge_config()
        valid = {"method": "POST", "config_id": LOUNGE, "body": body(config)}
        self.assertEqual(contract.command_errors(valid), [])
        for command in (
            {**valid, "method": "GET"},
            {**valid, "method": "DELETE"},
            {**valid, "config_id": LOUNGE_ENTITY},
            {**valid, "body": {**valid["body"], "id": LOUNGE}},
            {**valid, "body": {**valid["body"], "unknown": 1}},
            {**valid, "body": {k: v for k, v in valid["body"].items() if k != "name"}},
            {**valid, "body": {**valid["body"], "entities": {LAMP: {"x": 1}}}},
            {**valid, "extra": 1},
            [],
        ):
            with self.subTest(command=str(command)[:80]):
                self.assertNotEqual(contract.command_errors(command), [])

    def test_write_command_refuses_identity_and_metadata_changes(self):
        current = {
            "config_id": LOUNGE, "entity_id": LOUNGE_ENTITY,
            "registry_entry_id": "scnsyntheticloungedim", "config": lounge_config(),
            "loaded_matches_config": True,
        }
        proposed = deepcopy(current)
        proposed["config"]["name"] = "Renamed"
        self.assertEqual(contract.write_command(LOUNGE, current, proposed)["body"],
                         body(proposed["config"]))
        for key, value in (("entity_id", "scene.other"),
                           ("registry_entry_id", "scnother")):
            changed = deepcopy(proposed)
            changed[key] = value
            with self.assertRaises(contract.SceneConfigurationContractError):
                contract.write_command(LOUNGE, current, changed)
        for metadata in ({"x": 1}, None):
            changed = deepcopy(proposed)
            if metadata is None:
                changed["config"].pop("metadata")
            else:
                changed["config"]["metadata"] = metadata
            with self.assertRaises(contract.SceneConfigurationContractError):
                contract.write_command(LOUNGE, current, changed)
        with self.assertRaises(contract.SceneConfigurationContractError):
            contract.write_command(LOUNGE, current, deepcopy(current))
        with self.assertRaises(contract.SceneConfigurationContractError):
            contract.write_command(LOUNGE, None, proposed)

    async def test_gateway_never_sends_outside_the_vocabulary(self):
        gateway = self.resource_gateway.scene_configuration
        for command_type in ("scene/apply", "call_service", "config/entity_registry/update",
                             "config/entity_registry/remove"):
            with self.subTest(command_type=command_type):
                with self.assertRaises(contract.SceneConfigurationContractError):
                    await gateway._command({"type": command_type})
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(contract.ALLOWED_COMMANDS,
                         frozenset({REGISTRY_LIST, contract.REGISTRY_GET_COMMAND}))

    def test_config_id_bound(self):
        for value in (LOUNGE, "a" * 64, "abc-DEF_123"):
            self.assertTrue(contract.config_id_valid(value))
        for value in ("", "a" * 65, "scene.x", "a/b", "a b", 1, None, "../x"):
            self.assertFalse(contract.config_id_valid(value))

    def test_snapshot_rules(self):
        self.assertTrue(contract.snapshot_valid({"state": "on"}, requested=True))
        self.assertTrue(contract.snapshot_valid({"state": True}, requested=True))
        self.assertFalse(contract.snapshot_valid({"state": None}, requested=True))
        self.assertFalse(contract.snapshot_valid("on", requested=True))
        # A stored bare state is read, never requested.
        self.assertTrue(contract.snapshot_valid("on", requested=False))
        self.assertTrue(contract.snapshot_valid(True, requested=False))
        self.assertFalse(contract.snapshot_valid(1, requested=False))

    async def test_stored_bare_state_is_kept_and_re_sent_exactly(self):
        self.scene_rest.set_external(
            LOUNGE, entities={LAMP: lamp_snapshot(), STRIP: "off"}
        )
        _created, result = await self.applied(config_id=LOUNGE, name="Bare kept")
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.writes()[0][1]["entities"][STRIP], "off")
        self.assertEqual(self.stored()["entities"][STRIP], "off")

    async def test_preflight_holds_only_for_the_bound_state(self):
        check = self.resource_gateway.scene_configuration_preflight_evidence
        current = await self.resource_gateway.scene_configuration.read_state(LOUNGE)
        proposed = deepcopy(current)
        proposed["config"]["name"] = "Proposed"
        self.assertEqual(await check(LOUNGE, current, proposed), ())
        self.scene_rest.set_external(LOUNGE, name="Elsewhere")
        self.assertEqual(await check(LOUNGE, current, proposed), ("stale_target_state",))
        self.assertEqual(await check(LOUNGE, None, proposed), ("plan_bindings_invalid",))
        # Type-exact: a stored 77 that became 77.0 is stale.
        self.scene_rest.set_external(LOUNGE, **lounge_config())
        changed = lounge_config()["entities"]
        changed[LAMP]["brightness"] = float(changed[LAMP]["brightness"])
        self.scene_rest.set_external(LOUNGE, entities=changed)
        self.assertEqual(await check(LOUNGE, current, proposed), ("stale_target_state",))
        self.assertEqual(self.writes(), [])



class MutationSurvivorTests(_Base):
    """Round 0 survivors (docs/evidence/hamcp071-mutations), each killed here."""

    async def test_integration_entry_for_a_stored_config_is_not_ui_managed(self):
        collide = "1700000000077"
        config = dict(lounge_config(), id=collide, name="Synthetic collide")
        self.scene_rest.add_scene("scene.synthetic_collide", config,
                                  platform="synthetic_integration")
        await self.assert_create_refused(
            "scene_configuration_not_ui_managed", config_id=collide, name="x"
        )

    async def test_loaded_members_must_match_the_stored_members(self):
        # Name and icon still match; only the loaded member list is stale.
        self.scene_rest.set_external(LOUNGE, reload=False,
                                     entities={LAMP: lamp_snapshot()})
        await self.assert_create_refused(
            "scene_configuration_not_loaded", config_id=LOUNGE, icon="mdi:a"
        )

    async def test_rollback_admission_is_checked_before_the_forward_state(self):
        created = await self.approved(config_id=LOUNGE, name="New")
        self.core.withdraw()
        plans, requests = len(self.repository.list()), len(self.scene_rest.requests)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"],
                         "pending_signed_registry_admission")
        self.assertEqual(len(self.repository.list()), plans)
        self.assertEqual(len(self.scene_rest.requests), requests)

    async def test_send_boundary_records_the_send_time(self):
        _created, result = await self.applied(config_id=LOUNGE, name="New")
        self.assertEqual(result["task_state"], "succeeded_verified")
        view = self.service.get_execution_task(result["task_id"])
        child_id = self.runtime.children.declarations_for_task(
            result["task_id"])[0]["child_id"]
        self.assertIsInstance(
            view["verification_summary"][df.SEND_TIME_SUMMARY_KEY][child_id], str)
        self.assertIsNotNone(view["dispatched_at"])
        self.assertNotEqual(view["dispatched_at_basis"], df.DISPATCHED_AT_CALL_NOT_SENT)

    async def test_unrequested_field_is_disclosed_as_not_requested(self):
        created = await self.create(config_id=LOUNGE, name="Renamed")
        self.assertEqual(
            [(row["field"], row["requested"], row["changed"])
             for row in self.disclosure(created)["fields"]],
            [("name", True, True), ("icon", False, False)],
        )

    async def test_consumer_evidence_text_is_screened(self):
        async def evidence(entity_id):
            return {"status": "complete", "consumers": [
                {"entity_id": CONSUMER, "name": CREDENTIAL_TEXT}],
                "consumer_count": 1, "evidence_complete": True, "evidence_gaps": []}

        self.service.scene_configuration_gateway.consumer_evidence = evidence
        created = await self.create(config_id=LOUNGE, name="Renamed")
        consumers = self.disclosure(created)["consumers"]
        self.assertNotIn(CREDENTIAL_TEXT, str(consumers))
        self.assertIn(CONSUMER, str(consumers["consumers"]))
        self.assertGreaterEqual(consumers["withheld_text_count"], 1)

    async def test_bound_basis_must_carry_the_protection_floor(self):
        from ha_mcp_engineering.governance.models import (
            ApprovalPolicyClass, RiskLevel,
        )
        from ha_mcp_engineering.governance.policy import (
            SCENE_CONFIGURATION_INCOMPLETE, _scene_configuration_operation_policy,
        )

        created = await self.create(config_id=LEAVING, name="Renamed")
        operation = deepcopy(self.service._load(created["plan_id"]).operations[0])
        self.assertEqual(_scene_configuration_operation_policy(operation).policy_class,
                         ApprovalPolicyClass.ELEVATED_ADMIN)
        # A standard-shaped marker with the lock floor dropped from the basis.
        operation.risk.evidence = [
            dict(item, basis="", policy="standard", physical_consequence="none")
            if isinstance(item, dict) and item.get("trigger") == planning.POLICY_TRIGGER
            else item
            for item in operation.risk.evidence
        ]
        operation.risk.level = RiskLevel.LOW
        decision = _scene_configuration_operation_policy(operation)
        self.assertEqual(decision.policy_class, ApprovalPolicyClass.PROHIBITED)
        self.assertEqual(decision.reason_codes, (SCENE_CONFIGURATION_INCOMPLETE,))

    async def test_multi_operation_scene_plan_is_prohibited(self):
        from ha_mcp_engineering.governance.models import ApprovalPolicyClass
        from ha_mcp_engineering.governance.policy import (
            SCENE_CONFIGURATION_INCOMPLETE, _scene_configuration_operation_policy,
            evaluate_change_policy,
        )

        first = self.service._load((await self.create(config_id=LOUNGE, name="A"))["plan_id"])
        second = self.service._load((await self.create(config_id=LEAVING, name="B"))["plan_id"])
        mixed = deepcopy(first)
        mixed.operations = [first.operations[0], second.operations[0]]
        # Each child is complete on its own; only the plan-level rule refuses.
        for operation in mixed.operations:
            self.assertNotEqual(
                _scene_configuration_operation_policy(operation).policy_class,
                ApprovalPolicyClass.PROHIBITED,
            )
        decision = evaluate_change_policy(mixed)
        self.assertEqual(decision.policy_class, ApprovalPolicyClass.PROHIBITED)
        self.assertIn(SCENE_CONFIGURATION_INCOMPLETE, decision.reason_codes)

    async def bound(self):
        current = await self.resource_gateway.scene_configuration.read_state(LOUNGE)
        proposed = deepcopy(current)
        proposed["config"]["name"] = "Proposed"
        return current, proposed

    async def test_gateway_preflight_holds_only_for_the_bound_state(self):
        check = self.resource_gateway.scene_configuration_preflight_evidence
        current, proposed = await self.bound()
        self.assertEqual(await check(LOUNGE, current, proposed), ())

        def deleted():
            self.scene_rest.scenes.remove(self.scene_rest.config(LOUNGE))
            del self.ws.entries[LOUNGE_ENTITY]

        changes = (
            ("renamed", lambda: self.scene_rest.set_external(LOUNGE, name="Elsewhere")),
            # Absent everywhere: the POST would create it.
            ("deleted", deleted),
            # Store entry gone, registry entry kept: a contract error.
            ("store_only_deleted", lambda: self.scene_rest.scenes.remove(
                self.scene_rest.config(LOUNGE))),
        )
        for label, change in changes:
            with self.subTest(change=label):
                await self.asyncTearDown()
                await self.asyncSetUp()
                check = self.resource_gateway.scene_configuration_preflight_evidence
                current, proposed = await self.bound()
                change()
                self.assertEqual(
                    await check(LOUNGE, current, proposed), ("stale_target_state",)
                )
                self.assertEqual(self.writes(), [])

    async def test_strategy_passes_the_gateway_verdict_through(self):
        from ha_mcp_engineering.f3_configuration.strategies import (
            SceneConfigurationConfigurationStrategy,
        )

        strategy = SceneConfigurationConfigurationStrategy(contract.ACTION)
        current, proposed = await self.bound()

        class Stale:
            async def scene_configuration_preflight_evidence(self, *_args):
                return ("stale_target_state",)

        class Denied:
            async def scene_configuration_preflight_evidence(self, *_args):
                raise AuthorizationError("synthetic")

        for gateway, bound_state, expected in (
            (Stale(), current, ("stale_target_state",)),
            (Denied(), current, ("permission_failure",)),
            (object(), current, ("dependency_evidence_unavailable",)),
            (Stale(), None, ("dependency_evidence_unavailable",)),
        ):
            with self.subTest(gateway=type(gateway).__name__, bound=bound_state is not None):
                self.assertEqual(
                    await strategy.preflight_evidence(gateway, LOUNGE, bound_state, proposed),
                    expected,
                )

    async def test_rollback_checks_the_forward_child_record(self):
        created, result = await self.applied(config_id=LOUNGE, name="New")
        child_id = self.runtime.children.declarations_for_task(
            result["task_id"])[0]["child_id"]
        children = self.runtime.children
        real_get = children.get
        record = real_get(child_id)
        cases = (
            (replace(record, normalized_outcome="manual_review_required"),
             "forward_not_verified"),
            (replace(record, dispatch_count=2), "forward_not_dispatched"),
            (replace(record, dispatch_intent=None), "forward_not_dispatched"),
            (replace(record, evidence={**record.evidence,
                                       "resulting_state_fingerprint": "0" * 64}),
             "forward_not_verified"),
        )
        for changed, reason_code in cases:
            with self.subTest(reason_code=reason_code):
                children.get = lambda item, _c=changed: _c if item == child_id else real_get(item)
                plans, writes = len(self.repository.list()), len(self.writes())
                try:
                    with self.assertRaises(GovernanceError) as raised:
                        await self.service.rollback_change(created["plan_id"])
                finally:
                    children.get = real_get
                self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
                self.assertEqual(raised.exception.details["reason_code"], reason_code)
                self.assertEqual(len(self.repository.list()), plans)
                self.assertEqual(len(self.writes()), writes)
        # The unaltered record still restores.
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_plan_created")

    async def test_rollback_route_rechecks_its_lease_before_persisting(self):
        from unittest.mock import patch

        created, _result = await self.applied(config_id=LOUNGE, name="New")
        real_prepare = planning.prepare
        persisted = []

        async def prepare_then_withdraw(*args, **kwargs):
            value = await real_prepare(*args, **kwargs)
            self.core.withdraw()
            return value

        async def persist(*args, **kwargs):
            persisted.append(kwargs)
            raise AssertionError("persisted under a withdrawn lease")

        # The final persistence check is stubbed: only the route's own
        # lease.require_current() can refuse.
        self.service._create_scene_configuration_plan = persist
        plans = len(self.repository.list())
        with patch.object(planning, "prepare", prepare_then_withdraw):
            with self.assertRaises(GovernanceError) as raised:
                await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(persisted, [])
        self.assertEqual(len(self.repository.list()), plans)
        self.assertEqual(len(self.writes()), 1)

    async def test_rollback_after_external_restore_is_drift(self):
        created, _result = await self.applied(config_id=LOUNGE, name="New")
        self.scene_rest.set_external(LOUNGE, name="Synthetic Lounge dim")
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.details["reason_code"],
                         "scene_configuration_rollback_baseline_drift")


class ProfileShapeTests(unittest.TestCase):
    def test_seq16_profile_shape_is_the_documented_shape(self):
        from ha_mcp_engineering.ha_core_readmission.profiles import (
            CORE_NOVEMBER_BATCH_PROFILES,
        )

        (profile,) = [
            item for item in CORE_NOVEMBER_BATCH_PROFILES
            if item.capability_id == contract.PROFILE
        ]
        self.assertEqual(profile.to_mapping(), {
            "capability_id": "core.scene_configuration_operation",
            "capability_class": "scene_configuration_operation",
            "profile_id": "core_scene_configuration_operation_v1",
            "profile_version": 1,
            "adapter_id": "compiled-core-scene-configuration-operation-v1",
            "contract_fingerprint": (
                "sha256:8fa78120c76aa1d892a21540e730479302293370126779ea89a59a4cb7b009d7"
            ),
            "required_checks": [
                "rest_scene_config_get_by_config_id",
                "rest_scene_config_post_by_config_id",
                "websocket_entity_registry_list_and_get",
                "scene_config_and_loaded_scene_readback_exact",
            ],
            "auto_eligible": True,
        })


if __name__ == "__main__":
    unittest.main()
