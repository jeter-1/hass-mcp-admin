"""Offline tests of the one CI-harness send check (HAMCP-363).

``scripts/harness_send.py`` is pure: no socket is opened and nothing is sent.
Every value here is synthetic.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import harness_send  # noqa: E402
from harness_send import (  # noqa: E402
    LANE_ORIGIN_REFUSED,
    ORIGIN_REFUSED,
    ROUTE_REFUSED,
    URL_REFUSED,
    HarnessRouteError,
    harness_url,
    register_lane_origin,
)

SYNTHETIC_TOKEN = "synthetic-harness-token-not-a-secret"
LANE = "http://172.18.0.2:8123"

# One path for every ROUTES entry, in order, so a route cannot go untested.
ROUTE_SAMPLES = (
    "/api/websocket",
    "/api/onboarding",
    "/api/onboarding/users",
    "/auth/token",
    "/auth/login_flow",
    "/auth/login_flow/0123abcd",
    "/api/config",
    "/api/template",
    "/api/states",
    "/api/states/fan.hamcp_contract_fan",
    "/api/states/script.seq15_owned_script",
    "/api/config/config_entries/entry/01J0SYNTHETIC",
    "/api/config/config_entries/options/flow/" + "a" * 32,
    "/api/config/script/config/seq15_0123abcd",
    "/api/config/scene/config/" + "f" * 32,
    "/api/config/automation/config/hamcp371_automation",
    "/api/config/script/config/hamcp371_script",
    "/api/config/scene/config/hamcp371_scene",
    "/api/services/todo/add_item",
    "/api/services/fan/turn_off",
    "/api/services/input_number/set_value",
    "/api/services/input_select/select_option",
    "/api/services/script/reload",
    "/api/events/dev14_real_contract_trigger",
    "/",
    "/healthz",
    "/ready",
    "/__fixture__/stats",
    "/mcp",
    "/beta23-real-ha-mcp",
    "/disposable-dashboard-mcp",
    "/synthetic-upstream-secret/mcp/catalog/full",
    "/synthetic-upstream-secret/mcp",
    "/plans/plan_0123-abcd",
)


class HarnessSendTestCase(unittest.TestCase):
    def setUp(self):
        saved = set(harness_send._lane_origins)

        def restore():
            harness_send._lane_origins.clear()
            harness_send._lane_origins.update(saved)

        self.addCleanup(restore)
        harness_send._lane_origins.clear()

    def assertRefused(self, code, *args, function=harness_url):
        with self.assertRaises(HarnessRouteError) as caught:
            function(*args)
        self.assertEqual(caught.exception.args, (code,))
        return caught.exception


class AllowedTests(HarnessSendTestCase):
    def test_every_route_is_sampled_once_and_matches_only_its_own_pattern(self):
        self.assertEqual(len(ROUTE_SAMPLES), len(harness_send.ROUTES))
        for pattern, path in zip(harness_send.ROUTES, ROUTE_SAMPLES):
            with self.subTest(path=path):
                matching = [route for route in harness_send.ROUTES if route.fullmatch(path)]
                self.assertEqual(matching, [pattern])

    def test_loopback_origins_return_the_url_unchanged(self):
        for origin in ("http://127.0.0.1", "http://127.0.0.1:8123", "http://[::1]:18086",
                       "ws://127.0.0.1:8123", "ws://[::1]"):
            for path in ROUTE_SAMPLES:
                with self.subTest(origin=origin, path=path):
                    self.assertEqual(harness_url(origin + path), origin + path)
                    self.assertEqual(harness_url(origin, path), origin + path)

    def test_a_checked_url_checks_again_to_itself(self):
        url = harness_url("http://127.0.0.1:8123/api", "/config")
        self.assertEqual(harness_url(url), url)


class OriginTests(HarnessSendTestCase):
    def test_only_loopback_addresses_are_origins_without_registration(self):
        for url in ("http://localhost:8123/api/config", "http://127.0.0.2:8123/api/config",
                    "http://10.0.0.5:8123/api/config", "http://172.18.0.2:8123/api/config",
                    "http://0.0.0.0:8123/api/config", "http://192.0.2.1/mcp"):
            with self.subTest(url=url):
                code = URL_REFUSED if "localhost" in url else ORIGIN_REFUSED
                self.assertRefused(code, url)

    def test_other_schemes_names_and_address_forms_are_refused(self):
        for url in ("https://127.0.0.1:8123/api/config", "wss://127.0.0.1:8123/api/websocket",
                    "HTTP://127.0.0.1/mcp", "http://example.invalid/mcp", "http://127.1/mcp",
                    "http://::1/mcp", "http://[::ffff:127.0.0.1]/mcp", "http://127.0.0.1:0/mcp",
                    "http://127.0.0.1:/mcp", "http://127.0.0.1:08123/mcp", "//127.0.0.1/mcp",
                    "/api/config", "127.0.0.1:8123/api/config", "http://127.0.0.1"):
            with self.subTest(url=url):
                self.assertRefused(URL_REFUSED, url)

    def test_a_port_past_the_range_is_refused(self):
        self.assertRefused(ORIGIN_REFUSED, "http://127.0.0.1:65536/mcp")
        self.assertEqual(harness_url("http://127.0.0.1:65535/mcp"), "http://127.0.0.1:65535/mcp")


class RouteTests(HarnessSendTestCase):
    def test_paths_off_the_closed_set_are_refused(self):
        for path in ("/api/hassio/addons", "/api/services/homeassistant/restart",
                     "/api/config/core/check_config", "/api/states/light.kitchen",
                     "/api/services/light/turn_on", "/api/webhook/synthetic", "/mcp/",
                     "/mcp/extra", "//mcp", "/api/config/", "/API/config",
                     "/api/config/config_entries/entry/../../hassio",
                     "/plans/", "/" + "a" * 129 + "/mcp"):
            with self.subTest(path=path):
                self.assertRefused(ROUTE_REFUSED, "http://127.0.0.1:8123" + path)

    def test_dot_segments_are_refused_even_where_a_pattern_would_match(self):
        for path in ("/./mcp", "/../mcp", "/api/./config", "/api/../api/config"):
            with self.subTest(path=path):
                self.assertRefused(ROUTE_REFUSED, "http://127.0.0.1:8123" + path)
        self.assertFalse(harness_send._route_allowed("/api/../api/config"))

    def test_query_fragment_userinfo_escapes_and_separators_are_refused(self):
        for url in ("http://127.0.0.1:8123/api/config?x=1", "http://127.0.0.1:8123/api/config#x",
                    "http://u@127.0.0.1:8123/api/config", "http://127.0.0.1:8123/api%2Fconfig",
                    "http://127.0.0.1:8123/api\\config", "http://127.0.0.1:8123/api/config ",
                    "http://127.0.0.1:8123/api/\tconfig", "http://127.0.0.1:8123/api/config\n",
                    "http://127.0.0.1:8123/api/cönfig", "http://127.0.0.1:8123/api/config\x00"):
            with self.subTest(url=url):
                self.assertRefused(URL_REFUSED, url)

    def test_only_text_url_and_path_are_accepted(self):
        for args in ((b"http://127.0.0.1/mcp",), (None,), ("http://127.0.0.1", b"/mcp"),
                     ("http://127.0.0.1", None), (StrLike("http://127.0.0.1/mcp"),)):
            with self.subTest(args=args):
                self.assertRefused(URL_REFUSED, *args)


class StrLike(str):
    """A str subclass; the check accepts exact str only."""


class LaneOriginTests(HarnessSendTestCase):
    def test_a_registered_private_lane_origin_allows_http_and_ws_on_its_routes(self):
        self.assertEqual(register_lane_origin(LANE), LANE)
        self.assertEqual(harness_url(LANE, "/api/config"), LANE + "/api/config")
        self.assertEqual(harness_url("ws://172.18.0.2:8123/api/websocket"),
                         "ws://172.18.0.2:8123/api/websocket")
        self.assertRefused(ROUTE_REFUSED, LANE + "/api/hassio/addons")

    def test_registration_names_one_address_and_port_only(self):
        register_lane_origin(LANE)
        for url in ("http://172.18.0.2:8086/mcp", "http://172.18.0.3:8123/api/config",
                    "http://172.18.0.2/api/config"):
            with self.subTest(url=url):
                self.assertRefused(ORIGIN_REFUSED, url)

    def test_registering_again_is_a_no_op(self):
        register_lane_origin(LANE)
        register_lane_origin(LANE)
        self.assertEqual(harness_send._lane_origins, {LANE})

    def test_each_rfc1918_range_registers_on_both_lane_ports(self):
        for url in ("http://10.0.0.5:8123", "http://10.255.255.254:8086",
                    "http://172.16.0.2:8123", "http://172.31.255.254:8086",
                    "http://192.168.0.2:8123", "http://192.168.255.254:8086"):
            with self.subTest(url=url):
                self.assertEqual(register_lane_origin(url), url)
        self.assertEqual(len(harness_send._lane_origins), 6)

    def test_only_private_unicast_addresses_on_lane_ports_register(self):
        for url in ("http://127.0.0.1:8123", "http://8.8.8.8:8123", "http://169.254.1.1:8123",
                    "http://0.0.0.0:8123", "http://224.0.0.1:8123", "http://100.64.0.1:8123",
                    "http://172.18.0.2:8124", "http://172.18.0.2", "http://172.018.0.2:8123",
                    "http://172.18.0.2:8123/", "https://172.18.0.2:8123", "ws://172.18.0.2:8123",
                    "http://[fd00::2]:8123", "http://lane.invalid:8123", "http://172.18.0.256:8123",
                    # Cursor r1 R1-2: broadcast, documentation, reserved, and just
                    # outside 172.16/12.
                    "http://255.255.255.255:8123", "http://192.0.2.1:8123",
                    "http://198.51.100.1:8086", "http://240.0.0.1:8123", "http://192.0.0.8:8123",
                    "http://172.32.0.1:8123", "http://172.15.255.254:8123",
                    b"http://172.18.0.2:8123", None):
            with self.subTest(url=url):
                self.assertRefused(LANE_ORIGIN_REFUSED, url, function=register_lane_origin)
        self.assertEqual(harness_send._lane_origins, set())


class RefusalTests(HarnessSendTestCase):
    def test_a_refusal_carries_its_code_and_never_the_url(self):
        address = "http://10.11.12.13:8123"
        cases = (
            (URL_REFUSED, "http://127.0.0.1:8123/api/config?token=" + SYNTHETIC_TOKEN),
            (ORIGIN_REFUSED, address + "/" + SYNTHETIC_TOKEN + "/mcp"),
            (ROUTE_REFUSED, "http://127.0.0.1:8123/" + SYNTHETIC_TOKEN),
        )
        for code, url in cases:
            with self.subTest(code=code):
                error = self.assertRefused(code, url)
                text = str(error) + repr(error)
                self.assertNotIn(SYNTHETIC_TOKEN, text)
                self.assertNotIn("10.11.12.13", text)
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)

    def test_a_refusal_is_a_value_error_and_never_a_transport_error(self):
        # Transport handlers catch OSError as "unavailable"; a refusal must not
        # be mistaken for an unreachable target and retried.
        self.assertTrue(issubclass(HarnessRouteError, ValueError))
        self.assertFalse(issubclass(HarnessRouteError, OSError))


class ModuleTests(unittest.TestCase):
    def test_the_module_imports_only_the_standard_library_it_names(self):
        tree = ast.parse((SCRIPTS / "harness_send.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertEqual(imported, {"__future__", "ipaddress", "re"})

    def test_harness_routes_shares_the_one_module_copy(self):
        sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
        self.addCleanup(sys.path.remove, str(ROOT / "hass_mcp_engineering_beta"))
        import harness_routes

        self.assertIs(sys.modules["harness_send"], harness_send)
        self.assertIs(harness_routes.harness_send_url, harness_send.harness_url)
        self.assertIs(harness_routes.HarnessRouteError, HarnessRouteError)


if __name__ == "__main__":
    unittest.main()
