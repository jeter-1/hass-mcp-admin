"""Disposable-only scene EDIT scenarios; never used by Engineering runtime."""
import asyncio
import copy
import json
import os
from pathlib import Path
import re
import secrets

EDITS = ("name", "icon", "icon_clear", "members")
SCENARIOS = (*EDITS, "rollback", "no_change", "not_found", "unknown_member",
             "target_mismatch", "stale_state", "stored_mismatch", "id_assignment")
SCOPE = "stored_configuration_and_registry_identity"


def exact(left, right):
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":"))


def checks(name, value):
    from seq15_contract_acceptance import SCENE_POST, _governed_success, _refused, _stale
    if name in {*EDITS, "rollback"}:
        _governed_success(value, 1)
        assert exact(value["expected"], value["readback"])
        assert value["write_types"] == [SCENE_POST]
        assert value["registry_identity_unchanged"] is True
        assert value["member_state_unchanged"] is True
        assert value["full_entry_preserved"] is True
        assert value["verification_scope"] == SCOPE and value["loaded_state"] == "not_verified"
    if name == "rollback":
        assert value["status"] == "rollback_plan_created"
    reasons = {"no_change": "scene_configuration_no_change", "not_found": "scene_configuration_target_not_found",
               "unknown_member": "scene_configuration_unknown_entities",
               "target_mismatch": "scene_configuration_target_mismatch"}
    if name in reasons:
        _refused(value, reasons[name])
    if name == "stale_state":
        _stale(value)
    if name == "stored_mismatch":
        assert value["task_state"] == "failed_post_dispatch"
        assert value["dispatch_counts"] == [1] and value["ha_writes"] == 1
        assert value["write_types"] == [SCENE_POST]
        assert "verification_mismatch" in value["diagnostic_codes"]
        assert value["mismatch_retained"] is True and value["member_state_unchanged"] is True
    if name == "id_assignment":
        assert value == {"result": "PASS", "assigned_id_valid": True, "unrequested_keys_preserved": True}


async def run(c):
    import yaml
    from seq15_contract_acceptance import REGISTRY_LIST
    stage, raw, owned, runtime = c["stage"], c["raw"], c["owned"], c["runtime"]
    stage[0] = "scene_fixture_setup"
    config_id = "seq15_" + secrets.token_hex(4)
    route = "/config/scene/config/" + config_id
    created = await raw.ok({"type": "input_select/create", "name": "Seq15 Scene Member " + secrets.token_hex(4),
                            "options": ["Home", "Away", "Night"]})
    owned["input_select"] = created["id"]
    member = "input_select." + created["id"]
    owned["scenes"] = [config_id]

    async def member_state():
        return (await raw.rest("GET", "/states/" + member))["state"]

    async def stored():
        return await raw.rest("GET", route)

    async def identity():
        for _ in range(100):
            rows = [row for row in await raw.ok({"type": REGISTRY_LIST})
                    if row.get("platform") == "homeassistant" and row.get("unique_id") == config_id]
            if len(rows) == 1:
                return {key: rows[0][key] for key in ("entity_id", "id", "unique_id", "platform")}
            await asyncio.sleep(.1)
        raise AssertionError("Disposable scene registry identity unavailable")

    # A no-id entry is seeded before Core starts, in the isolated CI config.
    path = Path(os.environ["REAL_HA_CONTRACT_DIR"]) / "scenes.yaml"
    before_seed = yaml.safe_load(path.read_text())
    (seed,) = [row for row in before_seed if row.get("name") == "Seq15 Id Assignment"]
    assert "id" not in seed
    body = {"name": "Seq15 Scene " + secrets.token_hex(4), "icon": "mdi:home",
            "entities": {member: {"state": "Away"}}, "metadata": {member: {"entity_only": True}}}
    await raw.rest("POST", route, body)
    initial_identity = await identity()
    initial_member = await member_state()
    assert initial_member == "Home"
    after_seed = yaml.safe_load(path.read_text())
    (assigned,) = [row for row in after_seed if row.get("name") == "Seq15 Id Assignment"]
    assigned_id = assigned.get("id")
    assert isinstance(assigned_id, str) and re.fullmatch("[0-9a-f]{32}", assigned_id)
    owned["scenes"].append(assigned_id)
    scenarios = {"id_assignment": {"result": "PASS", "assigned_id_valid": True,
                                  "unrequested_keys_preserved": exact(seed, {k: v for k, v in assigned.items() if k != "id"})}}

    async def record_edit(name, change):
        stage[0] = name
        baseline, observed = await stored(), {}
        expected = copy.deepcopy(baseline)
        for key, value in change.items():
            if key == "icon" and value is None:
                expected.pop(key, None)
            elif key == "entities":
                expected[key].update(value)
            else:
                expected[key] = value
        value = await c["governed"](runtime, {"config_id": config_id, **change}, observed=observed)
        readback = await stored()
        scenarios[name] = {**value, "expected": expected, "readback": readback,
                           "registry_identity_unchanged": await identity() == initial_identity,
                           "member_state_unchanged": await member_state() == initial_member,
                           "full_entry_preserved": exact(readback, expected)}
        checks(name, scenarios[name])
        return baseline, observed["plan"]

    await record_edit("name", {"name": "Seq15 Scene Exact "})
    await record_edit("icon", {"icon": "mdi:map-marker"})
    await record_edit("icon_clear", {"icon": None})
    prior, forward = await record_edit("members", {"entities": {member: {"state": "Night"}}})
    stage[0] = "rollback"
    created, reverse = await c["rollback"](forward["plan_id"])
    result = await c["apply_created"](reverse)
    readback = await stored()
    scenarios["rollback"] = {**result, "status": created["status"], "expected": prior, "readback": readback,
                             "registry_identity_unchanged": await identity() == initial_identity,
                             "member_state_unchanged": await member_state() == initial_member,
                             "full_entry_preserved": exact(prior, readback)}
    checks("rollback", scenarios["rollback"])
    refusals = {"no_change": {"config_id": config_id, "name": readback["name"]},
                "not_found": {"config_id": "seq15_00000000", "name": "Absent"},
                "unknown_member": {"config_id": config_id, "entities": {"light.seq15_absent": {"state": "off"}}},
                "target_mismatch": {"config_id": config_id, "entity_id": "scene.seq15_absent", "name": "Other"}}
    for name, args in refusals.items():
        stage[0] = name
        scenarios[name] = await c["refused_plan"](runtime, args)
        checks(name, scenarios[name])

    async def drift():
        value = await stored()
        await raw.rest("POST", route, {**{k: v for k, v in value.items() if k != "id"}, "name": "Seq15 Other Writer"})

    stage[0] = "stale_state"
    result = await c["governed"](runtime, {"config_id": config_id, "name": "Seq15 Stale"}, between=drift)
    scenarios["stale_state"] = {**result, "drift_retained": (await stored())["name"] == "Seq15 Other Writer"}
    checks("stale_state", scenarios["stale_state"])

    stage[0] = "stored_mismatch"
    # The harness's independent writer changes actual Core storage after the one
    # Engineering POST, before its readback. No Engineering code is patched.
    c["observed_rest"].after_scene_save = drift
    result = await c["governed"](runtime, {"config_id": config_id, "name": "Seq15 Mismatch"})
    scenarios["stored_mismatch"] = {**result, "mismatch_retained": (await stored())["name"] == "Seq15 Other Writer",
                                    "member_state_unchanged": await member_state() == initial_member}
    checks("stored_mismatch", scenarios["stored_mismatch"])
    return scenarios
