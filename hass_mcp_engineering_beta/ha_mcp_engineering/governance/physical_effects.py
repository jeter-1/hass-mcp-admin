"""Deterministic protected physical-effect comparison (HAMCP-213 D2).

The comparison is server-owned review evidence computed from the immutable
normalized current and proposed configurations of one automation or script
operation. It is recomputed whenever policy is evaluated, an acknowledgement
is decided, or a plan is dispatched; a stored copy is never trusted. It is
never a grant: it only selects which owner decision the policy requires.

Verdicts:

- ``none``: neither side has a protected effect.
- ``equivalent``: the protected effects, their order, the reachability
  context, and the normalized run structure are identical. Only literal
  display text and non-aborting reference-only steps that no effect follows
  may differ (OD-10, conservative rule).
- ``changed``: anything else.
- ``unresolved``: the operation has, or may have, a protected effect that
  the reviewed grammar cannot bound: a templated or variable-driven service
  name; an effect-capable service whose target is templated, area, device,
  floor or label based, or absent; an unresolvable device action; a
  blueprint input; or an unbounded structure. Uncertainty is disclosed as
  "effect cannot be determined" and acknowledged, never treated as ``none``
  or ``equivalent`` (HAMCP-213 review H1).

Only closed, reviewed tables are used. An unknown service acting on a
protected object counts as an effect. Registry data is not available to this
pure comparison, so an area, device, floor or label selector is never
resolved to its members: it is always ``unresolved``.

Every reference-only step that can run before a protected effect in the same
run path is compared in full (service, all data including nested data,
target, ``continue_on_error``, ``enabled`` and position), because any change
to it can change whether the effect is reached (OD-10, review H2).

A call to a script (``script.<key>``, ``script.turn_on``/``toggle``/
``turn_off`` or ``homeassistant.turn_on``/``toggle``/``turn_off`` with a
literal script entity) counts as the protected effects of the called script,
directly or through further scripts up to ``MAX_CALL_DEPTH``. The called
scripts come from the operation's ``called_scripts`` snapshot, captured from
the governed configuration and entity-registry reads when the plan is created
and bound by the plan hash; the comparison itself stays pure. Both sides of
the operation use the same callee summary, so the calling step's reachability
is compared exactly as for a direct effect, while a change to the called
script itself is compared by that script's own operation (script parity). A
call whose script cannot be resolved (templated or variable name, unknown,
unreadable or uncaptured script, depth limit, cycle) is ``unresolved`` and
may be an unlock or disarm (the OD-6(b) floor applies).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from ..dependency.script_calls import (
    _BUILTINS as _SCRIPT_BUILTIN_SERVICES,
    _ENTITY as _SCRIPT_ENTITY,
    _SERVICE as _SCRIPT_SERVICE,
)
from .normalize import stable_hash
from .risk import (
    SAFETY_CRITICAL_SERVICES,
    SENSITIVE_ENTITY_DOMAINS,
    WATER_TARGET_TERMS,
    is_reference_only_service,
)


COMPARISON_MODEL = "physical-effect-comparison-v1"

VERDICT_NONE = "none"
VERDICT_EQUIVALENT = "equivalent"
VERDICT_CHANGED = "changed"
VERDICT_UNRESOLVED = "unresolved"

ROW_ADDED = "added"
ROW_REMOVED = "removed"
ROW_CHANGED = "changed"
ROW_EQUIVALENT = "equivalent"
ROW_UNRESOLVED = "unresolved"

SUPPORTED_RESOURCE_TYPES = frozenset({"automation", "script"})
SUPPORTED_ACTIONS = frozenset({"create", "update", "delete"})

# Reference-only services (risk.REFERENCE_ONLY_*) never act on the entity
# they mention (D2.1/D4.1).
# Services whose domain makes every literal target a potential protected
# object. Today's floor (OD-7, D5 follows separately) is the lock, alarm and
# valve domains plus covers and water controls matched by entity id.
_EFFECT_DOMAINS = frozenset({"cover", *SENSITIVE_ENTITY_DOMAINS})
_GENERIC_EFFECT_SERVICES = frozenset(
    {"homeassistant.turn_on", "homeassistant.turn_off", "homeassistant.toggle"}
)
# Effect-capable domains (review H1): a call or device action in one of these
# domains whose target cannot be resolved statically may act on a protected
# object. Switch and water_heater entities are routinely matched by the water
# floor; siren is listed by the architect direction although its literal
# targets are not yet part of the floor (OD-7/D5).
_UNRESOLVED_EFFECT_DOMAINS = frozenset(
    {*_EFFECT_DOMAINS, "siren", "switch", "water_heater"}
)
# Closed table of reviewed domains whose services cannot act on a protected
# object through an unresolved target (risk._HELPER_DIRECT_EFFECT_DOMAINS less
# the effect-capable domains above). Any other domain is unknown, so an
# unresolved target on its service is treated as a possible protected effect.
_KNOWN_NON_PROTECTED_DOMAINS = frozenset({"climate", "fan", "light"})
_KNOWN_TRANSITIVE_DOMAINS = frozenset({"automation", "scene", "script"})
_TRANSITIVE_EXACT_SERVICES = frozenset({"automation.trigger", "scene.turn_on"})
_TRANSITIVE_SERVICE_PREFIXES = ("script.",)
_SAFETY_CRITICAL_DEVICE_ACTIONS = frozenset(
    {("lock", "unlock"), ("alarm_control_panel", "disarm")}
)
# Allowlist of ignorable differences (HAMCP-213 PR 1 review, batch 3). The
# normalized current and proposed configurations must be identical on every
# run path except for exactly these reviewed fields; any other difference at
# any level, including ``enabled`` flags, ``mode``/``max``, variables,
# ``continue_on_error``, waits and timeouts, response handling and every
# unknown or unreviewed key, is ``changed``. The design document
# (docs/HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md, D2 item 8) carries the
# same table, and a test asserts that the two are equal. Each row is
# ``(scope, field, condition)``.
IGNORABLE_DIFFERENCES: tuple[tuple[str, str, str], ...] = (
    ("automation", "alias", "display name"),
    ("automation", "description", "display text"),
    ("script", "alias", "display name"),
    ("script", "description", "display text"),
    ("script", "icon", "display icon"),
    ("step", "alias", "display name of any action step"),
    ("choose_option", "alias", "display name of a choose option"),
    ("parallel_branch", "alias", "display name of a parallel branch wrapper"),
    (
        "reference_step_after_effects",
        "data.message",
        "template-free text of a reference-only step that runs after every protected effect on its path",
    ),
    (
        "reference_step_after_effects",
        "data.title",
        "template-free text of a reference-only step that runs after every protected effect on its path",
    ),
    (
        "reference_step_after_effects",
        "data.data",
        "notification payload of a reference-only step that runs after every protected effect on its path",
    ),
    (
        "reference_step_after_effects",
        "entity_id placement",
        "step, target or data placement and list form of the same entity ids",
    ),
    (
        "reference_step_after_effects",
        "action/service key",
        "the legacy service key spelling of the same reference-only service",
    ),
    (
        "non_aborting_reference_step_after_effects",
        "*",
        "a reference-only step with continue_on_error: true that runs after every protected effect on its path",
    ),
)


def _ignorable(scope: str) -> frozenset[str]:
    return frozenset(
        field for row_scope, field, _condition in IGNORABLE_DIFFERENCES
        if row_scope == scope
    )


_DISPLAY_DATA_KEYS = frozenset(
    field.split(".", 1)[1]
    for field in _ignorable("reference_step_after_effects")
    if field in {"data.message", "data.title"}
)
_DISPLAY_STEP_KEYS = _ignorable("step")
_CHOOSE_OPTION_IGNORED_KEYS = _ignorable("choose_option")
_PARALLEL_BRANCH_IGNORED_KEYS = _ignorable("parallel_branch")
_BODY_KEY = {"automation": "action", "script": "sequence"}
# The body is compared as the run structure; every other configuration key
# is the reachability context, compared verbatim.
_CONTEXT_EXCLUDED_KEYS = {
    resource_type: frozenset({body, *_ignorable(resource_type)})
    for resource_type, body in _BODY_KEY.items()
}
_PARALLEL_BRANCH_WRAPPER_KEYS = frozenset({"sequence", "alias", "enabled"})
_TARGET_SELECTORS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")
_UNLOCK_OR_DISARM = "unlock_or_disarm_effect"

MAX_STEPS = 4096
MAX_DEPTH = 24
MAX_ROWS = 32
MAX_EFFECTS_PER_ROW = 16
MAX_PATH_LENGTH = 160
MAX_OBJECT_ID_LENGTH = 255
MAX_BUTTON_IDS = 32
# Called scripts (HAMCP-213 PR 1 review): one operation may reach at most
# MAX_CALLED_SCRIPTS distinct scripts, MAX_CALL_DEPTH calls deep.
MAX_CALL_DEPTH = 8
MAX_CALLED_SCRIPTS = 16
MAX_CALLEE_ANALYSES = 256

CALLED_SCRIPT_MODEL = "called-script-snapshot-v1"
# Script storage keys, as bound by Home Assistant's service names
# (dependency/script_calls.py, docs/SCRIPT_CALL_DEPENDENCIES.md).
_SCRIPT_TURN_SERVICES = frozenset({"turn_on", "turn_off", "toggle"})
CALLED_SCRIPT_UNRESOLVED_REASONS = frozenset(
    {
        "called_script_dynamic",
        "called_script_unknown",
        "called_script_unreadable",
        "called_script_entity_unresolved",
        "called_script_not_captured",
        "called_script_depth_exceeded",
        "called_script_cycle",
        "called_script_bounds_exceeded",
    }
)


# Review H3: a comparison that exceeded a reviewed bound is incomplete. It is
# never a weaker class: policy prohibits it ("too many protected objects to
# review: split the change"), while the independent unlock/disarm floor scan
# still reports any unlock or disarm.
BOUNDS_EXCEEDED_REASONS = frozenset(
    {
        "comparison_bounds_exceeded",
        "comparison_input_invalid",
        "comparison_object_collision",
        "comparison_action_unsupported",
        "called_script_depth_exceeded",
        "called_script_bounds_exceeded",
    }
)
MAX_FLOOR_SCAN_NODES = 200_000
# Subtrees that hold call arguments, never a step; the floor scan does not
# treat a mapping inside them as an action.
_FLOOR_SCAN_ARGUMENT_KEYS = frozenset(
    {"data", "data_template", "target", "variables", "event_data", "event_data_template"}
)


class _Unbounded(Exception):
    """The structure exceeds the reviewed comparison bounds."""


def _has_template(value: str) -> bool:
    return "{{" in value or "{%" in value or "{#" in value


def protected_object_reason(entity_id: str) -> str | None:
    """Return the floor reason that makes an entity a protected object."""

    value = entity_id.strip().lower()
    domain = value.split(".", 1)[0]
    if domain in SENSITIVE_ENTITY_DOMAINS:
        return f"sensitive_domain_{domain}"
    if domain == "cover" and "garage" in value:
        return "garage_cover"
    if any(term in value for term in WATER_TARGET_TERMS):
        return "water_control"
    return None


def _step_service(step: dict[str, Any]) -> tuple[str | None, bool]:
    """Return ``(service, dynamic)`` for one call step.

    The legacy ``service_template`` key always names a service that the
    grammar does not resolve (review H1), so it is always dynamic.
    """

    if "service_template" in step:
        return None, True
    for key in ("action", "service"):
        if key not in step:
            continue
        value = step[key]
        if not isinstance(value, str) or not value.strip() or _has_template(value):
            return None, True
        return value.strip().lower(), False
    return None, False


def _split_entity_values(value: Any) -> tuple[list[str], bool]:
    """Return literal entity ids and whether any value is dynamic."""

    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list):
        return [], True
    found: list[str] = []
    dynamic = False
    for item in values:
        if not isinstance(item, str) or not item.strip() or _has_template(item):
            dynamic = True
            continue
        for part in item.split(","):
            part = part.strip().lower()
            if part:
                found.append(part)
    return found, dynamic


def _call_targets(
    step: dict[str, Any], *, include_data_entity: bool
) -> tuple[list[str], bool, bool]:
    """Return ``(entity_ids, dynamic, non_entity_selector)`` for a call."""

    entities: list[str] = []
    dynamic = False
    other = False
    holders: list[dict[str, Any]] = []
    target = step.get("target")
    if isinstance(target, dict):
        holders.append(target)
    elif "target" in step:
        dynamic = True
    holders.append(step)
    for holder in holders:
        for selector in _TARGET_SELECTORS:
            if selector not in holder:
                continue
            if selector != "entity_id":
                other = True
                continue
            values, is_dynamic = _split_entity_values(holder[selector])
            entities.extend(values)
            dynamic = dynamic or is_dynamic
    for data_key in ("data", "data_template"):
        if not include_data_entity or data_key not in step:
            continue
        data = step[data_key]
        if not isinstance(data, dict):
            # A templated data mapping can carry any selector.
            dynamic = True
            continue
        if "entity_id" in data:
            values, is_dynamic = _split_entity_values(data["entity_id"])
            entities.extend(values)
            dynamic = dynamic or is_dynamic
        # Home Assistant also accepts the non-entity selectors in data.
        other = other or any(selector in data for selector in _TARGET_SELECTORS[1:])
    if any(item in {"all", "*"} for item in entities):
        dynamic = True
        entities = [item for item in entities if item not in {"all", "*"}]
    return sorted(set(entities)), dynamic, other


@dataclass(frozen=True)
class _Effect:
    object_id: str
    service: str
    path: str
    data_keys: tuple[str, ...]
    reason: str
    floor: bool


@dataclass
class _Side:
    effects: list[_Effect]
    unresolved: list[tuple[str, str, bool]]  # (object key, reason, unlock/disarm)
    transitive: list[str]
    buttons: set[str]
    relevant: bool
    structure: Any


def _children(step: dict[str, Any]) -> Iterable[tuple[str, Any, bool]]:
    """Yield ``(label, step list, loops)`` for every nested step list."""

    choices = step.get("choose")
    if "choose" in step:
        if isinstance(choices, dict):
            choices = [choices]
        if not isinstance(choices, list):
            raise _Unbounded("choose")
        for index, option in enumerate(choices):
            if not isinstance(option, dict):
                raise _Unbounded("choose option")
            yield f"choose[{index}].sequence", option.get("sequence", []), False
        if "default" in step:
            yield "default", step.get("default"), False
    if "if" in step:
        yield "then", step.get("then", []), False
        if "else" in step:
            yield "else", step.get("else"), False
    if "repeat" in step:
        repeat = step.get("repeat")
        if not isinstance(repeat, dict):
            raise _Unbounded("repeat")
        yield "repeat.sequence", repeat.get("sequence", []), True
    if "parallel" in step:
        branches = step.get("parallel")
        if isinstance(branches, dict):
            branches = [branches]
        if not isinstance(branches, list):
            raise _Unbounded("parallel")
        for index, branch in enumerate(branches):
            if _branch_wrapper(branch):
                yield f"parallel[{index}].sequence", branch.get("sequence", []), False
            else:
                yield f"parallel[{index}]", [branch], False
    if (
        "sequence" in step
        and not {"choose", "repeat", "parallel", "if"}.intersection(step)
    ):
        yield "sequence", step.get("sequence"), False


def _as_steps(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list):
        raise _Unbounded("step list")
    return value


def _branch_wrapper(branch: Any) -> bool:
    """Whether a parallel branch is a ``sequence`` wrapper, not a step."""

    return isinstance(branch, dict) and set(branch) <= _PARALLEL_BRANCH_WRAPPER_KEYS


def _without(value: dict[str, Any], keys: frozenset[str]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in keys}


def _rebuild(step: dict[str, Any], walked: dict[str, list[Any]]) -> dict[str, Any]:
    """Return ``step`` verbatim with its nested step lists normalized.

    Only the ignorable fields of :data:`IGNORABLE_DIFFERENCES` are removed.
    Every nested step list that :func:`_children` yields is replaced in place
    by its normalized form, so every wrapper field (a parallel branch's
    ``enabled``, a choose option's conditions, a repeat's ``while``) stays
    in the compared structure. Keys that :func:`_children` does not walk,
    such as ``then`` without ``if``, are kept verbatim.
    """

    node = _without(step, _DISPLAY_STEP_KEYS)
    if "choose" in step:
        choices = step["choose"]
        options = [choices] if isinstance(choices, dict) else choices
        rebuilt = []
        for index, option in enumerate(options):
            item = _without(option, _CHOOSE_OPTION_IGNORED_KEYS)
            if "sequence" in option:
                item["sequence"] = walked[f"choose[{index}].sequence"]
            rebuilt.append(item)
        node["choose"] = rebuilt[0] if isinstance(choices, dict) else rebuilt
        if "default" in step:
            node["default"] = walked["default"]
    if "if" in step:
        for label in ("then", "else"):
            if label in step:
                node[label] = walked[label]
    if "repeat" in step:
        repeat = dict(step["repeat"])
        if "sequence" in repeat:
            repeat["sequence"] = walked["repeat.sequence"]
        node["repeat"] = repeat
    if "parallel" in step:
        branches = step["parallel"]
        items = [branches] if isinstance(branches, dict) else branches
        rebuilt = []
        for index, branch in enumerate(items):
            if _branch_wrapper(branch):
                item = _without(branch, _PARALLEL_BRANCH_IGNORED_KEYS)
                if "sequence" in branch:
                    item["sequence"] = walked[f"parallel[{index}].sequence"]
                rebuilt.append({"branch": item})
            else:
                rebuilt.append({"branch_step": walked[f"parallel[{index}]"]})
        node["parallel"] = rebuilt[0] if isinstance(branches, dict) else rebuilt
    if "sequence" in walked:
        node["sequence"] = walked["sequence"]
    return node


def _script_key(value: Any) -> str | None:
    if isinstance(value, str):
        match = _SCRIPT_SERVICE.fullmatch(f"script.{value}")
        if match and value not in _SCRIPT_BUILTIN_SERVICES:
            return value
    return None


def script_storage_key(value: Any) -> str | None:
    """Return ``value`` when it is a callable script storage key."""

    return _script_key(value)


def called_script_call_targets(
    service: str, step: dict[str, Any]
) -> tuple[list[str], list[str], bool]:
    """Return ``(storage keys, script entity ids, unresolved)`` for one call.

    ``script.<key>`` binds the script's storage key; the turn/toggle forms
    name script entities, which the plan resolves through the entity
    registry. ``unresolved`` is set when the call may run a script that the
    grammar cannot name (templated, variable, area, device, floor or label
    target, or none). ``script.reload`` runs no script.
    """

    keys: list[str] = []
    entities: list[str] = []
    if not service.startswith("script.") and service not in _GENERIC_EFFECT_SERVICES:
        return keys, entities, False
    name = service.split(".", 1)[1]
    if service.startswith("script.") and name == "reload":
        return keys, entities, False
    if service.startswith("script.") and name not in _SCRIPT_TURN_SERVICES:
        key = _script_key(name)
        if key is None:
            return keys, entities, True
        return [key], entities, False
    targets, dynamic, other = _call_targets(step, include_data_entity=True)
    scripts = [item for item in targets if item.startswith("script.")]
    unresolved = False
    if service.startswith("script."):
        # script.turn_on/toggle/turn_off act only on script entities.
        unresolved = dynamic or other or not targets or len(scripts) != len(targets)
    for entity_id in scripts:
        if _SCRIPT_ENTITY.fullmatch(entity_id):
            entities.append(entity_id)
        else:
            unresolved = True
    return keys, sorted(set(entities)), unresolved


class _CalledScripts:
    """Pure resolver over one operation's captured called-script snapshot."""

    def __init__(self, snapshot: Any) -> None:
        valid = (
            isinstance(snapshot, dict)
            and snapshot.get("model") == CALLED_SCRIPT_MODEL
        )
        scripts = snapshot.get("scripts") if valid else None
        entities = snapshot.get("entities") if valid else None
        self.scripts = scripts if isinstance(scripts, dict) else {}
        self.entities = entities if isinstance(entities, dict) else {}
        # A capture that hit MAX_CALLED_SCRIPTS left scripts unread (H3).
        self.not_captured = (
            "called_script_bounds_exceeded"
            if valid and snapshot.get("truncated") is True
            else "called_script_not_captured"
        )
        self.analyses = 0
        self._memo: dict[tuple[str, tuple[str, ...]], Any] = {}

    def entity_key(self, entity_id: str) -> tuple[str | None, str | None]:
        """Return ``(storage key, unresolved reason)`` for a script entity."""

        entry = self.entities.get(entity_id)
        if entry is None:
            return None, self.not_captured
        key = _script_key(entry)
        if key is not None:
            return key, None
        reason = entry.get("unresolved") if isinstance(entry, dict) else None
        if reason not in CALLED_SCRIPT_UNRESOLVED_REASONS:
            reason = "called_script_entity_unresolved"
        return None, reason

    def summary(
        self, key: str, stack: tuple[str, ...]
    ) -> str | tuple[list[_Effect], list[tuple[str, str, bool]]]:
        """Return the called script's protected effects, or why it is unresolved."""

        if key in stack:
            return "called_script_cycle"
        if len(stack) + 1 > MAX_CALL_DEPTH:
            return "called_script_depth_exceeded"
        memo_key = (key, stack)
        if memo_key in self._memo:
            return self._memo[memo_key]
        entry = self.scripts.get(key)
        result: Any
        if not isinstance(entry, dict):
            result = self.not_captured
        elif "configs" not in entry:
            reason = entry.get("unresolved")
            result = (
                reason
                if reason in CALLED_SCRIPT_UNRESOLVED_REASONS
                else "called_script_unreadable"
            )
        else:
            result = self._analyze_configs(key, entry["configs"], (*stack, key))
        self._memo[memo_key] = result
        return result

    def _analyze_configs(
        self, key: str, configs: Any, stack: tuple[str, ...]
    ) -> str | tuple[list[_Effect], list[tuple[str, str, bool]]]:
        if (
            not isinstance(configs, list)
            or not 1 <= len(configs) <= 2
            or not all(isinstance(config, dict) for config in configs)
        ):
            return "called_script_unreadable"
        effects: list[_Effect] = []
        unresolved: list[tuple[str, str, bool]] = []
        # A called script that the same plan changes is summarized over its
        # current and proposed bodies, identically for both caller sides.
        for config in configs:
            self.analyses += 1
            if self.analyses > MAX_CALLEE_ANALYSES:
                raise _Unbounded("called scripts")
            analyzer = _Analyzer(self, stack)
            try:
                analyzer.walk(config.get("sequence", []), "sequence", False, 0)
                for entity_id, _reason in _blueprint_inputs(config):
                    analyzer._add_unresolved(
                        entity_id, "unresolved_blueprint_input", False
                    )
            except (_Unbounded, RecursionError, TypeError, ValueError):
                return "called_script_bounds_exceeded"
            for effect in analyzer.effects:
                if effect not in effects:
                    effects.append(effect)
            for item in analyzer.unresolved:
                if item not in unresolved:
                    unresolved.append(item)
        return effects, unresolved


