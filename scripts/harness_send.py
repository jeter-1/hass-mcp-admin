"""One reviewed check for every CI-harness send (HAMCP-363).

Every script under ``scripts/`` that talks to a disposable Home Assistant
Core, an ``ha-mcp`` peer, an Engineering add-on or a fixture server passes the
exact URL it is about to send to through ``harness_url`` first. The harness
path-safety scanner (tests/test_hamcp200_path_safety.py) refuses a send whose
destination did not come from it. ``harness_url`` returns its input unchanged,
or raises ``HarnessRouteError`` before anything is sent.

Origin: ``http://`` or ``ws://`` to ``127.0.0.1`` or ``[::1]``, with or without
a port, or a lane origin registered in this process. ``localhost`` is refused:
it is a name the system resolves, not an address. Lane origins are the named
allowlist for the internal-bridge harnesses: only the A2 hand-over and the
dashboard lane register one, each the private Docker address it verified
itself, and nothing in the environment can add one. A ``ws://`` URL may use a
registered ``http://`` lane origin's address and port.

Route: the path after the origin must fullmatch one of ``ROUTES``, one closed
set for every origin. No query, fragment, user info, percent-escape,
backslash, whitespace or dot segment.

Refusals carry one fixed code and never the URL, so a refusal cannot echo a
token, a path secret or an address. Stdlib only, in conservative syntax: the
exact-image lanes run some of these scripts inside the Engineering image from a
read-only ``/lane/scripts`` mount. Import it by its top-level name
(``harness_send``), never as ``scripts.harness_send``: registered lane origins
live in this module, and a second copy would hold a second set.
"""

from __future__ import annotations

import ipaddress
import re

URL_REFUSED = "harness_url_refused"
ORIGIN_REFUSED = "harness_origin_refused"
ROUTE_REFUSED = "harness_route_refused"
LANE_ORIGIN_REFUSED = "lane_origin_refused"

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "[::1]"})
LANE_PORTS = frozenset({8086, 8123})
# The RFC 1918 private ranges: a Docker bridge address is in one of these.
LANE_NETWORKS = tuple(
    ipaddress.IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
# Printable ASCII only, without the characters that start a query, a fragment,
# user info or an escape, or that some clients read as a path separator.
_TEXT = re.compile(r"[!-~]+")
_FORBIDDEN = frozenset("?#%@\\")
_URL = re.compile(r"(http|ws)://(127\.0\.0\.1|\[::1\]|[0-9]{1,3}(?:\.[0-9]{1,3}){3})"
                  r"(?::([1-9][0-9]{0,4}))?(/[!-~]*)")
_LANE_ORIGIN = re.compile(r"http://([0-9]{1,3}(?:\.[0-9]{1,3}){3}):([1-9][0-9]{0,4})")

ROUTES = tuple(re.compile(pattern) for pattern in (
    # The disposable Core each lane starts. The Core and its paths are synthetic.
    r"/api/websocket",
    r"/api/onboarding",
    r"/api/onboarding/(?:users|core_config|integration|analytics)",
    r"/auth/token",
    r"/auth/login_flow",
    r"/auth/login_flow/[0-9A-Za-z]{1,64}",
    r"/api/config",
    r"/api/template",
    r"/api/states",
    r"/api/states/(?:fan\.hamcp_contract_fan|light\.hamcp_contract_light|switch\.hamcp_contract_switch"
    r"|input_(?:boolean|number|select)\.hamcp_seq17)",
    r"/api/states/(?:input_select|binary_sensor|script|scene)\.seq15_[0-9a-z_]{1,64}",
    r"/api/config/config_entries/(?:flow|entry)(?:/[0-9A-Za-z]{1,64})?",
    r"/api/config/config_entries/options/flow(?:/[0-9a-f]{32})?",
    r"/api/config/script/config/seq15_[0-9a-f]{8}",
    r"/api/config/scene/config/(?:seq15_[0-9a-f]{8}|[0-9a-f]{32})",
    r"/api/config/automation/config/hamcp371_automation",
    r"/api/config/script/config/hamcp371_script",
    r"/api/config/scene/config/hamcp371_scene",
    r"/api/services/(?:todo/add_item|automation/trigger|input_boolean/toggle)",
    r"/api/services/(?:light|switch|fan|input_boolean)/turn_off",
    r"/api/services/input_number/set_value",
    r"/api/services/input_select/select_option",
    r"/api/services/(?:automation|script)/reload",
    r"/api/events/dev14_real_contract_trigger",
    # ha-mcp peers, the Engineering add-on and the lanes' fixture servers.
    r"/",
    r"/healthz",
    r"/ready",
    r"/__fixture__/stats",
    r"/mcp",
    r"/beta23-real-ha-mcp",
    r"/disposable-dashboard-mcp",
    r"/synthetic-upstream-secret/mcp/catalog/full",
    r"/[0-9A-Za-z_-]{1,128}/mcp",
    r"/plans/[0-9A-Za-z_-]{1,160}",
))

_lane_origins = set()


class HarnessRouteError(ValueError):
    """A harness send named no harness route or origin; nothing was sent."""


def _refused(code):
    return HarnessRouteError(code)


def _port(text):
    return None if text is None else int(text)


def _canonical_ipv4(text):
    try:
        address = ipaddress.IPv4Address(text)
    except ValueError:
        return None
    return address if str(address) == text else None


def _route_allowed(path):
    if any(segment in (".", "..") for segment in path.split("/")):
        return False
    return any(route.fullmatch(path) for route in ROUTES)


def harness_url(url, path=""):
    """Return ``url + path`` unchanged once its origin and route are allowed.

    Raises ``HarnessRouteError`` with a fixed code otherwise; nothing is sent.
    """

    text = url + path if type(url) is str and type(path) is str else None
    if text is None or not _TEXT.fullmatch(text) or _FORBIDDEN.intersection(text):
        raise _refused(URL_REFUSED)
    match = _URL.fullmatch(text)
    if match is None:
        raise _refused(URL_REFUSED)
    _scheme, host, port, route = match.groups()
    port = _port(port)
    if port is not None and port > 65535:
        raise _refused(ORIGIN_REFUSED)
    if host not in LOOPBACK_HOSTS:
        if port is None or "http://%s:%d" % (host, port) not in _lane_origins:
            raise _refused(ORIGIN_REFUSED)
    if not _route_allowed(route):
        raise _refused(ROUTE_REFUSED)
    return text


def register_lane_origin(url):
    """Allow sends to one internal-bridge origin a lane verified; returns ``url``.

    Only ``http://<IPv4 in 10/8, 172.16/12 or 192.168/16>:<8123 or 8086>``,
    the address the caller read from the Docker network and container it
    created and checked. Loopback needs no registration and is refused here,
    as are broadcast, documentation, reserved and other non-RFC 1918 ranges.
    Registering again is a no-op. Raises ``HarnessRouteError`` otherwise.
    """

    match = _LANE_ORIGIN.fullmatch(url) if type(url) is str else None
    address = _canonical_ipv4(match.group(1)) if match is not None else None
    allowed = (
        address is not None
        and int(match.group(2)) in LANE_PORTS
        and any(address in network for network in LANE_NETWORKS)
    )
    if not allowed:
        raise _refused(LANE_ORIGIN_REFUSED)
    _lane_origins.add(url)
    return url
