"""HAMCP-345: dashboard risk breadth (valves, group rows, header toggle,
strategies).

Owner-approved outcome (2026-11-08): dashboard plans classify valve controls
(for example a water main shutoff) as high-consequence actions with
elevated review; entities-card group rows, whose members are not part of
the dashboard configuration, disclose that they cannot be classified at
plan time; controls rendered by dashboard strategies or by the entities-card
header toggle are analyzed or disclosed; the light/switch default-toggle and
display-only count residuals are pinned.

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
HEADER_GROUP_REASON = "header_toggle_group_members_not_established_at_plan_time"
STRATEGY_REASON = "strategy_generated_controls_cannot_be_analyzed"


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

    def test_group_on_non_row_shapes_is_unclassified(self):
        for card in (
            {"type": "button", "entity": GROUP},
            {"type": "tile", "entity": GROUP},
            {"type": "picture-glance", "image": "/local/x.png", "entities": [GROUP]},
            {"type": "toggle-group", "entities": [GROUP]},
            {"type": "entities", "entities": [],
             "footer": {"type": "buttons", "entities": [GROUP]}},
        ):
            with self.subTest(card=json.dumps(card)):
                self.assertEqual(changed(card_risk(card)), [])

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
