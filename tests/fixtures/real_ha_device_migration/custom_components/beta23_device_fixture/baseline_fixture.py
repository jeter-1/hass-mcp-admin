"""Exact-.4 disposable baseline fixture; never part of the Engineering image.

The anchor is synthetic Core registry metadata, not a running Supervisor. All
setup mutations precede observed reads. No production or caller-selected paths.
"""
from pathlib import Path

PREFIX = "native_baseline_"
COMMAND = "beta23_device_fixture/baseline"


def rows(changed=False):
    result = [{"id": f"{PREFIX}{i:03}", "alias": f"Native baseline {i:03}",
               "initial_state": i != 0,
               "triggers": [{"trigger": "event", "event_type": "native_baseline_never_fired"}],
               "conditions": [{"condition": "template", "value_template": "{{ false }}"}],
               "actions": [{"delay": "00:00:00"}], "mode": "single"} for i in range(121)]
    if changed:
        result[0]["description"] = "Synthetic changed configuration"
        result = [r for r in result if r["id"] != PREFIX + "001"]
        result.append({**result[-1], "id": PREFIX + "121", "alias": "Native baseline 121"})
    return result


def register(hass):
    import voluptuous as vol
    import yaml
    from homeassistant.components import websocket_api
    from homeassistant.config_entries import ConfigEntry, ConfigEntryState
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    key = COMMAND + "/state"
    if key in hass.data:
        return
    state = hass.data[key] = {}
    path = Path(hass.config.path("automations.yaml"))

    def write(changed):
        path.write_text(yaml.safe_dump(state["original_rows"] + rows(changed), sort_keys=False))

    def new_entry():
        entry = ConfigEntry(version=1, minor_version=1, domain="hassio",
            title="Synthetic baseline lineage", data={}, options={}, source="user",
            unique_id="synthetic-baseline-core", discovery_keys={}, subentries_data=None,
            state=ConfigEntryState.LOADED)
        hass.config_entries._entries[entry.entry_id] = entry
        return entry

    def anchor():
        return dr.async_get(hass).async_get_or_create(
            config_entry_id=state["entry"].entry_id,
            identifiers={("hassio", "core")}, name="Synthetic baseline Core anchor")

    @websocket_api.websocket_command({vol.Required("type"): COMMAND,
        vol.Required("action"): vol.In(["seed", "change", "restore", "recreate", "cleanup"])})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def command(_hass, connection, message):
        action = message["action"]
        try:
            if action == "seed":
                if state or hass.config_entries.async_entries("hassio") or path.is_symlink():
                    raise ValueError("fixture unavailable")
                original = await hass.async_add_executor_job(path.read_bytes)
                parsed = yaml.safe_load(original) or []
                if (not isinstance(parsed, list) or len(parsed) > 1000
                        or any(str(r.get("id", "")).startswith(PREFIX) for r in parsed)):
                    raise ValueError("fixture collision")
                state.update(original=original, original_rows=parsed)
                # Exact Core ConfigEntry/ConfigEntryItems writers, deliberately
                # without Supervisor setup or any external connection.
                state["entry"] = new_entry()
                state["device"] = anchor()
                registry = er.async_get(hass)
                unknown = registry.async_get_or_create("automation", "automation", PREFIX + "unknown",
                    suggested_object_id=PREFIX + "unknown")
                state["unknown"] = unknown.entity_id
                registry.async_get_or_create("automation", "automation", PREFIX + "disabled",
                    suggested_object_id=PREFIX + "disabled", disabled_by=er.RegistryEntryDisabler.USER)
                await hass.async_add_executor_job(write, False)
                await hass.services.async_call("automation", "reload", {}, blocking=True)
                hass.states.async_set(unknown.entity_id, "off", {"id": PREFIX + "unknown"})
                user = await hass.auth.async_create_user("Synthetic baseline nonadmin", group_ids=[])
                state["user"] = user
                refresh = await hass.auth.async_create_refresh_token(user, client_id="http://127.0.0.1/")
                # Transient disposable credential returned only to the test
                # client. It must never enter artifacts or printed results.
                connection.send_result(message["id"], {"fixture": "native-baseline-v1",
                    "nonadmin_token": hass.auth.async_create_access_token(refresh)})
                return
            if not state:
                raise ValueError("fixture not seeded")
            if action == "change":
                await hass.async_add_executor_job(write, True)
                # Match the exact Core automation DELETE hook: removing YAML
                # alone can leave a restored/unavailable registry placeholder.
                registry = er.async_get(hass)
                removed = registry.async_get_entity_id("automation", "automation", PREFIX + "001")
                if removed is not None:
                    registry.async_remove(removed)
                await hass.services.async_call("automation", "reload", {}, blocking=True)
                hass.states.async_set(state["unknown"], "off", {"id": PREFIX + "unknown"})
            elif action == "restore":
                # Core restores its tombstone's ID for the same entry/identifier.
                previous = state["device"].id
                dr.async_get(hass).async_remove_device(previous)
                state["device"] = anchor()
                if state["device"].id != previous:
                    raise ValueError("fixture restoration changed identity")
            elif action == "recreate":
                # A genuinely new config-entry ID changes the hashed lineage,
                # even when Core restores the device's orphaned persistent ID.
                old = state["entry"].entry_id
                dr.async_get(hass).async_clear_config_entry(old)
                del hass.config_entries._entries[old]
                state["entry"] = new_entry()
                if state["entry"].entry_id == old:
                    raise ValueError("fixture entry was not recreated")
                state["device"] = anchor()
            elif action == "cleanup":
                await hass.async_add_executor_job(path.write_bytes, state["original"])
                await hass.services.async_call("automation", "reload", {}, blocking=True)
                registry = er.async_get(hass)
                for record in list(registry.entities.values()):
                    if record.platform == "automation" and str(record.unique_id).startswith(PREFIX):
                        hass.states.async_remove(record.entity_id)
                        registry.async_remove(record.entity_id)
                if "user" in state:
                    await hass.auth.async_remove_user(state["user"])
                dr.async_get(hass).async_remove_device(state["device"].id)
                del hass.config_entries._entries[state["entry"].entry_id]
                state.clear()
            connection.send_result(message["id"], {"result": "PASS", "action": action})
        except Exception:
            # Test diagnostics are fixed too: never expose transient tokens.
            connection.send_error(message["id"], "fixture_refused", "Baseline fixture refused")

    websocket_api.async_register_command(hass, command)
