"""Offline HAMCP-366 device_configuration_entity lane boundaries.

No Docker, no network, no production key. Synthetic values only: the token,
the secrets and every refused host below are unmistakably fake. The real lane
run against Core 2026.11.0 is the evidence for the scenario sequence; these
tests pin the lane's guards, its refusal and failure-preservation paths, and
the product service's behaviour through the lane's closed clients.
"""
import asyncio
import errno
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import unittest
import warnings
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]
import core_device_configuration_local as lane  # noqa: E402
import device_configuration_contract_acceptance as acceptance  # noqa: E402
import lane_failure_diagnostics as diagnostics  # noqa: E402
import test_hamcp297_device_configuration as offline  # noqa: E402
from core_release_evidence import groups, passing_subset  # noqa: E402
from ha_mcp_engineering.clients.paths import rest_path  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.device_configuration.contracts import (  # noqa: E402
    ALLOWLIST, SERVICES, DeviceConfigurationRefusal, digest, identity, service_call)
from ha_mcp_engineering.device_configuration.gateway import DeviceConfigurationGateway  # noqa: E402
from ha_mcp_engineering.device_configuration.service import (  # noqa: E402
    DeviceConfigurationCoreAuthority, DeviceConfigurationService)
from ha_mcp_engineering.errors import HomeAssistantTimeoutError, HomeAssistantUnavailableError  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import profiles as profiles_module  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402

ARCHIVE = ROOT / ".artifacts/inputs/core-2026.11.0.tar.gz"
ENTRY_PATH = lane.LANE_ASSET / "stub_entry.py"
TOKEN = "synthetic-token-7f3a"
CAPABILITY = "core.device_configuration_entity_operation"
STUB_HASHES = {"stub_sha256": "1" * 64, "entry_sha256": "2" * 64,
               "dump_hashes": {"synthetic.json": "3" * 64},
               "refusal_fixture_sha256": {"manifest.json": "4" * 64}}
RECEIPT_STUB = {key: STUB_HASHES[key] for key in ("stub_sha256", "entry_sha256", "dump_hashes")}
# Synthetic lane network facts (never a real network).
LANE_SUBNET = {"Subnet": "172.30.5.0/24", "Gateway": "172.30.5.1"}
STUB_ADDRESS = "172.30.5.3"


def settings(ha_url=acceptance.CORE_URL, upstream=acceptance.UPSTREAM_URL, token=TOKEN):
    return Settings(ha_url=ha_url, ha_token=token, access_secret="synthetic-secret", port=8100,
                    audit_path="unused", rate_limit_per_minute=120, rate_limit_burst=25,
                    destructive_services=frozenset(), upstream_dashboard_mcp_url=upstream)


def url_cases(pinned):
    """Everything near the pinned constant that is not the constant."""
    scheme, rest = pinned.split("://", 1)
    authority, _, tail = rest.partition("/")
    path = "/" + tail if tail else ""
    host, port = authority.split(":")
    other = {"http": "https", "ws": "wss"}[scheme]
    cases = [
        f"{scheme}://{host}:{int(port) + 1}{path}", f"{scheme}://{host}{path}",
        f"{scheme}://{host}:8123{path}", f"{scheme}://{host}:0{port}{path}",
        f"{other}://{authority}{path}", f"{scheme.upper()}://{authority}{path}",
        pinned + "/", pinned + "/..", pinned + "/other", pinned + "//",
        pinned + "?k=synthetic-query", pinned + "#fragment",
        f"{scheme}://{authority}@evil.example{path}", f"{scheme}://user@{authority}{path}",
        f"{scheme}://user:synthetic-password@{authority}{path}",
        *(f"{scheme}://{name}:{port}{path}" for name in (
            "localhost", "LOCALHOST", "[::1]", "127.0.0.2", "127.1", "2130706433", "0.0.0.0",
            "127.0.0.1.evil.example", "evil.example", "supervisor", "192.0.2.10")),
        f"{scheme}://supervisor/core", " " + pinned, pinned + " ", pinned + "\n", pinned + "\x00",
        "", None, pinned.encode(), (pinned,), [pinned], int(port), True,
    ]
    assert not any(type(case) is str and case == pinned for case in cases)
    return cases


class Text(str):
    """Equal to the str it wraps, and never exactly a str."""


class Shape(dict):
    """Equal to the dict it wraps, and never exactly a dict."""


def tree(root):
    """Every path below root, following links, relative to it."""
    return {os.path.relpath(os.path.join(base, name), root)
            for base, directories, files in os.walk(root, followlinks=True) for name in directories + files}


def claims(calls):
    """Docker calls that create or remove a container or a network."""
    return [call for call in calls if call[:2] in (("docker", "run"), ("docker", "rm"))
            or call[:3] in (("docker", "network", "create"), ("docker", "network", "rm"))]


def chain(exc):
    found = []
    while exc is not None:
        found.append(exc)
        exc = exc.__cause__ or exc.__context__
    return found


def fake_session(events, *, replies=(), status=200, value=None):
    """Stand-in for aiohttp.ClientSession: records construction, dial and frames."""
    pending = list(replies)

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def receive_json(self):
            return pending.pop(0)

        async def send_json(self, frame):
            events.append(("send", frame))

    class Response:
        def __init__(self):
            self.status = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def json(self, content_type=None):
            return value

    class Session:
        def __init__(self, **kwargs):
            events.append(("session", kwargs.get("trust_env")))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def ws_connect(self, url, **kwargs):
            events.append(("connect", url))
            return Socket()

        def request(self, method, url, **kwargs):
            events.append(("request", method, url, kwargs))
            return Response()

    return Session


class NoTransport:
    """Patch every way the acceptance module can reach a network; none may be used."""

    def __enter__(self):
        self.session, self.websocket, self.rest = Mock(), Mock(), Mock()
        self.patches = [patch.object(acceptance.aiohttp, "ClientSession", new=self.session),
                        patch.object(acceptance, "HomeAssistantWebSocketClient", new=self.websocket),
                        patch.object(acceptance, "HomeAssistantRestClient", new=self.rest)]
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *_exc):
        for item in self.patches:
            item.stop()
        return False

    def untouched(self):
        return not (self.session.called or self.websocket.called or self.rest.called)


def load_entry():
    spec = importlib.util.spec_from_file_location("hamcp366_stub_entry", ENTRY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Contracts(unittest.TestCase):
    def test_group_names_exactly_the_capability_and_lends_no_other_group(self):
        mapping = groups()
        self.assertEqual(lane.GROUP, "device_configuration_entity")
        self.assertEqual(mapping[lane.GROUP], [CAPABILITY])
        self.assertEqual([name for name, values in mapping.items() if CAPABILITY in values], [lane.GROUP])
        ref = acceptance.reference()
        self.assertEqual(ref["capability_id"], CAPABILITY)
        self.assertEqual(passing_subset([ref], {lane.GROUP: True}), [ref])
        # A missing or failed receipt, or any other group's pass, never admits it.
        self.assertEqual(passing_subset([ref], {}), [])
        self.assertEqual(passing_subset([ref], {lane.GROUP: False}), [])
        self.assertEqual(passing_subset([ref], {lane.GROUP: "PASS"}), [])
        others = {name: True for name in mapping if name != lane.GROUP}
        self.assertEqual(passing_subset([ref], others), [])
        # The group's pass never adds the capability to a predecessor that lacks it.
        self.assertEqual(passing_subset([], {lane.GROUP: True}), [])

    def test_group_count_and_position(self):
        names = list(groups())
        self.assertEqual(len(names), len(set(names)))
        # Release convention: security_metadata stays last; new groups go before it.
        self.assertLess(names.index("helper_value"), names.index(lane.GROUP))
        self.assertLess(names.index(lane.GROUP), names.index("security_metadata"))
        self.assertEqual(names[-1], "security_metadata")
        self.assertEqual(len(groups()["general"]), 19)
        self.assertIn(lane.GROUP, diagnostics.LANES)
        self.assertEqual(lane.RECEIPT, "device_configuration_entity.json")

    def test_lane_and_acceptance_constants_agree(self):
        self.assertEqual(lane.PROFILE, acceptance.reference())
        self.assertEqual(tuple(lane.PROFILE), acceptance.REFERENCE_KEYS)
        self.assertEqual(lane.GROUP, acceptance.GROUP)
        self.assertEqual(lane.PROFILE["capability_id"], acceptance.CAPABILITY)
        self.assertEqual(lane.PREDECESSOR_CAPABILITIES, acceptance.PREDECESSOR_CAPABILITIES)
        self.assertEqual((lane.CORE_PORT, lane.UPSTREAM_PORT, lane.STUB_PORT),
                         (acceptance.CORE_PORT, acceptance.UPSTREAM_PORT, acceptance.STUB_PORT))
        self.assertEqual(len(set(lane.PORTS)), 3)
        self.assertEqual(f"http://127.0.0.1:{lane.CORE_PORT}", acceptance.CORE_URL)
        self.assertEqual(acceptance.CORE_API_URL, acceptance.CORE_URL + "/api")
        self.assertEqual(settings().websocket_url, acceptance.CORE_WS_URL)
        self.assertEqual(f"http://127.0.0.1:{lane.UPSTREAM_PORT}/beta23-real-ha-mcp", acceptance.UPSTREAM_URL)
        self.assertEqual(f"http://127.0.0.1:{lane.STUB_PORT}/_lane/control", acceptance.STUB_CONTROL_URL)
        self.assertEqual(lane.STUB_ALIAS, acceptance.STUB_ALIAS)
        self.assertEqual(f"ws://{lane.STUB_ALIAS}:{lane.STUB_CONTAINER_PORT}/ws", acceptance.MATTER_URL)
        manifest = json.loads((lane.LANE_ASSET / "custom_components" / lane.FIXTURE_COMPONENT
                               / "manifest.json").read_text())
        self.assertEqual(manifest["domain"], lane.FIXTURE_COMPONENT)
        self.assertEqual(acceptance.FIXTURE_DOMAIN, lane.FIXTURE_COMPONENT)

    def test_exactly_three_admitted_triples(self):
        self.assertEqual(len(acceptance.ADMITTED), 3)
        self.assertEqual(sorted(acceptance.ADMITTED), sorted(ALLOWLIST))
        lane_entities = {acceptance.SELECT: "startup_on_off", acceptance.SWITCH: "child_lock",
                         acceptance.NUMBER: "sensitivity_level"}
        self.assertEqual({(entity_id.split(".")[0], "matter", key) for entity_id, key in lane_entities.items()},
                         set(acceptance.ADMITTED))
        # Every service shape on the closed gateway list targets one lane entity
        # of one admitted domain, with the product's only service names.
        calls = [spec for spec in acceptance.GATEWAY_COMMANDS if spec["type"] == "call_service"]
        self.assertEqual(len(calls), 3)
        self.assertEqual({spec["target"]["entity_id"] for spec in calls}, set(lane_entities))
        for spec in calls:
            self.assertEqual(spec["target"]["entity_id"].split(".")[0], spec["domain"])
            self.assertEqual(set(spec.get("service_data", {})), set(sum(SERVICES[spec["domain"]].values(), ())))

    def test_refusal_fixture_declares_every_lane_case_and_never_matter(self):
        component = lane.LANE_ASSET / "custom_components" / lane.FIXTURE_COMPONENT
        declared = {}
        for domain in ("select", "switch", "number"):
            for key in re.findall(r'Fixture[A-Za-z]+\("([a-z0-9_]+)"', (component / (domain + ".py")).read_text()):
                declared[key] = domain
        self.assertEqual(declared, {case: domain for case, domain, _code in acceptance.FIXTURE_CASES})
        for path in component.iterdir():
            if path.suffix == ".py":
                self.assertNotIn('"matter"', path.read_text())
        codes = {code for _case, _domain, code in acceptance.FIXTURE_CASES}
        self.assertEqual(codes, {"device_configuration_target_kind_not_admitted",
                                 "device_configuration_not_config_category",
                                 "device_configuration_target_disabled", "device_configuration_no_device"})

    def test_predicted_refusal_for_kinds_outside_the_three_triples(self):
        devices = [{"id": "dev", "disabled_by": None}]
        def entry(entity_id, platform, key, **changes):
            return {"entity_id": entity_id, "platform": platform, "translation_key": key,
                    "entity_category": "config", "device_id": "dev", "disabled_by": None, **changes}
        for kind in acceptance.ADMITTED:
            self.assertIsNone(acceptance.predicted_refusal(entry(kind[0] + ".x", kind[1], kind[2]), devices))
        outside = [("select.x", "hamcp366_refusal_fixture", "startup_on_off"), ("select.x", "zha", "startup_on_off"),
                   ("select.x", "matter", "child_lock"), ("switch.x", "matter", "startup_on_off"),
                   ("number.x", "matter", "child_lock"), ("select.x", "matter", None),
                   ("select.x", "matter", "startup_on_off_"), ("select.x", "Matter", "startup_on_off"),
                   ("light.x", "matter", "child_lock"), ("button.x", "matter", "child_lock"),
                   ("number.x", "matter", "on_level"), ("switch.x", "matter", "power")]
        for entity_id, platform, key in outside:
            with self.subTest(entity_id=entity_id, platform=platform, key=key):
                self.assertEqual(acceptance.predicted_refusal(entry(entity_id, platform, key), devices),
                                 "device_configuration_target_kind_not_admitted")
        admitted = ("select.x", "matter", "startup_on_off")
        self.assertEqual(acceptance.predicted_refusal(entry(*admitted, entity_category=None), devices),
                         "device_configuration_not_config_category")
        self.assertEqual(acceptance.predicted_refusal(entry(*admitted, entity_category="diagnostic"), devices),
                         "device_configuration_not_config_category")
        self.assertEqual(acceptance.predicted_refusal(entry(*admitted, device_id=None), devices),
                         "device_configuration_no_device")
        self.assertEqual(acceptance.predicted_refusal(entry(*admitted), []), "device_configuration_no_device")
        self.assertEqual(acceptance.predicted_refusal(entry(*admitted, disabled_by="user"), devices),
                         "device_configuration_target_disabled")
        self.assertEqual(acceptance.predicted_refusal(entry(*admitted), [{"id": "dev", "disabled_by": "user"}]),
                         "device_configuration_target_disabled")

    def test_expected_frames_equal_the_product_frames_and_nothing_looser(self):
        cases = [(acceptance.SELECT, option) for option in acceptance.SELECT_OPTIONS]
        cases += [(acceptance.SWITCH, "on"), (acceptance.SWITCH, "off")]
        cases += [(acceptance.NUMBER, value) for value in acceptance.NUMBER_VALUES]
        for entity_id, value in cases:
            with self.subTest(entity_id=entity_id, value=value):
                product = service_call(entity_id, entity_id.split(".")[0], value)
                expected = acceptance.expected_payload(entity_id, value)
                self.assertTrue(acceptance.same_frame(product, expected))
                self.assertTrue(acceptance.listed(product, acceptance.GATEWAY_COMMANDS))
        number = acceptance.expected_payload(acceptance.NUMBER, "2")
        whole = {**number, "service_data": {"value": 2}}
        self.assertEqual(whole, number)  # equal in Python, and still not the same frame
        self.assertFalse(acceptance.same_frame(whole, number))
        reordered = {key: number[key] for key in reversed(list(number))}
        self.assertFalse(acceptance.same_frame(reordered, number))
        self.assertFalse(acceptance.same_frame({**number, "return_response": True}, number))

    def test_stub_values_restate_core(self):
        self.assertEqual([acceptance.stub_value(acceptance.SELECT, option) for option in ("on", "off", "toggle", "previous")],
                         [1, 0, 2, None])
        self.assertIs(acceptance.stub_value(acceptance.SWITCH, "on"), True)
        self.assertIs(acceptance.stub_value(acceptance.SWITCH, "off"), False)
        self.assertEqual([acceptance.stub_value(acceptance.NUMBER, value) for value in ("1", "2", "3")], [0, 1, 2])
        self.assertTrue(acceptance.same_state(acceptance.NUMBER, "2.0", "2"))
        self.assertFalse(acceptance.same_state(acceptance.NUMBER, "unavailable", "2"))
        self.assertFalse(acceptance.same_state(acceptance.SELECT, "On", "on"))


class FingerprintPrecheck(unittest.TestCase):
    """Design section 9.2: the run fails unless the loaded profile is the signing profile."""

    def profile(self, **changes):
        value = {**profiles_module.CORE_DEVICE_CONFIGURATION_PROFILES[0].to_mapping(), **changes}
        return SimpleNamespace(to_mapping=lambda: dict(value))

    def test_committed_profile_is_the_recorded_one(self):
        self.assertEqual(lane.loaded_profile(), lane.PROFILE)
        self.assertEqual(lane.PROFILE["contract_fingerprint"],
                         "sha256:74a03a640f60c9adaf8508a0a7fcbca7745f29b3d09e1dc9a611ac361475293c")
        self.assertEqual(lane.PROFILE["profile_version"], 1)

    def test_any_changed_reference_value_refuses(self):
        changes = {"contract_fingerprint": "sha256:" + "0" * 64, "profile_version": 2, "profile_id": "other_v1",
                   "adapter_id": "compiled-other-v1", "capability_id": "core.other_operation"}
        self.assertEqual(set(changes), set(lane.PROFILE))
        for key, value in changes.items():
            with self.subTest(key=key), patch.object(
                    profiles_module, "CORE_DEVICE_CONFIGURATION_PROFILES", (self.profile(**{key: value}),)):
                with self.assertRaises(lane.Refusal) as caught:
                    lane.loaded_profile()
                self.assertEqual(caught.exception.args, ("profile_fingerprint_mismatch",))
        # A type change is a change: version 1 is not True, 1.0 or "1".
        for value in (True, 1.0, "1", None):
            with self.subTest(version=value), patch.object(
                    profiles_module, "CORE_DEVICE_CONFIGURATION_PROFILES", (self.profile(profile_version=value),)):
                with self.assertRaises(lane.Refusal) as caught:
                    lane.loaded_profile()
                self.assertEqual(caught.exception.args, ("profile_fingerprint_mismatch",))

    def test_missing_reference_value_refuses(self):
        for key in lane.PROFILE:
            value = {name: item for name, item in lane.PROFILE.items() if name != key}
            with self.subTest(key=key), patch.object(
                    profiles_module, "CORE_DEVICE_CONFIGURATION_PROFILES",
                    (SimpleNamespace(to_mapping=lambda value=value: dict(value)),)):
                with self.assertRaises(lane.Refusal) as caught:
                    lane.loaded_profile()
                self.assertEqual(caught.exception.args, ("profile_fingerprint_mismatch",))

    def test_zero_or_two_profiles_refuse(self):
        for found in ((), (self.profile(), self.profile())):
            with self.subTest(count=len(found)), patch.object(
                    profiles_module, "CORE_DEVICE_CONFIGURATION_PROFILES", found):
                with self.assertRaises(lane.Refusal) as caught:
                    lane.loaded_profile()
                self.assertEqual(caught.exception.args, ("profile_fingerprint_mismatch",))


class LaneInputs(unittest.TestCase):
    def test_journal_or_archive_with_other_bytes_refuses_before_the_journal_is_parsed(self):
        import core_registry_one_tap
        archive_hash = json.loads(lane.CORE_PROVENANCE.read_text())["source_archive_sha256"]
        with tempfile.TemporaryDirectory() as directory:
            journal, archive = Path(directory) / "journal.json", Path(directory) / "archive.tar.gz"
            journal.write_bytes(b"synthetic journal")
            archive.write_bytes(b"synthetic archive")
            real = lane.sha256
            for hashes in ({journal: "0" * 64, archive: archive_hash}, {journal: lane.JOURNAL_SHA256, archive: "0" * 64},
                           {journal: real(journal), archive: real(archive)}):
                with self.subTest(hashes=sorted(hashes.values())), diagnostics.lane(lane.GROUP), \
                        patch.object(lane, "sha256", side_effect=lambda path: hashes[Path(path)]), \
                        patch.object(core_registry_one_tap, "load_public_key",
                                     side_effect=AssertionError("journal_parsed")):
                    with self.assertRaises(lane.Refusal) as caught:
                        lane.inputs(journal, archive)
                    self.assertEqual(caught.exception.args, ("input_hash_mismatch",))

    def test_optimised_interpreter_is_refused_before_any_host_command(self):
        # Under -O every acceptance assertion would vanish; the lane refuses to run at all.
        program = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import core_device_configuration_local as lane\n"
            "calls = []\n"
            "def command(*args, **kwargs):\n"
            "    calls.append(args)\n"
            "    raise SystemExit('host_command_reached')\n"
            "lane.command = command\n"
            "try:\n"
            "    lane.main(['--mode', 'local', '--pull', 'never', '--journal', 'j', '--source-archive', 'a',\n"
            "               '--run-dir', 'unused'])\n"
            "except lane.Refusal as exc:\n"
            "    print('refused', exc.args, len(calls))\n")
        result = subprocess.run([sys.executable, "-O", "-B", "-c", program, str(ROOT / "scripts")],
                                capture_output=True, text=True, timeout=60, cwd=tempfile.gettempdir(),
                                env={"PATH": os.environ.get("PATH", "")})
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "refused ('assertions_required',) 0"),
                         result.stderr[-500:])


