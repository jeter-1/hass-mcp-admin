"""Beta 45 authoritative helper-risk exclusion and neutrality provenance."""

from __future__ import annotations

from pathlib import Path
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    extract_document_with_obligations,
    resolve_literal_label_obligations,
)
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    AutomationActionRiskProfile,
    DependencyIndexSnapshot,
    OBLIGATION_LEDGER_MODEL,
    SourceCoverageItem,
    obligation_fingerprint,
)
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.obligation_ledger import (  # noqa: E402
    MAX_TEMPLATE_CANDIDATES,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.f3_configuration.locks import (  # noqa: E402
    helper_dependency_lock_key,
    operation_lock_requests,
    unconstrained_helper_dependency_lock_key,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.risk import (  # noqa: E402
    automation_action_consequence_profile,
)
from tests.f3_configuration_fixtures import (  # noqa: E402
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
    valid_config,
)


TARGET = "input_boolean.beta45_target"
UNRELATED = ("sensor.alpha", "sensor.bravo")
SUPPORTED_HA_VERSION = supported_home_assistant_versions()[-1]


def _profile(source_id: str, service: str) -> AutomationActionRiskProfile:
    action: dict[str, object] = {"service": service}
    if service == "cover.open_cover":
        action["target"] = {"entity_id": "cover.synthetic_garage"}
    projected = automation_action_consequence_profile({"action": [action]})
    return AutomationActionRiskProfile(
        source_id=source_id,
        source_entity_id=f"automation.{source_id}",
        risk_level=str(projected["risk_level"]),
        physical_consequence=str(projected["physical_consequence"]),
        complete=bool(projected["complete"]),
        truncated=bool(projected["truncated"]),
        action_domains=tuple(projected["action_domains"]),
        services=tuple(projected["services"]),
        reason_codes=tuple(projected["reason_codes"]),
        effect_projection_model=str(projected["effect_projection_model"]),
        effect_targets=tuple(projected["effect_targets"]),
        effect_data=tuple(projected["effect_data"]),
        effect_structure_fingerprint=str(
            projected["effect_structure_fingerprint"]
        ),
        effect_projection_fingerprint=str(
            projected["effect_projection_fingerprint"]
        ),
        effect_projection_clipped=bool(
            projected["effect_projection_clipped"]
        ),
        evidence_fingerprint=str(projected["evidence_fingerprint"]),
    )


def _extract(
    template: str,
    *,
    source_id: str = "unrelated_cover",
    action: dict[str, object] | None = None,
    trigger: list[dict[str, object]] | None = None,
):
    config: dict[str, object] = {
        "alias": "Synthetic Beta 45 provenance fixture",
        "trigger": trigger or [],
        "condition": [
            {"condition": "template", "value_template": template}
        ],
        "action": action
        or [
            {
                "service": "cover.open_cover",
                "target": {"entity_id": "cover.synthetic_garage"},
            }
        ],
        "mode": "single",
    }
    return config, extract_document_with_obligations(
        source_type="automation",
        source_id=source_id,
        source_entity_id=f"automation.{source_id}",
        source_name="Synthetic Beta 45 provenance fixture",
        source_state="on",
        config=config,
    )[2]


def _snapshot(
    obligations,
    *,
    source_id: str = "unrelated_cover",
    profile_service: str = "cover.open_cover",
) -> DependencyIndexSnapshot:
    return DependencyIndexSnapshot(
        fingerprint="a" * 64,
        generation=45,
        built_at_monotonic=time.monotonic(),
        built_at="2026-08-25T12:00:00+00:00",
        findings=(),
        dynamic_references=(),
        target_metadata={},
        coverage=(
            SourceCoverageItem(
                "automation", "direct_ha_api", "automation_config", "complete"
            ),
            SourceCoverageItem(
                "blueprint", "direct_ha_api", "blueprint_source", "complete"
            ),
        ),
        automation_action_profiles=(
            _profile(source_id, profile_service),
        ),
        obligations=tuple(obligations),
        obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
        home_assistant_version=SUPPORTED_HA_VERSION,
        home_assistant_version_status="observed",
    )


def _binding(obligations, **kwargs):
    return build_helper_dependency_risk_binding(
        _snapshot(obligations, **kwargs),
        entity_id=TARGET,
        index_metadata={
            "freshness": "current",
            "evidence_stale": False,
            "invalidated": False,
        },
    )


class Beta45FiniteProvenanceTests(unittest.TestCase):
    def test_beta44_false_opaque_consequence_reproduction_is_excluded(self):
        _config, obligations = _extract(
            "{{ states(['sensor.alpha', 'sensor.bravo'] "
            "| select('is_state', 'on') | list) }}"
        )

        binding = _binding(obligations)
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 45}}
        )

        self.assertTrue(binding["coverage_complete"])
        self.assertTrue(binding["evidence_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual("exact", binding["semantic_precision"])
        self.assertEqual(0, binding["opaque_obligation_count"])
        self.assertEqual([], binding["relevant_downstream_object_ids"])
        self.assertEqual("none", binding["physical_consequence"])
        self.assertEqual("low", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    def test_finite_candidate_transports_preserve_complete_exclusion(self):
        templates = (
            "{% set entity = 'sensor.alpha' if enabled else 'sensor.bravo' %}"
            "{{ states(entity) }}",
            "{% for entity in ['sensor.alpha', 'sensor.bravo'] %}"
            "{{ states(entity) }}{% endfor %}",
            "{% set values = {'first':'sensor.alpha','second':'sensor.bravo'} %}"
            "{{ states(values.first if enabled else values['second']) }}",
            "{{ states((['sensor.alpha'], ['sensor.bravo'])[0][0]) }}",
        )
        for template in templates:
            with self.subTest(template=template):
                _config, obligations = _extract(template)
                binding = _binding(obligations)
                self.assertTrue(binding["evidence_complete"], obligations)
                self.assertEqual(0, binding["opaque_obligation_count"])
                self.assertEqual(
                    [], binding["relevant_downstream_object_ids"]
                )

    def test_complete_candidates_containing_target_remain_consequential(self):
        _config, obligations = _extract(
            "{{ states(['sensor.alpha', 'input_boolean.beta45_target'] "
            "| select('is_state', 'on') | list) }}"
        )
        binding = _binding(obligations)
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 45}}
        )

        self.assertTrue(binding["evidence_complete"])
        self.assertIn(
            "automation.unrelated_cover",
            binding["relevant_downstream_object_ids"],
        )
        self.assertEqual("safety_critical", binding["physical_consequence"])
        self.assertEqual("high", risk.level.value)

    def test_complete_non_helper_domains_exclude_target(self):
        for template in (
            "{{ states.sensor | list }}",
            "{{ states.binary_sensor | list }}",
            "{{ (states.sensor if enabled else states.binary_sensor) | list }}",
        ):
            with self.subTest(template=template):
                _config, obligations = _extract(template)
                binding = _binding(obligations)
                self.assertTrue(binding["evidence_complete"], obligations)
                self.assertEqual(0, binding["opaque_obligation_count"])
                self.assertEqual([], binding["relevant_downstream_object_ids"])

    def test_unresolved_candidates_remain_opaque_and_consequential(self):
        templates = (
            "{{ states(caller_supplied_selector) }}",
            "{{ states('sensor.alpha' if enabled else caller_supplied_selector) }}",
            "{{ states | list }}",
            "{{ label_entities(caller_supplied_label) | list }}",
            "{% include caller_supplied_template %}",
            "{{ unknown_callable('sensor.alpha') }}",
        )
        for template in templates:
            with self.subTest(template=template):
                _config, obligations = _extract(template)
                binding = _binding(obligations)
                self.assertFalse(binding["evidence_complete"], obligations)
                self.assertFalse(binding["execution_eligible"], obligations)
                self.assertGreater(binding["opaque_obligation_count"], 0)
                self.assertEqual(
                    "safety_critical", binding["physical_consequence"]
                )
                risk = helper_dependency_risk_assessment(
                    {"binding": binding, "provenance": {"generation": 45}}
                )
                self.assertFalse(risk.apply_allowed)
                self.assertEqual("high", risk.level.value)

    def test_candidate_overflow_remains_nonactionable_coverage_failure(self):
        candidates = ",".join(
            f"'sensor.fixture_{index}'"
            for index in range(MAX_TEMPLATE_CANDIDATES + 1)
        )
        _config, obligations = _extract("{{ states([" + candidates + "]) }}")
        binding = _binding(obligations)
        self.assertFalse(binding["coverage_complete"])
        self.assertFalse(binding["execution_eligible"])
        self.assertGreater(binding["coverage_failure_count"], 0)

    def test_exact_unrelated_state_time_and_neutral_time_are_proportionate(self):
        _config, obligations = _extract(
            "{{ states.sensor.alpha.last_changed + now() }}"
        )
        binding = _binding(obligations)
        self.assertTrue(binding["evidence_complete"])
        self.assertEqual([], binding["relevant_downstream_object_ids"])
        self.assertEqual("none", binding["physical_consequence"])

        _config, neutral = _extract(
            "{{ now().timestamp() + 60 }}",
            action=[{"service": "notify.notify", "data": {"message": "ok"}}],
        )
        neutral_binding = _binding(neutral, profile_service="notify.notify")
        self.assertTrue(neutral_binding["evidence_complete"])
        self.assertEqual(0, neutral_binding["opaque_obligation_count"])

    def test_fixed_trigger_state_time_provenance_excludes_target(self):
        _config, obligations = _extract(
            "{{ trigger.to_state.last_changed.timestamp() + "
            "(now() - trigger.to_state.last_changed).total_seconds() }}",
            trigger=[
                {
                    "platform": "state",
                    "entity_id": "sensor.alpha",
                }
            ],
        )

        binding = _binding(obligations)

        self.assertTrue(binding["evidence_complete"], obligations)
        self.assertTrue(binding["execution_eligible"], obligations)
        self.assertEqual([], binding["relevant_downstream_object_ids"])
        self.assertEqual("none", binding["physical_consequence"])

    def test_exclusion_does_not_hide_independent_structured_target_edge(self):
        _config, obligations = _extract(
            "{{ states(['sensor.alpha', 'sensor.bravo']) }}",
            trigger=[
                {
                    "platform": "state",
                    "entity_id": TARGET,
                }
            ],
        )
        binding = _binding(obligations)
        self.assertIn(
            "automation.unrelated_cover",
            binding["relevant_downstream_object_ids"],
        )
        self.assertEqual("safety_critical", binding["physical_consequence"])

    def test_exclusion_does_not_hide_independent_template_target_edge(self):
        action = [
            {
                "service": "cover.open_cover",
                "target": {"entity_id": "cover.synthetic_garage"},
                "data": {"message": "{{ states('" + TARGET + "') }}"},
            }
        ]
        _config, obligations = _extract(
            "{{ states(['sensor.alpha', 'sensor.bravo']) }}",
            action=action,
        )

        binding = _binding(obligations)

        self.assertTrue(binding["evidence_complete"], obligations)
        self.assertIn(
            "automation.unrelated_cover",
            binding["relevant_downstream_object_ids"],
        )
        self.assertEqual("safety_critical", binding["physical_consequence"])


