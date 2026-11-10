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
# classified here.  HAMCP-345: valve is a high-consequence control domain
# (hui-valve-entity-row.ts:44-66 renders ha-entity-toggle, which calls
# valve.open_valve/close_valve through get_toggle_action.ts).
_ENTITY_ROW_CONTROL_DOMAINS = frozenset(
    {"lock", "cover", "alarm_control_panel", "light", "switch", "valve"}
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
    # HAMCP-345: create-row-element.ts:53 ``valve-entity`` always renders
    # the valve row's toggle.
    "valve-entity": "entity_row_valve_control",
}
_ENTITY_ROW_TOGGLE_TYPE = "toggle-entity"
_CONDITIONAL_ROW_TYPE = "conditional"
# HAMCP-345 (frontend a67b691a, pinned by Core 2026.11.0).
# DOMAINS_TOGGLE (common/const.ts:102-111): the domains whose entities the
# button card and row, the buttons row, the tile icon, picture-glance items
# and the entities-card header toggle toggle by default.
_DOMAINS_TOGGLE = frozenset(
    {
        "fan",
        "input_boolean",
        "light",
        "switch",
        "group",
        "automation",
        "humidifier",
        "valve",
    }
)
# Of those default toggles only the high-consequence domain and ``group``
# (disclosed, members unknown) are classified on the button, buttons,
# tile-icon and picture-glance shapes; their light/switch-class defaults
# remain a documented residual.
_DEFAULT_TOGGLE_HIGH_DOMAINS = _DOMAINS_TOGGLE & _HIGH_ENTITY_DOMAINS
_GROUP_DOMAIN = "group"
_GROUP_ROW_TYPE = "group-entity"
_BUTTON_TYPE = "button"
_BUTTONS_ROW_TYPE = "buttons"
# A ``group`` entity's members are its runtime ``entity_id`` state
# attribute (hui-group-entity-row.ts; Core group/entity.py), never part of
# the dashboard configuration, so its row cannot be classified at plan time.
_GROUP_MEMBERS_REASON = "group_row_members_not_established_at_plan_time"
_HEADER_TOGGLE_GROUP_REASON = (
    "header_toggle_group_members_not_established_at_plan_time"
)
# Owner direction 2026-11-10: the group-row rule also applies to a group
# entity's default control on the button card, tile icon, picture-glance
# items, toggle-group and header/footer ``buttons`` chips.
_GROUP_CONTROL_REASON = "group_control_members_not_established_at_plan_time"
# strategies/get-strategy.ts: a ``strategy`` key on the dashboard, a view
# or a section is replaced by cards generated in the browser.  Only the
# built-in ``iframe`` and ``map`` strategies generate their cards from the
# configuration alone, and those cards render no control.
_STRATEGY_REASON = "strategy_generated_controls_cannot_be_analyzed"
_NO_CONTROL_STRATEGY_TYPES = frozenset({"iframe", "map"})
# Shapes whose own configuration names the service a tap performs: the
# ``call-service``/``perform-action`` entities row and the picture-elements
# ``service-button``/``action-button`` element.
_SERVICE_ROW_TYPES = frozenset({"call-service", "perform-action"})
_SERVICE_ELEMENT_TYPES = frozenset({"service-button", "action-button"})
# HAMCP-337 F1: the area-controls feature's domain controls
# (AREA_CONTROL_DOMAINS, frontend a67b691a).  Any other control string is
# an entity ID.
_AREA_CONTROL_DOMAINS = frozenset(
    {
        "light",
        "fan",
        "cover-shutter",
        "cover-blind",
        "cover-curtain",
        "cover-shade",
        "cover-awning",
        "cover-garage",
        "cover-gate",
        "cover-door",
        "cover-window",
        "cover-damper",
        "switch",
    }
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


def _js_falsy(value: Any) -> bool:
    """Whether the frontend's ``||`` and ``!`` treat a JSON value as false."""

    if value is None or value is False or value == "":
        return True
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == 0
    )


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

    if action_name is not None and not isinstance(action_name, str):
        # HAMCP-337 D2: arbitrary JSON is data, never a crash.
        category = RiskCategory.UNKNOWN
        reason = "unknown_frontend_action_type"
    elif action_name in {None, "none"}:
        category = RiskCategory.UNKNOWN if action_name is None else RiskCategory.DISPLAY_ONLY
        reason = "action_type_missing" if action_name is None else "explicit_no_action"
    elif action_name == "more-info":
        category = RiskCategory.MORE_INFO
        reason = "frontend_more_info_action"
    elif action_name in {"navigate", "url"}:
        category = RiskCategory.NAVIGATION
        reason = "frontend_navigation_action"
    elif action_name == "toggle":
        # HAMCP-337 F1: handle-action.ts toggles the card or row
        # ``config.entity`` (frontend a67b691a); an ``entity`` inside the
        # action is never read for a toggle.
        domain = _entity_domain(card_entity)
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
    # create-element-base.ts: a falsy ``type`` selects the domain default
    # row; any other non-string type renders an error row.
    if _js_falsy(row_type):
        row_type = None
    elif not isinstance(row_type, str):
        return None
    if row_type in _ENTITY_ROW_TYPE_CONTROLS:
        return (
            entity,
            domain,
            RiskCategory.HIGH_CONSEQUENCE,
            _ENTITY_ROW_TYPE_CONTROLS[row_type],
        )
    if row_type == _BUTTON_TYPE:
        # hui-button-row.ts:44-54: the row toggles a DOMAINS_TOGGLE entity
        # unless its own ``tap_action`` replaces the default.
        if isinstance(row, dict) and "tap_action" in row:
            return None
        if domain == _GROUP_DOMAIN:
            return entity, domain, RiskCategory.UNKNOWN, _GROUP_MEMBERS_REASON
        if domain in _DEFAULT_TOGGLE_HIGH_DOMAINS:
            return (
                entity,
                domain,
                RiskCategory.HIGH_CONSEQUENCE,
                "button_row_default_toggle_targets_high_consequence_domain",
            )
        return None
    if row_type in {None, _ENTITY_ROW_TOGGLE_TYPE, _GROUP_ROW_TYPE}:
        if domain == _GROUP_DOMAIN:
            return entity, domain, RiskCategory.UNKNOWN, _GROUP_MEMBERS_REASON
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


