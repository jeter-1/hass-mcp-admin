"""Offline tests of the SR live-socket replay harness (HAMCP-371, SR).

No container, image, registry or Home Assistant is used.  The proxy tests use
disposable loopback HTTP peers with synthetic bodies; every token, secret and
value is synthetic.
"""

from __future__ import annotations

import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CONTROL_REGISTRY = ROOT / "tests/fixtures/ha_mcp_8_5_0_control/upstream_release_registry.json"
sys.path.insert(0, str(ROOT / "scripts"))
import core_read_replay_calls as calls  # noqa: E402
import core_read_replay_capture_local as lane  # noqa: E402
import hamcp371_live_replay_holder as holder  # noqa: E402
import hamcp371_live_replay_local as sr  # noqa: E402
from upstream_catalog_capture_local import Refusal  # noqa: E402

# HAMCP-371 (ruling 2026-11-16 08:18, T1 item 4): every scratch directory this module
# makes is removed by a cleanup on every exit path, and the module proves none remain:
# its default temp area is a private parent that must be empty when the module ends.
_SCRATCH = {}


def setUpModule():
    _SCRATCH["tempdir"] = tempfile.tempdir
    _SCRATCH["parent"] = Path(tempfile.mkdtemp(prefix="hamcp371-live-replay-"))
    tempfile.tempdir = str(_SCRATCH["parent"])


def tearDownModule():
    tempfile.tempdir = _SCRATCH["tempdir"]
    remaining = sorted(path.name for path in _SCRATCH["parent"].iterdir())
    if remaining:
        raise AssertionError(f"{__name__} left scratch directories: {remaining}")
    _SCRATCH["parent"].rmdir()


def scratch(test):
    """A new scratch directory that a cleanup removes, whether the test passes or fails."""
    path = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, path)
    return path

SYNTHETIC_TOKEN = "synthetic-administrator-token-not-real"


def tool_result(payload, is_error=False):
    return {"jsonrpc": "2.0", "id": 101, "result": {
        "content": [{"type": "text", "text": json.dumps(payload)}], "isError": is_error}}


def synthetic_seed():
    names = set()

    def walk(value):
        if isinstance(value, str) and value.startswith(calls.SEED):
            names.add(value[len(calls.SEED):])
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    for case in calls.CASES:
        if case["group"] == holder.READ_GROUP:
            walk(case["arguments"])
    return {name: f"synthetic_{name}" for name in sorted(names)}


class ResultClassTests(unittest.TestCase):
    def test_success_from_text_and_from_structured_content(self):
        klass, payload = holder.result_class({"response": tool_result({"success": True, "x": 1})})
        self.assertEqual((klass, payload["x"]), ("success", 1))
        structured = {"response": {"result": {"structuredContent": {"success": True},
                                              "content": []}}}
        self.assertEqual(holder.result_class(structured)[0], "success")

    def test_error_codes_jsonrpc_and_undecodable(self):
        self.assertEqual(holder.result_class({"response": tool_result(
            {"success": False, "error_code": "upstream_unavailable"}, True)})[0],
            "error:upstream_unavailable")
        self.assertEqual(holder.result_class({"response": tool_result(
            {"success": False, "error": {"code": "nested_code"}})})[0], "error:nested_code")
        self.assertEqual(holder.result_class({"response": tool_result({"success": False})})[0],
                         "error:unnamed")
        self.assertEqual(holder.result_class({"response": {"error": {"code": -32602}}})[0],
                         "jsonrpc_error:-32602")
        self.assertEqual(holder.result_class(None)[0], "undecodable")
        self.assertEqual(holder.result_class({"response": {"result": {"content": [
            {"type": "text", "text": "not json"}]}}})[0], "undecodable")

    def test_success_flag_with_is_error_is_not_success(self):
        self.assertEqual(holder.result_class({"response": tool_result(
            {"success": True}, is_error=True)})[0], "undecodable")

    def test_withheld_counts_keep_counts_only_to_depth_three(self):
        payload = {"fields_withheld": 2, "flag_withheld": True, "names_withheld": ["a"],
                   "data": {"rows_dropped_by_policy": {"b": 1, "a": 3},
                            "deep": {"value_replaced": 4, "deeper": {"x_withheld": 9}}}}
        self.assertEqual(holder.withheld_counts(payload), {
            "fields_withheld": 2, "data.rows_dropped_by_policy": {"a": 3, "b": 1},
            "data.deep.value_replaced": 4})
        self.assertEqual(holder.withheld_counts(None), {})


def written_record(out_dir, sequence):
    """The proxy writes a record once the response has been streamed; wait for it."""
    return holder.new_records(out_dir, sequence - 1, wait=10)[0]


