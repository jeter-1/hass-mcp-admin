"""Offline tests for the HAMCP-231 A2 success-lane harness (run pending HAMCP-297's stub).

The lane itself has not run: the committed stub path, hash and canary literals
come from HAMCP-297. These tests prove the harness refuses while the stub
contract is pending and that its assertions accept the specified stub reply
and reject a recorded ``refresh`` other than the literal ``False``, an extra
stub command, a leaked canary, an unresolved ``ha_device_id`` or a raw edge.
Canary values are parameters made at test time, never the stub's literals.
"""
from copy import deepcopy
import json
from pathlib import Path
import secrets
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "scripts"), str(ROOT / "tests"), str(ROOT)]

import core_thread_matter_topology_a2_local as lane  # noqa: E402
import core_thread_matter_topology_local as a1  # noqa: E402
import test_hamcp231_thread_matter_topology as base  # noqa: E402
import thread_matter_topology_a2_acceptance as a2  # noqa: E402
from ha_mcp_engineering import thread_matter_topology as tm  # noqa: E402
from thread_matter_topology_contract_acceptance import leaks  # noqa: E402

FABRIC = "00000000000000AB"


def canaries():
    """Parameterized stand-ins for the stub's canary literals (ext_address, BSSID, SSID, host, key)."""
    return {
        "ext_address": secrets.token_hex(8),
        "ext_address_2": secrets.token_hex(8),
        "bssid": ":".join(secrets.token_hex(1) for _ in range(6)),
        "ssid": "canary-ssid-" + secrets.token_hex(4),
        "host_name": "canary-host-" + secrets.token_hex(4),
        "key": secrets.token_hex(16),
    }


def device_id(node_id):
    return f"{node_id:032x}"[::-1]


def stub_reply(values):
    """The specified stub NetworkTopology, as Core 2026.11.0 serializes it with ha_device_id."""
    border = values["ext_address"]
    unknown = values["ext_address_2"]
    bssid = tm.normalize_identifier(values["bssid"])
    return {
        "collected_at": 1_793_000_000_000,
        "nodes": [
            base.node(f"br_{border}", "border_router", "thread", ext_address=border, rloc16=0x0400,
                      ext_pan_id=base.EXT_PAN_ID, network_name="StubMesh", host_name=values["host_name"],
                      role="leader", available=True, vendor_name="Stub OTBR " + values["key"]),
            base.node("61", "matter", "thread", node_id=61, role="router", available=True, rloc16=0x0401,
                      ext_address=secrets.token_hex(8), ext_pan_id=base.EXT_PAN_ID, network_name="StubMesh",
                      vendor_name="Eve Systems", model_name="Eve Energy", ha_device_id=device_id(61)),
            base.node("199", "matter", "thread", node_id=199, role="end_device", available=True, rloc16=0x0402,
                      ext_address=secrets.token_hex(8), ext_pan_id=base.EXT_PAN_ID, network_name="StubMesh",
                      vendor_name="Eve Systems", model_name="Eve Energy 20ECN4101", ha_device_id=device_id(199)),
            base.node("91", "matter", "wifi", node_id=91, available=True, ssid=values["ssid"],
                      bssid=values["bssid"], vendor_name="Aqara", model_name="Door and Window Sensor P2",
                      ha_device_id=device_id(91)),
            base.node(f"unknown_{unknown}", "thread_unknown", "thread", ext_address=unknown,
                      network_name="StubMesh"),
            base.node("ap_" + bssid, "wifi_ap", "wifi", bssid=values["bssid"], ssid=values["ssid"],
                      network_name=values["ssid"]),
        ],
        "connections": [
            base.link("61", f"br_{border}"),
            base.link("199", "61", strength="medium", path_cost=2),
            base.link(f"unknown_{unknown}", "61", strength="weak"),
            base.link("91", "ap_" + bssid, "wifi", source_to_target=None, target_to_source=None,
                      via_route_table=None, path_cost=None),
            base.link("61", "br_" + secrets.token_hex(8)),  # ghost edge
        ],
    }


def projected(reply):
    return tm.build_result(base.outcomes(topology=reply, sections=(tm.TOPOLOGY,)))


