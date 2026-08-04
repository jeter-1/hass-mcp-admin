"""Source-grounded, non-executing dashboard action-risk analysis."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .constants import MAX_JSON_DEPTH, MAX_JSON_NODES, MAX_RISK_FINDINGS, RISK_MODEL
from .errors import RiskAnalysisError
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


def _encode(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _pointer(tokens: tuple[str, ...]) -> str:
    return "/" + "/".join(_encode(token) for token in tokens)


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


def _action_findings(
    action: Any,
    *,
    path: str,
    card_entity: Any,
    custom_card: bool,
    conditional: bool,
) -> list[RiskFinding]:
    if not isinstance(action, dict):
        return [
            RiskFinding(
                RiskCategory.UNKNOWN,
                path,
                None,
                None,
                _entity_domain(card_entity),
                False,
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
        category = (
            RiskCategory.HIGH_CONSEQUENCE
            if domain in _HIGH_ENTITY_DOMAINS
            else RiskCategory.TOGGLE
        )
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
            False,
            reason,
        )
    )
    return findings


def _scan(configuration: dict[str, Any]) -> list[RiskFinding]:
    validate_json_value(configuration)
    findings: list[RiskFinding] = []
    stack: list[tuple[Any, tuple[str, ...], bool, bool]] = [
        (configuration, (), False, False)
    ]
    visited = 0
    while stack:
        value, tokens, inherited_custom, inherited_conditional = stack.pop()
        visited += 1
        if visited > MAX_JSON_NODES or len(tokens) > MAX_JSON_DEPTH:
            raise RiskAnalysisError("Dashboard risk traversal exceeds its reviewed bound")
        if isinstance(value, list):
            for index in range(len(value) - 1, -1, -1):
                stack.append(
                    (value[index], (*tokens, str(index)), inherited_custom, inherited_conditional)
                )
            continue
        if not isinstance(value, dict):
            continue

        card_type = value.get("type")
        custom = inherited_custom or (
            isinstance(card_type, str) and card_type.startswith("custom:")
        )
        conditional = inherited_conditional or card_type == "conditional"
        card_entity = value.get("entity")
        action_keys = [key for key in _ACTION_KEYS if key in value]
        for key in sorted(action_keys):
            findings.extend(
                _action_findings(
                    value[key],
                    path=_pointer((*tokens, key)),
                    card_entity=card_entity,
                    custom_card=custom,
                    conditional=conditional,
                )
            )

        domain = _entity_domain(card_entity)
        if domain in _HIGH_ENTITY_DOMAINS and not action_keys and "features" not in value:
            findings.append(
                RiskFinding(
                    RiskCategory.DISPLAY_ONLY,
                    _pointer((*tokens, "entity")),
                    None,
                    None,
                    domain,
                    False,
                    False,
                    "high_consequence_entity_is_display_only",
                )
            )
        if "features" in value and domain in _HIGH_ENTITY_DOMAINS:
            findings.append(
                RiskFinding(
                    RiskCategory.UNKNOWN,
                    _pointer((*tokens, "features")),
                    None,
                    None,
                    domain,
                    False,
                    False,
                    "interactive_feature_effect_requires_manual_review",
                )
            )

        for key, item in reversed(tuple(value.items())):
            stack.append((item, (*tokens, key), custom, conditional))
    if len(findings) > MAX_RISK_FINDINGS:
        raise RiskAnalysisError("Dashboard risk findings exceed the complete review bound")
    return findings


def _identity(finding: RiskFinding) -> tuple[Any, ...]:
    return (
        finding.category.value,
        finding.path,
        finding.action,
        finding.service,
        finding.entity_domain,
        finding.confirmation_present,
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