class SyntheticUpstream:
    """A loopback peer: SSE without a length, JSON with one, as the test sets."""

    def __init__(self, body, content_type, fixed_length):
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                outer.seen.append((dict(self.headers.items()), self.rfile.read(length)))
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Mcp-Session-Id", "synthetic-session")
                if fixed_length:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                for index in range(0, len(body), 7):
                    self.wfile.write(body[index:index + 7])
                    self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ProxyTests(unittest.TestCase):
    def start_proxy(self, out_dir, target):
        recorder = holder.Recorder(out_dir, target)
        server = ThreadingHTTPServer(("127.0.0.1", 0), holder.handler_for(recorder))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def post(self, port, body, headers, path="/synthetic-upstream-secret/mcp"):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        received = response.read()
        connection.close()
        return response, received

    def exchange(self, upstream_body, content_type, fixed_length):
        upstream = SyntheticUpstream(upstream_body, content_type, fixed_length)
        self.addCleanup(upstream.close)
        out_dir = scratch(self)
        port = self.start_proxy(out_dir, upstream.port)
        request = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": "ha_get_state", "arguments": {"entity_id": "sun.sun"}}}).encode()
        response, received = self.post(port, request, {
            "Content-Type": "application/json", "Authorization": "Bearer " + SYNTHETIC_TOKEN})
        record = written_record(out_dir, 1)
        return upstream, out_dir, request, response, received, record

    def test_streamed_body_passes_unchanged_and_is_stored_by_sha256(self):
        body = b'event: message\ndata: {"jsonrpc":"2.0","id":7,"result":{"isError":false}}\n\n'
        upstream, out_dir, request, response, received, record = self.exchange(
            body, "text/event-stream", fixed_length=False)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Transfer-Encoding"), "chunked")
        self.assertEqual(received, body)
        self.assertEqual(upstream.seen[0][1], request)
        self.assertEqual(upstream.seen[0][0].get("Authorization"), "Bearer " + SYNTHETIC_TOKEN)
        self.assertEqual((out_dir / "000001.response").read_bytes(), body)
        self.assertEqual((out_dir / "000001.request").read_bytes(), request)
        self.assertEqual(record["response_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(record["request_sha256"], hashlib.sha256(request).hexdigest())
        self.assertEqual((record["outcome"], record["response_bytes"]), ("complete", len(body)))
        self.assertEqual((record["jsonrpc_method"], record["tool"], record["jsonrpc_id"]),
                         ("tools/call", "ha_get_state", 7))

    def test_fixed_length_body_keeps_its_length(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {}}).encode()
        _, _, _, response, received, record = self.exchange(
            body, "application/json", fixed_length=True)
        self.assertEqual(response.getheader("Content-Length"), str(len(body)))
        self.assertIsNone(response.getheader("Transfer-Encoding"))
        self.assertEqual(received, body)
        self.assertEqual(record["response_bytes"], len(body))

    def test_authorization_is_withheld_from_every_stored_file(self):
        _, out_dir, _, _, _, record = self.exchange(b"{}", "application/json", True)
        stored = {name: value for name, value in record["request_headers"]}
        expected = f"<{len('Bearer ' + SYNTHETIC_TOKEN)} bytes withheld>"
        self.assertEqual(stored["Authorization"], expected)
        for path in out_dir.iterdir():
            self.assertNotIn(SYNTHETIC_TOKEN.encode(), path.read_bytes(), path.name)

    def test_unreachable_upstream_is_a_502_and_a_record(self):
        probe = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        closed = probe.server_address[1]
        probe.server_close()
        out_dir = scratch(self)
        port = self.start_proxy(out_dir, closed)
        response, _ = self.post(port, b"{}", {"Content-Type": "application/json"})
        self.assertEqual(response.status, 502)
        record = written_record(out_dir, 1)
        self.assertEqual((record["outcome"], record["status"]), ("upstream_unreachable", None))

    def test_a_path_off_the_harness_routes_is_a_502_and_a_record_without_relaying(self):
        upstream = SyntheticUpstream(b"{}", "application/json", True)
        self.addCleanup(upstream.close)
        out_dir = scratch(self)
        port = self.start_proxy(out_dir, upstream.port)
        for sequence, path in enumerate(("/api/hassio/addons", "/synthetic-upstream-secret/mcp?x=1",
                                         "/synthetic-upstream-secret/mcp/../../api/config"), start=1):
            with self.subTest(path=path):
                response, _ = self.post(port, b"{}", {"Content-Type": "application/json",
                                                      "Authorization": "Bearer " + SYNTHETIC_TOKEN}, path)
                self.assertEqual(response.status, 502)
                record = written_record(out_dir, sequence)
                self.assertEqual((record["outcome"], record["status"]), ("route_refused", None))
        self.assertEqual(upstream.seen, [])
        for stored in out_dir.iterdir():
            self.assertNotIn(SYNTHETIC_TOKEN.encode(), stored.read_bytes(), stored.name)

    def test_request_summary_of_other_bodies(self):
        self.assertEqual(holder.request_summary(b"")["decodable"], True)
        self.assertEqual(holder.request_summary(b"\xff")["decodable"], False)
        self.assertEqual(holder.request_summary(b"[1]")["decodable"], False)
        self.assertIsNone(holder.request_summary(
            b'{"method":"tools/list","params":{"name":"x"}}')["tool"])


def write_record(proxy_dir, sequence, method, tool, response):
    stem = f"{sequence:06d}"
    request = json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method,
                          "params": {"name": tool, "arguments": {"wire": sequence}}}).encode()
    (proxy_dir / (stem + ".request")).write_bytes(request)
    (proxy_dir / (stem + ".response")).write_bytes(response)
    (proxy_dir / (stem + ".json")).write_text(json.dumps({
        "sequence": sequence, "method": "POST", "jsonrpc_method": method, "tool": tool,
        "status": 200, "outcome": "complete",
        "request_sha256": hashlib.sha256(request).hexdigest(),
        "response_sha256": hashlib.sha256(response).hexdigest(),
        "response_bytes": len(response)}))


