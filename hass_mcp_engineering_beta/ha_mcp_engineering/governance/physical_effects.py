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
- ``unresolved``: the operation has a protected effect, but some service,
  target, blueprint input or structure cannot be bounded by the reviewed
  grammar. Uncertainty is disclosed and acknowledged, never treated as
  equivalence.

Only closed, reviewed tables are used. An unknown service acting on a
protected object counts as an effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

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
SUPPORTED_ACTIONS = frozenset({"create", "update"})

# Reference-only services (risk.REFERENCE_ONLY_*) never act on the entity
# they mention (D2.1/D4.1).
# Services whose domain makes every literal target a potential protected
# object. Today's floor (OD-7, D5 follows separately) is the lock, alarm and
# valve domains plus covers and water controls matched by entity id.
_EFFECT_DOMAINS = frozenset({"cover", *SENSITIVE_ENTITY_DOMAINS})
_GENERIC_EFFECT_SERVICES = frozenset(
    {"homeassistant.turn_on", "homeassistant.turn_off", "homeassistant.toggle"}
)
_TRANSITIVE_EXACT_SERVICES = frozenset({"automation.trigger", "scene.turn_on"})
_TRANSITIVE_SERVICE_PREFIXES = ("script.",)
_SAFETY_CRITICAL_DEVICE_ACTIONS = frozenset(
    {("lock", "unlock"), ("alarm_control_panel", "disarm")}
)
_DISPLAY_DATA_KEYS = frozenset({"message", "title"})
_DISPLAY_STEP_KEYS = frozenset({"alias"})
_CONTEXT_EXCLUDED_KEYS = {
    "automation": frozenset({"action", "alias", "description"}),
    "script": frozenset({"sequence", "alias", "description", "icon"}),
}
_BODY_KEY = {"automation": "action", "script": "sequence"}
_TARGET_SELECTORS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")

MAX_STEPS = 4096
MAX_DEPTH = 24
MAX_ROWS = 32
MAX_EFFECTS_PER_ROW = 16
MAX_PATH_LENGTH = 160
MAX_OBJECT_ID_LENGTH = 255
MAX_BUTTON_IDS = 32


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
    """Return ``(service, dynamic)`` for one call step."""

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
    data = step.get("data")
    if include_data_entity and isinstance(data, dict) and "entity_id" in data:
        values, is_dynamic = _split_entity_values(data["entity_id"])
        entities.extend(values)
        dynamic = dynamic or is_dynamic
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
    unresolved: list[tuple[str, str]]  # (object key, reason code)
    potential_unresolved: list[tuple[str, str]]
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
            if isinstance(branch, dict) and set(branch) <= {"sequence", "alias", "enabled"}:
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


def _shell(step: dict[str, Any]) -> dict[str, Any]:
    """Return the step without nested step lists or display aliases."""

    shell = {k: v for k, v in step.items() if k not in _DISPLAY_STEP_KEYS}
    if isinstance(shell.get("choose"), list):
        shell["choose"] = [
            {k: v for k, v in option.items() if k not in {"sequence", "alias"}}
            if isinstance(option, dict)
            else option
            for option in shell["choose"]
        ]
    elif isinstance(shell.get("choose"), dict):
        shell["choose"] = {
            k: v for k, v in shell["choose"].items() if k not in {"sequence", "alias"}
        }
    if "choose" in step:
        shell.pop("default", None)
    for key in ("then", "else", "parallel"):
        shell.pop(key, None)
    if isinstance(shell.get("repeat"), dict):
        shell["repeat"] = {
            k: v for k, v in shell["repeat"].items() if k != "sequence"
        }
    if "sequence" in shell and not {"choose", "repeat", "parallel", "if"}.intersection(step):
        shell.pop("sequence")
    return shell


