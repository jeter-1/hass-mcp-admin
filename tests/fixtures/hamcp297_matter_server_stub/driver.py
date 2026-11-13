"""HAMCP-297 spike driver: onboard disposable Core, add matter entry -> stub, exercise the three kinds."""

import asyncio
import json
import secrets
import sys
import time

import aiohttp

HA = "http://core:8123"
STUB = "http://stub:5580"
CLIENT_ID = HA + "/"
TARGET_KINDS = {
    ("select", "startup_on_off"),
    ("switch", "child_lock"),
    ("number", "sensitivity_level"),
}
RESULT = {"checks": [], "ok": True}


def check(name, ok, **detail):
    RESULT["checks"].append({"name": name, "ok": bool(ok), **detail})
    if not ok:
        RESULT["ok"] = False
    print(("PASS " if ok else "FAIL ") + name, json.dumps(detail, default=str)[:600], flush=True)


async def jreq(session, method, path, token=None, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with session.request(method, HA + path, headers=headers, **kw) as resp:
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"{method} {path} -> {resp.status} {text[:300]}")
        return json.loads(text) if text else None


async def onboard(session):
    for _ in range(240):
        try:
            async with session.get(HA + "/api/onboarding") as resp:
                if resp.status == 200:
                    break
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        await asyncio.sleep(1)
    else:
        raise RuntimeError("core not ready")
    steps = await jreq(session, "GET", "/api/onboarding")
    user = await jreq(session, "POST", "/api/onboarding/users", json={
        "client_id": CLIENT_ID, "name": "HAMCP-297 spike", "username": "spike_" + secrets.token_hex(4),
        "password": secrets.token_urlsafe(24), "language": "en"})
    tok = await jreq(session, "POST", "/auth/token", data={
        "grant_type": "authorization_code", "code": user["auth_code"], "client_id": CLIENT_ID})
    token = tok["access_token"]
    done = {s["step"]: s["done"] for s in steps}
    if not done.get("core_config"):
        await jreq(session, "POST", "/api/onboarding/core_config", token, json={})
    if not done.get("integration"):
        await jreq(session, "POST", "/api/onboarding/integration", token,
                   json={"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID})
    if not done.get("analytics"):
        await jreq(session, "POST", "/api/onboarding/analytics", token, json={})
    return token


class WS:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def call(self, **msg):
        self.n += 1
        msg["id"] = self.n
        await self.ws.send_json(msg)
        while True:
            reply = await self.ws.receive_json(timeout=60)
            if reply.get("id") == self.n and reply.get("type") == "result":
                return reply


async def stub_state(session):
    async with session.get(STUB + "/_stub/control") as resp:
        return await resp.json()


async def wait_state(ws, entity_id, expected, timeout=15):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        states = (await ws.call(type="get_states"))["result"]
        last = next((s for s in states if s["entity_id"] == entity_id), None)
        if last and str(last["state"]) == str(expected):
            return last
        await asyncio.sleep(0.5)
    return last


async def exercise(session, ws, entity_id, service_domain, service, data, expect_write, expect_state):
    before = len((await stub_state(session))["writes"])
    reply = await ws.call(type="call_service", domain=service_domain, service=service,
                          service_data=data, target={"entity_id": entity_id})
    check(f"service {service_domain}.{service} {entity_id} accepted", reply.get("success"), reply=reply.get("error"))
    state = await wait_state(ws, entity_id, expect_state)
    writes = (await stub_state(session))["writes"][before:]
    check(f"{entity_id} {service} exactly one write", len(writes) == 1, writes=writes)
    if writes:
        w = writes[0]
        check(f"{entity_id} {service} write matches", (w["node_id"], w["attribute_path"], w["value"]) == expect_write,
              got=[w["node_id"], w["attribute_path"], w["value"]], expected=list(expect_write))
    check(f"{entity_id} {service} state reflected", state and str(state["state"]) == str(expect_state),
          state=state and state["state"], expected=expect_state)
    return {"entity_id": entity_id, "service": f"{service_domain}.{service}", "data": data,
            "writes": writes, "state": state and state["state"]}


async def main():
    out = sys.argv[1]
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        token = await onboard(session)
        flow = await jreq(session, "POST", "/api/config/config_entries/flow", token, json={"handler": "matter"})
        check("matter flow starts at manual step", flow.get("step_id") == "manual", flow=flow.get("step_id"))
        done = await jreq(session, "POST", f"/api/config/config_entries/flow/{flow['flow_id']}", token,
                          json={"url": "ws://stub:5580/ws"})
        check("matter config entry created", done.get("type") == "create_entry", result=done.get("type"),
              errors=done.get("errors"))
        async with session.ws_connect(HA + "/api/websocket") as raw:
            await raw.receive_json()
            await raw.send_json({"type": "auth", "access_token": token})
            auth = await raw.receive_json()
            assert auth["type"] == "auth_ok", auth
            ws = WS(raw)
            entries = (await ws.call(type="config_entries/get", domain="matter"))["result"]
            for _ in range(60):
                entries = (await ws.call(type="config_entries/get", domain="matter"))["result"]
                if entries and entries[0]["state"] == "loaded":
                    break
                await asyncio.sleep(1)
            check("matter entry loaded", entries and entries[0]["state"] == "loaded", entries=entries)
            await asyncio.sleep(3)
            reg = (await ws.call(type="config/entity_registry/list"))["result"]
            matter = [e for e in reg if e["platform"] == "matter"]
            RESULT["matter_entity_count"] = len(matter)
            found = {}
            for e in matter:
                key = (e["entity_id"].split(".")[0], e.get("translation_key"))
                if key in TARGET_KINDS:
                    found.setdefault(key, []).append({k: e.get(k) for k in (
                        "entity_id", "platform", "translation_key", "entity_category", "unique_id",
                        "device_id", "disabled_by", "has_entity_name", "id")})
            RESULT["admitted_kind_entities"] = {f"{d}/{t}": v for (d, t), v in found.items()}
            for kind in sorted(TARGET_KINDS):
                ents = found.get(kind, [])
                check(f"kind {kind} present via matter", bool(ents) and all(
                    x["entity_category"] == "config" for x in ents), entities=[x["entity_id"] for x in ents])
            states = {s["entity_id"]: s for s in (await ws.call(type="get_states"))["result"]}
            interesting = [x["entity_id"] for v in found.values() for x in v]
            RESULT["initial_states"] = {e: {"state": states.get(e, {}).get("state"),
                                            "attributes": states.get(e, {}).get("attributes")} for e in interesting}
            ex = RESULT.setdefault("exercises", [])
            sel = "select.eve_energy_plug_power_on_behavior"
            sw = "switch.eve_energy_20ecn4101_child_lock_top"
            num = "number.aqara_door_and_window_sensor_p2_sensitivity"
            sw_paths = [p for p in ("1/319486977/319422481", "2/319486977/319422481")]
            sw_reg = next((x for x in found.get(("switch", "child_lock"), []) if x["entity_id"] == sw), None)
            RESULT["switch_unique_id"] = sw_reg and sw_reg["unique_id"]
            # endpoint for the top child lock: derive from unique_id suffix (deviceid_..-ep-cluster-attr style)
            sw_ep = sw_reg["unique_id"].split("-")[3] if sw_reg else None
            sw_path = f"{sw_ep or '?'}/319486977/319422481"
            ex.append(await exercise(session, ws, sel, "select", "select_option", {"option": "on"},
                                     (61, "1/6/16387", 1), "on"))
            ex.append(await exercise(session, ws, sel, "select", "select_option", {"option": "previous"},
                                     (61, "1/6/16387", None), "previous"))
            ex.append(await exercise(session, ws, sw, "switch", "turn_on", {}, (199, sw_path, True), "on"))
            ex.append(await exercise(session, ws, sw, "switch", "turn_off", {}, (199, sw_path, False), "off"))
            ex.append(await exercise(session, ws, num, "number", "set_value", {"value": 1},
                                     (91, "1/128/0", 0), "1"))
            ex.append(await exercise(session, ws, num, "number", "set_value", {"value": 3},
                                     (91, "1/128/0", 2), "3"))
            # A write the device refuses: Core surfaces an error, state unchanged, still exactly one write.
            async with session.post(STUB + "/_stub/control", json={"mode": "error"}) as resp:
                await resp.read()
            before = len((await stub_state(session))["writes"])
            reply = await ws.call(type="call_service", domain="number", service="set_value",
                                  service_data={"value": 2}, target={"entity_id": num})
            writes = (await stub_state(session))["writes"][before:]
            state = await wait_state(ws, num, "3", timeout=3)
            check("error write surfaces as service failure", reply.get("success") is False, error=reply.get("error"))
            check("error write recorded once, state unchanged", len(writes) == 1 and state["state"] == "3",
                  writes=writes, state=state["state"])
            async with session.post(STUB + "/_stub/control", json={"mode": "none"}) as resp:
                await resp.read()
            final = await stub_state(session)
            RESULT["stub"] = {k: final[k] for k in ("dump_hashes", "stub_sha256", "commands", "nodes")}
            RESULT["stub_commands_distinct"] = sorted(set(final["commands"]))
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(RESULT, handle, indent=2, sort_keys=True, default=str)
    print("RESULT", "PASS" if RESULT["ok"] else "FAIL", flush=True)
    return 0 if RESULT["ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
