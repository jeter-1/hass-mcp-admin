"""HAMCP-301 Option A: per-record attribution of rejected expand-snapshot states.

Live finding (2.4.1-rc.1): ``binary_sensor.1_1_1_1`` and
``binary_sensor.8_8_8_8`` are valid Home Assistant entity ids that the
dependency ``valid_entity_id`` rule rejects (it requires a letter in the
object id).  One rejected ``/api/states`` record marked the whole expand
snapshot incomplete, so every input_boolean and input_select was held
``group_membership_unresolved`` (``group_membership_state_inventory_incomplete``)
and every ``expand()`` with an absent member became a coverage failure named
after the snapshot bound.

A rejected record now decides nothing only when it is attributable: a dict
whose ``entity_id`` matches Core's grammar (homeassistant/core.py:180-183,
pinned 2026.10.3), whose domain is neither ``group`` nor ``zone`` (the only
``expand`` containers besides group-sourced entities, state.py:145-185 and
const.py:461), and whose attributes are a dict without ``entity_id``.  For a
target, it must also be outside the target's own domain.  Everything else,
and an over-bound state list, still holds.

Service tests drive the shipped governance service over a real
``DependencyIndex`` of a synthetic installation.  No test reaches a network,
a live Home Assistant, or the ha-mcp provider.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    ExpandSnapshotEvidence,
    _resolve_expand_candidates,
    resolve_expand_obligations,
)
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
    _build_expand_snapshot_evidence,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    helper_dependency as helper_module,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HelperDependencyRiskService,
)
from ha_mcp_engineering.governance.input_boolean_dependency import (  # noqa: E402
    build_input_boolean_dependency_binding,
)

from tests import hamcp285_fixtures as boolean_fixtures  # noqa: E402
from tests import test_beta39_obligation_governance as ledger  # noqa: E402
from tests import test_beta50_helper_production_target_scope as beta50  # noqa: E402
from tests import test_hamcp285_group_membership_hold as hold285  # noqa: E402


# Literal expectations, not values imported from the code under test.
UNATTRIBUTED = "group_membership_consumers_unattributed"
UNRESOLVED = "group_membership_unresolved"
INCOMPLETE = "group_membership_state_inventory_incomplete"
LIMIT = "expand_state_inventory_limit_exceeded"
MISSING = "expand_member_state_missing"
BOOLEAN_TARGET = "input_boolean.unit_target"
SELECT_TARGET = "input_select.unit_target"
SECRET_ATTRIBUTE = "synthetic-hamcp301-attribute-value"

# The two live records, as /api/states returns connectivity ping sensors.
LIVE_REJECTED = [
    {"entity_id": "binary_sensor.1_1_1_1", "state": "on",
     "attributes": {"device_class": "connectivity",
                    "friendly_name": SECRET_ATTRIBUTE}},
    {"entity_id": "binary_sensor.8_8_8_8", "state": "on",
     "attributes": {"device_class": "connectivity"}},
]


def state(entity_id: Any, attributes: Any = None) -> dict[str, Any]:
    return {"entity_id": entity_id, "state": "on",
            "attributes": {} if attributes is None else attributes}


def snapshot(extra: list[Any], *, base: list[Any] | None = None) -> ExpandSnapshotEvidence:
    states = [
        state("group.family", {"entity_id": ["person.a"]}),
        state("light.a"),
        state(BOOLEAN_TARGET),
        state(SELECT_TARGET, {"options": ["a", "b"]}),
    ] if base is None else base
    return _build_expand_snapshot_evidence(
        states=[*states, *extra],
        entity_registry=[{"entity_id": "light.a", "platform": "hue"}],
        entity_registry_complete=True,
    )


def hold(evidence: ExpandSnapshotEvidence, target: str) -> dict[str, Any] | None:
    return helper_module._group_membership_hold(
        SimpleNamespace(expand_snapshot_evidence=evidence), target
    )


def unresolved(evidence: ExpandSnapshotEvidence, target: str) -> list[str]:
    # A released target yields ``[]`` here, an assertion failure rather
    # than an error in the test that expected a hold.
    return (hold(evidence, target) or {}).get("unresolved_reason_codes") or []


# Each held case is rejected by the snapshot and fails exactly one part of
# the rule, so dropping that part releases it.
HELD_FOR_BOTH = {
    "group_domain": [state("group.1_2")],
    "zone_domain_with_persons": [state("zone.1_2", {"persons": ["person.a"]})],
    "entity_id_attribute": [state("binary_sensor.1_2", {"entity_id": []})],
    "attributes_not_a_dict": [state("binary_sensor.1_2", ["x"])],
    "attributes_missing": [{"entity_id": "binary_sensor.1_2", "state": "on"}],
    "record_not_a_dict": [["binary_sensor.1_2"]],
    "entity_id_not_a_string": [state(12)],
    "entity_id_missing": [{"state": "on", "attributes": {}}],
    "core_invalid_double_underscore": [state("sensor.__1")],
    "core_invalid_leading_underscore": [state("_sensor.1")],
    "core_invalid_upper_case": [state("Sensor.1")],
    "core_invalid_trailing_newline": [state("sensor.1\n")],
    "core_invalid_too_long": [state("sensor." + "1" * 249)],
    "duplicate_group": [state("group.family", {"entity_id": ["person.a"]})],
    "duplicate_with_entity_id_attribute": [state("light.a", {"entity_id": []})],
    "one_unattributable_among_live_records": [*LIVE_REJECTED, state("group.1_2")],
}


class AttributionEvidenceTests(unittest.TestCase):
    def test_the_live_records_are_attributed_without_attribute_values(self):
        evidence = snapshot(LIVE_REJECTED)
        self.assertFalse(evidence.state_inventory_complete)
        rejections = evidence.state_rejections
        self.assertIsNotNone(rejections)
        self.assertEqual(rejections.attributable_entity_ids,
                         ("binary_sensor.1_1_1_1", "binary_sensor.8_8_8_8"))
        self.assertEqual(rejections.unattributable_count, 0)
        self.assertEqual(len(rejections.fingerprint), 64)
        self.assertNotIn(SECRET_ATTRIBUTE, repr(rejections))
        self.assertNotIn("connectivity", repr(rejections))
        # The rejected records are still not projected as expand entities.
        self.assertNotIn("binary_sensor.1_1_1_1", evidence.entities)

    def test_the_fingerprint_binds_the_attribution(self):
        one = snapshot(LIVE_REJECTED[:1]).state_rejections
        both = snapshot(LIVE_REJECTED).state_rejections
        again = snapshot(list(reversed(LIVE_REJECTED))).state_rejections
        held = snapshot([*LIVE_REJECTED, state("group.1_2")]).state_rejections
        self.assertNotEqual(one.fingerprint, both.fingerprint)
        self.assertEqual(both.fingerprint, again.fingerprint)
        self.assertNotEqual(both.fingerprint, held.fingerprint)

    def test_a_complete_inventory_records_no_rejection(self):
        evidence = snapshot([])
        self.assertTrue(evidence.state_inventory_complete)
        self.assertEqual(evidence.state_rejections.attributable_entity_ids, ())
        self.assertEqual(evidence.state_rejections.unattributable_count, 0)

    def test_each_unattributable_record_is_counted(self):
        for name, extra in HELD_FOR_BOTH.items():
            with self.subTest(case=name):
                evidence = snapshot(extra)
                self.assertFalse(evidence.state_inventory_complete)
                self.assertGreaterEqual(
                    evidence.state_rejections.unattributable_count, 1
                )

    def test_an_over_bound_state_list_is_not_attributed(self):
        many = [state(f"sensor.s{n}") for n in range(10_001)]
        evidence = snapshot([], base=many)
        self.assertFalse(evidence.state_inventory_complete)
        self.assertIsNone(evidence.state_rejections)
        self.assertEqual(evidence.entities, {})
        exact = snapshot([], base=many[:10_000])
        self.assertTrue(exact.state_inventory_complete)
        self.assertIsNotNone(exact.state_rejections)


class MembershipAttributionTests(unittest.TestCase):
    def test_live_records_release_both_helper_domains(self):
        evidence = snapshot(LIVE_REJECTED)
        for target in (BOOLEAN_TARGET, SELECT_TARGET):
            with self.subTest(target=target):
                self.assertIsNone(hold(evidence, target))

    def test_every_unattributable_rejection_still_holds(self):
        for name, extra in HELD_FOR_BOTH.items():
            for target in (BOOLEAN_TARGET, SELECT_TARGET):
                with self.subTest(case=name, target=target):
                    held = hold(snapshot(extra), target)
                    self.assertIsNotNone(held)
                    self.assertEqual((held or {}).get("reason_code"), UNRESOLVED)
                    self.assertIn(INCOMPLETE, unresolved(snapshot(extra), target))

    def test_a_rejected_target_domain_record_holds_only_that_domain(self):
        # Per target, not global: the other helper domain stays released.
        boolean = snapshot([state("input_boolean.1_2")])
        self.assertEqual(unresolved(boolean, BOOLEAN_TARGET),
                         [INCOMPLETE])
        self.assertIsNone(hold(boolean, SELECT_TARGET))
        select = snapshot([state("input_select.1_2", {"options": ["a"]})])
        self.assertEqual(unresolved(select, SELECT_TARGET),
                         [INCOMPLETE])
        self.assertIsNone(hold(select, BOOLEAN_TARGET))

    def test_a_duplicate_target_domain_record_holds_that_domain(self):
        evidence = snapshot([state(BOOLEAN_TARGET)])
        self.assertEqual(unresolved(evidence, BOOLEAN_TARGET),
                         [INCOMPLETE])
        self.assertIsNone(hold(evidence, SELECT_TARGET))

    def test_a_duplicate_of_a_group_sourced_record_holds(self):
        # The retained first copy carries ``entity_id``; the later clean copy
        # cannot attribute the conflict away from a group-sourced entity.
        base = [
            state("light.room", {"entity_id": [BOOLEAN_TARGET]}),
            state(BOOLEAN_TARGET),
            state(SELECT_TARGET, {"options": ["a", "b"]}),
        ]
        evidence = snapshot([state("light.room")], base=base)
        self.assertEqual(evidence.state_rejections.unattributable_count, 1)
        for target in (BOOLEAN_TARGET, SELECT_TARGET):
            with self.subTest(target=target):
                self.assertIn(INCOMPLETE,
                              unresolved(evidence, target))

    def test_an_over_bound_state_list_holds(self):
        many = [state(f"sensor.s{n}") for n in range(10_001)]
        for target in (BOOLEAN_TARGET, SELECT_TARGET):
            with self.subTest(target=target):
                held = hold(snapshot([], base=many), target)
                self.assertEqual((held or {}).get("reason_code"), UNRESOLVED)
                self.assertEqual((held or {}).get("unresolved_reason_codes"), [INCOMPLETE])

    def test_unattributed_evidence_holds(self):
        # Evidence without attribution (None) keeps the earlier behaviour.
        evidence = ExpandSnapshotEvidence(
            entities={}, state_inventory_complete=False,
            source_inventory_complete=True,
        )
        self.assertEqual(unresolved(evidence, BOOLEAN_TARGET),
                         [INCOMPLETE])

    def test_a_real_group_member_is_still_held(self):
        evidence = snapshot([
            *LIVE_REJECTED, state("group.helpers", {"entity_id": [BOOLEAN_TARGET]}),
        ])
        held = hold(evidence, BOOLEAN_TARGET)
        self.assertEqual((held or {}).get("reason_code"), UNATTRIBUTED)
        self.assertTrue((held or {}).get("membership_resolved"))
        self.assertEqual(
            [item["group_entity_id"] for item in (held or {}).get("groups", [])],
            ["group.helpers"],
        )


class ExpandAttributionTests(unittest.TestCase):
    def resolve(self, extra: list[Any], members: list[str]):
        evidence = snapshot([
            *extra, state("group.x", {"entity_id": members}),
        ])
        return _resolve_expand_candidates(("group.x",), evidence)

    def test_resolvable_members_are_decided(self):
        leaves, material, complete, limit = self.resolve(LIVE_REJECTED, ["light.a"])
        self.assertTrue(complete)
        self.assertFalse(limit)
        self.assertEqual(leaves, ("light.a",))
        self.assertEqual(material["failure_reasons"], [])

    def test_an_absent_member_is_missing_not_a_bound(self):
        _, material, complete, limit = self.resolve(
            LIVE_REJECTED, ["light.a", "light.gone"]
        )
        self.assertFalse(complete)
        self.assertFalse(limit)
        self.assertEqual(material["failure_reasons"], [MISSING])
        # Identical to a complete inventory apart from the bound attribution.
        _, clean, _, clean_limit = self.resolve([], ["light.a", "light.gone"])
        self.assertEqual(clean["failure_reasons"], [MISSING])
        self.assertFalse(clean_limit)
        self.assertNotIn("state_rejection_fingerprint", clean)
        self.assertEqual(
            material.get("state_rejection_fingerprint"),
            snapshot(LIVE_REJECTED).state_rejections.fingerprint,
        )

    def test_an_unattributable_rejection_keeps_the_bound(self):
        for name, extra in HELD_FOR_BOTH.items():
            if name == "duplicate_group":
                continue  # group.family is present, so no member is lost
            with self.subTest(case=name):
                _, material, complete, limit = self.resolve(
                    extra, ["light.a", "light.gone"]
                )
                self.assertFalse(complete)
                self.assertTrue(limit)
                self.assertIn(LIMIT, material["failure_reasons"])

    def test_an_over_bound_state_list_keeps_the_bound(self):
        many = [state(f"sensor.s{n}") for n in range(10_001)]
        evidence = snapshot([], base=many)
        _, material, complete, limit = _resolve_expand_candidates(
            ("group.x",), evidence
        )
        self.assertFalse(complete)
        self.assertTrue(limit)
        self.assertEqual(material["failure_reasons"], [LIMIT])
        self.assertIn("state_rejection_fingerprint", material)
        self.assertIsNone(material.get("state_rejection_fingerprint"))

    def test_a_rejected_record_is_never_a_resolved_member(self):
        # The rejected id is not a valid member, so its group stays opaque.
        _, material, complete, limit = self.resolve(
            LIVE_REJECTED, ["binary_sensor.1_1_1_1"]
        )
        self.assertFalse(complete)
        self.assertFalse(limit)
        self.assertEqual(material["failure_reasons"], ["expand_membership_malformed"])

    def test_the_ledger_obligation_is_opaque_not_a_coverage_failure(self):
        base = replace(
            ledger.obligation(
                "bounded_semantic_opaque",
                exact=("group.x",),
                reason="entity_selector_transform_unresolved",
            ),
            context_provenance=("entity_selector_transform:expand",),
        )
        evidence = snapshot([
            *LIVE_REJECTED, state("group.x", {"entity_id": ["light.a", "light.gone"]}),
        ])
        [item] = resolve_expand_obligations([base], expand_snapshot_evidence=evidence)
        self.assertEqual(item.outcome, "bounded_semantic_opaque")
        self.assertEqual(item.reason_code, MISSING)
        self.assertFalse(item.limit_exceeded)
        self.assertEqual(item.target_selector_scope, "target_capable")
        held = snapshot([
            state("group.1_2"),
            state("group.x", {"entity_id": ["light.a", "light.gone"]}),
        ])
        [bound] = resolve_expand_obligations([base], expand_snapshot_evidence=held)
        self.assertEqual(bound.outcome, "coverage_failure")
        self.assertEqual(bound.reason_code, LIMIT)
        self.assertTrue(bound.limit_exceeded)
        self.assertEqual(bound.target_selector_scope, "coverage_failure")


# --------------------------------------------------------------------------
# Service level: the shipped planner over a real index
# --------------------------------------------------------------------------


class ServiceAttributionAssertions:
    """Mixed ahead of a HAMCP-285 harness, which supplies ``plan_with``."""

    async def test_live_records_leave_an_unreferenced_helper_standard(self):
        plan, binding = await self.plan_with({}, LIVE_REJECTED)
        self.assert_not_held(plan, binding)
        decision = plan["policy_decision"]
        self.assertEqual(
            (decision["policy_class"], decision["physical_consequence"],
             plan["risk"]["level"]),
            ("standard_admin", "none", "low"),
        )
        self.assertFalse(
            binding["dependency_lock_projection"]["coarse_automation_fence"]
        )

    async def test_a_rejected_group_record_still_holds(self):
        plan, binding = await self.plan_with(
            {}, [*LIVE_REJECTED, state("group.1_2")]
        )
        self.assert_held(plan, binding, groups=[], reason=UNRESOLVED,
                         unresolved=(INCOMPLETE,))

    async def test_a_rejected_target_domain_record_still_holds(self):
        domain = self.entity.split(".", 1)[0]
        plan, binding = await self.plan_with(
            {}, [*LIVE_REJECTED, state(f"{domain}.1_2")]
        )
        self.assert_held(plan, binding, groups=[], reason=UNRESOLVED,
                         unresolved=(INCOMPLETE,))

    async def test_a_real_group_member_is_still_held(self):
        plan, binding = await self.plan_with(
            {"group_consumer": hold285.group_consumer("state_trigger")},
            [*LIVE_REJECTED, hold285.group_state("group.x", [self.entity])],
        )
        self.assert_held(plan, binding, groups=[("group.x", "direct", 1)])

    async def test_live_shaped_acceptance_helper_loses_only_the_group_code(self):
        # engineering_acceptance on rc.1: unreferenced, in no group, attached
        # to an opaque dynamic template consumer, with the two live records.
        domain = self.entity.split(".", 1)[0]
        configs = {"dynamic": boolean_fixtures.consumer(
            "Dynamic",
            conditions=boolean_fixtures.template(
                "{{ is_state('%s.' ~ states('sensor.which'), 'on') }}" % domain
            ),
        )}
        plan, binding = await self.plan_with(configs, LIVE_REJECTED)
        self.assert_not_held(plan, binding)
        self.assertEqual(plan["policy_decision"]["policy_class"], "elevated_admin")
        self.assertEqual(plan["risk"]["level"], "high")
        self.assertTrue(plan["approval_actionable"])
        self.assertIn("opaque_downstream_obligations",
                      binding["consequence_uncertainty_reason_codes"])
        self.assertTrue(binding["execution_contract_complete"])
        # Without attribution the same installation also carried the hold.
        before_plan, before = await self.plan_with(
            configs, [*LIVE_REJECTED, state("sensor.__1")]
        )
        self.assertIn(UNRESOLVED, before["consequence_uncertainty_reason_codes"])
        self.assertEqual(before_plan["policy_decision"]["policy_class"],
                         "elevated_admin")


class BooleanServiceAttributionTests(ServiceAttributionAssertions,
                                     hold285.BooleanGroupCase):
    pass


class BooleanServiceAttributionNoiseTests(BooleanServiceAttributionTests):
    noise = True


class SelectServiceAttributionTests(ServiceAttributionAssertions,
                                    hold285.SelectGroupCase):
    pass


class SelectServiceAttributionNoiseTests(SelectServiceAttributionTests):
    noise = True


# --------------------------------------------------------------------------
# guest_mode stays safety-critical with an unrelated rejected record present
# --------------------------------------------------------------------------


class RejectedRecordBeta50Rest(beta50.SyntheticBeta50Rest):
    async def request(self, method: str, path: str):
        result = await super().request(method, path)
        if path == "/states":
            return [*result, *LIVE_REJECTED]
        return result


class GuestModeTests(unittest.IsolatedAsyncioTestCase):
    async def binding(self, rest) -> dict[str, Any]:
        index = DependencyIndex(
            DirectHaDependencyProvider(rest, beta50.SyntheticBeta50WebSocket())
        )
        evidence = await HelperDependencyRiskService(
            index, binding_builder=build_input_boolean_dependency_binding
        ).assess(beta50.CONSEQUENTIAL_TARGET, refresh=True)
        snapshot_evidence = index.current_snapshot().expand_snapshot_evidence
        return evidence["binding"], snapshot_evidence

    async def test_guest_mode_is_unchanged_by_an_unrelated_rejected_record(self):
        binding, evidence = await self.binding(RejectedRecordBeta50Rest())
        self.assertFalse(evidence.state_inventory_complete)
        self.assertEqual(evidence.state_rejections.attributable_entity_ids,
                         ("binary_sensor.1_1_1_1", "binary_sensor.8_8_8_8"))
        self.assertEqual(7, binding["exact_dependency_obligation_count"])
        self.assertEqual(7, len(binding["downstream_profiles"]))
        self.assertEqual("safety_critical", binding["physical_consequence"])
        self.assertTrue(binding["consequence_evidence_complete"])
        self.assertTrue(binding["execution_contract_complete"])
        self.assertNotIn("group_membership_hold", binding)
        clean, clean_evidence = await self.binding(beta50.SyntheticBeta50Rest())
        self.assertTrue(clean_evidence.state_inventory_complete)
        for key in ("exact_dependency_obligation_count", "physical_consequence",
                    "potential_physical_consequence", "downstream_profiles",
                    "consequence_uncertainty_reason_codes",
                    "relevant_downstream_object_ids"):
            self.assertEqual(binding[key], clean[key], key)


if __name__ == "__main__":
    unittest.main()
