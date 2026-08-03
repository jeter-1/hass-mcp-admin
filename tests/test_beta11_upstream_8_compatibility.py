from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import sys
import unittest

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
CAPTURES = ROOT / "docs" / "evidence" / "upstream-read-compatibility"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.providers.upstream_contracts import (  # noqa: E402
    COMPILED_ARGUMENT_SHAPES,
    CONTRACT_FAMILY_V3,
    PROHIBITED_ARGUMENTS,
    decide_admission,
    load_attestations,
)
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.tools import registered_tools  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
    validate_reviewed_release_evidence,
)
from tests.test_readonly_upstream_gateway import (  # noqa: E402
    FakeTransport,
    settings,
)


def capture(version: str) -> dict:
    return json.loads(
        (CAPTURES / f"ha-mcp-{version}.json").read_text(encoding="utf-8")
    )


def server() -> FastMCP:
    value = FastMCP("beta11-upstream-compatibility")

    async def native() -> str:
        return "native"

    value.tool(name="native_beta11_test")(native)
    return value


class Beta11ReleaseEvidenceTests(unittest.TestCase):
    def test_exact_release_evidence_and_policy_accounting(self):
        registry = validate_reviewed_release_evidence(
            repository_root=ROOT
        )
        self.assertEqual(
            registry.supported_versions,
            ("7.14.1", "7.14.2", "8.0.0"),
        )
        seven = registry.by_version["7.14.2"]
        eight = registry.by_version["8.0.0"]
        self.assertEqual(
            seven.catalog_fingerprint,
            "c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c",
        )
        self.assertEqual(seven.policy.classification_counts["automatic_read"], 26)
        self.assertEqual(
            eight.source_commit,
            "9dd3ac620e3149cd34ec3c990b6ee81e778191f2",
        )
        self.assertEqual(
            eight.image_index_digest,
            "sha256:d65630f6a3fd14d8f536c27432d4d2cf3045e6f6a2d196cba754ee8566491ae4",
        )
        self.assertEqual(
            eight.catalog_fingerprint,
            "0bc81aa7bd94416385520b9c4c4f7d9ccbc6a49f8f65b8a2a599135463327316",
        )
        self.assertEqual(
            eight.strict_full_contract_fingerprint,
            "ff18cda3ca27abc8cca69685fb5240942cbe24a1508f73b9a26e57e1afe44d5a",
        )
        self.assertEqual(
            eight.strict_full_contract_fingerprint_model,
            "ha-mcp-strict-full-contract-v1",
        )
        self.assertEqual(
            eight.addon_artifact_digests_by_platform,
            {
                "linux/amd64": {
                    "index_digest": "sha256:693ecd5c68f98e64111fbf58e02547a51b2168a942056684dbe262c550aff9cd",
                    "image_manifest_digest": "sha256:65856752c37e4c1f9093060fbbc4a1a826cac1cbd6a76e856af5f5672a96c404",
                },
                "linux/arm64": {
                    "index_digest": "sha256:150ee09078919a47db19639deaa8c27ec064390054e27b4e618f82eea9cf7f50",
                    "image_manifest_digest": "sha256:a4bc83ed6f1a531d445e8107c77b7e7d5289d25510316dc6698d65383bf2fedb",
                },
            },
        )
        self.assertEqual(
            eight.policy.classification_counts,
            {
                "automatic_read": 24,
                "held_for_canary": 2,
                "mixed_or_requires_wrapper": 14,
                "persistent_write": 32,
                "physical_or_high_risk_action": 4,
                "prohibited": 1,
                "unsupported": 1,
            },
        )
        held = {
            item.upstream_name
            for item in eight.policy.tools
            if item.classification == "held_for_canary"
        }
        self.assertEqual(held, {"ha_search", "ha_get_operation_status"})
        self.assertEqual(len(eight.tool_contracts), 78)

    def test_special_provider_input_contracts_remain_exact(self):
        seven = {item["name"]: item for item in capture("7.14.2")["tools"]}
        eight = {item["name"]: item for item in capture("8.0.0")["tools"]}
        registry = load_reviewed_upstream_release_registry()
        eight_policy = registry.by_version["8.0.0"].policy.by_name
        expected = {
            "ha_manage_backup": "mixed_or_requires_wrapper",
            "ha_get_addon": "mixed_or_requires_wrapper",
            "ha_manage_addon": "mixed_or_requires_wrapper",
            "ha_reload_core": "physical_or_high_risk_action",
            "ha_restart": "physical_or_high_risk_action",
        }
        for name, classification in expected.items():
            with self.subTest(name=name):
                self.assertEqual(
                    seven[name]["inputSchema"], eight[name]["inputSchema"]
                )
                self.assertEqual(
                    eight_policy[name].classification, classification
                )

    def test_dashboard_v3_contract_is_exact_and_wrapper_stays_narrow(self):
        tool = next(
            item
            for item in capture("8.0.0")["tools"]
            if item["name"] == "ha_config_get_dashboard"
        )
        decision = decide_admission(
            server_name="ha-mcp",
            server_version="8.0.0",
            protocol_version="2025-03-26",
            tool=tool,
            attestations=tuple((item, "builtin") for item in load_attestations()),
        )
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.contract_family, CONTRACT_FAMILY_V3)
        self.assertEqual(
            COMPILED_ARGUMENT_SHAPES,
            {
                "list_dashboards": {
                    "list_only": True,
                    "include_screenshot": False,
                },
                "get_dashboard_config": {
                    "url_path": "<exact-canonical-path>",
                    "list_only": False,
                    "force_reload": "<boolean>",
                    "include_screenshot": False,
                },
            },
        )
        for forbidden in ("view_path", "mode", "query"):
            self.assertIn(forbidden, PROHIBITED_ARGUMENTS)

        changed = deepcopy(tool)
        changed["_meta"]["ha_mcp"]["policy"]["enabled"] = True
        refused = decide_admission(
            server_name="ha-mcp",
            server_version="8.0.0",
            protocol_version="2025-03-26",
            tool=changed,
            attestations=tuple((item, "builtin") for item in load_attestations()),
        )
        self.assertFalse(refused.accepted)


