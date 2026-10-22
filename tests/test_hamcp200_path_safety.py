"""HAMCP-200 path-segment safety for every Home Assistant REST request.

Every REST request carries a ``Route``: a registered fixed template plus
values that passed one segment guard. ``rest_path`` is the only way to get
one, and the transport refuses a plain string (however it was built) or a
forged object before any session or credential exists. Traversal-shaped
identifiers are refused as truthful ``invalid_request`` failures, the
transport re-verifies the normalized route before attaching the token and
never follows redirects, no crafted identifier can reach ``/api/webhook/*``,
and a secondary source scan fails on raw interpolated paths or aliased
request methods. Offline and deterministic: the aiohttp session is a
counting fake and no Home Assistant is contacted.
"""

from __future__ import annotations

import ast
import asyncio
from collections import Counter
import copy
import dataclasses
import functools
import json
from pathlib import Path
import pickle
import re
import shutil
import subprocess
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
from tests.support.process_isolation import restore_after_class  # noqa: E402

from ha_mcp_engineering.application import create_application  # noqa: E402
from ha_mcp_engineering.clients import paths  # noqa: E402
from ha_mcp_engineering.clients.paths import (  # noqa: E402
    INVALID_IDENTIFIER_MESSAGE,
    ROUTE_TEMPLATES,
    Route,
    UnsafeRequestPathError,
    is_route,
    path_segment,
    request_url,
    require_route,
    rest_path,
)
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
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
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402
from scripts import harness_routes  # noqa: E402
from scripts.harness_routes import (  # noqa: E402
    HARNESS_ROUTE_TEMPLATES,
    HarnessRouteError,
    harness_request,
    harness_url,
)


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

    def test_well_formed_templates_outside_the_registry_are_refused(self):
        # Codex probe: a raw "/states/" + value has a valid route shape but
        # names no registered template, so even as a template it refuses.
        for template in (
            "/states/" + "sensor/extra",
            "/states/sensor",
            "/webhook/{}",
            "/hassio/addons",
            "/config/{}/config/{}",
            "/services/{}/{}",
        ):
            with self.subTest(template=template):
                with self.assertRaises(UnsafeRequestPathError) as caught:
                    rest_path(template, *(("x",) * template.count("{}")))
                self.assertEqual(caught.exception.details["reason"], "unregistered_route")

    def test_every_plain_string_is_refused_even_a_registered_static_route(self):
        self.assertEqual(rest_path("/states").template, "/states")
        for raw in (
            "/states",
            "/config/core/check_config",
            "/states/light.kitchen",
            "/states/" + "sensor/extra",
            "/config/automation/config/../../hassio",
            "/config/automation/config/1%2f..",
            "states",
            "/states?x=1",
            str(rest_path("/states")),
            rest_path("/states") + "",
            rest_path("/states/{}", "light.kitchen", kinds=("entity_id",)).replace(
                "light.kitchen", "light.kitchen/../../../hassio"
            ),
            None,
            b"/states",
            7,
        ):
            with self.subTest(raw=raw):
                self.assertFalse(is_route(raw))
                with self.assertRaises(UnsafeRequestPathError) as caught:
                    require_route(raw)
                self.assertEqual(caught.exception.details["reason"], "unbound_request_path")
                with self.assertRaises(UnsafeRequestPathError):
                    request_url(API_URL, raw)


class RouteForgeryTests(unittest.TestCase):
    """A ``Route`` exists only when ``rest_path`` issued it."""

    def test_route_cannot_be_constructed_or_subclassed(self):
        for build in (
            lambda: Route("/states"),
            lambda: Route.__new__(Route, "/states"),
            lambda: Route(),
        ):
            with self.assertRaises(TypeError):
                build()
        with self.assertRaises(TypeError):
            type("ForgedRoute", (Route,), {})

    def test_str_new_forgery_is_not_an_issued_route(self):
        forged = str.__new__(Route, "/states")
        self.assertIs(type(forged), Route)
        self.assertFalse(is_route(forged))
        with self.assertRaises(UnsafeRequestPathError) as caught:
            require_route(forged)
        self.assertEqual(caught.exception.details["reason"], "unbound_request_path")

    def test_issued_route_is_immutable_and_cannot_be_cloned_or_serialized(self):
        route = rest_path("/config/automation/config/{}", "1")
        self.assertTrue(is_route(route))
        for name in ("_template", "_values", "template", "extra"):
            with self.subTest(name=name):
                with self.assertRaises(AttributeError):
                    setattr(route, name, "/states/{}")
                with self.assertRaises(AttributeError):
                    delattr(route, name)
        self.assertIs(copy.copy(route), route)
        self.assertIs(copy.deepcopy(route), route)
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            with self.subTest(protocol=protocol):
                with self.assertRaises(TypeError):
                    pickle.dumps(route, protocol=protocol)

    def test_every_str_operation_on_a_route_yields_a_refused_plain_str(self):
        route = rest_path("/states/{}", "light.kitchen", kinds=("entity_id",))
        for derived in (
            route + "/x",
            "/x" + route,
            route[:],
            route.replace("kitchen", "x"),
            route.lower(),
            "%s" % route,
            f"{route}",
            str(route),
            "".join([route]),
        ):
            with self.subTest(derived=derived):
                self.assertIs(type(derived), str)
                self.assertFalse(is_route(derived))

    def test_tampering_with_an_issued_route_is_refused_centrally(self):
        # Bypassing the immutability guard still cannot retarget a route:
        # the transport re-encodes the bound template and values.
        route = rest_path("/config/automation/config/{}", "1")
        object.__setattr__(route, "_template", "/states/{}")
        with self.assertRaises(UnsafeRequestPathError) as caught:
            request_url(API_URL, route)
        self.assertEqual(caught.exception.details["reason"], "route_mismatch")
        route = rest_path("/config/automation/config/{}", "1")
        object.__setattr__(route, "_template", "/webhook/{}")
        with self.assertRaises(UnsafeRequestPathError) as caught:
            request_url(API_URL, route)
        self.assertEqual(caught.exception.details["reason"], "unregistered_route")


