"""Closed governed options edit of one config-entry helper (HAMCP-232).

Home Assistant Core has no read of a config entry's ``options``.  The only
reviewed way to see them is to open the entry's options flow, read every
field's ``description.suggested_value`` and abort the flow (owner decision
OD-1, 2026-10-26).  That read exists only inside this write family, only for
a plan's exact entries, and is never exposed as a general read.

Finishing an options flow replaces the entry's options wholesale
(``helpers/schema_config_entry_flow.py:204-224``): ``values.update(input)``,
then every Optional key of the step schema absent from the input is removed
unless its selector is ``read_only``, and an omitted Optional with a default
is reset to that default.  Engineering therefore never forwards a partial
input: it rebuilds the complete step dict from the read baseline, applies
the requested ``set``/``clear`` against a closed per-type catalog that mirrors
Core 2026.10.3, and predicts Core's coercion exactly.

The only write is one ``POST /config/config_entries/options/flow/{flow_id}``
per child.  Every other request is a read or an abort of a flow this gateway
opened itself.
"""

from __future__ import annotations

import asyncio
import copy
import math
import re
from dataclasses import dataclass
from typing import Any

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import ImmutableSandboxedEnvironment

from ..clients.paths import rest_path
from ..entity_ids import validate_entity_id
from ..errors import AuthorizationError, HomeAssistantApiError
from ..request_context import mark_provider_send
from .normalize import stable_hash

PROFILE = "core.config_entry_helper_options_operation"
PROVIDER = "home_assistant_config_entry_helper_options"
PROVIDER_CONTRACT = "compiled-core-config-entry-helper-options-operation-v1"
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
RESOURCE_TYPE = "config_entry_helper_options"
ACTION = "update"
CAPABILITY_IDENTITY = "update_config_entry_helper_options_configuration"
INTENT_KEY = "config_entry_helper_options"
INTENT_MODEL = "config_entry_helper_options_intent_v1"
BASELINE_MODEL = "config_entry_helper_options_baseline_v1"
CONSUMER_EVIDENCE_MODEL = "config_entry_helper_options_consumer_evidence_v1"
DOMAINS = ("filter", "statistics", "template")
TEMPLATE_TYPES = ("binary_sensor", "sensor")
ENTITY_DOMAINS = ("binary_sensor", "sensor")
SECTION = "additional_options"

OPTIONS_FLOW_ROUTE = "/config/config_entries/options/flow"
# The hashed provider payload names the reviewed submit operation, not a
# route template: the submit itself is sent only through ``rest_path``.
OPTIONS_FLOW_SUBMIT_OPERATION = "config_entry_options_flow_submit"
# The closed websocket vocabulary.  Everything else is refused before I/O.
READ_COMMANDS = (
    "config/entity_registry/get",
    "config/entity_registry/list",
    "config_entries/get_single",
)
# Never sent by this family (asserted by the dormant lane and the tests).
# The config-flow start is a CI-harness-only route (HAMCP-200), so the lane
# adds it to this list instead of naming it in the runtime package.
NEVER_SENT = (
    "DELETE /config/config_entries/entry/{}",
    "POST /config/config_entries/entry/{}/reload",
    "config_entries/update",
    "config_entries/disable",
    "config_entries/subentries/list",
    "call_service",
)

FLOW_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
ENTRY_ID_PATTERN = re.compile(r"[0-9A-Za-z]{1,64}\Z")
MAX_TEMPLATE_LENGTH = 4_096
MAX_TEXT_LENGTH = 64
MAX_DURATION_PART = 1_000_000
MAX_NUMBER = 1_000_000_000
# Year ~5138; any real ``modified_at`` is far below it.
MAX_TIMESTAMP = 1e11
MAX_REGISTRY_LIST_ITEMS = 5_000
MAX_ENTRY_ENTITIES = 16
MAX_SUGGESTED_TEXT = 8_192
# Core reloads in the background after finishing; readback polls this bound.
RELOAD_POLL_ATTEMPTS = 30
RELOAD_POLL_INTERVAL_SECONDS = 1.0
EXPECTED_EFFECT = "config_entry_helper_options_updated"
SUBMIT_REJECTED_REASON = "options_flow_submit_rejected"
ENTRY_UNHEALTHY = "applied_entry_unhealthy"
SUBMIT_UNCERTAIN = "options_submit_uncertain"
# One flow abort (DELETE) waits at most this long, also after a cancellation.
ABORT_TIMEOUT_SECONDS = 10.0
# A failed open with no received answer may still have created a flow whose
# id is unknown, so it cannot be aborted: Core holds it in memory until it
# times out or Core restarts.  Disclosed, never treated as "no flow".
FLOW_OPEN_UNCONFIRMED = "options_flow_open_unconfirmed"


def open_unconfirmed(exc: BaseException) -> bool:
    """True when a failed flow open may have created a flow (id unknown)."""

    if isinstance(
        exc, (AuthorizationError, HelperOptionsContractError, HelperOptionsProviderError)
    ):
        return False
    details = getattr(exc, "details", None)
    return not (
        isinstance(details, dict) and details.get("provider_response_received") is True
    )
