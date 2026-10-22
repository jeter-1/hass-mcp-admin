"""Planning rules for governed entity enable/disable and exposure (HAMCP-069B).

Input is validated before any Home Assistant read.  Planning reads each exact
registry entry, applies the exact refusals (design §2.4, OD-6, OD-10), binds
the evidence the approval reviews (config entry, protection, consumers), and
classifies each child under the existing F2 classes: standard, or
``elevated_admin`` through the existing elevated approval for protected
cases (OD-5, OD-9).  Nothing here dispatches.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..errors import AuthorizationError
from .entity_availability_exposure import (
    AVAILABILITY_RESOURCE_TYPE,
    BINDINGS_KEY,
    EXPOSURE_RESOURCE_TYPE,
    KNOWN_ASSISTANTS,
    PROVIDERS,
    WRITABLE_ASSISTANTS,
    entry_errors,
    entry_fingerprint,
    exposure_value,
    without_bindings,
)
from .entity_metadata import (
    EntityMetadataProviderError,
    effective_device_class,
    validate_entity_id,
)
from .entity_metadata_disclosure import EntityMetadataEvidenceUnavailable
from .entity_metadata_planning import EntityMetadataRefusal as Refusal
from ..dependency.models import OBLIGATION_LEDGER_MODEL
from .helper_dependency import (
    _causal_automation_sources,
    _obligation_targets_helper,
    home_assistant_version_admission,
)
from .models import ChangeRiskAssessment, RiskLevel
from .normalize import stable_hash
from .physical_effects import protected_object_reason

ALLOWED_INPUT_FIELDS = frozenset(
    {"operation", "entity_ids", "assistant", "expiration_minutes"}
)
OPERATION_TYPES = {
    "disable": AVAILABILITY_RESOURCE_TYPE,
    "enable": AVAILABILITY_RESOURCE_TYPE,
    "expose": EXPOSURE_RESOURCE_TYPE,
    "unexpose": EXPOSURE_RESOURCE_TYPE,
}
# OD-7 as amended (design §0.4): the existing shared bound of 8.
MAX_ENTITIES = 8
POLICY_TRIGGER = "entity_availability_exposure_policy"
FIELD_NOT_SUPPORTED = "entity_availability_exposure_field_not_supported"
INVALID_VALUE = "entity_availability_exposure_invalid_value"
DISCLOSURE_MODEL = "entity_availability_exposure_disclosure_v1"
# OD-4: every evidence gap is a protected dependency reason.
INCOMPLETE_PREFIX = "dependency_evidence_incomplete:"
INCOMPLETE_IMPACT_TEXT = "impact could not be fully determined"
ASSISTANT_NAMES = {
    "conversation": "Assist",
    "cloud.google_assistant": "Google Assistant",
    "cloud.alexa": "Alexa",
}
# Design §5: consumer kinds the dependency index cannot see.  Missing
# coverage is disclosed as unavailable, never as "no consumers".
UNAVAILABLE_COVERAGE = ("dashboards", "groups", "scenes", "templates")
MAX_DISCLOSED_CONSUMERS = 16
MAX_STATEMENTS = 10
SAFETY_SENSOR_CLASSES = frozenset(
    {"smoke", "carbon_monoxide", "gas", "moisture", "safety"}
)
PROTECTED_COVER_CLASSES = frozenset({"garage", "gate", "door"})
_KNOWN_CONSEQUENCES = frozenset({"none", "indirect", "direct", "safety_critical"})
DISABLE_RISK_TEXT = (
    "Disabling the entity may stop triggers or expose unavailable, unknown, "
    "or absent-state behavior."
)
GOOGLE_STORED_SETTING_CAVEAT = (
    "For Google Assistant, the readback proves only the stored exposure "
    "setting in Home Assistant; a cloud YAML entity filter can override it, "
    "and Engineering cannot observe that filter."
)
ENABLE_RELOAD_TIMER_NOTE = (
    "Home Assistant coalesces config-entry reloads after enables into one "
    "global timer, 30 s after the last enable, including enables from other "
    "plans; Engineering does not hold a lock for that reload."
)
INTEGRATION_ENABLE_NOTE = (
    "{entity_id} was disabled by its integration. Enabling overrides that; a "
    "rollback can only record it as disabled by the user."
)
ROLLBACK_DISCLOSURE = (
    "Reverses the verified children of plan {plan_id}, restoring the values "
    "captured before it. If an entity was edited since, this plan is refused "
    "as stale; it never merges."
)


@dataclass(frozen=True)
class Request:
    operation: str
    entity_ids: tuple[str, ...]
    assistant: str | None
    expiration_minutes: int

    @property
    def resource_type(self) -> str:
        return OPERATION_TYPES[self.operation]


@dataclass
class Item:
    """One prepared child: the exact entry, its final state and evidence."""

    operation_id: str
    entity_id: str
    resource_type: str
    operation: str
    assistant: str | None
    current: dict[str, Any]
    final: dict[str, Any]
    disclosure: dict[str, Any]
    classification: dict[str, Any]


@dataclass
class Preparation:
    request: Request
    items: list[Item]
    plan_disclosure: dict[str, Any] = field(default_factory=dict)

    def item(self, operation_id: str) -> Item:
        for candidate in self.items:
            if candidate.operation_id == operation_id:
                return candidate
        raise KeyError(operation_id)


def validate_request(arguments: Any) -> Request:
    """Validate the closed input before any Home Assistant read."""

    if not isinstance(arguments, dict) or not all(
        isinstance(key, str) for key in arguments
    ):
        raise Refusal("entity_availability_exposure_invalid_request")
    unsupported = sorted(set(arguments) - ALLOWED_INPUT_FIELDS)
    if unsupported:
        raise Refusal(FIELD_NOT_SUPPORTED, fields=unsupported[:16])
    operation = arguments.get("operation")
    if operation not in OPERATION_TYPES:
        raise Refusal(INVALID_VALUE, field="operation")
    entity_ids = arguments.get("entity_ids")
    if not isinstance(entity_ids, list) or not entity_ids:
        raise Refusal(INVALID_VALUE, field="entity_ids")
    if len(entity_ids) > MAX_ENTITIES:
        raise Refusal(
            "entity_availability_exposure_too_many_entities",
            limit=MAX_ENTITIES,
        )
    validated: list[str] = []
    for value in entity_ids:
        try:
            validated.append(validate_entity_id(value))
        except ValueError:
            raise Refusal(INVALID_VALUE, field="entity_ids") from None
    if len(set(validated)) != len(validated):
        raise Refusal("entity_availability_exposure_duplicate_entity")
    assistant = arguments.get("assistant")
    if OPERATION_TYPES[operation] == EXPOSURE_RESOURCE_TYPE:
        if assistant is None:
            raise Refusal("assistant_required", field="assistant")
        if assistant not in WRITABLE_ASSISTANTS:
            # OD-3: Alexa and every other value are out of scope.
            raise Refusal("assistant_not_supported", field="assistant")
    elif "assistant" in arguments:
        raise Refusal("assistant_not_applicable", field="assistant")
    expiration = arguments.get("expiration_minutes", 120)
    if type(expiration) is not int or not 5 <= expiration <= 1440:
        raise Refusal(INVALID_VALUE, field="expiration_minutes")
    return Request(operation, tuple(validated), assistant, expiration)


def object_protection(entry: dict[str, Any]) -> list[str]:
    """Return OD-4 object reasons (design §6.1 rules 1, 2, 3 and 5)."""

    entity_id = entry["entity_id"]
    domain = entity_id.split(".", 1)[0]
    reasons: list[str] = []
    floor = protected_object_reason(entity_id)
    if floor is not None:
        reasons.append(floor)
    device_class = effective_device_class(entry)
    if domain == "cover":
        if device_class in PROTECTED_COVER_CLASSES:
            reasons.append(f"cover_device_class_{device_class}")
        elif device_class is None:
            # Unresolved is protected (OD-4): a classless cover may be a door.
            reasons.append("cover_device_class_unresolved")
    if domain == "siren":
        reasons.append("siren_domain")
    if domain == "binary_sensor" and device_class in SAFETY_SENSOR_CLASSES:
        reasons.append(f"safety_sensor_{device_class}")
    return sorted(set(reasons))


def _ledger_coverage(snapshot: Any, item: Any) -> tuple[str | None, int]:
    """Return the authoritative (completeness, failed items) for one source."""

    if item is None:
        return None, 0
    if (
        getattr(snapshot, "obligation_ledger_model", None) == OBLIGATION_LEDGER_MODEL
        and item.obligation_ledger_completeness is not None
    ):
        return item.obligation_ledger_completeness, item.obligation_ledger_failed_item_count
    return item.completeness, item.failed_item_count


def dependency_evidence_gaps(snapshot: Any | None, entity_id: str) -> list[str]:
    """Return why the disable impact evidence is incomplete; ``[]`` = complete.

    This is the single "evidence complete" predicate (OD-4: unresolved is
    protected).  Every gap becomes a dependency reason, so classification,
    the policy recompute and the preflight consumer fingerprint all follow
    from this one decision.  Staleness cannot occur here: the reader always
    awaits a fresh (and at preflight, fenced) build, and a failed build is
    ``None``.  Structural script partiality and the permanently unindexed
    sources (``UNAVAILABLE_COVERAGE``) are disclosed warnings, not gaps.
    """

    if snapshot is None:
        return ["dependency_index_unavailable"]
    gaps: set[str] = set()
    admission = home_assistant_version_admission(snapshot)
    if not admission["admitted"]:
        gaps.add(str(admission["reason_code"]))
    if getattr(snapshot, "obligation_ledger_model", None) != OBLIGATION_LEDGER_MODEL:
        gaps.add("obligation_ledger_model_unsupported")
    coverage = {item.source_type: item for item in getattr(snapshot, "coverage", ()) or ()}
    for source_type in ("automation", "blueprint"):
        completeness, failed = _ledger_coverage(snapshot, coverage.get(source_type))
        if completeness != "complete" or failed:
            gaps.add(f"{source_type}_inventory_incomplete")
    if any(
        "exceeded the bounded index payload" in warning
        for item in coverage.values()
        for warning in item.warnings
    ):
        # The consumer list is the finding projection, which this truncates.
        gaps.add("dependency_index_payload_truncated")
    if getattr(snapshot, "automation_read_failures", ()):
        gaps.add("automation_read_failures")
    if getattr(snapshot, "obligation_overflow_count", 0):
        gaps.add("obligation_ledger_truncated")
    if getattr(snapshot, "dynamic_reference_overflow_count", 0):
        gaps.add("dynamic_references_truncated")
    for item in getattr(snapshot, "obligations", ()) or ():
        if item.source_type not in {"automation", "blueprint"}:
            continue
        outcome = _obligation_targets_helper(item, entity_id)
        if outcome == "coverage_failure":
            gaps.add("obligation_coverage_failure")
        elif outcome == "bounded_semantic_opaque":
            gaps.add("unresolved_dynamic_consumer")
    scripts = getattr(snapshot, "script_diagnostics", None)
    scripts = scripts.visible() if scripts is not None else None
    if scripts is None or scripts.coverage.completeness == "unavailable":
        gaps.add("script_evidence_unavailable")
    else:
        if scripts.coverage.failed_item_count:
            gaps.add("script_read_failures")
        if any("Inventory was bounded" in warning for warning in scripts.coverage.warnings):
            gaps.add("script_inventory_bounded")
    return sorted(gaps)


def _consumer_evidence(snapshot: Any | None, entity_id: str) -> dict[str, Any]:
    """Return the disable impact evidence for one target (design §5, §6.1)."""

    gaps = dependency_evidence_gaps(snapshot, entity_id)
    reasons: set[str] = {f"{INCOMPLETE_PREFIX}{gap}" for gap in gaps}
    if snapshot is None:
        return {
            "status": "unavailable",
            "consumers": [],
            "consumer_count": 0,
            "coverage": [],
            "evidence_complete": False,
            "evidence_gaps": gaps,
            "dependency_reasons": sorted(reasons),
        }
    consumers: dict[tuple[str, str, str], dict[str, Any]] = {}
    scripts = getattr(snapshot, "script_diagnostics", None)
    script_findings = scripts.visible().findings if scripts is not None else ()
    for finding in (*(getattr(snapshot, "findings", ()) or ()), *script_findings):
        if finding.target_entity_id != entity_id:
            continue
        key = (finding.source_type, finding.source_id, finding.relation)
        consumers.setdefault(
            key,
            {
                "source_type": finding.source_type,
                "source_id": finding.source_id,
                "source_entity_id": finding.source_entity_id
                if isinstance(finding.source_entity_id, str)
                else None,
                "relation": finding.relation,
            },
        )
    profiles = {
        profile.source_id: profile
        for profile in getattr(snapshot, "automation_action_profiles", ()) or ()
    }
    for source_id in sorted(_causal_automation_sources(snapshot, entity_id)):
        profile = profiles.get(source_id)
        consequence = getattr(profile, "physical_consequence", None)
        if (
            profile is None
            or consequence not in _KNOWN_CONSEQUENCES
            or not profile.complete
            or profile.processing_limit_exceeded
        ):
            reasons.add(f"unresolved_automation_consequence:{source_id}")
        elif consequence == "safety_critical":
            reasons.add(f"feeds_protected_automation:{source_id}")
    coverage = sorted(
        (
            {"source_type": item.source_type, "completeness": item.completeness}
            for item in (
                *(getattr(snapshot, "coverage", ()) or ()),
                *((scripts.visible().coverage,) if scripts is not None else ()),
            )
        ),
        key=lambda item: (item["source_type"], item["completeness"]),
    )
    ordered = [consumers[key] for key in sorted(consumers)]
    return {
        "status": "read",
        "consumers": ordered,
        "consumer_count": len(ordered),
        "coverage": coverage,
        "evidence_complete": not gaps,
        "evidence_gaps": gaps,
        "dependency_reasons": sorted(reasons),
    }


def consumer_fingerprint(operation: str, evidence: dict[str, Any] | None) -> str:
    """Return the execution-binding consumer fingerprint (disable only)."""

    if operation != "disable" or evidence is None:
        return stable_hash({"model": DISCLOSURE_MODEL, "consumers": "not_applicable"})
    return stable_hash({"model": DISCLOSURE_MODEL, **evidence})


async def read_consumer_evidence(
    snapshot_reader: Callable[..., Awaitable[Any]],
    entity_id: str,
    *,
    fenced: bool,
) -> dict[str, Any]:
    try:
        snapshot = await snapshot_reader(fenced=fenced)
    except EntityMetadataEvidenceUnavailable:
        snapshot = None
    return _consumer_evidence(snapshot, entity_id)


def _reload_expectation(config_entry: dict[str, Any] | None, prior: Any) -> str:
    if prior == "config_entry":
        return "none_config_entry_disabled"
    if config_entry is None:
        return "restart_required"
    if config_entry["status"] != "read":
        return "unknown"
    return (
        "config_entry_reload_after_30s"
        if config_entry["supports_unload"]
        else "restart_required"
    )


def _name(source: dict[str, Any]) -> str:
    return str(source.get("source_entity_id") or source["source_id"])[:120]


def _statements(
    item_operation: str,
    assistant: str | None,
    current: dict[str, Any],
    disclosure: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Return (consequence statements, warnings) in plain language."""

    entity_id = current["entity_id"]
    domain = entity_id.split(".", 1)[0]
    protection = disclosure["protection"]
    statements: list[str] = []
    warnings: list[str] = []
    if item_operation == "expose":
        name = ASSISTANT_NAMES[assistant or ""]
        verbs = {
            "lock": "unlock the door",
            "cover": "open it",
            "valve": "open or close the valve",
            "siren": "turn the siren on or off",
            "alarm_control_panel": "change the alarm state",
        }
        if protection["object_reasons"]:
            action = verbs.get(domain, "control it or read its state")
            if assistant == "conversation":
                statements.append(
                    f"This exposes {entity_id} to Assist: anyone who can speak "
                    f"to Assist may {action} without a PIN."
                )
            else:
                statements.append(
                    f"This exposes {entity_id} to {name}: anyone who can speak "
                    f"to it may {action}. Google asks for the secure-devices PIN "
                    "for locks, alarm panels and door, garage and gate covers "
                    "only while two-factor confirmation stays enabled."
                )
        else:
            statements.append(f"This exposes {entity_id} to {name}.")
        if assistant == "cloud.google_assistant":
            warnings.append(GOOGLE_STORED_SETTING_CAVEAT)
    elif item_operation == "unexpose":
        statements.append(
            f"This stops exposing {entity_id} to {ASSISTANT_NAMES[assistant or '']}."
        )
        if assistant == "cloud.google_assistant":
            warnings.append(GOOGLE_STORED_SETTING_CAVEAT)
    elif item_operation == "disable":
        consumers = disclosure["consumers"]
        statements.append(
            f"Disabling {entity_id} removes its state; consumers then see a "
            "missing entity. " + DISABLE_RISK_TEXT
        )
        for source in consumers["items"][: MAX_STATEMENTS - 3]:
            statements.append(
                f"Disabling this breaks {source['source_type']} {_name(source)} "
                f"({source['relation']})."
            )
        if not consumers["evidence_complete"]:
            statements.append(
                f"For {entity_id}, {INCOMPLETE_IMPACT_TEXT} ("
                + ", ".join(consumers["evidence_gaps"])
                + "); this is treated as protected."
            )
        elif not consumers["count"]:
            statements.append(
                "No indexed automation or script consumer was found."
            )
        warnings.append(
            "Consumer coverage: automations are indexed, scripts are partial, "
            "and scenes, groups, templates and dashboards are unavailable; an "
            "empty list is not proof that nothing uses this entity."
        )
        exposed = disclosure["stored_exposure_true"]
        if exposed and protection["object_reasons"]:
            warnings.append(
                f"{entity_id} stays exposed to "
                + ", ".join(ASSISTANT_NAMES[a] for a in exposed)
                + " while disabled; enabling it later restores that exposure."
            )
    else:
        reload = disclosure["reload"]
        entry = disclosure.get("config_entry") or {}
        others = disclosure.get("other_entities_in_entry")
        if reload == "config_entry_reload_after_30s":
            count = (
                f"its {others} other entities"
                if isinstance(others, int)
                else "all of its entities (count unavailable)"
            )
            statements.append(
                f"Enabling {entity_id} reloads its whole config entry "
                f"({entry.get('domain') or 'unknown integration'}) about 30 s "
                f"later: {count} become unavailable briefly and are set up again."
            )
            warnings.append(ENABLE_RELOAD_TIMER_NOTE)
        elif reload == "restart_required":
            statements.append(
                f"Enabling {entity_id} takes effect only after a Home Assistant "
                "restart; this plan does not restart Home Assistant."
            )
        else:
            statements.append(
                f"Engineering could not read the config entry of {entity_id}; "
                "enabling may reload the whole entry or need a restart."
            )
        if current.get("disabled_by") == "integration":
            warnings.append(INTEGRATION_ENABLE_NOTE.format(entity_id=entity_id))
        exposed = disclosure["stored_exposure_true"]
        if exposed and protection["object_reasons"]:
            statements.append(
                f"Enabling {entity_id} restores its voice exposure to "
                + ", ".join(ASSISTANT_NAMES[a] for a in exposed)
                + "."
            )
    return (
        [item[:300] for item in statements[:MAX_STATEMENTS]],
        [item[:500] for item in warnings[:MAX_STATEMENTS]],
    )


