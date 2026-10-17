"""HAMCP-200 path-segment safety for every Home Assistant REST request.

Every REST path is a fixed route template plus values that pass through one
segment guard. Traversal-shaped identifiers are refused as truthful
``invalid_request`` failures before any session or credential exists, the
transport re-verifies the normalized route before attaching the token and
never follows redirects, and a source scan fails on any raw interpolated
Home Assistant path. Offline and deterministic: the aiohttp session is a
counting fake and no Home Assistant is contacted.
"""

from __future__ import annotations

import ast
import asyncio
from collections import Counter
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
PACKAGE_DIR = BETA_DIR / "ha_mcp_engineering"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA_DIR))

from tests import test_beta_v2 as beta  # noqa: E402
from tests.same_thread_asgi_client import SameThreadAsgiTestClient  # noqa: E402

from ha_mcp_engineering.application import create_application  # noqa: E402
from ha_mcp_engineering.clients.paths import (  # noqa: E402
    INVALID_IDENTIFIER_MESSAGE,
    RestPath,
    UnsafeRequestPathError,
    path_segment,
    request_url,
    require_route,
    rest_path,
)
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient  # noqa: E402
from ha_mcp_engineering.errors import HomeAssistantApiError  # noqa: E402
from ha_mcp_engineering.governance import GOVERNANCE  # noqa: E402
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationMutationNotDispatchedError,
    ConfigurationResourceGateway,
)
from ha_mcp_engineering.governance.service import AutomationGateway  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.runtime import (  # noqa: E402
    CORE_READMISSION,
)
from ha_mcp_engineering.handoff.provider import (  # noqa: E402
    EngineeringHandoffProvider,
)
from ha_mcp_engineering.identifiers import (  # noqa: E402
    require_automation_config_id,
    require_domain,
    require_entity_id,
    require_script_id,
    require_service,
)
from ha_mcp_engineering.impact.provider import DirectHaImpactProvider  # noqa: E402
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402


API_URL = "http://supervisor/core/api"

# Literal, percent-encoded, double-encoded, backslash and mixed traversal.
TRAVERSAL_IDS = (
    "..",
    ".",
    "../../hassio/addons",
    "../../../supervisor/info",
    "1/../../../hassio/addons",
    "..%2f..%2fhassio%2faddons",
    "%2e%2e%2f%2e%2e%2fhassio",
    "%2E%2E%2Fhassio",
    "%252e%252e%252fhassio",
    "%25252e%25252e%25252fhassio",
    "..\\..\\hassio\\addons",
    "..%5c..%5chassio",
    "%2e%2e%5c%2e%2e%5chassio",
    ".%2e/hassio",
    "%2e./hassio",
    "1%2f..%2f..%2fhassio",
    "1%00",
    "1\x00",
    "1\n",
    "1?x=1",
    "1#fragment",
    "a.b",
)
# "a.b" is a valid entity ID shape; every other variant is invalid for all.
ENTITY_TRAVERSAL_IDS = tuple(value for value in TRAVERSAL_IDS if value != "a.b")
VALID_AUTOMATION_IDS = (
    "1700000000000",
    "3f2a9c4e-8d1b-4c6f-9a2e-5b7d0c1e4f6a",
    "morning_lights",
    "Kitchen_Motion_2",
    "legacy:import-01",
)


class CountingResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self.body


class CountingSessionFactory:
    """Stands in for ``aiohttp.ClientSession`` and records every request."""

    def __init__(self, status: int = 200, body: str = '{"id": "fixture"}'):
        self.status = status
        self.body = body
        self.sessions = 0
        self.requests: list[dict] = []

    def __call__(self, *args, **kwargs):
        self.sessions += 1
        factory = self

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def request(self, method, url, **request_kwargs):
                factory.requests.append(
                    {"method": method, "url": url, **request_kwargs}
                )
                return CountingResponse(factory.status, factory.body)

        return Session()


def rest_client() -> HomeAssistantRestClient:
    return HomeAssistantRestClient(
        beta.beta_settings(str(Path(tempfile.gettempdir()) / "hamcp200.jsonl"))
    )


