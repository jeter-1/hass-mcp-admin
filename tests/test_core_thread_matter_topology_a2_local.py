"""Offline tests for the HAMCP-231 A2 lane harness on HAMCP-297's Matter Server stub.

The lane runs only locally against cached images. These tests prove the
harness pins the stub by exact file set and SHA-256 before any container,
uses an internal bridge with no published port, and that its assertions accept
a reply shaped like the stub's and reject a recorded ``refresh`` other than the
literal ``False``, an extra stub command, a leaked canary, an unresolved
``ha_device_id``, a raw edge, a changed kept-raw value or an unwithheld Wi-Fi
name. Canary values are parameters made at test time, never the stub's
literals; the stub itself is never copied into this branch.
"""
import contextlib
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "scripts"), str(ROOT / "tests"), str(ROOT)]

import core_thread_matter_topology_a2_local as lane  # noqa: E402
import core_thread_matter_topology_local as a1  # noqa: E402
import real_ha_contract_tests as harness  # noqa: E402
import test_hamcp231_thread_matter_topology as base  # noqa: E402
import thread_matter_topology_a2_acceptance as a2  # noqa: E402
from ha_mcp_engineering import thread_matter_topology as tm  # noqa: E402
from thread_matter_topology_contract_acceptance import leaks  # noqa: E402

FABRIC = "00000000000000AB"
SPEC = json.loads(lane.EXPECTATIONS.read_text())
EXPECTED = SPEC["expected"]
BACKGROUND = EXPECTED["background_commands"]


def hex_id():
    return secrets.token_hex(8).upper()


def canaries():
    """Parameterized stand-ins for the stub's canary literals."""
    return {
        "border": hex_id(), "unknown": hex_id(), "ghost": hex_id(), "ext_61": hex_id(), "ext_199": hex_id(),
        "bssid": ":".join(secrets.token_hex(1) for _ in range(6)).upper(),
        "ssid": "canary-ssid-" + secrets.token_hex(4),
        "host_name": "canary-host-" + secrets.token_hex(4),
        "key": secrets.token_hex(16),
    }


def kept_raw():
    # Upper-case hex, as the stub sends it; the projection emits it lowercased.
    return {"border_router": {"network_name": "StubMesh-" + secrets.token_hex(3),
                              "ext_pan_id": "DEADBEEF" + secrets.token_hex(4).upper(), "rloc16": 0x4400}}


def device_id(node_id):
    return f"{node_id:032x}"[::-1]


def stub_reply(values, kept):
    """A reply shaped like the HAMCP-297 stub's NetworkTopology, as Core 2026.11.0 serializes it."""
    router = kept["border_router"]
    mesh = dict(ext_pan_id=router["ext_pan_id"], network_name=router["network_name"])
    border, unknown = "br_" + values["border"], "thread_" + values["unknown"]
    ap = "ap_" + tm.normalize_identifier(values["bssid"]).lower()
    return {
        "collected_at": 1_793_000_000_000,
        "nodes": [
            base.node("61", "matter", "thread", node_id=61, role="router", available=True, rloc16=0x4401,
                      ext_address=values["ext_61"], vendor_name="Eve Systems", model_name="Eve Energy",
                      ha_device_id=device_id(61), **mesh),
            base.node("199", "matter", "thread", node_id=199, role="reed", available=True, rloc16=0x4800,
                      ext_address=values["ext_199"], vendor_name="Eve Systems", model_name="Eve Energy 20ECN4101",
                      ha_device_id=device_id(199), **mesh),
            base.node("91", "matter", "wifi", node_id=91, available=True, ssid=values["ssid"],
                      bssid=values["bssid"], network_name=values["ssid"], vendor_name="Aqara",
                      model_name="Door and Window Sensor P2", ha_device_id=device_id(91)),
            base.node(border, "border_router", "thread", ext_address=values["border"], rloc16=router["rloc16"],
                      host_name=values["host_name"], role="leader", available=True,
                      vendor_name="canary " + values["key"], model_name="Canary Border Router", **mesh),
            base.node(unknown, "thread_unknown", "thread", ext_address=values["unknown"], role="end_device"),
            base.node(ap, "wifi_ap", "wifi", bssid=values["bssid"], ssid=values["ssid"],
                      network_name=values["ssid"]),
        ],
        "connections": [
            base.link("61", border),
            base.link("199", "61", strength="medium", path_cost=2),
            base.link("199", unknown, strength="weak"),
            base.link("61", "thread_" + values["ghost"]),  # ghost edge: no such node
            base.link("91", ap, "wifi", source_to_target=None, target_to_source=None,
                      via_route_table=None, path_cost=None),
        ],
    }


