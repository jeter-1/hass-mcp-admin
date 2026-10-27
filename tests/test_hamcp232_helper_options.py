"""HAMCP-232 governed helper options: admission, planning, execution, rollback.

Every path drives the shipped service, the F3 executor and runtime, and the
real ``HelperOptionsResourceGateway`` against the synthetic options-flow
server in ``tests/hamcp232_helper_options_fixtures.py``, which refuses any
call outside a Core-authority window exactly as the real client does and
fails the test on any request outside the reviewed routes.

``core.config_entry_helper_options_operation`` is runtime-only: no signed
registry references it, so it is inactive in every shipped build.  The Core
double admits it here only where a test says so.
"""

from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.clients.rest import ExpectedHttpStatus  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
)
from ha_mcp_engineering.f3_configuration.helper_options import (  # noqa: E402
    CORE_ADMISSION_UNAVAILABLE,
    HelperOptionsAdapter,
)
from ha_mcp_engineering.governance import helper_options as contract  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    helper_options_planning as planning,
)

from tests.hamcp232_helper_options_fixtures import (  # noqa: E402
    FILTER_LOWPASS,
    STEP_PREFIX,
    HelperOptionsServiceTestCase,
    flow_form,
)


PENDING = "pending_signed_registry_admission"
STALE = "stale_target_state"
ORPHANED = "options_flow_orphaned"
PENDING_DETAILS = {
    "reason": PENDING,
    "reason_code": PENDING,
    "resource_type": contract.RESOURCE_TYPE,
    "core_capability": contract.PROFILE,
    "plan_created": False,
    "provider_dispatch_occurred": False,
}
FILTERED = "sensor.filtered_power"
TIME_CONSTANT_20 = {"entity_id": FILTERED, "set": {"time_constant": 20}}


class TimestampBoundTests(unittest.TestCase):
    """Regression: a real ``modified_at`` (~1.7e9 s) refused every entry."""

    def test_real_epoch_seconds_are_accepted(self):
        for value in (0, 0.0, 1_700_000_000.0, 1_761_436_800.123, 4_102_444_800):
            with self.subTest(value=value):
                self.assertTrue(contract._timestamp(value))

    def test_non_timestamps_are_refused(self):
        for value in (-1.0, True, False, float("inf"), float("nan"), "1.7e9",
                      None, contract.MAX_TIMESTAMP * 2):
            with self.subTest(value=value):
                self.assertFalse(contract._timestamp(value))

    def test_generic_number_bound_is_unchanged(self):
        # The defect: ``_number`` caps at 1e9, below every current timestamp.
        self.assertFalse(contract._number(1_700_000_000.0))


class PinnedFieldContractTests(unittest.TestCase):
    """OD-3: pinned fields are refused by the contract itself, not only by the
    request vocabulary (which already excludes every pinned name)."""

    def test_every_pinned_field_is_refused_at_coercion(self):
        pinned = [
            (domain, step, spec)
            for (domain, step), fields in contract.STEP_CATALOG.items()
            for spec in fields
            if spec.pinned
        ]
        self.assertTrue(pinned)
        for domain, step, spec in pinned:
            with self.subTest(domain=domain, step=step, field=spec.name):
                self.assertNotIn(spec.name, contract.EDITABLE_FIELD_NAMES)
                with self.assertRaises(contract.HelperOptionsContractError) as raised:
                    contract.coerce_request_value(spec, "sensor.other")
                self.assertEqual(raised.exception.category, "options_field_pinned")


def valid_intent(**set_values) -> dict:
    proposal = contract.build_proposal(
        "filter", "lowpass", FILTER_LOWPASS, set_values or {"time_constant": 20}, ()
    )
    return contract.build_intent(
        entity_ids=[FILTERED],
        entry_id="filterentry01",
        domain="filter",
        step_id="lowpass",
        modified_at=1_761_436_800.0,
        baseline=deepcopy(FILTER_LOWPASS),
        proposal=proposal,
        consumer_evidence_fingerprint="0" * 64,
        dependency_reasons=[],
    )


def retarget(intent: dict, proposed: dict) -> dict:
    """Replace ``proposed`` and ``submit`` consistently so only the rule under test fires."""

    intent = deepcopy(intent)
    body = intent[contract.INTENT_KEY]
    body["proposed"] = deepcopy(proposed)
    body["submit"] = deepcopy(proposed)
    body["proposed_fingerprint"] = contract.fingerprint(contract.INTENT_MODEL, proposed)
    return intent


class IntentContractTests(unittest.TestCase):
    """The persisted intent is re-validated exactly at apply; a forged intent is refused."""

    def test_the_built_intent_is_valid(self):
        self.assertEqual(contract.intent_errors("filterentry01", valid_intent()), [])

    def test_an_intent_changing_a_pinned_field_is_refused(self):
        for name, value in (("entity_id", "sensor.other_power"), ("filter", "outlier")):
            with self.subTest(field=name):
                proposed = dict(valid_intent()[contract.INTENT_KEY]["proposed"], **{name: value})
                errors = contract.intent_errors("filterentry01", retarget(valid_intent(), proposed))
                self.assertIn("options intent changes a pinned field", errors)

    def test_a_no_op_intent_is_refused(self):
        errors = contract.intent_errors("filterentry01", retarget(valid_intent(), FILTER_LOWPASS))
        self.assertEqual(errors, ["options intent is a no-op"])