class _Analyzer:
    def __init__(self) -> None:
        self.steps = 0
        self.effects: list[_Effect] = []
        self.unresolved: list[tuple[str, str]] = []
        self.potential_unresolved: list[tuple[str, str]] = []
        self.transitive: list[str] = []
        self.buttons: set[str] = set()
        self.relevant = False
        self._effect_memo: dict[int, bool] = {}

    # -- classification of one step ---------------------------------------

    def _call_effects(self, step: dict[str, Any], path: str) -> bool:
        """Record effects of one call step; return whether it is an effect."""

        service, dynamic = _step_service(step)
        if dynamic:
            self.potential_unresolved.append((f"dynamic_service@{path}", "dynamic_service"))
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
        unresolved_target = dynamic_target or other_selector or (
            not entities and domain in _EFFECT_DOMAINS
        )
        if unresolved_target and (sensitive_service or domain in _EFFECT_DOMAINS or service in _GENERIC_EFFECT_SERVICES):
            key = f"{service}@{path}"
            if sensitive_service:
                self.relevant = True
                self.unresolved.append((key, "unresolved_protected_target"))
            else:
                self.potential_unresolved.append((key, "unresolved_effect_target"))
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

    def _device_effect(self, step: dict[str, Any], path: str) -> bool:
        domain = step.get("domain")
        device_id = step.get("device_id")
        if not isinstance(domain, str) or "type" not in step:
            return False
        kind = step.get("type")
        if not isinstance(device_id, str) or not isinstance(kind, str):
            if domain in SENSITIVE_ENTITY_DOMAINS:
                self.relevant = True
                self.unresolved.append((f"device:{domain}@{path}", "unresolved_protected_target"))
                return True
            return False
        entity = step.get("entity_id")
        reason = None
        object_id = f"device:{device_id}"
        if isinstance(entity, str) and entity and not _has_template(entity):
            reason = protected_object_reason(entity)
            object_id = entity.strip().lower()
        if reason is None and domain in SENSITIVE_ENTITY_DOMAINS:
            reason = f"sensitive_domain_{domain}"
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
                (domain, kind) in _SAFETY_CRITICAL_DEVICE_ACTIONS,
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
        if "action" in step or "service" in step:
            found = self._call_effects(step, path) or found
        found = self._device_effect(step, path) or found
        found = self._scene_effect(step) or found
        return found

    def contains_effect(self, steps: list[Any]) -> bool:
        """Whether any step in ``steps`` (recursively) is or may be an effect."""

        key = id(steps)
        cached = self._effect_memo.get(key)
        if cached is not None:
            return cached
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
        self._effect_memo[key] = result
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
                normalized.append(_reference_signature(service, step))
                continue
            node: dict[str, Any] = {"step": _shell(step)}
            for label, child, loops in _children(step):
                child_steps = _as_steps(child)
                follows = later or (loops and self.contains_effect(child_steps))
                node[label] = self.walk(child_steps, f"{here}.{label}", follows, depth + 1)
            normalized.append(node)
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


