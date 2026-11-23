"""Offline network guard for the unittest suite (HAMCP-363, rules day lock 4).

Unit tests stay offline: the suite refuses any connection, datagram or name
lookup that is not loopback, before anything is sent, so no test can reach
live Home Assistant, the house network or the internet by accident (a stub
left out, an address read from the environment). Disposable loopback peers and
AF_UNIX sockets stay allowed (tests/AGENTS.md).

Installed on import, like process_isolation. Discovery imports packages
without a pattern check, and ``support`` sorts before every ``test*`` module,
so ``python -m unittest discover -s tests`` and CI's shards import
``tests/support`` (whose ``__init__`` imports this module) before the first
test module. A dotted run (``python -m unittest tests.test_x``, check.ps1's
Fast tier) imports ``tests/__init__``, which imports ``tests.support`` first.

A refusal raises ``SocketBlockedError``, a RuntimeError with one fixed message
that never names the target. asyncio, http.client and urllib3 retry or fall
back only on OSError, so the refusal reaches the test. aiohappyeyeballs
(aiohttp's connector) also tries the next address after a RuntimeError, then
re-raises the refusal when every address was refused, since the messages
match; a refusal mixed with a real loopback failure comes back as OSError, and
nothing has left loopback either way. anyio (httpx's async transport) connects
to a numeric address inside a task group, so there the refusal arrives wrapped
in an ExceptionGroup.

Allowed: AF_INET and AF_INET6 to a numeric loopback address (127.0.0.0/8,
``::1``, ``::ffff:127.0.0.0/104``), or to ``localhost`` when every address it
resolves to is loopback; ``getaddrinfo`` with no host, or with the unspecified
address (``0.0.0.0``, ``::``), which is parsed rather than looked up; every
AF_UNIX address. Everything else is refused, every other family and the empty
name included. That covers a lookup of the machine's own name, so an
``HTTPServer`` bound to the wildcard address fails at bind (``getfqdn`` looks
the name up); bind test servers to 127.0.0.1.

Guarded:

* ``socket.socket`` ``connect``, ``connect_ex``, ``sendto`` and ``sendmsg``;
* ``socket`` ``getaddrinfo``, ``gethostbyname``, ``gethostbyname_ex``,
  ``gethostbyaddr`` and ``getnameinfo``;
* the proactor event loop's ``sock_connect`` and ``sock_sendto``, which do not
  go through ``socket.socket.connect`` on Windows.

Not covered (accidental reach is the threat, not a hostile test):

* child processes, which do not import this package;
* calls made in C, or through ``_socket`` or ctypes directly;
* the Windows proactor datagram transport, which sends without ``sock_sendto``;
* a test module run as a script, or by bare name from inside ``tests/``;
* a test that catches RuntimeError or Exception around a refused call
  (nothing was sent);
* AF_UNIX peers, including a Docker socket;
* a proxy on loopback: a client that honours a proxy variable such as
  ``HTTPS_PROXY`` naming a loopback address connects there, and the proxy may
  forward anywhere;
* binding and listening on any address: only outbound use is refused;
* lookups of ``localhost`` and reverse lookups of loopback addresses
  (``HTTPServer`` calls ``getfqdn`` on its bound address), which go to the
  system resolver; it normally answers from /etc/hosts.
"""

from __future__ import annotations

import asyncio.proactor_events
import ipaddress
import socket
from types import SimpleNamespace

MESSAGE = "unit tests are offline; use fixtures under tests/fixtures."
# Set on every installed function; its value is the state holding the originals.
MARKER = "__hamcp_offline_network__"
LOCALHOST = "localhost"
# The unspecified address: getaddrinfo parses it rather than looking it up.
# Connecting to it is still refused, and so is a lookup of "" (a name).
UNSPECIFIED = frozenset({"0.0.0.0", "::"})
_INET = tuple(f for f in (socket.AF_INET, getattr(socket, "AF_INET6", None)) if f is not None)
_UNIX = getattr(socket, "AF_UNIX", None)
_PROACTOR = asyncio.proactor_events.BaseProactorEventLoop
TARGETS = (
    ("connect", socket.socket, "connect"), ("connect_ex", socket.socket, "connect_ex"),
    ("sendto", socket.socket, "sendto"), ("sendmsg", socket.socket, "sendmsg"),
    ("getaddrinfo", socket, "getaddrinfo"), ("gethostbyname", socket, "gethostbyname"),
    ("gethostbyname_ex", socket, "gethostbyname_ex"), ("gethostbyaddr", socket, "gethostbyaddr"),
    ("getnameinfo", socket, "getnameinfo"),
    ("proactor_sock_connect", _PROACTOR, "sock_connect"),
    ("proactor_sock_sendto", _PROACTOR, "sock_sendto"),
)


class SocketBlockedError(RuntimeError):
    """A non-loopback connection or lookup, refused before anything was sent."""


def installed():
    """Return the installed guard's state, or None.

    Every target is checked, so a test that mocks one of them while this
    module is imported a second time does not install the guard twice.
    """
    for _key, owner, name in TARGETS:
        state = getattr(getattr(owner, name, None), MARKER, None)
        if state is not None:
            return state
    return None


