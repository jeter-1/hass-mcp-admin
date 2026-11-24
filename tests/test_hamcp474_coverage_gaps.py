"""HAMCP-474 D1: a deep automation is not opaque only because its path is long.

Three synthetic automation shapes nest ``choose``/``if`` blocks deeply enough
that a ``configuration_path:`` context value exceeds the per-value obligation
bound. Before D1 that copy of the location was digested as lost target detail,
so every obligation under it became a coverage failure and the automation
could not be ruled out for any target, although its entity IDs were intact.

D1 relabels only the obligation's own over-bound location copy. The full path
stays in ``config_path`` and the bound is still disclosed through
``evidence_bounded``. Every other evidence loss keeps failing closed; the
constructor-level negatives are in ``test_beta39_evidence_bounds``. Entity IDs
and structure are invented; nothing here reads live data.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency import models as dependency_models  # noqa: E402
from ha_mcp_engineering.dependency.bounded_sources import (  # noqa: E402
    bounded_source_entries,
)
from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    extract_document_obligation_evidence,
)
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    MAX_OBLIGATION_VALUE_BYTES,
)

LOCATION_PREFIX = "configuration_path:"
LOCK = "lock.front_door"
OVERSIZED_COVER = "cover." + "z" * (MAX_OBLIGATION_VALUE_BYTES + 12)


def _state(entity_id, state):
    return {"condition": "state", "entity_id": entity_id, "state": state}


def _garage_conditions():
    return [
        _state("binary_sensor.resident_one_in_bed", "on"),
        _state("binary_sensor.resident_two_in_bed", "on"),
        _state("cover.garage_door", "open"),
        _state("input_boolean.garage_auto_close", "on"),
        _state("binary_sensor.garage_obstruction", "off"),
    ]


def _away_or_in_bed(prefix):
    return {
        "condition": "or",
        "conditions": [
            {"condition": "and", "conditions": [
                _state("person.resident_one", "not_home"),
                _state("person.resident_two", "not_home"),
                _state("cover.garage_door", "open"),
            ]},
            {"condition": "and", "conditions": [
                _state("binary_sensor.resident_one_in_bed", "on"),
                _state("binary_sensor.resident_two_in_bed", "on"),
                _state(f"input_boolean.{prefix}_override", "off"),
            ]},
        ],
    }


def _close_cover(entity_id="cover.garage_door"):
    return {"action": "cover.close_cover", "target": {"entity_id": entity_id}}


def close_garage_both_in_bed(cover="cover.garage_door"):
    """Four nested ``choose`` levels, four options at the outer level."""

    def level(depth):
        if depth == 0:
            return [_close_cover(cover)]
        return [{
            "choose": [
                {"conditions": _garage_conditions(), "sequence": level(depth - 1)}
                for _ in range(4 if depth == 4 else 1)
            ],
            "default": [{
                "action": "notify.mobile_app_resident_one_phone",
                "data": {"message": "skip"},
            }],
        }]

    return {
        "id": "close_garage_both_in_bed",
        "alias": "Close garage when both in bed",
        "triggers": [
            {"trigger": "state", "entity_id": "binary_sensor.resident_one_in_bed", "to": "on"},
            {"trigger": "state", "entity_id": "binary_sensor.resident_two_in_bed", "to": "on"},
        ],
        "conditions": [],
        "actions": level(4),
        "mode": "restart",
    }


def garage_open_30m_away_auto_close_fallback():
    """Three nested ``choose`` blocks, each guarded by an or/and block."""

    def nest(depth):
        if depth == 0:
            return [_close_cover()]
        return [{"choose": [{
            "conditions": [_away_or_in_bed(f"g{depth}")] + _garage_conditions()[:3],
            "sequence": nest(depth - 1),
        }]}]

    return {
        "id": "garage_open_30m_away_auto_close_fallback",
        "alias": "Garage open 30m away auto close fallback",
        "triggers": [
            {"trigger": "state", "entity_id": "cover.garage_door", "to": "open", "for": "00:30:00"},
        ],
        "conditions": [],
        "actions": nest(3),
        "mode": "single",
    }


def front_door_lock_bedtime(*, deep):
    """A bedtime lock; ``deep`` adds or/and/or inside then.choose.sequence.choose."""

    jammed_conditions = [_state(LOCK, "jammed")]
    if deep:
        jammed_conditions = [{"condition": "or", "conditions": [
            {"condition": "and", "conditions": [
                _state(LOCK, "jammed"),
                {"condition": "or", "conditions": [
                    _state("binary_sensor.resident_one_in_bed", "on"),
                    _state("binary_sensor.resident_two_in_bed", "on"),
                ]},
            ]},
        ]}]
    return {
        "id": "front_door_lock_bedtime" + ("_deep" if deep else ""),
        "alias": "Front door lock bedtime",
        "triggers": [
            {"trigger": "state", "entity_id": "binary_sensor.resident_one_in_bed", "to": "on"},
            {"trigger": "state", "entity_id": "binary_sensor.resident_two_in_bed", "to": "on"},
            {"trigger": "time", "at": "23:30:00"},
            {"trigger": "state", "entity_id": LOCK, "to": "unlocked", "for": "00:10:00"},
        ],
        "conditions": [
            {"condition": "or", "conditions": [
                {"condition": "and", "conditions": [
                    _state("binary_sensor.resident_one_in_bed", "on"),
                    {"condition": "or", "conditions": [
                        _state("binary_sensor.resident_two_in_bed", "on"),
                        _state("person.resident_two", "not_home"),
                    ]},
                ]},
                {"condition": "time", "after": "23:00:00"},
                _state("input_boolean.bedtime_mode", "on"),
            ]},
            _state(LOCK, "unlocked"),
            _state("binary_sensor.front_door_contact", "off"),
        ],
        "actions": [
            {"if": [_state("binary_sensor.front_door_contact", "off")],
             "then": [{"choose": [{
                 "conditions": [_state(LOCK, "unlocked")],
                 "sequence": [
                     {"action": "lock.lock", "target": {"entity_id": LOCK}},
                     {"choose": [{
                         "conditions": jammed_conditions,
                         "sequence": [{
                             "action": "notify.mobile_app_resident_one_phone",
                             "data": {"message": "Front door jammed"},
                         }],
                     }]},
                 ],
             }]}],
             "else": [{
                 "action": "notify.mobile_app_resident_one_phone",
                 "data": {"message": "Door open"},
             }]},
            {"wait_for_trigger": [{"trigger": "state", "entity_id": LOCK, "to": "locked"}],
             "timeout": "00:01:00"},
            {"action": "input_boolean.turn_off", "target": {"entity_id": "input_boolean.bedtime_mode"}},
            {"action": "logbook.log", "data": {"name": "Front door", "message": "bedtime lock"}},
        ],
        "mode": "single",
    }


DEEP_SHAPES = {
    "close_garage_both_in_bed": close_garage_both_in_bed,
    "garage_open_30m_away_auto_close_fallback": garage_open_30m_away_auto_close_fallback,
    "front_door_lock_bedtime_deep": lambda: front_door_lock_bedtime(deep=True),
}


def _obligations(config):
    _findings, _dynamic, obligations = extract_document_obligation_evidence(
        source_type="automation",
        source_id=config["id"],
        config=config,
        source_entity_id=f"automation.{config['id']}",
        source_name=config["alias"],
    )
    return obligations


def _over_bound(obligation):
    """Return whether the obligation carries a location copy over the bound.

    Structured references carry ``configuration_path:<config_path>`` as their
    context; whole-template reads carry no location copy and are unaffected.
    """

    label = LOCATION_PREFIX + obligation.config_path
    return bool(obligation.context_provenance) and (
        len(label.encode("utf-8")) > MAX_OBLIGATION_VALUE_BYTES
    )


def without_location_relabel():
    """Restore the pre-D1 binding, in which the location copy is lost detail."""

    return patch.object(
        dependency_models,
        "_segment_location_context",
        lambda values, path: (tuple(values), False),
    )


class DeepShapeFixtureTests(unittest.TestCase):
    """The fixtures reach the bound, so the D1 assertions below are not vacuous."""

    def test_each_deep_shape_has_an_over_bound_location(self):
        for name, make in DEEP_SHAPES.items():
            with self.subTest(shape=name):
                self.assertTrue(any(_over_bound(o) for o in _obligations(make())))

    def test_without_the_relabel_each_deep_shape_is_coverage_failed(self):
        for name, make in DEEP_SHAPES.items():
            with self.subTest(shape=name), without_location_relabel():
                obligations = _obligations(make())
                failed = [o for o in obligations if o.coverage_failure_authority]
                self.assertTrue(failed)
                self.assertTrue(all(_over_bound(o) for o in failed))
                self.assertEqual(
                    1, bounded_source_entries(obligations).public()["bounded_source_total"]
                )

    def test_shallow_control_never_reaches_the_bound(self):
        obligations = _obligations(front_door_lock_bedtime(deep=False))
        self.assertFalse(any(_over_bound(o) for o in obligations))
        self.assertFalse(any(o.evidence_bounded for o in obligations))
        self.assertFalse(any(o.coverage_failure_authority for o in obligations))


class DeepShapeLocationRelabelTests(unittest.TestCase):
    def test_deep_shapes_keep_their_classification(self):
        for name, make in DEEP_SHAPES.items():
            with self.subTest(shape=name):
                obligations = _obligations(make())
                self.assertFalse(any(o.coverage_failure_authority for o in obligations))
                self.assertFalse(
                    any(o.lock_projection == "coverage_failure" for o in obligations)
                )
                self.assertEqual(
                    0, bounded_source_entries(obligations).public()["bounded_source_total"]
                )

    def test_the_bound_is_still_disclosed_for_the_same_obligations(self):
        for name, make in DEEP_SHAPES.items():
            with self.subTest(shape=name):
                with without_location_relabel():
                    before = {
                        (o.config_path, o.reason_code)
                        for o in _obligations(make())
                        if o.coverage_failure_authority
                    }
                after = {
                    (o.config_path, o.reason_code)
                    for o in _obligations(make())
                    if o.evidence_bounded
                }
                self.assertTrue(before)
                self.assertEqual(before, after)

    def test_full_location_is_kept_and_only_its_copy_is_relabelled(self):
        for name, make in DEEP_SHAPES.items():
            with self.subTest(shape=name):
                relabelled = [o for o in _obligations(make()) if _over_bound(o)]
                self.assertTrue(relabelled)
                for obligation in relabelled:
                    self.assertTrue(obligation.config_path.startswith("$."))
                    self.assertNotIn("sha256:", obligation.config_path)
                    locations = [
                        value
                        for value in obligation.context_provenance
                        if value.startswith(LOCATION_PREFIX)
                    ]
                    self.assertEqual(1, len(locations))
                    self.assertTrue(
                        locations[0].startswith(LOCATION_PREFIX + "sha256:")
                    )
                    self.assertLessEqual(
                        len(locations[0].encode("utf-8")), MAX_OBLIGATION_VALUE_BYTES
                    )
                    for entity_id in obligation.exact_entity_ids:
                        self.assertFalse(entity_id.startswith(("sha256:", "omitted:")))

    def test_relabel_is_deterministic(self):
        for name, make in DEEP_SHAPES.items():
            with self.subTest(shape=name):
                first = [o.context_provenance for o in _obligations(make())]
                second = [o.context_provenance for o in _obligations(make())]
                self.assertEqual(first, second)

    def test_deep_lock_automation_is_complete_for_the_lock_and_not_opaque(self):
        # Before D1 the lock obligations were intact, yet two over-bound
        # condition reads failed, so the automation could not be ruled out
        # for any target.
        with without_location_relabel():
            before = _obligations(front_door_lock_bedtime(deep=True))
        after = _obligations(front_door_lock_bedtime(deep=True))
        self.assertTrue(any(o.coverage_failure_authority for o in before))
        self.assertFalse(any(o.coverage_failure_authority for o in after))
        for obligations in (before, after):
            lock = [o for o in obligations if LOCK in o.exact_entity_ids]
            self.assertEqual(6, len(lock))
            self.assertIn("service_target", {o.relation for o in lock})
            for obligation in lock:
                self.assertFalse(obligation.coverage_failure_authority)
                self.assertEqual("exact_dependency", obligation.outcome)
                self.assertEqual("exact", obligation.lock_projection)


class DeepShapeFailClosedTests(unittest.TestCase):
    """Losing an entity ID in a deep shape still fails, relabel or not."""

    def test_oversized_entity_id_beside_a_relabel_still_fails(self):
        obligations = _obligations(close_garage_both_in_bed(cover=OVERSIZED_COVER))
        oversized = [
            o for o in obligations
            if o.config_path.endswith(".target.entity_id") and _over_bound(o)
        ]
        self.assertTrue(oversized)
        for obligation in oversized:
            self.assertTrue(obligation.coverage_failure_authority)
            self.assertEqual("coverage_failure", obligation.outcome)
            self.assertEqual("coverage_failure", obligation.lock_projection)
            self.assertNotIn(OVERSIZED_COVER, obligation.exact_entity_ids)
        self.assertEqual(
            1, bounded_source_entries(obligations).public()["bounded_source_total"]
        )
        oversized_ids = {id(o) for o in oversized}
        intact = [
            o for o in obligations
            if _over_bound(o) and id(o) not in oversized_ids
        ]
        self.assertTrue(intact)
        self.assertFalse(any(o.coverage_failure_authority for o in intact))


if __name__ == "__main__":
    unittest.main()
