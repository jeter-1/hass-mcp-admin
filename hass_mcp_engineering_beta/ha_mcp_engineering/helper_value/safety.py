"""Safety-critical warning from existing helper dependency evidence (HAMCP-311, HAMCP-327).

No new analysis: this reads the shipped target-scoped helper dependency binding
and projects it. Only EXACT evidence warns: the binding's positive
``physical_consequence`` is ``safety_critical`` and at least one consumer that
references the helper carries that consequence (with an exact obligation, or a
literal reference whose own analysis failed). Opaque or template readers and
conservative-scope consumers never warn; they are disclosed as information.

HAMCP-327 (fail to ask, never act blind): partial coverage is disclosed
evidence, not a failure. Evidence is established whenever the binding's
execution contract is complete (fresh, untruncated index; admitted Home
Assistant version). When it cannot be established, the change is never
dispatched blind: ``assess`` reports ``confirmation_reason`` ``evidence_unavailable``
and the caller must ask. Either confirmation is a chat confirmation conveyed by
the model, never external approval.
"""
from __future__ import annotations

import asyncio
from functools import partial

MAX_LISTED = 20
MAX_TEMPLATE_EXAMPLES = 3
MAX_NAME = 80
EVIDENCE_TIMEOUT_SECONDS = 30
MAX_REASON_CODES = 8
SAFETY_CRITICAL = "safety_critical"
CONFIRMATION = "model_conveyed_chat_confirmation"
# Closed set of reasons a change needs the chat confirmation before dispatch.
CONFIRMATION_SAFETY_CRITICAL = "safety_critical_consumers"
CONFIRMATION_EVIDENCE_UNAVAILABLE = "evidence_unavailable"
CONFIRMATION_REASONS = frozenset({CONFIRMATION_SAFETY_CRITICAL, CONFIRMATION_EVIDENCE_UNAVAILABLE})
_COVERAGE_FAILURE_SCOPES = frozenset({"coverage_failure_consequence_scope", "unreadable_consequence_scope"})


def _builder(domain):
    from ..governance.helper_dependency import build_helper_dependency_risk_binding
    from ..governance.input_boolean_dependency import (INPUT_BOOLEAN_DEPENDENCY_MODEL,
                                                       build_input_boolean_dependency_binding)
    from ..governance.input_select_dependency import (INPUT_SELECT_DEPENDENCY_MODEL,
                                                      build_input_select_dependency_binding)
    if domain == "input_boolean":
        return build_input_boolean_dependency_binding, INPUT_BOOLEAN_DEPENDENCY_MODEL
    if domain == "input_select":
        return build_input_select_dependency_binding, INPUT_SELECT_DEPENDENCY_MODEL
    return partial(build_helper_dependency_risk_binding, target_scoped=True), None


def automation_names(snapshot) -> dict[str, str]:
    """Bounded automation entity id -> friendly name map from the same snapshot."""
    names = {}
    for group in ("findings", "dynamic_references", "obligations"):
        for item in getattr(snapshot, group, ()) or ():
            entity = getattr(item, "source_entity_id", None)
            name = getattr(item, "source_name", None)
            if (getattr(item, "source_type", None) == "automation" and isinstance(entity, str)
                    and isinstance(name, str) and name and entity not in names):
                names[entity] = name[:MAX_NAME]
    return names


async def read_helper_dependency_evidence(entity_id: str) -> dict:
    """Read the shipped binding; failure is reported, never raised."""
    from ..dependency import DEPENDENCY_ANALYSIS
    from ..governance.helper_dependency import HelperDependencyRiskService

    builder, model = _builder(entity_id.split(".", 1)[0])
    captured = {}

    def capture(snapshot, **kwargs):
        captured["snapshot"] = snapshot
        return builder(snapshot, **kwargs)

    try:
        index = DEPENDENCY_ANALYSIS.require().index
        async with asyncio.timeout(EVIDENCE_TIMEOUT_SECONDS):
            evidence = await HelperDependencyRiskService(index, binding_builder=capture).assess(
                entity_id, refresh=True, fenced=False)
    except Exception as exc:
        return {"available": False, "failure_category": type(exc).__name__[:64]}
    binding = evidence.get("binding") if isinstance(evidence, dict) else None
    provenance = evidence.get("provenance") if isinstance(evidence, dict) else None
    failure = _unestablished(binding, provenance, model)
    if failure is not None:
        return {"available": False, "failure_category": failure}
    return {"available": True, "binding": binding,
            "names": automation_names(captured.get("snapshot"))}


def _code(value):
    return value[:64] if isinstance(value, str) and value else None