class _Analyzer:
    def __init__(
        self,
        callees: _CalledScripts | None = None,
        stack: tuple[str, ...] = (),
    ) -> None:
        self.callees = callees if callees is not None else _CalledScripts(None)
        self.stack = stack
        self.steps = 0
        self.effects: list[_Effect] = []
        self.unresolved: list[tuple[str, str, bool]] = []
        self.transitive: list[str] = []
        self.buttons: set[str] = set()
        self.relevant = False
        # Keyed by ``id()``; each entry also holds the keyed list so that its
        # id cannot be reused by a later (temporary) list while the analyzer
        # lives. A bare id key returned stale results for a reused id.
        self._effect_memo: dict[int, tuple[list[Any], bool]] = {}

    # -- classification of one step ---------------------------------------

    def _add_unresolved(self, key: str, reason: str, floor: bool) -> None:
        # Review H1: an effect the grammar cannot resolve always makes the
        # operation protected; it is never discarded as ``none``.
        self.relevant = True
        self.unresolved.append((key, reason, floor))

    def _call_effects(self, step: dict[str, Any], path: str) -> bool:
        """Record effects of one call step; return whether it is an effect."""

        service, dynamic = _step_service(step)
        if dynamic:
            # A templated or variable-driven service name may be any service,
            # including lock.unlock or alarm_control_panel.alarm_disarm.
            self._add_unresolved(f"dynamic_service@{path}", "dynamic_service", True)
            return True
        if service is None:
            return False
        if is_reference_only_service(service):
            self._record_buttons(service, step)
            return False
        domain = service.split(".", 1)[0]
        entities, dynamic_target, other_selector = _call_targets(
            step, include_data_entity=True
        )
        effect = False
        floor = service in SAFETY_CRITICAL_SERVICES
        data = step.get("data")
        data_keys = (
            tuple(sorted(str(key) for key in data)[:16])
            if isinstance(data, dict)
            else ()
        )
        for entity_id in entities:
            reason = protected_object_reason(entity_id)
            if reason is None:
                continue
            effect = True
            self.relevant = True
            self.effects.append(
                _Effect(entity_id, service, path, data_keys, reason, floor)
            )
        sensitive_service = domain in SENSITIVE_ENTITY_DOMAINS or floor
        effect_capable = (
            domain in _UNRESOLVED_EFFECT_DOMAINS
            or service in _GENERIC_EFFECT_SERVICES
            or domain
            not in _KNOWN_NON_PROTECTED_DOMAINS
            | _KNOWN_TRANSITIVE_DOMAINS
            | {"homeassistant"}
        )
        unresolved_target = dynamic_target or other_selector or (
            not entities and domain in _UNRESOLVED_EFFECT_DOMAINS
        )
        if unresolved_target and effect_capable:
            self._add_unresolved(
                f"{service}@{path}",
                (
                    "unresolved_protected_target"
                    if sensitive_service
                    else "unresolved_effect_target"
                ),
                floor,
            )
            effect = True
        if self._called_script_effects(service, step, path):
            effect = True
        if service in _TRANSITIVE_EXACT_SERVICES or service.startswith(
            _TRANSITIVE_SERVICE_PREFIXES
        ):
            callee = (
                ",".join(entities)
                if service in {"script.turn_on", "automation.trigger", "scene.turn_on"}
                else service
            )
            self.transitive.append(f"{service}:{callee}" if callee != service else service)
            effect = True
        return effect

    def _called_script_effects(
        self, service: str, step: dict[str, Any], path: str
    ) -> bool:
        """Record the protected effects of every script this call runs."""

        keys, entities, unresolved = called_script_call_targets(service, step)
        found = False
        if unresolved:
            # A templated, variable or selector target may run any script,
            # including one that unlocks or disarms.
            self._add_unresolved(
                f"{service}@{path}"[:MAX_OBJECT_ID_LENGTH],
                "called_script_dynamic",
                True,
            )
            found = True
        resolved: list[str] = list(keys)
        for entity_id in entities:
            key, reason = self.callees.entity_key(entity_id)
            if key is None:
                self._add_unresolved(
                    f"{entity_id}@{path}"[:MAX_OBJECT_ID_LENGTH], str(reason), True
                )
                found = True
            elif key not in resolved:
                resolved.append(key)
        for key in resolved:
            summary = self.callees.summary(key, self.stack)
            if isinstance(summary, str):
                self._add_unresolved(
                    f"script.{key}@{path}"[:MAX_OBJECT_ID_LENGTH], summary, True
                )
                found = True
                continue
            inner_effects, inner_unresolved = summary
            for inner in inner_effects:
                self.relevant = True
                found = True
                self.effects.append(
                    _Effect(
                        inner.object_id,
                        f"script.{key}>{inner.service}",
                        f"{path}>script.{key}:{inner.path}",
                        inner.data_keys,
                        inner.reason,
                        inner.floor,
                    )
                )
            for inner_key, code, floor in inner_unresolved:
                self._add_unresolved(
                    f"script.{key}>{inner_key}"[:MAX_OBJECT_ID_LENGTH], code, floor
                )
                found = True
        return found

    def _device_effect(self, step: dict[str, Any], path: str) -> bool:
        domain = step.get("domain")
        device_id = step.get("device_id")
        if not isinstance(domain, str) or "type" not in step:
            return False
        kind = step.get("type")
        floor = (domain, kind) in _SAFETY_CRITICAL_DEVICE_ACTIONS
        # A device action of an effect-capable or unknown domain acts on an
        # object that the grammar may be unable to name (review H1).
        effect_capable = "condition" not in step and (
            domain in _UNRESOLVED_EFFECT_DOMAINS
            or domain not in _KNOWN_NON_PROTECTED_DOMAINS
        )
        if not isinstance(device_id, str) or not isinstance(kind, str):
            if domain in SENSITIVE_ENTITY_DOMAINS:
                self._add_unresolved(
                    f"device:{domain}@{path}", "unresolved_protected_target", floor
                )
                return True
            if effect_capable:
                self._add_unresolved(
                    f"device:{domain}@{path}", "unresolved_effect_target", floor
                )
                return True
            return False
        entity = step.get("entity_id")
        reason = None
        object_id = f"device:{device_id}"
        literal_entity = (
            isinstance(entity, str)
            and "." in entity
            and not _has_template(entity)
        )
        if literal_entity:
            reason = protected_object_reason(entity)
            object_id = entity.strip().lower()
        if reason is None and domain in SENSITIVE_ENTITY_DOMAINS:
            reason = f"sensitive_domain_{domain}"
        if reason is None and effect_capable and not literal_entity:
            # The entity is a registry id, a template or absent, so the
            # protected floor cannot be evaluated for it.
            self._add_unresolved(
                f"device:{domain}.{kind}@{path}"[:MAX_OBJECT_ID_LENGTH],
                "unresolved_effect_target",
                floor,
            )
            return True
        if reason is None:
            return False
        self.relevant = True
        self.effects.append(
            _Effect(
                object_id,
                f"device:{domain}.{kind}",
                path,
                (),
                reason,
                floor,
            )
        )
        return True

    def _scene_effect(self, step: dict[str, Any]) -> bool:
        scene = step.get("scene")
        if "scene" not in step:
            return False
        self.transitive.append(
            f"scene:{scene}" if isinstance(scene, str) else "scene:dynamic"
        )
        return True

    def _record_buttons(self, service: str, step: dict[str, Any]) -> None:
        if not service.startswith("notify."):
            return
        data = step.get("data")
        payload = data.get("data") if isinstance(data, dict) else None
        actions = payload.get("actions") if isinstance(payload, dict) else None
        if not isinstance(actions, list):
            return
        for item in actions:
            if isinstance(item, dict) and isinstance(item.get("action"), str):
                self.buttons.add(item["action"][:64])

    # -- traversal ---------------------------------------------------------

    def _step_is_effect(self, step: Any, path: str) -> bool:
        if not isinstance(step, dict):
            raise _Unbounded("step")
        found = False
        if "action" in step or "service" in step or "service_template" in step:
            found = self._call_effects(step, path) or found
        found = self._device_effect(step, path) or found
        found = self._scene_effect(step) or found
        return found

    def contains_effect(self, steps: list[Any]) -> bool:
        """Whether any step in ``steps`` (recursively) is or may be an effect."""

        key = id(steps)
        cached = self._effect_memo.get(key)
        if cached is not None and cached[0] is steps:
            return cached[1]
        result = False
        for step in steps:
            if not isinstance(step, dict):
                raise _Unbounded("step")
            if _effect_shape(step):
                result = True
                break
            if any(
                self.contains_effect(_as_steps(child))
                for _label, child, _loop in _children(step)
            ):
                result = True
                break
        self._effect_memo[key] = (steps, result)
        return result

    def walk(self, steps: Any, path: str, effect_follows: bool, depth: int) -> list[Any]:
        if depth > MAX_DEPTH:
            raise _Unbounded("depth")
        steps = _as_steps(steps)
        normalized: list[Any] = []
        for index, step in enumerate(steps):
            self.steps += 1
            if self.steps > MAX_STEPS:
                raise _Unbounded("steps")
            here = f"{path}[{index}]"
            if not isinstance(step, dict):
                raise _Unbounded("step")
            later = effect_follows or self.contains_effect(steps[index + 1 :])
            self._step_is_effect(step, here)
            service, dynamic = _step_service(step)
            if service is not None and not dynamic and is_reference_only_service(service):
                non_aborting = step.get("continue_on_error") is True
                if non_aborting and not later:
                    # A non-aborting reference step that no effect follows can
                    # neither gate nor delay a protected effect (OD-10).
                    continue
                if later:
                    # Review H2: a reference step that can run before an
                    # effect is compared in full, nested data included; any
                    # change can change whether the effect is reached.
                    normalized.append(_reference_step(step))
                else:
                    normalized.append(_reference_signature(service, step))
                continue
            children = list(_children(step))
            # Parallel branches run concurrently: an effect in any branch can
            # follow (or be cut short by) a reference step in a sibling.
            parallel_effect = "parallel" in step and any(
                self.contains_effect(_as_steps(child))
                for label, child, _loops in children
                if label.startswith("parallel[")
            )
            walked: dict[str, list[Any]] = {}
            for label, child, loops in children:
                child_steps = _as_steps(child)
                follows = (
                    later
                    or parallel_effect
                    or (loops and self.contains_effect(child_steps))
                )
                walked[label] = self.walk(
                    child_steps, f"{here}.{label}", follows, depth + 1
                )
            normalized.append(_rebuild(step, walked))
        return normalized


