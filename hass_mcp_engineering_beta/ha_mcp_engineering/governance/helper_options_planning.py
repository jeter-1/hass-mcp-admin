"""Closed input for a governed helper options plan (HAMCP-232).

Input is validated before any Home Assistant read: 1-8 changes, each naming
one ``sensor``/``binary_sensor`` entity of a config-entry helper, the fields
to ``set`` and the optional fields to ``clear`` (both from the closed union
of the per-type catalog in ``helper_options``), a reason and an optional
expiration.  The per-type rules (which fields a step has, which are pinned,
which may be cleared, and each value's type) are applied once the entry and
its step are known.  Nothing here reads or dispatches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..entity_ids import validate_entity_id
from ..errors import AuthorizationError, HomeAssistantApiError
from . import helper_options as contract
from .entity_availability_exposure_planning import (
    INCOMPLETE_IMPACT_TEXT,
    INCOMPLETE_PREFIX,
    MAX_DISCLOSED_CONSUMERS,
    MAX_STATEMENTS,
    UNAVAILABLE_COVERAGE,
    object_protection,
)
from .entity_metadata_planning import EntityMetadataRefusal
from .models import ChangeRiskAssessment, RiskLevel

ALLOWED_INPUT_FIELDS = frozenset({"changes", "reason", "expiration_minutes"})
CHANGE_FIELDS = frozenset({"entity_id", "set", "clear"})
REQUIRED_CHANGE_FIELDS = frozenset({"entity_id"})
MAX_CHANGES = 8
MAX_FIELDS_PER_CHANGE = 16
MAX_REASON_LENGTH = 500
INVALID_VALUE = "helper_options_invalid_value"
FIELD_NOT_SUPPORTED = "helper_options_field_not_supported"
POLICY_TRIGGER = "config_entry_helper_options_policy"
DISCLOSURE_MODEL = "config_entry_helper_options_disclosure_v1"
BASIS_PROTECTED_DEPENDENCY = "options_protected_dependency"
# The dependency index has no config-entry helper source
# (``dependency/provider.py``): a template or filter that reads this helper,
# or a helper chain feeding it, is never indexed.  The evidence for an
# options edit is therefore always incomplete, which OD-4 makes protected.
HELPER_CHAIN_GAP = f"{INCOMPLETE_PREFIX}config_entry_helper_chain_coverage_unavailable"
TEMPLATE_FIELDS = ("state", f"{contract.SECTION}.availability")
CONCURRENCY_DISCLOSURE = (
    "Accepted residual window (OD-7): Home Assistant has no compare-and-swap "
    "for options. A concurrent change that lands between the final "
    "modified_at check and the one submit is overwritten, and a settings "
    "dialog opened before this apply and saved after it overwrites this "
    "change with its older values."
)
HIDDEN_KEYS_DISCLOSURE = (
    "Options outside the edited step are neither read nor submitted; Home "
    "Assistant carries them unchanged."
)
RELOAD_DISCLOSURE = (
    "Saving options reloads the helper. Accumulated state the reload "
    "re-derives (statistics buffer, filter history) restarts and is not "
    "restored by a rollback."
)
ROLLBACK_DISCLOSURE = (
    "Rollback is a new, separately approved plan that re-applies the "
    "captured visible options through the same guarded sequence, offered "
    "only while the live options still equal the verified result."
)
TEMPLATE_RENDER_DISCLOSURE = (
    "The new template is checked for syntax only; it is not rendered at plan "
    "time, so its resulting state is shown only after apply."
)


class HelperOptionsRefusal(EntityMetadataRefusal):
    """A deterministic refusal with a design reason code; no plan exists."""


Refusal = HelperOptionsRefusal


@dataclass(frozen=True)
class Change:
    entity_id: str
    set_values: tuple[tuple[str, Any], ...]
    clear: tuple[str, ...]

    def set_dict(self) -> dict[str, Any]:
        return dict(self.set_values)


@dataclass(frozen=True)
class Request:
    changes: tuple[Change, ...]
    reason: str
    expiration_minutes: int

    @property
    def entity_ids(self) -> tuple[str, ...]:
        return tuple(change.entity_id for change in self.changes)

    @property
    def lease_target_id(self) -> str:
        return ",".join(sorted(self.entity_ids))


def _change(value: Any) -> Change:
    if (
        not isinstance(value, dict)
        or not all(isinstance(key, str) for key in value)
        or not set(value) <= CHANGE_FIELDS
        or not REQUIRED_CHANGE_FIELDS <= set(value)
    ):
        raise Refusal(INVALID_VALUE, field="changes")
    entity_id = value["entity_id"]
    try:
        validate_entity_id(entity_id)
    except ValueError:
        raise Refusal(INVALID_VALUE, field="entity_id") from None
    if entity_id.split(".", 1)[0] not in contract.ENTITY_DOMAINS:
        raise Refusal("helper_options_entity_domain_not_supported", field="entity_id")
    set_values = value.get("set", {})
    if not isinstance(set_values, dict) or not all(
        isinstance(key, str) for key in set_values
    ):
        raise Refusal(INVALID_VALUE, field="set")
    clear = value.get("clear", [])
    if not isinstance(clear, list) or not all(isinstance(item, str) for item in clear):
        raise Refusal(INVALID_VALUE, field="clear")
    if not set_values and not clear:
        raise Refusal("helper_options_no_change_requested", field="set")
    if len(set_values) + len(clear) > MAX_FIELDS_PER_CHANGE:
        raise Refusal(INVALID_VALUE, field="set")
    if len(set(clear)) != len(clear):
        raise Refusal("helper_options_duplicate_field", field="clear")
    overlap = sorted(set(set_values) & set(clear))
    if overlap:
        raise Refusal("helper_options_set_and_clear_overlap", fields=overlap[:16])
    unknown = sorted((set(set_values) | set(clear)) - contract.EDITABLE_FIELD_NAMES)
    if unknown:
        raise Refusal("helper_options_field_not_editable", fields=unknown[:16])
    return Change(
        entity_id=entity_id,
        set_values=tuple(sorted(set_values.items())),
        clear=tuple(sorted(clear)),
    )


def validate_request(arguments: Any) -> Request:
    """Validate the closed input before any Home Assistant read."""

    if not isinstance(arguments, dict) or not all(
        isinstance(key, str) for key in arguments
    ):
        raise Refusal("helper_options_invalid_request")
    unsupported = sorted(set(arguments) - ALLOWED_INPUT_FIELDS)
    if unsupported:
        raise Refusal(FIELD_NOT_SUPPORTED, fields=unsupported[:16])
    changes = arguments.get("changes")
    if not isinstance(changes, list) or not changes:
        raise Refusal(INVALID_VALUE, field="changes")
    if len(changes) > MAX_CHANGES:
        raise Refusal("helper_options_too_many_targets", limit=MAX_CHANGES)
    validated = tuple(_change(value) for value in changes)
    entity_ids = [change.entity_id for change in validated]
    if len(set(entity_ids)) != len(entity_ids):
        raise Refusal("helper_options_duplicate_target", field="entity_id")
    reason = arguments.get("reason")
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > MAX_REASON_LENGTH
    ):
        raise Refusal(INVALID_VALUE, field="reason")
    expiration = arguments.get("expiration_minutes", 120)
    if type(expiration) is not int or not 5 <= expiration <= 1440:
        raise Refusal(INVALID_VALUE, field="expiration_minutes")
    return Request(validated, reason.strip(), expiration)


@dataclass
class Item:
    """One prepared options child: the exact baseline, intent and evidence."""

    operation_id: str
    entity_id: str
    entry_id: str
    domain: str
    current: dict[str, Any]
    intent: dict[str, Any]
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


def contract_reason(category: str) -> str:
    """Map a contract category to the family's refusal vocabulary."""

    return "helper_options_" + category.removeprefix("options_")