def sse(payload, is_error=False):
    return ("event: message\ndata: " + json.dumps(tool_result(payload, is_error)) +
            "\n\n").encode()


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.dir = scratch(self)

    def test_new_records_wait_for_each_record_and_stop_on_timeout(self):
        write_record(self.dir, 1, "initialize", None, b"{}")
        (self.dir / "000002.request").write_bytes(b"{}")
        with self.assertRaisesRegex(holder.Stop, "proxy_record_incomplete"):
            holder.new_records(self.dir, 1, wait=0, sleep=lambda _s: None)
        write_record(self.dir, 2, "tools/call", "ha_get_state", sse({"success": True}))
        self.assertEqual([r["sequence"] for r in holder.new_records(self.dir, 1)], [2])
        self.assertEqual(holder.new_records(self.dir, 2), [])

    def test_upstream_rows_and_wire_arguments(self):
        write_record(self.dir, 3, "tools/list", None, b"{}")
        write_record(self.dir, 4, "tools/call", "ha_get_state", sse({"success": True}))
        rows, other = holder.upstream_rows(holder.new_records(self.dir, 2))
        self.assertEqual([row["sequence"] for row in rows], [4])
        self.assertEqual(other, ["POST tools/list"])
        self.assertEqual(holder.wire_arguments(self.dir, rows), [{"wire": 4}])

    def test_upstream_is_error_reads_sse_and_plain_json(self):
        write_record(self.dir, 5, "tools/call", "t", sse({"success": True}))
        write_record(self.dir, 6, "tools/call", "t", sse({"success": False}))
        write_record(self.dir, 7, "tools/call", "t", json.dumps(
            {"jsonrpc": "2.0", "id": 7, "error": {"code": -32000}}).encode())
        write_record(self.dir, 8, "tools/call", "t", sse({"success": True}, is_error=True))
        write_record(self.dir, 9, "tools/call", "t", b"no message here")
        found = [holder.upstream_is_error(self.dir, {"sequence": n}) for n in range(5, 10)]
        self.assertEqual(found, [False, True, True, True, True])


def health_payload(**gateway):
    """A get_server_health envelope with the production control values (2026-11-14 22:18)."""
    values = {"upstream_advertised_tool_count": 80, "reviewed_policy_entry_count": 77,
              "version_status": "reviewed_exact", "admission_status": "admitted_exact",
              "catalog_comparison_status": "exact", "upstream_server_version": "8.5.0",
              "reviewed_upstream_version": "8.5.0", "exposed_tools": ["nested", "dropped"]}
    values.update(gateway)
    lifecycle = {"configured": True, "operational_status": "unavailable",
                 "last_failure_category": "upstream_version_mismatch",
                 "observed_upstream_version": "8.5.0", "selected_compatibility_entry_id": None,
                 "nested": {"left": "out"}}
    core = {"configured": True, "initialized": True, "observed_core_version": "2026.11.0",
            "identity_agreement": True, "fallback_count": 0, "counters": {"probes": 1},
            "release_registry": {"enabled": True, "sequence": 18, "cache_status": "valid",
                                 "freshness_status": "current", "surface_denied": False,
                                 "refresh_status": "failed", "failure_reason_counts": []}}
    return {"success": True, "data": {"upstream_read_gateway": values, "governance": {
        "operational_administration": {"lifecycle_provider": lifecycle}},
        "home_assistant_core_authority": core}}


class FakeSession:
    """Engineering's session, with the proxy records each read would cause."""

    def __init__(self, proxy_dir, answers):
        answers = {"get_server_health": (health_payload(), None), **answers}
        self.proxy_dir, self.answers, self.sequence = proxy_dir, answers, 0
        self.tools = []
        self.initialize = {"response": {"result": {"protocolVersion": "2025-06-18"}}}
        self.closed = False

    def call(self, tool, arguments):
        self.tools.append(tool)
        engineering, upstream = self.answers.get(tool, ({"success": True}, sse({"success": True})))
        if upstream is not None:
            self.sequence += 1
            write_record(self.proxy_dir, self.sequence, "tools/call", tool, upstream)
        return {"response": tool_result(engineering, engineering.get("success") is False)}

    def close(self):
        self.closed = True