class WebhookReachabilityTests(unittest.TestCase):
    """M1: a crafted ID can never reach /api/webhook/* or leave its template."""

    WEBHOOK_IDS = (
        "../../../webhook/hamcp200_synthetic_only",
        "../../webhook/x",
        "../webhook/x",
        "..%2f..%2f..%2fwebhook%2fx",
        "%2e%2e/%2e%2e/%2e%2e/webhook/x",
        "%252e%252e%252f%252e%252e%252fwebhook%252fx",
        "..\\..\\..\\webhook\\x",
        "1/../../../webhook/x",
        "x?/../../../webhook/x",
        "x#/../../../webhook/x",
    )

    def test_no_registered_template_names_a_webhook_or_unlisted_root(self):
        for template in ROUTE_TEMPLATES | HARNESS_ROUTE_TEMPLATES:
            with self.subTest(template=template):
                self.assertNotIn("webhook", template)
                self.assertIn(
                    template.split("/")[1],
                    # HAMCP-131 adds the fixed Supervisor /core/logs route.
                    {"addons", "config", "core", "events", "history", "logbook",
                     "services", "states", "template"},
                )

    def test_crafted_ids_never_reach_a_webhook_or_another_route(self):
        kinds_for = {"/states/{}": "entity_id", "/history/period/{}": "timestamp",
                     "/logbook/{}": "timestamp"}
        for template in sorted(ROUTE_TEMPLATES):
            if "{}" not in template:
                continue
            kind = kinds_for.get(template, "identifier")
            for value in self.WEBHOOK_IDS + TRAVERSAL_IDS:
                with self.subTest(template=template, value=value):
                    try:
                        route = rest_path(template, value, kinds=(kind,))
                    except UnsafeRequestPathError:
                        continue
                    # Only a value that is one inert segment gets this far.
                    url = request_url(API_URL, route)
                    self.assertEqual(
                        url.parts[:-1],
                        ("/", "core", "api", *template.split("/")[1:-1]),
                    )
                    self.assertEqual(url.parts[-1], value)
                    self.assertNotIn("webhook", url.parts[3:-1])

    def test_webhook_ids_send_no_request_through_the_client(self):
        factory = CountingSessionFactory()
        client = rest_client()
        with patched_session(factory):
            for value in self.WEBHOOK_IDS:
                with self.subTest(value=value):
                    with self.assertRaises(UnsafeRequestPathError):
                        asyncio.run(client.request(
                            "GET", rest_path("/config/automation/config/{}", value)
                        ))
                    # The pre-fix raw form is now refused as a plain string.
                    with self.assertRaises(UnsafeRequestPathError):
                        asyncio.run(client.request(
                            "GET", "/config/automation/config/" + value
                        ))
        self.assertEqual(factory.sessions, 0)
        self.assertEqual(factory.requests, [])

    def test_pre_fix_normalization_target_is_refused_by_the_url_guard(self):
        # The Codex probe's pre-fix URL normalized to /core/api/webhook/...;
        # even an issued route carrying that encoding cannot be sent.
        encoded = "/config/automation/config/../../../webhook/hamcp200_synthetic_only"
        with patch.object(paths, "_encode", lambda *args, **kwargs: encoded):
            route = paths._issue(
                encoded, "/config/automation/config/{}", ("1",), ("identifier",), ()
            )
            with self.assertRaises(UnsafeRequestPathError) as caught:
                request_url(API_URL, route)
        self.assertEqual(caught.exception.details["reason"], "route_mismatch")


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
        # An issued route whose text disagrees with its bound template.
        return paths._issue(
            encoded, "/config/automation/config/{}", values, kinds, query
        )

    def test_route_text_that_disagrees_with_its_binding_is_refused(self):
        for encoded in (
            "/config/automation/config/../../../hassio/addons",
            "/config/automation/config/2",
            "/states/1",
        ):
            with self.subTest(encoded=encoded):
                with self.assertRaises(UnsafeRequestPathError) as caught:
                    request_url(API_URL, self.forged(encoded))
                self.assertEqual(caught.exception.details["reason"], "route_mismatch")

    def test_crafted_route_that_normalizes_elsewhere_is_refused(self):
        # The re-encode check is bypassed here, so only the normalized-route
        # comparison can catch text that dot-normalizes into Supervisor routes.
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
                with patch.object(paths, "_encode", lambda *a, _e=encoded, **k: _e):
                    route = self.forged(encoded)
                    with self.assertRaises(UnsafeRequestPathError) as caught:
                        request_url(API_URL, route)
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
            "/states",
            str.__new__(Route, "/states"),
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


