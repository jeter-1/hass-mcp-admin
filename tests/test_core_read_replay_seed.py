"""Offline tests of the disposable-Core seed step (HAMCP-371).

The Core here is a synthetic in-process object.  No container, network,
registry, image or Home Assistant is used; every value, including the two
"tokens", is synthetic.  The real client (``AiohttpCore``) is not exercised:
it is first proven by the lane's own selftest against a Core it starts.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_read_replay_calls as calls  # noqa: E402
import core_read_replay_seed as seed  # noqa: E402

OWNER_ACCESS = "synthetic-owner-access"
USER_ACCESS = "synthetic-user-access"
TOKENS = {OWNER_ACCESS: "synthetic-administrator-long-lived",
          USER_ACCESS: "synthetic-ordinary-long-lived"}
LOCAL_NOW = "2026-01-01T12:34:56.789012-06:00"


class World:
    """What the synthetic Core remembers, shared by every client object."""

    def __init__(self, **overrides):
        self.calls, self.entities, self.states = [], [], [
            {"entity_id": f"group.{seed.PREFIX}_group",
             "attributes": {"friendly_name": seed.GROUP_NAME}}]
        self.options = {"version": seed.CORE_VERSION, "ordinary_is_admin": False,
                        "refuse": (), "fresh": True, "flow_fields": None,
                        "reload_lag": 0, **overrides}
        self.pending = []
        self.register("template", seed.STATISTICS_UNIQUE_ID, "sensor.synthetic_energy")
        self.codes, self.flows, self.passwords = {}, {}, []

    def register(self, platform, unique_id, entity_id):
        self.entities.append({"platform": platform, "unique_id": unique_id,
                              "entity_id": entity_id})
        self.states.append({"entity_id": entity_id, "attributes": {}})


class FakeCore:
    def __init__(self, world):
        self.world, self.access = world, None

    async def rest(self, method, path, *, token=None, body=None, form=None):
        world = self.world
        world.calls.append((method, path))
        if path in world.options["refuse"]:
            return 500, "synthetic Core error text that must not be stored"
        if path == "/api/onboarding" and method == "GET":
            return 200, [{"step": "user", "done": not world.options["fresh"]}]
        if path == "/api/onboarding/users":
            world.passwords.append(body["password"])
            world.codes["owner-code"] = OWNER_ACCESS
            return 200, {"auth_code": "owner-code"}
        if path == "/auth/token":
            return 200, {"access_token": world.codes[form["code"]]}
        if path == "/auth/login_flow":
            return 200, {"flow_id": "login-1", "type": "form"}
        if path == "/auth/login_flow/login-1":
            world.passwords.append(body["password"])
            world.codes["user-code"] = USER_ACCESS
            return 200, {"type": "create_entry", "result": "user-code"}
        if token != OWNER_ACCESS:
            return 401, "unauthorized"
        if path.startswith("/api/onboarding/"):
            return 200, {}
        if path == "/api/config":
            return 200, {"state": "RUNNING", "version": world.options["version"],
                         "time_zone": seed.TIME_ZONE, "components": [
                             "history", "recorder", "input_boolean", "zone", "person",
                             "group", "template", "automation", "script", "scene",
                             "lovelace", "trace", "blueprint", "sun"]}
        if path == "/api/template":
            return 200, LOCAL_NOW
        if path.startswith("/api/config/automation/config/") and method == "POST":
            # Core reloads the domain after it has answered the write.
            world.pending.append([world.options["reload_lag"], (
                "automation", path.rsplit("/", 1)[1], "automation.synthetic")])
            return 200, {"result": "ok"}
        if "/config/" in path and path.startswith("/api/config/") and method == "POST" \
                and "config_entries" not in path:
            return 200, {"result": "ok"}
        if "/config/" in path and method == "GET" and "config_entries" not in path:
            return 200, {"alias": "synthetic"}
        if path == "/api/config/config_entries/flow":
            handler = body["handler"]
            fields = world.options["flow_fields"] or {
                "local_calendar": ["calendar_name", "import"],
                "local_todo": ["todo_list_name"]}[handler]
            world.flows[handler] = fields
            return 200, {"type": "form", "flow_id": handler,
                         "data_schema": [{"name": name} for name in fields]}
        if path.startswith("/api/config/config_entries/flow/"):
            handler = path.rsplit("/", 1)[1]
            assert sorted(body) == sorted(world.flows[handler])
            world.register(handler, "synthetic-" + handler, {
                "local_calendar": "calendar.synthetic", "local_todo": "todo.synthetic"}[handler])
            return 200, {"type": "create_entry"}
        if path == "/api/config/config_entries/entry":
            return 200, [{"domain": "sun"}, {"domain": "local_calendar"}]
        if path == "/api/states":
            return 200, list(world.states)
        if path.startswith("/api/services/"):
            return 200, []
        raise AssertionError("unexpected REST call " + path)

    async def connect(self, token):
        self.access = token

    async def disconnect(self):
        self.access = None

    async def ws(self, kind, **fields):
        world = self.world
        world.calls.append(("WS", kind))
        if kind in world.options["refuse"]:
            raise seed.SeedError("websocket_command_failed")
        owner = self.access == OWNER_ACCESS
        if kind == "auth/current_user":
            return {"id": "owner-id" if owner else "user-id",
                    "is_admin": owner or world.options["ordinary_is_admin"],
                    "is_owner": owner}
        if kind == "auth/long_lived_access_token":
            assert fields["lifespan"] == 1
            return TOKENS[self.access]
        assert owner, "an ordinary user was given an administrative command"
        if kind == "config/auth/create":
            assert fields["group_ids"] == [seed.USER_GROUP]
            return {"user": {"id": "user-id"}}
        if kind == "config/auth_provider/homeassistant/create":
            world.passwords.append(fields["password"])
            return None
        if kind.endswith("_registry/create"):
            name = kind.split("/")[1].replace("_registry", "")
            return {name + "_id": "synthetic-" + name}
        if kind.endswith("_registry/list") and "entity" not in kind:
            return [{}]
        if kind == "config/entity_registry/list":
            for waiting in list(world.pending):
                if waiting[0] <= 0:
                    world.register(*waiting[1])
                    world.pending.remove(waiting)
                waiting[0] -= 1
            return list(world.entities)
        if kind == "config/entity_registry/get":
            return {"area_id": "synthetic-area", "labels": ["synthetic-label"]}
        if kind == "input_boolean/create":
            world.register("input_boolean", "synthetic_helper", "input_boolean.synthetic")
            return {"id": "synthetic_helper"}
        if kind == "zone/create":
            return {"id": "synthetic_zone"}
        if kind == "person/list":
            return {"storage": [{}], "config": []}
        if kind in ("zone/list", "lovelace/resources", "trace/list"):
            return [{}]
        return None


def run_seed(world):
    async def no_sleep(seconds):
        world.calls.append(("SLEEP", seconds))
    return asyncio.run(seed.seed(FakeCore(world), second_client=lambda: FakeCore(world),
                                 sleep=no_sleep, boot_polls=2))


class SeedTests(unittest.TestCase):
    def test_a_full_seed_yields_every_value_the_calls_module_needs(self):
        world = World()
        report, values, tokens = run_seed(world)
        self.assertEqual([row["status"] for row in report["steps"]], ["ok"] * len(seed.STEPS))
        self.assertIs(report["all_steps_ok"], True)
        self.assertEqual(tuple(sorted(values)), tuple(sorted(seed.SEED_KEYS)))
        # Every case of the calls module plans from these values alone.
        planned = calls.plan("9.1.2", list(calls.GROUPS), dict(values))
        self.assertEqual(len(planned), len(calls.CASES))
        self.assertEqual(values["helper_entity"], "input_boolean.synthetic")
        self.assertEqual(values["automation_id"], "hamcp371_automation")
        self.assertEqual(values["statistics_entity"], "sensor.synthetic_energy")
        self.assertEqual(values["window_start"], "2026-01-01T10:34:56-06:00")
        self.assertEqual(values["window_end"], "2026-01-01T13:34:56-06:00")
        self.assertEqual(values["window_start_offset_free"], "2026-01-01T10:34:56")
        self.assertEqual(tokens, {"administrator": TOKENS[OWNER_ACCESS],
                                  "ordinary_user": TOKENS[USER_ACCESS]})
        self.assertEqual(report["tokens_issued"], ["administrator", "ordinary_user"])

    def test_no_secret_reaches_the_report_or_the_seed_file(self):
        world = World()
        report, values, tokens = run_seed(world)
        with tempfile.TemporaryDirectory() as scratch:
            seed.write(scratch, report, values)
            stored = "".join(path.read_text(encoding="ascii")
                             for path in Path(scratch).iterdir())
            names = sorted(path.name for path in Path(scratch).iterdir())
        self.assertEqual(names, ["seed-report.json", "seed.json"])
        secrets_seen = (list(tokens.values()) + world.passwords
                        + [OWNER_ACCESS, USER_ACCESS, "owner-code", "user-code"])
        self.assertEqual(len(world.passwords), 3)
        for secret in secrets_seen:
            self.assertNotIn(secret, stored)
        # Two generated passwords: the owner's sent once, the ordinary user's twice.
        self.assertEqual(len(set(world.passwords)), 2)
        self.assertTrue(all(len(password) >= 24 for password in world.passwords))

    def test_a_refused_optional_step_is_a_fixed_token_and_no_seed_file(self):
        world = World(refuse=("zone/create",))
        report, values, _ = run_seed(world)
        rows = {row["step"]: row for row in report["steps"]}
        self.assertEqual(rows["zone_and_person"],
                         {"step": "zone_and_person", "status": "failed",
                          "failure": "websocket_command_failed"})
        self.assertEqual(rows["statistics"]["status"], "ok")
        self.assertIs(report["all_steps_ok"], False)
        self.assertIsNone(values)
        with tempfile.TemporaryDirectory() as scratch:
            seed.write(scratch, report, values)
            self.assertEqual([path.name for path in Path(scratch).iterdir()],
                             ["seed-report.json"])

    def test_core_error_text_is_never_stored(self):
        world = World(refuse=("/api/services/todo/add_item",))
        report, values, _ = run_seed(world)
        self.assertIsNone(values)
        self.assertNotIn("synthetic Core error text", json.dumps(report))
        rows = {row["step"]: row for row in report["steps"]}
        self.assertEqual(rows["todo"]["failure"], "rest_call_refused")

    def test_a_failed_required_step_stops_the_seed(self):
        world = World(version="2026.10.4")
        report, values, tokens = run_seed(world)
        statuses = [row["status"] for row in report["steps"]]
        self.assertEqual(statuses[:2], ["ok", "failed"])
        self.assertEqual(set(statuses[2:]), {"not_run"})
        self.assertEqual(report["steps"][1]["failure"], "core_version_mismatch")
        self.assertIsNone(values)
        self.assertEqual(tokens, {})
        self.assertNotIn(("WS", "auth/long_lived_access_token"), world.calls)

    def test_a_core_that_was_already_onboarded_is_refused(self):
        report, values, _ = run_seed(World(fresh=False))
        self.assertEqual(report["steps"][0]["failure"], "core_is_not_fresh")
        self.assertIsNone(values)

    def test_an_ordinary_user_that_reads_back_as_administrator_issues_no_token(self):
        world = World(ordinary_is_admin=True)
        report, values, tokens = run_seed(world)
        rows = {row["step"]: row for row in report["steps"]}
        self.assertEqual(rows["ordinary_user_token"]["failure"],
                         "ordinary_user_is_administrator")
        self.assertNotIn("ordinary_user", tokens)
        self.assertIsNone(values)

    def test_an_automation_core_has_not_reloaded_yet_is_waited_for(self):
        world = World(reload_lag=3)
        report, values, _ = run_seed(world)
        self.assertIs(report["all_steps_ok"], True)
        self.assertEqual(values["automation_entity"], "automation.synthetic")
        rows = {row["step"]: row for row in report["steps"]}
        self.assertEqual(rows["configured_objects"]["readback"]["automation_entity"],
                         "automation.synthetic")
        first = world.calls.index(("POST", "/api/config/scene/config/hamcp371_scene"))
        last = world.calls.index(("WS", "lovelace/resources/create"))
        waited = [call for call in world.calls[first:last] if call[0] in ("WS", "SLEEP")]
        self.assertEqual(waited, [("WS", "config/entity_registry/list"), ("SLEEP", 1)] * 3
                         + [("WS", "config/entity_registry/list")])

    def test_an_automation_that_never_appears_stops_the_seed_after_a_bounded_wait(self):
        world = World(reload_lag=10 ** 6)
        report, values, _ = run_seed(world)
        rows = {row["step"]: row for row in report["steps"]}
        self.assertEqual(rows["configured_objects"],
                         {"step": "configured_objects", "status": "failed",
                          "failure": "seeded_entity_not_in_registry"})
        self.assertEqual(rows["dashboard_resource"], {"step": "dashboard_resource",
                                                      "status": "not_run"})
        self.assertIsNone(values)
        self.assertEqual(world.calls.count(("SLEEP", 1)), seed.RELOAD_POLLS)
        self.assertNotIn(("POST", "/api/services/automation/trigger"), world.calls)

    def test_a_config_flow_field_outside_the_answer_table_is_refused(self):
        world = World(flow_fields=["synthetic_unreviewed_field"])
        report, _, _ = run_seed(world)
        rows = {row["step"]: row for row in report["steps"]}
        self.assertEqual(rows["calendar"]["failure"], "config_flow_field_not_reviewed")
        self.assertEqual(rows["todo"]["failure"], "config_flow_field_not_reviewed")


class ConstantTests(unittest.TestCase):
    def test_the_configuration_needs_no_route_and_binds_loopback(self):
        text = seed.CONFIGURATION_YAML
        self.assertIn("server_host: 127.0.0.1", text)
        self.assertIn("time_zone: America/Chicago", text)
        self.assertIn(seed.GROUP_NAME, text)
        self.assertFalse(seed.GROUP_NAME.isascii())
        for word in ("default_config", "cloud", "mobile_app", "met:", "radio_browser",
                     "updater", "http://", "https://"):
            with self.subTest(word=word):
                self.assertNotIn(word, text)
        self.assertEqual(sorted(seed.CONFIG_FILES), [
            "automations.yaml", "configuration.yaml", "scenes.yaml", "scripts.yaml"])

    def test_the_window_needs_an_explicit_offset(self):
        with self.assertRaises(seed.SeedError) as raised:
            seed.window("2026-01-01T12:00:00")
        self.assertEqual(str(raised.exception), "core_local_time_has_no_offset")

    def test_the_seed_key_set_is_the_calls_module_reference_set(self):
        names = set()

        def walk(value):
            if isinstance(value, str) and value.startswith(calls.SEED):
                names.add(value[len(calls.SEED):])
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
        for case in calls.CASES:
            walk(case["arguments"])
        self.assertEqual(names, set(seed.SEED_KEYS))


if __name__ == "__main__":
    unittest.main()
