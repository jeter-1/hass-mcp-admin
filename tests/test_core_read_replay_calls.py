"""Offline tests of the raw delegated-read calls module (HAMCP-371).

The only peer is a synthetic MCP responder this file starts on 127.0.0.1.  No
container, registry, upstream image or Home Assistant is used and every value
is synthetic.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_read_replay_calls as calls  # noqa: E402
import review_upstream_read_release as review  # noqa: E402

POLICY = "9.2.0"  # HAMCP-437 I2a-D: the shipped row
SEED = {
    "automation_id": "synthetic_automation", "automation_entity": "automation.synthetic",
    "calendar_entity": "calendar.synthetic", "scene_id": "synthetic_scene",
    "script_id": "synthetic_script", "template": "{{ 1 + 1 }}",
    "helper_entity": "input_boolean.synthetic", "statistics_entity": "sensor.synthetic",
    "todo_entity": "todo.synthetic", "zone_id": "synthetic_zone", "search_text": "synthetic",
    "window_start": "2026-01-01T00:00:00-06:00", "window_end": "2026-01-02T00:00:00-06:00",
    "window_start_offset_free": "2026-01-01T00:00:00",
    "window_end_offset_free": "2026-01-02T00:00:00",
}


def planned(*groups):
    return {case["id"]: case for case in calls.plan(POLICY, list(groups), dict(SEED))}


class Peer:
    """A synthetic streamable-HTTP responder that records what it was sent."""

    def __init__(self, *, fail_tool=None):
        self.requests, self.sessions = [], 0
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _reply(self, status, body=None, session=None):
                raw = b"" if body is None else json.dumps(body).encode("utf-8")
                self.send_response(status)
                if body is not None:
                    self.send_header("Content-Type", "application/json")
                if session is not None:
                    self.send_header("Mcp-Session-Id", session)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_DELETE(self):
                peer.requests.append(("DELETE", self.headers.get("Mcp-Session-Id"), None))
                self._reply(200)

            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                session = self.headers.get("Mcp-Session-Id")
                peer.requests.append((message["method"], session, message.get("params")))
                if message["method"] == "initialize":
                    peer.sessions += 1
                    self._reply(200, {"jsonrpc": "2.0", "id": message["id"], "result": {
                        "protocolVersion": "2025-03-26", "capabilities": {},
                        "serverInfo": {"name": "synthetic", "version": "0"}}},
                        session=f"synthetic-session-{peer.sessions}")
                elif message["method"] == "notifications/initialized":
                    self._reply(202)
                elif message["params"]["name"] == fail_tool:
                    self.wfile.write(b"not http\r\n\r\n")
                else:
                    failed = message["params"]["arguments"].get("entity_id", "").endswith(
                        calls.UNKNOWN_ID)
                    self._reply(200, {"jsonrpc": "2.0", "id": message["id"], "result": {
                        "isError": failed,
                        "content": [{"type": "text", "text": "{\"synthetic\": true}"}]}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class CaseTableTests(unittest.TestCase):
    def test_there_is_one_primary_call_for_each_of_the_27_reads(self):
        reads = [case for case in calls.CASES if case["group"] == "reads"]
        self.assertEqual(len(reads), calls.READ_COUNT)
        self.assertEqual(len({case["tool"] for case in reads}), calls.READ_COUNT)
        entries = calls.policy_entries(POLICY)
        for case in reads:
            with self.subTest(tool=case["tool"]):
                self.assertNotEqual(entries[case["tool"]].classification, "unsupported")

    def test_case_ids_are_unique_file_names(self):
        ids = [case["id"] for case in calls.CASES]
        self.assertEqual(len(ids), len(set(ids)))
        for name in ids:
            self.assertRegex(name, r"^[a-z0-9][a-z0-9-]*$")

    def test_no_case_carries_a_credential_or_a_schema_switch(self):
        text = json.dumps(calls.CASES).lower()
        for word in ("token", "password", "secret", "authorization", "include_schema",
                     "describe", "include_subentry_schema"):
            with self.subTest(word=word):
                self.assertNotIn(word, text)

    def test_every_seed_reference_is_one_the_seed_step_records(self):
        names = set()

        def walk(value):
            if isinstance(value, str) and value.startswith(calls.SEED):
                names.add(value[len(calls.SEED):])
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
        for case in calls.CASES:
            walk(case["arguments"])
        self.assertEqual(names, set(SEED))

    def test_the_review_probes_are_the_review_scripts_error_probes_word_for_word(self):
        probes = [case for case in calls.CASES if case["group"] == "review_probes"]
        self.assertEqual(
            {case["id"][len("e-"):].replace("-", "_"): (case["tool"], case["arguments"])
             for case in probes}, review.ERROR_PROBES)
        self.assertEqual({case["kind"] for case in probes}, {"upstream"})
        # Sent as written, and planned from no seed value at all.
        cases = {case["id"]: case for case in calls.plan(POLICY, ["review_probes"], {})}
        self.assertEqual(len(cases), len(review.ERROR_PROBES))
        for name, (tool, arguments) in review.ERROR_PROBES.items():
            with self.subTest(probe=name):
                case = cases["e-" + name.replace("_", "-")]
                self.assertEqual((case["upstream_tool"], case["wire_arguments"]),
                                 (tool, arguments))
                self.assertEqual(case["arguments_built_by"], "harness, sent as written")
        # Without a seed any other group is refused, not sent with a gap.
        with self.assertRaises(calls.CallsError) as raised:
            calls.plan(POLICY, ["review_probes", "errors"], {})
        self.assertEqual(str(raised.exception), "seed_value_missing")


class WireArgumentTests(unittest.TestCase):
    def test_reads_get_the_reviewed_adapter_arguments(self):
        cases = planned("reads")
        self.assertEqual(cases["r01-automation"]["wire_arguments"],
                         {"entity_id": "synthetic_automation"})
        self.assertEqual(cases["r09-helpers"]["wire_arguments"],
                         {"helper_type": "input_boolean", "limit": 5, "describe": False})
        self.assertEqual(cases["r10-template"]["wire_arguments"], {
            "template": "{{ 1 + 1 }}", "timeout": 3, "report_errors": True, "strict": False,
            "variables": None, "condition": None})
        self.assertEqual(cases["r12-blueprints"]["upstream_tool"], "ha_manage_blueprints")
        self.assertEqual(cases["r12-blueprints"]["wire_arguments"],
                         {"action": "list", "domain": "automation"})
        self.assertEqual(cases["r16-history"]["wire_arguments"], {
            "entity_ids": "input_boolean.synthetic", "limit": 5, "summary": False,
            "start_time": SEED["window_start"], "end_time": SEED["window_end"],
            "core_options": None, "include_schema": False})
        self.assertEqual(cases["r18-skill-guide"]["wire_arguments"], {"file": "SKILL.md"})
        self.assertEqual(cases["r26-integrations"]["wire_arguments"], {
            "exact_match": True, "include_options": False, "limit": 200, "offset": 0})
        self.assertEqual(cases["r27-apps"]["wire_arguments"],
                         {"include_stats": False, "source": "installed"})
        for case in cases.values():
            self.assertEqual(case["arguments_built_by"], "adapter code, policy " + POLICY)

    def test_the_schema_switches_stay_off_on_every_planned_call(self):
        for case in planned(*calls.GROUPS).values():
            with self.subTest(case=case["id"]):
                wire = case["wire_arguments"]
                self.assertIsNot(wire.get("include_schema"), True)
                self.assertIsNot(wire.get("describe"), True)
                self.assertNotIn("include_subentry_schema", wire)

    def test_the_offset_free_history_variant_is_a_labelled_raw_probe(self):
        # HAMCP-437 I2a-D: on 9.2.0 Engineering refuses an offset-free time
        # before dispatch; the harness mirrors that refusal and plans the raw
        # probe with the unchecked 9.1 row, as the committed s6 receipts hold.
        from ha_mcp_engineering.providers.upstream_history_9_2 import (
            HistoryTimeOffsetRequired,
        )

        entry = calls.policy_entries(POLICY)["ha_get_history"]
        public = {"entity_ids": "input_boolean.synthetic", "limit": 5,
                  "start_time": SEED["window_start_offset_free"],
                  "end_time": SEED["window_end_offset_free"]}
        with self.assertRaises(HistoryTimeOffsetRequired):
            calls.wire_call(entry, dict(public))
        case = planned("variants")["v16-history-offset-free"]
        self.assertEqual(case["upstream_tool"], "ha_get_history")
        self.assertEqual(case["wire_arguments"], {
            **public, "summary": False, "include_schema": False, "core_options": None})
        self.assertEqual(
            case["arguments_built_by"],
            "adapter code, policy " + POLICY
            + "; refused before dispatch by Engineering history_time_offset_required")
        for other in planned("variants").values():
            if other["id"] != "v16-history-offset-free":
                with self.subTest(case=other["id"]):
                    self.assertEqual(other["arguments_built_by"],
                                     "adapter code, policy " + POLICY)

    def test_a_probe_goes_to_the_peer_as_written(self):
        case = planned("errors")["p3-unknown-argument"]
        self.assertEqual(case["wire_arguments"], {
            "entity_id": "input_boolean.synthetic", "hamcp371_unknown_argument": True})
        self.assertEqual(case["arguments_built_by"], "harness, sent as written")

    def test_the_yaml_read_is_the_closed_reader_shape(self):
        case = planned("yaml")["y-group"]
        self.assertEqual(case["upstream_tool"], "ha_config_get_yaml")
        self.assertEqual(case["wire_arguments"]["file"], "configuration.yaml")
        self.assertEqual(case["wire_arguments"]["yaml_path"], "group")

    def test_refusals_are_fixed_tokens(self):
        with self.assertRaises(calls.CallsError) as raised:
            calls.plan("0.0.0", ["reads"], dict(SEED))
        self.assertEqual(str(raised.exception), "policy_version_not_reviewed")
        with self.assertRaises(calls.CallsError) as raised:
            calls.plan(POLICY, ["reads"], {})
        self.assertEqual(str(raised.exception), "seed_value_missing")
        with self.assertRaises(calls.CallsError) as raised:
            calls.yaml_wire_call("0.0.0", {"yaml_path": "group"})
        self.assertEqual(str(raised.exception), "yaml_binding_not_reviewed")
        with self.assertRaises(calls.CallsError) as raised:
            calls.resolve("$seed.flag", {"flag": True})
        self.assertEqual(str(raised.exception), "seed_value_missing")


class RunTests(unittest.TestCase):
    def run_groups(self, peer, *groups, idle_seconds=7.0):
        slept = []
        with tempfile.TemporaryDirectory() as scratch:
            out = Path(scratch) / "out"
            index = calls.run(peer.endpoint, list(planned(*groups).values()), out,
                              idle_seconds=idle_seconds, sleep=slept.append)
            stored = {path.name: json.loads(path.read_text(encoding="utf-8"))
                      for path in out.iterdir()}
        return index, stored, slept

    def test_every_call_is_stored_raw_with_its_arguments(self):
        with Peer() as peer:
            index, stored, slept = self.run_groups(peer, "errors", "transport")
        self.assertEqual(slept, [7.0])
        self.assertEqual(index["sessions_opened"], 2)
        self.assertEqual(index["session_delete_status"], [200, 200])
        self.assertIs(index["session_header_present"], True)
        rows = {row["id"]: row for row in index["calls"]}
        self.assertEqual(set(stored), {name + ".json" for name in rows} | {"_initialize.json"})
        self.assertIs(rows["p4-absent-state"]["is_error"], True)
        self.assertIs(rows["p3-wrong-type"]["is_error"], False)
        self.assertEqual(rows["t-new-session"]["session"], 1)
        self.assertEqual(rows["t-after-idle"]["idle_seconds_before"], 7.0)
        self.assertEqual(rows["t-after-idle"]["session"], 0)
        record = stored["p4-absent-state.json"]
        self.assertEqual(record["exchange"]["request"]["json"]["params"], {
            "name": "ha_get_state", "arguments": {"entity_id": "sensor." + calls.UNKNOWN_ID}})
        self.assertIsNone(record["exchange_failure"])
        # The peer saw the same session for the shared calls and a new one once.
        sent = [(method, session) for method, session, _ in peer.requests]
        self.assertEqual(sent.count(("initialize", None)), 2)
        self.assertIn(("tools/call", "synthetic-session-2"), sent)
        self.assertEqual(sent[-2:], [("DELETE", "synthetic-session-1"),
                                     ("DELETE", "synthetic-session-2")])
        # No index row repeats payload content.
        self.assertNotIn("synthetic\\\": true", json.dumps(index))

    def test_a_call_the_peer_breaks_is_a_recorded_result_and_the_run_continues(self):
        with Peer(fail_tool="ha_get_state") as peer:
            index, stored, _ = self.run_groups(peer, "errors")
        rows = {row["id"]: row for row in index["calls"]}
        self.assertEqual(rows["p4-absent-state"]["exchange_failure"], "peer_exchange_failed")
        self.assertIsNone(rows["p4-absent-state"]["http_status"])
        self.assertIsNone(stored["p4-absent-state.json"]["exchange"])
        self.assertIsNone(rows["p4-absent-entity"]["exchange_failure"])
        self.assertEqual(rows["p4-absent-entity"]["http_status"], 200)

    def test_main_sends_the_review_probes_with_no_seed_file(self):
        with Peer() as peer, tempfile.TemporaryDirectory() as scratch:
            out = Path(scratch) / "out"
            code = calls.main(["--endpoint", peer.endpoint, "--out", str(out),
                               "--policy", POLICY, "--groups", "review_probes"])
            index = json.loads((out / "_index.json").read_text(encoding="utf-8"))
            names = sorted(path.name for path in out.iterdir())
        self.assertEqual(code, 0)
        self.assertEqual([row["id"] for row in index["calls"]], [
            "e-invalid-search", "e-missing-state", "e-missing-automation",
            "e-missing-registry-entity"])
        self.assertEqual(names, sorted(["_index.json", "_initialize.json"]
                                       + [row["file"] for row in index["calls"]]))
        sent = [params for method, _session, params in peer.requests
                if method == "tools/call"]
        self.assertEqual([(item["name"], item["arguments"]) for item in sent],
                         list(review.ERROR_PROBES.values()))

    def test_main_without_a_seed_file_refuses_a_group_that_needs_one(self):
        with tempfile.TemporaryDirectory() as scratch:
            out = Path(scratch) / "out"
            with self.assertRaises(calls.CallsError) as raised:
                calls.main(["--endpoint", "http://127.0.0.1:9/mcp", "--out", str(out),
                            "--policy", POLICY, "--groups", "reads"])
            self.assertFalse(out.exists())
        self.assertEqual(str(raised.exception), "seed_value_missing")

    def test_an_existing_output_directory_is_never_reused(self):
        with Peer() as peer, tempfile.TemporaryDirectory() as scratch:
            with self.assertRaises(FileExistsError):
                calls.run(peer.endpoint, [], Path(scratch), idle_seconds=0)


if __name__ == "__main__":
    unittest.main()
