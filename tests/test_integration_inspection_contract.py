"""Pure/native inspection contract checks, with unmistakably synthetic fixtures."""

import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.integration_inspection import contracts as c
from ha_mcp_engineering.integration_inspection.models import InspectionError, Inspection
from ha_mcp_engineering.integration_inspection.provider import AlarmoProvider
from ha_mcp_engineering.integration_inspection.service import IntegrationInspectionService
from ha_mcp_engineering.request_context import begin_request, end_request


FIXTURES = Path(__file__).parent / "fixtures/integration_inspection"
TARGET = "alarm_control_panel.synthetic_master"


def fixture(name="alarmo_positive.json"):
    return json.loads((FIXTURES / name).read_text())


class SyntheticCore:
    def __init__(self):
        self.available, self.generation = True, 1
        self.current_observation = SimpleNamespace(version="2026.9.3")

    def route_status(self, requirements):
        assert requirements == c.CORE_REQUIREMENTS
        return {"available": self.available, "generation": self.generation}


class SyntheticClient:
    def __init__(self, values=None):
        self.values = values if values is not None else fixture()
        self.calls = []
        self.hook = None

    async def read_alarmo_inspection(self, kind, **kwargs):
        self.calls.append((kind, kwargs))
        if self.hook:
            await self.hook(kind, kwargs)
        value = self.values[kind.value]
        if isinstance(value, Exception):
            raise value
        if kind is c.ReadKind.ENTITY_REGISTRY and isinstance(value, dict):
            value = {key: value.get(key) for key in kwargs["entity_ids"]}
        return copy.deepcopy(value), len(c.canonical(value))


def setup_service(values=None, *, limit=60_000, clock=None, known_secrets=()):
    client, core = SyntheticClient(values), SyntheticCore()
    provider = AlarmoProvider(client, core, known_secrets=known_secrets)
    service = IntegrationInspectionService(provider, response_limit=limit, **({"clock": clock} if clock else {}))
    telemetry, token = begin_request()
    telemetry.caller_id = "synthetic_authenticated_caller"
    telemetry.core_dispatch_authorizer = lambda: core.available
    return service, client, core, telemetry, token


