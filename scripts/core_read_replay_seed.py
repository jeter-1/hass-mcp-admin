"""Seed one disposable Home Assistant Core for the read capture (HAMCP-371, S5/S6).

Runs inside the lane's holder container and talks only to ``127.0.0.1``.  It
onboards a Core the lane started itself, mints two disposable long-lived
tokens in it (one administrator, one ordinary user), creates one small object
for every delegated read and reads each one back.

Outputs:

- ``seed.json``: the values the calls module substitutes.  No secret.
- ``seed-report.json``: one row per step with its status and readback.  No
  secret.  A failed step carries a fixed token, never Core's text.
- standard output: one JSON line with the two tokens, for the host process to
  hold in memory.  The tokens are written nowhere else.

``seed.json`` is written only when every step succeeded.  ``--phase access``
runs onboarding and the two tokens only and writes no ``seed.json``.  Core's replies are
untrusted data: they are parsed as JSON and compared, never evaluated.

The host harness imports this module for its constants, so everything outside
``AiohttpCore`` is standard library only.
"""

import argparse
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import secrets
import sys

# The lane runs this file as ``python -I``, which leaves its own directory off
# ``sys.path``; harness_send sits next to it (HAMCP-363).
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from harness_send import harness_url  # noqa: E402

CORE_VERSION = "2026.11.0"
BASE = "http://127.0.0.1:8123"
CLIENT_ID = BASE + "/"
TIME_ZONE = "America/Chicago"
PREFIX = "hamcp371"
GROUP_NAME = "HAMCP371 groupe éü 雪"
STATISTICS_UNIQUE_ID = PREFIX + "_energy"
USER_GROUP = "system-users"
SEED_KEYS = (
    "automation_id", "automation_entity", "calendar_entity", "scene_id", "script_id",
    "template", "helper_entity", "statistics_entity", "todo_entity", "zone_id",
    "search_text", "window_start", "window_end", "window_start_offset_free",
    "window_end_offset_free")
# The whole configuration of the disposable Core.  An explicit integration
# list: nothing here needs a network route.  The group name is the non-ASCII
# text the YAML read must return unchanged.
CONFIGURATION_YAML = f"""homeassistant:
  name: HAMCP371 disposable
  time_zone: {TIME_ZONE}
  latitude: 0
  longitude: 0
  elevation: 0
  unit_system: metric
  country: US
http:
  server_host: 127.0.0.1
frontend:
config:
api:
websocket_api:
history:
logbook:
recorder:
system_health:
automation: !include automations.yaml
script: !include scripts.yaml
scene: !include scenes.yaml
input_boolean:
person:
zone:
sun:
conversation:
lovelace:
trace:
blueprint:
group:
  {PREFIX}_group:
    name: "{GROUP_NAME}"
    entities:
      - sun.sun
template:
  - sensor:
      - name: HAMCP371 energy
        unique_id: {STATISTICS_UNIQUE_ID}
        unit_of_measurement: kWh
        device_class: energy
        state_class: total_increasing
        state: "1"
"""
CONFIG_FILES = {
    "configuration.yaml": CONFIGURATION_YAML,
    "automations.yaml": "[]\n",
    "scripts.yaml": "{}\n",
    "scenes.yaml": "[]\n",
}
FLOW_ANSWERS = {"calendar_name": "HAMCP371 calendar", "import": "create_empty",
                "todo_list_name": "HAMCP371 todo"}
FLOW_STEPS = 4
# Core answers a configuration write before its reload of that domain has run
# (the reload is a task of its own), so a reloaded entity is polled for.
RELOAD_POLLS = 30


class SeedError(Exception):
    """A fixed token; Core's own text never enters it."""


