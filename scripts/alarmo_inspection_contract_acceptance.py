"""Synthetic source-derived Alarmo fixtures and disposable read-contract checks.

No household endpoint, installed data, user store, or credentials are accepted.
Source extraction executes only named data classes/getters/read callbacks from
the hash-verified official Alarmo revision; it is not a full Core acceptance run.
"""

import argparse
import ast
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import attr


COMMIT = "169e134f4b70d87aae36ba54a72a398ecc960afd"
MODES = ("armed_away", "armed_home", "armed_night", "armed_custom_bypass", "armed_vacation")
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/integration_inspection"


def extract(source, names, namespace):
    tree = ast.parse(source)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            if node.name in found:
                raise ValueError("Ambiguous source extraction")
            found[node.name] = node
    if set(found) != set(names):
        raise ValueError("Incomplete source extraction")
    for name in names:
        node = found[name]
        exec(compile(ast.Module(body=[node], type_ignores=[]), "verified-alarmo-source", "exec"), namespace)


def fixture_from_source(source_root, *, persisted=False):
    source_root = Path(source_root)
    profile = json.loads((FIXTURES / "alarmo_profile.json").read_text())
    source = {}
    for name, expected in profile["source_sha256"].items():
        data = (source_root / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Alarmo source hash mismatch")
        source[name] = data.decode("utf-8")
    constants = SimpleNamespace(DOMAIN="alarmo", **{"CONF_ALARM_" + mode.upper(): mode for mode in MODES})
    namespace = {"attr": attr, "const": constants, "SENSOR_TYPE_OTHER": "other",
                 "CodeFormat": SimpleNamespace(NUMBER="number"), "callback": lambda function: function}
    classes = ("ModeEntry", "MqttConfig", "MasterConfig", "AreaEntry", "Config", "SensorEntry", "SensorGroupEntry")
    extract(source["store.py"], classes, namespace)
    getter_names = ("async_get_config", "async_get_areas", "async_get_sensors", "async_get_sensor_groups", "_data_to_save")
    extract(source["store.py"], getter_names, namespace)
    store_class = type("SyntheticStore", (), {name: namespace[name] for name in getter_names})
    store = store_class()
    mode = namespace["ModeEntry"]
    area = namespace["AreaEntry"]
    sensor = namespace["SensorEntry"]
    group = namespace["SensorGroupEntry"]
    store.config = namespace["Config"](code_arm_required=True, code_disarm_required=True)
    store.areas = OrderedDict((key, area(area_id=key, name="synthetic-excluded-area-name", modes={
        name: mode(enabled=name != "armed_vacation", entry_time=None if name == "armed_night" else 0,
                   exit_time=20, trigger_time=120) for name in MODES})) for key in ("0", "upstairs"))
    store.sensors = OrderedDict((entity, sensor(entity_id=entity, area=where, type=kind,
        enabled=enabled, modes=list(modes), always_on=always)) for entity, where, kind, enabled, modes, always in (
            ("binary_sensor.synthetic_door", "0", "door", True, MODES[:3], False),
            ("binary_sensor.synthetic_disabled", "0", "window", False, MODES[:2], False),
            ("binary_sensor.synthetic_always", "upstairs", "environmental", True, (), True),
            ("binary_sensor.synthetic_unregistered", "upstairs", "motion", True, MODES[:1], False),
        ))
    store.sensor_groups = {"synthetic_group": group(group_id="synthetic_group", name="synthetic-excluded-group-name",
        entities=["binary_sensor.synthetic_door", "binary_sensor.synthetic_always"], timeout=0, event_count=2)}
    store.users, store.automations = {}, {}
    if persisted:
        return store._data_to_save()
    coordinator_ns = {"ATTR_ENTITIES": "entities"}
    extract(source["__init__.py"], ("async_get_sensor_groups", "async_get_group_for_sensor"), coordinator_ns)
    coordinator = type("SyntheticCoordinator", (), {name: coordinator_ns[name] for name in
        ("async_get_sensor_groups", "async_get_group_for_sensor")})()
    coordinator.store = store
    hass = SimpleNamespace(data={"alarmo": {"coordinator": coordinator,
        "areas": {key: SimpleNamespace(entity_id="alarm_control_panel.synthetic_" + key) for key in store.areas},
        "master": SimpleNamespace(entity_id="alarm_control_panel.synthetic_master")}})
    callbacks = ("websocket_get_config", "websocket_get_areas", "websocket_get_sensors",
                 "websocket_get_alarm_entities", "websocket_get_sensor_groups")
    extract(source["websockets.py"], callbacks, namespace)
    before = store._data_to_save()
    results = {}
    for kind, callback_name in zip(("general", "areas", "sensors", "alarm_entities", "sensor_groups"), callbacks):
        connection = SimpleNamespace(send_result=lambda _id, result, kind=kind: results.update({kind: result}))
        namespace[callback_name](hass, connection, {"id": 1})
    assert store._data_to_save() == before
    # These are explicitly synthetic Core transport fixtures, not Alarmo getter
    # output or proof of Core execution. The disposable lane verifies Core.
    results["manifest"] = {"domain": "alarmo", "version": "1.10.19"}
    results["config_entries"] = [{"entry_id": "synthetic_entry", "domain": "alarmo", "state": "loaded"}]
    results["entity_registry"] = {entity: {"entity_id": entity, "id": "synthetic_registry_" + str(i),
        "disabled_by": "user" if "disabled" in entity else None} for i, entity in enumerate([
            *store.sensors, "alarm_control_panel.synthetic_master", "alarm_control_panel.synthetic_0"])}
    results["entity_registry"]["binary_sensor.synthetic_unregistered"] = None
    return results


def prepare_disposable_fixture(archive_path, destination):
    """Stage an exact custom component and synthetic store in a NEW directory.

    The caller owns installing this overlay before starting disposable Core.
    This function never edits an existing HA directory or starts/restarts Core.
    """
    import tarfile
    profile = json.loads((FIXTURES / "alarmo_profile.json").read_text())
    archive_path, destination = Path(archive_path), Path(destination)
    if archive_path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Oversized Alarmo source archive")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != profile["archive_sha256"]:
        raise ValueError("Alarmo archive mismatch")
    if destination.exists():
        raise ValueError("Disposable fixture destination must be new")
    prefix = "alarmo-" + COMMIT + "/custom_components/alarmo/"
    with tarfile.open(archive_path) as archive:
        selected = [m for m in archive.getmembers() if m.name.startswith(prefix) and m.isfile()]
        if {m.name[len(prefix):] for m in selected} != set(profile["component_files_sha256"]):
            raise ValueError("Alarmo component inventory mismatch")
        values = {}
        for member in selected:
            relative = member.name[len(prefix):]
            if Path(relative).is_absolute() or ".." in Path(relative).parts or member.size > 8 * 1024 * 1024:
                raise ValueError("Invalid component member")
            value = archive.extractfile(member).read()
            if hashlib.sha256(value).hexdigest() != profile["component_files_sha256"][relative]:
                raise ValueError("Alarmo component hash mismatch")
            values[relative] = value
    component = destination / "custom_components/alarmo"
    component.mkdir(parents=True)
    for relative, value in values.items():
        path = component / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    data = fixture_from_source(component, persisted=True)
    storage = destination / ".storage"
    storage.mkdir()
    (storage / "alarmo.storage").write_text(json.dumps({
        "version": 6, "minor_version": 3, "key": "alarmo.storage", "data": data}, sort_keys=True))
    receipt = {"fixture": "synthetic-alarmo-inspection-v1", "alarmo_source": COMMIT,
               "archive_sha256": profile["archive_sha256"], "users": 0, "automations": 0,
               "source_writer": "AlarmoStorage._data_to_save", "core_started": False}
    (destination / "alarmo-inspection-fixture.json").write_text(json.dumps(receipt, sort_keys=True))
    return receipt


async def run_disposable(configured, existing_core, *, fixture_receipt, expected_image):
    """Public tool against an explicitly prepared loopback Core/Alarmo fixture.

    Invoked only by the existing owned-container harness. Config-entry creation
    is disposable setup, before the measured read interval. No production key
    or journal is used; a fresh ephemeral authority is deleted with its tempdir.
    """
    import base64
    from datetime import datetime, timezone
    import sys
    import tempfile
    from unittest.mock import patch
    from urllib.parse import urlsplit
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
    from core_registry_contract_lane import lane_entry
    from prepare_core_release_registry import prepare_candidate, sign_candidate
    from ha_mcp_engineering.clients import HomeAssistantRestClient, HomeAssistantWebSocketClient
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    from ha_mcp_engineering.ha_core_readmission.probe_profiles import CHILD_DEVICE_PROBE_PROFILE
    from ha_mcp_engineering.signed_registry import canonical_json
    from ha_mcp_engineering.integration_inspection import contracts as c
    from ha_mcp_engineering.integration_inspection.provider import AlarmoProvider
    from ha_mcp_engineering.integration_inspection.service import IntegrationInspectionService
    from ha_mcp_engineering.integration_inspection.runtime import INTEGRATION_INSPECTION
    from ha_mcp_engineering.tools.integration_inspection import registered_tool
    from ha_mcp_engineering.request_context import begin_request, end_request
    if urlsplit(configured.ha_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Disposable inspector requires loopback Core")
    receipt = json.loads(Path(fixture_receipt).read_text())
    profile = json.loads((FIXTURES / "alarmo_profile.json").read_text())
    if (receipt.get("fixture") != "synthetic-alarmo-inspection-v1" or receipt.get("alarmo_source") != COMMIT
            or receipt.get("archive_sha256") != profile["archive_sha256"]):
        raise ValueError("Missing exact disposable fixture binding")
    entry = lane_entry("2026.9.3")
    if expected_image != "ghcr.io/home-assistant/home-assistant:2026.9.3@" + entry["image_index_digest"]:
        raise ValueError("Disposable Core image mismatch")
    assert existing_core.current_observation.version == "2026.9.3"
    assert existing_core.acquire(c.CORE_REQUIREMENTS) is None
    rest, websocket = HomeAssistantRestClient(configured), HomeAssistantWebSocketClient(configured)
    # Setup flow is authorized only in this disposable harness, never the tool.
    entries = await websocket.command({"type": "config_entries/get", "domain": "alarmo"})
    if entries:
        raise ValueError("Unexpected pre-existing disposable Alarmo entry")
    created = await rest.request("POST", "/config/config_entries/flow", {"handler": "alarmo", "show_advanced_options": False})
    assert created["type"] == "create_entry"
    entities = await websocket.command({"type": "alarmo/entities"})
    targets = [r["entity_id"] for r in entities if type(r.get("area_id")) is int and r["area_id"] == 0]
    assert len(targets) == 1
    evidence = b"Synthetic exact Alarmo integration test authority only"
    entry.update(evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        probe_profile_id=CHILD_DEVICE_PROBE_PROFILE.profile_id,
        probe_profile_sha256=CHILD_DEVICE_PROBE_PROFILE.contract_fingerprint,
        capabilities=[{k: p.to_mapping()[k] for k in ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")}
                      for p in CORE_RUNTIME_CAPABILITY_PROFILES])
    key, now = Ed25519PrivateKey.generate(), datetime.now(timezone.utc)
    candidate = canonical_json(prepare_candidate(entry=entry, evidence=evidence, previous=None, public_key=key.public_key(), now=now))
    signed = sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(), previous=None, key=key, now=now)
    async def fetch(_url, _maximum):
        return signed
    with tempfile.TemporaryDirectory(prefix="alarmo-synthetic-authority-") as temporary:
        registry = CoreReleaseRegistry(enabled=True, public_key=base64.b64encode(key.public_key().public_bytes_raw()).decode(),
            fetcher=fetch, cache_path=Path(temporary) / "synthetic.json")
        assert await registry.refresh()
        runtime = CoreRuntime()
        runtime.configure(configured, release_registry=registry)
        await runtime.reconcile_once("alarmo_disposable_authority")
        authority = runtime.acquire(c.CORE_REQUIREMENTS)
        assert authority is not None
        commits = runtime.consume(authority)
        assert commits
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_disposable_inspector"
        telemetry.core_dispatch_authorizer = lambda: runtime.revalidate(authority, commits)
        calls = []
        class Capture:
            async def read_alarmo_inspection(self, kind, **kwargs):
                calls.append(c.command_payload(kind, kwargs.get("entity_ids")))
                return await websocket.read_alarmo_inspection(kind, **kwargs)
        service = IntegrationInspectionService(AlarmoProvider(Capture(), runtime))
        try:
            with patch.object(INTEGRATION_INSPECTION, "service", service):
                first = json.loads(await registered_tool().run({"alarm_entity_id": targets[0], "limit": 1}))
                assert first["success"] and first["data"]["membership"]["total_in_scope"] == 4
                assert len(calls) == 9 and telemetry.ha_request_count == 9 and telemetry.upstream_request_count == 0
                cursor = first["data"]["pagination"]["next_cursor"]
                second = json.loads(await registered_tool().run({"alarm_entity_id": targets[0], "limit": 1, "cursor": cursor}))
                assert second["success"] and len(calls) == 9 and telemetry.ha_request_count == 9
                assert second["data"]["pagination"]["snapshot_fingerprint"] == first["data"]["pagination"]["snapshot_fingerprint"]
        finally:
            end_request(token)
            assert runtime.finish(commits)
        result = {"scenario": "alarmo_inspection", "result": "PASS", "core": "2026.9.3", "alarmo_commit": COMMIT,
                  "feature_commands": [p["type"] for p in calls], "feature_writes": 0, "continuation_reads": 0,
                  "ephemeral_test_authority": True, "production_authority": False,
                  "setup_flow_outside_feature_interval": True, "container_cleanup": "owned_by_outer_harness"}
        print("Alarmo disposable inspection contract: " + json.dumps(result, sort_keys=True))
        return result


def generate(source_root):
    results = fixture_from_source(source_root)
    (FIXTURES / "alarmo_positive.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    partial = json.loads(json.dumps(results))
    partial["sensors"]["binary_sensor.synthetic_door"].pop("enabled")
    partial["sensors"]["binary_sensor.synthetic_always"]["area"] = "synthetic_missing"
    partial["entity_registry"] = None
    (FIXTURES / "alarmo_partial.json").write_text(json.dumps(partial, indent=2, sort_keys=True) + "\n")
    injected = json.loads(json.dumps(results))
    injected["general"]["mqtt"] = {"password": "SYNTHETIC_PRIVATE_MQTT", "url": "https://example.invalid/api/webhook/SYNTHETIC_PRIVATE_WEBHOOK"}
    injected["general"]["users"] = {"SYNTHETIC_PRIVATE_USER": {"code": "SYNTHETIC_PRIVATE_PIN", "hash": "SYNTHETIC_PRIVATE_HASH"}}
    injected["general"]["actions"] = {"instructions": "SYNTHETIC_INJECTION_IGNORE_RULES"}
    for record in injected["entity_registry"].values():
        if record is not None:
            record["options"] = {"token": "SYNTHETIC_PRIVATE_REGISTRY"}
    (FIXTURES / "alarmo_secret_canaries.json").write_text(json.dumps(injected, indent=2, sort_keys=True) + "\n")
    return {"result": "generated", "alarmo_source": COMMIT, "fixture_scope": "synthetic_named_exact_source_getters_and_callbacks", "full_core_execution": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--alarmo-source", type=Path)
    parser.add_argument("--generate-fixtures", action="store_true")
    parser.add_argument("--prepare-archive", type=Path)
    parser.add_argument("--destination", type=Path)
    arguments = parser.parse_args()
    if arguments.prepare_archive and arguments.destination:
        print(json.dumps(prepare_disposable_fixture(arguments.prepare_archive, arguments.destination), sort_keys=True))
    elif arguments.alarmo_source is None:
        parser.error("A source or explicit disposable preparation pair is required")
    elif arguments.generate_fixtures:
        print(json.dumps(generate(arguments.alarmo_source), sort_keys=True))
    else:
        actual = fixture_from_source(arguments.alarmo_source)
        expected = json.loads((FIXTURES / "alarmo_positive.json").read_text())
        if actual != expected:
            raise SystemExit("Source-derived fixture differs")
        print(json.dumps({"result": "PASS", "fixture_reproduced": True, "full_core_execution": False}))


if __name__ == "__main__":
    main()