class ReadsTests(unittest.TestCase):
    def run_reads(self, answers=None, factory=None, version="9.2.0"):
        base = scratch(self)
        proxy_dir = base / "proxy"
        proxy_dir.mkdir()
        (base / "seed.json").write_text(json.dumps(synthetic_seed()))
        sessions = []

        def make(endpoint):
            session = FakeSession(proxy_dir, answers or {})
            sessions.append(session)
            return session
        args = types.SimpleNamespace(endpoint="http://127.0.0.1:1/synthetic/mcp",
                                     seed=str(base / "seed.json"), proxy_dir=str(proxy_dir),
                                     out=str(base / "reads"), version=version)
        code = holder.reads(args, session_factory=factory or make, sleep=lambda _s: None)
        index = json.loads((base / "reads" / "_index.json").read_text())
        return code, index, sessions, base

    def r27_answer(self, upstream=sse({"success": False, "error_code": "no_supervisor"})):
        return {"ha_get_app": ({"success": False, "error_code": "upstream_error"}, upstream)}

    def test_read_cases_are_the_27_with_r12_in_get_mode(self):
        cases = holder.read_cases(synthetic_seed())
        self.assertEqual(len(cases), 27)
        self.assertEqual(cases[-1]["id"], "r27-apps")
        r12 = next(case for case in cases if case["id"] == "r12-blueprints")
        self.assertEqual(r12["arguments"], holder.BLUEPRINT_GET)
        self.assertNotIn(calls.SEED, json.dumps(cases))

    def test_all_27_rows_with_raw_upstream_sha256(self):
        code, index, sessions, base = self.run_reads(self.r27_answer())
        self.assertEqual((code, index["stop"], index["read_count"]), (0, None, 27))
        self.assertTrue(sessions[0].closed)
        first = index["reads"][0]
        self.assertEqual(first["result_class"], "success")
        stored = (base / "proxy" / "000001.response").read_bytes()
        self.assertEqual(first["upstream"][0]["raw_response_sha256"],
                         hashlib.sha256(stored).hexdigest())
        self.assertEqual(first["upstream_wire_arguments"], [{"wire": 1}])
        exchange = (base / "reads" / (first["id"] + ".json")).read_bytes()
        self.assertEqual(first["engineering_exchange_sha256"], hashlib.sha256(exchange).hexdigest())
        last = index["reads"][-1]
        self.assertEqual((last["note"], last["note_confirmed_by_response"]),
                         (holder.APP_NOTE, True))

    def test_a_refused_blueprint_get_is_a_stop(self):
        answers = {"ha_get_blueprint": ({"success": False, "error_code": "refused"}, None),
                   **self.r27_answer()}
        code, index, sessions, _ = self.run_reads(answers)
        self.assertEqual((code, index["stop"]), (1, "blueprint_get_refused"))
        ids = [case["id"] for case in holder.read_cases(synthetic_seed())]
        self.assertEqual([row["id"] for row in index["reads"]], ids[:ids.index("r12-blueprints")])
        self.assertTrue(sessions[0].closed)

    def test_r27_success_or_upstream_success_is_not_confirmed(self):
        answers = {"ha_get_app": ({"success": True}, sse({"success": True}))}
        self.assertEqual(self.run_reads(answers)[1]["stop"], "r27_not_confirmed")
        answers = self.r27_answer(upstream=sse({"success": True}))
        self.assertEqual(self.run_reads(answers)[1]["stop"], "r27_not_confirmed")

    def test_r27_refused_by_engineering_before_upstream_is_confirmed(self):
        code, index, _, _ = self.run_reads(self.r27_answer(upstream=None))
        self.assertEqual((code, index["reads"][-1]["upstream"]), (0, []))
        self.assertTrue(index["reads"][-1]["note_confirmed_by_response"])

    def test_a_session_that_does_not_open_is_a_stop(self):
        def refuse(_endpoint):
            raise calls.CallsError("initialize_failed")
        code, index, _, base = self.run_reads(factory=refuse)
        self.assertEqual((code, index["stop"], index["reads"]),
                         (1, "engineering_session_not_opened", []))
        self.assertFalse((base / "reads" / "_initialize.json").exists())


    def test_health_comes_first_and_is_recorded_without_gating_the_candidate(self):
        answers = {"get_server_health": (health_payload(admission_status="partially_admitted"),
                                         sse({"success": True})), **self.r27_answer()}
        code, index, sessions, base = self.run_reads(answers)
        self.assertEqual((code, index["stop"], index["read_count"]), (0, None, 27))
        self.assertEqual(sessions[0].tools[0], "get_server_health")
        health = index["health"]
        self.assertIsNone(health["control_gate"])
        self.assertFalse(health["response_truncated"])
        self.assertEqual(health["result_class"], "success")
        self.assertEqual(health["upstream_read_gateway"]["admission_status"], "partially_admitted")
        self.assertNotIn("exposed_tools", health["upstream_read_gateway"])
        self.assertEqual(health["lifecycle_provider"]["operational_status"], "unavailable")
        self.assertEqual(health["lifecycle_provider"]["last_failure_category"],
                         "upstream_version_mismatch")
        self.assertNotIn("nested", health["lifecycle_provider"])
        core = health["core_authority"]
        self.assertEqual((core["observed_core_version"], core["initialized"]), ("2026.11.0", True))
        self.assertNotIn("counters", core)
        self.assertEqual(core["release_registry"], {
            "cache_status": "valid", "enabled": True, "freshness_status": "current",
            "refresh_status": "failed", "sequence": 18, "surface_denied": False})
        self.assertEqual(health["upstream_tool_calls"], 1)
        stored = (base / "reads" / holder.HEALTH_FILE).read_bytes()
        self.assertEqual(health["engineering_exchange_sha256"], hashlib.sha256(stored).hexdigest())
        # The health exchange is not attributed to r01.
        self.assertEqual(index["reads"][0]["upstream"][0]["sequence"], 2)

    def test_the_control_gate_passes_on_the_production_values(self):
        code, index, _, _ = self.run_reads(self.r27_answer(), version="8.5.0")
        self.assertEqual((code, index["stop"], index["read_count"]), (0, None, 27))
        self.assertEqual(index["health"]["control_gate"],
                         {"expected": holder.CONTROL_GATE, "passed": True, "differ": []})

    def test_a_control_value_that_differs_stops_before_the_reads(self):
        for field, value in (("upstream_advertised_tool_count", 77),
                             ("reviewed_policy_entry_count", 78),
                             ("version_status", "reviewed_compatible"),
                             ("admission_status", "partially_admitted")):
            with self.subTest(field=field):
                answers = {"get_server_health": (health_payload(**{field: value}), None)}
                code, index, sessions, _ = self.run_reads(answers, version="8.5.0")
                self.assertEqual((code, index["stop"], index["reads"]),
                                 (1, "control_gate_failed", []))
                self.assertEqual(index["health"]["control_gate"]["differ"], [field])
                self.assertEqual(index["health"]["upstream_read_gateway"][field], value)
                self.assertEqual(sessions[0].tools, ["get_server_health"])
                self.assertTrue(sessions[0].closed)

    def test_health_without_a_core_authority_block_records_none(self):
        payload = health_payload()
        del payload["data"]["home_assistant_core_authority"]
        self.assertIsNone(holder.core_authority(payload))
        self.assertIsNone(holder.core_authority(None))
        payload = health_payload()
        payload["data"]["home_assistant_core_authority"]["release_registry"] = "unexpected"
        self.assertIsNone(holder.core_authority(payload)["release_registry"])

    def test_a_truncated_health_response_is_named_and_fails_the_control_gate(self):
        cut = {"success": True, "data": {}, "response_completeness": {"truncated": True}}
        code, index, _, _ = self.run_reads({"get_server_health": (cut, None)}, version="8.5.0")
        self.assertEqual((code, index["stop"]), (1, "control_gate_failed"))
        self.assertTrue(index["health"]["response_truncated"])
        self.assertEqual(index["health"]["control_gate"]["differ"], sorted(holder.CONTROL_GATE))

    def test_a_refused_health_call_fails_the_control_gate(self):
        answers = {"get_server_health": ({"success": False, "error_code": "refused"}, None)}
        code, index, _, _ = self.run_reads(answers, version="8.5.0")
        self.assertEqual((code, index["stop"]), (1, "control_gate_failed"))
        self.assertEqual(index["health"]["control_gate"]["differ"], sorted(holder.CONTROL_GATE))

    def test_ha_search_records_its_variant_and_warning_codes(self):
        search = {"success": True, "metadata": {
            "search_descriptor_variant": "unified",
            "warning_codes": ["search_budget_legacy_path_only"]}}
        answers = {"ha_search": (search, sse({"success": True})), **self.r27_answer()}
        _, index, _, _ = self.run_reads(answers)
        row = next(row for row in index["reads"] if row["tool"] == "ha_search")
        self.assertEqual((row["search_descriptor_variant"], row["warning_codes"]),
                         ("unified", ["search_budget_legacy_path_only"]))
        self.assertTrue(all("search_descriptor_variant" not in row
                            for row in index["reads"] if row["tool"] != "ha_search"))
        copied = sr.read_rows(index)
        self.assertEqual(next(item for item in copied if item["tool"] == "ha_search")
                         ["search_descriptor_variant"], "unified")
        self.assertNotIn("warning_codes", copied[0])
        self.assertIsNone(copied[0]["note"])

    def test_ha_search_without_metadata_records_no_variant(self):
        _, index, _, _ = self.run_reads(self.r27_answer())
        row = next(row for row in index["reads"] if row["tool"] == "ha_search")
        self.assertEqual((row["search_descriptor_variant"], row["warning_codes"]), (None, None))