def _effect_shape(step: dict[str, Any]) -> bool:
    """Structural (table-only) test used for the "effect follows" rule.

    Every call that is not reference-only, every device action, and every
    scene activation may be an effect, so a non-aborting reference step
    before it is kept (conservative OD-10). This over-approximates; it can
    only make a comparison ``changed``, never ``equivalent``.
    """

    service, dynamic = _step_service(step)
    if dynamic:
        return True
    if service is not None:
        return not is_reference_only_service(service)
    return "scene" in step or bool(
        isinstance(step.get("domain"), str) and "device_id" in step and "type" in step
    )


def _reference_step(step: dict[str, Any]) -> dict[str, Any]:
    """Full normalized form of a reference step that an effect may follow.

    Only the display ``alias`` is dropped; every behavioral field is kept.
    """

    return {
        "reference_step": {
            key: value for key, value in step.items() if key not in _DISPLAY_STEP_KEYS
        }
    }


def _reference_signature(service: str, step: dict[str, Any]) -> dict[str, Any]:
    """Signature of a reference step after which no protected effect can run.

    Everything is kept except the ``reference_step_after_effects`` rows of
    :data:`IGNORABLE_DIFFERENCES`: the display alias, template-free
    ``message``/``title`` text, the nested ``data.data`` payload, where and
    in which list form the entity ids are written, and the ``service`` key
    spelling. Every other field, including non-entity selectors and a
    non-mapping ``data``, is kept verbatim.
    """

    entities, dynamic, _other = _call_targets(step, include_data_entity=True)
    kept: dict[str, Any] = {}
    for key, value in step.items():
        if key in _DISPLAY_STEP_KEYS or key in {"action", "service", "entity_id"}:
            continue
        if key == "target" and isinstance(value, dict):
            remainder = _without(value, frozenset({"entity_id"}))
            if remainder:
                kept["target"] = remainder
        elif key == "data" and isinstance(value, dict):
            remainder = {
                name: item
                for name, item in value.items()
                if name not in {"data", "entity_id"}
                and not (
                    name in _DISPLAY_DATA_KEYS
                    and isinstance(item, str)
                    and not _has_template(item)
                )
            }
            if remainder:
                kept["data"] = remainder
        else:
            kept[key] = value
    return {
        "reference_call": service,
        "entity_ids": entities,
        "dynamic_target": dynamic,
        "step": kept,
    }


