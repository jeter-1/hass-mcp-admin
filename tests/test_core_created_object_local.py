"""Offline boundaries of the two created-object G4 lanes (HAMCP-329, HAMCP-298).

No Docker, network, Home Assistant or production key. The lane scenarios run
here against the real service and F3 runtime over the synthetic Core store, so
the shipped lane code is exercised before any container starts; the lane
runner's orchestration runs with every host command replaced.
"""
import asyncio
from copy import deepcopy
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_created_object_local as lane
import created_object_contract_acceptance as acceptance
from core_release_evidence import groups, manifest, passing_subset, read_json

import tests.hamcp329_298_fixtures as fixtures
from ha_mcp_engineering.clients import HomeAssistantRestClient
from ha_mcp_engineering.errors import HomeAssistantApiError, HomeAssistantUnavailableError

paths = fixtures.paths
SOURCE = {"commit": "a" * 40, "runtime_tree": "b" * 40}
ABSENT = dict.fromkeys(("scenes", "automations", "helpers", "registry_entries"), True)
API_URL, WEBSOCKET_URL = "http://127.0.0.1:18123/api", "ws://127.0.0.1:18123/api/websocket"
# The lane's own members, as ``acceptance.run`` declares them.
MEMBERS = ("input_boolean.hamcp_g4_lamp", "input_boolean.hamcp_g4_strip")
SCENE_ID, AUTOMATION_ID = "hamcp_g4_scene_0a1b2c3d", "hamcp_g4_consumer_0a1b2c3d"
# Every method and route the lane's own client may send (HAMCP-200).
LANE_REQUESTS = frozenset({
    ("GET", "/states/{}"), ("GET", "/config/scene/config/{}"), ("GET", "/config/automation/config/{}"),
    ("POST", "/config/automation/config/{}"), ("DELETE", "/config/automation/config/{}")})


def configured(api_url=API_URL, websocket_url=WEBSOCKET_URL):
    return SimpleNamespace(api_url=api_url, websocket_url=websocket_url, ha_token="synthetic",
                           ha_timeout_seconds=5)


class Contracts(unittest.TestCase):
    def test_each_lane_proves_exactly_its_declared_group(self):
        self.assertEqual(set(lane.LANES), set(acceptance.LANES))
        for group, (capability, fingerprint, name) in lane.LANES.items():
            with self.subTest(group=group):
                reference = acceptance.reference(group)
                self.assertEqual(groups()[group], [capability])
                self.assertEqual(acceptance.LANES[group]["capability"], capability)
                self.assertEqual(acceptance.LANES[group]["fingerprint"], fingerprint)
                self.assertEqual((reference["capability_id"], reference["contract_fingerprint"]),
                                 (capability, fingerprint))
                self.assertEqual(lane.compiled_reference(group), reference)
                self.assertIsNotNone(lane.host.LANE.fullmatch(name))
                # A passing lane admits its own profile and nothing else.
                self.assertEqual(passing_subset([reference], {group: True}), [reference])
                self.assertEqual(passing_subset([reference], {"general": True}), [])
        self.assertEqual(len({names for names in map(lane.resource_names, lane.LANES)}), 2)
        self.assertEqual({fixtures.SCENE_PROFILE, fixtures.HELPER_PROFILE},
                         {capability for capability, _, _ in lane.LANES.values()})

    def test_the_predecessor_route_probe_is_a_complete_create_request(self):
        """The route derives its exact target from the whole request, before acquiring."""
        from ha_mcp_engineering.ha_core_readmission import routes

        probe = acceptance.scene_probe("hamcp_g4_withheld_route", "input_boolean.hamcp_g4_lamp")
        self.assertEqual(
            routes.static_tool_requirements(acceptance.SCENE_TOOL, probe),
            (lane.LANES["scene_create_delete"][0],),
        )
        self.assertEqual(
            routes.static_tool_target(acceptance.SCENE_TOOL, probe),
            {"target_type": "scene_configuration", "target_id": "hamcp_g4_withheld_route"},
        )
        with self.assertRaises(routes.StaticToolTargetRefused):
            routes.static_tool_target(
                acceptance.SCENE_TOOL, {"action": "create", "config_id": probe["config_id"]}
            )

    def test_a_changed_compiled_fingerprint_refuses(self):
        changed = {**acceptance.LANES["scene_create_delete"], "fingerprint": "sha256:" + "0" * 64}
        with patch.dict(acceptance.LANES, {"scene_create_delete": changed}):
            with self.assertRaises(ValueError):
                acceptance.reference("scene_create_delete")

    def test_lane_containers_are_counted_by_host_admission(self):
        self.assertIn(lane.LABEL, lane.resources.LANE_LABELS)
        self.assertEqual(lane.NETWORK_POSTURE,
                         {"driver": "bridge", "host_bind_address": "127.0.0.1", "egress_allowed": True})

    def test_committed_source_record_covers_both_profiles(self):
        record = read_json(lane.SOURCE_HASHES)
        provenance = read_json(ROOT / "tests/fixtures/core_2026_11_0_lane_provenance.json")
        self.assertEqual((record["version"], record["commit"], record["tree"], record["archive_sha256"]),
                         (lane.VERSION, provenance["source_commit"], provenance["source_tree"],
                          provenance["source_archive_sha256"]))
        for capability, _, _ in lane.LANES.values():
            paths = manifest()["profiles"][capability]
            self.assertTrue(paths and set(paths) <= set(record["hashes"]) and set(paths) <= set(manifest()["paths"]))

    def test_request_kinds_never_carry_ids_and_classify_writes(self):
        kind = acceptance.rest_kind
        self.assertEqual(kind("POST", "/config/scene/config/private_id"), acceptance.SCENE_POST)
        self.assertEqual(kind("DELETE", SimpleNamespace(template="/config/scene/config/{}")), acceptance.SCENE_DELETE)
        self.assertEqual(kind("GET", "/states/light.private"), "GET /states/{}")
        writes = [acceptance.SCENE_POST, acceptance.SCENE_DELETE, acceptance.AUTOMATION_POST,
                  "input_boolean/create", "input_number/delete", "config/entity_registry/update",
                  "config/entity_registry/remove", "call_service:scene.turn_on", "execute_script",
                  "POST /services/scene/reload"]
        reads = ["GET /config/scene/config/{}", "GET /states/{}", "input_boolean/list",
                 "config/entity_registry/list", "config/entity_registry/get", "POST /template",
                 "POST /config/core/check_config", "get_states"]
        self.assertTrue(all(map(acceptance.is_write, writes)))
        self.assertFalse(any(map(acceptance.is_write, reads)))
        log = acceptance.RequestLog()
        for name in (reads[0], writes[3], reads[1], writes[0]):
            log.record(name, {"type": name})
        self.assertEqual(log.writes_since(0), [writes[3], writes[0]])
        self.assertEqual(log.writes_since(2), [writes[0]])
        self.assertEqual(log.payloads_since(0, writes[3]), [{"type": writes[3]}])

    def test_disposable_configuration_is_closed(self):
        self.assertEqual(sorted(lane.INCLUDED), sorted(
            line.split("!include ")[1] for line in lane.CONFIG.splitlines() if "!include" in line))
        self.assertNotIn("secret", lane.CONFIG.lower())
        self.assertEqual(lane.PORTS, (18123,))

    def test_cached_image_requires_exact_repo_digest(self):
        def reply(digests):
            return subprocess.CompletedProcess([], 0, json.dumps([{"Id": "config", "RepoDigests": digests}]).encode())
        with patch.object(lane, "command", return_value=reply(["repo@sha256:other"])):
            with self.assertRaisesRegex(lane.Refusal, "cached_image_digest_mismatch"):
                lane.cached_image("repo:tag@sha256:exact")
        with patch.object(lane, "command", return_value=reply(["repo@sha256:exact"])):
            self.assertEqual(lane.cached_image("repo:tag@sha256:exact"), "config")
        core, fixture = lane.images()
        self.assertEqual(core, "ghcr.io/home-assistant/home-assistant:2026.11.0@"
                         + lane.REVIEWED_CORE_BINDINGS["2026.11.0"].image_index_digest)
        self.assertEqual(fixture, lane.host.FIXTURE_IMAGE)

    def test_inspection_error_is_not_absence_and_wrong_owner_is_refused(self):
        failed = subprocess.CompletedProcess([], 1, b"")
        with patch.object(lane, "command", side_effect=[failed, lane.Refusal("daemon_failed")]):
            with self.assertRaises(lane.Refusal):
                lane.inspect_owned("container", "owned", "id")
        data = {"Config": {"Labels": {lane.LABEL: "another-run"}}}
        with patch.object(lane, "command",
                          return_value=subprocess.CompletedProcess([], 0, json.dumps([data]).encode())):
            with self.assertRaisesRegex(lane.Refusal, "resource_ownership_mismatch"):
                lane.inspect_owned("container", "owned", "id")

    def test_port_mapping_requires_the_actual_loopback_binding(self):
        good = {"HostIp": "127.0.0.1", "HostPort": "18123"}

        def data(ports, network="owned"):
            return {"NetworkSettings": {"Networks": {network: {}}, "Ports": ports}}
        with patch.object(lane, "inspect_owned", return_value=data({"8123/tcp": [good]})):
            lane.verify_port_mapping("core", "owned", 8123, 18123, "run")
        invalid = [None, data({}), data({"8123/tcp": None}), data({"8123/tcp": [good, good]}),
                   data({"8123/tcp": [good], "9999/tcp": [good]}), data({"8123/tcp": [good]}, "unowned"),
                   *(data({"8123/tcp": [{"HostIp": ip, "HostPort": "18123"}]}) for ip in ("0.0.0.0", "::", ""))]
        for value in invalid:
            with self.subTest(value=value), patch.object(lane, "inspect_owned", return_value=value):
                with self.assertRaises(lane.Refusal):
                    lane.verify_port_mapping("core", "owned", 8123, 18123, "run")