def classify(operation: str, disclosure: dict[str, Any]) -> dict[str, Any]:
    """Classify under OD-5: standard, or elevated for protected cases."""

    protection = disclosure["protection"]
    basis: list[str] = []
    if operation == "expose" and protection["object_reasons"]:
        basis.append("expose_protected_object")
    elif operation == "disable":
        if protection["object_reasons"]:
            basis.append("disable_protected_object")
        if protection["dependency_reasons"]:
            basis.append("disable_protected_dependency")
    elif (
        operation == "enable"
        and protection["object_reasons"]
        and disclosure["stored_exposure_true"]
    ):
        basis.append("enable_exposed_protected_object")
    return {
        "policy": "elevated" if basis else "standard",
        "physical_consequence": "safety_critical" if basis else "none",
        "basis": basis,
        "statements": list(disclosure["consequence_statements"]),
        "warnings": list(disclosure["warnings"]),
    }


def risk_assessment(operation_id: str, classification: dict[str, Any]) -> ChangeRiskAssessment:
    """Return the risk record that F2 policy reads for these resource types."""

    elevated = classification["policy"] == "elevated"
    reasons = list(classification["statements"])
    if elevated:
        reasons = [
            "Protected object: "
            + ", ".join(classification["basis"])
            + " (elevated approval required)",
            *reasons,
        ][:MAX_STATEMENTS]
    return ChangeRiskAssessment(
        level=RiskLevel.HIGH if elevated else RiskLevel.LOW,
        reasons=reasons or ["Entity enable/disable or exposure change"],
        apply_allowed=True,
        evidence=[
            {
                "field": operation_id,
                "trigger": POLICY_TRIGGER,
                "policy": classification["policy"],
                "physical_consequence": classification["physical_consequence"],
                "basis": ",".join(classification["basis"]),
            }
        ],
        warnings=list(classification["warnings"]),
    )