def _blueprint_inputs(config: dict[str, Any]) -> list[tuple[str, str]]:
    blueprint = config.get("use_blueprint")
    if blueprint is None:
        return []
    found: list[tuple[str, str]] = []
    pending = [blueprint.get("input") if isinstance(blueprint, dict) else blueprint]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and "." in value and not _has_template(value):
            reason = protected_object_reason(value)
            if reason is not None:
                found.append((value.strip().lower(), reason))
    return found


def _unlock_or_disarm_scan(
    resource_type: str,
    configs: Iterable[Any],
    called_scripts: Any,
) -> bool:
    """Whether an unlock or disarm is, or may be, present anywhere (OD-6(b)).

    Review H3: this floor is computed first and independently of every
    comparison bound, so a comparison that is incomplete still reports it.
    The scan is iterative and grammar-free: any mapping outside a call's
    argument subtrees that names ``lock.unlock`` or
    ``alarm_control_panel.alarm_disarm``, a templated or ``service_template``
    service, a lock unlock or alarm disarm device action, or a script call
    that the snapshot does not resolve, counts. Every captured called script
    is scanned too. Exceeding the scan budget or malformed input counts as
    present.
    """

    callees = _CalledScripts(called_scripts)
    pending: list[Any] = [
        config.get(_BODY_KEY[resource_type]) if isinstance(config, dict) else config
        for config in configs
        if config is not None
    ]
    for entry in callees.scripts.values():
        if not isinstance(entry, dict) or "configs" not in entry:
            # An unresolved callee may unlock or disarm.
            return True
        configs_value = entry["configs"]
        if not isinstance(configs_value, list):
            return True
        pending.extend(
            config.get("sequence") if isinstance(config, dict) else config
            for config in configs_value
        )
    visited = 0
    try:
        while pending:
            value = pending.pop()
            visited += 1
            if visited > MAX_FLOOR_SCAN_NODES:
                return True
            if isinstance(value, list):
                pending.extend(value)
                continue
            if not isinstance(value, dict):
                continue
            service, dynamic = _step_service(value)
            if dynamic or service in SAFETY_CRITICAL_SERVICES:
                return True
            if (value.get("domain"), value.get("type")) in _SAFETY_CRITICAL_DEVICE_ACTIONS:
                return True
            if service is not None:
                keys, entities, unresolved = called_script_call_targets(service, value)
                if unresolved:
                    return True
                for entity_id in entities:
                    key, _reason = callees.entity_key(entity_id)
                    if key is None:
                        return True
                    keys = [*keys, key]
                if any(key not in callees.scripts for key in keys):
                    return True
            pending.extend(
                item
                for key, item in value.items()
                if key not in _FLOOR_SCAN_ARGUMENT_KEYS
            )
    except (RecursionError, TypeError, ValueError):
        return True
    return False