class SourceBinding(unittest.TestCase):
    """The lane binds manifest, committed hashes and the exact archive together."""

    GROUP = "scene_create_delete"
    FILES = {"homeassistant/components/scene/__init__.py": b"scene", "homeassistant/components/config/scene.py": b"view"}

    def bound(self, root, *, hashes=None, paths=None, archive=None, group=None, **changes):
        capability = lane.LANES[self.GROUP][0]
        files = self.FILES if archive is None else archive
        record = {"version": lane.VERSION, "commit": "c" * 40, "tree": "d" * 40, "archive_sha256": "e" * 64,
                  "hashes": {path: hashlib.sha256(data).hexdigest() for path, data in self.FILES.items()}}
        record["hashes"].update(hashes or {})
        record.update(changes)
        (root / "hashes.json").write_text(json.dumps(record))
        (root / "manifest.json").write_text(json.dumps(
            {"profiles": {capability: list(self.FILES) if paths is None else paths}}))
        (root / "groups.json").write_text(json.dumps({self.GROUP: [capability] if group is None else group}))
        with tarfile.open(root / "core.tar.gz", "w:gz") as tar:
            for path, data in files.items():
                info = tarfile.TarInfo("core-" + "c" * 40 + "/" + path)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        provenance = {"source_commit": "c" * 40, "source_tree": "d" * 40, "source_archive_sha256": "e" * 64}
        with patch.object(lane, "SOURCE_HASHES", root / "hashes.json"), \
                patch.object(lane, "MANIFEST", root / "manifest.json"), patch.object(lane, "GROUPS", root / "groups.json"):
            return lane.source_manifest(self.GROUP, root / "core.tar.gz", provenance)

    def test_exact_archive_matches_and_every_divergence_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(self.bound(root)["profile_paths"], 2)
            path = next(iter(self.FILES))
            for reason, changes in (
                    ("created_object_manifest_missing", {"paths": []}),
                    ("created_object_manifest_missing", {"group": ["core.other"]}),
                    ("created_object_source_provenance_mismatch", {"commit": "f" * 40}),
                    ("created_object_source_provenance_mismatch", {"archive_sha256": "f" * 64}),
                    ("created_object_source_provenance_mismatch", {"version": "2026.10.0"}),
                    ("created_object_manifest_mismatch", {"paths": [path, path]}),
                    ("created_object_manifest_mismatch", {"paths": [path, "homeassistant/unrecorded.py"]}),
                    ("created_object_source_hash_mismatch", {"hashes": {path: "0" * 64}}),
                    ("created_object_source_hash_mismatch", {"archive": {path: b"scene"}}),
                    ("created_object_source_hash_mismatch", {"archive": {**self.FILES, path: b"changed"}})):
                with self.subTest(reason=reason, changes=sorted(changes)):
                    with self.assertRaisesRegex(lane.Refusal, reason):
                        self.bound(root, **changes)


