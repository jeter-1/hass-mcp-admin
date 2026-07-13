from dataclasses import asdict
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    ENTITY_TEMPLATE_HELPERS,
    extract_document,
    valid_entity_id,
)
from ha_mcp_engineering.integrity.models import (  # noqa: E402
    FINDING_TYPES,
    IntegrityEvidenceBundle,
)
from ha_mcp_engineering.integrity.rules import classify_integrity  # noqa: E402
from ha_mcp_engineering.integrity.service import (  # noqa: E402
    ConfigurationIntegrityAnalysisService,
)
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.providers import (  # noqa: E402
    ProviderCapability,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderResult,
)


OBSERVED_FALSE_POSITIVES = {
    "1.1",
    "8.8",
    "c.id",
    "c.limit",
    "c.name",
    "grace.get",
    "ns.bad",
    "ns.ids",
    "ns.lines",
}


def extract(config):
    return extract_document(
        source_type="automation",
        source_id="beta18-classifier",
        source_entity_id="automation.beta18_classifier",
        source_name="Beta 18 classifier",
        source_state="on",
        config=config,
    )


class CanonicalEntityIdTests(unittest.TestCase):
    def test_valid_literals_include_custom_integration_domains(self):
        for value in (
            "sensor.missing_example",
            "binary_sensor.missing_example",
            "input_boolean.missing_example",
            "customdomain.missing_example",
            "domain2.object_2",
        ):
            with self.subTest(value=value):
                self.assertTrue(valid_entity_id(value))

    def test_noncanonical_values_are_rejected(self):
        values = {
            "",
            "sensor",
            ".missing",
            "sensor.",
            "sensor.bad.extra",
            "Sensor.example",
            "sensor.Example",
            " sensor.example",
            "sensor.example ",
            "sensor.bad value",
            "sensor.bad-value",
            "1.1",
            "8.8",
            "2026.7.2",
            "192.168.1.10",
            "https://example.com/path",
            "{{ sensor.example }}",
        }
        for value in values:
            with self.subTest(value=value):
                self.assertFalse(valid_entity_id(value))


