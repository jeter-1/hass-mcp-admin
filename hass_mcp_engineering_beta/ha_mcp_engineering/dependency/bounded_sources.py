"""Name every source whose dependency analysis hit a bound (HAMCP-226, HAAC-E002a).

The dependency index already holds the identity of each automation, blueprint
and script whose obligation ledger ended in ``coverage_failure``; before
HAMCP-226 the outputs reported only a count. ``bounded_source_entries``
turns those obligations into a bounded, deterministic list::

    {source_type, source_id, source_entity_id, reason_code, bound, bound_value}

``bound`` names the limit from ``bound_table()``, whose values are read from
the constants the analysers enforce, never restated. A reason code the table
does not know (the set is open: bounded evidence keeps an obligation's
original reason, and some reasons are built from template text) maps to
``bound: "unclassified"`` and is still listed: an unknown reason never hides
a source. Read failures are reported by the existing read-failure fields and
are not repeated here.

Every identifier passes the shared credential screen (``untrusted_text``)
and the strict sanitizer. An entry whose source identity is withheld cannot
be named; it is counted in ``withheld`` and the caller reports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Any, Iterable

from ..sanitization import sanitize_untrusted_data
from ..untrusted_text import screen

MAX_BOUNDED_SOURCES = 50
# Obligations retained per script scan for naming (scripts are not in the
# shared obligation ledger; HAMCP-226 keeps their coverage-failure terminals).
MAX_SCRIPT_BOUNDED_OBLIGATIONS = 1_000
UNCLASSIFIED = "unclassified"
# Reported by the read-failure fields of each output, not as bound overruns.
READ_FAILURE_REASON_CODES = frozenset(
    {
        "automation_id_missing",
        "automation_config_invalid",
        "automation_config_unreadable",
        "loaded_config_unavailable",
        "pending_write_unreconciled",
        "blueprint_source_unavailable",
        "blueprint_parser_unavailable",
        "blueprint_source_invalid",
    }
)


@dataclass(frozen=True)
class BoundedSources:
    entries: tuple[dict[str, Any], ...]
    omitted: int
    withheld: int
    total: int

    def public(self) -> dict[str, Any]:
        return {
            "bounded_sources": [dict(item) for item in self.entries],
            "bounded_sources_omitted": self.omitted,
            "bounded_sources_withheld": self.withheld,
            "bounded_source_total": self.total,
        }


EMPTY = BoundedSources((), 0, 0, 0)


@lru_cache(maxsize=1)
def bound_table() -> dict[str, tuple[str, int]]:
    """``reason_code`` to ``(constant name, value)`` for every fixed bound."""

    from . import extraction as e
    from . import index as i
    from . import obligation_ledger as o
    from . import provider as p

    def bound(module, name):
        return name, int(getattr(module, name))

    return {
        "template_source_limit_exceeded": bound(o, "MAX_TEMPLATE_SOURCE_CHARS"),
        "template_ast_node_limit_exceeded": bound(o, "MAX_TEMPLATE_AST_NODES"),
        "template_work_limit_exceeded": bound(o, "MAX_TEMPLATE_WORK_UNITS"),
        "template_depth_limit_exceeded": bound(o, "MAX_TEMPLATE_DEPTH"),
        "template_obligation_limit_exceeded": bound(o, "MAX_TEMPLATE_OBLIGATIONS"),
        "template_scope_limit_exceeded": bound(o, "MAX_TEMPLATE_SCOPES"),
        "template_macro_capture_limit_exceeded": bound(o, "MAX_TEMPLATE_MACRO_CAPTURES"),
        "template_namespace_limit_exceeded": bound(o, "MAX_TEMPLATE_BINDINGS"),
        "template_macro_limit_exceeded": bound(o, "MAX_TEMPLATE_BINDINGS"),
        "template_binding_limit_exceeded": bound(o, "MAX_TEMPLATE_BINDINGS"),
        "template_macro_argument_limit_exceeded": bound(o, "MAX_TEMPLATE_CANDIDATES"),
        "template_value_container_limit_exceeded": bound(o, "MAX_TEMPLATE_CANDIDATES"),
        "entity_candidate_limit_exceeded": bound(o, "MAX_TEMPLATE_CANDIDATES"),
        "obligation_evidence_limit_exceeded": bound(o, "MAX_TEMPLATE_CANDIDATES"),
        "literal_label_candidate_union_limit_exceeded": bound(o, "MAX_TEMPLATE_CANDIDATES"),
        "expand_candidate_limit_exceeded": bound(o, "MAX_TEMPLATE_CANDIDATES"),
        "external_template_reference_limit_exceeded": bound(o, "MAX_TEMPLATE_EXTERNAL_REFERENCES"),
        "template_abstract_value_limit_exceeded": bound(o, "MAX_TEMPLATE_ABSTRACT_VALUE_UNITS"),
        "template_attribute_path_limit_exceeded": bound(o, "MAX_TEMPLATE_VALUE_DEPTH"),
        "configuration_context_value_limit_exceeded": bound(o, "MAX_TEMPLATE_VALUE_DEPTH"),
        "configuration_obligation_limit_exceeded": bound(e, "MAX_DOCUMENT_OBLIGATIONS"),
        "configuration_structure_limit_exceeded": bound(e, "MAX_CONFIGURATION_NODES"),
        "configuration_fingerprint_limit_exceeded": bound(e, "MAX_CONFIGURATION_NODES"),
        "configuration_context_evidence_limit_exceeded": bound(e, "MAX_CONTEXT_ENTITY_IDS"),
        "configuration_entity_candidate_limit_exceeded": bound(e, "MAX_CONTEXT_ENTITY_IDS"),
        "state_changed_entity_filter_limit_exceeded": bound(e, "MAX_CONTEXT_ENTITY_IDS"),
        "call_service_entity_filter_limit_exceeded": bound(e, "MAX_CONTEXT_ENTITY_IDS"),
        "event_type_selector_limit_exceeded": bound(e, "MAX_EVENT_SELECTOR_VALUES"),
        "call_service_selector_limit_exceeded": bound(e, "MAX_EVENT_SELECTOR_VALUES"),
        "expand_resolution_entity_limit_exceeded": bound(e, "MAX_EXPAND_RESOLUTION_ENTITIES"),
        "expand_resolution_depth_limit_exceeded": bound(e, "MAX_EXPAND_RESOLUTION_DEPTH"),
        "expand_state_inventory_limit_exceeded": bound(e, "MAX_EXPAND_SNAPSHOT_ENTITIES"),
        "expand_membership_limit_exceeded": bound(e, "MAX_EXPAND_MEMBERS_PER_ENTITY"),
        "automation_inventory_limit_exceeded": bound(p, "MAX_AUTOMATION_SOURCES"),
        "blueprint_input_resolution_limit_exceeded": bound(p, "MAX_BLUEPRINT_RESOLUTION_NODES"),
        "blueprint_source_limit_exceeded": bound(p, "MAX_BLUEPRINT_SOURCE_BYTES"),
        "dependency_obligation_index_overflow": bound(i, "MAX_DEPENDENCY_OBLIGATIONS"),
    }


def bound_for(reason_code: str) -> tuple[str, int | None]:
    """``(bound, bound_value)``; an unknown code is ``unclassified``."""

    return bound_table().get(reason_code, (UNCLASSIFIED, None))


def coverage_failure(item: Any) -> bool:
    return bool(
        getattr(item, "coverage_failure_authority", False)
        or getattr(item, "outcome", None) == "coverage_failure"
    )


def bounded_source_entries(
    obligations: Iterable[Any],
    *,
    known_secrets: tuple[str, ...] = (),
    source_types: Iterable[str] | None = None,
) -> BoundedSources:
    """The over-bound sources among ``obligations``, at most ``MAX_BOUNDED_SOURCES``.

    One entry per source and reason code, in ``(source_type, source_id,
    source_entity_id, reason_code)`` order. ``total`` counts every entry
    before the bound; ``omitted`` those past it; ``withheld`` the entries
    whose source identity the screen withheld (never listed).
    """

    wanted = set(source_types) if source_types is not None else None
    keys: set[tuple[str, str, str, str]] = set()
    for item in obligations:
        if not coverage_failure(item):
            continue
        reason = str(getattr(item, "reason_code", "") or "")
        if reason in READ_FAILURE_REASON_CODES:
            continue
        source_type = str(getattr(item, "source_type", "") or "unknown")
        if wanted is not None and source_type not in wanted:
            continue
        keys.add(
            (
                source_type,
                str(getattr(item, "source_id", "") or ""),
                str(getattr(item, "source_entity_id", "") or ""),
                reason,
            )
        )
    entries: list[dict[str, Any]] = []
    withheld = 0
    for source_type, source_id, source_entity_id, reason in sorted(keys):
        entry = _entry(
            source_type, source_id, source_entity_id, reason, known_secrets
        )
        if entry is None:
            withheld += 1
            continue
        entries.append(entry)
    listed = tuple(entries[:MAX_BOUNDED_SOURCES])
    return BoundedSources(
        listed, len(entries) - len(listed), withheld, len(entries) + withheld
    )


def _entry(source_type, source_id, source_entity_id, reason, known_secrets):
    for value in (source_type, source_id, source_entity_id):
        if value and not _shown(value, known_secrets):
            return None
    if not source_id:
        return None
    bound, bound_value = bound_for(reason)
    if not reason or not _shown(reason, known_secrets):
        # A reason built from template text cannot be shown; the source still is.
        reason, bound, bound_value = UNCLASSIFIED, UNCLASSIFIED, None
    return {
        "source_type": source_type,
        "source_id": source_id,
        "source_entity_id": source_entity_id or None,
        "reason_code": reason,
        "bound": bound,
        "bound_value": bound_value,
    }


def _shown(value: str, known_secrets: tuple[str, ...]) -> bool:
    if len(value) > 256 or screen(value, known_secrets) is not None:
        return False
    safe = sanitize_untrusted_data(
        value, known_secrets=known_secrets, max_string=256
    )
    return not (
        safe.failed_closed
        or safe.redacted_field_count
        or safe.truncated_field_count
        or safe.value != value
    )


def screening_warnings(bounded: BoundedSources, screened_text: int = 0) -> list[str]:
    """The fixed warnings for named bound overruns and withheld text."""

    warnings = []
    if bounded.total:
        warnings.append(
            f"{bounded.total} source/reason pair(s) exceeded dependency-analysis bounds and could not be ruled out; see bounded_sources."
        )
    if bounded.omitted:
        warnings.append(
            f"{bounded.omitted} bounded source entr(ies) were omitted after the first {MAX_BOUNDED_SOURCES}."
        )
    if bounded.withheld:
        warnings.append(
            f"{bounded.withheld} bounded source(s) could not be named: the credential screen withheld their identifier."
        )
    if screened_text:
        warnings.append(
            f"{screened_text} text field(s) were withheld whole by the credential screen."
        )
    return warnings


AVAILABILITY_STATES = ("available", "unavailable", "unknown", "absent")
_LAST_CHANGED = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)


def availability(state: Any) -> str:
    """Availability from a state value; ``None`` (no state entry) is ``absent``.

    Home Assistant's ``STATE_UNAVAILABLE`` and ``STATE_UNKNOWN`` are the
    literal strings ``unavailable`` and ``unknown``; any other value is an
    available state.
    """

    if state is None:
        return "absent"
    value = str(state)
    return value if value in {"unavailable", "unknown"} else "available"


def last_changed(value: Any) -> str | None:
    """``last_changed`` only when it is an ISO 8601 timestamp, else ``None``."""

    text = str(value or "")
    return text if _LAST_CHANGED.fullmatch(text) else None


def target_availability(metadata: dict[str, Any]) -> dict[str, Any]:
    """``{state, last_changed}`` for the analysed entity's dependency metadata."""

    if not metadata.get("entity_exists"):
        return {"state": "absent", "last_changed": None}
    return {
        "state": availability(metadata.get("state")),
        "last_changed": last_changed(metadata.get("last_changed")),
    }
