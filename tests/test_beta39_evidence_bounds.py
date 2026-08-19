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

    def test_exact_dependency_becomes_opaque_and_conservative(self):
        item = _obligation(
            outcome="exact_dependency",
            lock_projection="exact",
            exact_entity_ids=("input_boolean.example",),
            literal_selectors=("y" * (MAX_OBLIGATION_VALUE_BYTES + 1),),
        )
        self.assertEqual("bounded_semantic_opaque", item.outcome)
        self.assertEqual("conservative", item.lock_projection)
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
        self.assertEqual("bounded_semantic_opaque", item.outcome)
        self.assertEqual("conservative", item.lock_projection)

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


if __name__ == "__main__":
    unittest.main()