class RuntimeProbeTests(unittest.TestCase):
    """Codex's scanner counterexamples, executed: the client refuses them all.

    Each probe builds its path the way the review showed a source scan could
    miss. None yields a ``Route``, so each fails before any session exists.
    """

    ENTITY = "sensor/extra"

    async def alias_prefix(self, client):
        prefix = "/states/"
        path = prefix + self.ENTITY
        fetch = client.request
        return await fetch("GET", path)

    async def helper_wrapper(self, client):
        def build(prefix, item):
            return prefix + item
        return await client.request("GET", build("/states/", self.ENTITY))

    async def format_alias(self, client):
        render = "/states/{}".format
        return await client.request("GET", render(self.ENTITY))

    async def format_map(self, client):
        return await client.request(
            "GET", "/states/{entity}".format_map({"entity": self.ENTITY})
        )

    async def dynamic_template(self, client):
        caller_route = "states/" + self.ENTITY
        return await client.request("GET", rest_path("/" + caller_route))

    async def receiver_alias(self, client):
        rest = client
        alias = rest
        return await alias.request("GET", "/states/" + self.ENTITY)

    async def request_alias(self, client):
        fetch = getattr(client, "request")
        return await fetch("GET", "/states/" + self.ENTITY)

    async def partial_alias(self, client):
        fetch = functools.partial(client.request, "GET")
        return await fetch("/states/" + self.ENTITY)

    async def keyword_path(self, client):
        return await client.request("GET", path="/states/" + self.ENTITY)

    async def unsafe_binder(self, client):
        def state_path(entity):
            return "/states/" + entity
        return await client.request("GET", state_path(self.ENTITY))

    async def static_plain_string(self, client):
        return await client.request("GET", "/states")

    PROBES = (
        "alias_prefix", "helper_wrapper", "format_alias", "format_map",
        "dynamic_template", "receiver_alias", "request_alias", "partial_alias",
        "keyword_path", "unsafe_binder", "static_plain_string",
    )

    def test_every_probe_is_refused_before_any_session(self):
        factory = CountingSessionFactory()
        client = rest_client()
        with patched_session(factory):
            for name in self.PROBES:
                with self.subTest(probe=name):
                    with self.assertRaises(UnsafeRequestPathError) as caught:
                        asyncio.run(getattr(self, name)(client))
                    self.assertIn(
                        caught.exception.details["reason"],
                        {"unbound_request_path", "unregistered_route"},
                    )
        self.assertEqual(factory.sessions, 0)
        self.assertEqual(factory.requests, [])

    def test_the_same_requests_succeed_only_through_rest_path(self):
        factory = CountingSessionFactory()
        with patched_session(factory):
            asyncio.run(rest_client().request("GET", rest_path("/states")))
            asyncio.run(rest_client().request(
                "GET", rest_path("/states/{}", "sensor.extra", kinds=("entity_id",))
            ))
        self.assertEqual(
            [str(request["url"]) for request in factory.requests],
            [API_URL + "/states", API_URL + "/states/sensor.extra"],
        )


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
        restore_after_class(cls)
        class SyntheticCoreSource:
            async def capture_core_snapshot(self):
                return beta.core_snapshot(
                    "2026.8.1", evidence=beta.core_capability_evidence()
                )

        cls.tempdir = tempfile.TemporaryDirectory()
        # The refusal matrix exceeds the default synthetic burst; rate
        # limiting is not under test here.
        settings = dataclasses.replace(
            beta.beta_settings(str(Path(cls.tempdir.name) / "audit.jsonl")),
            rate_limit_per_minute=6000,
            rate_limit_burst=1000,
        )
        gateway = create_application(settings, core_snapshot_source=SyntheticCoreSource())
        asyncio.run(CORE_READMISSION.reconcile_once("startup"))
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

    def test_tools_refuse_webhook_traversal_without_a_request(self):
        # M1: the pre-fix exposure was a GET to a webhook; never again.
        for index, value in enumerate(WebhookReachabilityTests.WEBHOOK_IDS):
            with self.subTest(value=value):
                self.assert_refused(
                    "get_automation_config",
                    {"automation_id": value},
                    f"hamcp200-webhook-automation-{index}",
                )
                self.assert_refused(
                    "get_entity", {"entity_id": value}, f"hamcp200-webhook-entity-{index}"
                )

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
#
# The runtime type check above is the primary control: only a ``Route``
# reaches the network. These scans are a secondary net that keeps the
# source honest about it.

