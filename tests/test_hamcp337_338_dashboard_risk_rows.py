"""HAMCP-337/338: entities-card row risk and E031 lock-tile planning.

Every governed test here is plan-only: it creates (or is refused) a dashboard
update plan against the offline fake gateway and never approves or applies
it, so no provider write can occur.  The configurations are reconstructed
from the shapes reported in HA admin E031/E032; they are not the live
configurations, which were not accessed.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_dashboard.constants import (  # noqa: E402
    MAX_RISK_EVIDENCE_FINDINGS,
    MAX_RISK_FINDINGS,
)
from ha_mcp_engineering.f3_dashboard.errors import (  # noqa: E402
    PatchValidationError,
    RiskAnalysisError,
)
from ha_mcp_engineering.f3_dashboard.json_codec import engineering_sha256  # noqa: E402
from ha_mcp_engineering.f3_dashboard.models import (  # noqa: E402
    RiskCategory,
    RiskDisposition,
)
from ha_mcp_engineering.f3_dashboard.risk import (  # noqa: E402
    _card_path,
    analyze_dashboard_risk,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from test_dashboard_update_mvp import (  # noqa: E402
    _DashboardGateway,
    _UnusedConfigurationGateway,
    _unrelated_lifecycle_provider_identity,
)


LOCK = "lock.synthetic_front_door"
GLANCE_VIEW = 7
GLANCE_CARDS = f"/views/{GLANCE_VIEW}/sections/0/cards"
STRUCTURAL = re.compile(
    r"^(?:/|(?:/(?:views|sections|cards|badges|entities|features|elements|rows)"
    r"/[0-9]{1,5}|/(?:card|row))+)$"
)


def lock_tile(**extra) -> dict:
    return {
        "type": "tile",
        "entity": LOCK,
        "name": "Synthetic lock",
        "tap_action": {
            "action": "perform-action",
            "perform_action": "lock.lock",
            "target": {"entity_id": LOCK},
        },
        **extra,
    }


def unlock_tile(**extra) -> dict:
    return {
        "type": "tile",
        "entity": LOCK,
        "name": "Synthetic unlock",
        "tap_action": {
            "action": "perform-action",
            "perform_action": "lock.unlock",
            "target": {"entity_id": LOCK},
            "confirmation": {"text": "Unlock the synthetic front door?"},
        },
        **extra,
    }


def toggle_tile(index: int) -> dict:
    return {
        "type": "tile",
        "entity": f"light.synthetic_lamp_{index}",
        "tap_action": {"action": "toggle"},
    }


def background_dashboard(
    existing_findings: int, glance_cards: list | None = None
) -> dict:
    """A sections dashboard whose Glance view is ``/views/7``.

    Views 0-6 hold ``existing_findings`` unchanged more-info tiles, one
    finding each, standing in for an existing administrative dashboard.
    """

    views: list[dict] = []
    per_view, remainder = divmod(existing_findings, GLANCE_VIEW)
    sequence = 0
    for view in range(GLANCE_VIEW):
        cards = []
        for _ in range(per_view + (1 if view < remainder else 0)):
            cards.append(
                {
                    "type": "tile",
                    "entity": f"sensor.synthetic_reading_{sequence}",
                    "tap_action": {"action": "more-info"},
                }
            )
            sequence += 1
        views.append(
            {
                "title": f"Synthetic view {view}",
                "type": "sections",
                "sections": [{"type": "grid", "cards": cards}],
            }
        )
    views.append(
        {
            "title": "Glance",
            "path": "synthetic-glance",
            "type": "sections",
            "sections": [{"type": "grid", "cards": list(glance_cards or [])}],
        }
    )
    return {"title": "Synthetic home admin", "views": views}


def insert_cards(cards: list[dict], *, start: int = 0) -> list[dict]:
    return [
        {
            "operation_id": f"insert-{index}",
            "operation": "add",
            "path": f"{GLANCE_CARDS}/{start + index}",
            "value": card,
        }
        for index, card in enumerate(cards)
    ]


def append_cards(cards: list[dict]) -> list[dict]:
    return [
        {
            "operation_id": f"append-{index}",
            "operation": "add",
            "path": f"{GLANCE_CARDS}/-",
            "value": card,
        }
        for index, card in enumerate(cards)
    ]


class _PlanOnlyHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dashboard = _DashboardGateway(version="8.5.0")
        self.repository = ChangePlanRepository(self.root / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            _UnusedConfigurationGateway(),
            AuditLogger(str(self.root / "audit.jsonl"), "test-access-secret"),
            dashboard_gateway=self.dashboard,
            provider_identity_reader=_unrelated_lifecycle_provider_identity,
        )

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def create(self, configuration: dict, operations: list[dict]):
        self.dashboard.configuration = deepcopy(configuration)
        return await self.service.create_dashboard_update_plan(
            title="Synthetic dashboard change",
            description="Plan-only offline regression.",
            url_path="home-admin-dashboard",
            patch_operations=operations,
            expiration_minutes=30,
        )

    async def plan(self, configuration: dict, operations: list[dict]):
        created = await self.create(configuration, operations)
        self.assertEqual(self.dashboard.write_count, 0)
        self.assertEqual(self.dashboard.best_practice_count, 0)
        stored = self.repository.get(created["plan_id"])
        self.assertIsNotNone(stored)
        return created, stored, stored.proposed_config["dashboard_update"]

    @staticmethod
    def changed(update: dict) -> list[dict]:
        return [
            finding
            for finding in update["risk"]["findings"]
            if finding["introduced_or_changed"]
        ]

    def changed_at(self, update: dict, path: str) -> set[str]:
        digest = engineering_sha256(path)
        return {
            finding["category"]
            for finding in self.changed(update)
            if finding["path_sha256"] == digest
        }

    @staticmethod
    def flags(update: dict) -> set[str]:
        return {
            flag
            for entry in update["semantic_diff"]["entries"]
            for flag in entry["risk_flags"]
        }

    def assert_elevated(self, created: dict, stored, update: dict) -> None:
        self.assertEqual(update["risk"]["disposition"], "elevated_manual_review")
        self.assertFalse(update["risk"]["manual_review_required"])
        self.assertEqual(stored.risk.level.value, "high")
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        self.assertTrue(created["approval_actionable"])

    def assert_standard(self, created: dict, stored, update: dict) -> None:
        self.assertEqual(update["risk"]["disposition"], "standard_review")
        self.assertEqual(stored.risk.level.value, "medium")
        self.assertEqual(created["policy_decision"]["policy_class"], "standard_admin")

    def assert_refusal_creates_no_work(self) -> None:
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.service.task_repository.list(), [])
        self.assertEqual(self.dashboard.write_count, 0)
        self.assertEqual(self.dashboard.best_practice_count, 0)


class EntitiesRowLockProbeTests(_PlanOnlyHarness):
    """HAMCP-338 step 1: is an entities-card lock row a high-consequence control?

    Before this change (base 7038aaea) this probe failed: the bare row
    planned standard_review/medium/standard_admin with no findings, and the
    object row planned the same with one ``entity_display_only`` finding.
    """

    async def test_plan_only_entities_card_lock_row_is_high_consequence(self):
        for label, row in (
            ("bare_entity_id_row", LOCK),
            ("entity_object_row", {"entity": LOCK}),
        ):
            for path in (f"{GLANCE_CARDS}/0", f"{GLANCE_CARDS}/-"):
                with self.subTest(row_shape=label, path=path):
                    created, stored, update = await self.plan(
                        background_dashboard(0),
                        [
                            {
                                "operation_id": "add-entities-card",
                                "operation": "add",
                                "path": path,
                                "value": {"type": "entities", "entities": [row]},
                            }
                        ],
                    )
                    self.assertEqual(
                        self.changed_at(update, f"{GLANCE_CARDS}/0/entities/0"),
                        {"high_consequence_action"},
                    )
                    self.assertEqual(
                        update["risk"]["high_consequence_action_count"], 1
                    )
                    self.assert_elevated(created, stored, update)
                    if not path.endswith("/-"):
                        self.assertEqual(
                            self.flags(update), {"high_consequence_action"}
                        )


class EntitiesRowClassificationTests(unittest.TestCase):
    """HAMCP-338: rows are classified like a tile toggle of the same entity."""

    @staticmethod
    def entities(rows: list, *, card_type: str = "entities") -> dict:
        return {
            "views": [
                {"cards": [{"type": card_type, "title": "Rows", "entities": rows}]}
            ]
        }

    def row_risk(self, row, *, card_type: str = "entities"):
        return analyze_dashboard_risk(
            self.entities([]), self.entities([row], card_type=card_type)
        )

    @staticmethod
    def changed(risk):
        return [item for item in risk.findings if item.introduced_or_changed]

    def tile_toggle_category(self, entity: str) -> RiskCategory:
        risk = analyze_dashboard_risk(
            {"views": [{"cards": []}]},
            {
                "views": [
                    {
                        "cards": [
                            {
                                "type": "tile",
                                "entity": entity,
                                "tap_action": {"action": "toggle"},
                            }
                        ]
                    }
                ]
            },
        )
        (finding,) = [
            item
            for item in self.changed(risk)
            if item.path == "/views/0/cards/0/tap_action"
        ]
        return finding.category

    def test_each_owner_directed_domain_matches_tile_toggle_semantics(self):
        expected = {
            "lock": (RiskCategory.HIGH_CONSEQUENCE, 1),
            "cover": (RiskCategory.HIGH_CONSEQUENCE, 1),
            "alarm_control_panel": (RiskCategory.HIGH_CONSEQUENCE, 1),
            "light": (RiskCategory.TOGGLE, 0),
            "switch": (RiskCategory.TOGGLE, 0),
        }
        for domain, (category, high_count) in expected.items():
            entity = f"{domain}.synthetic_row"
            for label, row in (("bare", entity), ("object", {"entity": entity})):
                with self.subTest(domain=domain, row_shape=label):
                    risk = self.row_risk(row)
                    (finding,) = self.changed(risk)
                    self.assertIs(finding.category, category)
                    self.assertIs(finding.category, self.tile_toggle_category(entity))
                    self.assertEqual(finding.path, "/views/0/cards/0/entities/0")
                    self.assertEqual(finding.entity_domain, domain)
                    self.assertIsNone(finding.action)
                    self.assertIsNone(finding.service)
                    self.assertFalse(finding.confirmation_present)
                    self.assertEqual(
                        finding.reason_code,
                        "entity_row_control_targets_high_consequence_domain"
                        if category is RiskCategory.HIGH_CONSEQUENCE
                        else "entity_row_toggle_control",
                    )
                    self.assertIs(risk.disposition, RiskDisposition.ELEVATED_REVIEW)
                    self.assertFalse(risk.manual_review_required)
                    self.assertEqual(risk.high_consequence_action_count, high_count)

    def test_other_domains_keep_their_previous_classification(self):
        cases = (
            ("sensor.synthetic_row", "bare", ()),
            ("sensor.synthetic_row", "object", ()),
            ("fan.synthetic_row", "bare", ()),
            ("input_boolean.synthetic_row", "object", ()),
            # HAMCP-345 classifies valve rows as high-consequence controls
            # (tests/test_hamcp345_dashboard_risk_breadth.py).
            ("garage_door.synthetic_row", "object", (RiskCategory.DISPLAY_ONLY,)),
        )
        for entity, shape, expected in cases:
            row = entity if shape == "bare" else {"entity": entity}
            with self.subTest(entity=entity, row_shape=shape):
                risk = self.row_risk(row)
                self.assertEqual(
                    tuple(item.category for item in self.changed(risk)), expected
                )
                self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)

    def test_non_entity_and_typed_rows_are_ignored_without_failure(self):
        rows = (
            {"type": "section", "label": "Synthetic section"},
            {"type": "divider"},
            {"type": "weblink", "name": "Synthetic link", "url": "https://example.invalid"},
            {"type": "attribute", "entity": "light.synthetic_row", "attribute": "brightness"},
            {"type": "simple-entity", "entity": "switch.synthetic_row"},
            {"type": "sensor-entity", "entity": "switch.synthetic_row"},
            {"type": "text", "name": "Synthetic text", "text": "Synthetic"},
            {"type": "button", "entity": "fan.synthetic_row"},
            {"type": "buttons", "entities": ["fan.synthetic_row"]},
            {"type": "lock-entity", "entity": "no_domain_separator"},
            {"type": "toggle-entity", "entity": "fan.synthetic_row"},
            {"type": "conditional", "row": {"type": "divider"}},
            {"type": "custom:synthetic-row", "entity": "light.synthetic_row"},
            {"name": "Entity missing"},
            {"entity": None},
            {"entity": 7},
            {"entity": "no_domain_separator"},
            {"entity": ".synthetic_row"},
            None,
            7,
            ["light.synthetic_row"],
            "",
            "not_an_entity",
        )
        for row in rows:
            with self.subTest(row=json.dumps(row)):
                risk = self.row_risk(row)
                self.assertEqual(self.changed(risk), [])
                self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)

    def test_typed_high_consequence_object_row_stays_display_only(self):
        risk = self.row_risk({"type": "simple-entity", "entity": LOCK})
        self.assertEqual(
            [item.category for item in self.changed(risk)],
            [RiskCategory.DISPLAY_ONLY],
        )

    def test_entities_key_on_other_card_types_is_not_classified(self):
        for card_type in ("glance", "custom:synthetic-entities", "map"):
            with self.subTest(card_type=card_type):
                risk = self.row_risk("light.synthetic_row", card_type=card_type)
                self.assertEqual(self.changed(risk), [])

    def test_malformed_entities_member_does_not_crash_or_classify(self):
        for entities in ({"entity": LOCK}, LOCK, None, 3):
            with self.subTest(entities=json.dumps(entities)):
                current = {"views": [{"cards": []}]}
                proposed = {
                    "views": [{"cards": [{"type": "entities", "entities": entities}]}]
                }
                risk = analyze_dashboard_risk(current, proposed)
                self.assertFalse(
                    any(
                        item.reason_code.startswith("entity_row_")
                        for item in risk.findings
                    )
                )

    def test_explicit_row_action_is_reported_beside_the_row_control(self):
        risk = self.row_risk(
            {"entity": "light.synthetic_row", "tap_action": {"action": "more-info"}}
        )
        categories = {item.category for item in self.changed(risk)}
        self.assertEqual(categories, {RiskCategory.TOGGLE, RiskCategory.MORE_INFO})

    def test_object_lock_row_is_not_also_reported_display_only(self):
        risk = self.row_risk({"entity": LOCK, "name": "Synthetic door"})
        self.assertEqual(
            [item.category for item in self.changed(risk)],
            [RiskCategory.HIGH_CONSEQUENCE],
        )

    def test_unchanged_rows_are_not_newly_proposed_risk(self):
        current = self.entities(
            [LOCK, {"entity": "cover.synthetic_row"}, "light.synthetic_row"]
        )
        proposed = deepcopy(current)
        proposed["views"][0]["cards"][0]["title"] = "Renamed rows"
        risk = analyze_dashboard_risk(current, proposed)
        self.assertEqual(self.changed(risk), [])
        self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)
        self.assertEqual(len(risk.findings), 3)

    def test_changing_a_row_entity_to_a_lock_is_detected(self):
        current = self.entities([{"entity": "light.synthetic_row"}])
        proposed = self.entities([{"entity": LOCK}])
        risk = analyze_dashboard_risk(current, proposed)
        self.assertEqual(
            [item.category for item in self.changed(risk)],
            [RiskCategory.HIGH_CONSEQUENCE],
        )
        self.assertEqual(risk.high_consequence_action_count, 1)

    def test_rows_inside_custom_or_conditional_cards_keep_manual_context(self):
        for wrapper in ("custom:synthetic-stack", "conditional"):
            with self.subTest(wrapper=wrapper):
                current = {"views": [{"cards": []}]}
                proposed = {
                    "views": [
                        {
                            "cards": [
                                {
                                    "type": wrapper,
                                    "card": {"type": "entities", "entities": [LOCK]},
                                }
                            ]
                        }
                    ]
                }
                risk = analyze_dashboard_risk(current, proposed)
                (finding,) = self.changed(risk)
                self.assertIs(finding.category, RiskCategory.HIGH_CONSEQUENCE)
                self.assertEqual(finding.path, "/views/0/cards/0/card/entities/0")


TYPED_ROWS = (
    ("lock-entity", LOCK, "entity_row_lock_control"),
    ("cover-entity", "cover.synthetic_garage", "entity_row_cover_control"),
    ("toggle-entity", LOCK, "entity_row_control_targets_high_consequence_domain"),
    (
        "toggle-entity",
        "cover.synthetic_garage",
        "entity_row_control_targets_high_consequence_domain",
    ),
    (
        "toggle-entity",
        "alarm_control_panel.synthetic_home",
        "entity_row_control_targets_high_consequence_domain",
    ),
)


class TypedNativeRowPlanningTests(_PlanOnlyHarness):
    """HAMCP-337 F1: typed native lock and cover rows are high-consequence.

    Before this correction (reviewed head fc88e858) every typed row was
    skipped, so a ``lock-entity`` row planned standard_review/medium with
    one ``entity_display_only`` finding.
    """

    async def test_typed_lock_and_cover_rows_plan_elevated_review(self):
        for row_type, entity, reason in TYPED_ROWS:
            for path in (f"{GLANCE_CARDS}/0", f"{GLANCE_CARDS}/-"):
                with self.subTest(row_type=row_type, entity=entity, path=path):
                    await self.asyncTearDown()
                    await self.asyncSetUp()
                    created, stored, update = await self.plan(
                        background_dashboard(0),
                        [
                            {
                                "operation_id": "add-typed-row",
                                "operation": "add",
                                "path": path,
                                "value": {
                                    "type": "entities",
                                    "entities": [
                                        {"type": row_type, "entity": entity}
                                    ],
                                },
                            }
                        ],
                    )
                    row_path = f"{GLANCE_CARDS}/0/entities/0"
                    self.assertEqual(
                        self.changed_at(update, row_path),
                        {"high_consequence_action"},
                    )
                    (finding,) = self.changed(update)
                    self.assertEqual(finding["reason_code"], reason)
                    self.assertEqual(
                        update["risk"]["high_consequence_action_count"], 1
                    )
                    self.assert_elevated(created, stored, update)
                    if not path.endswith("/-"):
                        self.assertEqual(
                            self.flags(update), {"high_consequence_action"}
                        )


class NativeControlShapeTests(unittest.TestCase):
    """HAMCP-337 F1: every native shape that renders a lock, cover or alarm
    control without an action key is classified like a tile toggle."""

    @staticmethod
    def changed(risk):
        return [item for item in risk.findings if item.introduced_or_changed]

    def card_risk(self, card: dict):
        return analyze_dashboard_risk(
            {"views": [{"cards": []}]}, {"views": [{"cards": [card]}]}
        )

    def assert_single(self, card: dict, path: str, category, reason: str):
        risk = self.card_risk(card)
        self.assertEqual(
            [(item.path, item.category, item.reason_code) for item in self.changed(risk)],
            [(path, category, reason)],
        )
        self.assertIs(risk.disposition, RiskDisposition.ELEVATED_REVIEW)
        self.assertFalse(risk.manual_review_required)
        self.assertEqual(
            risk.high_consequence_action_count,
            1 if category is RiskCategory.HIGH_CONSEQUENCE else 0,
        )
        return risk

    def test_typed_row_matrix_for_the_owner_directed_domains(self):
        cases = []
        for domain, category in (
            ("lock", RiskCategory.HIGH_CONSEQUENCE),
            ("cover", RiskCategory.HIGH_CONSEQUENCE),
            ("alarm_control_panel", RiskCategory.HIGH_CONSEQUENCE),
            ("light", RiskCategory.TOGGLE),
            ("switch", RiskCategory.TOGGLE),
        ):
            reason = (
                "entity_row_control_targets_high_consequence_domain"
                if category is RiskCategory.HIGH_CONSEQUENCE
                else "entity_row_toggle_control"
            )
            for row_type in ("toggle-entity", None, ""):
                cases.append((row_type, f"{domain}.synthetic_row", category, reason))
            cases.append(
                ("lock-entity", f"{domain}.synthetic_row",
                 RiskCategory.HIGH_CONSEQUENCE, "entity_row_lock_control")
            )
            cases.append(
                ("cover-entity", f"{domain}.synthetic_row",
                 RiskCategory.HIGH_CONSEQUENCE, "entity_row_cover_control")
            )
        for row_type, entity, category, reason in cases:
            row = {"type": row_type, "entity": entity}
            with self.subTest(row=json.dumps(row)):
                self.assert_single(
                    {"type": "entities", "entities": [row]},
                    "/views/0/cards/0/entities/0",
                    category,
                    reason,
                )

    def test_conditional_and_entity_filter_rows_are_classified(self):
        conditional = {
            "type": "conditional",
            "conditions": [{"entity": "sun.sun", "state": "below_horizon"}],
            "row": {"type": "lock-entity", "entity": LOCK},
        }
        self.assert_single(
            {"type": "entities", "entities": [conditional]},
            "/views/0/cards/0/entities/0/row",
            RiskCategory.HIGH_CONSEQUENCE,
            "entity_row_lock_control",
        )
        for inner in (None, {"title": "Filtered"}, {"type": "entities"}):
            card = {
                "type": "entity-filter",
                "entities": [LOCK],
                "conditions": [{"state": "unlocked"}],
            }
            if inner is not None:
                card["card"] = inner
            with self.subTest(card=json.dumps(card)):
                self.assert_single(
                    card,
                    "/views/0/cards/0/entities/0",
                    RiskCategory.HIGH_CONSEQUENCE,
                    "entity_row_control_targets_high_consequence_domain",
                )
        risk = self.card_risk(
            {"type": "entity-filter", "entities": [LOCK], "card": {"type": "glance"}}
        )
        self.assertFalse(
            any(item.reason_code.startswith("entity_row_") for item in risk.findings)
        )

    def test_buttons_header_and_footer_toggle_every_chip(self):
        for slot in ("header", "footer"):
            for chip in (LOCK, {"entity": LOCK, "name": "Door"}):
                card = {
                    "type": "entities",
                    "entities": [],
                    slot: {"type": "buttons", "entities": [chip]},
                }
                with self.subTest(slot=slot, chip=json.dumps(chip)):
                    self.assert_single(
                        card,
                        f"/views/0/cards/0/{slot}/entities/0",
                        RiskCategory.HIGH_CONSEQUENCE,
                        "header_footer_button_targets_high_consequence_domain",
                    )
        self.assert_single(
            {
                "type": "entity",
                "entity": "sensor.synthetic_reading",
                "footer": {"type": "buttons", "entities": ["light.synthetic"]},
            },
            "/views/0/cards/0/footer/entities/0",
            RiskCategory.TOGGLE,
            "header_footer_button_toggle_control",
        )
        # A chip with its own action is classified from that action, not as
        # a default toggle.
        risk = self.card_risk(
            {
                "type": "entities",
                "entities": [],
                "footer": {
                    "type": "buttons",
                    "entities": [
                        {"entity": LOCK, "tap_action": {"action": "more-info"}}
                    ],
                },
            }
        )
        self.assertNotIn(
            RiskCategory.HIGH_CONSEQUENCE, {item.category for item in risk.findings}
        )
        self.assertIn(
            ("/views/0/cards/0/footer/entities/0/tap_action", RiskCategory.MORE_INFO),
            {(item.path, item.category) for item in self.changed(risk)},
        )
        # ``buttons`` outside a header/footer slot is not this shape.
        risk = self.card_risk(
            {"type": "entities", "entities": [], "other": {"type": "buttons", "entities": [LOCK]}}
        )
        self.assertEqual(self.changed(risk), [])

    def test_toggle_group_card_targets_each_entity(self):
        # The card sends one service, from the first entity's domain, to
        # every listed entity (hui-toggle-group-card.ts:130-141), so a
        # single-domain list is one finding per entity.
        for entities, category, reason in (
            (["light.synthetic", "light.synthetic_2"], RiskCategory.TOGGLE,
             "toggle_group_toggle_control"),
            ([LOCK, "lock.synthetic_back_door"], RiskCategory.HIGH_CONSEQUENCE,
             "toggle_group_targets_high_consequence_domain"),
        ):
            with self.subTest(entities=entities):
                risk = self.card_risk({"type": "toggle-group", "entities": entities})
                self.assertEqual(
                    [(item.path, item.category, item.reason_code)
                     for item in self.changed(risk)],
                    [
                        (f"/views/0/cards/0/entities/{index}", category, reason)
                        for index in range(len(entities))
                    ],
                )
                self.assertIs(risk.disposition, RiskDisposition.ELEVATED_REVIEW)
        # HAMCP-345 correction (owner ruling 2026-11-10): the lock in a
        # light-first list receives the light service, not a lock service.
        # A list whose domains differ is one disclosed finding for the card.
        risk = self.card_risk(
            {"type": "toggle-group", "entities": ["light.synthetic", LOCK]}
        )
        self.assertEqual(
            [(item.path, item.category, item.reason_code) for item in self.changed(risk)],
            [
                ("/views/0/cards/0/entities", RiskCategory.UNKNOWN,
                 "toggle_group_mixed_domains_receive_first_entity_service"),
            ],
        )
        self.assertIs(risk.disposition, RiskDisposition.MANUAL_REVIEW_REQUIRED)

    def test_area_controls_feature_is_classified_from_its_domains(self):
        cases = (
            (None, RiskCategory.HIGH_CONSEQUENCE),
            (["cover-garage"], RiskCategory.HIGH_CONSEQUENCE),
            (["light", {"entity_id": LOCK}], RiskCategory.HIGH_CONSEQUENCE),
            ([{"entity_id": "alarm_control_panel.synthetic"}], RiskCategory.HIGH_CONSEQUENCE),
            (["light", "switch"], RiskCategory.TOGGLE),
        )
        for controls, category in cases:
            feature = {"type": "area-controls"}
            if controls is not None:
                feature["controls"] = controls
            with self.subTest(controls=json.dumps(controls)):
                self.assert_single(
                    {"type": "area", "area": "synthetic_area", "features": [feature]},
                    "/views/0/cards/0/features/0",
                    category,
                    "area_controls_feature_targets_high_consequence_domain"
                    if category is RiskCategory.HIGH_CONSEQUENCE
                    else "area_controls_feature_toggle_control",
                )
        risk = self.card_risk(
            {
                "type": "area",
                "area": "synthetic_area",
                "features": [{"type": "area-controls", "controls": ["fan"]}],
            }
        )
        self.assertEqual(self.changed(risk), [])

    def test_alarm_panel_card_renders_arm_and_disarm_controls(self):
        risk = self.assert_single(
            {"type": "alarm-panel", "entity": "alarm_control_panel.synthetic_home"},
            "/views/0/cards/0/entity",
            RiskCategory.HIGH_CONSEQUENCE,
            "alarm_panel_card_arm_disarm_controls",
        )
        self.assertNotIn(
            RiskCategory.DISPLAY_ONLY, {item.category for item in risk.findings}
        )
        # Other cards showing the same alarm entity stay display-only.
        risk = self.card_risk(
            {"type": "entity", "entity": "alarm_control_panel.synthetic_home"}
        )
        self.assertEqual(
            [item.category for item in self.changed(risk)], [RiskCategory.DISPLAY_ONLY]
        )

    def test_service_rows_and_elements_are_classified_by_service(self):
        for shape in (
            {"type": "call-service", "name": "Unlock", "action": "lock.unlock",
             "data": {"entity_id": LOCK}},
            {"type": "call-service", "name": "Unlock", "service": "lock.unlock"},
            {"type": "perform-action", "name": "Open", "action": "cover.open_cover"},
        ):
            key = "action" if "action" in shape else "service"
            with self.subTest(shape=json.dumps(shape)):
                self.assert_single(
                    {"type": "entities", "entities": [shape]},
                    f"/views/0/cards/0/entities/0/{key}",
                    RiskCategory.HIGH_CONSEQUENCE,
                    "service_is_high_consequence_action",
                )
        for element_type in ("service-button", "action-button"):
            card = {
                "type": "picture-elements",
                "image": "/local/synthetic.png",
                "elements": [
                    {"type": element_type, "title": "Disarm",
                     "action": "alarm_control_panel.alarm_disarm"}
                ],
            }
            with self.subTest(element_type=element_type):
                self.assert_single(
                    card,
                    "/views/0/cards/0/elements/0/action",
                    RiskCategory.HIGH_CONSEQUENCE,
                    "service_is_high_consequence_action",
                )
        self.assert_single(
            {"type": "entities", "entities": [
                {"type": "call-service", "name": "Ping", "action": "notify.synthetic"}
            ]},
            "/views/0/cards/0/entities/0/action",
            RiskCategory.SERVICE_ACTION,
            "frontend_service_action",
        )

    def test_tile_icon_gestures_are_action_keys(self):
        for key in ("icon_tap_action", "icon_hold_action", "icon_double_tap_action"):
            with self.subTest(key=key):
                risk = self.card_risk(
                    {"type": "tile", "entity": LOCK, key: {"action": "toggle"}}
                )
                self.assertIn(
                    (
                        f"/views/0/cards/0/{key}",
                        RiskCategory.HIGH_CONSEQUENCE,
                        "toggle_targets_high_consequence_domain",
                    ),
                    {
                        (item.path, item.category, item.reason_code)
                        for item in self.changed(risk)
                    },
                )
                self.assertIs(risk.disposition, RiskDisposition.ELEVATED_REVIEW)
                self.assertEqual(risk.high_consequence_action_count, 1)

    def test_display_shapes_remain_display_only_negatives(self):
        for card in (
            {"type": "entities", "entities": [{"type": "simple-entity", "entity": LOCK}]},
            {"type": "entities", "entities": [{"type": "attribute", "entity": LOCK,
                                               "attribute": "battery"}]},
            {"type": "entities", "entities": [{"type": "sensor-entity", "entity": LOCK}]},
            {"type": "glance", "entities": [{"entity": LOCK}]},
        ):
            with self.subTest(card=json.dumps(card)):
                risk = self.card_risk(card)
                self.assertEqual(
                    {item.category for item in self.changed(risk)},
                    {RiskCategory.DISPLAY_ONLY},
                )
                self.assertIs(risk.disposition, RiskDisposition.STANDARD_REVIEW)
                self.assertEqual(risk.high_consequence_action_count, 0)


class E031LockTilePlanningTests(_PlanOnlyHarness):
    """HAMCP-337: every reported rejected shape now plans with its risk.

    The reported dashboard's existing content is unknown.  The base analyzer
    counted every finding in the whole dashboard against the 64-finding
    review bound.  A lock tile yields 3 findings, a confirmed unlock tile 5,
    a toggle tile 1, and ``grid_options`` one more per lock tile, so every
    reported accepted shape is at most 9 new findings and every rejected
    shape at least 10: exactly 55 existing findings fit the whole report.
    Each shape is planned on a 55-finding and a 64-finding background (the
    base analyzer refused both for every shape below).
    """

    BACKGROUNDS = (55, MAX_RISK_FINDINGS)

    def assert_lock_pair(self, update: dict, *, lock: str, unlock: str) -> None:
        self.assertEqual(
            self.changed_at(update, f"{unlock}/tap_action"),
            {"high_consequence_action", "confirmation_protected_action"},
        )
        self.assertEqual(
            self.changed_at(update, f"{lock}/tap_action"),
            {"high_consequence_action"},
        )

    async def test_both_lock_tiles_and_two_or_more_toggles_plan_elevated(self):
        for background in self.BACKGROUNDS:
            for toggles in (2, 3, 7):
                with self.subTest(background=background, toggles=toggles):
                    cards = [unlock_tile(), lock_tile()] + [
                        toggle_tile(index) for index in range(toggles)
                    ]
                    created, stored, update = await self.plan(
                        background_dashboard(background), insert_cards(cards)
                    )
                    self.assert_elevated(created, stored, update)
                    self.assert_lock_pair(
                        update, lock=f"{GLANCE_CARDS}/1", unlock=f"{GLANCE_CARDS}/0"
                    )
                    for index in range(toggles):
                        self.assertEqual(
                            self.changed_at(
                                update, f"{GLANCE_CARDS}/{index + 2}/tap_action"
                            ),
                            {"toggle"},
                        )
                    self.assertEqual(update["risk"]["high_consequence_action_count"], 2)
                    self.assertEqual(len(self.changed(update)), 8 + toggles)
                    self.assertEqual(
                        len(update["risk"]["findings"]), background + 8 + toggles
                    )
                    self.assertEqual(
                        self.flags(update),
                        {
                            "high_consequence_action",
                            "confirmation_protected_action",
                            "toggle",
                            "entity_display_only",
                        },
                    )

    async def test_both_lock_tiles_with_grid_options_plan_elevated(self):
        for background in self.BACKGROUNDS:
            with self.subTest(background=background):
                grid = {"grid_options": {"columns": 12}}
                created, stored, update = await self.plan(
                    background_dashboard(background),
                    insert_cards([unlock_tile(**grid), lock_tile(**grid)]),
                )
                self.assert_elevated(created, stored, update)
                self.assert_lock_pair(
                    update, lock=f"{GLANCE_CARDS}/1", unlock=f"{GLANCE_CARDS}/0"
                )
                self.assertEqual(len(self.changed(update)), 10)

    async def test_toggles_after_applied_lock_tiles_plan_elevated(self):
        for background in self.BACKGROUNDS:
            existing = background_dashboard(
                background, [unlock_tile(), lock_tile()]
            )
            for label, operations, count in (
                # The compiler refuses duplicate "-" paths, so a multi-card
                # append addresses the trailing indexes explicitly.
                (
                    "append_2",
                    insert_cards([toggle_tile(0), toggle_tile(1)], start=2),
                    2,
                ),
                (
                    "append_3",
                    insert_cards([toggle_tile(index) for index in range(3)], start=2),
                    3,
                ),
                (
                    "insert_7",
                    insert_cards([toggle_tile(index) for index in range(7)], start=2),
                    7,
                ),
            ):
                with self.subTest(background=background, shape=label):
                    created, stored, update = await self.plan(existing, operations)
                    self.assert_elevated(created, stored, update)
                    self.assertEqual(
                        {item["category"] for item in self.changed(update)},
                        {"toggle"},
                    )
                    self.assertEqual(len(self.changed(update)), count)
                    # The applied lock tiles are unchanged context.
                    self.assertEqual(update["risk"]["high_consequence_action_count"], 0)
                    self.assertEqual(
                        len(update["risk"]["findings"]), background + 8 + count
                    )

    async def test_seven_toggles_inserted_before_applied_lock_tiles(self):
        # Finding identity includes the path, so lock tiles shifted by the
        # insert are conservatively re-reported as changed risk.
        for background in self.BACKGROUNDS:
            with self.subTest(background=background):
                existing = background_dashboard(
                    background, [unlock_tile(), lock_tile()]
                )
                created, stored, update = await self.plan(
                    existing, insert_cards([toggle_tile(i) for i in range(7)])
                )
                self.assert_elevated(created, stored, update)
                self.assert_lock_pair(
                    update, lock=f"{GLANCE_CARDS}/8", unlock=f"{GLANCE_CARDS}/7"
                )
                self.assertEqual(len(self.changed(update)), 15)
                self.assertEqual(len(update["risk"]["findings"]), background + 15)


class E031AcceptedShapeTests(_PlanOnlyHarness):
    """Every reported accepted shape keeps its classification."""

    async def test_reported_accepted_shapes_keep_their_classification(self):
        cases = (
            ("toggles_only", [toggle_tile(0), toggle_tile(1)], {"toggle"}, 0),
            (
                "unlock_only",
                [unlock_tile()],
                {
                    "high_consequence_action",
                    "confirmation_protected_action",
                    "entity_display_only",
                },
                1,
            ),
            (
                "lock_only",
                [lock_tile()],
                {"high_consequence_action", "entity_display_only"},
                1,
            ),
            (
                "both_lock_tiles",
                [unlock_tile(), lock_tile()],
                {
                    "high_consequence_action",
                    "confirmation_protected_action",
                    "entity_display_only",
                },
                2,
            ),
            (
                "lock_and_one_toggle",
                [lock_tile(), toggle_tile(0)],
                {"high_consequence_action", "toggle", "entity_display_only"},
                1,
            ),
            (
                "lock_and_two_toggles",
                [lock_tile(), toggle_tile(0), toggle_tile(1)],
                {"high_consequence_action", "toggle", "entity_display_only"},
                1,
            ),
            (
                "unlock_and_one_toggle",
                [unlock_tile(), toggle_tile(0)],
                {
                    "high_consequence_action",
                    "confirmation_protected_action",
                    "toggle",
                    "entity_display_only",
                },
                1,
            ),
        )
        for label, cards, categories, high_count in cases:
            with self.subTest(shape=label):
                created, stored, update = await self.plan(
                    background_dashboard(55), insert_cards(cards)
                )
                self.assert_elevated(created, stored, update)
                self.assertEqual(
                    {item["category"] for item in self.changed(update)}, categories
                )
                self.assertEqual(
                    update["risk"]["high_consequence_action_count"], high_count
                )

    async def test_one_toggle_after_both_lock_tiles_is_toggle_only(self):
        # 55 + 8 existing + 1 = 64: also accepted by the base analyzer.
        existing = background_dashboard(55, [unlock_tile(), lock_tile()])
        created, stored, update = await self.plan(
            existing, append_cards([toggle_tile(0)])
        )
        self.assertEqual(len(update["risk"]["findings"]), MAX_RISK_FINDINGS)
        self.assert_elevated(created, stored, update)
        self.assertEqual(
            [item["category"] for item in self.changed(update)], ["toggle"]
        )

    async def test_entities_card_with_seven_lights_is_now_toggle(self):
        # HAMCP-338 (E032): previously standard_review/medium with no flags.
        rows = [f"light.synthetic_lamp_{index}" for index in range(7)]
        created, stored, update = await self.plan(
            background_dashboard(55),
            insert_cards([{"type": "entities", "entities": rows}]),
        )
        self.assert_elevated(created, stored, update)
        self.assertEqual(
            [item["category"] for item in self.changed(update)], ["toggle"] * 7
        )
        self.assertEqual(self.flags(update), {"toggle"})
        self.assertEqual(update["risk"]["high_consequence_action_count"], 0)

    async def test_layout_only_change_on_a_large_dashboard_stays_standard(self):
        existing = background_dashboard(
            MAX_RISK_EVIDENCE_FINDINGS - 8, [unlock_tile(), lock_tile()]
        )
        created, stored, update = await self.plan(
            existing,
            [
                {
                    "operation_id": "rename",
                    "operation": "replace",
                    "path": f"/views/{GLANCE_VIEW}/title",
                    "value": "Glance renamed",
                }
            ],
        )
        self.assert_standard(created, stored, update)
        self.assertEqual(self.changed(update), [])
        self.assertEqual(len(update["risk"]["findings"]), MAX_RISK_EVIDENCE_FINDINGS)


class RiskAnalysisRefusalTests(_PlanOnlyHarness):
    """HAMCP-337: genuine refusals name the card path and rule, bounded."""

    def assert_non_echoing(self, details: dict) -> None:
        public = json.dumps(details, sort_keys=True)
        audit = self.root / "audit.jsonl"
        public += audit.read_text() if audit.exists() else ""
        for forbidden in (
            "synthetic_lamp",
            "synthetic_reading",
            "synthetic_front_door",
            "light.",
            "sensor.",
            "lock.",
            "Synthetic",
            "Glance",
            "toggle",
            "tile",
        ):
            self.assertNotIn(forbidden, public)
        self.assertLessEqual(len(json.dumps(details)), 512)
        self.assertRegex(details["card_path"], STRUCTURAL)

    async def test_changed_findings_over_review_bound_name_card_and_rule(self):
        rows = [
            f"light.synthetic_lamp_{index}" for index in range(MAX_RISK_FINDINGS + 1)
        ]
        original = background_dashboard(4)
        with self.assertRaises(GovernanceError) as caught:
            await self.create(
                original, insert_cards([{"type": "entities", "entities": rows}])
            )
        self.assertEqual(caught.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        self.assertEqual(
            caught.exception.details,
            {
                "reason": "dashboard_risk_analysis_failed",
                "dashboard_error_code": "dashboard_risk_analysis_failed",
                "constraint": "changed_risk_findings",
                "stage": "risk_analysis",
                "observed": MAX_RISK_FINDINGS + 1,
                "limit": MAX_RISK_FINDINGS,
                "card_path": f"{GLANCE_CARDS}/0/entities/{MAX_RISK_FINDINGS}",
            },
        )
        self.assert_non_echoing(caught.exception.details)
        self.assert_refusal_creates_no_work()
        self.assertEqual(self.dashboard.configuration, original)

    async def test_digit_only_mapping_key_is_not_echoed_as_an_index(self):
        """HAMCP-337 F2: Codex's reproduction through real plan creation."""

        rows = [
            f"light.synthetic_lamp_{index}" for index in range(MAX_RISK_FINDINGS + 1)
        ]
        for container in ("cards", "entities", "views", "rows"):
            for wrapper_key, expected in (
                (container, f"{GLANCE_CARDS}/0"),
                ("card", f"{GLANCE_CARDS}/0/card"),
            ):
                with self.subTest(container=container, wrapper_key=wrapper_key):
                    await self.asyncTearDown()
                    await self.asyncSetUp()
                    inner = {"type": "entities", "entities": rows}
                    wrapper = {
                        "type": "custom:synthetic-wrapper",
                        wrapper_key: (
                            {"12345": inner}
                            if wrapper_key == container
                            else {container: {"12345": inner}}
                        ),
                    }
                    original = background_dashboard(4)
                    with self.assertRaises(GovernanceError) as caught:
                        await self.create(original, insert_cards([wrapper]))
                    details = caught.exception.details
                    self.assertEqual(details["constraint"], "changed_risk_findings")
                    self.assertEqual(details["observed"], MAX_RISK_FINDINGS + 1)
                    self.assertEqual(details["card_path"], expected)
                    self.assert_non_echoing(details)
                    audit = self.root / "audit.jsonl"
                    public = json.dumps(details) + (
                        audit.read_text() if audit.exists() else ""
                    )
                    self.assertNotIn("12345", public)
                    self.assert_refusal_creates_no_work()
                    self.assertEqual(self.dashboard.configuration, original)

    async def test_review_bound_is_inclusive(self):
        rows = [f"light.synthetic_lamp_{index}" for index in range(MAX_RISK_FINDINGS)]
        created, stored, update = await self.plan(
            background_dashboard(4),
            insert_cards([{"type": "entities", "entities": rows}]),
        )
        self.assertEqual(len(self.changed(update)), MAX_RISK_FINDINGS)
        self.assert_elevated(created, stored, update)

    async def test_total_evidence_bound_names_card_and_rule(self):
        original = background_dashboard(MAX_RISK_EVIDENCE_FINDINGS)
        with self.assertRaises(GovernanceError) as caught:
            await self.create(original, insert_cards([toggle_tile(0)]))
        self.assertEqual(caught.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        self.assertEqual(
            caught.exception.details,
            {
                "reason": "dashboard_risk_analysis_failed",
                "dashboard_error_code": "dashboard_risk_analysis_failed",
                "constraint": "risk_evidence_findings",
                "stage": "risk_analysis",
                "observed": MAX_RISK_EVIDENCE_FINDINGS + 1,
                "limit": MAX_RISK_EVIDENCE_FINDINGS,
                "card_path": f"{GLANCE_CARDS}/0",
            },
        )
        self.assert_non_echoing(caught.exception.details)
        self.assert_refusal_creates_no_work()

    async def test_total_evidence_bound_is_inclusive_and_artifact_fits(self):
        created, stored, update = await self.plan(
            background_dashboard(MAX_RISK_EVIDENCE_FINDINGS - 1),
            insert_cards([toggle_tile(0)]),
        )
        self.assertEqual(len(update["risk"]["findings"]), MAX_RISK_EVIDENCE_FINDINGS)
        self.assert_elevated(created, stored, update)
        self.assertIsNotNone(self.service.dashboard_artifacts.get(created["plan_id"]))

    async def test_non_risk_dashboard_failures_keep_legacy_details(self):
        with self.assertRaises(GovernanceError) as caught:
            await self.create(
                background_dashboard(0),
                [
                    {
                        "operation_id": "missing",
                        "operation": "remove",
                        "path": "/synthetic-private-missing",
                    }
                ],
            )
        self.assertEqual(
            caught.exception.details, {"reason": "dashboard_patch_compilation_failed"}
        )
        self.assert_refusal_creates_no_work()


class StructuralCardPathTests(unittest.TestCase):
    """HAMCP-337 F2: the path walk proves each index is a list position."""

    @staticmethod
    def document() -> dict:
        return {
            "title": "Synthetic",
            "views": [
                {"cards": []},
                {
                    "cards": [
                        {"type": "tile"},
                        {
                            "type": "conditional",
                            "card": {"type": "entities", "entities": ["a", "b", "c", "d", "e"]},
                        },
                        {"type": "custom:synthetic", "light.synthetic_lamp": {"cards": [{}, {}]}},
                        {"type": "custom:synthetic", "Synthetic Name": {}},
                    ]
                },
            ],
        }

    def test_card_path_keeps_only_proven_list_positions(self):
        document = self.document()
        cases = (
            ((), "/"),
            (("title",), "/"),
            (("views", "1", "cards", "0", "tap_action"), "/views/1/cards/0"),
            (("views", "1", "cards", "1", "card", "entities", "4"),
             "/views/1/cards/1/card/entities/4"),
            (("views", "1", "cards", "2", "light.synthetic_lamp", "cards", "1"),
             "/views/1/cards/2"),
            (("views", "1", "cards", "3", "Synthetic Name"), "/views/1/cards/3"),
            (("views", "1", "cards", "-1"), "/views/1"),
            (("views", "1", "cards", "٣"), "/views/1"),
            (("views", "1", "cards", "01"), "/views/1"),
            (("views", "1", "cards", "4"), "/views/1"),
            (("views", "123456"), "/"),
            (("synthetic_key", "views", "0"), "/"),
            (("views",), "/"),
        )
        for tokens, expected in cases:
            with self.subTest(tokens=tokens):
                self.assertEqual(_card_path(document, tokens), expected)
                self.assertRegex(_card_path(document, tokens), STRUCTURAL)

    def test_digit_only_mapping_keys_never_appear_as_indexes(self):
        for container in (
            "views", "sections", "cards", "badges",
            "entities", "features", "elements", "rows",
        ):
            for key in ("12345", "0", "7", "00042"):
                with self.subTest(container=container, key=key):
                    document = {
                        "views": [
                            {"cards": [{"type": "custom:synthetic", container: {key: {}}}]}
                        ]
                    }
                    tokens = ("views", "0", "cards", "0", container, key, "tap_action")
                    self.assertEqual(_card_path(document, tokens), "/views/0/cards/0")
                    root = {container: {key: {}}}
                    self.assertEqual(_card_path(root, (container, key)), "/")

    def test_wrappers_are_kept_only_for_object_values(self):
        for wrapper in ("card", "row"):
            for value, expected in (
                ({"12345": {}}, f"/views/0/cards/0/{wrapper}"),
                ([{}], "/views/0/cards/0"),
                ("synthetic", "/views/0/cards/0"),
            ):
                with self.subTest(wrapper=wrapper, value=json.dumps(value)):
                    document = {"views": [{"cards": [{wrapper: value}]}]}
                    tokens = ("views", "0", "cards", "0", wrapper, "12345", "0")
                    self.assertEqual(_card_path(document, tokens), expected)

    def test_unicode_and_escaped_mapping_keys_stop_the_walk(self):
        for key in ("~1", "a/b", "~0", "٣", "１２", "12\u0033", "12 ", " 12", ""):
            with self.subTest(key=key):
                document = {"views": [{"cards": {key: {"entities": [{}]}}}]}
                tokens = ("views", "0", "cards", key, "entities", "0")
                self.assertEqual(_card_path(document, tokens), "/views/0")

    def test_card_path_is_bounded(self):
        document: dict = {}
        cursor = document
        for _ in range(40):
            cursor["cards"] = [{} for _ in range(3)]
            cursor = cursor["cards"][2]
        path = _card_path(document, ("cards", "2") * 40)
        self.assertEqual(path, "/cards/2" * 8)
        self.assertLessEqual(len(path), 160)

    def test_error_rejects_non_structural_card_path(self):
        for value in (
            "/views/0/cards/light.synthetic_lamp",
            "/views/abc",
            "views/0",
            "/views/0\n",
            "/secrets/0",
            "/views/123456",
            "/cards/1" * 30,
            "",
            7,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    RiskAnalysisError(card_path=value)  # type: ignore[arg-type]

    def test_error_details_include_only_bounded_fields(self):
        error = RiskAnalysisError(
            "message text is never projected",
            constraint="changed_risk_findings",
            stage="risk_analysis",
            observed=65,
            limit=64,
            card_path="/views/7/sections/0/cards/3",
        )
        self.assertEqual(
            error.diagnostic_details(),
            {
                "reason": "dashboard_risk_analysis_failed",
                "dashboard_error_code": "dashboard_risk_analysis_failed",
                "constraint": "changed_risk_findings",
                "stage": "risk_analysis",
                "observed": 65,
                "limit": 64,
                "card_path": "/views/7/sections/0/cards/3",
            },
        )
        self.assertEqual(
            RiskAnalysisError().diagnostic_details(),
            {
                "reason": "dashboard_risk_analysis_failed",
                "dashboard_error_code": "dashboard_risk_analysis_failed",
            },
        )


class FailClosedPreservationTests(unittest.TestCase):
    def test_non_json_and_unbounded_input_still_fail_closed(self):
        current = {"views": []}
        deep: dict = {}
        cursor = deep
        for _ in range(60):
            cursor["cards"] = [{}]
            cursor = cursor["cards"][0]
        for proposed in (
            {"views": [{"cards": [{"type": "entities", "entities": [float("nan")]}]}]},
            {"views": [{"cards": [{"type": "entities", "entities": [object()]}]}]},
            deep,
        ):
            with self.assertRaises(PatchValidationError):
                analyze_dashboard_risk(current, proposed)

    def test_non_object_action_still_requires_manual_review(self):
        risk = analyze_dashboard_risk(
            {"views": [{"cards": []}]},
            {
                "views": [
                    {
                        "cards": [
                            {
                                "type": "entities",
                                "entities": [
                                    {"entity": "light.synthetic_row", "tap_action": "toggle"}
                                ],
                            }
                        ]
                    }
                ]
            },
        )
        self.assertTrue(risk.manual_review_required)
        self.assertIs(risk.disposition, RiskDisposition.MANUAL_REVIEW_REQUIRED)



HIGH_ENTITIES = (
    LOCK,
    "cover.synthetic_garage",
    "alarm_control_panel.synthetic_home",
)
CARD = f"{GLANCE_CARDS}/0"


class EffectiveDefaultActionTests(_PlanOnlyHarness):
    """HAMCP-337 F1 residual (Codex delta review of 0397095a).

    Each shape follows the pinned frontend's effective default and fallback
    rules (frontend a67b691a, pinned by Core 2026.11.0) instead of a
    key-presence approximation.  At 0397095a every positive below planned
    with high count 0.
    """

    async def plan_card(self, card: dict):
        return await self.plan(background_dashboard(0), insert_cards([card]))

    async def assert_high_at(self, card: dict, path: str) -> None:
        created, stored, update = await self.plan_card(card)
        self.assertIn("high_consequence_action", self.changed_at(update, path))
        self.assertGreaterEqual(update["risk"]["high_consequence_action_count"], 1)
        self.assert_elevated(created, stored, update)
        self.assertIn("high_consequence_action", self.flags(update))

    async def assert_no_high(self, card: dict) -> dict:
        created, stored, update = await self.plan_card(card)
        self.assertEqual(update["risk"]["high_consequence_action_count"], 0)
        self.assertNotIn(
            "high_consequence_action",
            {finding["category"] for finding in update["risk"]["findings"]},
        )
        return update

    async def fresh(self) -> None:
        await self.asyncTearDown()
        await self.asyncSetUp()

    async def test_header_footer_hold_or_double_tap_keeps_default_tap_toggle(self):
        # hui-buttons-header-footer.ts merges {tap_action: toggle,
        # hold_action: more-info, ...chip}: only a chip's own tap_action
        # replaces the default tap.
        overrides = (
            {"hold_action": {"action": "more-info"}},
            {"double_tap_action": {"action": "none"}},
            {"hold_action": {"action": "none"}, "double_tap_action": {"action": "more-info"}},
        )
        for slot in ("header", "footer"):
            for entity in HIGH_ENTITIES:
                for extra in overrides:
                    with self.subTest(slot=slot, entity=entity, extra=json.dumps(extra)):
                        await self.fresh()
                        await self.assert_high_at(
                            {
                                "type": "entities",
                                "entities": [],
                                slot: {
                                    "type": "buttons",
                                    "entities": [{"entity": entity, **extra}],
                                },
                            },
                            f"{CARD}/{slot}/entities/0",
                        )

    async def test_header_footer_explicit_tap_override_is_preserved(self):
        for tap in ({"action": "more-info"}, {"action": "none"}):
            with self.subTest(tap=json.dumps(tap)):
                await self.fresh()
                update = await self.assert_no_high(
                    {
                        "type": "entities",
                        "entities": [],
                        "footer": {
                            "type": "buttons",
                            "entities": [
                                {
                                    "entity": LOCK,
                                    "tap_action": tap,
                                    "hold_action": {"action": "more-info"},
                                }
                            ],
                        },
                    }
                )
                self.assertEqual(update["risk"]["disposition"], "standard_review")

    async def test_service_row_uses_the_falsy_action_fallback(self):
        # hui-call-service-row.ts: perform_action = action || service.
        for unused in (None, "", False, 0):
            with self.subTest(action=json.dumps(unused)):
                await self.fresh()
                await self.assert_high_at(
                    {
                        "type": "entities",
                        "entities": [
                            {
                                "type": "call-service",
                                "name": "Synthetic unlock",
                                "action": unused,
                                "service": "lock.unlock",
                            }
                        ],
                    },
                    f"{CARD}/entities/0/service",
                )

    async def test_service_row_explicit_tap_override_is_preserved(self):
        # The row's own tap_action is spread over the synthesized one.
        for tap in ({"action": "none"}, {"action": "more-info"}):
            with self.subTest(tap=json.dumps(tap)):
                await self.fresh()
                await self.assert_no_high(
                    {
                        "type": "entities",
                        "entities": [
                            {
                                "type": "call-service",
                                "name": "Synthetic unlock",
                                "action": "lock.unlock",
                                "tap_action": tap,
                            }
                        ],
                    }
                )
        # A service row naming no service renders an error row.
        await self.fresh()
        created, stored, update = await self.plan_card(
            {
                "type": "entities",
                "entities": [
                    {"type": "call-service", "name": "Nothing", "action": "", "service": None}
                ],
            }
        )
        self.assertEqual(self.changed(update), [])
        self.assert_standard(created, stored, update)

    async def test_service_element_uses_the_nullish_action_fallback(self):
        # hui-service-button-element.ts: action ?? service.
        for element_type in ("service-button", "action-button"):
            with self.subTest(element_type=element_type):
                await self.fresh()
                await self.assert_high_at(
                    {
                        "type": "picture-elements",
                        "image": "/local/synthetic.png",
                        "elements": [
                            {
                                "type": element_type,
                                "title": "Unlock",
                                "action": None,
                                "service": "lock.unlock",
                            }
                        ],
                    },
                    f"{CARD}/elements/0/service",
                )
                # An empty action is selected (not nullish) and names no
                # domain, so the element renders an error, not the service.
                await self.fresh()
                await self.assert_no_high(
                    {
                        "type": "picture-elements",
                        "image": "/local/synthetic.png",
                        "elements": [
                            {
                                "type": element_type,
                                "title": "Unlock",
                                "action": "",
                                "service": "lock.unlock",
                            }
                        ],
                    }
                )

    async def test_area_control_strings_that_are_not_domains_are_entities(self):
        # hui-area-controls-card-feature.ts _normalizeControl: a string
        # outside AREA_CONTROL_DOMAINS is an entity ID.
        for entity in ("cover.synthetic_garage", LOCK, "alarm_control_panel.synthetic"):
            for control in (entity, {"entity_id": entity}):
                with self.subTest(control=json.dumps(control)):
                    await self.fresh()
                    await self.assert_high_at(
                        {
                            "type": "area",
                            "area": "synthetic_room_a",
                            "features": [{"type": "area-controls", "controls": [control]}],
                        },
                        f"{CARD}/features/0",
                    )

    async def test_falsy_area_controls_use_the_default_domains(self):
        # this._config?.controls || DEFAULT_AREA_CONTROLS
        for controls in (False, 0, ""):
            with self.subTest(controls=json.dumps(controls)):
                await self.fresh()
                await self.assert_high_at(
                    {
                        "type": "area",
                        "area": "synthetic_room_a",
                        "features": [{"type": "area-controls", "controls": controls}],
                    },
                    f"{CARD}/features/0",
                )
        # An empty list is truthy: no control is rendered.
        await self.fresh()
        await self.assert_no_high(
            {
                "type": "area",
                "area": "synthetic_room_a",
                "features": [{"type": "area-controls", "controls": []}],
            }
        )

    async def test_falsy_row_type_uses_the_domain_default_row(self):
        # create-element-base.ts: !config.type && config.entity selects the
        # domain row.
        for row_type in (None, "", False, 0):
            for entity in HIGH_ENTITIES:
                with self.subTest(row_type=json.dumps(row_type), entity=entity):
                    await self.fresh()
                    await self.assert_high_at(
                        {
                            "type": "entities",
                            "entities": [{"type": row_type, "entity": entity}],
                        },
                        f"{CARD}/entities/0",
                    )

    async def test_entity_filter_inner_card_type_follows_the_spread_default(self):
        # hui-entity-filter-card.ts: {type: "entities", ...config.card}.
        for inner in (None, {}, [], 5, True, "synthetic"):
            with self.subTest(card=json.dumps(inner)):
                await self.fresh()
                card = {
                    "type": "entity-filter",
                    "entities": [LOCK],
                    "state_filter": ["unlocked"],
                }
                if inner is not None:
                    card["card"] = inner
                await self.assert_high_at(card, f"{CARD}/entities/0")
        for inner in ({"type": None}, {"type": "glance"}):
            with self.subTest(card=json.dumps(inner)):
                await self.fresh()
                await self.assert_no_high(
                    {
                        "type": "entity-filter",
                        "entities": [LOCK],
                        "state_filter": ["unlocked"],
                        "card": inner,
                    }
                )


def area_card(**extra) -> dict:
    return {
        "type": "area",
        "area": "synthetic_room_a",
        "name": "Synthetic room",
        "features": [{"type": "area-controls", "controls": ["cover-garage"]}],
        **extra,
    }


class AreaControlContextIdentityTests(_PlanOnlyHarness):
    """HAMCP-337 D1: an area-controls feature dispatches to the entities of
    its card's area minus ``exclude_entities`` (hui-area-card.ts feature
    context), so a change of either is a changed high-consequence control.
    At 0397095a retargeting planned medium/standard_admin with the stored
    finding marked unchanged."""

    FEATURE = f"{CARD}/features/0"

    async def plan_change(self, card: dict, operations: list[dict]):
        return await self.plan(background_dashboard(0, [card]), operations)

    def assert_changed_high(self, created, stored, update) -> None:
        self.assertEqual(self.changed_at(update, self.FEATURE), {"high_consequence_action"})
        self.assertEqual(update["risk"]["high_consequence_action_count"], 1)
        self.assert_elevated(created, stored, update)

    async def test_area_retarget_is_a_changed_high_consequence_control(self):
        created, stored, update = await self.plan_change(
            area_card(),
            [
                {
                    "operation_id": "retarget",
                    "operation": "replace",
                    "path": f"{CARD}/area",
                    "value": "synthetic_room_b",
                }
            ],
        )
        self.assert_changed_high(created, stored, update)
        # The changed finding is reported at the feature it binds; diff-entry
        # flags keep the existing path-overlap rule (as for a tile whose
        # ``entity`` is retargeted under an unchanged ``tap_action``).
        self.assertEqual(self.flags(update), set())

    async def test_exclusion_changes_are_changed_high_consequence_controls(self):
        cases = (
            (area_card(), "add", ["cover.synthetic_garage"]),
            (area_card(exclude_entities=["cover.synthetic_garage"]), "remove", None),
            (area_card(exclude_entities=["cover.synthetic_garage"]), "replace", []),
        )
        for card, operation, value in cases:
            with self.subTest(operation=operation):
                await self.asyncTearDown()
                await self.asyncSetUp()
                patch = {
                    "operation_id": "exclusion",
                    "operation": operation,
                    "path": f"{CARD}/exclude_entities",
                }
                if value is not None:
                    patch["value"] = value
                created, stored, update = await self.plan_change(card, [patch])
                self.assert_changed_high(created, stored, update)

    async def test_unrelated_edits_leave_the_control_unchanged(self):
        for path, value in (
            (f"{CARD}/name", "Renamed synthetic room"),
            ("/title", "Renamed synthetic dashboard"),
        ):
            with self.subTest(path=path):
                await self.asyncTearDown()
                await self.asyncSetUp()
                created, stored, update = await self.plan_change(
                    area_card(),
                    [
                        {
                            "operation_id": "rename",
                            "operation": "replace",
                            "path": path,
                            "value": value,
                        }
                    ],
                )
                self.assertEqual(self.changed(update), [])
                self.assertEqual(update["risk"]["high_consequence_action_count"], 0)
                self.assert_standard(created, stored, update)
                self.assertIn(
                    "high_consequence_action",
                    {finding["category"] for finding in update["risk"]["findings"]},
                )


def custom_card(schema: dict) -> dict:
    return {
        "type": "custom:synthetic-card",
        "entity": "sensor.synthetic_reading",
        "schema": schema,
    }


class NestedTypeDataPlanningTests(_PlanOnlyHarness):
    """HAMCP-337 D2 regression: dashboard data is arbitrary JSON, so a
    nested non-string ``type`` must not turn analyzable input into
    provider unavailability.  At 0397095a each existing configuration
    below refused an unrelated title edit with
    ``dashboard_provider_identity_unavailable``; at 50e127e5 it planned."""

    ODD_TYPES = ({"description": "synthetic"}, ["synthetic"], 5, True)

    async def plan_or_fail(self, current: dict, operations: list[dict]):
        # A refusal or crash must fail by assertion, not error the test.
        try:
            return await self.plan(current, operations)
        except GovernanceError as error:
            self.fail(f"analyzable dashboard data was refused: {error.code} {error.details}")

    def risk_or_fail(self, current: dict, proposed: dict):
        try:
            return analyze_dashboard_risk(current, proposed)
        except Exception as error:  # noqa: BLE001 - the regression is any crash
            self.fail(f"analyzable dashboard data crashed: {type(error).__name__}: {error}")

    def configurations(self):
        for odd in self.ODD_TYPES:
            yield "custom schema", custom_card({"type": odd})
            yield "row", {"type": "entities", "entities": [{"type": odd, "entity": LOCK}]}
            yield "conditional row", {
                "type": "entities",
                "entities": [
                    {"type": "conditional", "conditions": [], "row": {"type": odd, "entity": LOCK}}
                ],
            }
            yield "entity-filter card", {
                "type": "entity-filter",
                "entities": [LOCK],
                "state_filter": ["unlocked"],
                "card": {"type": odd},
            }
            yield "element", {
                "type": "picture-elements",
                "image": "/local/synthetic.png",
                "elements": [{"type": odd, "action": "lock.unlock"}],
            }
            yield "feature", {
                "type": "area",
                "area": "synthetic_room_a",
                "features": [{"type": odd, "controls": ["cover-garage"]}],
            }
            yield "header chip", {
                "type": "entities",
                "entities": [],
                "header": {"type": odd, "entities": [LOCK]},
            }
            yield "action type", {
                "type": "tile",
                "entity": "sensor.synthetic_reading",
                "tap_action": {"action": odd},
            }
            yield "area control", {
                "type": "area",
                "area": "synthetic_room_a",
                "features": [{"type": "area-controls", "controls": [odd]}],
            }

    async def test_existing_nested_type_data_still_plans_unrelated_edits(self):
        for label, card in self.configurations():
            with self.subTest(label=label, card=json.dumps(card)):
                await self.asyncTearDown()
                await self.asyncSetUp()
                created, stored, update = await self.plan_or_fail(
                    background_dashboard(0, [card]),
                    [
                        {
                            "operation_id": "retitle",
                            "operation": "replace",
                            "path": "/title",
                            "value": "Renamed synthetic dashboard",
                        }
                    ],
                )
                self.assertEqual(self.changed(update), [])
                self.assert_standard(created, stored, update)

    async def test_new_nested_type_data_plans_with_its_own_review(self):
        for label, card in self.configurations():
            with self.subTest(label=label, card=json.dumps(card)):
                await self.asyncTearDown()
                await self.asyncSetUp()
                created, stored, update = await self.plan_or_fail(
                    background_dashboard(0), insert_cards([card])
                )
                self.assertIn(
                    update["risk"]["disposition"],
                    {"standard_review", "elevated_manual_review", "manual_review_required"},
                )
                self.assertEqual(update["risk"]["high_consequence_action_count"], 0)

    def test_malformed_types_are_unclassified_and_valid_types_still_classify(self):
        for odd in self.ODD_TYPES:
            with self.subTest(odd=json.dumps(odd)):
                risk = self.risk_or_fail(
                    {"views": [{"cards": []}]},
                    {
                        "views": [
                            {
                                "cards": [
                                    {"type": "entities", "entities": [{"type": odd, "entity": LOCK}]},
                                    {
                                        "type": "picture-elements",
                                        "elements": [{"type": odd, "action": "lock.unlock"}],
                                    },
                                ]
                            }
                        ]
                    },
                )
                self.assertEqual(risk.high_consequence_action_count, 0)
        risk = analyze_dashboard_risk(
            {"views": [{"cards": []}]},
            {
                "views": [
                    {
                        "cards": [
                            {"type": "entities", "entities": [{"type": "lock-entity", "entity": LOCK}]},
                            {
                                "type": "picture-elements",
                                "elements": [{"type": "service-button", "action": "lock.unlock"}],
                            },
                        ]
                    }
                ]
            },
        )
        self.assertEqual(risk.high_consequence_action_count, 2)


class ToggleTargetEntityTests(_PlanOnlyHarness):
    """HAMCP-337 F1 (architect follow-up on 144d5639): handle-action.ts
    (frontend a67b691a) toggles the card or row ``config.entity``; an
    ``entity`` inside a toggle action is never read.  At 144d5639 a lock,
    cover or alarm card whose toggle named another entity planned as a
    plain toggle."""

    OTHER = "light.synthetic_other"
    GESTURES = ("tap_action", "hold_action", "double_tap_action")

    def shapes(self, entity: str, gesture: str, action: dict):
        yield {"type": "tile", "entity": entity, gesture: action}, f"{CARD}/{gesture}"
        yield {"type": "button", "entity": entity, gesture: action}, f"{CARD}/{gesture}"
        yield (
            {"type": "tile", "entity": entity, f"icon_{gesture}": action},
            f"{CARD}/icon_{gesture}",
        )
        yield (
            {"type": "entities", "entities": [{"entity": entity, gesture: action}]},
            f"{CARD}/entities/0/{gesture}",
        )

    async def categories_at(self, card: dict, path: str):
        await self.asyncTearDown()
        await self.asyncSetUp()
        created, stored, update = await self.plan(background_dashboard(0), insert_cards([card]))
        return created, stored, update, self.changed_at(update, path)

    async def test_toggle_naming_another_entity_classifies_the_card_entity(self):
        action = {"action": "toggle", "entity": self.OTHER}
        for entity in HIGH_ENTITIES:
            for gesture in self.GESTURES:
                for card, path in self.shapes(entity, gesture, action):
                    with self.subTest(entity=entity, card=json.dumps(card)):
                        created, stored, update, categories = await self.categories_at(card, path)
                        self.assertIn("high_consequence_action", categories)
                        self.assertNotIn("toggle", categories)
                        self.assert_elevated(created, stored, update)

    async def test_toggle_never_follows_the_action_entity(self):
        # The exact rule, not a union of both entities: a light card whose
        # toggle names a lock toggles the light, and a card with no entity
        # toggles nothing.
        action = {"action": "toggle", "entity": LOCK}
        for card, path in (
            ({"type": "tile", "entity": self.OTHER, "tap_action": action}, f"{CARD}/tap_action"),
            ({"type": "button", "entity": self.OTHER, "hold_action": action}, f"{CARD}/hold_action"),
            ({"type": "button", "tap_action": action}, f"{CARD}/tap_action"),
            ({"type": "button", "entity": "", "tap_action": action}, f"{CARD}/tap_action"),
        ):
            with self.subTest(card=json.dumps(card)):
                _, _, update, categories = await self.categories_at(card, path)
                self.assertEqual(categories, {"toggle"})
                self.assertEqual(update["risk"]["high_consequence_action_count"], 0)


if __name__ == "__main__":
    unittest.main()
