"""Thread dataset and Matter topology read (HAMCP-231, A1 + A2).

``core.thread_matter_topology_read`` is runtime-only.  Until an owner-signed
registry references it the service refuses ``authority_unavailable`` with
zero Home Assistant commands.  With admission, exactly two constant payloads
exist: ``{"type": "thread/list_datasets"}`` and
``{"type": "matter/network_topology", "refresh": false}``.  Every other
``thread/*``, ``otbr/*`` and ``matter/*`` command is unreachable, ``refresh``
is the literal ``False``, and every LAN identifier class is dropped or
rewritten to a per-response counter reference
(docs/HAMCP_231_THREAD_MATTER_TOPOLOGY_INVESTIGATION.md sections 5.1, 8, 9).

Fixtures model Core 2026.11.0 (74cf5a49) ``components/thread/websocket_api.py``
``ws_list_datasets`` and ``components/matter/api.py`` ``websocket_network_topology``
over matter-python-client 1.5.5 ``NetworkTopology``.  Every value is synthetic;
credential-shaped values are assembled at test time.  Signed admission uses
the test-only ephemeral ``CoreSigner`` key.

The static ``get_thread_matter_topology`` route is wired: the gateway refuses
it with ``pending_signed_registry_admission`` before the app until a signed
registry admits the profile, and admission is also proven at the
``CoreRuntime`` and service boundaries.
"""

from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
import inspect
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

import hamcp162_synthetic as syn  # noqa: E402
from ha_mcp_engineering import thread_matter_topology as tm  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import profiles as core_profiles  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.models import CoreCapabilityClass  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    CORE_RUNTIME_CAPABILITY_PROFILES,
    CORE_THREAD_MATTER_TOPOLOGY_PROFILES,
)
from ha_mcp_engineering.capabilities import (  # noqa: E402
    build_capability_catalog,
    capability_for_tool,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    DELEGATED_CORE_REQUIREMENTS,
    F3_ENTRY_ADMISSION_PROFILES,
    PENDING_ADMISSION_TOOLS,
    STATIC_TOOL_CORE_REQUIREMENTS,
    static_tool_requirements,
    THREAD_MATTER_TOPOLOGY_PROFILE,
    THREAD_MATTER_TOPOLOGY_RESOURCE_TYPE,
    THREAD_MATTER_TOPOLOGY_TOOL,
)
from ha_mcp_engineering.providers import upstream_device_projection as device_projection  # noqa: E402
from ha_mcp_engineering.providers import upstream_platform_reads as platform_reads  # noqa: E402
from ha_mcp_engineering.providers.models import ProviderCapability  # noqa: E402
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    ANALYTICAL_PROVIDER_POLICIES,
    TOOL_CAPABILITY_POLICY,
    CapabilityRoute,
    routing_for_tool,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.tools import thread_matter_topology as public  # noqa: E402
from ha_mcp_engineering.tools.registry import ENGINEERING_STATIC_TOOL_NAMES  # noqa: E402

PROFILE = "core.thread_matter_topology_read"
TOOL = "get_thread_matter_topology"
PENDING = "pending_signed_registry_admission"
HA_TOKEN = syn.needle("hatoken")
ACCESS_SECRET = syn.needle("accesssecret")
CORE_VERSION = "2026.11.0"
MODULE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/thread_matter_topology.py"
TOOL_MODULE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/tools/thread_matter_topology.py"
REFERENCE_KEYS = (
    "capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint",
)
PINNED_FINGERPRINT = "sha256:d03f005cfc0dbaaa03aad3e78d9147f83321bb3dec8015aa69edb93e358ebb6c"

# The exact thread/otbr/matter command set Core 2026.11.0 (74cf5a49) registers:
# components/thread/websocket_api.py:22-31, components/otbr/websocket_api.py:33-38,
# components/matter/api.py:63-77 (investigation section 4).
CORE_2026_11_COMMANDS = frozenset({
    "matter/commission", "matter/commission_on_network", "matter/interview_node",
    "matter/network_topology", "matter/node_diagnostics", "matter/open_commissioning_window",
    "matter/ping_node", "matter/remove_matter_fabric", "matter/set_thread",
    "matter/set_wifi_credentials", "matter/subscribe_network_topology",
    "otbr/create_network", "otbr/info", "otbr/set_channel", "otbr/set_network",
    "thread/add_dataset_tlv", "thread/delete_dataset", "thread/discover_routers",
    "thread/get_dataset_tlv", "thread/list_datasets", "thread/set_preferred_border_agent",
    "thread/set_preferred_dataset",
})
NEVER_SENT = frozenset(tm.NEVER_SENT_COMMANDS)

# ---------------------------------------------------------------------------
# Synthetic identifier canaries (section 8 item 4).  None is a real address.
# ---------------------------------------------------------------------------
BSSID = "AA:BB:CC:DD:EE:FF"
XA_OTBR = "5AF1E2D3C4B5A697"  # HA OTBR extended address
XA_APPLE = "6B02F3E4D5C6B7A8"  # non-HA border router
XA_NODE = "7C13A4B5C6D7E8F9"  # commissioned Thread node
XA_MISSING = "8D24B5C6D7E8F90A"  # commissioned, no HA registry entry
XA_UNKNOWN = "9E35C6D7E8F90A1B"  # uncommissioned Thread neighbour
XA_ISOLATED = "AF46D7E8F90A1B2C"  # isolated BR: only in A1
XA_GHOST = "B057E8F90A1B2C3D"  # connection endpoint with no node
BORDER_AGENT = "c168f90a1b2c3d4e5f60718293a4b5c6"
BORDER_AGENT_2 = "d279a01b2c3d4e5f60718293a4b5c6d7"
SSID = "SynthHomeWiFiQ7"
HOST_NAME = "synth-otbr-q7.local"
IP_CANARY = "fd11:2233:4455::abcd"
IP4_CANARY = "192.0.2.77"
MAC_CANARY = "02:00:5E:10:20:30"
IDENTIFIER_CANARIES = (BSSID, XA_OTBR, XA_APPLE, XA_NODE, XA_MISSING, XA_UNKNOWN, XA_ISOLATED,
                       XA_GHOST, BORDER_AGENT, BORDER_AGENT_2, SSID, HOST_NAME, IP_CANARY,
                       IP4_CANARY, MAC_CANARY)
HA_DEVICE = "0123456789abcdef0123456789abcdef"
EXT_PAN_ID = "1122334455667788"
DATASET_ID = "01JSYNTHDATASET0000000000A"
DATASET_ID_2 = "01JSYNTHDATASET0000000000B"


def variants(value: str) -> set[str]:
    """Upper, lower, with and without separators, and dash-separated hex."""

    bare = tm.normalize_identifier(value)
    out = {value, value.upper(), value.lower(), bare, bare.lower()}
    if len(bare) % 2 == 0 and all(c in "0123456789ABCDEF" for c in bare):
        pairs = [bare[i:i + 2] for i in range(0, len(bare), 2)]
        for separator in (":", "-"):
            out |= {separator.join(pairs), separator.join(pairs).lower()}
    return out