class StubAssets(unittest.TestCase):
    """The stub, its entry and the three dumps are hash-bound before any container."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stub = self.root / "stub"
        shutil.copytree(lane.STUB_ASSET, self.stub, ignore=shutil.ignore_patterns("__pycache__"))
        self.provenance = json.loads(lane.CORE_PROVENANCE.read_text())
        self.source = self.root / "core-provenance.json"
        self.source.write_text(json.dumps(self.provenance))
        self.archive = self.root / "core.tar.gz"
        self.build_archive()
        for name, value in (("STUB_ASSET", self.stub), ("CORE_PROVENANCE", self.source)):
            patcher = patch.object(lane, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def build_archive(self, *, change=None, omit=None, commit=None):
        prefix = "core-" + (commit or self.provenance["source_commit"]) + "/" + lane.DUMP_DIRECTORY
        with tarfile.open(self.archive, "w:gz") as archive:
            for path in sorted((lane.STUB_ASSET / "nodes").iterdir()):
                if path.name == omit:
                    continue
                data = path.read_bytes() + (b" " if path.name == change else b"")
                info = tarfile.TarInfo(prefix + path.name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    def refused(self, code):
        with self.assertRaises(lane.Refusal) as caught:
            lane.stub_assets(self.archive)
        self.assertEqual(caught.exception.args, (code,))

    def test_committed_assets_match_provenance_and_synthetic_archive(self):
        found = lane.stub_assets(self.archive)
        files = json.loads((lane.STUB_ASSET / "provenance.json").read_text())["files"]
        self.assertEqual(found["stub_sha256"], files["stub.py"])
        self.assertEqual(found["stub_sha256"], hashlib.sha256((lane.STUB_ASSET / "stub.py").read_bytes()).hexdigest())
        self.assertEqual(found["entry_sha256"], hashlib.sha256(ENTRY_PATH.read_bytes()).hexdigest())
        self.assertEqual(len(found["dump_hashes"]), 3)
        self.assertEqual(found["dump_hashes"], {name[len("nodes/"):]: value for name, value in files.items()
                                                if name.startswith("nodes/")})
        self.assertIn("manifest.json", found["refusal_fixture_sha256"])

    @unittest.skipUnless(ARCHIVE.is_file(), "exact Core 2026.11.0 archive not staged")
    def test_committed_dumps_equal_the_exact_core_archive(self):
        self.assertEqual(len(lane.stub_assets(ARCHIVE)["dump_hashes"]), 3)

    def test_changed_extra_or_missing_stub_file_refuses(self):
        with (self.stub / "stub.py").open("ab") as handle:
            handle.write(b"\n")
        self.refused("stub_asset_hash_mismatch")

    def test_changed_dump_refuses(self):
        with (self.stub / "nodes" / "eve_energy_plug.json").open("ab") as handle:
            handle.write(b" ")
        self.refused("stub_asset_hash_mismatch")

    def test_extra_file_refuses(self):
        (self.stub / "nodes" / "synthetic_extra.json").write_text("{}")
        self.refused("stub_asset_hash_mismatch")

    def test_missing_file_refuses(self):
        (self.stub / "driver.py").unlink()
        self.refused("stub_asset_hash_mismatch")

    def test_bytecode_cache_is_ignored(self):
        (self.stub / "__pycache__").mkdir()
        (self.stub / "__pycache__" / "stub.cpython-312.pyc").write_bytes(b"synthetic")
        self.assertEqual(len(lane.stub_assets(self.archive)["dump_hashes"]), 3)

    def test_dump_that_differs_from_the_core_archive_refuses(self):
        self.build_archive(change="eve_energy_plug.json")
        self.refused("stub_dump_source_mismatch")

    def test_dump_missing_from_the_core_archive_refuses(self):
        self.build_archive(omit="aqara_door_window_p2.json")
        self.refused("stub_dump_source_mismatch")

    def test_archive_of_another_commit_refuses(self):
        self.build_archive(commit="0" * 40)
        self.refused("stub_dump_source_mismatch")

    def test_dump_provenance_naming_another_source_commit_refuses(self):
        self.source.write_text(json.dumps({**self.provenance, "source_commit": "0" * 40}))
        self.build_archive(commit="0" * 40)
        self.refused("stub_dump_source_mismatch")


class StubContainer(unittest.TestCase):
    def test_stub_runs_test_only_on_the_lane_network_and_one_loopback_port(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, patch.object(
                lane, "command", side_effect=lambda *args, **kwargs: calls.append(args)):
            lane.start_stub("core:tag@sha256:a", "run-id", "256m", Path(directory),
                            {"b.json": "x", "a.json": "y", "c.json": "z"})
        (argv,), values = calls, lambda flag: [calls[0][i + 1] for i, item in enumerate(calls[0]) if item == flag]
        lane.host.require_memory(argv)
        self.assertEqual(argv[:3], ("docker", "run", "--detach"))
        self.assertEqual(values("--pull"), ["never"])
        self.assertEqual(values("--name"), [lane.STUB])
        self.assertEqual(values("--label"), [lane.LABEL + "=run-id"])
        self.assertEqual(values("--network"), [lane.NETWORK])
        self.assertEqual(values("--network-alias"), [lane.STUB_ALIAS])
        self.assertEqual(values("--memory"), ["256m"])
        self.assertEqual(values("--publish"), ["127.0.0.1:18299:5580"])
        self.assertEqual(values("--security-opt"), ["no-new-privileges"])
        self.assertEqual(values("--cap-drop"), ["ALL"])
        self.assertIn("--read-only", argv)
        self.assertEqual(values("--user"), [f"{os.getuid()}:{os.getgid()}"])
        for forbidden in ("--privileged", "--net", "--volume", "-v", "-p", "-P", "--publish-all", "--env", "-e",
                          "--cap-add", "--pid", "--add-host"):
            self.assertNotIn(forbidden, argv)
        mounts = values("--mount")
        writable = [mount for mount in mounts if not mount.endswith(",readonly")]
        self.assertEqual(len(writable), 1)
        self.assertTrue(writable[0].endswith(",target=/out"))
        targets = sorted(mount.split(",target=")[1].split(",")[0] for mount in mounts)
        self.assertEqual(targets, ["/lane/stub.py", "/nodes/a.json", "/nodes/b.json", "/nodes/c.json", "/out",
                                   "/wrapper/stub_entry.py"])
        image = argv.index("core:tag@sha256:a")
        self.assertEqual(argv[image - 2:image], ("--entrypoint", "python3"))
        self.assertEqual(argv[image + 1:], ("-I", "/wrapper/stub_entry.py", "--stub", "/lane/stub.py",
                                            "--nodes", "/nodes", "--out", "/out/stub.jsonl", "--port", "5580"))


class Orchestration(unittest.TestCase):
    def run_lane(self, *, exercise_error=None, cleanup_error=False, dirty=False, existing=False,
                 admission_error=None, memory_args=(), network_override=None, ports_override=None,
                 outcome=None, profile_error=False, assets_error=None, refusal=None,
                 stub_networks=None, ipam=None, changed=None, run_dir=None):
        calls, resources, admitted = [], {}, []
        lane_network = {"Driver": "bridge", "Internal": False,
                        "IPAM": {"Config": [dict(LANE_SUBNET)] if ipam is None else ipam}}
        good_ports = {lane.CORE: {"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18297"}]},
                      lane.STUB: {"5580/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18299"}]}}

        def command(*args, **kwargs):
            calls.append(args)
            stdout, code = b"", 0
            if args[:2] == ("git", "status"):
                stdout = b" M source.py" if dirty or (admitted and changed == "worktree") else b""
            elif args[:2] == ("git", "rev-parse"):
                stdout = (b"b" if admitted and changed == "head" else b"a") * 40
            elif "inspect" in args:
                code = 0 if existing else 1
            elif args[:3] == ("docker", "network", "create"):
                resources[lane.NETWORK] = True
            elif args[:2] == ("docker", "run"):
                lane.host.require_memory(args)
                resources[args[args.index("--name") + 1]] = True
            elif args[:2] == ("docker", "rm") or args[:3] == ("docker", "network", "rm"):
                resources.pop(args[-1], None)
            return subprocess.CompletedProcess(args, code, stdout)

        def inspect(kind, name, run_id):
            if name not in resources:
                return None
            if kind == "network":
                return network_override if network_override is not None else lane_network
            ports = dict(good_ports[name])
            if ports_override is not None and name == ports_override[0]:
                ports = ports_override[1]
            networks = {lane.NETWORK: {"IPAddress": STUB_ADDRESS if name == lane.STUB else "172.30.5.2"}}
            if stub_networks is not None and name == lane.STUB:
                networks = stub_networks
            return {"NetworkSettings": {"Networks": networks, "Ports": ports}}

        def remove(args, *_):
            if cleanup_error:
                raise lane.Refusal("cleanup_failed")
            shutil.rmtree(args.directory)

        def admit(_path):
            if admission_error is not None:
                raise admission_error
            admitted.append(True)  # the checkout can only change after this point
            return {"other_heavy_jobs": 0}

        passing = {"result": "PASS", "profile": dict(lane.PROFILE), "stub": json.loads(json.dumps(RECEIPT_STUB))}
        profile_patch = (patch.object(lane, "loaded_profile", side_effect=lane.Refusal("profile_fingerprint_mismatch"))
                         if profile_error else patch.object(lane, "loaded_profile", wraps=lane.loaded_profile))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".artifacts").mkdir()
            out = root / ".artifacts" / "run" if run_dir is None else run_dir(root)
            present = tree(root)
            argv = ["--mode", "local", "--pull", "never", "--journal", "journal",
                    "--source-archive", "archive", "--run-dir", str(out), *memory_args]
            with patch.dict(os.environ, {}, clear=True), patch.object(lane, "ROOT", root), \
                    patch.object(lane, "command", side_effect=command), profile_patch, \
                    patch.object(lane, "inputs", return_value={}) as inputs, \
                    patch.object(lane, "stub_assets", side_effect=assets_error,
                                 return_value=json.loads(json.dumps(STUB_HASHES))) as assets, \
                    patch.object(lane, "images", return_value=("core@sha256:a", "up@sha256:b", lane.host.FIXTURE_IMAGE)), \
                    patch.object(lane, "cached_image", return_value="config"), \
                    patch.object(lane.resources, "wait_for_admission", side_effect=admit), \
                    patch.object(lane, "sha256", return_value="c" * 64), \
                    patch.object(lane, "inspect_owned", side_effect=inspect), \
                    patch.object(lane.host, "local_remove", side_effect=remove), \
                    patch.object(lane, "require_free_ports"), patch.object(lane.signal, "signal"), \
                    patch.object(lane.diagnostics, "_raw_log", return_value=False), \
                    patch.object(lane, "exercise", new=AsyncMock(
                        side_effect=exercise_error, return_value=passing if outcome is None else outcome)) as exercise, \
                    patch("sys.stdout", new_callable=io.StringIO):
                if refusal is not None:
                    with self.assertRaises(lane.Refusal) as caught:
                        lane.main(argv)
                    self.assertEqual(caught.exception.args, (refusal,))
                    exercise.assert_not_called()
                    self.created, self.admitted = sorted(tree(root) - present), bool(admitted)
                    if changed is None and run_dir is None:
                        self.assertFalse(out.exists())
                        self.assertFalse(any(call[0] == "docker" and call[1] in ("run", "rm", "network")
                                             for call in calls))
                    return calls, inputs, assets
                code = lane.main(argv)
                if network_override is not None or ports_override is not None or stub_networks is not None \
                        or ipam is not None:
                    exercise.assert_not_called()  # before onboarding, auth or any readiness check
                result = json.loads((out / "device_configuration_entity.json").read_text())
                self.assertEqual(sorted(path.name for path in out.iterdir() if path.suffix == ".json"),
                                 ["device_configuration_entity.json"])
                if admission_error:
                    self.assertEqual(code, 1)
                    self.assertFalse(result["execution_started"])
                    self.assertEqual(result["reason"], "host_admission_timeout")
                    self.assertTrue(result["cleanup"]["clean"])
                    self.assertFalse(resources)
                    self.assertFalse(any(call[:2] == ("docker", "run") for call in calls))
                    return code, result, calls
                self.assertFalse((out / lane.CORE).exists() and not cleanup_error)
            self.assertFalse(resources)
            self.assertIn("started_utc", result)
            self.assertIn("ended_utc", result)
            for call in calls:
                if call[:3] == ("docker", "network", "create"):
                    self.assertEqual(call[call.index("--driver") + 1], "bridge")
                    self.assertNotIn("--internal", call)
                if call[:2] == ("docker", "run"):
                    self.assertEqual(call[call.index("--pull") + 1], "never")
                    published = [call[i + 1] for i, item in enumerate(call) if item == "--publish"]
                    self.assertEqual(len(published), 1)
                    self.assertTrue(published[0].startswith("127.0.0.1:"), published)
                    self.assertEqual(call.count("--memory"), 1)
                    self.assertEqual(call[call.index("--network") + 1], lane.NETWORK)
                self.assertNotIn("pull", call[1:2])
            return code, result, calls

    def test_pass_requires_cleanup_and_binds_profile_and_stub(self):
        code, result, calls = self.run_lane()
        self.assertEqual(code, 0)
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["cleanup"], {"clean": True, "containers_absent": True, "network_absent": True,
                                             "configuration_absent": True})
        self.assertEqual(result["group"], "device_configuration_entity")
        self.assertEqual(result["core_version"], "2026.11.0")
        self.assertEqual(result["signing_profile"], lane.PROFILE)
        self.assertEqual(result["profile"], lane.PROFILE)
        self.assertEqual(result["stub_assets"], STUB_HASHES)
        self.assertEqual(result["source_commit"], "a" * 40)
        self.assertEqual(result["network_posture"],
                         {"driver": "bridge", "host_bind_address": "127.0.0.1", "egress_allowed": True})
        self.assertEqual(result["container_memory"], {"core": "2g", "upstream": "768m", "stub": "256m", "fixture": "128m"})
        started = [call[call.index("--name") + 1] for call in calls if call[:2] == ("docker", "run")]
        self.assertEqual(started, [lane.STUB, lane.CORE])  # the stub's address is known before Core starts
        removed = [call[-1] for call in calls if call[:2] == ("docker", "rm")]
        self.assertEqual(sorted(removed), sorted([lane.CORE, lane.STUB]))
        core = next(call for call in calls if call[:2] == ("docker", "run") and lane.CORE in call)
        self.assertIn("127.0.0.1:18297:8123", core)
        # Core reaches the stub by one hosts entry: the stub name at the inspected lane address.
        hosts = [core[i + 1] for i, item in enumerate(core) if item == "--add-host"]
        self.assertEqual(hosts, [lane.STUB_ALIAS + ":" + STUB_ADDRESS])
        self.assertEqual(result["stub_host_entry"], {"name": lane.STUB_ALIAS, "address": STUB_ADDRESS})
        self.assertEqual(lane.STUB_ALIAS, acceptance.MATTER_URL.split("//")[1].split(":")[0])
        for forbidden in ("--privileged", "--net", "--volume", "-v", "-p", "-P", "--publish-all", "--env", "-e",
                          "--cap-add", "--pid", "--network-alias", "--dns"):
            self.assertNotIn(forbidden, core)

    def test_core_container_cannot_gain_new_privileges(self):
        _code, _result, calls = self.run_lane()
        core = next(call for call in calls if call[:2] == ("docker", "run") and lane.CORE in call)
        self.assertEqual(core[-1], "core@sha256:a")  # every flag below is an option of the run, not an argument
        self.assertEqual([core[i + 1] for i, item in enumerate(core) if item == "--security-opt"],
                         ["no-new-privileges"])

    def test_checkout_change_during_the_admission_wait_refuses_before_any_container(self):
        for changed in ("head", "worktree"):
            with self.subTest(changed=changed):
                calls, _inputs, _assets = self.run_lane(changed=changed, refusal="checkout_changed_during_admission")
                self.assertTrue(self.admitted)  # the change is seen by the recheck, not by the first check
                self.assertEqual(claims(calls), [])
                # The run directory was claimed and holds nothing: no Core configuration, no receipt.
                self.assertEqual(self.created, [os.path.join(".artifacts", "run")])

    def test_run_directory_outside_the_artifact_directory_or_through_a_link_is_refused(self):
        def elsewhere(root):
            (root / "elsewhere").mkdir()
            return root / "elsewhere" / "run"

        def sibling(root):
            (root / ".artifacts-other").mkdir()
            return root / ".artifacts-other" / "run"

        def parent_step(root):
            (root / "elsewhere").mkdir()
            return root / ".artifacts" / ".." / "elsewhere" / "run"

        def link_inside(root):
            (root / ".artifacts" / "real").mkdir()
            (root / ".artifacts" / "link").symlink_to(root / ".artifacts" / "real", target_is_directory=True)
            return root / ".artifacts" / "link" / "run"

        def link_outside(root):
            (root / "elsewhere").mkdir()
            (root / ".artifacts" / "link").symlink_to(root / "elsewhere", target_is_directory=True)
            return root / ".artifacts" / "link" / "run"

        def linked_run(root):
            (root / "elsewhere").mkdir()
            (root / ".artifacts" / "run").symlink_to(root / "elsewhere" / "run")  # dangling until created
            return root / ".artifacts" / "run"

        for case in (elsewhere, sibling, parent_step, link_inside, link_outside, linked_run,
                     lambda root: root / ".artifacts", lambda root: root):
            with self.subTest(case=case.__name__):
                calls, _inputs, _assets = self.run_lane(run_dir=case, refusal="new_task_artifact_directory_required")
                self.assertEqual(self.created, [])  # nothing was made anywhere, through a link or not
                self.assertFalse(self.admitted)
                self.assertEqual(claims(calls), [])

    def test_stub_address_outside_the_lane_network_never_starts_core(self):
        cases = [{"stub_networks": networks} for networks in (
            {}, {lane.NETWORK: {}}, {lane.NETWORK: {"IPAddress": ""}}, {lane.NETWORK: {"IPAddress": None}},
            {lane.NETWORK: {"IPAddress": 2887648515}}, {lane.NETWORK: {"IPAddress": "172.31.0.3"}},
            {lane.NETWORK: {"IPAddress": "172.30.5.0"}}, {lane.NETWORK: {"IPAddress": "172.30.5.255"}},
            {lane.NETWORK: {"IPAddress": "172.30.5.1"}}, {lane.NETWORK: {"IPAddress": "172.30.5.03"}},
            {lane.NETWORK: {"IPAddress": "172.30.5.3 "}}, {lane.NETWORK: {"IPAddress": "192.0.2.7"}},
            {lane.NETWORK: {"IPAddress": "127.0.0.1"}}, {lane.NETWORK: {"IPAddress": "fd00::3"}},
            {lane.NETWORK: {"IPAddress": "example.invalid"}},
            {"unowned": {"IPAddress": STUB_ADDRESS}},
            {lane.NETWORK: {"IPAddress": STUB_ADDRESS}, "bridge": {"IPAddress": "172.17.0.2"}})]
        cases += [{"ipam": ipam} for ipam in (
            [], [{}], [{"Subnet": None}], [{"Subnet": "not-a-subnet"}], [{"Subnet": "172.31.0.0/24"}],
            [{"Subnet": "192.0.2.0/24"}], [{"Subnet": "fd00::/64"}], ["172.30.5.0/24"],
            [dict(LANE_SUBNET), {"Subnet": "172.31.0.0/24"}],
            [{"Subnet": "172.30.5.0/24", "Gateway": STUB_ADDRESS}])]
        # An address inside the reported subnet is still refused when that subnet is not private.
        cases += [{"stub_networks": {lane.NETWORK: {"IPAddress": prefix + "3"}},
                   "ipam": [{"Subnet": prefix + "0/24", "Gateway": prefix + "1"}]}
                  for prefix in ("100.64.0.", "11.0.0.")]
        for case in cases:
            with self.subTest(case=case):
                code, result, calls = self.run_lane(**case)
                self.assertEqual(code, 1)
                self.assertEqual(result["result"], "WITHHELD")
                self.assertEqual(result["failure_type"], "Refusal")
                self.assertEqual((result["failure"]["stage"], result["failure"]["check"]),
                                 ("container_start", "port_mapping"))
                self.assertTrue(result["cleanup"]["clean"])
                self.assertNotIn("stub_host_entry", result)
                started = [call[call.index("--name") + 1] for call in calls if call[:2] == ("docker", "run")]
                self.assertEqual(started, [lane.STUB])
                self.assertFalse(any("--add-host" in call for call in calls))

    def test_stub_address_refusal_reason_is_fixed(self):
        network = {"IPAM": {"Config": [dict(LANE_SUBNET)]}}
        good = {"NetworkSettings": {"Networks": {lane.NETWORK: {"IPAddress": STUB_ADDRESS}}}}
        with patch.object(lane, "inspect_owned", return_value=good) as inspect:
            self.assertEqual(lane.stub_address("run", network), STUB_ADDRESS)
            inspect.assert_called_once_with("container", lane.STUB, "run")
        both = {"NetworkSettings": {"Networks": {lane.NETWORK: {"IPAddress": STUB_ADDRESS},
                                                 "bridge": {"IPAddress": "172.17.0.2"}}}}
        for data, facts in ((None, network), (good, None), (good, {}), (good, {"IPAM": None}), (both, network),
                            ({"NetworkSettings": {"Networks": {lane.NETWORK: {"IPAddress": "192.0.2.7"}}}}, network)):
            with self.subTest(data=data, facts=facts), patch.object(lane, "inspect_owned", return_value=data):
                with self.assertRaises(lane.Refusal) as caught:
                    lane.stub_address("run", facts)
                self.assertEqual(caught.exception.args, ("stub_address_not_on_lane_network",))

    def test_admission_timeout_creates_refusal_receipt_without_containers(self):
        self.run_lane(admission_error=lane.resources.Refusal("host_admission_timeout"))

    def test_explicit_memory_override(self):
        code, result, calls = self.run_lane(memory_args=("--core-memory", "3g", "--upstream-memory", "1g",
                                                         "--stub-memory", "128m", "--fixture-memory", "256m"))
        self.assertEqual(code, 0)
        self.assertEqual(result["container_memory"], {"core": "3g", "upstream": "1g", "stub": "128m", "fixture": "256m"})
        limits = {call[call.index("--name") + 1]: call[call.index("--memory") + 1]
                  for call in calls if call[:2] == ("docker", "run")}
        self.assertEqual(limits, {lane.CORE: "3g", lane.STUB: "128m"})

    def test_profile_mismatch_refuses_before_inputs_images_or_containers(self):
        calls, inputs, assets = self.run_lane(profile_error=True, refusal="profile_fingerprint_mismatch")
        inputs.assert_not_called()
        assets.assert_not_called()
        self.assertTrue(all(call[0] == "git" for call in calls), calls)

    def test_real_precheck_runs_inside_main(self):
        changed = SimpleNamespace(to_mapping=lambda: {**lane.PROFILE, "contract_fingerprint": "sha256:" + "0" * 64})
        with patch.object(profiles_module, "CORE_DEVICE_CONFIGURATION_PROFILES", (changed,)):
            calls, inputs, _assets = self.run_lane(refusal="profile_fingerprint_mismatch")
        inputs.assert_not_called()
        self.assertTrue(all(call[0] == "git" for call in calls), calls)

    def test_stub_asset_mismatch_refuses_before_images_or_containers(self):
        for code in ("stub_asset_hash_mismatch", "stub_dump_source_mismatch"):
            with self.subTest(code=code):
                calls, _inputs, _assets = self.run_lane(assets_error=lane.Refusal(code), refusal=code)
                self.assertTrue(all(call[0] == "git" for call in calls), calls)

    def test_receipt_for_another_profile_or_other_stub_bytes_is_withheld(self):
        good = {"result": "PASS", "profile": dict(lane.PROFILE), "stub": json.loads(json.dumps(RECEIPT_STUB))}
        cases = [{**good, "profile": {**lane.PROFILE, key: "other"}} for key in lane.PROFILE]
        cases += [{key: value for key, value in good.items() if key != "profile"},
                  {key: value for key, value in good.items() if key != "stub"},
                  {**good, "stub": {**RECEIPT_STUB, "stub_sha256": "9" * 64}},
                  {**good, "stub": {**RECEIPT_STUB, "entry_sha256": "9" * 64}},
                  {**good, "stub": {**RECEIPT_STUB, "dump_hashes": {"synthetic.json": "9" * 64}}},
                  {**good, "stub": {**RECEIPT_STUB, "dump_hashes": {}}}]
        for outcome in cases:
            with self.subTest(outcome=outcome):
                code, result, _calls = self.run_lane(outcome=outcome)
                self.assertEqual(code, 1)
                self.assertEqual(result["result"], "WITHHELD")
                self.assertEqual(result["failure_type"], "Refusal")
                self.assertTrue(result["cleanup"]["clean"])
                self.assertNotIn("operations", result)

    def test_missing_or_wildcard_mapping_refuses_before_readiness_and_cleans(self):
        for name, inner, outer in ((lane.CORE, "8123/tcp", "18297"), (lane.STUB, "5580/tcp", "18299")):
            for ports in ({}, {inner: None}, {inner: [{"HostIp": "0.0.0.0", "HostPort": outer}]},
                          {inner: [{"HostIp": "127.0.0.1", "HostPort": "9"}]},
                          {inner: [{"HostIp": "127.0.0.1", "HostPort": outer}],
                           "9999/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9999"}]}):
                with self.subTest(name=name, ports=ports):
                    code, result, calls = self.run_lane(ports_override=(name, ports))
                    self.assertEqual(code, 1)
                    self.assertEqual(result["result"], "WITHHELD")
                    self.assertEqual((result["failure"]["stage"], result["failure"]["check"]),
                                     ("container_start", "port_mapping"))
                    self.assertTrue(result["cleanup"]["clean"])
                    if name == lane.STUB:  # Core never starts beside an unsafe stub
                        self.assertFalse(any(call[:2] == ("docker", "run") and lane.CORE in call for call in calls))

    def test_wrong_network_posture_refuses_before_readiness(self):
        for network in ({"Driver": "bridge", "Internal": True}, {"Driver": "host", "Internal": False}, {}):
            with self.subTest(network=network):
                code, result, calls = self.run_lane(network_override=network)
                self.assertEqual(code, 1)
                self.assertEqual(result["failure"]["check"], "network_create")
                self.assertTrue(result["cleanup"]["clean"])
                self.assertFalse(any(call[:2] == ("docker", "run") for call in calls))

    def test_lane_failure_withholds_and_still_cleans(self):
        for error in (AssertionError("synthetic-private-text"), ValueError("core_url_not_pinned"),
                      asyncio.TimeoutError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                code, result, calls = self.run_lane(exercise_error=error)
                self.assertEqual(code, 1)
                self.assertEqual(result["result"], "WITHHELD")
                self.assertTrue(result["cleanup"]["clean"])
                self.assertNotIn("synthetic-private-text", json.dumps(result))
                self.assertNotIn("operations", result)
                self.assertEqual(sorted(call[-1] for call in calls if call[:2] == ("docker", "rm")),
                                 sorted([lane.CORE, lane.STUB]))

    def test_cleanup_failure_overrides_pass(self):
        code, result, _calls = self.run_lane(cleanup_error=True)
        self.assertEqual(code, 1)
        self.assertEqual(result["result"], "WITHHELD")
        self.assertFalse(result["cleanup"]["clean"])

    def test_dirty_checkout_never_starts(self):
        self.run_lane(dirty=True, refusal="clean_committed_checkout_required")

    def test_prior_run_is_preserved(self):
        self.run_lane(existing=True, refusal="disposable_name_already_exists")

    def test_ci_context_is_refused(self):
        for variable in ({"GITHUB_ACTIONS": "true"}, {"CORE_EVIDENCE_CONTEXT": "synthetic"}):
            with self.subTest(variable=variable), patch.object(lane, "command") as command, \
                    patch.dict(os.environ, variable, clear=True), patch("sys.stdout", new_callable=io.StringIO):
                with self.assertRaises(lane.Refusal) as caught:
                    lane.main(["--mode", "local", "--pull", "never", "--journal", "j", "--source-archive", "a",
                               "--run-dir", "unused"])
                self.assertEqual(caught.exception.args, ("local_only_closed_lane",))
                command.assert_not_called()


class PortMappings(unittest.TestCase):
    CASES = ((lane.CORE, 8123, lane.CORE_PORT), (lane.UPSTREAM, lane.UPSTREAM_PORT, lane.UPSTREAM_PORT),
             (lane.STUB, lane.STUB_CONTAINER_PORT, lane.STUB_PORT))

    def test_actual_mapping_for_all_three_containers(self):
        for name, inner, outer in self.CASES:
            data = {"NetworkSettings": {"Networks": {lane.NETWORK: {}},
                    "Ports": {f"{inner}/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(outer)}]}}}
            with self.subTest(name=name), patch.object(lane, "inspect_owned", return_value=data) as inspect:
                lane.verify_port_mapping(name, inner, outer, "run")
                inspect.assert_called_once_with("container", name, "run")

    def test_missing_wrong_extra_or_nonloopback_actual_bindings_refuse(self):
        for name, inner, outer in self.CASES:
            good, key = {"HostIp": "127.0.0.1", "HostPort": str(outer)}, f"{inner}/tcp"
            invalid = [{}, {key: None}, {key: []}, {f"{inner}/udp": [good]}, {key: [good, good]},
                       {key: [good], "9999/tcp": [good]}, {key: [{"HostIp": "127.0.0.1", "HostPort": "9999"}]}]
            invalid += [{key: [{"HostIp": ip, "HostPort": str(outer)}]} for ip in ("0.0.0.0", "::", "", "192.0.2.1")]
            for ports in invalid:
                data = {"HostConfig": {"PortBindings": {key: [good]}},
                        "NetworkSettings": {"Networks": {lane.NETWORK: {}}, "Ports": ports}}
                with self.subTest(name=name, ports=ports), diagnostics.lane(lane.GROUP), \
                        patch.object(lane, "inspect_owned", return_value=data):
                    with self.assertRaises(lane.Refusal) as caught:
                        lane.verify_port_mapping(name, inner, outer, "run")
                    self.assertEqual(caught.exception._local_lane_check, ("container_start", "port_mapping"))

    def test_missing_container_or_other_network_refuses(self):
        ports = {"5580/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18299"}]}
        for data in (None, {"NetworkSettings": {"Networks": {"unowned": {}}, "Ports": ports}},
                     {"NetworkSettings": {"Networks": {lane.NETWORK: {}, "bridge": {}}, "Ports": ports}}):
            with self.subTest(data=data), patch.object(lane, "inspect_owned", return_value=data):
                with self.assertRaises(lane.Refusal):
                    lane.verify_port_mapping(lane.STUB, 5580, 18299, "run")

    def test_inspection_error_is_not_absence_and_wrong_owner_is_refused(self):
        failed = subprocess.CompletedProcess([], 1, b"")
        with patch.object(lane, "command", side_effect=[failed, lane.Refusal("daemon_failed")]):
            with self.assertRaises(lane.Refusal):
                lane.inspect_owned("container", "owned", "id")
        data = {"Config": {"Labels": {lane.LABEL: "another-run"}}}
        with patch.object(lane, "command", return_value=subprocess.CompletedProcess([], 0, json.dumps([data]).encode())):
            with self.assertRaisesRegex(lane.Refusal, "resource_ownership_mismatch"):
                lane.inspect_owned("container", "owned", "id")


    def test_a_listed_resource_that_cannot_be_inspected_is_not_absent(self):
        failed = subprocess.CompletedProcess([], 1, b"")
        for kind, listing in (("container", ("ls", "-a", "--format", "{{.Names}}")),
                              ("network", ("ls", "--format", "{{.Name}}"))):
            with self.subTest(kind=kind):
                listed = subprocess.CompletedProcess([], 0, b"another\nowned\n")
                with patch.object(lane, "command", side_effect=[failed, listed]) as command:
                    with self.assertRaises(lane.Refusal) as caught:
                        lane.inspect_owned(kind, "owned", "id")
                self.assertEqual(caught.exception.args, ("owned_resource_inspection_failed",))
                self.assertEqual([(call.args, call.kwargs) for call in command.call_args_list],
                                 [(("docker", kind, "inspect", "owned"), {"check": False}),
                                  (("docker", kind, *listing), {})])
                # The same failed inspection is absence only when a successful listing lacks the exact name.
                others = subprocess.CompletedProcess([], 0, b"another\nowned-2\nnot-owned\n")
                with patch.object(lane, "command", side_effect=[failed, others]):
                    self.assertIsNone(lane.inspect_owned(kind, "owned", "id"))


class HostPreflight(unittest.TestCase):
    """Host checks that every orchestration test replaces: the ports and the cached images."""

    def test_an_occupied_lane_port_is_refused(self):
        self.assertEqual(lane.PORTS, (18297, 18298, 18299))
        # Real loopback sockets on ports the system chose; never one of the lane's own ports.
        with socket.socket() as held, socket.socket() as spare:
            held.bind(("127.0.0.1", 0))
            spare.bind(("127.0.0.1", 0))
            occupied, free = held.getsockname()[1], spare.getsockname()[1]
            spare.close()
            self.assertFalse({occupied, free} & set(lane.PORTS))
            for ports in ((occupied,), (free, occupied), (occupied, free), (free, free, occupied)):
                with self.subTest(ports=ports), patch.object(lane, "PORTS", ports):
                    with self.assertRaises(OSError) as caught:
                        lane.require_free_ports()
                    self.assertEqual(caught.exception.errno, errno.EADDRINUSE)
            with patch.object(lane, "PORTS", (free,)):
                lane.require_free_ports()
                lane.require_free_ports()  # the check holds nothing after it returns
        # Once the holder is gone the same port passes: the refusal was the occupied port.
        with patch.object(lane, "PORTS", (occupied, free)):
            lane.require_free_ports()

    def test_cached_image_without_the_pinned_digest_is_refused(self):
        pinned, other = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        image = "registry.example/synthetic:1.0@" + pinned

        def inspected(digests):
            value = {"Id": "sha256:synthetic-config"}
            if digests is not None:
                value["RepoDigests"] = digests
            return patch.object(lane, "command", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps([value]).encode()))

        for digests in (None, [], ["registry.example/synthetic@" + other],
                        ["registry.example/synthetic@" + other, "mirror.example/synthetic@" + other],
                        ["registry.example/synthetic@" + pinned[:-1]], ["registry.example/synthetic@" + pinned + "0"],
                        ["registry.example/synthetic@" + pinned.upper()], ["registry.example/synthetic:1.0@sha512:" + "a" * 64]):
            with self.subTest(digests=digests), diagnostics.lane(lane.GROUP), inspected(digests) as command:
                with self.assertRaises(lane.Refusal) as caught:
                    lane.cached_image(image)
                self.assertEqual(caught.exception.args, ("cached_image_digest_mismatch",))
                self.assertEqual(caught.exception._local_lane_check, ("image_verify", "cached_image_digest"))
                command.assert_called_once_with("docker", "image", "inspect", image)
        # The digest decides, under whichever repository name the cache lists it.
        with inspected(["mirror.example/other@" + other, "mirror.example/synthetic@" + pinned]):
            self.assertEqual(lane.cached_image(image), "sha256:synthetic-config")


class Exercise(unittest.IsolatedAsyncioTestCase):
    MEMORY = {"core": "2g", "upstream": "768m", "stub": "256m", "fixture": "128m"}

    def identity(self, **changes):
        images = {lane.CORE: "core@sha256:a", lane.UPSTREAM: "up@sha256:b", lane.STUB: "core@sha256:a"}
        limits = {lane.CORE: "2g", lane.UPSTREAM: "768m", lane.STUB: "256m"}
        ports = {lane.UPSTREAM: {f"{lane.UPSTREAM_PORT}/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(lane.UPSTREAM_PORT)}]}}

        def inspect(kind, name, run_id):
            data = {"Image": "config", "Config": {"Image": images[name]},
                    "HostConfig": {"Memory": lane.host.memory_bytes(limits[name])},
                    "NetworkSettings": {"Networks": {lane.NETWORK: {}}, "Ports": ports.get(name, {})}}
            for path, value in changes.get(name, {}).items():
                target = data
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = value
            return data
        return inspect

    async def run_exercise(self, *, configured=None, inspect=None, ready_error=None, observed=None):
        import real_ha_contract_tests as harness
        self.order = []

        async def started(token, **kwargs):
            self.assertEqual(token, TOKEN)
            self.assertEqual(kwargs["pull_policy"], "never")
            self.assertEqual(kwargs["memory"], "768m")
            kwargs["verify_started"]()
            self.order.append("upstream")

        async def ready(url):
            self.order.append(("stub_ready", url))
            if ready_error:
                raise ready_error

        async def accept(configured, entry, directory, **kwargs):
            self.order.append(("run", kwargs))
            return {"result": "PASS", "profile": dict(lane.PROFILE)}

        with tempfile.TemporaryDirectory() as directory, diagnostics.lane(lane.GROUP), \
                patch.object(harness, "bootstrap_disposable_admin", new=AsyncMock(return_value=TOKEN)), \
                patch.object(harness, "settings", return_value=configured or settings()), \
                patch.object(harness, "wait_for_runtime_ready",
                             new=AsyncMock(return_value={"version": lane.VERSION} if observed is None
                                           else observed)) as self.runtime_ready, \
                patch.object(harness, "_start_exact_upstream", side_effect=started), \
                patch.object(lane, "inspect_owned", side_effect=inspect or self.identity()), \
                patch.object(lane, "cached_image", return_value="config"), \
                patch.object(acceptance, "stub_ready", side_effect=ready), \
                patch.object(acceptance, "run", side_effect=accept):
            return await lane.exercise(Path(directory), {}, "core@sha256:a", "up@sha256:b", "run",
                                       self.MEMORY, json.loads(json.dumps(STUB_HASHES)))

    async def test_pass_checks_identity_then_stub_then_acceptance(self):
        result = await self.run_exercise()
        self.assertEqual(self.order[0], "upstream")
        self.assertEqual(self.order[1], ("stub_ready", acceptance.STUB_CONTROL_URL))
        self.assertEqual(self.order[2][0], "run")
        self.assertEqual(self.order[2][1], {"stub_url": acceptance.STUB_CONTROL_URL, "stub_hashes": RECEIPT_STUB})
        self.assertEqual(set(result["images"]), {lane.CORE, lane.UPSTREAM, lane.STUB})
        self.assertEqual(result["images"][lane.STUB]["image"], "core@sha256:a")

    async def test_unpinned_core_settings_refuse_before_readiness(self):
        for url in ("http://127.0.0.1:8123", "http://evil.example:18297", "http://localhost:18297"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as caught:
                    await self.run_exercise(configured=settings(ha_url=url))
                self.assertEqual(caught.exception.args, ("core_url_not_pinned",))
                self.runtime_ready.assert_not_called()
                self.assertEqual(self.order, [])

    async def test_another_running_core_version_refuses_before_the_upstream_starts(self):
        for observed in ({"version": "2026.10.4"}, {"version": "2026.11.1"}, {"version": "2026.11"},
                         {"version": "2026.11.0 "}, {"version": "2026.11.0.dev0"}, {"version": None},
                         {"version": 2026}, {"core_version": lane.VERSION}, {}):
            with self.subTest(observed=observed):
                with self.assertRaises(lane.Refusal) as caught:
                    await self.run_exercise(observed=observed)
                self.assertEqual(caught.exception.args, ("running_core_version_mismatch",))
                self.assertEqual(caught.exception._local_lane_check, ("image_verify", "running_identity"))
                self.runtime_ready.assert_awaited_once()
                self.assertEqual(self.order, [])  # no upstream, no stub use, no acceptance run

    async def test_upstream_mapping_guard_runs_before_acceptance(self):
        with self.assertRaises(lane.Refusal) as caught:
            await self.run_exercise(inspect=lambda kind, name, run_id: None)
        self.assertEqual(caught.exception._local_lane_check, ("container_start", "port_mapping"))
        self.assertEqual(self.order, [])

    async def test_wrong_image_or_memory_on_any_container_refuses_before_the_stub_is_used(self):
        for name in (lane.CORE, lane.UPSTREAM, lane.STUB):
            for path, value, code in ((("Config", "Image"), "other@sha256:c", "running_image_mismatch"),
                                      (("Image",), "other-config", "running_image_mismatch"),
                                      (("HostConfig", "Memory"), 1, "running_container_memory_mismatch"),
                                      (("HostConfig", "Memory"), 0, "running_container_memory_mismatch")):
                with self.subTest(name=name, path=path, value=value):
                    with self.assertRaises(lane.Refusal) as caught:
                        await self.run_exercise(inspect=self.identity(**{name: {path: value}}))
                    self.assertEqual(caught.exception.args, (code,))
                    self.assertEqual(self.order, ["upstream"])

    async def test_stub_that_never_answers_stops_before_acceptance(self):
        with self.assertRaises(AssertionError):
            await self.run_exercise(ready_error=AssertionError("synthetic-private-text"))
        self.assertEqual([item[0] for item in self.order[1:]], ["stub_ready"])


class UrlGuards(unittest.IsolatedAsyncioTestCase):
    """Every client dials a loopback constant or refuses with a fixed code before any I/O."""

    def assert_code(self, caught, code):
        exc = caught.exception
        self.assertIs(type(exc), ValueError)
        self.assertEqual(exc.args, (code,))
        self.assertEqual(str(exc), code)
        self.assertEqual(len(chain(exc)), 1)

    def test_any_core_url_but_the_constant_is_refused_before_any_client(self):
        builders = (acceptance.pinned, acceptance.LaneWebSocket, acceptance.LaneRest, acceptance.RawCore,
                    acceptance.RawRest, acceptance.LaneState)
        for url in url_cases(acceptance.CORE_URL):
            for build in builders:
                with self.subTest(url=url, build=build.__name__), NoTransport() as transport:
                    with self.assertRaises(ValueError) as caught:
                        build(settings(ha_url=url))
                    self.assert_code(caught, "core_url_not_pinned")
                    self.assertTrue(transport.untouched())

    def test_derived_urls_must_also_be_the_constants(self):
        good = {"ha_url": acceptance.CORE_URL, "api_url": acceptance.CORE_API_URL,
                "websocket_url": acceptance.CORE_WS_URL, "ha_token": TOKEN}
        bad = [{**good, "api_url": url} for url in url_cases(acceptance.CORE_API_URL)]
        bad += [{**good, "websocket_url": url} for url in url_cases(acceptance.CORE_WS_URL)]
        for fields in bad:
            for build in (acceptance.pinned, acceptance.LaneWebSocket, acceptance.LaneRest, acceptance.RawCore,
                          acceptance.RawRest, acceptance.LaneState):
                with self.subTest(fields=fields, build=build.__name__), NoTransport() as transport:
                    with self.assertRaises(ValueError) as caught:
                        build(SimpleNamespace(**fields))
                    self.assert_code(caught, "core_url_not_pinned")
                    self.assertTrue(transport.untouched())

    def test_a_refused_url_is_never_parsed(self):
        class Unparsed:
            ha_url = "http://evil.example:18297"

            @property
            def api_url(self):
                raise AssertionError("parsed a refused URL")

            websocket_url = api_url
        with self.assertRaises(ValueError) as caught:
            acceptance.pinned(Unparsed())
        self.assert_code(caught, "core_url_not_pinned")

    def test_a_str_subclass_equal_to_the_core_constant_is_refused(self):
        configured = settings(ha_url=Text(acceptance.CORE_URL))
        # Equal in every compared value; only its exact type differs from the constant.
        self.assertIs(type(configured.ha_url), Text)
        self.assertEqual((configured.ha_url, configured.api_url, configured.websocket_url),
                         (acceptance.CORE_URL, acceptance.CORE_API_URL, acceptance.CORE_WS_URL))
        for build in (acceptance.pinned, acceptance.LaneWebSocket, acceptance.LaneRest, acceptance.RawCore,
                      acceptance.RawRest, acceptance.LaneState):
            with self.subTest(build=build.__name__), NoTransport() as transport:
                with self.assertRaises(ValueError) as caught:
                    build(configured)
                self.assert_code(caught, "core_url_not_pinned")
                self.assertTrue(transport.untouched())

    def test_a_str_subclass_equal_to_the_upstream_constant_is_refused(self):
        configured = settings(upstream=Text(acceptance.UPSTREAM_URL))
        self.assertIs(type(configured.upstream_dashboard_mcp_url), Text)
        self.assertEqual(configured.upstream_dashboard_mcp_url, acceptance.UPSTREAM_URL)
        with self.assertRaises(ValueError) as caught:
            acceptance.pinned_upstream(configured)
        self.assert_code(caught, "upstream_url_not_pinned")

    async def test_a_str_subclass_equal_to_the_stub_constant_is_refused_before_a_session(self):
        url = Text(acceptance.STUB_CONTROL_URL)
        self.assertEqual(url, acceptance.STUB_CONTROL_URL)
        with NoTransport() as transport:
            with self.assertRaises(ValueError) as caught:
                acceptance.StubControl(url)
            self.assert_code(caught, "stub_url_not_pinned")
            with self.assertRaises(ValueError) as caught:
                await acceptance.stub_ready(url)
            self.assert_code(caught, "stub_url_not_pinned")
            self.assertTrue(transport.untouched())

    def test_any_upstream_url_but_the_constant_is_refused(self):
        for url in url_cases(acceptance.UPSTREAM_URL):
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as caught:
                    acceptance.pinned_upstream(settings(upstream=url))
                self.assert_code(caught, "upstream_url_not_pinned")

    async def test_any_stub_url_but_the_constant_is_refused_before_a_session(self):
        for url in url_cases(acceptance.STUB_CONTROL_URL) + ["http://127.0.0.1:18299/control",
                                                             "http://127.0.0.1:18299/ws",
                                                             "http://hamcp366-matter-stub:5580/_lane/control"]:
            with self.subTest(url=url), NoTransport() as transport:
                with self.assertRaises(ValueError) as caught:
                    acceptance.StubControl(url)
                self.assert_code(caught, "stub_url_not_pinned")
                with self.assertRaises(ValueError) as caught:
                    await acceptance.stub_ready(url)
                self.assert_code(caught, "stub_url_not_pinned")
                self.assertTrue(transport.untouched())

    def test_refusal_text_carries_neither_url_nor_token(self):
        url = "http://user:synthetic-password@127.0.0.1:18297@evil.example/api?k=synthetic-query"
        attempts = ((acceptance.RawCore, (settings(ha_url=url),)), (acceptance.LaneWebSocket, (settings(ha_url=url),)),
                    (acceptance.pinned_upstream, (settings(upstream=url),)), (acceptance.StubControl, (url,)))
        for build, args in attempts:
            with self.subTest(build=build.__name__), NoTransport():
                with self.assertRaises(ValueError) as caught:
                    build(*args)
                shown = str(caught.exception) + repr(caught.exception) + repr(caught.exception.__dict__)
                for private in (TOKEN, url, "evil.example", "synthetic-query", "synthetic-password", "127.0.0.1",
                                "http://", "18297"):
                    self.assertNotIn(private, shown)

    def test_pinned_settings_carry_the_constants_to_the_product_clients(self):
        configured = settings()
        safe = acceptance.pinned(configured)
        self.assertEqual((safe.ha_url, safe.api_url, safe.websocket_url),
                         (acceptance.CORE_URL, acceptance.CORE_API_URL, acceptance.CORE_WS_URL))
        self.assertEqual(acceptance.pinned_upstream(safe).upstream_dashboard_mcp_url, acceptance.UPSTREAM_URL)
        with NoTransport() as transport:
            acceptance.LaneWebSocket(configured)
            acceptance.LaneRest(configured)
            acceptance.LaneState(configured)
            self.assertEqual((transport.websocket.call_count, transport.rest.call_count), (1, 2))
            for call in transport.websocket.call_args_list + transport.rest.call_args_list:
                (given,), _kwargs = call
                self.assertEqual((given.ha_url, given.api_url, given.websocket_url),
                                 (acceptance.CORE_URL, acceptance.CORE_API_URL, acceptance.CORE_WS_URL))
            transport.session.assert_not_called()

    async def test_raw_websocket_dials_the_constant_without_proxies(self):
        events, payload = [], {"type": "config_entries/get", "domain": "matter"}
        replies = [{"type": "auth_required"}, {"type": "auth_ok"}, {"type": "result", "id": 1, "success": True}]
        proxies = {"HTTP_PROXY": "http://evil.example:3128", "ALL_PROXY": "http://evil.example:3128",
                   "WS_PROXY": "http://evil.example:3128"}
        with patch.dict(os.environ, proxies), patch.object(
                acceptance.aiohttp, "ClientSession", new=fake_session(events, replies=replies)):
            reply = await acceptance.RawCore(settings()).command(payload)
        self.assertTrue(reply["success"])
        self.assertEqual(events, [("session", False), ("connect", "ws://127.0.0.1:18297/api/websocket"),
                                  ("send", {"type": "auth", "access_token": TOKEN}), ("send", {"id": 1, **payload})])

    async def test_raw_rest_dials_the_constant_without_proxies_or_redirects(self):
        events, reload = [], "/api/config/config_entries/entry/01JSYNTHETICSYNTHETICSYNTH/reload"
        proxies = {"HTTP_PROXY": "http://evil.example:3128", "ALL_PROXY": "http://evil.example:3128"}
        with patch.dict(os.environ, proxies), patch.object(
                acceptance.aiohttp, "ClientSession", new=fake_session(events, value={"require_restart": False})):
            status, value = await acceptance.RawRest(settings()).rest("POST", reload)
        self.assertEqual((status, value), (200, {"require_restart": False}))
        (session, request) = events
        self.assertEqual(session, ("session", False))
        self.assertEqual(request[:3], ("request", "POST", "http://127.0.0.1:18297" + reload))
        self.assertIs(request[3]["allow_redirects"], False)
        self.assertEqual(request[3]["headers"], {"Authorization": "Bearer " + TOKEN})
        self.assertNotIn("json", request[3])

    async def test_stub_client_dials_the_constant_without_proxies_or_redirects(self):
        events = []
        with patch.object(acceptance.aiohttp, "ClientSession", new=fake_session(events, value={"writes": []})):
            control = acceptance.StubControl(acceptance.STUB_CONTROL_URL)
            self.assertEqual(await control.read(), {"writes": []})
            await control.post({"mode": "error"})
            self.assertEqual(await acceptance.stub_ready(acceptance.STUB_CONTROL_URL), {"writes": []})
        self.assertEqual([event for event in events if event[0] == "session"], [("session", False)] * 3)
        requests = [event for event in events if event[0] == "request"]
        self.assertEqual([(event[1], event[2]) for event in requests],
                         [("GET", "http://127.0.0.1:18299/_lane/control"),
                          ("POST", "http://127.0.0.1:18299/_lane/control"),
                          ("GET", "http://127.0.0.1:18299/_lane/control")])
        self.assertTrue(all(event[3]["allow_redirects"] is False for event in requests))
        self.assertEqual(requests[1][3]["json"], {"mode": "error"})

    async def test_stub_redirect_or_error_status_is_a_failure_not_a_follow(self):
        for status in (301, 302, 400, 500):
            with self.subTest(status=status), patch.object(
                    acceptance.aiohttp, "ClientSession", new=fake_session([], status=status, value={})):
                with self.assertRaises(AssertionError):
                    await acceptance.StubControl(acceptance.STUB_CONTROL_URL).read()


class CommandGuards(unittest.IsolatedAsyncioTestCase):
    """Only exact shapes on the closed lists leave the lane; all else is refused unsent."""

    FLOW = "/api/config/config_entries/flow"
    ENTRY = "/api/config/config_entries/entry/01JSYNTHETICSYNTHETICSYNTH/reload"

    def test_matcher_is_exact_in_keys_values_and_types(self):
        specs = ({"type": "a", "n": 1}, {"type": "b", "inner": {"flag": True}},
                 {"type": "c", "value": acceptance._one_of(1.0, 2.0)}, {"type": "d", "n": acceptance._int(1, 3)})
        for value in ({"type": "a", "n": 1}, {"n": 1, "type": "a"}, {"type": "b", "inner": {"flag": True}},
                      {"type": "c", "value": 2.0}, {"type": "d", "n": 3}):
            self.assertTrue(acceptance.listed(value, specs), value)
        for value in ({"type": "a"}, {"type": "a", "n": 1, "x": 1}, {"type": "a", "n": True}, {"type": "a", "n": 1.0},
                      {"type": "a", "n": "1"}, {"type": "b", "inner": {"flag": 1}}, {"type": "b", "inner": {}},
                      {"type": "b", "inner": {"flag": True, "x": 1}}, {"type": "b", "inner": None},
                      {"type": "c", "value": 2}, {"type": "c", "value": True}, {"type": "c", "value": 3.0},
                      {"type": "d", "n": 0}, {"type": "d", "n": 4}, {"type": "d", "n": True}, {"type": "d", "n": 2.0},
                      {"type": "e"}, {}, None, [], "a", [("type", "a"), ("n", 1)]):
            self.assertFalse(acceptance.listed(value, specs), value)
        entity = acceptance.ENTITY
        for value in ("select.a", "switch.a_1", "number." + "a" * 120):
            self.assertTrue(entity(value), value)
        for value in ("light.a", "select.", "select.A", "select.a\n", "select.a/../b", "select.a?x", " select.a",
                      "number." + "a" * 121, "select.a.b", b"select.a", None, 1):
            self.assertFalse(entity(value), value)

    async def raw_refused(self, payload):
        with NoTransport() as transport:
            with self.assertRaises(ValueError) as caught:
                await acceptance.RawCore(settings()).command(payload)
            self.assertIs(type(caught.exception), ValueError)
            self.assertEqual(caught.exception.args, ("core_command_not_listed",))
            self.assertEqual(len(chain(caught.exception)), 1)
            self.assertTrue(transport.untouched())
        return caught.exception

    async def test_raw_websocket_refuses_unlisted_commands_before_a_session(self):
        update = {"type": "config/entity_registry/update", "entity_id": "number.hamcp366_converter_number",
                  "options_domain": "number", "options": {"unit_of_measurement": "°F"}}
        refused = [
            {"type": "call_service", "domain": "switch", "service": "turn_on", "target": {"entity_id": acceptance.SWITCH}},
            {"type": "subscribe_events"}, {"type": "auth", "access_token": TOKEN}, {"type": "config/core/update"},
            {"type": "config/entity_registry/list", "extra": 1}, {"id": 5, "type": "config/device_registry/list"},
            {"type": "config/entity_registry/get"}, {"type": "config/entity_registry/get", "entity_id": "light.kitchen"},
            {"type": "config/entity_registry/get", "entity_id": acceptance.SELECT, "extra": 1},
            {"type": "config/entity_registry/get", "entity_id": [acceptance.SELECT]},
            {"type": "config/entity_registry/remove", "entity_id": acceptance.SWITCH},
            {"type": "config/entity_registry/remove", "entity_id": acceptance.NUMBER},
            {"type": "config/entity_registry/remove"},
            {"type": "config/device_registry/remove_config_entry", "device_id": "synthetic"},
            {**update, "entity_id": acceptance.NUMBER}, {**update, "entity_id": "number.other"},
            {**update, "disabled_by": "user"}, {**update, "options": {"unit_of_measurement": "°C"}},
            {**update, "options_domain": "sensor"}, {key: value for key, value in update.items() if key != "options"},
            {"type": "config/entity_registry/update", "entity_id": "number.hamcp366_x", "name": "synthetic"},
            {"type": "config_entries/get", "domain": "hue"}, {"type": "config_entries/get"},
            {"type": "config_entries/get", "domain": "matter", "type_filter": ["hub"]},
            {"type": "config_entries/disable", "entry_id": "synthetic", "disabled_by": "user"},
            {"type": True}, {"type": None}, {}, None, [], "config/device_registry/list",
            [("type", "config/device_registry/list")],
        ]
        for payload in refused:
            with self.subTest(payload=payload):
                await self.raw_refused(payload)

    async def test_a_dict_subclass_with_a_listed_shape_is_refused_unsent(self):
        get = {"type": "config_entries/get", "domain": "matter"}
        update = {"type": "config/entity_registry/update", "entity_id": "number.hamcp366_converter_number",
                  "options_domain": "number", "options": {"unit_of_measurement": "°F"}}
        nested = {**update, "options": Shape(update["options"])}
        for plain, shaped in ((get, Shape(get)), (update, Shape(update)), (update, nested)):
            with self.subTest(shaped=shaped):
                self.assertEqual(shaped, plain)  # the same keys and values as a listed shape
                self.assertTrue(acceptance.listed(plain, acceptance.RAW_COMMANDS))
                self.assertFalse(acceptance.listed(shaped, acceptance.RAW_COMMANDS))
                await self.raw_refused(shaped)
        flow = {"handler": "matter"}
        self.assertTrue(acceptance.listed({"method": "POST", "path": self.FLOW, "body": flow}, acceptance.RAW_REST))
        fault = {"mode": "error"}
        self.assertTrue(acceptance.listed(fault, acceptance.STUB_POSTS))
        with NoTransport() as transport:
            with self.assertRaises(ValueError) as caught:
                await acceptance.RawRest(settings()).rest("POST", self.FLOW, Shape(flow))
            self.assertEqual(caught.exception.args, ("core_rest_request_not_listed",))
            with self.assertRaises(ValueError) as caught:
                await acceptance.StubControl(acceptance.STUB_CONTROL_URL).post(Shape(fault))
            self.assertEqual(caught.exception.args, ("stub_request_not_listed",))
            self.assertTrue(transport.untouched())

    async def test_raw_websocket_sends_every_listed_shape(self):
        listed = [{"type": "config/entity_registry/list"}, {"type": "config/device_registry/list"},
                  {"type": "config/entity_registry/get", "entity_id": acceptance.NUMBER},
                  {"type": "config_entries/get", "domain": "matter"},
                  {"type": "config/entity_registry/update", "entity_id": "number.hamcp366_converter_number",
                   "options_domain": "number", "options": {"unit_of_measurement": "°F"}},
                  {"type": "config/entity_registry/remove", "entity_id": acceptance.SELECT}]
        self.assertEqual(len(listed), len(acceptance.RAW_COMMANDS))
        for payload in listed:
            events = []
            replies = [{"type": "auth_required"}, {"type": "auth_ok"}, {"type": "result", "id": 1, "success": True}]
            with self.subTest(payload=payload), patch.object(
                    acceptance.aiohttp, "ClientSession", new=fake_session(events, replies=replies)):
                await acceptance.RawCore(settings()).command(payload)
                self.assertEqual(events[-1], ("send", {"id": 1, **payload}))
                self.assertEqual(events[1], ("connect", acceptance.CORE_WS_URL))

    async def test_raw_rest_refuses_unlisted_requests_before_a_session(self):
        state = "/api/states/" + acceptance.SELECT
        flow_step = self.FLOW + "/0123456789abcdef0123456789abcdef"
        refused = [
            # HAMCP-366 correction: the state read left the raw session for the bound runtime route.
            ("GET", state, None), ("GET", "/api/states/" + acceptance.SWITCH, None),
            ("GET", "/api/states/" + acceptance.NUMBER, None), ("GET", "/states/" + acceptance.SELECT, None),
            ("GET", "/api/states/light.kitchen", None), ("GET", "/api/states", None), ("GET", "/api/config", None),
            ("GET", "/api/", None), ("GET", state + "/../../config", None), ("GET", state + "?k=synthetic-query", None),
            ("GET", state + "\n", None), ("GET", state + "#f", None), ("GET", "//evil.example" + state, None),
            ("GET", "http://evil.example" + state, None), ("GET", "@evil.example" + state, None),
            ("GET", state.encode(), None), ("GET", None, None), ("GET", state, {"state": "on"}),
            ("get", state, None), ("DELETE", state, None), ("PUT", state, None), ("HEAD", state, None),
            ("POST", state, {"state": "on"}), ("POST", "/api/services/switch/turn_on", {"entity_id": acceptance.SWITCH}),
            ("POST", "/api/services/homeassistant/restart", None), ("POST", "/api/template", {"template": "synthetic"}),
            ("POST", self.FLOW, {"handler": "hue"}), ("POST", self.FLOW, {"handler": "matter", "show_advanced_options": True}),
            ("POST", self.FLOW, None), ("POST", self.FLOW, {}), ("POST", self.FLOW, {"handler": ["matter"]}),
            ("GET", self.FLOW, None), ("POST", flow_step, {"url": "ws://evil.example:5580/ws"}),
            ("POST", flow_step, {"url": "ws://127.0.0.1:5580/ws"}), ("POST", flow_step, {"url": acceptance.MATTER_URL, "x": 1}),
            ("POST", flow_step, None), ("POST", self.FLOW + "/../../services/switch/turn_on", {"url": acceptance.MATTER_URL}),
            ("DELETE", flow_step, None), ("POST", self.ENTRY, {"x": 1}), ("DELETE", self.ENTRY[:-len("/reload")], None),
            ("POST", self.ENTRY[:-len("/reload")] + "/disable", None), ("POST", self.ENTRY + "/", None),
            ("POST", "/api/config/config_entries/entry/short/reload", None),
            ("POST", "/api/config/config_entries/entry/01JSYNTHETICSYNTHETICSYNTH0/reload", None),
            ("POST", "/api/config/config_entries/entry/01jsyntheticsyntheticsynth/reload", None),
        ]
        for method, path, body in refused:
            with self.subTest(method=method, path=path, body=body), NoTransport() as transport:
                with self.assertRaises(ValueError) as caught:
                    await acceptance.RawRest(settings()).rest(method, path, body)
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(caught.exception.args, ("core_rest_request_not_listed",))
                self.assertEqual(len(chain(caught.exception)), 1)
                self.assertTrue(transport.untouched())

    async def test_raw_rest_sends_every_listed_request(self):
        listed = [("POST", self.FLOW, {"handler": "matter"}),
                  ("POST", self.FLOW, {"handler": acceptance.FIXTURE_DOMAIN}),
                  ("POST", self.FLOW + "/0123456789abcdef0123456789abcdef", {"url": acceptance.MATTER_URL}),
                  ("POST", self.ENTRY, None)]
        for method, path, body in listed:
            events = []
            with self.subTest(path=path), patch.object(acceptance.aiohttp, "ClientSession", new=fake_session(events)):
                await acceptance.RawRest(settings()).rest(method, path, body)
                self.assertEqual(events[1][1:3], (method, acceptance.CORE_URL + path))

    async def test_stub_client_refuses_unlisted_posts_before_a_session(self):
        drift = {"action": "drift", "node_id": 61, "attribute_path": "1/6/16387", "value": 1}
        refused = [
            {"action": "push_network_topology"}, {"mode": "topology_error"}, {"mode": "none", "server_info_schema_version": 12},
            {"server_info_schema_version": 12}, {"mode": "delay"}, {"mode": "delay", "seconds": True},
            {"mode": "delay", "seconds": 1.0}, {"mode": "delay", "seconds": "1"}, {"mode": "delay", "seconds": 0},
            {"mode": "delay", "seconds": 11}, {"mode": "stall", "seconds": 61}, {"mode": "stall"},
            {"mode": "mismatch"}, {"mode": "mismatch", "stored": 3}, {"mode": "mismatch", "stored": True},
            {"mode": "mismatch", "stored": None}, {"mode": "error", "seconds": 1}, {"mode": "NONE"},
            {**drift, "node_id": 91, "attribute_path": "1/128/0"}, {**drift, "node_id": 199},
            {**drift, "attribute_path": "1/6/0"}, {**drift, "attribute_path": "1/6/16387/"}, {**drift, "value": 3},
            {**drift, "value": "1"}, {**drift, "value": True}, {**drift, "extra": 1}, {**drift, "node_id": "61"},
            {key: value for key, value in drift.items() if key != "value"}, {**drift, "action": "write"},
            {"action": "drift", "node_id": 199, "attribute_path": "1/319486977/319422481", "value": 1},
            {"action": "drift", "node_id": 199, "attribute_path": "1/6/16387", "value": True},
            {}, None, [], "none",
        ]
        for body in refused:
            with self.subTest(body=body), NoTransport() as transport:
                with self.assertRaises(ValueError) as caught:
                    await acceptance.StubControl(acceptance.STUB_CONTROL_URL).post(body)
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(caught.exception.args, ("stub_request_not_listed",))
                self.assertEqual(len(chain(caught.exception)), 1)
                self.assertTrue(transport.untouched())

    def test_every_listed_stub_post_is_also_listed_by_the_stub_entry(self):
        entry = load_entry()
        listed = [{"mode": "none"}, {"mode": "error"}, {"mode": "delay", "seconds": 6}, {"mode": "stall", "seconds": 45},
                  {"mode": "mismatch", "stored": 0},
                  {"action": "drift", "node_id": 61, "attribute_path": "1/6/16387", "value": None},
                  {"action": "drift", "node_id": 199, "attribute_path": "1/319486977/319422481", "value": True}]
        for body in listed:
            with self.subTest(body=body):
                self.assertTrue(acceptance.listed(body, acceptance.STUB_POSTS))
                self.assertTrue(entry.fault_listed(body) or entry.drift_listed(body))

    def gateway_clients(self):
        inner_ws, inner_rest = SimpleNamespace(command=AsyncMock(return_value="reply")), \
            SimpleNamespace(request=AsyncMock(return_value="state"))
        with patch.object(acceptance, "HomeAssistantWebSocketClient", return_value=inner_ws), \
                patch.object(acceptance, "HomeAssistantRestClient", return_value=inner_rest):
            journal = []
            return (acceptance.LaneWebSocket(settings(), journal), acceptance.LaneRest(settings(), journal),
                    inner_ws, inner_rest, journal)

    async def test_gateway_websocket_refuses_unlisted_commands_unsent(self):
        select = {"type": "call_service", "domain": "select", "service": "select_option",
                  "service_data": {"option": "on"}, "target": {"entity_id": acceptance.SELECT}}
        switch = {"type": "call_service", "domain": "switch", "service": "turn_on",
                  "target": {"entity_id": acceptance.SWITCH}}
        number = {"type": "call_service", "domain": "number", "service": "set_value",
                  "service_data": {"value": 2.0}, "target": {"entity_id": acceptance.NUMBER}}
        refused = [
            {"type": "config/entity_registry/list"}, {"type": "config/entity_registry/update", "entity_id": acceptance.SELECT},
            {"type": "config/entity_registry/remove", "entity_id": acceptance.SELECT},
            {"type": "config/device_registry/update", "device_id": "synthetic"}, {"type": "subscribe_events"},
            {"type": "config/entity_registry/get", "entity_id": "light.kitchen"},
            {"type": "config/entity_registry/get", "entity_id": acceptance.SELECT, "extra": 1},
            {"type": "config/entity_registry/get"}, {"type": "config/device_registry/list", "extra": 1},
            {**select, "target": {"entity_id": "select.other_plug_power_on_behavior"}},
            {**select, "target": {"entity_id": acceptance.SWITCH}}, {**select, "service_data": {"option": "bogus"}},
            {**select, "service_data": {"option": "on", "extra": 1}}, {**select, "service": "select_next"},
            {**select, "service": "select_first"}, {**select, "return_response": True}, {**select, "id": 7},
            {**select, "target": {"entity_id": [acceptance.SELECT]}},
            {**select, "target": {"entity_id": acceptance.SELECT, "device_id": "synthetic"}},
            {**select, "target": {"device_id": "synthetic"}}, {**select, "target": {"area_id": "synthetic"}},
            {key: value for key, value in select.items() if key != "target"},
            {key: value for key, value in select.items() if key != "service_data"},
            {**select, "domain": "switch"}, {**switch, "service": "toggle"}, {**switch, "service_data": {}},
            {**switch, "target": {"entity_id": "switch.other_child_lock"}}, {**switch, "domain": "homeassistant"},
            {**switch, "domain": "light"}, {**number, "service_data": {"value": 4.0}},
            {**number, "service_data": {"value": 2}}, {**number, "service_data": {"value": True}},
            {**number, "service_data": {"value": "2"}}, {**number, "service_data": {"value": 2.5}},
            {**number, "service_data": {"value": 2.0, "extra": 1}}, {**number, "service_data": {"amount": 2.0}},
            {**number, "target": {"entity_id": "number.other_sensitivity"}},
            {"type": "call_service", "domain": "button", "service": "press", "target": {"entity_id": "button.x"}},
            {"type": "call_service", "domain": "homeassistant", "service": "restart"},
            {"type": True}, {}, None, [], "call_service",
        ]
        for payload in refused:
            with self.subTest(payload=payload):
                ws, _rest, inner, _inner_rest, journal = self.gateway_clients()
                with self.assertRaises(ValueError) as caught:
                    await ws.command(payload)
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(caught.exception.args, ("core_command_not_listed",))
                self.assertEqual(len(chain(caught.exception)), 1)
                inner.command.assert_not_awaited()
                self.assertEqual((ws.sends, journal, ws.reads), ([], [], {}))
                self.assertEqual(ws.refused, ["core_command_not_listed"])

    async def test_gateway_websocket_delegates_every_listed_shape_unchanged(self):
        listed = [{"type": "config/entity_registry/get", "entity_id": "select.any_registered_entity"},
                  {"type": "config/device_registry/list"}]
        listed += [service_call(acceptance.SELECT, "select", option) for option in acceptance.SELECT_OPTIONS]
        listed += [service_call(acceptance.SWITCH, "switch", value) for value in ("on", "off")]
        listed += [service_call(acceptance.NUMBER, "number", value) for value in acceptance.NUMBER_VALUES]
        for payload in listed:
            with self.subTest(payload=payload):
                ws, _rest, inner, _inner_rest, _journal = self.gateway_clients()
                self.assertEqual(await ws.command(payload), "reply")
                inner.command.assert_awaited_once_with(payload)
                self.assertIs(inner.command.await_args.args[0], payload)
                self.assertEqual(ws.refused, [])
                self.assertEqual(len(ws.sends), 1 if payload["type"] == "call_service" else 0)

    async def test_gateway_rest_admits_one_state_read_and_refuses_the_rest(self):
        route = rest_path("/states/{}", acceptance.SELECT, kinds=("entity_id",))
        _ws, rest, _inner, inner, journal = self.gateway_clients()
        self.assertEqual(await rest.request("GET", route), "state")
        inner.request.assert_awaited_once_with("GET", route)
        self.assertEqual(journal, [("state", acceptance.SELECT)])
        refused = [
            (("POST", route), {}), (("DELETE", route), {}), (("get", route), {}),
            (("GET", str(route)), {}), (("GET", "/states/" + acceptance.SELECT), {}),
            (("GET", rest_path("/states")), {}), (("GET", rest_path("/config")), {}),
            (("GET", rest_path("/states/{}", "light.kitchen", kinds=("entity_id",))), {}),
            (("GET", route, {"state": "on"}), {}), (("GET", route, None, True), {}),
            (("GET", route), {"expected_statuses": frozenset({404})}), (("GET", route), {"logbook_read": True}),
            (("GET", None), {}),
        ]
        for args, kwargs in refused:
            with self.subTest(args=args, kwargs=kwargs):
                _ws, rest, _inner, inner, journal = self.gateway_clients()
                with self.assertRaises(ValueError) as caught:
                    await rest.request(*args, **kwargs)
                self.assertEqual(caught.exception.args, ("core_rest_request_not_listed",))
                self.assertEqual(len(chain(caught.exception)), 1)
                inner.request.assert_not_awaited()
                self.assertEqual((journal, rest.refused), ([], ["core_rest_request_not_listed"]))

    async def test_refusals_carry_no_payload_text(self):
        payload = {"type": "call_service", "domain": "switch", "service": "synthetic-private-service",
                   "target": {"entity_id": "switch.synthetic_private_entity"}}
        error = await self.raw_refused(payload)
        ws, _rest, _inner, _inner_rest, _journal = self.gateway_clients()
        with self.assertRaises(ValueError) as caught:
            await ws.command(payload)
        for exc in (error, caught.exception):
            shown = str(exc) + repr(exc) + repr(exc.__dict__)
            for private in ("synthetic-private-service", "synthetic_private_entity", TOKEN, "127.0.0.1"):
                self.assertNotIn(private, shown)


def recording_session(sends, *, status=200, text="{}", error=None):
    """Stand-in for aiohttp.ClientSession under either REST client: every construction and send is recorded."""

    class Response:
        def __init__(self):
            self.status = status

        async def __aenter__(self):
            if error is not None:
                raise error
            return self

        async def __aexit__(self, *_exc):
            return False

        async def json(self, content_type=None):
            return json.loads(text)

        async def text(self):
            return text

    class Session:
        def __init__(self, **kwargs):
            sends.append(("session", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def request(self, method, url, **kwargs):
            sends.append(("request", method, str(url), kwargs))
            return Response()

    return Session


class LaneRawRouteTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-366: the raw REST session is closed to three config-entry routes on the pinned loopback Core.

    Pins the one ``CORE_URL + path`` exemption in tests/test_hamcp200_path_safety.py.
    """

    FLOW = "/api/config/config_entries/flow"
    STEP = FLOW + "/0123456789abcdef0123456789abcdef"
    RELOAD = "/api/config/config_entries/entry/01JSYNTHETICSYNTHETICSYNTH/reload"
    ALLOWED = (("POST", FLOW, {"handler": "matter"}), ("POST", FLOW, {"handler": acceptance.FIXTURE_DOMAIN}),
               ("POST", STEP, {"url": acceptance.MATTER_URL}), ("POST", RELOAD, None))

    def test_only_the_pinned_loopback_core_is_accepted(self):
        sends = []
        with patch.object(acceptance.aiohttp, "ClientSession", new=recording_session(sends)):
            self.assertEqual(acceptance.RawRest(settings()).token, TOKEN)
            for url in ("http://homeassistant.local:18297", "http://localhost:18297", "http://[::1]:18297",
                        "http://127.0.0.1:8123", "https://127.0.0.1:18297", "http://supervisor/core",
                        "http://192.0.2.10:18297", "http://127.0.0.1:18297@evil.example",
                        "http://127.0.0.1:18297/"):
                with self.subTest(url=url), self.assertRaises(ValueError) as caught:
                    acceptance.RawRest(settings(ha_url=url))
                self.assertEqual(caught.exception.args, ("core_url_not_pinned",))
        self.assertEqual(sends, [])

    def test_the_closed_set_is_exactly_flow_start_flow_continue_and_entry_reload(self):
        self.assertEqual(len(acceptance.RAW_REST), 3)
        self.assertEqual([spec["method"] for spec in acceptance.RAW_REST], ["POST", "POST", "POST"])
        matched = []
        for method, path, body in self.ALLOWED:
            request = {"method": method, "path": path, "body": body}
            hits = [index for index, spec in enumerate(acceptance.RAW_REST) if acceptance._match(request, spec)]
            self.assertEqual(len(hits), 1, request)
            matched.append(hits[0])
        self.assertEqual(matched, [0, 0, 1, 2])
        source = Path(acceptance.__file__).read_text(encoding="utf-8")
        self.assertNotIn("/api/states", source)
        scenario = inspect.getsource(acceptance.run)
        self.assertEqual(scenario.count("http.rest("), 4)
        self.assertEqual(scenario.count("return await reader.read(entity_id)"), 1)
        self.assertEqual(scenario.count("LaneState(configured)"), 1)

    async def test_the_three_allowed_routes_are_sent_to_the_pinned_core_only(self):
        for method, path, body in self.ALLOWED:
            sends = []
            with self.subTest(path=path, body=body), patch.object(
                    acceptance.aiohttp, "ClientSession", new=recording_session(sends, text='{"type": "form"}')):
                status, value = await acceptance.RawRest(settings()).rest(method, path, body)
                self.assertEqual((status, value), (200, {"type": "form"}))
                (session, request) = sends
                self.assertEqual(session[0], "session")
                self.assertIs(session[1]["trust_env"], False)
                self.assertEqual(request[:3], ("request", "POST", "http://127.0.0.1:18297" + path))
                self.assertIs(request[3]["allow_redirects"], False)
                self.assertEqual(request[3]["headers"], {"Authorization": "Bearer " + TOKEN})
                self.assertEqual(request[3].get("json"), body)
                self.assertEqual("json" in request[3], body is not None)

    async def test_requests_outside_the_closed_set_are_refused_before_sending(self):
        refused = [
            ("GET", self.FLOW, None), ("GET", self.FLOW, {"handler": "matter"}), ("DELETE", self.STEP, None),
            ("PUT", self.STEP, {"url": acceptance.MATTER_URL}), ("GET", self.RELOAD, None),
            ("DELETE", self.RELOAD, None), ("post", self.RELOAD, None), ("POST", self.FLOW, {"handler": "hue"}),
            ("POST", self.FLOW, None), ("POST", self.FLOW, {"handler": "matter", "extra": 1}),
            ("POST", self.STEP, {"url": "ws://evil.example:5580/ws"}), ("POST", self.STEP, None),
            ("POST", self.RELOAD, {}), ("POST", self.RELOAD, {"x": 1}), ("POST", self.RELOAD + "/", None),
            ("POST", self.RELOAD[:-len("/reload")], None), ("DELETE", self.RELOAD[:-len("/reload")], None),
            ("POST", self.RELOAD[:-len("/reload")] + "/disable", None),
            ("POST", "/api/config/config_entries/options/flow", {"handler": "matter"}),
            ("POST", "/api/config/config_entries/entry", None),
            ("POST", self.FLOW + "/../entry/01JSYNTHETICSYNTHETICSYNTH/reload", None),
            ("POST", self.FLOW + "/" + "a" * 33, {"url": acceptance.MATTER_URL}),
            ("POST", "/config/config_entries/flow", {"handler": "matter"}),
            ("POST", "/api/services/homeassistant/restart", None),
            ("POST", "/api/services/switch/turn_on", {"entity_id": acceptance.SWITCH}),
            ("POST", "//evil.example" + self.FLOW, {"handler": "matter"}),
            ("POST", "@evil.example" + self.FLOW, {"handler": "matter"}),
            ("POST", Text(self.FLOW), {"handler": "matter"}), ("POST", None, None), (None, self.RELOAD, None),
        ]
        for method, path, body in refused:
            sends = []
            with self.subTest(method=method, path=path, body=body), patch.object(
                    acceptance.aiohttp, "ClientSession", new=recording_session(sends)):
                with self.assertRaises(ValueError) as caught:
                    await acceptance.RawRest(settings()).rest(method, path, body)
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(caught.exception.args, ("core_rest_request_not_listed",))
                self.assertEqual(sends, [])

    async def test_the_moved_state_path_is_refused_by_the_raw_sender(self):
        entities = (acceptance.SELECT, acceptance.SWITCH, acceptance.NUMBER, "select.hamcp366_t2_select")
        for entity_id in entities:
            for method, path, body in (("GET", "/api/states/" + entity_id, None), ("GET", "/states/" + entity_id, None),
                                       ("POST", "/api/states/" + entity_id, {"state": "on"}),
                                       ("GET", "/api/states", None)):
                sends = []
                with self.subTest(method=method, path=path), patch.object(
                        acceptance.aiohttp, "ClientSession", new=recording_session(sends)):
                    self.assertFalse(acceptance.listed({"method": method, "path": path, "body": body},
                                                       acceptance.RAW_REST))
                    with self.assertRaises(ValueError) as caught:
                        await acceptance.RawRest(settings()).rest(method, path, body)
                    self.assertEqual(caught.exception.args, ("core_rest_request_not_listed",))
                    self.assertEqual(sends, [])


class LaneStateReadTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-366: the lane's own state readback is one bound runtime route on the pinned Core."""

    STATE = '{"entity_id": "switch.synthetic", "state": "on", "attributes": {}}'

    async def test_state_is_read_on_the_bound_runtime_route_from_the_pinned_core(self):
        sends = []
        proxies = {"HTTP_PROXY": "http://evil.example:3128", "ALL_PROXY": "http://evil.example:3128"}
        with patch.dict(os.environ, proxies), patch.object(
                acceptance.aiohttp, "ClientSession", new=recording_session(sends, text=self.STATE)):
            value = await acceptance.LaneState(settings()).read(acceptance.SWITCH)
        self.assertEqual(value, json.loads(self.STATE))
        (session, request) = sends
        # The runtime client leaves aiohttp's default in place: the environment's proxies are not used.
        self.assertIs(session[1].get("trust_env", False), False)
        self.assertEqual(request[:3], ("request", "GET", "http://127.0.0.1:18297/api/states/" + acceptance.SWITCH))
        self.assertIs(request[3]["allow_redirects"], False)
        self.assertIsNone(request[3]["json"])
        self.assertEqual(request[3]["headers"]["Authorization"], "Bearer " + TOKEN)

    async def test_the_inner_request_is_exactly_one_bound_state_route(self):
        with NoTransport() as transport:
            inner = transport.rest.return_value
            inner.request = AsyncMock(return_value={"state": "previous"})
            reader = acceptance.LaneState(settings())
            self.assertEqual(await reader.read(acceptance.SELECT), {"state": "previous"})
            transport.session.assert_not_called()
        (method, route), kwargs = inner.request.await_args
        self.assertEqual((method, kwargs), ("GET", {"expected_statuses": frozenset({404})}))
        self.assertIs(type(route), acceptance.Route)
        self.assertEqual((route.template, route.values, route.kinds, route.query),
                         ("/states/{}", (acceptance.SELECT,), ("entity_id",), ()))
        self.assertEqual(route, rest_path("/states/{}", acceptance.SELECT, kinds=("entity_id",)))
        self.assertEqual(inner.request.await_count, 1)

    async def test_anything_but_a_lane_entity_id_is_refused_before_a_client_call(self):
        refused = ["light.kitchen", "sensor.eve_energy_plug_power", "select.", "select.A", "select.a/../b",
                   "select.a?k=synthetic-query", "select.a#f", "select.a\n", " select.a", "select.a/config",
                   "../config", "", None, 7, [acceptance.SELECT], Text(acceptance.SELECT),
                   acceptance.SELECT.encode(), "number." + "a" * 121]
        for entity_id in refused:
            sends = []
            with self.subTest(entity_id=entity_id), patch.object(
                    acceptance.aiohttp, "ClientSession", new=recording_session(sends)):
                reader = acceptance.LaneState(settings())
                with patch.object(reader.client, "request", new=AsyncMock()) as inner:
                    with self.assertRaises(ValueError) as caught:
                        await reader.read(entity_id)
                    inner.assert_not_called()
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(caught.exception.args, ("core_rest_request_not_listed",))
                self.assertEqual(sends, [])

    async def test_an_answer_other_than_200_is_no_state_and_is_never_followed(self):
        for status in (404, 401, 302, 307, 500, 503):
            sends = []
            with self.subTest(status=status), patch.object(
                    acceptance.aiohttp, "ClientSession", new=recording_session(sends, status=status, text="")):
                self.assertIsNone(await acceptance.LaneState(settings()).read(acceptance.SELECT))
                self.assertEqual([item[0] for item in sends], ["session", "request"])
                self.assertIs(sends[1][3]["allow_redirects"], False)

    async def test_a_core_that_cannot_be_reached_is_an_error_and_not_a_missing_state(self):
        for error in (acceptance.aiohttp.ClientConnectionError("synthetic"), asyncio.TimeoutError()):
            sends = []
            with self.subTest(error=type(error).__name__), patch.object(
                    acceptance.aiohttp, "ClientSession", new=recording_session(sends, error=error)):
                with self.assertRaises((HomeAssistantUnavailableError, HomeAssistantTimeoutError)):
                    await acceptance.LaneState(settings()).read(acceptance.SELECT)

    async def test_a_read_inside_a_product_request_is_not_that_requests(self):
        sends, authorizer = [], Mock(return_value=False)
        telemetry, token = begin_request("hamcp366-synthetic-request")
        telemetry.core_dispatch_authorizer = authorizer
        before = (telemetry.ha_duration_ms, set(telemetry.endpoint_categories), telemetry.error_code,
                  telemetry.timeout_occurred)
        try:
            with patch.object(acceptance.aiohttp, "ClientSession", new=recording_session(sends, text=self.STATE)):
                reader = acceptance.LaneState(settings())
                # A product request that could not authorize a Core dispatch still lets the lane read back.
                self.assertEqual((await reader.read(acceptance.SWITCH))["state"], "on")
            with patch.object(acceptance.aiohttp, "ClientSession",
                              new=recording_session(sends, status=404, text="")):
                self.assertIsNone(await reader.read(acceptance.SELECT))
        finally:
            end_request(token)
        authorizer.assert_not_called()
        self.assertEqual((telemetry.ha_duration_ms, set(telemetry.endpoint_categories), telemetry.error_code,
                          telemetry.timeout_occurred), before)
        self.assertEqual([item[0] for item in sends], ["session", "request", "session", "request"])