class UrlAndArgvTests(unittest.TestCase):
    def test_upstream_url_forms(self):
        # The full-catalog suffix is the harness's own STRICT_SUFFIX (ruling 2026-11-16
        # 08:18, T1 item 2); the endpoint-selector guard keeps strict literals out of tests.
        self.assertEqual(sr.upstream_url("9.2.0", 18086),
                         "http://127.0.0.1:18086/synthetic-upstream-secret/mcp" + sr.STRICT_SUFFIX)
        self.assertEqual(sr.upstream_url("8.5.0", 18087),
                         "http://127.0.0.1:18087/synthetic-upstream-secret/mcp")

    def engineering(self, **overrides):
        values = dict(name="e", run_id="run", holder="h", volume="v", image="img:local",
                      upstream=sr.upstream_url("9.2.0", sr.PROXY_PORT))
        values.update(overrides)
        return sr.engineering_argv(**values)

    def test_engineering_argv_posture_and_token_by_name(self):
        argv = self.engineering()
        self.assertEqual(argv[:2], ["docker", "create"])
        for flags in (["--pull", "never"], ["--network", "container:h"], ["--read-only"],
                      ["--cap-drop", "ALL"], ["--security-opt", "no-new-privileges"],
                      ["--memory", "384m"]):
            self.assertTrue(any(argv[i:i + len(flags)] == flags for i in range(len(argv))),
                            flags)
        self.assertIn("type=volume,source=v,target=/data", argv)
        self.assertEqual(argv[argv.index("img:local") - 1], "HA_TOKEN")
        self.assertNotIn("-p", argv)
        self.assertFalse(any(item.startswith("--publish") for item in argv))
        self.assertFalse(any("SUPERVISOR_TOKEN" in item or SYNTHETIC_TOKEN in item
                             for item in argv))
        self.assertIn("UPSTREAM_DASHBOARD_MCP_URL=http://127.0.0.1:18087/"
                      "synthetic-upstream-secret/mcp/catalog/full", argv)
        self.assertIn("HA_URL=http://127.0.0.1:8123", argv)

    def test_engineering_argv_refuses_a_supervisor_token(self):
        with self.assertRaisesRegex(Refusal, "engineering_given_a_supervisor_token"):
            self.engineering(upstream="http://127.0.0.1:1/?SUPERVISOR_TOKEN=x")

    def test_engineering_token_environment_drops_supervisor_token(self):
        with mock.patch.dict(os.environ, {"SUPERVISOR_TOKEN": "synthetic-supervisor"}):
            env = sr.engineering_token_environment(SYNTHETIC_TOKEN)
        self.assertNotIn("SUPERVISOR_TOKEN", env)
        self.assertEqual(env["HA_TOKEN"], SYNTHETIC_TOKEN)

    def test_peer_argv_uses_the_synthetic_secret_path_and_the_flags(self):
        argv = sr.peer_argv("p", "run", "h", "ghcr.io/x@sha256:" + "0" * 64)
        self.assertIn("MCP_SECRET_PATH=/synthetic-upstream-secret/mcp", argv)
        self.assertNotIn("MCP_SECRET_PATH=/mcp", argv)
        self.assertEqual(argv[-1], "ha-mcp-web")
        for name, value in lane.PRODUCTION_FLAGS:
            self.assertIn(f"{name}={value}", argv)
        self.assertEqual(argv[argv.index("--env", len(argv) - 4) + 1], lane.TOKEN_VARIABLE)

    def test_relabel_replaces_the_project_once_and_refuses_otherwise(self):
        argv = lane.idle_holder_argv("h", "run", scratch(self))
        relabeled = sr.relabel(argv)
        self.assertIn("com.docker.compose.project=hamcp371-sr", relabeled)
        self.assertNotIn("com.docker.compose.project=hamcp371-s6", relabeled)
        self.assertEqual(len(relabeled), len(argv))
        with self.assertRaisesRegex(Refusal, "lane_argv_not_reviewed"):
            sr.relabel(["docker", "run"])

    def test_memory_total_stays_within_the_ceiling(self):
        memory = sr.s4.host.memory_bytes
        total = sum(memory(v) for v in (sr.s4.HOLDER_MEMORY, lane.CORE_MEMORY,
                                         sr.s4.PEER_MEMORY, sr.ENGINEERING_MEMORY))
        self.assertLessEqual(total, memory(lane.MEMORY_CEILING))

    def test_options_archive_is_the_ci_options_the_core_key_and_the_committed_seed(self):
        seed = sr.core_seed()
        with tarfile.open(fileobj=io.BytesIO(sr.options_archive(seed))) as archive:
            members = archive.getmembers()
            bodies = {m.name: archive.extractfile(m).read() for m in members}
        self.assertEqual([m.name for m in members], ["options.json", *sr.CORE_SEED_FILES])
        self.assertEqual([(m.mode, m.mtime) for m in members],
                         [(0o644, 0), (0o600, 0), (0o600, 0)])
        options = json.loads(bodies["options.json"])
        self.assertEqual({k: v for k, v in options.items()
                          if not k.startswith("ha_core_release_registry_")},
                         sr.ENGINEERING_OPTIONS)
        self.assertEqual(sr.ENGINEERING_OPTIONS["access_secret"], sr.ENGINEERING_SECRET)
        self.assertFalse(options["upstream_trust_registry_enabled"])
        self.assertIs(options["ha_core_release_registry_enabled"], True)
        self.assertEqual(options["ha_core_release_registry_public_key"],
                         (ROOT / "docs/evidence/hamcp222/core-public-key.txt").read_text(
                             encoding="utf-8").strip())
        record = json.loads(sr.CORE_SEED_RECORD.read_text(encoding="utf-8"))
        for name in sr.CORE_SEED_FILES:
            self.assertEqual(bodies[name], (sr.CORE_SEED_DIR / name).read_bytes())
            self.assertEqual(hashlib.sha256(bodies[name]).hexdigest(),
                             record["files"][name]["sha256"])
            self.assertEqual(record["lane_paths"][name], f"/data/{name}")
        self.assertNotIn("data", sr.seed_summary(seed))
        self.assertEqual(sr.seed_summary(seed)["files"][sr.CORE_SEED_FILES[1]]["path"],
                         "/data/ha-core-release-registry-cache.json.lifecycle")

    def copied_seed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = Path(tmp.name) / "seed"
        directory.mkdir()
        for name in (*sr.CORE_SEED_FILES, "seed-record.json"):
            (directory / name).write_bytes((sr.CORE_SEED_DIR / name).read_bytes())
        return directory

    def test_core_seed_refuses_a_changed_missing_or_unbound_file(self):
        directory = self.copied_seed()
        self.assertEqual(sorted(sr.core_seed(directory / "seed-record.json")["files"]),
                         sorted(sr.CORE_SEED_FILES))
        witness = directory / sr.CORE_SEED_FILES[1]
        witness.write_bytes(witness.read_bytes().replace(b"committed", b"refreshing"))
        with self.assertRaisesRegex(Refusal, "core_seed_mismatch"):
            sr.core_seed(directory / "seed-record.json")
        witness.unlink()
        with self.assertRaisesRegex(Refusal, "core_seed_missing"):
            sr.core_seed(directory / "seed-record.json")
        directory = self.copied_seed()
        other_key = directory / "other-key.txt"
        other_key.write_text("c3ludGhldGljLW5vdC1hLWtleQ==\n", encoding="utf-8")
        with self.assertRaisesRegex(Refusal, "core_seed_mismatch"):
            sr.core_seed(directory / "seed-record.json", other_key)
        record = json.loads((directory / "seed-record.json").read_text(encoding="utf-8"))
        record["lane_paths"][sr.CORE_SEED_FILES[0]] = "/data/renamed.json"
        (directory / "seed-record.json").write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(Refusal, "core_seed_mismatch"):
            sr.core_seed(directory / "seed-record.json")

    def test_the_harness_never_composes_cache_or_witness_content(self):
        # Seed bytes reach the archive only as read from the committed files.
        for module in (sr, holder):
            source = Path(module.__file__).read_text(encoding="utf-8")
            for token in ("authority_journal", '"state"', "schema_version"):
                self.assertNotIn(token, source)