class Beta45LiteralLabelTests(unittest.TestCase):
    def _raw(self):
        return _extract(
            "{% for entity in label_entities('reviewed_label') %}"
            "{{ states(entity) }}{% endfor %}"
        )[1]

    def _resolve(self, members, *, complete=True, truncated=()):
        return resolve_literal_label_obligations(
            self._raw(),
            label_memberships={"reviewed_label": tuple(members)},
            label_membership_fingerprints={"reviewed_label": "b" * 64},
            label_membership_truncated=truncated,
            label_registry_complete=complete,
        )

    def test_complete_literal_label_excludes_or_includes_exact_target(self):
        excluding = _binding(self._resolve(UNRELATED))
        self.assertTrue(excluding["evidence_complete"])
        self.assertEqual([], excluding["relevant_downstream_object_ids"])

        including = _binding(self._resolve((UNRELATED[0], TARGET)))
        self.assertTrue(including["evidence_complete"])
        self.assertIn(
            "automation.unrelated_cover",
            including["relevant_downstream_object_ids"],
        )

    def test_empty_complete_label_is_neutral_but_other_helper_is_excluded(self):
        empty = _binding(self._resolve(()))
        other_helper = _binding(
            self._resolve(("input_boolean.some_other_helper",))
        )
        self.assertTrue(empty["evidence_complete"])
        self.assertTrue(other_helper["evidence_complete"])
        self.assertEqual([], empty["relevant_downstream_object_ids"])
        self.assertEqual([], other_helper["relevant_downstream_object_ids"])

    def test_failed_or_truncated_label_evidence_remains_opaque(self):
        oversized = tuple(
            f"sensor.label_member_{index}"
            for index in range(MAX_TEMPLATE_CANDIDATES + 1)
        )
        for obligations in (
            self._resolve(UNRELATED, complete=False),
            self._resolve(UNRELATED, truncated=("reviewed_label",)),
            self._resolve(oversized),
        ):
            with self.subTest(obligations=obligations):
                binding = _binding(obligations)
                self.assertFalse(binding["evidence_complete"])
                self.assertGreater(binding["opaque_obligation_count"], 0)

    def test_membership_and_provenance_change_fingerprints(self):
        first = self._resolve(("sensor.alpha",))
        second = self._resolve(("sensor.bravo",))
        self.assertNotEqual(
            [obligation_fingerprint(item) for item in first],
            [obligation_fingerprint(item) for item in second],
        )
        self.assertNotEqual(
            _binding(first)["evidence_fingerprint"],
            _binding(second)["evidence_fingerprint"],
        )
        self.assertEqual(
            _binding(first)["evidence_fingerprint"],
            _binding(first)["evidence_fingerprint"],
        )