def projected(reply):
    return tm.build_result(base.outcomes(topology=reply, sections=(tm.TOPOLOGY,)))


def registry():
    return [{"id": device_id(node_id), "identifiers": [["matter", f"deviceid_{FABRIC}-{node_id:016X}-MatterNodeDevice"]]}
            for node_id in (61, 91, 199)] + [{"id": "a" * 32, "identifiers": [["thread", "x"]]}]


def record(*commands):
    return [json.dumps({"t": index, "kind": "command", "command": name, "args": args})
            for index, (name, args) in enumerate(commands)]


def pinned_tree(directory):
    """A stand-in stub tree and the files map that pins it."""
    contents = {"stub.py": b"print('stand-in')\n", "provenance.json": b"{}\n", "nodes/a.json": b"{}\n"}
    for name, data in contents.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(data)
    files = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    return {**SPEC["stub"], "files": files}


class ExpectationsTests(unittest.TestCase):
    def test_committed_expectations_pin_the_hamcp297_stub(self):
        self.assertEqual(SPEC["model"], a2.EXPECTATIONS_MODEL)
        self.assertEqual(SPEC["status"], "ready")
        self.assertEqual(lane.expectations(), SPEC)
        stub = SPEC["stub"]
        self.assertEqual((stub["owner"], stub["source_pr"]), ("HAMCP-297", 428))
        self.assertEqual(stub["committed_dir"], "tests/fixtures/hamcp297_matter_server_stub")
        self.assertEqual(set(stub["files"]), {
            "driver.py", "provenance.json", "run_lane.sh", "stub.py", "nodes/aqara_door_window_p2.json",
            "nodes/eve_energy_20ecn4101.json", "nodes/eve_energy_plug.json"})
        self.assertEqual(EXPECTED["schema_fault_version"], 12)
        self.assertEqual(EXPECTED["topology_error_status"], "upstream_command_failed")

    def test_ready_requires_every_pin(self):
        def broken(change):
            spec = deepcopy(SPEC)
            change(spec)
            return spec
        cases = {
            "a2_expectations_model_mismatch": [lambda s: s.update(model="v1")],
            "a2_stub_pending": [
                lambda s: s.update(status="pending_hamcp297"),
                lambda s: s["stub"].update(committed_dir=None),
                lambda s: s["stub"]["files"].pop("stub.py"),
                lambda s: s["stub"]["files"].pop("provenance.json"),
                lambda s: s["stub"]["files"].update({"stub.py": "0" * 63}),
                lambda s: [s["stub"]["files"].pop(k) for k in list(s["stub"]["files"]) if k.startswith("nodes/")],
                lambda s: s["stub"].update(source_commit="aa25c564"),
            ],
            "a2_canaries_pending": [lambda s: s.update(canaries=SPEC["canaries"][:4]),
                                    lambda s: s["canaries"].append("short")],
            "a2_kept_raw_value_is_a_canary": [
                lambda s: s["canaries"].append(SPEC["kept_raw"]["border_router"]["network_name"])],
            "a2_expectations_incomplete": [lambda s: s["expected"].pop("schema_fault_status"),
                                           lambda s: s["expected"].update(nodes="6")],
        }
        for reason, changes in cases.items():
            for change in changes:
                with self.subTest(reason=reason), self.assertRaises(a2.ExpectationsPending) as caught:
                    a2.ready_expectations(broken(change))
                self.assertEqual(str(caught.exception), reason)


class StubPinTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".artifacts").mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=ROOT / ".artifacts")
        self.directory = Path(self.scratch.name).resolve()
        self.stub = pinned_tree(self.directory)

    def tearDown(self):
        self.scratch.cleanup()

    def refused(self, reason, directory=None, stub=None):
        with self.assertRaises(a1.Refusal) as caught:
            lane.verify_stub(self.directory if directory is None else directory, stub or self.stub)
        self.assertEqual(str(caught.exception), reason)

    def test_exact_pinned_tree_under_artifacts_is_accepted(self):
        identity = lane.verify_stub(self.directory, self.stub)
        self.assertEqual(identity["source"], "extracted_pinned_commit")
        self.assertEqual(identity["files_verified"], 3)

    def test_extra_missing_changed_or_linked_files_refuse(self):
        (self.directory / "nodes/extra.json").write_text("{}")
        self.refused("a2_stub_file_set_mismatch")
        (self.directory / "nodes/extra.json").unlink()
        (self.directory / "stub.py").write_text("print('changed')\n")
        self.refused("a2_stub_hash_mismatch")
        (self.directory / "stub.py").unlink()
        self.refused("a2_stub_file_set_mismatch")
        (self.directory / "stub.py").symlink_to(self.directory / "provenance.json")
        self.refused("a2_stub_hash_mismatch")

    def test_directory_outside_the_checkout_artifacts_refuses(self):
        # Location is checked before existence or content.
        self.refused("a2_stub_directory_not_committed_or_task_artifact", Path("/nonexistent-hamcp231/stub"))
        self.refused("a2_stub_directory_not_committed_or_task_artifact", ROOT / "scripts")
        self.refused("a2_stub_missing", self.directory / "absent")

    def test_unverified_stub_refuses_before_inputs_images_or_containers(self):
        calls = []

        def command(*args, **_kwargs):
            calls.append(args)
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")
        run_dir = ROOT / ".artifacts/never-created"
        argv = ["--mode", "local", "--pull", "never", "--journal", "j", "--source-archive", "a",
                "--run-dir", str(run_dir), "--stub-dir", str(self.directory / "absent")]
        # The closed-lane guard refuses first under CI or Evidence; clear it to reach the stub check.
        local = {k: v for k, v in os.environ.items() if k not in CLOSED_LANE_MARKERS}
        for environment, refusal in ((local, "a2_stub_missing"),
                                     ({**local, "GITHUB_ACTIONS": "true"}, "local_only_closed_lane"),
                                     ({**local, "CORE_EVIDENCE_CONTEXT": "1"}, "local_only_closed_lane")):
            calls.clear()
            with self.subTest(refusal=refusal, environment=sorted(set(environment) - set(local))), \
                    mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch.object(lane, "command", command), \
                    mock.patch.object(a1, "inputs", side_effect=AssertionError("inputs")) as inputs, \
                    mock.patch.object(a1, "images", side_effect=AssertionError("images")):
                with self.assertRaises(a1.Refusal) as caught:
                    lane._main(argv)
                self.assertEqual(str(caught.exception), refusal)
                inputs.assert_not_called()
                self.assertFalse(any(args[0] == "docker" for args in calls))
                self.assertFalse(run_dir.exists())


CLOSED_LANE_MARKERS = ("GITHUB_ACTIONS", "CORE_EVIDENCE_CONTEXT")


NETWORK_ID, OTHER_NETWORK_ID, RUN_ID = "ab" * 32, "cd" * 32, "run-364"
SUBNET, CORE_ADDRESS = "172.30.0.0/16", "172.30.0.2"


def created(**changes):
    """``docker network inspect`` of the network this run created, as Docker reports it."""
    return {"Name": lane.NETWORK, "Id": NETWORK_ID, "Driver": "bridge", "Internal": True,
            "Labels": {a1.LABEL: RUN_ID}, "IPAM": {"Config": [{"Subnet": SUBNET, "Gateway": "172.30.0.1"}]},
            **changes}