def secret_material() -> dict[str, str]:
    """Credential-shaped values assembled at test time."""

    return {
        "network_key": "00112233" + "44556677" + "8899aabb" + "ccddeeff",
        "pskc": "c0ffee00" + "c0ffee11" + "c0ffee22" + "c0ffee33",
        "tlv": "0e080000" + "00000001" + "0000" + "35060004" + "001fffe0" + "02081111" + "1111" + "2222" + "2222",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def dataset(dataset_id: str = DATASET_ID, **extra) -> dict:
    """Core 2026.11.0 ``ws_list_datasets`` item (``websocket_api.py:163-180``)."""

    record = {
        "channel": 15,
        "created": "2026-11-01T12:00:00.123456+00:00",
        "dataset_id": dataset_id,
        "extended_pan_id": EXT_PAN_ID,
        "network_name": "SynthMesh",
        "pan_id": "1a2b",
        "preferred": True,
        "preferred_border_agent_id": BORDER_AGENT,
        "preferred_extended_address": XA_OTBR,
        "source": "SynthOTBR",
    }
    record.update(extra)
    return record


def datasets_result(count: int = 2) -> dict:
    items = [dataset()]
    if count > 1:
        items.append(dataset(DATASET_ID_2, preferred=False, source="Google",
                             preferred_border_agent_id=BORDER_AGENT_2,
                             preferred_extended_address=XA_ISOLATED, pan_id="None", channel=None))
    for index in range(2, count):
        items.append(dataset(f"01JSYNTHDATASET{index:011d}", preferred=False,
                             preferred_border_agent_id=None, preferred_extended_address=None))
    return {"datasets": items}


def node(graph_id: str, kind: str, network_type: str, **extra) -> dict:
    """matter-python-client 1.5.5 ``NetworkNode`` as Core serializes it (plus ``ha_device_id``)."""

    record = {
        "id": graph_id, "kind": kind, "network_type": network_type, "node_id": None, "role": None,
        "available": None, "is_bridge": None, "ext_address": None, "rloc16": None,
        "ext_pan_id": None, "network_name": None, "ssid": None, "bssid": None, "host_name": None,
        "vendor_name": None, "model_name": None, "last_seen": None, "ha_device_id": None,
    }
    record.update(extra)
    return record


def link(source: str, target: str, network: str = "thread", **extra) -> dict:
    record = {
        "source": source, "target": target, "network": network, "strength": "strong",
        "source_to_target": {"strength": "strong", "lqi": 200, "rssi": -52},
        "target_to_source": {"strength": "medium", "lqi": 150, "rssi": -71},
        "via_route_table": True, "path_cost": 1,
    }
    record.update(extra)
    return record


def topology_result() -> dict:
    """The section 9 fixture home."""

    return {
        "collected_at": 1_793_000_000_000,
        "nodes": [
            node(f"br_{XA_OTBR}", "border_router", "thread", ext_address=XA_OTBR, rloc16=0x0400,
                 ext_pan_id=EXT_PAN_ID, network_name="SynthMesh", host_name=HOST_NAME,
                 vendor_name="Home Assistant", model_name="OpenThread Border Router", last_seen=1_792_999_990_000,
                 role="leader", available=True),
            node(f"br_{XA_APPLE}", "border_router", "thread", ext_address=XA_APPLE, rloc16=0x0800,
                 ext_pan_id=EXT_PAN_ID, network_name="SynthMesh", vendor_name="Apple", model_name="HomePod",
                 role="router", available=True),
            node("1", "matter", "thread", node_id=1, role="router", available=True, is_bridge=False,
                 ext_address=XA_NODE, rloc16=0x0401, ext_pan_id=EXT_PAN_ID, network_name="SynthMesh",
                 vendor_name="Synthetic Lights", model_name="Bulb", ha_device_id=HA_DEVICE),
            node("2", "matter", "thread", node_id=2, role="end_device", available=False,
                 ext_address=XA_MISSING, rloc16=0x0802, ext_pan_id=EXT_PAN_ID, network_name="SynthMesh",
                 vendor_name="Synthetic Sensors"),
            node(f"unknown_{XA_UNKNOWN}", "thread_unknown", "thread", ext_address=XA_UNKNOWN,
                 network_name="SynthMesh"),
            node("3", "matter", "wifi", node_id=3, available=True, ssid=SSID, bssid=BSSID,
                 network_name=SSID, vendor_name="Synthetic Plugs", ha_device_id="f" * 32,
                 ip_addresses=[IP_CANARY, IP4_CANARY], mac_address=MAC_CANARY),
            node("ap_" + tm.normalize_identifier(BSSID), "wifi_ap", "wifi", bssid=BSSID, ssid=SSID,
                 network_name=BSSID, vendor_name=f"Router {BSSID.lower().replace(':', '-')}"),
        ],
        "connections": [
            link("1", f"br_{XA_OTBR}", path_cost=1),
            link("1", f"br_{XA_APPLE}", strength="weak", path_cost=3, via_route_table=False,
                 source_to_target={"strength": "weak", "lqi": 30, "rssi": -92},
                 target_to_source=None),
            link("2", "1", strength="medium", path_cost=2),
            link(f"unknown_{XA_UNKNOWN}", "1", strength="none", via_route_table=None, path_cost=None,
                 source_to_target=None, target_to_source=None),
            link("3", "ap_" + tm.normalize_identifier(BSSID), "wifi", source_to_target=None,
                 target_to_source=None, via_route_table=None, path_cost=None),
            link("1", f"br_{XA_GHOST}"),  # endpoint is not a node: dropped, never raw
        ],
    }


def outcomes(datasets=None, topology=None, *, datasets_reason=None, topology_reason=None,
             sections=tm.SECTIONS) -> dict[str, tm.SectionOutcome]:
    out = {}
    if tm.DATASETS in sections:
        out[tm.DATASETS] = tm.SectionOutcome(
            tm.DATASETS, True, None if datasets_reason else (datasets_result() if datasets is None else datasets),
            datasets_reason)
    if tm.TOPOLOGY in sections:
        out[tm.TOPOLOGY] = tm.SectionOutcome(
            tm.TOPOLOGY, True, None if topology_reason else (topology_result() if topology is None else topology),
            topology_reason)
    return out


def result_frame(command_id: int, result) -> str:
    return json.dumps({"id": command_id, "type": "result", "success": True, "result": result})


def error_frame(command_id: int, code, message: str = "Unknown command.") -> str:
    return json.dumps({"id": command_id, "type": "result", "success": False,
                       "error": {"code": code, "message": message}})


def references() -> list[dict]:
    return [{key: profile.to_mapping()[key] for key in REFERENCE_KEYS}
            for profile in CORE_THREAD_MATTER_TOPOLOGY_PROFILES]


def profile_contract() -> dict:
    """The response contract material is part of the fingerprint; re-derive it."""

    source = inspect.getsource(core_profiles)
    start = source.index("CORE_THREAD_MATTER_TOPOLOGY_PROFILES = (")
    end = source.index("# HAMCP-290 (Release 2.5.0)")
    tree = ast.parse(source[start:end])
    call = tree.body[0].value.elts[0]
    for keyword in call.keywords:
        if keyword.arg == "response_contract":
            return ast.literal_eval(keyword.value)
    raise AssertionError("response_contract missing")


class AdmissionCore:
    def __init__(self, available: bool = True, version: str = CORE_VERSION):
        self.available = available
        self.generation = 7
        self.current_observation = SimpleNamespace(version=version)
        self.calls: list[tuple] = []

    def route_status(self, requirements):
        self.calls.append(tuple(requirements))
        return {"available": self.available, "generation": self.generation}


class FakeReader:
    def __init__(self, result=None, error=None, during=None):
        self.result = result
        self.error = error
        self.during = during
        self.calls = 0
        self.sections = None

    async def read(self, *, sections, core_version, authorize, on_dispatch=lambda _c: None,
                   on_sent=lambda _c: None):
        self.calls += 1
        self.sections = sections
        for section in sections:
            authorize()
            on_dispatch(tm.PAYLOADS[section]["type"])
            on_sent(tm.PAYLOADS[section]["type"])
        if self.during is not None:
            self.during()
        authorize()
        if self.error is not None:
            raise self.error
        return outcomes(sections=sections) if self.result is None else deepcopy(self.result)


class ServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = AdmissionCore()
        self.telemetry, self.token = begin_request()
        self.telemetry.caller_id = "synthetic_authenticated_caller"
        self.telemetry.core_dispatch_authorizer = lambda: self.core.available
        self.addCleanup(end_request, self.token)

    def service(self, reader) -> tm.ThreadMatterTopologyService:
        return tm.ThreadMatterTopologyService(reader, self.core, known_secrets=(ACCESS_SECRET, HA_TOKEN))

    async def refused(self, coroutine, reason: str) -> tm.TopologyError:
        with self.assertRaises(tm.TopologyError) as raised:
            await coroutine
        self.assertEqual(raised.exception.reason, reason)
        return raised.exception


# ---------------------------------------------------------------------------
# Contract, profile and vocabulary
# ---------------------------------------------------------------------------

class ContractTests(unittest.TestCase):
    def test_profile_is_runtime_only_semantic_read_without_fallback(self):
        self.assertIn(PROFILE, {p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES})
        self.assertNotIn(PROFILE, {p.capability_id for p in CORE_CAPABILITY_PROFILES})
        (profile,) = CORE_THREAD_MATTER_TOPOLOGY_PROFILES
        mapping = profile.to_mapping()
        self.assertEqual(profile.capability_id, PROFILE)
        self.assertEqual(mapping["profile_id"], "core_thread_matter_topology_read_v1")
        self.assertIs(profile.capability_class, CoreCapabilityClass.THREAD_MATTER_TOPOLOGY_READ)
        self.assertFalse(profile.capability_class.mutation_capable)
        self.assertTrue(profile.capability_class.semantic)
        self.assertEqual(profile.adapter_id, tm.PROVIDER_CONTRACT)
        self.assertEqual(profile.contract_fingerprint, PINNED_FINGERPRINT)
        self.assertEqual(THREAD_MATTER_TOPOLOGY_PROFILE, PROFILE)
        self.assertEqual(THREAD_MATTER_TOPOLOGY_TOOL, TOOL)
        self.assertEqual(THREAD_MATTER_TOPOLOGY_RESOURCE_TYPE, "thread_matter_topology")
        self.assertIn(PROFILE, F3_ENTRY_ADMISSION_PROFILES)
        self.assertEqual(ProviderCapability.THREAD_MATTER_TOPOLOGY.value, "thread_matter_topology")
        # Never compiled exact authority on any supported Core: signing only.
        for version, _commit, _digest in core_profiles.SUPPORTED_CORE_RELEASES:
            with self.subTest(version=version):
                self.assertNotIn(PROFILE, {
                    item.capability_ids[0] for item in core_profiles.compiled_exact_authority(version)})

    def test_no_existing_fingerprint_changes(self):
        others = [p for p in CORE_RUNTIME_CAPABILITY_PROFILES if p.capability_id != PROFILE]
        self.assertEqual(len(others), len(CORE_RUNTIME_CAPABILITY_PROFILES) - 1)
        self.assertNotIn(PINNED_FINGERPRINT, {p.contract_fingerprint for p in others})
        # The block sits outside HAMCP-290's fingerprint-material slice.
        source = inspect.getsource(core_profiles)
        start = source.index("CORE_MARKETPLACE_PROFILES = (")
        end = source.index("CORE_RUNTIME_CAPABILITY_PROFILES = (")
        self.assertNotIn("thread_matter", source[start:end])

    def test_contract_matches_the_module_constants(self):
        contract = profile_contract()
        self.assertEqual(contract["commands"], [tm.LIST_DATASETS_COMMAND, tm.NETWORK_TOPOLOGY_COMMAND])
        self.assertEqual(contract["payloads"], {
            tm.LIST_DATASETS_COMMAND: dict(tm.DATASETS_PAYLOAD),
            tm.NETWORK_TOPOLOGY_COMMAND: dict(tm.TOPOLOGY_PAYLOAD),
        })
        self.assertIs(contract["payloads"][tm.NETWORK_TOPOLOGY_COMMAND]["refresh"], False)
        self.assertEqual(contract["max_commands"], tm.MAX_COMMANDS)
        self.assertEqual(contract["caps"], {"datasets": tm.MAX_DATASETS, "nodes": tm.MAX_NODES,
                                            "connections": tm.MAX_CONNECTIONS})
        self.assertEqual(contract["bounds"], {
            "frame_bytes": tm.FRAME_BYTES, "auth_bytes": tm.AUTH_BYTES, "max_frames": tm.MAX_FRAMES,
            "max_upstream_datasets": tm.MAX_UPSTREAM_DATASETS, "max_upstream_nodes": tm.MAX_UPSTREAM_NODES,
            "max_upstream_connections": tm.MAX_UPSTREAM_CONNECTIONS, "max_seconds": int(tm.MAX_SECONDS),
        })
        self.assertEqual(set(contract["reasons"]), tm.SECTION_REASONS)
        self.assertEqual(contract["secret_keys"]["match"], list(tm.SECRET_KEY_MARKERS))
        self.assertEqual(contract["upstream_activity"], {k: list(v) for k, v in tm.UPSTREAM_ACTIVITY.items()})
        self.assertEqual(contract["never_sent"], list(tm.NEVER_SENT_COMMANDS))
        self.assertEqual(contract["identifier_policy"]["id"], tm.IDENTIFIER_POLICY)
        self.assertEqual(contract["write"], "none")
        self.assertEqual(contract["principal_origin"], "existing_configured_authenticated_origin_only")

    def test_payloads_are_the_closed_vocabulary(self):
        self.assertEqual(dict(tm.DATASETS_PAYLOAD), {"type": "thread/list_datasets"})
        self.assertEqual(dict(tm.TOPOLOGY_PAYLOAD), {"type": "matter/network_topology", "refresh": False})
        self.assertIs(tm.TOPOLOGY_PAYLOAD["refresh"], False)
        for payload in (tm.DATASETS_PAYLOAD, tm.TOPOLOGY_PAYLOAD):
            with self.assertRaises(TypeError):
                payload["refresh"] = True  # type: ignore[index]
        self.assertEqual(tm.COMMANDS, {"thread/list_datasets", "matter/network_topology"})
        self.assertEqual(tm.MAX_COMMANDS, 2)
        # Every other Core 2026.11.0 thread/otbr/matter command is never sent.
        self.assertEqual(NEVER_SENT, CORE_2026_11_COMMANDS - tm.COMMANDS)
        self.assertEqual(len(tm.NEVER_SENT_COMMANDS), 20)
        self.assertFalse(tm.COMMANDS & NEVER_SENT)

    def test_source_builds_no_other_command_and_refresh_is_a_literal_false(self):
        for path in (MODULE, TOOL_MODULE):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            literals = {
                node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value.startswith(("thread/", "otbr/", "matter/"))
            }
            with self.subTest(path=path.name):
                self.assertTrue(literals - NEVER_SENT <= tm.COMMANDS)
            refresh_values = [
                value for node in ast.walk(tree) if isinstance(node, ast.Dict)
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and key.value == "refresh"
            ]
            if path == MODULE:
                self.assertEqual(len(refresh_values), 1)
                self.assertIsInstance(refresh_values[0], ast.Constant)
                self.assertIs(refresh_values[0].value, False)
            else:
                self.assertEqual(refresh_values, [])
            # No direct OTBR, Matter Server, REST or ha-mcp path (section 8 item 10).
            imported = {
                (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
                for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
            }
            self.assertFalse({name for name in imported if "upstream" in name or "gateway" in name
                              or "http_client" in name or "rest" in name}, imported)
        source = MODULE.read_text(encoding="utf-8")
        for forbidden in ("5580", "8081", "core-matter-server", "otbr_url", "http://", "https://"):
            self.assertNotIn(forbidden, source)

    def test_public_schema_is_closed_with_no_refresh_input(self):
        registered = public.registered_tool()
        self.assertEqual(registered.name, TOOL)
        self.assertIs(registered.annotations.readOnlyHint, True)
        self.assertIs(registered.annotations.destructiveHint, False)
        self.assertIs(registered.annotations.idempotentHint, True)
        self.assertIs(registered.annotations.openWorldHint, False)
        self.assertIs(registered.parameters.get("additionalProperties"), False)
        self.assertEqual(set(registered.parameters["properties"]), {"sections"})
        self.assertEqual(registered.parameters.get("required", []), [])
        items = registered.parameters["properties"]["sections"]["anyOf"][0]["items"]
        self.assertEqual(items["enum"], ["datasets", "topology"])
        self.assertFalse(TOOL.startswith("ha_"))


class ArgumentTests(unittest.TestCase):
    def test_defaults_and_canonical_order(self):
        self.assertEqual(tm.validate_arguments({}).sections, ("datasets", "topology"))
        self.assertEqual(tm.validate_arguments({"sections": None}).sections, ("datasets", "topology"))
        self.assertEqual(tm.validate_arguments({"sections": ["topology", "datasets"]}).sections,
                         ("datasets", "topology"))
        self.assertEqual(tm.validate_arguments({"sections": ["topology"]}).sections, ("topology",))

    def test_refresh_is_refused_through_every_input_path(self):
        # Section 8 item 2: no caller-supplied refresh reaches the payload.
        for arguments in ({"refresh": True}, {"refresh": False}, {"sections": ["topology"], "refresh": True},
                          {"sections": ["topology"], "refresh": "true"}, {"device_id": "x"},
                          {"sections": [{"refresh": True}]}, {"sections": ["topology", "refresh"]}):
            with self.subTest(arguments=arguments), self.assertRaises(tm.TopologyError) as raised:
                tm.validate_arguments(arguments)
            self.assertEqual(raised.exception.reason, "invalid_arguments")
            self.assertEqual(raised.exception.details, {"reason": "invalid_arguments"})

    def test_closed_values(self):
        for arguments in (None, [], "datasets", {"sections": []}, {"sections": "datasets"},
                          {"sections": ["datasets", "datasets"]}, {"sections": ["border_routers"]},
                          {"sections": ["node"]}, {"sections": ["datasets", "topology", "x"]},
                          {"sections": [1]}):
            with self.subTest(arguments=arguments), self.assertRaises(tm.TopologyError):
                tm.validate_arguments(arguments)


class ClassifyReplyTests(unittest.TestCase):
    def reason(self, message, section=tm.TOPOLOGY, expected_id=1):
        with self.assertRaises(tm.SectionRefused) as raised:
            tm.classify_reply(message, expected_id, section)
        return raised.exception.reason

    def test_success_returns_the_result(self):
        self.assertEqual(tm.classify_reply(json.loads(result_frame(1, {"datasets": []})), 1, tm.DATASETS),
                         {"datasets": []})

    def test_typed_failures(self):
        # Section 8 item 9.
        table = (
            (tm.DATASETS, "unknown_command", "thread_not_loaded"),
            (tm.TOPOLOGY, "unknown_command", "matter_not_loaded"),
            (tm.TOPOLOGY, "not_supported", "matter_server_schema_below_13"),
            (tm.DATASETS, "not_supported", "upstream_command_failed"),
            (tm.TOPOLOGY, "unauthorized", "core_authority_refused"),
            (tm.DATASETS, "forbidden", "core_authority_refused"),
            (tm.TOPOLOGY, "node_not_found", "upstream_command_failed"),
            (tm.TOPOLOGY, "unknown_error", "upstream_command_failed"),
            (tm.TOPOLOGY, 5, "malformed_upstream_response"),
        )
        for section, code, expected in table:
            with self.subTest(section=section, code=code):
                self.assertEqual(self.reason(json.loads(error_frame(1, code, f"{HA_TOKEN} {XA_OTBR}")),
                                             section), expected)

    def test_malformed_results_are_section_local(self):
        for message in ({"id": 1, "type": "result", "success": True, "result": []},
                        {"id": 1, "type": "result", "success": True, "result": None},
                        {"id": 1, "type": "result", "success": True, "result": {}, "error": {}},
                        {"id": 1, "type": "result", "success": False, "result": {}, "error": {"code": "x"}},
                        {"id": 1, "type": "result", "success": False, "error": "unknown_command"}):
            with self.subTest(message=message):
                self.assertEqual(self.reason(message), "malformed_upstream_response")

    def test_envelope_mismatch_is_a_stream_fault(self):
        for message in ({"id": 2, "type": "result", "success": True, "result": {}},
                        {"id": 1, "type": "event", "success": True, "result": {}},
                        {"id": True, "type": "result", "success": True, "result": {}},
                        {"id": 1, "type": "result", "success": 1, "result": {}}, []):
            with self.subTest(message=message), self.assertRaises(tm.TopologyError) as raised:
                tm.classify_reply(message, 1, tm.TOPOLOGY)
            self.assertEqual(raised.exception.reason, "malformed_upstream_response")


# ---------------------------------------------------------------------------
# Projection, identifiers and secrets
# ---------------------------------------------------------------------------

class ProjectionTests(unittest.TestCase):
    def test_fixture_home_useful_success(self):
        # Section 9 acceptance.
        result = tm.build_result(outcomes())
        self.assertEqual(result["identifier_policy"], "opaque_per_response_v1")
        self.assertEqual(result["sections"], ["datasets", "topology"])
        topology = result["topology"]
        self.assertEqual(topology["status"], "ok")
        nodes = {n["id"]: n for n in topology["nodes"]}
        self.assertEqual(list(nodes), ["g1", "g2", "g3", "g4", "g5", "g6", "g7"])
        otbr, apple, light, missing, unknown, plug, ap = (nodes[f"g{i}"] for i in range(1, 8))
        self.assertEqual((otbr["kind"], otbr["role"], otbr["rloc16"], otbr["ext_pan_id"]),
                         ("border_router", "leader", 0x0400, EXT_PAN_ID))
        self.assertEqual(apple["kind"], "border_router")
        self.assertEqual((light["ha_device_id"], light["identity"], light["node_id"], light["available"]),
                         (HA_DEVICE, "ha_device", 1, True))
        self.assertEqual((missing["ha_device_id"], missing["identity"]), (None, "ha_registry_match_missing"))
        for item in (unknown, ap, otbr, apple):
            self.assertEqual((item["ha_device_id"], item["identity"]), (None, "not_an_ha_device"))
        self.assertEqual(plug["identity"], "ha_device")
        self.assertEqual(otbr["border_router_mdns_last_seen"], 1_792_999_990_000)
        self.assertNotIn("last_seen", otbr)
        # Every emitted edge resolves; the ghost edge is dropped and counted.
        connections = topology["connections"]
        self.assertEqual(topology["connections_dropped_unresolved"], 1)
        self.assertEqual(len(connections), topology["total"]["connections"] - 1)
        for connection in connections:
            self.assertIn(connection["source"], nodes)
            self.assertIn(connection["target"], nodes)
        first = connections[0]
        self.assertEqual((first["source"], first["target"], first["path_cost"], first["via_route_table"]),
                         ("g3", "g1", 1, True))
        self.assertEqual(first["source_to_target"], {"strength": "strong", "lqi": 200, "rssi": -52})
        weak = connections[1]
        self.assertEqual((weak["strength"], weak["source_to_target"]["lqi"], weak["target_to_source"]),
                         ("weak", 30, None))
        # Coverage and semantics are stated, not fabricated.
        self.assertEqual(topology["coverage"], {"border_routers": "neighbours_of_commissioned_nodes_only"})
        self.assertEqual(topology["connection_semantics"], "reported_links_not_end_to_end_paths")
        self.assertEqual(topology["upstream_activity"], ["cached_graph", "may_start_br_discovery"])
        # A1: the HA OTBR shares one opaque reference with its A2 node.
        datasets = result["datasets"]
        self.assertEqual(datasets["status"], "ok")
        self.assertEqual(datasets["upstream_activity"], ["cached_graph"])
        first, second = datasets["items"]
        self.assertEqual(first["preferred_extended_address_ref"], "g1")
        self.assertEqual(first["preferred_border_agent_ref"], "b1")
        self.assertEqual((second["preferred_extended_address_ref"], second["preferred_border_agent_ref"]),
                         ("x1", "b2"))
        # Kept network-level values (PAN IDs kept by design, section 5.1.2).
        self.assertEqual((first["extended_pan_id"], first["pan_id"], first["channel"], first["network_name"]),
                         (EXT_PAN_ID, "1a2b", 15, "SynthMesh"))
        self.assertEqual((second["pan_id"], second["channel"]), (None, None))
        self.assertEqual(result["completeness"], "partial")
        self.assertIn("connections_dropped_unresolved", result["partial_reasons"])
        self.assertIn("sanitized_text", result["partial_reasons"])  # the AP vendor text

    def test_network_name_by_type(self):
        # Section 8 item 5.
        nodes = {n["id"]: n for n in tm.build_result(outcomes())["topology"]["nodes"]}
        self.assertEqual((nodes["g3"]["network_name"], nodes["g3"]["network_name_withheld"]), ("SynthMesh", False))
        for reference in ("g6", "g7"):  # Wi-Fi station and Wi-Fi AP
            self.assertIsNone(nodes[reference]["network_name"])
            self.assertIs(nodes[reference]["network_name_withheld"], True)

    def test_identifier_canaries_never_appear_in_output(self):
        # Section 8 item 4, projection level (see also the end-to-end test).
        encoded = json.dumps(tm.build_result(outcomes()))
        for canary in IDENTIFIER_CANARIES:
            for variant in variants(canary):
                with self.subTest(variant=variant):
                    self.assertNotIn(variant, encoded)
        for key in ("ext_address", "bssid", "ssid", "host_name", "ip_addresses", "mac_address",
                    "preferred_border_agent_id", "preferred_extended_address", "last_seen"):
            self.assertNotIn(f'"{key}"', encoded)

    def test_free_text_carrying_an_identifier_is_withheld(self):
        topology = topology_result()
        topology["nodes"][1]["vendor_name"] = f"Apple {XA_OTBR.lower()}"
        topology["nodes"][1]["model_name"] = f"HomePod {HOST_NAME.upper()}"
        topology["nodes"][2]["network_name"] = f"Mesh-{SSID}"
        data = deepcopy(datasets_result())
        data["datasets"][0]["source"] = f"OTBR {BORDER_AGENT.upper()}"
        data["datasets"][1]["network_name"] = "Mesh " + ":".join(XA_ISOLATED[i:i + 2] for i in range(0, 16, 2))
        result = tm.build_result(outcomes(data, topology))
        nodes = {n["id"]: n for n in result["topology"]["nodes"]}
        self.assertIsNone(nodes["g2"]["vendor_name"])
        self.assertIsNone(nodes["g2"]["model_name"])
        self.assertEqual((nodes["g3"]["network_name"], nodes["g3"]["network_name_withheld"]), (None, True))
        self.assertIsNone(result["datasets"]["items"][0]["source"])
        self.assertIsNone(result["datasets"]["items"][1]["network_name"])
        self.assertEqual(result["withheld_identifier_field_count"], 6)  # five above + the AP vendor
        self.assertIn("sanitized_text", result["partial_reasons"])

    def test_diagnostic_mesh_ids_are_screened_on_nodes_and_endpoints(self):
        # Regression: matterjs-server a7922e85 also builds ``thread_<EXTMAC>`` and
        # ``meshrloc_<EXTPANID>_<RLOC16>`` ids (topology-utils.ts:428-429). The
        # embedded identifier must be screened even with no ext_address field,
        # including on a ghost connection endpoint that is never a node.
        mesh_mac, ghost_mac = "C168F90A1B2C3D4E", "D279A1B2C3D4E5F6"
        topology = topology_result()
        base_nodes = len(topology["nodes"])
        topology["nodes"].append(node(f"thread_{mesh_mac}", "thread_unknown", "thread"))
        topology["nodes"].append(node(f"meshrloc_{EXT_PAN_ID}_17408", "thread_unknown", "thread"))
        topology["connections"].append(link("1", f"thread_{mesh_mac}"))
        topology["connections"].append(link("1", f"thread_{ghost_mac}"))
        topology["nodes"][1]["vendor_name"] = f"Apple {mesh_mac.lower()}"
        topology["nodes"][1]["model_name"] = f"HomePod {ghost_mac}"
        data = deepcopy(datasets_result())
        data["datasets"][1]["preferred_extended_address"] = mesh_mac
        result = tm.build_result(outcomes(data, topology))
        section = result["topology"]
        nodes = {n["id"]: n for n in section["nodes"]}
        self.assertIsNone(nodes["g2"]["vendor_name"])
        self.assertIsNone(nodes["g2"]["model_name"])
        mesh_ref = f"g{base_nodes + 1}"
        self.assertEqual(nodes[mesh_ref]["kind"], "thread_unknown")
        self.assertIn(("g3", mesh_ref), [(e["source"], e["target"]) for e in section["connections"]])
        self.assertEqual(section["connections_dropped_unresolved"], 2)  # br_ ghost and thread_ ghost
        self.assertEqual(result["datasets"]["items"][1]["preferred_extended_address_ref"], mesh_ref)
        encoded = json.dumps(result)
        for canary in (mesh_mac, ghost_mac, f"thread_{mesh_mac}", "meshrloc"):
            for variant in variants(canary):
                with self.subTest(variant=variant):
                    self.assertNotIn(variant, encoded)

    def test_secret_keys_refuse_the_whole_section(self):
        # Section 8 item 3 / 5.1.2 rule 6: any depth, any case, any separator.
        material = secret_material()
        placements = (
            ("networkKey", material["network_key"]),
            ("network_key", material["network_key"]),
            ("PSKc", material["pskc"]),
            ("active_dataset_tlvs", material["tlv"]),
            ("tlv", material["tlv"]),
            ("pskd", "SYNTHPSKDQ7"),
        )
        for key, value in placements:
            for section in (tm.DATASETS, tm.TOPOLOGY):
                data, topology = datasets_result(), topology_result()
                if section == tm.DATASETS:
                    data["datasets"][0][key] = value
                else:
                    topology["nodes"][2]["extra"] = {"deeper": [{key: value}]}
                with self.subTest(key=key, section=section):
                    result = tm.build_result(outcomes(data, topology))
                    encoded = json.dumps(result)
                    self.assertEqual(result[section]["status"], "malformed_upstream_response")
                    other = tm.TOPOLOGY if section == tm.DATASETS else tm.DATASETS
                    self.assertEqual(result[other]["status"], "ok")  # not hidden
                    self.assertNotIn(value, encoded)
                    self.assertNotIn(value.upper(), encoded)
                    self.assertIn("section_unavailable", result["partial_reasons"])

    def test_tlv_shaped_hex_in_unexpected_or_text_fields_never_appears(self):
        material = secret_material()
        data, topology = datasets_result(), topology_result()
        data["datasets"][0]["unexpected"] = material["tlv"]  # unknown key: dropped
        data["datasets"][0]["network_name"] = material["tlv"]  # kept text: withheld
        topology["nodes"][0]["vendor_name"] = "OTBR " + material["network_key"]
        topology["nodes"][0]["ip"] = IP_CANARY
        topology["connections"][0]["mac"] = MAC_CANARY
        encoded = json.dumps(tm.build_result(outcomes(data, topology)))
        for value in (*material.values(), IP_CANARY, MAC_CANARY):
            self.assertNotIn(value, encoded)
            self.assertNotIn(value.upper(), encoded)

    def test_known_secrets_are_redacted_from_text(self):
        topology = topology_result()
        topology["nodes"][2]["model_name"] = f"Bulb {HA_TOKEN}"
        result = tm.build_result(outcomes(topology=topology), (ACCESS_SECRET, HA_TOKEN))
        self.assertNotIn(HA_TOKEN, json.dumps(result))
        self.assertIsNotNone(result["redaction"])

    def test_malformed_values_fail_the_section_closed(self):
        mutations = {
            tm.TOPOLOGY: (
                lambda t: t.update(collected_at="now"),
                lambda t: t.update(collected_at=-1),
                lambda t: t.update(nodes={}),
                lambda t: t["nodes"].append(deepcopy(t["nodes"][0])),  # duplicate id
                lambda t: t["nodes"][0].update(kind="Border Router"),
                lambda t: t["nodes"][0].update(rloc16=70000),
                lambda t: t["nodes"][0].update(ext_pan_id="xyz"),
                lambda t: t["nodes"][2].update(ha_device_id="not-a-device"),
                lambda t: t["nodes"][2].update(available="yes"),
                lambda t: t["nodes"][2].update(node_id=-1),
                lambda t: t["connections"][0].update(path_cost=1.5),
                lambda t: t["connections"][0]["source_to_target"].update(lqi=999),
                lambda t: t["connections"][0]["source_to_target"].update(rssi=-200),
                lambda t: t["connections"][0].update(source=None),
                lambda t: t["nodes"][0].update(vendor_name=7),
            ),
            tm.DATASETS: (
                lambda d: d.update(datasets=None),
                lambda d: d["datasets"][0].update(dataset_id="short"),
                lambda d: d["datasets"][1].update(dataset_id=DATASET_ID),
                lambda d: d["datasets"][0].update(extended_pan_id="11223344"),
                lambda d: d["datasets"][0].update(pan_id="12345"),
                lambda d: d["datasets"][0].update(channel="15"),
                lambda d: d["datasets"][0].update(preferred=None),
                lambda d: d["datasets"][0].update(created="yesterday"),
            ),
        }
        for section, changes in mutations.items():
            for index, change in enumerate(changes):
                data, topology = datasets_result(), topology_result()
                change(data if section == tm.DATASETS else topology)
                with self.subTest(section=section, index=index):
                    result = tm.build_result(outcomes(data, topology))
                    self.assertEqual(result[section]["status"], "malformed_upstream_response")
                    self.assertEqual(set(result[section]), {"status", "upstream_activity"})

    def test_freshness_is_assembly_time_not_data_age(self):
        # Section 8 item 7.
        first = topology_result()
        later = topology_result()
        later["collected_at"] = first["collected_at"] + 90_000
        a = tm.build_result(outcomes(topology=first, sections=(tm.TOPOLOGY,)))["topology"]
        b = tm.build_result(outcomes(topology=later, sections=(tm.TOPOLOGY,)))["topology"]
        self.assertEqual(a["graph_assembled_at"], "2026-10-26T07:33:20.000Z")
        self.assertGreater(b["graph_assembled_at"], a["graph_assembled_at"])
        self.assertEqual((a["observation_age"], b["observation_age"]), ("unknown", "unknown"))
        self.assertEqual(a["nodes"], b["nodes"])
        encoded = json.dumps(b)
        for claim in ("current", "fresh", "live", "observed_at", "\"last_seen\""):
            self.assertNotIn(claim, encoded)

    def test_bounds_truncate_with_accurate_totals(self):
        # Section 8 item 8: one over each cap.
        topology = topology_result()
        base = len(topology["nodes"])
        topology["nodes"] += [node(f"unknown_{i:016X}", "thread_unknown", "thread")
                              for i in range(tm.MAX_NODES + 1 - base)]
        result = tm.build_result(outcomes(datasets_result(tm.MAX_DATASETS + 1), topology))
        t, d = result["topology"], result["datasets"]
        self.assertEqual((len(t["nodes"]), t["total"]["nodes"], t["nodes_truncated"], t["truncated"]),
                         (tm.MAX_NODES, tm.MAX_NODES + 1, True, True))
        self.assertEqual((len(d["items"]), d["total"], d["truncated"]), (tm.MAX_DATASETS, tm.MAX_DATASETS + 1, True))
        self.assertIn("truncated", result["partial_reasons"])

        topology = topology_result()
        topology["connections"] = [link("1", f"br_{XA_OTBR}") for _ in range(tm.MAX_CONNECTIONS + 1)]
        t = tm.build_result(outcomes(topology=topology, sections=(tm.TOPOLOGY,)))["topology"]
        self.assertEqual((len(t["connections"]), t["total"]["connections"], t["connections_truncated"]),
                         (tm.MAX_CONNECTIONS, tm.MAX_CONNECTIONS + 1, True))

    def test_oversized_strings_are_capped(self):
        topology = topology_result()
        topology["nodes"][2]["vendor_name"] = "v" * (tm.LABEL_CHARS + 40)
        topology["nodes"][2]["network_name"] = "n" * (tm.NAME_CHARS + 40)
        result = tm.build_result(outcomes(topology=topology, sections=(tm.TOPOLOGY,)))
        light = result["topology"]["nodes"][2]
        # The shared sanitizer keeps ``bound`` characters plus its marker.
        marker = "...<truncated>"
        self.assertEqual(light["vendor_name"], "v" * tm.LABEL_CHARS + marker)
        self.assertEqual(light["network_name"], "n" * tm.NAME_CHARS + marker)
        self.assertEqual(result["truncated_field_count"], 2)
        self.assertIn("sanitized_text", result["partial_reasons"])

    def test_upstream_list_over_the_acquisition_bound_is_too_large(self):
        topology = topology_result()
        topology["connections"] = [link("1", "2")] * (tm.MAX_UPSTREAM_CONNECTIONS + 1)
        data = {"datasets": [dataset()] * (tm.MAX_UPSTREAM_DATASETS + 1)}
        result = tm.build_result(outcomes(data, topology))
        self.assertEqual(result["topology"]["status"], "upstream_response_too_large")
        self.assertEqual(result["datasets"]["status"], "upstream_response_too_large")

    def test_failed_sections_do_not_hide_each_other(self):
        result = tm.build_result(outcomes(topology_reason="matter_not_loaded"))
        self.assertEqual(result["topology"], {"status": "matter_not_loaded",
                                              "upstream_activity": ["cached_graph", "may_start_br_discovery"]})
        self.assertEqual(result["datasets"]["status"], "ok")
        self.assertEqual(result["datasets"]["items"][0]["preferred_extended_address_ref"], "x1")
        self.assertEqual(result["completeness"], "partial")
        self.assertIn("section_unavailable", result["partial_reasons"])
        undispatched = {tm.TOPOLOGY: tm.SectionOutcome(tm.TOPOLOGY, False, None, "timeout")}
        self.assertEqual(tm.build_result(undispatched)["topology"], {"status": "timeout", "upstream_activity": []})

    def test_clean_single_section_is_complete(self):
        data = datasets_result(1)
        result = tm.build_result(outcomes(data, sections=(tm.DATASETS,)))
        self.assertEqual(result["sections"], ["datasets"])
        self.assertNotIn("topology", result)
        self.assertEqual((result["completeness"], result["partial_reasons"]), ("complete", []))


# ---------------------------------------------------------------------------
# Service admission and authority
# ---------------------------------------------------------------------------

class ServiceAdmissionTests(ServiceTestCase):
    async def test_unadmitted_profile_refuses_with_zero_reads(self):
        self.core.available = False
        reader = FakeReader()
        error = await self.refused(self.service(reader).topology(tm.TopologyRequest()), "authority_unavailable")
        self.assertEqual(reader.calls, 0)
        self.assertEqual(error.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(error.details, {"reason": "authority_unavailable", "core_capability": PROFILE})

    async def test_unadmitted_profile_refuses_even_with_dispatch_authority(self):
        self.core.available = False
        self.telemetry.core_dispatch_authorizer = lambda: True
        reader = FakeReader()
        await self.refused(self.service(reader).topology(tm.TopologyRequest()), "authority_unavailable")
        self.assertEqual(reader.calls, 0)

    async def test_missing_dispatch_authority_or_observation_refuses(self):
        reader = FakeReader()
        self.telemetry.core_dispatch_authorizer = None
        await self.refused(self.service(reader).topology(tm.TopologyRequest()), "authority_unavailable")
        self.telemetry.core_dispatch_authorizer = lambda: True
        self.core.current_observation = None
        await self.refused(self.service(reader).topology(tm.TopologyRequest()), "authority_unavailable")
        self.assertEqual(reader.calls, 0)

    async def test_anonymous_caller_refuses(self):
        self.telemetry.caller_id = "anonymous"
        reader = FakeReader()
        await self.refused(self.service(reader).topology(tm.TopologyRequest()), "authority_unavailable")
        self.assertEqual(reader.calls, 0)

    async def test_non_request_refuses(self):
        reader = FakeReader()
        await self.refused(self.service(reader).topology({"refresh": True}), "invalid_arguments")
        self.assertEqual(reader.calls, 0)

    async def test_admission_asks_for_exactly_this_profile(self):
        await self.service(FakeReader()).topology(tm.TopologyRequest())
        self.assertEqual(tm.CORE_REQUIREMENTS, (PROFILE,))
        self.assertEqual(set(self.core.calls), {(PROFILE,)})

    async def test_authority_change_across_the_read_refuses(self):
        for change in (lambda: setattr(self.core, "generation", 8),
                       lambda: setattr(self.core, "available", False),
                       lambda: setattr(self.core.current_observation, "version", "2026.11.1")):
            self.core = AdmissionCore()
            with self.subTest(change=change):
                await self.refused(self.service(FakeReader(during=change)).topology(tm.TopologyRequest()),
                                   "authority_unavailable")

    async def test_authority_change_while_projecting_does_not_publish(self):
        original = tm.build_result

        def flipping(*args, **kwargs):
            value = original(*args, **kwargs)
            self.core.generation += 1
            return value

        with mock.patch.object(tm, "build_result", flipping):
            await self.refused(self.service(FakeReader()).topology(tm.TopologyRequest()), "authority_unavailable")

    async def test_unexpected_failure_is_closed_without_text(self):
        def raising(*_args, **_kwargs):
            raise RuntimeError(f"SYNTHETIC_PRIVATE {HA_TOKEN} {XA_OTBR}")

        with mock.patch.object(tm, "build_result", raising):
            error = await self.refused(self.service(FakeReader()).topology(tm.TopologyRequest()), "provider_error")
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(error.details) + str(error))
        self.assertNotIn(XA_OTBR, json.dumps(error.details) + str(error))

    async def test_dispatch_outside_the_closed_vocabulary_is_refused_before_send(self):
        # Section 8 item 1: every other command is refused before dispatch.
        class Dispatching(FakeReader):
            def __init__(self, commands):
                super().__init__()
                self.commands = commands
                self.sent: list[str] = []

            async def read(self, *, sections, core_version, authorize, on_dispatch=lambda _c: None,
                           on_sent=lambda _c: None):
                self.calls += 1
                for command in self.commands:
                    authorize()
                    on_dispatch(command)
                    self.sent.append(command)
                    on_sent(command)
                return outcomes(sections=sections)

        for command in sorted(NEVER_SENT):
            reader = Dispatching([command])
            with self.subTest(command=command):
                await self.refused(self.service(reader).topology(tm.TopologyRequest()), "provider_error")
                self.assertEqual(reader.sent, [])
        for commands in ([tm.LIST_DATASETS_COMMAND, tm.LIST_DATASETS_COMMAND],
                         [tm.LIST_DATASETS_COMMAND, tm.NETWORK_TOPOLOGY_COMMAND, tm.NETWORK_TOPOLOGY_COMMAND]):
            reader = Dispatching(commands)
            with self.subTest(commands=commands):
                await self.refused(self.service(reader).topology(tm.TopologyRequest()), "provider_error")
                self.assertEqual(reader.sent, commands[:-1])

    async def test_reader_returning_other_sections_is_refused(self):
        reader = FakeReader(result=outcomes(sections=(tm.DATASETS,)))
        await self.refused(self.service(reader).topology(tm.TopologyRequest()), "provider_error")

    async def test_unconfigured_runtime_fails_closed(self):
        runtime = tm.ThreadMatterTopologyRuntime()
        with self.assertRaises(tm.TopologyError) as raised:
            runtime.require()
        self.assertEqual(raised.exception.reason, "authority_unavailable")
        runtime.configure(FakeReader(), self.core, SimpleNamespace(access_secret="a", ha_token="b"))
        self.assertIsInstance(runtime.require(), tm.ThreadMatterTopologyService)


class ServiceReadTests(ServiceTestCase):
    async def test_admitted_read_returns_attributed_result(self):
        reader = FakeReader()
        result = await self.service(reader).topology(tm.TopologyRequest())
        self.assertEqual(reader.sections, ("datasets", "topology"))
        self.assertEqual(result["commands_sent"], 2)
        self.assertEqual(result["completeness"], "partial")
        self.assertEqual((self.telemetry.completeness, self.telemetry.result_status), ("partial", "partial"))

    async def test_single_section_sends_one_command(self):
        reader = FakeReader()
        result = await self.service(reader).topology(tm.validate_arguments({"sections": ["datasets"]}))
        self.assertEqual(reader.sections, ("datasets",))
        self.assertEqual(result["commands_sent"], 1)
        self.assertEqual(result["sections"], ["datasets"])

    async def test_reader_failure_is_a_typed_refusal(self):
        for reason in ("timeout", "transport_unavailable", "identity_mismatch", "core_authority_refused"):
            with self.subTest(reason=reason):
                await self.refused(self.service(FakeReader(error=tm.TopologyError(reason))).topology(
                    tm.TopologyRequest()), reason)


class PublicToolTests(ServiceTestCase):
    async def test_invalid_arguments_are_never_reflected(self):
        marker = "Refused_" + "q" * 8
        for arguments in ({"refresh": True}, {"sections": [marker]}, {"sections": ["topology"], marker: 1},
                          {"device_id": marker}):
            with self.subTest(arguments=arguments):
                rendered = await public.registered_tool().run(arguments)
                self.assertNotIn(marker, rendered)
                self.assertIn("invalid_arguments", rendered)

    async def test_refresh_never_reaches_the_service(self):
        service = self.service(FakeReader())
        with mock.patch.object(tm.THREAD_MATTER_TOPOLOGY, "service", service), \
                mock.patch.object(service, "topology", side_effect=AssertionError("dispatched")) as called:
            rendered = json.loads(await public.registered_tool().run({"sections": ["topology"], "refresh": True}))
        self.assertFalse(rendered["success"])
        called.assert_not_called()

    async def test_admitted_tool_renders_attribution_and_warnings(self):
        with mock.patch.object(tm.THREAD_MATTER_TOPOLOGY, "service", self.service(FakeReader())):
            rendered = json.loads(await public.registered_tool().run({}))
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual(rendered["metadata"]["provider"], "engineering")
        self.assertEqual(rendered["metadata"]["data_providers"], ["direct_ha_api"])
        self.assertIs(rendered["metadata"]["fallback_occurred"], False)
        self.assertEqual(rendered["metadata"]["completeness"], "partial")
        self.assertIn(public._PARTIAL, rendered["warnings"])
        self.assertNotIn(public._UNAVAILABLE, rendered["warnings"])
        self.assertEqual(rendered["data"]["topology"]["nodes"][0]["id"], "g1")

    async def test_unavailable_section_renders_a_warning(self):
        reader = FakeReader(result=outcomes(datasets_result(1), topology_reason="matter_not_loaded"))
        with mock.patch.object(tm.THREAD_MATTER_TOPOLOGY, "service", self.service(reader)):
            rendered = json.loads(await public.registered_tool().run({}))
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual(rendered["data"]["topology"]["status"], "matter_not_loaded")
        self.assertIn(public._UNAVAILABLE, rendered["warnings"])

    async def test_unadmitted_tool_renders_closed_refusal(self):
        self.core.available = False
        with mock.patch.object(tm.THREAD_MATTER_TOPOLOGY, "service", self.service(FakeReader())):
            rendered = json.loads(await public.registered_tool().run({}))
        self.assertFalse(rendered["success"])
        self.assertEqual(rendered["details"]["reason"], "authority_unavailable")
        self.assertIs(rendered["metadata"]["fallback_occurred"], False)

    async def test_unconfigured_runtime_renders_closed_refusal(self):
        with mock.patch.object(tm.THREAD_MATTER_TOPOLOGY, "service", None):
            rendered = json.loads(await public.registered_tool().run({}))
        self.assertFalse(rendered["success"])
        self.assertEqual(rendered["details"]["reason"], "authority_unavailable")


# ---------------------------------------------------------------------------
# Loopback WebSocket peer
# ---------------------------------------------------------------------------

class LoopbackPeer:
    """Disposable loopback Home Assistant WebSocket peer with a command ledger."""

    def __init__(self):
        self.ledger: list[dict] = []
        self.raw: list[str] = []
        self.connections = 0
        self.auth_type = "auth_ok"
        self.auth_version = CORE_VERSION
        self.replies: dict[str, str] = {}
        self.hold: str | None = None
        self.drop: str | None = None
        self.extra_frame: str | None = None

    async def handler(self, request):
        self.connections += 1
        ws = web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)
        try:
            await ws.send_str(json.dumps({"type": "auth_required", "ha_version": self.auth_version}))
            await ws.receive_json()
            await ws.send_json({"type": self.auth_type, "ha_version": self.auth_version})
            while True:
                message = await ws.receive()
                if message.type != web.WSMsgType.TEXT:
                    break
                self.raw.append(message.data)
                command = json.loads(message.data)
                self.ledger.append(command)
                if command.get("type") == self.drop:
                    break
                if command.get("type") == self.hold:
                    await asyncio.sleep(5)
                if self.extra_frame is not None:
                    await ws.send_str(self.extra_frame)
                reply = self.replies.get(command.get("type"), error_frame(0, "unknown_command"))
                await ws.send_str(reply.replace('"id": 0', f'"id": {command.get("id")}', 1))
        except (ConnectionError, TypeError, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            await ws.close()
        return ws


class LoopbackTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peer = LoopbackPeer()
        app = web.Application()
        app.router.add_get("/{path:.*}", self.peer.handler)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.settings = SimpleNamespace(
            websocket_url=f"ws://127.0.0.1:{port}/api/websocket", ha_token=HA_TOKEN,
            access_secret=ACCESS_SECRET, ha_timeout_seconds=2.0,
        )
        self.reader = tm.TopologyReader(self.settings)
        self.telemetry, self.token = begin_request()
        self.telemetry.caller_id = "synthetic_authenticated_caller"
        self.telemetry.core_dispatch_authorizer = lambda: True

    async def asyncTearDown(self):
        end_request(self.token)
        await self.runner.cleanup()
        # Negative reachability on every path: only the two constant payloads.
        sent = {command.get("type") for command in self.peer.ledger}
        self.assertTrue(sent <= tm.COMMANDS, sent)
        self.assertFalse(sent & NEVER_SENT)
        self.assertLessEqual(len(self.peer.ledger), tm.MAX_COMMANDS)
        self.assertLessEqual(self.peer.connections, 1)
        for command in self.peer.ledger:
            payload = {key: value for key, value in command.items() if key != "id"}
            self.assertIn(payload, [dict(tm.DATASETS_PAYLOAD), dict(tm.TOPOLOGY_PAYLOAD)])
            if command["type"] == tm.NETWORK_TOPOLOGY_COMMAND:
                self.assertIs(command["refresh"], False)
        for raw in self.peer.raw:
            self.assertNotIn('"refresh": true', raw)

    def reply(self, command: str, frame: str) -> None:
        self.peer.replies[command] = frame

    async def read(self, sections=tm.SECTIONS, authorize=lambda: None):
        return await self.reader.read(sections=sections, core_version=CORE_VERSION, authorize=authorize)

    async def failed(self, reason: str, sections=tm.SECTIONS, authorize=lambda: None):
        with self.assertRaises(tm.TopologyError) as raised:
            await self.read(sections, authorize)
        self.assertEqual(raised.exception.reason, reason)
        self.assertNotIn(HA_TOKEN, json.dumps(raised.exception.details))
        return raised.exception


class ReaderLoopbackTests(LoopbackTestCase):
    async def test_both_sections_are_two_constant_commands_on_one_connection(self):
        self.reply(tm.LIST_DATASETS_COMMAND, result_frame(0, datasets_result()))
        self.reply(tm.NETWORK_TOPOLOGY_COMMAND, result_frame(0, topology_result()))
        result = await self.read()
        self.assertEqual(self.peer.ledger, [{"id": 1, "type": "thread/list_datasets"},
                                            {"id": 2, "type": "matter/network_topology", "refresh": False}])
        self.assertIn('"refresh": false', self.peer.raw[1])
        self.assertEqual(self.peer.connections, 1)
        self.assertEqual({s: (o.dispatched, o.reason) for s, o in result.items()},
                         {"datasets": (True, None), "topology": (True, None)})
        self.assertEqual(self.telemetry.ha_active_requests, 0)
        self.assertIn("thread_matter_topology", self.telemetry.endpoint_categories)

    async def test_single_section_sends_only_its_command(self):
        self.reply(tm.NETWORK_TOPOLOGY_COMMAND, result_frame(0, topology_result()))
        result = await self.read((tm.TOPOLOGY,))
        self.assertEqual(self.peer.ledger, [{"id": 1, "type": "matter/network_topology", "refresh": False}])
        self.assertEqual(set(result), {"topology"})

    async def test_matter_not_set_up_is_matter_not_loaded_and_datasets_survive(self):
        # Core registers matter commands only in async_setup_entry.
        self.reply(tm.LIST_DATASETS_COMMAND, result_frame(0, datasets_result()))
        result = await self.read()
        self.assertEqual(result["topology"].reason, "matter_not_loaded")
        self.assertIsNone(result["datasets"].reason)

    async def test_schema_below_13_is_typed(self):
        self.reply(tm.LIST_DATASETS_COMMAND, error_frame(0, "unknown_command"))
        self.reply(tm.NETWORK_TOPOLOGY_COMMAND, error_frame(0, "not_supported", f"schema {XA_OTBR}"))
        result = await self.read()
        self.assertEqual((result["datasets"].reason, result["topology"].reason),
                         ("thread_not_loaded", "matter_server_schema_below_13"))

    async def test_failure_after_the_first_reply_marks_the_rest(self):
        self.reply(tm.LIST_DATASETS_COMMAND, result_frame(0, datasets_result()))
        self.peer.drop = tm.NETWORK_TOPOLOGY_COMMAND
        result = await self.read()
        self.assertIsNone(result["datasets"].reason)
        self.assertEqual(result["topology"].reason, "transport_unavailable")
        self.assertTrue(result["topology"].dispatched)

    async def test_failure_before_any_reply_raises(self):
        self.peer.drop = tm.LIST_DATASETS_COMMAND
        await self.failed("transport_unavailable")

    async def test_authority_loss_always_fails_the_whole_read(self):
        self.reply(tm.LIST_DATASETS_COMMAND, result_frame(0, datasets_result()))
        self.reply(tm.NETWORK_TOPOLOGY_COMMAND, result_frame(0, topology_result()))
        calls = []

        def authorize():
            calls.append(1)
            if len(calls) >= 3:
                raise tm.TopologyError("authority_unavailable")

        await self.failed("authority_unavailable", authorize=authorize)
        self.assertEqual([c["type"] for c in self.peer.ledger], [tm.LIST_DATASETS_COMMAND])

    async def test_auth_refusal_and_identity_mismatch(self):
        self.peer.auth_type = "auth_invalid"
        await self.failed("core_authority_refused")
        self.assertEqual(self.peer.ledger, [])

    async def test_identity_mismatch_sends_nothing(self):
        self.peer.auth_version = "2026.11.1"
        await self.failed("identity_mismatch")
        self.assertEqual(self.peer.ledger, [])

    async def test_unexpected_extra_frame_is_malformed(self):
        self.peer.extra_frame = json.dumps({"id": 1, "type": "event", "event": {}})
        await self.failed("malformed_upstream_response")

    async def test_duplicate_keys_and_constants_are_malformed(self):
        frame = result_frame(0, datasets_result()).replace('"type": "result"', '"type": "result", "type": "result"')
        self.reply(tm.LIST_DATASETS_COMMAND, frame)
        await self.failed("malformed_upstream_response")

    async def test_oversized_frame_is_too_large(self):
        topology = topology_result()
        topology["pad"] = "x" * (tm.FRAME_BYTES + 1)
        self.reply(tm.NETWORK_TOPOLOGY_COMMAND, result_frame(0, topology))
        await self.failed("upstream_response_too_large", (tm.TOPOLOGY,))

    async def test_timeout_is_typed(self):
        self.settings.ha_timeout_seconds = 0.3
        self.peer.hold = tm.LIST_DATASETS_COMMAND
        await self.failed("timeout")

    async def test_invalid_section_tuple_sends_nothing(self):
        for sections in ((), ("datasets", "datasets"), ("border_routers",), ["datasets"]):
            with self.subTest(sections=sections), self.assertRaises(tm.TopologyError):
                await self.reader.read(sections=sections, core_version=CORE_VERSION, authorize=lambda: None)
        self.assertEqual(self.peer.connections, 0)


class EndToEndLoopbackTests(LoopbackTestCase):
    async def test_canaries_absent_from_output_errors_logs_and_telemetry(self):
        # Section 8 items 3 and 4 across every channel in one run.
        material = secret_material()
        data = datasets_result()
        data["datasets"][1]["unexpected_ip"] = IP4_CANARY
        topology = topology_result()
        topology["nodes"][0]["unexpected_blob"] = material["tlv"]  # unknown, non-secret key name: dropped
        self.reply(tm.LIST_DATASETS_COMMAND, result_frame(0, data))
        self.reply(tm.NETWORK_TOPOLOGY_COMMAND, error_frame(0, "unknown_error", f"{XA_OTBR} {BSSID}"))
        records: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = Capture(logging.DEBUG)
        root = logging.getLogger()
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, previous)
        service = tm.ThreadMatterTopologyService(self.reader, AdmissionCore(),
                                                 known_secrets=(ACCESS_SECRET, HA_TOKEN))
        with mock.patch.object(tm.THREAD_MATTER_TOPOLOGY, "service", service):
            rendered = await public.registered_tool().run({})
            # Second run: topology succeeds, datasets carry a secret key.
            leaked = deepcopy(data)
            leaked["datasets"][0]["networkKey"] = material["network_key"]
            self.reply(tm.LIST_DATASETS_COMMAND, result_frame(0, leaked))
            self.reply(tm.NETWORK_TOPOLOGY_COMMAND, result_frame(0, topology))
            self.peer.ledger.clear()
            self.peer.raw.clear()
            self.peer.connections = 0
            second = await public.registered_tool().run({})
        first_value, second_value = json.loads(rendered), json.loads(second)
        self.assertTrue(first_value["success"], first_value)
        self.assertEqual(first_value["data"]["topology"]["status"], "upstream_command_failed")
        self.assertEqual(second_value["data"]["datasets"]["status"], "malformed_upstream_response")
        self.assertEqual(second_value["data"]["topology"]["status"], "ok")
        haystack = "\n".join([rendered, second, *records, repr(vars(self.telemetry))])
        for canary in (*IDENTIFIER_CANARIES, *material.values()):
            for variant in variants(canary):
                with self.subTest(variant=variant):
                    self.assertNotIn(variant, haystack)
        self.assertNotIn(HA_TOKEN, haystack)

    async def test_signed_admission_failure_lane_shape(self):
        # The A2 lane fail-closed case: Matter not set up on the exact Core.
        self.reply(tm.LIST_DATASETS_COMMAND, result_frame(0, {"datasets": []}))
        service = tm.ThreadMatterTopologyService(self.reader, AdmissionCore())
        result = await service.topology(tm.validate_arguments({"sections": ["topology"]}))
        self.assertEqual(result["topology"]["status"], "matter_not_loaded")
        self.assertEqual(result["commands_sent"], 1)
        self.assertEqual(self.peer.ledger, [{"id": 1, "type": "matter/network_topology", "refresh": False}])


# ---------------------------------------------------------------------------
# Signed admission (CoreRuntime level)
# ---------------------------------------------------------------------------

class UnadmittedCompiledCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tests.test_ha_core_2026_9_integration as core9
        from tests.test_ha_core_capability_auto_readmission import _evidence, _snapshot

        self.runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            _snapshot("2026.8.1", evidence=_evidence())
        )

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_compiled_core_never_admits_the_profile(self):
        self.assertIsNone(self.runtime.acquire((PROFILE,)))
        status = self.runtime.route_status((PROFILE,))
        self.assertIsNot(status.get("available"), True)

    async def test_gateway_refuses_with_pending_admission_before_the_app(self):
        import tests.test_hamcp069a_entity_metadata_readmission as readmission

        self.assertIsNone(self.runtime.acquire(static_tool_requirements(TOOL, {})))
        gateway, app = readmission.gateway_for(self.runtime)
        for arguments in ({}, {"sections": ["datasets"]}, {"sections": ["topology"]},
                          {"sections": ["topology"], "refresh": True}):
            with self.subTest(arguments=arguments):
                body = await readmission.call(gateway, TOOL, arguments)
                self.assertIn(b"CoreCapabilityUnavailable", body)
                self.assertIn(PENDING.encode(), body)
                self.assertIn(
                    f'"resource_type": "{THREAD_MATTER_TOPOLOGY_RESOURCE_TYPE}"'.encode(),
                    body.replace(b'\\"', b'"'),
                )
                self.assertIn(b'"provider_dispatch_occurred": false', body.replace(b'\\"', b'"'))
        # Zero frames: the app (and so Home Assistant) is never reached.
        self.assertEqual(app.calls, [])