async def _read(call: Any, reason_code: str, **details: Any) -> Any:
    try:
        return await call
    except AuthorizationError:
        raise Refusal("helper_options_permission_failure", kind="permission") from None
    except contract.HelperOptionsContractError as exc:
        raise Refusal(contract_reason(exc.category), kind="unreviewable", **details) from None
    except (contract.HelperOptionsProviderError, HomeAssistantApiError):
        raise Refusal(reason_code, kind="unavailable", **details) from None


def expected_basis(dependency_reasons: list[str]) -> tuple[str, ...]:
    """The single protection decision; policy recomputes it from the plan."""

    return (BASIS_PROTECTED_DEPENDENCY,) if dependency_reasons else ()


def classify(
    basis: tuple[str, ...], statements: list[str], warnings: list[str]
) -> dict[str, Any]:
    return {
        "policy": "elevated" if basis else "standard",
        "physical_consequence": "safety_critical" if basis else "none",
        "basis": list(basis),
        "statements": list(statements),
        "warnings": list(warnings),
    }


def risk_assessment(
    operation_id: str, classification: dict[str, Any]
) -> ChangeRiskAssessment:
    """Return the risk record and the one policy marker for an options child."""

    elevated = classification["policy"] == "elevated"
    reasons = list(classification["statements"])
    if elevated:
        reasons = [
            "Protected options edit: "
            + ", ".join(classification["basis"])
            + " (elevated approval required)",
            *reasons,
        ][:MAX_STATEMENTS]
    return ChangeRiskAssessment(
        level=RiskLevel.HIGH if elevated else RiskLevel.MEDIUM,
        reasons=reasons or ["Helper options edit"],
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


def template_references(visible: dict[str, Any]) -> tuple[set[str], bool]:
    """Exact entity ids a step's templates read, and whether any is dynamic."""

    from ..dependency.extraction import _template_references

    found: set[str] = set()
    dynamic = False
    for name in TEMPLATE_FIELDS:
        value = visible.get(name)
        if isinstance(value, str):
            exact, is_dynamic, _resolution = _template_references(value)
            found.update(exact)
            dynamic = dynamic or bool(is_dynamic)
    return found, dynamic


def _template_disclosure(
    domain: str, baseline: dict[str, Any], proposed: dict[str, Any]
) -> dict[str, Any] | None:
    if domain != "template":
        return None
    before, before_dynamic = template_references(baseline)
    after, after_dynamic = template_references(proposed)
    return {
        "referenced_entities": sorted(after)[:64],
        "newly_referenced": sorted(after - before)[:64],
        "no_longer_referenced": sorted(before - after)[:64],
        "dynamic_references_before": before_dynamic,
        "dynamic_references_after": after_dynamic,
        "plan_time_render": "not_performed",
    }


def _changes(baseline: dict[str, Any], proposed: dict[str, Any],
             requested: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for name in requested:
        rows.append({
            "field": name,
            "baseline": contract.canonical_out(baseline.get(name)),
            "proposed": contract.canonical_out(proposed.get(name)),
            "removed": name not in proposed,
        })
    return rows


def _proposed_protection(
    domain: str, step_id: str, entity_ids: list[str], proposed: dict[str, Any]
) -> list[str]:
    """A template's new device class can make its entity a protected object."""

    if domain != "template" or not isinstance(proposed.get("device_class"), str):
        return []
    reasons: list[str] = []
    for entity_id in entity_ids:
        if entity_id.split(".", 1)[0] != step_id:
            continue
        for reason in object_protection(
            {"entity_id": entity_id, "device_class": proposed["device_class"]}
        ):
            reasons.append(f"proposed_protected_object:{entity_id}:{reason}")
    return reasons


def _statements(
    entity_id: str,
    title: str,
    domain: str,
    rows: list[dict[str, Any]],
    normalization: list[dict[str, Any]],
    consumers: dict[str, Any],
    gaps: list[str],
) -> list[str]:
    statements = [
        f"Saving new options for {domain} helper {title or entity_id} "
        f"({entity_id}) reloads it: "
        + ", ".join(
            f"{row['field']} {'removed' if row['removed'] else 'set'}"
            for row in rows
        )
        + "."
    ]
    if normalization:
        statements.append(
            "Home Assistant also writes these defaults you did not request: "
            + ", ".join(item["field"] for item in normalization)
            + "."
        )
    for source in consumers["items"][: MAX_STATEMENTS - 4]:
        name = source.get("source_entity_id") or source.get("source_id")
        statements.append(
            f"{source['entity_id']} is used by {source['source_type']} {name} "
            f"({source['relation']})."
        )
    if gaps:
        statements.append(
            f"For this helper, {INCOMPLETE_IMPACT_TEXT} ("
            + ", ".join(gaps[:8])
            + "); this is treated as protected."
        )
    return [item[:300] for item in statements[:MAX_STATEMENTS]]


async def prepare(gateway: Any, request: Request) -> Preparation:
    """Read, refuse, bind and classify every child.  Nothing is dispatched.

    The only flow this opens is aborted before return; a failed abort is
    disclosed as an orphan flow and never makes the read succeed.
    """

    items: list[Item] = []
    seen_entries: set[str] = set()
    plan_domain: str | None = None
    orphan_flows = 0
    for index, change in enumerate(request.changes):
        target = {"entity_id": change.entity_id}
        registry = await _read(
            gateway.registry_entry(change.entity_id),
            "helper_options_registry_unreadable",
            **target,
        )
        if registry is None:
            raise Refusal("helper_options_target_not_found", kind="not_found", **target)
        entry_id = registry.get("config_entry_id")
        if entry_id is None:
            raise Refusal(
                "helper_options_not_config_entry_helper", kind="unreviewable", **target
            )
        if entry_id in seen_entries:
            raise Refusal("helper_options_duplicate_entry", **target)
        seen_entries.add(entry_id)
        facts = await _read(
            gateway.entry(entry_id), "helper_options_entry_unreadable", **target
        )
        if facts is None:
            raise Refusal("helper_options_target_not_found", kind="not_found", **target)
        if facts.domain not in contract.DOMAINS:
            raise Refusal(
                "helper_options_domain_not_supported", kind="unreviewable", **target
            )
        if plan_domain is None:
            plan_domain = facts.domain
        elif facts.domain != plan_domain:
            raise Refusal("helper_options_mixed_domains", **target)
        if not facts.supports_options:
            raise Refusal(
                "helper_options_options_not_supported", kind="unreviewable", **target
            )
        if facts.disabled_by is not None:
            raise Refusal("helper_options_entry_disabled", kind="unreviewable", **target)
        if facts.state != "loaded":
            raise Refusal("helper_options_entry_not_loaded", kind="unreviewable", **target)
        step_id, baseline, aborted = await _read(
            gateway.read_visible(entry_id, domain=facts.domain, expected_step_id=None),
            "helper_options_flow_unavailable",
            **target,
        )
        if not aborted:
            orphan_flows += 1
        # ``modified_at`` read after the flow opened: unchanged proves the
        # flow's snapshot is the baseline this plan binds.
        after = await _read(
            gateway.entry(entry_id), "helper_options_entry_unreadable", **target
        )
        if (
            after is None
            or after.domain != facts.domain
            or after.state != "loaded"
            or after.modified_at != facts.modified_at
        ):
            raise Refusal(
                "helper_options_target_changed_during_planning",
                kind="unavailable",
                **target,
            )
        set_values: dict[str, Any] = {}
        for name, value in change.set_values:
            spec = contract.field_spec(facts.domain, step_id, name)
            if spec is None:
                raise Refusal("helper_options_field_not_in_step", field=name, **target)
            try:
                set_values[name] = contract.coerce_request_value(spec, value)
            except contract.HelperOptionsContractError as exc:
                raise Refusal(contract_reason(exc.category), field=name, **target) from None
        for name in change.clear:
            spec = contract.field_spec(facts.domain, step_id, name)
            if spec is None:
                raise Refusal("helper_options_field_not_in_step", field=name, **target)
            if spec.pinned:
                raise Refusal("helper_options_field_pinned", field=name, **target)
            if not spec.clearable:
                raise Refusal("helper_options_field_not_clearable", field=name, **target)
        try:
            proposal = contract.build_proposal(
                facts.domain, step_id, baseline, set_values, change.clear
            )
        except contract.HelperOptionsContractError as exc:
            raise Refusal(
                contract_reason(exc.category),
                **{key: value for key, value in exc.details.items() if key in ("field", "rule")},
                **target,
            ) from None
        if contract.visible_equal(baseline, proposal.proposed):
            raise Refusal("helper_options_already_desired", **target)
        entity_ids = await _read(
            gateway.entry_entities(entry_id),
            "helper_options_registry_unreadable",
            **target,
        )
        if change.entity_id not in entity_ids or any(
            entity.split(".", 1)[0] not in contract.ENTITY_DOMAINS
            for entity in entity_ids
        ):
            raise Refusal("helper_options_entry_entities_unreviewable",
                          kind="unreviewable", **target)
        evidence = await _read(
            gateway.entry_consumer_evidence(entity_ids),
            "helper_options_dependency_evidence_unavailable",
            **target,
        )
        if evidence is None:
            raise Refusal(
                "helper_options_dependency_evidence_unavailable",
                kind="unavailable",
                **target,
            )
        reasons: set[str] = {HELPER_CHAIN_GAP}
        consumers_items: list[dict[str, Any]] = []
        consumer_count = 0
        gaps: set[str] = {HELPER_CHAIN_GAP.removeprefix(INCOMPLETE_PREFIX)}
        coverage: list[Any] = []
        for entity_id in entity_ids:
            item = evidence["entities"][entity_id]
            reasons.update(item["dependency_reasons"])
            gaps.update(item["evidence_gaps"])
            consumer_count += item["consumer_count"]
            coverage = item["coverage"]
            consumers_items.extend(
                {**source, "entity_id": entity_id} for source in item["consumers"]
            )
            entity_entry = registry if entity_id == change.entity_id else await _read(
                gateway.registry_entry(entity_id),
                "helper_options_registry_unreadable",
                **target,
            )
            if entity_entry is None:
                raise Refusal("helper_options_entry_entities_unreviewable",
                              kind="unreviewable", **target)
            reasons.update(
                f"protected_object:{entity_id}:{reason}"
                for reason in object_protection(entity_entry)
            )
        reasons.update(
            _proposed_protection(facts.domain, step_id, entity_ids, proposal.proposed)
        )
        dependency_reasons = sorted(
            reason[: contract.MAX_DEPENDENCY_REASON_LENGTH] for reason in reasons
        )
        dependency_reasons = sorted(set(dependency_reasons))[: contract.MAX_DEPENDENCY_REASONS]
        basis = expected_basis(dependency_reasons)
        consumers = {
            "status": "read",
            "evidence_complete": False,
            "evidence_gaps": sorted(gaps),
            "items": consumers_items[:MAX_DISCLOSED_CONSUMERS],
            "count": consumer_count,
            "truncated": consumer_count > MAX_DISCLOSED_CONSUMERS,
            "coverage": coverage,
            "coverage_unavailable": sorted({*UNAVAILABLE_COVERAGE, "config_entry_helpers"}),
        }
        rows = _changes(baseline, proposal.proposed, proposal.requested)
        normalization = [dict(item) for item in proposal.normalization_changes]
        statements = _statements(
            change.entity_id, facts.title, facts.domain, rows, normalization,
            consumers, sorted(gaps),
        )
        warnings = [CONCURRENCY_DISCLOSURE, HIDDEN_KEYS_DISCLOSURE,
                    RELOAD_DISCLOSURE, ROLLBACK_DISCLOSURE]
        template = _template_disclosure(facts.domain, baseline, proposal.proposed)
        if template is not None:
            warnings.append(TEMPLATE_RENDER_DISCLOSURE)
        intent = contract.build_intent(
            entity_ids=entity_ids,
            entry_id=entry_id,
            domain=facts.domain,
            step_id=step_id,
            modified_at=facts.modified_at,
            baseline=baseline,
            proposal=proposal,
            consumer_evidence_fingerprint=contract.consumer_fingerprint(evidence),
            dependency_reasons=dependency_reasons,
        )
        disclosure: dict[str, Any] = {
            "model": DISCLOSURE_MODEL,
            "entity_id": change.entity_id,
            "entry_id": entry_id,
            "domain": facts.domain,
            "step_id": step_id,
            "title": facts.title,
            "entry_entities": list(entity_ids),
            "changes": rows,
            "normalization_changes": normalization,
            "protection": {
                "protected": bool(basis),
                "basis": list(basis),
                "dependency_reasons": dependency_reasons,
            },
            "consumers": consumers,
            "template": template,
            "consequence_statements": statements,
            "warnings": [item[:500] for item in warnings],
            "residual_concurrency_window": "accepted_od_7",
            "provider": contract.PROVIDER,
            "fallback": "none",
        }
        items.append(
            Item(
                operation_id=f"options_{index + 1}",
                entity_id=change.entity_id,
                entry_id=entry_id,
                domain=facts.domain,
                current=intent[contract.INTENT_KEY]["baseline"],
                intent=intent,
                disclosure=disclosure,
                classification=classify(basis, statements, disclosure["warnings"]),
            )
        )
    return Preparation(
        request=request,
        items=items,
        plan_disclosure={
            "model": DISCLOSURE_MODEL,
            "reason": request.reason,
            "target_count": len(items),
            "limit": MAX_CHANGES,
            "domain": plan_domain,
            "execution": "ordered_non_atomic_stop_on_first_failure",
            "provider": contract.PROVIDER,
            "fallback": "none",
            "rollback": "new_separately_approved_options_plan",
            "residual_concurrency_window": CONCURRENCY_DISCLOSURE,
            "orphan_flows": orphan_flows,
        },
    )


def projection(item: Item) -> dict[str, Any]:
    """Return the bounded per-child plan projection."""

    return {
        **item.disclosure,
        "policy_basis": {
            "policy": item.classification["policy"],
            "physical_consequence": item.classification["physical_consequence"],
            "basis": list(item.classification["basis"]),
        },
    }


__all__ = [
    "ALLOWED_INPUT_FIELDS",
    "CHANGE_FIELDS",
    "Change",
    "FIELD_NOT_SUPPORTED",
    "HELPER_CHAIN_GAP",
    "Item",
    "POLICY_TRIGGER",
    "Preparation",
    "classify",
    "expected_basis",
    "prepare",
    "projection",
    "risk_assessment",
    "HelperOptionsRefusal",
    "INVALID_VALUE",
    "MAX_CHANGES",
    "Refusal",
    "Request",
    "validate_request",
]