class HandOverTests(unittest.TestCase):
    """The harness learns this lane's Core only from the network this run created and inspected."""

    def setUp(self):
        state = mock.patch.multiple(harness, HA_URL=harness.HA_URL, CLIENT_ID=harness.CLIENT_ID, _lane_core=None)
        state.start()
        self.addCleanup(state.stop)
        self.before = (harness.HA_URL, harness.CLIENT_ID)
        self.commands = []

    def docker(self, network, address=CORE_ADDRESS, attached=NETWORK_ID, reply=None):
        """Stand-ins for the two Docker reads; any other command fails the test."""
        def command(*args, check=True):
            self.commands.append((args, check))
            if network is None:
                return mock.Mock(returncode=1, stdout=b"", stderr=b"synthetic: no such network")
            return mock.Mock(returncode=0, stdout=json.dumps([network]).encode() if reply is None else reply)
        container = {"NetworkSettings": {"Ports": {"8123/tcp": None}, "Networks": {
            lane.NETWORK: {"IPAddress": address, "NetworkID": attached}}}}
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(lane, "command", command))
        stack.enter_context(mock.patch.object(lane, "inspect_owned", return_value=container))
        return stack

    def refused(self, kind, reason, network_id=NETWORK_ID, **docker):
        accept = mock.Mock(side_effect=AssertionError("the harness was handed a Core"))
        with self.docker(**docker), mock.patch.object(harness, "use_internal_lane_core", accept):
            with self.assertRaises(kind) as caught:
                lane.hand_over_core(network_id, RUN_ID)
        self.assertEqual(caught.exception.args, (reason,))
        accept.assert_not_called()
        self.assertEqual((harness.HA_URL, harness.CLIENT_ID, harness._lane_core), (*self.before, None))

    def test_the_created_internal_network_hands_its_core_over(self):
        self.assertEqual(self.before, ("http://127.0.0.1:8123", "http://127.0.0.1:8123/"))
        with self.docker(created()):
            origin, record = lane.hand_over_core(NETWORK_ID, RUN_ID)
        self.assertEqual(origin, "http://172.30.0.2:8123")
        self.assertEqual(record, {"name": lane.NETWORK, "subnet": SUBNET, "target_address": CORE_ADDRESS})
        self.assertEqual((harness.HA_URL, harness.CLIENT_ID, harness._lane_core),
                         (origin, origin + "/", origin))
        # One inspect of the created id, by id, in this process; nothing else was run.
        self.assertEqual(self.commands, [(("docker", "network", "inspect", NETWORK_ID), False)])

    def test_a_network_that_is_not_internal_refuses(self):
        for internal in (False, None, "true", 1):
            with self.subTest(internal=internal):
                self.refused(a1.Refusal, "lane_requires_internal_bridge", network=created(Internal=internal))
        missing = created()
        del missing["Internal"]
        self.refused(a1.Refusal, "lane_requires_internal_bridge", network=missing)
        self.refused(a1.Refusal, "lane_requires_internal_bridge", network=created(Driver="macvlan"))

    def test_a_network_this_run_did_not_create_refuses(self):
        for network in (None, created(Id=OTHER_NETWORK_ID), created(Name="bridge"), created(Name="homelan"),
                        created(Labels={a1.LABEL: "another-run"}), created(Labels={}), created(Labels=None)):
            with self.subTest(network=network):
                self.refused(a1.Refusal, "lane_network_not_created_by_run", network=network)
        for reply in (b"", b"not json", b"[]", b"{}", json.dumps([created(), created()]).encode(),
                      json.dumps([[created()]]).encode()):
            with self.subTest(reply=reply):
                self.refused(a1.Refusal, "lane_network_not_created_by_run", network=created(), reply=reply)
        # Only the 64-hex id printed by this run's create is ever inspected: a name is not.
        for network_id in ("", lane.NETWORK, "bridge", "host", NETWORK_ID[:12], NETWORK_ID.upper(),
                           NETWORK_ID + "\n", None, 7):
            with self.subTest(network_id=network_id):
                self.commands.clear()
                self.refused(a1.Refusal, "lane_network_not_created_by_run", network_id=network_id,
                             network=created())
                self.assertEqual(self.commands, [])

    def test_a_lan_style_address_with_no_matching_created_network_refuses(self):
        # A private address on a household network is inside no network this run created.
        for lan in ("192.168.1.10", "10.0.0.5", "172.16.4.9", "192.168.0.1"):
            with self.subTest(address=lan):
                with self.docker(created(), address=lan):
                    with self.assertRaises(ValueError) as caught:
                        lane.hand_over_core(NETWORK_ID, RUN_ID)
                self.assertEqual(caught.exception.args, ("real_ha_lane_core_outside_subnet",))
                self.assertEqual((harness.HA_URL, harness.CLIENT_ID, harness._lane_core), (*self.before, None))
        # Nor does an existing LAN network qualify by having the right range.
        lan_network = created(Id=OTHER_NETWORK_ID, Name="homelan", Labels={}, Driver="macvlan", Internal=False,
                              IPAM={"Config": [{"Subnet": "192.168.1.0/24"}]})
        self.refused(a1.Refusal, "lane_network_not_created_by_run", network=lan_network, address="192.168.1.10")
        self.refused(a1.Refusal, "lane_network_not_created_by_run", network_id=OTHER_NETWORK_ID,
                     network=lan_network, address="192.168.1.10")

    def test_an_address_outside_the_created_subnet_refuses(self):
        for outside in ("172.31.0.2", "172.29.255.254", "127.0.0.1", "172.30.0.0", "172.30.255.255"):
            with self.subTest(address=outside):
                with self.docker(created(), address=outside):
                    with self.assertRaises(ValueError) as caught:
                        lane.hand_over_core(NETWORK_ID, RUN_ID)
                self.assertEqual(caught.exception.args, ("real_ha_lane_core_outside_subnet",))
                self.assertIsNone(harness._lane_core)
        # A container on another network under the lane's name is refused before its address is used.
        self.refused(a1.Refusal, "lane_container_network_not_created_by_run", network=created(),
                     attached=OTHER_NETWORK_ID)

    def test_a_subnet_that_cannot_be_read_refuses(self):
        for ipam in ({}, None, {"Config": []}, {"Config": None}, {"Config": [{}]}, {"Config": [{"Subnet": None}]},
                     {"Config": [{"Subnet": SUBNET}, {"Subnet": "fd00::/64"}]}, {"Config": [SUBNET]}):
            with self.subTest(ipam=ipam):
                self.refused(a1.Refusal, "lane_network_subnet_unreadable", network=created(IPAM=ipam))
        # A subnet Docker would not report for a bridge pool is the harness's refusal.
        for subnet in ("10.0.0.0/8", "0.0.0.0/0", "fd00::/64", "172.30.0.2/16"):
            with self.subTest(subnet=subnet):
                with self.docker(created(IPAM={"Config": [{"Subnet": subnet}]})):
                    with self.assertRaises(ValueError) as caught:
                        lane.hand_over_core(NETWORK_ID, RUN_ID)
                self.assertEqual(caught.exception.args, ("real_ha_lane_subnet_not_usable",))
                self.assertIsNone(harness._lane_core)

    def test_the_stub_origin_is_its_address_inside_the_created_subnet(self):
        with self.docker(created(), address="172.30.0.3"):
            self.assertEqual(lane.stub_origin(NETWORK_ID, RUN_ID, 5580, SUBNET), "http://172.30.0.3:5580")
        # The address is read from the stub's own container, on the network this run created.
        self.assertEqual(self.commands, [])

    def test_a_stub_address_outside_the_created_subnet_refuses(self):
        for address, subnet in (("192.168.1.10", SUBNET), ("172.31.0.3", SUBNET), ("10.0.0.3", SUBNET),
                                ("127.0.0.1", SUBNET), ("172.30.0.3", "172.30.1.0/24"),
                                ("172.30.0.3", "not-a-subnet"), ("172.30.0.3", "172.30.0.1/16"),
                                ("not-an-address", SUBNET), ("fd00::3", SUBNET), ("172.30.0.3", "fd00::/64")):
            with self.subTest(address=address, subnet=subnet), self.docker(created(), address=address):
                with self.assertRaises(a1.Refusal) as caught:
                    lane.stub_origin(NETWORK_ID, RUN_ID, 5580, subnet)
                self.assertEqual(caught.exception.args, ("lane_stub_outside_created_network",))

    def test_a_stub_on_a_network_this_run_did_not_create_refuses(self):
        with self.docker(created(), address="172.30.0.3", attached=OTHER_NETWORK_ID):
            with self.assertRaises(a1.Refusal) as caught:
                lane.stub_origin(NETWORK_ID, RUN_ID, 5580, SUBNET)
        self.assertEqual(caught.exception.args, ("lane_container_network_not_created_by_run",))
        with self.docker(created(), address=""):
            with self.assertRaises(a1.Refusal) as caught:
                lane.stub_origin(NETWORK_ID, RUN_ID, 5580, SUBNET)
        self.assertEqual(caught.exception.args, ("lane_container_address_missing",))

    def test_the_runner_never_names_its_core_in_the_environment(self):
        source = Path(lane.__file__).read_text()
        self.assertNotIn('os.environ["REAL_HA_URL"]', source)
        self.assertNotIn("REAL_HA_URL=", source)
        self.assertIn('os.environ.pop("REAL_HA_URL", None)', source)
        # The id is the one this run's create printed, checked at once and again at the hand-over,
        # whose record of name, subnet and target address goes into the receipt.
        posture = source[source.index('"network_create"'):source.index('"stub_start"')]
        self.assertIn('network_id = command("docker", "network", "create", "--internal"', posture)
        self.assertIn("created_network(network_id, run_id)", posture)
        self.assertEqual(source.count("network_id = "), 1)
        self.assertIn('ha_url, result["lane_network"] = hand_over_core(network_id, run_id)', source)
        self.assertIn('stub_url = stub_origin(network_id, run_id, stub["port"], result["lane_network"]["subnet"])',
                      source)