def _refuse_state(operation: str, current: dict[str, Any], assistant: str | None) -> None:
    entity_id = current["entity_id"]
    disabled_by = current.get("disabled_by")
    if operation == "disable" and disabled_by is not None:
        raise Refusal("entity_already_disabled", entity_id=entity_id, disabled_by=disabled_by)
    if operation == "enable":
        if disabled_by is None:
            raise Refusal("entity_already_enabled", entity_id=entity_id)
        if disabled_by in {"device", "config_entry", "hass"}:
            # OD-6: device, config-entry and Core-internal reasons are refused.
            raise Refusal(f"entity_disabled_by_{disabled_by}", entity_id=entity_id)
    if operation in {"expose", "unexpose"}:
        value = exposure_value(current, assistant or "")
        # An absent key is never the desired state: the effective default is
        # never read (design §3.4), so an explicit write is always planned.
        if value["present"] and value["should_expose"] is (operation == "expose"):
            raise Refusal(
                "entity_already_exposed" if operation == "expose" else "entity_already_unexposed",
                entity_id=entity_id,
                assistant=assistant,
            )


def derive_final(operation: str, assistant: str | None, current: dict[str, Any]) -> dict[str, Any]:
    final = copy.deepcopy(without_bindings(current))
    if operation in {"disable", "enable"}:
        final["disabled_by"] = "user" if operation == "disable" else None
    else:
        options = final.setdefault("options", {})
        settings = dict(options.get(assistant) or {})
        settings["should_expose"] = operation == "expose"
        options[assistant] = settings
    return final