class SignedTestRegistryAdmissionTests(unittest.IsolatedAsyncioTestCase):
    """Test-only ephemeral CoreSigner key; nothing here is production-signed."""

    async def asyncSetUp(self):
        import tests.test_core_2026_10_device_registry as october

        await october.OctoberSignedSelectionTests.asyncSetUp(self)
        self._publish = october.OctoberSignedSelectionTests.publish

    async def publish(self, entry=None):
        return await self._publish(self, entry)

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_shipped_reference_set_withholds(self):
        self.assertNotIn(PROFILE, {c["capability_id"] for c in self.entry["capabilities"]})
        await self.publish()
        self.assertIsNone(self.core.acquire((PROFILE,)))
        telemetry, token = begin_request()
        self.addCleanup(end_request, token)
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: True
        reader = FakeReader()
        service = tm.ThreadMatterTopologyService(reader, self.core)
        with self.assertRaises(tm.TopologyError) as raised:
            await service.topology(tm.TopologyRequest())
        self.assertEqual(raised.exception.reason, "authority_unavailable")
        self.assertEqual(reader.calls, 0)

    async def test_signed_reference_admits_and_the_read_succeeds(self):
        entry = deepcopy(self.entry)
        entry["capabilities"] = [*self.entry["capabilities"], *references()]
        await self.publish(entry)
        self.assertIsNotNone(self.core.acquire((PROFILE,)))

        peer = LoopbackPeer()
        peer.auth_version = self.core.current_observation.version
        peer.replies[tm.LIST_DATASETS_COMMAND] = result_frame(0, datasets_result())
        peer.replies[tm.NETWORK_TOPOLOGY_COMMAND] = result_frame(0, topology_result())
        web_app = web.Application()
        web_app.router.add_get("/{path:.*}", peer.handler)
        runner = web.AppRunner(web_app, access_log=None)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        settings = SimpleNamespace(websocket_url=f"ws://127.0.0.1:{port}/api/websocket", ha_token=HA_TOKEN,
                                   access_secret=ACCESS_SECRET, ha_timeout_seconds=2.0)
        service = tm.ThreadMatterTopologyService(tm.TopologyReader(settings), self.core,
                                                 known_secrets=(ACCESS_SECRET, HA_TOKEN))
        telemetry, token = begin_request()
        self.addCleanup(end_request, token)
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: True
        result = await service.topology(tm.validate_arguments({}))
        self.assertEqual((result["datasets"]["status"], result["topology"]["status"]), ("ok", "ok"))
        self.assertEqual(len(result["topology"]["nodes"]), 7)
        self.assertEqual(result["commands_sent"], 2)
        self.assertEqual([c["type"] for c in peer.ledger], [tm.LIST_DATASETS_COMMAND, tm.NETWORK_TOPOLOGY_COMMAND])