class TrustedContextExtractionTests(unittest.TestCase):
    def test_deployed_false_positive_tokens_are_ignored_in_variable_template(self):
        tokens = ", ".join(sorted(OBSERVED_FALSE_POSITIVES))
        findings, dynamic = extract(
            {
                "variables": {
                    "diagnostics": "{{ [" + tokens + "] }}",
                    "object_summary": "{{ c.id ~ c.name ~ c.limit }}",
                    "namespace_summary": "{{ ns.bad ~ ns.ids ~ ns.lines }}",
                }
            }
        )
        self.assertEqual(findings, [])
        self.assertEqual(dynamic, [])

    def test_message_template_reports_only_recognized_dynamic_entity_expression(self):
        findings, dynamic = extract(
            {
                "action": [
                    {
                        "service": "notify.example",
                        "data": {
                            "message": (
                                "{{ c.id }} {{ ns.lines }} "
                                "{{ states(target_entity) }}"
                            )
                        },
                    }
                ]
            }
        )
        self.assertEqual(findings, [])
        self.assertEqual(len(dynamic), 1)
        self.assertEqual(dynamic[0].config_path, "$.action[0].data.message")

    def test_dotted_prose_services_network_values_and_identifiers_are_ignored(self):
        dotted_text = " ".join(
            (
                "2026.7.2",
                "192.168.1.10",
                "example.com",
                "subdomain.example.com",
                "https://example.com/path",
                "light.turn_on",
                "homeassistant.reload_config_entry",
                "file.yaml",
                "package.module",
                "object.attribute",
                "dict.value",
                "namespace.property",
                "3.14",
                "10.0",
                "v2.0",
                "123e4567-e89b-12d3-a456-426614174000",
                "aa:bb:cc:dd:ee:ff",
            )
        )
        config = {
            "alias": dotted_text,
            "description": dotted_text,
            "action": [
                {
                    "service": "light.turn_on",
                    "target": {
                        "device_id": "device.identifier",
                        "area_id": "area.identifier",
                    },
                    "data": {
                        "message": dotted_text,
                        "diagnostic": "{{ [object.attribute, dict.value] }}",
                    },
                }
            ],
        }
        findings, dynamic = extract(config)
        self.assertEqual(findings, [])
        self.assertEqual(dynamic, [])

    def test_template_comments_and_quoted_prose_are_not_executed_or_scanned(self):
        config = {
            "condition": [
                {
                    "condition": "template",
                    "value_template": (
                        "{{ \"states('sensor.quoted_prose')\" }} "
                        "{# states('sensor.template_comment') #}"
                    ),
                }
            ]
        }
        findings, dynamic = extract(config)
        self.assertEqual(findings, [])
        self.assertEqual(dynamic, [])

    def test_structured_entity_fields_remain_exact_across_nested_actions(self):
        config = {
            "trigger": [{"platform": "state", "entity_id": "sensor.missing_example"}],
            "condition": [
                {"condition": "state", "entity_id": ["binary_sensor.missing_example"]}
            ],
            "action": [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": ["light.missing_example"]},
                },
                {
                    "choose": [
                        {
                            "conditions": [
                                {"entity_id": "input_boolean.missing_example"}
                            ],
                            "sequence": [
                                {
                                    "if": [
                                        {"entity_id": "customdomain.missing_example"}
                                    ],
                                    "then": [],
                                }
                            ],
                        }
                    ]
                },
                {
                    "repeat": {
                        "while": [{"entity_id": "sensor.missing_example"}],
                        "sequence": [],
                    }
                },
            ],
        }
        findings, dynamic = extract(config)
        targets = {item.target_entity_id for item in findings}
        self.assertEqual(
            targets,
            {
                "sensor.missing_example",
                "binary_sensor.missing_example",
                "light.missing_example",
                "input_boolean.missing_example",
                "customdomain.missing_example",
            },
        )
        self.assertTrue(all(item.confidence == "exact" for item in findings))
        self.assertTrue(all(item.config_path.endswith("entity_id") for item in findings))
        self.assertEqual(dynamic, [])

    def test_recognized_literal_template_helpers_remain_exact(self):
        self.assertEqual(
            ENTITY_TEMPLATE_HELPERS,
            {"states", "is_state", "is_state_attr", "state_attr", "expand"},
        )
        templates = (
            "{{ states('sensor.missing_example') }}",
            "{{ is_state('binary_sensor.missing_example', 'on') }}",
            "{{ state_attr('sensor.missing_example', 'unit') }}",
            "{{ is_state_attr('climate.missing_example', 'hvac_mode', 'cool') }}",
            "{{ expand('group.missing_example') | list }}",
            "{{ states['input_boolean.missing_example'] }}",
            "{{ states.customdomain.missing_example.state }}",
        )
        config = {
            "condition": [
                {"condition": "template", "value_template": value}
                for value in templates
            ]
        }
        findings, dynamic = extract(config)
        self.assertEqual(
            {item.target_entity_id for item in findings},
            {
                "sensor.missing_example",
                "binary_sensor.missing_example",
                "climate.missing_example",
                "group.missing_example",
                "input_boolean.missing_example",
                "customdomain.missing_example",
            },
        )
        self.assertTrue(all(item.match_type == "template_literal" for item in findings))
        self.assertEqual(dynamic, [])

    def test_dynamic_helper_arguments_are_targetless_manual_review_evidence(self):
        templates = (
            "{{ states(entity_id_variable) }}",
            "{{ is_state(target_entity, 'on') }}",
            "{{ states[dynamic_entity] }}",
            "{{ states(domain ~ '.' ~ object_id) }}",
            "{{ expand(dynamic_group) }}",
        )
        config = {
            "condition": [
                {"condition": "template", "value_template": value}
                for value in templates
            ]
        }
        findings, dynamic = extract(config)
        self.assertEqual(findings, [])
        self.assertEqual(len(dynamic), len(templates))
        self.assertTrue(all("target_entity_id" not in asdict(item) for item in dynamic))
        self.assertTrue(all("could not be resolved" in item.warning for item in dynamic))

    def test_mixed_template_classifies_literal_dynamic_and_noise_independently(self):
        config = {
            "condition": [
                {
                    "condition": "template",
                    "value_template": (
                        "{{ states('sensor.missing_example') }} "
                        "{{ states(target_entity) }} "
                        "{{ [c.id, c.name, ns.lines, 1.1, 8.8] }}"
                    ),
                }
            ]
        }
        findings, dynamic = extract(config)
        self.assertEqual([item.target_entity_id for item in findings], ["sensor.missing_example"])
        self.assertEqual(len(dynamic), 1)
        self.assertNotIn("target_entity_id", asdict(dynamic[0]))

    def test_repeated_literal_deduplicates_and_noise_does_not_change_evidence_id(self):
        clean = {
            "condition": [
                {
                    "condition": "template",
                    "value_template": (
                        "{{ states('sensor.missing_example') }} "
                        "{{ states('sensor.missing_example') }}"
                    ),
                }
            ]
        }
        noisy = {
            "condition": [
                {
                    "condition": "template",
                    "value_template": (
                        "{{ states('sensor.missing_example') }} "
                        "{{ states('sensor.missing_example') }} "
                        "{{ [c.id, ns.lines, 1.1] }}"
                    ),
                }
            ]
        }
        clean_findings, _ = extract(clean)
        noisy_findings, _ = extract(noisy)
        self.assertEqual(len(clean_findings), 1)
        self.assertEqual(len(noisy_findings), 1)
        self.assertEqual(clean_findings[0].evidence_id, noisy_findings[0].evidence_id)