def resolve(value, pointer):
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class InspectionContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_disposable_hook_rejects_missing_fixture_and_not_run(self):
        import ast
        import os
        from types import ModuleType
        from unittest.mock import AsyncMock, patch
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "scripts/real_ha_contract_tests.py").read_text())
        owner = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_core_2026_9_child_contract")
        block = next(n for n in ast.walk(owner) if isinstance(n, ast.If)
                     and isinstance(n.test, ast.Compare) and ast.unparse(n.test) == "EXPECTED_HA_VERSION == '2026.9.4'")
        wrapper = ast.AsyncFunctionDef(name="invoke", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
                                      body=[block], decorator_list=[])
        for receipt, result in ((None, "PASS"), (Path("synthetic.json"), "NOT_RUN"), (Path("synthetic.json"), "PASS")):
            module = ModuleType("alarmo_inspection_contract_acceptance")
            module.run_disposable = AsyncMock(return_value={"result": result})
            context = dict(EXPECTED_HA_VERSION="2026.9.4", configured=object(), core_runtime=object(),
                           _environment_path=lambda name: receipt, os=os)
            exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), "bounded-ci-hook", "exec"), context)
            with patch.dict(sys.modules, {module.__name__: module}):
                if receipt is None or result != "PASS":
                    with self.assertRaises(RuntimeError):
                        await context["invoke"]()
                else:
                    await context["invoke"]()
            self.assertEqual(module.run_disposable.await_count, int(receipt is not None))

    def test_fixture_source_identity_and_archive_are_bound_without_installed_claim(self):
        profile = fixture("alarmo_profile.json")
        self.assertEqual(profile["source_commit"], c.ALARMO_COMMIT)
        self.assertEqual(len(profile["archive_sha256"]), 64)
        self.assertTrue({"store.py", "websockets.py", "manifest.json"}.issubset(profile["component_files_sha256"]))
        self.assertNotIn("installed", profile["generation"])

    async def test_useful_master_two_areas_five_modes_disabled_and_unregistered(self):
        service, client, core, telemetry, token = setup_service()
        self.addCleanup(end_request, token)
        report = await service.inspect(alarm_entity_id=TARGET)
        self.assertEqual(report["assessment"], "complete")
        self.assertEqual(report["membership"]["total_in_scope"], 4)
        self.assertEqual(len(client.calls), 9)
        self.assertEqual(len([r for r in report["records"] if r["kind"] == "mode"]), 10)
        sensors = {r["configured_entity_id"]: r for r in report["records"] if r["kind"] == "sensor"}
        self.assertFalse(sensors["binary_sensor.synthetic_disabled"]["enabled"]["value"])
        self.assertEqual(sensors["binary_sensor.synthetic_unregistered"]["registry"]["status"], "not_in_registry")
        self.assertEqual(report["routing"]["data_providers"], ["direct_ha_api"])
        self.assertEqual(telemetry.upstream_request_count, 0)
        self.assertFalse(report["freshness"]["atomic_snapshot"])
        self.assertEqual(report["behavior_verification"], "not_performed")
        Inspection.model_validate(report)

    async def test_area_string_zero_is_not_master_and_group_preserves_other_area_member(self):
        service, client, _, _, token = setup_service()
        self.addCleanup(end_request, token)
        report = await service.inspect(alarm_entity_id="alarm_control_panel.synthetic_0")
        self.assertEqual(report["target"]["scope"], {"kind": "area", "id": "0"})
        self.assertEqual(report["membership"]["total_in_scope"], 2)
        group = next(r for r in report["records"] if r["kind"] == "sensor_group")
        self.assertTrue(group["includes_members_outside_target_area"])
        self.assertIn("binary_sensor.synthetic_always", group["configured_members"]["value"])
        ids = client.calls[-2][1]["entity_ids"]
        self.assertNotIn("binary_sensor.synthetic_always", ids)

    async def test_all_page_evidence_resolves_to_captured_source_fields(self):
        raw = fixture()
        service, _, _, _, token = setup_service(raw)
        self.addCleanup(end_request, token)
        report = await service.inspect(alarm_entity_id=TARGET, limit=50)
        for item in report["evidence_entries"]:
            self.assertEqual(resolve(raw[item["source_id"]], item["pointer"]), item["value"])

    async def test_unknown_version_stops_before_integration_callbacks(self):
        raw = fixture()
        raw["manifest"]["version"] = "99.0.0"
        service, client, _, _, token = setup_service(raw)
        self.addCleanup(end_request, token)
        with self.assertRaisesRegex(InspectionError, "") as found:
            await service.inspect(alarm_entity_id=TARGET)
        self.assertEqual(found.exception.reason, "integration_version_unsupported")
        self.assertEqual([k for k, _ in client.calls], [c.ReadKind.MANIFEST])

    async def test_missing_authority_zero_requests_and_no_partial_workaround(self):
        service, client, core, _, token = setup_service()
        self.addCleanup(end_request, token)
        core.available = False
        with self.assertRaises(InspectionError) as found:
            await service.inspect(alarm_entity_id=TARGET)
        self.assertEqual(found.exception.reason, "authority_unavailable")
        self.assertEqual(client.calls, [])

    async def test_missing_authority_public_response_has_bounded_reason_and_zero_reads(self):
        from unittest.mock import patch
        from ha_mcp_engineering.integration_inspection.runtime import INTEGRATION_INSPECTION
        from ha_mcp_engineering.tools.integration_inspection import registered_tool
        service, client, core, telemetry, token = setup_service()
        self.addCleanup(end_request, token)
        core.available = False
        with patch.object(INTEGRATION_INSPECTION, "service", service):
            denied = json.loads(await registered_tool().run({"alarm_entity_id": TARGET}))
        self.assertFalse(denied["success"])
        self.assertEqual(denied["details"]["reason"], "authority_unavailable")
        self.assertEqual(denied["error_code"], "provider_unavailable")
        self.assertEqual(client.calls, [])
        self.assertEqual(telemetry.ha_request_count, 0)

    async def test_target_failure_stops_dependent_commands(self):
        service, client, _, _, token = setup_service()
        self.addCleanup(end_request, token)
        with self.assertRaises(InspectionError) as found:
            await service.inspect(alarm_entity_id="alarm_control_panel.synthetic_missing")
        self.assertEqual(found.exception.reason, "target_not_found")
        self.assertEqual(len(client.calls), 3)

    async def test_ambiguous_entries_and_panels_refuse_before_dependent_reads(self):
        for source, expected_calls in (("config_entries", 2), ("alarm_entities", 3)):
            raw = fixture()
            if source == "config_entries":
                raw[source].append(dict(raw[source][0]))
            else:
                raw[source].append(next(dict(row) for row in raw[source] if row["entity_id"] == TARGET))
            service, client, _, _, token = setup_service(raw)
            try:
                with self.assertRaises(InspectionError) as found:
                    await service.inspect(alarm_entity_id=TARGET)
                self.assertEqual(found.exception.reason, "ambiguous_target")
                self.assertEqual(len(client.calls), expected_calls)
            finally:
                end_request(token)

    async def test_neighbouring_write_flow_event_and_storage_interfaces_are_not_used(self):
        from unittest.mock import Mock
        service, client, _, _, token = setup_service()
        self.addCleanup(end_request, token)
        forbidden = [Mock(side_effect=AssertionError("Forbidden synthetic side effect")) for _ in range(8)]
        for name, spy in zip(("command", "request", "call_service", "async_save", "async_update", "async_dispatcher_send", "create_plan", "apply"), forbidden):
            setattr(client, name, spy)
        report = await service.inspect(alarm_entity_id=TARGET)
        self.assertEqual(report["assessment"], "complete")
        for spy in forbidden:
            spy.assert_not_called()
        allowed = {"manifest/get", "config_entries/get", "alarmo/entities", "alarmo/areas", "alarmo/sensors", "alarmo/config", "alarmo/sensor_groups", "config/entity_registry/get_entries"}
        self.assertEqual({c.command_payload(k, args.get("entity_ids"))["type"] for k, args in client.calls}, allowed)

    def test_strict_arguments_reject_extra_keys_values_and_boolean_limit(self):
        good = {"alarm_entity_id": TARGET}
        c.validate_arguments(good)
        for change in ({"limit": True}, {"limit": "25"}, {"limit": 0}, {"limit": 51},
                       {"integration": "other"}, {"cursor": 1}, {"cursor": "a" * 2049},
                       {"alarm_entity_id": TARGET + " "}, {"endpoint": "SYNTHETIC_SECRET"}):
            with self.subTest(change=list(change)), self.assertRaisesRegex(ValueError, "^Invalid integration inspection arguments.$"):
                c.validate_arguments({**good, **change})

    def test_closed_commands_and_registry_validation(self):
        self.assertEqual(len(c.COMMANDS), 8)
        with self.assertRaises(ValueError):
            c.command_payload("alarmo/arm")
        with self.assertRaises(ValueError):
            c.command_payload(c.ReadKind.GENERAL, (TARGET,))
        with self.assertRaises(ValueError):
            c.command_payload(c.ReadKind.ENTITY_REGISTRY, tuple("binary_sensor.x" for _ in range(514)))