def registry():
    return [{"id": device_id(node_id), "identifiers": [["matter", f"deviceid_{FABRIC}-{node_id:016X}-MatterNodeDevice"]]}
            for node_id in (61, 91, 199)] + [{"id": "a" * 32, "identifiers": [["thread", "x"]]}]


EXPECTED = json.loads((ROOT / "docs/evidence/hamcp231/lane/a2-stub-expectations.json").read_text())["expected"]


def record(*commands):
    return [json.dumps({"t": index, "kind": "command", "command": name, "args": args})
            for index, (name, args) in enumerate(commands)]


class ExpectationsPendingTests(unittest.TestCase):
    def test_committed_expectations_are_pending_and_the_lane_refuses(self):
        spec = json.loads(lane.EXPECTATIONS.read_text())
        self.assertEqual(spec["status"], "pending_hamcp297")
        self.assertEqual(spec["matter_python_client"], "1.5.5")
        with self.assertRaises(a1.Refusal) as caught:
            lane.expectations()
        self.assertEqual(str(caught.exception), "a2_stub_pending")

    def test_pending_refuses_before_inputs_images_or_containers(self):
        calls = []

        def command(*args, **_kwargs):
            calls.append(args)
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with mock.patch.object(lane, "command", command), \
                mock.patch.object(a1, "inputs", side_effect=AssertionError("inputs")) as inputs, \
                mock.patch.object(a1, "images", side_effect=AssertionError("images")):
            with self.assertRaises(a1.Refusal) as caught:
                lane._main(["--mode", "local", "--pull", "never", "--journal", "j", "--source-archive", "a",
                            "--run-dir", str(ROOT / ".artifacts/never-created")])
        self.assertEqual(str(caught.exception), "a2_stub_pending")
        inputs.assert_not_called()
        self.assertFalse(any(args[0] == "docker" for args in calls))
        self.assertFalse((ROOT / ".artifacts/never-created").exists())

    def test_ready_requires_stub_identity_and_canaries(self):
        spec = json.loads(lane.EXPECTATIONS.read_text())
        ready = deepcopy(spec)
        ready["status"] = "ready"
        ready["stub"].update(path="x/stub.py", sha256="0" * 64, nodes_path="x/nodes", nodes_sha256="1" * 64)
        with self.assertRaises(a2.ExpectationsPending) as caught:
            a2.ready_expectations(ready)
        self.assertEqual(str(caught.exception), "a2_canaries_pending")
        ready["canaries"] = list(canaries().values())
        self.assertIs(a2.ready_expectations(ready), ready)
        for key in ("path", "sha256", "nodes_path", "nodes_sha256"):
            with self.subTest(missing=key):
                broken = deepcopy(ready)
                broken["stub"][key] = None
                with self.assertRaises(a2.ExpectationsPending):
                    a2.ready_expectations(broken)

    def test_lane_is_distinct_from_a1_and_never_publishes_the_stub(self):
        self.assertNotEqual(lane.PORT, a1.PORT)
        self.assertNotEqual(lane.CORE, a1.CORE)
        self.assertNotEqual(lane.NETWORK, a1.NETWORK)
        source = Path(lane.__file__).read_text()
        stub_run = source[source.index('"stub_start"'):source.index('"core_start"')]
        self.assertNotIn("--publish", stub_run)
        self.assertIn("readonly", stub_run)
        # The stub is consumed by committed path and hash, never copied into this lane.
        self.assertFalse(list((ROOT / "docs/evidence/hamcp231").rglob("stub*.py")))