class ProductThroughGuards(unittest.IsolatedAsyncioTestCase):
    """The production service and gateway over the lane's closed clients.

    The peer is the HAMCP-297 offline fake, keyed by the lane's entity ids.
    """

    OTHER = "select.other_plug_power_on_behavior"
    OUTSIDE = "select.synthetic_unadmitted_kind"

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock, self.core, self.ha = offline.Clock(), offline.SignedCore(), offline.FakeHomeAssistant()
        entities = self.ha.entities
        self.ha.entities = {}
        for old, new in ((offline.SELECT, acceptance.SELECT), (offline.SWITCH, acceptance.SWITCH),
                         (offline.NUMBER, acceptance.NUMBER)):
            self.ha.entities[new] = entities[old]
            self.ha.entities[new]["registry"].update(entity_id=new, id="reg-" + new, unique_id="matter-unique-" + new)
        self.ha.entities[self.OTHER] = {
            "registry": offline.registry(self.OTHER, "startup_on_off", "dev-plug"), "state": "off",
            "attributes": {"options": ["off", "on", "toggle", "previous"], "friendly_name": "Other"}}
        self.ha.entities[self.OUTSIDE] = {
            "registry": offline.registry(self.OUTSIDE, "startup_on_off", "dev-plug", platform="hamcp366_refusal_fixture"),
            "state": "off", "attributes": {"options": ["off", "on"], "friendly_name": "Outside"}}
        self.given = []

        def product(configured):
            self.given.append(configured)
            return self.ha
        self.journal = []
        with patch.object(acceptance, "HomeAssistantWebSocketClient", side_effect=product), \
                patch.object(acceptance, "HomeAssistantRestClient", side_effect=product):
            self.ws = acceptance.LaneWebSocket(settings(), self.journal)
            self.rest = acceptance.LaneRest(settings(), self.journal)
        self.service = DeviceConfigurationService(
            self.tmp.name, DeviceConfigurationGateway(self.rest, self.ws),
            DeviceConfigurationCoreAuthority(self.core), now=self.clock, sleep=self.clock.sleep, safety_trigger=None)
        self.addAsyncCleanup(self.service.close)
        patcher = patch.object(acceptance, "operation_id",
                               side_effect=lambda: f"{int(self.clock().timestamp())}-{uuid4().hex}")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def control(self, request):
        telemetry, token = begin_request("synthetic-hamcp366-lane")
        telemetry.ordinary_device_configuration_binding = digest(request.model_dump())
        try:
            return await self.service.control(request)
        finally:
            end_request(token)

    async def test_clients_received_only_the_pinned_settings(self):
        self.assertEqual(len(self.given), 2)
        for given in self.given:
            self.assertEqual(given.ha_url, acceptance.CORE_URL)

    async def test_every_admitted_kind_verifies_through_the_closed_lists(self):
        for entity_id, value in ((acceptance.SELECT, "on"), (acceptance.SWITCH, "on"), (acceptance.NUMBER, "3")):
            with self.subTest(entity_id=entity_id):
                mark, sends = len(self.journal), len(self.ws.sends)
                receipt = await self.control(acceptance.device_request(entity_id, value))
                self.assertEqual(receipt["status"], "succeeded_verified", receipt)
                self.assertEqual(len(self.ws.sends), sends + 1)
                self.assertTrue(acceptance.same_frame(self.ws.sends[-1], acceptance.expected_payload(entity_id, value)))
                self.assertEqual(self.ha.writes[-1], self.ws.sends[-1])
                events = self.journal[mark:]
                sent_at = events.index(("send", entity_id))
                # Preparation, the locked preflight and once() each re-read the registry.
                self.assertEqual(events[:sent_at].count(("registry", entity_id)), 3)
                self.assertGreaterEqual(events[sent_at:].count(("state", entity_id)), 1)
                undo = await self.control(acceptance.device_request(entity_id, undo=receipt["task_id"]))
                self.assertEqual(undo["status"], "succeeded_verified", undo)
                self.assertEqual(len(self.ws.sends), sends + 2)
                self.assertTrue(acceptance.evidence(receipt)["verification"])
        self.assertEqual((self.ws.refused, self.rest.refused), ([], []))
        self.assertEqual(len(self.ha.writes), 6)

    async def test_armed_hook_before_the_locked_preflight_and_before_once(self):
        for nth, outcome in ((2, "preflight_rejected"), (3, "failed_pre_dispatch")):
            with self.subTest(nth=nth):
                ran = []

                async def hook():
                    ran.append(self.ws.reads[acceptance.SELECT])
                    self.ha.entities[acceptance.SELECT]["state"] = "toggle"
                    self.ha.touch(acceptance.SELECT)
                before = self.ws.reads.get(acceptance.SELECT, 0)
                self.ws.arm(acceptance.SELECT, nth, hook)
                receipt = await self.control(acceptance.device_request(acceptance.SELECT, "on"))
                self.assertEqual(ran, [before + nth])
                self.assertNotIn(acceptance.SELECT, self.ws.hooks)
                self.assertEqual(receipt["terminal_outcome"], outcome, receipt)
                self.assertEqual(receipt["reason"], "device_configuration_state_changed")
                self.assertFalse(receipt["dispatch_intent_recorded"])
                self.assertEqual((self.ws.sends, self.ha.writes), ([], []))
                self.ha.entities[acceptance.SELECT]["state"] = "previous"

    async def test_kind_outside_the_three_triples_is_refused_with_no_send(self):
        with self.assertRaises(DeviceConfigurationRefusal) as caught:
            await self.control(acceptance.device_request(self.OUTSIDE, "on"))
        self.assertEqual(caught.exception.details["category"], "device_configuration_target_kind_not_admitted")
        self.assertEqual(acceptance.predicted_refusal(self.ha.entities[self.OUTSIDE]["registry"], self.ha.devices),
                         "device_configuration_target_kind_not_admitted")
        self.assertEqual((self.ws.sends, self.ha.writes), ([], []))

    async def test_unsigned_authority_is_refused_with_no_send(self):
        self.core.signed = False
        with self.assertRaises(DeviceConfigurationRefusal) as caught:
            await self.control(acceptance.device_request(acceptance.SELECT, "on"))
        self.assertEqual(caught.exception.details["category"], "device_configuration_core_authority_unavailable")
        self.assertEqual((self.ws.sends, self.ha.writes), ([], []))

    async def test_product_admitted_entity_off_the_lane_list_never_reaches_core(self):
        """The lane list is narrower than the product: only its three entities are ever sent."""
        commands = len(self.ha.commands)
        receipt = await self.control(acceptance.device_request(self.OTHER, "on"))
        # The product cannot tell a refused send from a lost one: it holds the
        # operation unverified and never reports success.
        self.assertEqual(receipt["status"], "changed_unverified", receipt)
        self.assertIn(receipt["status"], acceptance.HELD_STATUSES)
        self.assertNotEqual(receipt.get("terminal_outcome"), "succeeded_verified")
        self.assertEqual(self.ws.refused, ["core_command_not_listed"])
        self.assertEqual((self.ws.sends, self.ha.writes), ([], []))
        self.assertFalse(any(item["type"] == "call_service" for item in self.ha.commands[commands:]))
        self.assertEqual(self.ha.entities[self.OTHER]["state"], "off")


