"""SR live-socket replay, the half that runs inside the lane's holder (HAMCP-371).

Two commands, standard library and this checkout's calls module only:

``proxy``  a recording pass-through between Engineering and the ha-mcp peer
           on loopback.  Every exchange is forwarded unchanged (status,
           headers and body, streamed) and stored: the request body, the
           response body as upstream sent it, and one record naming both by
           sha256.  Nothing is filtered, repaired or interpreted.

``reads``  one Engineering session that first calls get_server_health (the
           8.5.0 control gate) and then makes the 27 admitted reads with
           their public tool names and arguments.  Each read's receipt row
           names the call, Engineering's result class and withheld counts,
           and the upstream ``tools/call`` exchanges the proxy recorded while
           it ran, with the sha256 of each raw upstream response.

Peer and Engineering output is untrusted data: it is stored and counted,
never evaluated.  No token reaches this process; the reads use Engineering's
synthetic access-secret path.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import core_read_replay_calls as calls  # noqa: E402

RECORD_FORMAT_VERSION = 1
# Hop-by-hop headers are the connection's own and are not forwarded.
HOP_HEADERS = frozenset(("connection", "keep-alive", "proxy-connection", "te", "trailer",
                         "transfer-encoding", "upgrade", "host"))
# Header values that could carry a credential are stored as their length only.
REDACTED_HEADERS = frozenset(("authorization", "cookie", "set-cookie", "proxy-authorization"))
NO_BODY_STATUS = frozenset((204, 304))
UPSTREAM_SECONDS = 900
CHUNK = 65536
RECORD_WAIT_SECONDS = 60
SEQUENCE = re.compile(r"([0-9]{6})\.request")

# The 27 reads, in the calls module's order (ruling 2026-11-15 17:50).
READ_GROUP = "reads"
READ_COUNT = 27
# r12 reads one default Core blueprint in get mode; a refusal stops the run.
BLUEPRINT_CASE = "r12-blueprints"
BLUEPRINT_GET = {"domain": "automation", "path": "homeassistant/motion_light.yaml"}
# r27 has no Supervisor to read in the local lane.
APP_CASE = "r27-apps"
APP_NOTE = "not replayable in the local lane (no Supervisor)"
WITHHELD_KEY = re.compile(r"[a-z_]{1,64}(_withheld|_dropped_by_policy|_replaced)")
WITHHELD_DEPTH = 3
# One get_server_health call before the reads (ruling 2026-11-15 20:17 CST). The
# 8.5.0 control must report the production values Josh relayed (2026-11-14 22:18
# CST); any difference is recorded and stops the setup before its reads.
HEALTH_TOOL = "get_server_health"
HEALTH_FILE = "_health.json"
HEALTH_FIELDS = ("upstream_advertised_tool_count", "reviewed_policy_entry_count",
                 "version_status", "admission_status", "catalog_comparison_status",
                 "upstream_server_version", "reviewed_upstream_version")
CONTROL_VERSION = "8.5.0"
CONTROL_GATE = {"upstream_advertised_tool_count": 80, "reviewed_policy_entry_count": 77,
                "version_status": "reviewed_exact", "admission_status": "admitted_exact"}
SCALAR_CHARS = 128
# D6 (rulings 2026-11-15 22:26 CST): each ha_search result records its variant.
SEARCH_TOOL = "ha_search"


class Stop(Exception):
    """A read the contract makes a stop; the message is a fixed token."""


# --- The recording proxy -----------------------------------------------------


def stored_headers(pairs):
    return [[name, f"<{len(value)} bytes withheld>" if name.lower() in REDACTED_HEADERS
             else value] for name, value in pairs]


def request_summary(body):
    """The JSON-RPC method, id and tool name of one request body, when it is one message."""
    try:
        value = json.loads(body.decode("utf-8")) if body else None
    except (UnicodeDecodeError, ValueError):
        return {"jsonrpc_method": None, "jsonrpc_id": None, "tool": None, "decodable": False}
    if not isinstance(value, dict):
        return {"jsonrpc_method": None, "jsonrpc_id": None, "tool": None,
                "decodable": value is None}
    params = value.get("params") if isinstance(value.get("params"), dict) else {}
    method = value.get("method") if isinstance(value.get("method"), str) else None
    tool = params.get("name") if method == "tools/call" and isinstance(
        params.get("name"), str) else None
    return {"jsonrpc_method": method, "jsonrpc_id": value.get("id"), "tool": tool,
            "decodable": True}


class Recorder:
    def __init__(self, out_dir, target_port):
        self.out_dir, self.target_port = Path(out_dir), target_port
        self.lock, self.sequence = threading.Lock(), 0

    def next(self):
        with self.lock:
            self.sequence += 1
            return self.sequence

    def write(self, name, raw):
        partial = self.out_dir / (name + ".partial")
        partial.write_bytes(raw)
        os.replace(partial, self.out_dir / name)


def handler_for(recorder):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # The record is the log.
            return

        def do_GET(self):
            self.forward()

        def do_POST(self):
            self.forward()

        def do_DELETE(self):
            self.forward()

        def forward(self):
            sequence = recorder.next()
            stem = f"{sequence:06d}"
            started = time.monotonic()
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            recorder.write(stem + ".request", body)
            record = {"record_format_version": RECORD_FORMAT_VERSION, "sequence": sequence,
                      "method": self.command, "path": self.path,
                      "request_headers": stored_headers(self.headers.items()),
                      "request_bytes": len(body),
                      "request_sha256": hashlib.sha256(body).hexdigest(),
                      **request_summary(body)}
            digest, total, outcome = hashlib.sha256(), 0, "complete"
            try:
                upstream = http.client.HTTPConnection("127.0.0.1", recorder.target_port,
                                                      timeout=UPSTREAM_SECONDS)
                upstream.request(self.command, self.path, body=body or None,
                                 headers={name: value for name, value in self.headers.items()
                                          if name.lower() not in HOP_HEADERS})
                response = upstream.getresponse()
            except OSError:
                record.update(outcome="upstream_unreachable", status=None)
                recorder.write(stem + ".json", json.dumps(record, sort_keys=True).encode())
                self.send_error(502)
                return
            headers = [(name, value) for name, value in response.getheaders()
                       if name.lower() not in HOP_HEADERS]
            bodyless = self.command == "HEAD" or response.status in NO_BODY_STATUS
            fixed = response.getheader("Content-Length")
            record.update(status=response.status, response_headers=stored_headers(headers))
            with (recorder.out_dir / (stem + ".response")).open("wb") as stored:
                try:
                    self.send_response_only(response.status, response.reason)
                    for name, value in headers:
                        if name.lower() != "content-length" or fixed is not None:
                            self.send_header(name, value)
                    if not bodyless and fixed is None:
                        self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while not bodyless:
                        chunk = response.read1(CHUNK)
                        if not chunk:
                            break
                        stored.write(chunk)
                        digest.update(chunk)
                        total += len(chunk)
                        if fixed is None:
                            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                        else:
                            self.wfile.write(chunk)
                        self.wfile.flush()
                    if not bodyless and fixed is None:
                        self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    outcome = "client_closed"
                except OSError:
                    outcome = "upstream_stream_failed"
                finally:
                    upstream.close()
            record.update(outcome=outcome, response_bytes=total,
                          response_sha256=digest.hexdigest(),
                          seconds=round(time.monotonic() - started, 3))
            recorder.write(stem + ".json", json.dumps(record, sort_keys=True).encode())
            if outcome != "complete":
                self.close_connection = True

    return Handler


def proxy(args):
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=False)
    recorder = Recorder(out_dir, args.target)
    server = ThreadingHTTPServer(("127.0.0.1", args.listen), handler_for(recorder))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(args.seconds)
    server.shutdown()
    return 0


# --- The reads through Engineering ------------------------------------------


def read_cases(seed):
    """The 27 reads with resolved public arguments; r12 in get mode."""
    rows = []
    for case in calls.CASES:
        if case["group"] != READ_GROUP:
            continue
        arguments = (dict(BLUEPRINT_GET) if case["id"] == BLUEPRINT_CASE
                     else calls.resolve(case["arguments"], seed))
        rows.append({"id": case["id"], "tool": case["tool"], "arguments": arguments})
    if len(rows) != READ_COUNT or [row["id"] for row in rows][-1] != APP_CASE:
        raise Stop("read_cases_not_the_27")
    return rows


def tool_payload(result):
    """Engineering's tool payload: structured content, else the first JSON text item."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and "result" not in structured:
        return structured
    for item in result.get("content") or []:
        text = item.get("text") if isinstance(item, dict) else None
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    return None