def _reference_signature(service: str, step: dict[str, Any]) -> dict[str, Any]:
    entities, dynamic, other = _call_targets(step, include_data_entity=True)
    data = step.get("data") if isinstance(step.get("data"), dict) else {}
    kept_data = {
        key: value
        for key, value in data.items()
        if key not in _DISPLAY_DATA_KEYS and key not in {"data", "entity_id"}
    }
    templated_text = {
        key: value
        for key, value in data.items()
        if key in _DISPLAY_DATA_KEYS
        and (not isinstance(value, str) or _has_template(value))
    }
    others = {
        key: value
        for key, value in step.items()
        if key
        not in {"action", "service", "data", "target", "alias", *_TARGET_SELECTORS}
    }
    return {
        "reference_call": service,
        "entity_ids": entities,
        "dynamic_target": dynamic,
        "other_selectors": other,
        "target": step.get("target") if not isinstance(step.get("target"), dict) else {
            k: v for k, v in step["target"].items() if k != "entity_id"
        },
        "data": kept_data,
        "templated_text": templated_text,
        "step": others,
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


def _analyze(resource_type: str, config: dict[str, Any] | None) -> _Side:
    if config is None:
        return _Side([], [], [], [], set(), False, None)
    if not isinstance(config, dict):
        raise _Unbounded("config")
    analyzer = _Analyzer()
    body = config.get(_BODY_KEY[resource_type], [])
    structure = analyzer.walk(body, _BODY_KEY[resource_type], False, 0)
    for entity_id, _reason in _blueprint_inputs(config):
        analyzer.relevant = True
        analyzer.unresolved.append((entity_id, "unresolved_blueprint_input"))
    excluded = _CONTEXT_EXCLUDED_KEYS[resource_type]
    context = {key: value for key, value in config.items() if key not in excluded}
    return _Side(
        analyzer.effects,
        analyzer.unresolved,
        analyzer.potential_unresolved,
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

    @property
    def protected(self) -> bool:
        return self.verdict != VERDICT_NONE

    @property
    def acknowledgement_required(self) -> bool:
        return bool(self.acknowledgement_objects)


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


def _unbounded_comparison(resource_type: str, action: str, reason: str) -> PhysicalEffectComparison:
    row = {
        "object_id": "configuration",
        "verdict": ROW_UNRESOLVED,
        "protection": "comparison_bounds",
        "reason_codes": [reason],
        "before": [],
        "after": [],
        "acknowledgement_required": True,
    }
    return _finish(resource_type, action, VERDICT_UNRESOLVED, [reason], [row], set(), set(), [], [])


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
) -> PhysicalEffectComparison:
    projection = {
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
    )


def compare_physical_effects(
    resource_type: str,
    action: str,
    normalized_current: dict[str, Any] | None,
    normalized_proposed: dict[str, Any] | None,
) -> PhysicalEffectComparison:
    """Compare protected effects of one automation or script operation."""

    if resource_type not in SUPPORTED_RESOURCE_TYPES or action not in SUPPORTED_ACTIONS:
        return NONE_COMPARISON
    try:
        before = _analyze(resource_type, normalized_current)
        after = _analyze(resource_type, normalized_proposed)
    except (_Unbounded, RecursionError, TypeError, ValueError) as exc:
        reason = (
            "comparison_bounds_exceeded"
            if isinstance(exc, (_Unbounded, RecursionError))
            else "comparison_input_invalid"
        )
        return _unbounded_comparison(resource_type, action, reason)
    if not before.relevant and not after.relevant:
        return NONE_COMPARISON

    reasons: list[str] = []
    unresolved = before.unresolved + after.unresolved
    # Inside an operation that has protected effects, any call that might act
    # on an object the grammar cannot name is unresolved, not ignored.
    unresolved += before.potential_unresolved + after.potential_unresolved
    if before.structure is None or after.structure is None:
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
        reasons.extend(sorted({code for _key, code in unresolved}))
    elif reasons:
        verdict = VERDICT_CHANGED
    else:
        verdict = VERDICT_EQUIVALENT

    objects: dict[str, dict[str, list[_Effect]]] = {}
    for side, effects in (("before", before.effects), ("after", after.effects)):
        for effect in effects:
            objects.setdefault(effect.object_id, {"before": [], "after": []})[side].append(effect)
    if len(objects) + len(unresolved) > MAX_ROWS or any(
        len(object_id) > MAX_OBJECT_ID_LENGTH for object_id in objects
    ):
        return _unbounded_comparison(resource_type, action, "comparison_bounds_exceeded")
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
            row_reasons.append("unlock_or_disarm_effect")
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
    for key, code in sorted(set(unresolved)):
        if key in objects:
            continue
        rows.append(
            {
                "object_id": key[:MAX_OBJECT_ID_LENGTH],
                "verdict": ROW_UNRESOLVED,
                "protection": "effect_cannot_be_determined",
                "reason_codes": [code],
                "before": [],
                "after": [],
                "acknowledgement_required": True,
            }
        )
    if any(row["object_id"] in {r["object_id"] for r in rows[:i]} for i, row in enumerate(rows)):
        return _unbounded_comparison(resource_type, action, "comparison_object_collision")
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
    )


def compare_operation(operation: Any) -> PhysicalEffectComparison:
    """Recompute the comparison for one stored configuration operation."""

    return compare_physical_effects(
        str(getattr(operation, "resource_type", "")),
        str(getattr(operation, "action", "")),
        getattr(operation, "normalized_current_config", None),
        getattr(operation, "normalized_proposed_config", None),
    )


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
