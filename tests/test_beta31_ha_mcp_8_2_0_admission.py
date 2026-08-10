"""Exact ha-mcp 8.2.0 admission and dashboard-target regression tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
RUNTIME = BETA / "ha_mcp_engineering"
EVIDENCE = ROOT / "docs" / "evidence" / "upstream-read-compatibility"
sys.path.insert(0, str(BETA))
sys.path.insert(0, str(Path(__file__).parent))

from f3_dashboard_support import make_preread, make_proposal  # noqa: E402
from ha_mcp_engineering.f3_dashboard.errors import (  # noqa: E402
    KnownUpstreamCompatibilityError,
)
from ha_mcp_engineering.f3_dashboard.planning import (  # noqa: E402
    KNOWN_HYPHENLESS_EXISTING_UPDATE_INCOMPATIBLE_RELEASES,
)
from ha_mcp_engineering.f3_dashboard.provider import (  # noqa: E402
    EXACT_CONTRACTS,
    PROHIBITED_ARGUMENT_NAMES,
    admit_provider_contract,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
    validate_reviewed_release_catalog,
    validate_reviewed_release_evidence,
)


VERSION = "8.2.0"
ENTRY_ID = "ha-mcp-v8.2.0-dbcfc0ee"
SOURCE_COMMIT = "54c492510d05b1f33c777f1c94bfb6a50a7d7c42"
CATALOG_FINGERPRINT = (
    "97d88718be4542a60fc2911411da0ff0172ba0dfef821a9c83e998809dcaf4a2"
)
CAPTURE = EVIDENCE / "ha-mcp-8.2.0.json"
REVIEW = EVIDENCE / "ha-mcp-8.2.0-contract-review.json"
SOURCE_REVIEW = (
    EVIDENCE / "ha-mcp-8.2.0-dashboard-setter-source-review.json"
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ExactHaMcp820AdmissionTests(unittest.TestCase):
    def test_exact_release_identity_catalog_and_accounting_are_complete(self):
        registry = validate_reviewed_release_evidence(repository_root=ROOT)
        release = registry.by_version[VERSION]
        capture = load_json(CAPTURE)

        self.assertEqual(release.entry_id, ENTRY_ID)
        self.assertEqual(release.release_tag, "v8.2.0")
        self.assertEqual(release.source_commit, SOURCE_COMMIT)
        self.assertEqual(
            release.source_tag_object,
            "098540ba22d495fdb1701daf830d54762350fd46",
        )
        self.assertEqual(
            release.source_tree,
            "3788d2bfefc140364be66a37cb96a67ac73141df",
        )
        self.assertEqual(
            release.image_index_digest,
            "sha256:dbcfc0ee8ad02d2190ebde69e5cc6167175c79608bbf1d55cff9034e256face1",
        )
        self.assertEqual(set(release.architecture_image_digests_by_platform), {
            "linux/amd64",
            "linux/arm64",
        })
        self.assertEqual(release.allowed_protocol_versions, ("2025-03-26",))
        self.assertEqual(release.advertised_tool_count, 78)
        self.assertEqual(len(release.tool_contracts), 78)
        self.assertEqual(release.catalog_fingerprint, CATALOG_FINGERPRINT)
        self.assertEqual(capture["tool_count"], 78)
        self.assertEqual(capture["catalog_fingerprint"], CATALOG_FINGERPRINT)
        self.assertEqual(
            release.policy.classification_counts,
            {
                "automatic_read": 25,
                "held_for_canary": 1,
                "mixed_or_requires_wrapper": 13,
                "persistent_write": 33,
                "physical_or_high_risk_action": 4,
                "prohibited": 1,
                "unsupported": 1,
            },
        )
        self.assertEqual(
            {
                item.upstream_name
                for item in release.policy.tools
                if item.classification == "held_for_canary"
            },
            {"ha_get_operation_status"},
        )
        validation = validate_reviewed_release_catalog(
            release,
            observed_server_name=capture["server_name"],
            observed_upstream_version=capture["server_version"],
            observed_protocol_version=capture["protocol_version"],
            tools=capture["tools"],
        )
        self.assertTrue(validation.valid)
        self.assertEqual(validation.reviewed_accounted_count, 78)
        self.assertEqual(validation.unreviewed_tool_count, 0)
        self.assertEqual(validation.missing_tool_count, 0)

    def test_only_hacs_descriptor_changed_and_remains_nondelegated(self):
        before = {
            item["name"]: item
            for item in load_json(EVIDENCE / "ha-mcp-8.1.1.json")["tools"]
        }
        after = {item["name"]: item for item in load_json(CAPTURE)["tools"]}
        changed = {name for name in before if before[name] != after[name]}
        self.assertEqual(changed, {"ha_manage_hacs"})
        old_hacs = before["ha_manage_hacs"]
        new_hacs = after["ha_manage_hacs"]
        self.assertEqual(old_hacs["annotations"], new_hacs["annotations"])
        self.assertEqual(old_hacs["outputSchema"], new_hacs["outputSchema"])
        self.assertEqual(
            set(new_hacs["inputSchema"]["properties"]["action"]["enum"])
            - set(old_hacs["inputSchema"]["properties"]["action"]["enum"]),
            {"update_information"},
        )
        release = load_reviewed_upstream_release_registry().by_version[VERSION]
        policy = release.policy.by_name["ha_manage_hacs"]
        self.assertEqual(policy.classification, "persistent_write")
        self.assertNotIn(
            "ha_manage_hacs",
            {
                item.exposed_name
                for item in release.policy.tools
                if item.classification == "automatic_read"
            },
        )
        self.assertNotIn("ha_manage_security_policy", after)
        self.assertNotIn("ha_manage_security_policy", release.policy.by_name)

    def test_runtime_and_review_fixtures_are_exact_and_bounded(self):
        capture_bytes = CAPTURE.read_bytes()
        self.assertEqual(
            hashlib.sha256(capture_bytes).hexdigest(),
            "c7337087bb1f63dafeb3319c9ca8134db1069f87025b897781e7fae43f0474e4",
        )
        review = load_json(REVIEW)
        self.assertEqual(
            set(review["changed_runtime_descriptors"]),
            {"ha_manage_hacs"},
        )
        self.assertEqual(
            review["changed_runtime_descriptors"]["ha_manage_hacs"][
                "classification"
            ],
            "persistent_write",
        )
        self.assertEqual(review["classification_counts"]["automatic_read"], 25)
        self.assertEqual(review["held_tools"], ["ha_get_operation_status"])

    def test_unknown_adjacent_release_remains_unadmitted(self):
        registry = load_reviewed_upstream_release_registry()
        self.assertNotIn("8.2.1", registry.by_version)
        self.assertNotIn("8.3.0", registry.by_version)


class ExactHaMcp820DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_map_plans_fresh_only_on_exact_fixed_release(self):
        proposal, reader = await make_proposal(
            preread=make_preread(version="8.2.0", url_path="map"),
            url_path="map",
            plan_id="beta31map00000001",
        )
        self.assertEqual(proposal.target_id, "map")
        self.assertEqual(proposal.raw_evidence.upstream_version, "8.2.0")
        self.assertEqual(proposal.required_approval, "external_administrator")
        self.assertEqual(reader.mutation_count, 0)
        self.assertEqual(proposal.provider_projection.tool_name, "ha_config_set_dashboard")
        self.assertTrue(proposal.provider_projection.executable)

        with self.assertRaises(KnownUpstreamCompatibilityError):
            await make_proposal(
                preread=make_preread(version="8.1.1", url_path="map"),
                url_path="map",
                plan_id="beta31map00000002",
            )
        self.assertEqual(
            KNOWN_HYPHENLESS_EXISTING_UPDATE_INCOMPATIBLE_RELEASES,
            frozenset({"8.1.1"}),
        )

    async def test_existing_hyphenated_dashboard_remains_plannable(self):
        proposal, reader = await make_proposal(
            preread=make_preread(
                version="8.2.0",
                url_path="compatibility-fixture",
            ),
            url_path="compatibility-fixture",
            plan_id="beta31hyphen00001",
        )
        self.assertEqual(proposal.target_id, "compatibility-fixture")
        self.assertEqual(reader.mutation_count, 0)

    def test_exact_source_review_binds_all_four_target_cases(self):
        source = load_json(SOURCE_REVIEW)
        self.assertEqual(source["upstream"]["source_commit"], SOURCE_COMMIT)
        self.assertEqual(
            source["upstream"]["dashboard_correction_commit"],
            "801c22d0eaa59bfcbf44b51c257dafc635a075d3",
        )
        self.assertEqual(
            source["resolver_contract"],
            {
                "existing_hyphenless_exact_url_path": "accepted_update",
                "existing_hyphenated_exact_url_path": "accepted_update",
                "new_hyphenless_url_path": (
                    "rejected_validation_invalid_parameter"
                ),
                "new_hyphenated_url_path": "accepted_creation",
                "internal_id_match_cannot_exempt_different_hyphenless_path": True,
                "unreadable_dashboard_registry": "fail_closed",
                "strategy_dashboard_full_config_or_python_transform": "rejected",
            },
        )
        self.assertEqual(source["focused_upstream_tests"]["passed"], 49)
        self.assertEqual(source["focused_upstream_tests"]["failed"], 0)

    def test_setter_contract_is_exact_and_arbitrary_arguments_stay_prohibited(self):
        evidence = EXACT_CONTRACTS[VERSION]
        admission = admit_provider_contract(evidence)
        self.assertEqual(admission.compatibility_entry, ENTRY_ID)
        self.assertEqual(evidence.source_commit, SOURCE_COMMIT)
        self.assertEqual(
            set(PROHIBITED_ARGUMENT_NAMES),
            {
                "config",
                "python_transform",
                "title",
                "icon",
                "require_admin",
                "show_in_sidebar",
                "view_path",
                "return_screenshot",
                "resources",
                "preferences",
            },
        )


if __name__ == "__main__":
    unittest.main()