class NetworkPostureTests(unittest.TestCase):
    def test_internal_bridge_without_published_ports(self):
        self.assertIs(lane.NETWORK_POSTURE["internal"], True)
        self.assertIs(lane.NETWORK_POSTURE["published_ports"], False)
        self.assertIs(lane.NETWORK_POSTURE["egress_allowed"], False)
        self.assertNotEqual(lane.CORE, a1.CORE)
        self.assertNotEqual(lane.NETWORK, a1.NETWORK)
        source = Path(lane.__file__).read_text()
        self.assertIn('"network", "create", "--internal"', source)
        self.assertIn('network.get("Internal") is not True', source)
        self.assertNotIn("--publish", source)
        stub_run = source[source.index('"stub_start"'):source.index('"core_start"')]
        self.assertIn("target=/lane,readonly", stub_run)
        self.assertIn("lane_core_egress_reachable", source)

    def test_container_address_requires_only_the_lane_network_and_no_ports(self):
        def inspected(networks, ports=None):
            data = {"NetworkSettings": {"Networks": networks, "Ports": ports or {}}}
            return mock.patch.object(lane, "inspect_owned", return_value=data)
        own = {"IPAddress": "172.30.0.2", "NetworkID": NETWORK_ID}
        with inspected({lane.NETWORK: own}, {"8123/tcp": None}):
            self.assertEqual(lane.address(lane.CORE, "run", NETWORK_ID), "172.30.0.2")
        for networks, ports in (({lane.NETWORK: own, "bridge": {}}, None),
                                ({lane.NETWORK: own}, {"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8123"}]}),
                                ({lane.NETWORK: {**own, "IPAddress": ""}}, None),
                                # The lane's name, but not the network this run created.
                                ({lane.NETWORK: {**own, "NetworkID": OTHER_NETWORK_ID}}, None),
                                ({lane.NETWORK: {"IPAddress": "172.30.0.2"}}, None)):
            with self.subTest(networks=networks, ports=ports), inspected(networks, ports):
                with self.assertRaises(a1.Refusal):
                    lane.address(lane.CORE, "run", NETWORK_ID)

    def test_the_stub_is_never_copied_into_this_branch(self):
        """No file outside the committed stub directory carries a pinned stub hash."""
        committed = ROOT / SPEC["stub"]["committed_dir"]
        pinned = set(SPEC["stub"]["files"].values())
        for tree in ("docs", "scripts", "tests"):
            for path in (ROOT / tree).rglob("*"):
                if committed in path.parents or not path.is_file():
                    continue
                if path.suffix in (".py", ".json", ".sh"):
                    self.assertNotIn(hashlib.sha256(path.read_bytes()).hexdigest(), pinned, path)