class RequestVocabularyTests(unittest.TestCase):
    def test_one_to_eight_targets(self):
        changes = [{"entity_id": f"sensor.filtered_{index}", "set": {"time_constant": 20}}
                   for index in range(planning.MAX_CHANGES + 1)]
        self.assertEqual(planning.MAX_CHANGES, 8)  # OD-5
        request = planning.validate_request({"changes": changes[:-1], "reason": "Synthetic"})
        self.assertEqual(len(request.changes), planning.MAX_CHANGES)
        with self.assertRaises(planning.Refusal) as raised:
            planning.validate_request({"changes": changes, "reason": "Synthetic"})
        self.assertEqual(raised.exception.reason_code, "helper_options_too_many_targets")

    def test_template_types_are_the_closed_catalog(self):
        # OD-2: template sensor and binary_sensor only; the declared vocabulary
        # and the compiled step catalog must agree exactly.
        self.assertEqual(contract.TEMPLATE_TYPES, ("binary_sensor", "sensor"))
        self.assertEqual(
            {step for domain, step in contract.STEP_CATALOG if domain == "template"},
            set(contract.TEMPLATE_TYPES),
        )


class StubRest:
    def __init__(self, failure: Exception | None = None):
        self.failure = failure
        self.requests: list[tuple] = []

    async def request(self, method, path, body=None, **kwargs):
        self.requests.append((method, path))
        if self.failure is not None:
            raise self.failure
        return {"type": "create_entry"}


class GatewayContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_flow_ids_never_reach_core(self):
        rest = StubRest()
        gateway = contract.HelperOptionsGateway(rest, None)
        for flow_id in ("", "0" * 31, "0" * 33, "A" * 32, "g" * 32, "../" + "0" * 29,
                        "0" * 16 + "?x" + "0" * 14, None):
            with self.subTest(flow_id=flow_id):
                for call in (gateway.submit(flow_id, {}), gateway.abort_flow(flow_id)):
                    with self.assertRaises(contract.HelperOptionsContractError) as raised:
                        await call
                    self.assertEqual(raised.exception.category, "options_flow_id_malformed")
        self.assertEqual(rest.requests, [])

    async def test_a_raised_client_error_is_a_confirmed_rejection(self):
        # 400 and 404 arrive as responses; any other 4xx is raised by the client.
        for status in (403, 405, 409, 422):
            with self.subTest(status=status):
                rest = StubRest(HomeAssistantApiError(details={"status": status}))
                outcome = await contract.HelperOptionsGateway(rest, None).submit("0" * 32, {})
                self.assertEqual(
                    outcome, contract.SubmitOutcome(False, contract.SUBMIT_REJECTED_REASON, status)
                )
                self.assertEqual(len(rest.requests), 1)

    async def test_a_server_error_leaves_the_outcome_to_readback(self):
        rest = StubRest(HomeAssistantApiError(details={"status": 503}))
        outcome = await contract.HelperOptionsGateway(rest, None).submit("0" * 32, {})
        self.assertEqual(outcome, contract.SubmitOutcome(None, contract.SUBMIT_UNCERTAIN, 503))


class ServiceAdmissionTests(HelperOptionsServiceTestCase):
    """Unadmitted planning reads nothing and stores nothing."""

    core_admits_options = False

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add_filter()
        self.options_rest.requests.clear()
        self.ws.commands.clear()

    async def test_refuses_before_validation_and_any_read(self):
        for arguments in (
            {"changes": [TIME_CONSTANT_20], "reason": "Synthetic tuning"},
            {"changes": [], "reason": ""},
            {"changes": [{"entity_id": "light.x", "set": {"state": "x"}}]},
            {"unexpected": 1},
            None,
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_helper_options_plan(arguments)
                self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
                self.assertEqual(raised.exception.details, PENDING_DETAILS)
                self.assertEqual(self.ha_calls(), 0)
                self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.options_rest.flows, {})

    async def test_internal_builder_refuses_without_admission(self):
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_helper_options_plan(
                planning.Preparation(request=None, items=[]),
                _before_persist=lambda: None,
            )
        self.assertEqual(raised.exception.details, PENDING_DETAILS)
        self.assertEqual(self.repository.list(), [])

    async def test_generic_builder_refuses_a_raw_options_operation_first(self):
        # The lowest builder refuses the dormant resource type before it
        # validates anything, so an unadmitted raw route learns nothing more.
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_configuration_plan(
                title="t",
                description="d",
                operations=[{
                    "operation_id": "options_1",
                    "resource_type": contract.RESOURCE_TYPE,
                    "action": contract.ACTION,
                    "target_id": "filterentry01",
                    "proposed_config": {},
                }],
                _before_persist=lambda: None,
            )
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details, PENDING_DETAILS)
        self.assertEqual(self.ha_calls(), 0)
        self.assertEqual(self.repository.list(), [])


class AbortSettleTests(unittest.IsolatedAsyncioTestCase):
    """Review H2: the gateway's flow abort is truthful, bounded and survives
    the caller's cancellation."""

    async def test_abort_answers(self):
        received_404 = HomeAssistantApiError(
            details={"status": 404, "provider_response_received": True}
        )
        cases = {
            "aborted": (None, True),
            "already_gone": (received_404, True),
            "unanswered_404": (HomeAssistantApiError(details={"status": 404}), False),
            "server_error": (HomeAssistantApiError(
                details={"status": 500, "provider_response_received": True}), False),
            "timeout": (HomeAssistantTimeoutError(details={}), False),
        }
        for name, (failure, expected) in cases.items():
            with self.subTest(case=name):
                rest = StubRest(failure)
                gateway = contract.HelperOptionsGateway(rest, None)
                self.assertIs(await gateway.abort_flow("0" * 32), expected)
                self.assertEqual([method for method, _ in rest.requests], ["DELETE"])
        with self.assertRaises(AuthorizationError):
            await contract.HelperOptionsGateway(
                StubRest(AuthorizationError()), None
            ).abort_flow("0" * 32)

    async def test_a_cancelled_caller_still_completes_the_abort(self):
        class GatedRest:
            def __init__(self):
                self.started = asyncio.Event()
                self.gate = asyncio.Event()
                self.completed = False

            async def request(self, method, path, body=None, **kwargs):
                self.started.set()
                await self.gate.wait()
                self.completed = True
                return {"message": "Flow aborted"}

        rest = GatedRest()
        task = asyncio.ensure_future(
            contract.HelperOptionsGateway(rest, None).abort_flow("0" * 32)
        )
        await rest.started.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done(), "the cancelled caller waits for the abort")
        rest.gate.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(rest.completed, "the DELETE ran to completion")

    async def test_an_abort_that_never_answers_is_bounded(self):
        class SilentRest:
            cancelled = False

            async def request(self, method, path, body=None, **kwargs):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    SilentRest.cancelled = True
                    raise

        with mock.patch.object(contract, "ABORT_TIMEOUT_SECONDS", 0.05):
            aborted = await contract.HelperOptionsGateway(SilentRest(), None).abort_flow(
                "0" * 32
            )
            await asyncio.sleep(0)
        self.assertFalse(aborted, "an unanswered abort is an orphan, never success")
        self.assertTrue(SilentRest.cancelled)

    def test_open_unconfirmed_is_only_an_unanswered_open(self):
        self.assertTrue(contract.open_unconfirmed(HomeAssistantTimeoutError(details={})))
        self.assertTrue(contract.open_unconfirmed(
            HomeAssistantApiError(details={"provider_response_received": False})))
        self.assertFalse(contract.open_unconfirmed(
            HomeAssistantApiError(details={"status": 500, "provider_response_received": True})))
        self.assertFalse(contract.open_unconfirmed(AuthorizationError()))
        self.assertFalse(contract.open_unconfirmed(
            contract.HelperOptionsProviderError("options_form_malformed")))


