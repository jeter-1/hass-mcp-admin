"""Offline network guard (HAMCP-363, rules day lock 4).

Pins which hosts and addresses tests/support/offline_network.py allows and
refuses, that a refusal comes before the original call runs, and that every way
the unit suite is started installs the guard: discovery, a dotted run, an
isolated-module run and CI's shard runner. A negative control shows the entry
tests fail when the package hooks are missing. Connections made here go only
to disposable loopback peers; while refusals are exercised, the guard's
originals are replaced by recorders that fail the test if reached.
"""

import asyncio
import contextlib
import fnmatch
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.request

import aiohttp
from aiohttp import web
import httpx
from yarl import URL

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from support import offline_network  # noqa: E402

SHARD_SCRIPT = ROOT / "scripts" / "unit_test_shards.py"
UNIX = getattr(socket, "AF_UNIX", None)
INET = SimpleNamespace(family=socket.AF_INET)
INET6 = SimpleNamespace(family=socket.AF_INET6)
# A documentation address (RFC 5737) and a name; the guard looks up neither.
REFUSED_HOSTS = ("192.0.2.1", "example.com")
PLAN = {"schema_version": 1, "general_shards": 1, "default_module_seconds": 1, "module_seconds": {}}
LOOPBACK_INFOS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
                  (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 80, 0, 0))]
MIXED_INFOS = [*LOOPBACK_INFOS, (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 80))]
DISCOVER = ("-m", "unittest", "discover", "-s", "tests", "-v")
DOTTED = ("-m", "unittest", "-v", "tests.test_offline_loopback", "tests.test_offline_refused")
ISOLATED = ("-m", "unittest", "discover", "-s", "tests", "-t", "tests", "-p", "test_offline_*.py", "-v")
MODES = {"discovery": DISCOVER, "dotted": DOTTED, "isolated module": ISOLATED}

# The rules inventory's throwaway test. The guard's originals are replaced
# first, so a guard that let the call through fails here without a lookup.
REFUSED_TEST = '''\
import socket
import unittest

MARKER = @MARKER@


def reached(key):
    def call(*args, **kwargs):
        raise AssertionError("original reached: " + key)
    return call


class Refused(unittest.TestCase):
    def test_refused(self):
        state = getattr(socket.socket.connect, MARKER, None)
        if state is None:
            self.fail("offline guard not installed")
        for key in list(vars(state)):
            if key != "error":
                self.addCleanup(setattr, state, key, getattr(state, key))
                setattr(state, key, reached(key))
        socket.create_connection(("example.com", 80), timeout=1)
'''

LOOPBACK_TEST = '''\
import socket
import unittest


class Loopback(unittest.TestCase):
    def test_loopback(self):
        with socket.create_server(("127.0.0.1", 0)) as server:
            server.settimeout(5)
            with socket.create_connection(server.getsockname(), timeout=5) as client:
                peer, _address = server.accept()
                with peer:
                    peer.settimeout(5)
                    client.sendall(b"ping")
                    client.shutdown(socket.SHUT_WR)
                    with peer.makefile("rb") as stream:
                        self.assertEqual(stream.read(), b"ping")
'''


def target_url(host, port=None):
    """An HTTP URL for ``host``, built without a URL literal."""
    return str(URL.build(scheme="http", host=host, port=port, path="/"))


def originals(calls, **results):
    """Guard state whose originals record their calls instead of using the network."""
    def recorder(key):
        def call(*args, **kwargs):
            calls.append((key, args, kwargs))
            return results.get(key, key)
        return call

    def async_recorder(key):
        async def call(*args, **kwargs):
            calls.append((key, args, kwargs))
            return results.get(key, key)
        return call

    return SimpleNamespace(error=offline_network.SocketBlockedError,
                           **{key: (async_recorder if key.startswith("proactor_") else recorder)(key)
                              for key, _owner, _name in offline_network.TARGETS})


def delegate(function):
    """A stand-in for an installed function: same behaviour, no marker."""
    def call(*args, **kwargs):
        return function(*args, **kwargs)
    return call