#: Confirmed non-application categories of a received submit response.
SUBMIT_REJECTION_CATEGORIES = (
    "options_flow_aborted",
    "options_flow_validation_error",
    SUBMIT_REJECTED_REASON,
)
APPLIED_STATE_MODEL = "config_entry_helper_options_applied_state_v1"
STATE_CLASSES = ("measurement", "measurement_angle", "total", "total_increasing")
DEVICE_CLASS_PATTERN = re.compile(r"[a-z0-9_]{1,64}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_NO_DEFAULT = "__no_default__"
_TEMPLATE_ENVIRONMENT = ImmutableSandboxedEnvironment(
    extensions=("jinja2.ext.loopcontrols", "jinja2.ext.do")
)

# Value kinds.  "int" is a NumberSelector Core coerces back to int in the
# filter ``validate_options``; "float" is a NumberSelector Core stores as the
# selector's float; durations are stored exactly as submitted.
KIND_INT = "int"
KIND_FLOAT = "float"
KIND_BOOL = "bool"
KIND_DURATION = "duration"
KIND_TEMPLATE = "template"
KIND_UNIT = "unit"
KIND_DEVICE_CLASS = "device_class"
KIND_STATE_CLASS = "state_class"
KIND_PINNED = "pinned"


class HelperOptionsContractError(ValueError):
    """A value or response is outside the closed, reviewed contract."""

    def __init__(self, category: str, **details: Any) -> None:
        super().__init__(category)
        self.category = category
        self.details = details


class HelperOptionsProviderError(RuntimeError):
    """A closed read returned a response outside its contract."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class FieldSpec:
    """One options-flow field, mirrored from Core 2026.10.3 source."""

    name: str
    kind: str
    required: bool = False
    default: Any = _NO_DEFAULT
    minimum: float | None = None
    maximum: float | None = None
    days: bool = False

    @property
    def pinned(self) -> bool:
        return self.kind == KIND_PINNED

    @property
    def has_default(self) -> bool:
        return self.default != _NO_DEFAULT

    @property
    def clearable(self) -> bool:
        return not self.pinned and not self.required and not self.has_default

    @property
    def section(self) -> str | None:
        return self.name.split(".", 1)[0] if "." in self.name else None

    @property
    def leaf(self) -> str:
        return self.name.split(".", 1)[-1]


def _filter_base() -> tuple[FieldSpec, ...]:
    return (
        FieldSpec("entity_id", KIND_PINNED),
        FieldSpec("filter", KIND_PINNED),
        FieldSpec("precision", KIND_INT, default=2, minimum=0),
    )


def _template_tail(*section: FieldSpec) -> tuple[FieldSpec, ...]:
    return (
        FieldSpec("device_id", KIND_PINNED),
        FieldSpec(f"{SECTION}.availability", KIND_TEMPLATE),
        *section,
    )


# (domain, step_id) -> fields.  ``filter/config_flow.py:105-192``,
# ``statistics/config_flow.py:107-136``, ``template/config_flow.py:162-473``.
STEP_CATALOG: dict[tuple[str, str], tuple[FieldSpec, ...]] = {
    ("filter", "lowpass"): (
        FieldSpec("window_size", KIND_INT, default=1, minimum=0),
        FieldSpec("time_constant", KIND_INT, default=10, minimum=0),
        *_filter_base(),
    ),
    ("filter", "outlier"): (
        FieldSpec("window_size", KIND_INT, default=1, minimum=0),
        FieldSpec("radius", KIND_FLOAT, default=2.0, minimum=0),
        *_filter_base(),
    ),
    ("filter", "range"): (
        FieldSpec("lower_bound", KIND_FLOAT, minimum=0),
        FieldSpec("upper_bound", KIND_FLOAT, minimum=0),
        *_filter_base(),
    ),
    ("filter", "time_simple_moving_average"): (
        # The only option is "last"; it is pinned rather than editable.
        FieldSpec("type", KIND_PINNED, default="last"),
        FieldSpec("window_size", KIND_DURATION, required=True),
        *_filter_base(),
    ),
    ("filter", "throttle"): (
        FieldSpec("window_size", KIND_INT, default=1, minimum=0),
        *_filter_base(),
    ),
    ("filter", "time_throttle"): (
        FieldSpec("window_size", KIND_DURATION, required=True),
        *_filter_base(),
    ),
    ("statistics", "init"): (
        FieldSpec("entity_id", KIND_PINNED),
        FieldSpec("state_characteristic", KIND_PINNED),
        FieldSpec("sampling_size", KIND_FLOAT, minimum=1),
        FieldSpec("max_age", KIND_DURATION),
        FieldSpec("keep_last_sample", KIND_BOOL, default=False),
        FieldSpec("percentile", KIND_FLOAT, default=50.0, minimum=1, maximum=99),
        FieldSpec("precision", KIND_FLOAT, default=2.0, minimum=0),
    ),
    ("template", "sensor"): (
        FieldSpec("state", KIND_TEMPLATE, required=True),
        FieldSpec("unit_of_measurement", KIND_UNIT),
        FieldSpec("device_class", KIND_DEVICE_CLASS),
        FieldSpec("state_class", KIND_STATE_CLASS),
        *_template_tail(),
    ),
    ("template", "binary_sensor"): (
        FieldSpec("state", KIND_TEMPLATE, required=True),
        FieldSpec("device_class", KIND_DEVICE_CLASS),
        *_template_tail(
            FieldSpec(f"{SECTION}.delay_on", KIND_DURATION, days=True),
            FieldSpec(f"{SECTION}.delay_off", KIND_DURATION, days=True),
        ),
    ),
}
# Every field name a request may name (the closed union of the catalog).
EDITABLE_FIELD_NAMES = frozenset(
    spec.name
    for fields in STEP_CATALOG.values()
    for spec in fields
    if not spec.pinned
)
# Statistics ``validate_options`` (``statistics/config_flow.py:78-96``).
STATISTICS_RULES = (
    "missing_max_age_or_sampling_size",
    "missing_keep_last_sample",
)


def step_fields(domain: str, step_id: str) -> tuple[FieldSpec, ...]:
    fields = STEP_CATALOG.get((domain, step_id))
    if fields is None:
        raise HelperOptionsContractError("options_step_not_supported", step_id=step_id)
    return fields


def field_spec(domain: str, step_id: str, name: str) -> FieldSpec | None:
    for spec in step_fields(domain, step_id):
        if spec.name == name:
            return spec
    return None


def _number(value: Any) -> bool:
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and -MAX_NUMBER <= value <= MAX_NUMBER
    )


def _timestamp(value: Any) -> bool:
    """A Core ``modified_at``: a finite, non-negative POSIX timestamp.

    Separate from ``_number`` (the option-value bound): a current timestamp
    is ~1.7e9, above the option-value limit.
    """

    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and 0 <= value <= MAX_TIMESTAMP
    )


def _text(value: Any, *, limit: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= limit
        and value == value.strip()
        and not _CONTROL.search(value)
    )


def _duration(value: Any, *, days: bool) -> bool:
    keys = {"hours", "minutes", "seconds"} | ({"days"} if days else set())
    return (
        isinstance(value, dict)
        and 1 <= len(value) <= len(keys)
        and set(value) <= keys
        and all(
            type(part) is int and 0 <= part <= MAX_DURATION_PART
            for part in value.values()
        )
    )


def template_parses(source: str) -> bool:
    """Parse only; Engineering never renders or executes the template."""

    try:
        _TEMPLATE_ENVIRONMENT.parse(source)
    except TemplateSyntaxError:
        return False
    except Exception:  # noqa: BLE001 - any parser failure is a refusal
        return False
    return True


def coerce_request_value(spec: FieldSpec, value: Any) -> Any:
    """Validate one requested value and return what Core will store.

    Raises ``HelperOptionsContractError("options_value_invalid")`` for any
    value outside the field's compiled type.  Pinned fields are never
    accepted here.
    """

    def refuse() -> HelperOptionsContractError:
        return HelperOptionsContractError("options_value_invalid", field=spec.name)

    if spec.pinned:
        raise HelperOptionsContractError("options_field_pinned", field=spec.name)
    if spec.kind in (KIND_INT, KIND_FLOAT):
        if not _number(value):
            raise refuse()
        if spec.kind == KIND_INT and (type(value) is not int):
            raise refuse()
        if spec.minimum is not None and value < spec.minimum:
            raise refuse()
        if spec.maximum is not None and value > spec.maximum:
            raise refuse()
        return int(value) if spec.kind == KIND_INT else float(value)
    if spec.kind == KIND_BOOL:
        if type(value) is not bool:
            raise refuse()
        return value
    if spec.kind == KIND_DURATION:
        if not _duration(value, days=spec.days):
            raise refuse()
        return dict(value)
    if spec.kind == KIND_TEMPLATE:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > MAX_TEMPLATE_LENGTH
        ):
            raise refuse()
        if not template_parses(value):
            raise HelperOptionsContractError("options_template_unparseable", field=spec.name)
        return value
    if spec.kind == KIND_UNIT:
        if not _text(value, limit=MAX_TEXT_LENGTH):
            raise refuse()
        return value
    if spec.kind == KIND_DEVICE_CLASS:
        if not isinstance(value, str) or not DEVICE_CLASS_PATTERN.fullmatch(value):
            raise refuse()
        return value
    if spec.kind == KIND_STATE_CLASS:
        if value not in STATE_CLASSES:
            raise refuse()
        return value
    raise refuse()


def core_default(spec: FieldSpec) -> Any:
    """The value Core injects for an omitted Optional field with a default."""

    if spec.kind == KIND_INT:
        return int(spec.default)
    if spec.kind == KIND_FLOAT:
        return float(spec.default)
    return copy.deepcopy(spec.default)


def canonical(value: Any) -> Any:
    """Compare as Core stores JSON: ints and floats by value, bools apart."""

    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if type(value) in (int, float):
        return ("number", float(value))
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return ("opaque", repr(value))


def canonical_copy(value: Any) -> Any:
    """A deep copy for binding; equality is decided by ``canonical``."""

    return copy.deepcopy(value)


def values_equal(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def visible_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return set(left) == set(right) and all(
        values_equal(left[name], right[name]) for name in left
    )


def fingerprint(model: str, value: Any) -> str:
    return stable_hash({"model": model, "value": canonical(value)})


# --- form parsing -----------------------------------------------------------


def _suggested(field: dict[str, Any]) -> tuple[bool, Any]:
    description = field.get("description")
    if description is None:
        return False, None
    if not isinstance(description, dict) or set(description) - {"suggested_value"}:
        raise HelperOptionsProviderError("options_form_malformed")
    if "suggested_value" not in description:
        return False, None
    value = description["suggested_value"]
    if isinstance(value, str) and len(value) > MAX_SUGGESTED_TEXT:
        raise HelperOptionsProviderError("options_form_malformed")
    return True, copy.deepcopy(value)


def _field_list_names(
    schema: Any, *, prefix: str = ""
) -> tuple[dict[str, bool], dict[str, Any]]:
    """Return ``{name: required}`` and the suggested values of one field list."""

    if not isinstance(schema, list) or len(schema) > 64:
        raise HelperOptionsProviderError("options_form_malformed")
    names: dict[str, bool] = {}
    values: dict[str, Any] = {}
    for field in schema:
        if not isinstance(field, dict):
            raise HelperOptionsProviderError("options_form_malformed")
        name = field.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z_]{1,64}", name):
            raise HelperOptionsProviderError("options_form_malformed")
        required = field.get("required") is True
        if not required and field.get("optional") is not True:
            raise HelperOptionsProviderError("options_form_malformed")
        if field.get("type") == "expandable":
            if prefix:
                raise HelperOptionsProviderError("options_form_malformed")
            nested, nested_values = _field_list_names(
                field.get("schema"), prefix=f"{name}."
            )
            names.update(nested)
            values.update(nested_values)
            continue
        full = f"{prefix}{name}"
        if full in names:
            raise HelperOptionsProviderError("options_form_malformed")
        names[full] = required
        present, value = _suggested(field)
        if present:
            values[full] = value
    return names, values


def parse_form(
    response: Any, *, domain: str, expected_step_id: str | None
) -> tuple[str, str, dict[str, Any]]:
    """Return ``(flow_id, step_id, visible)`` from an options-flow form.

    The field names and required-ness must equal the compiled catalog for the
    step exactly; an unknown or missing field is schema drift.
    """

    if not isinstance(response, dict):
        raise HelperOptionsProviderError("options_form_malformed")
    flow_id = response.get("flow_id")
    if not isinstance(flow_id, str) or not FLOW_ID_PATTERN.fullmatch(flow_id):
        raise HelperOptionsProviderError("options_flow_id_malformed")
    if response.get("type") != "form":
        raise HelperOptionsContractError("options_flow_not_form", flow_id=flow_id)
    step_id = response.get("step_id")
    if not isinstance(step_id, str) or (domain, step_id) not in STEP_CATALOG:
        raise HelperOptionsContractError("options_step_not_supported", flow_id=flow_id)
    if expected_step_id is not None and step_id != expected_step_id:
        raise HelperOptionsContractError("options_schema_unrecognized", flow_id=flow_id)
    try:
        names, values = _field_list_names(response.get("data_schema"))
    except HelperOptionsProviderError:
        raise HelperOptionsContractError("options_schema_unrecognized", flow_id=flow_id) from None
    expected = {spec.name: spec.required for spec in step_fields(domain, step_id)}
    if names != expected:
        raise HelperOptionsContractError("options_schema_unrecognized", flow_id=flow_id)
    return flow_id, step_id, values


# --- proposal ---------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    """The exact step dict to submit and the visible options Core will store."""

    submit: dict[str, Any]
    proposed: dict[str, Any]
    requested: tuple[str, ...]
    normalization_changes: tuple[dict[str, Any], ...]


def nest(flat: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in sorted(flat):
        if "." in name:
            section, leaf = name.split(".", 1)
            result.setdefault(section, {})[leaf] = copy.deepcopy(flat[name])
        else:
            result[name] = copy.deepcopy(flat[name])
    return result


def build_proposal(
    domain: str,
    step_id: str,
    baseline: dict[str, Any],
    set_values: dict[str, Any],
    clear: tuple[str, ...],
) -> Proposal:
    """Emulate Core's options merge for the complete step dict.

    ``set_values`` are already validated and coerced.  The result submits
    every visible field (pinned fields at their baseline values), so Core
    removes only the cleared keys, and lists every unrequested difference
    (an injected default) separately.
    """

    fields = step_fields(domain, step_id)
    flat = {name: copy.deepcopy(value) for name, value in baseline.items()}
    for name, value in set_values.items():
        flat[name] = copy.deepcopy(value)
    for name in clear:
        flat.pop(name, None)
    proposed = dict(flat)
    normalization: list[dict[str, Any]] = []
    for spec in fields:
        if spec.name in proposed:
            continue
        if spec.required:
            raise HelperOptionsContractError("options_required_field_missing", field=spec.name)
        if spec.has_default and spec.section is None:
            value = core_default(spec)
            proposed[spec.name] = value
            normalization.append(
                {"field": spec.name, "baseline": None, "injected_default": canonical_out(value)}
            )
    if domain == "statistics":
        statistics_errors(proposed)
    submit = nest(flat)
    return Proposal(
        submit=submit,
        proposed=proposed,
        requested=tuple(sorted({*set_values, *clear})),
        normalization_changes=tuple(normalization),
    )


def canonical_out(value: Any) -> Any:
    return copy.deepcopy(value)


def statistics_errors(proposed: dict[str, Any]) -> None:
    """Refuse what Core's statistics ``validate_options`` would reject."""

    if proposed.get("sampling_size") is None and proposed.get("max_age") is None:
        raise HelperOptionsContractError("options_validation_would_fail",
                                         rule="missing_max_age_or_sampling_size")
    if proposed.get("keep_last_sample") is True and proposed.get("max_age") is None:
        raise HelperOptionsContractError("options_validation_would_fail",
                                         rule="missing_keep_last_sample")


# --- intent -----------------------------------------------------------------

INTENT_FIELDS = frozenset(
    {
        "model",
        "entity_ids",
        "entry_id",
        "domain",
        "step_id",
        "modified_at",
        "baseline",
        "proposed",
        "submit",
        "requested",
        "normalization_changes",
        "baseline_fingerprint",
        "proposed_fingerprint",
        "consumer_evidence_fingerprint",
        "dependency_reasons",
    }
)
MAX_DEPENDENCY_REASONS = 64
MAX_DEPENDENCY_REASON_LENGTH = 200


def _is_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def build_intent(
    *,
    entity_ids: list[str],
    entry_id: str,
    domain: str,
    step_id: str,
    modified_at: float,
    baseline: dict[str, Any],
    proposal: Proposal,
    consumer_evidence_fingerprint: str,
    dependency_reasons: list[str],
) -> dict[str, Any]:
    intent = {
        INTENT_KEY: {
            "model": INTENT_MODEL,
            "entity_ids": list(entity_ids),
            "entry_id": entry_id,
            "domain": domain,
            "step_id": step_id,
            "modified_at": modified_at,
            "baseline": copy.deepcopy(baseline),
            "proposed": copy.deepcopy(proposal.proposed),
            "submit": copy.deepcopy(proposal.submit),
            "requested": list(proposal.requested),
            "normalization_changes": [dict(item) for item in proposal.normalization_changes],
            "baseline_fingerprint": fingerprint(BASELINE_MODEL, baseline),
            "proposed_fingerprint": fingerprint(INTENT_MODEL, proposal.proposed),
            "consumer_evidence_fingerprint": consumer_evidence_fingerprint,
            "dependency_reasons": list(dependency_reasons),
        }
    }
    errors = intent_errors(entry_id, intent)
    if errors:
        raise ValueError("; ".join(errors))
    return intent


def intent_errors(target_id: str, proposed: Any) -> list[str]:
    """Validate an options intent exactly; anything else is refused."""

    errors: list[str] = []
    if not isinstance(target_id, str) or not ENTRY_ID_PATTERN.fullmatch(target_id):
        errors.append("options target_id must be an exact config entry id")
    if not isinstance(proposed, dict) or set(proposed) != {INTENT_KEY}:
        return [*errors, "options proposal must be exactly one options intent"]
    intent = proposed[INTENT_KEY]
    if not isinstance(intent, dict) or set(intent) != INTENT_FIELDS:
        return [*errors, "options intent fields are not the closed set"]
    if intent["model"] != INTENT_MODEL:
        errors.append("options intent model is unsupported")
    if intent["entry_id"] != target_id:
        errors.append("options intent entry_id does not match")
    domain, step_id = intent["domain"], intent["step_id"]
    if (domain, step_id) not in STEP_CATALOG:
        return [*errors, "options intent step is outside the catalog"]
    entity_ids = intent["entity_ids"]
    if (
        not isinstance(entity_ids, list)
        or not 1 <= len(entity_ids) <= MAX_ENTRY_ENTITIES
        or entity_ids != sorted(set(entity_ids))
    ):
        errors.append("options intent entity_ids are invalid")
    else:
        for entity_id in entity_ids:
            try:
                validate_entity_id(entity_id)
            except ValueError:
                errors.append("options intent entity_ids are invalid")
                break
            if entity_id.split(".", 1)[0] not in ENTITY_DOMAINS:
                errors.append("options intent entity_ids are invalid")
                break
    if not _timestamp(intent["modified_at"]):
        errors.append("options intent modified_at is invalid")
    names = {spec.name for spec in step_fields(domain, step_id)}
    for key in ("baseline", "proposed"):
        value = intent[key]
        if not isinstance(value, dict) or not set(value) <= names:
            errors.append(f"options intent {key} is outside the step")
    if not isinstance(intent["submit"], dict):
        errors.append("options intent submit is invalid")
    elif isinstance(intent["proposed"], dict):
        flat_submit = _flatten(intent["submit"])
        if flat_submit is None or not set(flat_submit) <= names:
            errors.append("options intent submit is outside the step")
        elif any(
            not values_equal(intent["proposed"].get(name), value)
            for name, value in flat_submit.items()
        ):
            errors.append("options intent submit does not match proposed")
    for spec in step_fields(domain, step_id):
        if spec.pinned and isinstance(intent["baseline"], dict) and isinstance(intent["proposed"], dict):
            if not values_equal(intent["baseline"].get(spec.name), intent["proposed"].get(spec.name)):
                errors.append("options intent changes a pinned field")
    requested = intent["requested"]
    if (
        not isinstance(requested, list)
        or not requested
        or requested != sorted(set(requested))
        or not set(requested) <= (names & EDITABLE_FIELD_NAMES)
    ):
        errors.append("options intent requested fields are invalid")
    if not isinstance(intent["normalization_changes"], list):
        errors.append("options intent normalization_changes are invalid")
    if isinstance(intent["baseline"], dict) and intent["baseline_fingerprint"] != fingerprint(
        BASELINE_MODEL, intent["baseline"]
    ):
        errors.append("options intent baseline_fingerprint does not match")
    if isinstance(intent["proposed"], dict) and intent["proposed_fingerprint"] != fingerprint(
        INTENT_MODEL, intent["proposed"]
    ):
        errors.append("options intent proposed_fingerprint does not match")
    if (
        isinstance(intent["baseline"], dict)
        and isinstance(intent["proposed"], dict)
        and visible_equal(intent["baseline"], intent["proposed"])
    ):
        errors.append("options intent is a no-op")
    if not _is_hash(intent["consumer_evidence_fingerprint"]):
        errors.append("options intent consumer_evidence_fingerprint is invalid")
    reasons = intent["dependency_reasons"]
    if (
        not isinstance(reasons, list)
        or len(reasons) > MAX_DEPENDENCY_REASONS
        or any(
            not isinstance(item, str)
            or not 1 <= len(item) <= MAX_DEPENDENCY_REASON_LENGTH
            for item in reasons
        )
        or reasons != sorted(set(reasons))
    ):
        errors.append("options intent dependency_reasons are invalid")
    return errors


def _flatten(submit: dict[str, Any]) -> dict[str, Any] | None:
    flat: dict[str, Any] = {}
    for name, value in submit.items():
        if name == SECTION:
            if not isinstance(value, dict):
                return None
            for leaf, item in value.items():
                flat[f"{SECTION}.{leaf}"] = item
        else:
            flat[name] = value
    return flat


def intent(proposed: dict[str, Any]) -> dict[str, Any]:
    return proposed[INTENT_KEY]


def consumer_fingerprint(evidence: dict[str, Any]) -> str:
    return stable_hash({"model": CONSUMER_EVIDENCE_MODEL, **evidence})


def applied_state_fingerprint(visible: dict[str, Any], modified_at: float) -> str:
    """Bind the visible options to the entry's ``modified_at`` at readback."""

    return stable_hash(
        {
            "model": APPLIED_STATE_MODEL,
            "visible": canonical(visible),
            "modified_at": modified_at,
        }
    )


def provider_payload(entry_id: str, submit: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "POST",
        "operation": OPTIONS_FLOW_SUBMIT_OPERATION,
        "handler": entry_id,
        "user_input": copy.deepcopy(submit),
    }


def provider_arguments_hash(entry_id: str, submit: dict[str, Any]) -> str:
    return stable_hash(provider_payload(entry_id, submit))


# --- gateway ----------------------------------------------------------------


@dataclass(frozen=True)
class EntryFacts:
    entry_id: str
    domain: str
    title: str
    state: str
    modified_at: float
    supports_options: bool
    disabled_by: str | None


@dataclass(frozen=True)
class SubmitOutcome:
    """``applied`` is True for create_entry, False for a confirmed rejection
    and None when the response was lost (readback decides)."""

    applied: bool | None
    category: str
    provider_status: int | None = None


@dataclass(frozen=True)
class HeldForm:
    """An options flow opened for dispatch and not yet submitted or aborted."""

    flow_id: str
    step_id: str
    visible: dict[str, Any]


class HeldFlowRefused(Exception):
    """The dispatch-time flow was not the expected form; nothing was sent.

    ``drift`` is True when Home Assistant answered with a different step or
    schema (the target changed), False for a malformed provider response.
    """

    def __init__(self, category: str, *, drift: bool, orphaned: bool) -> None:
        super().__init__(category)
        self.category = category
        self.drift = drift
        self.orphaned = orphaned


class HelperOptionsGateway:
    """The closed reads, the flow open/abort and the single step submit."""

    def __init__(self, rest_client: Any, websocket_client: Any) -> None:
        self.rest_client = rest_client
        self.websocket_client = websocket_client

    async def _command(self, command: dict[str, Any]) -> Any:
        if command.get("type") not in READ_COMMANDS:
            raise HelperOptionsContractError("command_outside_closed_vocabulary")
        return await self.websocket_client.command(dict(command))

    async def registry_entry(self, entity_id: str) -> dict[str, Any] | None:
        validate_entity_id(entity_id)
        try:
            value = await self._command(
                {"type": "config/entity_registry/get", "entity_id": entity_id}
            )
        except AuthorizationError:
            raise
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("status") == 404 or details.get("code") == "not_found":
                return None
            raise
        if not isinstance(value, dict) or value.get("entity_id") != entity_id:
            raise HelperOptionsProviderError("entity_registry_entry_malformed")
        entry_id = value.get("config_entry_id")
        if entry_id is not None and (
            not isinstance(entry_id, str) or not ENTRY_ID_PATTERN.fullmatch(entry_id)
        ):
            raise HelperOptionsProviderError("entity_registry_entry_malformed")
        return value

    async def entry_entities(self, entry_id: str) -> list[str]:
        """Every registry entity of the entry, sorted and bounded."""

        items = await self._command({"type": "config/entity_registry/list"})
        if not isinstance(items, list) or len(items) > MAX_REGISTRY_LIST_ITEMS:
            raise HelperOptionsProviderError("registry_list_unavailable")
        found: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise HelperOptionsProviderError("registry_list_malformed")
            if item.get("config_entry_id") == entry_id:
                entity_id = item.get("entity_id")
                try:
                    validate_entity_id(entity_id)
                except ValueError:
                    raise HelperOptionsProviderError("registry_list_malformed") from None
                found.add(entity_id)
        if len(found) > MAX_ENTRY_ENTITIES:
            raise HelperOptionsContractError("options_entry_too_many_entities")
        return sorted(found)

    async def entry(self, entry_id: str) -> EntryFacts | None:
        if not ENTRY_ID_PATTERN.fullmatch(entry_id):
            raise HelperOptionsContractError("options_entry_id_invalid")
        try:
            value = await self._command(
                {"type": "config_entries/get_single", "entry_id": entry_id}
            )
        except AuthorizationError:
            raise
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("status") == 404 or details.get("code") == "not_found":
                return None
            raise
        entry = value.get("config_entry") if isinstance(value, dict) else None
        if (
            not isinstance(entry, dict)
            or entry.get("entry_id") != entry_id
            or not isinstance(entry.get("domain"), str)
            or not isinstance(entry.get("state"), str)
            or not _timestamp(entry.get("modified_at"))
            or type(entry.get("supports_options")) is not bool
        ):
            raise HelperOptionsProviderError("config_entry_malformed")
        disabled_by = entry.get("disabled_by")
        title = entry.get("title")
        return EntryFacts(
            entry_id=entry_id,
            domain=entry["domain"][:64],
            title=title[:255] if isinstance(title, str) else "",
            state=entry["state"][:32],
            modified_at=float(entry["modified_at"]),
            supports_options=entry["supports_options"],
            disabled_by=disabled_by[:32] if isinstance(disabled_by, str) else None,
        )

    async def state(self, entity_id: str) -> str | None:
        validate_entity_id(entity_id)
        try:
            value = await self.rest_client.request(
                "GET", rest_path("/states/{}", entity_id, kinds=("entity_id",))
            )
        except AuthorizationError:
            raise
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("status") == 404:
                return None
            raise
        state = value.get("state") if isinstance(value, dict) else None
        return state[:255] if isinstance(state, str) else None

    async def open_flow(self, entry_id: str) -> Any:
        """Start an options flow for the exact entry (a read; aborted after)."""

        if not ENTRY_ID_PATTERN.fullmatch(entry_id):
            raise HelperOptionsContractError("options_entry_id_invalid")
        return await self.rest_client.request(
            "POST", rest_path("/config/config_entries/options/flow"), {"handler": entry_id}
        )

    async def abort_flow(self, flow_id: str) -> bool:
        """Abort a flow this gateway opened; False when the abort failed.

        True means Core no longer holds the flow: aborted now, or already
        gone (a 404: finished, aborted, or discarded by a Core restart).
        The DELETE runs to completion, bounded, even when the caller is
        cancelled, which is then re-raised: a cancellation never strands
        the flow it was releasing.
        """

        if not isinstance(flow_id, str) or not FLOW_ID_PATTERN.fullmatch(flow_id):
            raise HelperOptionsContractError("options_flow_id_malformed")
        return await _settle(asyncio.ensure_future(self._delete_flow(flow_id)))

    async def _delete_flow(self, flow_id: str) -> bool:
        try:
            await self.rest_client.request(
                "DELETE", rest_path("/config/config_entries/options/flow/{}", flow_id)
            )
        except AuthorizationError:
            raise
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            return (
                details.get("status") == 404
                and details.get("provider_response_received") is True
            )
        except Exception:  # noqa: BLE001 - an orphan is disclosed, never fatal
            return False
        return True

    async def read_visible(
        self, entry_id: str, *, domain: str, expected_step_id: str | None
    ) -> tuple[str, dict[str, Any], bool]:
        """Open, read every suggested value, and always abort.

        Returns ``(step_id, visible, aborted)``.  A failed abort is an orphan
        flow (in memory only, gone at the next Core restart) and is
        disclosed by the caller; it never makes the read succeed.
        """

        response = await self.open_flow(entry_id)
        flow_id = response.get("flow_id") if isinstance(response, dict) else None
        aborted = False
        try:
            _flow_id, step_id, visible = parse_form(
                response, domain=domain, expected_step_id=expected_step_id
            )
        finally:
            if isinstance(flow_id, str) and FLOW_ID_PATTERN.fullmatch(flow_id):
                if not (isinstance(response, dict) and response.get("type") in ("abort", "create_entry")):
                    aborted = await self.abort_flow(flow_id)
                else:
                    aborted = True
        return step_id, visible, aborted

    async def open_held(
        self, entry_id: str, *, domain: str, expected_step_id: str
    ) -> HeldForm:
        """Open a flow and keep it open for the one submit of a dispatch.

        A response that is not the expected form is aborted here and refused
        with ``HeldFlowRefused``; ``orphaned`` discloses a failed abort.
        """

        response = await self.open_flow(entry_id)
        try:
            flow_id, step_id, visible = parse_form(
                response, domain=domain, expected_step_id=expected_step_id
            )
        except (HelperOptionsContractError, HelperOptionsProviderError) as exc:
            orphaned = False
            flow_id = response.get("flow_id") if isinstance(response, dict) else None
            if (
                isinstance(flow_id, str)
                and FLOW_ID_PATTERN.fullmatch(flow_id)
                and response.get("type") not in ("abort", "create_entry")
            ):
                try:
                    orphaned = not await self.abort_flow(flow_id)
                except Exception:  # noqa: BLE001 - an orphan is disclosed
                    orphaned = True
            raise HeldFlowRefused(
                exc.category,
                drift=isinstance(exc, HelperOptionsContractError),
                orphaned=orphaned,
            ) from None
        return HeldForm(flow_id=flow_id, step_id=step_id, visible=visible)

    async def submit(self, flow_id: str, submit: dict[str, Any]) -> SubmitOutcome:
        """Send the one step submit.  This POST is the irreversible dispatch."""

        if not isinstance(flow_id, str) or not FLOW_ID_PATTERN.fullmatch(flow_id):
            raise HelperOptionsContractError("options_flow_id_malformed")
        mark_provider_send()  # the exact send boundary
        try:
            response = await self.rest_client.request(
                "POST",
                rest_path("/config/config_entries/options/flow/{}", flow_id),
                copy.deepcopy(submit),
                expected_statuses=frozenset({400, 404}),
            )
        except AuthorizationError:
            raise
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            status = details.get("status")
            if type(status) is int and 400 <= status < 500:
                return SubmitOutcome(False, SUBMIT_REJECTED_REASON, status)
            return SubmitOutcome(None, SUBMIT_UNCERTAIN, status if type(status) is int else None)
        except Exception:  # noqa: BLE001 - lost response: readback decides
            return SubmitOutcome(None, SUBMIT_UNCERTAIN)
        status = getattr(response, "status", None)
        if type(status) is int:
            return SubmitOutcome(False, SUBMIT_REJECTED_REASON, status)
        if not isinstance(response, dict):
            return SubmitOutcome(None, SUBMIT_UNCERTAIN)
        kind = response.get("type")
        if kind == "create_entry":
            return SubmitOutcome(True, "options_flow_finished")
        if kind == "form":
            return SubmitOutcome(False, "options_flow_validation_error")
        if kind == "abort":
            return SubmitOutcome(False, "options_flow_aborted")
        return SubmitOutcome(None, SUBMIT_UNCERTAIN)


    async def submit_dispatch(
        self, flow_id: str, submit: dict[str, Any]
    ) -> SubmitOutcome:
        """The one submit, with its outcome raised for the F3 dispatch.

        A confirmed non-application raises the not-dispatched error carrying
        a received response; a lost or unrecognized response raises
        ``HelperOptionsSubmitUncertain`` so readback decides.
        """

        from .resources import ConfigurationMutationNotDispatchedError

        outcome = await self.submit(flow_id, submit)
        if outcome.applied is True:
            return outcome
        if outcome.applied is False:
            raise ConfigurationMutationNotDispatchedError(
                "options flow submit was not applied",
                details={
                    "provider_response_received": True,
                    "reason": SUBMIT_REJECTED_REASON,
                    "category": outcome.category,
                    # Only a finished/aborted flow or a 404 (no such flow)
                    # proves Core holds nothing; any other rejection is
                    # treated as leaving the flow open, so it is aborted.
                    "flow_open": not (
                        outcome.category == "options_flow_aborted"
                        or outcome.provider_status == 404
                    ),
                },
            )
        raise HelperOptionsSubmitUncertain(
            "options flow submit result is uncertain",
            details={"reason": SUBMIT_UNCERTAIN},
        )


async def _settle(task: asyncio.Future) -> bool:
    """Await one flow abort; a caller's cancellation waits for it, bounded."""

    try:
        await asyncio.wait({task}, timeout=ABORT_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        try:
            await asyncio.wait({task}, timeout=ABORT_TIMEOUT_SECONDS)
        finally:
            if task.done() and not task.cancelled():
                task.exception()  # retrieved: the cancellation propagates
            else:
                task.cancel()
        raise
    if not task.done():
        task.cancel()
        return False
    if task.cancelled():
        return False
    return task.result() is True


class HelperOptionsSubmitUncertain(HomeAssistantApiError):
    """The submit was sent and its outcome is unknown; readback decides."""


class HelperOptionsResourceGateway(HelperOptionsGateway):
    """The options gateway plus the fenced consumer-evidence read.

    Attached to the configuration resource gateway by the runtime; the
    dependency snapshot reader is set there after construction, so it is
    resolved at call time.  ``None`` evidence means the reader is not wired,
    which planning and preflight treat as unavailable.
    """

    def __init__(self, configuration: Any) -> None:
        super().__init__(configuration.rest_client, configuration.websocket_client)
        self._configuration = configuration

    async def consumer_evidence(self, entity_id: str) -> dict[str, Any] | None:
        from .entity_availability_exposure_planning import read_consumer_evidence

        reader = getattr(self._configuration, "availability_exposure_snapshot_reader", None)
        if reader is None:
            return None
        return await read_consumer_evidence(reader, entity_id, fenced=True)

    async def entry_consumer_evidence(
        self, entity_ids: list[str]
    ) -> dict[str, Any] | None:
        evidence: dict[str, Any] = {}
        for entity_id in entity_ids:
            value = await self.consumer_evidence(entity_id)
            if value is None:
                return None
            evidence[entity_id] = value
        return {"entities": evidence}


__all__ = [
    "ACTION",
    "CAPABILITY_IDENTITY",
    "DOMAINS",
    "EDITABLE_FIELD_NAMES",
    "ENTITY_DOMAINS",
    "ENTRY_UNHEALTHY",
    "EXPECTED_EFFECT",
    "EntryFacts",
    "FieldSpec",
    "HelperOptionsContractError",
    "HelperOptionsGateway",
    "HelperOptionsProviderError",
    "HelperOptionsResourceGateway",
    "HelperOptionsSubmitUncertain",
    "SUBMIT_REJECTION_CATEGORIES",
    "SUBMIT_UNCERTAIN",
    "applied_state_fingerprint",
    "HeldFlowRefused",
    "HeldForm",
    "INTENT_KEY",
    "INTENT_MODEL",
    "NEVER_SENT",
    "OPTIONS_FLOW_ROUTE",
    "OPTIONS_FLOW_SUBMIT_OPERATION",
    "PENDING_ADMISSION_REASON",
    "PROFILE",
    "PROVIDER",
    "PROVIDER_CONTRACT",
    "Proposal",
    "READ_COMMANDS",
    "RESOURCE_TYPE",
    "STEP_CATALOG",
    "SubmitOutcome",
    "TEMPLATE_TYPES",
    "build_intent",
    "build_proposal",
    "canonical",
    "canonical_copy",
    "coerce_request_value",
    "consumer_fingerprint",
    "field_spec",
    "fingerprint",
    "intent",
    "intent_errors",
    "parse_form",
    "provider_arguments_hash",
    "provider_payload",
    "step_fields",
    "template_parses",
    "values_equal",
    "visible_equal",
]