HA_ROOTS = (
    "addons", "api", "calendars", "camera_proxy", "config", "core",
    "error_log", "events", "hassio", "history", "host", "logbook", "os",
    "services", "states", "supervisor", "template", "webhook",
)
URL_NAMES = frozenset({
    "api_url", "_api_url", "base_url", "_base_url", "ha_url", "_ha_url",
    "websocket_url", "_websocket_url", "SUPERVISOR_URL",
})
REQUEST_CALLS = frozenset({"request", "rest", "ws_connect"})
# HTTP verb methods count as requests only on a session-like receiver, so
# ordinary ``dict.get`` lookups are not mistaken for request paths.
SESSION_VERB_CALLS = frozenset({"get", "post", "put", "patch", "delete", "head"})
# A ``.request`` attribute read without calling it is an alias of the
# transport when its receiver looks like a client (``prepared.request`` on
# fan plans is data, not a transport).
CLIENT_RECEIVER = re.compile(r"client|rest|session", re.IGNORECASE)
# The literal registries themselves, which name every route by design.
REGISTRY_NAMES = frozenset({"ROUTE_TEMPLATES"})
PATHS_MODULE = "hass_mcp_engineering_beta/ha_mcp_engineering/clients/paths.py"
# Every runtime websocket connects to the one configured URL.
WEBSOCKET_URLS = frozenset({
    "self.settings.websocket_url",
    "self._settings.websocket_url",
    "self.client._ws_url",
})

# Reviewed non-request strings that resemble routes. Each entry is
# (repository-relative file, exact unparsed expression) so drift fails.
SCAN_EXEMPTIONS = frozenset({
    # The guarded URL constructor itself: the route was verified above it
    # and the normalized result is compared against the template below it.
    (
        PATHS_MODULE,
        "api_url.rstrip('/') + str.__str__(route)",
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
        and node.func.attr in {"format", "format_map", "join", "replace"}
    ):
        return True
    return False


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _request_aliases(tree: ast.AST, *, any_receiver: bool) -> list[ast.AST]:
    """``x.request`` read without a call, or ``getattr(x, "request")``."""

    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "request"
            and id(node) not in called
            and (any_receiver or CLIENT_RECEIVER.search(ast.unparse(node.value)))
        ):
            found.append(node)
        elif (
            isinstance(node, ast.Call)
            and _call_name(node) == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "request"
        ):
            found.append(node)
    return found


def _bare_route_literals(tree: ast.AST, filename: str) -> list[ast.AST]:
    """Route-shaped prefixes or templates outside a ``rest_path`` call.

    A template literal may appear only as ``rest_path``'s first argument
    (or in the registry, or compared with ``route.template``); a prefix
    such as ``"/states/"`` exists only to be concatenated.
    """

    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "rest_path" and node.args:
            allowed.add(id(node.args[0]))
        elif isinstance(node, ast.JoinedStr):
            # f-strings are judged whole by the dynamic-path rule.
            allowed.update(id(part) for part in node.values)
        elif isinstance(node, ast.Compare) and ast.unparse(node.left).endswith(".template"):
            allowed.update(id(item) for item in node.comparators)
        elif (
            filename == PATHS_MODULE
            and isinstance(node, (ast.Assign, ast.AnnAssign))
            and {ast.unparse(t) for t in getattr(node, "targets", [getattr(node, "target", None)])}
            & REGISTRY_NAMES
        ):
            allowed.update(id(item) for item in ast.walk(node))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in allowed
        and _route_like(node.value)
        and (node.value.endswith("/") or "{" in node.value)
    ]


