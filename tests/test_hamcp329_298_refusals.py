"""HAMCP-329/298 refusals and negative reachability.

The contract is ``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md``
§3.5 and §6.  Every test proves that nothing was sent (and, where the design
says so, that nothing was read) when a request, a profile admission, a
transport permit, a public surface or a plan's provenance does not hold.
Authorized successes are in ``test_hamcp329_298_created_objects.py``;
ownership and eligibility refusals in ``test_hamcp329_298_ownership.py``.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.clients import paths  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    created_object_delete,
    created_object_identity as identity_model,
    created_object_rollback as rollback_model,
    scene_configuration,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    evaluate_change_policy,
)

from tests.hamcp155_fixtures import PROFILES_155  # noqa: E402
from tests.hamcp329_298_fixtures import (  # noqa: E402
    API_URL,
    CreatedObjectServiceTestCase,
    EVENING,
    EVENING_ENTITY,
    HELPER_PROFILE,
    LAMP,
    SCENE_PREFIX,
    SCENE_PROFILE,
    automation_config,
    boolean_config,
    helper_operation,
    number_config,
    scene_request,
)


PENDING = rollback_model.PENDING_ADMISSION
SCENE_ROUTE = "/config/scene/config/{}"


class AdmissionRefusalTests(CreatedObjectServiceTestCase):
    """An unadmitted profile is refused before any read or stored state."""

    def assert_pending(self, error: GovernanceError, profile: str) -> None:
        self.assertEqual(error.code, ErrorCode.PROVIDER_UNAVAILABLE, error.details)
        self.assertEqual(error.details["reason"], PENDING, error.details)
        self.assertEqual(error.details["core_capability"], profile, error.details)

    async def test_unadmitted_scene_create_plan_reads_nothing_and_stores_nothing(self):
        self.core.withdraw(SCENE_PROFILE)
        mark = self.mark()
        with self.assertRaises(GovernanceError) as raised:
            await self.scene_plan()
        self.assert_pending(raised.exception, SCENE_PROFILE)
        self.assertIs(raised.exception.details["plan_created"], False)
        self.assertEqual(self.reads_since(mark), [])
        self.assertEqual(self.config_rest.scenes, {})

    async def test_unadmitted_scene_rollback_reads_nothing(self):
        created = await self.created_scene()
        self.core.withdraw(SCENE_PROFILE)
        mark = self.mark()
        self.assert_pending(await self.refused(created["plan_id"]), SCENE_PROFILE)
        self.assertEqual(self.reads_since(mark), [])
        self.assertIn(EVENING, self.config_rest.scenes)

    async def test_unadmitted_helper_rollback_reads_nothing(self):
        for domain, object_id in (
            ("input_boolean", "hvac_override"),
            ("input_number", "hvac_target"),
        ):
            with self.subTest(domain=domain):
                self.core.admit(HELPER_PROFILE)
                created = await self.created_helper(domain)
                self.core.withdraw(HELPER_PROFILE)
                mark = self.mark()
                self.assert_pending(
                    await self.refused(created["plan_id"]), HELPER_PROFILE
                )
                self.assertEqual(self.reads_since(mark), [])
                self.assertIn(object_id, self.ws.helpers[domain])

    async def test_one_profile_does_not_admit_the_other(self):
        scene = await self.created_scene()
        helper = await self.created_helper("input_boolean")
        self.core.withdraw(SCENE_PROFILE)
        self.assert_pending(await self.refused(scene["plan_id"]), SCENE_PROFILE)
        rollback = await self.service.rollback_change(helper["plan_id"])
        self.assertEqual(rollback["status"], "rollback_plan_created", rollback)
        self.core.admit(SCENE_PROFILE)
        self.core.withdraw(HELPER_PROFILE)
        mark = self.mark()
        result = await self.apply_rollback(
            await self.service.rollback_change(scene["plan_id"])
        )
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.writes_since(mark), [("DELETE", f"{SCENE_PREFIX}{EVENING}")]
        )

    async def assert_apply_refused(self, plan: dict, profile: str) -> None:
        mark = self.mark()
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(plan)
        self.assert_pending(raised.exception, profile)
        self.assertIs(raised.exception.details["execution_state_persisted"], False)
        self.assertIs(raised.exception.details["provider_dispatch_occurred"], False)
        self.assertEqual(self.reads_since(mark), [])
        self.assertIsNone(self.service.task_repository.get_for_plan(plan["plan_id"]))

    async def test_scene_create_withdrawn_after_approval_never_starts(self):
        plan = await self.scene_plan()
        await self.approve(plan)
        self.core.withdraw(SCENE_PROFILE)
        await self.assert_apply_refused(plan, SCENE_PROFILE)
        self.assertEqual(self.config_rest.scene_posts, [])

    async def test_scene_delete_withdrawn_after_approval_never_starts(self):
        _rollback, plan = await self.rollback_plan(await self.created_scene())
        self.core.withdraw(SCENE_PROFILE)
        await self.assert_apply_refused(plan, SCENE_PROFILE)
        self.assertEqual(self.config_rest.scene_deletes(), [])

    async def test_helper_delete_withdrawn_after_approval_never_starts(self):
        _rollback, plan = await self.rollback_plan(
            await self.created_helper("input_number")
        )
        self.core.withdraw(HELPER_PROFILE)
        await self.assert_apply_refused(plan, HELPER_PROFILE)
        self.assertEqual(self.ws.helper_commands("delete"), [])

    async def test_capture_while_unadmitted_reads_nothing_and_keeps_the_create(self):
        self.core.withdraw(HELPER_PROFILE)
        gateway = self.resource_gateway.created_object_delete
        with mock.patch.object(
            gateway, "registry_entry", wraps=gateway.registry_entry
        ) as registry, mock.patch.object(
            gateway, "read_config", wraps=gateway.read_config
        ) as body:
            created = await self.created_helper("input_boolean")
        registry.assert_not_called()
        body.assert_not_called()
        self.assertEqual(self.capture_sleeps, [])
        (record,) = self.identity_values(created["plan_id"])
        self.assertEqual(record["outcome"], "capability_unadmitted", record)
        self.assertIsNone(record["identity"])
        # Admission later never adopts an object whose identity was not
        # captured when it was created.
        self.core.admit(HELPER_PROFILE)
        mark = self.mark()
        await self.refused_rollback(
            created, {"create_helper": rollback_model.IDENTITY_NOT_CAPTURED}
        )
        self.assertEqual(self.reads_since(mark), [])

    async def created_pair(self, *creates: dict) -> dict:
        """Update an existing automation, then create two objects in order."""

        self.add("automation", "consumer")
        proposed = deepcopy(self.config_rest.configs["automation"]["consumer"])
        proposed["description"] = "edited before the creates"
        operations = [
            {
                "operation_id": "update_consumer",
                "resource_type": "automation",
                "action": "update",
                "target_id": "consumer",
                "depends_on": [],
                "proposed_config": proposed,
            }
        ]
        for create in creates:
            operations.append(
                {**create, "depends_on": [operations[-1]["operation_id"]]}
            )
        return await self.applied(await self.configuration_plan(*operations))

    async def assert_unadmitted_delete_blocks_the_earlier_create(
        self, created: dict, withdrawn: str, later: str, earlier: str
    ) -> None:
        self.core.withdraw(withdrawn)
        mark = self.mark()
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_partial_plan_created", rollback)
        self.assertEqual(rollback["operations_to_delete"], [])
        self.assertEqual(len(rollback["operations_to_restore"]), 1)
        self.assertEqual(
            rollback["operations_excluded"],
            [
                {"operation_id": later, "reason": PENDING},
                {
                    "operation_id": earlier,
                    "reason": rollback_model.PREREQUISITE_EXCLUDED,
                    "prerequisite": later,
                },
            ],
        )
        self.assertEqual(
            [
                (item.resource_type, item.action)
                for item in self.operations(rollback["rollback_plan_id"])
            ],
            [("automation", "update")],
        )
        # Neither excluded object is read: only the restore target is.
        self.assertEqual(
            set(self.reads_since(mark)),
            {("GET", "/config/automation/config/consumer")},
        )
        self.assertEqual(self.writes_since(mark), [])

    async def test_unadmitted_helper_delete_blocks_the_earlier_created_automation(
        self,
    ):
        created = await self.created_pair(
            {
                "operation_id": "create_automation",
                "resource_type": "automation",
                "action": "create",
                "target_id": "new_consumer",
                "proposed_config": automation_config("new_consumer"),
            },
            helper_operation("input_boolean", boolean_config()),
        )
        await self.assert_unadmitted_delete_blocks_the_earlier_create(
            created, HELPER_PROFILE, "create_helper", "create_automation"
        )
        self.assertIn("new_consumer", self.config_rest.configs["automation"])
        self.assertIn("hvac_override", self.ws.helpers["input_boolean"])

    async def test_unadmitted_automation_delete_blocks_the_earlier_created_helper(
        self,
    ):
        created = await self.created_pair(
            helper_operation("input_number", number_config()),
            {
                "operation_id": "create_automation",
                "resource_type": "automation",
                "action": "create",
                "target_id": "new_consumer",
                "proposed_config": automation_config("new_consumer"),
            },
        )
        (delete_profile,) = PROFILES_155
        await self.assert_unadmitted_delete_blocks_the_earlier_create(
            created, delete_profile, "create_automation", "create_helper"
        )
        self.assertIn("new_consumer", self.config_rest.configs["automation"])
        self.assertIn("hvac_target", self.ws.helpers["input_number"])


class SceneCreateRefusalTests(CreatedObjectServiceTestCase):
    async def refused_plan(self, request: dict) -> GovernanceError:
        mark = self.mark()
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_scene_configuration_plan(request)
        self.assertEqual(self.writes_since(mark), [])
        self.assertEqual(self.config_rest.scene_posts, [])
        return raised.exception

    async def test_existing_scene_id_is_refused_at_planning(self):
        self.config_rest.add_scene(
            EVENING, {"name": "Already here", "entities": {LAMP: {"state": "on"}}}
        )
        before = deepcopy(self.config_rest.scenes)
        error = await self.refused_plan(scene_request())
        self.assertEqual(error.code, ErrorCode.INVALID_REQUEST, error.details)
        self.assertEqual(
            error.details["reason"], "scene_configuration_target_exists", error.details
        )
        self.assertEqual(self.config_rest.scenes, before)

    async def test_registered_unique_id_is_refused_at_planning(self):
        # No stored scene, but a homeassistant scene entity already owns the
        # config id as its unique id.
        self.config_rest.register_scene(EVENING, {"name": "Orphan"})
        error = await self.refused_plan(scene_request())
        self.assertEqual(error.code, ErrorCode.INVALID_REQUEST, error.details)
        self.assertEqual(
            error.details["reason"],
            "scene_configuration_config_id_registered",
            error.details,
        )

    async def test_scene_added_after_approval_is_refused_at_preflight(self):
        plan = await self.scene_plan()
        await self.approve(plan)
        self.config_rest.add_scene(
            EVENING, {"name": "Raced", "entities": {LAMP: {"state": "on"}}}
        )
        before = deepcopy(self.config_rest.scenes)
        mark = self.mark()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIs(result["provider_dispatch_occurred"], False)
        self.assertEqual(
            self.children(plan["plan_id"]),
            [("scene_create_1", "terminal", "preflight_rejected", 0)],
        )
        self.assertEqual(
            self.codes(plan["plan_id"]), {"scene_configuration_target_exists"}
        )
        self.assertEqual(self.writes_since(mark), [])
        self.assertEqual(self.config_rest.scenes, before)
        self.assertEqual(self.identity_values(plan["plan_id"]), [None])

    async def test_unsupported_and_invalid_requests_read_nothing(self):
        cases = (
            (
                scene_request(entity_id="scene.chosen"),
                "scene_configuration_field_not_supported",
                {"fields": ["entity_id"]},
            ),
            (
                scene_request(entities={LAMP: None}),
                "scene_configuration_invalid_value",
                {"field": "entities"},
            ),
            (
                scene_request(icon=None),
                "scene_configuration_invalid_value",
                {"field": "icon"},
            ),
            (
                scene_request(snapshot_entities=[LAMP]),
                "scene_configuration_field_not_supported",
                {"fields": ["snapshot_entities"]},
            ),
            (
                {"action": "create", "config_id": EVENING, "name": "No members"},
                "scene_configuration_create_fields_required",
                {},
            ),
            (
                {
                    "action": "create",
                    "config_id": EVENING,
                    "entities": {LAMP: {"state": "on"}},
                },
                "scene_configuration_create_fields_required",
                {},
            ),
        )
        for request, reason, details in cases:
            with self.subTest(reason=reason, request=sorted(request)):
                mark = self.mark()
                error = await self.refused_plan(request)
                self.assertEqual(error.code, ErrorCode.INVALID_REQUEST, error.details)
                self.assertEqual(error.details["reason"], reason, error.details)
                for key, value in details.items():
                    self.assertEqual(error.details[key], value, error.details)
                self.assertEqual(self.reads_since(mark), [])

    async def test_an_omitted_or_update_action_never_creates(self):
        for request in (
            {key: value for key, value in scene_request().items() if key != "action"},
            scene_request(action="update"),
        ):
            with self.subTest(action=request.get("action")):
                error = await self.refused_plan(request)
                self.assertEqual(error.code, ErrorCode.ENTITY_NOT_FOUND, error.details)
                self.assertEqual(
                    error.details["reason"], "scene_configuration_target_not_found"
                )
                self.assertIs(error.details["plan_created"], False)
                self.assertEqual(self.config_rest.scenes, {})
        mark = self.mark()
        error = await self.refused_plan(scene_request(action="delete"))
        self.assertEqual(error.code, ErrorCode.INVALID_REQUEST, error.details)
        self.assertEqual(error.details["reason"], "scene_configuration_invalid_value")
        self.assertEqual(error.details["field"], "action")
        self.assertEqual(self.reads_since(mark), [])
        self.assertEqual(self.config_rest.scene_deletes(), [])

    async def test_scene_services_are_never_sent(self):
        for never in (
            "scene.create",
            "scene.apply",
            "scene.turn_on",
            "scene.reload",
            "scene.delete",
        ):
            self.assertIn(never, scene_configuration.NEVER_SENT)
        created = await self.created_scene()
        rollback, plan = await self.rollback_plan(created)
        await self.apply(plan)
        recreate = await self.service.rollback_change(rollback["rollback_plan_id"])
        await self.apply_rollback(recreate)
        self.assertEqual(self.service_calls(), [])
        self.assertEqual(
            {
                (method, path)
                for method, path in self.config_rest.requests
                if path.startswith(SCENE_PREFIX) and method != "GET"
            },
            {
                ("POST", f"{SCENE_PREFIX}{EVENING}"),
                ("DELETE", f"{SCENE_PREFIX}{EVENING}"),
            },
        )


class SceneDeletePermitTests(unittest.TestCase):
    """clients/paths: the scene config DELETE needs its single-use permit."""

    def route(self, config_id: str = EVENING):
        return paths.rest_path(SCENE_ROUTE, config_id)

    def refusal(self, call) -> str:
        with self.assertRaises(paths.UnsafeRequestPathError) as raised:
            call()
        return raised.exception.details["reason"]

    def test_delete_without_a_permit_is_refused(self):
        route = self.route()
        self.assertEqual(
            self.refusal(lambda: paths.require_route(route, "DELETE")),
            "scene_delete_permit_required",
        )
        self.assertEqual(
            self.refusal(lambda: paths.request_url(API_URL, route, "DELETE")),
            "scene_delete_permit_required",
        )

    def test_permit_is_for_exactly_one_config_id(self):
        with paths.scene_delete_permit(EVENING):
            self.assertEqual(
                self.refusal(
                    lambda: paths.request_url(
                        API_URL, self.route("another_scene"), "DELETE"
                    )
                ),
                "scene_delete_permit_required",
            )
            # The refused id did not consume the permit of the right one.
            url = paths.request_url(API_URL, self.route(), "DELETE")
        self.assertTrue(str(url).endswith(f"/config/scene/config/{EVENING}"))

    def test_permit_is_single_use(self):
        route = self.route()
        with paths.scene_delete_permit(EVENING):
            # Checking the route never claims; building the URL does.
            paths.require_route(route, "DELETE")
            paths.require_route(route, "DELETE")
            paths.request_url(API_URL, route, "DELETE")
            self.assertEqual(
                self.refusal(lambda: paths.request_url(API_URL, route, "DELETE")),
                "scene_delete_permit_required",
            )
            self.assertEqual(
                self.refusal(lambda: paths.require_route(route, "DELETE")),
                "scene_delete_permit_required",
            )

    def test_permit_ends_with_its_context(self):
        route = self.route()
        with paths.scene_delete_permit(EVENING):
            pass
        self.assertEqual(
            self.refusal(lambda: paths.request_url(API_URL, route, "DELETE")),
            "scene_delete_permit_required",
        )
        with self.assertRaises(RuntimeError):
            with paths.scene_delete_permit(EVENING):
                raise RuntimeError("send failed")
        self.assertEqual(
            self.refusal(lambda: paths.request_url(API_URL, route, "DELETE")),
            "scene_delete_permit_required",
        )

    def test_invalid_permit_is_refused(self):
        for value in ("", None, 7, [EVENING]):
            with self.subTest(value=value):
                def enter(value=value):
                    with paths.scene_delete_permit(value):
                        self.fail("an invalid permit was installed")

                self.assertEqual(self.refusal(enter), "scene_delete_permit_invalid")

    def test_permit_admits_no_other_method_or_route(self):
        route = self.route()
        with paths.scene_delete_permit(EVENING):
            for method in ("PUT", "PATCH", None):
                with self.subTest(method=method):
                    self.assertEqual(
                        self.refusal(lambda: paths.request_url(API_URL, route, method)),
                        "route_method_not_allowed",
                    )
            self.assertEqual(
                self.refusal(
                    lambda: paths.request_url(
                        API_URL, f"/config/scene/config/{EVENING}", "DELETE"
                    )
                ),
                "unbound_request_path",
            )
        # Reads and the reviewed POST never needed a permit.
        for method in ("GET", "POST"):
            paths.request_url(API_URL, route, method)


class SceneDeleteReachabilityTests(CreatedObjectServiceTestCase):
    async def test_the_synthetic_core_refuses_a_delete_without_the_permit(self):
        await self.created_scene()
        route = paths.rest_path(SCENE_ROUTE, EVENING)
        before = list(self.config_rest.requests)
        with self.assertRaises(paths.UnsafeRequestPathError) as raised:
            await self.config_rest.request("DELETE", route)
        self.assertEqual(
            raised.exception.details["reason"], "scene_delete_permit_required"
        )
        self.assertEqual(self.config_rest.requests, before)
        self.assertIn(EVENING, self.config_rest.scenes)

    async def test_the_scene_configuration_gateway_has_no_delete(self):
        gateway = self.resource_gateway.scene_configuration
        self.assertFalse(
            [name for name in dir(gateway) if "delete" in name.lower()],
            "the scene configuration gateway must not grow a delete",
        )
        source = Path(scene_configuration.__file__).read_text(encoding="utf-8")
        self.assertNotIn('request("DELETE"', source)
        self.assertNotIn("scene_delete_permit", source)

    async def test_only_the_created_object_gateway_installs_the_permit(self):
        package = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
        users = sorted(
            str(path.relative_to(package))
            for path in package.rglob("*.py")
            if "scene_delete_permit(" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            users, ["clients/paths.py", "governance/created_object_delete.py"]
        )

    async def test_a_governed_delete_sends_exactly_one_delete(self):
        _rollback, plan = await self.rollback_plan(await self.created_scene())
        mark = self.mark()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.writes_since(mark), [("DELETE", f"{SCENE_PREFIX}{EVENING}")]
        )
        again = await self.apply(plan)
        self.assertEqual(again["status"], "already_applied", again)
        self.assertIs(again["redispatch_performed"], False)
        self.assertEqual(len(self.config_rest.scene_deletes()), 1)


class PublicSurfaceTests(CreatedObjectServiceTestCase):
    """No public tool or request shape constructs a scene or helper delete."""

    async def test_the_public_delete_tool_refuses_scene_and_helper_types(self):
        await self.created_scene()
        await self.created_helper("input_boolean")
        for resource_type, target_id in (
            ("scene_configuration", EVENING),
            ("scene", EVENING_ENTITY),
            ("input_boolean", "input_boolean.hvac_override"),
            ("input_number", "input_number.hvac_target"),
            ("helper", "input_boolean.hvac_override"),
        ):
            with self.subTest(resource_type=resource_type):
                mark = self.mark()
                with self.assertRaises(GovernanceError) as raised:
                    await self.create_one(resource_type, target_id)
                error = raised.exception
                self.assertEqual(error.code, ErrorCode.INVALID_REQUEST, error.details)
                self.assertEqual(
                    error.details["reason"],
                    "configuration_delete_resource_type_not_supported",
                    error.details,
                )
                self.assertEqual(error.details["field"], "resource_type")
                self.assertEqual(self.writes_since(mark), [])
        self.assertIn(EVENING, self.config_rest.scenes)
        self.assertIn("hvac_override", self.ws.helpers["input_boolean"])

    async def test_a_generic_plan_cannot_carry_a_delete_operation(self):
        await self.created_scene()
        await self.created_helper("input_boolean")
        for operation in (
            {
                "operation_id": "delete_1",
                "resource_type": "scene_configuration",
                "action": "delete",
                "target_id": EVENING,
            },
            dict(
                helper_operation("input_boolean", boolean_config()),
                operation_id="delete_1",
                action="delete",
            ),
        ):
            with self.subTest(resource_type=operation["resource_type"]):
                mark = self.mark()
                with self.assertRaises(GovernanceError) as raised:
                    await self.configuration_plan(operation)
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    raised.exception.details,
                )
                self.assertEqual(self.writes_since(mark), [])

    async def marked_plan(self, created: dict):
        rollback = await self.service.rollback_change(created["plan_id"])
        return deepcopy(self.service._load(rollback["rollback_plan_id"]))

    async def test_a_delete_child_without_its_marker_is_prohibited(self):
        for label, create in (
            ("scene", self.created_scene),
            ("helper", lambda: self.created_helper("input_number")),
        ):
            with self.subTest(child=label):
                plan = await self.marked_plan(await create())
                self.assertEqual(
                    evaluate_change_policy(plan).policy_class.value, "standard_admin"
                )
                operation = plan.operations[0]
                operation.risk.evidence[:] = [
                    item
                    for item in operation.risk.evidence
                    if not (
                        isinstance(item, dict)
                        and item.get("trigger") == rollback_model.POLICY_TRIGGER
                    )
                ]
                decision = evaluate_change_policy(plan)
                self.assertEqual(decision.policy_class.value, "prohibited")
                self.assertIn(
                    "configuration_delete_contract_incomplete", decision.reason_codes
                )

    async def test_a_tampered_marker_is_prohibited(self):
        for label, create in (
            ("scene", self.created_scene),
            ("helper", lambda: self.created_helper("input_boolean")),
        ):
            base = await self.marked_plan(await create())
            for change in (
                {"ownership": rollback_model.MODIFIED_SINCE_CREATE},
                {"basis": rollback_model.BASIS_MODIFIED_SINCE_CREATE},
                {"registry_entry_id": "another-entry"},
                {"created_fingerprint": "0" * 64},
                {"observed_entity_id": "scene.someone_elses"},
                {"field": "delete_9"},
                {"model": "hamcp298-created-object-rollback-v0"},
                {"extra": "x"},
            ):
                with self.subTest(child=label, change=sorted(change)):
                    plan = deepcopy(base)
                    (marker,) = rollback_model.markers(plan.operations[0])
                    marker.update(change)
                    self.assertEqual(
                        evaluate_change_policy(plan).policy_class.value, "prohibited"
                    )
            with self.subTest(child=label, change="doubled"):
                plan = deepcopy(base)
                (marker,) = rollback_model.markers(plan.operations[0])
                plan.operations[0].risk.evidence.append(dict(marker))
                self.assertEqual(
                    evaluate_change_policy(plan).policy_class.value, "prohibited"
                )

    def test_the_helper_delete_builder_is_closed(self):
        build = created_object_delete.helper_delete_command
        self.assertEqual(
            build("input_boolean", "input_boolean.hvac_override"),
            {"type": "input_boolean/delete", "input_boolean_id": "hvac_override"},
        )
        self.assertEqual(
            build("input_number", "input_number.hvac_target"),
            {"type": "input_number/delete", "input_number_id": "hvac_target"},
        )
        for resource_type, target_id in (
            ("input_select", "input_select.mode"),
            ("input_text", "input_text.note"),
            ("scene_configuration", EVENING),
            ("automation", "porch_light"),
            ("helper", "input_boolean.hvac_override"),
        ):
            with self.subTest(resource_type=resource_type):
                with self.assertRaises(ValueError):
                    build(resource_type, target_id)
        for resource_type, target_id in (
            ("input_boolean", "input_number.hvac_target"),
            ("input_number", "input_boolean.hvac_override"),
            ("input_boolean", "hvac_override"),
            ("input_boolean", "input_boolean.hvac.override"),
        ):
            with self.subTest(target_id=target_id):
                with self.assertRaises(ValueError):
                    build(resource_type, target_id)


class ForgedProvenanceTests(CreatedObjectServiceTestCase):
    """§3.5: a self-consistent marker whose provenance does not verify.

    The forged plan is stored whole (plan, policy snapshot and approval
    binding agree with one another) and then approved, so only the executor's
    own check against the stored source plan and identity record is left.
    """

    async def assert_refused_before_intent(self, created: dict, change: dict) -> None:
        rollback = await self.service.rollback_change(created["plan_id"])
        forged = deepcopy(self.service._load(rollback["rollback_plan_id"]))
        (marker,) = rollback_model.markers(forged.operations[0])
        marker.update(change)
        self.assertTrue(rollback_model.valid_marker(marker, forged.operations[0]))
        forged.policy_decision = evaluate_change_policy(forged)
        self.assertEqual(forged.policy_decision.policy_class.value, "standard_admin")
        forged.approval.policy_decision_hash = (
            forged.policy_decision.policy_decision_hash
        )
        self.service._save(forged)
        plan = self.service.get_plan(rollback["rollback_plan_id"])
        self.assertNotEqual(plan["plan_hash"], rollback["plan_hash"])
        await self.approve(plan)
        await self.assert_binding_refused(plan)

    async def assert_binding_refused(self, plan: dict) -> None:
        mark = self.mark()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIs(result["provider_dispatch_occurred"], False)
        self.assertEqual(result["dispatch_phase"], "not_dispatched_pre_intent")
        self.assertEqual(
            self.children(plan["plan_id"]),
            [("delete_1", "terminal", "preflight_rejected", 0)],
        )
        self.assertEqual(
            self.codes(plan["plan_id"]), {"created_object_binding_unavailable"}
        )
        self.assertEqual(self.writes_since(mark), [])

    async def test_scene_marker_with_another_source_plan_hash(self):
        await self.assert_refused_before_intent(
            await self.created_scene(), {"source_plan_hash": "0" * 64}
        )
        self.assertIn(EVENING, self.config_rest.scenes)

    async def test_scene_marker_naming_an_unknown_source_plan(self):
        await self.assert_refused_before_intent(
            await self.created_scene(), {"source_plan_id": "0" * 32}
        )

    async def test_scene_marker_naming_another_plan_that_created_nothing_here(self):
        other = await self.created_helper("input_boolean")
        await self.assert_refused_before_intent(
            await self.created_scene(),
            {
                "source_plan_id": other["plan_id"],
                "source_plan_hash": self.service.plan_hash(
                    self.service._load(other["plan_id"])
                ),
                "source_operation_id": "create_helper",
            },
        )
        self.assertIn(EVENING, self.config_rest.scenes)

    async def test_scene_marker_with_another_source_operation_or_child(self):
        await self.assert_refused_before_intent(
            await self.created_scene(), {"source_operation_id": "scene_create_9"}
        )

    async def test_scene_marker_with_another_source_child(self):
        await self.assert_refused_before_intent(
            await self.created_scene(), {"source_child_id": "child-of-another-run"}
        )

    async def test_scene_marker_with_another_capture_fingerprint(self):
        await self.assert_refused_before_intent(
            await self.created_scene(), {"capture_fingerprint": "1" * 64}
        )

    async def test_helper_marker_with_another_source_plan_hash(self):
        for domain, object_id in (
            ("input_boolean", "hvac_override"),
            ("input_number", "hvac_target"),
        ):
            with self.subTest(domain=domain):
                await self.assert_refused_before_intent(
                    await self.created_helper(domain), {"source_plan_hash": "0" * 64}
                )
                self.assertIn(object_id, self.ws.helpers[domain])

    async def test_automation_marker_with_another_source_plan_hash(self):
        created = await self.applied(
            await self.configuration_plan(
                {
                    "operation_id": "create_1",
                    "resource_type": "automation",
                    "action": "create",
                    "target_id": "porch_light",
                    "proposed_config": automation_config("porch_light"),
                }
            )
        )
        await self.assert_refused_before_intent(
            created, {"source_plan_hash": "0" * 64}
        )
        self.assertIn("porch_light", self.config_rest.configs["automation"])

    async def test_a_removed_identity_record_no_longer_verifies(self):
        created = await self.created_scene()
        _rollback, plan = await self.rollback_plan(created)
        (declaration,) = self.runtime.children.declarations_for_task(
            self.task_id(created["plan_id"])
        )
        self.runtime.created_identities._path(declaration["child_id"]).unlink()
        await self.assert_binding_refused(plan)
        self.assertIn(EVENING, self.config_rest.scenes)


def entry(domain: str, unique_id: str, entity_id: str, **changes) -> dict:
    return {
        "platform": domain,
        "unique_id": unique_id,
        "entity_id": entity_id,
        "id": "reg1",
        "created_at": 1.5,
        "modified_at": 1.5,
        **changes,
    }


class IdentityModelTests(unittest.TestCase):
    """§3.3: the per-type identity correspondence and the delete argument."""

    FINGERPRINT = "f" * 64

    def test_scene_identity_uses_the_homeassistant_platform_and_config_id(self):
        identity = identity_model.identity_from(
            "scene_configuration",
            EVENING,
            entry("homeassistant", EVENING, "scene.any_name"),
            self.FINGERPRINT,
        )
        self.assertEqual(identity["registry_domain"], "scene")
        self.assertEqual(identity["registry_platform"], "homeassistant")
        self.assertEqual(identity["registry_unique_id"], EVENING)
        self.assertEqual(identity["entity_id"], "scene.any_name")
        self.assertEqual(
            identity["delete_argument"],
            {"method": "DELETE", "path": f"/config/scene/config/{EVENING}"},
        )
        self.assertEqual(identity_model.validate_identity(identity), identity)
        for wrong in (
            entry("scene", EVENING, "scene.any_name"),
            entry("homeassistant", "another_id", "scene.any_name"),
            entry("homeassistant", EVENING, "light.any_name"),
            entry("homeassistant", EVENING, "scene.any_name", id=""),
        ):
            with self.subTest(entry=wrong):
                with self.assertRaises(identity_model.IdentityMismatch):
                    identity_model.identity_from(
                        "scene_configuration", EVENING, wrong, self.FINGERPRINT
                    )

    def test_helper_identity_is_the_storage_object_id_of_its_own_domain(self):
        for domain, other in (
            ("input_boolean", "input_number"),
            ("input_number", "input_boolean"),
        ):
            target = f"{domain}.hvac_item"
            with self.subTest(domain=domain):
                identity = identity_model.identity_from(
                    domain,
                    target,
                    entry(domain, "hvac_item", f"{domain}.renamed_by_user"),
                    self.FINGERPRINT,
                )
                self.assertEqual(identity["storage_object_id"], "hvac_item")
                self.assertEqual(identity["registry_platform"], domain)
                self.assertEqual(identity["entity_id"], f"{domain}.renamed_by_user")
                self.assertEqual(
                    identity["delete_argument"],
                    {"type": f"{domain}/delete", f"{domain}_id": "hvac_item"},
                )
                for wrong in (
                    entry(other, "hvac_item", f"{domain}.hvac_item"),
                    entry(domain, "another_item", f"{domain}.hvac_item"),
                    entry(domain, "hvac_item", f"{other}.hvac_item"),
                ):
                    with self.assertRaises(identity_model.IdentityMismatch):
                        identity_model.identity_from(
                            domain, target, wrong, self.FINGERPRINT
                        )
                # A target of the other domain, a bare object id, or a dotted
                # object id never yields a delete argument.
                for bad in (f"{other}.hvac_item", "hvac_item", f"{domain}.hvac.item"):
                    with self.assertRaises(identity_model.IdentityMismatch):
                        identity_model.delete_argument(domain, bad)
                with self.assertRaises(identity_model.IdentityMismatch):
                    identity_model.delete_argument("input_select", "input_select.mode")

    def test_a_tampered_stored_identity_does_not_validate(self):
        identity = identity_model.identity_from(
            "input_number",
            "input_number.hvac_target",
            entry("input_number", "hvac_target", "input_number.hvac_target"),
            self.FINGERPRINT,
        )
        for change in (
            {"delete_argument": {"type": "input_number/delete", "input_number_id": "other"}},
            {"delete_argument": {"type": "input_boolean/delete", "input_boolean_id": "hvac_target"}},
            {"storage_object_id": "other"},
            {"registry_unique_id": "other"},
            {"registry_platform": "input_boolean"},
            {"resource_target": "input_number.other"},
            {"registry_entry_id": "reg2"},
            {"model": "hamcp298-created-identity-v0"},
        ):
            with self.subTest(change=sorted(change)):
                forged = {**identity, **change}
                with self.assertRaises(identity_model.IdentityMismatch):
                    identity_model.validate_identity(forged)
                # Even re-fingerprinted, a non-corresponding record is refused.
                if "registry_entry_id" not in change:
                    forged["capture_fingerprint"] = identity_model.capture_fingerprint(
                        forged
                    )
                    with self.assertRaises(identity_model.IdentityMismatch):
                        identity_model.validate_identity(forged)

    def test_a_present_object_with_no_corresponding_entry_is_not_absent(self):
        observe = identity_model.observed_ownership
        for resource_type, target, domain, unique_id in (
            ("scene_configuration", EVENING, "homeassistant", EVENING),
            ("input_boolean", "input_boolean.hvac_override", "input_boolean", "hvac_override"),
            ("input_number", "input_number.hvac_target", "input_number", "hvac_target"),
        ):
            with self.subTest(resource_type=resource_type):
                entity = (
                    "scene.synthetic_evening"
                    if resource_type == "scene_configuration"
                    else target
                )
                identity = identity_model.identity_from(
                    resource_type,
                    target,
                    entry(domain, unique_id, entity),
                    self.FINGERPRINT,
                )
                self.assertEqual(
                    observe(identity, None, self.FINGERPRINT)["status"],
                    rollback_model.IDENTITY_CHANGED,
                )
                self.assertEqual(
                    observe(identity, None, None)["status"],
                    rollback_model.ALREADY_ABSENT,
                )
                self.assertEqual(
                    observe(identity, entry(domain, unique_id, entity), self.FINGERPRINT)[
                        "status"
                    ],
                    rollback_model.OWNED,
                )


class CountingGateway:
    """The two capture reads, counted."""

    def __init__(self, entries: list, body, *, fail: str | None = None) -> None:
        self.entries = list(entries)
        self.body = body
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    async def registry_entry(self, resource_type: str, target_id: str):
        self.calls.append(("registry_entry", resource_type, target_id))
        if self.fail == "registry_entry":
            raise RuntimeError("registry read failed")
        return self.entries.pop(0) if self.entries else None

    async def read_config(self, resource_type: str, target_id: str):
        self.calls.append(("read_config", resource_type, target_id))
        if self.fail == "read_config":
            raise RuntimeError("config read failed")
        return self.body


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    """§3.3: the bounded, read-only capture never raises and never guesses."""

    TARGET = "input_boolean.hvac_override"

    def setUp(self) -> None:
        from ha_mcp_engineering.governance.resources import resource_fingerprint

        self.body = {"id": "hvac_override", **boolean_config()}
        self.fingerprint = resource_fingerprint("input_boolean", self.body)
        self.entry = entry("input_boolean", "hvac_override", self.TARGET)
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    async def capture(self, gateway: CountingGateway, target: str | None = None):
        return await identity_model.capture(
            gateway,
            "input_boolean",
            target or self.TARGET,
            self.fingerprint,
            sleep=self.sleep,
        )

    def kinds(self, gateway: CountingGateway) -> list[str]:
        return [call[0] for call in gateway.calls]

    async def test_captured_on_the_first_read(self):
        gateway = CountingGateway([self.entry], self.body)
        outcome, identity = await self.capture(gateway)
        self.assertEqual(outcome, identity_model.CAPTURED)
        self.assertEqual(identity["registry_entry_id"], "reg1")
        self.assertEqual(self.kinds(gateway), ["registry_entry", "read_config"])
        self.assertEqual(self.sleeps, [])

    async def test_a_late_entry_is_polled_within_the_bound(self):
        gateway = CountingGateway([None, None, self.entry], self.body)
        outcome, _identity = await self.capture(gateway)
        self.assertEqual(outcome, identity_model.CAPTURED)
        self.assertEqual(
            self.kinds(gateway), ["registry_entry"] * 3 + ["read_config"]
        )
        self.assertEqual(self.sleeps, [identity_model.POLL_INTERVAL_SECONDS] * 2)

    async def test_an_entry_that_never_appears_is_not_observed(self):
        gateway = CountingGateway([], self.body)
        self.assertEqual(
            await self.capture(gateway), (identity_model.NOT_OBSERVED, None)
        )
        self.assertEqual(
            self.kinds(gateway), ["registry_entry"] * identity_model.POLL_ATTEMPTS
        )
        self.assertEqual((identity_model.POLL_ATTEMPTS, identity_model.POLL_INTERVAL_SECONDS), (6, 0.5))
        self.assertEqual(self.sleeps, [0.5] * 5)

    async def test_a_failed_read_is_reported_and_never_raised(self):
        for fail in ("registry_entry", "read_config"):
            with self.subTest(fail=fail):
                gateway = CountingGateway([self.entry], self.body, fail=fail)
                self.assertEqual(
                    await self.capture(gateway), (identity_model.READ_FAILED, None)
                )

    async def test_a_different_body_or_entry_is_an_identity_mismatch(self):
        for gateway in (
            CountingGateway([self.entry], dict(self.body, name="Changed")),
            CountingGateway([self.entry], None),
            CountingGateway([dict(self.entry, platform="input_number")], self.body),
            CountingGateway([dict(self.entry, unique_id="other")], self.body),
            CountingGateway([dict(self.entry, id="")], self.body),
        ):
            with self.subTest(entry=gateway.entries, body=gateway.body):
                self.assertEqual(
                    await self.capture(gateway), (identity_model.IDENTITY_MISMATCH, None)
                )

    async def test_a_target_of_another_domain_is_never_read(self):
        for target in ("input_number.hvac_override", "hvac_override"):
            with self.subTest(target=target):
                gateway = CountingGateway([self.entry], self.body)
                self.assertEqual(
                    await self.capture(gateway, target),
                    (identity_model.IDENTITY_MISMATCH, None),
                )
                self.assertEqual(gateway.calls, [])


if __name__ == "__main__":
    unittest.main()