# ---------------------------------------------------------------------------
# Delegated surfaces this build must not change (section 8 items 11 and 12)
# ---------------------------------------------------------------------------

class GatewayWiringTests(unittest.TestCase):
    """The static route is registered once, read-only, and policy bound."""

    def test_tool_is_registered_read_only_and_policy_bound_under_its_own_name(self):
        self.assertEqual(list(ENGINEERING_STATIC_TOOL_NAMES).count(TOOL), 1)
        self.assertEqual(STATIC_TOOL_CORE_REQUIREMENTS[TOOL], (PROFILE,))
        self.assertEqual(static_tool_requirements(TOOL, {}), (PROFILE,))
        self.assertEqual(
            PENDING_ADMISSION_TOOLS[TOOL], (THREAD_MATTER_TOPOLOGY_RESOURCE_TYPE, PROFILE)
        )
        self.assertEqual(TOOL_CAPABILITY_POLICY[TOOL], ProviderCapability.THREAD_MATTER_TOPOLOGY)
        self.assertEqual(routing_for_tool(TOOL).route, CapabilityRoute.ENGINEERING_NATIVE)
        policy = ANALYTICAL_PROVIDER_POLICIES[TOOL]
        self.assertEqual(policy["policy_id"], "thread_matter_topology_read_v1")
        self.assertEqual(
            (policy["access"], policy["writes_allowed"], policy["fallback_policy"]),
            ("read", "none", "none"),
        )
        self.assertIn("refresh=false", policy["scope"])
        # Engineering-owned name: no ha_ prefix and no delegated route.
        self.assertFalse(TOOL.startswith("ha_"))
        self.assertNotIn(TOOL, DELEGATED_CORE_REQUIREMENTS)
        registered = public.registered_tool()
        self.assertIs(registered.parameters.get("additionalProperties"), False)
        self.assertNotIn("refresh", json.dumps(registered.parameters))

    def test_profile_is_an_entry_profile_no_f3_entry_requires(self):
        from ha_mcp_engineering.ha_core_readmission import routes

        self.assertIn(PROFILE, F3_ENTRY_ADMISSION_PROFILES)
        required = {
            profile
            for tool, profiles in STATIC_TOOL_CORE_REQUIREMENTS.items()
            if tool != TOOL
            for profile in profiles
        }
        self.assertNotIn(PROFILE, required)
        for name in dir(routes):
            table = getattr(routes, name)
            if name.startswith("F3_") and name != "F3_ENTRY_ADMISSION_PROFILES" and isinstance(table, dict):
                self.assertNotIn(PROFILE, json.dumps(table, default=str), name)

    def test_catalog_states_the_requirement_and_never_claims_admission(self):
        entry = capability_for_tool(TOOL)
        self.assertEqual(entry["routing"], "engineering_native")
        self.assertEqual(entry["risk"], "read")
        self.assertEqual(entry["core_capabilities"], [PROFILE])
        self.assertEqual(entry["core_admission_requirement"], "signed_core_registry_admission")
        self.assertEqual(entry["core_admission"], "not_evaluated")
        self.assertEqual(entry["fallback"], "none")
        catalog = build_capability_catalog()
        rows = [item for item in catalog["beta_native"] if item["tool"] == TOOL]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation_class"], "read")
        # A governance snapshot that does not name this read never projects
        # an admission onto it; the gateway refusal is the pending signal.
        current = build_capability_catalog(core_admission={
            "status": "current", "generation": 3, "providers": {},
            "tools": {"create_entity_metadata_plan": "admitted"},
        })
        [row] = [item for item in current["beta_native"] if item["tool"] == TOOL]
        self.assertEqual(row["core_admission"], "not_evaluated")


