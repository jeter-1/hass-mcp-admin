"""Synthetic loopback ha-mcp peers for the HAMCP-319 no-redirect matrix.

Offline and deterministic: two ``ThreadingHTTPServer`` peers on 127.0.0.1.
The *origin* serves a minimal MCP streamable-HTTP endpoint on exactly one
bound path and query; every other path on the origin, and every request to
the separate *destination* peer, is counted as a redirect-destination hit.
Counters are kept per peer and per request type.

One request type can be answered with a 3xx (``inject``). Every bound URL and
every ``Location`` carries its own synthetic canary so tests can assert that
no sink echoes them.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
import http.server
import json
import threading
from typing import Callable, Iterator


URL_PATH_CANARY = "syntheticpathcanary7Q2"
URL_QUERY_CANARY = "syntheticquerycanary7Q2"
URL_USER_CANARY = "syntheticusercanary7Q2"
URL_PASS_CANARY = "syntheticpasscanary7Q2"
LOCATION_CANARY = "syntheticlocationcanary9K4"
ALL_CANARIES = (
    URL_PATH_CANARY,
    URL_QUERY_CANARY,
    URL_USER_CANARY,
    URL_PASS_CANARY,
    LOCATION_CANARY,
)

# Request types the matrix injects at. ``get_resumption`` and
# ``notifications/cancelled`` are not emitted by Engineering under the pinned
# mcp==1.30.0 and are driven through the SDK session directly.
REQUEST_TYPES = (
    "initialize",
    "notifications/initialized",
    "notifications/cancelled",
    "tools/list:first",
    "tools/list:cursor",
    "tools/call",
    "get_stream",
    "get_resumption",
    "get_reconnect",
    "delete",
)
GET_POST_STATUSES = (301, 302, 303, 307, 308)
DELETE_STATUSES = (307, 308)
LOCATION_KINDS = (
    "same_origin_default",
    "same_origin_readonly_full",
    "same_origin_search",
    "same_path_slash",
    "cross_origin",
)

PEER_VERSIONS = ("8.5.0", "9.1.0")


@dataclass(frozen=True)
class Injection:
    request_type: str
    status: int
    location_kind: str
    # 1-based occurrence of ``request_type`` that is redirected (2 targets the
    # second ``initialize``: the Engineering-level retained-worker reconnect).
    occurrence: int = 1


@dataclass
class PeerCounters:
    origin: Counter = field(default_factory=Counter)
    destination: Counter = field(default_factory=Counter)
    executions: int = 0
    injected: int = 0

    def origin_total(self) -> int:
        return sum(self.origin.values())

    def destination_total(self) -> int:
        return sum(self.destination.values())


def _tool(name: str) -> dict:
    return {
        "name": name,
        "description": f"Synthetic {name}.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    }


class RedirectPeer:
    """Origin and destination peers sharing one counter set."""

    def __init__(
        self,
        *,
        version: str,
        form: str,
        inject: Injection | None = None,
        sse_call: bool = False,
        hold_list_until_get: bool = False,
        tool_names: tuple[str, ...] = ("ha_synthetic_one", "ha_synthetic_two"),
        raw_suffix: str | None = None,
        host: str | None = None,
        tools: tuple[dict, ...] | None = None,
        page_size: int | None = None,
        call_handler: Callable[[str, dict], dict] | None = None,
        delete_status: int = 200,
    ):
        # ``tools``: serve these raw descriptors verbatim (in this order) in
        # pages of ``page_size`` (one page when None) instead of the synthetic
        # two-page catalog. ``call_handler(name, arguments)`` returns the
        # ``structuredContent`` of a successful ``tools/call``.
        self.tools = tools
        self.page_size = page_size
        self.call_handler = call_handler
        self.calls: list[tuple[str, dict]] = []
        # Status of a non-redirected DELETE (500 makes cleanup fail).
        self.delete_status = delete_status
        # ``raw_suffix``: serve an arbitrary configured value (path, query and
        # anything after the authority) verbatim; every origin request then
        # counts as an origin request whatever path the client normalized it
        # to. ``host`` replaces the loopback host spelling in the URL only.
        self.raw_suffix = raw_suffix
        self.host = host
        self.version = version
        self.form = form
        self.inject = inject
        self.sse_call = sse_call
        self.hold_list_until_get = hold_list_until_get
        self.tool_names = tool_names
        self.counters = PeerCounters()
        self.lock = threading.Lock()
        self.get_answered = threading.Event()
        self.release = threading.Event()
        self.bound_path = ""
        self.bound_query = ""
        self.origin_base = ""
        self.destination_base = ""
        self.url = ""
        self.headers_seen: list[dict[str, str]] = []

    # -- configuration -------------------------------------------------
    def _bound_path_for(self, form: str) -> str:
        if form == "strict_full":
            return f"/private_{URL_PATH_CANARY}/catalog/full"
        if form == "strict_full_like_85":
            # A strict-form value served by an 8.5 peer.
            return f"/private_{URL_PATH_CANARY}/catalog/full"
        if form == "legacy":
            return f"/private_{URL_PATH_CANARY}/mcp"
        raise ValueError(form)

    def location_for(self, kind: str) -> str:
        base = self.bound_path.rsplit("/catalog/full", 1)[0]
        if base == self.bound_path:
            base = self.bound_path.rsplit("/", 1)[0]
        canary = f"?loc={LOCATION_CANARY}"
        if kind == "same_origin_default":
            return f"{self.origin_base}{base}/default{canary}"
        if kind == "same_origin_readonly_full":
            return f"{self.origin_base}{base}/readonly/catalog/full{canary}"
        if kind == "same_origin_search":
            return f"{self.origin_base}{base}/catalog/search{canary}"
        if kind == "same_path_slash":
            path = (
                self.bound_path[:-1]
                if self.bound_path.endswith("/")
                else self.bound_path + "/"
            )
            return f"{self.origin_base}{path}{canary}"
        if kind == "cross_origin":
            return f"{self.destination_base}{self.bound_path}{canary}"
        raise ValueError(kind)

    # -- request classification -----------------------------------------
    def _classify(self, method: str, headers, body: bytes) -> str:
        if method == "DELETE":
            return "delete"
        if method == "GET":
            last = headers.get("last-event-id")
            if last == "resume-token-1":
                return "get_resumption"
            if last:
                return "get_reconnect"
            return "get_stream"
        try:
            message = json.loads(body or b"{}")
        except ValueError:
            return "post_invalid"
        rpc_method = message.get("method", "")
        if rpc_method == "tools/list":
            params = message.get("params") or {}
            return "tools/list:cursor" if params.get("cursor") else "tools/list:first"
        return str(rpc_method) or "post_unknown"

    def _should_inject(self, request_type: str) -> bool:
        inject = self.inject
        if inject is None or inject.request_type != request_type:
            return False
        with self.lock:
            if self.counters.injected:
                return False
            if self.counters.origin[request_type] != inject.occurrence:
                return False
            self.counters.injected += 1
        return True

    def _page(self, cursor) -> dict:
        tools = list(self.tools or ())
        size = self.page_size or max(len(tools), 1)
        index = 0
        if cursor:
            index = int(str(cursor).rsplit("-", 1)[1]) - 1
        page = {"tools": tools[index * size:(index + 1) * size]}
        if (index + 1) * size < len(tools):
            page["nextCursor"] = f"synthetic-cursor-{index + 2}"
        return page

    # -- servers ----------------------------------------------------------
    def _origin_handler(peer):  # noqa: N805 - closure factory
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _body(self) -> bytes:
                length = int(self.headers.get("Content-Length", "0") or 0)
                return self.rfile.read(length) if length else b""

            def _send(self, status, body=b"", headers=None, content_type=None):
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                if content_type:
                    self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            def _handle(self, method: str):
                body = self._body() if method == "POST" else b""
                path, _, query = self.path.partition("?")
                bound = peer.raw_suffix is not None or (
                    path == peer.bound_path and query == peer.bound_query
                )
                request_type = peer._classify(method, self.headers, body)
                with peer.lock:
                    peer.headers_seen.append(
                        {k.lower(): v for k, v in self.headers.items()}
                    )
                    if bound:
                        peer.counters.origin[request_type] += 1
                    else:
                        peer.counters.destination[f"{method}:{request_type}"] += 1
                if not bound:
                    self._send(500)
                    return
                if peer._should_inject(request_type):
                    if request_type == "get_stream":
                        peer.get_answered.set()
                    self._send(
                        peer.inject.status,
                        headers={
                            "Location": peer.location_for(peer.inject.location_kind)
                        },
                    )
                    return
                if method == "DELETE":
                    self._send(peer.delete_status)
                    return
                if method == "GET":
                    if request_type == "get_stream":
                        peer.get_answered.set()
                    self._send(405)
                    return
                message = json.loads(body or b"{}")
                request_id = message.get("id")
                rpc_method = message.get("method", "")
                if request_id is None:
                    self._send(202)
                    return
                if rpc_method == "initialize":
                    result = {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "ha-mcp", "version": peer.version},
                    }
                    self._json(request_id, result, {"Mcp-Session-Id": "synthetic-session-1"})
                    return
                if rpc_method == "tools/list":
                    if peer.hold_list_until_get:
                        peer.get_answered.wait(10)
                        peer.release.wait(10)
                    params = message.get("params") or {}
                    if peer.tools is not None:
                        self._json(request_id, peer._page(params.get("cursor")))
                        return
                    if params.get("cursor"):
                        result = {"tools": [_tool(n) for n in peer.tool_names[1:]]}
                    else:
                        result = {
                            "tools": [_tool(n) for n in peer.tool_names[:1]],
                            "nextCursor": "synthetic-cursor-2",
                        }
                    self._json(request_id, result)
                    return
                if rpc_method == "tools/call":
                    if peer.sse_call:
                        # Priming event with an id, then end the stream without
                        # a response: the SDK reconnects with Last-Event-ID.
                        payload = b"id: event-1\nretry: 5\nevent: message\ndata:\n\n"
                        self._send(200, payload, content_type="text/event-stream")
                        return
                    params = message.get("params") or {}
                    name = str(params.get("name"))
                    arguments = params.get("arguments") or {}
                    with peer.lock:
                        peer.counters.executions += 1
                        peer.calls.append((name, arguments))
                    structured = (
                        peer.call_handler(name, arguments)
                        if peer.call_handler is not None
                        else {"ok": True}
                    )
                    result = {
                        "content": [{"type": "text", "text": json.dumps(structured)}],
                        "structuredContent": structured,
                        "isError": False,
                    }
                    self._json(request_id, result)
                    return
                self._json(request_id, {})

            def _json(self, request_id, result, headers=None):
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": request_id, "result": result}
                ).encode()
                self._send(200, body, headers=headers, content_type="application/json")

            def do_POST(self):
                self._handle("POST")

            def do_GET(self):
                self._handle("GET")

            def do_DELETE(self):
                self._handle("DELETE")

            def log_message(self, _format, *_args):
                return

        return Handler

    def _destination_handler(peer):  # noqa: N805
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _any(self, method):
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                with peer.lock:
                    peer.counters.destination[f"{method}:cross_origin"] += 1
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                self._any("POST")

            def do_GET(self):
                self._any("GET")

            def do_DELETE(self):
                self._any("DELETE")

            def log_message(self, _format, *_args):
                return

        return Handler

    @contextmanager
    def serve(self) -> Iterator["RedirectPeer"]:
        origin = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), self._origin_handler()
        )
        destination = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), self._destination_handler()
        )
        origin.daemon_threads = True
        destination.daemon_threads = True
        threads = [
            threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.05},
                daemon=True,
            )
            for server in (origin, destination)
        ]
        for thread in threads:
            thread.start()
        try:
            ohost, oport = origin.server_address
            dhost, dport = destination.server_address
            self.origin_base = f"http://{ohost}:{oport}"
            self.destination_base = f"http://{dhost}:{dport}"
            if self.raw_suffix is not None:
                self.url = f"http://{self.host or ohost}:{oport}{self.raw_suffix}"
                yield self
                return
            self.bound_path = self._bound_path_for(self.form)
            self.bound_query = f"credential={URL_QUERY_CANARY}"
            self.url = (
                f"http://{URL_USER_CANARY}:{URL_PASS_CANARY}@{ohost}:{oport}"
                f"{self.bound_path}?{self.bound_query}"
            )
            yield self
        finally:
            self.get_answered.set()
            self.release.set()
            for server in (origin, destination):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)


def peer_configurations() -> tuple[tuple[str, str], ...]:
    """(version, form) pairs: 8.5 on legacy and strict form, 9.1 on strict."""

    return (("8.5.0", "legacy"), ("8.5.0", "strict_full"), ("9.1.0", "strict_full"))


def matrix_cells(request_types: tuple[str, ...], *, full_locations: bool = True):
    """Yield (request_type, status, location_kind, version, form) cells."""

    for request_type in request_types:
        statuses = DELETE_STATUSES if request_type == "delete" else GET_POST_STATUSES
        for index, status in enumerate(statuses):
            for version, form in peer_configurations():
                if full_locations:
                    for kind in LOCATION_KINDS:
                        yield request_type, status, kind, version, form
                else:
                    kind = LOCATION_KINDS[index % len(LOCATION_KINDS)]
                    yield request_type, status, kind, version, form