class CompositeObservation(ProductThroughGuards):
    """The lane reads the runtime's own composite observation; it does not restate it."""

    async def test_composite_is_the_observation_the_undo_fingerprint_is_made_of(self):
        for entity_id, value in ((acceptance.SELECT, "on"), (acceptance.SWITCH, "on"), (acceptance.NUMBER, "3")):
            with self.subTest(entity_id=entity_id):
                receipt = await self.control(acceptance.device_request(entity_id, value))
                self.assertEqual(receipt["status"], "succeeded_verified", receipt)
                mark, sends, commands = len(self.journal), len(self.ws.sends), len(self.ha.commands)
                observed = await acceptance.composite(self.service, entity_id)
                sidecar = self.service._load_verified(receipt["task_id"])
                self.assertEqual(digest(observed), sidecar["post_fingerprint"])
                self.assertEqual(digest(observed), receipt["verification"]["resulting_state_fingerprint"])
                self.assertEqual(identity(observed), sidecar["identity"])
                # One registry read, then one state read, through the closed lists; nothing sent.
                self.assertEqual(self.journal[mark:], [("registry", entity_id), ("state", entity_id)])
                self.assertEqual(len(self.ws.sends), sends)
                self.assertFalse(any(item["type"] == "call_service" for item in self.ha.commands[commands:]))
                # The authority window is taken and given back.
                self.assertEqual((self.core.leases, self.core.commits), (set(), set()))
        self.assertEqual((self.ws.refused, self.rest.refused), ([], []))

    async def test_composite_keeps_the_runtime_refusals(self):
        with self.assertRaises(DeviceConfigurationRefusal) as caught:
            await acceptance.composite(self.service, self.OUTSIDE)
        self.assertEqual(caught.exception.details["category"], "device_configuration_target_kind_not_admitted")
        self.core.signed = False
        commands = len(self.ha.commands)
        with self.assertRaises(DeviceConfigurationRefusal) as caught:
            await acceptance.composite(self.service, acceptance.SELECT)
        self.assertEqual(caught.exception.details["category"], "device_configuration_core_authority_unavailable")
        self.assertEqual(self.ha.commands[commands:], [])
        self.assertEqual((self.ws.sends, self.ha.writes), ([], []))

    async def test_delta_names_exactly_the_fields_that_move_the_fingerprint(self):
        first = await acceptance.composite(self.service, acceptance.SELECT)
        same = acceptance.observation_delta(first, await acceptance.composite(self.service, acceptance.SELECT))
        self.assertEqual((same["fingerprint_changed"], same["changed_fields"], same["identity_restored"]),
                         (False, [], True))
        self.assertEqual(same["fingerprint_before"], same["fingerprint_after"])
        self.assertEqual(same["fingerprint_before"], digest(first))
        self.assertTrue(all(not item["changed"] and item["before"] == item["after"]
                            for item in same["fields"].values()))

        # A restored registry id: only what Core rewrote moves.
        entity = self.ha.entities[acceptance.SELECT]
        entity["registry"]["modified_at"] += 60
        self.ha.touch(acceptance.SELECT)
        second = await acceptance.composite(self.service, acceptance.SELECT)
        restored = acceptance.observation_delta(first, second)
        self.assertTrue(restored["fingerprint_changed"])
        self.assertEqual(restored["fingerprint_after"], digest(second))
        self.assertTrue(restored["identity_restored"])
        self.assertEqual(restored["changed_fields"],
                         ["context_id", "last_changed", "last_updated", "registry.modified_at"])
        self.assertEqual(restored["fields"]["registry.modified_at"],
                         {"before": first["registry"]["modified_at"],
                          "after": first["registry"]["modified_at"] + 60, "changed": True})
        self.assertFalse(restored["fields"]["registry.id"]["changed"])

        # A new registry id is an identity change, and is named as a changed field.
        entity["registry"]["id"] = "reg-replacement"
        replaced = acceptance.observation_delta(second, await acceptance.composite(self.service, acceptance.SELECT))
        self.assertFalse(replaced["identity_restored"])
        self.assertEqual(replaced["changed_fields"], ["registry.id"])


