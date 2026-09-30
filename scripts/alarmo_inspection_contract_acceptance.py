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


EXPECTED_COMMANDS = ["manifest/get", "config_entries/get", "alarmo/entities", "alarmo/areas",
    "alarmo/sensors", "alarmo/config", "alarmo/sensor_groups", "config/entity_registry/get_entries", "manifest/get"]
OBSERVER = ROOT / "tests/fixtures/alarmo_interval_observer/custom_components/alarmo_interval_observer/__init__.py"
OBSERVER_HOOKS = {"ActiveConnection.async_handle", "ConfigEntriesFlowManager.async_init", "FlowManager.async_init",
    "FlowManager.async_configure", "FlowManager.async_abort", "ServiceRegistry.async_call", "Store.async_save",
    "Store.async_delay_save", "Store._async_write_data", "Store.async_remove"}
STORE_HASH_KEYS = {"alarmo.storage", "core.config_entries", "core.entity_registry", "core.device_registry"}


def verify_interval(value, identity, expected_commands, *, kind="inspection"):
    """Require independently observed Core activity, never client-side zero claims."""
    import math
    import re
    keys = {"interval_id", "kind", "events", "overflow", "expired", "baseline_store_hashes", "model",
            "elapsed_seconds", "final_store_hashes", "coverage", "core_version", "observer_sha256"}
    def require(condition):
        if not condition:
            raise ValueError("Disposable Core interval failed verification")
    require(type(value) is dict and set(value) == keys)
    require(value["model"] == "alarmo-core-interval-v1" and value["core_version"] == "2026.9.4")
    require(value["interval_id"] == identity and type(identity) is str and re.fullmatch(r"[0-9a-f]{32}", identity))
    require(value["kind"] == kind and value["overflow"] is False and value["expired"] is False)
    elapsed = value["elapsed_seconds"]
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed <= 45)
    require(value["observer_sha256"] == hashlib.sha256(OBSERVER.read_bytes()).hexdigest())
    require(type(value["coverage"]) is dict and set(value["coverage"]) == OBSERVER_HOOKS
            and all(item is True for item in value["coverage"].values()))
    for name in ("baseline_store_hashes", "final_store_hashes"):
        hashes = value[name]
        require(type(hashes) is dict and set(hashes) == STORE_HASH_KEYS)
        require(all(item is None or (type(item) is str and re.fullmatch(r"[0-9a-f]{64}", item)) for item in hashes.values()))
    require(value["baseline_store_hashes"] == value["final_store_hashes"])
    events = value["events"]
    require(type(events) is list and len(events) <= 128)
    commands, background = [], []
    for event in events:
        require(type(event) is dict and event.get("origin") in ("command", "background"))
        if event.get("kind") == "command":
            require(set(event) == {"kind", "origin", "command"} and type(event["command"]) is str)
            commands.append(event["command"])
        else:
            # Auth token last-use and restore-state bookkeeping may occur on
            # Core's independent timers. Report it; do not call global writes zero.
            require(set(event) == {"kind", "origin", "store"}
                    and event["kind"] in ("storage_save", "storage_delay_save", "storage_write")
                    and event["origin"] == "background" and event["store"] in ("auth", "core.restore_state"))
            background.append(event)
    require(commands == expected_commands)
    return {"result": "PASS", "commands": commands, "background_bookkeeping": background,
            "config_options_flow_events": 0, "service_events": 0, "command_origin_storage_events": 0,
            "fixed_persisted_stores_unchanged": True,
            "limitations": ["Finite interval; no claim about future arbitrary tasks or writes bypassing Core Store.",
                           "Background auth/restore-state bookkeeping is reported separately."]}


