"""HAMCP-145 governed input_select option: admission, planning, execution, rollback.

Every service path drives the shipped service, the F3 executor and runtime,
and the real ``InputSelectGateway`` against the synthetic Home Assistant in
``tests/hamcp145_input_select_fixtures.py``, which refuses any call outside a
Core-authority window exactly as the real clients do and fails the test on
any request outside the reviewed vocabulary.  The dependency reader is
exercised against a real ``DependencyIndex`` over synthetic automations.

``core.typed_input_select_option_operation`` is runtime-only: no signed
registry references it, so it is inactive in every shipped build.  The Core
double admits it here only where a test says so.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.dependency.state_trigger_values import (  # noqa: E402
    extract_state_trigger_values,
    option_effect,
)
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.f3.contracts import LockMode  # noqa: E402
from ha_mcp_engineering.f3_configuration.locks import (  # noqa: E402
    resource_lock_key,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    input_select_option as contract,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    input_select_option_planning as planning,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HELPER_DEPENDENCY_RISK_MODEL,
    HelperDependencyRiskService,
)
from ha_mcp_engineering.governance.input_select_dependency import (  # noqa: E402
    INPUT_SELECT_DEPENDENCY_MODEL,
    REPROJECTED_REASON,
    build_input_select_dependency_binding,
    failed_input_select_binding,
    option_trigger_disclosure,
)

from tests.hamcp145_input_select_fixtures import (  # noqa: E402
    ENTITY,
    OPTIONS,
    InputSelectServiceTestCase,
    SyntheticInputSelectRest,
    SyntheticInputSelectWebSocket,
    trigger_record,
)


PENDING = "pending_signed_registry_admission"
STALE = "stale_target_state"
PENDING_DETAILS = {
    "reason": PENDING,
    "reason_code": PENDING,
    "resource_type": contract.TARGET_TYPE,
    "core_capability": contract.PROFILE,
    "plan_created": False,
    "provider_dispatch_occurred": False,
}
SECRET = "synthetic-secret-value"


def extract(trigger, key="triggers"):
    return extract_state_trigger_values(
        {key: [trigger]}, source_id="a1", source_entity_id="automation.a1",
        secret=SECRET,
    )


# --------------------------------------------------------------------------
# Extractor
# --------------------------------------------------------------------------


class StateTriggerValueExtractionTests(unittest.TestCase):
    def test_literal_values_are_sorted_and_deduplicated(self):
        (record,) = extract({
            "trigger": "state", "entity_id": [ENTITY, "light.kitchen"],
            "to": ["Night", "Away", "Away"], "from": "Home",
            "not_to": ["Home"], "not_from": ["Night"],
        })
        self.assertEqual(record.entity_ids, (ENTITY,), "other domains ignored")
        self.assertEqual(record.to, ("Away", "Night"))
        self.assertEqual(record.from_, ("Home",))
        self.assertEqual(record.not_to, ("Home",))
        self.assertEqual(record.not_from, ("Night",))
        self.assertTrue(record.complete)
        self.assertEqual(record.config_path, "triggers[0]")

    def test_legacy_trigger_key_and_platform_form(self):
        (record,) = extract({"platform": "state", "entity_id": ENTITY}, key="trigger")
        self.assertEqual(record.config_path, "trigger[0]")
        self.assertIsNone(record.to, "an unconstrained side is None")
        self.assertIsNone(record.from_)
        self.assertTrue(record.complete)

    def test_unreadable_values_are_incomplete_never_absent(self):
        cases = {
            "trigger_value_template": {"to": "{{ states('sensor.x') }}"},
            "trigger_value_not_literal": {"to": 3},
            "trigger_value_not_retained": {"to": SECRET},
            "attribute_trigger": {"attribute": "options", "to": "Away"},
            "trigger_value_limit_exceeded": {"to": [f"o{i}" for i in range(65)]},
        }
        for reason, fields in cases.items():
            with self.subTest(reason=reason):
                (record,) = extract({"trigger": "state", "entity_id": ENTITY, **fields})
                self.assertFalse(record.complete)
                self.assertIn(reason, record.reason_codes)
                self.assertEqual(option_effect(record, current="Home", option="Away"),
                                 "unknown")

    def test_non_state_and_non_input_select_triggers_are_ignored(self):
        for trigger in (
            {"trigger": "event", "event_type": "x"},
            {"trigger": "state", "entity_id": "input_boolean.guest", "to": "on"},
            {"trigger": "state", "entity_id": "INPUT_SELECT.Bad"},
            "not-a-mapping",
        ):
            with self.subTest(trigger=trigger):
                self.assertEqual(extract(trigger), ())
        self.assertEqual(
            extract_state_trigger_values(None, source_id="a", source_entity_id=None,
                                         secret=SECRET),
            (),
        )

    def test_option_effect(self):
        (to_away,) = extract({"trigger": "state", "entity_id": ENTITY, "to": "Away"})
        (from_home,) = extract({"trigger": "state", "entity_id": ENTITY, "from": "Home"})
        (not_night,) = extract({"trigger": "state", "entity_id": ENTITY,
                                "not_to": ["Night"]})
        (any_change,) = extract({"trigger": "state", "entity_id": ENTITY})
        self.assertEqual(option_effect(to_away, current="Home", option="Away"), "fires")
        self.assertEqual(option_effect(to_away, current="Home", option="Night"),
                         "does_not_fire")
        self.assertEqual(option_effect(from_home, current="Home", option="Night"), "fires")
        self.assertEqual(option_effect(from_home, current="Away", option="Night"),
                         "does_not_fire")
        self.assertEqual(option_effect(not_night, current="Home", option="Night"),
                         "does_not_fire")
        self.assertEqual(option_effect(not_night, current="Home", option="Away"), "fires")
        self.assertEqual(option_effect(any_change, current="Home", option="Away"), "fires")


# --------------------------------------------------------------------------
# Reader, over a real DependencyIndex
# --------------------------------------------------------------------------


SUPPORTED_HA_VERSION = supported_home_assistant_versions()[-1]


def automation_configs():
    return {
        "mode_away": {
            "alias": "Synthetic away",
            "triggers": [{"trigger": "state", "entity_id": ENTITY, "to": "Away"}],
            "actions": [{"action": "notify.notify", "data": {"message": "away"}}],
        },
        "mode_template": {
            "alias": "Synthetic templated",
            "trigger": [{"platform": "state", "entity_id": ENTITY, "to": "{{ x }}"}],
            "action": [{"service": "notify.notify"}],
        },
        "other_select": {
            "alias": "Synthetic other select",
            "trigger": [{"platform": "state", "entity_id": "input_select.other",
                         "to": "On"}],
            "action": [{"service": "notify.notify"}],
        },
        "service_watch": {
            "alias": "Synthetic service watch",
            "trigger": [{"platform": "event", "event_type": "call_service",
                         "event_data": {"domain": "light", "service": "turn_on"}}],
            "action": [{"service": "notify.notify"}],
        },
    }


class SyntheticDependencyRest:
    def __init__(self, configs):
        self.configs = configs

    async def request(self, method, path):
        if path == "/config":
            return {"version": SUPPORTED_HA_VERSION}
        if path == "/states":
            states = [
                {"entity_id": ENTITY, "state": "Home",
                 "attributes": {"options": list(OPTIONS)}},
                {"entity_id": "input_select.other", "state": "Off",
                 "attributes": {"options": ["On", "Off"]}},
                {"entity_id": "input_boolean.guest", "state": "off", "attributes": {}},
            ]
            states += [
                {"entity_id": f"automation.{key}", "state": "on",
                 "attributes": {"id": key, "friendly_name": value["alias"]}}
                for key, value in self.configs.items()
            ]
            return states
        prefix = "/config/automation/config/"
        if path.startswith(prefix):
            return self.configs[path.removeprefix(prefix)]
        raise AssertionError(path)


class SyntheticDependencyWebSocket:
    async def command(self, payload):
        if payload in ({"type": "config/entity_registry/list"},
                       {"type": "config/label_registry/list"}):
            return []
        raise AssertionError(payload)


class InputSelectDependencyReaderTests(unittest.IsolatedAsyncioTestCase):
    async def assess(self, configs=None, entity_id=ENTITY, builder=True):
        index = DependencyIndex(DirectHaDependencyProvider(
            SyntheticDependencyRest(configs or automation_configs()),
            SyntheticDependencyWebSocket(), concurrency=2,
        ))
        service = (
            HelperDependencyRiskService(
                index, binding_builder=build_input_select_dependency_binding
            )
            if builder else HelperDependencyRiskService(index)
        )
        return (await service.assess(entity_id, refresh=True))["binding"]

    async def test_call_service_filter_is_reprojected_for_input_select_only(self):
        binding = await self.assess()
        self.assertEqual(binding["model"], INPUT_SELECT_DEPENDENCY_MODEL)
        self.assertEqual(binding["base_model"], HELPER_DEPENDENCY_RISK_MODEL)
        self.assertEqual(binding["reprojected_call_service_obligations"], 1)
        self.assertIn(REPROJECTED_REASON, binding["consequence_uncertainty_reason_codes"])
        self.assertIn("automation.service_watch", binding["relevant_downstream_object_ids"])
        self.assertGreaterEqual(binding["opaque_obligation_count"], 1)
        self.assertTrue(binding["execution_eligible"], "opacity elevates, never refuses")
        # The input_boolean v13 binding over the same index is unchanged.
        boolean = await self.assess(entity_id="input_boolean.guest", builder=False)
        self.assertEqual(boolean["model"], HELPER_DEPENDENCY_RISK_MODEL)
        self.assertEqual(boolean["opaque_obligation_count"], 0)
        self.assertNotIn(REPROJECTED_REASON,
                         boolean["consequence_uncertainty_reason_codes"])
        self.assertNotIn("option_trigger_records", boolean)

    async def test_only_the_exact_target_trigger_records_are_bound(self):
        binding = await self.assess()
        self.assertEqual(
            [(r["automation_id"], r["complete"]) for r in binding["option_trigger_records"]],
            [("automation.mode_away", True), ("automation.mode_template", False)],
        )
        disclosure = option_trigger_disclosure(binding, current="Home", option="Away")
        self.assertEqual(
            [(t["automation_id"], t["effect"]) for t in disclosure["option_triggers"]],
            [("automation.mode_away", "fires"), ("automation.mode_template", "unknown")],
        )
        self.assertEqual(disclosure["automations_without_literal_option_trigger"],
                         ["automation.service_watch"])
        self.assertTrue(disclosure["per_option_effect_is_not_risk_evidence"])
        night = option_trigger_disclosure(binding, current="Home", option="Night")
        self.assertEqual(night["option_triggers"][0]["effect"], "does_not_fire")

    async def test_a_trigger_value_edit_changes_the_fingerprint(self):
        first = await self.assess()
        configs = automation_configs()
        configs["mode_away"]["triggers"][0]["to"] = "Night"
        edited = await self.assess(configs)
        self.assertNotEqual(first["evidence_fingerprint"], edited["evidence_fingerprint"])
        self.assertEqual(first["evidence_fingerprint"],
                         (await self.assess())["evidence_fingerprint"], "deterministic")

    def test_failed_binding_keeps_the_input_select_model(self):
        binding = failed_input_select_binding(ENTITY)
        self.assertEqual(binding["model"], INPUT_SELECT_DEPENDENCY_MODEL)
        self.assertFalse(binding["execution_eligible"])
        self.assertEqual(binding["option_trigger_records"], [])

    def test_target_must_be_an_input_select(self):
        with self.assertRaises(ValueError):
            build_input_select_dependency_binding(
                None, entity_id="input_boolean.guest", index_metadata={}
            )


# --------------------------------------------------------------------------
# Gateway
# --------------------------------------------------------------------------


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rest = SyntheticInputSelectRest()
        self.ws = SyntheticInputSelectWebSocket(self.rest)
        self.rest.add(ENTITY, "Home", OPTIONS)
        self.gateway = contract.InputSelectGateway(self.rest, self.ws)
        self.before = []

    async def before_dispatch(self):
        self.before.append(True)

    async def test_exact_payload_only(self):
        result = await self.gateway.select_option(ENTITY, "Away",
                                                  before_dispatch=self.before_dispatch)
        self.assertTrue(result.provider_response_received)
        self.assertEqual(self.ws.commands, [{
            "type": "call_service", "domain": "input_select",
            "service": "select_option", "service_data": {"option": "Away"},
            "target": {"entity_id": ENTITY},
        }])
        self.assertEqual(self.before, [True])

    async def test_invalid_entity_or_option_is_refused_before_io(self):
        for entity, option in (
            ("input_boolean.guest", "Away"), ("input_select.Bad", "Away"),
            (ENTITY, ""), (ENTITY, "x" * 256), (ENTITY, "Away\n"), (ENTITY, 1),
        ):
            with self.subTest(entity=entity, option=option):
                with self.assertRaises(ValueError):
                    await self.gateway.select_option(
                        entity, option, before_dispatch=self.before_dispatch
                    )
        with self.assertRaises(ValueError):
            await self.gateway.read_state("input_boolean.guest")
        self.assertEqual(self.before, [])
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(self.rest.requests, [])

    async def test_read_state_shape_and_categories(self):
        state = await self.gateway.read_state(ENTITY)
        self.assertEqual(set(state), {"entity_id", "state", "options", "last_changed"})
        self.assertEqual(state["options"], OPTIONS)
        cases = {
            "entity_unavailable": ("unavailable", []),
            "invalid_provider_response": ("Home", ["Home", "Home"]),
        }
        for category, (value, options) in cases.items():
            with self.subTest(category=category):
                self.rest.add(ENTITY, value, options)
                with self.assertRaises(contract.InputSelectGatewayError) as raised:
                    await self.gateway.read_state(ENTITY)
                self.assertEqual(raised.exception.category, category)
        with self.assertRaises(contract.InputSelectGatewayError) as raised:
            await self.gateway.read_state("input_select.missing")
        self.assertEqual(raised.exception.category, "entity_not_found")

    async def test_transport_errors_map_to_closed_categories(self):
        class Raising:
            def __init__(self, exc):
                self.exc = exc

            async def request(self, method, path):
                raise self.exc

        for exc, category in (
            (AuthorizationError(), "permission_failure"),
            (HomeAssistantTimeoutError(), "provider_timeout"),
            (HomeAssistantUnavailableError(), "provider_unavailable"),
            (HomeAssistantApiError(details={"status": 403}), "permission_failure"),
            (HomeAssistantApiError(details={"status": 500}), "provider_error"),
        ):
            with self.subTest(category=category):
                gateway = contract.InputSelectGateway(Raising(exc), self.ws)
                with self.assertRaises(contract.InputSelectGatewayError) as raised:
                    await gateway.read_state(ENTITY)
                self.assertEqual(raised.exception.category, category)

    async def test_before_dispatch_failure_never_sends(self):
        async def refuse():
            raise RuntimeError("synthetic intent persistence failure")

        with self.assertRaises(contract.InputSelectGatewayError) as raised:
            await self.gateway.select_option(ENTITY, "Away", before_dispatch=refuse)
        self.assertTrue(raised.exception.call_not_sent)
        self.assertFalse(raised.exception.dispatched)
        self.assertEqual(self.ws.commands, [])

    async def test_errors_after_the_send_are_dispatched(self):
        for mode, category in (
            ("rejected", "provider_rejected"),
            ("lost_applied", "dispatch_indeterminate"),
            ("lost_not_applied", "dispatch_indeterminate"),
        ):
            with self.subTest(mode=mode):
                self.ws.mode = mode
                with self.assertRaises(contract.InputSelectGatewayError) as raised:
                    await self.gateway.select_option(
                        ENTITY, "Away", before_dispatch=self.before_dispatch
                    )
                self.assertEqual(raised.exception.category, category)
                self.assertTrue(raised.exception.dispatched)
                self.assertFalse(raised.exception.call_not_sent)


# --------------------------------------------------------------------------
# Service admission
# --------------------------------------------------------------------------


class UnadmittedServiceTests(InputSelectServiceTestCase):
    core_admits_input_select = False

    async def test_every_entry_refuses_before_validation_and_any_read(self):
        for arguments in (
            {"entity_id": ENTITY, "option": "Away", "reason": "r"},
            {"entity_id": "input_boolean.guest", "option": "", "extra": 1},
            None,
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_input_select_option_plan(arguments)
                self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
                self.assertEqual(raised.exception.details, PENDING_DETAILS)
        self.assertEqual(self.ha_calls(), 0)
        self.assertEqual(self.dependency.calls, [])
        self.assertEqual(self.repository.list(), [])

    async def test_internal_builder_refuses_without_admission(self):
        with self.assertRaises(GovernanceError) as raised:
            self.service._create_input_select_option_plan(
                planning.Request(ENTITY, "Away", "r", 120),
                baseline={}, provider_evidence={}, dependency_evidence={},
                caller_context=None, _before_persist=lambda: None,
            )
        self.assertEqual(raised.exception.details, PENDING_DETAILS)
        self.assertEqual(self.repository.list(), [])

    async def test_capability_reports_pending_admission(self):
        status = self.service._input_select_provider_health_snapshot(None)
        self.assertEqual(status["core_capability"], contract.PROFILE)


class AdmittedServiceBoundaryTests(InputSelectServiceTestCase):
    async def test_internal_builder_requires_a_persistence_lease(self):
        with self.assertRaises(GovernanceError) as raised:
            self.service._create_input_select_option_plan(
                planning.Request(ENTITY, "Away", "r", 120),
                baseline={"entity_id": ENTITY, "state": "Home",
                          "options": OPTIONS, "last_changed": "x"},
                provider_evidence=contract.provider_evidence(),
                dependency_evidence=await self.dependency(ENTITY),
                caller_context=None,
            )
        self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        self.assertEqual(self.repository.list(), [])

    async def test_admission_withdrawn_during_planning_stores_nothing(self):
        original = self.dependency.__call__

        async def withdraw_then_read(entity_id, **kwargs):
            self.core.withdraw()
            return await original(entity_id, **kwargs)

        self.service.input_select_dependency_risk_reader = withdraw_then_read
        with self.assertRaises(GovernanceError) as raised:
            await self.create_select("Away")
        self.assertIn(raised.exception.code, {ErrorCode.PROVIDER_UNAVAILABLE,
                                              ErrorCode.INTERNAL_INVARIANT_VIOLATION})
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.sent(), [])

    async def test_planning_lease_is_bound_to_the_exact_target(self):
        await self.create_select("Away")
        leases = [lease for lease in self.core.planning_leases
                  if contract.PROFILE in lease[0]]
        self.assertEqual(leases, [(
            ("core.dependency_helper_planning", contract.PROFILE),
            {"target_type": "input_select", "target_id": ENTITY},
        )])

    async def test_legacy_apply_route_never_sends(self):
        plan = await self.approved_select("Away")
        self.service.f3_runtime = None
        with self.assertRaises(GovernanceError):
            await self.apply(plan)
        self.assertEqual(self.sent(), [])

    async def test_input_boolean_route_cannot_reach_an_input_select(self):
        from ha_mcp_engineering.governance.helper_state import (
            validate_input_boolean_entity_id,
        )

        with self.assertRaises(ValueError):
            validate_input_boolean_entity_id(ENTITY)
        with self.assertRaises(GovernanceError):
            await self.service.create_helper_state_plan(
                entity_id=ENTITY, desired_state="on"
            )
        self.assertEqual(self.ha_calls(), 0)


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


class PlanningTests(InputSelectServiceTestCase):
    async def assert_refused(self, arguments, reason, *, code=None, field=None):
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_input_select_option_plan(arguments)
        details = raised.exception.details
        self.assertEqual(details["reason_code"], reason, details)
        self.assertIs(details["plan_created"], False)
        if code is not None:
            self.assertEqual(raised.exception.code, code)
        if field is not None:
            self.assertEqual(details.get("field"), field)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.sent(), [])
        return raised.exception

    async def test_input_refusals_read_nothing(self):
        valid = {"entity_id": ENTITY, "option": "Away", "reason": "r"}
        await self.assert_refused(None, "input_select_option_invalid_request")
        await self.assert_refused({**valid, "service": "set_options"},
                                  planning.FIELD_NOT_SUPPORTED)
        for field, value in (
            ("entity_id", "input_boolean.guest"), ("entity_id", "input_select.Bad"),
            ("option", ""), ("option", "x" * 256), ("option", "Away\x00"),
            ("option", 3), ("reason", "  "), ("expiration_minutes", 4),
            ("expiration_minutes", 1441), ("expiration_minutes", True),
        ):
            with self.subTest(field=field, value=value):
                await self.assert_refused({**valid, field: value},
                                          planning.INVALID_VALUE, field=field)
        missing = dict(valid)
        del missing["option"]
        await self.assert_refused(missing, planning.INVALID_VALUE, field="option")
        self.assertEqual(self.ha_calls(), 0)
        self.assertEqual(self.dependency.calls, [])

    async def test_option_comparison_is_exact(self):
        for option in ("away", " Away", "Away "):
            with self.subTest(option=option):
                await self.assert_refused(
                    {"entity_id": ENTITY, "option": option, "reason": "r"},
                    "option_not_available",
                )
        self.assertEqual(self.dependency.calls, [], "no dependency read on refusal")

    async def test_read_failures_map_to_closed_codes(self):
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_input_select_option_plan(
                {"entity_id": "input_select.missing", "option": "Away", "reason": "r"}
            )
        self.assertEqual(raised.exception.code, ErrorCode.ENTITY_NOT_FOUND)
        self.assertEqual(raised.exception.details["reason"], "input_select_entity_not_found")
        self.select_rest.add(ENTITY, "unavailable", [])
        with self.assertRaises(GovernanceError) as raised:
            await self.create_select("Away")
        self.assertEqual(raised.exception.code, ErrorCode.HA_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason"], "input_select_entity_unavailable")
        self.assertEqual(self.repository.list(), [])

    async def test_already_selected_is_no_change_with_no_plan(self):
        result = await self.create_select("Home")
        self.assertEqual(result["status"], "no_change")
        self.assertEqual(result["outcome"], "already_desired")
        self.assertIs(result["plan_created"], False)
        self.assertIs(result["verified"], True)
        self.assertIs(result["provider_dispatch_occurred"], False)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.dependency.calls, [])
        self.assertEqual(self.sent(), [])

    async def test_plan_discloses_the_option_change_and_option_triggers(self):
        self.dependency.records = [
            trigger_record("automation.away_lights", to=["Away"]),
            trigger_record("automation.night_locks", to=["Night"]),
            trigger_record("automation.templated", complete=False),
        ]
        created = await self.create_select("Away")
        self.assertEqual(created["status"], "awaiting_approval")
        self.assertFalse(created["provider_dispatch_occurred"])
        plan = created["plan"]
        dry_run = plan["dry_run_results"]
        self.assertEqual(dry_run["from_option"], "Home")
        self.assertEqual(dry_run["to_option"], "Away")
        self.assertEqual(dry_run["options"], OPTIONS)
        self.assertEqual(dry_run["rollback_route"],
                         "separately_approved_return_to_prior_option")
        self.assertNotIn("rollback_of_plan_id", dry_run)
        self.assertEqual(
            [(t["automation_id"], t["effect"])
             for t in dry_run["option_triggers"]["option_triggers"]],
            [("automation.away_lights", "fires"),
             ("automation.night_locks", "does_not_fire"),
             ("automation.templated", "unknown")],
        )
        self.assertEqual(dry_run["dependency_risk"]["model"], INPUT_SELECT_DEPENDENCY_MODEL)
        self.assertEqual(self.dependency.calls, [False], "planning read is not fenced")
        self.assertEqual(self.sent(), [])

    async def test_standard_policy_without_consequential_dependency(self):
        plan = (await self.create_select("Away"))["plan"]
        self.assertEqual(plan["policy_decision"]["risk_delta"], "low")
        self.assertEqual(plan["policy_decision"]["physical_consequence"], "none")
        self.assertIn("exact_input_select_option_standard_policy",
                      plan["policy_decision"]["reason_codes"])

    async def test_consequential_or_opaque_dependency_elevates(self):
        for name, setup in (
            ("direct", lambda d: (setattr(d, "automation_ids", ["automation.opens_cover"]),
                                  setattr(d, "consequence", "direct"))),
            ("opaque", lambda d: (setattr(d, "automation_ids", ["automation.benign"]),
                                  setattr(d, "opaque", True))),
            ("incomplete", lambda d: setattr(d, "complete", False)),
        ):
            with self.subTest(case=name):
                self.dependency.automation_ids = []
                self.dependency.consequence = "none"
                self.dependency.opaque = False
                self.dependency.complete = True
                setup(self.dependency)
                plan = (await self.create_select("Away"))["plan"]
                self.assertEqual(plan["risk"]["level"], "high")
                self.assertEqual(plan["policy_decision"]["policy_class"], "elevated_admin")
                self.assertTrue(plan["approval_actionable"])


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


class ExecutionTests(InputSelectServiceTestCase):
    async def test_apply_sends_once_and_verifies_by_readback(self):
        plan = await self.approved_select("Away")
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertTrue(result["provider_dispatch_occurred"])
        self.assertEqual(self.current(), "Away")
        self.assertEqual(self.sent(), [contract.select_option_payload(ENTITY, "Away")])
        codes = self.child_codes(result["task_id"])
        self.assertIn("exact_readback_verified", codes)
        self.assertIn("input_select_option_verified", codes)
        self.assertEqual(self.dependency.calls, [False, True], "preflight read is fenced")
        self.assertEqual(self.select_rest.authority_refusals, [])
        self.assertEqual(self.select_ws.authority_refusals, [])
        # Planning, preflight, pre-dispatch drift read, readback.
        self.assertEqual(self.select_rest.reads(), 4)

    async def test_option_list_uses_the_exclusive_target_and_shared_reload_locks(self):
        plan = await self.approved_select("Away")
        stored = self.service._load(plan["plan_id"])
        modes = self.runtime._legacy_plan_lock_modes(stored, ())
        self.assertEqual(modes, {
            resource_lock_key("input_select", ENTITY): LockMode.EXCLUSIVE,
            "reload:input_select": LockMode.SHARED,
            "home_assistant:core": LockMode.SHARED,
        })

    async def assert_refused_before_send(self, result, code):
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn(code, self.child_codes(result["task_id"]))
        for record in self.child_records(result["task_id"]):
            self.assertIsNone(record.dispatch_intent)
            self.assertEqual(record.dispatch_count, 0)
        self.assertEqual(self.sent(), [])

    async def test_drift_before_apply_is_refused_with_no_send(self):
        cases = (
            ("state", {"state": "Night"}, STALE),
            ("options_reordered", {"options": ["Away", "Home", "Night"]}, STALE),
            ("option_removed", {"options": ["Home", "Night"]}, "option_not_available"),
        )
        for name, change, code in cases:
            with self.subTest(case=name):
                self.select_rest.add(ENTITY, "Home", OPTIONS)
                plan = await self.approved_select("Away")
                self.select_rest.set_external(ENTITY, **change)
                await self.assert_refused_before_send(await self.apply(plan), code)

    async def test_a_restamped_but_equal_state_is_stale(self):
        plan = await self.approved_select("Away")
        self.select_rest.set_external(ENTITY, state="Night")
        self.select_rest.set_external(ENTITY, state="Home")
        await self.assert_refused_before_send(await self.apply(plan), STALE)

    async def test_drift_in_the_predispatch_read_is_refused_with_no_intent(self):
        plan = await self.approved_select("Away")
        reads = self.select_rest.reads()
        self.select_rest.on_read[reads + 2] = (
            lambda: self.select_rest.set_external(ENTITY, state="Night")
        )
        await self.assert_refused_before_send(await self.apply(plan), STALE)
        self.assertEqual(self.select_rest.authority_refusals, [],
                         "the drift read ran inside a Core window")

    async def test_admission_withdrawn_after_approval_refuses_at_entry(self):
        plan = await self.approved_select("Away")
        reads = self.select_rest.reads()
        self.core.withdraw()
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(plan)
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"], PENDING)
        self.assertIs(raised.exception.details["provider_dispatch_occurred"], False)
        self.assertEqual(self.select_rest.reads(), reads)
        self.assertEqual(self.sent(), [])

    async def test_admission_withdrawn_during_the_predispatch_read_never_sends(self):
        plan = await self.approved_select("Away")
        reads = self.select_rest.reads()
        self.select_rest.on_read[reads + 2] = self.core.withdraw
        try:
            result = await self.apply(plan)
        except GovernanceError:
            result = None
        if result is not None:
            self.assertNotEqual(result["task_state"], "succeeded_verified", result)
            self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.current(), "Home")

    async def test_already_desired_at_preflight_verifies_without_a_send(self):
        plan = await self.approved_select("Away")
        self.select_rest.set_external(ENTITY, state="Away")
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.sent(), [])

    async def test_lost_response_that_applied_is_verified(self):
        plan = await self.approved_select("Away")
        self.select_ws.mode = "lost_applied"
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.sent()), 1)

    async def test_responses_that_did_not_apply_fail_without_a_resend(self):
        for mode in ("lost_not_applied", "rejected", "ignored"):
            with self.subTest(mode=mode):
                self.select_rest.add(ENTITY, "Home", OPTIONS)
                self.select_ws.commands.clear()
                plan = await self.approved_select("Away")
                self.select_ws.mode = mode
                result = await self.apply(plan)
                self.select_ws.mode = "success"
                self.assertNotEqual(result["task_state"], "succeeded_verified", result)
                self.assertTrue(result["provider_dispatch_occurred"])
                self.assertEqual(len(self.sent()), 1, "never redispatched")
                self.assertEqual(self.current(), "Home")
                self.assertNotIn("exact_readback_verified",
                                 self.child_codes(result["task_id"]))

    async def test_rejection_that_applied_is_decided_by_readback(self):
        """Regression: a Core error after the send was a confirmed failure.

        Design §4.2: it is never proof of failure; the readback decides.
        """

        plan = await self.approved_select("Away")
        original = self.select_ws.command

        async def apply_then_reject(payload):
            await original(payload)
            raise HomeAssistantApiError(details={"provider_response_received": True})

        self.select_ws.command = apply_then_reject
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.sent()), 1)
        codes = self.child_codes(result["task_id"])
        self.assertIn("provider_rejection_readback_decides", codes)
        self.assertNotIn("provider_rejection_confirmed", codes)

    async def test_dependency_fingerprint_drift_refuses_before_send(self):
        plan = await self.approved_select("Away")
        self.dependency.records = [trigger_record("automation.new", to=["Away"])]
        await self.assert_refused_before_send(await self.apply(plan),
                                              "dependency_risk_drift")

    async def test_ineligible_dependency_evidence_refuses_before_send(self):
        plan = await self.approved_select("Away")
        original = self.dependency.__call__

        async def failed(entity_id, **kwargs):
            await original(entity_id, **kwargs)
            return {"binding": failed_input_select_binding(entity_id)}

        self.service.input_select_dependency_risk_reader = failed
        await self.restart_runtime()
        await self.assert_refused_before_send(await self.apply(plan),
                                              "dependency_coverage_failure")


# --------------------------------------------------------------------------
# Rollback (OD-12: separately approved)
# --------------------------------------------------------------------------


class RollbackTests(InputSelectServiceTestCase):
    async def forward(self, option="Away"):
        plan = await self.approved_select(option)
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        return plan

    async def assert_rollback_refused(self, plan, reason):
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(plan["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(raised.exception.details.get("reason_code"), reason,
                         raised.exception.details)
        return raised.exception

    async def test_rollback_is_a_separate_plan_that_restores_the_prior_option(self):
        plan = await self.forward("Away")
        rollback = await self.service.rollback_change(plan["plan_id"])
        self.assertEqual(rollback["status"], "rollback_plan_created", rollback)
        self.assertTrue(rollback["approval_required"])
        self.assertFalse(rollback["provider_dispatch_occurred"])
        self.assertEqual(len(self.sent()), 1, "rollback never runs itself")
        reverse = rollback["plan"]
        self.assertEqual(reverse["dry_run_results"]["to_option"], "Home")
        self.assertEqual(reverse["dry_run_results"]["rollback_of_plan_id"], plan["plan_id"])
        await self.approve(reverse)
        result = await self.apply(reverse)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.current(), "Home")
        self.assertEqual(self.sent()[-1], contract.select_option_payload(ENTITY, "Home"))

    async def test_rollback_requires_a_verified_source(self):
        plan = await self.approved_select("Away")
        await self.assert_rollback_refused(plan, "input_select_rollback_source_not_verified")
        self.assertEqual(self.sent(), [])

    async def test_rollback_refused_after_later_drift(self):
        plan = await self.forward("Away")
        self.select_rest.set_external(ENTITY, state="Night")
        await self.assert_rollback_refused(plan, "rollback_baseline_drift")
        self.assertEqual(len(self.sent()), 1)

    async def test_rollback_refused_when_the_prior_option_was_removed(self):
        plan = await self.forward("Away")
        self.select_rest.set_external(ENTITY, options=["Away", "Night"])
        await self.assert_rollback_refused(plan, "option_not_available")

    async def test_rollback_refused_without_admission(self):
        plan = await self.forward("Away")
        reads = self.select_rest.reads()
        self.core.withdraw()
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(plan["plan_id"])
        self.assertEqual(raised.exception.details.get("reason_code"), PENDING)
        self.assertEqual(self.select_rest.reads(), reads)
        self.assertEqual(len(self.sent()), 1)


# --------------------------------------------------------------------------
# Reachability
# --------------------------------------------------------------------------


def _string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


class ReachabilityTests(unittest.TestCase):
    def test_select_option_gateway_marks_the_send_boundary(self):
        tree = ast.parse((PACKAGE / "governance" / "input_select_option.py")
                         .read_text(encoding="utf-8"))
        (select,) = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "select_option"
        ]
        calls = [
            node.func.id for node in ast.walk(select)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertIn("mark_provider_send", calls)
        self.assertLess(
            [n.lineno for n in ast.walk(select) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "mark_provider_send"][0],
            [n.lineno for n in ast.walk(select) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", None) == "command"][0],
        )

    def test_never_sent_services_appear_in_no_send_path(self):
        for service in contract.NEVER_SENT_SERVICES:
            with self.subTest(service=service):
                self.assertNotEqual(
                    contract.select_option_payload(ENTITY, "Away")["service"], service
                )
        self.assertEqual(contract.provider_evidence()["argument_constraints"]["services"],
                         ["select_option"])

    def test_the_service_names_are_confined(self):
        """Only the gateway (and Core's profile table) names a select service.

        The dependency extractor also names ``select_option``, but only as a
        read-only call_service event-filter selector for lock projection; it
        has no send path (it imports no transport).
        """

        gateway = PACKAGE / "governance" / "input_select_option.py"
        extraction = PACKAGE / "dependency" / "extraction.py"
        expected = {"select_option": {gateway, extraction,
                                      PACKAGE / "ha_core_readmission" / "profiles.py"}}
        extraction_source = extraction.read_text(encoding="utf-8")
        for transport in ("clients.", "websocket", "rest_path", "mark_provider_send"):
            self.assertNotIn(transport, extraction_source)
        # ``reload`` is a generic name; the gateway's closed payload builder
        # (``test_exact_payload_only``) proves it is never sent here.
        never = [n for n in contract.NEVER_SENT_SERVICES if n != "reload"]
        for name in ("select_option", *never):
            with self.subTest(service=name):
                found = {
                    path for path in PACKAGE.rglob("*.py")
                    if name in _string_constants(path)
                }
                self.assertEqual(found, expected.get(name, {gateway}),
                                 sorted(str(p) for p in found))


if __name__ == "__main__":
    unittest.main()