class Transport:
    """Stands in for the guarded client: records what reaches it, answers as told."""

    def __init__(self, answer=None):
        self.sent, self.answer = [], answer

    async def request(self, method, path, body=None, raw=False, expected_statuses=frozenset()):
        self.sent.append((method, path, body, expected_statuses))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def lane_client(answer=None):
    raw = acceptance.LaneRaw(configured())
    raw.client = Transport(answer)
    return raw


class LaneClient(unittest.IsolatedAsyncioTestCase):
    """The lane's own client (HAMCP-200): a guarded client, bound routes, lane fixtures only."""

    def routes(self):
        return (acceptance.scene_route(SCENE_ID), acceptance.automation_route(AUTOMATION_ID),
                acceptance.state_route(MEMBERS[0]))

    def test_only_one_exact_loopback_core_is_accepted_for_rest_and_websocket(self):
        for host in ("127.0.0.1:18123", "localhost:8123", "[::1]:8123", "127.0.0.1"):
            with self.subTest(host=host):
                settings = configured("http://" + host + "/api", "ws://" + host + "/api/websocket")
                raw = acceptance.LaneRaw(settings)
                # A separate guarded Engineering client, and the WebSocket of the same Core.
                self.assertIs(type(raw.client), HomeAssistantRestClient)
                self.assertIs(raw.client.settings, settings)
                self.assertEqual(raw.url, "ws://" + host + "/api/websocket")
        refused = [
            # REST anywhere but loopback.
            ("http://192.0.2.10:8123/api", "ws://192.0.2.10:8123/api/websocket"),
            ("http://homeassistant.local:8123/api", "ws://homeassistant.local:8123/api/websocket"),
            ("http://127.0.0.1.example.com/api", "ws://127.0.0.1.example.com/api/websocket"),
            ("http://127.0.0.1:18123@example.com/api", "ws://127.0.0.1:18123@example.com/api/websocket"),
            ("http://user@127.0.0.1:18123/api", "ws://user@127.0.0.1:18123/api/websocket"),
            ("https://127.0.0.1:18123/api", WEBSOCKET_URL),
            # A base that is not exactly the API root would re-root every route.
            *[(API_URL + suffix, WEBSOCKET_URL) for suffix in (
                "?", "#", "/", "?x=1", "#x", "/services/homeassistant/restart?", "/../api", "/x")],
            ("http://127.0.0.1:18123", WEBSOCKET_URL), ("http://127.0.0.1:18123/", WEBSOCKET_URL),
            # The WebSocket must be the same loopback Core.
            *[(API_URL, url) for url in (
                "ws://192.0.2.10:8123/api/websocket", "ws://homeassistant.local:18123/api/websocket",
                "ws://127.0.0.1:18124/api/websocket", "ws://localhost:18123/api/websocket",
                "wss://127.0.0.1:18123/api/websocket", "ws://127.0.0.1:18123/api/websocket?x=1",
                "ws://127.0.0.1:18123/api/websocket#x", "ws://127.0.0.1:18123/api/websocket/",
                "ws://127.0.0.1:18123@example.com/api/websocket", "ws://x", "")],
        ]
        for api_url, websocket_url in refused:
            with self.subTest(api_url=api_url, websocket_url=websocket_url):
                with patch.object(acceptance.RawWebSocket, "__init__") as built, self.assertRaises(ValueError):
                    acceptance.LaneRaw(configured(api_url, websocket_url))
                built.assert_not_called()

    async def test_only_the_lane_method_and_route_pairs_are_sent(self):
        self.assertEqual(acceptance.LANE_REQUESTS, LANE_REQUESTS)
        self.assertLessEqual({template for _method, template in LANE_REQUESTS}, paths.ROUTE_TEMPLATES)
        sent = set()
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "get", "delete", "", None):
            for route in self.routes():
                raw = lane_client({"id": "x"})
                with self.subTest(method=method, template=route.template):
                    if (method, route.template) not in LANE_REQUESTS:
                        with self.assertRaises(ValueError):
                            await raw.status(method, route, {"alias": "x"})
                        self.assertEqual(raw.client.sent, [])
                        continue
                    self.assertEqual(await raw.status(method, route, {"alias": "x"}), (200, {"id": "x"}))
                    # Exactly one request, on the bound route itself, with 404 the only expected failure.
                    self.assertEqual(raw.client.sent, [(method, route, {"alias": "x"}, frozenset({404}))])
                    self.assertIs(raw.client.sent[0][1], route)
                    self.assertTrue(paths.is_route(route))
                    sent.add((method, route.template))
        self.assertEqual(sent, LANE_REQUESTS)

    async def test_only_lane_owned_identities_are_bound_or_sent(self):
        self.assertEqual([(str(route), route.template, route.values, route.kinds) for route in self.routes()], [
            ("/config/scene/config/" + SCENE_ID, "/config/scene/config/{}", (SCENE_ID,), ("identifier",)),
            ("/config/automation/config/" + AUTOMATION_ID, "/config/automation/config/{}", (AUTOMATION_ID,),
             ("identifier",)),
            ("/states/" + MEMBERS[0], "/states/{}", (MEMBERS[0],), ("entity_id",))])
        foreign_ids = ("users_own_scene", "hamcp_g4_", "HAMCP_G4_X", "hamcp_g4_x/../y", "hamcp_g4_x?y", "hamcp_g4_x#y",
                       "hamcp_g4_x\n", "hamcp_g4_" + "a" * 49, "xhamcp_g4_x", "hamcp_g4_x y", "", None, 7,
                       b"hamcp_g4_x")
        foreign_entities = ("light.kitchen", "input_boolean.users_real_lamp", "scene.users_own_scene",
                            "automation.hamcp_g4_x", "script.hamcp_g4_x", "input_select.hamcp_g4_x",
                            "input_boolean.hamcp_g4_", "input_boolean.hamcp_g4_x/../y", "hamcp_g4_x",
                            "input_boolean.hamcp_g4_x\n", "scene.hamcp_g4_" + "a" * 73, "", None)
        for binder, values in ((acceptance.scene_route, foreign_ids), (acceptance.automation_route, foreign_ids),
                               (acceptance.state_route, foreign_entities)):
            for value in values:
                with self.subTest(binder=binder.__name__, value=value), self.assertRaises(ValueError):
                    binder(value)
        # A path the binders did not issue is refused before any request, whatever built it.
        unbound = [
            "/config/scene/config/" + SCENE_ID, "/states/" + MEMBERS[0], "/config/automation/config/" + AUTOMATION_ID,
            "/services/scene/turn_on", "/config/scene/config/hamcp_g4_x/../y", "", None,
            paths.rest_path("/config/scene/config/{}", "users_own_scene"),
            paths.rest_path("/config/automation/config/{}", "users_own_automation"),
            paths.rest_path("/states/{}", "light.kitchen", kinds=("entity_id",)),
            paths.rest_path("/states/{}", "input_boolean.users_real_lamp", kinds=("entity_id",)),
            paths.rest_path("/states/{}", "automation.hamcp_g4_x", kinds=("entity_id",)),
            paths.rest_path("/states/{}", MEMBERS[0], kinds=("entity_id",), query={"x": "y"}),
            paths.rest_path("/config/script/config/{}", "hamcp_g4_x"),
            paths.rest_path("/services/notify/{}", "hamcp_g4_x"), paths.rest_path("/states"),
            paths.rest_path("/config/core/check_config"),
        ]
        for path in unbound:
            for method in ("GET", "POST", "DELETE"):
                raw = lane_client({"id": "x"})
                with self.subTest(path=path, method=method), self.assertRaises(ValueError):
                    await raw.status(method, path)
                self.assertEqual(raw.client.sent, [])

    async def test_a_scene_config_delete_or_write_is_unreachable_from_the_lane_client(self):
        route = acceptance.scene_route(SCENE_ID)
        for method in ("DELETE", "POST", "PUT"):
            raw = lane_client()
            with self.subTest(method=method), self.assertRaises(ValueError):
                await raw.status(method, route, {"name": "x", "entities": {}})
            self.assertEqual(raw.client.sent, [])
        # Behind that check stands the shipped transport guard: the lane's
        # guarded client holds no delete permit, so nothing is sent.
        raw = acceptance.LaneRaw(configured())
        for method, reason in (("DELETE", "scene_delete_permit_required"), ("PUT", "route_method_not_allowed")):
            with self.subTest(method=method), patch("aiohttp.ClientSession") as session:
                with self.assertRaises(paths.UnsafeRequestPathError) as refused:
                    await raw.client.request(method, acceptance.scene_route(SCENE_ID))
                self.assertEqual(refused.exception.details, {"reason": reason})
                session.assert_not_called()
        # The lane neither installs the permit nor keeps a REST session of its own.
        source = inspect.getsource(acceptance)
        for name in ("scene_delete_permit", "aiohttp", "ClientSession", "RAW_ROUTE", "api_url +"):
            self.assertNotIn(name, source)
        # Its one REST send is the guarded client's, on the route ``status`` checked.
        self.assertEqual(inspect.getsource(acceptance.LaneRaw).count(".request("), 1)
        self.assertEqual(source.count("self.client.request("), 1)
        self.assertIn("payload = await self.client.request(method, route, body, expected_statuses=frozenset({404}))",
                      inspect.getsource(acceptance.LaneRaw.status))

    async def test_the_guarded_client_refuses_a_base_that_would_re_root_a_route(self):
        # What the lane client's exact-origin check stands in front of.
        for api_url in (API_URL + "?", API_URL + "#", API_URL + "/services/homeassistant/restart?",
                        "http://user@127.0.0.1:18123/api"):
            with self.subTest(api_url=api_url), patch("aiohttp.ClientSession") as session:
                with self.assertRaises(paths.UnsafeRequestPathError) as refused:
                    await HomeAssistantRestClient(configured(api_url)).request(
                        "GET", acceptance.scene_route(SCENE_ID))
                self.assertEqual(refused.exception.details, {"reason": "route_mismatch"})
                session.assert_not_called()

    async def test_statuses_are_what_the_guarded_client_classified(self):
        route = acceptance.automation_route(AUTOMATION_ID)

        def failure(status):
            return HomeAssistantApiError(details={"status": status, "method": "DELETE",
                                                  "provider_response_received": True})
        for answer, expected in (({"alias": "x"}, (200, {"alias": "x"})), (None, (200, None)),
                                 (fixtures.ExpectedHttpStatus(404), (404, None)),
                                 (failure(400), (400, None)), (failure(500), (500, None)),
                                 # A redirect is a status; the guarded client never follows one.
                                 (failure(302), (302, None))):
            with self.subTest(answer=answer):
                self.assertEqual(await lane_client(answer).status("DELETE", route), expected)
        # No received status is no answer: the failure is not turned into one.
        for answer in (HomeAssistantApiError(), HomeAssistantApiError(details={"status": "404"}),
                       HomeAssistantApiError(details={"status": True}), HomeAssistantUnavailableError(),
                       paths.UnsafeRequestPathError("route_mismatch")):
            with self.subTest(answer=answer), self.assertRaises(type(answer)):
                await lane_client(answer).status("DELETE", route)


