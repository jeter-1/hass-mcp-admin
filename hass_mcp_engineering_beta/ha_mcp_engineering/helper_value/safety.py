"""Safety-critical warning from existing helper dependency evidence (HAMCP-311).

No new analysis: this reads the shipped target-scoped helper dependency binding
and projects it. Only EXACT evidence warns: the binding's positive
``physical_consequence`` is ``safety_critical`` and at least one consumer that
references the helper carries that consequence. Opaque or template readers,
conservative-scope consumers and unavailable evidence never warn; they are
disclosed as information. The warning is a chat confirmation conveyed by the
model, never external approval.
"""
from __future__ import annotations

import asyncio
from functools import partial

MAX_LISTED = 20
MAX_TEMPLATE_EXAMPLES = 3
MAX_NAME = 80
EVIDENCE_TIMEOUT_SECONDS = 30
SAFETY_CRITICAL = "safety_critical"
CONFIRMATION = "model_conveyed_chat_confirmation"


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
    if (not isinstance(binding, dict) or (model is not None and binding.get("model") != model)
            or binding.get("completeness") == "failed"
            or (isinstance(provenance, dict) and provenance.get("completeness") == "failed")):
        category = provenance.get("failure_category") if isinstance(provenance, dict) else None
        return {"available": False,
                "failure_category": category[:64] if isinstance(category, str) else "evidence_failed"}
    return {"available": True, "binding": binding,
            "names": automation_names(captured.get("snapshot"))}


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
            "failure_category": evidence.get("failure_category"),
            "safety_critical": False,
            "note": ("Helper dependency evidence was unavailable, so no safety-critical "
                     "check could be made; the change proceeds without one."),
        }
    binding, names = evidence["binding"], evidence.get("names") or {}
    profiles = [item for item in binding.get("downstream_profiles") or [] if isinstance(item, dict)]
    referenced = [item for item in profiles if item.get("evidence_class") == "referenced"]
    templates = [item for item in profiles if item.get("evidence_class") == "opaque"]
    critical = [item for item in referenced if item.get("physical_consequence") == SAFETY_CRITICAL]
    exact_count = binding.get("exact_dependency_obligation_count")
    exact = not (type(exact_count) is int and exact_count == 0)
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
    return result
