"""Offline tests of the raw MCP observer (HAMCP-371, P1 and P2).

The peer is a disposable loopback HTTP server with synthetic data, started
and stopped by each test.  No Docker, external network or Home Assistant.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mcp_raw_observation as observer  # noqa: E402

SESSION = "synthetic-session-not-a-secret"
INSTRUCTIONS = "synthetic instructions"


def tool(name: str) -> dict:
    return {
        "name": name,
        "description": "synthetic " + name,
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    }


class Peer:
    """A scripted streamable-HTTP peer; ``mode`` selects its behaviour."""

    def __init__(self, mode: str = "json") -> None:
        self.mode = mode
        self.requests: list[dict] = []
        peer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                return

            def _send(self, status: int, body: bytes, content_type: str | None,
                      extra: dict | None = None) -> None:
                self.send_response(status)
                if content_type:
                    self.send_header("Content-Type", content_type)
                for name, value in (extra or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def do_DELETE(self) -> None:
                peer.requests.append({"method": "DELETE", "headers": dict(self.headers)})
                self._send(200, b"", None)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                message = json.loads(self.rfile.read(length))
                peer.requests.append({"method": "POST", "headers": dict(self.headers),
                                      "json": message})
                if "id" not in message:
                    self._send(202, b"", None)
                    return
                status, reply = peer.reply(message)
                extra = {"Mcp-Session-Id": SESSION} if message["method"] == "initialize" else {}
                if isinstance(reply, bytes):
                    self._send(status, reply, "application/json", extra)
                elif peer.mode == "sse":
                    progress = {"jsonrpc": "2.0", "method": "notifications/message",
                                "params": {"level": "info", "data": "synthetic"}}
                    body = (": synthetic comment\r\n"
                            "event: message\r\ndata: " + json.dumps(progress) + "\r\n\r\n"
                            "event: message\r\nid: 7\r\ndata: " + json.dumps(reply) + "\r\n\r\n"
                            "event: message\r\ndata: {\"never\": \"read\"}\r\n\r\n")
                    self._send(status, body.encode("utf-8"), "text/event-stream", extra)
                else:
                    self._send(status, json.dumps(reply).encode("utf-8"),
                               "application/json; charset=utf-8", extra)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def reply(self, message: dict):
        method, identifier = message["method"], message["id"]
        if method == "initialize":
            if self.mode == "initialize_error":
                return 200, {"jsonrpc": "2.0", "id": identifier,
                             "error": {"code": -32602, "message": "synthetic"}}
            return 200, {"jsonrpc": "2.0", "id": identifier, "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": True}, "experimental": {}},
                "serverInfo": {"name": "synthetic-peer", "version": "0.0.0"},
                "instructions": INSTRUCTIONS}}
        cursor = message["params"].get("cursor")
        if self.mode == "raw":
            return 200, b'{"jsonrpc":"2.0","id":%d,"result":{"tools":[],"tools":[]}}' % identifier
        if self.mode == "oversize":
            return 200, {"jsonrpc": "2.0", "id": identifier, "result": {
                "tools": [tool("ha_big")], "padding": "x" * 4096}}
        if self.mode == "repeat_cursor":
            return 200, {"jsonrpc": "2.0", "id": identifier,
                         "result": {"tools": [], "nextCursor": "again"}}
        if self.mode == "duplicate_tool":
            return 200, {"jsonrpc": "2.0", "id": identifier,
                         "result": {"tools": [tool("ha_one"), tool("ha_one")]}}
        if self.mode == "http_error":
            return 500, {"jsonrpc": "2.0", "id": identifier, "result": {"tools": []}}
        if cursor is None:
            return 200, {"jsonrpc": "2.0", "id": identifier,
                         "result": {"tools": [tool("ha_two"), tool("ha_one")],
                                    "nextCursor": "page-2"}}
        return 200, {"jsonrpc": "2.0", "id": identifier, "result": {"tools": [tool("ha_three")]}}

    def __enter__(self) -> "Peer":
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/mcp"


class ObserveTests(unittest.TestCase):
    def test_json_peer_is_recorded_whole_and_in_wire_order(self) -> None:
        with Peer() as peer:
            observation = observer.observe(peer.endpoint)
            requests = list(peer.requests)
        self.assertEqual(observation["observation_format_version"], 1)
        self.assertEqual(observation["negotiated_protocol_version"], "2025-03-26")
        self.assertTrue(observation["session_header_present"])
        result = observation["initialize"]["response"]["result"]
        self.assertEqual(result["instructions"], INSTRUCTIONS)
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": True},
                                                  "experimental": {}})
        # The raw body is retained next to the parsed message.
        self.assertEqual(json.loads(observation["initialize"]["body_text"]),
                         observation["initialize"]["response"])
        self.assertIn(["Mcp-Session-Id", SESSION], observation["initialize"]["response_headers"])
        self.assertEqual(observation["tool_names_in_wire_order"], ["ha_two", "ha_one", "ha_three"])
        self.assertEqual(observation["tool_count"], 3)
        self.assertEqual(len(observation["tools_list_pages"]), 2)
        self.assertEqual(
            observation["tools_list_pages"][0]["response"]["result"]["tools"][0], tool("ha_two"))
        self.assertEqual(observation["session_delete"]["status"], 200)
        self.assertEqual([item["method"] for item in requests],
                         ["POST", "POST", "POST", "POST", "DELETE"])
        self.assertEqual(requests[0]["json"]["params"]["protocolVersion"],
                         observer.REQUESTED_PROTOCOL_VERSION)
        self.assertNotIn("Mcp-Session-Id", requests[0]["headers"])
        for later in requests[1:]:
            self.assertEqual(later["headers"]["Mcp-Session-Id"], SESSION)
            self.assertEqual(later["headers"]["Mcp-Protocol-Version"], "2025-03-26")
        self.assertEqual(requests[3]["json"]["params"], {"cursor": "page-2"})
        # The request record names the headers sent, never the session value.
        self.assertNotIn(SESSION, json.dumps(observation["tools_list_pages"][0]["request"]))

    def test_event_stream_peer_stops_at_the_matching_response(self) -> None:
        with Peer("sse") as peer:
            observation = observer.observe(peer.endpoint)
        page = observation["tools_list_pages"][0]
        self.assertEqual(page["content_type"], "text/event-stream")
        self.assertEqual([message.get("method") for message in page["messages"]],
                         ["notifications/message", None])
        self.assertEqual(page["response"]["result"]["nextCursor"], "page-2")
        self.assertIn("synthetic comment", page["body_text"])
        self.assertNotIn("never", page["body_text"])
        self.assertEqual(observation["tool_count"], 3)

    def test_serialization_is_stable_ascii(self) -> None:
        with Peer() as peer, tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "observation.json"
            self.assertEqual(observer.main(["--endpoint", peer.endpoint,
                                            "--output", str(output)]), 0)
            raw = output.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        raw.decode("ascii")
        self.assertEqual(observer.serialize(json.loads(raw)), raw)


class RefusalTests(unittest.TestCase):
    def test_only_an_exact_loopback_http_endpoint_is_accepted(self) -> None:
        for endpoint in (
            "http://192.168.1.10:8123/mcp",
            "http://localhost:18086/mcp",
            "http://homeassistant.local:8123/mcp",
            "https://127.0.0.1:18086/mcp",
            "http://127.0.0.1/mcp",
            "http://user:synthetic@127.0.0.1:18086/mcp",
            "http://127.0.0.1:18086/mcp?x=1",
            "http://127.0.0.1:18086",
            "http://127.0.0.1:notaport/mcp",
            "http://[::1]:18086/mcp",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(observer.ObservationError) as caught:
                    observer.observe(endpoint, connect=self.fail)
                self.assertIn(str(caught.exception),
                              {"endpoint_not_loopback_http", "endpoint_port_invalid"})

    def assert_refused(self, mode: str, reason: str) -> None:
        with Peer(mode) as peer:
            with self.assertRaises(observer.ObservationError) as caught:
                observer.observe(peer.endpoint)
        self.assertEqual(str(caught.exception), reason)

    def test_peer_failures_are_fixed_tokens(self) -> None:
        self.assert_refused("initialize_error", "initialize_failed")
        self.assert_refused("http_error", "tools_list_failed")
        self.assert_refused("repeat_cursor", "catalog_cursor_invalid")
        self.assert_refused("duplicate_tool", "catalog_duplicate_tool_name")
        self.assert_refused("raw", "response_json_duplicate_key")

    def test_body_bound_is_enforced(self) -> None:
        original = observer.MAX_BODY_BYTES
        observer.MAX_BODY_BYTES = 2048
        try:
            self.assert_refused("oversize", "response_body_exceeds_bound")
        finally:
            observer.MAX_BODY_BYTES = original

    def test_unreachable_peer_is_reported_not_raised_raw(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with self.assertRaises(observer.ObservationError) as caught:
            observer.observe(f"http://127.0.0.1:{port}/mcp")
        self.assertEqual(str(caught.exception), "peer_exchange_failed")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "observation.json"
            self.assertEqual(observer.main(["--endpoint", f"http://127.0.0.1:{port}/mcp",
                                            "--output", str(output)]), 1)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