class AdmittedServiceBoundaryTests(HelperOptionsServiceTestCase):
    async def test_adapter_admission_follows_the_live_core_status(self):
        adapter = self.runtime._configuration_adapters[contract.CAPABILITY_IDENTITY]
        self.assertTrue(self.runtime._helper_options_admitted())
        self.assertTrue(adapter._admitted())
        self.core.withdraw()
        self.assertFalse(self.runtime._helper_options_admitted())
        self.assertFalse(adapter._admitted())

    async def test_internal_builder_requires_a_persistence_lease(self):
        self.add_filter()
        request = planning.validate_request(
            {"changes": [TIME_CONSTANT_20], "reason": "Synthetic tuning"}
        )
        prepared = await planning.prepare(self.service.helper_options_gateway, request)
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_helper_options_plan(prepared)
        self.assertEqual(raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        self.assertEqual(self.repository.list(), [])

    async def test_admission_withdrawn_at_the_persist_boundary_stores_nothing(self):
        self.add_filter()
        original = self.service._create_configuration_plan

        async def withdraw_then_create(**kwargs):
            self.core.withdraw()
            return await original(**kwargs)

        self.service._create_configuration_plan = withdraw_then_create
        with self.assertRaises(GovernanceError) as raised:
            await self.create_options(TIME_CONSTANT_20)
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertIs(raised.exception.details["plan_created"], False)
        self.assertEqual(self.repository.list(), [])

    async def test_admission_withdrawn_after_approval_never_opens_a_flow(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        opens = len(self.options_rest.opens())
        self.core.withdraw()
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"], PENDING)
        self.assertIs(raised.exception.details["provider_dispatch_occurred"], False)
        self.assertEqual(len(self.options_rest.opens()), opens)
        self.assertEqual(self.options_rest.submits(), [])

    async def test_legacy_apply_route_never_submits(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        self.service.f3_runtime = None
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(self.options_rest.submits(), [])

    async def test_generic_configuration_plan_cannot_carry_options(self):
        self.add_filter()
        with self.assertRaises(GovernanceError):
            await self.service._create_configuration_plan(
                title="t",
                description="d",
                operations=[{
                    "operation_id": "options_1",
                    "resource_type": contract.RESOURCE_TYPE,
                    "action": contract.ACTION,
                    "target_id": "filterentry01",
                    "proposed_config": {},
                }],
                _before_persist=lambda: None,
            )
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.options_rest.submits(), [])

    async def test_generic_resource_write_cannot_reach_options(self):
        self.add_filter()
        with self.assertRaises(Exception):
            await self.resource_gateway.write(
                "update", contract.RESOURCE_TYPE, "filterentry01", {}
            )
        self.assertEqual(self.options_rest.requests, [])


class PlanningTests(HelperOptionsServiceTestCase):
    async def assert_refused(self, reason, *changes, code=None):
        before = len(self.repository.list())
        with self.assertRaises(GovernanceError) as raised:
            await self.create_options(*changes)
        self.assertEqual(raised.exception.details["reason_code"], reason, raised.exception.details)
        self.assertIs(raised.exception.details["plan_created"], False)
        if code is not None:
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(len(self.repository.list()), before)
        self.assertEqual(self.options_rest.submits(), [])
        self.assertEqual(self.options_rest.flows, {}, "every planning flow is aborted")
        return raised.exception

    async def test_filter_plan_reads_through_one_aborted_flow(self):
        self.add_filter()
        created = await self.create_options(TIME_CONSTANT_20)
        self.assertEqual(len(self.options_rest.opens()), 1)
        self.assertEqual(len(self.options_rest.aborts()), 1)
        self.assertEqual(self.options_rest.flows, {})
        self.assertEqual(self.options_rest.submits(), [])
        self.assert_never_sent()
        disclosure = self.options_disclosure(created)
        self.assertEqual(disclosure["domain"], "filter")
        self.assertEqual(disclosure["step_id"], "lowpass")
        self.assertEqual(
            disclosure["changes"],
            [{"field": "time_constant", "baseline": 10, "proposed": 20, "removed": False}],
        )
        self.assertEqual(disclosure["normalization_changes"], [])
        self.assertEqual(disclosure["residual_concurrency_window"], "accepted_od_7")
        self.assertEqual(disclosure["fallback"], "none")
        self.assertIn(planning.CONCURRENCY_DISCLOSURE, disclosure["warnings"])
        self.assertEqual(self.options_rest.visible("filterentry01"), FILTER_LOWPASS)

    async def test_every_plan_is_protected_and_elevated(self):
        # HELPER_CHAIN_GAP: the dependency index has no config-entry helper
        # source, so the evidence is always incomplete (OD-4).
        self.add_filter()
        disclosure = self.options_disclosure(await self.create_options(TIME_CONSTANT_20))
        self.assertTrue(disclosure["protection"]["protected"])
        self.assertIn(planning.HELPER_CHAIN_GAP, disclosure["protection"]["dependency_reasons"])
        self.assertEqual(disclosure["policy_basis"]["policy"], "elevated")
        self.assertFalse(disclosure["consumers"]["evidence_complete"])

    async def test_injected_default_is_disclosed_separately(self):
        self.add_filter()
        del self.options_rest.options["filterentry01"]["visible"]["precision"]
        disclosure = self.options_disclosure(await self.create_options(TIME_CONSTANT_20))
        self.assertEqual(
            disclosure["normalization_changes"],
            [{"field": "precision", "baseline": None, "injected_default": 2}],
        )
        self.assertTrue(any("defaults you did not request" in s
                            for s in disclosure["consequence_statements"]))

    async def test_statistics_clear_one_bound_but_not_both(self):
        self.add_statistics()
        created = await self.create_options(
            {"entity_id": "sensor.mean_temperature", "clear": ["sampling_size"]}
        )
        (row,) = self.options_disclosure(created)["changes"]
        self.assertEqual(row["field"], "sampling_size")
        self.assertTrue(row["removed"])
        self.options_rest.options["statsentry01"]["visible"].pop("max_age")
        self.options_rest.options["statsentry01"]["visible"]["keep_last_sample"] = False
        await self.assert_refused(
            "helper_options_validation_would_fail",
            {"entity_id": "sensor.mean_temperature", "clear": ["sampling_size"]},
        )

    async def test_template_disclosure_and_unparseable_template(self):
        self.add_template_sensor()
        created = await self.create_options(
            {"entity_id": "sensor.double_power",
             "set": {"state": "{{ states('sensor.other_power') | float(0) }}"}}
        )
        template = self.options_disclosure(created)["template"]
        self.assertEqual(template["newly_referenced"], ["sensor.other_power"])
        self.assertEqual(template["no_longer_referenced"], ["sensor.raw_power"])
        self.assertEqual(template["plan_time_render"], "not_performed")
        await self.assert_refused(
            "helper_options_template_unparseable",
            {"entity_id": "sensor.double_power", "set": {"state": "{{ states("}},
        )

    async def test_template_binary_sensor_section_field(self):
        self.add_template_binary_sensor()
        created = await self.create_options(
            {"entity_id": "binary_sensor.door_open",
             "set": {"additional_options.delay_on": {"seconds": 5}}}
        )
        (row,) = self.options_disclosure(created)["changes"]
        self.assertEqual(row["field"], "additional_options.delay_on")

    async def test_input_refusals_read_nothing(self):
        self.add_filter()
        self.options_rest.requests.clear()
        self.ws.commands.clear()
        for reason, change in (
            ("helper_options_field_not_editable",
             {"entity_id": FILTERED, "set": {"filter": "outlier"}}),
            ("helper_options_field_not_editable",
             {"entity_id": FILTERED, "clear": ["entity_id"]}),
            ("helper_options_entity_domain_not_supported",
             {"entity_id": "light.lamp", "set": {"time_constant": 3}}),
            ("helper_options_no_change_requested", {"entity_id": FILTERED}),
            ("helper_options_set_and_clear_overlap",
             {"entity_id": FILTERED, "set": {"precision": 1}, "clear": ["precision"]}),
        ):
            with self.subTest(reason=reason, change=change):
                await self.assert_refused(reason, change)
        self.assertEqual(self.ha_calls(), 0)

    async def test_step_rules_after_the_read(self):
        self.add_filter()
        for reason, change in (
            ("helper_options_field_not_clearable",
             {"entity_id": FILTERED, "clear": ["precision"]}),
            ("helper_options_field_not_in_step",
             {"entity_id": FILTERED, "set": {"radius": 3.0}}),
            ("helper_options_value_invalid",
             {"entity_id": FILTERED, "set": {"time_constant": 2.5}}),
            ("helper_options_value_invalid",
             {"entity_id": FILTERED, "set": {"time_constant": True}}),
            ("helper_options_already_desired",
             {"entity_id": FILTERED, "set": {"time_constant": 10}}),
        ):
            with self.subTest(reason=reason, change=change):
                await self.assert_refused(reason, change)

    async def test_entry_refusals(self):
        self.add_filter()
        self.ws.entries["sensor.plain"] = dict(self.ws.entries[FILTERED])
        self.ws.entries["sensor.plain"].update(entity_id="sensor.plain", config_entry_id=None)
        await self.assert_refused(
            "helper_options_not_config_entry_helper",
            {"entity_id": "sensor.plain", "set": {"precision": 1}},
        )
        self.ws.config_entries["filterentry01"]["domain"] = "utility_meter"
        await self.assert_refused("helper_options_domain_not_supported", TIME_CONSTANT_20)
        self.ws.config_entries["filterentry01"]["domain"] = "filter"
        self.ws.config_entries["filterentry01"]["state"] = "setup_retry"
        await self.assert_refused("helper_options_entry_not_loaded", TIME_CONSTANT_20)
        self.ws.config_entries["filterentry01"]["state"] = "loaded"
        self.ws.config_entries["filterentry01"]["supports_options"] = False
        await self.assert_refused("helper_options_options_not_supported", TIME_CONSTANT_20)
        self.assertEqual(self.options_rest.opens(), [], "no flow opened for a refused entry")

    async def test_one_domain_per_plan(self):
        self.add_filter()
        self.add_statistics()
        await self.assert_refused(
            "helper_options_mixed_domains",
            TIME_CONSTANT_20,
            {"entity_id": "sensor.mean_temperature", "set": {"precision": 1.0}},
        )
        # The first entry was read (and its flow aborted) before the second
        # entry's domain refused the plan.
        self.assertEqual(len(self.options_rest.opens()), 1)
        self.assertEqual(len(self.options_rest.aborts()), 1)

    async def test_schema_drift_aborts_the_flow_and_refuses(self):
        self.add_filter()
        form = flow_form("0" * 32, "filter", "lowpass", FILTER_LOWPASS)
        del form["flow_id"]
        form["data_schema"] = [f for f in form["data_schema"] if f["name"] != "precision"]
        self.options_rest.open_response = form
        await self.assert_refused("helper_options_schema_unrecognized", TIME_CONSTANT_20)
        self.assertEqual(len(self.options_rest.aborts()), 1)

    async def test_failed_abort_at_planning_is_disclosed_as_an_orphan(self):
        self.add_filter()
        self.options_rest.abort_failure = HomeAssistantApiError(
            details={"status": 500, "provider_response_received": True}
        )
        created = await self.create_options(TIME_CONSTANT_20)
        self.assertEqual(
            created["dry_run_results"][contract.INTENT_KEY]["orphan_flows"], 1
        )
        self.assertEqual(len(self.options_rest.flows), 1, "the orphan is Core-memory only")
        self.assertEqual(self.options_rest.submits(), [])

    async def test_change_while_the_flow_is_open_refuses(self):
        self.add_filter()
        self.options_rest.after_open = (
            lambda entry_id, flow_id, ordinal: self.options_rest.touch(entry_id)
        )
        await self.assert_refused(
            "helper_options_target_changed_during_planning", TIME_CONSTANT_20
        )


class AdapterAdmissionTests(HelperOptionsServiceTestCase):
    """The adapter re-reads admission itself at preflight, pre-dispatch and
    readback; Core and the service still admit, so only its own gate refuses."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add_filter()
        self.created = await self.approved_options(TIME_CONSTANT_20)
        self.adapter = self.runtime._configuration_adapters[contract.CAPABILITY_IDENTITY]
        self.adapter_admits = True
        self.adapter._admission = lambda: self.adapter_admits

    async def test_preflight_refuses_before_any_read(self):
        self.adapter_admits = False
        calls = self.ha_calls()
        result = await self.apply(self.created)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn(CORE_ADMISSION_UNAVAILABLE, self.child_codes(result["task_id"]))
        self.assertEqual(self.ha_calls(), calls, "no Home Assistant read")

    async def test_predispatch_refuses_before_opening_a_flow(self):
        original = HelperOptionsAdapter.preflight

        async def then_withdraw(adapter, *args, **kwargs):
            outcome = await original(adapter, *args, **kwargs)
            self.adapter_admits = False
            return outcome

        opens = len(self.options_rest.opens())
        with mock.patch.object(HelperOptionsAdapter, "preflight", then_withdraw):
            result = await self.apply(self.created)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn(CORE_ADMISSION_UNAVAILABLE, self.child_codes(result["task_id"]))
        self.assertEqual(len(self.options_rest.opens()), opens, "no flow opened")
        self.assertEqual(self.options_rest.submits(), [])

    async def test_readback_refuses_after_the_submit(self):
        gateway = self.adapter.gateway
        original = gateway.options_submit

        async def submit_then_withdraw(*args, **kwargs):
            outcome = await original(*args, **kwargs)
            self.adapter_admits = False
            return outcome

        with mock.patch.object(gateway, "options_submit", submit_then_withdraw):
            result = await self.apply(self.created)
        self.assertEqual(len(self.options_rest.submits()), 1)
        self.assertNotEqual(result["task_state"], "succeeded_verified", result)
        self.assertIn(CORE_ADMISSION_UNAVAILABLE, self.child_codes(result["task_id"]))


class ExecutionTests(HelperOptionsServiceTestCase):
    async def test_apply_verifies_and_offers_a_separately_approved_rollback(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertTrue(result["provider_dispatch_occurred"])
        # OD-7: the apply receipt carries the accepted-window disclosure.
        (receipt_child,) = result["plan"]["operations"]
        self.assertIn(planning.CONCURRENCY_DISCLOSURE[:500],
                      receipt_child["risk"]["warnings"])
        self.assertEqual(self.options_rest.visible("filterentry01")["time_constant"], 20)
        self.assertEqual(len(self.options_rest.submits()), 1)
        (body,) = self.options_rest.submitted_bodies()
        self.assertEqual(body, {**FILTER_LOWPASS, "time_constant": 20})
        self.assertEqual(self.options_rest.flows, {}, "no flow left open")
        # Planning, held dispatch, readback: three opens, two aborts.
        self.assertEqual(len(self.options_rest.opens()), 3)
        self.assertEqual(len(self.options_rest.aborts()), 2)
        self.assertIn("exact_readback_verified", self.child_codes(result["task_id"]))
        self.assertEqual(getattr(self.options_rest, "authority_refusals", []), [])
        self.assert_never_sent()

        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_plan_created", rollback)
        self.assertFalse(rollback["provider_dispatch_occurred"])
        self.assertEqual(rollback["defaults_not_restored"], [])
        self.assertEqual(len(self.options_rest.submits()), 1, "rollback never runs itself")
        restore = self.service.get_plan(rollback["rollback_plan_id"])
        await self.approve(restore)
        restored = await self.apply(restore)
        self.assertEqual(restored["task_state"], "succeeded_verified", restored)
        self.assertEqual(self.options_rest.visible("filterentry01"), FILTER_LOWPASS)

    async def test_drift_before_apply_is_stale_with_no_flow(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        opens = len(self.options_rest.opens())
        self.options_rest.set_external("filterentry01", time_constant=15)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn(STALE, self.child_codes(result["task_id"]))
        self.assertEqual(len(self.options_rest.opens()), opens, "preflight opens no flow")
        self.assertEqual(self.options_rest.submits(), [])

    async def test_visible_drift_in_the_window_is_stale_and_aborted(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        # A value change Core did not stamp: only the held form shows it.
        self.options_rest.options["filterentry01"]["visible"]["window_size"] = 4
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIn(STALE, self.child_codes(result["task_id"]))
        self.assertEqual(self.options_rest.submits(), [])
        self.assertEqual(self.options_rest.flows, {}, "the held flow is aborted")

    async def test_entry_moved_after_the_open_is_stale_and_aborted(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        planning_opens = len(self.options_rest.opens())

        def touch(entry_id, flow_id, ordinal):
            if ordinal == planning_opens + 1:
                self.options_rest.touch(entry_id)

        self.options_rest.after_open = touch
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        codes = self.child_codes(result["task_id"])
        self.assertIn(STALE, codes)
        self.assertNotIn(ORPHANED, codes)
        self.assertEqual(self.options_rest.submits(), [])
        self.assertEqual(self.options_rest.flows, {})

    async def test_failed_abort_of_a_drifted_flow_is_an_orphan(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        self.options_rest.options["filterentry01"]["visible"]["window_size"] = 4
        self.options_rest.abort_failure = HomeAssistantApiError(
            details={"status": 500, "provider_response_received": True}
        )
        result = await self.apply(created)
        codes = self.child_codes(result["task_id"])
        self.assertIn(STALE, codes)
        self.assertIn(ORPHANED, codes)
        self.assertEqual(self.options_rest.submits(), [])

    async def test_window_reads_outside_core_authority_fail_closed(self):
        """Mutation: the held-flow open moved outside the read window."""

        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        with mock.patch.object(HelperOptionsAdapter, "PREDISPATCH_READS_HOME_ASSISTANT", False):
            result = await self.apply(created)
        self.assertNotEqual(result["task_state"], "succeeded_verified", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.options_rest.submits(), [])
        self.assertTrue(getattr(self.options_rest, "authority_refusals", []))

    async def test_before_dispatch_failure_releases_the_held_flow(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        original = HelperOptionsAdapter.dispatch

        async def failing_dispatch(adapter, operation, preflight, *, before_dispatch):
            async def refuse():
                raise RuntimeError("synthetic intent persistence failure")

            return await original(adapter, operation, preflight, before_dispatch=refuse)

        with mock.patch.object(HelperOptionsAdapter, "dispatch", failing_dispatch):
            try:
                await self.apply(created)
            except Exception:  # noqa: BLE001 - any failure is acceptable here
                pass
        self.assertEqual(self.options_rest.submits(), [])
        self.assertEqual(self.options_rest.flows, {}, "the held flow is released")
        self.assertEqual(getattr(self.options_rest, "authority_refusals", []), [],
                         "the release abort runs in its own Core window")
        self.assertEqual(len(self.options_rest.aborts()), 2)


class OrphanDisclosureTests(HelperOptionsServiceTestCase):
    async def test_failed_release_after_a_before_dispatch_failure_is_disclosed(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        original = HelperOptionsAdapter.dispatch
        abort_failure = HomeAssistantApiError(details={"status": 500})

        async def failing_dispatch(adapter, operation, preflight, *, before_dispatch):
            async def refuse():
                self.options_rest.abort_failure = abort_failure
                raise RuntimeError("synthetic intent persistence failure")

            return await original(adapter, operation, preflight, before_dispatch=refuse)

        with mock.patch.object(HelperOptionsAdapter, "dispatch", failing_dispatch):
            result = await self.apply(created)
        self.assertFalse(result["provider_dispatch_occurred"], result)
        self.assertEqual(self.options_rest.submits(), [])
        codes = self.child_codes(result["task_id"])
        self.assertIn("dispatch_intent_persistence_failed", codes)
        self.assertIn("options_flow_orphaned", codes, "the failed release is disclosed")
        self.assertEqual(len(self.options_rest.flows), 1, "the orphan is Core-memory only")

    async def test_failed_abort_of_the_readback_flow_is_disclosed(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        abort_failure = HomeAssistantApiError(details={"status": 500})

        def fail_aborts_after_the_submit(entry_id, flow_id, ordinal):
            if self.options_rest.submits():
                self.options_rest.abort_failure = abort_failure

        self.options_rest.after_open = fail_aborts_after_the_submit
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertIn("options_flow_orphaned", self.child_codes(result["task_id"]))
        self.assertEqual(len(self.options_rest.flows), 1)


class FlowLifecycleTests(HelperOptionsServiceTestCase):
    """Review H2: every dispatch path leaves no options flow open, and
    reports a flow it could not abort truthfully."""

    def gateway(self):
        return self.runtime._configuration_adapters[contract.CAPABILITY_IDENTITY].gateway

    async def test_core_restart_after_the_open_refuses_with_no_false_orphan(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        planning_opens = len(self.options_rest.opens())

        def restart(entry_id, flow_id, ordinal):
            if ordinal == planning_opens + 1:
                # Core restarted: its in-memory flows are gone, the entry
                # was set up again.
                self.options_rest.flows.clear()
                self.options_rest.touch(entry_id)

        self.options_rest.after_open = restart
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        codes = self.child_codes(result["task_id"])
        self.assertIn(STALE, codes)
        self.assertNotIn(ORPHANED, codes, "a flow Core no longer holds is not an orphan")
        self.assertEqual(self.options_rest.submits(), [])
        self.assertEqual(self.options_rest.flows, {})

    async def test_core_restart_before_the_submit_is_not_applied(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        gateway = self.gateway()
        original = gateway.options_submit

        async def restart_then_submit(flow_id, submit):
            self.options_rest.flows.clear()
            return await original(flow_id, submit)

        with mock.patch.object(gateway, "options_submit", restart_then_submit):
            result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        self.assertNotIn(ORPHANED, self.child_codes(result["task_id"]))
        self.assertEqual(len(self.options_rest.submits()), 1, "never resubmitted")
        self.assertEqual(self.options_rest.visible("filterentry01"), FILTER_LOWPASS)
        self.assertEqual(self.options_rest.flows, {})

    async def test_lost_submit_response_aborts_the_flow_by_id(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        self.options_rest.submit_failure = HomeAssistantTimeoutError(details={})
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        self.assertNotIn(ORPHANED, self.child_codes(result["task_id"]))
        self.assertEqual(len(self.options_rest.submits()), 1, "never resubmitted")
        self.assertEqual(self.options_rest.flows, {})

    async def test_lost_submit_that_applied_leaves_no_flow_and_no_orphan(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        self.options_rest.submit_failure = HomeAssistantTimeoutError(details={})
        self.options_rest.submit_failure_applies = True
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertNotIn(ORPHANED, self.child_codes(result["task_id"]))
        self.assertEqual(self.options_rest.flows, {})

    async def test_lost_submit_with_a_failed_abort_is_disclosed_as_an_orphan(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        planning_opens = len(self.options_rest.opens())
        self.options_rest.submit_failure = HomeAssistantApiError(
            details={"provider_response_received": False}
        )
        abort_failure = HomeAssistantApiError(
            details={"status": 500, "provider_response_received": True}
        )

        def fail_only_the_held_abort(entry_id, flow_id, ordinal):
            self.options_rest.abort_failure = (
                abort_failure if ordinal == planning_opens + 1 else None
            )

        self.options_rest.after_open = fail_only_the_held_abort
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        self.assertIn(ORPHANED, self.child_codes(result["task_id"]))
        self.assertEqual(len(self.options_rest.flows), 1, "only the held flow, disclosed")
        self.assertEqual(len(self.options_rest.submits()), 1)

    async def test_cancellation_during_the_submit_aborts_the_flow(self):
        """Mutation O-H2a: the submit's abort guard narrowed to ``Exception``."""

        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        rest = self.options_rest
        original = rest.request
        submitting = asyncio.Event()

        async def hang_on_submit(method, path, body=None, **kwargs):
            if method == "POST" and str(path).startswith(STEP_PREFIX):
                submitting.set()
                await asyncio.Event().wait()
            return await original(method, path, body, **kwargs)

        rest.request = hang_on_submit
        task = asyncio.ensure_future(self.apply(created))
        await submitting.wait()
        self.assertEqual(len(rest.flows), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(rest.flows, {}, "the cancelled submit's flow is aborted")
        self.assertEqual(getattr(rest, "authority_refusals", []), [])

    async def test_cancellation_during_the_window_checks_aborts_the_flow(self):
        """Mutation O-H2b: the held flow registered only after the checks."""

        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        gateway = self.gateway()
        original = gateway.options_entry
        planning_opens = len(self.options_rest.opens())

        async def cancel_after_the_open(*args, **kwargs):
            if len(self.options_rest.opens()) > planning_opens:
                raise asyncio.CancelledError()
            return await original(*args, **kwargs)

        with mock.patch.object(gateway, "options_entry", cancel_after_the_open):
            with self.assertRaises(asyncio.CancelledError):
                await self.apply(created)
        self.assertEqual(self.options_rest.submits(), [])
        self.assertEqual(self.options_rest.flows, {})

    async def test_lost_open_response_is_disclosed_and_never_submits(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        rest = self.options_rest
        original = rest.request

        async def lose_the_open(method, path, body=None, **kwargs):
            response = await original(method, path, body, **kwargs)
            if method == "POST" and str(path) == STEP_PREFIX.rstrip("/"):
                raise HomeAssistantTimeoutError(details={})
            return response

        rest.request = lose_the_open
        result = await self.apply(created)
        rest.request = original
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        codes = self.child_codes(result["task_id"])
        self.assertIn(contract.FLOW_OPEN_UNCONFIRMED, codes)
        self.assertEqual(self.options_rest.submits(), [])
        # Core created the flow; its id never arrived, so it is disclosed,
        # not aborted.
        self.assertEqual(len(self.options_rest.flows), 1)


class ReadbackRuleTests(HelperOptionsServiceTestCase):
    async def test_proposed_options_without_a_stored_update_are_not_verified(self):
        # Core moves ``modified_at`` whenever it stores options; equal options
        # with an unmoved entry are not proof of this submit.
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        original = self.options_rest._apply

        def apply_without_moving(entry_id, submit):
            before = self.options_rest.modified_at(entry_id)
            original(entry_id, submit)
            self.ws.config_entries[entry_id]["modified_at"] = before

        self.options_rest._apply = apply_without_moving
        result = await self.apply(created)
        self.assertNotEqual(result["task_state"], "succeeded_verified", result)
        self.assertNotIn("exact_readback_verified", self.child_codes(result["task_id"]))
        self.assertEqual(len(self.options_rest.submits()), 1, "never resubmitted")


class SubmitOutcomeTests(HelperOptionsServiceTestCase):
    async def apply_with(self, **settings):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        for name, value in settings.items():
            setattr(self.options_rest, name, value)
        return created, await self.apply(created)

    async def assert_not_applied(self, created, result):
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.dispatch_count, 1)
        self.assertNotIn("exact_readback_verified", self.child_codes(result["task_id"]))
        self.assertEqual(self.options_rest.visible("filterentry01"), FILTER_LOWPASS)
        self.assertEqual(len(self.options_rest.submits()), 1, "never resubmitted")
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)

    async def test_abort_answer_is_rejected(self):
        created, result = await self.apply_with(
            submit_response={"type": "abort", "flow_id": "0" * 32, "reason": "synthetic"}
        )
        await self.assert_not_applied(created, result)
        self.assertIn("options_flow_aborted", self.child_codes(result["task_id"]))

    async def test_validation_form_is_rejected_and_the_flow_aborted(self):
        form = flow_form("0" * 32, "filter", "lowpass", FILTER_LOWPASS,
                         errors={"base": "synthetic"})
        created, result = await self.apply_with(submit_response=form)
        await self.assert_not_applied(created, result)
        self.assertIn("options_flow_validation_error", self.child_codes(result["task_id"]))
        self.assertEqual(self.options_rest.flows, {})

    async def test_bad_request_is_rejected_and_the_flow_aborted(self):
        created, result = await self.apply_with(submit_response=ExpectedHttpStatus(400))
        await self.assert_not_applied(created, result)
        self.assertEqual(self.options_rest.flows, {})

    async def test_lost_response_that_applied_is_verified_by_readback(self):
        _created, result = await self.apply_with(
            submit_failure=HomeAssistantApiError(
                details={"status": 502, "provider_response_received": True}
            ),
            submit_failure_applies=True,
        )
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.options_rest.submits()), 1)

    async def test_lost_response_not_applied_is_a_mismatch(self):
        _created, result = await self.apply_with(
            submit_failure=HomeAssistantApiError(
                details={"provider_response_received": False}
            ),
        )
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        (record,) = self.child_records(result["task_id"])
        self.assertEqual(record.normalized_outcome, "verification_mismatch")
        self.assertEqual(len(self.options_rest.submits()), 1, "never resubmitted")

    async def test_entry_not_reloaded_is_manual_review(self):
        _created, result = await self.apply_with(state_after_submit="setup_retry")
        self.assertEqual(result["task_state"], "manual_review_required", result)
        self.assertIn(contract.ENTRY_UNHEALTHY, self.child_codes(result["task_id"]))
        self.assertEqual(
            len(self.poll_sleeps), contract.RELOAD_POLL_ATTEMPTS - 1,
            "bounded reload poll",
        )


