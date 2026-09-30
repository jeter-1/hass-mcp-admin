"""Data-only, binary-owned read contract. No runtime discovery or forwarding."""

from enum import Enum
import hashlib
import json
import re


class ReadKind(str, Enum):
    MANIFEST = "manifest"
    CONFIG_ENTRIES = "config_entries"
    ALARM_ENTITIES = "alarm_entities"
    AREAS = "areas"
    SENSORS = "sensors"
    GENERAL = "general"
    SENSOR_GROUPS = "sensor_groups"
    ENTITY_REGISTRY = "entity_registry"


ALARMO_VERSION = "1.10.19"
ALARMO_COMMIT = "169e134f4b70d87aae36ba54a72a398ecc960afd"
ADAPTER_ID = "alarmo-inspection-v1"
PROFILE_ID = "alarmo-configuration-read-v1"
CORE_CAPABILITY = "core.integration_inspection_metadata_read"
CORE_REQUIREMENTS = ("core.basic_websocket_read", CORE_CAPABILITY)
MODES = ("armed_away", "armed_home", "armed_night", "armed_custom_bypass", "armed_vacation")
SENSOR_TYPES = ("door", "window", "motion", "tamper", "environmental", "other")
GENERAL_FIELDS = ("code_arm_required", "code_disarm_required", "code_mode_change_required",
                  "disarm_after_trigger", "ignore_blocking_sensors_after_trigger")
SENSOR_FLAGS = ("enabled", "use_entry_delay", "use_exit_delay", "always_on", "arm_on_close",
                "allow_open", "trigger_unavailable", "auto_bypass")
COMMANDS = {
    ReadKind.MANIFEST: ("manifest/get", ("integration", "alarmo")),
    ReadKind.CONFIG_ENTRIES: ("config_entries/get", ("domain", "alarmo")),
    ReadKind.ALARM_ENTITIES: ("alarmo/entities", None),
    ReadKind.AREAS: ("alarmo/areas", None),
    ReadKind.SENSORS: ("alarmo/sensors", None),
    ReadKind.GENERAL: ("alarmo/config", None),
    ReadKind.SENSOR_GROUPS: ("alarmo/sensor_groups", None),
    ReadKind.ENTITY_REGISTRY: ("config/entity_registry/get_entries", None),
}
MAX_COMMANDS = 9
COLLECTION_SECONDS = 30
COMMAND_SECONDS = 5
MAX_COLLECTORS = 2
MAX_FRAMES = 16
AUTH_BYTES = 65_536
FRAME_BYTES = 2_097_152
COLLECTION_BYTES = 8_388_608
MAX_NODES = 50_000
MAX_DEPTH = 16
MAX_AREAS = 32
MAX_SENSORS = 512
MAX_GROUPS = 128
MAX_EDGES = 2048
MAX_REGISTRY = 513
SNAPSHOT_BYTES = 2_097_152
MAX_SNAPSHOTS = 8
SNAPSHOT_TTL = 300
PAGE_BYTES = 49_152
ENVELOPE_BYTES = 60_000
MAX_GAPS = 16
ENTITY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
PANEL_PATTERN = re.compile(r"^alarm_control_panel\.[a-z0-9_]{1,100}$")
OPAQUE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
NEVER_COLLECTED = ("users", "actions", "diagnostics", "options_flows", "files")
EXCLUDED = ("names", "codes_and_hashes", "code_lengths", "mqtt", "templates", "registry_options",
            "sensor_specific_timings", "runtime_watcher_state")


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


CORE_METADATA_CONTRACT = {
    "commands": [[COMMANDS[k][0], COMMANDS[k][1]] for k in
                 (ReadKind.MANIFEST, ReadKind.CONFIG_ENTRIES, ReadKind.ENTITY_REGISTRY)],
    "manifest_projection": ["domain", "version"],
    "config_entry_projection": ["entry_id", "domain", "state"],
    "registry_projection": ["entity_id", "id", "disabled_by"],
    "registry_result": "requested_entity_id_to_extended_record_or_null",
    "registry_maximum": MAX_REGISTRY,
    "principal_origin": "existing_configured_authenticated_origin_only",
    "forbidden": ["options_flows", "writes", "fallback", "arbitrary_commands"],
}
ADAPTER_CONTRACT_SHA256 = digest({
    "profile": PROFILE_ID, "adapter": ADAPTER_ID, "source": ALARMO_COMMIT,
    "version": ALARMO_VERSION, "commands": {k.value: v for k, v in COMMANDS.items()},
    "general": GENERAL_FIELDS, "sensor_flags": SENSOR_FLAGS, "modes": MODES,
    "mode_times": "strict_nullable_integer_not_effective_delay",
    "eligibility": "enabled AND area_mode.enabled AND (mode IN modes OR always_on)",
    "excluded": EXCLUDED, "fallback": "none",
})


def valid_entity(value) -> bool:
    return type(value) is str and len(value) <= 128 and ENTITY_PATTERN.fullmatch(value) is not None


def validate_arguments(arguments) -> None:
    if (type(arguments) is not dict or not set(arguments) <= {"alarm_entity_id", "integration", "limit", "cursor"}
            or type(arguments.get("alarm_entity_id")) is not str
            or not PANEL_PATTERN.fullmatch(arguments["alarm_entity_id"])
            or arguments.get("integration", "alarmo") != "alarmo"
            or type(arguments.get("integration", "alarmo")) is not str
            or type(arguments.get("limit", 25)) is not int
            or not 1 <= arguments.get("limit", 25) <= 50
            or type(arguments.get("cursor", "")) is not str
            or len(arguments.get("cursor", "")) > 2048):
        raise ValueError("Invalid integration inspection arguments.")


def command_payload(kind: ReadKind, entity_ids: tuple[str, ...] | None = None) -> dict:
    if type(kind) is not ReadKind:
        raise ValueError("Invalid inspection read kind.")
    if kind is ReadKind.ENTITY_REGISTRY:
        if (type(entity_ids) is not tuple or not 1 <= len(entity_ids) <= MAX_REGISTRY
                or any(not valid_entity(item) for item in entity_ids)
                or tuple(sorted(set(entity_ids))) != entity_ids):
            raise ValueError("Invalid inspection registry selection.")
    elif entity_ids is not None:
        raise ValueError("Invalid inspection registry selection.")
    command, selector = COMMANDS[kind]
    payload = {"type": command}
    if selector:
        payload[selector[0]] = selector[1]
    if kind is ReadKind.ENTITY_REGISTRY:
        payload["entity_ids"] = list(entity_ids)
    return payload