class StubRecordTests(unittest.TestCase):
    startup = (("server_info", None), ("start_listening", None))

    def check(self, *commands, mark=2):
        return a2.check_stub_record(a2.stub_commands(record(*self.startup, *commands)), mark=mark,
                                    topology_reads=2, startup_commands=EXPECTED["startup_commands"])

    def test_recorded_refresh_is_the_literal_false(self):
        summary = self.check(("get_network_topology", {"refresh": False}),
                             ("get_network_topology", {"refresh": False}))
        self.assertEqual(summary["recorded_refresh"], [False, False])
        self.assertEqual(summary["engineering_topology_commands"], 2)

    def test_any_other_refresh_or_argument_fails(self):
        for args in ({"refresh": True}, {"refresh": 0}, {"refresh": None}, {}, None,
                     {"refresh": False, "extra": 1}):
            with self.subTest(args=args):
                with self.assertRaises(AssertionError):
                    self.check(("get_network_topology", {"refresh": False}), ("get_network_topology", args))

    def test_engineering_adds_only_topology_commands(self):
        for extra in ("remove_node", "commission_with_code", "ping_node", "interview_node", "subscribe"):
            with self.subTest(extra=extra):
                with self.assertRaises(AssertionError):
                    self.check(("get_network_topology", {"refresh": False}), (extra, {}),
                               ("get_network_topology", {"refresh": False}))
        with self.assertRaises(AssertionError):
            self.check(("get_network_topology", {"refresh": False}))

    def test_harness_raw_read_before_the_mark_is_also_checked(self):
        commands = a2.stub_commands(record(*self.startup, ("get_network_topology", {"refresh": True}),
                                           ("get_network_topology", {"refresh": False}),
                                           ("get_network_topology", {"refresh": False})))
        with self.assertRaises(AssertionError):
            a2.check_stub_record(commands, mark=3, topology_reads=2,
                                 startup_commands=EXPECTED["startup_commands"])
        with self.assertRaises(AssertionError):
            a2.check_stub_record(a2.stub_commands(record(("remove_node", {}), *self.startup)), mark=3,
                                 topology_reads=0, startup_commands=EXPECTED["startup_commands"])


class TopologySuccessTests(unittest.TestCase):
    def setUp(self):
        self.values = canaries()
        self.reply = stub_reply(self.values)
        self.result = projected(deepcopy(self.reply))
        self.devices = a2.registry_node_devices(registry())

    def test_registry_maps_node_devices_only(self):
        self.assertEqual(self.devices, {61: device_id(61), 91: device_id(91), 199: device_id(199)})
        with self.assertRaises(AssertionError):
            a2.registry_node_devices(registry() + registry()[:1])

    def test_specified_stub_reply_passes_with_opaque_ids_and_no_canary(self):
        summary = a2.check_topology_success(self.result[tm.TOPOLOGY], raw=self.reply, devices=self.devices,
                                            expected=EXPECTED)
        self.assertEqual(summary["matter_nodes"], [61, 91, 199])
        self.assertEqual(summary["ha_device_ids_resolved"], 3)
        self.assertEqual(summary["dropped_unresolved"], 1)
        self.assertEqual(leaks(self.result, tuple(self.values.values())), [])
        self.assertEqual(self.result["identifier_policy"], "opaque_per_response_v1")

    def test_unresolved_or_wrong_ha_device_id_fails(self):
        devices = {**self.devices, 199: "b" * 32}
        with self.assertRaises(AssertionError):
            a2.check_topology_success(self.result[tm.TOPOLOGY], raw=self.reply, devices=devices, expected=EXPECTED)
        reply = deepcopy(self.reply)
        reply["nodes"][2]["ha_device_id"] = None
        with self.assertRaises(AssertionError):
            a2.check_topology_success(projected(deepcopy(reply))[tm.TOPOLOGY], raw=reply, devices=self.devices,
                                      expected=EXPECTED)

    def test_raw_edge_endpoint_or_missing_kind_fails(self):
        section = deepcopy(self.result[tm.TOPOLOGY])
        section["connections"][0]["target"] = "br_" + self.values["ext_address"]
        with self.assertRaises(AssertionError):
            a2.check_topology_success(section, raw=self.reply, devices=self.devices, expected=EXPECTED)
        reply = deepcopy(self.reply)
        del reply["nodes"][5]
        del reply["connections"][3]
        with self.assertRaises(AssertionError):
            a2.check_topology_success(projected(deepcopy(reply))[tm.TOPOLOGY], raw=reply, devices=self.devices,
                                      expected=EXPECTED)

    def test_a_leaked_canary_is_detected(self):
        leaked = deepcopy(self.result)
        leaked[tm.TOPOLOGY]["nodes"][0]["vendor_name"] = self.values["bssid"].upper()
        self.assertEqual(leaks(leaked, (self.values["bssid"],)), [0])


if __name__ == "__main__":
    unittest.main()
