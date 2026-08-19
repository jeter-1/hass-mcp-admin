"""Effect-projection bounds are structural, not the display bound.

The helper effect projection retains service-call targets and data as
structural evidence. Bounding that evidence with `_HELPER_PROFILE_LIMIT` - the
limit for display lists such as action domains, services, and reason codes -
clipped ordinary automations: one notification payload can exceed 31 flattened
leaves on its own. A clipped projection raises `action_profile_truncated`,
which is a coverage failure, so it makes every helper that could reach that
automation non-actionable rather than merely elevated.

These tests pin the two bounds apart, prove an ordinary payload survives, and
prove the structural bound still catches a genuinely oversized automation.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.governance.risk import (  # noqa: E402
    _HELPER_EFFECT_PROJECTION_VALUE_LIMIT,
    _HELPER_EFFECT_STRUCTURE_NODE_LIMIT,
    _HELPER_PROFILE_LIMIT,
    automation_action_consequence_profile,
)


def _notification_automation(leaf_count: int) -> dict:
    """One notification action whose data payload has `leaf_count` leaves."""

    return {
        "action": [
            {
                "service": "notify.mobile_app_example",
                "data": {
                    "message": "status",
                    "data": {
                        f"field_{index:03d}": f"value_{index:03d}"
                        for index in range(leaf_count - 1)
                    },
                },
            }
        ]
    }


class EffectProjectionBoundSeparationTests(unittest.TestCase):
    def test_structural_and_display_bounds_are_distinct(self):
        # Reusing one constant for both is the defect this file guards.
        self.assertNotEqual(
            _HELPER_EFFECT_PROJECTION_VALUE_LIMIT, _HELPER_PROFILE_LIMIT
        )
        self.assertGreater(
            _HELPER_EFFECT_PROJECTION_VALUE_LIMIT, _HELPER_PROFILE_LIMIT
        )
        # Effect values are a subset of the action structure, so their bound
        # must stay below the structure budget that catches oversized configs.
        self.assertLess(
            _HELPER_EFFECT_PROJECTION_VALUE_LIMIT,
            _HELPER_EFFECT_STRUCTURE_NODE_LIMIT,
        )

    def test_ordinary_notification_payload_is_not_clipped(self):
        # Comfortably past the old 31-leaf budget, well within the new one.
        profile = automation_action_consequence_profile(
            _notification_automation(64)
        )
        self.assertFalse(profile["effect_projection_clipped"])
        self.assertFalse(profile["truncated"])

    def test_payload_just_past_the_old_display_bound_survives(self):
        profile = automation_action_consequence_profile(
            _notification_automation(_HELPER_PROFILE_LIMIT + 2)
        )
        self.assertFalse(
            profile["effect_projection_clipped"],
            "an ordinary payload was clipped by the display bound",
        )

    def test_payload_past_the_structural_bound_is_still_clipped(self):
        profile = automation_action_consequence_profile(
            _notification_automation(
                _HELPER_EFFECT_PROJECTION_VALUE_LIMIT + 8
            )
        )
        self.assertTrue(
            profile["effect_projection_clipped"],
            "the structural bound stopped bounding",
        )

    def test_oversized_action_structure_still_clips(self):
        # The structure node budget remains the bound that catches a
        # pathologically large automation, independent of payload width.
        automation = {
            "action": [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": f"light.item_{index:03d}"},
                }
                for index in range(_HELPER_EFFECT_STRUCTURE_NODE_LIMIT)
            ]
        }
        profile = automation_action_consequence_profile(automation)
        self.assertTrue(profile["effect_projection_clipped"])

    def test_display_lists_remain_bounded_by_the_display_limit(self):
        # Many distinct services: the display bound must still apply to them.
        automation = {
            "action": [
                {"service": f"script.example_{index:03d}"}
                for index in range(_HELPER_PROFILE_LIMIT * 2)
            ]
        }
        profile = automation_action_consequence_profile(automation)
        self.assertLessEqual(
            len(profile["services"]), _HELPER_PROFILE_LIMIT
        )
        self.assertTrue(profile["truncated"])

    def test_clipping_still_reports_itself_when_it_happens(self):
        clipped = automation_action_consequence_profile(
            _notification_automation(
                _HELPER_EFFECT_PROJECTION_VALUE_LIMIT + 8
            )
        )
        # A clipped projection must stay visible and must not claim
        # completeness; silent truncation is the failure being guarded.
        self.assertTrue(clipped["effect_projection_clipped"])
        self.assertFalse(clipped["complete"])
        self.assertTrue(
            any(
                str(item).startswith("overflow_sha256:")
                or str(item).startswith("sha256:")
                for item in clipped["effect_data"]
            )
            or clipped["effect_projection_fingerprint"],
            "omitted effect values must remain represented",
        )

    def test_projection_is_deterministic(self):
        first = automation_action_consequence_profile(
            _notification_automation(64)
        )
        second = automation_action_consequence_profile(
            _notification_automation(64)
        )
        self.assertEqual(
            first["effect_projection_fingerprint"],
            second["effect_projection_fingerprint"],
        )
        self.assertEqual(
            first["evidence_fingerprint"], second["evidence_fingerprint"]
        )


if __name__ == "__main__":
    unittest.main()
