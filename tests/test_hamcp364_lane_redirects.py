"""HAMCP-364: a lane session ends a request at a redirect, before a second connection.

Offline. Each test starts its own loopback stub on a free port and drives the
real clients over real aiohttp sessions. While a test runs, every socket connect
of the process is recorded: one to 127.0.0.1 is sent to the stub whatever port
it names, and one to anything else is refused inside the test and never reaches
the network. So no test connects off-host, with a guard in place or removed.
The Thread/Matter A2 lane's Core and stub are not on loopback: a test of its
clients names two synthetic bridge addresses as that lane's own, and a connect
to either is sent to the same loopback stub and recorded under the address the
client dialled.
Redirect targets are address literals from the documentation ranges, which
aiohttp does not resolve, and every other lookup is refused the same way. The
Core harness is never run; only its request functions are called.
"""
import ast
import asyncio
import errno
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
import configuration_delete_contract_acceptance as config_delete  # noqa: E402
import core_thread_matter_topology_a2_local as a2_runner  # noqa: E402
import created_object_contract_acceptance as created  # noqa: E402
import entity_metadata_contract_acceptance as entity  # noqa: E402
import lane_session as subject  # noqa: E402
import marketplace_read_contract_acceptance as marketplace  # noqa: E402
import real_ha_contract_tests as harness  # noqa: E402
import security_metadata_contract_acceptance as security  # noqa: E402
import seq13_contract_acceptance as seq13  # noqa: E402
import seq15_contract_acceptance as seq15  # noqa: E402
import thread_matter_topology_a2_acceptance as a2  # noqa: E402
import thread_matter_topology_contract_acceptance as thread  # noqa: E402

SCRIPTS = ROOT / "scripts"
LOOPBACK = "127.0.0.1"
MAIN_PORT, LOCAL_PORT, UPSTREAM_PORT = 8123, 18123, 18086
MAIN_CORE, LOCAL_CORE = f"http://{LOOPBACK}:{MAIN_PORT}", f"http://{LOOPBACK}:{LOCAL_PORT}"
MAIN_SOCKET, LOCAL_SOCKET = f"ws://{LOOPBACK}:{MAIN_PORT}/api/websocket", f"ws://{LOOPBACK}:{LOCAL_PORT}/api/websocket"
# The Thread/Matter lanes: A1's Core on its own loopback port; A2's Core and stub on the
# internal bridge its runner creates, here two synthetic addresses that reach the stub above.
THREAD_PORT, STUB_PORT = 18124, 5580
THREAD_SOCKET = f"ws://{LOOPBACK}:{THREAD_PORT}/api/websocket"
BRIDGE, BRIDGE_STUB, BRIDGE_SUBNET = "172.30.0.2", "172.30.0.3", "172.30.0.0/16"
BRIDGE_CORE, BRIDGE_SOCKET = f"http://{BRIDGE}:{MAIN_PORT}", f"ws://{BRIDGE}:{MAIN_PORT}/api/websocket"
BRIDGE_STUB_ORIGIN = f"http://{BRIDGE_STUB}:{STUB_PORT}"
# Reserved documentation addresses (TEST-NET-2); never a real peer, and never dialled here.
ELSEWHERE, PROXY = "198.51.100.7", "198.51.100.9"
TOKEN = "synthetic-token-364"
REFUSED = "lane_redirect_refused"
# The statuses aiohttp 3.14.4 follows, and 3xx statuses it hands back as they are.
FOLLOWED, HANDED_BACK = (301, 302, 303, 307, 308), (300, 304, 305)
# Where a redirect points: another host by each spelling aiohttp accepts, and the same host.
AWAY = (f"http://{ELSEWHERE}:8123/api/websocket", f"ws://{ELSEWHERE}:8123/api/websocket",
        f"//{ELSEWHERE}/api/websocket")
SAME_HOST = "/api/elsewhere"
REAL_CONNECT, REAL_CONNECT_EX, REAL_LOOKUP = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo


def configured(url):
    return SimpleNamespace(websocket_url=url, api_url=MAIN_CORE + "/api", ha_token=TOKEN)


def configured_local():
    """Settings for the created-object client: its own Core, for the WebSocket and for REST."""
    return SimpleNamespace(websocket_url=LOCAL_SOCKET, api_url=LOCAL_CORE + "/api", ha_token=TOKEN,
                           ha_timeout_seconds=10)


class Stub:
    """A loopback stand-in for the Core: counts requests; answers a redirect or the lane's own exchange."""

    def __init__(self):
        self.requests, self.frames, self.answers, self.body = [], [], [], [{"step": "user", "done": False}]
        self.bearers = []

    async def start(self):
        application = web.Application()
        application.router.add_route("*", "/{path:.*}", self.answer)
        self.runner = web.AppRunner(application, access_log=None, shutdown_timeout=1)
        await self.runner.setup()
        loop = asyncio.get_running_loop()
        debug = loop.get_debug()
        loop.set_debug(False)  # in debug mode asyncio logs every server it starts
        try:
            await web.TCPSite(self.runner, LOOPBACK, 0).start()
        finally:
            loop.set_debug(debug)
        self.port = self.runner.addresses[0][1]

    def then(self, *answers):
        """Queue the next answers: None for the lane's own exchange, or (status, location). The last one repeats."""
        self.requests, self.frames, self.answers, self.bearers = [], [], list(answers), []

    async def answer(self, request):
        self.requests.append((request.method, request.path))
        self.bearers.append(request.headers.get("Authorization"))
        answer = self.answers.pop(0) if len(self.answers) > 1 else (self.answers[0] if self.answers else None)
        if answer is not None:
            status, location = answer
            # A body a careless caller would take for a good answer.
            more = {} if status == 304 else {"text": '[{"step": "user", "done": false}]'}
            return web.Response(status=status, headers={"Location": location}, content_type="application/json", **more)
        if request.headers.get("Upgrade", "").lower() != "websocket":
            return web.json_response(self.body)
        peer = web.WebSocketResponse()
        await peer.prepare(request)
        await peer.send_json({"type": "auth_required"})
        self.frames.append(await peer.receive_json())
        await peer.send_json({"type": "auth_ok"})
        command = await peer.receive_json()
        self.frames.append(command)
        await peer.send_json({"id": command["id"], "type": "result", "success": True, "result": ["answer"]})
        await peer.close()
        return peer


