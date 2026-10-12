"""HAMCP-161 R1: the Core 2026.10.1 restored device template attributes.

Core 2026.10.1 (helpers/template/extensions/devices.py) makes ``device_attr`` and
``is_device_attr`` return ``config_entries``, ``config_entries_subentries`` and
``primary_config_entry`` for non-composite devices, where 2026.10.0 raised. The
``core.template_semantics`` analysis contract is the offline, binary-owned
semantic registry: these lookups must stay opaque and conservative, exactly
like any other device attribute, so a changed rendered value cannot change
the dependency result.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.obligation_ledger import (  # noqa: E402
    analyze_template_obligations,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    EXPECTED_SEMANTIC_REGISTRY_SHA256,
)


RESTORED = ("config_entries", "config_entries_subentries", "primary_config_entry")
CONTROL = "manufacturer"
TARGET = "input_boolean.hamcp161_fixture_flag"
# Fields that only identify the expression text, not the analysis result.
TEXT_IDENTITY = {"evidence_id", "expression_fingerprint"}


def _valid_entity_id(value: str) -> bool:
    domain, separator, object_id = value.partition(".")
    return bool(separator and domain and object_id and " " not in value)


def _analyze(template: str):
    return analyze_template_obligations(
        template,
        source_type="automation",
        source_id="automation.hamcp161_fixture",
        config_path="action.0.data.message",
        relation="template_reference",
        source_entity_id="automation.hamcp161_fixture",
        source_name="HAMCP-161 fixture",
        source_state="on",
        configuration_fingerprint="fixture-configuration-v1",
        entity_id_validator=_valid_entity_id,
    )


def _result_shape(template: str):
    result = _analyze(template)
    obligations = tuple(
        {k: v for k, v in dataclasses.asdict(item).items() if k not in TEXT_IDENTITY}
        for item in result.obligations
    )
    return result.coverage_failed, result.semantic_registry_sha256, obligations


class RestoredDeviceTemplateAttributeAnalysisTests(unittest.TestCase):
    def test_restored_attributes_are_opaque_and_conservative(self) -> None:
        for function, template in (
            ("device_attr", "{{{{ device_attr('fixture_device', '{attr}') }}}}"),
            ("is_device_attr", "{{{{ is_device_attr('fixture_device', '{attr}', 'x') }}}}"),
        ):
            for attr in RESTORED:
                with self.subTest(function=function, attr=attr):
                    result = _analyze(template.format(attr=attr))
                    self.assertEqual(result.semantic_registry_sha256, EXPECTED_SEMANTIC_REGISTRY_SHA256)
                    self.assertEqual(len(result.obligations), 1, result.obligations)
                    item = result.obligations[0]
                    self.assertEqual(item.outcome, "bounded_semantic_opaque")
                    self.assertEqual(item.obligation_kind, f"global_{function}")
                    self.assertEqual(item.reason_code, f"{function}_registry_lookup_opaque")
                    self.assertEqual(item.semantic_category, "attribute_item_access")
                    self.assertEqual(item.lock_projection, "conservative")
                    self.assertEqual(item.exact_entity_ids, ())

    def test_restored_attributes_analyze_exactly_like_an_ordinary_attribute(self) -> None:
        for template in (
            "{{{{ device_attr('fixture_device', '{attr}') }}}}",
            "{{{{ is_device_attr('fixture_device', '{attr}', 'x') }}}}",
            "{{% if device_attr('fixture_device', '{attr}') %}}{{{{ states('" + TARGET + "') }}}}{{% endif %}}",
        ):
            control = _result_shape(template.format(attr=CONTROL))
            for attr in RESTORED:
                with self.subTest(template=template, attr=attr):
                    self.assertEqual(_result_shape(template.format(attr=attr)), control)

    def test_lookup_value_cannot_hide_a_dependent_state_read(self) -> None:
        for attr in RESTORED:
            with self.subTest(attr=attr):
                obligations = _analyze(
                    "{% if device_attr('fixture_device', '" + attr + "') %}"
                    "{{ states('" + TARGET + "') }}{% endif %}"
                ).obligations
                self.assertTrue(
                    any(item.outcome == "exact_dependency" and TARGET in item.exact_entity_ids
                        for item in obligations),
                    obligations,
                )
                self.assertTrue(
                    any(item.outcome == "bounded_semantic_opaque"
                        and item.lock_projection == "conservative" for item in obligations),
                    obligations,
                )


if __name__ == "__main__":
    unittest.main()