class GuardedStore:
    """The guarded client's contract over the synthetic store: the shipped route checks, then Core's answer."""

    def __init__(self, core):
        self.core, self.sent = core, []

    async def request(self, method, path, body=None, raw=False, expected_statuses=frozenset()):
        route = paths.require_route(path, method)
        paths.request_url(API_URL, route, method)
        self.sent.append((method, route.template))
        status, payload = self.answer(method, route.template, route.values[0])
        if status in expected_statuses:
            return fixtures.ExpectedHttpStatus(status)
        if status >= 300:
            raise HomeAssistantApiError(details={"status": status, "method": method})
        return payload

    def answer(self, method, template, key):
        rest, websocket = self.core.rest, self.core.websocket
        if template == acceptance.STATE_ROUTE:
            assert method == "GET", method
            return (200, {"state": "on"}) if key in rest.states else (404, None)
        assert template in (acceptance.SCENE_ROUTE, acceptance.AUTOMATION_ROUTE), template
        store = rest.scenes if template == acceptance.SCENE_ROUTE else rest.configs["automation"]
        if method == "GET":
            return (200, deepcopy(store[key])) if key in store else (404, None)
        assert (method, template) == ("DELETE", acceptance.AUTOMATION_ROUTE), (method, template)
        if key not in store:
            return 400, None
        if not self.core.stuck:
            del store[key]
            for entity_id, entry in list(websocket.entries.items()):
                if entry.get("platform") == "automation" and entry.get("unique_id") == key:
                    del websocket.entries[entity_id]
                    rest.states.discard(entity_id)
        return 200, {"result": "ok"}