async def _read_entry(gateway: Any, entity_id: str) -> dict[str, Any]:
    try:
        current = await gateway.read_entry(entity_id)
    except AuthorizationError:
        raise Refusal("entity_registry_permission_failure", kind="permission") from None
    except EntityMetadataProviderError:
        raise Refusal(
            "entity_registry_entry_unreviewable", kind="unreviewable", entity_id=entity_id
        ) from None
    if current is None:
        # OD-10 and design §2.4: a state-only entity cannot be governed here.
        raise Refusal("entity_not_in_registry", kind="not_found", entity_id=entity_id)
    if entry_errors(entity_id, current):
        raise Refusal(
            "entity_registry_entry_unreviewable", kind="unreviewable", entity_id=entity_id
        )
    return current


async def config_entry_evidence(gateway: Any, current: dict[str, Any]) -> dict[str, Any] | None:
    entry_id = current.get("config_entry_id")
    if entry_id is None:
        return None
    try:
        return await gateway.config_entry(entry_id)
    except AuthorizationError:
        raise Refusal("entity_registry_permission_failure", kind="permission") from None
    except EntityMetadataProviderError:
        return {
            "entry_id": entry_id,
            "domain": None,
            "supports_unload": None,
            "disabled_by": None,
            "status": "unavailable",
        }