def scan_source(source: str, filename: str = "<synthetic>") -> list[str]:
    """Return raw path constructions, aliased transports and computed templates."""

    findings = []
    tree = ast.parse(source)
    flagged: list[ast.AST] = []
    for node in ast.walk(tree):
        expression = None
        if _dynamic(node):
            target = node.func.value if isinstance(node, ast.Call) else node
            prefix = _literal_prefix(target)
            if _route_like(prefix):
                expression = node
            elif _names(node) & URL_NAMES and not isinstance(node, ast.Call):
                expression = node
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Constant)
            and _route_like(_literal_prefix(node.value))
        ):
            # A bound method of a route literal (``"/states/{}".format``).
            expression = node
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            session_verb = (
                name in SESSION_VERB_CALLS
                and receiver is not None
                and "session" in ast.unparse(receiver).lower()
            )
            if name == "rest_path" and not (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                # A computed template, however safe its pieces look.
                expression = node
            elif name == "ws_connect" and (
                not node.args or ast.unparse(node.args[0]) not in WEBSOCKET_URLS
            ):
                expression = node
            elif name in REQUEST_CALLS or session_verb:
                arguments = list(node.args) + [k.value for k in node.keywords if k.arg in {"path", "url"}]
                for argument in arguments:
                    if _dynamic(argument):
                        expression = argument
                        break
        if expression is not None:
            flagged.append(expression)
    flagged.extend(_request_aliases(tree, any_receiver=False))
    flagged.extend(_bare_route_literals(tree, filename))
    for expression in flagged:
        text = ast.unparse(expression)
        if (filename, text) not in SCAN_EXEMPTIONS:
            findings.append(f"{filename}:{expression.lineno}: {text}")
    return sorted(set(findings))


def package_sources():
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")


def scan_package() -> list[str]:
    findings = []
    for relative, source in package_sources():
        findings.extend(scan_source(source, relative))
    return findings


def rest_path_templates(sources) -> set[str]:
    templates = set()
    for _, source in sources:
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and _call_name(node) == "rest_path"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                templates.add(node.args[0].value)
    return templates


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

    def test_runtime_uses_exactly_the_runtime_route_registry(self):
        # Every runtime template is a registered literal, and every runtime
        # registration is in use: the registry is the reviewed route list.
        self.assertEqual(rest_path_templates(package_sources()), set(ROUTE_TEMPLATES))
        self.assertFalse(ROUTE_TEMPLATES & HARNESS_ROUTE_TEMPLATES)

    def test_no_runtime_module_names_or_imports_a_harness_route(self):
        # Including clients/paths.py: the runtime holds no harness registry.
        findings = []
        for relative, source in package_sources():
            for node in ast.walk(ast.parse(source)):
                names = {getattr(node, "id", None), getattr(node, "attr", None),
                         getattr(node, "name", None), getattr(node, "module", None)}
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and any(t in node.value for t in HARNESS_ROUTE_TEMPLATES)
                ) or names & {"HARNESS_ROUTE_TEMPLATES", "harness_routes", "harness_request"}:
                    findings.append(f"{relative}:{getattr(node, 'lineno', '?')}")
        self.assertEqual(findings, [])

    def test_websockets_connect_only_to_the_configured_url(self):
        # No websocket path builder exists: every connection uses the one
        # configured URL, and the cached copies are assigned from it.
        connects = []
        for relative, source in package_sources():
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call) and _call_name(node) == "ws_connect":
                    connects.append(ast.unparse(node.args[0]))
                if (
                    isinstance(node, ast.Assign)
                    and any(ast.unparse(t) == "self._ws_url" for t in node.targets)
                ):
                    self.assertEqual(ast.unparse(node.value), "settings.websocket_url", relative)
        self.assertTrue(connects)
        self.assertLessEqual(set(connects), WEBSOCKET_URLS)

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
            'path = f"/webhook/{webhook_id}"',
            'await session.ws_connect(base + "/websocket")',
            'await session.ws_connect(f"{host}/api/websocket")',
        )
        for source in raw_sources:
            with self.subTest(source=source):
                self.assertTrue(scan_source(source), source)

    def test_scanner_fails_on_each_codex_counterexample(self):
        # Codex's package-scan misses, now each flagged by the scanner (and,
        # independently, refused at runtime by RuntimeProbeTests).
        variants = {
            "alias_prefix": 'prefix="/states/"\npath=prefix+entity\nfetch=client.request\nawait fetch("GET",path)',
            "helper_wrapper": 'def build(prefix,item):\n return prefix+item\nawait client.request("GET",build("/states/",entity))',
            "format_alias": 'render="/states/{}".format\npath=render(entity)\nawait client.request("GET",path)',
            "format_map": 'await client.request("GET","/states/{entity}".format_map(values))',
            "dynamic_template": 'path=rest_path("/"+caller_route)',
            "getattr_alias": 'fetch=getattr(self.rest_client,"request")\nawait fetch("GET",path)',
            "partial_alias": 'fetch=functools.partial(self.rest_client.request,"GET")',
            "argument_alias": 'await run(self.rest_client.request, path)',
            "named_template": 'TEMPLATE="/states/{}"\npath=rest_path(TEMPLATE, entity)',
            "lookup_template": 'path=rest_path(ROUTES[kind], entity)',
        }
        for name, source in variants.items():
            with self.subTest(variant=name):
                self.assertTrue(scan_source(source), name)

    def test_scanner_accepts_guarded_and_non_request_forms(self):
        clean_sources = (
            'path = rest_path("/states/{}", entity_id, kinds=("entity_id",))',
            'value = await client.request("GET", rest_path("/states"))',
            'value = await client.request("GET", rest_path("/config/automation/config/{}", x))',
            'value = await client.request("GET", path)',
            'pointer = f"/views/{index}/cards/{card}"',
            'prefix = f"/{secret}"',
            'review_path = f"/app/{slug}"',
            'url = request_url(self._api_url, route)',
            'request = prepared.request',
            'if route.template == "/states/{}": pass',
            'await session.ws_connect(self.settings.websocket_url)',
        )
        for source in clean_sources:
            with self.subTest(source=source):
                self.assertEqual(scan_source(source), [])


HARNESS_BINDERS = frozenset({"rest_path", "state_path"})
# Reviewed raw HTTP sessions in harnesses that are not the guarded client:
# the disposable-Core onboarding bootstrap and the 850 relay passthrough.
HARNESS_EXEMPTIONS = frozenset({
    ("real_ha_contract_tests.py", "f'{HA_URL}{path}'"),
})


def _binder_findings(tree: ast.AST) -> list[str]:
    """A local binder must return ``rest_path`` with a literal template."""

    findings = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in HARNESS_BINDERS
        ):
            continue
        returns = [item for item in ast.walk(node) if isinstance(item, ast.Return)]
        ok = node.name != "rest_path" and returns and all(
            isinstance(item.value, ast.Call)
            and _call_name(item.value) == "rest_path"
            and item.value.args
            and isinstance(item.value.args[0], ast.Constant)
            and isinstance(item.value.args[0].value, str)
            for item in returns
        )
        if not ok:
            findings.append(f"{node.lineno}: unsafe binder {node.name}")
    return findings