class EngineeringContainerTests(unittest.TestCase):
    image = {"image": "hamcp371-sr-engineering:abc", "config_digest": "sha256:" + "1" * 64}

    def data(self, **changes):
        value = {"Image": self.image["config_digest"], "Config": {
            "Image": self.image["image"], "Env": ["HA_TOKEN=x", "HA_URL=y"]},
            "HostConfig": {"Memory": sr.s4.host.memory_bytes("384m"), "ReadonlyRootfs": True,
                           "NetworkMode": "container:hid"},
            "NetworkSettings": {"Networks": {}, "Ports": {}},
            "Mounts": [{"Type": "volume", "Name": "vol", "Destination": "/data"}],
            "State": {"Running": True}}
        for key, item in changes.items():
            value[key] = item
        return value

    def test_accepts_the_expected_container(self):
        found = sr.verify_engineering(self.data(), "hid", self.image, "vol")
        self.assertEqual(found["environment_names"], ["HA_TOKEN", "HA_URL"])

    def test_refusals(self):
        cases = {
            "running_image_mismatch": {"Image": "sha256:" + "2" * 64},
            "engineering_network_not_holder_namespace": {"HostConfig": {
                "Memory": sr.s4.host.memory_bytes("384m"), "ReadonlyRootfs": True,
                "NetworkMode": "host"}},
            "engineering_mounts_not_the_run_volume": {"Mounts": [
                {"Type": "bind", "Name": None, "Destination": "/data"}]},
            "engineering_token_variable_mismatch": {"Config": {
                "Image": self.image["image"], "Env": ["HA_TOKEN=x", "SUPERVISOR_TOKEN=y"]}},
            "container_publishes_ports": {"NetworkSettings": {
                "Networks": {}, "Ports": {"18100/tcp": [{"HostPort": "18100"}]}}},
            "container_not_running": {"State": {"Running": False}},
        }
        for failure, change in cases.items():
            with self.subTest(failure), self.assertRaisesRegex(Refusal, failure):
                sr.verify_engineering(self.data(**change), "hid", self.image, "vol")


