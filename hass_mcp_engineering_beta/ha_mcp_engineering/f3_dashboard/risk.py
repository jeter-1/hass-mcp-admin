"""Source-grounded, non-executing dashboard action-risk analysis."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .constants import (
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_RISK_EVIDENCE_FINDINGS,
    MAX_RISK_FINDINGS,
    RISK_MODEL,
)
from .errors import (
    MAX_STRUCTURAL_CARD_PATH_SEGMENTS,
    STRUCTURAL_CARD_CONTAINERS,
    STRUCTURAL_CARD_WRAPPERS,
    RiskAnalysisError,
)
from .json_codec import engineering_sha256, validate_json_value
from .models import (
    DashboardRiskEvidence,
    RiskCategory,
    RiskDisposition,
    RiskFinding,
)


_ACTION_KEYS = frozenset({"tap_action", "hold_action", "double_tap_action"})
_HIGH_ENTITY_DOMAINS = frozenset(
    {"lock", "cover", "alarm_control_panel", "garage_door", "valve"}
)
_HIGH_CONSEQUENCE_SERVICES = frozenset(
    {
        "lock.lock",
        "lock.unlock",
        "lock.open",
        "cover.open_cover",
        "cover.close_cover",
        "cover.stop_cover",
        "alarm_control_panel.alarm_arm_home",
        "alarm_control_panel.alarm_arm_away",
        "alarm_control_panel.alarm_arm_night",
        "alarm_control_panel.alarm_disarm",
        "valve.open_valve",
        "valve.close_valve",
    }
)
_DESTRUCTIVE_ADMIN_SERVICES = frozenset(
    {
        "homeassistant.restart",
        "homeassistant.stop",
        "homeassistant.reload_all",
        "lovelace.reload_resources",
        "script.reload",
        "automation.reload",
    }
)
_TEMPLATE_MARKERS = ("{{", "{%", "${")
# HAMCP-338 (owner direction 2026-11-08): an entities-card row without its
# own row ``type`` renders a domain control.  Rows for these domains are
# classified exactly like a toggle of the same entity on a tile, through
# ``_domain_control_category``: lock, cover and alarm_control_panel are
# high-consequence, light and switch are toggles.  Other domains, typed rows
# (section, divider, weblink, buttons, custom and so on) and non-entity rows
# are not classified here.
_ENTITY_ROW_CONTROL_DOMAINS = frozenset(
    {"lock", "cover", "alarm_control_panel", "light", "switch"}
)
_STRUCTURAL_INDEX_MAX_DIGITS = 5
_RISK_STAGE = "risk_analysis"


def _encode(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _pointer(tokens: tuple[str, ...]) -> str:
    return "/" + "/".join(_encode(token) for token in tokens)


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    if pointer in {"", "/"}:
        return ()
    return tuple(
        token.replace("~1", "/").replace("~0", "~")
        for token in pointer[1:].split("/")
    )


def _structural_index(token: str) -> bool:
    return (
        0 < len(token) <= _STRUCTURAL_INDEX_MAX_DIGITS
        and all("0" <= character <= "9" for character in token)
    )


def _card_path(tokens: tuple[str, ...]) -> str:
    """Return the structural location of ``tokens`` without any data value.

    Only fixed container tokens followed by an ASCII list index and fixed
    single-card wrapper tokens are kept; the walk stops at the first other
    token, so mapping keys, entity IDs and names are never included.
    """

    segments: list[str] = []
    index = 0
    while index < len(tokens) and len(segments) < MAX_STRUCTURAL_CARD_PATH_SEGMENTS:
        token = tokens[index]
        if (
            token in STRUCTURAL_CARD_CONTAINERS
            and index + 1 < len(tokens)
            and _structural_index(tokens[index + 1])
        ):
            segments.append(f"/{token}/{tokens[index + 1]}")
            index += 2
        elif token in STRUCTURAL_CARD_WRAPPERS:
            segments.append(f"/{token}")
            index += 1
        else:
            break
    return "".join(segments) or "/"


def _domain_control_category(domain: str | None) -> RiskCategory:
    """Classify a direct domain control the way a tile toggle is classified."""

    return (
        RiskCategory.HIGH_CONSEQUENCE
        if domain in _HIGH_ENTITY_DOMAINS
        else RiskCategory.TOGGLE
    )


def _entity_domain(value: Any) -> str | None:
    if not isinstance(value, str) or "." not in value:
        return None
    domain, _ = value.split(".", 1)
    return domain if domain else None


def _contains_template(value: Any) -> bool:
    if isinstance(value, str):
        return any(marker in value for marker in _TEMPLATE_MARKERS)
    if isinstance(value, list):
        return any(_contains_template(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_template(item) for item in value.values())
    return False


def _semantic_binding(
    action: Any,
    *,
    card_type: str | None,
    card_entity: Any,
    custom_card: bool,
    conditional: bool,
    custom_card_configuration: Any,
    conditional_configuration: Any,
) -> str:
    action_mapping = action if isinstance(action, dict) else {}

    def field(name: str) -> dict[str, Any]:
        return {
            "present": name in action_mapping,
            "value": action_mapping.get(name),
        }

    effective_entity = (
        action_mapping["entity"]
        if "entity" in action_mapping
        else card_entity
    )
    projection = {
        "model": "f3-dashboard-action-semantic-binding-v1",
        "data_role": "untrusted_data",
        "complete_action": action,
        "card_type": card_type,
        "effective_card_entity": card_entity,
        "action_entity": field("entity"),
        "effective_entity": effective_entity,
        "target": field("target"),
        "service_data": field("service_data"),
        "data": field("data"),
        "payload": field("payload"),
        "navigation_path": field("navigation_path"),
        "url_path": field("url_path"),
        "url": field("url"),
        "confirmation": field("confirmation"),
        "custom_card_semantics_inherited": custom_card,
        "conditional_semantics_inherited": conditional,
        "custom_card_configuration": (
            custom_card_configuration if custom_card else None
        ),
        "conditional_configuration": (
            conditional_configuration if conditional else None
        ),
    }
    return engineering_sha256(projection)


def _action_findings(
    action: Any,
    *,
    path: str,
    card_entity: Any,
    card_type: str | None,
    custom_card: bool,
    conditional: bool,
    custom_card_configuration: Any,
    conditional_configuration: Any,
) -> list[RiskFinding]:
    binding = _semantic_binding(
        action,
        card_type=card_type,
        card_entity=card_entity,
        custom_card=custom_card,
        conditional=conditional,
        custom_card_configuration=custom_card_configuration,
        conditional_configuration=conditional_configuration,
    )
    if not isinstance(action, dict):
        return [
            RiskFinding(
                RiskCategory.UNKNOWN,
                path,
                None,
                None,
                _entity_domain(card_entity),
                False,
                binding,
                False,
                "action_shape_not_object",
            )
        ]
    action_name = action.get("action")
    confirmation = isinstance(action.get("confirmation"), dict)
    service = action.get("perform_action") or action.get("service")
    service = service if isinstance(service, str) else None
    entity = action.get("entity", card_entity)
    domain = _entity_domain(entity)
    findings: list[RiskFinding] = []

    if custom_card:
        findings.append(
            RiskFinding(
                RiskCategory.OPAQUE_CUSTOM,
                path,
                action_name if isinstance(action_name, str) else None,
                service,
                domain,
                confirmation,
                binding,
                False,
                "custom_card_may_reinterpret_action_schema",
            )
        )
    if conditional or _contains_template(action):
        findings.append(
            RiskFinding(
                RiskCategory.TEMPLATE_OR_CONDITIONAL,
                path,
                action_name if isinstance(action_name, str) else None,
                service,
                domain,
                confirmation,
                binding,
                False,
                "action_contains_conditional_or_template_data",
            )
        )
    if confirmation:
        findings.append(
            RiskFinding(
                RiskCategory.CONFIRMATION,
                path,
                action_name if isinstance(action_name, str) else None,
                service,
                domain,
                True,
                binding,
                False,
                "frontend_confirmation_present_but_not_authority",
            )
        )

    if action_name in {None, "none"}:
        category = RiskCategory.UNKNOWN if action_name is None else RiskCategory.DISPLAY_ONLY
        reason = "action_type_missing" if action_name is None else "explicit_no_action"
    elif action_name == "more-info":
        category = RiskCategory.MORE_INFO
        reason = "frontend_more_info_action"
    elif action_name in {"navigate", "url"}:
        category = RiskCategory.NAVIGATION
        reason = "frontend_navigation_action"
    elif action_name == "toggle":
        category = _domain_control_category(domain)
        reason = (
            "toggle_targets_high_consequence_domain"
            if category is RiskCategory.HIGH_CONSEQUENCE
            else "frontend_toggle_action"
        )
    elif action_name in {"call-service", "perform-action"}:
        if service in _DESTRUCTIVE_ADMIN_SERVICES or (
            service is not None and service.startswith("hassio.")
        ):
            category = RiskCategory.DESTRUCTIVE_ADMIN
            reason = "service_is_destructive_administrative_action"
        elif service in _HIGH_CONSEQUENCE_SERVICES:
            category = RiskCategory.HIGH_CONSEQUENCE
            reason = "service_is_high_consequence_action"
        else:
            category = RiskCategory.SERVICE_ACTION
            reason = "frontend_service_action"
    elif action_name == "fire-dom-event":
        category = RiskCategory.OPAQUE_CUSTOM
        reason = "frontend_custom_dom_event_action"
    elif action_name == "assist":
        category = RiskCategory.UNKNOWN
        reason = "assist_action_effect_not_bounded_by_dashboard_schema"
    else:
        category = RiskCategory.UNKNOWN
        reason = "unknown_frontend_action_type"

    findings.append(
        RiskFinding(
            category,
            path,
            action_name if isinstance(action_name, str) else None,
            service,
            domain,
            confirmation,
            binding,
            False,
            reason,
        )
    )
    return findings


def _entity_row_findings(
    card: dict[str, Any],
    *,
    tokens: tuple[str, ...],
    custom_card: bool,
    conditional: bool,
    custom_card_configuration: Any,
    conditional_configuration: Any,
) -> tuple[list[RiskFinding], set[tuple[str, ...]]]:
    """Classify the implicit control of each default entities-card row."""

    rows = card.get("entities")
    if card.get("type") != "entities" or not isinstance(rows, list):
        return [], set()
    findings: list[RiskFinding] = []
    classified_rows: set[tuple[str, ...]] = set()
    for index, row in enumerate(rows):
        if isinstance(row, str):
            entity: Any = row
        elif isinstance(row, dict) and "type" not in row:
            entity = row.get("entity")
        else:
            continue
        domain = _entity_domain(entity)
        if domain not in _ENTITY_ROW_CONTROL_DOMAINS:
            continue
        row_tokens = (*tokens, "entities", str(index))
        category = _domain_control_category(domain)
        binding = engineering_sha256(
            {
                "model": "f3-dashboard-entity-row-semantic-binding-v1",
                "data_role": "untrusted_data",
                "card_type": "entities",
                "complete_row": row,
                "row_entity": entity,
                "custom_card_semantics_inherited": custom_card,
                "conditional_semantics_inherited": conditional,
                "custom_card_configuration": (
                    custom_card_configuration if custom_card else None
                ),
                "conditional_configuration": (
                    conditional_configuration if conditional else None
                ),
            }
        )
        findings.append(
            RiskFinding(
                category,
                _pointer(row_tokens),
                None,
                None,
                domain,
                False,
                binding,
                False,
                (
                    "entity_row_control_targets_high_consequence_domain"
                    if category is RiskCategory.HIGH_CONSEQUENCE
                    else "entity_row_toggle_control"
                ),
            )
        )
        if isinstance(row, dict):
            classified_rows.add(row_tokens)
    return findings, classified_rows


def _scan(configuration: dict[str, Any]) -> list[RiskFinding]:
    validate_json_value(configuration)
    findings: list[RiskFinding] = []
    stack: list[
        tuple[
            Any,
            tuple[str, ...],
            bool,
            bool,
            str | None,
            Any,
            Any,
            Any,
        ]
    ] = [
        (configuration, (), False, False, None, None, None, None)
    ]
    visited = 0
    # Object rows whose implicit control is already classified are not also
    # reported as display-only entities.
    classified_rows: set[tuple[str, ...]] = set()
    while stack:
        (
            value,
            tokens,
            inherited_custom,
            inherited_conditional,
            inherited_card_type,
            inherited_card_entity,
            inherited_custom_configuration,
            inherited_conditional_configuration,
        ) = stack.pop()
        visited += 1
        if visited > MAX_JSON_NODES:
            raise RiskAnalysisError(
                "Dashboard risk traversal exceeds its reviewed bound",
                constraint="risk_traversal_nodes",
                stage=_RISK_STAGE,
                limit=MAX_JSON_NODES,
                card_path=_card_path(tokens),
            )
        if len(tokens) > MAX_JSON_DEPTH:
            raise RiskAnalysisError(
                "Dashboard risk traversal exceeds its reviewed bound",
                constraint="risk_traversal_depth",
                stage=_RISK_STAGE,
                limit=MAX_JSON_DEPTH,
                card_path=_card_path(tokens),
            )
        if isinstance(value, list):
            for index in range(len(value) - 1, -1, -1):
                stack.append(
                    (
                        value[index],
                        (*tokens, str(index)),
                        inherited_custom,
                        inherited_conditional,
                        inherited_card_type,
                        inherited_card_entity,
                        inherited_custom_configuration,
                        inherited_conditional_configuration,
                    )
                )
            continue
        if not isinstance(value, dict):
            continue

        declared_card_type = value.get("type")
        card_type = (
            declared_card_type
            if isinstance(declared_card_type, str)
            else inherited_card_type
        )
        custom = inherited_custom or (
            isinstance(declared_card_type, str)
            and declared_card_type.startswith("custom:")
        )
        conditional = (
            inherited_conditional or declared_card_type == "conditional"
        )
        card_entity = (
            value["entity"] if "entity" in value else inherited_card_entity
        )
        custom_configuration = inherited_custom_configuration
        if custom and not inherited_custom:
            custom_configuration = value
        conditional_configuration = inherited_conditional_configuration
        if conditional and not inherited_conditional:
            conditional_configuration = value
        action_keys = [key for key in _ACTION_KEYS if key in value]
        for key in sorted(action_keys):
            findings.extend(
                _action_findings(
                    value[key],
                    path=_pointer((*tokens, key)),
                    card_entity=card_entity,
                    card_type=card_type,
                    custom_card=custom,
                    conditional=conditional,
                    custom_card_configuration=custom_configuration,
                    conditional_configuration=conditional_configuration,
                )
            )

        row_findings, row_tokens = _entity_row_findings(
            value,
            tokens=tokens,
            custom_card=custom,
            conditional=conditional,
            custom_card_configuration=custom_configuration,
            conditional_configuration=conditional_configuration,
        )
        findings.extend(row_findings)
        classified_rows.update(row_tokens)

        domain = _entity_domain(card_entity)
        context_binding: str | None = None
        if domain in _HIGH_ENTITY_DOMAINS and (
            (not action_keys and "features" not in value)
            or "features" in value
        ):
            context_binding = engineering_sha256(
                {
                    "model": "f3-dashboard-card-semantic-binding-v1",
                    "data_role": "untrusted_data",
                    "card_type": card_type,
                    "card_entity": card_entity,
                    "features": value.get("features"),
                    "custom_card_semantics_inherited": custom,
                    "conditional_semantics_inherited": conditional,
                    "custom_card_configuration": custom_configuration,
                    "conditional_configuration": conditional_configuration,
                }
            )
        if (
            domain in _HIGH_ENTITY_DOMAINS
            and not action_keys
            and "features" not in value
            and tokens not in classified_rows
        ):
            if context_binding is None:
                raise RiskAnalysisError(
                    "Dashboard risk binding was not calculated",
                    constraint="risk_semantic_binding",
                    stage=_RISK_STAGE,
                    card_path=_card_path(tokens),
                )
            findings.append(
                RiskFinding(
                    RiskCategory.DISPLAY_ONLY,
                    _pointer((*tokens, "entity")),
                    None,
                    None,
                    domain,
                    False,
                    context_binding,
                    False,
                    "high_consequence_entity_is_display_only",
                )
            )
        if "features" in value and domain in _HIGH_ENTITY_DOMAINS:
            if context_binding is None:
                raise RiskAnalysisError(
                    "Dashboard risk binding was not calculated",
                    constraint="risk_semantic_binding",
                    stage=_RISK_STAGE,
                    card_path=_card_path(tokens),
                )
            findings.append(
                RiskFinding(
                    RiskCategory.UNKNOWN,
                    _pointer((*tokens, "features")),
                    None,
                    None,
                    domain,
                    False,
                    context_binding,
                    False,
                    "interactive_feature_effect_requires_manual_review",
                )
            )

        for key, item in reversed(tuple(value.items())):
            stack.append(
                (
                    item,
                    (*tokens, key),
                    custom,
                    conditional,
                    card_type,
                    card_entity,
                    custom_configuration,
                    conditional_configuration,
                )
            )
    if len(findings) > MAX_RISK_EVIDENCE_FINDINGS:
        raise RiskAnalysisError(
            "Dashboard risk findings exceed the evidence bound",
            constraint="risk_evidence_findings",
            stage=_RISK_STAGE,
            observed=len(findings),
            limit=MAX_RISK_EVIDENCE_FINDINGS,
            card_path=_card_path(
                _pointer_tokens(findings[MAX_RISK_EVIDENCE_FINDINGS].path)
            ),
        )
    return findings


def _identity(finding: RiskFinding) -> tuple[Any, ...]:
    return (
        finding.category.value,
        finding.path,
        finding.action,
        finding.service,
        finding.entity_domain,
        finding.confirmation_present,
        finding.semantic_binding_sha256,
        finding.reason_code,
    )


def _projection(finding: RiskFinding) -> dict[str, Any]:
    return {
        "category": finding.category.value,
        "path": finding.path,
        "action": finding.action,
        "service": finding.service,
        "entity_domain": finding.entity_domain,
        "confirmation_present": finding.confirmation_present,
        "semantic_binding_sha256": finding.semantic_binding_sha256,
        "introduced_or_changed": finding.introduced_or_changed,
        "reason_code": finding.reason_code,
    }


def analyze_dashboard_risk(
    current: dict[str, Any], proposed: dict[str, Any]
) -> DashboardRiskEvidence:
    """Analyze configuration data without executing templates or custom code."""

    current_identities = {_identity(finding) for finding in _scan(current)}
    proposed = [
        replace(finding, introduced_or_changed=_identity(finding) not in current_identities)
        for finding in _scan(proposed)
    ]
    changed = [finding for finding in proposed if finding.introduced_or_changed]
    # HAMCP-337 (E031): the complete-review bound counts only the findings a
    # reviewer must assess.  It previously counted every finding in the
    # whole dashboard, so a dashboard already near the bound refused a small
    # valid change such as two lock tiles and two toggles.
    if len(changed) > MAX_RISK_FINDINGS:
        raise RiskAnalysisError(
            "Dashboard risk findings exceed the complete review bound",
            constraint="changed_risk_findings",
            stage=_RISK_STAGE,
            observed=len(changed),
            limit=MAX_RISK_FINDINGS,
            card_path=_card_path(_pointer_tokens(changed[MAX_RISK_FINDINGS].path)),
        )
    manual_categories = {
        RiskCategory.OPAQUE_CUSTOM,
        RiskCategory.TEMPLATE_OR_CONDITIONAL,
        RiskCategory.UNKNOWN,
    }
    elevated_categories = {
        RiskCategory.TOGGLE,
        RiskCategory.SERVICE_ACTION,
        RiskCategory.HIGH_CONSEQUENCE,
        RiskCategory.DESTRUCTIVE_ADMIN,
    }
    manual = any(finding.category in manual_categories for finding in changed)
    if manual:
        disposition = RiskDisposition.MANUAL_REVIEW_REQUIRED
    elif any(finding.category in elevated_categories for finding in changed):
        disposition = RiskDisposition.ELEVATED_REVIEW
    else:
        disposition = RiskDisposition.STANDARD_REVIEW
    projection = [_projection(finding) for finding in proposed]
    return DashboardRiskEvidence(
        model=RISK_MODEL,
        disposition=disposition,
        findings=tuple(proposed),
        manual_review_required=manual,
        opaque_custom_action_count=sum(
            finding.category is RiskCategory.OPAQUE_CUSTOM for finding in changed
        ),
        high_consequence_action_count=sum(
            finding.category is RiskCategory.HIGH_CONSEQUENCE for finding in changed
        ),
        destructive_admin_action_count=sum(
            finding.category is RiskCategory.DESTRUCTIVE_ADMIN for finding in changed
        ),
        evidence_sha256=engineering_sha256(projection),
    )
