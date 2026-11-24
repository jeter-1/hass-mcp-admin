"""HAMCP-292: the dashboard review projection keeps in-bound strings whole.

The review projection was sanitized with a 512-character string bound. A
longer plan description (reviewed bound 2,000) or patch path (reviewed bound
1,024) was cut and given Engineering's own truncation suffix, which the strict
creation gate then refused as a marker collision
(``plan_contains_prohibited_sensitive_data``). The reported HA Admin plan text
below, 521 characters, was refused that way; a 473-character rewording passed.

The negatives keep every real refusal. A secret, a known sensitive value or a
forged marker is still refused before any Home Assistant read when it is in the
description, the title, a patch pointer or a nested patch value, including past
or across the old 512-character cut and at the 2,000-character bound.

The scanner checks each field separately, so fragments of a known secret split
across fields are not detected. That limitation predates HAMCP-292, and these
tests do not claim cross-field coverage.
"""

from __future__ import annotations

from pathlib import Path
import sys
from html import escape
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

import hamcp162_synthetic as syn  # noqa: E402
from ha_mcp_engineering.approval_web import _render_review  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_dashboard.constants import (  # noqa: E402
    MAX_DESCRIPTION_CHARS,
    MAX_POINTER_CHARS,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    persistence_safety_errors,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.sanitization import TRUNCATION_SUFFIX  # noqa: E402
from test_dashboard_update_mvp import (  # noqa: E402
    _DashboardGateway,
    _unrelated_lifecycle_provider_identity,
    _UnusedConfigurationGateway,
)


# The plan description HA Admin reported as refused (521 characters).
REPORTED_REFUSED = (
    "HAAC-PR016 re-issue 2 (unchanged scope). Plan 7cfdf3e6 was approved but "
    "failed pre-dispatch (provider_unavailable_pre_dispatch during a gateway "
    "core-authority re-probe at 08:50 UTC); nothing was written. Add two "
    "read-only temperature chips to the Home Main Dashboard, Home view, Home "
    "Status section, mushroom chips card (after the Alarm chip): Outdoor = "
    "sensor.local_outdoor_temperature, Indoor = "
    "sensor.mudroom_thermostat_temperature. No other changes. Rollback: "
    "remove /views/0/sections/0/cards/1/chips/8 then /chips/7."
)
# The rewording HA Admin reported as accepted (473 characters).
REPORTED_ACCEPTED = (
    "HAAC-PR016 re-issue (unchanged scope; previous plans expired or failed "
    "before dispatch with nothing written). Add two read-only temperature "
    "chips to the Home Main Dashboard, Home view, Home Status section, "
    "mushroom chips card (after the Alarm chip): Outdoor = "
    "sensor.local_outdoor_temperature, Indoor = "
    "sensor.mudroom_thermostat_temperature. Default tap = more-info. No other "
    "card, view or entity changes. Rollback: remove "
    "/views/0/sections/0/cards/1/chips/8 then /chips/7."
)
SAFE = "Add two read-only temperature chips. "
PAST_OLD_CUT = (SAFE * 20)[:600]
KNOWN_SECRET = "synthetic-known-sensitive-value-292"
SECRETS = (
    ("bearer", syn.bearer("synthQ29yZWN0SG9yc2VCYXR0ZXJ5U3RhcGxlMjky")),
    ("jwt", syn.jwt("hamcp292")),
    ("known", KNOWN_SECRET),
)
SENSITIVE = "dashboard_contains_prohibited_sensitive_data"

CHIPS = [{"type": "entity", "entity": f"sensor.chip_{index}"} for index in range(7)]
HOME_DASHBOARD = {
    "title": "Home Main Dashboard",
    "views": [
        {
            "title": "Home",
            "sections": [
                {
                    "type": "grid",
                    "cards": [
                        {"type": "heading", "heading": "Home Status"},
                        {"type": "custom:mushroom-chips-card", "chips": CHIPS},
                    ],
                }
            ],
        }
    ],
}
CHIP_OPERATIONS = [
    {
        "operation_id": "outdoor",
        "operation": "add",
        "path": "/views/0/sections/0/cards/1/chips/7",
        "value": {"type": "entity", "entity": "sensor.local_outdoor_temperature"},
    },
    {
        "operation_id": "indoor",
        "operation": "add",
        "path": "/views/0/sections/0/cards/1/chips/8",
        "value": {"type": "entity", "entity": "sensor.mudroom_thermostat_temperature"},
    },
]


def _words(length):
    """Ordinary text of exactly ``length`` characters, ending in a space.

    A token placed after it starts a word. The bearer and JWT patterns match
    only at a word start.
    """
    return (SAFE * 60)[: length - 1] + " "


def _key_operation(length):
    """One add operation whose pointer is exactly ``length`` characters."""
    return [
        {
            "operation_id": "long_key",
            "operation": "add",
            "path": "/" + "k" * (length - 1),
            "value": "plain",
        }
    ]


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item)


class DashboardReviewProjectionBoundTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dashboard = _DashboardGateway()
        self.dashboard.configuration = HOME_DASHBOARD
        self.repository = ChangePlanRepository(self.root / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            _UnusedConfigurationGateway(),
            AuditLogger(str(self.root / "audit.jsonl"), "test-access-secret"),
            sensitive_values=(KNOWN_SECRET,),
            dashboard_gateway=self.dashboard,
            provider_identity_reader=_unrelated_lifecycle_provider_identity,
        )
        runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            dashboard_gateway=self.dashboard,
            provider_identity_reader=_unrelated_lifecycle_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = runtime
        await runtime.recover_once("startup")

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def _plan(
        self,
        description,
        *,
        operations=CHIP_OPERATIONS,
        title="HAAC-PR016 Home temperature chips",
    ):
        return await self.service.create_dashboard_update_plan(
            title=title,
            description=description,
            url_path="dashboard-home",
            patch_operations=operations,
        )

    def _stored(self, created):
        plan = self.repository.get(created["plan_id"])
        self.assertIsNotNone(plan)
        return plan.to_dict()

    def _assert_created_whole(self, created, text):
        self.assertEqual(created["status"], "awaiting_approval")
        record = self._stored(created)
        projected = record["proposed_config"]["dashboard_update"]
        self.assertEqual(projected["description"], text)
        self.assertFalse(
            any(TRUNCATION_SUFFIX in item for item in _walk_strings(record))
        )
        # The stored record still passes the strict detector unchanged.
        self.assertEqual(
            persistence_safety_errors(record, (KNOWN_SECRET,)), []
        )

    async def _assert_refused_before_read(self, description, reason, **plan_args):
        with self.assertRaises(GovernanceError) as raised:
            await self._plan(description, **plan_args)
        self.assertEqual(raised.exception.details.get("reason"), reason)
        self.assertEqual(self.dashboard.preread_count, 0)
        self.assertEqual(self.repository.read_all_for_inspection(), [])

    # Success: the reported text and the bounds.

    async def test_reported_refused_text_now_creates_a_plan(self):
        self.assertEqual(len(REPORTED_REFUSED), 521)
        created = await self._plan(REPORTED_REFUSED)
        self._assert_created_whole(created, REPORTED_REFUSED)

    async def test_reported_accepted_text_still_creates_a_plan(self):
        self.assertEqual(len(REPORTED_ACCEPTED), 473)
        created = await self._plan(REPORTED_ACCEPTED)
        self._assert_created_whole(created, REPORTED_ACCEPTED)

    async def test_description_at_the_old_cut_and_one_past_it(self):
        for length in (512, 513):
            with self.subTest(length=length):
                text = (SAFE * 20)[:length]
                created = await self._plan(text)
                self._assert_created_whole(created, text)

    async def test_description_at_the_reviewed_bound_creates_a_plan(self):
        text = (SAFE * 60)[:MAX_DESCRIPTION_CHARS]
        created = await self._plan(text)
        self._assert_created_whole(created, text)

    async def test_patch_path_past_the_old_cut_and_at_the_bound_creates_a_plan(
        self,
    ):
        for length in (601, MAX_POINTER_CHARS):
            with self.subTest(length=length):
                operations = _key_operation(length)
                path = operations[0]["path"]
                self.assertEqual(len(path), length)
                created = await self._plan(
                    "Add one long-named top-level key.", operations=operations
                )
                self.assertEqual(created["status"], "awaiting_approval")
                record = self._stored(created)
                paths = [
                    entry["path"]
                    for entry in record["proposed_config"]["dashboard_update"][
                        "semantic_diff"
                    ]["entries"]
                ]
                self.assertIn(path, paths)
                self.assertFalse(
                    any(TRUNCATION_SUFFIX in item for item in _walk_strings(record))
                )

    async def test_created_long_description_plan_survives_reload(self):
        created = await self._plan(REPORTED_REFUSED)
        reloaded = ChangePlanRepository(self.root / "plans").get(
            created["plan_id"]
        )
        self.assertIsNotNone(reloaded)
        self.assertEqual(
            reloaded.to_dict()["proposed_config"]["dashboard_update"][
                "description"
            ],
            REPORTED_REFUSED,
        )

    async def test_review_summary_shows_the_whole_in_bound_description(self):
        for length in (1_500, MAX_DESCRIPTION_CHARS):
            with self.subTest(length=length):
                text = (SAFE * 60)[:length]
                created = await self._plan(text)
                self._assert_created_whole(created, text)
                self.assertEqual(created["description"], text)
                plan = self.repository.get(created["plan_id"])
                self.assertEqual(plan.description, text)
                self.assertEqual(
                    self.service._review_summary(plan)["description"], text
                )

    async def test_review_page_renders_text_after_character_1000(self):
        # Codex r1 L1: the approval page shows what follows character 1,000.
        tail = "Rollback-after-character-1000: remove chip 8, then chip 7."
        self.assertNotIn(tail, SAFE * 60)
        for text in (
            _words(1_001) + tail,
            _words(MAX_DESCRIPTION_CHARS - len(tail)) + tail,
        ):
            with self.subTest(length=len(text)):
                self.assertLessEqual(len(text), MAX_DESCRIPTION_CHARS)
                created = await self._plan(text)
                self._assert_created_whole(created, text)
                plan = self.repository.get(created["plan_id"])
                html = _render_review(
                    "", self.service._review_summary(plan), "csrf"
                )
                self.assertIn(
                    "<tr><th>Description</th><td><code>"
                    f"{escape(text)}</code></td></tr>",
                    html,
                )

    # Negatives: the bound and every real refusal are kept.

    async def test_description_over_the_reviewed_bound_is_refused(self):
        # The honest bound refusal, not the projection's marker collision,
        # and before any Home Assistant read.
        await self._assert_refused_before_read(
            (SAFE * 60)[: MAX_DESCRIPTION_CHARS + 1],
            "dashboard_update_planning_failed",
        )

    async def test_patch_path_past_the_bound_is_refused(self):
        # The pointer bound is enforced when the patch is compiled, after
        # the dashboard read; nothing is stored.
        with self.assertRaises(GovernanceError) as raised:
            await self._plan(
                "Add one long-named top-level key.",
                operations=_key_operation(MAX_POINTER_CHARS + 1),
            )
        self.assertEqual(
            raised.exception.details.get("reason"), "invalid_dashboard_patch"
        )
        self.assertEqual(self.repository.read_all_for_inspection(), [])

    async def test_secret_in_the_title_is_refused(self):
        for name, secret in SECRETS:
            with self.subTest(secret=name):
                await self._assert_refused_before_read(
                    "Add two read-only temperature chips.",
                    SENSITIVE,
                    title="Home chips " + secret,
                )

    async def test_secret_in_a_patch_pointer_is_refused(self):
        for name, secret in SECRETS:
            with self.subTest(secret=name):
                await self._assert_refused_before_read(
                    "Add one key.",
                    SENSITIVE,
                    operations=[
                        {
                            "operation_id": "key",
                            "operation": "add",
                            "path": "/views/" + secret,
                            "value": "plain",
                        }
                    ],
                )

    async def test_secret_in_a_nested_patch_value_is_refused(self):
        for name, secret in SECRETS:
            with self.subTest(secret=name):
                await self._assert_refused_before_read(
                    "Add one markdown card.",
                    SENSITIVE,
                    operations=[
                        {
                            "operation_id": "card",
                            "operation": "add",
                            "path": "/views/0/sections/0/cards/2",
                            "value": {
                                "type": "markdown",
                                "content": "Status",
                                "nested": ["one", {"deep": "note " + secret}],
                            },
                        }
                    ],
                )

    async def test_token_across_the_old_cut_or_ending_at_the_bound_is_refused(
        self,
    ):
        for name, secret in SECRETS:
            for start in (490, 500, 511):
                with self.subTest(secret=name, start=start):
                    self.assertGreater(start + len(secret), 512)
                    await self._assert_refused_before_read(
                        _words(start) + secret + " " + SAFE, SENSITIVE
                    )
            with self.subTest(secret=name, ends_at=MAX_DESCRIPTION_CHARS):
                description = _words(MAX_DESCRIPTION_CHARS - len(secret)) + secret
                self.assertEqual(len(description), MAX_DESCRIPTION_CHARS)
                await self._assert_refused_before_read(description, SENSITIVE)

    async def test_bearer_secret_past_the_old_cut_is_refused(self):
        secret = syn.bearer("synthQ29yZWN0SG9yc2VCYXR0ZXJ5U3RhcGxlMjky")
        for description in (
            secret + " " + PAST_OLD_CUT,
            PAST_OLD_CUT + " " + secret,
        ):
            with self.subTest(offset=description.index("Bear")):
                await self._assert_refused_before_read(
                    description, SENSITIVE
                )

    async def test_jwt_past_the_old_cut_is_refused(self):
        await self._assert_refused_before_read(
            PAST_OLD_CUT + " " + syn.jwt("hamcp292"),
            SENSITIVE,
        )

    async def test_known_sensitive_value_past_the_old_cut_is_refused(self):
        await self._assert_refused_before_read(
            PAST_OLD_CUT + " " + KNOWN_SECRET,
            SENSITIVE,
        )

    async def test_forged_markers_past_the_old_cut_are_refused(self):
        for forged in ("[REDACTED:token]", TRUNCATION_SUFFIX):
            with self.subTest(forged=forged):
                await self._assert_refused_before_read(
                    PAST_OLD_CUT + " " + forged,
                    SENSITIVE,
                )


if __name__ == "__main__":
    unittest.main()