class Beta45LabelProviderTests(unittest.IsolatedAsyncioTestCase):
    class Rest:
        async def request(self, method, path):
            if path == "/config":
                return {"version": SUPPORTED_HA_VERSION}
            if path == "/states":
                return [
                    {
                        "entity_id": "automation.label_cover",
                        "state": "on",
                        "attributes": {
                            "id": "label_cover",
                            "friendly_name": "Synthetic label cover",
                        },
                    }
                ]
            if path == "/config/automation/config/label_cover":
                return {
                    "alias": "Synthetic label cover",
                    "trigger": [],
                    "condition": [
                        {
                            "condition": "template",
                            "value_template": (
                                "{% for entity in "
                                "label_entities('reviewed_label') %}"
                                "{{ states(entity) }}{% endfor %}"
                            ),
                        }
                    ],
                    "action": [
                        {
                            "service": "cover.open_cover",
                            "target": {
                                "entity_id": "cover.synthetic_garage"
                            },
                        }
                    ],
                    "mode": "single",
                }
            raise AssertionError(path)

    class WebSocket:
        async def command(self, payload):
            if payload == {"type": "config/entity_registry/list"}:
                return [
                    {
                        "entity_id": "sensor.alpha",
                        "labels": ["reviewed_label_id"],
                    }
                ]
            if payload == {"type": "config/label_registry/list"}:
                return [
                    {
                        "label_id": "reviewed_label_id",
                        "name": "reviewed_label",
                    }
                ]
            raise AssertionError(payload)

    async def test_index_construction_resolves_label_before_projection(self):
        result = await DirectHaDependencyProvider(
            self.Rest(), self.WebSocket(), concurrency=2
        ).scan()

        label_obligations = [
            item
            for item in result.obligations
            if "entity_set_producer:label_entities"
            in item.context_provenance
        ]
        self.assertTrue(label_obligations)
        self.assertTrue(
            all(item.outcome == "exact_dependency" for item in label_obligations)
        )
        self.assertTrue(
            all(
                item.exact_entity_ids == ("sensor.alpha",)
                for item in label_obligations
            )
        )
        self.assertFalse(
            any(
                item.literal_label_selectors == ("reviewed_label",)
                and not item.candidate_resolution_complete
                for item in result.dynamic_references
            )
        )
        binding = build_helper_dependency_risk_binding(
            DependencyIndexSnapshot(
                fingerprint="c" * 64,
                generation=45,
                built_at_monotonic=time.monotonic(),
                built_at="2026-08-25T12:00:00+00:00",
                findings=tuple(result.findings),
                dynamic_references=tuple(result.dynamic_references),
                target_metadata=result.target_metadata,
                coverage=tuple(result.coverage),
                automation_action_profiles=tuple(
                    result.automation_action_profiles
                ),
                obligations=tuple(result.obligations),
                obligation_ledger_model=result.obligation_ledger_model,
                label_memberships=result.label_memberships,
                label_membership_fingerprints=(
                    result.label_membership_fingerprints
                ),
                label_registry_complete=result.label_registry_complete,
                home_assistant_version=result.home_assistant_version,
                home_assistant_version_status=(
                    result.home_assistant_version_status
                ),
            ),
            entity_id=TARGET,
            index_metadata={
                "freshness": "current",
                "evidence_stale": False,
                "invalidated": False,
            },
        )
        self.assertTrue(binding["evidence_complete"])
        self.assertEqual([], binding["relevant_downstream_object_ids"])
        self.assertEqual("none", binding["physical_consequence"])