def _refuse():
    raise SocketBlockedError(MESSAGE)


def _text(host):
    if isinstance(host, str):
        return host
    if isinstance(host, (bytes, bytearray)):
        try:
            return bytes(host).decode("ascii")
        except UnicodeDecodeError:
            return None
    return None


def _numeric_loopback(host):  # str only: ip_address() also parses ints
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    # is_loopback unwraps ::ffff:127.0.0.0/104 itself (Python 3.12.14, CI's version).
    return address.is_loopback


def _all_loopback(infos):
    return bool(infos) and all(_numeric_loopback(info[4][0]) for info in infos)


def host_allowed(state, host, family=0):
    """True for a numeric loopback host, or ``localhost`` resolving only to loopback."""
    host = _text(host)
    if host is None:
        return False
    if _numeric_loopback(host):
        return True
    return host == LOCALHOST and _all_loopback(state.getaddrinfo(host, None, family))


def address_allowed(state, family, address):
    """True for every AF_UNIX address and an AF_INET or AF_INET6 loopback address."""
    if _UNIX is not None and family == _UNIX:
        return True
    if family not in _INET or not isinstance(address, tuple) or not address:
        return False
    return host_allowed(state, address[0], family)


def wrappers(state):
    """Return the guard's functions over ``state``'s originals, keyed like TARGETS."""
    def connect(self, address, /):
        if not address_allowed(state, self.family, address):
            _refuse()
        return state.connect(self, address)

    def connect_ex(self, address, /):
        if not address_allowed(state, self.family, address):
            _refuse()
        return state.connect_ex(self, address)

    def sendto(self, *args):
        if len(args) >= 2 and not address_allowed(state, self.family, args[-1]):
            _refuse()
        return state.sendto(self, *args)

    def sendmsg(self, *args):
        if len(args) >= 4 and args[3] is not None and not address_allowed(state, self.family, args[3]):
            _refuse()
        return state.sendmsg(self, *args)

    def getaddrinfo(host, port, *args, **kwargs):
        text = _text(host)
        if host is None or text in UNSPECIFIED or (text is not None and _numeric_loopback(text)):
            return state.getaddrinfo(host, port, *args, **kwargs)
        if text == LOCALHOST:
            infos = state.getaddrinfo(host, port, *args, **kwargs)
            if _all_loopback(infos):
                return infos
        _refuse()

    def gethostbyname(hostname, /):
        text = _text(hostname)
        if text is not None and _numeric_loopback(text):
            return state.gethostbyname(hostname)
        if text == LOCALHOST:
            address = state.gethostbyname(hostname)
            if _numeric_loopback(address):
                return address
        _refuse()

    def gethostbyname_ex(hostname, /):
        text = _text(hostname)
        if text is not None and _numeric_loopback(text):
            return state.gethostbyname_ex(hostname)
        if text == LOCALHOST:
            result = state.gethostbyname_ex(hostname)
            if result[2] and all(_numeric_loopback(a) for a in result[2]):
                return result
        _refuse()

    def gethostbyaddr(ip_address, /):
        text = _text(ip_address)
        if text is not None and _numeric_loopback(text):
            return state.gethostbyaddr(ip_address)
        if text == LOCALHOST:
            result = state.gethostbyaddr(ip_address)
            if result[2] and all(_numeric_loopback(a) for a in result[2]):
                return result
        _refuse()

    def getnameinfo(sockaddr, flags, /):
        host = _text(sockaddr[0]) if isinstance(sockaddr, tuple) and sockaddr else None
        if host is None or not _numeric_loopback(host):
            _refuse()
        return state.getnameinfo(sockaddr, flags)

    async def proactor_sock_connect(self, sock, address):
        if not address_allowed(state, sock.family, address):
            _refuse()
        return await state.proactor_sock_connect(self, sock, address)

    async def proactor_sock_sendto(self, sock, data, address):
        if not address_allowed(state, sock.family, address):
            _refuse()
        return await state.proactor_sock_sendto(self, sock, data, address)

    return {"connect": connect, "connect_ex": connect_ex, "sendto": sendto, "sendmsg": sendmsg,
            "getaddrinfo": getaddrinfo, "gethostbyname": gethostbyname,
            "gethostbyname_ex": gethostbyname_ex, "gethostbyaddr": gethostbyaddr,
            "getnameinfo": getnameinfo, "proactor_sock_connect": proactor_sock_connect,
            "proactor_sock_sendto": proactor_sock_sendto}


def install():
    """Install the guard once per process; returns its state."""
    state = installed()
    if state is not None:
        return state
    present = [(key, owner, name) for key, owner, name in TARGETS if hasattr(owner, name)]
    state = SimpleNamespace(error=SocketBlockedError,
                            **{key: getattr(owner, name) for key, owner, name in present})
    functions = wrappers(state)
    for key, owner, name in present:
        function = functions[key]
        function.__wrapped__ = getattr(state, key)
        setattr(function, MARKER, state)
        setattr(owner, name, function)
    return state


STATE = install()
# Discovery imports this module as support.offline_network and dotted runs as
# tests.support.offline_network; both copies raise and catch one class.
SocketBlockedError = STATE.error
