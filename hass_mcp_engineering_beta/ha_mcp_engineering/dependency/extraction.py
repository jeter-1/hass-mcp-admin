"""Static exact-reference extraction for Home Assistant configuration objects."""

from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import json
import re
from typing import Any, Iterable

from ..logging_config import redact_data
from .dynamic_resolution import (
    BoundedTemplateContext,
    CandidateResolution,
    MAX_DYNAMIC_LABEL_SELECTORS,
)
from .models import (
    DependencyFinding,
    DependencyObligation,
    DynamicReference,
    evidence_id,
    obligation_fingerprint,
)
from .obligation_ledger import (
    MAX_TEMPLATE_CANDIDATES,
    TemplateContextEvidence,
    TemplateContextValueEvidence,
    analyze_template_obligations,
)
from .semantic_registry import (
    SEMANTIC_REGISTRY_MODEL,
    semantic_registry_identity,
)


ENTITY_ID_COMPONENT = re.compile(r"^[a-z0-9_]+$")
ENTITY_BEARING_KEYS = frozenset({"entity_id"})
ENTITY_TEMPLATE_HELPERS = frozenset(
    {
        "states",
        "is_state",
        "is_state_attr",
        "state_attr",
        "has_value",
        "expand",
    }
)
ENTITY_TEMPLATE_FILTERS = frozenset(
    {"states", "state_attr", "has_value"}
)
ENTITY_TEMPLATE_TESTS = frozenset(
    {"is_state", "is_state_attr", "has_value"}
)
ENTITY_COLLECTION_TEST_FILTERS = frozenset({"select", "reject"})
ENTITY_COLLECTION_ATTRIBUTE_TEST_FILTERS = frozenset(
    {"selectattr", "rejectattr"}
)
ENTITY_COLLECTION_MAP_FILTER = "map"
COLLECTION_CANDIDATE_PRESERVING_FILTERS = frozenset(
    {
        "select",
        "reject",
        "selectattr",
        "rejectattr",
        "list",
        "unique",
        "sort",
        "reverse",
    }
)
MAX_TEMPLATE_SEGMENT_CHARS = 65_536
MAX_TEMPLATE_ARGUMENT_CHARS = 4_096
MAX_LITERAL_ARGUMENTS = 100
MAX_TEMPLATE_NESTING = 8
FREE_TEXT_KEYS = {"alias", "description", "message", "title", "name", "friendly_name", "event_type"}
TEMPLATE_KEYS = {"value_template", "template", "availability", "state", "condition", "until", "while"}
ENTITY_OUTPUT_KEYS = frozenset(
    {
        "entity",
        "entity_id",
        "entity_ids",
        "entities",
    }
)
MAX_CONTEXT_ENTITY_IDS = 128
MAX_CONTEXT_VARIABLES = 128
MAX_CONTEXT_VALUE_DEPTH = 8
MAX_CONTEXT_SCALAR_CHARS = 1_024
MAX_DOCUMENT_OBLIGATIONS = 2_000
MAX_CONFIGURATION_NODES = 10_000
MAX_CONFIGURATION_DEPTH = 64
MAX_EVENT_SELECTOR_VALUES = 128
ACTION_SEQUENCE_KEYS = frozenset(
    {"action", "actions", "sequence", "then", "else", "default"}
)
ACTION_POSITION_KEYS = ACTION_SEQUENCE_KEYS.union({"parallel"})


def _blueprint_source_obligation_fingerprint(
    *,
    configuration_fingerprint: str,
    blueprint_path: Any,
    source_id: str,
) -> str:
    material = {
        "configuration_fingerprint": configuration_fingerprint,
        "path_sha256": (
            hashlib.sha256(str(blueprint_path).encode("utf-8")).hexdigest()
            if blueprint_path is not None
            else None
        ),
        "path_valid": isinstance(blueprint_path, str),
        "source_id": source_id,
    }
    return hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def valid_entity_id(value: str) -> bool:
    """Return whether *value* is an exact canonical Home Assistant entity ID.

    Syntax alone never establishes that a string is an entity reference; callers
    must also establish an entity-bearing configuration or template context.
    """

    if (
        not isinstance(value, str)
        or len(value) > 255
        or value != value.strip()
        or value != value.lower()
    ):
        return False
    if value.count(".") != 1 or any(marker in value for marker in ("{{", "}}", "{%", "%}")):
        return False
    domain, object_id = value.split(".", 1)
    if not domain or not object_id:
        return False
    if not ENTITY_ID_COMPONENT.fullmatch(domain) or not ENTITY_ID_COMPONENT.fullmatch(object_id):
        return False
    # Custom integrations may introduce domains unknown to this server. Requiring
    # a letter in each component rejects decimals and version fragments without a
    # brittle allow-list of Home Assistant domains.
    return any(char.isalpha() for char in domain) and any(
        char.isalpha() for char in object_id
    )