async def run_disposable(configured, existing_core, *, fixture_receipt, expected_image):
    """Required Core .4 integration using a real signed 19-to-20 extension.

    Setup is confined to an owned loopback disposable Core. Observer intervals
    surround public-tool calls; setup flows and registry probes stay outside.
    """
    import asyncio
    import base64
    from datetime import datetime, timedelta, timezone
    import os
    import sys
    import tempfile
    from unittest.mock import patch
    from urllib.parse import urlsplit
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
    from core_registry_contract_lane import lane_entry
    from prepare_core_release_registry import prepare_candidate, sign_candidate, EXTENSION_EVIDENCE_MODEL
    from ha_mcp_engineering.clients import HomeAssistantRestClient, HomeAssistantWebSocketClient
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    from ha_mcp_engineering.ha_core_readmission.probe_profiles import CHILD_DEVICE_PROBE_PROFILE
    from ha_mcp_engineering.signed_registry import canonical_json
    from ha_mcp_engineering.integration_inspection import contracts as c
    from ha_mcp_engineering.integration_inspection.models import Inspection
    from ha_mcp_engineering.integration_inspection.provider import AlarmoProvider
    from ha_mcp_engineering.integration_inspection.service import IntegrationInspectionService, references
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
    entry = lane_entry("2026.9.4")
    if expected_image != "ghcr.io/home-assistant/home-assistant:2026.9.4@" + entry["image_index_digest"]:
        raise ValueError("Disposable Core image mismatch")
    assert existing_core.current_observation.version == "2026.9.4"
    assert existing_core.acquire(c.CORE_REQUIREMENTS) is None
    rest, websocket = HomeAssistantRestClient(configured), HomeAssistantWebSocketClient(configured)
    entries = await websocket.command({"type": "config_entries/get", "domain": "alarmo"})
    if entries:
        raise ValueError("Unexpected pre-existing disposable Alarmo entry")
    created = await rest.request("POST", "/config/config_entries/flow", {"handler": "alarmo", "show_advanced_options": False})
    assert created["type"] == "create_entry"
    # Setup readiness polling only, never retry an inspection command.
    for _ in range(30):
        entries = await websocket.command({"type": "config_entries/get", "domain": "alarmo"})
        if len(entries) == 1 and entries[0].get("state") == "loaded":
            break
        await asyncio.sleep(1)
    else:
        raise ValueError("Disposable Alarmo setup did not settle")
    entities = await websocket.command({"type": "alarmo/entities"})
    targets = [r["entity_id"] for r in entities if type(r.get("area_id")) is int and r["area_id"] == 0]
    assert len(targets) == 1

    async def start(kind):
        return (await websocket.command({"type": "alarmo_interval_observer/start", "kind": kind}))["interval_id"]
    async def finish(identity):
        return await websocket.command({"type": "alarmo_interval_observer/finish", "interval_id": identity})
    # A real harmless extra command proves the observer/verifier rejects an
    # unexpected request; a fabricated zero client counter cannot satisfy it.
    control_id = await start("control")
    await websocket.command({"type": "get_states"})
    control = await finish(control_id)
    verify_interval(control, control_id, ["other"], kind="control")
    try:
        verify_interval(control, control_id, [], kind="control")
    except ValueError:
        pass
    else:
        raise AssertionError("Observer negative control failed")

    evidence = b"Synthetic disposable Core nineteen-capability predecessor"
    refs = [{k: p.to_mapping()[k] for k in ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")}
            for p in CORE_RUNTIME_CAPABILITY_PROFILES]
    added = [r for r in refs if r["capability_id"] == "core.integration_inspection_metadata_read"]
    old = [r for r in refs if r not in added]
    assert len(old) == 19 and len(added) == 1
    entry.update(evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        probe_profile_id=CHILD_DEVICE_PROBE_PROFILE.profile_id,
        probe_profile_sha256=CHILD_DEVICE_PROBE_PROFILE.contract_fingerprint, capabilities=old)
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
        try:
            await runtime.reconcile_once("alarmo_disposable_predecessor")
            assert runtime.health_snapshot()["compatible_count"] == 19
            assert runtime.acquire(c.CORE_REQUIREMENTS) is None
            service = IntegrationInspectionService(AlarmoProvider(websocket, runtime))
            denied_id = await start("control")
            telemetry, token = begin_request()
            telemetry.caller_id = "synthetic_disposable_inspector"
            telemetry.core_dispatch_authorizer = lambda: False
            try:
                with patch.object(INTEGRATION_INSPECTION, "service", service):
                    denied = json.loads(await registered_tool().run({"alarm_entity_id": targets[0]}))
                assert not denied["success"] and denied["details"]["reason"] == "authority_unavailable"
                assert telemetry.ha_request_count == 0
            finally:
                end_request(token)
            denied_observation = await finish(denied_id)
            denial_proof = verify_interval(denied_observation, denied_id, [], kind="control")
            previous = signed
            extension = canonical_json({"model": EXTENSION_EVIDENCE_MODEL, "version": entry["version"],
                "previous_entry_sha256": hashlib.sha256(canonical_json(entry)).hexdigest(),
                "previous_capabilities": old, "added_capabilities": added,
                "review_artifacts_sha256": [hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                            hashlib.sha256(OBSERVER.read_bytes()).hexdigest()]})
            extended = {**entry, "capabilities": old + added, "evidence_sha256": hashlib.sha256(extension).hexdigest()}
            candidate = canonical_json(prepare_candidate(entry=extended, evidence=extension, previous=previous,
                public_key=key.public_key(), now=now + timedelta(seconds=1), operation="extend-capabilities"))
            signed = sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(), previous=previous,
                                    key=key, now=now + timedelta(seconds=1))
            assert await registry.refresh()
            await runtime.reconcile_once("alarmo_disposable_extension")
            assert runtime.health_snapshot()["compatible_count"] == 20
            authority = runtime.acquire(c.CORE_REQUIREMENTS)
            assert authority is not None
            commits = runtime.consume(authority)
            assert commits
            measured_id = await start("inspection")
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
                    first = json.loads(await registered_tool().run({"alarm_entity_id": targets[0], "limit": 10}))
                    assert first["success"] and first["data"]["membership"]["total_in_scope"] == 4
                    assert len(calls) == 9 and telemetry.ha_request_count == 9 and telemetry.upstream_request_count == 0
                    cursor = first["data"]["pagination"]["next_cursor"]
                    assert cursor
                    second = json.loads(await registered_tool().run({"alarm_entity_id": targets[0], "limit": 10, "cursor": cursor}))
                    assert second["success"] and len(calls) == 9 and telemetry.ha_request_count == 9
                    assert not second["data"]["pagination"]["has_more"]
                    assert second["data"]["pagination"]["snapshot_fingerprint"] == first["data"]["pagination"]["snapshot_fingerprint"]
            finally:
                end_request(token)
                assert runtime.finish(commits)
            observed = await finish(measured_id)
            measured = verify_interval(observed, measured_id, EXPECTED_COMMANDS)
            records = []
            for page in (first["data"], second["data"]):
                Inspection.model_validate(page)
                refs_in_page = set(references(page["records"]))
                assert refs_in_page == {(e["source_id"], e["pointer"]) for e in page["evidence_entries"]}
                assert not page["routing"]["fallback_occurred"] and page["routing"]["data_providers"] == ["direct_ha_api"]
                assert page["freshness"]["atomic_snapshot"] is False and not page["truncation"]["occurred"]
                records.extend(page["records"])
            sensors = {r["configured_entity_id"]: r for r in records if r["kind"] == "sensor"}
            assert len(sensors) == 4 and sensors["binary_sensor.synthetic_disabled"]["enabled"]["value"] is False
            assert sensors["binary_sensor.synthetic_always"]["always_on"]["value"] is True
            assert sensors["binary_sensor.synthetic_unregistered"]["registry"]["status"] == "not_in_registry"
            modes = [r for r in records if r["kind"] == "mode"]
            assert len(modes) == 10
            assert {m["entry_time_seconds"]["value"] for m in modes} == {0, None}
            assert any(m["enabled"]["value"] is False for m in modes)
            assert all(m["exit_time_seconds"]["value"] == 20 for m in modes)
            assert len([r for r in records if r["kind"] == "sensor_group"]) == 1
            encoded = json.dumps([first, second])
            assert "synthetic-excluded" not in encoded and "mqtt" not in json.dumps(records)
            health = runtime.health_snapshot()
            assert all(health[k] == 0 for k in ("issued_lease_count", "active_commit_count", "fallback_count"))
            result = {"scenario": "alarmo_inspection", "result": "PASS", "core": "2026.9.4", "alarmo_commit": COMMIT,
                "authority_transition": [19, 20], "same_registry_instance": True, "preserved_envelopes": 1,
                "observer_negative_control": "PASS", "negative_control_observation": control,
                "missing_authority": denial_proof, "missing_authority_observation": denied_observation,
                "interval": measured, "interval_observation": observed,
                "feature_commands": [p["type"] for p in calls], "continuation_reads": 0, "pages": 2,
                "records": len(records), "assessment": first["data"]["assessment"],
                "ephemeral_test_authority": True, "production_authority": False,
                "setup_flow_outside_feature_interval": True, "container_cleanup": "pending_outer_harness"}
            output = os.environ.get("REAL_HA_ALARMO_RESULT")
            if not output:
                raise ValueError("Missing disposable receipt destination")
            Path(output).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
            print("Alarmo disposable inspection contract: " + json.dumps(result, sort_keys=True))
            return result
        finally:
            await runtime.shutdown()


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