def _native_server(name: str):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name)
    server.add_tool(public.registered_tool().fn, name=TOOL)
    return server


class GatewayImpostorTests(unittest.IsolatedAsyncioTestCase):
    async def test_upstream_tool_advertising_the_native_name_is_never_exposed_in_its_place(self):
        from test_readonly_upstream_gateway import catalog_tool, initialize, policy_entry, registered_tools

        for reviewed in (False, True):
            with self.subTest(reviewed=reviewed):
                server = _native_server("hamcp231-impostor")
                original = registered_tools(server).get(TOOL)
                entries = [policy_entry("ha_get_state")]
                if reviewed:
                    entries.append(policy_entry(TOOL))
                gateway, server, transport = await initialize(
                    entries, [catalog_tool("ha_get_state"), catalog_tool(TOOL)], server=server)
                self.assertIs(registered_tools(server).get(TOOL), original)
                self.assertIsNotNone(registered_tools(server).get("ha_get_state"))
                self.assertEqual(transport.calls, [])


class DelegatedSurfaceRegressionTests(unittest.TestCase):
    def test_ha_get_device_node_diagnostics_passthrough_is_pinned(self):
        # Section 8 item 11 (the section 3.1 gap): ha-mcp 9.1.0's
        # node_diagnostics sub-object is projected by the HAMCP-319 allowlist;
        # Thread mesh-local addresses are withheld, nothing else is added.
        fixture = json.loads((ROOT / "tests/fixtures/ha_mcp_9_1_0/device/device-results.json").read_text("utf-8"))
        payload = deepcopy(fixture["single_matter_thread"])
        payload["device"]["node_diagnostics"]["network_key"] = secret_material()["network_key"]
        projected = device_projection.project_device_result(payload, upstream_version="9.1.0")
        diagnostics = projected["device"]["node_diagnostics"]
        self.assertEqual(diagnostics, {
            "network_type": "thread", "node_type": "end_device", "available": True,
            "network_name": "SyntheticNet", "mac_address": None, "active_fabric_index": 1,
            "unavailable_fields": {"ip_addresses": "thread_mesh_local_withheld"},
            "active_fabrics": [
                {"fabric_index": 1, "fabric_id": 1, "vendor_id": 4939, "vendor_name": "Synthetic Vendor",
                 "fabric_label": "Home"},
                {"fabric_index": 2, "fabric_id": 2, "vendor_id": 24582, "vendor_name": "Synthetic Two",
                 "fabric_label": ""},
            ],
        })
        self.assertNotIn(secret_material()["network_key"], json.dumps(projected))

    def test_ha_get_system_health_thread_network_section_is_unchanged(self):
        # Section 8 item 12 (item 20): same fixture, same output; HAMCP-231
        # adds no reason codes to the delegated section.
        raw = {
            "border_routers": [{"extended_address": "aabbccddeeff0011", "channel": 15,
                                "extended_pan_id": "1122334455667788", "border_agent_id": "agent-1",
                                "unexpected": "dropped"}],
            "datasets": "dropped",
        }
        projection = platform_reads._Projection()
        out = platform_reads._project_section(projection, "thread_network", raw)
        self.assertEqual(out, {
            "count": 1, "total_count": 1, "truncated": False,
            "border_routers": [{"extended_address": "aabbccddeeff0011", "channel": 15,
                                "extended_pan_id": "1122334455667788", "border_agent_id": "agent-1"}],
        })
        self.assertNotIn("thread_matter_topology", Path(platform_reads.__file__).read_text("utf-8"))
        self.assertNotIn("thread_matter_topology", Path(device_projection.__file__).read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