def scan_harness_source(source: str, filename: str = "<synthetic>") -> list[str]:
    """Flag a harness path the guarded transport would refuse, or an alias."""

    tree = ast.parse(source)
    findings = _binder_findings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "harness_request":
            # The harness-only sender takes a literal harness template.
            template = node.args[2] if len(node.args) >= 3 else None
            if not (
                isinstance(template, ast.Constant)
                and template.value in HARNESS_ROUTE_TEMPLATES
            ):
                findings.append(f"{node.lineno}: harness route {ast.unparse(node)[:80]}")
            continue
        if not (isinstance(node, ast.Call) and _call_name(node) == "request"):
            continue
        paths_given = [k.value for k in node.keywords if k.arg in {"path", "url"}]
        if len(node.args) >= 2:
            paths_given.insert(0, node.args[1])
        for path in paths_given:
            if (filename, ast.unparse(path)) in HARNESS_EXEMPTIONS:
                continue
            if isinstance(path, ast.Call) and _call_name(path) in HARNESS_BINDERS:
                if _call_name(path) == "rest_path" and not (
                    path.args and isinstance(path.args[0], ast.Constant)
                ):
                    findings.append(f"{node.lineno}: computed template {ast.unparse(path)[:80]}")
                continue
            if (isinstance(path, ast.Constant) and isinstance(path.value, str)) or _dynamic(path):
                # A plain string, static or built, is refused by the client.
                findings.append(f"{node.lineno}: {ast.unparse(node)[:90]}")
    for alias in _request_aliases(tree, any_receiver=True):
        findings.append(f"{alias.lineno}: aliased request {ast.unparse(alias)[:80]}")
    return findings


def harness_sources():
    for script in sorted((ROOT / "scripts").glob("*.py")):
        source = script.read_text(encoding="utf-8")
        if "ha_mcp_engineering.clients" in source:
            yield script.name, source


def harness_request_templates(sources) -> Counter:
    templates = Counter()
    for _, source in sources:
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and _call_name(node) == "harness_request"
                and len(node.args) >= 3
                and isinstance(node.args[2], ast.Constant)
            ):
                templates[node.args[2].value] += 1
    return templates


# The six harness calls that sent a harness-only route through the runtime
# client before the move (9552182), by template. They now go through
# ``harness_request`` and must keep sending exactly these routes.
PRE_MOVE_HARNESS_CALLS = Counter({
    "/config/config_entries/flow": 1,
    "/events/dev14_real_contract_trigger": 1,
    "/services/automation/reload": 2,
    "/services/script/reload": 2,
})


class HarnessScriptTests(unittest.TestCase):
    """Disposable-Core harnesses call the guarded transport with bound routes."""

    def test_harness_scripts_bind_every_home_assistant_rest_path(self):
        findings = []
        for name, source in harness_sources():
            findings.extend(f"{name}:{item}" for item in scan_harness_source(source, name))
        self.assertEqual(findings, [])

    def test_harness_templates_are_registered_and_exemptions_present(self):
        # Through the runtime client, harnesses use runtime routes only.
        self.assertLessEqual(rest_path_templates(harness_sources()), set(ROUTE_TEMPLATES))
        # Every harness-only route goes through the harness sender, at the
        # same call sites as before the move, and every one is still used.
        self.assertEqual(harness_request_templates(harness_sources()), PRE_MOVE_HARNESS_CALLS)
        self.assertEqual(set(PRE_MOVE_HARNESS_CALLS), set(HARNESS_ROUTE_TEMPLATES))
        sources = dict(harness_sources())
        for filename, text in HARNESS_EXEMPTIONS:
            self.assertIn(
                text,
                {ast.unparse(n) for n in ast.walk(ast.parse(sources[filename]))},
            )

    def test_harness_scanner_flags_paths_the_transport_refuses(self):
        for source in (
            'await rest.request("GET", "/states/" + FAN)',
            'await rest.request("POST", "/states/light.synthetic_search_fixture", body={})',
            'await rest.request("GET", f"/config/script/config/{key}")',
            'await gateway.rest_client.request("DELETE", f"/config/{kind}/config/{rid}")',
            # Raw static routes are refused at runtime now, so flagged too.
            'await rest.request("GET", "/config")',
            # Codex's harness-scan misses.
            'client=rest\nawait client.request("GET","/states/"+entity)',
            'fetch=rest.request\nawait fetch("GET","/states/"+entity)',
            'await rest.request("GET",path="/states/"+entity)',
            'def state_path(entity):\n return "/states/"+entity\nawait rest.request("GET",state_path(entity))',
            'def state_path(entity):\n return rest_path(TEMPLATE, entity)',
            'fetch=getattr(rest,"request")',
            'await rest.request("GET", rest_path("/" + route))',
            # Harness-only routes go only through the harness sender, literally.
            'await harness_request(rest.settings, "POST", "/states", {})',
            'await harness_request(rest.settings, "POST", ROUTE, {})',
            'await harness_request(rest.settings, "POST", "/services/" + name, {})',
        ):
            with self.subTest(source=source):
                self.assertTrue(scan_harness_source(source))
        for source in (
            'await rest.request("GET", rest_path("/config"))',
            'await rest.request("GET", state_path(FAN))',
            'await rest.request("GET", rest_path("/config/script/config/{}", key))',
            'await session.request("GET", url)',
            'await harness_request(rest.settings, "POST", "/services/script/reload", {})',
            'def state_path(entity_id):\n from x import rest_path\n return rest_path("/states/{}", entity_id, kinds=("entity_id",))',
        ):
            with self.subTest(source=source):
                self.assertEqual(scan_harness_source(source), [])


