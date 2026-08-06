"""Beta 23 compatibility-family admission and exact-runtime authority tests."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ha_mcp_engineering.compatibility_family import (
    CompatibilityFamilyError,
    DescriptorNormalizationRule,
    compare_family_candidate,
    load_compatibility_families,
)
from ha_mcp_engineering.upstream_tool_policy import (
    RELEASE_REGISTRY_PATH,
    ReviewedUpstreamReleaseRegistry,
    UpstreamToolPolicyError,
    canonical_json,
    load_reviewed_upstream_release_registry,
    validate_reviewed_release_catalog,
    validate_reviewed_release_evidence,
)
from scripts.admit_upstream_compatibility_family import (
    require_distinct_capture_paths,
    require_exact_version,
)


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_DIR = ROOT / "docs/evidence/upstream-read-compatibility"
FAMILY_PATH = (
    ROOT
    / "hass_mcp_engineering_beta/ha_mcp_engineering"
    / "upstream_compatibility_families.json"
)


class CompatibilityFamilyAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.families = load_compatibility_families(FAMILY_PATH)
        cls.family = cls.families.by_id["ha-mcp-8.1.x-v1"]
        cls.registry = load_reviewed_upstream_release_registry()
        cls.baseline = cls.registry.historical_by_version["8.1.0"]
        cls.candidate = cls.registry.historical_by_version["8.1.1"]
        cls.baseline_capture = json.loads(
            (CAPTURE_DIR / "ha-mcp-8.1.0.json").read_text(encoding="utf-8")
        )
        cls.candidate_capture = json.loads(
            (CAPTURE_DIR / "ha-mcp-8.1.1.json").read_text(encoding="utf-8")
        )
        cls.classifications = {
            item.upstream_name: item.classification
            for item in cls.baseline.policy.tools
        }

    def compare(
        self,
        tools: list[dict[str, object]],
        *,
        family=None,
        observed_non_catalog_categories=None,
    ):
        return compare_family_candidate(
            family or self.family,
            candidate_version="8.1.1",
            baseline_tools=self.baseline_capture["tools"],
            candidate_tools=tools,
            baseline_classifications=self.classifications,
            observed_non_catalog_categories=(
                observed_non_catalog_categories
                if observed_non_catalog_categories is not None
                else {
                    "immutable_identity_only",
                    "documentation_only",
                    "packaging_or_dependency_only",
                }
            ),
        )

    def changed_tools(self) -> list[dict[str, object]]:
        return json.loads(canonical_json(self.candidate_capture["tools"]))

    def tool(self, tools: list[dict[str, object]], name: str) -> dict[str, object]:
        return next(item for item in tools if item["name"] == name)

    def test_exact_8_1_1_candidate_is_automatically_admitted(self) -> None:
        comparison = self.compare(self.candidate_capture["tools"])
        self.assertEqual(comparison.outcome, "admitted_automatic")
        self.assertEqual(comparison.material_drift_categories, ())
        self.assertEqual(comparison.unknown_drift, ())
        self.assertEqual(len(comparison.unchanged_tools), 78)
        self.assertEqual(
            dict(comparison.provider_dispositions),
            {
                "backup": "admitted",
                "dashboard": "admitted",
                "lifecycle": "admitted",
                "read_gateway": "admitted",
            },
        )

    def test_exact_entry_not_version_family_is_runtime_authority(self) -> None:
        self.assertIn("8.1.1", self.registry.by_version)
        self.assertNotIn("8.1.2", self.registry.by_version)
        self.assertNotIn("8.2.0", self.registry.by_version)
        self.assertNotIn("9.1.1", self.registry.by_version)
        self.assertTrue(self.family.candidate_is_eligible("8.1.2"))
        self.assertFalse(self.family.candidate_is_eligible("8.2.0"))
        self.assertFalse(self.family.candidate_is_eligible("9.1.1"))

    def test_exact_release_identity_and_version_bindings_are_retained(self) -> None:
        self.assertEqual(
            self.candidate.source_tag_object,
            "46fa04345df4ae0a98b7e5bb9fbcebdb03018f3e",
        )
        self.assertEqual(
            self.candidate.source_tree,
            "f7cd88857a84fc4ad040ba01f62efc84211d9645",
        )
        self.assertEqual(
            require_exact_version(
                "8.1.1", expected="8.1.1", field="installed package version"
            ),
            "8.1.1",
        )
        with self.assertRaisesRegex(
            SystemExit, "does not match the exact candidate version"
        ):
            require_exact_version(
                "8.1.0", expected="8.1.1", field="installed package version"
            )
        with self.assertRaisesRegex(SystemExit, "distinct evidence"):
            require_distinct_capture_paths(
                CAPTURE_DIR / "ha-mcp-8.1.1.json",
                CAPTURE_DIR / "ha-mcp-8.1.1.json",
            )

    def test_input_output_and_annotation_drift_hold_automatic_read(self) -> None:
        for component in ("inputSchema", "outputSchema", "annotations"):
            with self.subTest(component=component):
                tools = self.changed_tools()
                target = self.tool(tools, "ha_get_hacs_info")
                target[component] = {"beta23_changed": True}
                comparison = self.compare(tools)
                self.assertEqual(
                    comparison.outcome, "admitted_with_selective_holds"
                )
                self.assertEqual(
                    comparison.held_automatic_reads, ("ha_get_hacs_info",)
                )
                self.assertEqual(
                    dict(comparison.provider_dispositions)["read_gateway"],
                    "partial",
                )

    def test_lifecycle_contract_drift_holds_only_lifecycle_surface(self) -> None:
        tools = self.changed_tools()
        self.tool(tools, "ha_get_addon")["outputSchema"] = {
            "beta23_changed": True
        }
        comparison = self.compare(tools)
        self.assertEqual(
            comparison.outcome, "admitted_with_selective_holds"
        )
        self.assertEqual(
            dict(comparison.provider_dispositions),
            {
                "backup": "admitted",
                "dashboard": "admitted",
                "lifecycle": "held",
                "read_gateway": "admitted",
            },
        )

    def test_non_catalog_provider_drift_is_selective(self) -> None:
        for category, held_surface in (
            ("lifecycle_provider", "lifecycle"),
            ("dashboard_provider", "dashboard"),
        ):
            with self.subTest(category=category):
                comparison = self.compare(
                    self.candidate_capture["tools"],
                    observed_non_catalog_categories={
                        "immutable_identity_only",
                        category,
                    },
                )
                self.assertEqual(
                    comparison.outcome, "admitted_with_selective_holds"
                )
                dispositions = dict(comparison.provider_dispositions)
                self.assertEqual(dispositions[held_surface], "held")
                self.assertEqual(
                    [
                        name
                        for name, disposition in dispositions.items()
                        if disposition == "held"
                    ],
                    [held_surface],
                )

    def test_security_and_unscoped_envelope_drift_reject_globally(self) -> None:
        for category in (
            "security_or_transport_behavior",
            "consumed_response_envelope",
            "classification_change",
            "unknown_drift",
        ):
            with self.subTest(category=category):
                comparison = self.compare(
                    self.candidate_capture["tools"],
                    observed_non_catalog_categories={category},
                )
                self.assertEqual(comparison.outcome, "rejected")
                self.assertEqual(
                    set(dict(comparison.provider_dispositions).values()),
                    {"held"},
                )

    def test_descriptor_drift_cannot_be_self_declared_nonsemantic(self) -> None:
        with self.assertRaisesRegex(
            CompatibilityFamilyError, "family_candidate_drift_unknown"
        ):
            self.compare(
                self.candidate_capture["tools"],
                observed_non_catalog_categories={
                    "descriptor_wording_unchanged_semantics"
                },
            )

    def test_changed_write_stays_nondelegated_without_global_rejection(self) -> None:
        tools = self.changed_tools()
        self.tool(tools, "ha_manage_hacs")["inputSchema"] = {
            "beta23_changed": True
        }
        comparison = self.compare(tools)
        self.assertEqual(
            comparison.outcome, "admitted_with_selective_holds"
        )
        self.assertEqual(comparison.held_automatic_reads, ())
        self.assertEqual(comparison.nondelegated_changed_tools, ("ha_manage_hacs",))

    def test_unknown_descriptor_or_tool_set_drift_rejects(self) -> None:
        tools = self.changed_tools()
        self.tool(tools, "ha_get_hacs_info")["description"] = "semantic change"
        comparison = self.compare(tools)
        self.assertEqual(comparison.outcome, "rejected")
        self.assertEqual(
            comparison.unknown_drift, ("ha_get_hacs_info:description",)
        )

        tools = self.changed_tools()
        tools.pop()
        comparison = self.compare(tools)
        self.assertEqual(comparison.outcome, "rejected")
        self.assertIn("tool_addition_removal_or_rename", comparison.material_drift_categories)

    def test_descriptor_normalization_requires_an_explicit_tool_rule(self) -> None:
        tools = self.changed_tools()
        target = self.tool(tools, "ha_get_hacs_info")
        original = str(target["description"])
        target["description"] = original.replace(" ", "  ", 1)
        self.assertEqual(self.compare(tools).outcome, "rejected")
        ruled_family = replace(
            self.family,
            descriptor_normalization_rules=(
                DescriptorNormalizationRule(
                    rule_id="hacs-whitespace-v1",
                    tool_name="ha_get_hacs_info",
                    field="description",
                    normalizer="ascii-whitespace-v1",
                ),
            ),
        )
        comparison = self.compare(tools, family=ruled_family)
        self.assertEqual(comparison.outcome, "admitted_automatic")
        self.assertEqual(
            comparison.normalized_descriptor_tools, ("ha_get_hacs_info",)
        )

    def test_release_specific_revocation_remains_historically_readable(self) -> None:
        revoked = replace(
            self.candidate,
            revoked=True,
            revocation_reason="test-only release-specific revocation",
        )
        releases = tuple(
            revoked if item.version == revoked.version else item
            for item in self.registry.releases
        )
        registry = ReviewedUpstreamReleaseRegistry(
            registry_format_version=self.registry.registry_format_version,
            default_version=self.registry.default_version,
            releases=releases,
        )
        self.assertNotIn("8.1.1", registry.by_version)
        self.assertIn("8.1.1", registry.historical_by_version)
        self.assertIn("8.1.0", registry.by_version)

    def test_family_decision_digest_tampering_fails_closed(self) -> None:
        raw = json.loads(RELEASE_REGISTRY_PATH.read_text(encoding="utf-8"))
        release = next(item for item in raw["releases"] if item["version"] == "8.1.1")
        release["family_admission"]["decision_sha256"] = "sha256:" + "0" * 64
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".json",
            dir=RELEASE_REGISTRY_PATH.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(canonical_json(raw) + b"\n")
        try:
            with self.assertRaisesRegex(
                UpstreamToolPolicyError,
                "release_registry_family_admission_invalid",
            ):
                validate_reviewed_release_evidence(
                    temporary,
                    repository_root=ROOT,
                )
        finally:
            temporary.unlink(missing_ok=True)

    def test_provider_disposition_tampering_fails_closed(self) -> None:
        raw = json.loads(RELEASE_REGISTRY_PATH.read_text(encoding="utf-8"))
        release = next(item for item in raw["releases"] if item["version"] == "8.1.1")
        release["provider_dispositions"]["lifecycle"] = "held"
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".json",
            dir=RELEASE_REGISTRY_PATH.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(canonical_json(raw) + b"\n")
        try:
            with self.assertRaisesRegex(
                UpstreamToolPolicyError,
                "release_registry_family_admission_invalid",
            ):
                validate_reviewed_release_evidence(
                    temporary,
                    repository_root=ROOT,
                )
        finally:
            temporary.unlink(missing_ok=True)

    def test_packaged_runtime_load_does_not_read_review_time_evidence(self) -> None:
        with patch(
            "ha_mcp_engineering.compatibility_family.sha256_resource",
            side_effect=AssertionError("runtime reached review-time evidence"),
        ):
            registry = load_reviewed_upstream_release_registry()
        self.assertEqual(
            registry.by_version["8.1.1"].entry_id,
            "ha-mcp-v8.1.1-e1d76a6e",
        )

    def test_exact_evidence_and_complete_accounting_validate(self) -> None:
        registry = validate_reviewed_release_evidence(
            repository_root=ROOT
        )
        release = registry.by_version["8.1.1"]
        result = validate_reviewed_release_catalog(
            release,
            observed_server_name="ha-mcp",
            observed_upstream_version="8.1.1",
            observed_protocol_version="2025-03-26",
            tools=self.candidate_capture["tools"],
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.expected_tool_count, 78)
        self.assertEqual(result.reviewed_accounted_count, 78)
        self.assertEqual(result.unreviewed_tool_count, 0)
        self.assertEqual(result.additional_tool_count, 0)
        self.assertEqual(result.missing_tool_count, 0)

    def test_family_policy_rejects_wildcard_and_prerelease_versions(self) -> None:
        with self.assertRaises(CompatibilityFamilyError):
            self.family.candidate_is_eligible("8.1.x")
        with self.assertRaises(CompatibilityFamilyError):
            self.family.candidate_is_eligible("8.1.2-rc.1")


if __name__ == "__main__":
    unittest.main()