def verify_disposable_publication(image, container, source_archive, output):
    """Bind owned linux/amd64 container to verified index/manifest bytes."""
    import subprocess
    entry = json.loads((ROOT / "tests/fixtures/core_2026_9_4_lane_provenance.json").read_text())
    expected = "ghcr.io/home-assistant/home-assistant:2026.9.4@" + entry["image_index_digest"]
    if image != expected or container != "beta25-real-ha-ha-2026-9-4":
        raise ValueError("Unexpected disposable image or container")
    archive = Path(source_archive)
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        size = 0
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > 128 * 1024 * 1024:
                raise ValueError("Oversized Core source archive")
            digest.update(chunk)
    if digest.hexdigest() != entry["source_archive_sha256"]:
        raise ValueError("Core source archive mismatch")
    def command(arguments):
        # Only fixed disposable Docker metadata commands, no inspect environment.
        result = subprocess.run(["docker", *arguments], check=True, capture_output=True, timeout=90)
        if len(result.stdout) > 2 * 1024 * 1024:
            raise ValueError("Oversized image metadata")
        return result.stdout
    def verify_bytes(raw, expected_digest):
        # buildx can append a display newline; accept only digest-matching bytes.
        for value in (raw, raw[:-1] if raw.endswith(b"\n") else raw):
            if "sha256:" + hashlib.sha256(value).hexdigest() == expected_digest:
                return value
        raise ValueError("Image content digest mismatch")
    index = verify_bytes(command(["buildx", "imagetools", "inspect", "--raw", image]), entry["image_index_digest"])
    descriptors = [d for d in json.loads(index)["manifests"]
                   if d.get("platform", {}).get("os") == "linux" and d["platform"].get("architecture") == "amd64"]
    if len(descriptors) != 1 or descriptors[0]["digest"] != entry["architecture_manifests"]["linux/amd64"]:
        raise ValueError("Architecture descriptor mismatch")
    descriptor = descriptors[0]
    reference = "ghcr.io/home-assistant/home-assistant@" + descriptor["digest"]
    manifest = verify_bytes(command(["buildx", "imagetools", "inspect", "--raw", reference]), descriptor["digest"])
    if len(manifest) != descriptor["size"]:
        raise ValueError("Architecture manifest size mismatch")
    config_digest = json.loads(manifest)["config"]["digest"]
    before = command(["container", "inspect", "--format", "{{.Id}} {{.Image}}", container]).decode().strip().split()
    image_identity = command(["image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}", image]).decode().strip().split()
    after = command(["container", "inspect", "--format", "{{.Id}} {{.Image}}", container]).decode().strip().split()
    if len(before) != 2 or before != after or before[1] != config_digest or image_identity != [config_digest, "linux/amd64"]:
        raise ValueError("Running disposable image binding failed")
    receipt = {"result": "PASS", "index": entry["image_index_digest"], "platform": "linux/amd64",
               "platform_manifest": descriptor["digest"], "configuration_digest": config_digest,
               "source_commit": entry["source_commit"], "source_archive_sha256": digest.hexdigest(),
               "container_identity_continuity": True, "arm64_execution": False}
    output = Path(output)
    output.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    output.with_suffix(".index.json").write_bytes(index)
    output.with_suffix(".manifest.json").write_bytes(manifest)
    return receipt