HARNESS_API_URL = "http://127.0.0.1:8123/api"
# The exact URLs the runtime client sent for the harness routes before the
# move: ``request_url`` built ``api_url.rstrip("/") + template``.
PRE_MOVE_HARNESS_URLS = {
    "/config/config_entries/flow": "http://127.0.0.1:8123/api/config/config_entries/flow",
    "/events/dev14_real_contract_trigger":
        "http://127.0.0.1:8123/api/events/dev14_real_contract_trigger",
    "/services/automation/reload": "http://127.0.0.1:8123/api/services/automation/reload",
    "/services/script/reload": "http://127.0.0.1:8123/api/services/script/reload",
}


def harness_settings(ha_url: str = "http://127.0.0.1:8123"):
    return dataclasses.replace(
        rest_client().settings, ha_url=ha_url, ha_token="synthetic-harness-token"
    )


def patched_harness_session(factory: CountingSessionFactory):
    return patch.object(harness_routes.aiohttp, "ClientSession", new=factory)


class HarnessRouteIsolationTests(unittest.TestCase):
    """The runtime REST client accepts only runtime templates."""

    def test_runtime_registry_holds_no_harness_template(self):
        self.assertFalse(ROUTE_TEMPLATES & HARNESS_ROUTE_TEMPLATES)
        self.assertFalse(hasattr(paths, "HARNESS_ROUTE_TEMPLATES"))
        self.assertNotIn("HARNESS_ROUTE_TEMPLATES", paths.__all__)

    def test_rest_path_refuses_every_harness_template(self):
        for template in sorted(HARNESS_ROUTE_TEMPLATES):
            with self.subTest(template=template):
                with self.assertRaises(UnsafeRequestPathError) as caught:
                    rest_path(template)
                self.assertEqual(caught.exception.details["reason"], "unregistered_route")

    def test_harness_route_given_to_the_runtime_client_sends_nothing(self):
        factory = CountingSessionFactory()
        client = rest_client()
        with patched_session(factory):
            for template in sorted(HARNESS_ROUTE_TEMPLATES):
                # A route issued for a harness template without rest_path's
                # registry check, as if the runtime still accepted one.
                forged = paths._issue(template, template, (), (), ())
                self.assertTrue(is_route(forged))
                for given, reason in (
                    (template, "unbound_request_path"),
                    (harness_url(HARNESS_API_URL, template), "unbound_request_path"),
                    (forged, "unregistered_route"),
                ):
                    with self.subTest(template=template, given=type(given).__name__):
                        with self.assertRaises(UnsafeRequestPathError) as caught:
                            asyncio.run(client.request("POST", given, {}))
                        self.assertEqual(caught.exception.details["reason"], reason)
                        # The shared URL guard used by every runtime transport.
                        with self.assertRaises(UnsafeRequestPathError):
                            request_url(API_URL, given)
        self.assertEqual(factory.sessions, 0)
        self.assertEqual(factory.requests, [])