def patched_session(factory: CountingSessionFactory):
    return patch(
        "ha_mcp_engineering.clients.rest.aiohttp.ClientSession", new=factory
    )


class SegmentGuardTests(unittest.TestCase):
    def test_traversal_variants_are_refused_by_the_segment_guard(self):
        for value in TRAVERSAL_IDS:
            with self.subTest(value=value):
                with self.assertRaises(UnsafeRequestPathError) as caught:
                    path_segment(value)
                self.assertEqual(caught.exception.safe_message, INVALID_IDENTIFIER_MESSAGE)
                with self.assertRaises(UnsafeRequestPathError):
                    rest_path("/config/automation/config/{}", value)

    def test_non_string_empty_oversized_and_undecodable_values_are_refused(self):
        for value in (None, 7, b"1", "", "a" * 513, "%ff%fe", ["1"]):
            with self.subTest(value=value):
                with self.assertRaises(UnsafeRequestPathError):
                    path_segment(value)

    def test_valid_identifiers_encode_as_exactly_one_segment(self):
        for value in VALID_AUTOMATION_IDS:
            with self.subTest(value=value):
                path = rest_path("/config/automation/config/{}", value)
                url = request_url(API_URL, path)
                self.assertEqual(url.parts[-1], value)
                self.assertEqual(
                    url.path, "/core/api/config/automation/config/" + value
                )

    def test_typed_entity_and_timestamp_segments_allow_only_their_grammar(self):
        url = request_url(
            API_URL, rest_path("/states/{}", "light.kitchen", kinds=("entity_id",))
        )
        self.assertEqual(url.path, "/core/api/states/light.kitchen")
        url = request_url(
            API_URL,
            rest_path(
                "/history/period/{}",
                "2026-10-17T08:00:00.123456+00:00",
                kinds=("timestamp",),
            ),
        )
        self.assertEqual(url.parts[-1], "2026-10-17T08:00:00.123456+00:00")
        for value, kind in (
            ("light.kitchen", "identifier"),
            ("../states", "entity_id"),
            ("light.kitchen/..", "entity_id"),
            ("light..kitchen", "entity_id"),
            ("2026-10-17T08:00:00+00:00/../x", "timestamp"),
            ("..", "timestamp"),
        ):
            with self.subTest(value=value, kind=kind):
                with self.assertRaises(UnsafeRequestPathError):
                    path_segment(value, kind=kind)

    def test_query_values_are_encoded_and_cannot_add_parameters(self):
        path = rest_path(
            "/history/period/{}",
            "2026-10-17T08:00:00+00:00",
            kinds=("timestamp",),
            query=[("filter_entity_id", "sensor.a&end_time=x#y"), ("minimal_response", None)],
        )
        url = request_url(API_URL, path)
        self.assertEqual(
            list(url.query.items()),
            [("filter_entity_id", "sensor.a&end_time=x#y"), ("minimal_response", "")],
        )
        self.assertFalse(url.raw_fragment)
        with self.assertRaises(UnsafeRequestPathError):
            rest_path("/logbook/{}", "2026-10-17T08:00:00+00:00",
                      kinds=("timestamp",), query={"entity": "a\nb"})

    def test_templates_must_be_fixed_source_routes(self):
        for template in ("states/{}", "/states/{}?x", "/../{}", "/States/{}", "/a.b/{}"):
            with self.subTest(template=template):
                with self.assertRaises(ValueError):
                    rest_path(template, "1")
        with self.assertRaises(ValueError):
            rest_path("/states/{}")
        with self.assertRaises(ValueError):
            rest_path("/states/{}", "1", query={"Bad-Key": "1"})

    def test_raw_strings_are_accepted_only_as_fixed_literal_routes(self):
        self.assertEqual(require_route("/states").template, "/states")
        self.assertEqual(
            require_route("/config/core/check_config").template,
            "/config/core/check_config",
        )
        for raw in (
            "/states/light.kitchen",
            "/config/automation/config/../../hassio",
            "/config/automation/config/1%2f..",
            "states",
            "/states?x=1",
            None,
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(UnsafeRequestPathError) as caught:
                    require_route(raw)
                self.assertEqual(caught.exception.details["reason"], "unbound_request_path")


class IdentifierGrammarTests(unittest.TestCase):
    def test_tool_boundary_grammars_accept_valid_and_refuse_traversal(self):
        for value in VALID_AUTOMATION_IDS:
            self.assertEqual(require_automation_config_id(value), value)
        self.assertEqual(require_script_id("morning_routine"), "morning_routine")
        self.assertEqual(require_domain("light"), "light")
        self.assertEqual(require_service("turn_on"), "turn_on")
        self.assertEqual(require_entity_id("sensor.kitchen_2"), "sensor.kitchen_2")
        checks = (
            require_automation_config_id,
            require_script_id,
            require_domain,
            require_service,
            require_entity_id,
        )
        for check in checks:
            values = ENTITY_TRAVERSAL_IDS if check is require_entity_id else TRAVERSAL_IDS
            for value in values + ("sensor.x\n", "sensor.x/y", None, ""):
                with self.subTest(check=check.__name__, value=value):
                    with self.assertRaises(UnsafeRequestPathError) as caught:
                        check(value)
                    self.assertEqual(
                        caught.exception.details["reason"], "invalid_identifier"
                    )


class NormalizedRouteTests(unittest.TestCase):
    """The transport re-verifies the route before the token is attached."""

    def forged(self, encoded, values=("1",), kinds=("identifier",), query=()):
        return RestPath(
            encoded, "/config/automation/config/{}", values, kinds, query
        )

    def test_crafted_route_that_normalizes_elsewhere_is_refused(self):
        # The bound value is valid, so only the normalized-route comparison
        # can catch a path string that dot-normalizes into Supervisor routes.
        for encoded in (
            "/config/automation/config/../../../hassio/addons",
            "/config/automation/config/%2e%2e/%2e%2e/%2e%2e/hassio",
            "/config/automation/config/1/extra",
            "/config/automation/1",
            "/config/automation/config/2",
            "/config/automation/config/1#x",
            "/config/automation/config/1?x=1",
            "//evil.example/config/automation/config/1",
        ):
            with self.subTest(encoded=encoded):
                with self.assertRaises(UnsafeRequestPathError) as caught:
                    request_url(API_URL, self.forged(encoded))
                self.assertEqual(caught.exception.details["reason"], "route_mismatch")

    def test_central_guard_rechecks_values_however_the_path_was_built(self):
        # The encoded path decodes to the expected parts, so only the central
        # per-value re-check refuses these separator-bearing identifiers.
        for value, encoded in (
            ("a/b", "/config/automation/config/a%2Fb"),
            ("..\\..\\hassio", "/config/automation/config/..%5C..%5Chassio"),
            ("1\x00", "/config/automation/config/1%00"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(UnsafeRequestPathError) as caught:
                    request_url(API_URL, self.forged(encoded, values=(value,)))
                self.assertEqual(
                    caught.exception.details["reason"], "invalid_identifier"
                )

    def test_forged_route_never_opens_a_session_or_attaches_the_token(self):
        factory = CountingSessionFactory()
        client = rest_client()
        forged = (
            self.forged("/config/automation/config/../../../hassio/addons"),
            self.forged("/config/automation/config/a%2Fb", values=("a/b",)),
            "/config/automation/config/../../hassio",
            "/states/" + "light.kitchen",
        )
        with patched_session(factory):
            for path in forged:
                with self.subTest(path=str(path)):
                    with self.assertRaises(UnsafeRequestPathError):
                        asyncio.run(client.request("GET", path))
        self.assertEqual(factory.sessions, 0)
        self.assertEqual(factory.requests, [])

    def test_redirects_are_disabled_and_a_3xx_is_a_failure(self):
        for status in (301, 302, 307, 308):
            factory = CountingSessionFactory(status=status, body="")
            with self.subTest(status=status), patched_session(factory):
                with self.assertRaises(HomeAssistantApiError) as caught:
                    asyncio.run(
                        rest_client().request(
                            "GET", rest_path("/config/automation/config/{}", "1")
                        )
                    )
                self.assertEqual(caught.exception.details["status"], status)
                self.assertEqual(len(factory.requests), 1)
                self.assertIs(factory.requests[0]["allow_redirects"], False)

    def test_valid_request_sends_the_exact_template_url(self):
        factory = CountingSessionFactory()
        with patched_session(factory):
            value = asyncio.run(
                rest_client().request(
                    "GET", rest_path("/config/automation/config/{}", "1700000000000")
                )
            )
        self.assertEqual(value, {"id": "fixture"})
        request = factory.requests[0]
        self.assertEqual(
            str(request["url"]),
            "http://supervisor/core/api/config/automation/config/1700000000000",
        )
        self.assertIs(request["allow_redirects"], False)


class ProviderBoundaryTests(unittest.TestCase):
    """Home Assistant-derived and governed identifiers hit the same guard."""

    def test_governed_automation_write_is_not_dispatched_for_unsafe_ids(self):
        factory = CountingSessionFactory()
        gateway = AutomationGateway(rest_client())
        with patched_session(factory):
            for value in TRAVERSAL_IDS:
                with self.subTest(value=value):
                    with self.assertRaises(ConfigurationMutationNotDispatchedError) as caught:
                        asyncio.run(gateway.write(value, {"alias": "x"}))
                    self.assertEqual(caught.exception.details["reason"], "invalid_identifier")
                    self.assertFalse(caught.exception.details["provider_response_received"])
                    with self.assertRaises(UnsafeRequestPathError):
                        asyncio.run(gateway.get(value))
        self.assertEqual(factory.sessions, 0)

    def test_configuration_resource_write_backstop_refuses_unsafe_ids(self):
        factory = CountingSessionFactory()
        gateway = ConfigurationResourceGateway(rest_client(), websocket_client=None)
        with patched_session(factory):
            for resource_type in ("automation", "script"):
                for value in ("../../hassio/addons", "%2e%2e%2fhassio", "a\\b"):
                    with self.subTest(resource_type=resource_type, value=value):
                        with self.assertRaises(ConfigurationMutationNotDispatchedError) as caught:
                            asyncio.run(
                                gateway._write_rest_configuration(resource_type, value, {})
                            )
                        self.assertEqual(
                            caught.exception.details["reason"], "invalid_identifier"
                        )
                        self.assertNotIn("resource_id", caught.exception.details)
        self.assertEqual(factory.sessions, 0)

    def test_handoff_skips_unsafe_home_assistant_derived_ids_without_a_request(self):
        factory = CountingSessionFactory()
        provider = EngineeringHandoffProvider(
            governance=None,
            incident=None,
            dependency_index=None,
            rest_client=rest_client(),
            health=None,
        )
        with patched_session(factory):
            configs = asyncio.run(
                provider._automation_configs(
                    ["1700000000000", "../../hassio/addons", "%2e%2e%2fsupervisor"]
                )
            )
            states, coverage = asyncio.run(
                provider._states(["light.kitchen", "light.kitchen/../../config"])
            )
        self.assertEqual(
            [str(request["url"]) for request in factory.requests],
            [
                "http://supervisor/core/api/config/automation/config/1700000000000",
                "http://supervisor/core/api/states",
                "http://supervisor/core/api/states/light.kitchen",
            ],
        )
        self.assertEqual(set(states), {"light.kitchen"})
        self.assertEqual((coverage.completeness, coverage.failed_items), ("partial", 1))
        self.assertIsNotNone(configs)

    def test_impact_exact_state_refuses_unsafe_entity_without_a_request(self):
        factory = CountingSessionFactory()
        provider = DirectHaImpactProvider(None, rest_client(), None)
        with patched_session(factory):
            for value in ("../config", "light.kitchen/..", "light.%2e%2e"):
                with self.subTest(value=value):
                    with self.assertRaises(UnsafeRequestPathError):
                        asyncio.run(provider._exact_state(value))
        self.assertEqual(factory.sessions, 0)


class ToolRefusalTests(unittest.TestCase):
    """MCP tool calls refuse traversal truthfully, audited, with no request."""

    @classmethod
    def setUpClass(cls):
        class SyntheticCoreSource:
            async def capture_core_snapshot(self):
                return beta.core_snapshot(
                    "2026.8.1", evidence=beta.core_capability_evidence()
                )

        cls.tempdir = tempfile.TemporaryDirectory()
        settings = beta.beta_settings(str(Path(cls.tempdir.name) / "audit.jsonl"))
        gateway = create_application(settings, core_snapshot_source=SyntheticCoreSource())
        asyncio.run(CORE_READMISSION.reconcile_once("synthetic-application-startup"))
        asyncio.run(
            GOVERNANCE.require().f3_runtime.recover_once("synthetic-application-startup")
        )
        cls.client_context = SameThreadAsgiTestClient(
            gateway, lifespan_app=gateway.app, follow_redirects=False
        )
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.tempdir.cleanup()

    def rpc(self, name, arguments, request_id):
        response = self.client.post(
            f"/{beta.SECRET}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
                "x-request-id": request_id,
            },
        )
        line = next(
            item
            for item in response.text.replace("\r", "").splitlines()
            if item.startswith("data: ")
        )
        call = json.loads(line.removeprefix("data: "))
        return json.loads(call["result"]["content"][0]["text"])

    def audit_record(self, request_id):
        path = Path(self.tempdir.name) / "audit.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()]
        return next(r for r in reversed(records) if r.get("request_id") == request_id)

    def assert_refused(self, name, arguments, request_id):
        factory = CountingSessionFactory()
        before = Counter(METRICS.snapshot()["recent_error_counts"])
        with patched_session(factory):
            payload = self.rpc(name, arguments, request_id)
        audit = self.audit_record(request_id)
        after = Counter(METRICS.snapshot()["recent_error_counts"])
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "invalid_request")
        self.assertIn(INVALID_IDENTIFIER_MESSAGE, json.dumps(payload))
        self.assertEqual(audit["error_code"], "invalid_request")
        self.assertEqual(audit["result_status"], "failure")
        self.assertEqual(audit["ha_endpoint_categories"], [])
        self.assertEqual(after["invalid_request"] - before["invalid_request"], 1)
        self.assertEqual(factory.sessions, 0)
        self.assertEqual(factory.requests, [])
        return payload, audit

    def test_get_automation_config_refuses_traversal_without_a_request(self):
        for index, value in enumerate(TRAVERSAL_IDS):
            with self.subTest(value=value):
                payload, _ = self.assert_refused(
                    "get_automation_config",
                    {"automation_id": value},
                    f"hamcp200-automation-{index}",
                )
                self.assertIn("invalid_identifier", json.dumps(payload))

    def test_get_automation_config_reads_valid_ids(self):
        for index, value in enumerate(VALID_AUTOMATION_IDS):
            factory = CountingSessionFactory(body=json.dumps({"id": value}))
            with self.subTest(value=value), patched_session(factory):
                payload = self.rpc(
                    "get_automation_config",
                    {"automation_id": value},
                    f"hamcp200-automation-valid-{index}",
                )
                self.assertTrue(payload["success"], payload)
                self.assertEqual(
                    [str(request["url"]) for request in factory.requests],
                    ["http://supervisor/core/api/config/automation/config/" + value],
                )
                self.assertEqual(
                    self.audit_record(f"hamcp200-automation-valid-{index}")[
                        "ha_endpoint_categories"
                    ],
                    ["config/automation"],
                )

    def test_get_entity_refuses_traversal_without_a_request(self):
        for index, value in enumerate(
            ENTITY_TRAVERSAL_IDS + ("light.kitchen/../../config", "light.%2e%2e", "light.k%2fx")
        ):
            with self.subTest(value=value):
                self.assert_refused(
                    "get_entity", {"entity_id": value}, f"hamcp200-entity-{index}"
                )

    def test_get_history_refuses_traversal_without_a_request(self):
        for index, value in enumerate(
            ENTITY_TRAVERSAL_IDS + ("sensor.a&end_time=x", "sensor.a/../b")
        ):
            with self.subTest(value=value):
                self.assert_refused(
                    "get_history", {"entity_id": value}, f"hamcp200-history-{index}"
                )

    def test_get_history_valid_entity_is_one_encoded_query_value(self):
        factory = CountingSessionFactory(body="[]")
        with patched_session(factory):
            payload = self.rpc(
                "get_history", {"entity_id": "sensor.kitchen_2"}, "hamcp200-history-ok"
            )
        self.assertNotIn("invalid_request", json.dumps(payload))
        self.assertEqual(len(factory.requests), 1)
        url = factory.requests[0]["url"]
        self.assertEqual(url.parts[-3:-1], ("history", "period"))
        self.assertEqual(url.query.getall("filter_entity_id"), ["sensor.kitchen_2"])

    def test_reliability_analysis_refuses_traversal_without_a_request(self):
        # The service strips surrounding whitespace before its grammar check
        # and uses only the stripped value, so "1\n" is the valid ID "1".
        for index, value in enumerate(v for v in TRAVERSAL_IDS if v != "1\n"):
            with self.subTest(value=value):
                payload, _ = self.assert_refused(
                    "automation_reliability_analysis",
                    {"automation_id": value},
                    f"hamcp200-reliability-{index}",
                )
                self.assertIn("invalid_identifier", json.dumps(payload))


# ---------------------------------------------------------------- source scan

HA_ROOTS = (
    "addons", "api", "calendars", "camera_proxy", "config", "core",
    "error_log", "events", "hassio", "history", "host", "logbook", "os",
    "services", "states", "supervisor", "template",
)
URL_NAMES = frozenset({
    "api_url", "_api_url", "base_url", "_base_url", "ha_url", "_ha_url",
    "websocket_url", "_websocket_url", "SUPERVISOR_URL",
})
REQUEST_CALLS = frozenset({"request", "rest", "ws_connect"})
# HTTP verb methods count as requests only on a session-like receiver, so
# ordinary ``dict.get`` lookups are not mistaken for request paths.
SESSION_VERB_CALLS = frozenset({"get", "post", "put", "patch", "delete", "head"})

# Reviewed non-request strings that resemble routes. Each entry is
# (repository-relative file, exact unparsed expression) so drift fails.
SCAN_EXEMPTIONS = frozenset({
    # The guarded URL constructor itself: the route was verified above it
    # and the normalized result is compared against the template below it.
    (
        "hass_mcp_engineering_beta/ha_mcp_engineering/clients/paths.py",
        "api_url.rstrip('/') + str(route)",
    ),
    # The fixed REST base derived from configuration, not a request path.
    (
        "hass_mcp_engineering_beta/ha_mcp_engineering/configuration.py",
        "f'{self.ha_url}/api'",
    ),
    # Hashed into an F3 strategy descriptor only; never sent as a request.
    # Re-encoding would change persisted plan hashes.
    (
        "hass_mcp_engineering_beta/ha_mcp_engineering/f3_configuration/strategies.py",
        "f'/config/{self.resource_type}/config/{target_id}'",
    ),
})


def _literal_prefix(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        text = ""
        for part in node.values:
            if not (isinstance(part, ast.Constant) and isinstance(part.value, str)):
                break
            text += part.value
        return text
    if isinstance(node, ast.BinOp):
        return _literal_prefix(node.left)
    return None


def _names(node: ast.AST) -> set[str]:
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
    return found


def _route_like(prefix: str | None) -> bool:
    if not prefix or not prefix.startswith("/"):
        return False
    return re.split(r"[/{%?]", prefix[1:], maxsplit=1)[0] in HA_ROOTS


def _dynamic(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(part, ast.FormattedValue) for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"format", "join", "replace"}
    ):
        return True
    return False


def scan_source(source: str, filename: str = "<synthetic>") -> list[str]:
    """Return raw interpolated Home Assistant path constructions."""

    findings = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        expression = None
        if _dynamic(node):
            target = node.func.value if isinstance(node, ast.Call) else node
            prefix = _literal_prefix(target)
            if _route_like(prefix):
                expression = node
            elif _names(node) & URL_NAMES and not isinstance(node, ast.Call):
                expression = node
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else ""
            )
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            session_verb = (
                name in SESSION_VERB_CALLS
                and receiver is not None
                and "session" in ast.unparse(receiver).lower()
            )
            if name in REQUEST_CALLS or session_verb:
                arguments = list(node.args) + [k.value for k in node.keywords if k.arg in {"path", "url"}]
                for argument in arguments:
                    if _dynamic(argument):
                        expression = argument
                        break
        if expression is not None:
            text = ast.unparse(expression)
            if (filename, text) not in SCAN_EXEMPTIONS:
                findings.append(f"{filename}:{expression.lineno}: {text}")
    return findings