def _default_toggle_control(
    domain: str | None, shape: str
) -> tuple[RiskCategory, str] | None:
    """Classify the default toggle of a button card, tile icon or
    picture-glance item: a group discloses, valve is high-consequence and
    the light/switch-class defaults remain a documented residual."""

    if domain == _GROUP_DOMAIN:
        return RiskCategory.UNKNOWN, _GROUP_CONTROL_REASON
    if domain in _DEFAULT_TOGGLE_HIGH_DOMAINS:
        return (
            RiskCategory.HIGH_CONSEQUENCE,
            f"{shape}_default_toggle_targets_high_consequence_domain",
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
        # hui-entity-filter-card.ts renders ``{type: "entities",
        # ...config.card}``: only an object ``card`` carrying its own
        # ``type`` key replaces the entities card.
        inner = card.get("card")
        inner_type = (
            inner.get("type", "entities") if isinstance(inner, dict) else "entities"
        )
        if inner_type == "entities":
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
    classified_locations: set[tuple[str, ...]],
) -> tuple[list[RiskFinding], set[tuple[str, ...]]]:
    """Classify the native controls ``value`` renders without action keys.

    HAMCP-338 rows and HAMCP-337 F1 typed rows, conditional rows,
    entity-filter rows, ``buttons`` header/footer chips, toggle-group cards,
    area-controls features and the alarm-panel card.  HAMCP-345 adds valve
    controls, group rows, ``button``/``buttons`` rows, the entities-card
    header toggle, the valve defaults of the button card, tile icon and
    picture-glance items, and the group defaults of those shapes, the
    toggle-group and header/footer chips.  ``classified_locations`` are objects an enclosing
    shape already classified (a ``button`` row is not also a button card).
    Returns the findings and the object locations already classified, which
    are therefore not also reported as display-only entities.
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
        if (
            isinstance(row, dict)
            and row.get("type") == _BUTTONS_ROW_TYPE
            and isinstance(row.get("entities"), list)
        ):
            # hui-buttons-row.ts:25-38: each button toggles a DOMAINS_TOGGLE
            # entity unless its own ``tap_action`` replaces the default.
            for button_index, button in enumerate(row["entities"]):
                if isinstance(button, dict):
                    if "tap_action" in button:
                        continue
                    button_entity = button.get("entity")
                else:
                    button_entity = button
                button_domain = _entity_domain(button_entity)
                if button_domain == _GROUP_DOMAIN:
                    category = RiskCategory.UNKNOWN
                    reason = _GROUP_MEMBERS_REASON
                elif button_domain in _DEFAULT_TOGGLE_HIGH_DOMAINS:
                    category = RiskCategory.HIGH_CONSEQUENCE
                    reason = "buttons_row_default_toggle_targets_high_consequence_domain"
                else:
                    continue
                button_location = (*location, "entities", str(button_index))
                add(
                    category,
                    button_location,
                    button_domain,
                    reason,
                    context_binding(
                        "f3-dashboard-default-control-semantic-binding-v1",
                        shape="buttons_row",
                        complete_control=button,
                        control_entity=button_entity,
                        container_card_type=value.get("type"),
                        conditional_rows=wrappers,
                    ),
                )
                if isinstance(button, dict):
                    classified.add(button_location)
            continue
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

    # Entities-card header toggle (hui-entities-card.ts:31-52, 242-254;
    # hui-entities-toggle.ts:19-27, 61-65).  It is shown when
    # ``show_header_toggle`` is truthy or, with that key absent and a
    # ``title`` present, when at least two rows name a DOMAINS_TOGGLE
    # entity.  It toggles every row entity in DOMAINS_TOGGLE at once.  An
    # entity-filter card renders ``{type: "entities", entities: [],
    # ...card}`` (hui-entity-filter-card.ts:123-127) with a state-filtered
    # subset of its rows, so its rows are the superset here.
    header_rows = _row_containers(value)
    if value.get("type") == "entities":
        header_config: Any = value
        header_location = (*tokens, "show_header_toggle")
    else:
        header_config = value.get("card")
        header_location = (*tokens, "card", "show_header_toggle")
    if header_rows and isinstance(header_config, dict):
        row_entities = [
            row if isinstance(row, str) else row["entity"]
            for row in header_rows
            if isinstance(row, str) or (isinstance(row, dict) and "entity" in row)
        ]
        toggleable = [
            entity for entity in row_entities if _entity_domain(entity) in _DOMAINS_TOGGLE
        ]
        if "show_header_toggle" in header_config:
            shown = not _js_falsy(header_config["show_header_toggle"])
        else:
            shown = "title" in header_config and len(toggleable) >= 2
        if shown:
            by_domain: dict[str, list[Any]] = {}
            for entity in toggleable:
                by_domain.setdefault(_entity_domain(entity) or "", []).append(entity)
            for domain in sorted(by_domain):
                if domain == _GROUP_DOMAIN:
                    category = RiskCategory.UNKNOWN
                    reason = _HEADER_TOGGLE_GROUP_REASON
                else:
                    category = (
                        RiskCategory.HIGH_CONSEQUENCE
                        if domain in _HIGH_ENTITY_DOMAINS
                        else RiskCategory.TOGGLE
                    )
                    reason = _domain_reason(category, "header_toggle")
                add(
                    category,
                    header_location,
                    domain,
                    reason,
                    context_binding(
                        "f3-dashboard-header-toggle-semantic-binding-v1",
                        shape="entities_header_toggle",
                        container_card_type=value.get("type"),
                        title_present="title" in header_config,
                        show_header_toggle={
                            "present": "show_header_toggle" in header_config,
                            "value": header_config.get("show_header_toggle"),
                        },
                        toggled_entities=by_domain[domain],
                    ),
                )

    # Valve defaults of the card shapes that toggle a DOMAINS_TOGGLE entity
    # when no action key replaces the default.
    own_entity = value.get("entity")
    own_domain = _entity_domain(own_entity)
    default_shape: str | None = None
    if tokens in classified_locations:
        pass
    elif value.get("type") == _BUTTON_TYPE and "tap_action" not in value:
        # hui-button-card.ts:71-74, 166-178.
        default_shape = "button_card"
    elif value.get("type") == "tile" and "icon_tap_action" not in value:
        # tile-card-helpers.ts:10-25; hui-tile-card.ts:117-125 (defaults spread at 123).
        default_shape = "tile_icon"
    own_control = (
        None
        if default_shape is None
        else _default_toggle_control(own_domain, default_shape)
    )
    if default_shape is not None and own_control is not None:
        add(
            own_control[0],
            (*tokens, "entity"),
            own_domain,
            own_control[1],
            context_binding(
                "f3-dashboard-default-control-semantic-binding-v1",
                shape=default_shape,
                complete_control={
                    key: value.get(key)
                    for key in ("type", "entity", "tap_action", "icon_tap_action")
                },
                control_entity=own_entity,
            ),
        )
        classified.add(tokens)
    if (
        value.get("type") == "picture-glance"
        and isinstance(value.get("entities"), list)
        and _js_falsy(value.get("force_dialog"))
    ):
        # hui-picture-glance-card.ts:96-105, 273-277: an item toggles a
        # DOMAINS_TOGGLE entity unless ``force_dialog`` or its own
        # ``tap_action`` replaces the default.
        for index, item in enumerate(value["entities"]):
            if isinstance(item, dict):
                if "tap_action" in item:
                    continue
                item_entity = item.get("entity")
            else:
                item_entity = item
            item_domain = _entity_domain(item_entity)
            item_control = _default_toggle_control(item_domain, "picture_glance")
            if item_control is None:
                continue
            location = (*tokens, "entities", str(index))
            add(
                item_control[0],
                location,
                item_domain,
                item_control[1],
                context_binding(
                    "f3-dashboard-default-control-semantic-binding-v1",
                    shape="picture_glance_entity",
                    complete_control=item,
                    control_entity=item_entity,
                    force_dialog={
                        "present": "force_dialog" in value,
                        "value": value.get("force_dialog"),
                    },
                ),
            )
            if isinstance(item, dict):
                classified.add(location)

    # ``buttons`` header/footer: every chip toggles on tap by default, with
    # no domain filter (hui-buttons-header-footer.ts merges ``{tap_action:
    # toggle, hold_action: more-info, ...chip}``).  Only the chip's own
    # ``tap_action`` replaces the default tap; its other gestures are
    # classified through ``_ACTION_KEYS`` as well.
    if (
        tokens
        and tokens[-1] in _HEADER_FOOTER_KEYS
        and value.get("type") == "buttons"
        and isinstance(value.get("entities"), list)
    ):
        for index, chip in enumerate(value["entities"]):
            if isinstance(chip, dict):
                if "tap_action" in chip:
                    continue
                entity = chip.get("entity")
            else:
                entity = chip
            domain = _entity_domain(entity)
            if domain == _GROUP_DOMAIN:
                category = RiskCategory.UNKNOWN
                reason = _GROUP_CONTROL_REASON
            elif domain in _ENTITY_ROW_CONTROL_DOMAINS:
                category = _domain_control_category(domain)
                reason = _domain_reason(category, "header_footer_button")
            else:
                continue
            location = (*tokens, "entities", str(index))
            add(
                category,
                location,
                domain,
                reason,
                context_binding(
                    "f3-dashboard-default-control-semantic-binding-v1",
                    shape="buttons_header_footer",
                    complete_control=chip,
                    control_entity=entity,
                ),
            )
            if isinstance(chip, dict):
                classified.add(location)

    # Toggle-group card: one tap toggles every listed entity.  A group entity
    # is passed to ``<first entity domain>.<toggle action>``
    # (hui-toggle-group-card.ts:129-137), whose effect on the group's
    # runtime members is not established at plan time.
    if value.get("type") == "toggle-group" and isinstance(value.get("entities"), list):
        for index, entity in enumerate(value["entities"]):
            domain = _entity_domain(entity)
            if domain == _GROUP_DOMAIN:
                category = RiskCategory.UNKNOWN
                reason = _GROUP_CONTROL_REASON
            elif domain in _ENTITY_ROW_CONTROL_DOMAINS:
                category = _domain_control_category(domain)
                reason = _domain_reason(category, "toggle_group")
            else:
                continue
            add(
                category,
                (*tokens, "entities", str(index)),
                domain,
                reason,
                context_binding(
                    "f3-dashboard-default-control-semantic-binding-v1",
                    shape="toggle_group",
                    complete_control=value["entities"],
                    control_entity=entity,
                ),
            )

    # Area-controls feature: toggles whole domains of the area; with falsy
    # ``controls`` it shows every default domain, cover classes included
    # (``controls || DEFAULT_AREA_CONTROLS``).  An object control with an
    # ``entity_id`` key and any string outside AREA_CONTROL_DOMAINS name an
    # entity (``_normalizeControl``).
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
            if _js_falsy(controls):
                domains.add("cover")
            elif isinstance(controls, list):
                for control in controls:
                    if isinstance(control, dict) and "entity_id" in control:
                        entity_domain = _entity_domain(control["entity_id"])
                    elif isinstance(control, str) and control in _AREA_CONTROL_DOMAINS:
                        entity_domain = control.split("-", 1)[0]
                    else:
                        entity_domain = _entity_domain(control)
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
                    # HAMCP-337 D1: the feature acts on the entities of the
                    # card's area minus its exclusions (hui-area-card.ts
                    # feature context), so those are part of the control.
                    container_card_type=value.get("type"),
                    area={"present": "area" in value, "value": value.get("area")},
                    exclude_entities={
                        "present": "exclude_entities" in value,
                        "value": value.get("exclude_entities"),
                    },
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

    control_type = value.get("type")
    if not isinstance(control_type, str):
        return None
    if control_type in _SERVICE_ROW_TYPES:
        # hui-call-service-row.ts: ``perform_action: action || service``,
        # and the row's own ``tap_action`` replaces that synthesized tap.
        if "tap_action" in value:
            return None
        key = (
            "action"
            if not _js_falsy(value.get("action"))
            else "service"
            if not _js_falsy(value.get("service"))
            else None
        )
    elif control_type in _SERVICE_ELEMENT_TYPES:
        # hui-service-button-element.ts: ``action ?? service``.
        key = (
            "action"
            if value.get("action") is not None
            else "service"
            if value.get("service") is not None
            else None
        )
    else:
        return None
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


def _strategy_findings(configuration: dict[str, Any]) -> list[RiskFinding]:
    """Disclose dashboard, view and section strategies (HAMCP-345).

    strategies/get-strategy.ts:319-374 replaces a dashboard, view or
    section carrying a ``strategy`` key (data/lovelace/config/types.ts:28-32,
    view.ts:94-98, section.ts:57-61) with ``{...base, ...generated}``, where
    ``generated`` comes from ``strategy.generate(config, hass)`` in the
    browser (lines 156-199).  Built-in strategies other than ``iframe`` and
    ``map`` read the live registries and states, and ``custom:`` strategies
    run custom code, so the controls they render cannot be analyzed here.
    The ``iframe`` and ``map`` view strategies generate one iframe or map
    card from the configuration alone (iframe-view-strategy.ts:17-30,
    map-view-strategy.ts:16-31; their dashboard strategies generate a view
    with that strategy), and neither card renders a control.  Base keys
    that survive the merge are scanned like any other configuration.
    """

    findings: list[RiskFinding] = []

    def disclose(container: Any, tokens: tuple[str, ...], level: str) -> None:
        if not isinstance(container, dict) or "strategy" not in container:
            return
        strategy = container["strategy"]
        # A non-string ``type`` is never a built-in strategy and must not
        # reach the set membership test (unhashable values raise).
        if (
            isinstance(strategy, dict)
            and isinstance(strategy.get("type"), str)
            and strategy["type"] in _NO_CONTROL_STRATEGY_TYPES
        ):
            return
        findings.append(
            RiskFinding(
                RiskCategory.UNKNOWN,
                _pointer((*tokens, "strategy")),
                None,
                None,
                None,
                False,
                engineering_sha256(
                    {
                        "model": "f3-dashboard-strategy-semantic-binding-v1",
                        "data_role": "untrusted_data",
                        "level": level,
                        "strategy": strategy,
                    }
                ),
                False,
                _STRATEGY_REASON,
            )
        )

    disclose(configuration, (), "dashboard")
    views = configuration.get("views")
    if not isinstance(views, list):
        return findings
    sections: list[tuple[Any, tuple[str, ...]]] = []
    for view_index, view in enumerate(views):
        view_tokens = ("views", str(view_index))
        disclose(view, view_tokens, "view")
        if isinstance(view, dict) and isinstance(view.get("sections"), list):
            sections.extend(
                (section, (*view_tokens, "sections", str(index)))
                for index, section in enumerate(view["sections"])
            )
    # Sections nest through their own ``sections`` key
    # (expandLovelaceSectionStrategies); the JSON depth bound applies.
    while sections:
        section, section_tokens = sections.pop()
        if len(section_tokens) > MAX_JSON_DEPTH:
            raise RiskAnalysisError(
                "Dashboard risk traversal exceeds its reviewed bound",
                constraint="risk_traversal_depth",
                stage=_RISK_STAGE,
                limit=MAX_JSON_DEPTH,
                card_path=_card_path(configuration, section_tokens),
            )
        disclose(section, section_tokens, "section")
        if isinstance(section, dict) and isinstance(section.get("sections"), list):
            sections.extend(
                (child, (*section_tokens, "sections", str(index)))
                for index, child in enumerate(section["sections"])
            )
    return findings


def _scan(configuration: dict[str, Any]) -> list[RiskFinding]:
    validate_json_value(configuration)
    findings: list[RiskFinding] = _strategy_findings(configuration)
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
            classified_locations=classified_rows,
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
