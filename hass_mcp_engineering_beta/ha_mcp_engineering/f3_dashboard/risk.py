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


# HAMCP-337 F1: the tile card's icon gestures are action keys too.
_ACTION_KEYS = frozenset(
    {
        "tap_action",
        "hold_action",
        "double_tap_action",
        "icon_tap_action",
        "icon_hold_action",
        "icon_double_tap_action",
    }
)
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
# HAMCP-338 (owner direction 2026-11-08): a native control that the
# frontend renders for an entity without any action configuration is
# classified exactly like a toggle of the same entity on a tile, through
# ``_domain_control_category``: lock, cover and alarm_control_panel are
# high-consequence, light and switch are toggles.  Other domains are not
# classified here.
_ENTITY_ROW_CONTROL_DOMAINS = frozenset(
    {"lock", "cover", "alarm_control_panel", "light", "switch"}
)
# HAMCP-337 F1: native row types, read at frontend 20261028.4 (commit
# a67b691a, pinned by Core 2026.11.0).  An explicit row ``type`` takes
# precedence over the domain default.  ``lock-entity`` and ``cover-entity``
# always render lock and cover controls whatever the entity;
# ``toggle-entity`` renders a toggle that reaches the domain's toggle
# service.  Display rows (``simple-entity``, ``sensor-entity``,
# ``attribute``, ``section``, ``divider``, ``weblink``, ``text``), ``button``
# and ``buttons`` rows (which toggle only light/switch-class domains by
# default), custom rows and other types are not classified here; any
# explicit action they carry is classified through ``_ACTION_KEYS``.
_ENTITY_ROW_TYPE_CONTROLS = {
    "lock-entity": "entity_row_lock_control",
    "cover-entity": "entity_row_cover_control",
}
_ENTITY_ROW_TOGGLE_TYPE = "toggle-entity"
_CONDITIONAL_ROW_TYPE = "conditional"
# Shapes whose own configuration names the service a tap performs: the
# ``call-service``/``perform-action`` entities row and the picture-elements
# ``service-button``/``action-button`` element.
_SERVICE_CONTROL_TYPES = frozenset(
    {"call-service", "perform-action", "service-button", "action-button"}
)
_HEADER_FOOTER_KEYS = frozenset({"header", "footer"})
_AREA_CONTROLS_FEATURE = "area-controls"
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


def _card_path(document: Any, tokens: tuple[str, ...]) -> str:
    """Return the structural location of ``tokens`` without any data value.

    The walk follows ``tokens`` through ``document`` itself, so each kept
    index is proven to be a list position rather than inferred from the
    token's spelling.  Only a fixed container token whose value is a list,
    followed by a canonical ASCII index of that list, and a fixed single-card
    wrapper token whose value is an object are kept.  The walk stops at the
    first other token, so mapping keys (including digit-only keys such as
    ``"12345"``), entity IDs and names are never included (HAMCP-337 F2).
    """

    segments: list[str] = []
    node = document
    index = 0
    while index < len(tokens) and len(segments) < MAX_STRUCTURAL_CARD_PATH_SEGMENTS:
        token = tokens[index]
        if not isinstance(node, dict) or token not in node:
            break
        child = node[token]
        if token in STRUCTURAL_CARD_CONTAINERS:
            if not isinstance(child, list) or index + 1 >= len(tokens):
                break
            position = tokens[index + 1]
            if (
                not _structural_index(position)
                or str(int(position)) != position
                or int(position) >= len(child)
            ):
                break
            segments.append(f"/{token}/{position}")
            node = child[int(position)]
            index += 2
        elif token in STRUCTURAL_CARD_WRAPPERS and isinstance(child, dict):
            segments.append(f"/{token}")
            node = child
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


def _row_control(row: Any) -> tuple[Any, str, RiskCategory, str] | None:
    """Return the native control an entities-card row renders, if classified."""

    if isinstance(row, str):
        entity: Any = row
        row_type: Any = None
    elif isinstance(row, dict):
        entity = row.get("entity")
        row_type = row.get("type")
    else:
        return None
    domain = _entity_domain(entity)
    if domain is None:
        return None
    if row_type in _ENTITY_ROW_TYPE_CONTROLS:
        return (
            entity,
            domain,
            RiskCategory.HIGH_CONSEQUENCE,
            _ENTITY_ROW_TYPE_CONTROLS[row_type],
        )
    if row_type in {None, "", _ENTITY_ROW_TOGGLE_TYPE}:
        if domain not in _ENTITY_ROW_CONTROL_DOMAINS:
            return None
        category = _domain_control_category(domain)
        return (
            entity,
            domain,
            category,
            "entity_row_control_targets_high_consequence_domain"
            if category is RiskCategory.HIGH_CONSEQUENCE
            else "entity_row_toggle_control",
        )
    return None