def scan_package() -> list[str]:
    findings = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        findings.extend(scan_source(path.read_text(encoding="utf-8"), relative))
    return findings


class SourceScanTests(unittest.TestCase):
    def test_engineering_source_has_no_raw_interpolated_request_path(self):
        self.assertEqual(scan_package(), [])

    def test_every_exemption_is_still_present(self):
        present = set()
        for filename, text in SCAN_EXEMPTIONS:
            source = (ROOT / filename).read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, (ast.JoinedStr, ast.BinOp)) and ast.unparse(node) == text:
                    present.add((filename, text))
        self.assertEqual(present, set(SCAN_EXEMPTIONS))

    def test_scanner_fails_on_each_raw_interpolation_form(self):
        raw_sources = (
            'path = f"/config/automation/config/{automation_id}"',
            'path = f"/states/{entity_id}"',
            'path = "/states/" + entity_id',
            'path = "/history/period/%s" % start',
            'path = "/services/{}/{}".format(domain, service)',
            'url = f"{self._api_url}/states"',
            'url = self.settings.api_url + "/states"',
            'url = f"{SUPERVISOR_URL}/addons/self/info"',
            'value = await client.request("GET", f"/x/{y}")',
            'value = await rest("GET", "/x/" + y)',
            'value = await session.get(base + path)',
            'value = await client.request("GET", path="/logbook/" + start)',
        )
        for source in raw_sources:
            with self.subTest(source=source):
                self.assertTrue(scan_source(source), source)

    def test_scanner_accepts_guarded_and_non_request_forms(self):
        clean_sources = (
            'path = rest_path("/states/{}", entity_id, kinds=("entity_id",))',
            'value = await client.request("GET", "/states")',
            'value = await client.request("GET", rest_path("/config/automation/config/{}", x))',
            'pointer = f"/views/{index}/cards/{card}"',
            'prefix = f"/{secret}"',
            'review_path = f"/app/{slug}"',
            'url = request_url(self._api_url, "/states")',
        )
        for source in clean_sources:
            with self.subTest(source=source):
                self.assertEqual(scan_source(source), [])


class CompatibilityEntryPointTests(unittest.TestCase):
    """Finding 15: the compatibility module is not a second server."""

    def test_compatibility_module_has_no_executable_entry_point(self):
        source = (PACKAGE_DIR / "tools" / "compatibility.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        for name in ("main", "Gateway", "TokenBucket", "audit_write"):
            self.assertNotIn(name, top_level)
            self.assertFalse(hasattr(compatibility, name))
        main_guards = [
            node
            for node in tree.body
            if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test)
        ]
        self.assertEqual(main_guards, [])
        self.assertNotIn("uvicorn", source)

    def test_canonical_application_remains_the_entry_point(self):
        self.assertTrue((PACKAGE_DIR / "__main__.py").is_file())


if __name__ == "__main__":
    unittest.main()