def _analyze(
    resource_type: str,
    config: dict[str, Any] | None,
    callees: _CalledScripts | None = None,
) -> _Side:
    if config is None:
        return _Side([], [], [], set(), False, None)
    if not isinstance(config, dict):
        raise _Unbounded("config")
    analyzer = _Analyzer(callees)
    body = config.get(_BODY_KEY[resource_type], [])
    structure = analyzer.walk(body, _BODY_KEY[resource_type], False, 0)
    for entity_id, _reason in _blueprint_inputs(config):
        analyzer._add_unresolved(entity_id, "unresolved_blueprint_input", False)
    excluded = _CONTEXT_EXCLUDED_KEYS[resource_type]
    context = {key: value for key, value in config.items() if key not in excluded}
    return _Side(
        analyzer.effects,
        analyzer.unresolved,
        analyzer.transitive,
        analyzer.buttons,
        analyzer.relevant,
        {"context": context, "body": structure},
    )


@dataclass(frozen=True)
class PhysicalEffectComparison:
    verdict: str
    reason_codes: tuple[str, ...]
    projection: dict[str, Any] | None
    projection_hash: str | None
    acknowledgement_objects: tuple[str, ...]
    # The independent OD-6(b) floor scan (review H3); see
    # :func:`_unlock_or_disarm_scan`.
    floor: bool = False

    @property
    def protected(self) -> bool:
        return self.verdict != VERDICT_NONE

    @property
    def bounds_exceeded(self) -> bool:
        """Whether a reviewed bound was exceeded, so the result is incomplete."""

        return bool(BOUNDS_EXCEEDED_REASONS.intersection(self.reason_codes))

    @property
    def acknowledgement_required(self) -> bool:
        return bool(self.acknowledgement_objects)

    @property
    def unlock_or_disarm(self) -> bool:
        """Whether any row is, or may be, an unlock or disarm effect (OD-6(b))."""

        rows = (self.projection or {}).get("rows", [])
        return self.floor or any(
            _UNLOCK_OR_DISARM in row.get("reason_codes", []) for row in rows
        )


