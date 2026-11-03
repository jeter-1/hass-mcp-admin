"""Target-scoped dependency reader for input_boolean (HAMCP-285).

The v13 reader attributed every global dependency gap to every helper: any
opaque template anywhere, and any coverage gap, made every automation
relevant to an input_boolean that no automation names.  That is consequence
uncertainty, not execution uncertainty.  This reader applies the HAMCP-274
relevance-by-evidence rules to input_boolean:

* The shared builder runs with ``target_scoped=True``: an automation is
  relevant only when it references this target (literally, or through a
  group, ``expand()``, an area, a label, a device or domain-wide iteration)
  or when an obligation stays genuinely opaque for this target.  An
  attributable coverage gap scopes only its own automation, a consumer set
  beyond the disclosure bound takes the coarse ``reload:automation`` fence
  instead of refusing, and missing evidence never carries a positive
  consequence label.
* A literal ``label_entities`` selector left incomplete only by malformed
  registry records naming other entities is decided from the target's own
  record, exactly as for input_select.
* ``call_service`` event-trigger exclusions are proven for input_boolean by
  the extractor itself, so nothing is re-projected.

The model is ``helper-dependency-risk-v14``.  Its evidence fingerprint binds
the v13 base material of the target-scoped snapshot plus the label decisions.
v13 remains a compatible, readable model: a pending v13 plan is replanned
rather than approved, an approved one is refused at fresh apply, and recovery
may still reconstruct its lock graph.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .helper_dependency import (
    INPUT_BOOLEAN_DEPENDENCY_MODEL,
    HelperDependencyRiskService,
    _failed_binding,
    build_helper_dependency_risk_binding,
)
from .input_select_dependency import _label_target_projection
from .normalize import stable_hash


def _project_labels(snapshot: Any, entity_id: str) -> tuple[Any, dict[str, Any]]:
    dispositions: dict[str, str] = {}
    decided_count = 0
    projected = []
    for item in tuple(getattr(snapshot, "obligations", ()) or ()):
        if item.source_type in {"automation", "blueprint"}:
            decided, decided_dispositions = _label_target_projection(
                item, snapshot, entity_id
            )
            for fingerprint, disposition in decided_dispositions:
                dispositions[fingerprint] = disposition
            if decided is not None:
                decided_count += 1
                item = decided
        projected.append(item)
    summary = {
        "label_obligations_target_decided": decided_count,
        "label_target_dispositions": [
            [key, dispositions[key]] for key in sorted(dispositions)
        ],
    }
    if not decided_count:
        return snapshot, summary
    return replace(snapshot, obligations=tuple(projected)), summary


def _wrap(base: dict[str, Any], summary: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = dict(summary or {
        "label_obligations_target_decided": 0,
        "label_target_dispositions": [],
    })
    material = {
        "model": INPUT_BOOLEAN_DEPENDENCY_MODEL,
        "base_model": base.get("model"),
        "base_evidence_fingerprint": base.get("evidence_fingerprint"),
        **summary,
    }
    binding = {
        **base,
        "model": INPUT_BOOLEAN_DEPENDENCY_MODEL,
        "base_model": base.get("model"),
        "base_evidence_fingerprint": base.get("evidence_fingerprint"),
        **summary,
    }
    binding["evidence_fingerprint"] = stable_hash(material)
    return binding


def build_input_boolean_dependency_binding(
    snapshot: Any, *, entity_id: str, index_metadata: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(entity_id, str) or not entity_id.startswith("input_boolean."):
        raise ValueError("input_boolean dependency target is invalid")
    projected, summary = _project_labels(snapshot, entity_id)
    base = build_helper_dependency_risk_binding(
        projected,
        entity_id=entity_id,
        index_metadata=index_metadata,
        target_scoped=True,
    )
    return _wrap(base, summary)


def failed_input_boolean_binding(entity_id: str) -> dict[str, Any]:
    return _wrap(_failed_binding(entity_id, "failed"))


async def read_input_boolean_dependency_risk(
    entity_id: str, *, refresh: bool = True, fenced: bool = False
) -> dict[str, Any]:
    from ..dependency import DEPENDENCY_ANALYSIS

    try:
        index = DEPENDENCY_ANALYSIS.require().index
    except Exception as exc:
        return {
            "binding": failed_input_boolean_binding(entity_id),
            "provenance": {
                "provider": "dependency_index",
                "completeness": "failed",
                "failure_category": type(exc).__name__[:64],
                "fallback": "none",
                "fallback_occurred": False,
            },
        }
    return await assess_input_boolean_dependency_risk(
        index, entity_id, refresh=refresh, fenced=fenced
    )


async def assess_input_boolean_dependency_risk(
    index: Any, entity_id: str, *, refresh: bool = True, fenced: bool = False
) -> dict[str, Any]:
    """Assess one input_boolean through the shared service and this builder."""

    evidence = await HelperDependencyRiskService(
        index, binding_builder=build_input_boolean_dependency_binding
    ).assess(entity_id, refresh=refresh, fenced=fenced)
    binding = evidence.get("binding")
    if isinstance(binding, dict) and binding.get("model") != INPUT_BOOLEAN_DEPENDENCY_MODEL:
        # The service's own failure path builds a v13 failed binding.
        evidence["binding"] = failed_input_boolean_binding(entity_id)
    return evidence


__all__ = [
    "INPUT_BOOLEAN_DEPENDENCY_MODEL",
    "assess_input_boolean_dependency_risk",
    "build_input_boolean_dependency_binding",
    "failed_input_boolean_binding",
    "read_input_boolean_dependency_risk",
]