def result_class(record):
    """``success``, ``error:<code>``, ``jsonrpc_error:<code>`` or ``undecodable``."""
    response = (record or {}).get("response")
    if not isinstance(response, dict):
        return "undecodable", None
    if isinstance(response.get("error"), dict):
        return f"jsonrpc_error:{response['error'].get('code')}", None
    result = response.get("result")
    if not isinstance(result, dict):
        return "undecodable", None
    payload = tool_payload(result)
    if not isinstance(payload, dict):
        return "undecodable", None
    if payload.get("success") is True and result.get("isError") is not True:
        return "success", payload
    if payload.get("success") is False:
        code = payload.get("error_code")
        if not isinstance(code, str):
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            code = error.get("code") if isinstance(error.get("code"), str) else "unnamed"
        return "error:" + code[:64], payload
    return "undecodable", payload


def withheld_counts(payload, depth=WITHHELD_DEPTH, prefix=""):
    """Every withheld, dropped or replaced counter in the payload, by dotted key; no names."""
    found = {}
    if not isinstance(payload, dict) or depth == 0:
        return found
    for key in sorted(payload):
        if not isinstance(key, str):
            continue
        value, path = payload[key], prefix + key
        if WITHHELD_KEY.fullmatch(key):
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                found[path] = value
            elif isinstance(value, dict) and all(
                    isinstance(item, int) and not isinstance(item, bool)
                    for item in value.values()):
                found[path] = {str(name): value[name] for name in sorted(value)}
        elif isinstance(value, dict):
            found.update(withheld_counts(value, depth - 1, path + "."))
    return found