class StubRecordTests(unittest.TestCase):
    startup = (("server_info", None), ("start_listening", None), ("get_nodes", None))

    def check(self, *commands, mark=3, reads=2):
        return a2.check_stub_record(a2.stub_commands(record(*self.startup, *commands)), mark=mark,
                                    topology_reads=reads, background_commands=BACKGROUND)

    def test_recorded_refresh_is_the_literal_false(self):
        summary = self.check(("get_network_topology", {"refresh": False}), ("check_node_update", {"node_id": 61}),
                             ("get_network_topology", {"refresh": False}))
        self.assertEqual(summary["recorded_refresh"], [False, False])
        self.assertEqual(summary["engineering_topology_commands"], 2)

    def test_any_other_refresh_or_argument_fails(self):
        for args in ({"refresh": True}, {"refresh": 0}, {"refresh": None}, {}, None,
                     {"refresh": False, "extra": 1}):
            with self.subTest(args=args), self.assertRaises(AssertionError):
                self.check(("get_network_topology", {"refresh": False}), ("get_network_topology", args))

    def test_only_topology_and_core_background_commands(self):
        for extra in ("remove_node", "commission_with_code", "ping_node", "interview_node",
                      "set_thread_dataset", "update_node"):
            with self.subTest(extra=extra), self.assertRaises(AssertionError):
                self.check(("get_network_topology", {"refresh": False}), (extra, {}),
                           ("get_network_topology", {"refresh": False}))
        with self.assertRaises(AssertionError):
            self.check(("get_network_topology", {"refresh": False}))

    def test_harness_raw_read_before_the_mark_is_also_checked(self):
        with self.assertRaises(AssertionError):
            self.check(("get_network_topology", {"refresh": True}), ("get_network_topology", {"refresh": False}),
                       ("get_network_topology", {"refresh": False}), mark=4)
        with self.assertRaises(AssertionError):
            a2.check_stub_record(a2.stub_commands(record(("remove_node", {}), *self.startup)), mark=4,
                                 topology_reads=0, background_commands=BACKGROUND)

    def test_stub_state_agrees_with_the_record(self):
        state = {"topology_requests": [{"refresh": False}] * 4}
        self.assertEqual(a2.check_stub_state(state, recorded=4),
                         {"topology_requests": 4, "refresh_values": [False]})
        for bad in ({"topology_requests": [{"refresh": False}] * 3},
                    {"topology_requests": [{"refresh": False}] * 3 + [{"refresh": True}]},
                    {"topology_requests": [{"refresh": False}] * 3 + [{}]}, {}):
            with self.subTest(state=bad), self.assertRaises((AssertionError, KeyError)):
                a2.check_stub_state(bad, recorded=4)