class FieldChanges(unittest.TestCase):
    def test_every_leaf_is_listed_and_change_is_type_exact(self):
        found = acceptance.field_changes(
            {"a": {"b": 1, "c": [1, 2]}, "d": None, "e": 1, "gone": "x", "empty": {}},
            {"a": {"b": 1, "c": [2, 1]}, "d": None, "e": 1.5, "new": "y", "empty": {}})
        self.assertEqual(sorted(found), ["a.b", "a.c", "d", "e", "empty", "gone", "new"])
        self.assertEqual(found["a.b"], {"before": 1, "after": 1, "changed": False})
        self.assertEqual(found["d"], {"before": None, "after": None, "changed": False})
        self.assertEqual(found["e"], {"before": 1, "after": 1.5, "changed": True})
        self.assertEqual(found["a.c"], {"before": {"sha256": digest([1, 2])},
                                        "after": {"sha256": digest([2, 1])}, "changed": True})
        self.assertEqual(found["gone"], {"before": "x", "after": {"absent": True}, "changed": True})
        self.assertEqual(found["new"], {"before": {"absent": True}, "after": "y", "changed": True})
        self.assertFalse(found["empty"]["changed"])
        # An absent field is not the same as a present null.
        self.assertTrue(acceptance.field_changes({"a": None}, {})["a"]["changed"])

    def test_registry_level_lists_only_the_named_registry_facts(self):
        before = {"entity_id": "select.x", "id": "one", "created_at": 1.0, "modified_at": 2.0,
                  "device_id": "dev", "unique_id": "u", "platform": "matter", "config_entry_id": "entry",
                  "name": "never recorded"}
        found = acceptance.registry_level(before, {**before, "modified_at": 3.0, "name": "other"})
        self.assertEqual(tuple(sorted(found)), tuple(sorted(acceptance.REGISTRY_LEVEL)))
        self.assertEqual([name for name, item in found.items() if item["changed"]], ["modified_at"])
        self.assertEqual(found["id"], {"before": "one", "after": "one", "changed": False})

    def test_reregistration_paths_are_inside_the_closed_lists(self):
        """The observation uses no command or route the lane did not already allow."""
        self.assertEqual(acceptance.REREGISTRATION_PATH,
                         ("websocket config/entity_registry/remove",
                          "POST /api/config/config_entries/entry/{entry_id}/reload"))
        self.assertEqual(acceptance.RELOAD_ONLY_PATH, acceptance.REREGISTRATION_PATH[1:])
        removes = [shape for shape in acceptance.RAW_COMMANDS if shape["type"] == "config/entity_registry/remove"]
        self.assertEqual(removes, [{"type": "config/entity_registry/remove", "entity_id": acceptance.SELECT}])