NONE_COMPARISON = PhysicalEffectComparison(VERDICT_NONE, (), None, None, ())


def _effect_summary(effects: list[_Effect]) -> list[dict[str, Any]]:
    return [
        {
            "service": effect.service[:96],
            "position": effect.path[:MAX_PATH_LENGTH],
            "data_fields": list(effect.data_keys),
        }
        for effect in effects[:MAX_EFFECTS_PER_ROW]
    ]


def _called_script_fingerprints(called_scripts: Any) -> dict[str, Any] | None:
    """Content fingerprints of every captured callee (review H5).

    Bound into the comparison projection (and so the projection hash), so
    the evidence names the exact called-script content it was computed
    from; dispatch re-reads the callees and refuses a stale snapshot.
    """

    if not isinstance(called_scripts, dict) or called_scripts.get("model") != CALLED_SCRIPT_MODEL:
        return None
    scripts = called_scripts.get("scripts")
    entities = called_scripts.get("entities")
    return {
        "snapshot": stable_hash(called_scripts),
        "scripts": {
            str(key): stable_hash(value)
            for key, value in sorted((scripts if isinstance(scripts, dict) else {}).items())
        },
        "entities": {
            str(key): stable_hash(value)
            for key, value in sorted((entities if isinstance(entities, dict) else {}).items())
        },
    }


