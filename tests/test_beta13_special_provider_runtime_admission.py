from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
EVIDENCE = ROOT / "docs/evidence/upstream-read-compatibility"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1,
    load_reviewed_upstream_release_registry,
    validate_reviewed_release_catalog,
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reviewed_tools(version: str) -> list[dict]:
    return deepcopy(load_json(EVIDENCE / f"ha-mcp-{version}.json")["tools"])


def addon_8_tools() -> list[dict]:
    tools = reviewed_tools("8.0.0")
    reconstruction = load_json(
        EVIDENCE / "ha-mcp-8.0.0-live-addon-reconstruction.json"
    )
    policy = reconstruction["transform"]["replacement"]
    for tool in tools:
        tool["_meta"]["ha_mcp"]["policy"] = deepcopy(policy)
    return tools


def validate(version: str, tools: list[dict], **overrides):
    release = load_reviewed_upstream_release_registry().by_version[version]
    return validate_reviewed_release_catalog(
        overrides.pop("release", release),
        observed_server_name=overrides.pop("server_name", "ha-mcp"),
        observed_upstream_version=overrides.pop("upstream_version", version),
        observed_protocol_version=overrides.pop(
            "protocol_version", "2025-03-26"
        ),
        tools=tools,
    )


class ReviewedCatalogValidatorTests(unittest.TestCase):
    def test_exact_reviewed_standalone_catalogs_are_fully_accounted(self):
        for version in ("7.14.2", "8.0.0"):
            with self.subTest(version=version):
                result = validate(version, reviewed_tools(version))
                self.assertTrue(result.valid)
                self.assertEqual(result.validation_status, "accepted_exact")
                self.assertEqual(result.expected_tool_count, 78)
                self.assertEqual(result.observed_tool_count, 78)
                self.assertEqual(result.reviewed_accounted_count, 78)
                self.assertEqual(result.missing_tool_count, 0)
                self.assertEqual(result.additional_tool_count, 0)
                self.assertEqual(result.duplicated_tool_count, 0)
                self.assertEqual(result.unreviewed_tool_count, 0)
                self.assertEqual(result.invalid_descriptor_count, 0)
                self.assertEqual(
                    result.expected_normalized_catalog_fingerprint,
                    result.normalized_catalog_fingerprint,
                )
                self.assertEqual(
                    result.aggregate_fingerprint_model,
                    REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1,
                )
                self.assertEqual(
                    set(dict(result.component_mismatch_counts).values()),
                    {0},
                )

    def test_exact_addon_catalog_uses_same_normalized_identity(self):
        standalone = validate("8.0.0", reviewed_tools("8.0.0"))
        addon = validate("8.0.0", addon_8_tools())

        self.assertTrue(addon.valid)
        self.assertEqual(addon.reviewed_accounted_count, 78)
        self.assertNotEqual(
            standalone.observed_raw_catalog_fingerprint,
            addon.observed_raw_catalog_fingerprint,
        )
        self.assertEqual(
            addon.observed_raw_catalog_fingerprint,
            "c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768",
        )
        self.assertEqual(
            standalone.normalized_catalog_fingerprint,
            addon.normalized_catalog_fingerprint,
        )

    def test_identity_tool_set_and_contract_drift_fail_closed(self):
        tools = addon_8_tools()
        cases = (
            (
                "unknown_patch",
                tools,
                {"upstream_version": "8.0.1"},
                "upstream_version_mismatch",
            ),
            (
                "wrong_protocol",
                tools,
                {"protocol_version": "2025-11-25"},
                "unsupported_protocol_version",
            ),
            (
                "missing_tool",
                tools[:-1],
                {},
                "rejected_catalog_mismatch",
            ),
            (
                "duplicate_tool",
                [*tools, deepcopy(tools[0])],
                {},
                "rejected_catalog_mismatch",
            ),
        )
        changed = deepcopy(tools)
        changed[0]["description"] += " drift"
        cases += (
            (
                "description_drift",
                changed,
                {},
                "rejected_catalog_mismatch",
            ),
        )
        for name, candidate, overrides, status in cases:
            with self.subTest(name=name):
                result = validate("8.0.0", candidate, **overrides)
                self.assertFalse(result.valid)
                self.assertEqual(result.validation_status, status)
                if name in {"missing_tool", "duplicate_tool"}:
                    self.assertEqual(
                        result.normalized_catalog_fingerprint, None
                    )
                elif name == "description_drift":
                    self.assertNotEqual(
                        result.normalized_catalog_fingerprint,
                        result.expected_normalized_catalog_fingerprint,
                    )
                else:
                    self.assertEqual(
                        result.normalized_catalog_fingerprint,
                        result.expected_normalized_catalog_fingerprint,
                    )

    def test_reviewed_classification_and_runtime_model_are_fail_closed(self):
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version["8.0.0"]
        entries = tuple(
            replace(entry, classification="persistent_write")
            if entry.upstream_name == "ha_manage_backup"
            else entry
            for entry in release.policy.tools
        )
        changed_policy = replace(release.policy, tools=entries)
        changed_release = replace(release, policy=changed_policy)
        result = validate(
            "8.0.0", addon_8_tools(), release=changed_release
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.classification_mismatch_count, 1)
        self.assertEqual(
            result.classification_mismatches, ("ha_manage_backup",)
        )

        unsupported = replace(
            release,
            runtime_contract_fingerprint_model="unreviewed-model-v9",
        )
        result = validate("8.0.0", addon_8_tools(), release=unsupported)
        self.assertFalse(result.valid)
        self.assertEqual(
            result.validation_status,
            "unsupported_runtime_fingerprint_model",
        )
        self.assertEqual(result.normalized_catalog_fingerprint, None)


if __name__ == "__main__":
    unittest.main()