class Wire:
    """Every connect and lookup of the process while a test runs."""

    def __init__(self, stub_port):
        self.stub_port, self.connects, self.lookups = stub_port, [], []
        self.local = {LOOPBACK}  # plus the bridge addresses of a test that hands a Core over

    @property
    def off_host(self):
        return [target for target in self.connects if target[0] not in self.local] + self.lookups

    def dial(self, real):
        def dial(sock, address):
            if type(address) is not tuple:  # a filesystem socket: on this host by construction
                return real(sock, address)
            self.connects.append(tuple(address[:2]))
            if address[0] not in self.local:
                raise ConnectionRefusedError(errno.ECONNREFUSED, "refused inside the test")
            return real(sock, (LOOPBACK, self.stub_port))
        return dial

    def lookup(self, host, *args, **kwargs):
        if host not in (None, LOOPBACK):
            self.lookups.append(host)
            raise socket.gaierror(socket.EAI_NONAME, "refused inside the test")
        return REAL_LOOKUP(host, *args, **kwargs)


def plain_session(*, timeout=None):
    """A session without the lane's redirect trace, as the clients built before this change."""
    return aiohttp.ClientSession(timeout=timeout or aiohttp.ClientTimeout(total=10), trust_env=False)


class Wired(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stub = Stub()
        await self.stub.start()
        self.addAsyncCleanup(self.stub.runner.cleanup)
        self.wire = Wire(self.stub.port)
        self.enterContext(patch.object(socket.socket, "connect", self.wire.dial(REAL_CONNECT)))
        self.enterContext(patch.object(socket.socket, "connect_ex", self.wire.dial(REAL_CONNECT_EX)))
        self.enterContext(patch.object(socket, "getaddrinfo", self.wire.lookup))

    def redirect(self, status=302, location=AWAY[0]):
        self.stub.then((status, location))
        self.wire.connects.clear()
        self.wire.lookups.clear()

    def assert_code(self, raised):
        self.assertIs(type(raised.exception), subject.LaneRedirectRefused)
        self.assertEqual(raised.exception.args, (REFUSED,))

    def hand_over(self):
        """The A2 runner's hand-over, through the harness's own function; undone after the test."""
        self.enterContext(patch.multiple(harness, HA_URL=harness.HA_URL, CLIENT_ID=harness.CLIENT_ID,
                                         _lane_core=None))
        self.assertEqual(harness.use_internal_lane_core(BRIDGE_CORE, BRIDGE_SUBNET), BRIDGE_CORE)
        self.wire.local |= {BRIDGE, BRIDGE_STUB}

    def assert_refused(self, raised, port, host=LOOPBACK):
        """The fixed refusal; one connection, to the stub; one request; no frame; nothing dialled off-host."""
        self.assert_code(raised)
        self.assertEqual(self.wire.off_host, [])
        self.assertEqual(self.wire.connects, [(host, port)])
        self.assertEqual(len(self.stub.requests), 1)
        self.assertEqual(self.stub.frames, [])

    async def waited(self, _seconds):
        raise AssertionError("the loop waited for another answer")

    def no_waiting(self):
        """The harness's readiness loops fail the test if they sleep instead of ending."""
        return patch.object(harness, "asyncio", SimpleNamespace(sleep=self.waited, TimeoutError=asyncio.TimeoutError))


class LaneWebSocket(Wired):
    """Each lane's websocket handshake, answered with a redirect, ends before the token frame."""

    CLIENTS = {
        "marketplace": (lambda: marketplace.RawCore(configured(LOCAL_SOCKET)).command, {"type": "marketplace/info"},
                        LOCAL_PORT),
        "security_metadata": (lambda: security.RawCore(configured(LOCAL_SOCKET)).command,
                              {"type": security.CURRENT_USER}, LOCAL_PORT),
        "entity_metadata": (lambda: entity.RawWebSocket(configured(MAIN_SOCKET)).call,
                            {"type": entity.GET, "entity_id": "switch.synthetic"}, MAIN_PORT),
        "configuration_delete": (lambda: config_delete.LaneRaw(configured(MAIN_SOCKET)).call,
                                 {"type": config_delete.REGISTRY_LIST}, MAIN_PORT),
        "seq13": (lambda: seq13.LaneRaw(configured(MAIN_SOCKET)).call, {"type": seq13.REGISTRY_LIST}, MAIN_PORT),
        "seq15": (lambda: seq15.LaneRaw(configured(MAIN_SOCKET)).call, {"type": seq15.REGISTRY_LIST}, MAIN_PORT),
        # One client per created-object lane, each with a command on its own list.
        "created_scene": (lambda: created.SceneLaneRaw(configured_local()).call, {"type": created.REGISTRY_LIST},
                          LOCAL_PORT),
        "created_helper": (lambda: created.HelperLaneRaw(configured_local()).call, {"type": "input_boolean/list"},
                           LOCAL_PORT),
        # The Thread/Matter lanes: A1 on its own loopback port, A2 on the Core handed over.
        "thread_matter": (lambda: thread.RawCore(configured(THREAD_SOCKET)).command,
                          {"type": thread.LIST_DATASETS_COMMAND}, THREAD_PORT),
        "thread_matter_a2": (lambda: a2.A2RawCore(configured(BRIDGE_SOCKET)).command,
                             {"type": a2.DEVICE_REGISTRY_COMMAND}, MAIN_PORT),
    }
    # The one client whose Core is not on loopback.
    HOSTS = {"thread_matter_a2": BRIDGE}

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.hand_over()

    async def refused(self, name):
        send, payload, port = self.CLIENTS[name]
        host = self.HOSTS.get(name, LOOPBACK)
        self.redirect(302, AWAY[0])
        with self.assertRaises(Exception) as raised:
            await send()(dict(payload))
        self.assert_refused(raised, port, host)
        for status in FOLLOWED:
            for location in (*AWAY, SAME_HOST):
                with self.subTest(status=status, location=location):
                    self.redirect(status, location)
                    with self.assertRaises(Exception) as raised:
                        await send()(dict(payload))
                    self.assert_refused(raised, port, host)

    async def answered(self, name):
        send, payload, port = self.CLIENTS[name]
        reply = await send()(dict(payload))
        self.assertEqual(reply, {"id": 1, "type": "result", "success": True, "result": ["answer"]})
        self.assertEqual(self.stub.requests, [("GET", "/api/websocket")])
        self.assertEqual(self.stub.frames, [{"type": "auth", "access_token": TOKEN}, {"id": 1, **payload}])
        self.assertEqual(self.wire.connects, [(self.HOSTS.get(name, LOOPBACK), port)])
        self.assertEqual(self.wire.off_host, [])

    async def test_marketplace_handshake_redirect_is_refused(self):
        await self.refused("marketplace")

    async def test_security_metadata_handshake_redirect_is_refused(self):
        await self.refused("security_metadata")

    async def test_entity_metadata_handshake_redirect_is_refused(self):
        await self.refused("entity_metadata")

    async def test_configuration_delete_handshake_redirect_is_refused(self):
        await self.refused("configuration_delete")

    async def test_seq13_handshake_redirect_is_refused(self):
        await self.refused("seq13")

    async def test_seq15_handshake_redirect_is_refused(self):
        await self.refused("seq15")

    async def test_created_scene_handshake_redirect_is_refused(self):
        await self.refused("created_scene")

    async def test_created_helper_handshake_redirect_is_refused(self):
        await self.refused("created_helper")

    async def test_thread_matter_handshake_redirect_is_refused(self):
        await self.refused("thread_matter")

    async def test_thread_matter_a2_handshake_redirect_is_refused(self):
        await self.refused("thread_matter_a2")

    def test_every_client_in_the_table_has_its_redirect_test(self):
        tests = {name for name in dir(self) if name.endswith("_handshake_redirect_is_refused")}
        self.assertEqual(tests, {f"test_{name}_handshake_redirect_is_refused" for name in self.CLIENTS})

    async def test_each_lane_command_is_still_answered_over_its_pinned_socket(self):
        for name in self.CLIENTS:
            with self.subTest(client=name):
                self.stub.then()
                self.wire.connects.clear()
                await self.answered(name)

    async def test_a_3xx_that_is_not_followed_fails_the_handshake_on_the_one_connection(self):
        for name, (send, payload, port) in self.CLIENTS.items():
            for status in HANDED_BACK:
                with self.subTest(client=name, status=status):
                    self.redirect(status)
                    with self.assertRaises(aiohttp.WSServerHandshakeError):
                        await send()(dict(payload))
                    self.assertEqual(self.wire.connects, [(self.HOSTS.get(name, LOOPBACK), port)])
                    self.assertEqual((self.wire.off_host, len(self.stub.requests), self.stub.frames), ([], 1, []))

    def test_the_lane_subclasses_send_through_the_shared_call(self):
        for lane in (config_delete, seq13, seq15, created):
            self.assertIs(lane.LaneRaw.call, entity.RawWebSocket.call)
            self.assertNotIn("call", vars(lane.LaneRaw))
        for client in created.LANE_CLIENTS.values():
            self.assertIs(client.call, entity.RawWebSocket.call)
            self.assertEqual(sorted(name for name in vars(client) if not name.startswith("__")), ["commands"])
        # The A2 client is the A1 client with its own Core and list, and no send of its own.
        self.assertIs(a2.A2RawCore.command, thread.RawCore.command)
        self.assertEqual(sorted(name for name in vars(a2.A2RawCore) if not name.startswith("__")),
                         ["commands", "core_url"])


class A2Admin(Wired):
    """The A2 runner's REST client: the accepted Core and the stub only; a 3xx from either is a refusal."""

    ENTRY, FLOW = "/api/config/config_entries/entry", "/api/config/config_entries/flow"
    REFUSED_TARGET = "lane_admin_target_refused"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.hand_over()
        self.admin = a2_runner.CoreAdmin(BRIDGE_CORE, TOKEN, BRIDGE_STUB_ORIGIN)

    def requests(self):
        """Every request the client sends: the send, then the method, path, host, port and bearer it arrives with."""
        core, stub = (BRIDGE, MAIN_PORT, "Bearer " + TOKEN), (BRIDGE_STUB, STUB_PORT, None)
        admin = self.admin
        return (
            (lambda: admin.core("GET", self.ENTRY + "?domain=matter"), "GET", self.ENTRY, *core),
            (lambda: admin.core("POST", self.FLOW, {"handler": "matter"}), "POST", self.FLOW, *core),
            (lambda: admin.core("POST", self.FLOW + "/synthetic", {"url": "ws://matter-stub:5580/ws"}),
             "POST", self.FLOW + "/synthetic", *core),
            (lambda: admin.core("GET", "/api/config"), "GET", "/api/config", *core),
            (lambda: admin.core("POST", self.ENTRY + "/synthetic/reload"), "POST", self.ENTRY + "/synthetic/reload",
             *core),
            (lambda: admin.stub_control({"fault": "synthetic"}), "POST", "/_stub/control", *stub),
            (admin.stub_state, "GET", "/_stub/control", *stub),
        )

    def assert_target_refused(self, raised):
        self.assertIs(type(raised.exception), a2_runner.Refusal)
        self.assertEqual(raised.exception.args, (self.REFUSED_TARGET,))
        self.assertEqual((self.wire.connects, self.wire.lookups, self.stub.requests), ([], [], []))

    def test_the_table_is_every_request_the_client_makes(self):
        tree = ast.parse((SCRIPTS / "core_thread_matter_topology_a2_local.py").read_text(encoding="utf-8"))
        admin, = (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CoreAdmin")
        sends = sorted(ast.unparse(call) for call in calls(admin) if named(call) in {"self.core", "self._call"})
        self.assertEqual(sends, sorted([
            "self._call(method, True, path, body)",
            "self._call('POST', False, '/_stub/control', body)",
            "self._call('GET', False, '/_stub/control')",
            "self.core('GET', '/api/config/config_entries/entry?domain=matter')",
            "self.core('POST', '/api/config/config_entries/flow', {'handler': 'matter'})",
            "self.core('POST', f\"/api/config/config_entries/flow/{flow['flow_id']}\", "
            "{'url': f'ws://{STUB_ALIAS}:5580/ws'})",
            "self.core('GET', '/api/config')",
            "self.core('POST', f\"/api/config/config_entries/entry/{entry['entry_id']}/reload\")"]))
        self.assertEqual(len(self.requests()), 7)

    async def test_each_request_answered_with_a_redirect_is_refused(self):
        for send, method, path, host, port, _bearer in self.requests():
            for status in (302, *FOLLOWED, *HANDED_BACK):
                for location in (AWAY[0], AWAY[2], SAME_HOST):
                    with self.subTest(method=method, path=path, status=status, location=location):
                        self.redirect(status, location)
                        with self.assertRaises(Exception) as raised:
                            await send()
                        self.assert_refused(raised, port, host)
                        self.assertEqual(self.stub.requests, [(method, path)])

    async def test_without_the_status_check_a_redirect_would_be_taken_for_the_answer(self):
        # The control: the request itself declines the redirect, so it stays on the one
        # connection, and without the lane's check the 3xx body would be returned as the answer.
        for send, method, path, host, port, _bearer in self.requests():
            with self.subTest(method=method, path=path):
                self.redirect(302, AWAY[0])
                with patch.object(subject, "refuse_redirect", lambda status: None):
                    answer = await send()
                self.assertEqual(answer, [{"step": "user", "done": False}])
                self.assertEqual((self.wire.connects, self.wire.off_host), ([(host, port)], []))
                self.assertEqual(self.stub.requests, [(method, path)])

    async def test_other_answers_are_returned_and_the_token_goes_to_the_core_only(self):
        for send, method, path, host, port, bearer in self.requests():
            with self.subTest(method=method, path=path):
                self.stub.then()
                self.wire.connects.clear()
                self.assertEqual(await send(), self.stub.body)
                self.assertEqual((self.stub.requests, self.stub.bearers), ([(method, path)], [bearer]))
                self.assertEqual((self.wire.connects, self.wire.off_host), ([(host, port)], []))

    def test_only_the_accepted_core_and_a_bare_stub_origin_build_the_client(self):
        self.stub.then()
        self.wire.connects.clear()
        for core in (MAIN_CORE, LOCAL_CORE, f"http://{ELSEWHERE}:{MAIN_PORT}", f"http://{BRIDGE_STUB}:{MAIN_PORT}",
                     BRIDGE_CORE + "/", BRIDGE_CORE + "/api", BRIDGE_CORE.replace("http", "https"), "", None,
                     BRIDGE_CORE.encode()):
            with self.subTest(core=core), self.assertRaises(Exception) as raised:
                a2_runner.CoreAdmin(core, TOKEN, BRIDGE_STUB_ORIGIN)
            self.assert_target_refused(raised)
        for stub in (BRIDGE_CORE, BRIDGE_STUB_ORIGIN + "/", BRIDGE_STUB_ORIGIN + "/_stub/control",
                     f"http://{BRIDGE_STUB}", f"https://{BRIDGE_STUB}:{STUB_PORT}", f"ws://{BRIDGE_STUB}:{STUB_PORT}",
                     f"http://matter-stub:{STUB_PORT}", f"http://localhost:{STUB_PORT}",
                     f"http://token@{BRIDGE_STUB}:{STUB_PORT}", f"http://[::1]:{STUB_PORT}",
                     f"http://172.030.0.3:{STUB_PORT}", f"http://{BRIDGE_STUB}:0", f"http://{BRIDGE_STUB}:055",
                     BRIDGE_STUB_ORIGIN + "?x=1", BRIDGE_STUB_ORIGIN + "#x", " " + BRIDGE_STUB_ORIGIN,
                     BRIDGE_STUB_ORIGIN + "\n", "", None, BRIDGE_STUB_ORIGIN.encode()):
            with self.subTest(stub=stub), self.assertRaises(Exception) as raised:
                a2_runner.CoreAdmin(BRIDGE_CORE, TOKEN, stub)
            self.assert_target_refused(raised)

    def test_no_client_is_built_before_a_core_is_handed_over(self):
        self.stub.then()
        self.wire.connects.clear()
        with patch.object(harness, "_lane_core", None), self.assertRaises(Exception) as raised:
            a2_runner.CoreAdmin(BRIDGE_CORE, TOKEN, BRIDGE_STUB_ORIGIN)
        self.assert_target_refused(raised)

    async def test_a_target_changed_after_the_client_was_built_is_refused_before_a_session(self):
        elsewhere = f"http://{ELSEWHERE}:{MAIN_PORT}"
        sessions = []
        opened = patch.object(subject, "lane_session", lambda **kwargs: sessions.append(kwargs))
        for name, value, send in (
                ("ha_url", elsewhere, lambda admin: admin.core("GET", "/api/config")),
                ("ha_url", MAIN_CORE, lambda admin: admin.core("GET", "/api/config")),
                ("ha_url", BRIDGE_STUB_ORIGIN, lambda admin: admin.core("GET", "/api/config")),
                ("ha_url", None, lambda admin: admin.core("GET", "/api/config")),
                ("stub_url", BRIDGE_CORE, lambda admin: admin.stub_state()),
                ("stub_url", f"http://matter-stub:{STUB_PORT}", lambda admin: admin.stub_state()),
                ("stub_url", f"http://{ELSEWHERE}:{STUB_PORT}/", lambda admin: admin.stub_control({})),
                ("stub_url", f"http://token@{ELSEWHERE}:{STUB_PORT}", lambda admin: admin.stub_control({})),
                ("stub_url", None, lambda admin: admin.stub_state())):
            with self.subTest(name=name, value=value):
                admin = a2_runner.CoreAdmin(BRIDGE_CORE, TOKEN, BRIDGE_STUB_ORIGIN)
                setattr(admin, name, value)
                self.stub.then()
                self.wire.connects.clear()
                with opened, self.assertRaises(Exception) as raised:
                    await send(admin)
                self.assert_target_refused(raised)
                self.assertEqual(sessions, [])
        # Nor does the Core's request follow the harness to another Core: the two must agree.
        admin = a2_runner.CoreAdmin(BRIDGE_CORE, TOKEN, BRIDGE_STUB_ORIGIN)
        with patch.object(harness, "_lane_core", "http://172.30.0.9:8123"), opened:
            with self.assertRaises(Exception) as raised:
                await admin.core("GET", "/api/config")
        self.assert_target_refused(raised)
        self.assertEqual(sessions, [])

    async def test_a_path_that_is_not_rooted_or_a_target_that_is_neither_is_refused_before_a_session(self):
        sessions = []
        opened = patch.object(subject, "lane_session", lambda **kwargs: sessions.append(kwargs))
        self.stub.then()
        self.wire.connects.clear()
        for path in ("api/config", "", f"@{ELSEWHERE}/api/config", f".{ELSEWHERE}/api", ":9/api/config",
                     f"{ELSEWHERE}/api/config", None, b"/api/config", ("/api/config",)):
            for to_core in (True, False):
                with self.subTest(path=path, to_core=to_core), opened:
                    with self.assertRaises(Exception) as raised:
                        await self.admin._call("GET", to_core, path)
                    self.assert_target_refused(raised)
        # The second argument says which of the two origins, and is never an origin itself.
        for target in (BRIDGE_CORE, BRIDGE_STUB_ORIGIN, f"http://{ELSEWHERE}:{MAIN_PORT}", 1, 0, None, "", "core"):
            with self.subTest(target=target), opened:
                with self.assertRaises(Exception) as raised:
                    await self.admin._call("GET", target, "/api/config")
                self.assert_target_refused(raised)
        self.assertEqual(sessions, [])


class CreatedObjectRest(Wired):
    """The created-object client's REST requests: a 3xx answer is a refusal, never an answer and never followed.

    They go through a guarded Engineering REST client of the lane's own, the real
    one here, which opens its own session and declines redirects itself. The
    lane's part is to refuse the 3xx it is handed instead of reporting it.
    """

    NAME = "hamcp_g4_lamp_0a1b2c3d"
    CLIENTS = (created.SceneLaneRaw, created.HelperLaneRaw)

    def test_the_rest_requests_are_checked_through_each_lane_client(self):
        self.assertEqual(self.CLIENTS, tuple(created.LANE_CLIENTS.values()))
        for client in self.CLIENTS:
            self.assertIs(client.status, created.LaneRaw.status)

    def requests(self):
        """Every method and route the client sends, with the path each reaches."""
        scene, automation = "/api/config/scene/config/", "/api/config/automation/config/"
        sent = (("GET", created.state_route("scene." + self.NAME), None, "/api/states/scene." + self.NAME),
                ("GET", created.scene_route(self.NAME), None, scene + self.NAME),
                ("GET", created.automation_route(self.NAME), None, automation + self.NAME),
                ("POST", created.automation_route(self.NAME), {"alias": "synthetic"}, automation + self.NAME),
                ("DELETE", created.automation_route(self.NAME), None, automation + self.NAME))
        self.assertEqual({(method, route.template) for method, route, _, _ in sent}, created.LANE_REQUESTS)
        return sent

    async def test_each_rest_request_answered_with_a_redirect_is_refused(self):
        for client in self.CLIENTS:
            for method, route, body, path in self.requests():
                for status in (302, *FOLLOWED, *HANDED_BACK):
                    for location in (AWAY[0], AWAY[2], SAME_HOST):
                        with self.subTest(client=client.__name__, method=method, path=path, status=status,
                                          location=location):
                            self.redirect(status, location)
                            with self.assertRaises(Exception) as raised:
                                await client(configured_local()).status(method, route, body)
                            self.assert_refused(raised, LOCAL_PORT)
                            self.assertEqual(self.stub.requests, [(method, path)])

    async def test_the_guarded_client_does_not_follow_a_redirect_by_itself(self):
        # The control for the lane's own check: with it taken away the 3xx comes back as a
        # status, on the one connection. So nothing above depends on it for staying on-host,
        # and the refusal above is the lane's and not the client's.
        for client in self.CLIENTS:
            for method, route, body, path in self.requests():
                with self.subTest(client=client.__name__, method=method, path=path):
                    self.redirect(302, AWAY[0])
                    with patch.object(created, "refuse_redirect", lambda status: None):
                        answer = await client(configured_local()).status(method, route, body)
                    self.assertEqual(answer, (302, None))
                    self.assertEqual((self.wire.connects, self.wire.off_host), ([(LOOPBACK, LOCAL_PORT)], []))
                    self.assertEqual(self.stub.requests, [(method, path)])

    async def test_other_answers_are_still_reported_as_they_were(self):
        for client in self.CLIENTS:
            for method, route, body, path in self.requests():
                with self.subTest(client=client.__name__, method=method, path=path):
                    self.stub.then()
                    self.wire.connects.clear()
                    self.assertEqual(await client(configured_local()).status(method, route, body),
                                     (200, self.stub.body))
                    for status in (404, 400, 401, 405, 500):
                        self.redirect(status, AWAY[0])
                        self.assertEqual(await client(configured_local()).status(method, route, body),
                                         (status, None))
                        self.assertEqual((self.wire.connects, self.wire.off_host), ([(LOOPBACK, LOCAL_PORT)], []))
                        self.assertEqual(self.stub.requests, [(method, path)])

    def test_the_status_handed_back_is_checked_before_it_is_reported(self):
        tree = ast.parse((SCRIPTS / "created_object_contract_acceptance.py").read_text(encoding="utf-8"))
        lane = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "LaneRaw")
        status = next(node for node in lane.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "status")
        handlers = [node for node in ast.walk(status) if isinstance(node, ast.ExceptHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertEqual([ast.unparse(node) for node in handlers[0].body[-2:]],
                         ["refuse_redirect(status)", "return (status, None)"])
        # That is the one place the client reports a failed request's status, and the one request it makes.
        reported = sorted(ast.unparse(node) for node in ast.walk(status) if isinstance(node, ast.Return))
        self.assertEqual(reported, ["return (200, payload)", "return (payload.status, None)", "return (status, None)"])
        sent = [ast.unparse(node.func) for node in ast.walk(lane) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "request"]
        self.assertEqual(sent, ["self.client.request"])
        self.assertIs(created.refuse_redirect, subject.refuse_redirect)


class HarnessRest(Wired):
    """The Core harness's REST requests: a 3xx answer is a refusal, never an answer and never followed."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.enterContext(patch.object(harness, "HA_URL", MAIN_CORE))
        self.enterContext(patch.object(harness, "UPSTREAM_PORT", UPSTREAM_PORT))
        self.enterContext(patch.object(harness, "UPSTREAM_IMAGE", "registry.invalid/ha-mcp@sha256:" + "0" * 64))
        self.enterContext(patch.object(harness, "_docker_command", AsyncMock()))
        self.enterContext(patch.dict(os.environ, {"HA_CONTRACT_NETWORK": ""}))
        self.scratch = self.enterContext(tempfile.TemporaryDirectory())

    async def json_request(self, session_factory, method, **more):
        async with session_factory(timeout=aiohttp.ClientTimeout(total=10)) as session:
            return await harness._json_request(session, method, "/api/onboarding", token=TOKEN, **more)

    async def refused_request(self, session_factory, method, **more):
        for status in (302, *FOLLOWED, *HANDED_BACK):
            for location in (AWAY[0], AWAY[2], SAME_HOST):
                with self.subTest(status=status, location=location):
                    self.redirect(status, location)
                    with self.assertRaises(Exception) as raised:
                        await self.json_request(session_factory, method, **more)
                    self.assert_refused(raised, MAIN_PORT)
                    self.assertEqual(self.stub.requests, [(method, "/api/onboarding")])

    async def test_json_request_get_redirect_is_refused(self):
        await self.refused_request(subject.lane_session, "GET")

    async def test_json_request_post_redirect_is_refused(self):
        await self.refused_request(subject.lane_session, "POST", json_body={"client_id": "synthetic"})

    async def test_json_request_refuses_a_redirect_on_a_session_that_would_follow_it(self):
        # Without the lane's trace behind it: the request itself must not follow, and must refuse the answer.
        await self.refused_request(plain_session, "GET")
        await self.refused_request(plain_session, "POST", data={"grant_type": "synthetic"})

    async def test_json_request_still_returns_a_good_answer_and_reports_a_failed_one(self):
        self.assertEqual(await self.json_request(subject.lane_session, "GET"), self.stub.body)
        self.assertEqual(self.stub.requests, [("GET", "/api/onboarding")])
        self.assertEqual((self.wire.connects, self.wire.off_host), ([(LOOPBACK, MAIN_PORT)], []))
        self.redirect(404)
        with self.assertRaises(RuntimeError) as raised:
            await self.json_request(subject.lane_session, "GET")
        self.assertEqual(raised.exception.args,
                         ("Disposable Home Assistant bootstrap failed at /api/onboarding (404)",))

    async def test_readiness_poll_redirect_is_refused_at_once(self):
        for session_factory in (subject.lane_session, plain_session):
            with self.subTest(session=session_factory.__name__):
                self.redirect(302, f"http://{ELSEWHERE}:8123/api/onboarding")
                with patch.object(harness, "lane_session", session_factory), self.no_waiting(), \
                        self.assertRaises(Exception) as raised:
                    await harness.bootstrap_disposable_admin()
                self.assert_refused(raised, MAIN_PORT)
                self.assertEqual(self.stub.requests, [("GET", "/api/onboarding")])

    async def test_upstream_liveness_redirect_is_refused_at_once(self):
        for session_factory in (subject.lane_session, plain_session):
            with self.subTest(session=session_factory.__name__):
                self.redirect(302, f"http://{ELSEWHERE}:{UPSTREAM_PORT}/healthz")
                with patch.object(harness, "lane_session", session_factory), self.no_waiting(), \
                        self.assertRaises(Exception) as raised:
                    await harness._start_exact_upstream(TOKEN, environment_directory=self.scratch)
                self.assert_refused(raised, UPSTREAM_PORT)
                self.assertEqual(self.stub.requests, [("GET", "/healthz")])
        self.assertEqual(os.listdir(self.scratch), [])

    async def test_upstream_liveness_still_ends_on_the_expected_answer(self):
        self.stub.body = {"status": "ok", "server": "ha-mcp"}
        with self.no_waiting():
            await harness._start_exact_upstream(TOKEN, environment_directory=self.scratch)
        self.assertEqual(self.stub.requests, [("GET", "/healthz")])
        self.assertEqual((self.wire.connects, self.wire.off_host), ([(LOOPBACK, UPSTREAM_PORT)], []))

    async def handed_over(self, started):
        """Use the session the harness hands on as a careless caller would; return what was seen."""
        seen = []

        async def careless(session, *_args, **_kwargs):
            seen.append(session.trust_env)
            self.stub.answers = [(302, AWAY[0])]
            async with session.get(MAIN_CORE + "/api/config/config_entries/flow"):
                pass

        with started(careless), self.no_waiting(), self.assertRaises(Exception) as raised:
            await self.subject()
        self.assert_code(raised)
        # The session may send this request on the connection it already holds; none goes anywhere else.
        self.assertEqual((self.wire.off_host, set(self.wire.connects)), ([], {(LOOPBACK, MAIN_PORT)}))
        self.assertEqual(self.stub.requests[-1], ("GET", "/api/config/config_entries/flow"))
        self.assertEqual(seen, [False])

    async def test_bootstrap_hands_its_requests_a_lane_session(self):
        self.subject = harness.bootstrap_disposable_admin
        await self.handed_over(lambda careless: patch.object(harness, "_json_request", careless))

    async def test_fixture_preparation_hands_its_flow_a_lane_session(self):
        self.subject = harness.prepare_migration_fixture
        with patch.object(harness, "DEVICE_FIXTURE_PATH", Path(self.scratch) / "fixture.json"), \
                patch.object(harness, "TOKEN_PATH", Path(self.scratch) / "token"), \
                patch.object(harness, "bootstrap_disposable_admin", AsyncMock(return_value=TOKEN)), \
                patch.object(harness, "settings", MagicMock()), \
                patch.object(harness, "HomeAssistantRestClient", MagicMock()), \
                patch.object(harness, "HomeAssistantWebSocketClient", MagicMock()), \
                patch.object(harness, "wait_for_runtime_ready", AsyncMock()):
            await self.handed_over(lambda careless: patch.object(harness, "_advance_config_flow", careless))
        self.assertEqual(os.listdir(self.scratch), [])


class Helper(Wired):
    """The shared session and the status check, on their own."""

    def url(self, scheme="http"):
        return f"{scheme}://{LOOPBACK}:{MAIN_PORT}/api/anything"

    async def test_a_followed_status_ends_a_get_a_post_and_a_handshake(self):
        for status in FOLLOWED:
            for location in (*AWAY, SAME_HOST):
                for verb in ("get", "post", "ws_connect"):
                    with self.subTest(status=status, location=location, verb=verb):
                        self.redirect(status, location)
                        async with subject.lane_session(timeout=aiohttp.ClientTimeout(total=10)) as session:
                            with self.assertRaises(Exception) as raised:
                                # No allow_redirects here: the trace alone ends the request.
                                async with getattr(session, verb)(self.url("ws" if verb == "ws_connect" else "http")):
                                    pass
                        self.assert_refused(raised, MAIN_PORT)

    async def test_a_session_without_the_trace_is_seen_and_stopped_by_this_module(self):
        # The control for every "nothing was dialled off-host" above: a followed redirect is
        # recorded and refused here, so an empty record means no attempt was made.
        for verb in ("get", "ws_connect"):
            with self.subTest(verb=verb):
                self.redirect(302, AWAY[0])
                async with plain_session() as session:
                    with self.assertRaises(aiohttp.ClientConnectorError):
                        async with getattr(session, verb)(self.url("ws" if verb == "ws_connect" else "http")):
                            pass
                self.assertEqual(self.wire.connects, [(LOOPBACK, MAIN_PORT), (ELSEWHERE, 8123)])
                self.assertEqual(self.wire.off_host, [(ELSEWHERE, 8123)])
                self.assertEqual(len(self.stub.requests), 1)

    async def test_a_lane_session_ignores_proxy_settings(self):
        proxies = {name: f"http://{PROXY}:3128" for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                                                             "ALL_PROXY", "all_proxy", "WS_PROXY", "ws_proxy")}
        with patch.dict(os.environ, proxies):
            for name in ("NO_PROXY", "no_proxy"):
                os.environ.pop(name, None)
            async with subject.lane_session(timeout=aiohttp.ClientTimeout(total=10)) as session:
                self.assertIs(session.trust_env, False)
                async with session.get(self.url(), allow_redirects=False) as response:
                    self.assertEqual((response.status, await response.json()), (200, self.stub.body))
            self.assertEqual((self.wire.connects, self.wire.off_host), ([(LOOPBACK, MAIN_PORT)], []))
            # The control: a session told to trust the environment dials the proxy, and is stopped here.
            self.wire.connects.clear()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=True) as session:
                with self.assertRaises(aiohttp.ClientError):
                    async with session.get(self.url(), allow_redirects=False):
                        pass
            self.assertEqual(self.wire.off_host, [(PROXY, 3128)])

    async def test_a_lane_session_keeps_the_timeout_it_is_given(self):
        timeout = aiohttp.ClientTimeout(total=7)
        async with subject.lane_session(timeout=timeout) as session:
            self.assertIs(session.timeout, timeout)

    def test_the_status_check_refuses_exactly_the_3xx_answers(self):
        for status in (100, 101, 200, 204, 299, 400, 404, 500):
            self.assertIsNone(subject.refuse_redirect(status))
        for status in (300, 301, 302, 303, 304, 305, 307, 308, 399):
            with self.subTest(status=status), self.assertRaises(Exception) as raised:
                subject.refuse_redirect(status)
            self.assertIs(type(raised.exception), subject.LaneRedirectRefused)
            self.assertEqual(raised.exception.args, (REFUSED,))

    def test_the_refusal_is_one_fixed_code_that_no_readiness_loop_waits_through(self):
        self.assertEqual(subject.REDIRECT_REFUSED, REFUSED)
        self.assertEqual(subject.LaneRedirectRefused.__mro__,
                         (subject.LaneRedirectRefused, Exception, BaseException, object))
        for waited_through in (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, OSError, ValueError):
            self.assertFalse(issubclass(subject.LaneRedirectRefused, waited_through))

    def test_a_lane_receipt_shows_the_redirect_code_and_no_other_code_of_these_clients(self):
        import lane_failure_diagnostics as diagnostics

        def shown(error):
            with patch.object(diagnostics, "_raw_log", return_value=False):
                return diagnostics.failure(error)["exception_chain"][0]["message"]

        self.assertIn(REFUSED, diagnostics.SAFE_MESSAGES)
        self.assertEqual(shown(subject.LaneRedirectRefused(REFUSED)), REFUSED)
        # Every other refusal these clients raise is still shown as redacted.
        others = (entity.RAW_URL_REFUSED, entity.RAW_COMMAND_REFUSED, harness.CORE_URL_REFUSED,
                  harness.CORE_PATH_REFUSED, harness.CORE_TARGET_REFUSED, harness.FLOW_ID_REFUSED,
                  harness.LANE_CORE_REFUSED, harness.LANE_SUBNET_REFUSED, harness.LANE_CORE_OUTSIDE,
                  harness.LANE_CORE_ALREADY_SET, a2_runner.ADMIN_TARGET_REFUSED,
                  "lane_stub_outside_created_network", "lane_network_not_created_by_run",
                  "lane_network_subnet_unreadable", "lane_container_network_not_created_by_run")
        self.assertEqual(len(set(others)), len(others))
        for code in others:
            with self.subTest(code=code):
                self.assertIs(type(code), str)
                self.assertNotIn(code, diagnostics.SAFE_MESSAGES)
                self.assertEqual(shown(ValueError(code)), "[redacted]")
        self.assertEqual(sorted(code for code in diagnostics.SAFE_MESSAGES if "refused" in code), [REFUSED])


def calls(tree):
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def named(call):
    """The dotted name a call is made by, as written."""
    return ast.unparse(call.func)


class Inventory(unittest.TestCase):
    """Every session of the lane scripts is the shared one; the list is closed here."""

    # script: (lane_session calls, websocket connections)
    SCRIPTS = {
        "marketplace_read_contract_acceptance.py": (1, 1),
        "security_metadata_contract_acceptance.py": (1, 1),
        "entity_metadata_contract_acceptance.py": (1, 1),
        "real_ha_contract_tests.py": (3, 0),
        # No session of their own: the created-object client dials through the shared
        # ``call`` above and its runner signs in through the harness's bootstrap.
        "created_object_contract_acceptance.py": (0, 0),
        "core_created_object_local.py": (0, 0),
        # The Thread/Matter lanes: A1's client; the A2 client is a subclass of it with no
        # session of its own; the A2 runner's REST client; the A1 runner has none.
        "thread_matter_topology_contract_acceptance.py": (1, 1),
        "thread_matter_topology_a2_acceptance.py": (0, 0),
        "core_thread_matter_topology_local.py": (0, 0),
        "core_thread_matter_topology_a2_local.py": (1, 0),
    }
    # script: the requests it makes on a session, each inside ``async with``
    REQUESTS = {
        "real_ha_contract_tests.py": ["session.get", "session.get", "session.request"],
        "core_thread_matter_topology_a2_local.py": ["session.request"],
    }
    VERBS = {"request", "get", "post", "put", "patch", "delete", "head", "options"}

    def tree(self, name):
        return ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))

    def test_no_lane_script_builds_a_session_or_opens_a_url_of_its_own(self):
        for name, (sessions, sockets) in self.SCRIPTS.items():
            with self.subTest(script=name):
                made = [named(call) for call in calls(self.tree(name))]
                self.assertEqual([item for item in made if item.endswith("urlopen")], [])
                self.assertEqual([item for item in made if item != "ClientSession" and item.endswith("ClientSession")],
                                 [])
                self.assertEqual(made.count("lane_session"), sessions)
                self.assertEqual(len([item for item in made if item.endswith(".ws_connect")]), sockets)

    def test_the_one_other_session_of_the_harness_is_the_protocol_client_not_an_http_session(self):
        tree = self.tree("real_ha_contract_tests.py")
        imported = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    and any(alias.name == "ClientSession" and alias.asname is None for alias in node.names)]
        self.assertEqual(imported, ["mcp"])
        built = [ast.unparse(call) for call in calls(tree) if named(call) == "ClientSession"]
        self.assertEqual(built, ["ClientSession(read_stream, write_stream)"])
        for name in set(self.SCRIPTS) - {"real_ha_contract_tests.py"}:
            self.assertEqual([call for call in calls(self.tree(name)) if named(call) == "ClientSession"], [])

    def test_every_websocket_of_a_lane_script_is_opened_on_its_lane_session(self):
        for name, (_sessions, sockets) in self.SCRIPTS.items():
            opened = 0
            for function in ast.walk(self.tree(name)):
                if not isinstance(function, ast.AsyncFunctionDef):
                    continue
                dialled = [call for call in calls(function) if named(call).endswith(".ws_connect")]
                if not dialled:
                    continue
                bound = [item.optional_vars.id for node in ast.walk(function) if isinstance(node, ast.AsyncWith)
                         for item in node.items
                         if isinstance(item.context_expr, ast.Call) and named(item.context_expr) == "lane_session"]
                self.assertEqual(len(bound), 1, name)
                self.assertEqual([named(call) for call in dialled], [bound[0] + ".ws_connect"], name)
                opened += 1
            self.assertEqual(opened, sockets, name)

    def test_every_harness_request_declines_redirects_and_checks_the_status_first(self):
        for name in self.SCRIPTS:
            with self.subTest(script=name):
                tree = self.tree(name)
                sent = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncWith)
                        and isinstance(node.items[0].context_expr, ast.Call)
                        and named(node.items[0].context_expr) in {"session." + verb for verb in self.VERBS}]
                self.assertEqual(sorted(named(node.items[0].context_expr) for node in sent),
                                 self.REQUESTS.get(name, []))
                for node in sent:
                    call = node.items[0].context_expr
                    self.assertEqual([ast.unparse(word.value) for word in call.keywords
                                      if word.arg == "allow_redirects"], ["False"])
                    self.assertEqual(ast.unparse(node.body[0]),
                                     f"refuse_redirect({node.items[0].optional_vars.id}.status)")
                # No request leaves a session any other way: not awaited bare, and not under another name.
                every = [named(call) for call in calls(tree)
                         if isinstance(call.func, ast.Attribute) and call.func.attr in self.VERBS
                         and isinstance(call.func.value, ast.Name) and call.func.value.id == "session"]
                self.assertEqual(len(every), len(sent))


if __name__ == "__main__":
    unittest.main()