class CatalogAndControlTests(unittest.TestCase):
    def catalog(self, names):
        path = scratch(self) / "peer.json"
        path.write_text(json.dumps({"tool_names_in_wire_order": names}))
        return path

    def test_catalog_check_requires_yaml_read_for_the_candidate_only(self):
        path = self.catalog(["ha_get_state"])
        with self.assertRaisesRegex(Refusal, "peer_catalog_lacks_the_yaml_read"):
            sr.catalog_check(path, "9.2.0")
        found = sr.catalog_check(path, "8.5.0")
        self.assertEqual(found["present"], {"ha_config_get_yaml": False})
        self.assertFalse(found["present_required"])
        for version in ("9.2.0", "8.5.0"):
            with self.subTest(version), self.assertRaisesRegex(
                    Refusal, "peer_catalog_advertises_a_disabled_tool"):
                sr.catalog_check(self.catalog(["ha_config_get_yaml", "ha_write_file"]), version)

    def resolved(self, distributions, paths=None):
        return json.dumps({"distributions": distributions,
                           "core_python_path": paths or [lane.OVERLAY_TARGET]}).encode()

    def wheels(self):
        return {pin["requirement"].split(">=")[0]: {"version": pin["version"],
                                                     "location": lane.OVERLAY_TARGET}
                for pin in lane.N32_WHEELS}

    def test_control_resolved_check(self):
        good = {**self.wheels(), "mcp": {"version": "1.24.0", "location": "/usr/lib"}}
        self.assertIsNone(sr.control_resolved_check(self.resolved(good))[1])
        with_ha_mcp = {**good, "ha-mcp": {"version": "9.2.0", "location": lane.OVERLAY_TARGET}}
        self.assertEqual(sr.control_resolved_check(self.resolved(with_ha_mcp))[1],
                         "control_core_resolves_ha_mcp")
        mcp_overlay = {**good, "mcp": {"version": "1", "location": lane.OVERLAY_TARGET}}
        self.assertEqual(sr.control_resolved_check(self.resolved(mcp_overlay))[1],
                         "overlay_not_resolved_in_core")
        missing = {"mcp": good["mcp"]}
        self.assertEqual(sr.control_resolved_check(self.resolved(missing))[1],
                         "overlay_not_resolved_in_core")
        self.assertEqual(sr.control_resolved_check(b"not json")[1],
                         "overlay_not_resolved_in_core")

    def test_control_requirements_from_the_committed_inventory(self):
        rows = [{"requirement": r, "source": "core image 2.3.0"} for r in
                lane.COMPONENT_REQUIREMENTS["9.2.0"]]
        self.assertEqual(len(sr.control_requirements({"requirements": rows})), 3)
        rows[1]["source"] = "missing"
        with self.assertRaisesRegex(Refusal, "profile_b_not_feasible"):
            sr.control_requirements({"requirements": rows})
        with self.assertRaisesRegex(Refusal, "control_requirements_not_in_inventory"):
            sr.control_requirements({"requirements": rows[1:]})

    def control_component(self):
        lines = sr.CONTROL_TREE_LIST.read_text().splitlines()[1:-1]
        records = []
        for line in lines:
            match = sr.LIST_LINE.fullmatch(line)
            records.append({"path": match.group(4), "size": int(match.group(2)),
                            "sha256": match.group(1)})
        return {"records": records}

    def test_control_tree_equals_the_committed_listing(self):
        found = sr.control_tree(self.control_component())
        self.assertEqual((found["files"], found["equal"]), (54, True))
        changed = self.control_component()
        changed["records"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(Refusal, "control_tree_listing_mismatch"):
            sr.control_tree(changed)
        fewer = self.control_component()
        fewer["records"].pop()
        with self.assertRaisesRegex(Refusal, "control_tree_listing_mismatch"):
            sr.control_tree(fewer)

    def test_control_binding_is_the_reviewed_registry_row(self):
        # The shipped registry holds only 9.2.0 (HAMCP-437), so the lane's
        # default control binding refuses; the reviewed 8.5.0 row is frozen
        # at 0f1adcb5 (tests/fixtures/ha_mcp_8_5_0_control).
        with self.assertRaisesRegex(Refusal, "^control_registry_row_missing$"):
            sr.control_binding()
        bound = sr.control_binding(registry=CONTROL_REGISTRY)
        self.assertEqual(bound["index_digest"], "sha256:e1538bcdadb13a5467bbb8258fba3262"
                                                "a05518585e18115742da638adec7057c")
        self.assertEqual(bound["revision"], "8182c6e6db68b37ae7041dec6dd4ade7b7356fb7")
        self.assertEqual(bound["labels"]["org.opencontainers.image.version"], "8.5.0")
        self.assertEqual(sr.COMPONENT_OF, {"9.2.0": "9.2.0", "8.5.0": "2.2.1"})

    def test_names_are_per_setup_and_the_control_has_no_package(self):
        candidate, control = sr.names("9.2.0"), sr.names("8.5.0")
        self.assertIn("package", candidate)
        self.assertNotIn("package", control)
        self.assertFalse(set(candidate.values()) & set(control.values()))
        self.assertEqual(sr.container_keys(control), ["engineering", "peer", "core", "holder"])


class BuildTests(unittest.TestCase):
    def repository(self, link=False):
        path = scratch(self)
        git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        context = path / sr.CONTEXT_PATH
        context.mkdir()
        (context / "Dockerfile").write_text("FROM scratch\n")
        (context / "run.sh").write_text("#!/bin/sh\n")
        (context / "run.sh").chmod(0o755)
        (context / "pkg").mkdir()
        (context / "pkg" / "a.py").write_text("A = 1\n")
        if link:
            (context / "link").symlink_to("Dockerfile")
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "synthetic"], check=True)

        def read(*args):
            return subprocess.run([*git, *args], check=True, capture_output=True).stdout
        return path, read

    def test_context_is_deterministic_and_keeps_modes(self):
        path, read = self.repository()
        first, record = sr.build_context(f"HEAD:{sr.CONTEXT_PATH}", read)
        os.utime(path / sr.CONTEXT_PATH / "Dockerfile", (1, 1))
        second, again = sr.build_context(f"HEAD:{sr.CONTEXT_PATH}", read)
        self.assertEqual((first, record), (second, again))
        self.assertEqual(record["sha256"], hashlib.sha256(first).hexdigest())
        with tarfile.open(fileobj=io.BytesIO(first)) as archive:
            members = {m.name: m for m in archive.getmembers()}
        self.assertEqual(sorted(members), ["Dockerfile", "pkg/a.py", "run.sh"])
        self.assertEqual(members["run.sh"].mode, 0o755)
        self.assertEqual(members["Dockerfile"].mode, 0o644)
        self.assertEqual({m.mtime for m in members.values()}, {0})

    def test_context_refuses_a_link(self):
        _, read = self.repository(link=True)
        with self.assertRaisesRegex(Refusal, "build_context_entry_not_a_file"):
            sr.build_context(f"HEAD:{sr.CONTEXT_PATH}", read)

    def test_context_refuses_a_changed_object(self):
        _, read = self.repository()

        def tampered(*args):
            body = read(*args)
            return body + b"x" if args[0] == "cat-file" else body
        with self.assertRaisesRegex(Refusal, "build_context_object_mismatch"):
            sr.build_context(f"HEAD:{sr.CONTEXT_PATH}", tampered)

    def test_build_arguments_match_ci(self):
        argv = sr.build_argv("t:1", sr.build_arguments("2.4.2-beta.9", "a" * 40, "T"))
        self.assertEqual(argv, ["docker", "build", "--tag", "t:1",
                                "--build-arg", "BUILD_VERSION=2.4.2-beta.9",
                                "--build-arg", "HAMCP_BUILD_SHA=" + "a" * 40,
                                "--build-arg", "HAMCP_BUILD_TIME=T",
                                "--build-arg", "HAMCP_BUILD_DIRTY=false", "-"])
        self.assertNotIn("--push", argv)
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        for key, _ in sr.build_arguments("v", "r", "t"):
            self.assertIn(f'--build-arg "{key}=', ci)

    def test_config_version_reads_the_committed_version(self):
        text = (ROOT / sr.CONTEXT_PATH / "config.yaml").read_text()
        self.assertRegex(sr.config_version(text), r"^[0-9]+\.[0-9]+\.[0-9]+")
        with self.assertRaisesRegex(Refusal, "engineering_version_unreadable"):
            sr.config_version("name: x\n")

    def test_recorded_image_must_be_unchanged(self):
        record = {"schema": sr.IMAGE_SCHEMA, "pushed": False, "tag": "t:1",
                  "image_id": "sha256:" + "3" * 64, "revision": "r" * 40, "tree": "t" * 40,
                  "context": {"sha256": "c" * 64}}
        path = scratch(self) / "engineering-image.json"
        path.write_text(json.dumps(record))

        def read(*_args):
            return b"t" * 40 + b"\n"
        good = [{"Id": record["image_id"], "Config": {"Labels": {
            "org.opencontainers.image.revision": record["revision"]}}}]
        found = sr.engineering_image(path, inspect=lambda _tag: good, read=read)
        self.assertEqual(found["config_digest"], record["image_id"])
        with self.assertRaisesRegex(Refusal, "engineering_image_not_the_recorded_build"):
            sr.engineering_image(path, inspect=lambda _tag: [{"Id": "sha256:" + "4" * 64}],
                                 read=read)
        with self.assertRaisesRegex(Refusal, "engineering_tree_changed_since_build"):
            sr.engineering_image(path, inspect=lambda _tag: good, read=lambda *_a: b"u" * 40)
        path.write_text(json.dumps({**record, "pushed": True}))
        with self.assertRaisesRegex(Refusal, "engineering_image_record_mismatch"):
            sr.engineering_image(path, inspect=lambda _tag: good, read=read)


if __name__ == "__main__":
    unittest.main()