def leaves(error):
    """The innermost exceptions of an exception group."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in leaves(inner)]
    return [error]


def present_targets():
    return [(key, owner, name) for key, owner, name in offline_network.TARGETS if hasattr(owner, name)]


def forget_tests_package(before):
    """Drop the ``tests`` package modules imported since ``before``."""
    for name in set(sys.modules) - before:
        if name == "tests" or name.startswith("tests."):
            del sys.modules[name]


class OfflineGuardClassificationTests(unittest.TestCase):
    def test_numeric_loopback_hosts_are_allowed_without_a_lookup(self):
        for host in ("127.0.0.1", "127.255.255.254", "::1", "::1%lo", "::ffff:127.0.0.1",
                     b"127.0.0.1", bytearray(b"::1")):
            with self.subTest(host=host):
                calls = []
                self.assertTrue(offline_network.host_allowed(originals(calls), host))
                self.assertEqual(calls, [])

    def test_other_hosts_are_refused_without_a_lookup(self):
        for host in ("", "<broadcast>", "0.0.0.0", "::", "192.0.2.1", "2001:db8::1", "fe80::1%eth0",
                     "::ffff:192.0.2.1", "::127.0.0.1", "127.1", "0x7f.1", "2130706433", 2130706433,
                     "LOCALHOST", "localhost.", "example.com", None, b"\xff", b"example.com"):
            with self.subTest(host=host):
                calls = []
                self.assertFalse(offline_network.host_allowed(originals(calls), host))
                self.assertEqual(calls, [])

    def test_localhost_is_allowed_only_when_every_address_is_loopback(self):
        for infos, allowed in ((LOOPBACK_INFOS, True), (MIXED_INFOS, False), ([], False)):
            with self.subTest(infos=infos):
                calls = []
                state = originals(calls, getaddrinfo=infos)
                self.assertIs(offline_network.host_allowed(state, "localhost", socket.AF_INET), allowed)
                self.assertEqual(calls, [("getaddrinfo", ("localhost", None, socket.AF_INET), {})])

    @unittest.skipIf(UNIX is None, "no AF_UNIX on this platform")
    def test_every_unix_address_is_allowed(self):
        for address in ("peer.sock", b"peer.sock", "\0hamcp363"):
            with self.subTest(address=address):
                calls = []
                self.assertTrue(offline_network.address_allowed(originals(calls), UNIX, address))
                self.assertEqual(calls, [])

    def test_other_families_are_refused(self):
        families = [socket.AF_UNSPEC, 9999] + [getattr(socket, name) for name in ("AF_PACKET", "AF_NETLINK", "AF_VSOCK")
                                               if hasattr(socket, name)]
        for family in families:
            with self.subTest(family=family):
                calls = []
                self.assertFalse(offline_network.address_allowed(originals(calls), family, ("127.0.0.1", 80)))
                self.assertEqual(calls, [])

    def test_malformed_inet_addresses_are_refused(self):
        for address in ("127.0.0.1", b"127.0.0.1", (), ["127.0.0.1", 80], None):
            with self.subTest(address=address):
                calls = []
                self.assertFalse(offline_network.address_allowed(originals(calls), socket.AF_INET, address))
                self.assertEqual(calls, [])


class OfflineGuardWrapperTests(unittest.TestCase):
    def functions(self, **results):
        self.calls = []
        return offline_network.wrappers(originals(self.calls, **results))

    def assertRefused(self, function, *args, calls=()):
        with self.assertRaises(offline_network.SocketBlockedError) as caught:
            function(*args)
        self.assertEqual(str(caught.exception), offline_network.MESSAGE)
        self.assertNotIsInstance(caught.exception, OSError)
        self.assertEqual(self.calls, list(calls))

    def test_wrappers_cover_every_target(self):
        self.assertEqual(set(self.functions()), {key for key, _owner, _name in offline_network.TARGETS})

    def test_loopback_calls_reach_the_original(self):
        loopback = ("127.0.0.1", 80)
        cases = [
            ("connect", (INET, loopback)), ("connect", (INET6, ("::1", 80, 0, 0))),
            ("connect_ex", (INET, loopback)),
            ("sendto", (INET, b"x", loopback)), ("sendto", (INET, b"x", 0, loopback)), ("sendto", (INET, b"x")),
            ("sendmsg", (INET, [b"x"])), ("sendmsg", (INET, [b"x"], [], 0, None)),
            ("sendmsg", (INET, [b"x"], [], 0, loopback)),
            ("gethostbyname", ("127.0.0.1",)), ("gethostbyname_ex", ("127.0.0.1",)),
            ("gethostbyaddr", ("127.0.0.1",)), ("gethostbyaddr", ("::1",)),
            ("getnameinfo", (loopback, 0)), ("getnameinfo", (("::1", 80, 0, 0), socket.NI_NUMERICHOST)),
        ]
        if UNIX is not None:
            cases.append(("connect", (SimpleNamespace(family=UNIX), "peer.sock")))
        for key, args in cases:
            with self.subTest(key=key, args=args):
                self.assertEqual(self.functions()[key](*args), key)
                self.assertEqual(self.calls, [(key, args, {})])

    def test_getaddrinfo_passes_through_hosts_it_does_not_look_up(self):
        for host in (None, "0.0.0.0", "::", "127.0.0.1", "::1", b"127.0.0.1"):
            with self.subTest(host=host):
                args = (host, 80, socket.AF_UNSPEC, socket.SOCK_STREAM)
                self.assertEqual(self.functions()["getaddrinfo"](*args, flags=socket.AI_PASSIVE), "getaddrinfo")
                self.assertEqual(self.calls, [("getaddrinfo", args, {"flags": socket.AI_PASSIVE})])

    def test_localhost_lookups_return_only_loopback_answers(self):
        lookup = ("getaddrinfo", ("localhost", 80), {})
        self.assertIs(self.functions(getaddrinfo=LOOPBACK_INFOS)["getaddrinfo"]("localhost", 80), LOOPBACK_INFOS)
        self.assertEqual(self.calls, [lookup])
        for infos in (MIXED_INFOS, []):
            with self.subTest(infos=infos):
                self.assertRefused(self.functions(getaddrinfo=infos)["getaddrinfo"], "localhost", 80, calls=[lookup])
        good = {"gethostbyname": "127.0.0.1", "gethostbyname_ex": ("localhost", [], ["127.0.0.1"]),
                "gethostbyaddr": ("localhost", [], ["127.0.0.1"])}
        for key, answer in good.items():
            with self.subTest(key=key, answer=answer):
                self.assertIs(self.functions(**{key: answer})[key]("localhost"), answer)
                self.assertEqual(self.calls, [(key, ("localhost",), {})])
        mixed = ("localhost", [], ["127.0.0.1", "192.0.2.1"])
        bad = {"gethostbyname": ["192.0.2.1"], "gethostbyname_ex": [mixed, ("localhost", [], [])],
               "gethostbyaddr": [mixed, ("localhost", [], [])]}
        for key, answers in bad.items():
            for answer in answers:
                with self.subTest(key=key, answer=answer):
                    self.assertRefused(self.functions(**{key: answer})[key], "localhost",
                                       calls=[(key, ("localhost",), {})])

    def test_localhost_connect_looks_up_then_checks_every_address(self):
        lookup = ("getaddrinfo", ("localhost", None, socket.AF_INET), {})
        address = ("localhost", 80)
        self.assertEqual(self.functions(getaddrinfo=LOOPBACK_INFOS)["connect"](INET, address), "connect")
        self.assertEqual(self.calls, [lookup, ("connect", (INET, address), {})])
        self.assertRefused(self.functions(getaddrinfo=MIXED_INFOS)["connect"], INET, address, calls=[lookup])

    def test_non_loopback_calls_are_refused_before_the_original(self):
        cases = [
            ("connect", INET, ("192.0.2.1", 80)), ("connect", INET, ("0.0.0.0", 80)),
            ("connect", INET, ("example.com", 80)), ("connect", INET6, ("2001:db8::1", 80, 0, 0)),
            ("connect", SimpleNamespace(family=9999), ("127.0.0.1", 80)),
            ("connect_ex", INET, ("192.0.2.1", 80)),
            ("sendto", INET, b"x", ("192.0.2.1", 9)), ("sendto", INET, b"x", 0, ("example.com", 9)),
            ("sendmsg", INET, [b"x"], [], 0, ("192.0.2.1", 9)),
            ("getaddrinfo", "example.com", 80), ("getaddrinfo", "192.0.2.1", 80),
            ("getaddrinfo", b"example.com", 80), ("getaddrinfo", "LOCALHOST", 80),
            ("getaddrinfo", "", 80), ("getaddrinfo", b"", 80),
            ("gethostbyname", "example.com"), ("gethostbyname_ex", "example.com"),
            ("gethostbyaddr", "192.0.2.1"), ("gethostbyaddr", "example.com"),
            ("getnameinfo", ("192.0.2.1", 80), 0), ("getnameinfo", ("localhost", 80), 0),
            ("getnameinfo", "127.0.0.1", 0), ("getnameinfo", (), 0),
        ]
        for key, *args in cases:
            with self.subTest(key=key, args=args):
                self.assertRefused(self.functions()[key], *args)


class OfflineGuardProactorTests(unittest.IsolatedAsyncioTestCase):
    async def test_proactor_calls_check_the_socket_family_and_address(self):
        for key, extra in (("proactor_sock_connect", ()), ("proactor_sock_sendto", (b"x",))):
            with self.subTest(key=key):
                calls = []
                function = offline_network.wrappers(originals(calls))[key]
                loop = object()
                allowed = (loop, INET, *extra, ("127.0.0.1", 9))
                self.assertEqual(await function(*allowed), key)
                self.assertEqual(calls, [(key, allowed, {})])
                with self.assertRaises(offline_network.SocketBlockedError):
                    await function(loop, INET, *extra, ("192.0.2.1", 9))
                self.assertEqual(calls, [(key, allowed, {})])


class OfflineGuardInstallTests(unittest.TestCase):
    def test_guard_is_installed_once_over_the_originals(self):
        state = offline_network.STATE
        self.assertIs(offline_network.installed(), state)
        self.assertIs(state.error, offline_network.SocketBlockedError)
        self.assertTrue(issubclass(offline_network.SocketBlockedError, RuntimeError))
        self.assertFalse(issubclass(offline_network.SocketBlockedError, OSError))
        expected = {key for key, _owner, _name in offline_network.TARGETS}
        if sys.platform == "win32":
            expected.discard("sendmsg")
        self.assertEqual(set(vars(state)), {"error"} | expected)
        for key, owner, name in present_targets():
            with self.subTest(key=key):
                function = getattr(owner, name)
                self.assertIs(getattr(function, offline_network.MARKER), state)
                self.assertIs(function.__wrapped__, getattr(state, key))
                self.assertIsNone(getattr(getattr(state, key), offline_network.MARKER, None))
        self.assertIs(offline_network.install(), state)

    def test_one_replaced_target_does_not_install_the_guard_again(self):
        state = offline_network.STATE
        for key, owner, name in present_targets():
            with self.subTest(key=key):
                stand_in = delegate(getattr(owner, name))
                with mock.patch.object(owner, name, stand_in):
                    self.assertIs(offline_network.installed(), state)
                    self.assertIs(offline_network.install(), state)
                    self.assertIs(getattr(owner, name), stand_in)
                self.assertIs(getattr(getattr(owner, name), offline_network.MARKER), state)

    def test_install_wraps_whatever_is_in_place_when_no_target_is_guarded(self):
        state = offline_network.STATE
        stubs = {}
        with contextlib.ExitStack() as stack:
            for key, owner, name in present_targets():
                stubs[key] = delegate(getattr(owner, name))
                stack.enter_context(mock.patch.object(owner, name, stubs[key]))
            self.assertIsNone(offline_network.installed())
            fresh = offline_network.install()
            self.assertIsNot(fresh, state)
            self.assertIs(offline_network.installed(), fresh)
            self.assertIs(fresh.error, offline_network.SocketBlockedError)
            for key, owner, name in present_targets():
                with self.subTest(key=key):
                    function = getattr(owner, name)
                    self.assertIs(getattr(function, offline_network.MARKER), fresh)
                    self.assertIs(function.__wrapped__, stubs[key])
                    self.assertIs(getattr(fresh, key), stubs[key])
        for key, owner, name in present_targets():
            with self.subTest(key=key, restored=True):
                self.assertIs(getattr(getattr(owner, name), offline_network.MARKER), state)

    def test_a_second_copy_of_the_module_shares_the_installed_guard(self):
        before = {key: getattr(owner, name) for key, owner, name in present_targets()}
        spec = importlib.util.spec_from_file_location("hamcp363_offline_network_copy", offline_network.__file__)
        second = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(second)
        self.assertIs(second.STATE, offline_network.STATE)
        self.assertIs(second.SocketBlockedError, offline_network.SocketBlockedError)
        self.assertIsNot(second.wrappers, offline_network.wrappers)
        for key, owner, name in present_targets():
            with self.subTest(key=key):
                self.assertIs(getattr(owner, name), before[key])

    def test_discovery_and_dotted_import_names_share_one_guard(self):
        self.addCleanup(forget_tests_package, set(sys.modules))
        with mock.patch.object(sys, "path", [str(ROOT), str(ROOT / "tests"), *sys.path]):
            modules = [importlib.import_module(name)
                       for name in ("support.offline_network", "tests.support.offline_network")]
        for module in modules:
            with self.subTest(module=module.__name__):
                self.assertIs(module.STATE, offline_network.STATE)
                self.assertIs(module.SocketBlockedError, offline_network.SocketBlockedError)
        self.assertEqual(Path(modules[0].__file__).resolve(), Path(modules[1].__file__).resolve())


class OfflineGuardLoopbackTests(unittest.TestCase):
    def roundtrip(self, server, address):
        with server:
            server.settimeout(5)
            with socket.create_connection(address, timeout=5) as client:
                peer, _address = server.accept()
                with peer:
                    peer.settimeout(5)
                    client.sendall(b"ping")
                    client.shutdown(socket.SHUT_WR)
                    with peer.makefile("rb") as stream:
                        self.assertEqual(stream.read(), b"ping")

    def test_tcp_to_ipv4_loopback(self):
        server = socket.create_server(("127.0.0.1", 0))
        self.roundtrip(server, server.getsockname())

    def test_tcp_to_ipv6_loopback(self):
        try:
            server = socket.create_server(("::1", 0), family=socket.AF_INET6)
        except OSError as error:
            self.skipTest(f"no IPv6 loopback: {error}")
        self.roundtrip(server, server.getsockname()[:2])

    def test_tcp_to_localhost(self):
        server = socket.create_server(("127.0.0.1", 0))
        self.roundtrip(server, ("localhost", server.getsockname()[1]))

    def test_a_refused_loopback_connection_stays_an_oserror(self):
        with socket.create_server(("127.0.0.1", 0)) as server:
            address = server.getsockname()
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection(address, timeout=5)

    def test_udp_to_loopback(self):
        receiver = self.enterContext(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(5)
        address = receiver.getsockname()
        sender = self.enterContext(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        sender.sendto(b"one", address)
        sender.sendto(b"two", 0, address)
        expected = [b"one", b"two"]
        if hasattr(sender, "sendmsg"):
            sender.sendmsg([b"three"], [], 0, address)
            expected.append(b"three")
        self.assertEqual([receiver.recvfrom(64)[0] for _ in expected], expected)
        # A malformed call still fails the way the original does.
        with self.assertRaises(TypeError):
            sender.sendto(b"x")

    def test_socketpair(self):
        left, right = socket.socketpair()
        with left, right:
            left.settimeout(5)
            right.settimeout(5)
            left.sendall(b"pair")
            self.assertEqual(right.recv(4), b"pair")

    @unittest.skipUnless(sys.platform.startswith("linux"), "abstract AF_UNIX addresses are Linux-only")
    def test_abstract_unix_socket(self):
        address = f"\0hamcp363-{os.getpid()}-{id(self)}"
        server = self.enterContext(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
        server.bind(address)
        server.listen(1)
        server.settimeout(5)
        client = self.enterContext(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
        client.settimeout(5)
        client.connect(address)
        peer, _address = server.accept()
        with peer:
            peer.settimeout(5)
            client.sendall(b"unix")
            self.assertEqual(peer.recv(4), b"unix")


class OfflineGuardLoopbackClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def handler(_request):
            return web.Response(text="offline")

        app = web.Application()
        app.router.add_get("/", handler)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        self.url = target_url("127.0.0.1", runner.addresses[0][1])

    async def test_aiohttp_reaches_a_loopback_peer(self):
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.get(self.url) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.text(), "offline")

    async def test_httpx_reaches_a_loopback_peer(self):
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "offline")


class RefusalRecorder:
    """Replaces the guard's originals with recorders that fail the test if reached."""

    def setUp(self):
        super().setUp()
        self.reached = []
        for key in list(vars(offline_network.STATE)):
            if key != "error":
                self.enterContext(mock.patch.object(offline_network.STATE, key, self.recorder(key)))

    def recorder(self, key):
        def call(*args, **kwargs):
            self.reached.append(key)
            raise AssertionError("original reached: " + key)
        return call

    def assertBlocked(self, error):
        self.assertIsInstance(error, offline_network.SocketBlockedError)
        self.assertEqual(str(error), offline_network.MESSAGE)
        self.assertEqual(self.reached, [])

    def assertCallBlocked(self, function, *args, **kwargs):
        with self.assertRaises(offline_network.SocketBlockedError) as caught:
            function(*args, **kwargs)
        self.assertBlocked(caught.exception)