class StubEntry(unittest.IsolatedAsyncioTestCase):
    """The lane entry replaces the stub's open control route with one closed route."""

    def setUp(self):
        # The HAMCP-297 stub is a frozen fixture that leaves its dump files to the collector.
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore", ResourceWarning)
        self.entry = load_entry()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "stub.jsonl"
        self.stub = self.entry.load(str(lane.STUB_ASSET / "stub.py")).Stub(str(lane.STUB_ASSET / "nodes"), str(self.out))
        self.stub.push_attribute = AsyncMock()

    def request(self, method, body=None, error=None):
        return SimpleNamespace(app={self.entry.STUB: self.stub}, method=method,
                               json=AsyncMock(return_value=body, side_effect=error))

    def snapshot(self):
        return json.dumps({"fault": self.stub.fault, "nodes": {str(key): value["attributes"]
                                                                for key, value in self.stub.nodes.items()}},
                          sort_keys=True)

    async def test_unlisted_posts_are_refused_and_change_nothing(self):
        drift = {"action": "drift", "node_id": 61, "attribute_path": "1/6/16387", "value": 1}
        refused = [
            {"action": "push_network_topology"}, {"mode": "topology_error"}, {"mode": "none", "server_info_schema_version": 12},
            {"server_info_schema_version": 12}, {"mode": "delay"}, {"mode": "delay", "seconds": 11},
            {"mode": "delay", "seconds": "1"}, {"mode": "delay", "seconds": True}, {"mode": "delay", "seconds": 0},
            {"mode": "stall", "seconds": 61}, {"mode": "mismatch"}, {"mode": "mismatch", "stored": "x"},
            {"mode": "mismatch", "stored": [1]}, {"mode": "error", "extra": 1}, {"mode": ["none"]}, {"mode": None},
            {**drift, "node_id": 999}, {**drift, "attribute_path": "9/9/9"}, {**drift, "node_id": "61"},
            {**drift, "value": {"a": 1}}, {**drift, "value": 1.5}, {**drift, "value": "1"}, {**drift, "extra": 1},
            {**drift, "action": "write"}, {key: value for key, value in drift.items() if key != "value"},
            {}, [], "none", None, 1,
        ]
        before = self.snapshot()
        for body in refused:
            with self.subTest(body=body):
                response = await self.entry.control(self.request("POST", body))
                self.assertEqual(response.status, 400)
                self.assertEqual(json.loads(response.text), {"error": "lane_control_request_not_listed"})
                self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.out.exists())
        self.stub.push_attribute.assert_not_awaited()
        self.assertEqual(self.stub.writes, [])

    async def refused_drifts(self, bodies):
        before = self.snapshot()
        for body in bodies:
            with self.subTest(body=body):
                response = await self.entry.control(self.request("POST", body))
                self.assertEqual(response.status, 400)
                self.assertEqual(json.loads(response.text), {"error": "lane_control_request_not_listed"})
                self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.out.exists())
        self.stub.push_attribute.assert_not_awaited()
        self.assertEqual(self.stub.writes, [])

    async def test_drift_node_id_that_is_not_exactly_an_integer_is_refused(self):
        # 61.0 equals 61 and finds the same loaded node, so only the type check refuses it.
        self.assertIs(self.stub.nodes.get(61.0), self.stub.nodes[61])
        self.assertIn("1/6/16387", self.stub.nodes[61]["attributes"])
        drift = {"action": "drift", "attribute_path": "1/6/16387", "value": 1}
        await self.refused_drifts([{**drift, "node_id": 61.0}, {**drift, "node_id": 61.0, "value": None},
                                   {**drift, "node_id": [61]}, {**drift, "node_id": {"id": 61}},
                                   {**drift, "node_id": None}, {**drift, "node_id": True}])

    async def test_drift_attribute_path_that_is_not_a_string_is_refused(self):
        drift = {"action": "drift", "node_id": 61, "value": 1}
        await self.refused_drifts([{**drift, "attribute_path": path} for path in (
            ["1/6/16387"], ["1", "6", "16387"], [], {"1/6/16387": 1}, {}, 16387, 1.5, None, True)])

    async def test_malformed_json_and_other_methods_are_refused(self):
        before = self.snapshot()
        response = await self.entry.control(self.request("POST", error=ValueError("synthetic")))
        self.assertEqual(response.status, 400)
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
            response = await self.entry.control(self.request(method, {"mode": "error"}))
            self.assertEqual(response.status, 400, method)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.out.exists())

    async def test_listed_fault_is_set_exactly_and_recorded(self):
        for body in ({"mode": "error"}, {"mode": "delay", "seconds": 6}, {"mode": "stall", "seconds": 45},
                     {"mode": "mismatch", "stored": 0}, {"mode": "none"}):
            response = await self.entry.control(self.request("POST", body))
            self.assertEqual(response.status, 200)
            self.assertEqual(self.stub.fault, body)
        records = [json.loads(line) for line in self.out.read_text().splitlines()]
        self.assertEqual([record["kind"] for record in records], ["lane_fault"] * 5)
        self.assertEqual(self.stub.writes, [])

    async def test_drift_updates_one_attribute_and_reports_it_without_a_write(self):
        before = self.stub.nodes[61]["attributes"]["1/6/16387"]
        value = 2 if before != 2 else 1
        others = json.dumps({key: item for key, item in self.stub.nodes[61]["attributes"].items()
                             if key != "1/6/16387"}, sort_keys=True)
        response = await self.entry.control(self.request("POST", {
            "action": "drift", "node_id": 61, "attribute_path": "1/6/16387", "value": value}))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), {"drift": {"before": before, "value": value}})
        self.assertEqual(self.stub.nodes[61]["attributes"]["1/6/16387"], value)
        self.assertEqual(json.dumps({key: item for key, item in self.stub.nodes[61]["attributes"].items()
                                     if key != "1/6/16387"}, sort_keys=True), others)
        self.stub.push_attribute.assert_awaited_once_with(61, "1/6/16387", value)
        self.assertEqual(self.stub.writes, [])
        (record,) = [json.loads(line) for line in self.out.read_text().splitlines()]
        self.assertEqual((record["kind"], record["before"], record["value"]), ("lane_drift", before, value))

    async def test_read_reports_stub_entry_and_dump_hashes(self):
        response = await self.entry.control(self.request("GET"))
        observed = json.loads(response.text)
        files = json.loads((lane.STUB_ASSET / "provenance.json").read_text())["files"]
        self.assertEqual(observed["entry_sha256"], hashlib.sha256(ENTRY_PATH.read_bytes()).hexdigest())
        self.assertEqual(observed["stub_sha256"], files["stub.py"])
        self.assertEqual(observed["dump_hashes"], {name[len("nodes/"):]: value for name, value in files.items()
                                                   if name.startswith("nodes/")})
        self.assertEqual(observed["writes"], [])

    async def test_application_serves_only_the_protocol_socket_and_the_lane_route(self):
        routes = sorted((route.method, route.resource.canonical) for route in self.entry.application(self.stub).router.routes())
        self.assertEqual(routes, [("*", "/_lane/control"), ("GET", "/ws"), ("HEAD", "/ws")])
        self.assertEqual(sorted(self.stub.nodes), [61, 91, 199])
        self.assertEqual((acceptance.SELECT_NODE, acceptance.NUMBER_NODE, acceptance.SWITCH_NODE), (61, 91, 199))
        for node, path in ((61, acceptance.SELECT_PATH), (91, acceptance.NUMBER_PATH)):
            self.assertIn(path, self.stub.nodes[node]["attributes"])
        self.assertTrue(any(path.endswith(acceptance.SWITCH_PATH_SUFFIX) for path in self.stub.nodes[199]["attributes"]))