async def prepare(
    gateway: Any,
    snapshot_reader: Callable[..., Awaitable[Any]],
    request: Request,
    *,
    rollback_fingerprints: dict[str, str] | None = None,
) -> Preparation:
    """Read, refuse, bind and classify every child.  Nothing is dispatched.

    ``rollback_fingerprints`` (F3 rollback only) maps each entity to the
    forward child's verified readback fingerprint; the live entry must still
    match it exactly (including any reported tolerated Alexa key).
    """

    entries: list[dict[str, Any]] = []
    for entity_id in request.entity_ids:
        current = await _read_entry(gateway, entity_id)
        if (
            rollback_fingerprints is not None
            and entry_fingerprint(current) != rollback_fingerprints[entity_id]
        ):
            raise Refusal("entry_changed_since_apply", kind="rollback", entity_id=entity_id)
        _refuse_state(request.operation, current, request.assistant)
        entries.append(current)
    if request.operation == "enable":
        groups = {entry.get("config_entry_id") for entry in entries}
        if len(groups) != 1:
            # OD-7: one enable plan approves exactly one reload blast radius.
            raise Refusal(
                "enable_spans_multiple_config_entries",
                config_entry_count=len(groups),
            )
    entries.sort(key=lambda entry: (entry.get("config_entry_id") or "", entry["entity_id"]))
    items: list[Item] = []
    expose_new: dict[str, Any] = {}
    if request.assistant is not None:
        try:
            expose_new[request.assistant] = await gateway.expose_new(request.assistant)
        except AuthorizationError:
            raise Refusal("entity_registry_permission_failure", kind="permission") from None
    for index, current in enumerate(entries):
        entity_id = current["entity_id"]
        operation_id = f"{request.operation}_{index + 1}"
        config_entry = None
        others: int | str | None = None
        if request.operation == "enable":
            config_entry = await config_entry_evidence(gateway, current)
            if config_entry is not None and config_entry["status"] == "read":
                try:
                    counted = await gateway.other_entities_in_entry(
                        config_entry["entry_id"], entity_id
                    )
                except AuthorizationError:
                    raise Refusal(
                        "entity_registry_permission_failure", kind="permission"
                    ) from None
                others = counted if counted is not None else "unavailable"
        consumers = None
        if request.operation == "disable":
            consumers = await read_consumer_evidence(
                snapshot_reader, entity_id, fenced=False
            )
        protection = {
            "object_reasons": object_protection(current),
            "dependency_reasons": list(consumers["dependency_reasons"]) if consumers else [],
        }
        disclosure: dict[str, Any] = {
            "model": DISCLOSURE_MODEL,
            "entity_id": entity_id,
            "registry_id": current.get("id"),
            "operation": request.operation,
            "assistant": request.assistant,
            "config_entry_id": current.get("config_entry_id"),
            "current": {
                "disabled_by": current.get("disabled_by"),
                "exposure": {a: exposure_value(current, a) for a in KNOWN_ASSISTANTS},
            },
            "protection": {
                "protected": bool(
                    protection["object_reasons"] or protection["dependency_reasons"]
                ),
                **protection,
            },
            "stored_exposure_true": [
                assistant
                for assistant in KNOWN_ASSISTANTS
                if exposure_value(current, assistant)["should_expose"] is True
            ],
        }
        if request.operation == "enable":
            disclosure["config_entry"] = config_entry
            disclosure["other_entities_in_entry"] = others
            disclosure["reload"] = _reload_expectation(
                config_entry, current.get("disabled_by")
            )
        if consumers is not None:
            disclosure["consumers"] = {
                "status": consumers["status"],
                "evidence_complete": consumers["evidence_complete"],
                "evidence_gaps": consumers["evidence_gaps"],
                "items": consumers["consumers"][:MAX_DISCLOSED_CONSUMERS],
                "count": consumers["consumer_count"],
                "truncated": consumers["consumer_count"] > MAX_DISCLOSED_CONSUMERS,
                "coverage": consumers["coverage"],
                "coverage_unavailable": list(UNAVAILABLE_COVERAGE),
            }
        if request.assistant is not None:
            disclosure["expose_new_entities"] = expose_new.get(request.assistant)
            disclosure["readback_proves"] = (
                "stored_setting_only"
                if request.assistant == "cloud.google_assistant"
                else "stored_setting_is_effective"
            )
        statements, warnings = _statements(
            request.operation, request.assistant, current, disclosure
        )
        disclosure["consequence_statements"] = statements
        disclosure["warnings"] = warnings
        final = derive_final(request.operation, request.assistant, current)
        final[BINDINGS_KEY] = {
            "operation": request.operation,
            "assistant": request.assistant,
            "config_entry": config_entry,
            "protection": protection,
            "consumer_evidence_fingerprint": consumer_fingerprint(
                request.operation, consumers
            ),
        }
        items.append(
            Item(
                operation_id=operation_id,
                entity_id=entity_id,
                resource_type=request.resource_type,
                operation=request.operation,
                assistant=request.assistant,
                current=current,
                final=final,
                disclosure=disclosure,
                classification=classify(request.operation, disclosure),
            )
        )
    return Preparation(
        request=request,
        items=items,
        plan_disclosure={
            "model": DISCLOSURE_MODEL,
            "operation": request.operation,
            "assistant": request.assistant,
            "entity_count": len(items),
            "limit": MAX_ENTITIES,
            "execution": "ordered_non_atomic_stop_on_first_failure",
            "provider": PROVIDERS[request.resource_type],
            "fallback": "none",
        },
    )