class OfflineGuardBlockedSyncTests(RefusalRecorder, unittest.TestCase):
    def test_stream_connects_are_refused(self):
        for address in (("192.0.2.1", 80), ("example.com", 80)):
            with self.subTest(address=address), socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
                self.assertCallBlocked(stream.connect, address)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
            self.assertCallBlocked(stream.connect_ex, ("192.0.2.1", 80))

    def test_ipv6_connect_is_refused(self):
        try:
            stream = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        except OSError as error:
            self.skipTest(f"no IPv6: {error}")
        with stream:
            self.assertCallBlocked(stream.connect, ("2001:db8::1", 80, 0, 0))

    def test_datagrams_are_refused(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
            self.assertCallBlocked(datagram.sendto, b"x", ("192.0.2.1", 9))
            self.assertCallBlocked(datagram.sendto, b"x", 0, ("192.0.2.1", 9))
            self.assertCallBlocked(datagram.sendto, b"x", ("example.com", 9))
            if hasattr(datagram, "sendmsg"):
                self.assertCallBlocked(datagram.sendmsg, [b"x"], [], 0, ("192.0.2.1", 9))

    def test_create_connection_is_refused(self):
        for host in REFUSED_HOSTS:
            with self.subTest(host=host):
                self.assertCallBlocked(socket.create_connection, (host, 80), timeout=1)

    def test_lookups_are_refused(self):
        calls = [(socket.getaddrinfo, "example.com", 80), (socket.getaddrinfo, "192.0.2.1", 80),
                 (socket.gethostbyname, "example.com"), (socket.gethostbyname_ex, "example.com"),
                 (socket.gethostbyaddr, "192.0.2.1"), (socket.getnameinfo, ("192.0.2.1", 80), 0),
                 (socket.getfqdn, "example.com")]
        for function, *args in calls:
            with self.subTest(function=function.__name__, args=args):
                self.assertCallBlocked(function, *args)

    def test_http_clients_are_refused(self):
        client = self.enterContext(httpx.Client(trust_env=False))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for host in REFUSED_HOSTS:
            with self.subTest(client="httpx", host=host):
                self.assertCallBlocked(client.get, target_url(host), timeout=1)
            with self.subTest(client="urllib", host=host):
                self.assertCallBlocked(opener.open, target_url(host), timeout=1)


class OfflineGuardBlockedAsyncTests(RefusalRecorder, unittest.IsolatedAsyncioTestCase):
    async def assertAwaitBlocked(self, awaitable):
        with self.assertRaises(offline_network.SocketBlockedError) as caught:
            await awaitable
        self.assertBlocked(caught.exception)

    async def test_asyncio_open_connection_is_refused(self):
        for host in REFUSED_HOSTS:
            with self.subTest(host=host):
                await self.assertAwaitBlocked(asyncio.wait_for(asyncio.open_connection(host, 80), 5))

    async def test_aiohttp_is_refused(self):
        async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=5)) as session:
            for host in REFUSED_HOSTS:
                with self.subTest(host=host):
                    with self.assertRaises(offline_network.SocketBlockedError) as caught:
                        async with session.get(target_url(host)):
                            pass
                    self.assertBlocked(caught.exception)

    async def test_httpx_is_refused_for_a_name(self):
        async with httpx.AsyncClient(trust_env=False) as client:
            await self.assertAwaitBlocked(client.get(target_url("example.com"), timeout=5))

    async def test_httpx_is_refused_for_an_address(self):
        # anyio connects to a numeric address inside a task group.
        async with httpx.AsyncClient(trust_env=False) as client:
            with self.assertRaises(BaseExceptionGroup) as caught:
                await client.get(target_url("192.0.2.1"), timeout=5)
        errors = leaves(caught.exception)
        self.assertTrue(errors)
        for error in errors:
            self.assertBlocked(error)