class IntegrityClassifierRegressionTests(unittest.TestCase):
    def test_rejected_template_tokens_do_not_become_integrity_targets_or_totals(self):
        findings, dynamics = extract(
            {
                "condition": [
                    {
                        "condition": "template",
                        "value_template": (
                            "{{ states('sensor.real_missing') }} "
                            "{{ [1.1, 8.8, c.id, c.limit, c.name, grace.get, ns.bad, ns.ids, ns.lines] }}"
                        ),
                    }
                ]
            }
        )
        bundle = IntegrityEvidenceBundle(
            exact_references=[asdict(item) for item in findings],
            dynamic_references=[asdict(item) for item in dynamics],
            current_states={},
            entity_registry={},
            states_available=True,
            registry_available=True,
            coverage=[],
            index={"generation": 1, "fingerprint": "beta18"},
            evidence_collection_duration_ms=0.0,
            orphan_scope_complete=False,
        )
        classified, _, _ = classify_integrity(
            bundle,
            finding_types=list(FINDING_TYPES),
            include_orphan_candidates=False,
        )
        exact_targets = {item.target_entity_id for item in classified if item.target_entity_id}
        self.assertEqual(exact_targets, {"sensor.real_missing"})
        self.assertTrue(OBSERVED_FALSE_POSITIVES.isdisjoint(exact_targets))
        self.assertEqual(
            [item.finding_type for item in classified], ["missing_entity_reference"]
        )
        self.assertEqual(classified[0].severity, "high")


class _IntegrityProvider:
    provider_id = "engineering"

    def __init__(self, value):
        self.value = value

    async def fetch(self, request):
        return ProviderResult(
            provider_id=self.provider_id,
            capability=ProviderCapability.CONFIGURATION_INTEGRITY_ANALYSIS,
            completeness=ProviderCompleteness.COMPLETE,
            coverage=ProviderCoverage(1, 1),
            data=self.value,
        )

    def active_index_identity(self):
        return {
            "valid": True,
            "generation": self.value.index["generation"],
            "fingerprint": self.value.index["fingerprint"],
        }


class IntegrityCounterRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_tokens_do_not_inflate_result_or_health_aggregates(self):
        METRICS.reset()
        findings, dynamics = extract(
            {
                "condition": [
                    {
                        "condition": "template",
                        "value_template": (
                            "{{ states('sensor.real_missing') }} "
                            "{{ [1.1, 8.8, c.id, c.limit, c.name, grace.get, ns.bad, ns.ids, ns.lines] }}"
                        ),
                    }
                ]
            }
        )
        evidence = IntegrityEvidenceBundle(
            exact_references=[asdict(item) for item in findings],
            dynamic_references=[asdict(item) for item in dynamics],
            current_states={},
            entity_registry={},
            states_available=True,
            registry_available=True,
            coverage=[],
            index={
                "generation": 1,
                "fingerprint": "beta18",
                "cache_hit": True,
                "refreshed": False,
            },
            evidence_collection_duration_ms=0.0,
            orphan_scope_complete=False,
        )
        output = await ConfigurationIntegrityAnalysisService(
            _IntegrityProvider(evidence)
        ).analyze(
            finding_types=["missing_entity_reference"],
            include_orphan_candidates=False,
        )
        self.assertEqual(output.data["finding_count"], 1)
        self.assertEqual(output.data["unique_target_entity_count"], 1)
        self.assertEqual(
            output.data["findings_by_type"]["missing_entity_reference"], 1
        )
        self.assertEqual(output.data["findings_by_severity"]["high"], 1)
        health = METRICS.snapshot()["configuration_integrity_analysis"]
        self.assertEqual(health["finding_count"], 1)
        self.assertEqual(health["unique_target_entity_count"], 1)
        self.assertEqual(
            health["findings_by_type"]["missing_entity_reference"], 1
        )
        self.assertEqual(health["findings_by_severity"]["high"], 1)


if __name__ == "__main__":
    unittest.main()