class Beta11GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def gateway_for(self, version: str) -> tuple[UpstreamReadGateway, FastMCP]:
        observed = capture("8.0.0" if version.startswith("8.") else version)
        transport = FakeTransport(observed["tools"], version=version)
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=transport,
            release_registry=load_reviewed_upstream_release_registry(),
            admission_validator=lambda _catalog: None,
        )
        target = server()
        await gateway.initialize(target)
        return gateway, target

    async def test_exact_8_0_admits_24_and_holds_two(self):
        gateway, target = await self.gateway_for("8.0.0")
        health = gateway.health_snapshot()
        self.assertEqual(health["dynamically_exposed_count"], 24)
        self.assertEqual(health["held_read_count"], 2)
        self.assertEqual(
            health["held_tools"],
            ["ha_get_operation_status", "ha_search"],
        )
        self.assertEqual(health["reviewed_accounted_tool_count"], 78)
        self.assertTrue(health["reviewed_tool_accounting_valid"])
        self.assertEqual(health["missing_automatic_read_count"], 0)
        self.assertEqual(health["unreviewed_tool_count"], 0)
        self.assertEqual(health["fallback_count"], 0)
        self.assertEqual(
            health["reviewed_stock_catalog_fingerprint"],
            "0bc81aa7bd94416385520b9c4c4f7d9ccbc6a49f8f65b8a2a599135463327316",
        )
        self.assertEqual(
            health["strict_full_contract_fingerprint"],
            "ff18cda3ca27abc8cca69685fb5240942cbe24a1508f73b9a26e57e1afe44d5a",
        )
        self.assertTrue(health["static_review_completed"])
        names = set(registered_tools(target))
        self.assertNotIn("ha_search", names)
        self.assertNotIn("ha_get_operation_status", names)
        self.assertEqual(len(gateway._registered_names), 24)

    async def test_unknown_8_x_does_not_inherit_exact_8_0_trust(self):
        gateway, target = await self.gateway_for("8.0.1")
        health = gateway.health_snapshot()
        self.assertEqual(health["version_status"], "rejected_unreviewed")
        self.assertEqual(health["dynamically_exposed_count"], 0)
        self.assertEqual(health["fallback_count"], 0)
        self.assertFalse(gateway._registered_names)
        self.assertNotIn("ha_search", registered_tools(target))

    async def test_exact_7_14_2_remains_26_reads(self):
        gateway, _target = await self.gateway_for("7.14.2")
        health = gateway.health_snapshot()
        self.assertEqual(health["dynamically_exposed_count"], 26)
        self.assertEqual(health["held_read_count"], 0)
        self.assertEqual(
            health["observed_catalog_fingerprint"],
            "c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c",
        )
        self.assertEqual(health["fallback_count"], 0)