class AcceptanceFailurePreservation(unittest.IsolatedAsyncioTestCase):
    """run() fails closed before any key, authority, client or request."""

    ENTRY = {"version": "2026.11.0", "capabilities": [{"capability_id": f"core.synthetic_{n}"} for n in range(32)]}

    async def refused(self, configured, stub_url, error, entry=None):
        with tempfile.TemporaryDirectory() as directory, diagnostics.lane(lane.GROUP), NoTransport() as transport, \
                patch.object(acceptance.Ed25519PrivateKey, "generate") as key, \
                patch.object(acceptance, "CoreRuntime") as runtime, \
                patch.object(acceptance, "DeviceConfigurationService") as service:
            with self.assertRaises(error) as caught:
                await acceptance.run(configured, entry or self.ENTRY, Path(directory), stub_url=stub_url,
                                     stub_hashes=RECEIPT_STUB)
            runtime.assert_not_called()
            service.assert_not_called()
            transport.session.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])
        return caught.exception, key, transport

    async def test_unpinned_core_upstream_or_stub_refuses_before_anything_exists(self):
        cases = ((settings(ha_url="http://127.0.0.1:8123"), acceptance.STUB_CONTROL_URL, "core_url_not_pinned"),
                 (settings(ha_url="http://evil.example:18297"), acceptance.STUB_CONTROL_URL, "core_url_not_pinned"),
                 (settings(upstream="http://127.0.0.1:18086/beta23-real-ha-mcp"), acceptance.STUB_CONTROL_URL,
                  "upstream_url_not_pinned"),
                 (settings(upstream=""), acceptance.STUB_CONTROL_URL, "upstream_url_not_pinned"),
                 (settings(), "http://127.0.0.1:18299/control", "stub_url_not_pinned"),
                 (settings(), "http://hamcp366-matter-stub:5580/_lane/control", "stub_url_not_pinned"))
        for configured, stub_url, code in cases:
            with self.subTest(code=code, stub_url=stub_url):
                exc, key, transport = await self.refused(configured, stub_url, ValueError)
                self.assertEqual(exc.args, (code,))
                key.assert_not_called()
                self.assertTrue(transport.untouched())
                self.assertEqual(exc._local_lane_check, ("helper_setup", "helper_contract"))

    async def test_predecessor_that_is_not_the_reviewed_one_fails_before_any_request(self):
        signed = {**self.ENTRY, "capabilities": self.ENTRY["capabilities"][:31] + [{"capability_id": CAPABILITY}]}
        short = {**self.ENTRY, "capabilities": self.ENTRY["capabilities"][:31]}
        for entry in (signed, short):
            with self.subTest(count=len(entry["capabilities"])):
                await self.refused(settings(), acceptance.STUB_CONTROL_URL, AssertionError, entry)

    async def test_stub_bytes_other_than_the_hashed_ones_fail_before_any_core_request(self):
        events = []
        observed = {**RECEIPT_STUB, "writes": []}
        for change in ({"stub_sha256": "9" * 64}, {"entry_sha256": "9" * 64}, {"dump_hashes": {}},
                       {"writes": [{"node_id": 61}]}):
            events.clear()
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory, \
                    diagnostics.lane(lane.GROUP), \
                    patch.object(acceptance.aiohttp, "ClientSession",
                                 new=fake_session(events, value={**observed, **change})), \
                    patch.object(acceptance, "HomeAssistantWebSocketClient"), \
                    patch.object(acceptance, "HomeAssistantRestClient"), \
                    patch.object(acceptance, "CoreRuntime") as runtime:
                with self.assertRaises(AssertionError):
                    await acceptance.run(settings(), self.ENTRY, Path(directory), stub_url=acceptance.STUB_CONTROL_URL,
                                         stub_hashes=RECEIPT_STUB)
                runtime.assert_not_called()
                requests = [event for event in events if event[0] == "request"]
                self.assertEqual([(event[1], event[2]) for event in requests], [("GET", acceptance.STUB_CONTROL_URL)])


if __name__ == "__main__":
    unittest.main()
