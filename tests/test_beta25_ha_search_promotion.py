"""Beta 25 exact-release ``ha_search`` promotion invariants."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import unittest

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
CAPTURE = (
    ROOT
    / "docs"
    / "evidence"
    / "upstream-read-compatibility"
    / "ha-mcp-8.1.1.json"
)
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.capabilities import (  # noqa: E402
    dynamic_upstream_capabilities,
    replace_dynamic_upstream_capabilities,
)
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.tools import registered_tools  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)
from tests.test_readonly_upstream_gateway import (  # noqa: E402
    FakeTransport,
    settings,
)


ENTRY_ID = "ha-mcp-v8.1.1-e1d76a6e"
PRE_PROMOTION_VERSION = "2.2.0-beta.24"
BETA25_VERSION = "2.2.0-beta.25"
AUTHORITATIVE_VERSION_PATTERNS = {
    "add_on": (
        BETA / "config.yaml",
        re.compile(r'(?m)^version: "([^"]+)"$'),
    ),
    "runtime": (
        BETA / "ha_mcp_engineering" / "version.py",
        re.compile(r'(?m)^SERVER_VERSION = "([^"]+)"$'),
    ),
    "validator": (
        ROOT / "scripts" / "validate_addon_metadata.py",
        re.compile(r'(?m)^BETA_VERSION = "([^"]+)"$'),
    ),
}
NORMALIZED_CATALOG_FINGERPRINT = (
    "389c33d95537d93ad96d33f2859716611c60fa53313c6d56a598fb3c9034a82b"
)


def captured_tools() -> list[dict]:
    return deepcopy(json.loads(CAPTURE.read_text(encoding="utf-8"))["tools"])


class Beta25SearchPromotionTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        replace_dynamic_upstream_capabilities((), {})

    def require_published_phase(self, expected_version: str) -> dict[str, str]:
        versions: dict[str, str] = {}
        for authority, (path, pattern) in AUTHORITATIVE_VERSION_PATTERNS.items():
            matches = pattern.findall(path.read_text(encoding="utf-8"))
            self.assertEqual(len(matches), 1, authority)
            versions[authority] = matches[0]
        self.assertEqual(len(set(versions.values())), 1)
        actual_version = next(iter(versions.values()))
        self.assertIn(
            actual_version,
            (PRE_PROMOTION_VERSION, BETA25_VERSION),
        )
        if actual_version != expected_version:
            self.skipTest(
                f"{expected_version} assertions do not apply to "
                f"published phase {actual_version}"
            )
        return versions

    def assert_beta25_release_evidence(self) -> None:
        self.assertTrue(
            (ROOT / "docs" / "V2_2_0_BETA25_RELEASE_NOTES.md").is_file()
        )
        self.assertTrue(
            (ROOT / "docs" / "V2_2_0_BETA25_ACCEPTANCE.md").is_file()
        )
        review = json.loads(
            (
                ROOT
                / "docs"
                / "evidence"
                / "upstream-read-compatibility"
                / "ha-mcp-8.1.1-contract-review.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            review["runtime_catalog"]["normalized_aggregate_fingerprint"],
            NORMALIZED_CATALOG_FINGERPRINT,
        )

    async def gateway(
        self,
        *,
        result: dict | None = None,
    ) -> tuple[UpstreamReadGateway, FakeTransport, FastMCP]:
        transport = FakeTransport(
            captured_tools(),
            version="8.1.1",
            result=result,
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=transport,
            release_registry=load_reviewed_upstream_release_registry(),
            admission_validator=lambda _catalog: None,
        )
        server = FastMCP("beta25-search-promotion")
        await gateway.initialize(server)
        self.assertEqual(
            gateway.health_snapshot()["admission_status"],
            "admitted_exact",
        )
        return gateway, transport, server

    def test_only_exact_8_1_1_search_classification_is_promoted(self):
        registry = load_reviewed_upstream_release_registry()
        eight_zero = registry.by_version["8.0.0"].policy
        eight_one_zero = registry.by_version["8.1.0"].policy
        eight_one_one = registry.by_version["8.1.1"].policy

        self.assertEqual(eight_zero.classification_counts["automatic_read"], 24)
        self.assertEqual(eight_zero.classification_counts["held_for_canary"], 2)
        self.assertEqual(eight_one_zero.classification_counts["automatic_read"], 24)
        self.assertEqual(eight_one_zero.classification_counts["held_for_canary"], 2)
        self.assertEqual(eight_one_one.classification_counts["automatic_read"], 25)
        self.assertEqual(eight_one_one.classification_counts["held_for_canary"], 1)
        self.assertEqual(
            {
                item.upstream_name
                for item in eight_one_one.tools
                if item.classification == "held_for_canary"
            },
            {"ha_get_operation_status"},
        )
        self.assertEqual(
            eight_one_one.by_name["ha_search"].classification,
            "automatic_read",
        )
        release = registry.by_version["8.1.1"]
        search_contract = release.tool_contracts_by_name["ha_search"]
        operation_status_contract = release.tool_contracts_by_name[
            "ha_get_operation_status"
        ]
        self.assertEqual(search_contract.policy_classification, "automatic_read")
        self.assertTrue(search_contract.reviewed_automatic_read)
        self.assertIsNone(search_contract.quarantine_reason)
        self.assertEqual(
            operation_status_contract.policy_classification,
            "held_for_canary",
        )
        self.assertFalse(operation_status_contract.reviewed_automatic_read)
        self.assertEqual(
            operation_status_contract.quarantine_reason,
            "policy:held_for_canary",
        )
        classifications_8_1_0 = {
            item.upstream_name: item.classification
            for item in eight_one_zero.tools
        }
        classifications_8_1_1 = {
            item.upstream_name: item.classification
            for item in eight_one_one.tools
        }
        self.assertEqual(
            {
                name
                for name in classifications_8_1_0
                if classifications_8_1_0[name] != classifications_8_1_1[name]
            },
            {"ha_search"},
        )

    def test_beta25_is_staged_without_changing_published_versions(self):
        self.assertEqual(
            set(self.require_published_phase(PRE_PROMOTION_VERSION).values()),
            {PRE_PROMOTION_VERSION},
        )
        self.assertEqual(
            (ROOT / ".release" / "next-version").read_text(encoding="utf-8"),
            f"{BETA25_VERSION}\n",
        )
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(
                encoding="utf-8"
            ),
        )
        self.assert_beta25_release_evidence()

    def test_beta25_generated_release_state_is_exact(self):
        self.assertEqual(
            set(self.require_published_phase(BETA25_VERSION).values()),
            {BETA25_VERSION},
        )
        self.assertFalse((ROOT / ".release" / "next-version").exists())
        self.assertIn(
            'version: "1.1.2"',
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(
                encoding="utf-8"
            ),
        )
        self.assert_beta25_release_evidence()

    async def test_search_uses_normal_gateway_and_canary_rejects_it(self):
        gateway, transport, server = await self.gateway(
            result={
                "structuredContent": {
                    "success": True,
                    "query": "porch",
                    "entities": [],
                    "partial": False,
                },
                "isError": False,
            }
        )
        tools = registered_tools(server)
        health = gateway.health_snapshot()
        self.assertEqual(health["exact_matched_automatic_read_count"], 25)
        self.assertEqual(health["dynamically_exposed_count"], 25)
        self.assertEqual(health["held_read_count"], 1)
        self.assertEqual(health["held_tools"], ["ha_get_operation_status"])
        self.assertEqual(health["fallback_count"], 0)
        self.assertEqual(len(tools), 25)
        self.assertIn("ha_search", tools)
        self.assertNotIn("ha_get_operation_status", tools)
        self.assertEqual(len(dynamic_upstream_capabilities()), 25)

        delegated = json.loads(
            await tools.get("ha_search").run({"query": "porch", "limit": 1})
        )
        self.assertTrue(delegated["success"])
        self.assertEqual(
            delegated["metadata"]["provider"],
            "upstream_read_gateway",
        )
        self.assertEqual(delegated["metadata"]["fallback"], "none")
        self.assertEqual(delegated["metadata"]["completeness"], "complete")
        self.assertFalse(delegated["metadata"].get("truncated", False))
        self.assertEqual(transport.calls[0][0], "ha_search")

        attempts_before = len(transport.attempts)
        calls_before = len(transport.calls)
        canary = json.loads(
            await gateway.run_held_read_canary(
                upstream_tool_name="ha_search",
                expected_compatibility_entry_id=ENTRY_ID,
                arguments={"query": "porch", "limit": 1},
            )
        )
        self.assertFalse(canary["success"])
        self.assertEqual(
            canary["details"]["reason"],
            "tool_not_held_for_canary",
        )
        self.assertFalse(
            canary["details"]["canary_evidence"]["dispatch_occurred"]
        )
        self.assertEqual(len(transport.attempts), attempts_before)
        self.assertEqual(len(transport.calls), calls_before)


if __name__ == "__main__":
    unittest.main()