class OfflineCore(acceptance.LaneRaw):
    """The shipped lane client; only its two transports are answered from the synthetic store."""

    def __init__(self, rest, websocket):
        super().__init__(configured())
        self.rest, self.websocket = rest, websocket
        self.stuck = False
        self.client = GuardedStore(self)

    async def ok(self, payload):
        kind = payload["type"]
        if kind == acceptance.REGISTRY_LIST:
            return [deepcopy(entry) for entry in self.websocket.entries.values()]
        domain, _, verb = kind.partition("/")
        assert verb == "list" and domain in acceptance.HELPER_DOMAINS, kind
        return [deepcopy(item) for item in self.websocket.helpers[domain].values()]

    async def call(self, payload):
        domain = payload["type"].partition("/")[0]
        if payload[domain + "_id"] not in self.websocket.helpers[domain]:
            return {"success": False, "error": {"code": "not_found"}}
        if not self.stuck:
            await self.websocket._answer(dict(payload))
        return {"success": True, "result": None}


class Scenarios(fixtures.CreatedObjectServiceTestCase):
    """Every lane scenario, unchanged, over the real service and F3 runtime."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.log = acceptance.RequestLog()
        # One ordered log over the only two clients any gateway holds.
        self.rest.request = acceptance.ObservedRest(SimpleNamespace(request=self.rest.request), self.log).request
        self.ws.command = acceptance.ObservedWebSocket(SimpleNamespace(command=self.ws.command), self.log).command
        self.raw = OfflineCore(self.rest, self.ws)
        for member in MEMBERS:
            self.config_rest.add_member(member)

        async def add_automation(target_id):
            self.add("automation", target_id)
            return deepcopy(self.rest.configs["automation"][target_id])
        self.lane_context = acceptance.Context(
            service=self.service, log=self.log, reader=acceptance.Reader(self.raw.status, self.raw.ok),
            scene_plan=lambda request: self.service.create_scene_configuration_plan(dict(request)),
            configuration_plan=lambda operations: self.service.create_configuration_plan(
                title="Synthetic create", description="Synthetic governed create", operations=list(operations)),
            add_automation=add_automation, members=MEMBERS, suffix="0a1b2c3d", settle=0)

    async def receipt(self, group):
        """A receipt shaped as ``acceptance.run`` builds it, from the offline scenarios."""
        c, profile = self.lane_context, lane.LANES[group][0]
        admission = {"profile_available": False, "control_capability_acquired": True}
        self.core.withdraw(profile)
        if group == "scene_create_delete":
            admission.update(await acceptance.withheld_scene_create(c, c.scene_plan), route_acquired=False)
        else:
            admission.update(await acceptance.withheld_helper_undo(c))
        self.core.admit(profile)
        scenarios = await acceptance.run_scenarios(group, c)
        absence = await acceptance.cleanup_fixtures(self.raw, c.owned, attempts=2, delay=0)
        return {"result": "PASS", "model": acceptance.MODEL, "lane": group, "core": acceptance.CORE_VERSION,
                "profile": acceptance.reference(group), "engineering_source": SOURCE,
                "authority_transition": [32, 33], "production_authority": False,
                "predecessor_admission": admission,
                "engineering_write_kinds": sorted(set(self.log.writes_since(0))),
                "authority_leases_released": True, "scenarios": scenarios, "fixture_absence": absence}

    def verify(self, group, receipt):
        with patch.object(acceptance, "engineering_source", return_value=SOURCE):
            return acceptance.verify_result(group, acceptance.load_receipt(acceptance.receipt_bytes(receipt)))

    def refuses(self, group, receipt, *path, value):
        changed = deepcopy(receipt)
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with self.subTest(path=path, value=value), self.assertRaises((AssertionError, KeyError, TypeError)):
            self.verify(group, changed)

    async def test_scene_lane_forward_undo_recreate_and_refusals(self):
        group = "scene_create_delete"
        receipt = await self.receipt(group)
        self.assertEqual(self.verify(group, receipt), {
            "lane": group, "profile_fingerprint": lane.LANES[group][1], "kinds": ["scene"],
            "scenarios": sorted(acceptance.LANES[group]["scenarios"]), "result": "PASS"})
        before, scenarios = receipt["predecessor_admission"], receipt["scenarios"]
        self.assertEqual((before["reason_code"], before["plan_created"], before["ha_requests"], before["undo_writes"]),
                         (acceptance.PENDING, False, 0, []))
        self.assertEqual(receipt["engineering_write_kinds"], sorted([acceptance.SCENE_DELETE, acceptance.SCENE_POST]))
        self.assertEqual(list(scenarios), list(acceptance.LANES[group]["scenarios"]))
        self.assertEqual([scenarios[name]["writes"] for name in scenarios],
                         [[acceptance.SCENE_POST], [acceptance.SCENE_DELETE], [acceptance.SCENE_POST], [],
                          [acceptance.SCENE_DELETE]])
        self.assertEqual(scenarios["scene_existing_id_refused"]["reason_code"], "scene_configuration_target_exists")
        # The recreate left Core through its own governed undo, read back by the lane's client.
        final = scenarios["scene_recreate_undo"]
        self.assertEqual((final["rollback_status"], final["task_state"], final["children"]),
                         ("rollback_plan_created", "succeeded_verified", [["delete_1", "succeeded_verified", 1]]))
        self.assertEqual(final["readback"], {"config_status": 404, "registry_entity_id": None, "state_status": 404,
                                             "entity_id": scenarios["scene_recreate"]["readback"]["entity_id"]})
        self.assertEqual(scenarios["scene_recreate"]["identity_outcome"], "captured")
        self.assertEqual(receipt["fixture_absence"], ABSENT)
        self.assertEqual((self.rest.scenes, self.lane_context.owned["scenes"]), ({}, [SCENE_ID]))
        # Both scene deletes were Engineering's, through its gateway; the lane's own client only read.
        self.assertEqual(self.log.writes_since(0).count(acceptance.SCENE_DELETE), 2)
        self.assertEqual({pair for pair in self.raw.client.sent},
                         {("GET", acceptance.SCENE_ROUTE), ("GET", acceptance.STATE_ROUTE)})
        # The members a scene names were never driven.
        self.assertEqual(self.service_calls(), [])
        for path, value in (
                (("result",), "WITHHELD"), (("production_authority",), True), (("authority_transition",), [32, 34]),
                (("profile", "contract_fingerprint"), lane.LANES["created_helper_delete"][1]),
                (("engineering_source", "runtime_tree"), "c" * 40),
                (("engineering_write_kinds",), [acceptance.SCENE_POST, "call_service:scene.turn_on"]),
                (("fixture_absence", "registry_entries"), False), (("authority_leases_released",), False),
                (("predecessor_admission", "reason_code"), "other"), (("predecessor_admission", "plan_created"), True),
                (("predecessor_admission", "ha_requests"), 1), (("predecessor_admission", "route_acquired"), True),
                (("predecessor_admission", "undo_writes"), [acceptance.SCENE_POST]),
                (("scenarios", "scene_create", "task_state"), "succeeded_unverified"),
                (("scenarios", "scene_create", "children"), [["scene_create_1", "succeeded_verified", 2]]),
                (("scenarios", "scene_create", "members_unchanged"), False),
                (("scenarios", "scene_create", "identity_outcome"), "unavailable"),
                (("scenarios", "scene_undo", "writes"), []),
                (("scenarios", "scene_undo", "readback", "config_status"), 200),
                (("scenarios", "scene_undo", "readback_fingerprint"), "sha256:short"),
                (("scenarios", "scene_recreate", "readback_fingerprint"), "sha256:" + "0" * 64),
                (("scenarios", "scene_recreate", "identity_outcome"), "unavailable"),
                (("scenarios", "scene_recreate_undo", "writes"), []),
                (("scenarios", "scene_recreate_undo", "writes"), [acceptance.SCENE_POST]),
                (("scenarios", "scene_recreate_undo", "task_state"), "succeeded_unverified"),
                (("scenarios", "scene_recreate_undo", "rollback_status"), "refused"),
                (("scenarios", "scene_recreate_undo", "children"), []),
                (("scenarios", "scene_recreate_undo", "readback", "config_status"), 200),
                (("scenarios", "scene_recreate_undo", "readback", "state_status"), 200),
                (("scenarios", "scene_recreate_undo", "readback", "registry_entity_id"), "scene.other"),
                (("scenarios", "scene_recreate_undo", "readback", "entity_id"), "scene.other"),
                (("scenarios", "scene_recreate_undo", "readback_fingerprint"), "sha256:short"),
                (("fixture_absence", "scenes"), False),
                (("scenarios", "scene_existing_id_refused", "plan_created"), True),
                (("scenarios", "scene_existing_id_refused", "reason_code"), "")):
            self.refuses(group, receipt, *path, value=value)
        without = deepcopy(receipt)
        del without["scenarios"]["scene_recreate_undo"]
        with self.assertRaises(AssertionError):
            self.verify(group, without)
        with self.assertRaises(AssertionError):
            self.verify("created_helper_delete", receipt)

    async def test_a_scene_left_behind_is_reported_and_is_not_a_pass(self):
        """Nothing but the governed undo removes a scene, so a lane that skips it cannot pass."""
        group = "scene_create_delete"
        spec = {**acceptance.LANES[group], "scenarios": acceptance.LANES[group]["scenarios"][:-1]}
        with patch.dict(acceptance.LANES, {group: spec}):
            receipt = await self.receipt(group)
            self.assertNotIn("scene_recreate_undo", receipt["scenarios"])
            # Clean-up read the scene as present and said so; it removed nothing.
            self.assertEqual(receipt["fixture_absence"], {**ABSENT, "scenes": False, "registry_entries": False})
            self.assertEqual(list(self.rest.scenes), [SCENE_ID])
            self.assertEqual({method for method, template in self.raw.client.sent
                              if template == acceptance.SCENE_ROUTE}, {"GET"})
            self.assertEqual(self.log.writes_since(0).count(acceptance.SCENE_DELETE), 1)
        # ``acceptance.run`` ends in this strict check, so no PASS receipt exists for such a run:
        # not as it stands, and not with the absence claimed either.
        for candidate in (receipt, {**receipt, "fixture_absence": ABSENT}):
            with self.assertRaises(AssertionError):
                self.verify(group, candidate)

    async def test_helper_lane_forward_undo_and_mixed_reverse_plan(self):
        group = "created_helper_delete"
        receipt = await self.receipt(group)
        self.assertEqual(self.verify(group, receipt), {
            "lane": group, "profile_fingerprint": lane.LANES[group][1], "kinds": ["input_boolean", "input_number"],
            "scenarios": sorted(acceptance.LANES[group]["scenarios"]), "result": "PASS"})
        before, scenarios = receipt["predecessor_admission"], receipt["scenarios"]
        self.assertEqual(before, {
            "profile_available": False, "control_capability_acquired": True,
            "reason_code": "no_eligible_created_object", "provider_dispatch_occurred": False,
            "operations_excluded": ["created_object_identity_not_captured"],
            "forward_task_state": "succeeded_verified", "identity_outcome": "capability_unadmitted",
            "identity_captured": False, "helper_retained": True, "undo_writes": []})
        self.assertEqual(set(receipt["engineering_write_kinds"]), set(acceptance.LANES[group]["writes"]))
        self.assertEqual(scenarios["mixed_plan_undo"]["writes"], [acceptance.AUTOMATION_POST, "input_boolean/delete"])
        self.assertEqual(scenarios["mixed_plan_undo"]["reverse_order"], [["automation", "update"], ["helper", "delete"]])
        # The retained predecessor helper is the lane's to remove, with its raw client.
        self.assertEqual(receipt["fixture_absence"], ABSENT)
        self.assertEqual(self.ws.helpers, {"input_boolean": {}, "input_number": {}})
        self.assertEqual(self.rest.configs["automation"], {})
        self.assertEqual(len(self.lane_context.owned["helpers"]), 4)
        for path, value in (
                (("predecessor_admission", "forward_task_state"), "failed"),
                (("predecessor_admission", "helper_retained"), False),
                (("predecessor_admission", "identity_captured"), True),
                (("predecessor_admission", "reason_code"), acceptance.PENDING),
                (("predecessor_admission", "operations_excluded"), []),
                (("predecessor_admission", "undo_writes"), ["input_boolean/delete"]),
                (("engineering_write_kinds",), ["input_boolean/delete", "input_boolean/update"]),
                (("fixture_absence", "helpers"), False),
                (("scenarios", "helper_undo_input_number", "delete_argument_is_captured_identity"), False),
                (("scenarios", "helper_undo_input_number", "readback", "stored"), True),
                (("scenarios", "helper_undo_input_boolean", "rollback_status"), "refused"),
                (("scenarios", "helper_create_input_boolean", "writes"), ["input_number/create"]),
                (("scenarios", "mixed_plan_undo", "reverse_order"), [["helper", "delete"], ["automation", "update"]]),
                (("scenarios", "mixed_plan_undo", "writes"), ["input_boolean/delete", acceptance.AUTOMATION_POST]),
                (("scenarios", "mixed_plan_undo", "consumer_restored"), False)):
            self.refuses(group, receipt, *path, value=value)
        without = deepcopy(receipt)
        del without["scenarios"]["helper_undo_input_number"]
        with self.assertRaises(AssertionError):
            self.verify(group, without)

    async def test_a_lane_step_that_does_not_hold_fails_the_lane(self):
        c = self.lane_context
        # A second, unexpected write during an apply.
        plan = await c.scene_plan({"action": "create", "config_id": "hamcp_g4_scene_x", "name": "HAMCP G4 x",
                                   "entities": {MEMBERS[0]: {"state": "on"}}})
        with self.assertRaises(AssertionError):
            await acceptance.applied(c, plan, [acceptance.SCENE_POST, acceptance.SCENE_POST])
        # An undo that Core ignores never reads back absent.
        await acceptance.scene_create(c)
        self.rest.ignore_deletes = True
        with self.assertRaises(AssertionError):
            await acceptance.scene_undo(c)
        # A refusal that does not happen is a failure, not a pass.
        with self.assertRaises(AssertionError):
            await acceptance.refusal(asyncio.sleep(0))

    async def test_an_ignored_undo_of_the_recreate_fails_the_lane_and_leaves_the_scene_reported(self):
        c = self.lane_context
        for step in (acceptance.scene_create, acceptance.scene_undo, acceptance.scene_recreate,
                     acceptance.scene_existing_id_refused):
            await step(c)
        self.rest.ignore_deletes = True
        # The lane's last step: Engineering sent its delete, Core kept the scene, the read-back says so.
        with self.assertRaises(AssertionError):
            await acceptance.scene_recreate_undo(c)
        self.assertEqual(list(self.rest.scenes), [SCENE_ID])
        self.assertEqual(await acceptance.cleanup_fixtures(self.raw, c.owned, attempts=2, delay=0),
                         {**ABSENT, "scenes": False, "registry_entries": False})
        self.assertNotIn(("DELETE", acceptance.SCENE_ROUTE), self.raw.client.sent)

    async def test_cleanup_removes_no_scene_and_reports_what_is_left(self):
        self.rest.add_scene("hamcp_g4_scene_left", {"name": "HAMCP G4 left", "entities": {}})
        self.ws.add_helper("input_boolean", {"name": "HAMCP G4 left"})
        self.add("automation", "hamcp_g4_consumer_left")
        owned = {"scenes": ["hamcp_g4_scene_left", "hamcp_g4_scene_never"],
                 "automations": ["hamcp_g4_consumer_left", "hamcp_g4_consumer_never"],
                 "helpers": [("input_boolean", "hamcp_g4_left")]}
        self.raw.stuck = True
        self.assertEqual(await acceptance.cleanup_fixtures(self.raw, owned, attempts=2, delay=0),
                         dict.fromkeys(ABSENT, False))
        self.raw.stuck = False
        # The automation and the helper are the lane client's to remove; the scene is not.
        self.assertEqual(await acceptance.cleanup_fixtures(self.raw, owned, attempts=2, delay=0),
                         {"scenes": False, "automations": True, "helpers": True, "registry_entries": False})
        self.assertEqual(list(self.rest.scenes), ["hamcp_g4_scene_left"])
        self.assertEqual(set(self.raw.client.sent), {
            ("GET", acceptance.SCENE_ROUTE), ("GET", acceptance.AUTOMATION_ROUTE),
            ("DELETE", acceptance.AUTOMATION_ROUTE)})
        self.assertLessEqual(set(self.raw.client.sent), acceptance.LANE_REQUESTS)
        # Once it is gone by another hand (the governed undo, in a lane), clean-up reads it absent.
        del self.rest.scenes["hamcp_g4_scene_left"]
        for entity_id, entry in list(self.ws.entries.items()):
            if entry.get("unique_id") == "hamcp_g4_scene_left":
                del self.ws.entries[entity_id]
        self.assertEqual(await acceptance.cleanup_fixtures(self.raw, owned, attempts=2, delay=0), ABSENT)
        self.assertNotIn(("DELETE", acceptance.SCENE_ROUTE), self.raw.client.sent)


class Orchestration(unittest.TestCase):
    GROUP = "created_helper_delete"

    def run_lane(self, *, exercise_error=None, cleanup_error=False, dirty=False, existing=False,
                 admission_error=None, memory_args=(), network_override=None, ports_override=None,
                 profile=None, group=None, environment=None):
        group = group or self.GROUP
        core, network = lane.resource_names(group)
        reference = {"capability_id": lane.LANES[group][0], "contract_fingerprint": lane.LANES[group][1]}
        calls, resources = [], {}

        def command(*args, **kwargs):
            calls.append(args)
            stdout, code = b"", 0
            if args[:2] == ("git", "status"):
                stdout = b" M source.py" if dirty else b""
            elif args[:2] == ("git", "rev-parse"):
                stdout = b"a" * 40
            elif "inspect" in args:
                code = 0 if existing else 1
            elif args[:3] == ("docker", "network", "create"):
                resources[network] = True
            elif args[:2] == ("docker", "run"):
                resources[core] = True
            elif args[:2] == ("docker", "rm") or args[:3] == ("docker", "network", "rm"):
                resources.pop(args[-1], None)
            return subprocess.CompletedProcess(args, code, stdout)

        def inspect(kind, name, run_id):
            if name not in resources:
                return None
            if kind == "network":
                return network_override if network_override is not None else {"Driver": "bridge", "Internal": False}
            ports = {"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18123"}]}
            return {"NetworkSettings": {"Networks": {network: {}},
                                        "Ports": ports if ports_override is None else ports_override}}

        def remove(args, *_):
            if cleanup_error:
                raise lane.Refusal("cleanup_failed")
            shutil.rmtree(args.directory)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".artifacts").mkdir()
            out = root / ".artifacts" / "run"
            argv = ["--lane", group, "--mode", "local", "--pull", "never", "--journal", "journal",
                    "--source-archive", "archive", "--run-dir", str(out), *memory_args]
            passed = {"result": "PASS", "profile": reference if profile is None else profile}
            with patch.dict(os.environ, environment or {}, clear=True), patch.object(lane, "ROOT", root), \
                    patch.object(lane, "command", side_effect=command), \
                    patch.object(lane, "inputs", return_value=({}, reference, {"profile_paths": 1})), \
                    patch.object(lane, "images", return_value=("core@sha256:a", lane.host.FIXTURE_IMAGE)), \
                    patch.object(lane, "cached_image", return_value="config"), \
                    patch.object(lane.resources, "wait_for_admission", side_effect=admission_error,
                                 return_value={"other_heavy_jobs": 0}), \
                    patch.object(lane, "inspect_owned", side_effect=inspect), \
                    patch.object(lane.host, "local_remove", side_effect=remove), \
                    patch.object(lane, "require_free_ports"), patch.object(lane.signal, "signal"), \
                    patch.object(lane.diagnostics, "_raw_log", return_value=False), \
                    patch.object(lane, "exercise", new=AsyncMock(side_effect=exercise_error,
                                                                 return_value=passed)) as exercise, \
                    patch("sys.stdout", new_callable=io.StringIO):
                if dirty or existing or environment:
                    with self.assertRaises(lane.Refusal):
                        lane.main(argv)
                    self.assertFalse(out.exists())
                    self.assertFalse(any(c[:2] in (("docker", "run"), ("docker", "rm")) for c in calls))
                    return None
                code = lane.main(argv)
                if network_override is not None or ports_override is not None:
                    exercise.assert_not_called()  # Before onboarding, auth or any readiness check.
                elif not admission_error:
                    self.assertEqual(exercise.await_args.args[:2], (group, out / "engineering-state"))
                    # The disposable configuration existed, closed, while the lane ran.
                    self.assertEqual(os.environ["REAL_HA_URL"], "http://127.0.0.1:18123")
                    self.assertNotIn("REAL_HA_UPSTREAM_IMAGE", os.environ)
                    self.assertEqual(os.environ["HA_CONTRACT_CONTAINER"], core)
                result = json.loads((out / (group + ".json")).read_text())
                self.assertEqual(sorted(path.name for path in out.iterdir()),
                                 [*([core] if cleanup_error else []), group + ".json"])
                if admission_error:
                    self.assertEqual(code, 1)
                    self.assertFalse(result["execution_started"])
                    self.assertEqual(result["reason"], "host_admission_timeout")
                    self.assertTrue(result["cleanup"]["clean"])
                    self.assertFalse(resources)
                    self.assertFalse(any(c[:2] == ("docker", "run") for c in calls))
                    return None
            self.assertFalse(resources)
            self.assertEqual((result["group"], result["contract_fingerprint"], result["source_commit"]),
                             (group, lane.LANES[group][1], "a" * 40))
            for call in calls:
                if call[:3] == ("docker", "network", "create"):
                    self.assertEqual(call[call.index("--driver") + 1], "bridge")
                    self.assertNotIn("--internal", call)
                    self.assertTrue(call[call.index("--label") + 1].startswith(lane.LABEL + "="))
                if call[:2] == ("docker", "run"):
                    self.assertEqual(call[call.index("--pull") + 1], "never")
                    self.assertIn("127.0.0.1:18123:8123", call)
                    self.assertEqual(call[call.index("--memory") + 1], "3g" if memory_args else "2g")
                    self.assertEqual(call[call.index("--name") + 1], core)
                    self.assertTrue(call[call.index("--label") + 1].startswith(lane.LABEL + "="))
                self.assertNotIn("pull", call[1:2])
            return code, result

    def test_admission_timeout_creates_refusal_receipt_without_containers(self):
        self.run_lane(admission_error=lane.resources.Refusal("host_admission_timeout"))

    def test_explicit_memory_override(self):
        code, result = self.run_lane(memory_args=("--core-memory", "3g", "--fixture-memory", "256m"))
        self.assertEqual(code, 0)
        self.assertEqual(result["container_memory"], {"core": "3g", "fixture": "256m"})

    def test_each_lane_passes_only_with_cleanup_and_its_own_profile(self):
        for group in lane.LANES:
            with self.subTest(group=group):
                code, result = self.run_lane(group=group)
                self.assertEqual((code, result["result"]), (0, "PASS"))
                self.assertTrue(result["cleanup"]["clean"])
                self.assertEqual(result["network_posture"], lane.NETWORK_POSTURE)
                self.assertEqual(result["profile_reference"]["capability_id"], lane.LANES[group][0])
        other = {"capability_id": lane.LANES["scene_create_delete"][0],
                 "contract_fingerprint": lane.LANES["scene_create_delete"][1]}
        code, result = self.run_lane(profile=other)
        self.assertEqual((code, result["result"], result["failure_type"]), (1, "WITHHELD", "Refusal"))
        self.assertTrue(result["cleanup"]["clean"])

    def test_missing_or_wildcard_mapping_refuses_before_readiness_and_cleans(self):
        for ports in ({}, {"8123/tcp": None}, {"8123/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18123"}]}):
            with self.subTest(ports=ports):
                code, result = self.run_lane(ports_override=ports)
                self.assertEqual(code, 1)
                self.assertEqual((result["failure"]["stage"], result["failure"]["check"]),
                                 ("container_start", "port_mapping"))
                self.assertTrue(result["cleanup"]["clean"])

    def test_wrong_network_posture_refuses_before_readiness(self):
        for network in ({"Driver": "bridge", "Internal": True}, {"Driver": "host", "Internal": False}, {}):
            with self.subTest(network=network):
                code, result = self.run_lane(network_override=network)
                self.assertEqual(code, 1)
                self.assertEqual(result["failure"]["check"], "network_create")
                self.assertTrue(result["cleanup"]["clean"])

    def test_lane_failure_withholds_and_still_cleans(self):
        code, result = self.run_lane(exercise_error=AssertionError("synthetic-private-text"))
        self.assertEqual((code, result["result"]), (1, "WITHHELD"))
        self.assertTrue(result["cleanup"]["clean"])
        self.assertNotIn("synthetic-private-text", json.dumps(result))

    def test_cleanup_failure_overrides_pass(self):
        code, result = self.run_lane(cleanup_error=True)
        self.assertEqual((code, result["result"]), (1, "WITHHELD"))
        self.assertFalse(result["cleanup"]["clean"])

    def test_dirty_checkout_never_starts(self):
        self.run_lane(dirty=True)

    def test_prior_run_is_preserved(self):
        self.run_lane(existing=True)

    def test_hosted_or_promotion_context_never_starts(self):
        self.run_lane(environment={"GITHUB_ACTIONS": "true"})
        self.run_lane(environment={"CORE_EVIDENCE_CONTEXT": "promotion"})


if __name__ == "__main__":
    unittest.main()