def _unbounded_comparison(
    resource_type: str,
    action: str,
    reason: str,
    *,
    floor: bool,
    fingerprints: dict[str, Any] | None,
) -> PhysicalEffectComparison:
    row = {
        "object_id": "configuration",
        "verdict": ROW_UNRESOLVED,
        "protection": "comparison_bounds",
        "reason_codes": [reason] + ([_UNLOCK_OR_DISARM] if floor else []),
        "before": [],
        "after": [],
        "acknowledgement_required": True,
    }
    return _finish(
        resource_type, action, VERDICT_UNRESOLVED, [reason], [row],
        set(), set(), [], [], floor=floor, fingerprints=fingerprints,
    )


def _finish(
    resource_type: str,
    action: str,
    verdict: str,
    reasons: list[str],
    rows: list[dict[str, Any]],
    buttons_before: set[str],
    buttons_after: set[str],
    transitive_before: list[str],
    transitive_after: list[str],
    *,
    floor: bool,
    fingerprints: dict[str, Any] | None,
) -> PhysicalEffectComparison:
    projection: dict[str, Any] = {
        "model": COMPARISON_MODEL,
        "resource_type": resource_type,
        "action": action,
        "verdict": verdict,
        "reason_codes": sorted(set(reasons)),
        "rows": rows,
        "notification_buttons": {
            "added": sorted(buttons_after - buttons_before)[:MAX_BUTTON_IDS],
            "removed": sorted(buttons_before - buttons_after)[:MAX_BUTTON_IDS],
        },
        "transitive_calls": {
            "before": sorted(set(transitive_before))[:MAX_ROWS],
            "after": sorted(set(transitive_after))[:MAX_ROWS],
        },
    }
    if fingerprints is not None:
        projection["called_script_fingerprints"] = fingerprints
    if floor:
        projection["unlock_or_disarm_floor"] = True
    acknowledgement = tuple(
        sorted(
            row["object_id"] for row in rows if row["acknowledgement_required"]
        )
    )
    return PhysicalEffectComparison(
        verdict,
        tuple(sorted(set(reasons))),
        projection,
        stable_hash(projection),
        acknowledgement,
        floor,
    )