class AiohttpCore:
    """The real client.  ``rest`` and ``ws`` are all a step may use."""

    def __init__(self, session, base=BASE):
        self.session, self.base = session, base
        self.socket, self.sequence = None, 0

    async def rest(self, method, path, *, token=None, body=None, form=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        async with self.session.request(method, harness_url(self.base, path), headers=headers,
                                        json=body, data=form) as response:
            raw = await response.read()
            try:
                return response.status, json.loads(raw)
            except ValueError:
                return response.status, raw.decode("utf-8", "replace")

    async def connect(self, token):
        socket = await self.session.ws_connect(harness_url(self.base, "/api/websocket"),
                                             max_msg_size=0)
        if (await socket.receive_json()).get("type") != "auth_required":
            raise SeedError("websocket_handshake_unexpected")
        await socket.send_json({"type": "auth", "access_token": token})
        if (await socket.receive_json()).get("type") != "auth_ok":
            raise SeedError("websocket_auth_refused")
        self.socket, self.sequence = socket, 0

    async def ws(self, kind, **fields):
        self.sequence += 1
        await self.socket.send_json({"id": self.sequence, "type": kind, **fields})
        while True:
            message = await self.socket.receive_json()
            if message.get("id") == self.sequence and message.get("type") == "result":
                if message.get("success") is not True:
                    raise SeedError("websocket_command_failed")
                return message.get("result")

    async def disconnect(self):
        if self.socket is not None:
            await self.socket.close()
            self.socket = None


def expect(condition, token):
    if not condition:
        raise SeedError(token)


async def ok(core, method, path, *, token, body=None, statuses=(200, 201)):
    status, reply = await core.rest(method, path, token=token, body=body)
    expect(status in statuses, "rest_call_refused")
    return reply


def registry_entity(entries, platform, unique_id=None):
    """The one entity id a platform registered, by unique id when one is given."""
    found = [entry["entity_id"] for entry in entries
             if entry.get("platform") == platform
             and (unique_id is None or str(entry.get("unique_id")) == str(unique_id))]
    expect(len(found) == 1, "seeded_entity_not_in_registry")
    return found[0]


async def reloaded_entity(ctx, platform, unique_id):
    """The registry entry a domain reload creates; a bounded wait, never a guess."""
    for _ in range(RELOAD_POLLS):
        try:
            return registry_entity(await entities(ctx), platform, unique_id)
        except SeedError:
            await ctx["sleep"](1)
    raise SeedError("seeded_entity_not_in_registry")


def window(local_now):
    """An explicit-offset window around Core's local time, and its offset-free twin."""
    moment = datetime.fromisoformat(local_now)
    expect(moment.utcoffset() is not None, "core_local_time_has_no_offset")
    start = (moment - timedelta(hours=2)).replace(microsecond=0)
    end = (moment + timedelta(hours=1)).replace(microsecond=0)
    return {"window_start": start.isoformat(), "window_end": end.isoformat(),
            "window_start_offset_free": start.replace(tzinfo=None).isoformat(),
            "window_end_offset_free": end.replace(tzinfo=None).isoformat()}, moment


async def authorization_code_token(core, code):
    status, reply = await core.rest("POST", "/auth/token", form={
        "grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID})
    expect(status == 200 and isinstance(reply, dict) and reply.get("access_token"),
           "token_exchange_refused")
    return reply["access_token"]


async def onboard(ctx):
    core, password = ctx["core"], secrets.token_urlsafe(24)
    for _ in range(ctx["boot_polls"]):
        try:
            status, steps = await core.rest("GET", "/api/onboarding")
        except OSError:
            status, steps = None, None
        if status == 200:
            break
        await ctx["sleep"](1)
    else:
        raise SeedError("core_onboarding_not_reachable")
    expect(isinstance(steps, list) and not any(step.get("done") for step in steps),
           "core_is_not_fresh")
    status, reply = await core.rest("POST", "/api/onboarding/users", body={
        "client_id": CLIENT_ID, "name": "HAMCP371 owner", "username": PREFIX + "_owner",
        "password": password, "language": "en"})
    expect(status == 200 and isinstance(reply, dict) and reply.get("auth_code"),
           "onboarding_user_refused")
    ctx["access"] = await authorization_code_token(core, reply["auth_code"])
    for path, body in (("core_config", {}),
                       ("integration", {"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID}),
                       ("analytics", {})):
        await ok(core, "POST", "/api/onboarding/" + path, token=ctx["access"], body=body)
    return {"steps_listed": len(steps)}


async def ready(ctx):
    for _ in range(ctx["boot_polls"]):
        status, config = await ctx["core"].rest("GET", "/api/config", token=ctx["access"])
        if status == 200 and isinstance(config, dict) and config.get("state") == "RUNNING":
            break
        await ctx["sleep"](1)
    else:
        raise SeedError("core_not_running")
    expect(config.get("version") == CORE_VERSION, "core_version_mismatch")
    expect(config.get("time_zone") == TIME_ZONE, "core_time_zone_mismatch")
    wanted = {"history", "recorder", "input_boolean", "zone", "person", "group", "template",
              "automation", "script", "scene", "lovelace", "trace", "blueprint", "sun"}
    missing = sorted(wanted - set(config.get("components") or []))
    expect(not missing, "core_component_not_loaded")
    status, local_now = await ctx["core"].rest(
        "POST", "/api/template", token=ctx["access"], body={"template": "{{ now().isoformat() }}"})
    expect(status == 200 and isinstance(local_now, str), "core_local_time_unreadable")
    values, ctx["now"] = window(local_now.strip())
    ctx["seed"].update(values)
    return {"version": config["version"], "time_zone": config["time_zone"],
            "components": len(config["components"]), "utc_offset": str(ctx["now"].utcoffset())}


async def administrator_token(ctx):
    await ctx["core"].connect(ctx["access"])
    user = await ctx["core"].ws("auth/current_user")
    expect(user.get("is_admin") is True and user.get("is_owner") is True,
           "first_user_is_not_administrator")
    ctx["tokens"]["administrator"] = await ctx["core"].ws(
        "auth/long_lived_access_token", client_name="HAMCP371 lane administrator", lifespan=1)
    expect(isinstance(ctx["tokens"]["administrator"], str), "token_not_issued")
    return {"is_admin": True, "is_owner": True, "kind": "long-lived access token of the "
            "disposable Core's owner user", "lifespan_days": 1}


async def ordinary_user_token(ctx):
    core, password = ctx["core"], secrets.token_urlsafe(24)
    created = await core.ws("config/auth/create", name="HAMCP371 ordinary user",
                            group_ids=[USER_GROUP], local_only=False)
    user_id = created["user"]["id"]
    await core.ws("config/auth_provider/homeassistant/create", user_id=user_id,
                  username=PREFIX + "_user", password=password)
    status, flow = await core.rest("POST", "/auth/login_flow", body={
        "client_id": CLIENT_ID, "handler": ["homeassistant", None], "redirect_uri": CLIENT_ID})
    expect(status == 200 and isinstance(flow, dict) and flow.get("flow_id"),
           "login_flow_refused")
    status, done = await core.rest("POST", "/auth/login_flow/" + flow["flow_id"], body={
        "client_id": CLIENT_ID, "username": PREFIX + "_user", "password": password})
    expect(status == 200 and isinstance(done, dict) and done.get("type") == "create_entry",
           "login_flow_refused")
    access = await authorization_code_token(core, done["result"])
    second = ctx["second_client"]()
    await second.connect(access)
    try:
        user = await second.ws("auth/current_user")
        expect(user.get("id") == user_id and user.get("is_admin") is False
               and user.get("is_owner") is False, "ordinary_user_is_administrator")
        ctx["tokens"]["ordinary_user"] = await second.ws(
            "auth/long_lived_access_token", client_name="HAMCP371 lane ordinary user",
            lifespan=1)
    finally:
        await second.disconnect()
    expect(isinstance(ctx["tokens"]["ordinary_user"], str), "token_not_issued")
    return {"is_admin": False, "is_owner": False, "group_ids": [USER_GROUP],
            "lifespan_days": 1}


async def registries(ctx):
    core = ctx["core"]
    floor = await core.ws("config/floor_registry/create", name="HAMCP371 floor", level=1)
    area = await core.ws("config/area_registry/create", name="HAMCP371 area",
                         floor_id=floor["floor_id"])
    label = await core.ws("config/label_registry/create", name="HAMCP371 label")
    category = await core.ws("config/category_registry/create", scope="automation",
                             name="HAMCP371 category")
    ctx.update(area_id=area["area_id"], label_id=label["label_id"],
               category_id=category["category_id"])
    return {"floors": len(await core.ws("config/floor_registry/list")),
            "areas": len(await core.ws("config/area_registry/list")),
            "labels": len(await core.ws("config/label_registry/list")),
            "categories": len(await core.ws("config/category_registry/list",
                                            scope="automation"))}


async def entities(ctx):
    return await ctx["core"].ws("config/entity_registry/list")


async def helper(ctx):
    created = await ctx["core"].ws("input_boolean/create", name="HAMCP371 helper")
    entity = registry_entity(await entities(ctx), "input_boolean", created["id"])
    ctx["seed"].update(helper_entity=entity, search_text="HAMCP371",
                       template="{{ states('" + entity + "') }}")
    return {"entity": entity}


async def configured_objects(ctx):
    core, token, target = ctx["core"], ctx["access"], ctx["seed"]["helper_entity"]
    toggle = [{"action": "input_boolean.toggle", "target": {"entity_id": target}}]
    objects = {
        "automation": {"alias": "HAMCP371 automation", "description": "disposable seed",
                       "triggers": [], "conditions": [], "actions": toggle, "mode": "single"},
        "script": {"alias": "HAMCP371 script", "sequence": toggle, "mode": "single"},
        "scene": {"name": "HAMCP371 scene", "entities": {target: "on"}},
    }
    readback = {}
    for domain, body in objects.items():
        object_id = f"{PREFIX}_{domain}"
        await ok(core, "POST", f"/api/config/{domain}/config/{object_id}", token=token,
                 body=body)
        stored = await ok(core, "GET", f"/api/config/{domain}/config/{object_id}", token=token)
        expect(isinstance(stored, dict), "configured_object_not_readable")
        ctx["seed"][domain + "_id"] = object_id
        readback[domain] = sorted(stored)
    ctx["seed"]["automation_entity"] = await reloaded_entity(
        ctx, "automation", ctx["seed"]["automation_id"])
    return {"stored_keys": readback, "automation_entity": ctx["seed"]["automation_entity"]}


async def dashboard_resource(ctx):
    await ctx["core"].ws("lovelace/resources/create", res_type="module",
                         url="/local/hamcp371-resource.js")
    return {"resources": len(await ctx["core"].ws("lovelace/resources"))}


async def flow_entry(ctx, handler):
    """Start one config flow and answer its forms from the fixed answer table."""
    core, token = ctx["core"], ctx["access"]
    step = await ok(core, "POST", "/api/config/config_entries/flow", token=token,
                    body={"handler": handler, "show_advanced_options": False})
    for _ in range(FLOW_STEPS):
        expect(isinstance(step, dict), "config_flow_reply_unreadable")
        if step.get("type") == "create_entry":
            return
        expect(step.get("type") == "form" and step.get("flow_id"), "config_flow_not_a_form")
        names = [field.get("name") for field in step.get("data_schema") or []]
        expect(all(name in FLOW_ANSWERS for name in names), "config_flow_field_not_reviewed")
        step = await ok(core, "POST", "/api/config/config_entries/flow/" + step["flow_id"],
                        token=token, body={name: FLOW_ANSWERS[name] for name in names})
    raise SeedError("config_flow_did_not_finish")


async def calendar(ctx):
    await flow_entry(ctx, "local_calendar")
    entity = registry_entity(await entities(ctx), "local_calendar")
    begin = (ctx["now"] + timedelta(minutes=10)).replace(microsecond=0)
    await ctx["core"].ws("calendar/event/create", entity_id=entity, event={
        "summary": "HAMCP371 event", "dtstart": begin.isoformat(),
        "dtend": (begin + timedelta(minutes=30)).isoformat()})
    ctx["seed"]["calendar_entity"] = entity
    return {"entity": entity, "events_created": 1}


async def todo(ctx):
    await flow_entry(ctx, "local_todo")
    entity = registry_entity(await entities(ctx), "local_todo")
    await ok(ctx["core"], "POST", "/api/services/todo/add_item", token=ctx["access"],
             body={"entity_id": entity, "item": "HAMCP371 item"})
    ctx["seed"]["todo_entity"] = entity
    return {"entity": entity, "items_added": 1}


async def zone_and_person(ctx):
    core = ctx["core"]
    zone = await core.ws("zone/create", name="HAMCP371 zone", latitude=1.0, longitude=1.0,
                         radius=100, icon="mdi:flask", passive=False)
    await core.ws("person/create", name="HAMCP371 person", device_trackers=[])
    ctx["seed"]["zone_id"] = zone["id"]
    return {"zones": len(await core.ws("zone/list")),
            "persons": len((await core.ws("person/list"))["storage"])}


async def metadata(ctx):
    core, seed = ctx["core"], ctx["seed"]
    await core.ws("config/entity_registry/update", entity_id=seed["helper_entity"],
                  area_id=ctx["area_id"], labels=[ctx["label_id"]])
    await core.ws("config/entity_registry/update", entity_id=seed["automation_entity"],
                  categories={"automation": ctx["category_id"]})
    await core.ws("homeassistant/expose_entity", assistants=["conversation"],
                  entity_ids=[seed["helper_entity"]], should_expose=True)
    entry = await core.ws("config/entity_registry/get", entity_id=seed["helper_entity"])
    expect(entry.get("area_id") == ctx["area_id"] and entry.get("labels") == [ctx["label_id"]],
           "entity_metadata_not_stored")
    return {"area_set": True, "labels": 1, "category_set": True, "exposed_to": ["conversation"]}


async def activity(ctx):
    """One automation run (a trace) and state changes (history) inside the window."""
    core, token, seed = ctx["core"], ctx["access"], ctx["seed"]
    await ok(core, "POST", "/api/services/automation/trigger", token=token,
             body={"entity_id": seed["automation_entity"]})
    for _ in range(2):
        await ctx["sleep"](1)
        await ok(core, "POST", "/api/services/input_boolean/toggle", token=token,
                 body={"entity_id": seed["helper_entity"]})
    await ctx["sleep"](2)
    traces = await core.ws("trace/list", domain="automation", item_id=seed["automation_id"])
    expect(len(traces) >= 1, "automation_trace_not_recorded")
    return {"automation_runs": 1, "helper_toggles": 2, "traces": len(traces)}


async def statistics(ctx):
    core = ctx["core"]
    entity = registry_entity(await entities(ctx), "template", STATISTICS_UNIQUE_ID)
    hour = (ctx["now"] - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    await core.ws("recorder/import_statistics", metadata={
        "has_mean": False, "has_sum": True, "name": None, "source": "recorder",
        "statistic_id": entity, "unit_of_measurement": "kWh"},
        stats=[{"start": hour.isoformat(), "sum": 1.0, "state": 1.0}])
    ctx["seed"]["statistics_entity"] = entity
    return {"entity": entity, "rows_imported": 1}


async def readback(ctx):
    core = ctx["core"]
    states = await ok(core, "GET", "/api/states", token=ctx["access"])
    present = {state.get("entity_id") for state in states}
    wanted = [ctx["seed"][key] for key in ("automation_entity", "calendar_entity",
                                           "helper_entity", "statistics_entity", "todo_entity")]
    expect(all(entity in present for entity in wanted), "seeded_entity_has_no_state")
    group = [state for state in states if state.get("entity_id") == f"group.{PREFIX}_group"]
    expect(len(group) == 1 and group[0]["attributes"].get("friendly_name") == GROUP_NAME,
           "non_ascii_group_name_not_loaded")
    entries = await ok(core, "GET", "/api/config/config_entries/entry", token=ctx["access"])
    return {"states": len(states), "entity_registry": len(await entities(ctx)),
            "devices": len(await core.ws("config/device_registry/list")),
            "config_entry_domains": sorted({entry.get("domain") for entry in entries}),
            "non_ascii_group_name_loaded": True}


# name, whether the remaining steps depend on it.
STEPS = (("onboard", onboard, True), ("ready", ready, True),
         ("administrator_token", administrator_token, True),
         ("ordinary_user_token", ordinary_user_token, False),
         ("registries", registries, True), ("helper", helper, True),
         ("configured_objects", configured_objects, True),
         ("dashboard_resource", dashboard_resource, False), ("calendar", calendar, False),
         ("todo", todo, False), ("zone_and_person", zone_and_person, False),
         ("metadata", metadata, False), ("activity", activity, False),
         ("statistics", statistics, False), ("readback", readback, False))


# ``access`` (N31, 2026-11-15): onboarding and the two tokens only, so the
# review probes meet a Core with no seeded object.  It writes no seed file.
ACCESS_STEPS = ("onboard", "ready", "administrator_token", "ordinary_user_token")
PHASES = ("all", "access")


async def seed(core, *, second_client, sleep=asyncio.sleep, boot_polls=240, phase="all"):
    """Run every step of ``phase``.  Returns ``(report, seed values or None, tokens)``."""
    if phase not in PHASES:
        raise SeedError("unknown_phase")
    steps = STEPS if phase == "all" else tuple(item for item in STEPS
                                               if item[0] in ACCESS_STEPS)
    ctx = {"core": core, "second_client": second_client, "sleep": sleep,
           "boot_polls": boot_polls, "seed": {}, "tokens": {}}
    rows, stopped = [], False
    for name, step, needed in steps:
        if stopped:
            rows.append({"step": name, "status": "not_run"})
            continue
        try:
            rows.append({"step": name, "status": "ok", "readback": await step(ctx)})
        except SeedError as exc:
            rows.append({"step": name, "status": "failed", "failure": str(exc)})
            stopped = needed
        except Exception as exc:  # noqa: BLE001 - the type name only, never Core's text.
            rows.append({"step": name, "status": "failed", "failure": "unexpected_error",
                         "failure_type": type(exc).__name__})
            stopped = needed
    complete = (all(row["status"] == "ok" for row in rows)
                and (phase == "access" or set(ctx["seed"]) == set(SEED_KEYS)))
    report = {"seed_format_version": 1, "core_version": CORE_VERSION, "phase": phase,
              "steps": rows,
              "all_steps_ok": complete,
              "tokens_issued": sorted(ctx["tokens"]),
              "token_kind": "long-lived access tokens minted in this disposable Core; "
                            "held in memory by the host, written to no file"}
    values = ({key: ctx["seed"][key] for key in SEED_KEYS}
              if complete and phase == "all" else None)
    return report, values, ctx["tokens"]


async def run(out_dir, phase="all"):
    import aiohttp  # The holder image carries it; the host never reaches this line.

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        core = AiohttpCore(session)
        try:
            report, values, tokens = await seed(core, second_client=lambda: AiohttpCore(session),
                                                phase=phase)
        finally:
            await core.disconnect()
    write(out_dir, report, values)
    return report, tokens


def write(out_dir, report, values):
    out_dir = Path(out_dir)
    (out_dir / "seed-report.json").write_text(
        json.dumps(report, indent=1, sort_keys=True, ensure_ascii=True) + "\n", encoding="ascii")
    if values is not None:
        (out_dir / "seed.json").write_text(
            json.dumps(values, indent=1, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="ascii")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--phase", choices=PHASES, default="all")
    args = parser.parse_args(argv)
    report, tokens = asyncio.run(run(args.out, args.phase))
    # The only place the tokens appear: this process's standard output.
    sys.stdout.write(json.dumps({"all_steps_ok": report["all_steps_ok"], "tokens": tokens},
                                sort_keys=True) + "\n")
    return 0 if report["all_steps_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
