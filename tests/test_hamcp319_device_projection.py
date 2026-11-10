"""HAMCP-319 section 6.5 (DCL2): radio credential projection for ``ha_get_device``.

Synthetic data only: no production record, no captured device payload, no live
Home Assistant and no deployed endpoint. Every gateway test runs against an
exact 8.5.0 peer and an exact 9.1.0 peer (the reviewed tools/list captures)
through the real ``UpstreamReadGateway`` with in-process mock transports.

Also covers the section 5 Core child-device binding for 9.1.0 and the T2
``ha_get_system_health`` radio-section canaries.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
import logging
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402

from ha_mcp_engineering import routing  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.probe_profiles import (  # noqa: E402
    CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    CHILD_DEVICE_PROBE_PROFILE,
    DEVICE_DEPENDENT_DELEGATED_TOOLS,
    delegated_provider_compatibility,
)
from ha_mcp_engineering.providers import upstream_device_projection as projection  # noqa: E402
from ha_mcp_engineering.providers import upstream_platform_reads as platform  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)
from test_ha_mcp_production_readmission import _GatewayTransport, _settings  # noqa: E402
from test_upstream_8_5_compatibility import capture  # noqa: E402

FIXTURE_DIR = ROOT / "tests/fixtures/ha_mcp_9_1_0/device"
FIXTURE = json.loads((FIXTURE_DIR / "device-results.json").read_text(encoding="utf-8"))
VERSIONS = ("8.5.0", "9.1.0")
DEVICE = "ha_get_device"
RADIO = "ha_manage_radio"
CANARY = "CANARY"
# Legacy URL for 8.5.0; the strict full-catalog URL for 9.1.0. Host and path
# are synthetic redaction secrets distinct from every code under test.
URLS = {
    "8.5.0": "http://synthetic-provider:9583/synthetic-path-credential/mcp",
    "9.1.0": "http://synthetic-provider:9583/synthetic-path-credential/catalog/full",
}
CODES = projection.CLOSED_WARNING_CODES

# Credential-class keys (section 6.5 table) plus unforeseen names. Each is
# placed with a distinct canary value.
CREDENTIAL_KEYS = (
    # Thread
    "network_key", "pskc", "pskd", "active_dataset_tlvs", "pending_dataset_tlvs",
    "dataset", "joiner_credential", "commissioner_credential",
    # Matter
    "setup_code", "qr_code", "manual_pairing_code", "noc", "icac", "rcac", "ipk",
    "epoch_keys", "private_key", "attestation_certificate",
    # Z-Wave
    "s0_legacy_key", "s2_access_control_key", "s2_authenticated_key",
    "s2_unauthenticated_key",
    # Zigbee
    "link_key", "trust_center_link_key",
    # Unforeseen names carrying the same material
    "blob", "x_radio_material", "opaque",
)


def canary_fields(tag: str) -> dict:
    """Credential-class keys with distinct canaries; ``dataset`` is structured."""

    out = {name: f"{CANARY}-{tag}-{name}" for name in CREDENTIAL_KEYS}
    out["dataset"] = {
        "network_key": f"{CANARY}-{tag}-dataset-network-key",
        "pskc": f"{CANARY}-{tag}-dataset-pskc",
        "tlvs": f"{CANARY}-{tag}-dataset-tlvs",
        "mesh_local_prefix": f"{CANARY}-{tag}-dataset-mlp",
    }
    return out


def ok(payload) -> dict:
    return {"isError": False, "structuredContent": deepcopy(payload),
            "content": [{"type": "text", "text": json.dumps(payload)}]}


def structured_only(payload, clean) -> dict:
    """``payload`` reaches only structuredContent; the text carries ``clean``."""

    return {"isError": False, "structuredContent": deepcopy(payload),
            "content": [{"type": "text", "text": json.dumps(clean)}]}


def text_only(payload) -> dict:
    return {"isError": False, "content": [{"type": "text", "text": json.dumps(payload)}]}


def text_disagrees(payload, clean) -> dict:
    """``payload`` reaches only the text; structuredContent is ``clean``."""

    return {"isError": False, "structuredContent": deepcopy(clean),
            "content": [{"type": "text", "text": json.dumps(payload)}]}


def placements(payload, clean):
    """(name, call_result, payload-is-authoritative) for every placement."""

    yield "both", ok(payload), True
    yield "structured_only", structured_only(payload, clean), True
    yield "text_only", text_only(payload), True
    yield "text_disagrees", text_disagrees(payload, clean), False


class Recorder:
    """Mock Core REST / WebSocket / radio client. Records any request."""

    def __init__(self) -> None:
        self.calls: list = []

    async def request(self, *args, **kwargs):
        self.calls.append(("request", args, kwargs))
        raise AssertionError("unexpected Core REST request")

    async def command(self, *args, **kwargs):
        self.calls.append(("command", args, kwargs))
        raise AssertionError("unexpected Core WebSocket command")


class Transport(_GatewayTransport):
    def __init__(self, tools, version, call_result=None):
        super().__init__(tools, version=version)
        self.sent: list = []
        self.result = call_result or ok(FIXTURE["single_zha"])

    async def execute_read(self, tool, arguments, **kwargs):
        exchange = await super().execute_read(tool, arguments, **kwargs)
        self.sent.append((tool, deepcopy(arguments)))
        return replace(exchange, call_result=deepcopy(self.result))


class Harness:
    def __init__(self, test, version, *, core_runtime=None, tools=None):
        self.test = test
        self.version = version
        self.transport = Transport(capture(version)["tools"] if tools is None else tools, version)
        self.rest = Recorder()
        self.websocket = Recorder()
        self.gateway = UpstreamReadGateway()
        kwargs = {"core_runtime": core_runtime} if core_runtime is not None else {}
        self.gateway.configure(
            replace(_settings("synthetic-hamcp319-device"), ha_mcp_release_registry_enabled=False,
                    upstream_dashboard_mcp_url=URLS[version]),
            transport=self.transport, ha_rest_client=self.rest,
            ha_websocket_client=self.websocket, **kwargs,
        )
        self.mcp = FastMCP(f"synthetic-hamcp319-device-{version}")
        self.sinks: list[str] = []

    async def start(self):
        await self.gateway.initialize(self.mcp)
        return self

    def snapshot(self):
        return self.gateway._registered_tool_registry.snapshot()

    async def call(self, arguments, call_result=None, name=DEVICE):
        if call_result is not None:
            self.transport.result = call_result
        telemetry, token = begin_request("synthetic-hamcp319-device")
        with capture_logs() as records:
            try:
                raw = await self.snapshot()[name].run(arguments)
            finally:
                end_request(token)
        self.sinks.append(raw)
        self.sinks.append(repr(telemetry))
        self.sinks.append(json.dumps(telemetry.audit_context, default=repr))
        self.sinks.append(json.dumps(routing._delegated_audit_attribution(telemetry), default=repr))
        self.sinks.extend(render_record(r) for r in records)
        return json.loads(raw), telemetry

    def scan(self, markers):
        """Assert no marker in any sink: result, text, error, warning, audit,
        DEBUG logs and the server-health block."""

        blob = "\n".join([
            *self.sinks,
            json.dumps(self.gateway.readmission_audit_snapshot(), default=repr),
            json.dumps(self.gateway.health_snapshot(), default=repr),
        ])
        for marker in markers:
            self.test.assertNotIn(marker, blob)

    def assert_no_side_calls(self, expected_calls):
        self.test.assertEqual(self.transport.calls, expected_calls)
        self.test.assertEqual([tool for tool, _ in self.transport.sent], [DEVICE] * expected_calls)
        self.test.assertEqual(self.rest.calls, [])
        self.test.assertEqual(self.websocket.calls, [])
        assert_no_radio_dispatch(self.test, self.transport.sent)


def assert_no_radio_dispatch(test, sent):
    for tool, arguments in sent:
        test.assertNotEqual(tool, RADIO)
        text = json.dumps(arguments)
        for prefix in ("otbr/", "thread/", "network_status", "zwave_js/", "matter/", "zha/"):
            test.assertNotIn(prefix, text)


def render_record(record) -> str:
    try:
        message = record.getMessage()
    except Exception:  # pragma: no cover - defensive formatting only
        message = repr(record.msg)
    return f"{record.name} {record.levelname} {message} {record.args!r} {record.exc_text!r} {record.__dict__!r}"


@contextmanager
def capture_logs():
    """Capture every log record from every logger at DEBUG."""

    records: list = []
    original = logging.Logger.callHandlers

    def hook(self, record):
        records.append(record)
        return original(self, record)

    loggers = [logging.getLogger()] + [
        item for item in logging.Logger.manager.loggerDict.values() if isinstance(item, logging.Logger)
    ]
    levels = {item: item.level for item in loggers}
    disabled = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    for item in loggers:
        item.setLevel(logging.DEBUG)
    try:
        with patch.object(logging.Logger, "callHandlers", hook):
            yield records
    finally:
        for item, level in levels.items():
            item.setLevel(level)
        logging.disable(disabled)


def markers_in(value) -> set[str]:
    found: set[str] = set()

    def walk(item):
        if isinstance(item, str):
            if CANARY in item:
                found.add(item)
        elif isinstance(item, dict):
            for key, child in item.items():
                walk(key)
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def project(payload, version="9.1.0"):
    return projection.project_device_result(deepcopy(payload), upstream_version=version)


def assert_closed_warnings(test, value):
    warnings = value.get("warnings", [])
    test.assertEqual(warnings, sorted(warnings))
    test.assertTrue(set(warnings) <= CODES, warnings)


# ---------------------------------------------------------------------------
# Canary-laden payloads
# ---------------------------------------------------------------------------

def canary_single(tag: str) -> dict:
    """A Matter + ZHA + Z-Wave enriched single device with canaries at every level."""

    base = deepcopy(FIXTURE["single_matter_wifi"])
    device = base["device"]
    device.update(canary_fields(f"{tag}-device"))
    device["radio_metrics"] = {"lqi": 200, "rssi": -60, **canary_fields(f"{tag}-radio")}
    device["node_status"] = {
        **FIXTURE["single_zwave"]["device"]["node_status"], **canary_fields(f"{tag}-node-status"),
    }
    diagnostics = device["node_diagnostics"]
    diagnostics.update(canary_fields(f"{tag}-diag"))
    for index, fabric in enumerate(diagnostics["active_fabrics"]):
        fabric.update(canary_fields(f"{tag}-fabric{index}"))
    device["connections"] = device["connections"] + [
        ["network_key", f"{CANARY}-{tag}-conn-network-key"],
        ["pskc", f"{CANARY}-{tag}-conn-pskc"],
    ]
    device["identifiers"] = device["identifiers"] + [
        ["active_dataset_tlvs", f"{CANARY}-{tag}-ident-tlvs"],
        ["dataset", f"{CANARY}-{tag}-ident-dataset"],
    ]
    device["entities"][0].update(canary_fields(f"{tag}-entity"))
    base["entities"] = deepcopy(device["entities"])
    base.update(canary_fields(f"{tag}-top"))
    base["warnings"] = [
        f"ZHA radio metrics unavailable: {CANARY}-{tag}-zha-exception",
        f"Matter node diagnostics unavailable: {CANARY}-{tag}-matter-exception",
        f"{CANARY}-{tag}-raw-warning",
    ]
    return base


def canary_list(tag: str) -> dict:
    base = deepcopy(FIXTURE["list_z2m"])
    base.update(canary_fields(f"{tag}-top"))
    for index, row in enumerate(base["devices"]):
        row.update(canary_fields(f"{tag}-row{index}"))
        row["connections"] = [["network_key", f"{CANARY}-{tag}-row{index}-conn"]]
        row["identifiers"] = [["mqtt", f"zigbee2mqtt_0x00124b00000000{index:02x}"],
                              ["pskc", f"{CANARY}-{tag}-row{index}-ident"]]
    base["bridge"].update(canary_fields(f"{tag}-bridge"))
    return base


# ---------------------------------------------------------------------------
# Registry pairs
# ---------------------------------------------------------------------------

CONNECTION_POSITIVES = [
    ["mac", "aa:bb:cc:dd:ee:21"],
    ["bluetooth", "AA:BB:CC:DD:EE:22"],
    ["zigbee", "00:11:22:33:44:55:66:23"],
    ["upnp", "uuid:12345678-90ab-cdef-1234-567890abcd24"],
    ["ieee", "0x00124b0000000025"],
]
IDENTIFIER_POSITIVES = [
    ["zha", "00:11:22:33:44:55:66:31"],
    ["zwave_js", "3967641190-32"],
    ["zwave_js", "3967641190-32-1"],
    ["zwave_js", "3967641190-32-271:4098:52"],
    ["matter", "deviceid_0123456789ABCDEF-00000000000000C3-MatterNodeDevice"],
    ["matter", "deviceid_0123456789ABCDEF-00000000000000C3-4"],
    ["mqtt", "zigbee2mqtt_0x00124b0000000035"],
    ["mqtt", "zigbee2mqtt_bridge_0x00124b0000000036"],
    ["hassio", "core"],
    ["hassio", "supervisor"],
    ["hassio", "host"],
    ["hassio", "OS"],
]
NEGATIVE_DISCRIMINATORS = (
    "network_key", "pskc", "active_dataset_tlvs", "dataset",
    "zha_network_key", "mac_key", "api_key", "thread_credential", "password",
    "MAC", "Zha", "mac_", "zha_group",
)


def negative_pairs(tag: str):
    """(pair, markers) for every negative row in section 8.3."""

    c = f"{CANARY}-{tag}"
    rows = [
        # Thread credential discriminators
        (["network_key", f"{c}-network-key"], [f"{c}-network-key"]),
        (["pskc", f"{c}-pskc"], [f"{c}-pskc"]),
        (["active_dataset_tlvs", f"{c}-tlvs"], [f"{c}-tlvs"]),
        (["dataset", f"{c}-dataset"], [f"{c}-dataset"]),
        # Unknown credential-labelled discriminators
        (["zha_network_key", f"{c}-zha-network-key"], [f"{c}-zha-network-key"]),
        (["mac_key", f"{c}-mac-key"], [f"{c}-mac-key"]),
        (["api_key", f"{c}-api-key"], [f"{c}-api-key"]),
        (["thread_credential", f"{c}-thread-credential"], [f"{c}-thread-credential"]),
        (["password", f"{c}-password"], [f"{c}-password"]),
        # Discriminator-carried canary
        ([f"{c}-discriminator", "aa:bb:cc:dd:ee:40"], [f"{c}-discriminator", "aa:bb:cc:dd:ee:40"]),
        # Case and prefix variants with values that would pass the real row
        (["MAC", "de:ad:be:ef:00:01"], ["de:ad:be:ef:00:01"]),
        (["Zha", "de:ad:be:ef:00:00:00:02"], ["de:ad:be:ef:00:00:00:02"]),
        (["mac_", "de:ad:be:ef:00:03"], ["de:ad:be:ef:00:03"]),
        (["zha_group", "de:ad:be:ef:00:00:00:04"], ["de:ad:be:ef:00:00:00:04"]),
        (["Zha", f"{c}-zha-case"], [f"{c}-zha-case"]),
        # Allowlisted discriminator, out-of-format value
        (["mac", f"{c}-mac-value"], [f"{c}-mac-value"]),
        (["mac", "DE:AD:BE:EF:00:05"], ["DE:AD:BE:EF:00:05"]),
        (["zha", "de:ad:be:ef:00:00:06"], ["de:ad:be:ef:00:00:06"]),
        (["mqtt", f"home/{c}-mqtt-payload"], [f"{c}-mqtt-payload"]),
        (["matter", f"serial_{c}-matter"], [f"{c}-matter"]),
        (["zwave_js", "3967641190-77-g1"], ["3967641190-77-g1"]),
        (["hassio", f"a0d7b954_{c}-addon"], [f"{c}-addon"]),
        (["hassio", "mount_synthetic77"], ["mount_synthetic77"]),
        (["mac", (f"{c}-long-" + "x" * 65)[:65]], [f"{c}-long-"]),
        (["mac", ""], []),
        # Malformed pairs
        ([f"{c}-arity1"], [f"{c}-arity1"]),
        (["mac", "de:ad:be:ef:00:07", f"{c}-arity3"], ["de:ad:be:ef:00:07", f"{c}-arity3"]),
        ([7, f"{c}-number-first"], [f"{c}-number-first"]),
        (["mac", 8], []),
        ([None, f"{c}-null-first"], [f"{c}-null-first"]),
        (["mac", None], []),
        ([[f"{c}-nested-first"], "aa:bb:cc:dd:ee:41"], [f"{c}-nested-first", "aa:bb:cc:dd:ee:41"]),
        (["mac", ["de:ad:be:ef:00:08"]], ["de:ad:be:ef:00:08"]),
        ([{"k": f"{c}-object-first"}, "aa:bb:cc:dd:ee:42"], [f"{c}-object-first", "aa:bb:cc:dd:ee:42"]),
        (["mac", {"k": f"{c}-object-second"}], [f"{c}-object-second"]),
        (f"{c}-scalar-entry", [f"{c}-scalar-entry"]),
    ]
    return rows


def interleave(positives, negatives):
    """Positives in order, each negative after a positive, plus one duplicate."""

    out = []
    for index, positive in enumerate(positives):
        out.append(deepcopy(positive))
        for pair, _ in negatives[index::len(positives)]:
            out.append(deepcopy(pair))
    out.append(deepcopy(positives[0]))
    return out


def pairs_device(tag: str, *, base="single_child"):
    device = deepcopy(FIXTURE[base]["device"])
    conn_neg = negative_pairs(f"{tag}-conn")
    ident_neg = negative_pairs(f"{tag}-ident")
    device["connections"] = interleave(CONNECTION_POSITIVES, conn_neg)
    device["identifiers"] = interleave(IDENTIFIER_POSITIVES, ident_neg)
    markers = [m for _, ms in conn_neg + ident_neg for m in ms]
    return device, len(conn_neg), len(ident_neg), markers


# ---------------------------------------------------------------------------
# Unit tests of the compiled rule (no gateway)
# ---------------------------------------------------------------------------

class CompiledRuleTests(unittest.TestCase):
    def test_fixture_provenance_is_synthetic_class_s(self):
        provenance = json.loads((FIXTURE_DIR / "provenance.json").read_text(encoding="utf-8"))
        entry = provenance["fixtures"]["device-results.json"]
        self.assertEqual(entry["class"], "S")
        self.assertTrue(entry["synthetic"])
        self.assertFalse(entry["captured_device_payload"])
        import hashlib
        digest = hashlib.sha256((FIXTURE_DIR / "device-results.json").read_bytes()).hexdigest()
        self.assertEqual(entry["sha256"], digest)
        self.assertEqual(set(entry["entries"]), {f"/{k}" for k in FIXTURE if k != "synthetic"})
        self.assertTrue(FIXTURE["synthetic"])
        self.assertEqual(provenance["core_domain_set"]["sha256"], projection.CORE_DOMAIN_SET_SHA256)
        self.assertEqual(len(projection.CORE_INTEGRATION_DOMAINS), projection.CORE_DOMAIN_SET_COUNT)

    def test_unreviewed_versions_untouched_and_reviewed_fail_closed(self):
        payload = {"success": True, "device": {"network_key": "x"}}
        for version in ("8.4.3", "8.6.0"):
            self.assertIs(projection.project_device_result(payload, upstream_version=version), payload)
        for version in VERSIONS:
            for bad in ("text", None, [], {"success": False, "device": {}},
                        {"success": True}, {"success": True, "device": {}, "devices": []},
                        {"success": True, "device": "x"}, {"success": True, "devices": {}},
                        {"success": "true", "device": {}}):
                with self.subTest(version=version, bad=bad):
                    with self.assertRaisesRegex(ValueError, "^device_projection_invalid$"):
                        projection.project_device_result(deepcopy(bad), upstream_version=version)

    def test_clean_fixtures_identical_on_both_versions(self):
        for name, payload in FIXTURE.items():
            if name == "synthetic":
                continue
            with self.subTest(fixture=name):
                self.assertEqual(project(payload, "8.5.0"), project(payload, "9.1.0"))


# ---------------------------------------------------------------------------
# Gateway tests
# ---------------------------------------------------------------------------

class DeviceProjectionGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def _canaries(self, version):
        h = await Harness(self, version).start()
        markers: set[str] = set()
        calls = 0
        clean_single = FIXTURE["single_matter_wifi"]
        clean_list = FIXTURE["list_z2m"]
        cases = []
        for tag_base, builder, clean, args in (
            ("single", canary_single, clean_single, {"device_id": "synthetic-matter-wifi"}),
            ("list", canary_list, clean_list, {"integration": "zigbee2mqtt"}),
        ):
            for placement in ("both", "structured_only", "text_only", "text_disagrees"):
                tag = f"{version}-{tag_base}-{placement}"
                payload = builder(tag)
                markers |= markers_in(payload)
                result_map = dict((n, (r, a)) for n, r, a in placements(payload, clean))
                cases.append((tag, result_map[placement][0], args, True))
        # Malformed shapes and errors
        oversize = deepcopy(clean_single)
        oversize["device"]["network_key"] = f"{CANARY}-{version}-oversize-" + "z" * 8000
        oversize["device"]["node_diagnostics"]["pskc"] = [f"{CANARY}-{version}-mixed", 5, {"k": f"{CANARY}-{version}-mixed-obj"}]
        cases.append(("oversize", ok(oversize), {"device_id": "x"}, True))
        string_object = deepcopy(clean_single)
        string_object["device"]["node_diagnostics"] = f"{CANARY}-{version}-diag-string"
        string_object["device"]["radio_metrics"] = [f"{CANARY}-{version}-radio-list", 1]
        string_object["device"]["node_status"] = f"{CANARY}-{version}-status-string"
        string_object["device"]["labels"] = ["synthetic-label", {"k": f"{CANARY}-{version}-label-obj"}, 3]
        cases.append(("string_for_object", ok(string_object), {"device_id": "x"}, True))
        omitted = {"success": True, "device": {"device_id": "synthetic-omitted"}}
        cases.append(("omitted", ok(omitted), {"device_id": "x"}, True))
        duplicate_text = (
            '{"success": true, "device": {"device_id": "synthetic-dup", '
            f'"network_key": "{CANARY}-{version}-dup-first", '
            f'"network_key": "{CANARY}-{version}-dup-second", '
            '"node_diagnostics": {"network_type": "wifi", '
            f'"pskc": "{CANARY}-{version}-dup-pskc-a", "pskc": "{CANARY}-{version}-dup-pskc-b"}}}}'
        )
        cases.append(("duplicate_keys", {"isError": False, "content": [{"type": "text", "text": duplicate_text}]},
                      {"device_id": "x"}, False))
        markers |= {f"{CANARY}-{version}-dup-first", f"{CANARY}-{version}-dup-second",
                    f"{CANARY}-{version}-dup-pskc-a", f"{CANARY}-{version}-dup-pskc-b"}
        for name in ("oversize", "string_object"):
            markers |= {m[:40] for m in markers_in(locals()[name])}
        device_string = {"success": True, "device": f"{CANARY}-{version}-device-string"}
        cases.append(("device_string", ok(device_string), {"device_id": "x"}, False))
        markers.add(f"{CANARY}-{version}-device-string")
        non_json = {"isError": False, "content": [{"type": "text", "text": f"not json {CANARY}-{version}-raw-text"}]}
        cases.append(("non_json_text", non_json, {"device_id": "x"}, False))
        markers.add(f"{CANARY}-{version}-raw-text")
        multi = {"isError": False, "content": [
            {"type": "text", "text": f"{CANARY}-{version}-multi-a"},
            {"type": "text", "text": f"{CANARY}-{version}-multi-b"}]}
        cases.append(("multi_content", multi, {"device_id": "x"}, False))
        markers |= {f"{CANARY}-{version}-multi-a", f"{CANARY}-{version}-multi-b"}
        error_payload = {"isError": True, "content": [{"type": "text", "text": json.dumps({
            "success": False,
            "error": {"code": "RESOURCE_NOT_FOUND",
                      "message": f"Device not found {CANARY}-{version}-error-message",
                      "context": {"available_device_ids": [f"{CANARY}-{version}-error-id"],
                                  "network_key": f"{CANARY}-{version}-error-key"}},
        })}]}
        cases.append(("upstream_error", error_payload, {"device_id": "x"}, False))
        markers |= {f"{CANARY}-{version}-error-message", f"{CANARY}-{version}-error-id",
                    f"{CANARY}-{version}-error-key"}
        failed_success = {"success": False, "device": canary_fields(f"{version}-failed")}
        cases.append(("success_false", ok(failed_success), {"device_id": "x"}, False))
        markers |= markers_in(failed_success)

        self.assertGreater(len(markers), 300)
        for tag, call_result, args, expect_success in cases:
            with self.subTest(version=version, case=tag):
                result, telemetry = await h.call(args, call_result)
                calls += 1
                self.assertEqual(result["success"], expect_success, result)
                self.assertFalse(result["metadata"]["fallback_occurred"])
                if expect_success:
                    data = result["data"]
                    assert_closed_warnings(self, data)
                    self.assertEqual(markers_in(data), set())
                else:
                    self.assertNotIn("data", result)
                    self.assertTrue(result["metadata"]["upstream_dispatch_occurred"])
        h.scan(markers)
        h.assert_no_side_calls(calls)
        return h

    async def test_device_projection_credential_canaries_85(self):
        await self._canaries("8.5.0")

    async def test_device_projection_credential_canaries_91(self):
        await self._canaries("9.1.0")

    async def test_device_projection_useful_fields_preserved(self):
        for version in VERSIONS:
            h = await Harness(self, version).start()
            calls = 0
            with self.subTest(version=version, fixture="canary_single"):
                result, _ = await h.call({"device_id": "x"}, ok(canary_single(f"{version}-useful")))
                calls += 1
                device = result["data"]["device"]
                source = FIXTURE["single_matter_wifi"]["device"]
                for name in ("device_id", "name", "manufacturer", "model", "sw_version", "area_id",
                             "via_device_id", "config_entries", "labels", "integration_type",
                             "integration_sources", "name_by_user", "default_name", "hw_version",
                             "serial_number", "disabled_by"):
                    self.assertEqual(device[name], source[name], name)
                self.assertEqual(device["entities"], source["entities"])
                self.assertEqual(result["data"]["entities"], source["entities"])
                self.assertEqual(device["radio_metrics"], {"lqi": 200, "rssi": -60})
                self.assertEqual(device["node_status"], FIXTURE["single_zwave"]["device"]["node_status"])
                diag = device["node_diagnostics"]
                expected_diag = source["node_diagnostics"]
                for name in ("network_type", "node_type", "available", "network_name", "mac_address",
                             "active_fabric_index", "ip_addresses", "active_fabrics"):
                    self.assertEqual(diag[name], expected_diag[name], name)
                self.assertEqual(device["connections"], source["connections"])
                self.assertEqual(device["identifiers"], source["identifiers"])
                self.assertEqual(device["registry_pairs_withheld"], {"connections": 2, "identifiers": 2})
                self.assertGreater(result["data"]["fields_withheld"], 0)
            for name in ("single_zha", "single_zwave", "single_matter_wifi", "single_z2m",
                         "single_hassio", "single_child", "list_z2m"):
                with self.subTest(version=version, fixture=name):
                    result, _ = await h.call({"device_id": "x"}, ok(FIXTURE[name]))
                    calls += 1
                    self.assertTrue(result["success"], result)
                    # Clean fixtures project to themselves: nothing useful lost.
                    self.assertEqual(result["data"], FIXTURE[name])
            with self.subTest(version=version, fixture="single_child"):
                child = result_child = (await h.call({"device_id": "x"}, ok(FIXTURE["single_child"])))[0]
                calls += 1
                device = result_child["data"]["device"]
                self.assertEqual(device["via_device_id"], "synthetic-parent-bridge")
                self.assertEqual(device["area_id"], "living_room")
                self.assertEqual(device["config_entries"], ["01SYNTHCHILDENTRY"])
                self.assertTrue(child["success"])
            for profile in (CHILD_DEVICE_PROBE_PROFILE, CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE):
                self.assertEqual(delegated_provider_compatibility(
                    tool_name=DEVICE, core_version="2026.10.0", adapter_version=version,
                    probe_profile=profile), (True, None))
            h.assert_no_side_calls(calls)

    async def _pairs(self, version):
        h = await Harness(self, version).start()
        calls = 0
        all_markers: list[str] = []
        # Single-device read in every placement.
        for placement in ("both", "structured_only", "text_only", "text_disagrees"):
            tag = f"{version}-{placement}"
            device, conn_neg, ident_neg, markers = pairs_device(tag)
            all_markers += markers
            payload = deepcopy(FIXTURE["single_child"])
            payload["device"] = device
            call_result = dict((n, r) for n, r, _ in placements(payload, FIXTURE["single_child"]))[placement]
            with self.subTest(version=version, mode="single", placement=placement):
                result, _ = await h.call({"device_id": "synthetic-child-device"}, call_result)
                calls += 1
                self.assertTrue(result["success"], result)
                data = result["data"]
                out = data["device"]
                if placement == "text_disagrees":
                    self.assertEqual(data, FIXTURE["single_child"])
                    continue
                self.assertEqual(out["connections"], CONNECTION_POSITIVES)
                self.assertEqual(out["identifiers"], IDENTIFIER_POSITIVES)
                self.assertEqual(out["registry_pairs_withheld"],
                                 {"connections": conn_neg, "identifiers": ident_neg})
                self.assertEqual(data["warnings"], [projection.REGISTRY_PAIR_WITHHELD])
                self.assertNotIn("fields_withheld", data)
                # Effective area and child config entries unchanged by the pair rule.
                self.assertEqual(out["area_id"], "living_room")
                self.assertEqual(out["via_device_id"], "synthetic-parent-bridge")
                self.assertEqual(out["config_entries"], ["01SYNTHCHILDENTRY"])
                self.assertEqual(set(out["registry_pairs_withheld"]), {"connections", "identifiers"})
        # Derived identity fields for the ZHA, Zigbee2MQTT and Z-Wave devices.
        for name, expected in (
            ("single_zha", {"ieee_address": "00:11:22:33:44:55:66:77",
                            "zha_trigger_hint": "Use ieee '00:11:22:33:44:55:66:77' for zha_event triggers"}),
            ("single_z2m", {"ieee_address": "0x00124b0012345678"}),
            ("single_zwave", {"node_id": "12"}),
        ):
            payload = deepcopy(FIXTURE[name])
            device, conn_neg, ident_neg, markers = pairs_device(f"{version}-{name}", base=name)
            all_markers += markers
            payload["device"] = device
            with self.subTest(version=version, mode="derived", fixture=name):
                result, _ = await h.call({"device_id": "x"}, ok(payload))
                calls += 1
                out = result["data"]["device"]
                for key, value in expected.items():
                    self.assertEqual(out[key], value)
                self.assertEqual(out["connections"], CONNECTION_POSITIVES)
                self.assertEqual(out["identifiers"], IDENTIFIER_POSITIVES)
                self.assertEqual(result["data"]["warnings"], [projection.REGISTRY_PAIR_WITHHELD])
        # List mode: every devices[] row.
        for placement in ("both", "structured_only", "text_only"):
            payload = deepcopy(FIXTURE["list_z2m"])
            expected_counts = []
            for index, row in enumerate(payload["devices"]):
                device, conn_neg, ident_neg, markers = pairs_device(f"{version}-list{index}-{placement}")
                all_markers += markers
                row["connections"] = device["connections"]
                row["identifiers"] = device["identifiers"]
                expected_counts.append({"connections": conn_neg, "identifiers": ident_neg})
            call_result = dict((n, r) for n, r, _ in placements(payload, FIXTURE["list_z2m"]))[placement]
            with self.subTest(version=version, mode="list", placement=placement):
                result, _ = await h.call({"integration": "zigbee2mqtt"}, call_result)
                calls += 1
                data = result["data"]
                self.assertEqual(data["warnings"], [projection.REGISTRY_PAIR_WITHHELD])
                for row, counts in zip(data["devices"], expected_counts):
                    self.assertEqual(row["connections"], CONNECTION_POSITIVES)
                    self.assertEqual(row["identifiers"], IDENTIFIER_POSITIVES)
                    self.assertEqual(row["registry_pairs_withheld"], counts)
                self.assertEqual(data["bridge"], FIXTURE["list_z2m"]["bridge"])
        # Non-array fields and the 65th entry.
        payload = deepcopy(FIXTURE["single_child"])
        payload["device"]["connections"] = f"{CANARY}-{version}-nonarray"
        payload["device"]["identifiers"] = {"k": f"{CANARY}-{version}-nonarray-object"}
        all_markers += [f"{CANARY}-{version}-nonarray", f"{CANARY}-{version}-nonarray-object"]
        with self.subTest(version=version, mode="non_array"):
            result, _ = await h.call({"device_id": "x"}, ok(payload))
            calls += 1
            out = result["data"]["device"]
            self.assertEqual(out["connections"], [])
            self.assertEqual(out["identifiers"], [])
            self.assertEqual(out["registry_pairs_withheld"], {"connections": 1, "identifiers": 1})
            self.assertEqual(result["data"]["warnings"], [projection.REGISTRY_PAIR_WITHHELD])
        payload = deepcopy(FIXTURE["single_child"])
        sixty_four = [["mac", f"aa:bb:cc:dd:{i // 256:02x}:{i % 256:02x}"] for i in range(64)]
        payload["device"]["connections"] = sixty_four + [["network_key", f"{CANARY}-{version}-sixty-fifth"]]
        payload["device"]["identifiers"] = [["zha", f"00:11:22:33:44:55:{i // 256:02x}:{i % 256:02x}"]
                                            for i in range(64)] + [["zha", "00:11:22:33:44:55:66:99"]]
        all_markers += [f"{CANARY}-{version}-sixty-fifth", "00:11:22:33:44:55:66:99"]
        with self.subTest(version=version, mode="sixty_fifth"):
            result, _ = await h.call({"device_id": "x"}, ok(payload))
            calls += 1
            out = result["data"]["device"]
            self.assertEqual(out["connections"], sixty_four)
            self.assertEqual(len(out["identifiers"]), 64)
            self.assertEqual(out["registry_pairs_withheld"], {"connections": 1, "identifiers": 1})
        # Disclosure never names a discriminator, value or index.
        for discriminator in NEGATIVE_DISCRIMINATORS:
            all_markers.append(f'"{discriminator}"')
        h.scan([m for m in all_markers if m])
        h.assert_no_side_calls(calls)
        # Guards the derived-field rule: no negative discriminator is a Core domain.
        for discriminator in NEGATIVE_DISCRIMINATORS:
            self.assertNotIn(discriminator, projection.CORE_INTEGRATION_DOMAINS)

    async def test_device_registry_pairs_85(self):
        await self._pairs("8.5.0")

    async def test_device_registry_pairs_91(self):
        await self._pairs("9.1.0")

    async def _derived(self, version):
        h = await Harness(self, version).start()
        calls = 0
        markers = []
        c = f"{CANARY}-{version}-derived"
        bad_cases = [
            ("ieee_canary", "single_zha", {"ieee_address": f"{c}-ieee",
                                           "zha_trigger_hint": f"Use ieee '{c}-ieee' for zha_event triggers"},
             ("ieee_address", "zha_trigger_hint"), 2),
            ("ieee_non_eui64", "single_zha", {"ieee_address": "00:11:22:33:44:55:66",
                                              "zha_trigger_hint": "Use ieee '00:11:22:33:44:55:66' for zha_event triggers"},
             ("ieee_address", "zha_trigger_hint"), 2),
            ("hint_differs", "single_zha", {"zha_trigger_hint": f"Use ieee '00:11:22:33:44:55:66:77' {c}-hint"},
             ("zha_trigger_hint",), 1),
            ("node_non_numeric", "single_zwave", {"node_id": f"{c}-node"}, ("node_id",), 1),
            ("node_five_digits", "single_zwave", {"node_id": "12345"}, ("node_id",), 1),
            ("source_outside", "single_zha", {"integration_sources": ["zha", "network_key", f"{c}-source"]},
             (), 2),
            ("type_outside", "single_zha", {"integration_type": "network_key"}, ("integration_type",), 1),
            ("type_canary", "single_hassio", {"integration_type": f"{c}-type"}, ("integration_type",), 1),
            ("bridge_ieee", "list_z2m", None, (), 1),
        ]
        for tag, base, update, omitted, count in bad_cases:
            payload = deepcopy(FIXTURE[base])
            if base == "list_z2m":
                payload["bridge"]["ieee_address"] = f"{c}-bridge-ieee"
                payload["devices"][1]["ieee_address"] = f"{c}-row-ieee"
                markers += [f"{c}-bridge-ieee", f"{c}-row-ieee"]
            else:
                payload["device"].update(update)
                markers += [v for v in json.dumps(update).split('"') if CANARY in v]
            with self.subTest(version=version, case=tag):
                result, _ = await h.call({"device_id": "x"}, ok(payload))
                calls += 1
                self.assertTrue(result["success"], result)
                data = result["data"]
                self.assertEqual(data["warnings"], [projection.REGISTRY_IDENTITY_WITHHELD])
                self.assertNotIn("fields_withheld", data)
                if base == "list_z2m":
                    self.assertNotIn("ieee_address", data["bridge"])
                    self.assertEqual(data["bridge"]["identity_fields_withheld"], 1)
                    row = data["devices"][1]
                    self.assertNotIn("ieee_address", row)
                    self.assertEqual(row["identity_fields_withheld"], 1)
                    self.assertEqual(data["devices"][0], FIXTURE["list_z2m"]["devices"][0])
                    continue
                device = data["device"]
                self.assertEqual(device["identity_fields_withheld"], count)
                for name in omitted:
                    self.assertNotIn(name, device)
                # Valid values on the same device still pass.
                for name, value in FIXTURE[base]["device"].items():
                    if name in omitted or name in (update or {}):
                        continue
                    self.assertEqual(device[name], value, name)
                if tag == "source_outside":
                    self.assertEqual(device["integration_sources"], ["zha"])
        h.scan(markers)
        h.assert_no_side_calls(calls)

    async def test_device_registry_derived_fields_85(self):
        await self._derived("8.5.0")

    async def test_device_registry_derived_fields_91(self):
        await self._derived("9.1.0")

    async def test_device_projection_thread_ip_withheld(self):
        for version in VERSIONS:
            h = await Harness(self, version).start()
            calls = 0
            seen: list = []
            for network_type, passes in (("wifi", True), ("ethernet", True), ("thread", False),
                                         (None, False), ("missing", False), ("Thread", False),
                                         ("unknown_radio", False), (7, False)):
                payload = deepcopy(FIXTURE["single_matter_wifi"])
                diag = payload["device"]["node_diagnostics"]
                # Distinct addresses per case: a passing case must not mask a leak.
                index = len(seen)
                addresses = [f"fd00:db8:0:0:aa:bb:cc:{index:02x}", f"192.0.2.{100 + index}"]
                seen.append((addresses, passes))
                diag["ip_addresses"] = list(addresses)
                if network_type == "missing":
                    del diag["network_type"]
                else:
                    diag["network_type"] = network_type
                with self.subTest(version=version, network_type=network_type):
                    result, _ = await h.call({"device_id": "x"}, ok(payload))
                    calls += 1
                    out = result["data"]["device"]["node_diagnostics"]
                    if passes:
                        self.assertEqual(out["ip_addresses"], addresses)
                        self.assertNotIn("unavailable_fields", out)
                        self.assertNotIn("warnings", result["data"])
                    else:
                        self.assertNotIn("ip_addresses", out)
                        self.assertEqual(out["unavailable_fields"],
                                         {"ip_addresses": projection.THREAD_MESH_LOCAL_WITHHELD})
                        self.assertIn(projection.THREAD_MESH_LOCAL_WITHHELD, result["data"]["warnings"])
                        for address in addresses:
                            self.assertNotIn(address, json.dumps(result))
            # Released fixture: Thread mesh-local address withheld, rest intact.
            result, _ = await h.call({"device_id": "x"}, ok(FIXTURE["single_matter_thread"]))
            calls += 1
            diag = result["data"]["device"]["node_diagnostics"]
            self.assertEqual(result["data"]["warnings"], [projection.THREAD_MESH_LOCAL_WITHHELD])
            self.assertEqual(diag["network_type"], "thread")
            self.assertEqual(diag["network_name"], "SyntheticNet")
            self.assertEqual(diag["active_fabrics"],
                             FIXTURE["single_matter_thread"]["device"]["node_diagnostics"]["active_fabrics"])
            withheld = [a for addresses, passes in seen if not passes for a in addresses]
            self.assertEqual(len(withheld), 12)
            h.scan(["fd00:db8:0:0:1:2:3:4", *withheld])
            h.assert_no_side_calls(calls)

    async def test_device_projection_warnings_closed_codes(self):
        for version in VERSIONS:
            h = await Harness(self, version).start()
            calls = 0
            c = f"{CANARY}-{version}-warning"
            cases = [
                ([f"ZHA radio metrics unavailable: {c}-zha"], [projection.ZHA_RADIO_METRICS_UNAVAILABLE]),
                ([f"Z-Wave node status unavailable: {c}-zwave"], [projection.ZWAVE_NODE_STATUS_UNAVAILABLE]),
                ([f"Matter node diagnostics unavailable: {c}-matter"],
                 [projection.MATTER_NODE_DIAGNOSTICS_UNAVAILABLE]),
                ([f"{c}-unrecognized"], [projection.ENRICHMENT_WARNING_UNRECOGNIZED]),
                ([f"zha radio metrics unavailable: {c}-lower"], [projection.ENRICHMENT_WARNING_UNRECOGNIZED]),
                ([{"message": f"{c}-object"}, 5, None], [projection.ENRICHMENT_WARNING_UNRECOGNIZED]),
                (f"{c}-string-warnings", [projection.ENRICHMENT_WARNING_UNRECOGNIZED]),
                ([f"ZHA radio metrics unavailable: {c}-a", f"Z-Wave node status unavailable: {c}-b",
                  f"Matter node diagnostics unavailable: {c}-c", f"{c}-d"],
                 sorted([projection.ZHA_RADIO_METRICS_UNAVAILABLE, projection.ZWAVE_NODE_STATUS_UNAVAILABLE,
                         projection.MATTER_NODE_DIAGNOSTICS_UNAVAILABLE,
                         projection.ENRICHMENT_WARNING_UNRECOGNIZED])),
                ([], None),
            ]
            for warnings, expected in cases:
                payload = deepcopy(FIXTURE["single_zha"])
                payload["warnings"] = warnings
                with self.subTest(version=version, warnings=warnings):
                    result, _ = await h.call({"device_id": "x"}, ok(payload))
                    calls += 1
                    data = result["data"]
                    if expected is None:
                        self.assertNotIn("warnings", data)
                    else:
                        self.assertEqual(data["warnings"], expected)
                    self.assertNotIn("unavailable:", json.dumps(result))
                    self.assertNotIn(CANARY, json.dumps(result))
                    assert_closed_warnings(self, data)
            h.scan([f"{c}-{s}" for s in ("zha", "zwave", "matter", "unrecognized", "lower", "object",
                                          "string-warnings", "a", "b", "c", "d")]
                   + ["radio metrics unavailable", "node status unavailable", "diagnostics unavailable"])
            h.assert_no_side_calls(calls)

    async def _manage_radio_unreachable(self, version):
        h = await Harness(self, version).start()
        entry = load_reviewed_upstream_release_registry().by_version[version].policy.by_name[RADIO]
        self.assertNotEqual(entry.classification, "automatic_read")
        self.assertIn(RADIO, {tool["name"] for tool in capture(version)["tools"]})
        snapshot = h.snapshot()
        self.assertNotIn(RADIO, snapshot)
        self.assertIn(DEVICE, snapshot)
        listed = {tool.name for tool in await h.mcp.list_tools()}
        self.assertNotIn(RADIO, listed)
        self.assertFalse(any("radio" in name for name in listed))
        for arguments in ({}, {"action": "network_status", "radio": "thread"},
                          {"action": "list_datasets", "radio": "thread"}):
            with self.assertRaises(Exception):
                await h.mcp.call_tool(RADIO, arguments)
        # A device read never reaches radio commands either.
        await h.call({"device_id": "x"}, ok(canary_single(f"{version}-radio")))
        self.assertEqual(h.transport.calls, 1)
        h.assert_no_side_calls(1)

    async def test_manage_radio_unreachable_85(self):
        await self._manage_radio_unreachable("8.5.0")

    async def test_manage_radio_unreachable_91(self):
        await self._manage_radio_unreachable("9.1.0")


# ---------------------------------------------------------------------------
# T2: system health radio sections
# ---------------------------------------------------------------------------

class HealthRadioSectionTests(unittest.IsolatedAsyncioTestCase):
    SECTIONS = ["thread_network", "matter_network", "zha_network", "zwave_network"]

    def health(self, tag):
        c = canary_fields
        return {
            "success": True,
            "health_info": {"type": "initial", "data": {
                "homeassistant": {"info": {"version": "2026.11.0", **c(f"{tag}-ha")}},
            }},
            "thread_network": {
                "border_routers": [{
                    "extended_address": "aabbccddeeff0011", "channel": 15,
                    "extended_pan_id": "1122334455667788", "border_agent_id": "agent-1",
                    "active_dataset_tlvs": f"{CANARY}-{tag}-otbr-tlvs",
                    "otbr_info": {"active_dataset_tlvs": f"{CANARY}-{tag}-otbr-info-tlvs",
                                  "network_key": f"{CANARY}-{tag}-otbr-info-key"},
                    **c(f"{tag}-router"),
                }],
                "datasets": [{"tlv": f"{CANARY}-{tag}-dataset-list"}],
                **c(f"{tag}-thread"),
            },
            "matter_network": {"config_entry_id": "01MATTERENTRY", "state": "loaded", "title": "Matter",
                               **c(f"{tag}-matter")},
            "zha_network": {"devices": [{"ieee": "00:11:22:33:44:55:66:77", "name": "Hall", "manufacturer": "S",
                                         "model": "Z1", "lqi": 211, "rssi": -52, "available": True,
                                         **c(f"{tag}-zha-device")}],
                            "total_count": 1, **c(f"{tag}-zha")},
            "zwave_network": {
                "controller": {"home_id": 3735928559, "type": "primary", "own_node_id": 1,
                               **c(f"{tag}-zwave-controller")},
                "nodes": [{"node_id": 2, "status": "alive", "is_routing": True, "is_secure": True,
                           "zwave_plus_version": 2, "is_controller_node": False, **c(f"{tag}-zwave-node")}],
                "total_count": 1, **c(f"{tag}-zwave"),
            },
        }

    async def test_health_thread_network_credential_canaries(self):
        for version in VERSIONS:
            h = await Harness(self, version).start()
            self.assertIn(platform.HEALTH, h.snapshot())
            calls = 0
            markers: set[str] = set()
            for placement in ("both", "structured_only", "text_only", "text_disagrees"):
                tag = f"{version}-health-{placement}"
                payload = self.health(tag)
                markers |= markers_in(payload)
                clean = json.loads(json.dumps(payload))
                for section in self.SECTIONS:
                    clean[section] = {k: v for k, v in clean[section].items()
                                      if k in ("border_routers", "devices", "nodes", "controller",
                                               "total_count", "config_entry_id", "state", "title")}
                clean = json.loads(json.dumps(clean).replace(CANARY, "synthetic"))
                call_result = dict((n, r) for n, r, _ in placements(payload, clean))[placement]
                with self.subTest(version=version, placement=placement):
                    result, _ = await h.call({"sections": self.SECTIONS}, call_result, name=platform.HEALTH)
                    calls += 1
                    self.assertTrue(result["success"], result)
                    sections = result["data"]["sections"]
                    router = sections["thread_network"]["border_routers"][0]
                    self.assertEqual(router, {"extended_address": "aabbccddeeff0011", "channel": 15,
                                              "extended_pan_id": "1122334455667788",
                                              "border_agent_id": "agent-1"})
                    self.assertEqual(set(sections["thread_network"]),
                                     {"count", "total_count", "truncated", "border_routers"})
                    self.assertEqual(sections["matter_network"],
                                     {"config_entry_id": "01MATTERENTRY", "state": "loaded", "title": "Matter"})
                    self.assertEqual(set(sections["zha_network"]["devices"][0]),
                                     {"ieee", "name", "manufacturer", "model", "lqi", "rssi", "available"})
                    self.assertEqual(sections["zwave_network"]["nodes"][0]["node_id"], 2)
                    self.assertEqual(markers_in(result), set())
            h.scan(markers)
            self.assertEqual(h.transport.calls, calls)
            self.assertEqual({tool for tool, _ in h.transport.sent}, {platform.HEALTH})
            assert_no_radio_dispatch(self, h.transport.sent)
            self.assertEqual(h.rest.calls, [])
            self.assertEqual(h.websocket.calls, [])


# ---------------------------------------------------------------------------
# Section 5: Core child-device binding
# ---------------------------------------------------------------------------

class CoreChildDeviceBindingTests(unittest.IsolatedAsyncioTestCase):
    PROFILES = (CHILD_DEVICE_PROBE_PROFILE, CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE)

    def _binding(self, version):
        # Signed Core profiles are unchanged; the binding is binary-owned.
        self.assertEqual(CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE.contract_fingerprint,
                         "sha256:8dad86e454ca58ae558c9229ebd0fd7b748a3a2ff2a9bbb860a1f97a71eaee6b")
        self.assertEqual(CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE.delegated_device_adapters, ("8.5.0",))
        self.assertEqual(CHILD_DEVICE_PROBE_PROFILE.delegated_device_adapters, ("8.4.3",))
        # 9.1.0 is in neither signed profile: only the binary-owned row admits it.
        for profile in self.PROFILES:
            self.assertNotIn("9.1.0", profile.delegated_device_adapters)
        for profile in self.PROFILES:
            for tool in sorted(DEVICE_DEPENDENT_DELEGATED_TOOLS):
                with self.subTest(version=version, profile=profile.profile_id, tool=tool):
                    self.assertEqual(delegated_provider_compatibility(
                        tool_name=tool, core_version="2026.10.0", adapter_version=version,
                        probe_profile=profile), (True, None))
                for other in ("8.4.3", "8.5.0", "8.6.0", "9.0.0", "9.1.1", "9.10", None):
                    if other == version:
                        continue
                    # Pre-existing rows stay as they were: 8.5.0 and 8.6.0 on
                    # the v1 profile, 8.6.0 on October, and each profile's own
                    # delegated_device_adapters.
                    if other in profile.delegated_device_adapters or (
                        other in ("8.5.0", "8.6.0") and profile == CHILD_DEVICE_PROBE_PROFILE
                    ) or (other == "8.6.0" and profile == CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE):
                        continue
                    with self.subTest(version=other, profile=profile.profile_id, tool=tool):
                        self.assertEqual(delegated_provider_compatibility(
                            tool_name=tool, core_version="2026.10.0", adapter_version=other,
                            probe_profile=profile), (False, "ha_mcp_child_device_contract_unproven"))
                renamed = replace(profile, profile_id="synthetic-unknown")
                with self.subTest(version=version, profile="renamed", tool=tool):
                    self.assertEqual(delegated_provider_compatibility(
                        tool_name=tool, core_version="2026.10.0", adapter_version=version,
                        probe_profile=renamed),
                        (True, None) if version in renamed.delegated_device_adapters
                        else (False, "ha_mcp_child_device_contract_unproven"))

    async def _gateway(self, version):
        from test_core_2026_10_device_registry import OctoberSignedSelectionTests

        case = OctoberSignedSelectionTests()
        case.addCleanup = self.addCleanup
        await case.asyncSetUp()
        self.assertEqual((await case.publish())["compatible_count"], 22)
        h = await Harness(self, version, core_runtime=case.core).start()
        self.assertTrue(DEVICE_DEPENDENT_DELEGATED_TOOLS <= h.snapshot().keys())
        result, telemetry = await h.call({"device_id": "synthetic-child-device"}, ok(FIXTURE["single_child"]))
        self.assertTrue(result["success"], result)
        device = result["data"]["device"]
        self.assertEqual(device["via_device_id"], "synthetic-parent-bridge")
        self.assertEqual(device["area_id"], "living_room")
        self.assertEqual(device["config_entries"], ["01SYNTHCHILDENTRY"])
        self.assertEqual(result["data"], FIXTURE["single_child"])
        self.assertEqual(telemetry.upstream_request_count, 1)
        self.assertFalse(result["metadata"]["fallback_occurred"])
        h.assert_no_side_calls(1)
        self.assertEqual(case.core.health_snapshot()["active_commit_count"], 0)

    async def test_91_core_child_device_positive(self):
        self._binding("9.1.0")
        await self._gateway("9.1.0")

    async def test_85_core_child_device_positive(self):
        self._binding("8.5.0")
        await self._gateway("8.5.0")


# Section 6.5 optional backstop: the exact key list of the design.
THREAD_CREDENTIAL_KEYS = (
    "network_key", "pskc", "pskd", "active_dataset_tlvs", "pending_dataset_tlvs",
    "dataset_tlvs", "tlv",
)


class ThreadCredentialSanitizerTests(unittest.TestCase):
    """Section 8.3: ``test_sanitizer_thread_credential_category`` (section 6.5
    backstop). The ``ha_get_device`` allowlist stays the control; this is the
    second line of defense for a generic attribute carrying the same keys."""

    def test_sanitizer_thread_credential_category(self):
        from ha_mcp_engineering import sanitization
        from ha_mcp_engineering.providers.upstream_yaml_reader import REDACTION_CATEGORIES

        marker = "[REDACTED:thread_credential]"
        # The reader's closed category set carries the new category, so a YAML
        # value under these keys is screened rather than refused as unknown.
        self.assertIn("thread_credential", REDACTION_CATEGORIES)
        for key in THREAD_CREDENTIAL_KEYS:
            # Exact, case and separator variants all normalize to the key.
            variants = {key, key.upper(), key.replace("_", "-"), key.title().replace("_", " ")}
            for variant in sorted(variants):
                normalized = sanitization.normalize_key(variant)
                with self.subTest(key=variant):
                    self.assertEqual(normalized, key)
                    self.assertEqual(sanitization.key_category(normalized, ()), "thread_credential")
                    # Nested under an arbitrary attribute container, with
                    # every value shape a generic attribute can carry.
                    for value in (
                        f"{CANARY}-{key}",
                        {"inner": f"{CANARY}-{key}-dict"},
                        [f"{CANARY}-{key}-list"],
                        1234567,
                    ):
                        payload = {
                            "attributes": {
                                variant: value,
                                "network_name": "synthetic-net",
                                "channel": 15,
                                "extended_pan_id": "dead00beef00cafe",
                            }
                        }
                        result = sanitization.sanitize_untrusted_data(payload)
                        attributes = result.value["attributes"]
                        self.assertEqual(attributes[variant], marker)
                        self.assertEqual(result.redaction_categories, ("thread_credential",))
                        self.assertEqual(result.redacted_field_count, 1)
                        self.assertFalse(result.failed_closed)
                        self.assertNotIn(CANARY, json.dumps(result.value))
                        # Released neighbors are untouched.
                        self.assertEqual(attributes["network_name"], "synthetic-net")
                        self.assertEqual(attributes["channel"], 15)
                        self.assertEqual(attributes["extended_pan_id"], "dead00beef00cafe")
        # Keys outside the list keep their earlier treatment: no category.
        for key in ("network_name", "channel", "extended_pan_id", "pan_id",
                    "mesh_local_prefix", "dataset", "tlvs"):
            with self.subTest(neighbor=key):
                self.assertIsNone(sanitization.key_category(key, ()))


if __name__ == "__main__":
    unittest.main()