def compare_physical_effects(
    resource_type: str,
    action: str,
    normalized_current: dict[str, Any] | None,
    normalized_proposed: dict[str, Any] | None,
    called_scripts: dict[str, Any] | None = None,
) -> PhysicalEffectComparison:
    """Compare protected effects of one automation or script operation.

    ``called_scripts`` is the operation's captured called-script snapshot;
    without it every script call is ``unresolved``.
    """

    if resource_type not in SUPPORTED_RESOURCE_TYPES:
        return NONE_COMPARISON
    if action not in SUPPORTED_ACTIONS:
        # HAMCP-155: "not a create or update" never means "no protected
        # effect". An automation or script operation this comparison does
        # not model is unresolved, so policy cannot treat it as ordinary.
        return _unbounded_comparison(
            resource_type,
            str(action)[:32],
            "comparison_action_unsupported",
            floor=True,
            fingerprints=None,
        )
    if action == "delete":
        # A delete's proposal is its closed intent; the after side is absence,
        # so every protected effect of the pre-image is removed (design §3.1).
        normalized_proposed = None
    # Review H3: the unlock/disarm floor comes first, independent of bounds.
    try:
        floor = _unlock_or_disarm_scan(
            resource_type, (normalized_current, normalized_proposed), called_scripts
        )
    except Exception:  # noqa: BLE001 - an unscannable input keeps the floor
        floor = True
    fingerprints = _called_script_fingerprints(called_scripts)
    try:
        callees = _CalledScripts(called_scripts)
        before = _analyze(resource_type, normalized_current, callees)
        after = _analyze(resource_type, normalized_proposed, callees)
    except (_Unbounded, RecursionError, TypeError, ValueError) as exc:
        reason = (
            "comparison_bounds_exceeded"
            if isinstance(exc, (_Unbounded, RecursionError))
            else "comparison_input_invalid"
        )
        return _unbounded_comparison(
            resource_type, action, reason, floor=floor, fingerprints=fingerprints
        )
    if not before.relevant and not after.relevant:
        return replace(NONE_COMPARISON, floor=floor)

    reasons: list[str] = []
    unresolved = before.unresolved + after.unresolved
    if action == "delete":
        # Every protected effect of the pre-image is removed.
        reasons.append("configuration_deleted")
    elif before.structure is None or after.structure is None:
        # A create (or a deleted side) has no prior reachability to preserve,
        # so every protected effect it carries is new.
        reasons.append("configuration_created")
    else:
        if before.structure["context"] != after.structure["context"]:
            reasons.append("reachability_context_changed")
            if before.structure["context"].get("mode") != after.structure["context"].get("mode"):
                reasons.append("mode_changed")
        if before.structure["body"] != after.structure["body"]:
            reasons.append("run_structure_changed")
    effect_list_before = [(e.service, e.object_id) for e in before.effects]
    effect_list_after = [(e.service, e.object_id) for e in after.effects]
    if effect_list_before != effect_list_after:
        reasons.append("protected_effects_changed")
    if unresolved:
        verdict = VERDICT_UNRESOLVED
        reasons.extend(sorted({code for _key, code, _floor in unresolved}))
    elif reasons:
        verdict = VERDICT_CHANGED
    else:
        verdict = VERDICT_EQUIVALENT

    objects: dict[str, dict[str, list[_Effect]]] = {}
    for side, effects in (("before", before.effects), ("after", after.effects)):
        for effect in effects:
            objects.setdefault(effect.object_id, {"before": [], "after": []})[side].append(effect)
    unresolved_rows: dict[str, tuple[set[str], bool]] = {}
    for key, code, floor in unresolved:
        codes, seen_floor = unresolved_rows.setdefault(key[:MAX_OBJECT_ID_LENGTH], (set(), False))
        codes.add(code)
        unresolved_rows[key[:MAX_OBJECT_ID_LENGTH]] = (codes, seen_floor or floor)
    if len(objects) + len(unresolved_rows) > MAX_ROWS or any(
        len(object_id) > MAX_OBJECT_ID_LENGTH for object_id in objects
    ):
        return _unbounded_comparison(
            resource_type, action, "comparison_bounds_exceeded",
            floor=floor, fingerprints=fingerprints,
        )
    rows: list[dict[str, Any]] = []
    for object_id in sorted(objects):
        sides = objects[object_id]
        floor = any(effect.floor for effect in sides["before"] + sides["after"])
        protection = (sides["after"] or sides["before"])[0].reason
        if not sides["before"]:
            row_verdict = ROW_ADDED
        elif not sides["after"]:
            row_verdict = ROW_REMOVED
        elif verdict == VERDICT_EQUIVALENT:
            row_verdict = ROW_EQUIVALENT
        else:
            row_verdict = ROW_CHANGED
        row_reasons = []
        if floor:
            # OD-6(b): an unlock or disarm effect always needs the per-object
            # acknowledgement, even when the comparison is equivalent.
            row_reasons.append(_UNLOCK_OR_DISARM)
        rows.append(
            {
                "object_id": object_id,
                "verdict": row_verdict,
                "protection": protection,
                "reason_codes": row_reasons,
                "before": _effect_summary(sides["before"]),
                "after": _effect_summary(sides["after"]),
                "acknowledgement_required": bool(
                    row_verdict != ROW_EQUIVALENT or floor
                ),
            }
        )
    for key in sorted(unresolved_rows):
        codes, floor = unresolved_rows[key]
        if key in objects:
            # The same name is also a resolved effect; its row already
            # requires the acknowledgement (the verdict is unresolved).
            continue
        rows.append(
            {
                "object_id": key,
                "verdict": ROW_UNRESOLVED,
                "protection": "effect_cannot_be_determined",
                "reason_codes": sorted(codes) + ([_UNLOCK_OR_DISARM] if floor else []),
                "before": [],
                "after": [],
                "acknowledgement_required": True,
            }
        )
    if any(row["object_id"] in {r["object_id"] for r in rows[:i]} for i, row in enumerate(rows)):
        return _unbounded_comparison(
            resource_type, action, "comparison_object_collision",
            floor=floor, fingerprints=fingerprints,
        )
    return _finish(
        resource_type,
        action,
        verdict,
        reasons,
        rows,
        before.buttons,
        after.buttons,
        before.transitive,
        after.transitive,
        floor=floor,
        fingerprints=fingerprints,
    )


def compare_operation(operation: Any) -> PhysicalEffectComparison:
    """Recompute the comparison for one stored configuration operation."""

    return compare_physical_effects(
        str(getattr(operation, "resource_type", "")),
        str(getattr(operation, "action", "")),
        getattr(operation, "normalized_current_config", None),
        getattr(operation, "normalized_proposed_config", None),
        getattr(operation, "called_scripts", None),
    )


def called_script_references(
    resource_type: str, config: Any
) -> tuple[list[str], list[str]]:
    """Return the literal script keys and script entities a body calls.

    Used only to capture the called-script snapshot; the comparison
    resolves the same forms through :func:`called_script_call_targets`.
    Malformed or unbounded bodies return what was found so far; the
    comparison reports them itself.
    """

    keys: list[str] = []
    entities: list[str] = []
    if not isinstance(config, dict) or resource_type not in _BODY_KEY:
        return keys, entities
    pending: list[tuple[Any, int]] = [(config.get(_BODY_KEY[resource_type]), 0)]
    visited = 0
    while pending:
        value, depth = pending.pop()
        try:
            steps = _as_steps(value)
        except _Unbounded:
            continue
        for step in steps:
            visited += 1
            if visited > MAX_STEPS or depth > MAX_DEPTH or not isinstance(step, dict):
                continue
            service, dynamic = _step_service(step)
            if service is not None and not dynamic:
                found_keys, found_entities, _unresolved = called_script_call_targets(
                    service, step
                )
                keys.extend(key for key in found_keys if key not in keys)
                entities.extend(item for item in found_entities if item not in entities)
            try:
                children = list(_children(step))
            except _Unbounded:
                continue
            pending.extend((child, depth + 1) for _label, child, _loop in children)
    return keys, entities


def plan_acknowledgement_binding(operations: Iterable[Any]) -> dict[str, Any] | None:
    """Return the plan-level acknowledgement binding, or ``None``.

    The binding names every ``(operation_id, object_id)`` row that requires
    the safety-critical acknowledgement and hashes every protected
    comparison of the plan. It is recomputed from the immutable plan for
    challenge issue, decision, and dispatch.
    """

    comparisons = []
    objects = []
    for operation in sorted(operations, key=lambda item: item.order):
        comparison = compare_operation(operation)
        if not comparison.protected:
            continue
        comparisons.append(
            {
                "operation_id": operation.operation_id,
                "projection_hash": comparison.projection_hash,
            }
        )
        objects.extend(
            {"operation_id": operation.operation_id, "object_id": object_id}
            for object_id in comparison.acknowledgement_objects
        )
    if not objects:
        return None
    projection = {"model": COMPARISON_MODEL, "operations": comparisons}
    return {
        "projection_hash": stable_hash(projection),
        "objects": sorted(
            objects, key=lambda item: (item["operation_id"], item["object_id"])
        ),
    }


MAX_ACKNOWLEDGED_OBJECTS = 32


def acknowledgement_object_token(row: dict[str, str]) -> str:
    """Return the opaque form token naming one acknowledgement row.

    The Ingress review form submits one token per checked row. Tokens are
    derived from the exact ``(operation_id, object_id)`` pair so a submission
    can only name rows the immutable plan's recomputed binding contains.
    """

    return stable_hash(
        {"operation_id": row["operation_id"], "object_id": row["object_id"]}
    )[:32]