class Beta45ConfigurationLockTests(unittest.IsolatedAsyncioTestCase):
    async def _lock_keys(self, template: str, *, action: str) -> set[str]:
        proposed = valid_config("automation")
        proposed["condition"] = [
            {"condition": "template", "value_template": template}
        ]
        current = None
        if action == "update":
            current = valid_config("automation")
            current["condition"] = [
                {"condition": "template", "value_template": template}
            ]
        gateway = SyntheticConfigurationGateway(
            ({("automation", "porch_light"): current} if current else {})
        )
        prepared = await adapter_for(
            "automation", action, gateway
        ).prepare(
            proposal_for(
                "automation",
                action,
                current_config=current,
                proposed_config=proposed,
            )
        )
        return {item.key for item in operation_lock_requests(prepared)}

    async def test_exact_conservative_and_excluded_locks_match_evidence(self):
        exact = "{{ states(['sensor.alpha', '" + TARGET + "']) }}"
        excluded = "{{ states(['sensor.alpha', 'sensor.bravo'] | list) }}"
        opaque = "{{ states(caller_supplied_selector) }}"
        for action in ("create", "update"):
            with self.subTest(action=action):
                exact_keys = await self._lock_keys(exact, action=action)
                excluded_keys = await self._lock_keys(excluded, action=action)
                opaque_keys = await self._lock_keys(opaque, action=action)
                self.assertIn(helper_dependency_lock_key(TARGET), exact_keys)
                self.assertNotIn(
                    unconstrained_helper_dependency_lock_key(), exact_keys
                )
                self.assertFalse(
                    any(
                        key.startswith("helper_dependency:")
                        for key in excluded_keys
                    )
                )
                self.assertIn(
                    unconstrained_helper_dependency_lock_key(), opaque_keys
                )


if __name__ == "__main__":
    unittest.main()