def _domain_reason(category: RiskCategory, shape: str) -> str:
    return (
        f"{shape}_targets_high_consequence_domain"
        if category is RiskCategory.HIGH_CONSEQUENCE
        else f"{shape}_toggle_control"
    )


def _row_containers(card: dict[str, Any]) -> list[Any]:
    """Entities-card row lists rendered by ``card``."""

    card_type = card.get("type")
    rows = card.get("entities")
    if not isinstance(rows, list):
        return []
    if card_type == "entities":
        return rows
    if card_type == "entity-filter":
        # The entity-filter card renders an entities card unless its own
        # ``card`` names another type.
        inner = card.get("card")
        inner_type = inner.get("type") if isinstance(inner, dict) else None
        if inner is None or (isinstance(inner, dict) and inner_type in {None, "entities"}):
            return rows
    return []


def _default_control_findings(
    value: dict[str, Any],
    *,
    tokens: tuple[str, ...],
    custom_card: bool,
    conditional: bool,
    custom_card_configuration: Any,
    conditional_configuration: Any,
) -> tuple[list[RiskFinding], set[tuple[str, ...]]]:
    """Classify the native controls ``value`` renders without action keys.

    HAMCP-338 rows and HAMCP-337 F1 typed rows, conditional rows,
    entity-filter rows, ``buttons`` header/footer chips, toggle-group cards,
    area-controls features and the alarm-panel card.  Returns the findings
    and the object locations already classified, which are therefore not
    also reported as display-only entities.
    """

    findings: list[RiskFinding] = []
    classified: set[tuple[str, ...]] = set()

    def add(
        category: RiskCategory,
        location: tuple[str, ...],
        domain: str | None,
        reason: str,
        binding: str,
    ) -> None:
        findings.append(
            RiskFinding(
                category,
                _pointer(location),
                None,
                None,
                domain,
                False,
                binding,
                False,
                reason,
            )
        )

    def context_binding(model: str, **fields: Any) -> str:
        return engineering_sha256(
            {
                "model": model,
                "data_role": "untrusted_data",
                **fields,
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

    # Entities-card rows, including a conditional row's wrapped ``row``.
    for index, row in enumerate(_row_containers(value)):
        location = (*tokens, "entities", str(index))
        wrappers: list[Any] = []
        while (
            isinstance(row, dict)
            and row.get("type") == _CONDITIONAL_ROW_TYPE
            and isinstance(row.get("row"), dict)
        ):
            wrappers.append(row)
            row = row["row"]
            location = (*location, "row")
        control = _row_control(row)
        if control is None:
            continue
        entity, domain, category, reason = control
        binding_fields: dict[str, Any] = {
            "card_type": "entities",
            "complete_row": row,
            "row_entity": entity,
        }
        if wrappers or value.get("type") != "entities":
            binding_fields["container_card_type"] = value.get("type")
            binding_fields["conditional_rows"] = wrappers
        add(
            category,
            location,
            domain,
            reason,
            context_binding(
                "f3-dashboard-entity-row-semantic-binding-v1", **binding_fields
            ),
        )
        if isinstance(row, dict):
            classified.add(location)

    # ``buttons`` header/footer: every chip toggles on tap by default, with
    # no domain filter (hui-buttons-header-footer.ts).
    if (
        tokens
        and tokens[-1] in _HEADER_FOOTER_KEYS
        and value.get("type") == "buttons"
        and isinstance(value.get("entities"), list)
    ):
        for index, chip in enumerate(value["entities"]):
            if isinstance(chip, dict):
                if any(key in chip for key in _ACTION_KEYS):
                    continue
                entity = chip.get("entity")
            else:
                entity = chip
            domain = _entity_domain(entity)
            if domain not in _ENTITY_ROW_CONTROL_DOMAINS:
                continue
            location = (*tokens, "entities", str(index))
            category = _domain_control_category(domain)
            add(
                category,
                location,
                domain,
                _domain_reason(category, "header_footer_button"),
                context_binding(
                    "f3-dashboard-default-control-semantic-binding-v1",
                    shape="buttons_header_footer",
                    complete_control=chip,
                    control_entity=entity,
                ),
            )
            if isinstance(chip, dict):
                classified.add(location)

    # Toggle-group card: one tap toggles every listed entity.
    if value.get("type") == "toggle-group" and isinstance(value.get("entities"), list):
        for index, entity in enumerate(value["entities"]):
            domain = _entity_domain(entity)
            if domain not in _ENTITY_ROW_CONTROL_DOMAINS:
                continue
            category = _domain_control_category(domain)
            add(
                category,
                (*tokens, "entities", str(index)),
                domain,
                _domain_reason(category, "toggle_group"),
                context_binding(
                    "f3-dashboard-default-control-semantic-binding-v1",
                    shape="toggle_group",
                    complete_control=value["entities"],
                    control_entity=entity,
                ),
            )

    # Area-controls feature: toggles whole domains of the area; without
    # ``controls`` it shows every default domain, cover classes included.
    features = value.get("features")
    if isinstance(features, list):
        for index, feature in enumerate(features):
            if not (
                isinstance(feature, dict)
                and feature.get("type") == _AREA_CONTROLS_FEATURE
            ):
                continue
            controls = feature.get("controls")
            domains: set[str] = set()
            if controls is None:
                domains.add("cover")
            elif isinstance(controls, list):
                for control in controls:
                    if isinstance(control, str):
                        domains.add(control.split("-", 1)[0])
                    elif isinstance(control, dict):
                        entity_domain = _entity_domain(control.get("entity_id"))
                        if entity_domain is not None:
                            domains.add(entity_domain)
            if domains & _HIGH_ENTITY_DOMAINS & _ENTITY_ROW_CONTROL_DOMAINS:
                category = RiskCategory.HIGH_CONSEQUENCE
            elif domains & _ENTITY_ROW_CONTROL_DOMAINS:
                category = RiskCategory.TOGGLE
            else:
                continue
            add(
                category,
                (*tokens, "features", str(index)),
                None,
                _domain_reason(category, "area_controls_feature"),
                context_binding(
                    "f3-dashboard-default-control-semantic-binding-v1",
                    shape="area_controls_feature",
                    complete_control=feature,
                    control_entity=None,
                ),
            )

    # Alarm-panel card: arm and disarm buttons are always rendered.
    entity = value.get("entity")
    if value.get("type") == "alarm-panel" and _entity_domain(entity) == "alarm_control_panel":
        add(
            RiskCategory.HIGH_CONSEQUENCE,
            (*tokens, "entity"),
            "alarm_control_panel",
            "alarm_panel_card_arm_disarm_controls",
            context_binding(
                "f3-dashboard-default-control-semantic-binding-v1",
                shape="alarm_panel_card",
                complete_control={"entity": entity, "states": value.get("states")},
                control_entity=entity,
            ),
        )
        classified.add(tokens)
    return findings, classified


def _service_control_action(value: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """The tap action of a shape whose configuration names its service."""

    if value.get("type") not in _SERVICE_CONTROL_TYPES:
        return None
    key = "action" if "action" in value else "service" if "service" in value else None
    if key is None:
        return None
    action: dict[str, Any] = {
        "action": "perform-action",
        "perform_action": value[key],
    }
    for name in ("target", "data", "service_data", "confirmation"):
        if name in value:
            action[name] = value[name]
    return key, action


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
                card_path=_card_path(configuration, tokens),
            )
        if len(tokens) > MAX_JSON_DEPTH:
            raise RiskAnalysisError(
                "Dashboard risk traversal exceeds its reviewed bound",
                constraint="risk_traversal_depth",
                stage=_RISK_STAGE,
                limit=MAX_JSON_DEPTH,
                card_path=_card_path(configuration, tokens),
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

        service_control = _service_control_action(value)
        if service_control is not None:
            service_key, service_action = service_control
            findings.extend(
                _action_findings(
                    service_action,
                    path=_pointer((*tokens, service_key)),
                    card_entity=card_entity,
                    card_type=card_type,
                    custom_card=custom,
                    conditional=conditional,
                    custom_card_configuration=custom_configuration,
                    conditional_configuration=conditional_configuration,
                )
            )

        row_findings, row_tokens = _default_control_findings(
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
                    card_path=_card_path(configuration, tokens),
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
                    card_path=_card_path(configuration, tokens),
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
                configuration,
                _pointer_tokens(findings[MAX_RISK_EVIDENCE_FINDINGS].path),
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
    proposed_configuration = proposed
    proposed = [
        replace(finding, introduced_or_changed=_identity(finding) not in current_identities)
        for finding in _scan(proposed_configuration)
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
            card_path=_card_path(
                proposed_configuration,
                _pointer_tokens(changed[MAX_RISK_FINDINGS].path),
            ),
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