class HarnessSenderTests(unittest.TestCase):
    """``scripts/harness_routes`` sends exactly the pre-move harness URLs."""

    def test_each_harness_route_sends_the_pre_move_url(self):
        self.assertEqual(set(PRE_MOVE_HARNESS_URLS), set(HARNESS_ROUTE_TEMPLATES))
        factory = CountingSessionFactory(body='{"type": "create_entry"}')
        settings = harness_settings()
        with patched_harness_session(factory):
            for template, expected in sorted(PRE_MOVE_HARNESS_URLS.items()):
                with self.subTest(template=template):
                    result = asyncio.run(harness_request(
                        settings, "POST", template, {"source": "disposable_contract"}
                    ))
                    self.assertEqual(result, {"type": "create_entry"})
                    sent = factory.requests[-1]
                    self.assertEqual(sent["method"], "POST")
                    self.assertEqual(str(sent["url"]), expected)
                    self.assertEqual(sent["url"], harness_url(settings.api_url, template))
                    self.assertEqual(sent["headers"], {
                        "Authorization": "Bearer synthetic-harness-token",
                        "Content-Type": "application/json",
                    })
                    self.assertEqual(sent["json"], {"source": "disposable_contract"})
                    self.assertIs(sent["allow_redirects"], False)
        self.assertEqual(len(factory.requests), len(PRE_MOVE_HARNESS_URLS))

    def test_harness_sender_refuses_anything_but_a_harness_route(self):
        factory = CountingSessionFactory()
        settings = harness_settings()
        with patched_harness_session(factory):
            for template in (
                "/states", "/config", "/states/{}", "/template",
                "/services/automation/reload/../../../hassio/addons",
                "/services/automation/reload?x=1", "/webhook/x",
                "services/automation/reload", rest_path("/states"), None, b"/events",
            ):
                with self.subTest(template=template):
                    with self.assertRaises(HarnessRouteError):
                        asyncio.run(harness_request(settings, "POST", template, {}))
            # Only a loopback (disposable) Core; never Supervisor or a host.
            for ha_url in ("http://supervisor/core", "http://homeassistant.local:8123",
                           "http://127.0.0.1@example.invalid:8123"):
                with self.subTest(ha_url=ha_url):
                    with self.assertRaises(HarnessRouteError):
                        asyncio.run(harness_request(
                            harness_settings(ha_url), "POST", "/services/script/reload", {}
                        ))
            # Never inside an Engineering request context.
            _telemetry, context = begin_request()
            try:
                with self.assertRaises(HarnessRouteError):
                    asyncio.run(harness_request(settings, "POST", "/services/script/reload", {}))
            finally:
                end_request(context)
        self.assertEqual(factory.sessions, 0)
        self.assertEqual(factory.requests, [])

    def test_harness_failure_is_not_followed_and_raises(self):
        factory = CountingSessionFactory(status=302, body="")
        with patched_harness_session(factory):
            with self.assertRaises(HomeAssistantApiError) as caught:
                asyncio.run(harness_request(
                    harness_settings(), "POST", "/services/automation/reload", {}
                ))
        self.assertEqual(caught.exception.details["status"], 302)
        self.assertEqual(len(factory.requests), 1)
        self.assertIs(factory.requests[0]["allow_redirects"], False)

    def test_harness_transport_errors_raise_the_runtime_typed_errors(self):
        class FailingSession:
            def __init__(self, failure, **_kwargs):
                self.failure = failure

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            def request(self, *_args, **_kwargs):
                raise self.failure

        for failure, expected in (
            (asyncio.TimeoutError(), HomeAssistantTimeoutError),
            (harness_routes.aiohttp.ClientConnectionError(), HomeAssistantUnavailableError),
        ):
            with self.subTest(failure=type(failure).__name__):
                session = functools.partial(FailingSession, failure)
                with patch.object(harness_routes.aiohttp, "ClientSession", new=session):
                    with self.assertRaises(expected) as caught:
                        asyncio.run(harness_request(
                            harness_settings(), "POST", "/services/script/reload", {}
                        ))
                self.assertEqual(caught.exception.details, {
                    "method": "POST", "endpoint_category": "services",
                })


class AddOnPackagingTests(unittest.TestCase):
    """The add-on image contains no harness route and cannot import one."""

    def copied_build_files(self):
        dockerfile = (BETA_DIR / "Dockerfile").read_text(encoding="utf-8")
        sources = []
        for line in dockerfile.splitlines():
            words = line.split()
            if not words or words[0] != "COPY" or any(w.startswith("--from") for w in words):
                continue
            sources.extend(words[1:-1])
        self.assertIn("ha_mcp_engineering", sources)
        files = []
        for source in sources:
            path = BETA_DIR / source
            self.assertTrue(path.exists(), source)
            candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
            files.extend(
                item for item in candidates
                if item.is_file() and "__pycache__" not in item.parts
                and item.suffix not in {".pyc", ".pyo", ".pyd"}
            )
        return files

    def test_add_on_build_contains_no_harness_route_templates(self):
        files = self.copied_build_files()
        self.assertIn(PACKAGE_DIR / "clients" / "paths.py", files)
        forbidden = [t.encode() for t in HARNESS_ROUTE_TEMPLATES]
        forbidden += [b"HARNESS_ROUTE_TEMPLATES", b"harness_routes", b"harness_request"]
        findings = [
            f"{item.relative_to(ROOT).as_posix()}: {needle.decode()}"
            for item in files
            for needle in forbidden
            if needle in item.read_bytes()
        ]
        self.assertEqual(findings, [])

    def test_image_copy_cannot_import_or_request_a_harness_route(self):
        script = (
            "import importlib.util, sys\n"
            "sys.path[:0] = [sys.argv[1]]\n"
            "from ha_mcp_engineering.clients import paths\n"
            "assert importlib.util.find_spec('harness_routes') is None\n"
            "assert importlib.util.find_spec('scripts') is None\n"
            "assert not hasattr(paths, 'HARNESS_ROUTE_TEMPLATES')\n"
            "for template in sys.argv[2:]:\n"
            "    try:\n"
            "        paths.rest_path(template)\n"
            "    except paths.UnsafeRequestPathError as error:\n"
            "        assert error.details['reason'] == 'unregistered_route'\n"
            "    else:\n"
            "        raise SystemExit('accepted ' + template)\n"
            "print('isolated')\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "app"
            shutil.copytree(PACKAGE_DIR, app / "ha_mcp_engineering",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            result = subprocess.run(
                [sys.executable, "-I", "-c", script, str(app), *sorted(HARNESS_ROUTE_TEMPLATES)],
                cwd=app, capture_output=True, text=True, timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip(), "isolated")


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