def make_coverage_failure_obligation(
    *,
    source_type: str,
    source_id: str,
    source_entity_id: str | None,
    config_path: str,
    relation: str,
    reason_code: str,
    configuration_fingerprint: str | None = None,
    limit_exceeded: bool = False,
) -> DependencyObligation:
    """Create one bounded identity-preserving coverage-failure terminal."""

    registry = semantic_registry_identity()
    material = json.dumps(
        {
            "source_type": source_type,
            "source_id": source_id,
            "source_entity_id": source_entity_id,
            "config_path": config_path,
            "relation": relation,
            "reason_code": reason_code,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    expression_fingerprint = hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()
    return DependencyObligation(
        evidence_id=evidence_id(
            source_type,
            source_id,
            config_path,
            relation,
            "coverage_failure",
            reason_code,
        ),
        source_type=source_type,
        source_id=source_id,
        source_entity_id=source_entity_id,
        config_path=config_path,
        relation=relation,
        outcome="coverage_failure",
        obligation_kind="coverage_failure",
        reason_code=reason_code,
        semantic_category="external_opaque",
        semantic_registry_version=SEMANTIC_REGISTRY_MODEL,
        semantic_registry_fingerprint=str(registry["sha256"]),
        expression_fingerprint=expression_fingerprint,
        configuration_fingerprint=(
            configuration_fingerprint or expression_fingerprint
        ),
        context_provenance=(f"configuration_path:{config_path}",),
        limit_exceeded=limit_exceeded,
        lock_projection="coverage_failure",
    )


def extract_document_obligation_evidence(
    *,
    source_type: str,
    source_id: str,
    config: dict[str, Any],
    source_entity_id: str | None = None,
    source_name: str | None = None,
    source_state: str | None = None,
    secret: str = "",
) -> tuple[
    list[DependencyFinding],
    list[DynamicReference],
    list[DependencyObligation],
]:
    """Analyze one complete configuration through the obligation ledger.

    The two compatibility projections are derived from the same terminal
    obligations retained by governance and F3.  They are never used to decide
    that missing ledger evidence is harmless.  In particular, complete Jinja
    strings are parsed once with shared statement/output scope; the historical
    per-segment scanner is not consulted by this API.
    """

    (
        configuration_fingerprint,
        configuration_fingerprint_limit_exceeded,
    ) = _dependency_configuration_fingerprint(config)
    context, context_limit_exceeded = _template_context_evidence(
        config,
        source_entity_id=source_entity_id,
    )
    context_value_budget = [MAX_CONFIGURATION_NODES]
    registry = semantic_registry_identity()
    safe_source_name = _bounded(source_name, secret=secret)
    safe_source_entity_id = _bounded(source_entity_id, 128, secret)
    safe_source_state = _bounded(source_state, 32, secret)
    findings: list[DependencyFinding] = []
    obligations: list[DependencyObligation] = []
    document_limit_exceeded = False
    document_limit_reason = "configuration_obligation_limit_exceeded"
    event_scan_nodes = 0
    blueprint = config.get("use_blueprint") if isinstance(config, dict) else None
    blueprint_path = blueprint.get("path") if isinstance(blueprint, dict) else None
    if context_limit_exceeded or configuration_fingerprint_limit_exceeded:
        obligations.append(
            make_coverage_failure_obligation(
                source_type=source_type,
                source_id=source_id,
                source_entity_id=safe_source_entity_id,
                config_path="$",
                relation="other_structured_reference",
                reason_code=(
                    "configuration_fingerprint_limit_exceeded"
                    if configuration_fingerprint_limit_exceeded
                    else "configuration_context_evidence_limit_exceeded"
                ),
                configuration_fingerprint=configuration_fingerprint,
                limit_exceeded=True,
            )
        )

    # A raw automation using a blueprint does not contain the blueprint's
    # causal trigger/condition/action body.  F3 configuration locking analyzes
    # proposed configuration locally, so absence of helper references in the
    # raw ``use_blueprint`` mapping cannot prove target exclusion.  Retain one
    # bounded external-source obligation.  A separate evidence-bound discharge
    # can replace it only after the exact resolved blueprint configuration has
    # itself produced a complete obligation ledger.  This makes create,
    # update, and removal serialize through the conservative helper-dependency
    # guard without adding blueprint write or reload authority.
    if (
        source_type == "automation"
        and isinstance(config, dict)
        and "use_blueprint" in config
    ):
        safe_blueprint_path = _bounded(blueprint_path, 256, secret)
        blueprint_fingerprint = _blueprint_source_obligation_fingerprint(
            configuration_fingerprint=configuration_fingerprint,
            blueprint_path=blueprint_path,
            source_id=source_id,
        )
        obligations.append(
            DependencyObligation(
                evidence_id=evidence_id(
                    source_type,
                    source_id,
                    "$.use_blueprint.path",
                    "blueprint_source_unavailable_to_local_analysis",
                    blueprint_fingerprint,
                ),
                source_type=source_type,
                source_id=source_id,
                source_entity_id=safe_source_entity_id,
                source_name=safe_source_name,
                source_state=safe_source_state,
                config_path="$.use_blueprint.path",
                relation="blueprint_resolved_role",
                outcome="bounded_semantic_opaque",
                obligation_kind="external_blueprint_source",
                reason_code=(
                    "blueprint_source_unavailable_to_local_analysis"
                ),
                semantic_category="external_opaque",
                semantic_registry_version=SEMANTIC_REGISTRY_MODEL,
                semantic_registry_fingerprint=str(registry["sha256"]),
                expression_fingerprint=blueprint_fingerprint,
                configuration_fingerprint=configuration_fingerprint,
                literal_selectors=(
                    (safe_blueprint_path,) if safe_blueprint_path else ()
                ),
                context_provenance=(
                    f"automation:{source_id}",
                    "configuration_path:$.use_blueprint.path",
                ),
                lock_projection="conservative",
            )
        )

    def add_structured(
        entity_id: str,
        relation: str,
        path: str,
        *,
        match_type: str = "structured_exact",
        blueprint_input: str | None = None,
    ) -> None:
        nonlocal document_limit_exceeded, document_limit_reason
        if not valid_entity_id(entity_id):
            return
        if len(obligations) >= MAX_DOCUMENT_OBLIGATIONS - 1:
            document_limit_exceeded = True
            document_limit_reason = "configuration_obligation_limit_exceeded"
            return
        finding_id = evidence_id(
            source_type,
            source_id,
            entity_id,
            relation,
            path,
            blueprint_input,
        )
        findings.append(
            DependencyFinding(
                evidence_id=finding_id,
                target_entity_id=entity_id,
                source_type=source_type,
                source_id=source_id,
                source_entity_id=safe_source_entity_id,
                source_name=safe_source_name,
                relation=relation,
                config_path=path,
                confidence=(
                    "resolved"
                    if match_type == "blueprint_resolved"
                    else "exact"
                ),
                match_type=match_type,
                blueprint_path=_bounded(blueprint_path, 256, secret),
                blueprint_input=blueprint_input,
                source_state=safe_source_state,
                evidence_summary=_summary(relation),
            )
        )
        expression_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "entity_id": entity_id,
                    "path": path,
                    "relation": relation,
                    "blueprint_input": blueprint_input,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        obligations.append(
            DependencyObligation(
                evidence_id=evidence_id(finding_id, "obligation"),
                source_type=source_type,
                source_id=source_id,
                source_entity_id=safe_source_entity_id,
                source_name=safe_source_name,
                source_state=safe_source_state,
                config_path=path,
                relation=relation,
                outcome="exact_dependency",
                obligation_kind="structured_entity_reference",
                reason_code="structured_exact_entity_reference",
                semantic_category="state_entity_access",
                semantic_registry_version=SEMANTIC_REGISTRY_MODEL,
                semantic_registry_fingerprint=str(registry["sha256"]),
                expression_fingerprint=expression_fingerprint,
                configuration_fingerprint=configuration_fingerprint,
                exact_entity_ids=(entity_id,),
                context_provenance=(f"configuration_path:{path}",),
                lock_projection="exact",
            )
        )

    def bounded_event_scalars(
        value: Any,
        *,
        key: str,
    ) -> tuple[set[str], bool, bool]:
        """Return finite scalar selectors without materializing overflow.

        Duplicate values remain complete; only malformed, templated, or
        bounded-overflow inputs make the selector non-conclusive.
        """

        if isinstance(value, str):
            raw_values = (value,)
            collection_complete = True
            limit_exceeded = len(value) > 256
        elif isinstance(value, list):
            raw_values = value[:MAX_EVENT_SELECTOR_VALUES]
            collection_complete = bool(value)
            limit_exceeded = len(value) > MAX_EVENT_SELECTOR_VALUES
        else:
            raw_values = ()
            collection_complete = False
            limit_exceeded = False
        values: set[str] = set()
        for item in raw_values:
            if (
                not isinstance(item, str)
                or len(item) > 256
                or _is_template(item, key)
            ):
                collection_complete = False
                if isinstance(item, str) and len(item) > 256:
                    limit_exceeded = True
                continue
            values.add(item)
        return values, bool(
            collection_complete and not limit_exceeded
        ), limit_exceeded

    def add_state_changed_event_obligation(
        trigger_config: dict[str, Any],
        *,
        path: str,
        relation: str,
    ) -> None:
        """Account for event triggers that can observe every state change."""

        nonlocal document_limit_exceeded, document_limit_reason
        trigger_kind = trigger_config.get(
            "platform", trigger_config.get("trigger")
        )
        if trigger_kind != "event":
            return
        (
            exact_event_types,
            event_type_complete,
            event_type_limit_exceeded,
        ) = bounded_event_scalars(
            trigger_config.get("event_type"), key="event_type"
        )
        dynamic_event_type = not event_type_complete
        if (
            "state_changed" not in exact_event_types
            and event_type_complete
        ):
            return

        event_data = trigger_config.get("event_data")
        entity_value = (
            event_data.get("entity_id")
            if isinstance(event_data, dict)
            else None
        )
        candidates, candidates_truncated = _bounded_literal_entities_deep(
            entity_value
        )
        candidate_set = tuple(sorted(set(candidates)))
        exact_filter = bool(
            "state_changed" in exact_event_types
            and not dynamic_event_type
            and candidate_set
            and not candidates_truncated
            and _entity_candidate_value_complete(entity_value)
        )
        if event_type_limit_exceeded:
            outcome = "coverage_failure"
            reason = "event_type_selector_limit_exceeded"
            category = "external_opaque"
            lock = "coverage_failure"
        elif candidates_truncated:
            outcome = "coverage_failure"
            reason = "state_changed_entity_filter_limit_exceeded"
            category = "external_opaque"
            lock = "coverage_failure"
        elif exact_filter:
            outcome = "exact_dependency"
            reason = "state_changed_exact_entity_filter"
            category = "state_entity_access"
            lock = "exact"
        else:
            outcome = "bounded_semantic_opaque"
            reason = (
                "dynamic_event_trigger_may_be_state_changed"
                if dynamic_event_type
                else "state_changed_event_filter_unbounded"
            )
            category = "state_entity_access"
            lock = "conservative"
        material = {
            "path": path,
            "relation": relation,
            "event_types": sorted(exact_event_types),
            "dynamic_event_type": dynamic_event_type,
            "candidate_entity_ids": list(candidate_set),
            "outcome": outcome,
            "reason": reason,
        }
        expression_fingerprint = hashlib.sha256(
            json.dumps(
                material, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        if len(obligations) >= MAX_DOCUMENT_OBLIGATIONS - 1:
            document_limit_exceeded = True
            document_limit_reason = "configuration_obligation_limit_exceeded"
            return
        obligations.append(
            DependencyObligation(
                evidence_id=evidence_id(
                    source_type,
                    source_id,
                    path,
                    "state_changed_event_trigger",
                    expression_fingerprint,
                ),
                source_type=source_type,
                source_id=source_id,
                source_entity_id=safe_source_entity_id,
                source_name=safe_source_name,
                source_state=safe_source_state,
                config_path=path,
                relation=relation,
                outcome=outcome,
                obligation_kind="state_changed_event_trigger",
                reason_code=reason,
                semantic_category=category,
                semantic_registry_version=SEMANTIC_REGISTRY_MODEL,
                semantic_registry_fingerprint=str(registry["sha256"]),
                expression_fingerprint=expression_fingerprint,
                configuration_fingerprint=configuration_fingerprint,
                exact_entity_ids=candidate_set,
                literal_selectors=tuple(sorted(exact_event_types)),
                context_provenance=(
                    f"configuration_path:{path}",
                    "event_type:state_changed",
                ),
                limit_exceeded=bool(
                    candidates_truncated or event_type_limit_exceeded
                ),
                lock_projection=lock,
            )
        )

    def add_call_service_event_obligation(
        trigger_config: dict[str, Any],
        *,
        path: str,
        relation: str,
    ) -> None:
        """Account for the event emitted by the exact helper service call."""

        nonlocal document_limit_exceeded, document_limit_reason
        trigger_kind = trigger_config.get(
            "platform", trigger_config.get("trigger")
        )
        if trigger_kind != "event":
            return
        (
            exact_event_types,
            event_type_complete,
            event_type_limit_exceeded,
        ) = bounded_event_scalars(
            trigger_config.get("event_type"), key="event_type"
        )
        if "call_service" not in exact_event_types and event_type_complete:
            return

        event_data = trigger_config.get("event_data")
        event_data = event_data if isinstance(event_data, dict) else {}

        domains, domains_complete, domains_limit = (
            bounded_event_scalars(
                event_data.get("domain"), key="event_data"
            )
        )
        services, services_complete, services_limit = (
            bounded_event_scalars(
                event_data.get("service"), key="event_data"
            )
        )
        disjoint = bool(
            (domains_complete and "input_boolean" not in domains)
            or (
                services_complete
                and not services.intersection({"turn_on", "turn_off"})
            )
        )
        service_data = event_data.get("service_data")
        entity_value = (
            service_data.get("entity_id")
            if isinstance(service_data, dict)
            else None
        )
        candidates, candidates_truncated = _bounded_literal_entities_deep(
            entity_value
        )
        candidate_set = tuple(sorted(set(candidates)))
        exact_target_filter = bool(
            candidate_set
            and not candidates_truncated
            and _entity_candidate_value_complete(entity_value)
        )
        if (
            event_type_limit_exceeded
            or domains_limit
            or services_limit
        ):
            outcome = "coverage_failure"
            reason = "call_service_selector_limit_exceeded"
            category = "external_opaque"
            lock = "coverage_failure"
        elif candidates_truncated:
            outcome = "coverage_failure"
            reason = "call_service_entity_filter_limit_exceeded"
            category = "external_opaque"
            lock = "coverage_failure"
        elif disjoint:
            outcome = "proven_target_exclusion"
            reason = "call_service_filter_proven_disjoint"
            category = "state_entity_access"
            lock = "none"
            candidate_set = ()
        elif exact_target_filter:
            outcome = "exact_dependency"
            reason = "call_service_exact_entity_filter"
            category = "state_entity_access"
            lock = "exact"
        else:
            outcome = "bounded_semantic_opaque"
            reason = "call_service_event_filter_unbounded"
            category = "state_entity_access"
            lock = "conservative"
        material = {
            "path": path,
            "relation": relation,
            "domains": sorted(domains),
            "domains_complete": domains_complete,
            "services": sorted(services),
            "services_complete": services_complete,
            "candidate_entity_ids": list(candidate_set),
            "outcome": outcome,
            "reason": reason,
        }
        expression_fingerprint = hashlib.sha256(
            json.dumps(
                material, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        if len(obligations) >= MAX_DOCUMENT_OBLIGATIONS - 1:
            document_limit_exceeded = True
            document_limit_reason = "configuration_obligation_limit_exceeded"
            return
        obligations.append(
            DependencyObligation(
                evidence_id=evidence_id(
                    source_type,
                    source_id,
                    path,
                    "call_service_event_trigger",
                    expression_fingerprint,
                ),
                source_type=source_type,
                source_id=source_id,
                source_entity_id=safe_source_entity_id,
                source_name=safe_source_name,
                source_state=safe_source_state,
                config_path=path,
                relation=relation,
                outcome=outcome,
                obligation_kind="call_service_event_trigger",
                reason_code=reason,
                semantic_category=category,
                semantic_registry_version=SEMANTIC_REGISTRY_MODEL,
                semantic_registry_fingerprint=str(registry["sha256"]),
                expression_fingerprint=expression_fingerprint,
                configuration_fingerprint=configuration_fingerprint,
                exact_entity_ids=candidate_set,
                literal_selectors=tuple(
                    sorted(
                        {
                            *(f"domain:{item}" for item in domains),
                            *(f"service:{item}" for item in services),
                        }
                    )
                ),
                context_provenance=(
                    f"configuration_path:{path}",
                    "event_type:call_service",
                ),
                limit_exceeded=bool(
                    candidates_truncated
                    or event_type_limit_exceeded
                    or domains_limit
                    or services_limit
                ),
                lock_projection=lock,
            )
        )

    def scan_event_triggers(
        value: Any,
        *,
        path: str,
        relation: str,
        depth: int = 0,
    ) -> None:
        nonlocal document_limit_exceeded, document_limit_reason
        nonlocal event_scan_nodes
        event_scan_nodes += 1
        if (
            event_scan_nodes > MAX_CONFIGURATION_NODES
            or depth > MAX_CONFIGURATION_DEPTH
        ):
            document_limit_exceeded = True
            document_limit_reason = "configuration_structure_limit_exceeded"
            return
        if isinstance(value, dict):
            add_state_changed_event_obligation(
                value, path=path, relation=relation
            )
            add_call_service_event_obligation(
                value, path=path, relation=relation
            )
            for key, item in value.items():
                if (
                    document_limit_exceeded
                    or event_scan_nodes >= MAX_CONFIGURATION_NODES
                ):
                    document_limit_exceeded = True
                    document_limit_reason = (
                        "configuration_structure_limit_exceeded"
                    )
                    break
                scan_event_triggers(
                    item,
                    path=f"{path}.{key}",
                    relation=_relation_for(
                        path, key, relation, source_type
                    ),
                    depth=depth + 1,
                )
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if (
                    document_limit_exceeded
                    or event_scan_nodes >= MAX_CONFIGURATION_NODES
                ):
                    document_limit_exceeded = True
                    document_limit_reason = (
                        "configuration_structure_limit_exceeded"
                    )
                    break
                scan_event_triggers(
                    item,
                    path=f"{path}[{index}]",
                    relation=relation,
                    depth=depth + 1,
                )

    def walk(
        value: Any,
        path: str,
        relation: str,
        parent_key: str = "",
        depth: int = 0,
        template_context: TemplateContextEvidence = context,
        structured_entity_roles: bool = True,
        entity_output_roles: bool = True,
    ) -> None:
        nonlocal document_limit_exceeded
        nonlocal configuration_walk_nodes
        nonlocal document_limit_reason
        configuration_walk_nodes += 1
        if (
            configuration_walk_nodes > MAX_CONFIGURATION_NODES
            or depth > MAX_CONFIGURATION_DEPTH
        ):
            document_limit_exceeded = True
            document_limit_reason = "configuration_structure_limit_exceeded"
            return
        if len(obligations) >= MAX_DOCUMENT_OBLIGATIONS - 1:
            document_limit_exceeded = True
            return
        if isinstance(value, dict):
            if set(value) == {"__blueprint_input__"}:
                input_name = _bounded(
                    value.get("__blueprint_input__"), 128, secret
                )
                expression_fingerprint = hashlib.sha256(
                    json.dumps(
                        {
                            "blueprint_input": input_name,
                            "path": path,
                            "relation": relation,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                obligations.append(
                    DependencyObligation(
                        evidence_id=evidence_id(
                            source_type,
                            source_id,
                            path,
                            "blueprint_input_unresolved",
                            input_name,
                        ),
                        source_type=source_type,
                        source_id=source_id,
                        source_entity_id=safe_source_entity_id,
                        source_name=safe_source_name,
                        source_state=safe_source_state,
                        config_path=path,
                        relation=relation,
                        outcome="bounded_semantic_opaque",
                        obligation_kind="blueprint_input",
                        reason_code="blueprint_input_value_unresolved",
                        semantic_category="external_opaque",
                        semantic_registry_version=SEMANTIC_REGISTRY_MODEL,
                        semantic_registry_fingerprint=str(
                            registry["sha256"]
                        ),
                        expression_fingerprint=expression_fingerprint,
                        configuration_fingerprint=(
                            configuration_fingerprint
                        ),
                        literal_selectors=(
                            (str(input_name),) if input_name else ()
                        ),
                        context_provenance=(
                            f"blueprint_input:{input_name or 'unknown'}",
                            f"configuration_path:{path}",
                        ),
                        lock_projection="conservative",
                    )
                )
                return
            if source_type == "scene" and path in {"$", "$.entities"}:
                entities = value.get("entities") if path == "$" else value
                if isinstance(entities, dict):
                    for entity in entities:
                        add_structured(
                            str(entity),
                            "scene_entity",
                            f"$.entities.{entity}",
                        )
            mapping_context = template_context
            root_variables_processed = False
            root_variables = value.get("variables") if path == "$" else None
            if isinstance(root_variables, dict):
                root_variables_processed = True
                if len(root_variables) > MAX_CONTEXT_VARIABLES:
                    document_limit_exceeded = True
                    document_limit_reason = (
                        "configuration_context_evidence_limit_exceeded"
                    )
                    return
                for raw_name, raw_value in root_variables.items():
                    name = str(raw_name)
                    variable_path = f"{path}.variables.{name}"
                    walk(
                        raw_value,
                        variable_path,
                        relation,
                        name,
                        depth + 2,
                        mapping_context,
                        False,
                        False,
                    )
                    mapping_context, variable_limit = (
                        _context_with_variables(
                            mapping_context,
                            {name: raw_value},
                            path=path,
                            context_value_budget=context_value_budget,
                            # Home Assistant preserves run variables supplied
                            # by automation.trigger/script callers instead of
                            # overwriting them with root configuration defaults.
                            join_existing=True,
                        )
                    )
                    if variable_limit:
                        document_limit_exceeded = True
                        document_limit_reason = (
                            "configuration_context_evidence_limit_exceeded"
                        )
                        return
            for key, item in value.items():
                if document_limit_exceeded:
                    break
                if (
                    path == "$"
                    and key == "variables"
                    and root_variables_processed
                ):
                    continue
                child_path = f"{path}.{key}"
                child_relation = _relation_for(
                    path, key, relation, source_type
                )
                if structured_entity_roles and key in ENTITY_BEARING_KEYS:
                    entity_values, entity_values_truncated = (
                        _bounded_literal_entities_deep(item)
                    )
                    if entity_values_truncated:
                        document_limit_exceeded = True
                        document_limit_reason = (
                            "configuration_entity_candidate_limit_exceeded"
                        )
                    for entity in entity_values:
                        add_structured(entity, child_relation, child_path)
                elif source_type == "group" and key == "entities":
                    entity_values, entity_values_truncated = (
                        _bounded_literal_entities_deep(item)
                    )
                    if entity_values_truncated:
                        document_limit_exceeded = True
                        document_limit_reason = (
                            "configuration_entity_candidate_limit_exceeded"
                        )
                    for entity in entity_values:
                        add_structured(entity, "group_member", child_path)
                elif path.endswith(".use_blueprint.input"):
                    if not any(
                        term in key.lower()
                        for term in (
                            "secret",
                            "token",
                            "password",
                            "webhook",
                            "api_key",
                            "url",
                        )
                    ):
                        (
                            blueprint_entities,
                            blueprint_entities_truncated,
                        ) = _bounded_literal_entities_deep(item)
                        if blueprint_entities_truncated:
                            document_limit_exceeded = True
                            document_limit_reason = (
                                "configuration_entity_candidate_limit_exceeded"
                            )
                        for entity in blueprint_entities:
                            add_structured(
                                entity,
                                "blueprint_input",
                                child_path,
                                blueprint_input=key,
                            )
                child_context = mapping_context
                if path.endswith(".repeat") and key in {
                    "sequence",
                    "while",
                    "until",
                }:
                    child_context, repeat_limit = _repeat_template_context(
                        template_context,
                        value,
                        context_value_budget=context_value_budget,
                    )
                    if repeat_limit:
                        document_limit_exceeded = True
                        document_limit_reason = (
                            "configuration_context_evidence_limit_exceeded"
                        )
                walk(
                    item,
                    child_path,
                    child_relation,
                    key,
                    depth + 1,
                    child_context,
                    structured_entity_roles,
                    entity_output_roles,
                )
            return
        if isinstance(value, list):
            sequence_context = template_context
            for index, item in enumerate(value):
                if document_limit_exceeded:
                    break
                item_path = f"{path}[{index}]"
                if (
                    parent_key in ACTION_POSITION_KEYS
                    and isinstance(item, dict)
                    and item.get("enabled", True) is False
                ):
                    # Home Assistant skips the complete action step, including
                    # sequence/parallel descendants, when enabled is the
                    # literal false value.
                    continue
                if (
                    parent_key in ACTION_POSITION_KEYS
                    and _is_direct_variables_action(item)
                ):
                    enabled = item.get("enabled", True)
                    if isinstance(enabled, str) and _is_template(
                        enabled, "enabled"
                    ):
                        walk(
                            enabled,
                            f"{item_path}.enabled",
                            relation,
                            "enabled",
                            depth + 2,
                            sequence_context,
                            False,
                            False,
                        )
                    if enabled is False:
                        continue
                    if len(item["variables"]) > MAX_CONTEXT_VARIABLES:
                        document_limit_exceeded = True
                        document_limit_reason = (
                            "configuration_context_evidence_limit_exceeded"
                        )
                        break

                    # Home Assistant renders a variables action in mapping
                    # insertion order and makes each completed assignment
                    # visible to the next one.  Analyze the value with that
                    # execution-path context before transferring its bounded
                    # value.  A dynamic ``enabled`` property joins the
                    # skipped and executed paths rather than overwriting the
                    # pre-action binding.
                    pre_action_context = sequence_context
                    variable_context = pre_action_context
                    conditional_transfer = enabled is not True
                    for raw_name, raw_value in item["variables"].items():
                        name = str(raw_name)
                        variable_path = (
                            f"{item_path}.variables.{name}"
                        )
                        walk(
                            raw_value,
                            variable_path,
                            relation,
                            name,
                            depth + 2,
                            variable_context,
                            False,
                            False,
                        )
                        variable_context, variable_limit = (
                            _context_with_variables(
                                variable_context,
                                {name: raw_value},
                                path=item_path,
                                context_value_budget=context_value_budget,
                                join_existing=False,
                            )
                        )
                        if variable_limit:
                            document_limit_exceeded = True
                            document_limit_reason = (
                                "configuration_context_evidence_limit_exceeded"
                            )
                            break
                    # Parallel branches share the incoming context but never
                    # transfer bindings laterally into a sibling branch.
                    # Their possible post-branch values are joined only by
                    # the enclosing parallel action when execution continues.
                    if parent_key != "parallel":
                        sequence_context = (
                            _join_template_contexts(
                                pre_action_context, variable_context
                            )
                            if conditional_transfer
                            else variable_context
                        )
                    continue
                walk(
                    item,
                    item_path,
                    relation,
                    parent_key,
                    depth + 1,
                    sequence_context,
                    structured_entity_roles,
                    entity_output_roles,
                )
                if parent_key in ACTION_SEQUENCE_KEYS:
                    variable_actions, variable_scan_limit = (
                        _nested_variable_actions(item, path=item_path)
                    )
                    if variable_scan_limit:
                        document_limit_exceeded = True
                        document_limit_reason = (
                            "configuration_context_evidence_limit_exceeded"
                        )
                        break
                    for (
                        variable_path,
                        variables,
                        conditional_transfer,
                    ) in variable_actions:
                        sequence_context, variable_limit = (
                            _context_with_variables(
                                sequence_context,
                                variables,
                                path=variable_path,
                                context_value_budget=context_value_budget,
                                join_existing=bool(
                                    variable_path != item_path
                                    or conditional_transfer
                                ),
                            )
                        )
                        if variable_limit:
                            document_limit_exceeded = True
                            document_limit_reason = (
                                "configuration_context_evidence_limit_exceeded"
                            )
                            break
            return
        if not isinstance(value, str) or not _is_template(value, parent_key):
            return
        result = analyze_template_obligations(
            value,
            source_type=source_type,
            source_id=source_id,
            config_path=path,
            relation=relation,
            source_entity_id=safe_source_entity_id,
            source_name=safe_source_name,
            source_state=safe_source_state,
            configuration_fingerprint=configuration_fingerprint,
            entity_id_validator=valid_entity_id,
            context=template_context,
            entity_output_role=bool(
                entity_output_roles and parent_key in ENTITY_OUTPUT_KEYS
            ),
        )
        remaining = MAX_DOCUMENT_OBLIGATIONS - 1 - len(obligations)
        if len(result.obligations) > remaining:
            document_limit_exceeded = True
            document_limit_reason = "configuration_obligation_limit_exceeded"
        obligations.extend(result.obligations[: max(0, remaining)])

    configuration_walk_nodes = 0
    scan_event_triggers(
        config, path="$", relation="other_structured_reference"
    )
    walk(config, "$", "other_structured_reference")
    if document_limit_exceeded:
        obligations.append(
            make_coverage_failure_obligation(
                source_type=source_type,
                source_id=source_id,
                source_entity_id=safe_source_entity_id,
                config_path="$",
                relation="other_structured_reference",
                reason_code=document_limit_reason,
                configuration_fingerprint=configuration_fingerprint,
                limit_exceeded=True,
            )
        )
    if not obligations:
        obligations.append(
            DependencyObligation(
                evidence_id=evidence_id(
                    source_type, source_id, "configuration_neutral"
                ),
                source_type=source_type,
                source_id=source_id,
                source_entity_id=safe_source_entity_id,
                source_name=safe_source_name,
                source_state=safe_source_state,
                config_path="$",
                relation="other_structured_reference",
                outcome="proven_dependency_neutral",
                obligation_kind="whole_configuration",
                reason_code="configuration_dependency_neutral",
                semantic_category="dependency_neutral",
                semantic_registry_version=SEMANTIC_REGISTRY_MODEL,
                semantic_registry_fingerprint=str(registry["sha256"]),
                expression_fingerprint=hashlib.sha256(
                    b"configuration_dependency_neutral"
                ).hexdigest(),
                configuration_fingerprint=configuration_fingerprint,
            )
        )
    ordered_obligations = sorted(
        _deduplicate_obligations(obligations),
        key=lambda item: (
            item.source_type,
            item.source_entity_id or "",
            item.source_id,
            item.config_path,
            item.evidence_id,
            obligation_fingerprint(item),
        ),
    )
    projected_findings, projected_dynamic = _project_obligations(
        ordered_obligations,
        secret=secret,
    )
    findings.extend(projected_findings)
    return (
        _deduplicate(findings),
        _deduplicate_dynamic(projected_dynamic),
        ordered_obligations,
    )


# Canonical shared safety API used by provider, governance, and F3.  Keep the
# longer name as a descriptive compatibility alias for focused callers.
extract_document_with_obligations = extract_document_obligation_evidence


def discharge_resolved_blueprint_source_obligation(
    *,
    automation_config: dict[str, Any],
    resolved_blueprint_config: dict[str, Any],
    raw_obligations: Iterable[DependencyObligation],
    source_id: str,
    source_entity_id: str | None = None,
    source_name: str | None = None,
    source_state: str | None = None,
    secret: str = "",
) -> tuple[
    list[DependencyObligation],
    list[DependencyFinding],
    list[DynamicReference],
    list[DependencyObligation],
    frozenset[str],
]:
    """Evidence-bind one raw blueprint boundary to its analyzed source ledger.

    This is deliberately not a caller assertion.  The raw automation and
    resolved blueprint configurations are fingerprinted again, the exact raw
    external-source obligation must match, and every resolved terminal must
    belong to the same source/configuration and be free of coverage failure.
    Any mismatch keeps the raw opacity and its conservative lock projection.
    """

    raw = list(raw_obligations)
    (
        resolved_findings,
        resolved_dynamic,
        resolved,
    ) = extract_document_obligation_evidence(
        source_type="blueprint",
        source_id=source_id,
        source_entity_id=source_entity_id,
        source_name=source_name,
        source_state=source_state,
        config=resolved_blueprint_config,
        secret=secret,
    )
    automation_fingerprint, automation_limit = (
        _dependency_configuration_fingerprint(automation_config)
    )
    resolved_fingerprint, resolved_limit = (
        _dependency_configuration_fingerprint(resolved_blueprint_config)
    )
    blueprint = (
        automation_config.get("use_blueprint")
        if isinstance(automation_config, dict)
        else None
    )
    blueprint_path = (
        blueprint.get("path") if isinstance(blueprint, dict) else None
    )
    expected_expression_fingerprint = (
        _blueprint_source_obligation_fingerprint(
            configuration_fingerprint=automation_fingerprint,
            blueprint_path=blueprint_path,
            source_id=source_id,
        )
    )
    candidates = [
        item
        for item in raw
        if item.source_type == "automation"
        and item.source_id == source_id
        and item.obligation_kind == "external_blueprint_source"
        and item.reason_code
        == "blueprint_source_unavailable_to_local_analysis"
    ]
    resolved_is_bound = bool(
        not automation_limit
        and not resolved_limit
        and isinstance(blueprint_path, str)
        and len(candidates) == 1
        and candidates[0].configuration_fingerprint
        == automation_fingerprint
        and candidates[0].expression_fingerprint
        == expected_expression_fingerprint
        and candidates[0].literal_selectors
        == (_bounded(blueprint_path, 256, secret),)
        and resolved
        and all(
            item.source_type == "blueprint"
            and item.source_id == source_id
            and item.configuration_fingerprint == resolved_fingerprint
            and item.semantic_registry_version
            == candidates[0].semantic_registry_version
            and item.semantic_registry_fingerprint
            == candidates[0].semantic_registry_fingerprint
            and item.outcome != "coverage_failure"
            and not item.limit_exceeded
            for item in resolved
        )
    )
    if not resolved_is_bound:
        return (
            raw,
            resolved_findings,
            resolved_dynamic,
            resolved,
            frozenset(),
        )

    candidate = candidates[0]
    resolved_ledger_fingerprint = hashlib.sha256(
        json.dumps(
            sorted(obligation_fingerprint(item) for item in resolved),
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    discharge_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "raw_obligation": obligation_fingerprint(candidate),
                "resolved_configuration": resolved_fingerprint,
                "resolved_ledger": resolved_ledger_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    discharged = replace(
        candidate,
        outcome="proven_dependency_neutral",
        reason_code="blueprint_source_analyzed_by_obligation_ledger",
        semantic_category="external_source_discharge",
        expression_fingerprint=discharge_fingerprint,
        context_provenance=tuple(
            sorted(
                set(candidate.context_provenance).union(
                    {
                        "blueprint_source:resolved",
                        (
                            "resolved_blueprint_configuration:"
                            + resolved_fingerprint
                        ),
                        (
                            "resolved_blueprint_ledger:"
                            + resolved_ledger_fingerprint
                        ),
                    }
                )
            )
        ),
        lock_projection="none",
    )
    adjusted = [
        discharged if item.evidence_id == candidate.evidence_id else item
        for item in raw
    ]
    return (
        adjusted,
        resolved_findings,
        resolved_dynamic,
        resolved,
        frozenset(
            {evidence_id(candidate.evidence_id, "compatibility")}
        ),
    )


def _project_obligations(
    obligations: list[DependencyObligation],
    *,
    secret: str,
) -> tuple[list[DependencyFinding], list[DynamicReference]]:
    """Project ledger terminals into the shipped compatibility records."""

    findings: list[DependencyFinding] = []
    dynamic: list[DynamicReference] = []
    for item in obligations:
        if item.obligation_kind == "structured_entity_reference":
            continue
        if item.outcome == "exact_dependency" and item.exact_entity_ids:
            for entity_id in item.exact_entity_ids:
                findings.append(
                    DependencyFinding(
                        evidence_id=evidence_id(
                            item.evidence_id, "compatibility", entity_id
                        ),
                        target_entity_id=entity_id,
                        source_type=item.source_type,
                        source_id=item.source_id,
                        source_entity_id=item.source_entity_id,
                        source_name=item.source_name,
                        relation=item.relation,
                        config_path=item.config_path,
                        confidence="exact",
                        match_type="template_ast_exact",
                        source_state=item.source_state,
                        evidence_summary=_summary(item.relation),
                    )
                )
            if not item.possible_entity_domains:
                continue
        if item.outcome not in {
            "exact_dependency",
            "bounded_semantic_opaque",
            "coverage_failure",
        }:
            continue
        if (
            item.outcome == "exact_dependency"
            and item.exact_entity_ids
            and not item.possible_entity_domains
            and not item.literal_selectors
        ):
            continue
        complete = item.outcome == "exact_dependency"
        dynamic.append(
            DynamicReference(
                evidence_id=evidence_id(item.evidence_id, "compatibility"),
                source_type=item.source_type,
                source_id=item.source_id,
                config_path=item.config_path,
                warning=(
                    "Template dependency evidence exceeded coverage bounds."
                    if item.outcome == "coverage_failure"
                    else "Template entity dependency is semantically opaque."
                    if item.outcome == "bounded_semantic_opaque"
                    else "Template dependency candidates were resolved statically."
                ),
                excerpt=None,
                source_entity_id=item.source_entity_id,
                source_name=item.source_name,
                source_state=item.source_state,
                possible_entity_domains=item.possible_entity_domains,
                possible_entity_ids=item.exact_entity_ids,
                literal_label_selectors=item.literal_selectors,
                candidate_resolution_kind=(
                    "coverage_failure"
                    if item.outcome == "coverage_failure"
                    else "semantic_opaque"
                    if item.outcome == "bounded_semantic_opaque"
                    else "ast_exact_candidates"
                ),
                candidate_resolution_complete=complete,
                candidate_resolution_limit_exceeded=bool(
                    item.limit_exceeded
                    or item.outcome == "coverage_failure"
                ),
            )
        )
    return findings, dynamic


def project_obligations(
    obligations: Iterable[DependencyObligation],
    *,
    secret: str = "",
) -> tuple[list[DependencyFinding], list[DynamicReference]]:
    """Project an already-resolved authoritative ledger for compatibility."""

    return _project_obligations(list(obligations), secret=secret)


def resolve_literal_label_obligations(
    obligations: Iterable[DependencyObligation],
    *,
    label_memberships: dict[str, tuple[str, ...]],
    label_membership_fingerprints: dict[str, str],
    label_membership_truncated: Iterable[str],
    label_registry_complete: bool,
) -> list[DependencyObligation]:
    """Discharge literal ``label_entities`` opacity from one scan snapshot.

    The template analyzer proves only the selector's bounded provenance.  The
    dependency provider then resolves that selector against the same complete
    entity/label registry generation used to build the index.  Failed,
    truncated, mixed-producer, or dynamic selector evidence is left opaque.
    """

    truncated = set(label_membership_truncated)
    resolved: list[DependencyObligation] = []
    for item in obligations:
        context = set(item.context_provenance)
        producers = {
            value.removeprefix("entity_set_producer:")
            for value in context
            if value.startswith("entity_set_producer:")
        }
        eligible = bool(
            producers == {"label_entities"}
            and "entity_selector_provenance:complete" in context
            and "entity_selector_provenance:incomplete" not in context
            and item.literal_selectors
            and label_registry_complete
        )
        if not eligible:
            resolved.append(item)
            continue
        selectors = tuple(sorted(set(item.literal_selectors)))
        if any(
            selector in truncated
            or selector not in label_memberships
            or selector not in label_membership_fingerprints
            for selector in selectors
        ):
            resolved.append(item)
            continue
        candidates = tuple(
            sorted(
                {
                    entity_id
                    for selector in selectors
                    for entity_id in label_memberships[selector]
                    if valid_entity_id(entity_id)
                },
                key=lambda value: value.encode("utf-8"),
            )
        )
        if len(candidates) > MAX_TEMPLATE_CANDIDATES:
            resolved.append(item)
            continue
        if any(
            len(label_memberships[selector])
            != sum(
                valid_entity_id(entity_id)
                for entity_id in label_memberships[selector]
            )
            for selector in selectors
        ):
            resolved.append(item)
            continue
        resolution_material = {
            "model": "literal-label-membership-v1",
            "selectors": [
                {
                    "selector": selector,
                    "membership_fingerprint": (
                        label_membership_fingerprints[selector]
                    ),
                }
                for selector in selectors
            ],
            "candidate_entity_ids": list(candidates),
        }
        resolution_fingerprint = hashlib.sha256(
            json.dumps(
                resolution_material,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        bound_context = tuple(
            sorted(
                context.union(
                    {
                        "label_membership_model:literal-label-membership-v1",
                        "label_membership_fingerprint:"
                        + resolution_fingerprint,
                    }
                )
            )
        )
        resolved.append(
            replace(
                item,
                outcome=(
                    "exact_dependency"
                    if candidates
                    else "proven_dependency_neutral"
                ),
                reason_code=(
                    "literal_label_membership_resolved"
                    if candidates
                    else "literal_label_membership_empty"
                ),
                semantic_category=(
                    "state_entity_access"
                    if candidates
                    else "dependency_neutral"
                ),
                exact_entity_ids=candidates,
                possible_entity_domains=(
                    tuple(
                        sorted(
                            {value.split(".", 1)[0] for value in candidates}
                        )
                    )
                    if candidates
                    else None
                ),
                context_provenance=bound_context,
                lock_projection="exact" if candidates else "none",
            )
        )
    return resolved


def _deduplicate_obligations(
    items: list[DependencyObligation],
) -> list[DependencyObligation]:
    return list({item.evidence_id: item for item in items}.values())


def _dependency_configuration_fingerprint(
    config: dict[str, Any],
) -> tuple[str, bool]:
    work_units = 0
    limit_exceeded = False

    def normalize(value: Any, key: str = "", depth: int = 0) -> Any:
        nonlocal work_units, limit_exceeded
        work_units += 1
        if (
            work_units > MAX_CONFIGURATION_NODES
            or depth > MAX_CONFIGURATION_DEPTH
        ):
            limit_exceeded = True
            return {"coverage_limit": True}
        if isinstance(value, dict):
            remaining = max(0, MAX_CONFIGURATION_NODES - work_units)
            if len(value) > remaining:
                limit_exceeded = True
                return {
                    "coverage_limit": True,
                    "container_size": len(value),
                }
            if key == "variables":
                # Home Assistant renders a variables mapping in insertion
                # order.  Preserve that order inside the otherwise canonical
                # configuration fingerprint so reordering material dataflow
                # invalidates an earlier approval.
                ordered_variables: list[list[Any]] = []
                for item_key, item in value.items():
                    if work_units >= MAX_CONFIGURATION_NODES:
                        limit_exceeded = True
                        ordered_variables.append(
                            [
                                "__coverage_limit__",
                                {"container_size": len(value)},
                            ]
                        )
                        break
                    ordered_variables.append(
                        [
                            str(item_key),
                            normalize(item, str(item_key), depth + 1),
                        ]
                    )
                return {"__ordered_variables__": ordered_variables}
            normalized: dict[str, Any] = {}
            for item_key, item in sorted(
                value.items(), key=lambda pair: str(pair[0])
            ):
                if work_units >= MAX_CONFIGURATION_NODES:
                    limit_exceeded = True
                    normalized["__coverage_limit__"] = {
                        "container_size": len(value)
                    }
                    break
                if (
                    str(item_key) in {
                        "alias",
                        "description",
                        "friendly_name",
                        "name",
                    }
                    and not (
                        isinstance(item, str)
                        and _is_template(item, str(item_key))
                    )
                ):
                    continue
                normalized[str(item_key)] = normalize(
                    item, str(item_key), depth + 1
                )
            return normalized
        if isinstance(value, list):
            normalized_list: list[Any] = []
            for item in value:
                if work_units >= MAX_CONFIGURATION_NODES:
                    limit_exceeded = True
                    normalized_list.append(
                        {"coverage_limit": True, "container_size": len(value)}
                    )
                    break
                normalized_list.append(normalize(item, key, depth + 1))
            return normalized_list
        if isinstance(value, tuple):
            normalized_tuple: list[Any] = []
            for item in value:
                if work_units >= MAX_CONFIGURATION_NODES:
                    limit_exceeded = True
                    normalized_tuple.append(
                        {"coverage_limit": True, "container_size": len(value)}
                    )
                    break
                normalized_tuple.append(normalize(item, key, depth + 1))
            return normalized_tuple
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return {"type": type(value).__name__}

    encoded = json.dumps(
        normalize(config),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), limit_exceeded


def _bounded_context_value(
    value: Any,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> tuple[TemplateContextValueEvidence, bool]:
    """Convert one configuration value into bounded parse-only evidence."""

    if budget is None:
        budget = [MAX_CONFIGURATION_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_CONTEXT_VALUE_DEPTH:
        marker = "configuration_context_value_limit_exceeded"
        return (
            TemplateContextValueEvidence(
                kind="opaque",
                complete=False,
                fingerprint=hashlib.sha256(marker.encode()).hexdigest(),
            ),
            True,
        )

    def finish(
        kind: str,
        *,
        literal_string: str | None = None,
        literal_number: float | None = None,
        literal_boolean: bool | None = None,
        fields: tuple[
            tuple[str, TemplateContextValueEvidence], ...
        ] = (),
        items: tuple[TemplateContextValueEvidence, ...] = (),
        complete: bool = True,
    ) -> TemplateContextValueEvidence:
        material = {
            "kind": kind,
            "literal_string_sha256": (
                hashlib.sha256(literal_string.encode("utf-8")).hexdigest()
                if literal_string is not None
                else None
            ),
            "literal_number": literal_number,
            "literal_boolean": literal_boolean,
            "fields": [(name, item.fingerprint) for name, item in fields],
            "items": [item.fingerprint for item in items],
            "complete": complete,
        }
        return TemplateContextValueEvidence(
            kind=kind,
            literal_string=literal_string,
            literal_number=literal_number,
            literal_boolean=literal_boolean,
            fields=fields,
            items=items,
            complete=complete,
            fingerprint=hashlib.sha256(
                json.dumps(
                    material,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        )

    if isinstance(value, str):
        if _contains_template_value(value):
            return finish("dynamic_scalar", complete=False), False
        if len(value) > MAX_CONTEXT_SCALAR_CHARS:
            return finish("opaque", complete=False), True
        return finish("string", literal_string=value), False
    if isinstance(value, bool):
        return finish("boolean", literal_boolean=value), False
    if value is None:
        return finish("null"), False
    if isinstance(value, (int, float)):
        return finish("number", literal_number=float(value)), False
    if isinstance(value, dict):
        if len(value) > MAX_CONTEXT_VARIABLES:
            return finish("opaque", complete=False), True
        fields: list[tuple[str, TemplateContextValueEvidence]] = []
        limit_exceeded = False
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            if not isinstance(key, str) or len(key) > MAX_CONTEXT_SCALAR_CHARS:
                return finish("opaque", complete=False), True
            child, child_limit = _bounded_context_value(
                item,
                depth=depth + 1,
                budget=budget,
            )
            fields.append((key, child))
            limit_exceeded = bool(limit_exceeded or child_limit)
        return (
            finish(
                "mapping",
                fields=tuple(fields),
                # Shape completeness is independent from member semantics.
                # An opaque member must not poison a proven ordinary sibling.
                complete=not limit_exceeded,
            ),
            limit_exceeded,
        )
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_CONTEXT_ENTITY_IDS:
            return finish("opaque", complete=False), True
        items: list[TemplateContextValueEvidence] = []
        limit_exceeded = False
        for item in value:
            child, child_limit = _bounded_context_value(
                item,
                depth=depth + 1,
                budget=budget,
            )
            items.append(child)
            limit_exceeded = bool(limit_exceeded or child_limit)
        return (
            finish(
                "sequence",
                items=tuple(items),
                complete=not limit_exceeded,
            ),
            limit_exceeded,
        )
    return finish("opaque", complete=False), False


def _template_context_evidence(
    config: dict[str, Any],
    *,
    source_entity_id: str | None,
) -> tuple[TemplateContextEvidence, bool]:
    trigger_ids: set[str] = set()
    trigger_zone_ids: set[str] = set()
    wait_trigger_ids: set[str] = set()
    wait_trigger_zone_ids: set[str] = set()
    provenance: set[str] = set()
    limit_exceeded = False
    work_units = 0

    def tick(depth: int) -> bool:
        nonlocal work_units, limit_exceeded
        work_units += 1
        if (
            work_units > MAX_CONFIGURATION_NODES
            or depth > MAX_CONFIGURATION_DEPTH
        ):
            limit_exceeded = True
            return False
        return True

    def collect_trigger_entities(
        value: Any, output: set[str], depth: int = 0
    ) -> None:
        nonlocal limit_exceeded
        if not tick(depth):
            return
        if isinstance(value, dict):
            entities = value.get("entity_id")
            bounded_entities, entities_truncated = (
                _bounded_literal_entities_deep(entities)
            )
            if entities_truncated:
                limit_exceeded = True
            for entity_id in bounded_entities:
                output.add(entity_id)
            for item in value.values():
                if work_units >= MAX_CONFIGURATION_NODES:
                    break
                if isinstance(item, (dict, list)):
                    collect_trigger_entities(item, output, depth + 1)
        elif isinstance(value, list):
            for item in value:
                if work_units >= MAX_CONFIGURATION_NODES:
                    break
                collect_trigger_entities(item, output, depth + 1)

    def collect_zone_trigger_entities(
        value: Any, output: set[str], depth: int = 0
    ) -> None:
        """Collect exact zone objects only from reviewed zone triggers."""

        nonlocal limit_exceeded
        if not tick(depth):
            return
        if isinstance(value, list):
            for item in value:
                if work_units >= MAX_CONFIGURATION_NODES:
                    break
                collect_zone_trigger_entities(item, output, depth + 1)
            return
        if not isinstance(value, dict):
            return
        trigger_kind = value.get("platform", value.get("trigger"))
        if trigger_kind == "zone":
            zones, zones_truncated = _bounded_literal_entities_deep(
                value.get("zone")
            )
            if zones_truncated:
                limit_exceeded = True
            output.update(
                entity_id
                for entity_id in zones
                if entity_id.startswith("zone.")
            )

    for key in ("trigger", "triggers"):
        if key in config:
            before = len(trigger_ids)
            collect_trigger_entities(config[key], trigger_ids, 0)
            before_zone = len(trigger_zone_ids)
            collect_zone_trigger_entities(
                config[key], trigger_zone_ids, 0
            )
            if len(trigger_ids) > before:
                provenance.add(f"automation.{key}.entity_id")
            if len(trigger_zone_ids) > before_zone:
                provenance.add(f"automation.{key}.zone")

    def collect_wait_triggers(value: Any, depth: int = 0) -> None:
        if not tick(depth):
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if work_units >= MAX_CONFIGURATION_NODES:
                    break
                if key == "wait_for_trigger":
                    before = len(wait_trigger_ids)
                    collect_trigger_entities(
                        item, wait_trigger_ids, depth + 1
                    )
                    if len(wait_trigger_ids) > before:
                        provenance.add("automation.wait_for_trigger.entity_id")
                    before_zone = len(wait_trigger_zone_ids)
                    collect_zone_trigger_entities(
                        item, wait_trigger_zone_ids, depth + 1
                    )
                    if len(wait_trigger_zone_ids) > before_zone:
                        provenance.add("automation.wait_for_trigger.zone")
                collect_wait_triggers(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                if work_units >= MAX_CONFIGURATION_NODES:
                    break
                collect_wait_triggers(item, depth + 1)

    collect_wait_triggers(config)

    # Root configuration variables are rendered in mapping insertion order
    # and become visible one assignment at a time.  The document walker owns
    # that path-sequential transfer.  Pre-seeding the context here would let a
    # later binding influence an earlier template and could falsely prove a
    # target exclusion.
    if len(trigger_ids) > MAX_CONTEXT_ENTITY_IDS:
        limit_exceeded = True
    if len(trigger_zone_ids) > MAX_CONTEXT_ENTITY_IDS:
        limit_exceeded = True
    if len(wait_trigger_ids) > MAX_CONTEXT_ENTITY_IDS:
        limit_exceeded = True
    if len(wait_trigger_zone_ids) > MAX_CONTEXT_ENTITY_IDS:
        limit_exceeded = True
    trigger_ids = set(sorted(trigger_ids)[:MAX_CONTEXT_ENTITY_IDS])
    trigger_zone_ids = set(
        sorted(trigger_zone_ids)[:MAX_CONTEXT_ENTITY_IDS]
    )
    wait_trigger_ids = set(
        sorted(wait_trigger_ids)[:MAX_CONTEXT_ENTITY_IDS]
    )
    wait_trigger_zone_ids = set(
        sorted(wait_trigger_zone_ids)[:MAX_CONTEXT_ENTITY_IDS]
    )
    safe_this = (
        source_entity_id
        if isinstance(source_entity_id, str)
        and valid_entity_id(source_entity_id)
        else None
    )
    if safe_this:
        provenance.add("automation.this.entity_id")
    return (
        TemplateContextEvidence(
            trigger_entity_ids=tuple(sorted(trigger_ids)),
            trigger_zone_entity_ids=tuple(sorted(trigger_zone_ids)),
            wait_trigger_entity_ids=tuple(sorted(wait_trigger_ids)),
            wait_trigger_zone_entity_ids=tuple(
                sorted(wait_trigger_zone_ids)
            ),
            this_entity_id=safe_this,
            provenance=tuple(sorted(provenance)),
        ),
        limit_exceeded,
    )


def _repeat_template_context(
    base: TemplateContextEvidence,
    repeat_config: Any,
    *,
    context_value_budget: list[int],
) -> tuple[TemplateContextEvidence, bool]:
    """Add the exact bounded Home Assistant repeat context for its sequence."""

    if not isinstance(repeat_config, dict):
        return base, False

    def runtime_scalar(name: str) -> TemplateContextValueEvidence:
        return TemplateContextValueEvidence(
            kind="dynamic_scalar",
            complete=True,
            fingerprint=hashlib.sha256(
                f"ha_repeat_runtime_scalar:{name}".encode("utf-8")
            ).hexdigest(),
        )

    fields: dict[str, tuple[TemplateContextValueEvidence, ...]] = {}
    if "count" in repeat_config or "for_each" in repeat_config:
        for name in ("index", "first", "last"):
            fields[name] = (runtime_scalar(name),)
    elif "while" in repeat_config or "until" in repeat_config:
        fields["index"] = (runtime_scalar("index"),)

    limit_exceeded = False
    if "for_each" in repeat_config:
        item, item_limit = _bounded_context_value(
            repeat_config.get("for_each"),
            budget=context_value_budget,
        )
        limit_exceeded = item_limit
        if item.kind == "sequence":
            fields["item"] = item.items
        else:
            fields["item"] = (item,)

    # Home Assistant establishes its special ``repeat`` local on entry to the
    # sequence. It shadows an outer variable with that name. A later variables
    # action may overwrite it; `_context_with_variables` models that subsequent
    # transfer path explicitly.
    variable_values = dict(base.variable_values)
    variable_values.pop("repeat", None)
    variable_entity_ids = dict(base.variable_entity_ids)
    variable_entity_ids.pop("repeat", None)
    incomplete = set(base.incomplete_variable_names)
    incomplete.discard("repeat")
    return (
        replace(
            base,
            variable_values=variable_values,
            variable_entity_ids=variable_entity_ids,
            incomplete_variable_names=tuple(sorted(incomplete)),
            repeat_values=fields,
            repeat_values_join_variable=False,
        ),
        limit_exceeded,
    )


def _context_with_variables(
    base: TemplateContextEvidence,
    variables: Any,
    *,
    path: str,
    context_value_budget: list[int],
    join_existing: bool,
) -> tuple[TemplateContextEvidence, bool]:
    """Join one bounded variables action into subsequent template context."""

    if not isinstance(variables, dict):
        return base, False
    if len(variables) > MAX_CONTEXT_VARIABLES:
        return base, True

    values = {
        name: {item.fingerprint: item for item in alternatives}
        for name, alternatives in base.variable_values.items()
    }
    candidates = {
        name: set(entity_ids)
        for name, entity_ids in base.variable_entity_ids.items()
    }
    incomplete = set(base.incomplete_variable_names)
    provenance = set(base.provenance)
    repeat_values = dict(base.repeat_values)
    repeat_values_join_variable = base.repeat_values_join_variable
    limit_exceeded = False

    for raw_name, raw_value in sorted(
        variables.items(), key=lambda pair: str(pair[0])
    ):
        name = str(raw_name)
        if len(name) > MAX_CONTEXT_SCALAR_CHARS:
            limit_exceeded = True
            continue
        evidence, evidence_limit = _bounded_context_value(
            raw_value,
            budget=context_value_budget,
        )
        limit_exceeded = bool(limit_exceeded or evidence_limit)
        if not join_existing:
            values[name] = {}
            candidates.pop(name, None)
            incomplete.discard(name)
        elif (
            name not in values
            and name not in candidates
            and name not in incomplete
        ):
            # A joined transfer always has a path on which this assignment did
            # not occur.  For root automation/script variables that path is
            # also the externally supplied run-variable override, which Home
            # Assistant deliberately preserves instead of overwriting.  Keep
            # the alternative as a tainted scalar: ordinary rendering remains
            # neutral, while later entity selection becomes opaque.
            unbound_fingerprint = hashlib.sha256(
                f"joined_runtime_variable:{name}".encode("utf-8")
            ).hexdigest()
            values[name] = {
                unbound_fingerprint: TemplateContextValueEvidence(
                    kind="dynamic_scalar",
                    complete=False,
                    fingerprint=unbound_fingerprint,
                )
            }
            incomplete.add(name)
        if name == "repeat" and repeat_values:
            if join_existing:
                repeat_values_join_variable = True
            else:
                repeat_values = {}
                repeat_values_join_variable = False
        alternatives = values.setdefault(name, {})
        if (
            evidence.fingerprint not in alternatives
            and len(alternatives) >= MAX_CONTEXT_ENTITY_IDS
        ):
            limit_exceeded = True
            incomplete.add(name)
        else:
            alternatives[evidence.fingerprint] = evidence
        if evidence.kind in {"dynamic_scalar", "opaque"}:
            incomplete.add(name)
        exact, exact_limit = _bounded_literal_entities_deep(raw_value)
        limit_exceeded = bool(limit_exceeded or exact_limit)
        if exact:
            target = candidates.setdefault(name, set())
            target.update(exact)
            if len(target) > MAX_CONTEXT_ENTITY_IDS:
                limit_exceeded = True
                candidates[name] = set(
                    sorted(target)[:MAX_CONTEXT_ENTITY_IDS]
                )
        provenance.add(f"{path}.variables.{name}")

    return (
        replace(
            base,
            variable_values={
                name: tuple(
                    item
                    for _fingerprint, item in sorted(alternatives.items())
                )
                for name, alternatives in sorted(values.items())
            },
            variable_entity_ids={
                name: tuple(sorted(entity_ids))
                for name, entity_ids in sorted(candidates.items())
            },
            incomplete_variable_names=tuple(sorted(incomplete)),
            provenance=tuple(sorted(provenance)),
            repeat_values=repeat_values,
            repeat_values_join_variable=repeat_values_join_variable,
        ),
        limit_exceeded,
    )


def _is_direct_variables_action(value: Any) -> bool:
    """Return whether *value* is one exact Home Assistant variables action."""

    if not isinstance(value, dict):
        return False
    action_keys = set(value).difference(
        {"alias", "enabled", "continue_on_error"}
    )
    return action_keys == {"variables"} and isinstance(
        value.get("variables"), dict
    )


def _join_template_contexts(
    skipped: TemplateContextEvidence,
    executed: TemplateContextEvidence,
) -> TemplateContextEvidence:
    """Join skipped/executed action paths without mixing intra-action state."""

    values: dict[str, dict[str, TemplateContextValueEvidence]] = {}
    incomplete = set(skipped.incomplete_variable_names).union(
        executed.incomplete_variable_names
    )
    names = set(skipped.variable_values).union(executed.variable_values)
    for name in sorted(names):
        alternatives: dict[str, TemplateContextValueEvidence] = {}
        for context in (skipped, executed):
            for item in context.variable_values.get(name, ()):
                alternatives[item.fingerprint] = item
        if (
            name not in skipped.variable_values
            or name not in executed.variable_values
        ):
            marker = hashlib.sha256(
                f"joined_runtime_variable:{name}".encode("utf-8")
            ).hexdigest()
            alternatives[marker] = TemplateContextValueEvidence(
                kind="dynamic_scalar",
                complete=False,
                fingerprint=marker,
            )
            incomplete.add(name)
        values[name] = alternatives

    candidates: dict[str, set[str]] = {}
    for context in (skipped, executed):
        for name, entity_ids in context.variable_entity_ids.items():
            candidates.setdefault(name, set()).update(entity_ids)

    repeat_values: dict[
        str, dict[str, TemplateContextValueEvidence]
    ] = {}
    for context in (skipped, executed):
        for name, alternatives in context.repeat_values.items():
            target = repeat_values.setdefault(name, {})
            for item in alternatives:
                target[item.fingerprint] = item
    repeat_special_present = bool(repeat_values)
    repeat_variable_present = "repeat" in values

    return replace(
        skipped,
        variable_values={
            name: tuple(
                item
                for _fingerprint, item in sorted(alternatives.items())
            )
            for name, alternatives in sorted(values.items())
        },
        variable_entity_ids={
            name: tuple(sorted(entity_ids))
            for name, entity_ids in sorted(candidates.items())
        },
        incomplete_variable_names=tuple(sorted(incomplete)),
        provenance=tuple(
            sorted(set(skipped.provenance).union(executed.provenance))
        ),
        repeat_values={
            name: tuple(
                item
                for _fingerprint, item in sorted(alternatives.items())
            )
            for name, alternatives in sorted(repeat_values.items())
        },
        repeat_values_join_variable=bool(
            skipped.repeat_values_join_variable
            or executed.repeat_values_join_variable
            or (repeat_special_present and repeat_variable_present)
        ),
    )


def _nested_variable_actions(
    value: Any,
    *,
    path: str,
    depth: int = 0,
    budget: list[int] | None = None,
    action_position: bool = True,
) -> tuple[list[tuple[str, dict[str, Any], bool]], bool]:
    """Collect bounded branch-local variable actions for post-branch joins."""

    if budget is None:
        budget = [MAX_CONFIGURATION_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_CONFIGURATION_DEPTH:
        return [], True
    found: list[tuple[str, dict[str, Any], bool]] = []
    limit_exceeded = False
    if isinstance(value, dict):
        # A variables action may include ordinary action metadata, but a
        # service-data mapping merely named "variables" is not a binding.
        if action_position and _is_direct_variables_action(value):
            enabled = value.get("enabled", True)
            if enabled is not False:
                found.append(
                    (
                        path,
                        value["variables"],
                        enabled is not True,
                    )
                )
        # Descend only through Home Assistant action containers. Arbitrary
        # service data, targets, event payloads, and notification content may
        # legitimately contain a key named ``variables`` but do not establish
        # template bindings for later actions.
        for key in sorted(
            set(value).intersection(
                ACTION_SEQUENCE_KEYS.union({"choose", "repeat", "parallel"})
            )
        ):
            item = value[key]
            child_action_position = (
                key in ACTION_SEQUENCE_KEYS or key == "parallel"
            )
            child, child_limit = _nested_variable_actions(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
                budget=budget,
                action_position=child_action_position,
            )
            found.extend(child)
            limit_exceeded = bool(limit_exceeded or child_limit)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child, child_limit = _nested_variable_actions(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
                budget=budget,
                action_position=action_position,
            )
            found.extend(child)
            limit_exceeded = bool(limit_exceeded or child_limit)
    if len(found) > MAX_CONTEXT_VARIABLES:
        return found[:MAX_CONTEXT_VARIABLES], True
    return found, limit_exceeded


def extract_document(
    *,
    source_type: str,
    source_id: str,
    config: dict[str, Any],
    source_entity_id: str | None = None,
    source_name: str | None = None,
    source_state: str | None = None,
    secret: str = "",
) -> tuple[list[DependencyFinding], list[DynamicReference]]:
    findings: list[DependencyFinding] = []
    dynamic: list[DynamicReference] = []
    blueprint = config.get("use_blueprint") if isinstance(config, dict) else None
    blueprint_path = blueprint.get("path") if isinstance(blueprint, dict) else None
    resolved_selector_count = 0

    def add(entity_id: str, relation: str, path: str, *, match_type: str = "structured_exact", blueprint_input: str | None = None, excerpt: str | None = None):
        if not valid_entity_id(entity_id):
            return
        normalized = entity_id
        eid = evidence_id(source_type, source_id, normalized, relation, path, blueprint_input)
        findings.append(
            DependencyFinding(
                evidence_id=eid,
                target_entity_id=normalized,
                source_type=source_type,
                source_id=source_id,
                source_entity_id=source_entity_id,
                source_name=_bounded(source_name, secret=secret),
                relation=relation,
                config_path=path,
                confidence="exact" if match_type != "blueprint_resolved" else "resolved",
                match_type=match_type,
                blueprint_path=_bounded(blueprint_path, 256, secret),
                blueprint_input=blueprint_input,
                source_state=source_state,
                evidence_summary=_summary(relation),
                excerpt=_bounded(excerpt, 240, secret) if excerpt else None,
            )
        )

    def add_dynamic(
        path: str,
        text: str,
        resolution: CandidateResolution,
    ):
        nonlocal resolved_selector_count
        safe_selectors = tuple(
            value
            for selector in resolution.literal_label_selectors
            if isinstance(
                (value := _bounded(selector, 128, secret)), str
            )
        )
        selector_sanitization_incomplete = bool(
            safe_selectors != resolution.literal_label_selectors
        )
        resolved_selector_count += len(
            safe_selectors
        )
        selector_limit_exceeded = (
            resolved_selector_count > MAX_DYNAMIC_LABEL_SELECTORS
        )
        safe = _bounded(text, 240, secret)
        dynamic.append(
            DynamicReference(
                evidence_id=evidence_id(source_type, source_id, "dynamic", path),
                source_type=source_type,
                source_id=source_id,
                config_path=path,
                warning="Dynamic template reference could not be resolved statically.",
                excerpt=safe,
                source_entity_id=_bounded(source_entity_id, 128, secret),
                source_name=_bounded(source_name, 160, secret),
                source_state=_bounded(source_state, 32, secret),
                possible_entity_domains=(
                    resolution.possible_entity_domains
                ),
                possible_entity_ids=resolution.entity_ids,
                literal_label_selectors=(
                    safe_selectors
                ),
                candidate_resolution_kind=(
                    "resolution_limit"
                    if selector_limit_exceeded
                    else "unresolved"
                    if selector_sanitization_incomplete
                    else resolution.kind
                ),
                candidate_resolution_complete=bool(
                    resolution.complete
                    and not selector_limit_exceeded
                    and not selector_sanitization_incomplete
                ),
                candidate_resolution_limit_exceeded=bool(
                    resolution.limit_exceeded
                    or selector_limit_exceeded
                ),
            )
        )

    def walk(value: Any, path: str, relation: str, parent_key: str = ""):
        if isinstance(value, dict):
            if source_type == "scene" and path in {"$", "$.entities"}:
                entities = value.get("entities") if path == "$" else value
                if isinstance(entities, dict):
                    for entity in entities:
                        add(str(entity), "scene_entity", f"$.entities.{entity}")
            for key, item in value.items():
                child_path = f"{path}.{key}"
                child_relation = _relation_for(path, key, relation, source_type)
                if key in ENTITY_BEARING_KEYS:
                    for entity in _literal_entities(item):
                        add(entity, child_relation, child_path)
                elif source_type == "group" and key == "entities":
                    for entity in _literal_entities(item):
                        add(entity, "group_member", child_path)
                elif path.endswith(".use_blueprint.input"):
                    if not any(term in key.lower() for term in ("secret", "token", "password", "webhook", "api_key", "url")):
                        for entity in _literal_entities_deep(item):
                            add(entity, "blueprint_input", child_path, blueprint_input=key)
                walk(item, child_path, child_relation, key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]", relation, parent_key)
        elif isinstance(value, str):
            if parent_key in FREE_TEXT_KEYS and not (
                "{{" in value or "{%" in value or "{#" in value
            ):
                return
            if _is_template(value, parent_key):
                literals, unresolved, resolution = (
                    _template_references(value)
                )
                for entity in literals:
                    add(entity, "template_literal", path, match_type="template_literal", excerpt=value)
                if unresolved:
                    add_dynamic(path, value, resolution)

    walk(config, "$", "other_structured_reference")
    return _deduplicate(findings), _deduplicate_dynamic(dynamic)


def resolve_blueprint_roles(
    findings: list[DependencyFinding],
    blueprint_config: dict[str, Any],
    *,
    source_id: str,
) -> list[DependencyFinding]:
    """Map !input markers to structural blueprint roles without exposing source."""
    roles: dict[str, set[str]] = {}

    def walk(value: Any, path: str, relation: str):
        if isinstance(value, dict):
            if set(value) == {"__blueprint_input__"}:
                roles.setdefault(str(value["__blueprint_input__"]), set()).add(relation)
                return
            for key, item in value.items():
                walk(item, f"{path}.{key}", _relation_for(path, key, relation, "blueprint"))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]", relation)

    walk(blueprint_config, "$", "other_structured_reference")
    resolved = []
    for finding in findings:
        if finding.relation != "blueprint_input" or not finding.blueprint_input:
            continue
        for role in sorted(roles.get(finding.blueprint_input, ())):
            resolved.append(
                replace(
                    finding,
                    evidence_id=evidence_id(finding.evidence_id, "resolved", role),
                    relation="blueprint_resolved_role",
                    confidence="resolved",
                    match_type="blueprint_resolved",
                    config_path=f"use_blueprint.input.{finding.blueprint_input} -> {role}",
                    evidence_summary=f"Blueprint input resolves to {role}.",
                    excerpt=None,
                )
            )
    return resolved


def _relation_for(path: str, key: str, current: str, source_type: str) -> str:
    lowered = key.lower()
    if lowered in {"trigger", "triggers"}:
        return "trigger"
    if lowered == "wait_for_trigger":
        return "wait_for_trigger"
    if lowered in {"condition", "conditions"}:
        if ".choose" in path:
            return "choose_condition"
        if ".if" in path:
            return "if_condition"
        if ".repeat" in path:
            return "repeat_condition"
        return "condition"
    if lowered in {"if"}:
        return "if_condition"
    if lowered in {"while", "until"} and ".repeat" in path:
        return "repeat_condition"
    if lowered == "target":
        return "service_target" if ".action" in path or ".sequence" in path else "action_target"
    if lowered == "data":
        return "action_data"
    if source_type == "script" and lowered in {"sequence", "action", "actions"}:
        return "script_reference"
    if lowered in {"action", "actions", "sequence", "parallel", "then", "else", "choose", "repeat"}:
        return "action_target"
    return current


def _literal_entities(value: Any) -> Iterable[str]:
    values = [value] if isinstance(value, str) else value
    if isinstance(values, list):
        for item in values:
            if isinstance(item, str) and valid_entity_id(item):
                yield item


def _literal_entities_deep(value: Any) -> Iterable[str]:
    if isinstance(value, str) and valid_entity_id(value):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _literal_entities_deep(item)
    elif isinstance(value, list):
        for item in value:
            yield from _literal_entities_deep(item)


def _bounded_literal_entities_deep(
    value: Any,
) -> tuple[tuple[str, ...], bool]:
    """Return deterministic deep entity literals with explicit bounds."""

    candidates: set[str] = set()
    work_units = 0
    limit_exceeded = False

    def visit(item: Any, depth: int) -> None:
        nonlocal work_units, limit_exceeded
        work_units += 1
        if (
            work_units > MAX_CONFIGURATION_NODES
            or depth > MAX_CONFIGURATION_DEPTH
        ):
            limit_exceeded = True
            return
        if isinstance(item, str):
            if valid_entity_id(item):
                candidates.add(item)
                if len(candidates) > MAX_CONTEXT_ENTITY_IDS:
                    limit_exceeded = True
            return
        if isinstance(item, dict):
            for nested in item.values():
                if limit_exceeded:
                    break
                visit(nested, depth + 1)
            return
        if isinstance(item, (list, tuple)):
            for nested in item:
                if limit_exceeded:
                    break
                visit(nested, depth + 1)

    visit(value, 0)
    return (
        tuple(sorted(candidates)[:MAX_CONTEXT_ENTITY_IDS]),
        limit_exceeded,
    )


def _entity_candidate_value_complete(value: Any) -> bool:
    """Return whether every bounded leaf is an exact entity candidate."""

    work_units = 0

    def visit(item: Any, depth: int) -> bool:
        nonlocal work_units
        work_units += 1
        if (
            work_units > MAX_CONFIGURATION_NODES
            or depth > MAX_CONFIGURATION_DEPTH
        ):
            return False
        if isinstance(item, str):
            return valid_entity_id(item)
        if isinstance(item, dict):
            return all(visit(nested, depth + 1) for nested in item.values())
        if isinstance(item, (list, tuple)):
            return all(visit(nested, depth + 1) for nested in item)
        return False

    return visit(value, 0)


def _contains_template_value(value: Any) -> bool:
    """Boundedly detect template-bearing surrounding configuration values."""

    work_units = 0

    def visit(item: Any, depth: int) -> bool:
        nonlocal work_units
        work_units += 1
        if (
            work_units > MAX_CONFIGURATION_NODES
            or depth > MAX_CONFIGURATION_DEPTH
        ):
            return True
        if isinstance(item, str):
            return any(marker in item for marker in ("{{", "{%", "{#"))
        if isinstance(item, dict):
            return any(visit(nested, depth + 1) for nested in item.values())
        if isinstance(item, (list, tuple)):
            return any(visit(nested, depth + 1) for nested in item)
        return False

    return visit(value, 0)


def _is_template(value: str, key: str) -> bool:
    return "{{" in value or "{%" in value or key in TEMPLATE_KEYS


def _template_references(
    value: str,
) -> tuple[list[str], bool, CandidateResolution]:
    """Extract references only from recognized Home Assistant template syntax.

    The scanner never executes Jinja and never promotes arbitrary dotted tokens.
    It examines helper calls and ``states`` lookup syntax outside quoted prose and
    template comments. Dynamic arguments are reported without inventing targets.
    """

    exact: set[str] = set()
    dynamic_resolutions: list[CandidateResolution] = []
    context = BoundedTemplateContext(valid_entity_id)
    for segment_type, segment in _template_segments(value):
        bounded = segment[:MAX_TEMPLATE_SEGMENT_CHARS]
        if segment_type == "statement":
            context.apply_statement(bounded)
        found, resolutions = _scan_template_segment(
            bounded, candidate_context=context
        )
        exact.update(found)
        dynamic_resolutions.extend(resolutions)
        if len(segment) > len(bounded):
            dynamic_resolutions.append(
                CandidateResolution(
                    complete=False,
                    limit_exceeded=True,
                    kind="resolution_limit",
                )
            )
    if not dynamic_resolutions:
        return sorted(exact), False, CandidateResolution()
    complete = all(item.complete for item in dynamic_resolutions)
    limit_exceeded = any(
        item.limit_exceeded for item in dynamic_resolutions
    )
    entity_ids = sorted(
        {
            entity_id
            for item in dynamic_resolutions
            for entity_id in item.entity_ids
        }
    )
    labels = sorted(
        {
            label
            for item in dynamic_resolutions
            for label in item.literal_label_selectors
        }
    )
    if len(entity_ids) > MAX_LITERAL_ARGUMENTS:
        entity_ids = entity_ids[:MAX_LITERAL_ARGUMENTS]
        complete = False
        limit_exceeded = True
    if len(labels) > MAX_DYNAMIC_LABEL_SELECTORS:
        labels = labels[:MAX_DYNAMIC_LABEL_SELECTORS]
        complete = False
        limit_exceeded = True
    domains: tuple[str, ...] | None
    if complete and not labels:
        domain_values: set[str] = set()
        for item in dynamic_resolutions:
            if item.possible_entity_domains is None:
                complete = False
                break
            domain_values.update(item.possible_entity_domains)
        domains = tuple(sorted(domain_values)) if complete else None
    else:
        domains = None
    kinds = {item.kind for item in dynamic_resolutions}
    if limit_exceeded:
        kind = "resolution_limit"
    elif len(kinds) == 1:
        kind = next(iter(kinds))
    elif complete:
        kind = "finite_union"
    else:
        kind = "unresolved"
    return sorted(exact), True, CandidateResolution(
        entity_ids=tuple(entity_ids),
        literal_label_selectors=tuple(labels),
        possible_entity_domains=domains,
        complete=complete,
        limit_exceeded=limit_exceeded,
        kind=kind,
    )


def _template_segments(value: str) -> list[tuple[str, str]]:
    segments: list[tuple[str, str]] = []
    saw_tag = False
    cursor = 0
    while cursor < len(value):
        positions = [
            (position, opener, closer)
            for opener, closer in (("{{", "}}"), ("{%", "%}"), ("{#", "#}"))
            if (position := value.find(opener, cursor)) >= 0
        ]
        if not positions:
            break
        start, opener, closer = min(positions, key=lambda item: item[0])
        saw_tag = True
        end = value.find(closer, start + 2)
        if end < 0:
            end = len(value)
        if opener != "{#":
            segments.append(
                (
                    "statement" if opener == "{%" else "expression",
                    value[start + 2 : end],
                )
            )
        cursor = min(len(value), end + 2)
    if not saw_tag:
        return [("expression", value)]
    return segments


def _scan_template_segment(
    value: str,
    *,
    depth: int = 0,
    candidate_context: BoundedTemplateContext | None = None,
) -> tuple[set[str], list[CandidateResolution]]:
    exact, unresolved = _scan_template_entity_operators(
        value, candidate_context=candidate_context
    )
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if not (char.isalpha() or char == "_"):
            cursor += 1
            continue
        start = cursor
        cursor += 1
        while cursor < len(value) and (value[cursor].isalnum() or value[cursor] == "_"):
            cursor += 1
        name = value[start:cursor]
        if name not in ENTITY_TEMPLATE_HELPERS:
            continue
        if start > 0 and (value[start - 1].isalnum() or value[start - 1] in {"_", "."}):
            continue
        if _is_filter_or_test_identifier(value, start):
            # Filter and test arguments are not entity operands.  Their entity
            # operand is projected by _scan_template_entity_operators().
            continue
        lookahead = cursor
        while lookahead < len(value) and value[lookahead].isspace():
            lookahead += 1

        if lookahead < len(value) and value[lookahead] == "(":
            inner, end = _extract_balanced(value, lookahead, "(", ")")
            if inner is None:
                unresolved.append(CandidateResolution())
                cursor = lookahead + 1
                continue
            arguments = _split_top_level_args(inner)
            target_arguments = arguments if name == "expand" else arguments[:1]
            if not target_arguments:
                unresolved.append(CandidateResolution())
            for argument in target_arguments:
                literals = _literal_string_arguments(argument)
                if literals is None:
                    unresolved.append(
                        _resolve_dynamic_argument(
                            argument, candidate_context
                        )
                    )
                    continue
                entities = tuple(
                    item for item in literals if valid_entity_id(item)
                )
                exact.update(entities)
                if not literals or len(entities) != len(literals):
                    unresolved.append(CandidateResolution())
            if depth < MAX_TEMPLATE_NESTING:
                nested, nested_dynamic = _scan_template_segment(
                    inner,
                    depth=depth + 1,
                    candidate_context=candidate_context,
                )
                exact.update(nested)
                unresolved.extend(nested_dynamic)
            elif any(helper in inner for helper in ENTITY_TEMPLATE_HELPERS):
                unresolved.append(
                    CandidateResolution(
                        complete=False,
                        limit_exceeded=True,
                        kind="resolution_limit",
                    )
                )
            cursor = end
            continue

        if name == "states" and lookahead < len(value) and value[lookahead] == "[":
            inner, end = _extract_balanced(value, lookahead, "[", "]")
            if inner is None:
                unresolved.append(CandidateResolution())
                cursor = lookahead + 1
                continue
            literals = _literal_string_arguments(inner)
            if literals is None:
                unresolved.append(
                    _resolve_dynamic_argument(inner, candidate_context)
                )
            else:
                entities = tuple(
                    item for item in literals if valid_entity_id(item)
                )
                exact.update(entities)
                if not literals or len(entities) != len(literals):
                    unresolved.append(CandidateResolution())
            cursor = end
            continue

        if name == "states" and value.startswith(".", lookahead):
            match = re.match(r"\.([a-z0-9_]+)\.([a-z0-9_]+)", value[lookahead:])
            if match:
                entity_id = f"{match.group(1)}.{match.group(2)}"
                if valid_entity_id(entity_id):
                    exact.add(entity_id)
                cursor = lookahead + match.end()
                continue
            domain_match = re.match(
                r"\.([a-z0-9_]+)(?![a-z0-9_.])",
                value[lookahead:],
            )
            if domain_match:
                domain = domain_match.group(1)
                if (
                    ENTITY_ID_COMPONENT.fullmatch(domain)
                    and any(character.isalpha() for character in domain)
                ):
                    unresolved.append(
                        CandidateResolution(
                            possible_entity_domains=(domain,),
                            complete=True,
                            kind="proven_domain_collection",
                        )
                    )
                else:
                    unresolved.append(CandidateResolution())
                cursor = lookahead + domain_match.end()
                continue
        if name == "states":
            # Bare ``states`` is the official all-state collection.  It can
            # select the target helper and therefore must remain explicitly
            # unresolved rather than disappearing as zero evidence.
            unresolved.append(
                CandidateResolution(kind="unrestricted_state_collection")
            )
    return exact, unresolved


def _scan_template_entity_operators(
    value: str,
    *,
    candidate_context: BoundedTemplateContext | None,
) -> tuple[set[str], list[CandidateResolution]]:
    """Project reviewed Home Assistant entity filters and tests.

    Jinja filters/tests place the entity operand before the operator, unlike
    the equivalent function forms.  This scanner recognizes only those
    reviewed operators and a bounded trailing operand atom.  Anything that
    uses one of the operators but cannot be proven exact or finite is emitted
    as unresolved evidence instead of disappearing from the index.
    """

    exact: set[str] = set()
    unresolved = _scan_template_collection_entity_operators(
        value, candidate_context=candidate_context
    )
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char == "|":
            name_start = cursor + 1
            while name_start < len(value) and value[name_start].isspace():
                name_start += 1
            name_end = name_start
            while name_end < len(value) and (
                value[name_end].isalnum() or value[name_end] == "_"
            ):
                name_end += 1
            if value[name_start:name_end] in ENTITY_TEMPLATE_FILTERS:
                _project_template_operator_operand(
                    value[:cursor],
                    exact=exact,
                    unresolved=unresolved,
                    candidate_context=candidate_context,
                )
            cursor = max(cursor + 1, name_end)
            continue
        if char.isalpha() or char == "_":
            start = cursor
            cursor += 1
            while cursor < len(value) and (
                value[cursor].isalnum() or value[cursor] == "_"
            ):
                cursor += 1
            if value[start:cursor] != "is":
                continue
            name_start = cursor
            while name_start < len(value) and value[name_start].isspace():
                name_start += 1
            name_end = name_start
            while name_end < len(value) and (
                value[name_end].isalnum() or value[name_end] == "_"
            ):
                name_end += 1
            if value[name_start:name_end] == "not":
                name_start = name_end
                while (
                    name_start < len(value)
                    and value[name_start].isspace()
                ):
                    name_start += 1
                name_end = name_start
                while name_end < len(value) and (
                    value[name_end].isalnum()
                    or value[name_end] == "_"
                ):
                    name_end += 1
            if value[name_start:name_end] in ENTITY_TEMPLATE_TESTS:
                _project_template_operator_operand(
                    value[:start],
                    exact=exact,
                    unresolved=unresolved,
                    candidate_context=candidate_context,
                )
            cursor = max(cursor, name_end)
            continue
        cursor += 1
    return exact, unresolved


def _scan_template_collection_entity_operators(
    value: str,
    *,
    candidate_context: BoundedTemplateContext | None,
    depth: int = 0,
    scan_budget: list[int] | None = None,
) -> list[CandidateResolution]:
    """Project bounded candidates used by reviewed collection operators.

    Home Assistant exposes state-aware filters and tests through Jinja's
    ``map``/``select``/``reject`` collection operators.  These operators receive
    their entity candidates from the collection to the left of the pipeline,
    not from the quoted filter/test name.  Only a finite literal/context value
    or an exact ``states.<domain>`` collection is conclusive.  Any ambiguous
    collection or operator remains explicit incomplete evidence. Parenthesized
    and container-nested expressions are inspected recursively within the same
    static bounds; templates are never rendered.
    """

    if scan_budget is None:
        scan_budget = [MAX_LITERAL_ARGUMENTS]
    contains_collection_operator = _contains_collection_operator(value)
    malformed_collection_fragment = bool(
        contains_collection_operator
        and _has_unmatched_structural_delimiter(value)
    )
    if depth > MAX_TEMPLATE_NESTING:
        return (
            [
                CandidateResolution(
                    complete=False,
                    limit_exceeded=True,
                    kind="resolution_limit",
                )
            ]
            if contains_collection_operator
            else []
        )

    conditional = _split_top_level_conditional(value)
    if conditional is not None:
        resolutions: list[CandidateResolution] = []
        for branch in conditional:
            if not _contains_collection_operator(branch):
                continue
            if scan_budget[0] <= 0:
                resolutions.append(_candidate_resolution_limit())
                break
            scan_budget[0] -= 1
            resolutions.extend(
                _scan_template_collection_entity_operators(
                    branch,
                    candidate_context=candidate_context,
                    depth=depth + 1,
                    scan_budget=scan_budget,
                )
            )
        if malformed_collection_fragment:
            if scan_budget[0] > 0:
                scan_budget[0] -= 1
            resolutions.append(_candidate_resolution_limit())
        return _bound_candidate_resolutions(resolutions)

    resolutions = _scan_top_level_collection_entity_operator(
        value, candidate_context=candidate_context
    )
    if malformed_collection_fragment:
        if scan_budget[0] > 0:
            scan_budget[0] -= 1
        resolutions.append(_candidate_resolution_limit())
        return _bound_candidate_resolutions(resolutions)
    pairs = {"(": ")", "[": "]", "{": "}"}
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        closer = pairs.get(char)
        if closer is None:
            cursor += 1
            continue
        inner, end = _extract_balanced(value, cursor, char, closer)
        if inner is None:
            if _contains_collection_operator(value[cursor:]):
                if scan_budget[0] > 0:
                    scan_budget[0] -= 1
                resolutions.append(_candidate_resolution_limit())
            # The remaining fragment is malformed.  Do not retry every later
            # opener against the same suffix: that turns one bounded scan into
            # quadratic work.  One explicit limit result conservatively binds
            # the whole malformed remainder.
            break
        if scan_budget[0] <= 0 or depth >= MAX_TEMPLATE_NESTING:
            if _contains_collection_operator(inner):
                resolutions.append(_candidate_resolution_limit())
            cursor = end
            continue
        scan_budget[0] -= 1
        resolutions.extend(
            _scan_template_collection_entity_operators(
                inner,
                candidate_context=candidate_context,
                depth=depth + 1,
                scan_budget=scan_budget,
            )
        )
        cursor = end
    return _bound_candidate_resolutions(resolutions)


def _candidate_resolution_limit() -> CandidateResolution:
    return CandidateResolution(
        complete=False,
        limit_exceeded=True,
        kind="resolution_limit",
    )


def _bound_candidate_resolutions(
    resolutions: list[CandidateResolution],
) -> list[CandidateResolution]:
    if len(resolutions) <= MAX_LITERAL_ARGUMENTS:
        return resolutions
    return [
        *resolutions[: MAX_LITERAL_ARGUMENTS - 1],
        _candidate_resolution_limit(),
    ]


def _scan_top_level_collection_entity_operator(
    value: str,
    *,
    candidate_context: BoundedTemplateContext | None,
) -> list[CandidateResolution]:
    """Project the first reviewed operator in one outermost pipeline."""

    parts = _split_top_level_pipeline(value)
    if len(parts) < 2:
        return []
    base_expression = parts[0]
    prior_stages: list[str] = []
    for stage in parts[1:]:
        parsed = _parse_pipeline_stage(stage)
        if parsed is None:
            prior_stages.append(stage)
            continue
        filter_name, arguments = parsed
        operator_name: str | None = None
        reviewed = False
        operator_unresolved = False
        if filter_name in ENTITY_COLLECTION_TEST_FILTERS:
            if arguments is None:
                operator_unresolved = True
            elif arguments:
                operator_name = _literal_operator_name(arguments[0])
                reviewed = operator_name in ENTITY_TEMPLATE_TESTS
                operator_unresolved = operator_name is None
        elif filter_name in ENTITY_COLLECTION_ATTRIBUTE_TEST_FILTERS:
            if arguments is None:
                operator_unresolved = True
            elif len(arguments) >= 2:
                attribute_name = _literal_operator_name(arguments[0])
                operator_name = _literal_operator_name(arguments[1])
                reviewed = bool(
                    attribute_name == "entity_id"
                    and operator_name in ENTITY_TEMPLATE_TESTS
                )
                operator_unresolved = bool(
                    operator_name is None
                    or (
                        operator_name in ENTITY_TEMPLATE_TESTS
                        and attribute_name != "entity_id"
                    )
                )
        elif filter_name == ENTITY_COLLECTION_MAP_FILTER:
            if arguments is None:
                operator_unresolved = True
            elif arguments:
                operator_name = _literal_operator_name(arguments[0])
                reviewed = operator_name in ENTITY_TEMPLATE_FILTERS
                operator_unresolved = bool(
                    operator_name is None
                    and not re.match(
                        r"\s*attribute\s*=", arguments[0]
                    )
                )

        if reviewed or operator_unresolved:
            if not _pipeline_preserves_collection_candidates(prior_stages):
                resolution = CandidateResolution()
            elif operator_unresolved:
                resolution = CandidateResolution()
            else:
                resolution = _resolve_collection_candidate_expression(
                    base_expression, candidate_context
                )
            kind_operator = operator_name or "unresolved_operator"
            return [
                replace(
                    resolution,
                    kind=(
                        f"collection_{filter_name}_{kind_operator}_"
                        f"{resolution.kind}"
                    ),
                )
            ]
        prior_stages.append(stage)
    return []


def _contains_collection_operator(value: str) -> bool:
    """Return whether a bounded fragment contains a reviewed collection stage."""

    cursor = 0
    names = (
        ENTITY_COLLECTION_TEST_FILTERS
        | ENTITY_COLLECTION_ATTRIBUTE_TEST_FILTERS
        | {ENTITY_COLLECTION_MAP_FILTER}
    )
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char != "|":
            cursor += 1
            continue
        start = cursor + 1
        while start < len(value) and value[start].isspace():
            start += 1
        end = start
        while end < len(value) and (
            value[end].isalnum() or value[end] == "_"
        ):
            end += 1
        if value[start:end] in names:
            return True
        cursor = max(cursor + 1, end)
    return False


def _has_unmatched_structural_delimiter(value: str) -> bool:
    """Return whether one static fragment has mismatched grouping syntax."""

    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    stack: list[str] = []
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char in pairs:
            stack.append(pairs[char])
        elif char in closers:
            if not stack or stack[-1] != char:
                return True
            stack.pop()
        cursor += 1
    return bool(stack)


def _split_top_level_pipeline(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char == "|" and not stack:
            parts.append(value[start:cursor].strip())
            start = cursor + 1
        cursor += 1
    parts.append(value[start:].strip())
    return parts


def _split_top_level_conditional(
    value: str,
) -> tuple[str, str, str] | None:
    """Split one bounded Jinja inline conditional without evaluating it."""

    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    if_start: int | None = None
    if_end: int | None = None
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char in pairs:
            stack.append(pairs[char])
            cursor += 1
            continue
        if stack and char == stack[-1]:
            stack.pop()
            cursor += 1
            continue
        if stack:
            cursor += 1
            continue
        if char.isalpha() or char == "_":
            start = cursor
            cursor += 1
            while cursor < len(value) and (
                value[cursor].isalnum() or value[cursor] == "_"
            ):
                cursor += 1
            token = value[start:cursor]
            if token == "if" and if_start is None:
                if_start = start
                if_end = cursor
            elif token == "else" and if_start is not None:
                true_branch = value[:if_start].strip()
                condition = value[if_end:start].strip()
                false_branch = value[cursor:].strip()
                if true_branch and condition and false_branch:
                    return true_branch, condition, false_branch
                return None
            continue
        cursor += 1
    return None


def _parse_pipeline_stage(
    stage: str,
) -> tuple[str, list[str] | None] | None:
    match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", stage)
    if match is None:
        return None
    name = match.group(1)
    cursor = match.end()
    while cursor < len(stage) and stage[cursor].isspace():
        cursor += 1
    if cursor == len(stage):
        return name, []
    if stage[cursor] != "(":
        return name, None
    inner, end = _extract_balanced(stage, cursor, "(", ")")
    if inner is None or stage[end:].strip():
        return name, None
    return name, _split_top_level_args(inner)


def _literal_operator_name(argument: str) -> str | None:
    if len(argument) > MAX_TEMPLATE_ARGUMENT_CHARS:
        return None
    try:
        parsed = ast.literal_eval(argument.strip())
    except (RecursionError, SyntaxError, ValueError):
        return None
    if not isinstance(parsed, str):
        return None
    return parsed


def _pipeline_preserves_collection_candidates(
    stages: list[str],
) -> bool:
    for stage in stages:
        parsed = _parse_pipeline_stage(stage)
        if parsed is None:
            return False
        name, arguments = parsed
        if name == ENTITY_COLLECTION_MAP_FILTER:
            if _map_stage_preserves_entity_candidates(arguments):
                continue
            return False
        if name not in COLLECTION_CANDIDATE_PRESERVING_FILTERS:
            return False
        if arguments is None:
            return False
    return True


def _map_stage_preserves_entity_candidates(
    arguments: list[str] | None,
) -> bool:
    if arguments is None or len(arguments) != 1:
        return False
    return bool(
        re.fullmatch(
            r"\s*attribute\s*=\s*(['\"])entity_id\1\s*",
            arguments[0],
        )
    )


def _resolve_collection_candidate_expression(
    expression: str,
    candidate_context: BoundedTemplateContext | None,
    *,
    depth: int = 0,
) -> CandidateResolution:
    if depth > MAX_TEMPLATE_NESTING:
        return CandidateResolution(
            complete=False,
            limit_exceeded=True,
            kind="resolution_limit",
        )
    bounded = expression.strip()
    if not bounded:
        return CandidateResolution()
    if len(bounded) > MAX_TEMPLATE_ARGUMENT_CHARS:
        return CandidateResolution(
            complete=False,
            limit_exceeded=True,
            kind="resolution_limit",
        )
    if bounded.startswith("("):
        inner, end = _extract_balanced(bounded, 0, "(", ")")
        if inner is not None and end == len(bounded):
            return _resolve_collection_candidate_expression(
                inner,
                candidate_context,
                depth=depth + 1,
            )
    conditional = _split_top_level_conditional(bounded)
    if conditional is not None:
        true_branch, _condition, false_branch = conditional
        return _merge_collection_candidate_resolutions(
            (
                _resolve_collection_candidate_expression(
                    true_branch,
                    candidate_context,
                    depth=depth + 1,
                ),
                _resolve_collection_candidate_expression(
                    false_branch,
                    candidate_context,
                    depth=depth + 1,
                ),
            )
        )
    pipeline = _split_top_level_pipeline(bounded)
    if len(pipeline) > 1:
        if not _pipeline_preserves_collection_candidates(pipeline[1:]):
            return CandidateResolution()
        return _resolve_collection_candidate_expression(
            pipeline[0],
            candidate_context,
            depth=depth + 1,
        )
    literals = _literal_string_arguments(bounded)
    if literals is not None:
        entity_ids = tuple(
            sorted({item for item in literals if valid_entity_id(item)})
        )
        complete = len(entity_ids) == len(literals)
        return CandidateResolution(
            entity_ids=entity_ids,
            possible_entity_domains=(
                tuple(
                    sorted(
                        {item.split(".", 1)[0] for item in entity_ids}
                    )
                )
                if complete
                else None
            ),
            complete=complete,
            kind=(
                "finite_collection_candidates"
                if complete
                else "unresolved"
            ),
        )
    domain_match = re.fullmatch(r"states\.([a-z0-9_]+)", bounded)
    if domain_match is not None:
        domain = domain_match.group(1)
        if ENTITY_ID_COMPONENT.fullmatch(domain) and any(
            character.isalpha() for character in domain
        ):
            return CandidateResolution(
                possible_entity_domains=(domain,),
                complete=True,
                kind="proven_domain_collection",
            )
        return CandidateResolution()
    return _resolve_dynamic_argument(bounded, candidate_context)


def _merge_collection_candidate_resolutions(
    resolutions: tuple[CandidateResolution, ...],
) -> CandidateResolution:
    entity_ids = tuple(
        sorted(
            {
                entity_id
                for resolution in resolutions
                for entity_id in resolution.entity_ids
            }
        )
    )
    labels = tuple(
        sorted(
            {
                label
                for resolution in resolutions
                for label in resolution.literal_label_selectors
            }
        )
    )
    limit_exceeded = any(
        resolution.limit_exceeded for resolution in resolutions
    )
    complete = all(resolution.complete for resolution in resolutions)
    if (
        len(entity_ids) > MAX_LITERAL_ARGUMENTS
        or len(labels) > MAX_DYNAMIC_LABEL_SELECTORS
    ):
        limit_exceeded = True
        complete = False
    entity_ids = entity_ids[:MAX_LITERAL_ARGUMENTS]
    labels = labels[:MAX_DYNAMIC_LABEL_SELECTORS]
    domains: tuple[str, ...] | None = None
    if complete and not labels:
        domain_values: set[str] = set()
        for resolution in resolutions:
            if resolution.possible_entity_domains is None:
                complete = False
                break
            domain_values.update(resolution.possible_entity_domains)
        if complete:
            domains = tuple(sorted(domain_values))
    return CandidateResolution(
        entity_ids=entity_ids,
        literal_label_selectors=labels,
        possible_entity_domains=domains,
        complete=complete,
        limit_exceeded=limit_exceeded,
        kind=(
            "resolution_limit"
            if limit_exceeded
            else (
                "finite_conditional_collection"
                if complete
                else "unresolved"
            )
        ),
    )


def _project_template_operator_operand(
    prefix: str,
    *,
    exact: set[str],
    unresolved: list[CandidateResolution],
    candidate_context: BoundedTemplateContext | None,
) -> None:
    operand = _trailing_template_operand(prefix)
    if operand is None:
        unresolved.append(CandidateResolution())
        return
    literals = _literal_string_arguments(operand)
    if literals is not None:
        entities = tuple(
            item for item in literals if valid_entity_id(item)
        )
        if entities and len(entities) == len(literals):
            exact.update(entities)
            return
        unresolved.append(CandidateResolution())
        return
    unresolved.append(
        _resolve_dynamic_argument(operand, candidate_context)
    )


def _trailing_template_operand(value: str) -> str | None:
    """Return one bounded trailing quoted literal or variable expression."""

    bounded = value[-MAX_TEMPLATE_ARGUMENT_CHARS:].rstrip()
    if not bounded:
        return None
    if bounded[-1] in {"'", '"'}:
        quote = bounded[-1]
        cursor = len(bounded) - 2
        while cursor >= 0:
            if bounded[cursor] == quote:
                escapes = 0
                previous = cursor - 1
                while previous >= 0 and bounded[previous] == "\\":
                    escapes += 1
                    previous -= 1
                if escapes % 2 == 0:
                    return bounded[cursor:]
            cursor -= 1
        return None
    match = re.search(
        r"(?P<operand>[A-Za-z_][A-Za-z0-9_]*"
        r"(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[^\[\]]+\])*)\s*$",
        bounded,
    )
    if match is None:
        return None
    return match.group("operand")


def _is_filter_or_test_identifier(value: str, start: int) -> bool:
    prefix = value[:start].rstrip()
    if prefix.endswith("|"):
        return True
    return bool(re.search(r"\bis(?:\s+not)?\s*$", prefix))


def _resolve_dynamic_argument(
    argument: str,
    candidate_context: BoundedTemplateContext | None,
) -> CandidateResolution:
    if candidate_context is not None:
        resolved = candidate_context.resolve(argument)
        if resolved.complete or resolved.limit_exceeded:
            return resolved
    domains = _constrained_dynamic_entity_domains(argument)
    if domains is not None:
        return CandidateResolution(
            possible_entity_domains=tuple(sorted(domains)),
            complete=True,
            kind="proven_domain",
        )
    return CandidateResolution()


def _constrained_dynamic_entity_domains(
    argument: str,
) -> frozenset[str] | None:
    """Recognize one complete fixed-domain plus simple-name expression."""

    match = re.fullmatch(
        r"\s*(?P<quote>['\"])(?P<domain>[a-z0-9_]+)\."
        r"(?P=quote)\s*~\s*"
        r"(?P<suffix>[A-Za-z_][A-Za-z0-9_]*)\s*",
        argument,
    )
    if match is None:
        return None
    domain = match.group("domain")
    if not ENTITY_ID_COMPONENT.fullmatch(domain) or not any(
        character.isalpha() for character in domain
    ):
        return None
    return frozenset({domain})


def _extract_balanced(
    value: str, start: int, opener: str, closer: str
) -> tuple[str | None, int]:
    depth = 0
    cursor = start
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return value[start + 1 : cursor], cursor + 1
        cursor += 1
    return None, start + 1


def _skip_quoted(value: str, start: int) -> int:
    quote = value[start]
    cursor = start + 1
    while cursor < len(value):
        if value[cursor] == "\\":
            cursor += 2
            continue
        if value[cursor] == quote:
            return cursor + 1
        cursor += 1
    return len(value)


def _split_top_level_args(value: str) -> list[str]:
    arguments: list[str] = []
    start = 0
    depth = 0
    cursor = 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char in pairs:
            depth += 1
        elif char in closers:
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            arguments.append(value[start:cursor].strip())
            start = cursor + 1
        cursor += 1
    final = value[start:].strip()
    if final:
        arguments.append(final)
    return arguments


def _literal_string_arguments(value: str) -> tuple[str, ...] | None:
    if len(value) > MAX_TEMPLATE_ARGUMENT_CHARS:
        return None
    try:
        parsed = ast.literal_eval(value.strip())
    except (RecursionError, SyntaxError, ValueError):
        return None
    if isinstance(parsed, str):
        return (parsed,)
    if (
        isinstance(parsed, (list, tuple))
        and len(parsed) <= MAX_LITERAL_ARGUMENTS
        and all(isinstance(item, str) for item in parsed)
    ):
        return tuple(parsed)
    return None


def _summary(relation: str) -> str:
    return {
        "trigger": "Entity is used by a trigger.",
        "condition": "Entity is used by a condition.",
        "action_target": "Entity is used by an action.",
        "service_target": "Entity is targeted by a service action.",
        "action_data": "Entity is supplied in action data.",
        "template_literal": "Entity is referenced literally by a behavioral template.",
        "blueprint_input": "Entity is supplied to a blueprint input.",
        "group_member": "Entity is an explicit group member.",
    }.get(relation, f"Entity has a {relation} reference.")


def _bounded(value: Any, limit: int = 160, secret: str = "") -> str | None:
    if value is None:
        return None
    safe = redact_data(str(value), secret=secret, max_string=limit)
    text = str(safe)
    for marker in ("authorization:", "bearer ", "/mcp"):
        if marker in text.lower():
            return "<redacted>"
    return text


def _deduplicate(findings: list[DependencyFinding]) -> list[DependencyFinding]:
    return sorted({item.evidence_id: item for item in findings}.values(), key=lambda item: item.evidence_id)


def _deduplicate_dynamic(items: list[DynamicReference]) -> list[DynamicReference]:
    return sorted({item.evidence_id: item for item in items}.values(), key=lambda item: item.evidence_id)