class RollbackTests(HelperOptionsServiceTestCase):
    async def forward(self, *changes):
        created = await self.approved_options(*changes)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        return created

    async def test_rollback_refused_after_later_drift(self):
        self.add_filter()
        created = await self.forward(TIME_CONSTANT_20)
        self.options_rest.set_external("filterentry01", time_constant=30)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(
            raised.exception.details["reason"], "helper_options_rollback_baseline_drift"
        )

    async def test_rollback_refused_after_an_unstamped_touch(self):
        self.add_filter()
        created = await self.forward(TIME_CONSTANT_20)
        self.options_rest.touch("filterentry01")
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(
            raised.exception.details["reason"], "helper_options_rollback_baseline_drift"
        )

    async def test_cleared_field_is_restored_and_set_field_is_cleared(self):
        self.add_statistics()
        created = await self.forward(
            {"entity_id": "sensor.mean_temperature", "clear": ["sampling_size"]}
        )
        self.assertNotIn("sampling_size", self.options_rest.visible("statsentry01"))
        rollback = await self.service.rollback_change(created["plan_id"])
        restore = self.service.get_plan(rollback["rollback_plan_id"])
        await self.approve(restore)
        self.assertEqual(
            (await self.apply(restore))["task_state"], "succeeded_verified"
        )
        self.assertEqual(self.options_rest.visible("statsentry01")["sampling_size"], 20.0)

        self.add_template_sensor(entry_id="templateentry09", entity_id="sensor.tripled")
        created = await self.forward(
            {"entity_id": "sensor.tripled", "set": {"device_class": "power"}}
        )
        rollback = await self.service.rollback_change(created["plan_id"])
        restore = self.service.get_plan(rollback["rollback_plan_id"])
        await self.approve(restore)
        self.assertEqual(
            (await self.apply(restore))["task_state"], "succeeded_verified"
        )
        self.assertNotIn("device_class", self.options_rest.visible("templateentry09"))

    async def test_injected_defaults_are_disclosed_not_restored(self):
        self.add_filter()
        del self.options_rest.options["filterentry01"]["visible"]["precision"]
        created = await self.forward(TIME_CONSTANT_20)
        self.assertEqual(self.options_rest.visible("filterentry01")["precision"], 2)
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(
            rollback["defaults_not_restored"],
            [{"operation_id": "options_1", "field": "precision"}],
        )

    async def test_partial_forward_gives_a_partial_rollback(self):
        self.add_template_sensor()
        self.add_template_sensor(entry_id="templateentry03", entity_id="sensor.tripled")
        created = await self.approved_options(
            {"entity_id": "sensor.double_power", "set": {"unit_of_measurement": "kW"}},
            {"entity_id": "sensor.tripled", "set": {"unit_of_measurement": "kW"}},
        )
        self.options_rest.options["templateentry03"]["visible"]["unit_of_measurement"] = "mW"
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "manual_review_required", result)
        self.assertIn(STALE, self.child_codes(result["task_id"]))
        self.assertEqual(len(self.options_rest.submits()), 1, "one sent, one refused")
        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_partial_plan_created", rollback)
        self.assertEqual(rollback["operations_to_restore"], ["options_1"])
        self.assertEqual(rollback["operations_excluded"], ["options_2"])

    async def test_rollback_refused_without_admission(self):
        self.add_filter()
        created = await self.forward(TIME_CONSTANT_20)
        self.core.withdraw()
        with self.assertRaises(GovernanceError):
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(len(self.options_rest.submits()), 1)


class ReachabilityTests(unittest.TestCase):
    """Only the options gateway and the path table name the options routes."""

    def test_options_flow_routes_appear_only_in_reviewed_modules(self):
        allowed = {
            PACKAGE / "governance" / "helper_options.py",
            PACKAGE / "clients" / "paths.py",
        }
        found = set()
        for path in PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "config_entries/options/flow" in node.value
                ):
                    found.add(path)
        self.assertTrue(found)
        self.assertLessEqual(found, allowed, sorted(str(p) for p in found - allowed))

    def test_submit_marks_the_provider_send_boundary(self):
        source = (PACKAGE / "governance" / "helper_options.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        (submit,) = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "submit"
        ]
        calls = [
            node.func.id for node in ast.walk(submit)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertIn("mark_provider_send", calls)


if __name__ == "__main__":
    unittest.main()
