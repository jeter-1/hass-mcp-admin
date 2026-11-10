"""HAMCP-297: governed device-configuration entities (switch-like, Matter only).

Offline synthetic fixtures: a fake Home Assistant REST/WebSocket pair that runs
the real clients' Core-authority check, a synthetic signed Core runtime, the
production gateway, adapter, F3 executor and durable stores. Contract:
``docs/HAMCP_297_DEVICE_CONFIGURATION_ENTITIES_DESIGN.md``.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from core_authority_fakes import require_core_authority  # noqa: E402
from ha_mcp_engineering.device_configuration import adapter as adapter_module  # noqa: E402
from ha_mcp_engineering.device_configuration.adapter import PROFILE  # noqa: E402
from ha_mcp_engineering.device_configuration.contracts import (  # noqa: E402
    ALLOWLIST, DISCLOSURE_KINDS, NEVER_SENT, READ_COMMANDS, SERVICES, UNIT_CONVERTERS,
    DeviceConfigurationRefusal, DeviceConfigurationRequest, digest, service_call,
)
from ha_mcp_engineering.device_configuration.gateway import DeviceConfigurationGateway  # noqa: E402
from ha_mcp_engineering.device_configuration.service import (  # noqa: E402
    CONFIRMATION_REASONS, DEVICE_CONFIGURATION_OPERATIONS, DeviceConfigurationCoreAuthority,
    DeviceConfigurationService, request_summary,
)
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError, ErrorCode, HomeAssistantApiError, HomeAssistantTimeoutError,
)
from ha_mcp_engineering.f3.contracts import LockMode, LockRequest, LockScope  # noqa: E402
from ha_mcp_engineering.f3.models import LockOwner, LockTiming  # noqa: E402
from ha_mcp_engineering.helper_value.safety import CONFIRMATION_SAFETY_CRITICAL  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.tools.device_configuration import control_device_configuration  # noqa: E402
from ha_mcp_engineering.tools.governance import get_execution_task  # noqa: E402

SELECT = "select.plug_power_on_behavior"
SWITCH = "switch.plug_child_lock"
NUMBER = "number.sensor_sensitivity"
CORE = "2026.11.0"


def category(exc):
    return exc.details["category"]


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 10, 6, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)

    async def sleep(self, seconds):
        self.advance(seconds)


@dataclass(frozen=True)
class Lease:
    nonce: str
    generation: int
    core_version: str
    capability_ids: tuple


class SignedCore:
    """Synthetic signed applicability naming the device-configuration profile."""

    def __init__(self):
        self.allowed, self.signed, self.version, self.generation = True, True, CORE, 1
        self.leases, self.commits, self.requirements = set(), set(), []

    async def reconcile_once(self, trigger):
        return None

    def route_status(self, requirements):
        return {"available": self.allowed and self.signed and PROFILE.capability_id in requirements}

    def acquire(self, requirements, **kwargs):
        self.requirements.append(tuple(requirements))
        expected = kwargs["expected_core_version"]
        if (not self.allowed or not self.signed or PROFILE.capability_id not in requirements
                or expected not in {None, self.version}):
            return None
        token = Lease(uuid.uuid4().hex, self.generation, self.version, tuple(requirements))
        self.leases.add(token)
        return token

    def consume(self, token):
        if token not in self.leases or not self.allowed:
            return None
        self.leases.remove(token)
        self.commits.add(token)
        return token

    def revalidate(self, token, commit):
        return (self.allowed and token == commit and token in self.commits
                and token.generation == self.generation)

    def release(self, token):
        self.leases.discard(token)

    def finish(self, token):
        self.commits.discard(token)


def registry(entity_id, translation_key, device_id, **changes):
    value = {
        "id": "reg-" + entity_id.split(".", 1)[1], "entity_id": entity_id,
        "created_at": 1762000000.0, "modified_at": 1762000001.0, "entity_category": "config",
        "device_id": device_id, "platform": "matter", "translation_key": translation_key,
        "unique_id": "matter-unique-" + entity_id, "config_entry_id": "entry-matter",
        "original_device_class": None, "options": {}, "disabled_by": None, "capabilities": None,
    }
    value.update(changes)
    return value


def device(device_id, name):
    return {"id": device_id, "created_at": 1762000000.0, "name": name, "name_by_user": None,
            "identifiers": [["matter", "deadbeef-" + device_id]],
            "primary_config_entry": "entry-matter", "disabled_by": None}


class FakeHomeAssistant:
    """Synthetic REST + WebSocket peer; applies writes like the spike stub."""

    def __init__(self):
        number_attrs = {"min": 1, "max": 3, "step": 1, "mode": "slider"}
        self.entities = {
            SELECT: {"registry": registry(SELECT, "startup_on_off", "dev-plug"),
                     "state": "previous",
                     "attributes": {"options": ["off", "on", "toggle", "previous"],
                                    "friendly_name": "Plug Power-on behavior"}},
            SWITCH: {"registry": registry(SWITCH, "child_lock", "dev-plug"), "state": "off",
                     "attributes": {"friendly_name": "Plug Child lock"}},
            NUMBER: {"registry": registry(NUMBER, "sensitivity_level", "dev-sensor",
                                          capabilities=dict(number_attrs)),
                     "state": "2", "attributes": {**number_attrs,
                                                  "friendly_name": "Sensor Sensitivity"}},
        }
        self.devices = [device("dev-plug", "Eve Energy Plug"), device("dev-sensor", "Door P2")]
        self.revision = 0
        self.commands, self.rest, self.writes = [], [], []
        self.mode = "apply"
        self.reads = {"registry": 0, "state": 0}
        self.on_read = None  # (kind, count) -> None, before answering

    def touch(self, entity_id):
        self.revision += 1
        self.entities[entity_id]["stamp"] = f"2026-11-10T06:00:{self.revision:02d}+00:00"
        self.entities[entity_id]["context"] = f"ctx-{self.revision}"

    def gateway(self):
        return DeviceConfigurationGateway(self, self)

    async def command(self, payload):
        require_core_authority(self, "websocket.command", "device_configuration")
        self.commands.append(deepcopy(payload))
        kind = payload["type"]
        if kind == "config/entity_registry/get":
            self.reads["registry"] += 1
            if self.on_read:
                self.on_read("registry", self.reads["registry"])
            entity = self.entities.get(payload["entity_id"])
            if entity is None:
                raise HomeAssistantApiError(details={"status": 404})
            return deepcopy(entity["registry"])
        if kind == "config/device_registry/list":
            return deepcopy(self.devices)
        if kind == "call_service":
            return self.call_service(payload)
        raise AssertionError("unrelated Home Assistant command: " + kind)

    def call_service(self, payload):
        if self.mode == "not_sent":
            raise AuthorizationError(call_not_sent=True)
        self.writes.append(deepcopy(payload))
        entity_id = payload["target"]["entity_id"]
        if self.mode in {"apply", "timeout_applied"}:
            data = payload.get("service_data", {})
            if payload["domain"] == "select":
                value = data["option"]
            elif payload["domain"] == "switch":
                value = payload["service"].removeprefix("turn_")
            else:
                number = data["value"]
                value = str(int(number)) if float(number).is_integer() else repr(number)
            self.entities[entity_id]["state"] = value
            self.touch(entity_id)
        if self.mode == "timeout_applied":
            raise HomeAssistantTimeoutError()
        if self.mode == "rejected":
            raise HomeAssistantApiError(details={"status": None})
        return None

    async def request(self, method, path):
        require_core_authority(self, "rest.request", "states")
        self.rest.append((method, str(path)))
        assert method == "GET"
        self.reads["state"] += 1
        if self.on_read:
            self.on_read("state", self.reads["state"])
        entity_id = str(path).rsplit("/", 1)[1]
        entity = self.entities[entity_id]
        return {"entity_id": entity_id, "state": entity["state"],
                "attributes": deepcopy(entity["attributes"]),
                "last_changed": entity.get("stamp", "2026-11-10T05:00:00+00:00"),
                "last_updated": entity.get("stamp", "2026-11-10T05:00:00+00:00"),
                "context": {"id": entity.get("context", "ctx-0"), "parent_id": None,
                            "user_id": None}}


class DeviceConfigurationTests(unittest.IsolatedAsyncioTestCase):
    trigger = None

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock, self.core, self.ha = Clock(), SignedCore(), FakeHomeAssistant()
        self.service = self.make_service()
        self.telemetry, self.token = begin_request("synthetic-device-configuration")
        self.addCleanup(end_request, self.token)

    def make_service(self, **kwargs):
        service = DeviceConfigurationService(
            self.tmp.name, self.ha.gateway(), DeviceConfigurationCoreAuthority(self.core),
            now=self.clock, sleep=self.clock.sleep,
            safety_trigger=kwargs.pop("safety_trigger", self.trigger), **kwargs)
        self.addAsyncCleanup(service.close)
        return service

    def request(self, entity_id=SELECT, action="select_option", value="on", **kwargs):
        return DeviceConfigurationRequest(
            entity_id=entity_id, action=action, value=value,
            operation_id=f"{int(self.clock().timestamp())}-{uuid.uuid4().hex}", **kwargs)

    async def call(self, request, service=None):
        self.telemetry.ordinary_device_configuration_binding = digest(request.model_dump())
        return await (service or self.service).control(request)

    async def refused(self, request, code):
        with self.assertRaises(DeviceConfigurationRefusal) as raised:
            await self.call(request)
        self.assertEqual(category(raised.exception), code)
        self.assertIsNone(self.service.load(request.task_id))  # no task, no dispatch
        return raised.exception

    def settled(self):
        self.assertEqual(self.core.leases, set())
        self.assertEqual(self.core.commits, set())
        self.assertEqual(self.service.locks.records(), ())

    # --- Success (the switch-like path) ----------------------------------

    async def test_select_switch_number_each_one_closed_call_and_verified(self):
        cases = [(SELECT, "select_option", "on", ("select", "select_option", {"option": "on"})),
                 (SWITCH, "turn_on", None, ("switch", "turn_on", None)),
                 (NUMBER, "set_value", "3", ("number", "set_value", {"value": 3.0}))]
        for entity_id, action, value, (domain, service, data) in cases:
            before = len(self.ha.writes)
            result = await self.call(self.request(entity_id, action, value))
            self.assertEqual(result["status"], "succeeded_verified", result)
            self.assertEqual(result["terminal_outcome"], "succeeded_verified")
            self.assertEqual(result["provider_attempt_count"], 1)
            self.assertEqual(len(self.ha.writes), before + 1)
            expected = {"type": "call_service", "domain": domain, "service": service,
                        "target": {"entity_id": entity_id}}
            if data is not None:
                expected["service_data"] = data
            self.assertEqual(self.ha.writes[-1], expected)
            self.assertEqual(result["safety_gate"], "not_safety_critical")
            self.assertIs(result["external_approval"], False)
            self.assertEqual([item["kind"] for item in result["disclosures"]],
                             list(DISCLOSURE_KINDS))
            self.assertTrue(result["undo"]["available"], result["undo"])
            self.assertEqual(result["target"]["target_kind"][0], domain)
            self.assertEqual(result["core_binding"]["version"], CORE)
            self.assertNotIn("plan_id", result)
            self.settled()

    async def test_number_receipt_carries_identity_conversion_proof(self):
        result = await self.call(self.request(NUMBER, "set_value", "1"))
        self.assertEqual(result["baseline"]["identity_conversion"],
                         "device_class_has_no_unit_converter")
        self.assertEqual(result["baseline"]["value"], "2")
        self.assertEqual(result["requested_value"], "1")

    async def test_value_already_current_is_zero_dispatch_no_change(self):
        result = await self.call(self.request(SELECT, "select_option", "previous"))
        self.assertEqual(result["status"], "no_change", result)
        self.assertEqual(result["safety_gate"], "not_evaluated_value_already_current")
        self.assertEqual(result["undo"]["available"], False)
        self.assertEqual(self.ha.writes, [])
        self.settled()

    async def test_exact_duplicate_reconciles_and_changed_arguments_refuse(self):
        request = self.request()
        first = await self.call(request)
        for _ in range(2):
            again = await self.call(request)
            self.assertEqual(again["task_id"], first["task_id"])
            self.assertEqual(again["status"], "succeeded_verified")
        changed = request.model_copy(update={"value": "off"})
        with self.assertRaises(DeviceConfigurationRefusal) as raised:
            await self.call(changed)
        self.assertEqual(category(raised.exception), "device_configuration_operation_id_rebound")
        self.assertEqual(len(self.ha.writes), 1)

    async def test_concurrent_exact_duplicate_has_one_owner(self):
        request = self.request()
        self.telemetry.ordinary_device_configuration_binding = digest(request.model_dump())
        values = await asyncio.gather(self.service.control(request),
                                      self.service.control(request))
        self.assertEqual(len(self.ha.writes), 1)
        self.assertTrue(all(item["task_id"] == request.task_id for item in values))
        self.settled()

    async def test_real_tool_and_task_reader(self):
        request = self.request(SWITCH, "turn_on", None)
        self.telemetry.ordinary_device_configuration_binding = digest(request.model_dump())
        with patch.object(DEVICE_CONFIGURATION_OPERATIONS, "service", self.service):
            value = json.loads(await control_device_configuration(
                entity_id=SWITCH, action="turn_on", operation_id=request.operation_id))
            self.assertTrue(value["success"], value)
            self.assertEqual(value["data"]["status"], "succeeded_verified")
            read = json.loads(await get_execution_task(request.task_id))
            self.assertEqual(read["data"]["task_id"], request.task_id)
            self.assertEqual(read["data"]["status"], "succeeded_verified")
        self.assertEqual(len(self.ha.writes), 1)

    async def test_restart_reads_receipt_without_redispatch(self):
        request = self.request()
        await self.call(request)
        replacement = self.make_service()
        self.clock.advance(400)
        result = await self.call(request, replacement)
        self.assertEqual(result["status"], "succeeded_verified")
        self.assertEqual(len(self.ha.writes), 1)

    # --- Input refusals (closed vocabulary) ------------------------------

    def test_parse_refusals_are_closed(self):
        base = {"entity_id": SWITCH, "action": "turn_on",
                "operation_id": f"{int(self.clock().timestamp())}-{uuid.uuid4().hex}"}
        cases = [
            ({**base, "service": "toggle"}, "device_configuration_field_not_supported"),
            ({**base, "action": "toggle"}, "device_configuration_action_not_supported"),
            ({**base, "action": "select_option", "value": "on"},
             "device_configuration_action_not_supported"),
            ({**base, "value": "on"}, "device_configuration_invalid_value"),
            ({**base, "entity_id": NUMBER, "action": "set_value", "value": "1e3"},
             "device_configuration_invalid_value"),
            ({**base, "entity_id": NUMBER, "action": "set_value", "value": "-0"},
             "device_configuration_invalid_value"),
            ({**base, "entity_id": NUMBER, "action": "set_value", "value": "0.1000001"},
             "device_configuration_invalid_value"),
            ({**base, "entity_id": "light.kitchen"}, "device_configuration_invalid_value"),
            ({**base, "action": "undo"}, "device_configuration_invalid_value"),
            ({**base, "entity_id": SELECT, "action": "select_option", "value": "on\x00"},
             "device_configuration_invalid_value"),
        ]
        for raw, code in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(DeviceConfigurationRefusal) as raised:
                    DeviceConfigurationRequest.parse(raw)
                self.assertEqual(category(raised.exception), code)

    def test_never_sent_services_are_not_constructible(self):
        for entity_id, domain, value in [(SWITCH, "switch", "toggle"),
                                         ("button.x", "button", "press"),
                                         (SELECT, "number", "1"), (NUMBER, "number", "1e3")]:
            with self.assertRaises(DeviceConfigurationRefusal):
                service_call(entity_id, domain, value)
        self.assertIn("switch.toggle", NEVER_SENT)
        self.assertIn("return_response:true", NEVER_SENT)

    # --- Target-proof refusals before any task ---------------------------

    async def test_target_proof_refusals_leave_no_task_and_no_write(self):
        entity = self.ha.entities[SELECT]["registry"]
        cases = [
            (lambda: entity.update(entity_category=None), "device_configuration_not_config_category"),
            (lambda: entity.update(entity_category="diagnostic"),
             "device_configuration_not_config_category"),
            (lambda: entity.update(device_id=None), "device_configuration_no_device"),
            (lambda: entity.update(platform="tplink"),
             "device_configuration_target_kind_not_admitted"),
            (lambda: entity.update(translation_key=None),
             "device_configuration_target_kind_not_admitted"),
            (lambda: entity.update(translation_key="power_on_behavior"),
             "device_configuration_target_kind_not_admitted"),
            (lambda: entity.update(disabled_by="user"), "device_configuration_target_disabled"),
            (lambda: self.ha.devices[0].update(disabled_by="user"),
             "device_configuration_target_disabled"),
            (lambda: self.ha.entities[SELECT].update(state="unavailable"),
             "device_configuration_entity_unavailable"),
            (lambda: self.ha.entities[SELECT]["attributes"].update(options=["on"]),
             "device_configuration_baseline_not_restorable"),
        ]
        original_entity, original_device = deepcopy(entity), deepcopy(self.ha.devices[0])
        original_state = deepcopy(self.ha.entities[SELECT])
        for change, code in cases:
            with self.subTest(code=code):
                change()
                await self.refused(self.request(), code)
                entity.clear(); entity.update(deepcopy(original_entity))
                self.ha.devices[0] = deepcopy(original_device)
                self.ha.entities[SELECT].update(deepcopy({k: v for k, v in original_state.items()
                                                          if k != "registry"}))
        await self.refused(self.request("select.unknown"), "device_configuration_entity_not_registered")
        self.assertEqual(self.ha.writes, [])
        self.settled()

    async def test_value_refusals_never_round_snap_or_clamp(self):
        await self.refused(self.request(SELECT, "select_option", "toggle_twice"),
                           "device_configuration_value_not_offered")
        await self.refused(self.request(NUMBER, "set_value", "4"),
                           "device_configuration_number_out_of_range")
        await self.refused(self.request(NUMBER, "set_value", "2.5"),
                           "device_configuration_number_step_misaligned")
        self.assertEqual(self.ha.writes, [])

    async def test_number_unit_conversion_and_bounds_refuse(self):
        reg = self.ha.entities[NUMBER]["registry"]
        reg["original_device_class"] = "temperature"
        await self.refused(self.request(NUMBER, "set_value", "3"),
                           "device_configuration_number_unit_conversion")
        reg["original_device_class"] = "distance"
        reg["options"] = {"number": {"unit_of_measurement": "cm"}}
        await self.refused(self.request(NUMBER, "set_value", "3"),
                           "device_configuration_number_unit_conversion")
        reg["original_device_class"], reg["options"] = None, {}
        reg["capabilities"] = {"min": 1, "max": 5, "step": 1, "mode": "slider"}
        await self.refused(self.request(NUMBER, "set_value", "3"),
                           "device_configuration_number_bounds_inconsistent")
        self.assertEqual(self.ha.writes, [])

    # --- Invalid authority ----------------------------------------------

    async def test_no_connector_binding_never_reaches_home_assistant(self):
        with self.assertRaises(DeviceConfigurationRefusal) as raised:
            await self.service.control(self.request())
        self.assertEqual(category(raised.exception),
                         "device_configuration_connector_authorization_required")
        self.assertEqual(self.ha.commands, [])
        self.assertEqual(self.ha.rest, [])

    async def test_unsigned_or_withheld_core_refuses_before_io(self):
        for change in ({"signed": False}, {"allowed": False}):
            with self.subTest(change=change):
                for key, value in change.items():
                    setattr(self.core, key, value)
                exc = await self.refused(self.request(),
                                         "device_configuration_core_authority_unavailable")
                self.assertEqual(exc.code, ErrorCode.PROVIDER_UNAVAILABLE)
                self.assertEqual(exc.details["status"], "provider_unavailable")
                self.core.signed = self.core.allowed = True
        self.assertEqual(self.ha.commands, [])
        self.settled()

    def test_signed_binding_shape_is_exact(self):
        good = {"version": CORE, "contract_fingerprint": PROFILE.contract_fingerprint}
        self.assertEqual(adapter_module.checked_binding(good), good)
        for bad in (None, {"version": CORE}, {**good, "contract_fingerprint": "sha256:" + "0" * 64},
                    {**good, "version": "2026.11"}, {**good, "extra": 1}):
            with self.assertRaises(ValueError):
                adapter_module.checked_binding(bad)

    async def test_core_retired_inside_final_check_never_writes(self):
        def retire(kind, count):
            if kind == "state" and count == 3:
                self.core.allowed = False
        self.ha.on_read = retire
        result = await self.call(self.request())
        self.assertEqual(self.ha.writes, [])
        self.assertEqual(result["status"], "refused", result)
        self.assertEqual(result["reason"], "device_configuration_core_authority_retired")
        self.assertFalse(result["dispatch_intent_recorded"])

    async def test_gateway_authority_retired_before_intent_never_writes(self):
        def retire(kind, count):
            if kind == "state" and count == 3:
                self.telemetry.ordinary_device_configuration_binding = None
        self.ha.on_read = retire
        result = await self.call(self.request())
        self.assertEqual(self.ha.writes, [])
        self.assertNotEqual(result["status"], "succeeded_verified")
        self.assertFalse(result["dispatch_intent_recorded"])

    # --- Stale state and provider drift (D1, N12) -------------------------

    async def test_n12_state_changed_in_final_check_reaches_caller(self):
        def change(kind, count):
            if kind == "state" and count == 3:  # prepare, preflight, once()
                self.ha.touch(SELECT)
        self.ha.on_read = change
        result = await self.call(self.request())
        self.assertEqual(self.ha.writes, [])
        self.assertEqual(result["terminal_outcome"], "failed_pre_dispatch", result)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["reason"], "device_configuration_state_changed")
        self.assertFalse(result["dispatch_intent_recorded"])
        self.settled()

    async def test_kind_drift_in_final_check_is_state_changed(self):
        def drift(kind, count):
            if kind == "registry" and count == 3:
                self.ha.entities[SELECT]["registry"]["translation_key"] = "other"
        self.ha.on_read = drift
        result = await self.call(self.request())
        self.assertEqual(self.ha.writes, [])
        self.assertEqual(result["reason"], "device_configuration_state_changed")

    async def test_identity_drift_before_preflight_refuses(self):
        def drift(kind, count):
            if kind == "registry" and count == 2:
                self.ha.entities[SELECT]["registry"]["unique_id"] = "replaced-device"
        self.ha.on_read = drift
        result = await self.call(self.request())
        self.assertEqual(self.ha.writes, [])
        self.assertEqual(result["terminal_outcome"], "preflight_rejected", result)
        self.assertEqual(result["reason"], "device_configuration_state_changed")

    async def test_missing_dispatch_boundary_stays_dispatch_refused(self):
        request = self.request()
        self.telemetry.ordinary_device_configuration_binding = digest(request.model_dump())
        prepared = await self.service.adapter.inner.prepare(request)
        inner = self.service.adapter.inner

        async def no_boundary(entity_id, domain, value, once):
            return "sha256:" + "0" * 64
        with patch.object(inner.provider, "dispatch", no_boundary):
            with self.assertRaises(DeviceConfigurationRefusal) as raised:
                await inner.dispatch(prepared, None, before_dispatch=self._noop)
        self.assertEqual(category(raised.exception), "device_configuration_dispatch_refused")
        self.assertEqual(self.ha.writes, [])

    async def _noop(self):
        return None

    async def test_d1_code_is_returned_not_raised(self):
        """Proof the D1 override is load-bearing: without it the code is lost."""
        request = self.request()
        self.telemetry.ordinary_device_configuration_binding = digest(request.model_dump())
        prepared = await self.service.adapter.inner.prepare(request)
        self.ha.touch(SELECT)
        result = await self.service.adapter.inner.dispatch(prepared, None,
                                                           before_dispatch=self._noop)
        self.assertEqual(result.diagnostic_codes, ("device_configuration_state_changed",))
        self.assertEqual(self.ha.writes, [])
        with patch.object(adapter_module, "PRE_SEND_REFUSALS", frozenset()):
            with self.assertRaises(DeviceConfigurationRefusal) as raised:
                await self.service.adapter.inner.dispatch(prepared, None,
                                                          before_dispatch=self._noop)
        self.assertEqual(category(raised.exception), "device_configuration_dispatch_refused")

    async def test_expired_operation_id_never_dispatches(self):
        for seconds in (-301, 31):
            request = self.request().model_copy(update={
                "operation_id": f"{int(self.clock().timestamp()) + seconds}-{uuid.uuid4().hex}"})
            with self.assertRaises(DeviceConfigurationRefusal) as raised:
                await self.call(request)
            self.assertEqual(category(raised.exception), "device_configuration_operation_id_expired")
        self.assertEqual(self.ha.writes, [])

    # --- Unsafe concurrency ----------------------------------------------

    async def test_held_entity_lock_is_lock_conflict_without_write(self):
        owner = LockOwner("synthetic-owner", "synthetic-other", None, "other", "synthetic-attempt")
        held = self.service.locks.acquire_once(
            (LockRequest("entity:" + SELECT, (LockScope.RESOURCE,), LockMode.EXCLUSIVE,
                         ("synthetic",)),),
            owner=owner, timing=LockTiming(120, 10, 0), now=self.clock())
        result = await self.call(self.request())
        self.assertEqual(result["terminal_outcome"], "lock_conflict", result)
        self.assertEqual(self.ha.writes, [])
        self.service.locks.release(held)
        self.settled()

    # --- Uncertain and unverifiable execution ----------------------------

    async def test_timeout_after_send_then_exact_readback_never_replays(self):
        self.ha.mode = "timeout_applied"
        request = self.request()
        result = await self.call(request)
        self.assertEqual(result["status"], "succeeded_verified", result)
        self.assertFalse(result["provider_response_received"])
        await self.call(request)
        self.assertEqual(len(self.ha.writes), 1)
        self.settled()

    async def test_call_not_sent_is_dispatch_failed_confirmed(self):
        self.ha.mode = "not_sent"
        result = await self.call(self.request())
        self.assertEqual(result["terminal_outcome"], "dispatch_failed_confirmed", result)
        self.assertEqual(result["status"], "provider_unavailable")
        self.assertEqual(self.ha.writes, [])

    async def test_rejected_after_send_is_never_success_or_replayed(self):
        self.ha.mode = "rejected"
        request = self.request()
        result = await self.call(request)
        self.assertNotEqual(result["status"], "succeeded_verified", result)
        self.assertTrue(result["dispatch_intent_recorded"])
        self.clock.advance(200)
        again = await self.service.reconcile(request.task_id)
        self.assertNotEqual(again["status"], "succeeded_verified")
        self.assertEqual(len(self.ha.writes), 1)

    async def test_ack_without_effect_is_changed_unverified_then_readonly_recovery(self):
        self.ha.mode = "ack_only"
        request = self.request()
        result = await self.call(request)
        self.assertNotEqual(result["status"], "succeeded_verified", result)
        self.assertEqual(result["undo"]["available"], False)
        self.ha.entities[SELECT]["state"] = "on"
        self.ha.touch(SELECT)
        self.clock.advance(121)
        result = await self.service.reconcile(request.task_id)
        self.assertEqual(len(self.ha.writes), 1)
        self.assertIn(result["status"], {"succeeded_verified", "changed_unverified",
                                         "verification_failed"})

    async def test_command_vocabulary_is_closed(self):
        await self.call(self.request())
        await self.call(self.request(NUMBER, "set_value", "1"))
        for command in self.ha.commands:
            if command["type"] == "call_service":
                self.assertIn(command["service"], SERVICES[command["domain"]])
                self.assertEqual(set(command) - {"service_data"},
                                 {"type", "domain", "service", "target"})
                self.assertEqual(set(command["target"]), {"entity_id"})
                self.assertEqual(sorted(command.get("service_data", {})),
                                 sorted(SERVICES[command["domain"]][command["service"]]))
            else:
                self.assertIn(command["type"], READ_COMMANDS)
        self.assertTrue(all(method == "GET" for method, _ in self.ha.rest))

    # --- Undo -------------------------------------------------------------

    async def undo(self, original, **kwargs):
        return self.request(SELECT, "undo", None, undo_task_id=original, **kwargs)

    async def test_undo_restores_prior_value_once(self):
        forward = await self.call(self.request())
        self.assertEqual(self.ha.entities[SELECT]["state"], "on")
        undo = await self.undo(forward["task_id"])
        result = await self.call(undo)
        self.assertEqual(result["status"], "succeeded_verified", result)
        self.assertIs(result["undone"], True)
        self.assertEqual(result["undo_of"], forward["task_id"])
        self.assertEqual(self.ha.entities[SELECT]["state"], "previous")
        self.assertEqual(self.service.receipt(forward["task_id"])["undo"]["reason"],
                         "device_configuration_undo_already_claimed")
        await self.refused(await self.undo(forward["task_id"]),
                           "device_configuration_undo_post_state_unproven")
        await self.refused(await self.undo(result["task_id"]),
                           "device_configuration_undo_not_eligible")
        self.assertEqual(len(self.ha.writes), 2)

    async def test_undo_refuses_after_external_change(self):
        forward = await self.call(self.request())
        self.ha.touch(SELECT)
        await self.refused(await self.undo(forward["task_id"]),
                           "device_configuration_undo_post_state_unproven")
        self.assertEqual(len(self.ha.writes), 1)

    async def test_undo_refuses_identity_change_unknown_task_and_other_entity(self):
        forward = await self.call(self.request())
        await self.refused(await self.undo("0" * 32), "device_configuration_undo_unavailable")
        other = self.request(SWITCH, "undo", None, undo_task_id=forward["task_id"])
        await self.refused(other, "device_configuration_undo_unavailable")
        self.ha.entities[SELECT]["registry"]["unique_id"] = "replacement"
        await self.refused(await self.undo(forward["task_id"]),
                           "device_configuration_undo_identity_changed")
        self.assertEqual(len(self.ha.writes), 1)

    async def test_corrupt_or_missing_sidecar_fails_closed(self):
        forward = await self.call(self.request())
        path = self.service._verified_path(forward["task_id"])
        path.write_text("{}")
        self.assertEqual(self.service.receipt(forward["task_id"])["undo"]["available"], False)
        await self.refused(await self.undo(forward["task_id"]),
                           "device_configuration_undo_unavailable")
        path.write_text("x" * 5000)
        await self.refused(await self.undo(forward["task_id"]),
                           "device_configuration_undo_unavailable")
        path.unlink()
        await self.refused(await self.undo(forward["task_id"]),
                           "device_configuration_undo_unavailable")
        self.assertEqual(len(self.ha.writes), 1)

    async def test_no_change_and_unverified_forward_are_not_undoable(self):
        noop = await self.call(self.request(SELECT, "select_option", "previous"))
        await self.refused(await self.undo(noop["task_id"]), "device_configuration_undo_unavailable")
        self.ha.mode = "ack_only"
        pending = await self.call(self.request())
        await self.refused(await self.undo(pending["task_id"]), "device_configuration_undo_unavailable")

    async def test_undo_claim_held_by_dispatched_undo_refuses_second_undo(self):
        forward = await self.call(self.request())
        self.ha.mode = "ack_only"
        first = await self.call(await self.undo(forward["task_id"]))
        self.assertTrue(first["dispatch_intent_recorded"])
        await self.refused(await self.undo(forward["task_id"]),
                           "device_configuration_undo_already_claimed")
        self.assertEqual(len(self.ha.writes), 2)

    async def test_undo_claim_released_by_proven_zero_dispatch(self):
        forward = await self.call(self.request())
        owner = LockOwner("synthetic-owner", "synthetic-other", None, "other", "synthetic-attempt")
        held = self.service.locks.acquire_once(
            (LockRequest("entity:" + SELECT, (LockScope.RESOURCE,), LockMode.EXCLUSIVE,
                         ("synthetic",)),),
            owner=owner, timing=LockTiming(120, 10, 0), now=self.clock())
        blocked = await self.call(await self.undo(forward["task_id"]))
        self.assertEqual(blocked["terminal_outcome"], "lock_conflict", blocked)
        self.service.locks.release(held)
        result = await self.call(await self.undo(forward["task_id"]))
        self.assertEqual(result["status"], "succeeded_verified", result)
        self.assertEqual(len(self.ha.writes), 2)

    async def test_r15_undo_claim_and_receipt_share_one_transaction(self):
        forward = await self.call(self.request())
        undo = await self.undo(forward["task_id"])
        self.telemetry.ordinary_device_configuration_binding = digest(undo.model_dump())
        prepared = await self.service.adapter.inner.prepare(undo)
        original = self.service.transaction
        entries = []

        def counting():
            entries.append(1)
            return original()
        with patch.object(self.service, "transaction", counting):
            self.service.save(prepared)
        self.assertEqual(len(entries), 1)
        self.assertTrue(self.service._claim_path(forward["task_id"]).exists())
        self.assertIsNotNone(self.service.load(undo.task_id))
        # A same-task retry after a crash keeps its own claim.
        self.service._path(undo.task_id).unlink()
        self.assertEqual(self.service.save(prepared), prepared)

    # --- Safety gate (Q8: empty production trigger; test-only trigger) ---

    async def test_production_runtime_has_no_trigger(self):
        runtime = type(DEVICE_CONFIGURATION_OPERATIONS)()
        runtime.configure(SimpleNamespace(governance_path=self.tmp.name), self.core, self.ha, self.ha)
        self.addAsyncCleanup(runtime.service.close)
        self.assertIsNone(runtime.service.safety_trigger)
        self.assertEqual(CONFIRMATION_REASONS, frozenset({CONFIRMATION_SAFETY_CRITICAL}))

    async def test_safety_critical_requires_chat_confirmation_and_creates_no_task(self):
        service = self.make_service(safety_trigger=lambda prepared: CONFIRMATION_SAFETY_CRITICAL)
        request = self.request()
        result = await self.call(request, service)
        self.assertEqual(result["status"], "confirmation_required", result)
        self.assertEqual(result["dispatch"], "no_dispatch")
        self.assertIs(result["task_created"], False)
        self.assertIs(result["external_approval"], False)
        self.assertIsNone(service.load(request.task_id))
        self.assertEqual(self.ha.writes, [])
        summary = request_summary(self.telemetry)
        self.assertEqual(summary["safety_gate"], "confirmation_required")
        self.assertEqual(summary["status"], "confirmation_required")

    async def test_acknowledged_safety_critical_change_still_dispatches(self):
        """Owner-authorized exact high-consequence operation remains actionable."""
        service = self.make_service(safety_trigger=lambda prepared: CONFIRMATION_SAFETY_CRITICAL)
        request = self.request(acknowledge_safety_critical=True)
        result = await self.call(request, service)
        self.assertEqual(result["status"], "succeeded_verified", result)
        self.assertEqual(result["safety_gate"], "acknowledged")
        self.assertEqual(result["safety_confirmation_reason"], CONFIRMATION_SAFETY_CRITICAL)
        self.assertEqual(len(self.ha.writes), 1)
        self.assertEqual(request_summary(self.telemetry)["safety_gate"], "acknowledged")

    async def test_invalid_trigger_reason_refuses_before_task(self):
        service = self.make_service(safety_trigger=lambda prepared: "made_up_reason")
        request = self.request(acknowledge_safety_critical=True)
        self.telemetry.ordinary_device_configuration_binding = digest(request.model_dump())
        with self.assertRaises(DeviceConfigurationRefusal) as raised:
            await service.control(request)
        self.assertEqual(category(raised.exception), "device_configuration_safety_gate_invalid")
        self.assertIsNone(service.load(request.task_id))
        self.assertEqual(self.ha.writes, [])

    # --- Hold-release review path and approval-page resolver -------------

    async def test_fresh_readback_never_dispatches(self):
        request = self.request()
        await self.call(request)
        prepared = self.service.load(request.task_id)
        state, matched, readback = await self.service._fresh_readback(prepared)
        self.assertEqual(state, {"requested": "on", "state": "on"})
        self.assertTrue(matched)
        self.assertRegex(readback, r"^[0-9a-f]{64}$")
        self.assertEqual(len(self.ha.writes), 1)

    def test_approval_resolver_routes_each_kind_explicitly(self):
        from ha_mcp_engineering import approval_web
        from ha_mcp_engineering.fan.service import FAN_OPERATIONS
        from ha_mcp_engineering.power.service import POWER_OPERATIONS
        app = approval_web.IngressApprovalApplication(object())
        sentinels = {name: object() for name in ("fan", "power", "device_configuration")}
        with patch.object(FAN_OPERATIONS, "service", sentinels["fan"]), \
                patch.object(POWER_OPERATIONS, "service", sentinels["power"]), \
                patch.object(DEVICE_CONFIGURATION_OPERATIONS, "service",
                             sentinels["device_configuration"]):
            for kind, sentinel in sentinels.items():
                self.assertIs(app._typed_service(kind), sentinel)
            for kind in ("helper_value", "unknown", "Device_Configuration"):
                self.assertIsNone(app._typed_service(kind))
        self.assertIn("device_configuration", approval_web.TYPED_HOLD_KINDS)

    # --- T6: the compiled allowlist and signed profile --------------------

    def test_t6_allowlist_and_profile_contract(self):
        self.assertEqual(ALLOWLIST, (("number", "matter", "sensitivity_level"),
                                     ("select", "matter", "startup_on_off"),
                                     ("switch", "matter", "child_lock")))
        # The compiled allowlist is part of the signed profile material: any
        # change to it changes this fingerprint (Q9).
        self.assertEqual(PROFILE.capability_id, "core.device_configuration_entity_operation")
        self.assertEqual(PROFILE.contract_fingerprint, "sha256:74a03a640f60c9adaf8508a0a7fcbca"
                                                       "7745f29b3d09e1dc9a611ac361475293c")
        self.assertEqual(len(UNIT_CONVERTERS), 41)
        self.assertEqual(len(set(UNIT_CONVERTERS)), 41)
        self.assertEqual(list(UNIT_CONVERTERS), sorted(UNIT_CONVERTERS))

if __name__ == "__main__":
    unittest.main()