class OfflineGuardPackageHookTests(unittest.TestCase):
    def test_nothing_discovery_imports_sorts_before_the_support_package(self):
        # Discovery imports every package without a pattern check, in sorted order.
        for entry in sorted(os.listdir(ROOT / "tests")):
            if entry == "support":
                break
            with self.subTest(entry=entry):
                self.assertFalse((ROOT / "tests" / entry / "__init__.py").exists())
                self.assertFalse(fnmatch.fnmatchcase(entry.lower(), "test*.py"))
        else:
            self.fail("tests/support is missing")


class OfflineGuardSuiteEntryTests(unittest.TestCase):
    def tree(self, hooks):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "source"
        (root / "tests" / "support").mkdir(parents=True)
        for relative in ("tests/__init__.py", "tests/support/__init__.py"):
            if hooks:
                shutil.copyfile(ROOT / relative, root / relative)
            else:
                (root / relative).write_text("", encoding="utf-8")
        shutil.copyfile(ROOT / "tests/support/offline_network.py", root / "tests/support/offline_network.py")
        refused = REFUSED_TEST.replace("@MARKER@", repr(offline_network.MARKER))
        (root / "tests/test_offline_refused.py").write_text(refused, encoding="utf-8")
        (root / "tests/test_offline_loopback.py").write_text(LOOPBACK_TEST, encoding="utf-8")
        return root

    def python(self, root, *args, extra=None):
        # PYTHONSAFEPATH would drop the working directory, which a dotted run imports from.
        env = {key: value for key, value in os.environ.items()
               if key not in {"PYTHONPATH", "PYTHONSAFEPATH", "PYTHONSTARTUP"}}
        env.update(extra or {})
        return subprocess.run([sys.executable, "-B", *args], cwd=root, env=env, capture_output=True,
                              text=True, timeout=120, check=False)

    def commit(self, root):
        for args in (("init", "-q"), ("config", "user.name", "Synthetic test"),
                     ("config", "user.email", "synthetic@example.invalid"),
                     ("add", "."), ("commit", "-qm", "Synthetic source")):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, timeout=60)

    def assertGuarded(self, result):
        self.assertEqual(result.returncode, 1, result.stderr)
        for needle in ("SocketBlockedError: " + offline_network.MESSAGE, "Refused.test_refused) ... ERROR",
                       "Loopback.test_loopback) ... ok", "Ran 2 tests"):
            self.assertIn(needle, result.stderr)
        for needle in ("original reached", "offline guard not installed"):
            self.assertNotIn(needle, result.stderr)

    def test_discovery_dotted_and_isolated_runs_install_the_guard(self):
        root = self.tree(hooks=True)
        for mode, args in MODES.items():
            with self.subTest(mode=mode):
                self.assertGuarded(self.python(root, *args))

    def test_ci_shard_runner_installs_the_guard(self):
        root = self.tree(hooks=True)
        (root / "scripts").mkdir()
        shutil.copyfile(SHARD_SCRIPT, root / "scripts/unit_test_shards.py")
        (root / "ci").mkdir()
        (root / "ci/unit-test-shards.json").write_text(json.dumps(PLAN), encoding="utf-8")
        self.commit(root)
        result = self.python(root, "scripts/unit_test_shards.py", "run", "--shard", "0", "--output", "manifest.json",
                             extra={"GITHUB_RUN_ID": "1001", "GITHUB_RUN_ATTEMPT": "1"})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Shard 0: 2 of 2 tests executed.", result.stdout)
        self.assertIn("Unit test shard did not succeed.", result.stderr)
        self.assertIn("SocketBlockedError: " + offline_network.MESSAGE, result.stderr)
        self.assertNotIn("original reached", result.stdout + result.stderr)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        loopback, refused = "test_offline_loopback.Loopback.test_loopback", "test_offline_refused.Refused.test_refused"
        self.assertFalse(manifest["was_successful"])
        self.assertEqual(manifest["assigned"], [loopback, refused])
        self.assertEqual(sorted(manifest["executed"]), [[loopback, "success"], [refused, "error"]])
        self.assertEqual(manifest["scratch_leaks"], [])
        self.assertEqual(list((root / ".artifacts/tmp").iterdir()), [])

    def test_entry_tests_fail_without_the_package_hooks(self):
        root = self.tree(hooks=False)
        for mode, args in MODES.items():
            with self.subTest(mode=mode):
                result = self.python(root, *args)
                self.assertEqual(result.returncode, 1, result.stderr)
                for needle in ("offline guard not installed", "Refused.test_refused) ... FAIL",
                               "Loopback.test_loopback) ... ok"):
                    self.assertIn(needle, result.stderr)
                self.assertNotIn("SocketBlockedError", result.stderr)


if __name__ == "__main__":
    unittest.main()