def projection(prepared: Preparation, item: Item, *, plan_id: str | None = None) -> dict[str, Any]:
    """Return the bounded per-child plan projection."""

    return {
        **item.disclosure,
        "final": {
            "disabled_by": item.final.get("disabled_by"),
            "exposure": {
                a: exposure_value(item.final, a) for a in KNOWN_ASSISTANTS
            },
        },
        "policy_basis": {
            "policy": item.classification["policy"],
            "physical_consequence": item.classification["physical_consequence"],
            "basis": list(item.classification["basis"]),
        },
        "rollback_disclosure": rollback_disclosure(item, plan_id=plan_id),
        "provider": PROVIDERS[item.resource_type],
        "fallback": "none",
    }


def rollback_disclosure(item: Item, *, plan_id: str | None = None) -> str:
    if item.operation in {"expose", "unexpose"} and not exposure_value(
        item.current, item.assistant or ""
    )["present"]:
        return (
            "No rollback: there was no prior stored exposure setting, and Home "
            "Assistant has no command that restores an unset preference."
        )
    return ROLLBACK_DISCLOSURE.format(plan_id=plan_id or "<this plan>")


def inverse_operation(operation: str, prior: dict[str, Any], assistant: str | None) -> str:
    """Return the reverse operation, refusing what cannot be restored (OD-8)."""

    if operation == "disable":
        return "enable"
    if operation == "enable":
        return "disable"
    value = exposure_value(prior, assistant or "")
    if not value["present"]:
        raise Refusal(
            "prior_exposure_preference_unset_not_restorable", kind="rollback"
        )
    return "expose" if value["should_expose"] else "unexpose"
