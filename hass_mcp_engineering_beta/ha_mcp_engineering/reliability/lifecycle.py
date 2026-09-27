"""Pure, bounded static lifecycle inspection; no evaluation, IO or execution authority.

Semantics and source/test references: docs/AUTOMATION_LIFECYCLE_ANALYSIS.md.
Only recognized structural positions are traversed. Counts describe sites, not runs.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
import hashlib
import json
import math
import re

from .models import stable_id

MAX_ITEMS = 10_000
MAX_DEPTH = 32
MAX_ENTRIES = 32
MAX_JSON_BYTES = 16_384
MAX_PATH = 256
MAX_SCALAR = 2_000
MODEL = "lifecycle-static-v1"
_ACTION_KEYS = (
    "delay", "wait_template", "condition", "and", "or", "not", "event",
    "device_id", "scene", "repeat", "choose", "wait_for_trigger", "variables",
    "if", "action", "service", "service_template", "stop", "parallel",
    "sequence", "set_conversation_response",
)
_DURATION_KEYS = ("days", "hours", "minutes", "seconds", "milliseconds")
_QUALIFICATION = "Conditional on reaching this path and, for a wait, actually suspending; not an observed run or failure."
_HAZARDS = {
    "trigger_for_interruption": (
        "Pending qualification can be lost when the trigger is detached or Core restarts; not every reload detaches an unchanged automation.",
        "Compare intended qualification with a persisted deadline and explicit startup reconciliation; verify restoration semantics before changing anything.",
    ),
    "suspended_sequence_interruption": (
        "An affected stop, unload or Core restart can end a suspended run before later actions; unchanged automations can survive a reload.",
        "Inspect the actions after this suspension and whether an explicit durable deadline and reconciliation path preserve their intent.",
    ),
}
_NOTES = {
    "repeat_reconciliation_review": "Review termination and reconciliation if this conditional repeat runs; repetition alone proves neither long runtime nor nontermination.",
    "scheduled_trigger_catchup_review": "Review startup catch-up for this schedule; no missed occurrence or downtime has been observed.",
    "dynamic_suspension_review": "Suspension syntax is present, but its dynamic or unsupported inputs were not evaluated.",
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class _Scanner:
    def __init__(self, automation_id):
        self.automation_id = automation_id
        self.items = 0
        self.exhausted = False
        self.findings = []
        self.notes = []
        self.gaps = []
        self.gap_counts = Counter()
        self.limits = set()
        self.suppressed = Counter()
        self.hazard_count = 0
        self.note_count = 0
        self.startup_count = 0
        self.digest = hashlib.sha256(MODEL.encode())

    def touch(self, path, depth):
        # Charge before rejecting depth. Never enumerate a rejected subtree.
        if self.exhausted:
            return False
        if self.items >= MAX_ITEMS:
            self.exhausted = True
            self.limits.add("structural_items")
            return False
        self.items += 1
        if depth > MAX_DEPTH:
            self.limits.add("depth")
            return False
        if len(path) > MAX_PATH:
            self.limits.add("path_length")
            return False
        return True

    def entry(self, kind, rule, path):
        # Only fixed categories/structural paths enter the digest or output.
        if len(path) > MAX_PATH:
            self.limits.add("path_length")
            return
        self.digest.update(_json((kind, rule, path)).encode())
        if kind == "hazard":
            self.hazard_count += 1
        elif kind == "note":
            self.note_count += 1
        else:
            self.gap_counts[rule] += 1
        size = len(self.findings) + len(self.notes) + len(self.gaps)
        if size >= MAX_ENTRIES:
            self.limits.add("retained_entries")
            # Preserve a positive even if earlier gaps/notes filled retention.
            if kind == "hazard" and not self.findings:
                (self.notes if self.notes else self.gaps).pop()
            else:
                return
        if kind == "gap":
            self.gaps.append({"category": rule, "configuration_path": path})
            return
        value = {
            "finding_id": stable_id("lifecycle", self.automation_id, kind, rule, path),
            "rule_id": rule, "configuration_path": path,
            "evidence_basis": "static_configuration",
            "reachability_qualification": _QUALIFICATION,
            "status": "possible" if kind == "hazard" else "review_note",
            "possible_consequence": _HAZARDS[rule][0] if kind == "hazard" else _NOTES[rule],
            "recommended_next_investigation": _HAZARDS[rule][1] if kind == "hazard" else "Inspect the cited syntax and required recovery behavior without assuming execution.",
            "derivation": {"rule_model": MODEL, "runtime_evidence_used": False},
        }
        (self.findings if kind == "hazard" else self.notes).append(value)

    def gap(self, category, path):
        self.entry("gap", category, path)

    def scalar(self, value, path, depth):
        if not self.touch(path, depth):
            return False
        if isinstance(value, str) and len(value) > MAX_SCALAR:
            self.limits.add("scalar_length")
            return False
        return True

    def enabled(self, value, path, depth, *, unresolved_category="enabled_unresolved"):
        if not self.scalar(value, path, depth):
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                if math.isfinite(value):
                    return value != 0
            except OverflowError:
                pass
        if isinstance(value, str):
            value = value.strip().lower()
            if value in ("1", "true", "yes", "on", "enable"):
                return True
            if value in ("0", "false", "no", "off", "disable"):
                return False
        if unresolved_category is not None:
            self.gap(unresolved_category, path)
        return None

    def duration(self, value, path, depth):
        if not self.scalar(value, path, depth):
            return None
        try:
            if isinstance(value, dict):
                if not value or len(value) > len(_DURATION_KEYS):
                    raise ValueError
                parts = {}
                for key, item in value.items():
                    # Unknown keys are never reflected into paths.
                    if not self.scalar(item, path, depth + 1):
                        return None
                    if key not in _DURATION_KEYS or not isinstance(item, (str, int, float)):
                        raise ValueError
                    parts[key] = float(item)
                result = timedelta(**parts).total_seconds()
            elif isinstance(value, (int, float, str)):
                if isinstance(value, str) and ":" in value:
                    sign = -1 if value.startswith("-") else 1
                    fields = (value[1:] if value.startswith(("-", "+")) else value).split(":")
                    if len(fields) not in (2, 3):
                        raise ValueError
                    result = (timedelta(hours=int(fields[0]), minutes=int(fields[1]),
                                        seconds=float(fields[2]) if len(fields) == 3 else 0) * sign).total_seconds()
                else:
                    result = timedelta(seconds=float(value)).total_seconds()
            else:
                raise ValueError
            if not math.isfinite(result) or result < 0:
                raise ValueError
            return result
        except (ValueError, TypeError, OverflowError):
            self.gap("duration_unresolved", path)
            return None

    def literal_template(self, value, path, depth):
        if not self.scalar(value, path, depth):
            return None
        # No template parsing/rendering, including apparently constant Jinja.
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        self.gap("condition_unresolved", path)
        return None

    def conditions(self, value, path, depth):
        if not self.touch(path, depth):
            return None
        if isinstance(value, list):
            unknown = False
            for index, item in enumerate(value):
                if self.exhausted:
                    return None
                result = self.conditions(item, f"{path}[{index}]", depth + 1)
                if result is False:
                    # Earlier unknown conditions may error; do not overstate reachability.
                    return None if unknown else False
                unknown |= result is None
            return None if unknown else True
        if not isinstance(value, dict):
            self.gap("condition_unresolved", path)
            return None
        if "enabled" in value:
            enabled = self.enabled(value["enabled"], path + ".enabled", depth + 1)
            if enabled is False:
                self.suppressed["disabled_condition"] += 1
                # Core's condition lists ignore disabled checkers (None).
                return True
            if enabled is None:
                return None
        kind = value.get("condition")
        if not self.scalar(kind, path + ".condition", depth + 1):
            return None
        if kind == "template":
            return self.literal_template(value.get("value_template"), path + ".value_template", depth + 1)
        if kind == "and":
            return self.conditions(value.get("conditions"), path + ".conditions", depth + 1)
        # Other conditions need runtime inputs or semantics outside this slice.
        self.gap("condition_unresolved", path)
        return None

    def section(self, config, singular, plural):
        if singular in config and plural in config:
            self.gap("ambiguous_structure", "$." + plural)
            return None
        return plural if plural in config else singular if singular in config else None

    def triggers(self, value, path, depth, *, emit=True):
        if not self.touch(path, depth):
            return None
        if isinstance(value, list):
            active, unknown = False, False
            for index, item in enumerate(value):
                if self.exhausted:
                    return None
                result = self.triggers(item, f"{path}[{index}]", depth + 1, emit=emit)
                active |= result is True
                unknown |= result is None
            return True if active else None if unknown else False
        if not isinstance(value, dict):
            self.gap("malformed_trigger", path)
            return None
        if "triggers" in value and len(value) == 1:
            return self.triggers(value["triggers"], path + ".triggers", depth + 1, emit=emit)
        enabled = self.enabled(value.get("enabled", True), path + ".enabled", depth + 1)
        if enabled is False:
            self.suppressed["disabled_subtrees"] += 1
            return False
        if "trigger" in value and "platform" in value:
            self.gap("ambiguous_structure", path)
            return None
        family_key = "trigger" if "trigger" in value else "platform"
        family = value.get(family_key)
        if not self.scalar(family, path + "." + family_key, depth + 1):
            return None
        if not isinstance(family, str) or family not in ("state", "numeric_state", "time", "event", "homeassistant"):
            self.gap("unsupported_trigger", path)
            return None
        if "for" in value:
            if family not in ("state", "numeric_state"):
                self.gap("unsupported_trigger", path)
            else:
                duration = self.duration(value["for"], path + ".for", depth + 1)
                if duration == 0:
                    self.suppressed["zero_duration_constructs"] += 1
                elif duration is not None and emit:
                    self.entry("hazard", "trigger_for_interruption", path + ".for")
        if family == "homeassistant" and value.get("event") == "start" and emit:
            self.startup_count += 1
        if family == "time" and emit:
            self.schedule(value.get("at"), path + ".at", depth + 1)
        return True if enabled is True else None

    def schedule(self, value, path, depth):
        if not self.touch(path, depth):
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                if self.exhausted:
                    break
                self.schedule(item, f"{path}[{index}]", depth + 1)
        elif isinstance(value, str) and len(value) <= MAX_SCALAR and re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,6})?)?", value):
            self.entry("note", "scheduled_trigger_catchup_review", path)
        else:
            if isinstance(value, str) and len(value) > MAX_SCALAR:
                self.limits.add("scalar_length")
            self.gap("schedule_unresolved", path)

    def sequence(self, value, path, depth):
        if not self.touch(path, depth):
            return None
        if isinstance(value, dict):
            return self.action(value, path, depth + 1)
        if not isinstance(value, list):
            self.gap("malformed_sequence", path)
            return None
        may_block = False
        for index, item in enumerate(value):
            if self.exhausted:
                break
            flow = self.action(item, f"{path}[{index}]", depth + 1)
            may_block |= flow == "may_block"
            if flow in ("stop", "block"):
                if index + 1 < len(value):
                    self.suppressed["unreachable_suffixes"] += 1
                return "block" if may_block else flow
        return None

    def action(self, value, path, depth):
        if not self.touch(path, depth):
            return None
        if not isinstance(value, dict):
            self.gap("malformed_action", path)
            return None
        enabled = self.enabled(value.get("enabled", True), path + ".enabled", depth + 1)
        if enabled is False:
            self.suppressed["disabled_subtrees"] += 1
            return None
        keys = [key for key in _ACTION_KEYS if key in value]
        if len(keys) != 1:
            self.gap("ambiguous_or_unsupported_action", path)
            return None
        key = keys[0]
        child = path + "." + key
        flow = None
        if key == "delay":
            duration = self.duration(value[key], child, depth + 1)
            if duration == 0:
                self.suppressed["zero_duration_constructs"] += 1
            elif duration is None:
                self.entry("note", "dynamic_suspension_review", child)
            else:
                self.entry("hazard", "suspended_sequence_interruption", child)
        elif key in ("wait_template", "wait_for_trigger"):
            flow = self.wait(value, key, path, depth)
        elif key in ("condition", "and", "or", "not"):
            decision = self.conditions(value, path, depth + 1)
            if decision is False:
                flow = "block"
            elif decision is None:
                flow = "may_block"
        elif key == "stop":
            flow = "stop"
        elif key == "sequence":
            result = self.sequence(value[key], child, depth + 1)
            flow = "stop" if result == "stop" else None
        elif key == "if":
            decision = self.conditions(value[key], child, depth + 1)
            for branch, reachable in (("then", decision is not False), ("else", decision is not True)):
                if branch not in value:
                    continue
                if not reachable:
                    self.suppressed["unreachable_subtrees"] += 1
                    continue
                result = self.sequence(value[branch], path + "." + branch, depth + 1)
                if decision is not None and result == "stop":
                    flow = "stop"
        elif key == "choose":
            flow = self.choose(value, path, depth)
        elif key == "repeat":
            flow = self.repeat(value[key], child, depth + 1)
        elif key == "parallel":
            parallel = value[key]
            if self.touch(child, depth + 1):
                listed = isinstance(parallel, list)
                if not listed:
                    parallel = (parallel,)
                for index, branch in enumerate(parallel):
                    if self.exhausted:
                        break
                    branch_path = f"{child}[{index}]" if listed else child
                    # Core awaits all siblings before propagating a branch stop.
                    # A failed condition ends only its branch; keep scanning peers.
                    if self.sequence(branch, branch_path, depth + 2) == "stop":
                        flow = "stop"
        elif key in ("action", "service", "service_template"):
            service = value[key]
            if self.scalar(service, child, depth + 1):
                if not isinstance(service, str) or not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", service):
                    self.gap("service_unresolved", child)
                elif service.startswith("script.") and service not in ("script.turn_off", "script.reload"):
                    self.gap("called_script_not_inspected", child)
        if flow == "stop" and key in ("parallel", "sequence", "if", "choose", "repeat") and "continue_on_error" in value:
            continuation_path = path + ".continue_on_error"
            continuation = self.enabled(value["continue_on_error"], continuation_path, depth + 1,
                                        unresolved_category=None)
            if continuation is not False:
                # A prior/competing error may replace the stop, then be swallowed
                # at this container. Exception outcomes are not statically solved.
                self.gap("container_error_continuation_unresolved", continuation_path)
                flow = None
        # Data, variables, aliases and service payloads are intentionally opaque.
        return flow if enabled is True else "may_block" if flow in ("block", "may_block") else None

    def wait(self, value, key, path, depth):
        timeout = self.duration(value["timeout"], path + ".timeout", depth + 1) if "timeout" in value else "absent"
        if key == "wait_template":
            ready = self.literal_template(value[key], path + "." + key, depth + 1)
            if ready is True:
                self.suppressed["satisfied_waits"] += 1
                return None
        else:
            ready = False
        if timeout == 0:
            self.suppressed["zero_duration_constructs"] += 1
            # A zero timeout normally continues. Only an explicit false aborts.
            continuation = self.enabled(value.get("continue_on_timeout", True), path + ".continue_on_timeout", depth + 1)
            return "stop" if continuation is False and ready is False else None
        if key == "wait_for_trigger":
            attached = self.triggers(value[key], path + "." + key, depth + 1, emit=False)
            if attached is False:
                self.suppressed["inactive_wait_triggers"] += 1
                return None
            if attached is None:
                ready = None
        if timeout is None or ready is None:
            self.entry("note", "dynamic_suspension_review", path + "." + key)
        else:
            self.entry("hazard", "suspended_sequence_interruption", path + "." + key)
        return None

    def choose(self, value, path, depth):
        choices = value["choose"]
        child = path + ".choose"
        if not self.touch(child, depth + 1):
            return None
        if not isinstance(choices, list):
            self.gap("malformed_choose", child)
            return None
        uncertain = False
        selected = False
        flow = None
        for index, branch in enumerate(choices):
            if self.exhausted:
                break
            branch_path = f"{child}[{index}]"
            if not self.touch(branch_path, depth + 2):
                continue
            if not isinstance(branch, dict):
                self.gap("malformed_choose", branch_path)
                uncertain = True
                continue
            decision = self.conditions(branch.get("conditions"), branch_path + ".conditions", depth + 3)
            if decision is False:
                self.suppressed["unreachable_subtrees"] += 1
                continue
            result = self.sequence(branch.get("sequence"), branch_path + ".sequence", depth + 3)
            if decision is True:
                selected = True
                if not uncertain and result == "stop":
                    flow = "stop"
                if index + 1 < len(choices):
                    self.suppressed["unreachable_suffixes"] += 1
                break
            uncertain = True
        if "default" in value:
            if selected:
                self.suppressed["unreachable_subtrees"] += 1
            elif not self.exhausted:
                result = self.sequence(value["default"], path + ".default", depth + 1)
                if not uncertain and result == "stop":
                    flow = "stop"
        return flow

    def repeat(self, value, path, depth):
        if not self.touch(path, depth):
            return
        if not isinstance(value, dict):
            self.gap("malformed_repeat", path)
            return
        modes = [key for key in ("count", "for_each", "while", "until") if key in value]
        if len(modes) != 1:
            self.gap("malformed_repeat", path)
            return
        key = modes[0]
        selector = value[key]
        entered = False
        if key in ("while", "until"):
            decision = self.conditions(selector, path + "." + key, depth + 1)
            if key == "while" and decision is False:
                self.suppressed["zero_iteration_subtrees"] += 1
                return
            entered = key == "until" or decision is True
            self.entry("note", "repeat_reconciliation_review", path)
        elif self.scalar(selector, path + "." + key, depth + 1):
            if key == "count":
                try:
                    if not isinstance(selector, (str, int, float)):
                        raise ValueError
                    count = int(selector)
                except (ValueError, TypeError, OverflowError):
                    self.gap("repeat_count_unresolved", path + ".count")
                else:
                    if count <= 0:
                        self.suppressed["zero_iteration_subtrees"] += 1
                        return
                    entered = True
            elif isinstance(selector, list):
                if not selector:
                    self.suppressed["zero_iteration_subtrees"] += 1
                    return
                entered = True
            else:
                self.gap("repeat_items_unresolved", path + ".for_each")
        result = self.sequence(value.get("sequence"), path + ".sequence", depth + 1)
        return "stop" if entered and result == "stop" else None

    def scan(self, config):
        if not self.touch("$", 0):
            return
        if not isinstance(config, dict):
            self.gap("malformed_configuration", "$")
            return
        if "use_blueprint" in config:
            self.gap("blueprint_not_expanded", "$.use_blueprint")
        key = self.section(config, "trigger", "triggers")
        if key:
            self.triggers(config[key], "$." + key, 1)
        condition_key = self.section(config, "condition", "conditions")
        allowed = self.conditions(config[condition_key], "$." + condition_key, 1) if condition_key else True
        key = self.section(config, "action", "actions")
        if key:
            if allowed is False:
                self.suppressed["unreachable_subtrees"] += 1
            else:
                self.sequence(config[key], "$." + key, 1)

    def public(self, fingerprint, timestamp, detail):
        value = {
            "model_version": MODEL, "scope": "one_automation_triggers_and_inline_actions",
            "evidence_basis": "static_configuration", "runtime_evidence_used": False,
            "hazards_detected": self.hazard_count > 0, "hazard_count": self.hazard_count,
            "review_note_count": self.note_count,
            "findings": self.findings, "review_notes": self.notes,
            "coverage": {"gap_counts": dict(sorted(self.gap_counts.items())), "gaps": self.gaps,
                         "structural_items_examined": self.items, "suppression_counts": dict(sorted(self.suppressed.items())),
                         "called_script_bodies": "not_inspected", "blueprint_expansion": "not_performed"},
            "startup_observation": {"literal_start_trigger_count": self.startup_count,
                                    "recovery_verified": False},
            "provenance": {"provider": "direct_ha_api", "derived_by": "engineering",
                           "source_type": "automation_config", "fallback_occurred": False},
            "analysis_timestamp": timestamp,
            "configuration_fingerprint": fingerprint,
            "configuration_fingerprint_kind": "sanitized_configuration",
            "atomic_snapshot": False,
            "limits": {"structural_items": MAX_ITEMS, "depth": MAX_DEPTH, "retained_entries": MAX_ENTRIES,
                       "json_bytes": MAX_JSON_BYTES, "path_characters": MAX_PATH, "scalar_characters": MAX_SCALAR},
            "display_omission": {"findings": 0, "review_notes": 0, "gaps": 0},
        }
        value["evidence_fingerprint"] = "0" * 64
        self.finish_status(value)
        # Bound the richest representation first: detail levels share coverage/counts.
        # Output size checks serialize only this already bounded, safe object.
        while len(_json(value).encode()) > MAX_JSON_BYTES:
            self.limits.add("output_bytes")
            entries = value["coverage"]["gaps"] or value["review_notes"] or value["findings"]
            if not entries:
                break  # Fixed metadata is well below the production 16 KiB ceiling.
            entries.pop()
            self.finish_status(value)
        value["evidence_fingerprint"] = hashlib.sha256(
            (self.digest.hexdigest() + _json(value)).encode()
        ).hexdigest()
        if detail == "summary":
            for key in ("findings", "review_notes"):
                value["display_omission"][key] = max(0, len(value[key]) - 3)
                value[key] = [
                    {k: v for k, v in item.items() if k not in ("recommended_next_investigation", "derivation")}
                    for item in value[key][:3]
                ]
            value["display_omission"]["gaps"] = max(0, len(value["coverage"]["gaps"]) - 3)
            value["coverage"]["gaps"] = value["coverage"]["gaps"][:3]
        elif detail == "standard":
            value["findings"] = [{k: v for k, v in item.items() if k != "derivation"} for item in value["findings"]]
            value["review_notes"] = [{k: v for k, v in item.items() if k != "derivation"} for item in value["review_notes"]]
        return value

    def finish_status(self, value):
        complete = not self.gap_counts and not self.limits
        value.update({
            "coverage_complete": complete, "truncated": bool(self.limits),
            "count_precision": "lower_bound" if self.limits else "exact",
            "limiting_reasons": sorted(self.limits),
            "assessment": "partial" if not complete else "potential_hazards" if self.hazard_count else "no_detected_hazards_in_scope",
        })


def analyze_lifecycle(configuration, *, automation_id, configuration_fingerprint, analysis_timestamp, detail_level):
    """Inspect one captured config without modifying it; return only safe bounded data."""
    scanner = _Scanner(automation_id)
    scanner.scan(configuration)
    return scanner.public(configuration_fingerprint, analysis_timestamp, detail_level)
