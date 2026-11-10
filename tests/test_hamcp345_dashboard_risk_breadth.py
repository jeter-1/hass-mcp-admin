"""HAMCP-345: dashboard risk breadth (valves, group rows, header toggle,
strategies).

Owner-approved outcome (2026-11-08): dashboard plans classify valve controls
(for example a water main shutoff) as high-consequence actions with
elevated review; entities-card group rows, whose members are not part of
the dashboard configuration, disclose that they cannot be classified at
plan time, and so does a group entity's default control on the button card,
tile icon, picture-glance items, toggle-group and header/footer buttons
(owner direction 2026-11-10); controls rendered by dashboard strategies or by the entities-card
header toggle are analyzed or disclosed; the light/switch default-toggle and
display-only count residuals are pinned.

Owner ruling 2026-11-10 (review correction): a toggle-group card is
classified by what the card sends, one service from ``entities[0]`` for the
whole list; a list whose domains differ requires manual review; no shape
gets a lower disposition than on the release base.

Frontend rules are read at frontend a67b691a (pinned by Core 2026.11.0);
the group-toggle service semantics at Core 74cf5a49.  See
docs/evidence/hamcp345/README.md.

Every governed test is plan-only against the offline fake gateway: no plan
is approved or applied and no provider write can occur.  All entity IDs are
synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

from ha_mcp_engineering.f3_dashboard.errors import PatchValidationError  # noqa: E402
from ha_mcp_engineering.f3_dashboard.models import (  # noqa: E402
    RiskCategory,
    RiskDisposition,
)
from ha_mcp_engineering.f3_dashboard.risk import analyze_dashboard_risk  # noqa: E402
from test_hamcp337_338_dashboard_risk_rows import (  # noqa: E402
    GLANCE_CARDS,
    _PlanOnlyHarness,
    background_dashboard,
    insert_cards,
)


VALVE = "valve.synthetic_water_main"
GROUP = "group.synthetic_doors"
LOCK = "lock.synthetic_front_door"
CARD = f"{GLANCE_CARDS}/0"
U = "/views/0/cards/0"

HIGH = RiskCategory.HIGH_CONSEQUENCE
UNKNOWN = RiskCategory.UNKNOWN
TOGGLE = RiskCategory.TOGGLE
GROUP_REASON = "group_row_members_not_established_at_plan_time"
GROUP_CONTROL_REASON = "group_control_members_not_established_at_plan_time"
HEADER_GROUP_REASON = "header_toggle_group_members_not_established_at_plan_time"
STRATEGY_REASON = "strategy_generated_controls_cannot_be_analyzed"
MIXED_REASON = "toggle_group_mixed_domains_receive_first_entity_service"


def card_risk(card: dict):
    return analyze_dashboard_risk(
        {"views": [{"cards": []}]}, {"views": [{"cards": [card]}]}
    )


def changed(risk) -> list[tuple[str, RiskCategory, str]]:
    return [
        (item.path, item.category, item.reason_code)
        for item in risk.findings
        if item.introduced_or_changed
    ]


class ValveControlTests(unittest.TestCase):
    """Valve controls are high-consequence on every default-control shape."""

    def assert_high(self, card: dict, path: str, reason: str) -> None:
        risk = card_risk(card)
        self.assertIn((path, HIGH, reason), changed(risk))
        self.assertGreaterEqual(risk.high_consequence_action_count, 1)
        self.assertIs(risk.disposition, RiskDisposition.ELEVATED_REVIEW)
        self.assertFalse(risk.manual_review_required)

    def test_entities_rows(self):
        row_reason = "entity_row_control_targets_high_consequence_domain"
        cases = (
            ("bare", VALVE, "entities/0", row_reason),
            ("object", {"entity": VALVE}, "entities/0", row_reason),
            ("valve_entity", {"type": "valve-entity", "entity": VALVE},
             "entities/0", "entity_row_valve_control"),
            ("toggle_entity", {"type": "toggle-entity", "entity": VALVE},
             "entities/0", row_reason),
            ("falsy_type", {"type": "", "entity": VALVE}, "entities/0", row_reason),
            ("conditional",
             {"type": "conditional", "conditions": [], "row": {"entity": VALVE}},
             "entities/0/row", row_reason),
            ("button_row", {"type": "button", "entity": VALVE}, "entities/0",
             "button_row_default_toggle_targets_high_consequence_domain"),
        )
        for label, row, path, reason in cases:
            with self.subTest(row=label):
                self.assert_high(
                    {"type": "entities", "entities": [row]}, f"{U}/{path}", reason
                )
        with self.subTest(row="entity_filter"):
            self.assert_high(
                {"type": "entity-filter", "entities": [VALVE], "state_filter": ["on"]},
                f"{U}/entities/0",
                row_reason,
            )

    def test_buttons_row_buttons(self):
        reason = "buttons_row_default_toggle_targets_high_consequence_domain"
        risk = card_risk(
            {
                "type": "entities",
                "entities": [
                    {"type": "buttons", "entities": [VALVE, {"entity": VALVE}]}
                ],
            }
        )
        self.assertEqual(
            changed(risk),
            [
                (f"{U}/entities/0/entities/0", HIGH, reason),
                (f"{U}/entities/0/entities/1", HIGH, reason),
            ],
        )
        self.assertEqual(risk.high_consequence_action_count, 2)

    def test_header_footer_toggle_group_and_area_controls(self):
        self.assert_high(
            {
                "type": "entities",
                "entities": [],
                "footer": {"type": "buttons", "entities": [VALVE]},
            },
            f"{U}/footer/entities/0",
            "header_footer_button_targets_high_consequence_domain",
        )
        self.assert_high(
            {"type": "toggle-group", "entities": [VALVE]},
            f"{U}/entities/0",
            "toggle_group_targets_high_consequence_domain",
        )
        for control in (VALVE, {"entity_id": VALVE}):
            with self.subTest(control=json.dumps(control)):
                self.assert_high(
                    {
                        "type": "area",
                        "area": "synthetic_utility",
                        "features": [{"type": "area-controls", "controls": [control]}],
                    },
                    f"{U}/features/0",
                    "area_controls_feature_targets_high_consequence_domain",
                )

    def test_card_defaults(self):
        self.assert_high(
            {"type": "button", "entity": VALVE},
            f"{U}/entity",
            "button_card_default_toggle_targets_high_consequence_domain",
        )
        self.assert_high(
            {"type": "tile", "entity": VALVE},
            f"{U}/entity",
            "tile_icon_default_toggle_targets_high_consequence_domain",
        )
        for force_dialog in ("absent", False, None, 0, ""):
            card = {"type": "picture-glance", "image": "/local/x.png",
                    "entities": ["light.synthetic_lamp", {"entity": VALVE}]}
            if force_dialog != "absent":
                card["force_dialog"] = force_dialog
            with self.subTest(force_dialog=json.dumps(force_dialog)):
                risk = card_risk(card)
                self.assertEqual(
                    changed(risk),
                    [(f"{U}/entities/1", HIGH,
                      "picture_glance_default_toggle_targets_high_consequence_domain")],
                )

    def test_explicit_actions_replace_the_default(self):
        # handle-action.ts:36-48 and the setConfig spreads: a present action
        # key replaces the default toggle.
        for card in (
            {"type": "button", "entity": VALVE, "tap_action": {"action": "more-info"}},
            {"type": "tile", "entity": VALVE, "icon_tap_action": {"action": "more-info"}},
            {"type": "picture-glance", "image": "/local/x.png", "force_dialog": True,
             "entities": [VALVE]},
            {"type": "picture-glance", "image": "/local/x.png",
             "entities": [{"entity": VALVE, "tap_action": {"action": "more-info"}}]},
            {"type": "entities", "entities": [
                {"type": "button", "entity": VALVE, "tap_action": {"action": "more-info"}},
                {"type": "buttons", "entities": [
                    {"entity": VALVE, "tap_action": {"action": "more-info"}}]},
            ]},
        ):
            with self.subTest(card=json.dumps(card)):
                risk = card_risk(card)
                self.assertNotIn(HIGH, {item[1] for item in changed(risk)})
                self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)

    def test_button_row_is_not_also_a_button_card(self):
        risk = card_risk(
            {"type": "entities", "entities": [{"type": "button", "entity": VALVE}]}
        )
        self.assertEqual(
            changed(risk),
            [(f"{U}/entities/0", HIGH,
              "button_row_default_toggle_targets_high_consequence_domain")],
        )

    def test_unchanged_valve_control_is_not_newly_changed(self):
        current = {"views": [{"cards": [{"type": "tile", "entity": VALVE}]}]}
        proposed = {
            "views": [
                {"cards": [{"type": "tile", "entity": VALVE},
                           {"type": "markdown", "content": "Synthetic note"}]}
            ]
        }
        risk = analyze_dashboard_risk(current, proposed)
        self.assertEqual(changed(risk), [])
        self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)
        self.assertIn(HIGH, {item.category for item in risk.findings})


class GroupRowTests(unittest.TestCase):
    """A ``group`` entity's members are its runtime ``entity_id`` attribute,
    so an entities-card group row discloses instead of going quiet."""

    def test_group_rows_disclose_unknown_members(self):
        for label, row, path in (
            ("bare", GROUP, "entities/0"),
            ("object", {"entity": GROUP}, "entities/0"),
            ("group_entity", {"type": "group-entity", "entity": GROUP}, "entities/0"),
            ("toggle_entity", {"type": "toggle-entity", "entity": GROUP}, "entities/0"),
            ("button_row", {"type": "button", "entity": GROUP}, "entities/0"),
            ("buttons_row", {"type": "buttons", "entities": [GROUP]},
             "entities/0/entities/0"),
            ("conditional",
             {"type": "conditional", "conditions": [], "row": {"entity": GROUP}},
             "entities/0/row"),
        ):
            with self.subTest(row=label):
                risk = card_risk({"type": "entities", "entities": [row]})
                self.assertEqual(changed(risk), [(f"{U}/{path}", UNKNOWN, GROUP_REASON)])
                self.assertIs(risk.disposition, RiskDisposition.MANUAL_REVIEW_REQUIRED)
                self.assertTrue(risk.manual_review_required)

    def test_group_row_with_its_own_tap_action_is_classified_from_it(self):
        risk = card_risk(
            {"type": "entities", "entities": [
                {"type": "button", "entity": GROUP, "tap_action": {"action": "more-info"}}]}
        )
        self.assertNotIn(UNKNOWN, {item[1] for item in changed(risk)})

    def test_platform_groups_and_ordinary_rows_are_not_over_classified(self):
        # A light group is a light entity: classified by its own domain.
        risk = card_risk(
            {"type": "entities", "entities": ["light.synthetic_downstairs_group"]}
        )
        self.assertEqual(
            changed(risk),
            [(f"{U}/entities/0", TOGGLE, "entity_row_toggle_control")],
        )
        self.assertIs(risk.disposition, RiskDisposition.ELEVATED_REVIEW)
        for row in ("fan.synthetic_fan", "sensor.synthetic_reading",
                    "input_boolean.synthetic_flag",
                    {"type": "attribute", "entity": GROUP, "attribute": "entity_id"},
                    {"type": "simple-entity", "entity": GROUP}):
            with self.subTest(row=json.dumps(row)):
                risk = card_risk({"type": "entities", "entities": [row]})
                self.assertEqual(changed(risk), [])
                self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)

    def test_unchanged_group_row_is_not_newly_changed(self):
        rows = {"type": "entities", "entities": [GROUP]}
        risk = analyze_dashboard_risk(
            {"views": [{"cards": [rows]}]},
            {"views": [{"cards": [rows, {"type": "markdown", "content": "x"}]}]},
        )
        self.assertEqual(changed(risk), [])
        self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)


class GroupNonRowShapeTests(unittest.TestCase):
    """Owner direction 2026-11-10: the group-row rule applies to a group
    entity's default control on the button card, tile icon, picture-glance
    items, toggle-group and header/footer ``buttons`` chips."""

    def test_group_default_controls_disclose_unknown_members(self):
        image = "/local/x.png"
        for label, card, paths in (
            ("button_card", {"type": "button", "entity": GROUP}, ["entity"]),
            ("tile_icon", {"type": "tile", "entity": GROUP}, ["entity"]),
            ("picture_glance_string",
             {"type": "picture-glance", "image": image, "entities": [GROUP]},
             ["entities/0"]),
            ("picture_glance_object",
             {"type": "picture-glance", "image": image, "entities": [{"entity": GROUP}]},
             ["entities/0"]),
            ("toggle_group", {"type": "toggle-group", "entities": [GROUP]},
             ["entities/0"]),
            ("footer_buttons_string",
             {"type": "entities", "entities": [],
              "footer": {"type": "buttons", "entities": [GROUP]}},
             ["footer/entities/0"]),
            ("header_buttons_object",
             {"type": "entities", "entities": [],
              "header": {"type": "buttons", "entities": [{"entity": GROUP}]}},
             ["header/entities/0"]),
        ):
            with self.subTest(shape=label):
                risk = card_risk(card)
                self.assertEqual(
                    changed(risk),
                    [(f"{U}/{path}", UNKNOWN, GROUP_CONTROL_REASON) for path in paths],
                )
                self.assertIs(risk.disposition, RiskDisposition.MANUAL_REVIEW_REQUIRED)
                self.assertTrue(risk.manual_review_required)
                self.assertEqual(risk.high_consequence_action_count, 0)

    def test_group_beside_other_toggle_group_domains_is_one_mixed_disclosure(self):
        # The card sends ``light.turn_on``/``light.turn_off`` to the group and
        # to the lock: neither receives a service of its own domain, so the
        # card is one disclosed finding, not a light toggle, a group
        # disclosure and a lock action.
        risk = card_risk(
            {"type": "toggle-group", "entities": ["light.synthetic_lamp", GROUP, LOCK]}
        )
        self.assertEqual(changed(risk), [(f"{U}/entities", UNKNOWN, MIXED_REASON)])
        self.assertIs(risk.disposition, RiskDisposition.MANUAL_REVIEW_REQUIRED)
        self.assertEqual(risk.high_consequence_action_count, 0)
        # Several groups and nothing else share the first entity's domain:
        # each stays disclosed by the group rule.
        risk = card_risk(
            {"type": "toggle-group", "entities": [GROUP, "group.synthetic_windows"]}
        )
        self.assertEqual(
            changed(risk),
            [
                (f"{U}/entities/0", UNKNOWN, GROUP_CONTROL_REASON),
                (f"{U}/entities/1", UNKNOWN, GROUP_CONTROL_REASON),
            ],
        )

    def test_falsy_force_dialog_keeps_the_picture_glance_default(self):
        for value in (False, 0, "", None):
            with self.subTest(force_dialog=value):
                risk = card_risk(
                    {"type": "picture-glance", "image": "/local/x.png",
                     "force_dialog": value, "entities": [GROUP]}
                )
                self.assertEqual(
                    changed(risk), [(f"{U}/entities/0", UNKNOWN, GROUP_CONTROL_REASON)]
                )

    def test_an_explicit_action_replaces_the_group_default(self):
        more_info = {"action": "more-info"}
        image = "/local/x.png"
        for label, card in (
            ("button_tap", {"type": "button", "entity": GROUP, "tap_action": more_info}),
            ("tile_icon_tap",
             {"type": "tile", "entity": GROUP, "icon_tap_action": more_info}),
            ("picture_glance_force_dialog",
             {"type": "picture-glance", "image": image, "force_dialog": True,
              "entities": [GROUP]}),
            ("picture_glance_item_tap",
             {"type": "picture-glance", "image": image,
              "entities": [{"entity": GROUP, "tap_action": more_info}]}),
            ("footer_chip_tap",
             {"type": "entities", "entities": [],
              "footer": {"type": "buttons",
                         "entities": [{"entity": GROUP, "tap_action": more_info}]}}),
        ):
            with self.subTest(shape=label):
                risk = card_risk(card)
                self.assertNotIn(
                    GROUP_CONTROL_REASON, {item[2] for item in changed(risk)}
                )
                self.assertNotIn(UNKNOWN, {item[1] for item in changed(risk)})

    def test_explicit_group_toggle_keeps_the_row_precedent(self):
        # A row's explicit ``tap_action`` already bypasses the group-row rule;
        # an explicit toggle of a group stays a toggle on cards as on rows.
        for card in (
            {"type": "button", "entity": GROUP, "tap_action": {"action": "toggle"}},
            {"type": "entities", "entities": [
                {"type": "button", "entity": GROUP, "tap_action": {"action": "toggle"}}]},
        ):
            with self.subTest(card=json.dumps(card)):
                risk = card_risk(card)
                self.assertEqual(
                    [item[1:] for item in changed(risk)],
                    [(TOGGLE, "frontend_toggle_action")],
                )

    def test_group_entities_outside_these_shapes_are_not_classified(self):
        for card in (
            {"type": "entity", "entity": GROUP},
            {"type": "glance", "entities": [GROUP]},
            {"type": "history-graph", "entities": [GROUP]},
            {"type": "entities", "entities": [],
             "footer": {"type": "graph", "entity": GROUP}},
        ):
            with self.subTest(card=json.dumps(card)):
                risk = card_risk(card)
                self.assertEqual(changed(risk), [])
                self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)

    def test_unchanged_group_card_is_not_newly_changed(self):
        tile = {"type": "tile", "entity": GROUP}
        risk = analyze_dashboard_risk(
            {"views": [{"cards": [tile]}]},
            {"views": [{"cards": [tile, {"type": "markdown", "content": "x"}]}]},
        )
        self.assertEqual(changed(risk), [])
        self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)
        # Retargeting the tile to a different group is a material change.
        risk = analyze_dashboard_risk(
            {"views": [{"cards": [tile]}]},
            {"views": [{"cards": [{"type": "tile", "entity": "group.synthetic_windows"}]}]},
        )
        self.assertEqual(changed(risk), [(f"{U}/entity", UNKNOWN, GROUP_CONTROL_REASON)])


class ToggleGroupSingleServiceTests(unittest.TestCase):
    """Owner ruling 2026-11-10: a toggle-group is classified by what the card
    sends.  ``hui-toggle-group-card.ts:130-141`` computes the domain from
    ``entities[0]``, takes ``getToggleAction`` for that domain and calls that
    one service with the whole list.  A list whose domains differ is one
    disclosed finding and requires manual review."""

    LIGHT = "light.synthetic_lamp"
    LIGHT2 = "light.synthetic_second"
    VALVE2 = "valve.synthetic_second"
    LOCK2 = "lock.synthetic_back_door"

    # Dispositions of ``analyze_dashboard_risk`` on the release base
    # (release/2.6.0 at cb9d6783) for a toggle-group card with these entities
    # added to an empty view, recorded by
    # docs/evidence/hamcp345/toggle_group_base_vs_head.py.
    BASE_DISPOSITIONS = (
        ([LOCK, LOCK2], "elevated_manual_review"),
        ([VALVE], "standard_review"),
        ([VALVE, VALVE2], "standard_review"),
        ([LIGHT, LIGHT2], "elevated_manual_review"),
        ([GROUP], "standard_review"),
        ([LIGHT, LOCK], "elevated_manual_review"),
        ([LIGHT, VALVE], "elevated_manual_review"),
        ([VALVE, LIGHT], "elevated_manual_review"),
        ([LIGHT, LIGHT2, VALVE], "elevated_manual_review"),
        ([LOCK, LIGHT], "elevated_manual_review"),
        ([LIGHT, GROUP, LOCK], "elevated_manual_review"),
        ([GROUP, LOCK], "elevated_manual_review"),
        (["fan.synthetic", "input_boolean.synthetic"], "standard_review"),
        (["fan.synthetic", "fan.synthetic"], "standard_review"),
        ([5, LOCK], "elevated_manual_review"),
        ([LOCK, 5], "elevated_manual_review"),
        ([5, None], "standard_review"),
        ([{"entity": LOCK}, LIGHT], "elevated_manual_review"),
        ([], "standard_review"),
    )
    RANK = {
        "standard_review": 0,
        "elevated_manual_review": 1,
        "manual_review_required": 2,
    }

    def assert_mixed(self, entities: list, domain: str | None) -> None:
        risk = card_risk({"type": "toggle-group", "entities": entities})
        self.assertEqual(changed(risk), [(f"{U}/entities", UNKNOWN, MIXED_REASON)])
        self.assertEqual(
            [item.entity_domain for item in risk.findings if item.introduced_or_changed],
            [domain],
        )
        self.assertIs(risk.disposition, RiskDisposition.MANUAL_REVIEW_REQUIRED)
        self.assertTrue(risk.manual_review_required)
        self.assertEqual(risk.high_consequence_action_count, 0)

    def test_valve_that_is_not_the_first_entity_is_not_a_valve_action(self):
        # The card calls ``light.turn_on``/``light.turn_off`` with both IDs;
        # the valve is never sent ``valve.open_valve``/``valve.close_valve``.
        for entities in (
            [self.LIGHT, VALVE],
            [self.LIGHT, self.LIGHT2, VALVE],
            [self.LIGHT, VALVE, self.LIGHT2],
        ):
            with self.subTest(entities=entities):
                risk = card_risk({"type": "toggle-group", "entities": entities})
                self.assertNotIn(HIGH, {item[1] for item in changed(risk)})
                self.assertNotIn(
                    "toggle_group_targets_high_consequence_domain",
                    {item[2] for item in changed(risk)},
                )
                self.assert_mixed(entities, "light")

    def test_mixed_domains_are_one_manual_review_finding(self):
        for label, entities, domain in (
            ("light_lock", [self.LIGHT, LOCK], "light"),
            ("lock_light", [LOCK, self.LIGHT], "lock"),
            ("valve_light", [VALVE, self.LIGHT], "valve"),
            ("valve_lock", [VALVE, LOCK], "valve"),
            ("lock_valve", [LOCK, VALVE], "lock"),
            ("light_switch", [self.LIGHT, "switch.synthetic"], "light"),
            ("light_group", [self.LIGHT, GROUP], "light"),
            ("group_lock", [GROUP, LOCK], "group"),
            ("fan_input_boolean", ["fan.synthetic", "input_boolean.synthetic"], "fan"),
            ("sensor_binary_sensor", ["sensor.synthetic", "binary_sensor.synthetic"],
             "sensor"),
            ("differs_only_in_last",
             [LOCK, self.LOCK2, "lock.synthetic_side_door", self.LIGHT], "lock"),
            ("malformed_first", [5, LOCK], None),
            ("malformed_later", [LOCK, 5], "lock"),
            ("no_domain_later", [self.LIGHT, "nodot"], "light"),
        ):
            with self.subTest(entities=label):
                self.assert_mixed(entities, domain)

    def test_a_single_domain_list_is_classified_by_that_domain(self):
        high = "toggle_group_targets_high_consequence_domain"
        toggle = "toggle_group_toggle_control"
        for label, entities, category, reason, disposition in (
            ("valve_only", [VALVE], HIGH, high, RiskDisposition.ELEVATED_REVIEW),
            ("valves", [VALVE, self.VALVE2], HIGH, high, RiskDisposition.ELEVATED_REVIEW),
            ("valve_twice", [VALVE, VALVE], HIGH, high, RiskDisposition.ELEVATED_REVIEW),
            ("locks", [LOCK, self.LOCK2], HIGH, high, RiskDisposition.ELEVATED_REVIEW),
            ("covers", ["cover.synthetic_a", "cover.synthetic_b"], HIGH, high,
             RiskDisposition.ELEVATED_REVIEW),
            ("lights", [self.LIGHT, self.LIGHT2], TOGGLE, toggle,
             RiskDisposition.ELEVATED_REVIEW),
            ("switches", ["switch.synthetic_a", "switch.synthetic_b"], TOGGLE, toggle,
             RiskDisposition.ELEVATED_REVIEW),
            ("groups", [GROUP, "group.synthetic_windows"], UNKNOWN, GROUP_CONTROL_REASON,
             RiskDisposition.MANUAL_REVIEW_REQUIRED),
        ):
            with self.subTest(entities=label):
                risk = card_risk({"type": "toggle-group", "entities": entities})
                self.assertEqual(
                    changed(risk),
                    [
                        (f"{U}/entities/{index}", category, reason)
                        for index in range(len(entities))
                    ],
                )
                self.assertIs(risk.disposition, disposition)
                self.assertEqual(
                    risk.high_consequence_action_count,
                    len(entities) if category is HIGH else 0,
                )

    def test_first_entity_valve_keeps_the_valve_action(self):
        risk = card_risk({"type": "toggle-group", "entities": [VALVE, self.VALVE2]})
        self.assertEqual(
            [
                (item.path, item.category, item.entity_domain)
                for item in risk.findings
                if item.introduced_or_changed
            ],
            [
                (f"{U}/entities/0", HIGH, "valve"),
                (f"{U}/entities/1", HIGH, "valve"),
            ],
        )
        self.assertEqual(risk.high_consequence_action_count, 2)
        self.assertIs(risk.disposition, RiskDisposition.ELEVATED_REVIEW)
        self.assertFalse(risk.manual_review_required)

    def test_single_domain_lists_outside_the_control_domains_stay_unclassified(self):
        # Unchanged from the base: these lists were not classified before
        # this change and share one domain (or name none at all).
        for entities in (
            ["fan.synthetic_a", "fan.synthetic_b"],
            ["sensor.synthetic"],
            [5, None],
            ["nodot", ""],
            [],
        ):
            with self.subTest(entities=entities):
                risk = card_risk({"type": "toggle-group", "entities": entities})
                self.assertEqual(changed(risk), [])
                self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)
        for entities in (None, LOCK, {"0": LOCK}):
            with self.subTest(not_a_list=entities):
                risk = card_risk({"type": "toggle-group", "entities": entities})
                self.assertNotIn(MIXED_REASON, {item[2] for item in changed(risk)})

    def test_unchanged_mixed_toggle_group_is_not_newly_changed(self):
        mixed = {"type": "toggle-group", "entities": [self.LIGHT, VALVE]}
        risk = analyze_dashboard_risk(
            {"views": [{"cards": [mixed]}]},
            {"views": [{"cards": [mixed, {"type": "markdown", "content": "x"}]}]},
        )
        self.assertEqual(changed(risk), [])
        self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)
        self.assertEqual(
            [(item.path, item.category, item.reason_code) for item in risk.findings],
            [(f"{U}/entities", UNKNOWN, MIXED_REASON)],
        )
        # Reordering, appending, retargeting a later entity and moving
        # between a single-domain and a mixed list are material changes.
        for label, proposed, expected in (
            ("reordered", [VALVE, self.LIGHT], [(f"{U}/entities", UNKNOWN, MIXED_REASON)]),
            ("appended", [self.LIGHT, VALVE, LOCK],
             [(f"{U}/entities", UNKNOWN, MIXED_REASON)]),
            ("retargeted", [self.LIGHT, self.VALVE2],
             [(f"{U}/entities", UNKNOWN, MIXED_REASON)]),
            ("made_single_domain", [self.LIGHT],
             [(f"{U}/entities/0", TOGGLE, "toggle_group_toggle_control")]),
        ):
            with self.subTest(change=label):
                risk = analyze_dashboard_risk(
                    {"views": [{"cards": [mixed]}]},
                    {"views": [{"cards": [{"type": "toggle-group", "entities": proposed}]}]},
                )
                self.assertEqual(changed(risk), expected)
        risk = analyze_dashboard_risk(
            {"views": [{"cards": [{"type": "toggle-group", "entities": [self.LIGHT]}]}]},
            {"views": [{"cards": [mixed]}]},
        )
        self.assertEqual(changed(risk), [(f"{U}/entities", UNKNOWN, MIXED_REASON)])
        self.assertIs(risk.disposition, RiskDisposition.MANUAL_REVIEW_REQUIRED)

    def test_no_toggle_group_shape_is_below_its_base_disposition(self):
        for entities, base in self.BASE_DISPOSITIONS:
            with self.subTest(entities=json.dumps(entities)):
                risk = card_risk({"type": "toggle-group", "entities": entities})
                self.assertGreaterEqual(
                    self.RANK[risk.disposition.value], self.RANK[base]
                )


class HeaderToggleTests(unittest.TestCase):
    """hui-entities-card.ts:31-52 computeShowHeaderToggle and
    hui-entities-toggle.ts:19-27, 61-65."""

    HEADER = f"{U}/show_header_toggle"

    def header(self, risk) -> list[tuple[RiskCategory, str]]:
        return [
            (category, reason)
            for path, category, reason in changed(risk)
            if path.endswith("/show_header_toggle")
        ]

    def test_explicit_true_shows_the_toggle(self):
        for value in (True, 1, "yes"):
            with self.subTest(show_header_toggle=json.dumps(value)):
                risk = card_risk(
                    {"type": "entities", "show_header_toggle": value,
                     "entities": ["fan.synthetic_fan"]}
                )
                self.assertEqual(
                    changed(risk), [(self.HEADER, TOGGLE, "header_toggle_toggle_control")]
                )
                self.assertIs(risk.disposition, RiskDisposition.ELEVATED_REVIEW)

    def test_title_and_two_toggleable_rows_show_it_by_default(self):
        risk = card_risk(
            {"type": "entities", "title": "Synthetic",
             "entities": ["fan.synthetic_a", {"entity": "fan.synthetic_b"}]}
        )
        self.assertEqual(changed(risk), [(self.HEADER, TOGGLE, "header_toggle_toggle_control")])

    def test_one_finding_per_toggled_domain(self):
        risk = card_risk(
            {"type": "entities", "title": "Synthetic",
             "entities": [VALVE, "light.synthetic_lamp", GROUP]}
        )
        self.assertEqual(
            self.header(risk),
            [
                (UNKNOWN, HEADER_GROUP_REASON),
                (TOGGLE, "header_toggle_toggle_control"),
                (HIGH, "header_toggle_targets_high_consequence_domain"),
            ],
        )
        self.assertIs(risk.disposition, RiskDisposition.MANUAL_REVIEW_REQUIRED)

    def test_entity_filter_inner_card(self):
        risk = card_risk(
            {"type": "entity-filter", "state_filter": ["on"],
             "entities": [VALVE, "switch.synthetic_pump"],
             "card": {"title": "Synthetic"}}
        )
        self.assertIn(
            (f"{U}/card/show_header_toggle", HIGH,
             "header_toggle_targets_high_consequence_domain"),
            changed(risk),
        )

    def test_hidden_header_toggle_is_not_classified(self):
        fans = ["fan.synthetic_a", "fan.synthetic_b"]
        for label, card in (
            ("no_title", {"type": "entities", "entities": fans}),
            ("one_toggleable_row",
             {"type": "entities", "title": "t", "entities": ["fan.synthetic_a", LOCK]}),
            ("false", {"type": "entities", "title": "t", "show_header_toggle": False,
                       "entities": fans}),
            ("null", {"type": "entities", "title": "t", "show_header_toggle": None,
                      "entities": fans}),
            ("zero", {"type": "entities", "title": "t", "show_header_toggle": 0,
                      "entities": fans}),
            ("explicit_but_nothing_toggleable",
             {"type": "entities", "show_header_toggle": True,
              "entities": ["sensor.synthetic_a", LOCK]}),
            ("entity_filter_without_card",
             {"type": "entity-filter", "entities": fans, "title": "t"}),
        ):
            with self.subTest(card=label):
                self.assertEqual(self.header(card_risk(card)), [])

    def test_unchanged_header_toggle_is_not_newly_changed(self):
        card = {"type": "entities", "title": "t", "entities": ["fan.a", "fan.b"]}
        risk = analyze_dashboard_risk(
            {"views": [{"cards": [card]}]},
            {"views": [{"cards": [card, {"type": "markdown", "content": "x"}]}]},
        )
        self.assertEqual(changed(risk), [])
        # Adding a valve row to the toggled set changes the header finding.
        grown = {**card, "entities": [*card["entities"], VALVE]}
        risk = analyze_dashboard_risk(
            {"views": [{"cards": [card]}]}, {"views": [{"cards": [grown]}]}
        )
        self.assertIn(
            (self.HEADER, HIGH, "header_toggle_targets_high_consequence_domain"),
            changed(risk),
        )
        # A toggle that now also toggles another fan is a changed control.
        grown = {**card, "entities": [*card["entities"], "fan.c"]}
        risk = analyze_dashboard_risk(
            {"views": [{"cards": [card]}]}, {"views": [{"cards": [grown]}]}
        )
        self.assertEqual(
            changed(risk), [(self.HEADER, TOGGLE, "header_toggle_toggle_control")]
        )


class StrategyTests(unittest.TestCase):
    """strategies/get-strategy.ts:156-199, 319-374: generated controls."""

    def test_strategies_disclose_at_every_level(self):
        cases = (
            ({"strategy": {"type": "original-states"}}, "/strategy"),
            ({"views": [{"strategy": {"type": "custom:synthetic"}}]}, "/views/0/strategy"),
            ({"views": [{"strategy": "custom:synthetic"}]}, "/views/0/strategy"),
            ({"views": [{"type": "sections",
                         "sections": [{"strategy": {"type": "area"}}]}]},
             "/views/0/sections/0/strategy"),
            ({"views": [{"type": "sections", "sections": [
                {"type": "grid", "sections": [{"strategy": {"type": "custom:s"}}]}]}]},
             "/views/0/sections/0/sections/0/strategy"),
            ({"views": [{"strategy": {"type": "iframe", "url": "x"}, "cards": []},
                        {"strategy": {"type": None}}]},
             "/views/1/strategy"),
        )
        for configuration, path in cases:
            with self.subTest(path=path, configuration=json.dumps(configuration)):
                risk = analyze_dashboard_risk({"views": []}, configuration)
                self.assertEqual(changed(risk), [(path, UNKNOWN, STRATEGY_REASON)])
                self.assertIs(risk.disposition, RiskDisposition.MANUAL_REVIEW_REQUIRED)

    def test_malformed_strategy_types_disclose_without_crashing(self):
        # Correction (base-vs-head planning check on 57454d31): an
        # unhashable ``type`` raised TypeError in the iframe/map membership
        # test, which planning reported as provider identity unavailable.
        # get-strategy.ts:104-119 renders an error for such a strategy, so
        # it is disclosed like any other strategy.
        for strategy_type in ({}, [], {"description": "x"}, ["map"], {"type": "map"}, 5, True):
            strategy = {"type": strategy_type}
            for configuration, path in (
                ({"strategy": strategy}, "/strategy"),
                ({"views": [{"strategy": strategy}]}, "/views/0/strategy"),
                ({"views": [{"type": "sections", "sections": [{"strategy": strategy}]}]},
                 "/views/0/sections/0/strategy"),
            ):
                with self.subTest(path=path, strategy=json.dumps(strategy)):
                    try:
                        risk = analyze_dashboard_risk({"views": []}, configuration)
                    except Exception as error:  # the corrected defect
                        self.fail(f"strategy analysis raised {type(error).__name__}")
                    self.assertEqual(changed(risk), [(path, UNKNOWN, STRATEGY_REASON)])

    def test_iframe_and_map_strategies_are_not_over_classified(self):
        risk = analyze_dashboard_risk(
            {"views": []},
            {"views": [
                {"strategy": {"type": "iframe", "url": "https://example.invalid"}},
                {"strategy": {"type": "map"}},
            ]},
        )
        self.assertEqual(changed(risk), [])
        self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)

    def test_base_keys_beside_a_strategy_are_still_scanned(self):
        risk = analyze_dashboard_risk(
            {"views": []},
            {"views": [{"strategy": {"type": "map"},
                        "badges": [{"type": "entity", "entity": VALVE,
                                    "tap_action": {"action": "toggle"}}]}]},
        )
        self.assertIn(HIGH, {item[1] for item in changed(risk)})

    def test_unchanged_strategy_is_not_newly_changed(self):
        current = {"views": [{"strategy": {"type": "custom:synthetic"}},
                             {"cards": []}]}
        proposed = {"views": [{"strategy": {"type": "custom:synthetic"}},
                              {"cards": [{"type": "markdown", "content": "x"}]}]}
        risk = analyze_dashboard_risk(current, proposed)
        self.assertEqual(changed(risk), [])
        self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)
        # A changed strategy configuration is a changed finding.
        edited = {"views": [{"strategy": {"type": "custom:synthetic", "x": 1}},
                            {"cards": []}]}
        risk = analyze_dashboard_risk(current, edited)
        self.assertEqual(changed(risk), [("/views/0/strategy", UNKNOWN, STRATEGY_REASON)])

    def test_deep_section_nesting_is_disclosed_or_refused_never_dropped(self):
        def nested(levels: int) -> dict:
            section: dict = {"strategy": {"type": "custom:s"}}
            for _ in range(levels):
                section = {"type": "grid", "sections": [section]}
            return {"views": [{"type": "sections", "sections": [section]}]}

        risk = analyze_dashboard_risk({"views": []}, nested(21))
        self.assertEqual(
            [item[1:] for item in changed(risk)], [(UNKNOWN, STRATEGY_REASON)]
        )
        with self.assertRaises(PatchValidationError):
            analyze_dashboard_risk({"views": []}, nested(22))


class DocumentedResidualTests(unittest.TestCase):
    """Residuals the outcome documents rather than classifies."""

    def test_light_and_switch_defaults_on_button_shapes_are_unclassified(self):
        for entity in ("light.synthetic_lamp", "switch.synthetic_pump"):
            for card in (
                {"type": "button", "entity": entity},
                {"type": "tile", "entity": entity},
                {"type": "picture-glance", "image": "/local/x.png", "entities": [entity]},
                {"type": "entities", "entities": [{"type": "button", "entity": entity}]},
                {"type": "entities", "entities": [{"type": "buttons", "entities": [entity]}]},
            ):
                with self.subTest(entity=entity, card=json.dumps(card)):
                    risk = card_risk(card)
                    self.assertEqual(changed(risk), [])
                    self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)

    def test_display_only_counts(self):
        # A valve tile with an explicit toggle: the tap toggle, the icon
        # default toggle and the nested-action display-only finding.
        risk = card_risk(
            {"type": "tile", "entity": VALVE, "tap_action": {"action": "toggle"}}
        )
        self.assertEqual(
            changed(risk),
            [
                (f"{U}/tap_action", HIGH, "toggle_targets_high_consequence_domain"),
                (f"{U}/entity", HIGH,
                 "tile_icon_default_toggle_targets_high_consequence_domain"),
                (f"{U}/tap_action/entity", RiskCategory.DISPLAY_ONLY,
                 "high_consequence_entity_is_display_only"),
            ],
        )
        self.assertEqual(risk.high_consequence_action_count, 2)
        # A valve whose icon action is more-info: display-only, standard.
        risk = card_risk(
            {"type": "tile", "entity": VALVE, "icon_tap_action": {"action": "more-info"}}
        )
        self.assertEqual(
            [item[1] for item in changed(risk)],
            [RiskCategory.MORE_INFO, RiskCategory.DISPLAY_ONLY],
        )
        self.assertEqual(risk.high_consequence_action_count, 0)


class PlanLevelTests(_PlanOnlyHarness):
    """Governed plan-only evidence of the review level each shape receives."""

    async def plan_card(self, card: dict):
        return await self.plan(background_dashboard(0), insert_cards([card]))

    async def fresh(self) -> None:
        await self.asyncTearDown()
        await self.asyncSetUp()

    def assert_manual(self, created: dict, stored, update: dict) -> None:
        self.assertEqual(update["risk"]["disposition"], "manual_review_required")
        self.assertTrue(update["risk"]["manual_review_required"])
        self.assertEqual(stored.risk.level.value, "high")
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        self.assertTrue(created["approval_actionable"])

    async def test_water_main_valve_tile_is_high_consequence(self):
        created, stored, update = await self.plan_card(
            {"type": "tile", "entity": VALVE, "name": "Water main"}
        )
        self.assertEqual(self.changed_at(update, f"{CARD}/entity"),
                         {"high_consequence_action"})
        self.assertEqual(update["risk"]["high_consequence_action_count"], 1)
        self.assert_elevated(created, stored, update)
        self.assertIn("high_consequence_action", self.flags(update))

    async def test_valve_row_is_high_consequence(self):
        created, stored, update = await self.plan_card(
            {"type": "entities", "entities": [VALVE]}
        )
        self.assertEqual(self.changed_at(update, f"{CARD}/entities/0"),
                         {"high_consequence_action"})
        self.assert_elevated(created, stored, update)

    async def test_group_row_requires_manual_review(self):
        created, stored, update = await self.plan_card(
            {"type": "entities", "entities": [GROUP]}
        )
        self.assertEqual(self.changed_at(update, f"{CARD}/entities/0"),
                         {"unknown_action_semantics"})
        self.assert_manual(created, stored, update)

    async def test_group_tile_requires_manual_review(self):
        created, stored, update = await self.plan_card({"type": "tile", "entity": GROUP})
        self.assertEqual(self.changed_at(update, f"{CARD}/entity"),
                         {"unknown_action_semantics"})
        self.assert_manual(created, stored, update)

    async def test_mixed_toggle_group_requires_manual_review(self):
        # A valve after a light: the plan is disclosed for manual review,
        # carries no valve action, and stays approvable.
        created, stored, update = await self.plan_card(
            {"type": "toggle-group", "entities": ["light.synthetic_lamp", VALVE]}
        )
        self.assertEqual(self.changed_at(update, f"{CARD}/entities"),
                         {"unknown_action_semantics"})
        self.assertEqual(self.changed_at(update, f"{CARD}/entities/1"), set())
        self.assertEqual(update["risk"]["high_consequence_action_count"], 0)
        self.assert_manual(created, stored, update)

    async def test_valve_toggle_group_is_high_consequence(self):
        created, stored, update = await self.plan_card(
            {"type": "toggle-group", "entities": [VALVE]}
        )
        self.assertEqual(self.changed_at(update, f"{CARD}/entities/0"),
                         {"high_consequence_action"})
        self.assertEqual(update["risk"]["high_consequence_action_count"], 1)
        self.assert_elevated(created, stored, update)

    async def test_malformed_strategy_type_plans_with_manual_review(self):
        configuration = background_dashboard(0)
        created, stored, update = await self.plan(
            configuration,
            [{"operation_id": "add-strategy-view", "operation": "add",
              "path": "/views/-",
              "value": {"title": "Generated", "strategy": {"type": {"description": "x"}}}}],
        )
        self.assertEqual(
            self.changed_at(update, f"/views/{len(configuration['views'])}/strategy"),
            {"unknown_action_semantics"},
        )
        self.assert_manual(created, stored, update)

    async def test_strategy_view_requires_manual_review(self):
        configuration = background_dashboard(0)
        created, stored, update = await self.plan(
            configuration,
            [{"operation_id": "add-strategy-view", "operation": "add",
              "path": "/views/-",
              "value": {"title": "Generated", "strategy": {"type": "custom:synthetic"}}}],
        )
        self.assertEqual(
            self.changed_at(update, f"/views/{len(configuration['views'])}/strategy"),
            {"unknown_action_semantics"},
        )
        self.assert_manual(created, stored, update)

    async def test_header_toggle_is_elevated(self):
        created, stored, update = await self.plan_card(
            {"type": "entities", "title": "Synthetic",
             "entities": ["fan.synthetic_a", "fan.synthetic_b"]}
        )
        self.assertEqual(self.changed_at(update, f"{CARD}/show_header_toggle"),
                         {"toggle"})
        self.assert_elevated(created, stored, update)

    async def test_non_control_shapes_stay_standard(self):
        for card in (
            {"type": "button", "entity": "light.synthetic_lamp"},
            {"type": "entities", "entities": ["fan.synthetic_a"]},
            {"type": "entities", "title": "t", "show_header_toggle": False,
             "entities": ["fan.synthetic_a", "fan.synthetic_b"]},
            {"type": "tile", "entity": VALVE, "icon_tap_action": {"action": "more-info"}},
        ):
            with self.subTest(card=json.dumps(card)):
                await self.fresh()
                created, stored, update = await self.plan_card(card)
                self.assert_standard(created, stored, update)
        await self.fresh()
        created, stored, update = await self.plan(
            background_dashboard(0),
            [{"operation_id": "add-map-view", "operation": "add", "path": "/views/-",
              "value": {"title": "Map", "strategy": {"type": "map"}}}],
        )
        self.assert_standard(created, stored, update)


if __name__ == "__main__":
    unittest.main()