def _unestablished(binding, provenance, model):
    """Why evidence cannot be established, or ``None`` when it can.

    Coverage reasons alone (bounded, opaque or unreadable automations; group
    membership) leave ``completeness`` ``failed`` but are disclosed evidence:
    the consumers they concern are still named and classified. Evidence is
    unestablished only on a reader failure, a wrong model, or an incomplete
    execution contract (index not fresh or truncated, Home Assistant version
    not admitted, the service's own failed binding).
    """
    if not isinstance(binding, dict):
        return "evidence_failed"
    # The service's own failure binding uses the base model, so its reported
    # failure category is surfaced before the model check.
    category = provenance.get("failure_category") if isinstance(provenance, dict) else None
    if _code(category):
        return _code(category)
    if model is not None and binding.get("model") != model:
        return "evidence_model_mismatch"
    if binding.get("execution_contract_complete") is not True:
        codes = binding.get("execution_block_reason_codes")
        first = codes[0] if isinstance(codes, list) and codes else None
        return _code(first) or "evidence_incomplete"
    return None


def _reason_codes(binding):
    codes = binding.get("coverage_failure_reason_codes")
    if not isinstance(codes, list):
        return []
    return [code[:64] for code in codes if isinstance(code, str)][:MAX_REASON_CODES]


def _consumer(profile, names):
    automation_id = str(profile.get("automation_id"))
    return {"automation_id": automation_id,
            "name": names.get(automation_id),
            "physical_consequence": str(profile.get("physical_consequence"))}


def assess(evidence: dict) -> dict:
    """Project evidence to the warning decision and bounded consumer information."""
    if not evidence.get("available"):
        return {
            "evidence": "unavailable",
            "failure_category": _code(evidence.get("failure_category")) or "evidence_failed",
            "safety_critical": False,
            "confirmation_reason": CONFIRMATION_EVIDENCE_UNAVAILABLE,
            "known_consumer_count": None, "known_consumers": None,
            "template_reader_count": None, "template_readers": None,
            "coverage_complete": False,
            "consequence": ("Helper dependency evidence could not be established, so a "
                            "safety-critical automation cannot be ruled out."),
        }
    binding, names = evidence["binding"], evidence.get("names") or {}
    profiles = [item for item in binding.get("downstream_profiles") or [] if isinstance(item, dict)]
    referenced = [item for item in profiles if item.get("evidence_class") == "referenced"]
    templates = [item for item in profiles if item.get("evidence_class") == "opaque"]
    critical = [item for item in referenced if item.get("physical_consequence") == SAFETY_CRITICAL]
    exact_count = binding.get("exact_dependency_obligation_count")
    exact = not (type(exact_count) is int and exact_count == 0)
    # HAMCP-327 / HAMCP-301 C-iv: a consumer that names the helper literally but
    # whose obligation analysis failed contributes no exact obligation. Its
    # literal reference cannot be ruled out, so it warns like exact evidence.
    exact = exact or any(set(item.get("relationships") or ()) & _COVERAGE_FAILURE_SCOPES
                         for item in critical)
    safety_critical = bool(binding.get("physical_consequence") == SAFETY_CRITICAL and critical and exact)
    counts = binding.get("relevant_consumer_counts_by_class")
    counts = counts if isinstance(counts, dict) else {}
    template_count = counts.get("opaque", len(templates))
    template_count = template_count if type(template_count) is int else len(templates)
    known_count = counts.get("referenced", len(referenced))
    known_count = known_count if type(known_count) is int else len(referenced)
    examples = [names.get(str(item.get("automation_id"))) or str(item.get("automation_id"))
                for item in templates[:MAX_TEMPLATE_EXAMPLES]]
    result = {
        "evidence": "available",
        "physical_consequence": str(binding.get("physical_consequence")),
        "safety_critical": safety_critical,
        "known_consumer_count": known_count,
        "known_consumers": [_consumer(item, names) for item in referenced[:MAX_LISTED]],
        "template_reader_count": template_count,
        "template_readers": (f"may read via templates: {template_count}, including "
                             + ", ".join(examples)) if template_count and examples else
                            (f"may read via templates: {template_count}" if template_count else None),
        "coverage_complete": binding.get("coverage_complete") is True,
        "coverage_reason_codes": _reason_codes(binding),
        "potential_physical_consequence": _code(binding.get("potential_physical_consequence")),
        "confirmation_reason": CONFIRMATION_SAFETY_CRITICAL if safety_critical else None,
    }
    if safety_critical:
        result["consequential_automations"] = [_consumer(item, names) for item in critical[:MAX_LISTED]]
        result["consequence"] = (f"{len(critical)} automation(s) that reference this helper have "
                                 "safety-critical physical consequences.")
    elif known_count:
        result["consequence"] = (f"{known_count} automation(s) reference this helper and may react; "
                                 "none has exact safety-critical evidence.")
    else:
        result["consequence"] = "No automation with exact evidence references this helper."
    if not safety_critical and result["potential_physical_consequence"] == SAFETY_CRITICAL:
        # Disclosed, never a warning (HAMCP-311): the consumer is in scope only
        # because its dependency on this helper could not be resolved.
        result["consequence"] += (" An automation whose dependency on this helper could not be "
                                  "resolved has a safety-critical action; it is not proven to "
                                  "react to this helper.")
    return result
