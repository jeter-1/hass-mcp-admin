"""RC4-DR-1: integration identifier text is not a registry ID.

The 4/149 string lengths and pair shape come from the sanitized 2026-09-09
probe (receipt SHA256 f061c2343a66438135eeae33ee119480202e37ef061f0dd66f1a00c2abc9a086).
All values and surrounding records here are synthetic, using the existing
Core 2026.9 fixture; this is not a captured household registry.
Core authority: home-assistant/core@fc034572d0216a04ed40a07154394908a594dfed,
homeassistant/helpers/device_registry.py (string pairs and identifier collision
checks), and components/config/device_registry.py (complete list serialization).
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from tests import test_ha_core_2026_9_integration as core_tests
from tests import test_rc3_postcore_regressions as postcore
from tests import test_dependency_build_authority as dependency_tests
from ha_mcp_engineering.ha_core_readmission.device_registry import (
    MAX_DEVICE_ID_CHARS, MAX_DEVICE_RECORD_BYTES, MAX_DEVICE_RECORDS,
    assess_device_registry,
)
from ha_mcp_engineering.ha_core_readmission.models import canonical_json
from ha_mcp_engineering.audit import AuditLogger
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient
from ha_mcp_engineering.dependency.runtime import DependencyAnalysisRuntime
from ha_mcp_engineering.errors import HomeAssistantUnavailableError
from ha_mcp_engineering.routing import AuthenticatedMcpGateway
from ha_mcp_engineering.tools import compatibility


def registry():
    return core_tests._fixture(core_tests.DEVICE_FIXTURE)["devices"]


class IdentifierContractTests(unittest.TestCase):
    def test_observed_identifier_shape_is_accepted_without_modification(self):
        records = registry()
        records[0]["identifiers"] = [["test", "I" * 149]]
        original = deepcopy(records)
        result = assess_device_registry(records)
        self.assertTrue(result.complete, result.reason_code)
        self.assertEqual(result.record_count, len(records))
        self.assertEqual(result.child_count, 2)
        self.assertEqual(records, original)

    def test_valid_identifier_text_and_sequence_controls(self):
        for text in ("", "I" * 128, "I" * 129, "I" * 149, "I" * 10_000,
                     "界" * 149, " one\nline\t\x00\x7f "):
            for record in (0, 1):
                for part in (0, 1):
                    with self.subTest(length=len(text), record=record, part=part):
                        values = registry()
                        values[record]["identifiers"][0][part] = text
                        before = deepcopy(values)
                        result = assess_device_registry(values)
                        self.assertTrue(result.complete, result.reason_code)
                        self.assertEqual(result.record_count, len(values))
                        self.assertEqual(values, before)
        values = registry()
        values[0]["identifiers"] = (("test", "I" * 149),)
        self.assertTrue(assess_device_registry(values).complete)

    def test_invalid_pair_types_and_arity_are_refused(self):
        bad = (None, False, 12, "pair", b"pair", {}, [None], [False], [1],
               ["ab"], [[]], [["one"]], [["a", "b", "c"]], [[{}, "x"]],
               [[[], "x"]], [[None, "x"]], [[False, "x"]], [["x", 1]],
               [["x", b"bytes"]])
        for value in bad:
            with self.subTest(value=value):
                values = registry()
                values[0]["identifiers"] = value
                self.assertFalse(assess_device_registry(values).complete)

    def test_identifier_collection_bound_is_unchanged(self):
        for count, complete in ((0, True), (256, True), (257, False)):
            values = registry()
            values[0]["identifiers"] = [["test", str(i)] for i in range(count)]
            self.assertEqual(assess_device_registry(values).complete, complete)

    def test_registry_id_and_reference_bounds_remain_128(self):
        self.assertEqual(MAX_DEVICE_ID_CHARS, 128)
        for field in ("id", "config_entry_id", "config_subentry_id", "area_id",
                      "disabled_by", "primary_config_entry"):
            for value in ("", "X" * 129, "with\ncontrol", 1, False):
                with self.subTest(field=field, value=value):
                    values = registry()
                    values[0]["identifiers"] = [["test", "I" * 149]]
                    values[0][field] = value
                    self.assertFalse(assess_device_registry(values).complete)
        values = registry()
        values[0]["id"] = "R" * 128
        for child in values[1:]:
            child["parent_device_id"] = values[0]["id"]
        self.assertTrue(assess_device_registry(values).complete)
        values[1]["parent_device_id"] = "R" * 129
        self.assertEqual(assess_device_registry(values).reason_code,
                         "child_parent_identity_malformed")

    def test_connections_keep_their_original_validation(self):
        for value, complete in (([], True), ([["mac", "C" * 128]], True),
                                ([["mac", "C" * 129]], False),
                                ([["", "value"]], False),
                                ([["mac", "line\nvalue"]], False),
                                ([["mac", 12]], False), ([["mac"]], False),
                                ([["mac", "value"]] * 257, False)):
            with self.subTest(complete=complete):
                values = registry()
                values[0]["identifiers"] = [["test", "I" * 149]]
                values[0]["connections"] = value
                self.assertEqual(assess_device_registry(values).complete, complete)

    def test_whole_registry_duplicate_and_parent_checks_remain(self):
        for case, reason in (("duplicate", "device_identity_duplicate"),
                             ("missing", "child_parent_missing"),
                             ("self", "device_parent_cycle"),
                             ("cycle", "device_parent_cycle"),
                             ("chain", "child_parent_not_regular")):
            with self.subTest(case=case):
                values = registry()
                values[0]["identifiers"] = [["test", "I" * 149]]
                if case == "duplicate":
                    values[2]["id"] = values[1]["id"]
                elif case == "missing":
                    values[2]["parent_device_id"] = "synthetic-missing"
                elif case == "self":
                    values[2]["parent_device_id"] = values[2]["id"]
                elif case == "cycle":
                    values[1]["parent_device_id"] = values[2]["id"]
                    values[2]["parent_device_id"] = values[1]["id"]
                else:
                    values[2]["parent_device_id"] = values[1]["id"]
                result = assess_device_registry(values)
                self.assertFalse(result.complete)
                self.assertEqual(result.reason_code, reason)

    def test_later_malformed_record_is_not_omitted(self):
        values = registry()
        values[0]["identifiers"] = [["test", "I" * 149]]
        values[2]["identifiers"] = [["test", False]]
        result = assess_device_registry(values)
        self.assertFalse(result.complete)
        self.assertEqual(result.reason_code, "device_record_malformed")
        self.assertEqual(result.record_count, 2)

    def test_record_count_bound_still_precedes_field_validation(self):
        self.assertEqual(MAX_DEVICE_RECORDS, 4096)
        self.assertEqual(assess_device_registry([{}] * 4096).reason_code,
                         "device_identity_malformed")
        self.assertEqual(assess_device_registry([{}] * 4097).reason_code,
                         "device_registry_oversized_or_invalid")

    def test_exact_byte_limit_bounds_identifier_text_without_a_second_cap(self):
        self.assertEqual(MAX_DEVICE_RECORD_BYTES, 1_000_000)
        values = registry()
        values[0]["identifiers"] = [["test", ""]]
        space = MAX_DEVICE_RECORD_BYTES - len(canonical_json(
            values, maximum=MAX_DEVICE_RECORD_BYTES))
        values[0]["identifiers"][0][1] = "I" * space
        self.assertEqual(len(canonical_json(values, maximum=MAX_DEVICE_RECORD_BYTES)),
                         MAX_DEVICE_RECORD_BYTES)
        before = deepcopy(values)
        self.assertTrue(assess_device_registry(values).complete)
        self.assertEqual(values, before)
        values[0]["identifiers"][0][1] += "I"
        self.assertEqual(assess_device_registry(values).reason_code,
                         "device_registry_oversized_or_invalid")


class IdentifierAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await postcore.PostCoreRegressions.asyncSetUp(self)
        self.network.records[0]["identifiers"] = [["test", "I" * 149]]

    async def asyncTearDown(self):
        await postcore.PostCoreRegressions.asyncTearDown(self)

    assert_settled = postcore.PostCoreRegressions.assert_settled
    assert_authority_round_trip = postcore.PostCoreRegressions.assert_authority_round_trip

    async def test_complete_probes_admit_all_three_profiles_on_reviewed_core(self):
        for version in ("2026.9.0", "2026.9.1"):
            with self.subTest(version=version):
                self.network.version = version
                before = deepcopy(self.network.records)
                health = await self.core.reconcile_once("synthetic_identifier")
                self.assertEqual(health["compatible_count"], 17)
                self.assertEqual(self.network.records, before)
                for capability in postcore.DEVICE_CAPABILITIES:
                    self.assert_authority_round_trip(capability)
                self.assert_authority_round_trip()

    async def test_invalid_evidence_withholds_only_device_profiles(self):
        for field, value in (("identifiers", [["test", False]]),
                             ("connections", [["mac", "C" * 129]]),
                             ("id", "D" * 129)):
            with self.subTest(field=field):
                self.network.records = registry()
                self.network.records[0]["identifiers"] = [["test", "I" * 149]]
                self.network.records[0][field] = value
                health = await self.core.reconcile_once("synthetic_invalid_identifier")
                self.assertEqual(health["compatible_count"], 14)
                missing = {p["capability_id"] for p in health["authority_profiles"]
                           if p["reason_code"] == "capability_evidence_missing"}
                self.assertEqual(missing, postcore.DEVICE_CAPABILITIES)
                for capability in missing:
                    self.assertIsNone(self.core.acquire((capability,)))
                self.assert_authority_round_trip()

    async def test_useful_delegated_reads_and_exact_catalog_recover(self):
        await self.core.reconcile_once("synthetic_identifier")
        reviewed = core_tests.load_reviewed_upstream_release_registry()
        release = reviewed.by_version["8.4.3"]
        capture = core_tests._capture_for_release(release)
        review = core_tests._fixture(core_tests.ROOT / release.artifact_evidence_resource)
        by_name = {item["name"]: item for item in capture["tools"]}
        transport = core_tests.FakeTransport(
            [by_name[name] for name in review["runtime_catalog"]["runtime_tool_order"]],
            version="8.4.3")
        gateway = core_tests.UpstreamReadGateway()
        gateway.configure(self.settings, transport=transport,
                          release_registry=reviewed, core_runtime=self.core)
        server = postcore.FastMCP("synthetic-rc4-identifiers")
        static = postcore.registered_tools(postcore.get_registered_server())
        postcore.McpSdkToolRegistry(server).replace(
            {name: static[name] for name in postcore.ENGINEERING_STATIC_TOOL_NAMES})
        await gateway.initialize(server)
        baseline = {tool.name: tool.model_dump(mode="json")
                    for tool in await server.list_tools()}
        self.assertEqual(len(baseline), 76)
        self.assertNotIn("ha_get_operation_status", baseline)
        cases = {"ha_get_device": {"device_id": "parent-garage"},
                 "ha_get_entity": {"entity_id": "cover.synthetic_garage_door"},
                 "ha_get_entity_exposure": {"entity_id": "cover.synthetic_garage_door"},
                 "ha_get_overview": {}, "ha_search": {"query": "synthetic"}}
        with patch.object(self.core, "finish", wraps=self.core.finish) as finish:
            for name, args in cases.items():
                transport.result = {"content": [{"type": "text", "text": json.dumps(
                    {"success": True, "entity_id": "cover.synthetic_garage_door",
                     "device_id": "child-door", "parent_device_id": "parent-garage",
                     "area_id": "garage"})}], "isError": False}
                result = json.loads(await postcore.registered_tools(server)[name].run(args))
                self.assertTrue(result["success"], result)
                self.assertTrue(core_tests._contains_projection(result["data"], "child-door"))
                self.assertEqual(transport.calls[-1][0], name)
                self.assert_settled()
            self.assertEqual(finish.call_count, len(cases))
        calls = len(transport.calls)
        self.network.records[2]["identifiers"] = [["test", 1]]
        await self.core.reconcile_once("synthetic_later_rejection")
        await gateway.initialize(server)
        self.assertEqual(len(postcore.registered_tools(server)), 71)
        self.assertEqual(len(transport.calls), calls)
        result = json.loads(await postcore.registered_tools(server)["ha_get_state"].run(
            {"entity_id": "sensor.synthetic"}))
        self.assertTrue(result["success"])
        self.network.records[2]["identifiers"] = [["test", "I" * 149]]
        await self.core.reconcile_once("synthetic_restore_identifiers")
        await gateway.initialize(server)
        self.assertEqual({tool.name: tool.model_dump(mode="json")
                          for tool in await server.list_tools()}, baseline)
        self.assertEqual(gateway.health_snapshot()["fallback_count"], 0)

    async def test_native_device_read_retains_dispatch_authority(self):
        await self.core.reconcile_once("synthetic_identifier")
        network = dependency_tests._Network()
        network.payloads["config/device_registry/list"] = self.network.records
        client = HomeAssistantWebSocketClient(self.settings)

        async def app(_scope, receive, send):
            await receive()
            value = await compatibility.list_devices(limit=3)
            body = json.dumps({"jsonrpc": "2.0", "id": "core-route-test",
                               "result": {"content": [{"type": "text", "text": value}],
                                          "isError": False}}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body, "more_body": False})

        gateway = AuthenticatedMcpGateway(app, self.settings,
            AuditLogger("unused", self.settings.access_secret, enabled=False),
            core_runtime=self.core)
        with patch.object(compatibility, "WEBSOCKET_CLIENT", client), patch(
                "ha_mcp_engineering.clients.websocket.aiohttp.ClientSession",
                side_effect=network.session), patch.object(
                self.core, "finish", wraps=self.core.finish) as finish:
            body = await core_tests.Core20269StaticRouteTests._call(gateway, "list_devices")
            value = json.loads(json.loads(body)["result"]["content"][0]["text"])
            self.assertIn("parent-garage", json.dumps(value))
            self.assertEqual(network.calls, ["config/device_registry/list"])
            self.assertEqual(finish.call_count, 1)
        for context in network.telemetry:
            self.assertFalse(context.authorize_core_dispatch())
        self.assert_settled()

    async def test_dependency_prewarm_and_refresh_use_owned_authority(self):
        await self.core.reconcile_once("synthetic_identifier")
        network = dependency_tests._Network()
        network.payloads["/config"] = {"version": "2026.9.1"}
        runtime = DependencyAnalysisRuntime()
        runtime.configure(HomeAssistantRestClient(self.settings),
                          HomeAssistantWebSocketClient(self.settings), core_runtime=self.core)
        self.addAsyncCleanup(runtime.shutdown)
        with patch("ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
                   side_effect=network.session), patch.object(
                   self.core, "finish", wraps=self.core.finish) as finish, patch.object(
                   self.core, "revalidate", wraps=self.core.revalidate) as revalidate:
            task = runtime.start_prewarm(startup_delay_seconds=0)
            self.assertTrue(await asyncio.wait_for(task, 3))
            index = runtime.require().index
            self.assertEqual(runtime.health()["prewarm_state"], "complete")
            first, _, _ = await index.get()
            second, _, _ = await index.get(refresh=True)
            self.assertGreater(second.generation, first.generation)
            self.assertTrue(index.active_identity()["current"])
            # Prewarm connectivity, the initial shared build, and the forced
            # refresh each own a distinct scope; every consumed lease finishes once.
            finished = [tuple(commit.lease for commit in call.args[0])
                        for call in finish.call_args_list]
            self.assertEqual(len(finished), 3)
            for leases in finished:
                self.assertEqual(finished.count(leases), 1)
            self.assertGreaterEqual(revalidate.call_count, len(network.calls))
            self.assertTrue(all(t.tool_name == "dependency_index_build" for t in network.telemetry))
            for context in network.telemetry:
                self.assertFalse(context.authorize_core_dispatch())
        self.assert_settled()
        self.network.records[2]["identifiers"] = [["test", False]]
        await self.core.reconcile_once("synthetic_later_invalid_identifier")
        calls = len(network.calls)
        with patch("ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
                   side_effect=network.session):
            with self.assertRaises(HomeAssistantUnavailableError):
                await runtime.require().index.get()
        self.assertEqual(len(network.calls), calls)
        self.assert_authority_round_trip()

    async def test_identifier_correction_does_not_admit_unreviewed_core(self):
        self.network.version = "2026.9.2"
        await self.core.reconcile_once("synthetic_unreviewed_patch")
        for capability in postcore.DEVICE_CAPABILITIES:
            self.assertIsNone(self.core.acquire((capability,)))
