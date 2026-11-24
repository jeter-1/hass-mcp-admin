"""B39-136-R4: selector evidence is bounded and sanitized before it exists.

Obligation evidence is derived from configuration and template literals, so a
single value can be arbitrarily long and can carry secret-bearing material.
Bounding happens inside the obligation constructor, which means no module can
create an obligation that escapes it.  Losing target-specific detail removes
the basis for an exact or proven-exclusion claim, so the obligation must be
reclassified conservatively rather than silently truncated.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.models import (  # noqa: E402
    MAX_OBLIGATION_EXACT_AGGREGATE_BYTES,
    MAX_OBLIGATION_SELECTOR_AGGREGATE_BYTES,
    MAX_OBLIGATION_TEXT_BYTES,
    MAX_OBLIGATION_VALUE_BYTES,
    DependencyObligation,
    obligation_fingerprint,
)


BASE = dict(
    evidence_id="ev_bounds",
    source_type="automation",
    source_id="beta39_bounds",
    config_path="$.condition[0].value_template",
    relation="template_reference",
    obligation_kind="reviewed_semantic",
    reason_code="synthetic_reason",
    semantic_category="provenance_preserving",
    semantic_registry_version="home-assistant-template-semantic-registry-v1",
    semantic_registry_fingerprint="f" * 64,
    expression_fingerprint="e" * 64,
    configuration_fingerprint="c" * 64,
)


def _obligation(**overrides) -> DependencyObligation:
    fields = dict(BASE)
    fields.update(overrides)
    return DependencyObligation(**fields)


class ObligationEvidenceBoundTests(unittest.TestCase):
    def test_ordinary_evidence_is_retained_verbatim(self):
        item = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            exact_entity_ids=("input_boolean.example", "light.porch"),
            literal_selectors=("kitchen", "porch"),
        )
        self.assertEqual(
            ("input_boolean.example", "light.porch"), item.exact_entity_ids
        )
        self.assertEqual(("kitchen", "porch"), item.literal_selectors)
        self.assertFalse(item.evidence_bounded)
        self.assertEqual("exact_dependency", item.outcome)
        self.assertEqual("exact", item.lock_projection)

    def test_oversized_value_becomes_a_deterministic_digest(self):
        oversized = "x" * (MAX_OBLIGATION_VALUE_BYTES + 1)
        first = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            literal_selectors=(oversized,),
        )
        second = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            literal_selectors=(oversized,),
        )
        self.assertEqual(1, len(first.literal_selectors))
        self.assertTrue(first.literal_selectors[0].startswith("sha256:"))
        self.assertNotIn(oversized, str(first))
        # Deterministic: identical input yields identical evidence, so drift
        # against a stored fingerprint is still detectable.
        self.assertEqual(first.literal_selectors, second.literal_selectors)
        self.assertEqual(
            obligation_fingerprint(first), obligation_fingerprint(second)
        )

    def test_a_changed_oversized_value_changes_the_fingerprint(self):
        base = "x" * (MAX_OBLIGATION_VALUE_BYTES + 1)
        changed = base[:-1] + "y"
        first = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            literal_selectors=(base,),
        )
        second = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            literal_selectors=(changed,),
        )
        self.assertNotEqual(
            obligation_fingerprint(first), obligation_fingerprint(second)
        )

    def test_secret_bearing_value_is_replaced_not_persisted(self):
        secret = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345"
        item = _obligation(
            outcome="proven_dependency_neutral",
            lock_projection="none",
            literal_selectors=(secret,),
        )
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz012345", str(item))
        self.assertTrue(item.literal_selectors[0].startswith("sha256:"))
        self.assertTrue(item.evidence_bounded)

    def test_aggregate_bound_keeps_a_digest_of_what_was_dropped(self):
        values = tuple(f"selector_{index:05d}" for index in range(400))
        item = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            literal_selectors=values,
        )
        retained = item.literal_selectors
        self.assertLess(len(retained), len(values))
        self.assertTrue(retained[-1].startswith("omitted:sha256:"))
        total = sum(
            len(value.encode("utf-8")) for value in retained[:-1]
        )
        self.assertLessEqual(
            total, MAX_OBLIGATION_SELECTOR_AGGREGATE_BYTES
        )

    def test_dropped_set_digest_changes_when_the_dropped_set_changes(self):
        first_values = tuple(f"selector_{i:05d}" for i in range(400))
        second_values = first_values[:-1] + ("selector_zzzzz",)
        first = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            literal_selectors=first_values,
        )
        second = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            literal_selectors=second_values,
        )
        self.assertNotEqual(
            first.literal_selectors[-1], second.literal_selectors[-1]
        )

    def test_exact_entity_aggregate_is_bounded(self):
        values = tuple(
            f"input_boolean.helper_{index:05d}" for index in range(1000)
        )
        item = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            exact_entity_ids=values,
        )
        total = sum(
            len(value.encode("utf-8"))
            for value in item.exact_entity_ids[:-1]
        )
        self.assertLessEqual(total, MAX_OBLIGATION_EXACT_AGGREGATE_BYTES)
        self.assertLess(len(item.exact_entity_ids), len(values))

    def test_oversized_source_name_is_replaced(self):
        name = "n" * (MAX_OBLIGATION_TEXT_BYTES + 1)
        item = _obligation(
            outcome="proven_dependency_neutral",
            lock_projection="none",
            source_name=name,
        )
        self.assertTrue(item.source_name.startswith("sha256:"))
        self.assertTrue(item.evidence_bounded)
        # Losing a display label is not a loss of target-specific detail, so
        # the classification is unchanged.
        self.assertEqual("proven_dependency_neutral", item.outcome)
        self.assertEqual("none", item.lock_projection)


class ConservativeReclassificationTests(unittest.TestCase):
    """Losing target detail must reclassify, not silently truncate."""

    def test_exact_dependency_becomes_coverage_failed_when_detail_is_lost(self):
        item = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            exact_entity_ids=("input_boolean.example",),
            literal_selectors=("y" * (MAX_OBLIGATION_VALUE_BYTES + 1),),
        )
        self.assertEqual("coverage_failure", item.outcome)
        self.assertEqual("coverage_failure", item.lock_projection)
        self.assertEqual("coverage_failure", item.target_selector_scope)
        self.assertTrue(item.limit_exceeded)
        self.assertTrue(item.evidence_bounded)

    def test_proven_target_exclusion_cannot_survive_lost_detail(self):
        item = _obligation(
            outcome="proven_target_exclusion",
            lock_projection="none",
            exact_entity_ids=tuple(
                f"light.item_{index:05d}" for index in range(1000)
            ),
        )
        self.assertEqual("coverage_failure", item.outcome)
        self.assertEqual("coverage_failure", item.lock_projection)
        self.assertEqual("coverage_failure", item.target_selector_scope)

    def test_coverage_failure_stays_a_coverage_failure(self):
        item = _obligation(
            outcome="coverage_failure",
            lock_projection="coverage_failure",
            literal_selectors=("z" * (MAX_OBLIGATION_VALUE_BYTES + 1),),
        )
        self.assertEqual("coverage_failure", item.outcome)
        self.assertEqual("coverage_failure", item.lock_projection)

    def test_bounding_is_idempotent(self):
        first = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            literal_selectors=("y" * (MAX_OBLIGATION_VALUE_BYTES + 1),),
        )
        second = _obligation(
            outcome=first.outcome,
            lock_projection=first.lock_projection,
            literal_selectors=first.literal_selectors,
            limit_exceeded=first.limit_exceeded,
            evidence_bounded=first.evidence_bounded,
        )
        self.assertEqual(first.literal_selectors, second.literal_selectors)
        self.assertEqual(first.outcome, second.outcome)
        self.assertEqual(first.lock_projection, second.lock_projection)

    def test_bounded_flag_is_part_of_the_fingerprint(self):
        plain = _obligation(
            outcome="bounded_semantic_opaque",
            lock_projection="conservative",
            reason_code="synthetic_reason",
        )
        bounded = _obligation(
            outcome="bounded_semantic_opaque",
            lock_projection="conservative",
            reason_code="synthetic_reason",
            literal_selectors=("y" * (MAX_OBLIGATION_VALUE_BYTES + 1),),
        )
        self.assertFalse(plain.evidence_bounded)
        self.assertTrue(bounded.evidence_bounded)
        self.assertNotEqual(
            obligation_fingerprint(plain), obligation_fingerprint(bounded)
        )


LONG_PATH = (
    "$.actions[0].repeat.sequence[0]"
    + ".choose[0].sequence[0]" * 5
    + ".choose[0].conditions[0].entity_id"
)
LOCATION_PREFIX = "configuration_path:"


def _located(**overrides) -> DependencyObligation:
    fields = dict(
        config_path=LONG_PATH,
        outcome="exact_dependency",
        lock_projection="exact",
        exact_entity_ids=("input_boolean.example",),
        context_provenance=(LOCATION_PREFIX + LONG_PATH,),
    )
    fields.update(overrides)
    return _obligation(**fields)


def _assert_coverage_failed(test: unittest.TestCase, item) -> None:
    test.assertEqual("coverage_failure", item.outcome)
    test.assertEqual("coverage_failure", item.lock_projection)
    test.assertEqual("coverage_failure", item.target_selector_scope)
    test.assertTrue(item.limit_exceeded)
    test.assertTrue(item.evidence_bounded)


class LocationContextRelabelTests(unittest.TestCase):
    """HAMCP-474 D1: only the obligation's own over-bound location is relabelled."""

    def test_fixture_location_exceeds_the_value_bound(self):
        self.assertGreater(
            len((LOCATION_PREFIX + LONG_PATH).encode("utf-8")),
            MAX_OBLIGATION_VALUE_BYTES,
        )

    def test_own_over_bound_location_keeps_the_classification(self):
        for outcome, lock in (
            ("exact_dependency", "exact"),
            ("proven_target_exclusion", "none"),
        ):
            with self.subTest(outcome=outcome):
                item = _located(outcome=outcome, lock_projection=lock)
                self.assertEqual(outcome, item.outcome)
                self.assertEqual(lock, item.lock_projection)
                self.assertEqual(
                    "closed_finite_candidates", item.target_selector_scope
                )
                self.assertFalse(item.limit_exceeded)
                self.assertFalse(item.coverage_failure_authority)
                self.assertTrue(item.evidence_bounded)
                self.assertEqual(LONG_PATH, item.config_path)
                self.assertEqual(
                    ("input_boolean.example",), item.exact_entity_ids
                )
                (context,) = item.context_provenance
                self.assertTrue(
                    context.startswith(LOCATION_PREFIX + "sha256:")
                )
                self.assertLessEqual(
                    len(context.encode("utf-8")), MAX_OBLIGATION_VALUE_BYTES
                )

    def test_relabel_is_deterministic_idempotent_and_path_sensitive(self):
        first = _located()
        again = _located()
        rebuilt = _located(
            context_provenance=first.context_provenance,
            evidence_bounded=first.evidence_bounded,
        )
        other_path = LONG_PATH.replace("entity_id", "state")
        other = _located(
            config_path=other_path,
            context_provenance=(LOCATION_PREFIX + other_path,),
        )
        self.assertEqual(first.context_provenance, again.context_provenance)
        self.assertEqual(first.context_provenance, rebuilt.context_provenance)
        self.assertEqual("exact_dependency", rebuilt.outcome)
        self.assertNotEqual(
            first.context_provenance, other.context_provenance
        )
        self.assertNotEqual(
            obligation_fingerprint(first), obligation_fingerprint(other)
        )

    def test_in_bound_location_is_retained_verbatim(self):
        short = "$.conditions[0].entity_id"
        item = _located(
            config_path=short,
            context_provenance=(LOCATION_PREFIX + short,),
        )
        self.assertEqual((LOCATION_PREFIX + short,), item.context_provenance)
        self.assertFalse(item.evidence_bounded)
        self.assertEqual("exact_dependency", item.outcome)

    def test_selector_markers_beside_a_relabel_are_kept(self):
        for marker in (
            "entity_set_producer:label_entities",
            "entity_selector_transform:expand",
        ):
            with self.subTest(marker=marker):
                item = _located(
                    context_provenance=(LOCATION_PREFIX + LONG_PATH, marker)
                )
                self.assertEqual("exact_dependency", item.outcome)
                self.assertTrue(item.evidence_bounded)
                self.assertTrue(
                    item.context_provenance[0].startswith(
                        LOCATION_PREFIX + "sha256:"
                    )
                )
                self.assertEqual(marker, item.context_provenance[1])

    # Negatives: every other loss still fails closed.

    def test_foreign_over_bound_value_still_fails(self):
        for value in (
            LOCATION_PREFIX + LONG_PATH + "[1]",
            LOCATION_PREFIX + LONG_PATH.replace("[0]", "[1]", 1),
            LONG_PATH + "x" * MAX_OBLIGATION_VALUE_BYTES,
            "context_path:" + LONG_PATH,
        ):
            with self.subTest(value=value[:40]):
                _assert_coverage_failed(
                    self, _located(context_provenance=(value,))
                )

    def test_sanitizer_altered_own_location_still_fails(self):
        for path in (
            LONG_PATH + ".data.synthetic...<truncated>",
            LONG_PATH
            + ".data.Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
        ):
            with self.subTest(path=path[-24:]):
                item = _located(
                    config_path=path,
                    context_provenance=(LOCATION_PREFIX + path,),
                )
                _assert_coverage_failed(self, item)
                self.assertNotIn(
                    "abcdefghijklmnopqrstuvwxyz012345",
                    str(item.context_provenance),
                )

    def test_non_path_context_loss_beside_a_relabel_still_fails(self):
        for value in (
            "entity_selector_transform:" + "x" * MAX_OBLIGATION_VALUE_BYTES,
            "label_resolution_fingerprint:" + "f" * MAX_OBLIGATION_VALUE_BYTES,
        ):
            with self.subTest(value=value[:30]):
                _assert_coverage_failed(
                    self,
                    _located(
                        context_provenance=(
                            LOCATION_PREFIX + LONG_PATH,
                            value,
                        )
                    ),
                )

    def test_context_aggregate_overflow_beside_a_relabel_still_fails(self):
        values = (LOCATION_PREFIX + LONG_PATH,) + tuple(
            f"context_marker:{index:05d}" for index in range(200)
        )
        item = _located(context_provenance=values)
        _assert_coverage_failed(self, item)
        self.assertTrue(item.context_provenance[-1].startswith("omitted:"))

    def test_target_detail_loss_beside_a_relabel_still_fails(self):
        oversized = "y" * (MAX_OBLIGATION_VALUE_BYTES + 1)
        for overrides in (
            {"literal_selectors": (oversized,)},
            {
                "exact_entity_ids": tuple(
                    f"input_boolean.helper_{index:05d}" for index in range(1000)
                )
            },
            {
                "outcome": "proven_target_exclusion",
                "lock_projection": "none",
                "exact_entity_ids": (),
                "possible_entity_domains": (oversized,),
            },
        ):
            with self.subTest(field=next(iter(overrides))):
                _assert_coverage_failed(self, _located(**overrides))


if __name__ == "__main__":
    unittest.main()
