"""Closed helper value contract (HAMCP-311). No generic services or data.

Each helper domain has a fixed allowlist of services, each with a fixed data
shape. Values are validated twice: statically here, then against the live
state and attributes before dispatch. Semantics follow Home Assistant Core
2026.10.3 (``homeassistant/components/<domain>/__init__.py``); see
``docs/TYPED_HELPER_CONTROL.md`` for the per-service evidence.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime
from decimal import Decimal, InvalidOperation, localcontext
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from ..errors import ErrorCode
from ..fan.contracts import OPERATION_PATTERN, digest, FanRefusal

PROVIDER = "upstream_typed_helper_value"
FAMILY = "helper_value"
_SOURCES = {
    "8.6.0": ("ha-mcp-v8.6.0-7426fb16", "fc54437a804858732e4bc927add98e202d879a09"),
    "8.4.3": ("ha-mcp-v8.4.3-d5cea47a", "eac7a3aa7063432e9af17e7d7726040e909c7b8f"),
    "8.5.0": ("ha-mcp-v8.5.0-e1538bcd", "311d6dc273fb4e9a5b8cde0de15f69472a64fe44"),
    # HAMCP-319: exact reviewed registry entry ha-mcp-v9.1.0-4259f247.
    "9.1.0": ("ha-mcp-v9.1.0-4259f247", "d2e3f4b0647d815fb32d530fed15a6a99b1d4b33"),
}
HELPER_SEMANTIC_CONTRACTS = {version: "ha-mcp-" + version + "-single-helper-value-v1"
                             for version in _SOURCES}
# Signed-only family: there is no historical (unsigned) contract to preserve,
# so the provider's third release element is the semantic contract itself.
HELPER_RELEASES = {version: (*source, HELPER_SEMANTIC_CONTRACTS[version])
                   for version, source in _SOURCES.items()}
HELPER_CONTRACTS = tuple(HELPER_SEMANTIC_CONTRACTS.values())

HELPER_ACTIONS = {
    "input_boolean": ("turn_on", "turn_off"),
    "input_select": ("select_option",),
    "input_number": ("set_value",),
    "input_text": ("set_value",),
    "input_datetime": ("set_datetime",),
    "input_button": ("press",),
    "counter": ("increment", "decrement", "reset", "set_value"),
    "timer": ("start", "pause", "cancel", "finish"),
}
ACTIONS = tuple(sorted({action for actions in HELPER_ACTIONS.values() for action in actions}))
ENTITY_PATTERN = r"^(" + "|".join(HELPER_ACTIONS) + r")\.[a-z0-9_]{1,120}$"
# Actions that set a value: an already-current value is ``no_change``.
# Press, increment/decrement and timer actions are transitions and never are.
VALUE_ACTIONS = frozenset({
    ("input_boolean", "turn_on"), ("input_boolean", "turn_off"),
    ("input_select", "select_option"), ("input_number", "set_value"),
    ("input_text", "set_value"), ("input_datetime", "set_datetime"),
    ("counter", "reset"), ("counter", "set_value"),
})
VALUE_REQUIRED = frozenset({
    ("input_select", "select_option"), ("input_number", "set_value"),
    ("input_text", "set_value"), ("input_datetime", "set_datetime"),
    ("counter", "set_value"),
})
VALUE_OPTIONAL = frozenset({("timer", "start")})
# Attributes read from the live state; all other attributes are never requested.
ATTRIBUTE_KEYS = {
    "input_boolean": (),
    "input_select": ("options",),
    "input_number": ("min", "max", "step"),
    "input_text": ("min", "max", "pattern", "mode"),
    "input_datetime": ("has_date", "has_time"),
    "input_button": (),
    "counter": ("initial", "step", "minimum", "maximum"),
    "timer": ("duration", "remaining", "finishes_at", "last_transition"),
}
MAX_TEXT = 255  # Core MAX_LENGTH_STATE_STATE
MAX_OPTIONS = 256
MAX_PATTERN = 255
COUNTER_LIMIT = 2 ** 53
# The largest off-step error accepted, as a fraction of ONE step: it absorbs
# binary float noise (0.1 * 3) and never scales with the value's magnitude.
STEP_ALLOWANCE = Decimal("1e-9")
DATE_RE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
TIME_RE = r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
DURATION_RE = re.compile(r"([0-9]{1,3}):([0-5][0-9]):([0-5][0-9])")
# Authority or provider absence, not a request fault: no dispatch occurred.
UNAVAILABLE_REFUSALS = frozenset({
    "helper_core_authority_unavailable", "helper_core_authority_retired",
    "helper_provider_unconfigured", "helper_provider_authority_retired",
    "helper_provider_authority_unavailable", "helper_provider_authority_denied",
    "helper_service_unavailable", "helper_runtime_unavailable",
})
STAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]{8,15}(?:\+00:00|Z)?")


class HelperRefusal(FanRefusal):
    """Fixed local categories only; never upstream exception text."""

    def __init__(self, category):
        if isinstance(category, str) and category.startswith("fan_"):
            category = "helper_" + category[4:]
        super().__init__(category)
        if category in UNAVAILABLE_REFUSALS:
            self.code = ErrorCode.PROVIDER_UNAVAILABLE
        status = "provider_unavailable" if self.code == ErrorCode.PROVIDER_UNAVAILABLE else "refused"
        self.details = {**self.details, "status": status}


class HelperRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    entity_id: str = Field(pattern=ENTITY_PATTERN)
    action: Literal[ACTIONS]  # type: ignore[valid-type]
    operation_id: str = Field(pattern=OPERATION_PATTERN)
    value: StrictStr | StrictInt | StrictFloat | None = None
    acknowledge_safety_critical: StrictBool = False

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def key(self) -> tuple[str, str]:
        return self.domain, self.action

    def checked(self) -> HelperRequest:
        if self.action not in HELPER_ACTIONS[self.domain]:
            raise HelperRefusal("helper_action_not_allowed")
        if self.value is None:
            if self.key in VALUE_REQUIRED:
                raise HelperRefusal("helper_value_required")
            return self
        if self.key not in VALUE_REQUIRED | VALUE_OPTIONAL:
            raise HelperRefusal("helper_value_not_allowed")
        value = self.value
        if self.domain in {"input_select", "input_text"}:
            # An empty value is checked against the live options or length
            # and pattern, never refused here.
            if not isinstance(value, str) or len(value) > MAX_TEXT:
                raise HelperRefusal("helper_value_invalid")
        elif self.domain == "input_number":
            if isinstance(value, str) or not math.isfinite(value):
                raise HelperRefusal("helper_value_invalid")
        elif self.domain == "counter":
            if not isinstance(value, int) or abs(value) > COUNTER_LIMIT:
                raise HelperRefusal("helper_value_invalid")
        elif self.domain == "input_datetime":
            datetime_parts(value)
        elif self.domain == "timer":
            duration_seconds(value)
        return self

    @property
    def task_id(self) -> str:
        return digest({"helper_value_operation_id": self.operation_id})[:32]

    def service_data(self) -> dict:
        self.checked()
        if self.value is None:
            return {}
        if self.domain == "input_select":
            return {"option": self.value}
        if self.domain == "input_datetime":
            day, clock = datetime_parts(self.value)
            return {**({"date": day} if day else {}), **({"time": clock} if clock else {})}
        if self.domain == "timer":
            return {"duration": canonical_duration(self.value)}
        return {"value": self.value}

    def arguments(self) -> dict:
        return {"domain": self.domain, "service": self.action,
                "entity_id": self.entity_id, "data": self.service_data(), "wait": False,
                "return_response": False, "verbose": False}

    def is_fresh(self, now) -> bool:
        age = now.timestamp() - int(self.operation_id.split("-", 1)[0])
        return -30 <= age <= 300


def datetime_parts(value) -> tuple[str | None, str | None]:
    """Exact Core state format: date, time, or ``date time`` (no zone)."""
    if not isinstance(value, str):
        raise HelperRefusal("helper_datetime_invalid")
    match = re.fullmatch(r"(?:(" + DATE_RE + r")(?: (" + TIME_RE + r"))?|(" + TIME_RE + r"))", value)
    if match is None:
        raise HelperRefusal("helper_datetime_invalid")
    day, clock = match.group(1), match.group(2) or match.group(3)
    try:
        if day:
            date.fromisoformat(day)
        if clock:
            dtime.fromisoformat(clock)
    except ValueError:
        raise HelperRefusal("helper_datetime_invalid") from None
    return day, clock


def duration_seconds(value) -> int:
    match = DURATION_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise HelperRefusal("helper_duration_invalid")
    hours, minutes, seconds = (int(part) for part in match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        raise HelperRefusal("helper_duration_invalid")
    return total


def canonical_duration(value) -> str:
    """Core ``_format_timedelta``: ``H:MM:SS``."""
    total = duration_seconds(value)
    return f"{total // 3600}:{total % 3600 // 60:02}:{total % 60:02}"


def _number(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if integer and not isinstance(value, int):
        return None
    if not math.isfinite(value):
        return None
    return value


def checked_state(raw, entity_id):
    """Bounded projection of only the attributes this contract needs."""
    if not isinstance(raw, dict) or raw.get("entity_id") != entity_id:
        raise HelperRefusal("helper_target_identity_unproven")
    domain = entity_id.split(".", 1)[0]
    state = raw.get("state")
    if not isinstance(state, str) or len(state) > MAX_TEXT or state in {"unavailable"}:
        raise HelperRefusal("helper_target_state_unavailable")
    # Only input_text and input_select can hold an empty value, and only when
    # the live configuration allows it (``_check_domain_state``). ``unknown``
    # refuses per domain where it is not a value (boolean, counter, timer); a
    # never-pressed input_button is ``unknown`` and may be pressed.
    if not state and domain not in {"input_text", "input_select"}:
        raise HelperRefusal("helper_target_state_unavailable")
    stamp = raw.get("last_updated")
    if not isinstance(stamp, str) or not 1 <= len(stamp) <= 64:
        raise HelperRefusal("helper_state_revision_unproven")
    attrs = raw.get("attributes", {})
    if not isinstance(attrs, dict):
        raise HelperRefusal("helper_attributes_unproven")
    out = {"entity_id": entity_id, "state": state, "last_updated": stamp}
    picked = {}
    for key in ATTRIBUTE_KEYS[domain]:
        value = attrs.get(key)
        if value is None:
            picked[key] = None
        elif isinstance(value, (bool, int, float)) and not (isinstance(value, float) and not math.isfinite(value)):
            picked[key] = value
        elif isinstance(value, str) and len(value) <= MAX_TEXT:
            picked[key] = value
        elif (key == "options" and isinstance(value, list) and len(value) <= MAX_OPTIONS
              and all(isinstance(item, str) and len(item) <= MAX_TEXT for item in value)):
            picked[key] = list(value)
        else:
            raise HelperRefusal("helper_attributes_unproven")
    if picked:
        out["attributes"] = picked
    _check_domain_state(domain, out)
    return out


def _check_domain_state(domain, state):
    value, attrs = state["state"], state.get("attributes", {})
    if domain == "input_boolean" and value not in {"on", "off"}:
        raise HelperRefusal("helper_target_state_unavailable")
    if domain == "input_select" and not isinstance(attrs.get("options"), list):
        raise HelperRefusal("helper_attributes_unproven")
    if domain == "input_select" and not value and "" not in attrs["options"]:
        raise HelperRefusal("helper_target_state_unavailable")
    if domain == "input_number":
        if any(_number(attrs.get(key)) is None for key in ("min", "max", "step")):
            raise HelperRefusal("helper_attributes_unproven")
        if attrs["step"] <= 0 or attrs["min"] > attrs["max"]:
            raise HelperRefusal("helper_attributes_unproven")
    if domain == "input_text":
        if any(_number(attrs.get(key), integer=True) is None for key in ("min", "max")):
            raise HelperRefusal("helper_attributes_unproven")
        if attrs.get("pattern") is not None and not isinstance(attrs["pattern"], str):
            raise HelperRefusal("helper_attributes_unproven")
        if not value:
            try:
                check_text(attrs, value)
            except HelperRefusal:
                raise HelperRefusal("helper_target_state_unavailable") from None
    if domain == "input_datetime":
        if type(attrs.get("has_date")) is not bool or type(attrs.get("has_time")) is not bool:
            raise HelperRefusal("helper_attributes_unproven")
    if domain == "counter":
        if _number(attrs.get("step"), integer=True) is None or attrs["step"] <= 0:
            raise HelperRefusal("helper_attributes_unproven")
        if _number(attrs.get("initial"), integer=True) is None:
            raise HelperRefusal("helper_attributes_unproven")
        for key in ("minimum", "maximum"):
            if attrs.get(key) is not None and _number(attrs[key], integer=True) is None:
                raise HelperRefusal("helper_attributes_unproven")
        if not re.fullmatch(r"-?[0-9]{1,17}", value):
            raise HelperRefusal("helper_target_state_unavailable")
    if domain == "timer" and value not in {"idle", "active", "paused"}:
        raise HelperRefusal("helper_target_state_unavailable")


def _counter_next(state, delta=None, absolute=None):
    attrs = state["attributes"]
    value = int(state["state"]) + delta if absolute is None else absolute
    if attrs.get("minimum") is not None:
        value = max(attrs["minimum"], value)
    if attrs.get("maximum") is not None:
        value = min(attrs["maximum"], value)
    return value


def check_text(attrs, value) -> None:
    """Live input_text length and full-match pattern, empty values included."""
    if not attrs["min"] <= len(value) <= attrs["max"]:
        raise HelperRefusal("helper_value_length_out_of_range")
    pattern = attrs.get("pattern")
    if pattern:
        if len(pattern) > MAX_PATTERN:
            raise HelperRefusal("helper_pattern_unsupported")
        try:
            compiled = re.compile(pattern)
        except (re.error, RecursionError):
            raise HelperRefusal("helper_pattern_unsupported") from None
        if compiled.fullmatch(value) is None:
            raise HelperRefusal("helper_value_pattern_mismatch")


def _decimal(value) -> Decimal:
    # repr() is the shortest decimal that round-trips the float: the value the
    # client or Home Assistant wrote, not its binary approximation.
    return Decimal(value) if isinstance(value, int) else Decimal(repr(value))


def on_step(value, low, step) -> bool:
    """Exact decimal check that ``value - low`` is a whole number of steps.

    The accepted error is at most ``STEP_ALLOWANCE`` of one step at every
    magnitude, so a half step is refused however large the value is.
    """
    with localcontext() as context:
        context.prec = 1000  # exact for any finite float operands
        try:
            unit = _decimal(step)
            remainder = (_decimal(value) - _decimal(low)) % unit
        except InvalidOperation:
            return False
        return min(abs(remainder), unit - abs(remainder)) <= unit * STEP_ALLOWANCE


def check_live(request: HelperRequest, state: dict) -> None:
    """Refuse a value or action the live helper configuration cannot accept."""
    domain, action, value = request.domain, request.action, request.value
    attrs = state.get("attributes", {})
    if domain == "input_select":
        if value not in attrs["options"]:
            raise HelperRefusal("helper_option_not_available")
    elif domain == "input_number":
        low, high, step = attrs["min"], attrs["max"], attrs["step"]
        if not low <= value <= high:
            raise HelperRefusal("helper_value_out_of_range")
        if not on_step(value, low, step):
            raise HelperRefusal("helper_value_not_on_step")
    elif domain == "input_text":
        check_text(attrs, value)
    elif domain == "input_datetime":
        day, clock = datetime_parts(value)
        if bool(day) != attrs["has_date"] or bool(clock) != attrs["has_time"]:
            raise HelperRefusal("helper_datetime_fields_mismatch")
    elif domain == "counter":
        if action == "set_value":
            if ((attrs.get("minimum") is not None and value < attrs["minimum"])
                    or (attrs.get("maximum") is not None and value > attrs["maximum"])):
                raise HelperRefusal("helper_value_out_of_range")
            if value % attrs["step"] != 0:
                raise HelperRefusal("helper_value_not_on_step")
        elif action in {"increment", "decrement"}:
            delta = attrs["step"] if action == "increment" else -attrs["step"]
            if _counter_next(state, delta) == int(state["state"]):
                # Core clamps silently: no transition could be verified.
                raise HelperRefusal("helper_counter_at_limit")
    elif domain == "timer":
        if action == "pause" and state["state"] != "active":
            raise HelperRefusal("helper_timer_not_active")
        if action in {"cancel", "finish"} and state["state"] not in {"active", "paused"}:
            raise HelperRefusal("helper_timer_not_running")


def expected_number_state(value) -> float:
    # Core stores float(value) and renders it with 15 significant digits.
    return float(f"{float(value):.15}")


def desired(request: HelperRequest, state: dict, baseline: dict | None = None) -> bool:
    """Whether ``state`` shows the requested result.

    Value actions compare the exact value. Transition actions need the
    pre-dispatch ``baseline``; without one (the no-op check) they are never
    already reached.
    """
    domain, action, value = request.domain, request.action, request.value
    current = state["state"]
    if domain == "input_boolean":
        return current == ("on" if action == "turn_on" else "off")
    if domain in {"input_select", "input_text"}:
        return current == value
    if domain == "input_number":
        try:
            return float(current) == expected_number_state(value)
        except ValueError:
            return False
    if domain == "input_datetime":
        day, clock = datetime_parts(value)
        return current == " ".join(part for part in (day, clock) if part)
    if domain == "counter" and action in {"reset", "set_value"}:
        if not re.fullmatch(r"-?[0-9]{1,17}", current):
            return False
        target = (_counter_next(state, absolute=state["attributes"]["initial"])
                  if action == "reset" else value)
        if action == "reset" and baseline is not None:
            target = _counter_next(baseline, absolute=baseline["attributes"]["initial"])
        return int(current) == target
    if baseline is None:
        return False
    if domain == "input_button":
        return current != baseline["state"] and STAMP_RE.fullmatch(current) is not None
    if domain == "counter":
        delta = baseline["attributes"]["step"] if action == "increment" else -baseline["attributes"]["step"]
        return re.fullmatch(r"-?[0-9]{1,17}", current) is not None and int(current) == _counter_next(baseline, delta)
    if domain == "timer":
        attrs, before = state.get("attributes", {}), baseline.get("attributes", {})
        if action == "start":
            if current != "active" or not attrs.get("finishes_at"):
                return False
            if value is not None and attrs.get("duration") != canonical_duration(value):
                return False
            return baseline["state"] != "active" or attrs.get("finishes_at") != before.get("finishes_at")
        if action == "pause":
            return current == "paused"
        return current == "idle" and attrs.get("last_transition") == ("cancelled" if action == "cancel" else "finished")
    return False


def verification_basis(request: HelperRequest) -> str:
    """What the readback proves, per action (documented in the receipt)."""
    domain, action = request.key
    if domain == "input_button":
        return "last_pressed_timestamp_changed"
    if domain == "counter" and action in {"increment", "decrement"}:
        return "counter_value_equals_baseline_plus_or_minus_step"
    if domain == "counter" and action == "reset":
        return "counter_value_equals_initial"
    if domain == "timer":
        return {"start": "timer_active_with_new_finishes_at",
                "pause": "timer_paused",
                "cancel": "timer_idle_last_transition_cancelled",
                "finish": "timer_idle_last_transition_finished"}[action]
    return "exact_state_value"
