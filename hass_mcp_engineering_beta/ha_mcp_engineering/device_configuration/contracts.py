"""Closed device-configuration contract (HAMCP-297). No generic services or data.

One exact entity per call, one closed service per domain, registry proof of a
configuration-category device entity of exactly one of three compiled target
kinds. Semantics follow Home Assistant Core 2026.11.0 (``74cf5a49``); see
``docs/HAMCP_297_DEVICE_CONFIGURATION_ENTITIES_DESIGN.md``.

This module is imported by ``ha_core_readmission.profiles``; it must never
import ``ha_core_readmission``.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError

from ..errors import ErrorCode
from ..fan.contracts import OPERATION_PATTERN, FanRefusal, digest

FAMILY = "device_configuration"
PROVIDER = "direct_home_assistant_device_configuration"
PROVIDER_CONTRACT = "direct-ha-exact-device-configuration-v1"
CAPABILITY_ID = "core.device_configuration_entity_operation"
ADAPTER_ID = "compiled-core-device-configuration-entity-operation-v1"
CORE_VERSION = "2026.11.0"

DOMAIN_ACTIONS = {
    "select": ("select_option",),
    "switch": ("turn_on", "turn_off"),
    "number": ("set_value",),
}
ACTIONS = ("select_option", "set_value", "turn_off", "turn_on", "undo")
ENTITY_PATTERN = r"^(select|switch|number)\.[a-z0-9_]{1,120}$"
TASK_ID_PATTERN = r"^[a-f0-9]{32}$"
NUMBER_PATTERN = re.compile(r"-?(0|[1-9][0-9]{0,8})(\.[0-9]{1,6})?")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MAX_TEXT = 255  # Core MAX_LENGTH_STATE_STATE
MAX_OPTIONS = 256
MAX_IDENTIFIERS = 32
MAX_NUMBER_OPTIONS = 16

# Q9/B15/B16: the compiled target-kind allowlist, (domain, platform,
# translation_key). A null translation_key never matches. Changing this set
# changes the signed profile fingerprint.
ALLOWLIST = (
    ("number", "matter", "sensitivity_level"),
    ("select", "matter", "startup_on_off"),
    ("switch", "matter", "child_lock"),
)
# Core 2026.11.0 ``components/number/const.py`` UNIT_CONVERTERS keys (41),
# embedded verbatim in the profile (§5.5.1 rule 2).
UNIT_CONVERTERS = (
    "absolute_humidity", "apparent_power", "area", "atmospheric_pressure",
    "blood_glucose_concentration", "carbon_monoxide", "conductivity", "current",
    "data_rate", "data_size", "distance", "duration", "energy", "energy_distance",
    "energy_storage", "frequency", "gas", "nitrogen_dioxide", "nitrogen_monoxide",
    "ozone", "power", "power_factor", "precipitation", "precipitation_intensity",
    "pressure", "radon", "reactive_energy", "reactive_power", "speed",
    "sulphur_dioxide", "temperature", "temperature_delta",
    "volatile_organic_compounds", "volatile_organic_compounds_parts", "voltage",
    "volume", "volume_flow_rate", "volume_storage", "water", "weight", "wind_speed",
)
# The complete command vocabulary (§5.4).
READ_COMMANDS = ("config/device_registry/list", "config/entity_registry/get")
REST_READS = ("GET /api/states/{entity_id}",)
SERVICES = {
    "number": {"set_value": ("value",)},
    "select": {"select_option": ("option",)},
    "switch": {"turn_off": (), "turn_on": ()},
}
NEVER_SENT = (
    "select.select_first", "select.select_last", "select.select_next",
    "select.select_previous", "switch.toggle", "number.set_value:any_key_except_value",
    "button.press", "update.*", "light.*", "config/entity_registry/update",
    "config/device_registry/update", "return_response:true", "non_entity_targets",
)
PREDISPATCH_CHECKS = (
    "entity_registry_config_category_device_proof",
    "device_configuration_target_kind_admitted",
    "device_configuration_state_readback_exact",
    "device_configuration_number_identity_conversion",
)
UNDO_RULES = (
    "original_forward_change_succeeded_verified_same_namespace_same_entity",
    "verified_observation_sidecar_bound_to_prepared_hash",
    "target_identity_equal_to_recorded",
    "current_observation_equal_to_recorded_post_change_observation",
    "restore_value_revalidated_against_current_attributes",
    "claimed_once_inside_receipt_transaction",
    "undo_of_undo_not_eligible",
)
NUMERIC_RULES = (
    "bounds_finite_min_lt_max_step_gt_0_equal_registry_capabilities",
    "identity_conversion_proof_unit_converters_registry_unit_or_temperature",
    "assumed_state_disclosed",
    "range_and_exact_decimal_step",
    "ieee754_double_round_trip",
    "readback_exact_decimal_with_unchanged_unit_and_bounds",
    "never_rounded_snapped_or_clamped",
    "undo_value_revalidated",
)
LOCK_IDENTITY = (
    "entity:<entity_id>:exclusive", "entity_registry:<entity_id>:shared",
    "home_assistant:core:shared",
)

# Authority or runtime absence, not a request fault: no dispatch occurred.
UNAVAILABLE_REFUSALS = frozenset({
    "device_configuration_core_authority_unavailable",
    "device_configuration_core_authority_retired",
    "device_configuration_provider_unavailable",
    "device_configuration_runtime_unavailable",
})
INVALID_VALUE = "device_configuration_invalid_value"
ACTION_NOT_SUPPORTED = "device_configuration_action_not_supported"
FIELD_NOT_SUPPORTED = "device_configuration_field_not_supported"
STATE_CHANGED = "device_configuration_state_changed"

DISCLOSURE_KINDS = (
    "persistence", "outage_restart_recovery", "physical_access", "sensing_behavior",
    "feedback_limits", "integration_semantics",
)


class DeviceConfigurationRefusal(FanRefusal):
    """Fixed local categories only; never Home Assistant response text."""

    def __init__(self, category):
        if isinstance(category, str) and category.startswith("fan_"):
            category = "device_configuration_" + category[4:]
        super().__init__(category)
        if category in UNAVAILABLE_REFUSALS:
            self.code = ErrorCode.PROVIDER_UNAVAILABLE
        status = "provider_unavailable" if self.code == ErrorCode.PROVIDER_UNAVAILABLE else "refused"
        self.details = {**self.details, "status": status}


Refusal = DeviceConfigurationRefusal


def validate_option(value) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_TEXT or _CONTROL.search(value):
        raise Refusal(INVALID_VALUE)
    return value


def canonical_number(value) -> Decimal:
    """A canonical decimal literal that round-trips exactly through a double."""
    if not isinstance(value, str) or not NUMBER_PATTERN.fullmatch(value):
        raise Refusal(INVALID_VALUE)
    number = Decimal(value)
    if value.startswith("-") and number == 0:
        raise Refusal(INVALID_VALUE)  # sign only on non-zero values
    if Decimal(repr(float(value))) != number:
        raise Refusal(INVALID_VALUE)
    return number


class DeviceConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    entity_id: str = Field(pattern=ENTITY_PATTERN)
    action: Literal[ACTIONS]  # type: ignore[valid-type]
    operation_id: str = Field(pattern=OPERATION_PATTERN)
    value: StrictStr | None = None
    undo_task_id: str | None = Field(default=None, pattern=TASK_ID_PATTERN)
    acknowledge_safety_critical: StrictBool = False

    @classmethod
    def parse(cls, raw) -> DeviceConfigurationRequest:
        """Validate a raw argument mapping with the three closed input refusals."""
        if not isinstance(raw, dict):
            raise Refusal(INVALID_VALUE)
        try:
            request = cls.model_validate(raw)
        except ValidationError as exc:
            errors = exc.errors()
            if any(item.get("type") == "extra_forbidden" for item in errors):
                raise Refusal(FIELD_NOT_SUPPORTED) from None
            if any(tuple(item.get("loc", ()))[:1] == ("action",) for item in errors):
                raise Refusal(ACTION_NOT_SUPPORTED) from None
            raise Refusal(INVALID_VALUE) from None
        return request.checked()

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def is_undo(self) -> bool:
        return self.action == "undo"

    def checked(self) -> DeviceConfigurationRequest:
        if not self.is_undo and self.action not in DOMAIN_ACTIONS[self.domain]:
            raise Refusal(ACTION_NOT_SUPPORTED)
        if (self.action in {"select_option", "set_value"}) != (self.value is not None):
            raise Refusal(INVALID_VALUE)
        if self.is_undo != (self.undo_task_id is not None):
            raise Refusal(INVALID_VALUE)
        if self.value is not None:
            if self.domain == "select":
                validate_option(self.value)
            else:
                canonical_number(self.value)
        return self

    @property
    def task_id(self) -> str:
        return digest({"device_configuration_operation_id": self.operation_id})[:32]

    def is_fresh(self, now) -> bool:
        age = now.timestamp() - int(self.operation_id.split("-", 1)[0])
        return -30 <= age <= 300


def forward_value(request) -> str | None:
    """The value a forward request sets (``None`` for an undo)."""
    if request.action == "turn_on":
        return "on"
    if request.action == "turn_off":
        return "off"
    return request.value


def service_call(entity_id: str, domain: str, value: str) -> dict:
    """The only commands this boundary can construct (§5.4)."""
    if not isinstance(entity_id, str) or not re.fullmatch(ENTITY_PATTERN, entity_id) \
            or entity_id.split(".", 1)[0] != domain:
        raise Refusal(INVALID_VALUE)
    payload = {"type": "call_service", "domain": domain}
    if domain == "select":
        payload.update(service="select_option", service_data={"option": validate_option(value)})
    elif domain == "switch":
        if value not in ("on", "off"):
            raise Refusal(INVALID_VALUE)
        payload["service"] = "turn_" + value
    elif domain == "number":
        payload.update(service="set_value", service_data={"value": float(canonical_number(value))})
    else:
        raise Refusal(INVALID_VALUE)
    payload["target"] = {"entity_id": entity_id}
    return payload


# --- Observation projection (§5.7) ----------------------------------------

def _text(value, *, nullable=False, limit=MAX_TEXT):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        raise Refusal(STATE_CHANGED)
    return value


def _stamp(value):
    """Registry timestamps are JSON numbers in Core 2026.11.0; accept strings too."""
    if isinstance(value, str) and 1 <= len(value) <= 64:
        return value
    if type(value) in {int, float} and math.isfinite(value):
        return value
    raise Refusal(STATE_CHANGED)


def _scalar(value):
    if value is None or type(value) in {bool, int, str} or (
            type(value) is float and math.isfinite(value)):
        if isinstance(value, str) and len(value) > MAX_TEXT:
            raise Refusal(STATE_CHANGED)
        return value
    raise Refusal(STATE_CHANGED)


def checked_entry(entity_id: str, entry) -> dict:
    """§5.5 items 1-2 and the registry part of the projection."""
    if entry is None or not isinstance(entry, dict) or entry.get("entity_id") != entity_id:
        raise Refusal("device_configuration_entity_not_registered")
    if entry.get("entity_category") != "config":
        raise Refusal("device_configuration_not_config_category")
    device_id = entry.get("device_id")
    if not isinstance(device_id, str) or not 1 <= len(device_id) <= MAX_TEXT:
        raise Refusal("device_configuration_no_device")
    options = entry.get("options")
    number_options = options.get("number") if isinstance(options, dict) else None
    if number_options is not None:
        if not isinstance(number_options, dict) or len(number_options) > MAX_NUMBER_OPTIONS:
            raise Refusal(STATE_CHANGED)
        number_options = {_text(key, limit=64): _scalar(value)
                          for key, value in sorted(number_options.items())}
    capabilities = entry.get("capabilities")
    return {
        "id": _text(entry.get("id")),
        "created_at": _stamp(entry.get("created_at")),
        "modified_at": _stamp(entry.get("modified_at")),
        "entity_category": "config",
        "device_id": device_id,
        "platform": _text(entry.get("platform")),
        "translation_key": _text(entry.get("translation_key"), nullable=True),
        "unique_id": _text(entry.get("unique_id")),
        "config_entry_id": _text(entry.get("config_entry_id"), nullable=True),
        "original_device_class": _text(entry.get("original_device_class"), nullable=True),
        "options_number": number_options,
        "disabled_by": _text(entry.get("disabled_by"), nullable=True, limit=64),
        "_capabilities": capabilities if isinstance(capabilities, dict) else None,
    }


def checked_device(registry: dict, devices) -> tuple[dict, str | None]:
    """§5.5 item 3 and the device part of the projection, plus a display name."""
    if not isinstance(devices, list):
        raise Refusal(STATE_CHANGED)
    matches = [item for item in devices
               if isinstance(item, dict) and item.get("id") == registry["device_id"]]
    if len(matches) != 1:
        raise Refusal("device_configuration_no_device")
    device = matches[0]
    identifiers = device.get("identifiers")
    if not isinstance(identifiers, list) or len(identifiers) > MAX_IDENTIFIERS:
        raise Refusal(STATE_CHANGED)
    pairs = []
    for pair in identifiers:
        if not isinstance(pair, list) or len(pair) != 2:
            raise Refusal(STATE_CHANGED)
        pairs.append([_text(pair[0]), _text(pair[1])])
    name = device.get("name_by_user") or device.get("name")
    name = name[:MAX_TEXT] if isinstance(name, str) and not _CONTROL.search(name) else None
    return {
        "id": registry["device_id"],
        "created_at": _stamp(device.get("created_at")),
        "identifiers": sorted(pairs),
        "primary_config_entry": _text(device.get("primary_config_entry"), nullable=True),
        "disabled_by": _text(device.get("disabled_by"), nullable=True, limit=64),
    }, name


def check_enabled_and_kind(domain: str, registry: dict, device: dict, extra_kinds=()) -> tuple:
    """§5.5 items 4-5. Registry reads only before this point."""
    if registry["disabled_by"] is not None or device["disabled_by"] is not None:
        raise Refusal("device_configuration_target_disabled")
    kind = (domain, registry["platform"], registry["translation_key"])
    if kind[2] is None or (kind not in ALLOWLIST and kind not in extra_kinds):
        raise Refusal("device_configuration_target_kind_not_admitted")
    return kind


def _bound(value) -> Decimal:
    if type(value) not in {int, float} or not math.isfinite(value):
        raise Refusal("device_configuration_number_bounds_inconsistent")
    return Decimal(repr(float(value)))


def state_decimal(state) -> Decimal | None:
    if not isinstance(state, str) or not 1 <= len(state) <= 64:
        return None
    try:
        value = Decimal(state)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def checked_state(entity_id: str, raw, registry: dict) -> tuple[dict, str | None]:
    """§5.5 item 6 and the state part of the projection, plus a display name."""
    if not isinstance(raw, dict) or raw.get("entity_id") != entity_id:
        raise Refusal(STATE_CHANGED)
    state = raw.get("state")
    attributes = raw.get("attributes")
    context = raw.get("context")
    if not isinstance(attributes, dict) or not isinstance(context, dict):
        raise Refusal(STATE_CHANGED)
    if state in {"unavailable", "unknown"}:
        raise Refusal("device_configuration_entity_unavailable")
    if not isinstance(state, str) or not 1 <= len(state) <= MAX_TEXT:
        raise Refusal("device_configuration_baseline_not_restorable")
    assumed = attributes.get("assumed_state", False)
    if type(assumed) is not bool:
        raise Refusal(STATE_CHANGED)
    domain = entity_id.split(".", 1)[0]
    projected = {}
    if domain == "switch":
        if state not in {"on", "off"}:
            raise Refusal("device_configuration_baseline_not_restorable")
    elif domain == "select":
        options = attributes.get("options")
        if (not isinstance(options, list) or not 1 <= len(options) <= MAX_OPTIONS
                or not all(isinstance(item, str) and 1 <= len(item) <= MAX_TEXT for item in options)
                or len(set(options)) != len(options) or state not in options):
            raise Refusal("device_configuration_baseline_not_restorable")
        projected["options"] = list(options)
    else:
        low, high, step = (_bound(attributes.get(key)) for key in ("min", "max", "step"))
        if not low < high or not step > 0:
            raise Refusal("device_configuration_number_bounds_inconsistent")
        capabilities = registry["_capabilities"] or {}
        try:
            same = all(_bound(capabilities.get(key)) == value
                       for key, value in (("min", low), ("max", high), ("step", step)))
        except DeviceConfigurationRefusal:
            same = False
        if not same:
            raise Refusal("device_configuration_number_bounds_inconsistent")
        device_class = registry["original_device_class"]
        if device_class in UNIT_CONVERTERS and (
                "unit_of_measurement" in (registry["options_number"] or {})
                or device_class == "temperature"):
            raise Refusal("device_configuration_number_unit_conversion")
        unit = attributes.get("unit_of_measurement")
        mode = attributes.get("mode")
        projected.update(min=str(low), max=str(high), step=str(step),
                         mode=_text(mode, nullable=True, limit=32),
                         unit_of_measurement=_text(unit, nullable=True, limit=64))
        try:
            restorable_number(state, projected)
        except DeviceConfigurationRefusal:
            raise Refusal("device_configuration_baseline_not_restorable") from None
    name = attributes.get("friendly_name")
    name = name[:MAX_TEXT] if isinstance(name, str) and not _CONTROL.search(name) else None
    return {
        "entity_id": entity_id, "state": state,
        "last_changed": _text(raw.get("last_changed"), limit=64),
        "last_updated": _text(raw.get("last_updated"), limit=64),
        "context_id": _text(context.get("id"), limit=64),
        "assumed_state": assumed,
        "attributes": projected,
    }, name


def restorable_number(state: str, attributes: dict) -> str:
    """The canonical form of a numeric state that passes rules 4-5."""
    value = state_decimal(state)
    if value is None:
        raise Refusal(INVALID_VALUE)
    text = format(value.normalize(), "f") if value != 0 else "0"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    canonical_number(text)
    check_number(Decimal(text), attributes)
    return text


def check_number(value: Decimal, attributes: dict) -> None:
    """§5.5.1 rule 4: inside the bounds and exactly on a step, no tolerance."""
    low, high, step = (Decimal(attributes[key]) for key in ("min", "max", "step"))
    if not low <= value <= high:
        raise Refusal("device_configuration_number_out_of_range")
    if ((value - low) / step) % 1 != 0:
        raise Refusal("device_configuration_number_step_misaligned")


def observation(state: dict, registry: dict, device: dict) -> dict:
    """The one composite projection; its digest is the state fingerprint."""
    return {
        **state,
        "registry": {key: value for key, value in registry.items() if not key.startswith("_")},
        "device": device,
    }


def identity(observed: dict) -> dict:
    """The §5.11.1 target identity."""
    registry, device = observed["registry"], observed["device"]
    return {
        "registry": {key: registry[key] for key in (
            "id", "created_at", "unique_id", "platform", "translation_key",
            "config_entry_id", "device_id", "entity_category")},
        "device": {key: device[key] for key in (
            "id", "created_at", "identifiers", "primary_config_entry")},
    }


def baseline_value(observed: dict) -> str:
    """The restorable prior value, in the form a request would carry."""
    if observed["entity_id"].startswith("number."):
        return restorable_number(observed["state"], observed["attributes"])
    return observed["state"]


def check_value(domain: str, value: str, observed: dict) -> None:
    """§5.5 item 7 against the current attributes (also the undo revalidation)."""
    if domain == "select":
        if value not in observed["attributes"]["options"]:
            raise Refusal("device_configuration_value_not_offered")
    elif domain == "number":
        check_number(canonical_number(value), observed["attributes"])
    elif value not in {"on", "off"}:
        raise Refusal(INVALID_VALUE)


def matches(domain: str, value: str, observed: dict) -> bool:
    """Exact readback: string for select/switch, numeric for number."""
    if domain == "number":
        current = state_decimal(observed["state"])
        return current is not None and current == Decimal(value)
    return observed["state"] == value


def verified(domain: str, value: str, observed: dict, baseline: dict) -> bool:
    """§5.9: requested value, unchanged identity and (number) unchanged unit/bounds."""
    if not matches(domain, value, observed) or identity(observed) != identity(baseline):
        return False
    if domain == "number":
        keys = ("min", "max", "step", "unit_of_measurement")
        return all(observed["attributes"].get(key) == baseline["attributes"].get(key) for key in keys)
    return True


def target_kind(observed: dict) -> list:
    registry = observed["registry"]
    return [observed["entity_id"].split(".", 1)[0], registry["platform"], registry["translation_key"]]


def disclosures(observed: dict) -> list[dict]:
    """The six §5.6 disclosures, on every result. Disclosure, never refusal."""
    attributes = observed.get("attributes", {}) if observed else {}
    number = observed is not None and observed["entity_id"].startswith("number.")
    basis = ({"value": observed["state"], **{key: attributes.get(key) for key in (
        "unit_of_measurement", "min", "max", "step")}} if number else None)
    feedback = ("Home Assistant state is the integration's report, not physical observation "
                "(physical_feedback_verified is false).")
    if observed is not None and observed.get("assumed_state") is True:
        feedback += (" This entity reports assumed_state, so readback proves only Home "
                     "Assistant's optimistic state.")
    return [
        {"kind": "persistence", "text": (
            "This configuration setting may persist outside Home Assistant (on the device or in "
            "its integration), so a Home Assistant restart or removing the device may not undo "
            "it; the configuration category alone does not prove where it is stored.")},
        {"kind": "outage_restart_recovery", "text": (
            "Settings such as power-on behavior decide what the device does when power returns "
            "after an outage, a breaker trip or a reboot: a load set to off stays off (for "
            "example a freezer, sump pump or network equipment), and on or previous may switch "
            "a load on unattended.")},
        {"kind": "physical_access", "text": (
            "Settings such as child lock change whether the device's own buttons work. Locking "
            "can block manual control in an emergency; unlocking can allow unintended manual "
            "control.")},
        {"kind": "sensing_behavior", "text": (
            "Settings such as sensitivity change when a sensor reports motion, presence, leaks, "
            "contact or alarm conditions, and so what automations, lights or alarms do. Lower "
            "sensitivity can miss events; higher sensitivity can cause false triggers."),
         "basis": basis},
        {"kind": "feedback_limits", "text": feedback},
        {"kind": "integration_semantics", "text": (
            "Option labels and numeric scales are integration-defined. The result shows the raw "
            "option or number sent to the device, with the friendly name when available.")},
    ]