def scalars(value):
    """The bounded scalar items of one dict, by key; nested values are left out."""
    if not isinstance(value, dict):
        return None
    return {str(key): (item[:SCALAR_CHARS] if isinstance(item, str) else item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if item is None or isinstance(item, (str, int, float, bool))}


def health_fields(payload):
    """The admission fields and the lifecycle provider status of one health payload."""
    data = payload.get("data") if isinstance(payload, dict) else None
    data = data if isinstance(data, dict) else {}
    gateway = data.get("upstream_read_gateway")
    gateway = gateway if isinstance(gateway, dict) else {}
    lifecycle = data.get("governance")
    for key in ("operational_administration", "lifecycle_provider"):
        lifecycle = lifecycle.get(key) if isinstance(lifecycle, dict) else None
    return scalars({field: gateway.get(field) for field in HEALTH_FIELDS}), scalars(lifecycle)


def control_gate(version, klass, fields):
    """None outside the control; else whether every relayed value matched, and which did not."""
    if version != CONTROL_VERSION:
        return None
    differ = sorted(key for key, value in CONTROL_GATE.items()
                    if klass != "success" or fields.get(key) != value)
    return {"expected": dict(CONTROL_GATE), "passed": not differ, "differ": differ}


def search_attribution(row, payload):
    """The descriptor variant and warning codes Engineering put on one ha_search result."""
    if row["tool"] != SEARCH_TOOL:
        return row
    metadata = payload.get("metadata") if isinstance(payload, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    variant = metadata.get("search_descriptor_variant")
    codes = metadata.get("warning_codes")
    row["search_descriptor_variant"] = variant[:SCALAR_CHARS] if isinstance(variant, str) else None
    row["warning_codes"] = ([code[:SCALAR_CHARS] for code in codes if isinstance(code, str)]
                            if isinstance(codes, list) else None)
    return row


def health_step(session, args, out_dir, proxy_dir, sleep):
    """One get_server_health call, stored like a read; the control gate stops on a difference."""
    after = max(sequences(proxy_dir), default=0)
    record, failure = None, None
    try:
        record = session.call(HEALTH_TOOL, {})
    except (calls.raw.ObservationError, calls.CallsError) as exc:
        failure = str(exc)
    stored = calls.raw.serialize({"tool": HEALTH_TOOL, "arguments": {}, "exchange": record,
                                  "exchange_failure": failure})
    (out_dir / HEALTH_FILE).write_bytes(stored)
    klass, payload = result_class(record) if failure is None else ("exchange_failed", None)
    fields, lifecycle = health_fields(payload)
    upstream, other = upstream_rows(new_records(proxy_dir, after, sleep=sleep))
    health = {"tool": HEALTH_TOOL, "arguments": {}, "file": HEALTH_FILE,
              "engineering_exchange_sha256": hashlib.sha256(stored).hexdigest(),
              "exchange_failure": failure, "result_class": klass,
              "upstream_read_gateway": fields, "lifecycle_provider": lifecycle,
              "response_truncated": isinstance(payload, dict) and isinstance(
                  payload.get("response_completeness"), dict)
              and payload["response_completeness"].get("truncated") is True,
              "upstream_tool_calls": len(upstream), "other_upstream_traffic": other,
              "control_gate": control_gate(args.version, klass, fields)}
    return health


def sequences(proxy_dir):
    return sorted(int(match.group(1)) for match in (
        SEQUENCE.fullmatch(path.name) for path in Path(proxy_dir).iterdir()) if match)


def new_records(proxy_dir, after, wait=RECORD_WAIT_SECONDS, sleep=time.sleep):
    """The proxy records of exchanges that started after ``after``, once each is complete."""
    proxy_dir, deadline = Path(proxy_dir), time.monotonic() + wait
    while True:
        started = [number for number in sequences(proxy_dir) if number > after]
        done = [proxy_dir / f"{number:06d}.json" for number in started]
        if all(path.is_file() for path in done):
            return [json.loads(path.read_text(encoding="utf-8")) for path in done]
        if time.monotonic() > deadline:
            raise Stop("proxy_record_incomplete")
        sleep(0.2)


def upstream_rows(records):
    """The upstream ``tools/call`` exchanges of one read, by sha256; other traffic counted."""
    rows = [{"sequence": record.get("sequence"), "tool": record.get("tool"),
             "status": record.get("status"), "outcome": record.get("outcome"),
             "request_sha256": record.get("request_sha256"),
             "raw_response_sha256": record.get("response_sha256"),
             "raw_response_bytes": record.get("response_bytes")}
            for record in records if record.get("jsonrpc_method") == "tools/call"]
    other = sorted({f"{record.get('method')} {record.get('jsonrpc_method')}"
                    for record in records if record.get("jsonrpc_method") != "tools/call"})
    return rows, other


def wire_arguments(proxy_dir, rows):
    """The arguments Engineering sent upstream, read from each stored request body."""
    built = []
    for row in rows:
        raw = (Path(proxy_dir) / f"{row['sequence']:06d}.request").read_bytes()
        params = (json.loads(raw.decode("utf-8")) or {}).get("params") or {}
        built.append(params.get("arguments"))
    return built


def upstream_is_error(proxy_dir, row):
    """Whether one stored raw upstream response is an error result (or no result)."""
    raw = (Path(proxy_dir) / f"{row['sequence']:06d}.response").read_bytes()
    text = raw.decode("utf-8", "replace")
    messages = []
    for line in text.splitlines():
        if line.startswith("data:"):
            messages.append(line[5:].strip())
    for item in messages or [text]:
        try:
            value = json.loads(item)
        except ValueError:
            continue
        if isinstance(value, dict) and ("error" in value or "result" in value):
            if "error" in value:
                return True
            result = value["result"] if isinstance(value["result"], dict) else {}
            payload = tool_payload(result)
            return result.get("isError") is True or (
                isinstance(payload, dict) and payload.get("success") is False)
    return True


def check_contract(row, proxy_dir):
    """r12 must succeed in get mode; r27 must be an error on both sides."""
    if row["id"] == BLUEPRINT_CASE and row["result_class"] != "success":
        raise Stop("blueprint_get_refused")
    if row["id"] == APP_CASE:
        confirmed = (row["result_class"] != "success"
                     and all(upstream_is_error(proxy_dir, item) for item in row["upstream"]))
        row.update(note=APP_NOTE, note_confirmed_by_response=confirmed)
        if not confirmed:
            raise Stop("r27_not_confirmed")
    return row


def reads(args, session_factory=calls.Session, sleep=time.sleep):
    out_dir, proxy_dir = Path(args.out), Path(args.proxy_dir)
    out_dir.mkdir(parents=True, exist_ok=False)
    seed = json.loads(Path(args.seed).read_text(encoding="utf-8"))
    cases = read_cases(seed)
    rows, stop, health = [], None, None
    try:
        session = session_factory(args.endpoint)
    except (calls.raw.ObservationError, calls.CallsError):
        index = {"version": args.version, "reads": [], "read_count": 0,
                 "stop": "engineering_session_not_opened"}
        (out_dir / "_index.json").write_bytes(calls.raw.serialize(index))
        return 1
    (out_dir / "_initialize.json").write_bytes(calls.raw.serialize(session.initialize))
    try:
        health = health_step(session, args, out_dir, proxy_dir, sleep)
        if health["control_gate"] is not None and not health["control_gate"]["passed"]:
            raise Stop("control_gate_failed")
        for case in cases:
            after = max(sequences(proxy_dir), default=0)
            record, failure = None, None
            try:
                record = session.call(case["tool"], case["arguments"])
            except (calls.raw.ObservationError, calls.CallsError) as exc:
                failure = str(exc)
            stored = calls.raw.serialize({"case": case, "exchange": record,
                                          "exchange_failure": failure})
            (out_dir / (case["id"] + ".json")).write_bytes(stored)
            klass, payload = result_class(record) if failure is None else (
                "exchange_failed", None)
            upstream, other = upstream_rows(new_records(proxy_dir, after, sleep=sleep))
            row = {"id": case["id"], "version": args.version, "tool": case["tool"],
                   "arguments": case["arguments"], "file": case["id"] + ".json",
                   "engineering_exchange_sha256": hashlib.sha256(stored).hexdigest(),
                   "exchange_failure": failure, "result_class": klass,
                   "withheld": withheld_counts(payload), "upstream": upstream,
                   "upstream_wire_arguments": wire_arguments(proxy_dir, upstream),
                   "other_upstream_traffic": other}
            rows.append(check_contract(search_attribution(row, payload), proxy_dir))
    except Stop as exc:
        stop = str(exc)
    finally:
        try:
            session.close()
        except calls.raw.ObservationError:
            pass
        index = {"version": args.version, "endpoint_form": "engineering access-secret path",
                 "health": health, "reads": rows, "stop": stop, "read_count": len(rows)}
        (out_dir / "_index.json").write_bytes(calls.raw.serialize(index))
    return 1 if stop else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("proxy")
    sub.add_argument("--listen", type=int, required=True)
    sub.add_argument("--target", type=int, required=True)
    sub.add_argument("--out", required=True)
    sub.add_argument("--seconds", type=float, required=True)
    sub = commands.add_parser("reads")
    sub.add_argument("--endpoint", required=True)
    sub.add_argument("--seed", required=True)
    sub.add_argument("--proxy-dir", required=True)
    sub.add_argument("--out", required=True)
    sub.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    return proxy(args) if args.command == "proxy" else reads(args)


if __name__ == "__main__":
    raise SystemExit(main())
