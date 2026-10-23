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
# Core files that serve the three metadata reads Engineering adds around the
# Alarmo commands. Each run hashes them from the slot's verified source archive
# (HAMCP-159); docs/evidence/alarmo-inspection-source-review.json is historical.
CORE_READ_SOURCES = ("homeassistant/components/config/entity_registry.py",
                     "homeassistant/components/config/config_entries.py",
                     "homeassistant/components/websocket_api/commands.py")


def slot_binding(core_version):
    """Exact Alarmo-bearing support-window Core slot; never a family or prefix."""
    import sys
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    from support_window import Refusal, alarmo_slot
    if type(core_version) is not str:
        raise ValueError("Alarmo requires an exact support-window Core slot")
    try:
        return alarmo_slot(core_version)
    except Refusal as exc:
        raise ValueError("Alarmo requires an exact support-window Core slot") from exc


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
    store.areas = OrderedDict((key, area(area_id=key, name=("synthetic-excluded-area-" + key if persisted else "synthetic-excluded-area-name"), modes={
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


STORE_EVENT_KINDS = ("storage_save", "storage_delay_save", "storage_write", "storage_remove")
EVENT_KINDS = ("command", "config_flow_init", "flow_init", "flow_configure", "flow_abort", "service", *STORE_EVENT_KINDS)
OBSERVER_COMMANDS = ("manifest/get", "config_entries/get", "config/entity_registry/get_entries", "auth/current_user",
    "config/device_registry/list", "config/entity_registry/list", "ping", "alarmo/config", "alarmo/areas",
    "alarmo/sensors", "alarmo/entities", "alarmo/sensor_groups", "other")
TOLERATED_STORES = ("auth", "core.restore_state")
NAMED_STORES = ("alarmo.storage", "core.config_entries", "core.entity_registry", "core.device_registry",
                *TOLERATED_STORES, "other")
# The observer's fixed store-class vocabulary, mirrored exactly (a test binds
# the two). A class outside it is ``invalid``, never echoed (HAMCP-180).
CORE_STORE_NAMES = ("analytics", "area_registry", "category_registry", "config", "config_entries",
    "device_registry", "entity_registry", "floor_registry", "label_registry", "labs", "logger", "network",
    "restore_state", "uuid")
STORE_DOMAINS = ("ai_task", "alarmo", "alexa", "analytics", "application_credentials", "assist_pipeline",
    "auth_module", "auth_provider", "backup", "bluetooth", "broadlink", "camera", "cloud", "counter", "emulated_hue",
    "energy", "esphome", "evohome", "feedreader", "freebox", "frontend", "google", "google_assistant", "hassio",
    "homeassistant", "homeassistant_hardware", "homekit", "homekit_controller", "http", "icloud", "image",
    "image_upload", "input_boolean", "input_button", "input_datetime", "input_number", "input_select", "input_text",
    "knx", "labs", "logger", "lovelace", "mobile_app", "nest", "network", "onboarding", "person", "reolink",
    "repairs", "risco", "roborock", "schedule", "tag", "template", "thread", "timer", "trace", "unifi", "vizio",
    "voip", "zone")
STORE_CLASSES = frozenset({"unclassified", "core", *NAMED_STORES[:-1], *("core." + name for name in CORE_STORE_NAMES),
                           *STORE_DOMAINS})
WINDOW_LABEL = r"[a-z][a-z0-9_]{0,31}"
MAX_DIAGNOSTIC_ITEMS = 16
OBSERVER_MODEL = "alarmo-core-interval-v2"
# Every setup, readiness or start failure names one fixed cause, mapped to its
# condition family. Observer /start refusals use the same cause names.
SETUP_FAILURES = {
    "readiness_budget_exhausted": "setup_not_settled",
    "setup_not_settled_late_start": "setup_not_settled",
    "readiness_timeout": "setup_observation_unavailable",
    "readiness_transport_error": "setup_observation_unavailable",
    "readiness_shape_invalid": "setup_observation_unavailable",
    "readiness_hooks_not_intact": "setup_observation_unavailable",
    "store_class_list_malformed": "setup_observation_unavailable",
    "start_timeout": "setup_observation_unavailable",
    "start_transport_error": "setup_observation_unavailable",
    "start_response_invalid": "setup_observation_unavailable",
    "core_not_running": "interval_start_refused",
    "hooks_not_intact": "interval_start_refused",
    "store_hash_unavailable": "interval_start_refused",
    "interval_unavailable": "interval_start_refused",
    "start_internal_error": "interval_start_refused",
}
START_REFUSALS = ("setup_not_settled_late_start", "core_not_running", "hooks_not_intact",
                  "store_hash_unavailable", "interval_unavailable", "start_internal_error")
# How a failed /start was attributed by its one /ready read-back (HAMCP-180).
# Only a refusal recorded for this attempt's own identifier may name the cause.
START_RECOVERY = ("recovery_unavailable", "ready_recovery_malformed", "no_refusal", "current_refusal",
                  "stale_refusal_ignored")
ATTEMPT_ID = r"[0-9a-f]{32}"
SETUP_DIAGNOSTIC_KEYS = frozenset({"condition", "cause", "phase", "window", "window_index", "readiness_reads",
                                   "core_running", "pending_store_classes", "start_recovery"})


class ObservationError(ValueError):
    """Observer refusal carrying a bounded diagnostic (HAMCP-180).

    The message stays generic. ``diagnostic`` holds only fixed condition codes,
    observer vocabulary tokens, bounded store classes, a window label/index and
    counts, never paths, hashes, arguments or store contents.
    """

    def __init__(self, message, diagnostic):
        super().__init__(message)
        self.diagnostic = diagnostic


def _token(value, allowed):
    return value if type(value) is str and value in allowed else "invalid"


def _store_class(value):
    return value if type(value) is str and value in STORE_CLASSES else "invalid"


def _event_summary(event):
    """One event as bounded tokens; anything outside the vocabulary is ``invalid``."""
    if type(event) is not dict:
        return {"kind": "invalid"}
    summary = {"kind": _token(event.get("kind"), EVENT_KINDS),
               "origin": _token(event.get("origin"), ("command", "background"))}
    if "command" in event:
        summary["command"] = _token(event["command"], OBSERVER_COMMANDS)
    if "store" in event:
        summary["store"] = _token(event["store"], NAMED_STORES)
    if "store_class" in event:
        summary["store_class"] = _store_class(event["store_class"])
    unexpected = set(event) - {"kind", "origin", "command", "store", "store_class"}
    if unexpected:
        summary["unexpected_field_count"] = len(unexpected)
    return summary


def _event_counts(events):
    counts = {}
    for event in events if type(events) is list else ():
        summary = _event_summary(event)
        summary.pop("unexpected_field_count", None)
        if summary.get("kind") == "command":
            summary.pop("command", None)
        key = tuple(sorted(summary.items()))
        counts[key] = counts.get(key, 0) + 1
    rows = [dict(key, count=count) for key, count in sorted(counts.items())]
    return rows[:MAX_DIAGNOSTIC_ITEMS]


def verify_interval(value, identity, expected_commands, *, core_version, kind="inspection",
                    window=None, window_index=None):
    """Require independently observed Core activity, never client-side zero claims.

    A failure raises ``ObservationError`` naming the first failed condition
    with its window, the offending event's bounded summary and the interval's
    event counts. The checks themselves are unchanged.
    """
    import math
    import re
    keys = {"interval_id", "kind", "events", "overflow", "expired", "baseline_store_hashes", "model",
            "elapsed_seconds", "final_store_hashes", "coverage", "core_version", "observer_sha256"}
    events = value.get("events") if type(value) is dict else None
    context = {"window": window if type(window) is str and re.fullmatch(WINDOW_LABEL, window) else None,
               "window_index": window_index if type(window_index) is int and 0 <= window_index < 64 else None,
               "interval_kind": kind if kind in ("inspection", "control") else "invalid",
               "event_count": len(events) if type(events) is list else None,
               "event_counts": _event_counts(events)}
    def require(condition, code, **detail):
        if not condition:
            raise ObservationError("Disposable Core interval failed verification",
                                   {"condition": code, **context, **detail})
    require(type(value) is dict and set(value) == keys, "receipt_shape")
    require(type(core_version) is str and value["model"] == OBSERVER_MODEL, "observer_model")
    require(value["core_version"] == core_version, "core_version")
    require(value["interval_id"] == identity and type(identity) is str and re.fullmatch(r"[0-9a-f]{32}", identity),
            "interval_identity")
    require(value["kind"] == kind, "interval_kind")
    require(value["overflow"] is False, "event_overflow")
    require(value["expired"] is False, "interval_expired")
    elapsed = value["elapsed_seconds"]
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed <= 45, "elapsed_seconds")
    require(value["observer_sha256"] == hashlib.sha256(OBSERVER.read_bytes()).hexdigest(), "observer_source")
    require(type(value["coverage"]) is dict and set(value["coverage"]) == OBSERVER_HOOKS
            and all(item is True for item in value["coverage"].values()), "hook_coverage")
    for name in ("baseline_store_hashes", "final_store_hashes"):
        hashes = value[name]
        require(type(hashes) is dict and set(hashes) == STORE_HASH_KEYS, "store_hash_shape")
        require(all(item is None or (type(item) is str and re.fullmatch(r"[0-9a-f]{64}", item)) for item in hashes.values()),
                "store_hash_shape")
    changed = sorted(key for key in STORE_HASH_KEYS
                     if value["baseline_store_hashes"][key] != value["final_store_hashes"][key])
    require(not changed, "store_hash_changed", changed_stores=changed)
    require(type(events) is list and len(events) <= 128, "events_shape")
    commands, background = [], []
    for index, event in enumerate(events):
        detail = {"event_index": index, "event": _event_summary(event)}
        require(type(event) is dict and event.get("origin") in ("command", "background"), "event_shape", **detail)
        if event.get("kind") == "command":
            require(set(event) == {"kind", "origin", "command"} and type(event["command"]) is str,
                    "command_event_shape", **detail)
            commands.append(event["command"])
        else:
            # Auth token last-use and restore-state bookkeeping may occur on
            # Core's independent timers. Report it; do not call global writes zero.
            require(event.get("kind") in ("storage_save", "storage_delay_save", "storage_write"),
                    "unexpected_event_kind", **detail)
            require(event["origin"] == "background", "command_origin_storage", **detail)
            fields = {"kind", "origin", "store", "store_class"} if event.get("store") == "other" else {
                "kind", "origin", "store"}
            require(set(event) == fields, "storage_event_shape", **detail)
            require(event["store"] in TOLERATED_STORES, "untolerated_store", **detail)
            background.append(event)
    require(commands == expected_commands, "commands_mismatch",
            expected_command_count=len(expected_commands) if type(expected_commands) is list else None,
            observed_command_count=len(commands),
            observed_commands=[_token(item, OBSERVER_COMMANDS) for item in commands[:MAX_DIAGNOSTIC_ITEMS]])
    return {"result": "PASS", "commands": commands, "background_bookkeeping": background,
            "config_options_flow_events": 0, "service_events": 0, "command_origin_storage_events": 0,
            "fixed_persisted_stores_unchanged": True,
            "limitations": ["Finite interval; no claim about future arbitrary tasks or writes bypassing Core Store.",
                           "Background auth/restore-state bookkeeping is reported separately."]}


def _window(window, window_index):
    import re
    return {"window": window if type(window) is str and re.fullmatch(WINDOW_LABEL, window) else None,
            "window_index": window_index if type(window_index) is int and 0 <= window_index < 64 else None}


def setup_failure(cause, phase, *, window=None, window_index=None, readiness_reads=0, core_running=None,
                  pending_store_classes=None, start_recovery=None):
    """One setup/readiness/start failure as the fixed-shape diagnostic (HAMCP-180).

    Only a known cause, phase, window, counts, a boolean, vocabulary classes and
    the start read-back's attribution token; never an exception message, key,
    argument, attempt identifier or path.
    """
    cause = cause if type(cause) is str and cause in SETUP_FAILURES else "start_internal_error"
    classes = None
    if type(pending_store_classes) is list:
        classes = [_store_class(item) for item in pending_store_classes[:MAX_DIAGNOSTIC_ITEMS]]
    diagnostic = {"condition": SETUP_FAILURES[cause], "cause": cause,
                  "phase": phase if phase in ("readiness", "start") else "invalid",
                  **_window(window, window_index),
                  "readiness_reads": readiness_reads if type(readiness_reads) is int else None,
                  "core_running": core_running if type(core_running) is bool else None,
                  "pending_store_classes": classes,
                  "start_recovery": start_recovery if type(start_recovery) is str and start_recovery in START_RECOVERY
                  else None}
    return ObservationError("Disposable setup observation failed", diagnostic)


def _timed_out(error):
    return isinstance(error, TimeoutError) or isinstance(error.__cause__, TimeoutError)


def _class_list_problem(classes, pending):
    """Element types are checked before anything hashes or sorts them."""
    if type(classes) is not list or len(classes) > MAX_DIAGNOSTIC_ITEMS:
        return True
    if not all(type(item) is str for item in classes) or not all(item in STORE_CLASSES for item in classes):
        return True
    return classes != sorted(set(classes)) or bool(classes) != pending


def _refusal_problem(refusal):
    import re
    if refusal is None:
        return False
    return (type(refusal) is not dict or set(refusal) != {"attempt_id", "cause", "pending_store_classes"}
            or type(refusal["attempt_id"]) is not str or not re.fullmatch(ATTEMPT_ID, refusal["attempt_id"])
            or type(refusal["cause"]) is not str or refusal["cause"] not in START_REFUSALS
            or type(refusal["pending_store_classes"]) is not list
            or _class_list_problem(refusal["pending_store_classes"], bool(refusal["pending_store_classes"])))


def readiness_problem(value, *, store_classes=True):
    """The fixed cause a readiness value fails with, or ``None``.

    The whole envelope (shape, classes, retained refusal) is validated before
    the hook bit is read, so ``readiness_hooks_not_intact`` implies a
    well-formed value.
    """
    booleans = ("core_running", "storage_pending", "hooks_intact")
    fields = {*booleans, "pending_store_classes", "last_start_refusal"} if store_classes else set(booleans)
    if type(value) is not dict or set(value) != fields or any(type(value[item]) is not bool for item in booleans):
        return "readiness_shape_invalid"
    if store_classes:
        if _class_list_problem(value["pending_store_classes"], value["storage_pending"]):
            return "store_class_list_malformed"
        if _refusal_problem(value["last_start_refusal"]):
            return "readiness_shape_invalid"
    if not value["hooks_intact"]:
        return "readiness_hooks_not_intact"
    return None


def decode_readiness(value, *, store_classes=True):
    """``(problem, state)`` for one ``/ready`` value; never raises on observer data.

    ``state`` holds copies of validated fields only, and is ``None`` unless the
    envelope is well formed (``problem`` is ``None`` or
    ``readiness_hooks_not_intact``). Any unexpected error while decoding is the
    fixed ``readiness_shape_invalid`` (HAMCP-180).
    """
    try:
        problem = readiness_problem(value, store_classes=store_classes)
        if problem not in (None, "readiness_hooks_not_intact"):
            return problem, None
        state = {"core_running": value["core_running"], "storage_pending": value["storage_pending"],
                 "pending_store_classes": None, "refusal": None}
        if store_classes:
            refusal = value["last_start_refusal"]
            state.update(pending_store_classes=list(value["pending_store_classes"]),
                         refusal=None if refusal is None else {
                             "attempt_id": refusal["attempt_id"], "cause": refusal["cause"],
                             "pending_store_classes": list(refusal["pending_store_classes"])})
        return problem, state
    except Exception:
        return "readiness_shape_invalid", None


async def wait_disposable_setup(websocket, *, clock=None, sleep=None, store_classes=True,
                                window=None, window_index=None):
    """Wait for Core's source-defined 180s startup saves, outside observation.

    At most 41 readiness reads in 200 seconds. Never flush or retry a feature
    command; interval/feature budgets remain 45/30 seconds respectively.
    The Alarmo observer's readiness covers every untolerated Core Store and
    names pending ones by bounded class (HAMCP-180); ``store_classes=False``
    keeps the dashboard observer's three-field shape. Every failure raises
    ``ObservationError`` with the fixed setup diagnostic; cancellation and the
    causal error are preserved.
    """
    import asyncio
    import time
    clock = clock or time.monotonic
    sleep = sleep or asyncio.sleep
    deadline = clock() + 200
    last = {"readiness_reads": 0, "core_running": None, "pending_store_classes": None}
    def failure(cause):
        return setup_failure(cause, "readiness", window=window, window_index=window_index, **last)
    for attempt in range(41):
        remaining = deadline - clock()
        if remaining <= 0:
            break
        try:
            async with asyncio.timeout(remaining):
                value = await websocket.command({"type": "alarmo_interval_observer/ready"})
        except Exception as exc:  # CancelledError is not an Exception and propagates
            raise failure("readiness_timeout" if _timed_out(exc) else "readiness_transport_error") from exc
        last["readiness_reads"] = attempt + 1
        problem, state = decode_readiness(value, store_classes=store_classes)
        if problem:
            raise failure(problem)
        last["pending_store_classes"] = state["pending_store_classes"]
        last["core_running"] = state["core_running"]
        if clock() > deadline:
            break
        if state["core_running"] and not state["storage_pending"]:
            return {"result": "PASS", "readiness_reads": attempt + 1}
        remaining = deadline - clock()
        if remaining <= 0 or attempt == 40:
            break
        await sleep(min(5, remaining))
    raise failure("readiness_budget_exhausted")


async def start_interval(websocket, kind, *, window=None, window_index=None):
    """Open one observer interval or raise the fixed setup diagnostic (HAMCP-180).

    Each start carries a fresh test-owned ``attempt_id``. A failed start is
    attributed by one bounded ``/ready`` read-back, and only an observer refusal
    recorded for that same attempt may name the cause. Nothing is retried.
    """
    import uuid
    attempt_id = uuid.uuid4().hex
    try:
        value = await websocket.command({"type": "alarmo_interval_observer/start", "kind": kind,
                                         "attempt_id": attempt_id})
    except Exception as exc:  # CancelledError is not an Exception and propagates
        raise await _start_failure(websocket, exc, attempt_id, window=window, window_index=window_index) from exc
    interval_id = _start_response(value)
    if interval_id is None:
        raise setup_failure("start_response_invalid", "start", window=window, window_index=window_index)
    return interval_id


def _start_response(value):
    """The interval identifier from a ``/start`` result, or ``None``; never raises."""
    import re
    try:
        if (type(value) is dict and set(value) == {"model", "interval_id"} and value["model"] == OBSERVER_MODEL
                and type(value["interval_id"]) is str and re.fullmatch(r"[0-9a-f]{32}", value["interval_id"])):
            return value["interval_id"]
    except Exception:
        pass
    return None


async def _start_failure(websocket, error, attempt_id, *, window=None, window_index=None):
    """The structured failure for one failed ``/start`` (HAMCP-180).

    The current failure (``start_timeout`` or ``start_transport_error``) stays
    the cause unless the read-back is well formed and its retained refusal
    carries this attempt's identifier. A malformed or unavailable read-back, or
    a refusal from another attempt, never replaces it; ``start_recovery`` says
    which case applied.
    """
    import asyncio
    cause = "start_timeout" if _timed_out(error) else "start_transport_error"
    state = {"readiness_reads": 0, "core_running": None, "pending_store_classes": None,
             "start_recovery": "recovery_unavailable"}
    try:
        async with asyncio.timeout(10):
            value = await websocket.command({"type": "alarmo_interval_observer/ready"})
    except Exception:  # CancelledError is not an Exception and propagates
        pass
    else:
        state["readiness_reads"] = 1
        _problem, ready = decode_readiness(value)
        if ready is None:
            state["start_recovery"] = "ready_recovery_malformed"
        else:
            state.update(core_running=ready["core_running"], pending_store_classes=ready["pending_store_classes"])
            refusal = ready["refusal"]
            if refusal is None:
                state["start_recovery"] = "no_refusal"
            elif refusal["attempt_id"] == attempt_id:
                cause = refusal["cause"]
                state.update(pending_store_classes=refusal["pending_store_classes"], start_recovery="current_refusal")
            else:
                state["start_recovery"] = "stale_refusal_ignored"
    return setup_failure(cause, "start", window=window, window_index=window_index, **state)


async def close_disposable_authority(runtime):
    """Retire and join the test-owned Core monitor using its actual lifecycle."""
    import asyncio
    monitor = runtime._connection_monitor_task
    runtime.request_reconciliation(connection_changed=True)
    if monitor is not None:
        await asyncio.gather(monitor, return_exceptions=True)
    assert runtime._connection_monitor_task is None


async def run_disposable(configured, existing_core, *, fixture_receipt, expected_image, core_version):
    """Attach the bounded observer diagnostic to an observation failure (HAMCP-180)."""
    try:
        return await _run_disposable(configured, existing_core, fixture_receipt=fixture_receipt,
                                     expected_image=expected_image, core_version=core_version)
    except ObservationError as exc:
        exc.contract_missing_key = "alarmo_inspection"
        exc.contract_diagnostic = {"observation_failure": exc.diagnostic}
        raise


async def _run_disposable(configured, existing_core, *, fixture_receipt, expected_image, core_version):
    """Required slot-Core integration using a real signed 19-to-20 extension.

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
    from core_registry_contract_lane import lane_entry, probe_profile_for, reconcile_through_gate
    from harness_routes import harness_request
    from prepare_core_release_registry import prepare_candidate, sign_candidate, EXTENSION_EVIDENCE_MODEL
    from ha_mcp_engineering.clients import HomeAssistantWebSocketClient
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    from ha_mcp_engineering.signed_registry import canonical_json
    from ha_mcp_engineering.integration_inspection import contracts as c
    from ha_mcp_engineering.integration_inspection.models import Inspection
    from ha_mcp_engineering.integration_inspection.provider import AlarmoProvider
    from ha_mcp_engineering.integration_inspection.service import IntegrationInspectionService, references
    from ha_mcp_engineering.integration_inspection.runtime import INTEGRATION_INSPECTION
    from ha_mcp_engineering.tools.integration_inspection import registered_tool
    from ha_mcp_engineering.request_context import begin_request, end_request
    from entity_metadata_contract_acceptance import load_receipt
    if urlsplit(configured.ha_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Disposable inspector requires loopback Core")
    # HAMCP-180: the shared strict boundary refuses a repeated key before any binding check.
    receipt = load_receipt(Path(fixture_receipt).read_bytes())
    profile = json.loads((FIXTURES / "alarmo_profile.json").read_text())
    if (receipt.get("fixture") != "synthetic-alarmo-inspection-v1" or receipt.get("alarmo_source") != COMMIT
            or receipt.get("archive_sha256") != profile["archive_sha256"]):
        raise ValueError("Missing exact disposable fixture binding")
    binding = slot_binding(core_version)
    entry = lane_entry(core_version)
    if expected_image != binding["image"] or entry["image_index_digest"] != binding["image_index_digest"]:
        raise ValueError("Disposable Core image mismatch")
    assert existing_core.current_observation.version == core_version
    probe_profile = probe_profile_for(core_version)
    assert existing_core.acquire(c.CORE_REQUIREMENTS) is None
    websocket = HomeAssistantWebSocketClient(configured)
    entries = await websocket.command({"type": "config_entries/get", "domain": "alarmo"})
    if entries:
        raise ValueError("Unexpected pre-existing disposable Alarmo entry")
    created = await harness_request(configured, "POST", "/config/config_entries/flow", {"handler": "alarmo", "show_advanced_options": False})
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
    assert len(targets) == 1 and len(entities) == 3 and len({row["entity_id"] for row in entities}) == 3
    setup_readiness = await wait_disposable_setup(websocket, window="negative_control")

    async def start(kind, window):
        return await start_interval(websocket, kind, window=window)
    async def finish(identity):
        return await websocket.command({"type": "alarmo_interval_observer/finish", "interval_id": identity})
    # A real harmless extra command proves the observer/verifier rejects an
    # unexpected request; a fabricated zero client counter cannot satisfy it.
    control_id = await start("control", "negative_control")
    await websocket.command({"type": "get_states"})
    control = await finish(control_id)
    verify_interval(control, control_id, ["other"], kind="control", core_version=core_version,
                    window="negative_control")
    try:
        verify_interval(control, control_id, [], kind="control", core_version=core_version)
    except ObservationError:
        pass
    else:
        raise AssertionError("Observer negative control failed")

    evidence = b"Synthetic disposable Core nineteen-capability predecessor"
    refs = [{k: p.to_mapping()[k] for k in ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")}
            for p in CORE_RUNTIME_CAPABILITY_PROFILES
            # HAMCP-069A/069B/165: the dormant entity and label profiles are not signed here.
            if p.capability_id not in {"core.automation_baseline_metadata_read",
                                       "core.entity_registry_metadata_operation",
                                       "core.entity_registry_availability_operation",
                                       "core.entity_assistant_exposure_operation",
                                       "core.label_registry_operation"}]
    added = [r for r in refs if r["capability_id"] == "core.integration_inspection_metadata_read"]
    old = [r for r in refs if r not in added]
    assert len(old) == 19 and len(added) == 1
    entry.update(evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        probe_profile_id=probe_profile.profile_id,
        probe_profile_sha256=probe_profile.contract_fingerprint, capabilities=old)
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
            await runtime.reconcile_once("startup")
            assert runtime.health_snapshot()["compatible_count"] == 19
            assert runtime.acquire(c.CORE_REQUIREMENTS) is None
            service = IntegrationInspectionService(AlarmoProvider(websocket, runtime))
            denied_id = await start("control", "missing_authority")
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
            denial_proof = verify_interval(denied_observation, denied_id, [], kind="control",
                                           core_version=core_version, window="missing_authority")
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
            # The registry change retires the generation; the extension is
            # admitted only through the restoration gate.
            await reconcile_through_gate(runtime)
            assert runtime.health_snapshot()["compatible_count"] == 20
            authority = runtime.acquire(c.CORE_REQUIREMENTS)
            assert authority is not None
            commits = runtime.consume(authority)
            assert commits
            measured_id = await start("inspection", "inspection")
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
            measured = verify_interval(observed, measured_id, EXPECTED_COMMANDS, core_version=core_version,
                                       window="inspection")
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
            result = {"scenario": "alarmo_inspection", "result": "PASS", "core": core_version,
                "support_slot": binding["slot"], "core_image": binding["image"], "alarmo_commit": COMMIT,
                "setup_readiness": setup_readiness,
                "authority_transition": [19, 20], "same_registry_instance": True, "preserved_envelopes": 1,
                "observer_negative_control": "PASS", "negative_control_observation": control,
                "missing_authority": denial_proof, "missing_authority_observation": denied_observation,
                "interval": measured, "interval_observation": observed,
                "feature_commands": [p["type"] for p in calls], "continuation_reads": 0, "pages": 2,
                "records": len(records), "assessment": first["data"]["assessment"],
                "ephemeral_test_authority": True, "production_authority": False,
                "setup_flow_outside_feature_interval": True, "container_cleanup": "pending_outer_harness"}
        finally:
            await close_disposable_authority(runtime)
        result["runtime_monitor_closed"] = True
        output = os.environ.get("REAL_HA_ALARMO_RESULT")
        if not output:
            raise ValueError("Missing disposable receipt destination")
        Path(output).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
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


def core_read_source_hashes(archive):
    """Hash the Core read handlers inside the already digest-verified archive."""
    import tarfile
    with tarfile.open(archive) as stream:
        roots, found = set(), {}
        for member in stream:
            parts = Path(member.name).parts
            if not parts or Path(member.name).is_absolute() or ".." in parts:
                raise ValueError("Invalid Core source archive member")
            roots.add(parts[0])
            relative = "/".join(parts[1:])
            if relative in CORE_READ_SOURCES and member.isfile():
                if relative in found or member.size > 4 * 1024 * 1024:
                    raise ValueError("Ambiguous or oversized Core read source")
                found[relative] = hashlib.sha256(stream.extractfile(member).read()).hexdigest()
    if len(roots) != 1 or set(found) != set(CORE_READ_SOURCES):
        raise ValueError("Core read sources unavailable in verified archive")
    return dict(sorted(found.items()))


def verify_disposable_publication(image, container, source_archive, output, core_version):
    """Bind owned linux/amd64 container to verified index/manifest bytes."""
    import subprocess
    binding = slot_binding(core_version)
    entry = binding["provenance"]
    if image != binding["image"] or container != "beta25-real-ha-" + binding["lane"]:
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
    receipt = {"result": "PASS", "core_version": core_version, "support_slot": binding["slot"],
               "index": entry["image_index_digest"], "platform": "linux/amd64",
               "platform_manifest": descriptor["digest"], "configuration_digest": config_digest,
               "source_commit": entry["source_commit"], "source_archive_sha256": digest.hexdigest(),
               "core_read_sources_sha256": core_read_source_hashes(archive),
               "container_identity_continuity": True, "arm64_execution": False}
    output = Path(output)
    output.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    output.with_suffix(".index.json").write_bytes(index)
    output.with_suffix(".manifest.json").write_bytes(manifest)
    return receipt


def verify_disposable_cleanup(result_path, output, core_version, entity_metadata_receipt=None):
    """After outer cleanup, fail on Docker errors, missing run or owned residue.

    The standalone entity-metadata artifact (HAMCP-179), when given, must be the
    byte-identical canonical form of the retained, verified receipt.
    """
    import subprocess
    binding = slot_binding(core_version)
    lane = binding["lane"]
    def names(arguments):
        result = subprocess.run(["docker", *arguments], check=True, capture_output=True, timeout=30)
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("Oversized disposable cleanup inventory")
        return set(result.stdout.decode().splitlines())
    owned = {"beta25-real-ha-" + lane, "beta25-real-ha-writer-" + lane, "beta25-real-ha-upstream-" + lane}
    if owned & names(["container", "ls", "--all", "--format", "{{.Names}}"]):
        raise ValueError("Disposable containers remain")
    if "beta-rc2-contract-" + lane in names(["network", "ls", "--format", "{{.Name}}"]):
        raise ValueError("Disposable network remains")
    # HAMCP-179: one strict boundary; a repeated key at any depth refuses before validation.
    from entity_metadata_contract_acceptance import load_receipt, receipt_bytes, verify_result as verify_entity_metadata
    value = load_receipt(Path(result_path).read_bytes())
    if (value.get("result") != "PASS" or value.get("scenario") != "alarmo_inspection"
            or value.get("core") != core_version or value.get("support_slot") != binding["slot"]
            or value.get("core_image") != binding["image"] or value.get("runtime_monitor_closed") is not True):
        raise ValueError("Required Alarmo integration did not pass")
    for key, proof, expected, kind in (("interval_observation", "interval", EXPECTED_COMMANDS, "inspection"),
                ("missing_authority_observation", "missing_authority", [], "control")):
        observation = value[key]
        if verify_interval(observation, observation["interval_id"], expected, kind=kind,
                           core_version=core_version, window=proof) != value[proof]:
            raise ValueError("Retained interval verification mismatch")
    control = value["negative_control_observation"]
    verify_interval(control, control["interval_id"], ["other"], kind="control", core_version=core_version,
                    window="negative_control")
    from automation_baseline_contract_acceptance import verify_result as verify_baseline
    baseline = verify_baseline(value["automation_baseline"], core_version=core_version, support_slot=binding["slot"])
    entity_metadata = verify_entity_metadata(value["entity_metadata"], core_version=core_version,
                                             support_slot=binding["slot"])
    canonical = receipt_bytes(value["entity_metadata"])
    # Byte equality with a canonical dump of the strictly loaded value admits no repeated key.
    if entity_metadata_receipt is not None and Path(entity_metadata_receipt).read_bytes() != canonical:
        raise ValueError("Standalone entity metadata receipt differs from the retained receipt")
    entity_metadata["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    receipt = {"result": "PASS", "core": core_version, "support_slot": binding["slot"],
               "owned_containers_remaining": 0, "owned_network_remaining": False,
               "required_alarmo_interval": "PASS", "required_automation_baseline": baseline,
               "required_entity_metadata": entity_metadata,
               "integration_receipt_sha256": hashlib.sha256(Path(result_path).read_bytes()).hexdigest()}
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
            os.environ["REAL_HA_CORE_ARCHIVE"], os.environ["REAL_HA_ALARMO_IMAGE_RESULT"],
            os.environ["HA_CONTRACT_VERSION"]), sort_keys=True))
        return
    if arguments.verify_cleanup:
        print(json.dumps(verify_disposable_cleanup(os.environ["REAL_HA_ALARMO_RESULT"],
            os.environ["REAL_HA_ALARMO_CLEANUP_RESULT"], os.environ["HA_CONTRACT_VERSION"],
            os.environ["REAL_HA_ENTITY_METADATA_RESULT"]), sort_keys=True))
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
