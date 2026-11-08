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
            ("valve.synthetic_row", "bare", ()),
            ("valve.synthetic_row", "object", (RiskCategory.DISPLAY_ONLY,)),
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
    def test_card_path_keeps_only_structural_tokens(self):
        cases = (
            ((), "/"),
            (("title",), "/"),
            (("views", "7", "sections", "0", "cards", "3", "tap_action"),
             "/views/7/sections/0/cards/3"),
            (("views", "0", "cards", "1", "card", "entities", "4"),
             "/views/0/cards/1/card/entities/4"),
            (("views", "0", "cards", "2", "light.synthetic_lamp", "cards", "1"),
             "/views/0/cards/2"),
            (("views", "0", "cards", "Synthetic Name"), "/views/0"),
            (("views", "0", "cards", "-1"), "/views/0"),
            (("views", "0", "cards", "٣"), "/views/0"),
            (("views", "123456"), "/"),
            (("synthetic_key", "views", "0"), "/"),
        )
        for tokens, expected in cases:
            with self.subTest(tokens=tokens):
                self.assertEqual(_card_path(tokens), expected)
                self.assertRegex(_card_path(tokens), STRUCTURAL)

    def test_card_path_is_bounded(self):
        tokens = ("cards", "99999") * 40
        path = _card_path(tokens)
        self.assertEqual(path, "/cards/99999" * 8)
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


if __name__ == "__main__":
    unittest.main()