def verify_disposable_cleanup(result_path, output):
    """After outer cleanup, fail on Docker errors, missing run or owned residue."""
    import subprocess
    def names(arguments):
        result = subprocess.run(["docker", *arguments], check=True, capture_output=True, timeout=30)
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("Oversized disposable cleanup inventory")
        return set(result.stdout.decode().splitlines())
    owned = {"beta25-real-ha-ha-2026-9-4", "beta25-real-ha-writer-ha-2026-9-4", "beta25-real-ha-upstream-ha-2026-9-4"}
    if owned & names(["container", "ls", "--all", "--format", "{{.Names}}"]):
        raise ValueError("Disposable containers remain")
    if "beta-rc2-contract-ha-2026-9-4" in names(["network", "ls", "--format", "{{.Name}}"]):
        raise ValueError("Disposable network remains")
    value = json.loads(Path(result_path).read_text())
    if value.get("result") != "PASS" or value.get("scenario") != "alarmo_inspection" or value.get("core") != "2026.9.4":
        raise ValueError("Required Alarmo integration did not pass")
    for key, proof, expected, kind in (("interval_observation", "interval", EXPECTED_COMMANDS, "inspection"),
                ("missing_authority_observation", "missing_authority", [], "control")):
        observation = value[key]
        if verify_interval(observation, observation["interval_id"], expected, kind=kind) != value[proof]:
            raise ValueError("Retained interval verification mismatch")
    control = value["negative_control_observation"]
    verify_interval(control, control["interval_id"], ["other"], kind="control")
    receipt = {"result": "PASS", "owned_containers_remaining": 0, "owned_network_remaining": False,
               "required_alarmo_interval": "PASS", "integration_receipt_sha256": hashlib.sha256(Path(result_path).read_bytes()).hexdigest()}
    Path(output).write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--alarmo-source", type=Path)
    parser.add_argument("--generate-fixtures", action="store_true")
    parser.add_argument("--prepare-archive", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--verify-publication", action="store_true")
    parser.add_argument("--verify-cleanup", action="store_true")
    arguments = parser.parse_args()
    import os
    if arguments.verify_publication:
        print(json.dumps(verify_disposable_publication(os.environ["HA_CONTRACT_IMAGE"], os.environ["HA_CONTRACT_CONTAINER"],
            os.environ["REAL_HA_CORE_ARCHIVE"], os.environ["REAL_HA_ALARMO_IMAGE_RESULT"]), sort_keys=True))
        return
    if arguments.verify_cleanup:
        print(json.dumps(verify_disposable_cleanup(os.environ["REAL_HA_ALARMO_RESULT"],
            os.environ["REAL_HA_ALARMO_CLEANUP_RESULT"]), sort_keys=True))
        return
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