class TopologySuccessTests(unittest.TestCase):
    def setUp(self):
        self.values = canaries()
        self.kept = kept_raw()
        self.reply = stub_reply(self.values, self.kept)
        self.result = projected(deepcopy(self.reply))
        self.devices = a2.registry_node_devices(registry())

    def check(self, section=None, reply=None, devices=None, kept=None):
        return a2.check_topology_success(self.result[tm.TOPOLOGY] if section is None else section,
                                         raw=self.reply if reply is None else reply,
                                         devices=self.devices if devices is None else devices,
                                         expected=EXPECTED, kept_raw=self.kept if kept is None else kept)

    def test_registry_maps_node_devices_only(self):
        self.assertEqual(self.devices, {61: device_id(61), 91: device_id(91), 199: device_id(199)})
        with self.assertRaises(AssertionError):
            a2.registry_node_devices(registry() + registry()[:1])

    def test_stub_shaped_reply_passes_with_opaque_ids_kept_raw_values_and_no_canary(self):
        summary = self.check()
        self.assertEqual(summary["matter_nodes"], [61, 91, 199])
        self.assertEqual(summary["dropped_unresolved"], 1)
        self.assertEqual(summary["withheld_border_router_text"], ["vendor_name"])
        self.assertIn("connections_dropped_unresolved", self.result["partial_reasons"])
        self.assertEqual(self.result["identifier_policy"], "opaque_per_response_v1")
        values = [*self.values.values(), "br_" + self.values["border"], "thread_" + self.values["unknown"],
                  "thread_" + self.values["ghost"], tm.normalize_identifier(self.values["bssid"])]
        self.assertEqual(leaks(self.result, tuple(values)), [])

    def test_unresolved_or_wrong_ha_device_id_fails(self):
        with self.assertRaises(AssertionError):
            self.check(devices={**self.devices, 199: "b" * 32})
        reply = deepcopy(self.reply)
        reply["nodes"][1]["ha_device_id"] = None
        with self.assertRaises(AssertionError):
            self.check(section=projected(deepcopy(reply))[tm.TOPOLOGY], reply=reply)

    def test_raw_edge_endpoint_or_missing_kind_fails(self):
        section = deepcopy(self.result[tm.TOPOLOGY])
        section["connections"][0]["target"] = "br_" + self.values["border"]
        with self.assertRaises(AssertionError):
            self.check(section=section)
        reply = deepcopy(self.reply)
        del reply["nodes"][5]
        del reply["connections"][4]
        with self.assertRaises(AssertionError):
            self.check(section=projected(deepcopy(reply))[tm.TOPOLOGY], reply=reply)

    def test_changed_kept_raw_value_or_unwithheld_text_fails(self):
        router = next(node for node in self.result[tm.TOPOLOGY]["nodes"] if node["kind"] == "border_router")
        self.assertEqual(router["ext_pan_id"], self.kept["border_router"]["ext_pan_id"].lower())
        for key, value in (("rloc16", 0x4401), ("ext_pan_id", "0" * 16), ("network_name", "other"),
                           ("ext_pan_id", self.kept["border_router"]["ext_pan_id"][:-1] + "Z")):
            kept = deepcopy(self.kept)
            kept["border_router"][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.check(kept=kept)
        section = deepcopy(self.result[tm.TOPOLOGY])
        station = next(node for node in section["nodes"] if node.get("node_id") == 91)
        station.update(network_name="visible", network_name_withheld=False)
        with self.assertRaises(AssertionError):
            self.check(section=section)
        section = deepcopy(self.result[tm.TOPOLOGY])
        next(node for node in section["nodes"] if node["kind"] == "border_router")["vendor_name"] = "x"
        with self.assertRaises(AssertionError):
            self.check(section=section)

    def test_a_leaked_canary_is_detected(self):
        leaked = deepcopy(self.result)
        leaked[tm.TOPOLOGY]["nodes"][0]["vendor_name"] = self.values["bssid"].lower()
        self.assertEqual(leaks(leaked, (self.values["bssid"],)), [0])


class FaultShapeTests(unittest.TestCase):
    """The two stub faults as Core 2026.11.0 relays them (matter/api.py)."""

    def failure(self, code):
        return {"id": 7, "type": "result", "success": False,
                "error": {"code": code, "message": "test-stub injected topology failure"}}

    def test_topology_error_code_7_is_upstream_command_failed_without_text(self):
        with self.assertRaises(tm.SectionRefused) as caught:
            tm.classify_reply(self.failure("7"), 7, tm.TOPOLOGY)
        self.assertEqual(caught.exception.reason, EXPECTED["topology_error_status"])
        result = tm.build_result(base.outcomes(topology_reason=caught.exception.reason, sections=(tm.TOPOLOGY,)))
        self.assertEqual(set(result[tm.TOPOLOGY]), {"status", "upstream_activity"})
        self.assertNotIn("injected", json.dumps(result))

    def test_schema_below_13_is_not_supported(self):
        with self.assertRaises(tm.SectionRefused) as caught:
            tm.classify_reply(self.failure("not_supported"), 7, tm.TOPOLOGY)
        self.assertEqual(caught.exception.reason, EXPECTED["schema_fault_status"])


if __name__ == "__main__":
    unittest.main()
