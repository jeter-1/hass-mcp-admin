from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
CAPTURES = ROOT / "docs" / "evidence" / "upstream-read-compatibility"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleGateway,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED,
    LIFECYCLE_ADDON_RESPONSE_MODEL_STRUCTURED_V1,
    ReviewedOperationalLifecycleProvider,
)
from ha_mcp_engineering.providers.supervisor_self import (  # noqa: E402
    SupervisorSelfAddonIdentity,
)
from ha_mcp_engineering.providers.upstream_contracts import (  # noqa: E402
    CONTRACT_FAMILY_V3,
    decide_admission,
    load_attestations,
)
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.tools import registered_tools  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1,
    load_reviewed_upstream_release_registry,
    validate_reviewed_release_catalog,
)
from tests.test_2_1a_beta2_operational_lifecycle import (  # noqa: E402
    FakeMcpTransport,
    LegacyGateway,
    UPSTREAM_ADDON_SLUG,
    lifecycle_settings,
)
from tests.test_readonly_upstream_gateway import (  # noqa: E402
    FakeTransport,
    settings,
)


VERSION = "8.1.0"
ENTRY_ID = "ha-mcp-v8.1.0-4c07e625"
PROTOCOL = "2025-03-26"
STANDALONE_RAW = (
    "d8ac6e0736f7bfdc58d3ec8a31f512d8ab70be13336753f4388d7619019a53a2"
)
ADDON_RAW = (
    "6b5cd123cc60ff6668c2ff4dd1f9cedbe6a7a21fe43fe00471cd46611d4406d7"
)
NORMALIZED = (
    "5ec7b1f4a4c2ffabb2acc14c73a230f08a5f94908b6f27e57cb6739d662f03d7"
)


def capture(version: str) -> dict:
    return json.loads(
        (CAPTURES / f"ha-mcp-{version}.json").read_text(encoding="utf-8")
    )


def addon_tools() -> list[dict]:
    tools = deepcopy(capture(VERSION)["tools"])
    for tool in tools:
        policy = tool["_meta"]["ha_mcp"]["policy"]
        policy.update(
            {"deployment": "addon", "enabled": True, "live": True}
        )
    return tools


def native_server() -> FastMCP:
    server = FastMCP("ha-mcp-8.1.0-review")

    async def native() -> str:
        return "native"

    server.tool(name="native_8_1_review")(native)
    return server


class ExactEightOneRegistryTests(unittest.IsolatedAsyncioTestCase):
    def test_exact_oci_entry_accounts_for_all_tools_and_artifacts(self):
        release = load_reviewed_upstream_release_registry().by_version[VERSION]

        self.assertEqual(release.entry_id, ENTRY_ID)
        self.assertEqual(
            release.source_commit,
            "0683f5ff34e5c71f35bce08d1cedcdee3c0a60b2",
        )
        self.assertEqual(
            release.image_index_digest,
            "sha256:4c07e6259a42ed33958ac9d018aba7f4b03ea676388fd3264f8abde5ea767f76",
        )
        self.assertEqual(
            release.architecture_image_digests_by_platform,
            {
                "linux/amd64": "sha256:c1d7eb571a417c5b3765c1d4971cbedb7d2800725bb9bab1a510c876cbacb78c",
                "linux/arm64": "sha256:4bbb28a184e1a9a307bff2b55fe4423cb011e7ef7c0d4fade407c6460d6481b0",
            },
        )
        self.assertEqual(
            release.addon_artifact_digests_by_platform,
            {
                "linux/amd64": {
                    "index_digest": "sha256:2744a11c90f7a66e61fabe8166d058191d236094393c50d976978407c039d45d",
                    "image_manifest_digest": "sha256:f415b72351d79414a3133c227622633d9c190a3f4f6b849eed93ac524ac1c2d5",
                },
                "linux/arm64": {
                    "index_digest": "sha256:71bd08ac7ab4272bc226b91d299929949fa24b674e164121566bc1d84666e273",
                    "image_manifest_digest": "sha256:2dad5c7f8afcfb8c5624d82a7d9c322fc70351d32d9697e07a162ec7015250b0",
                },
            },
        )
        self.assertEqual(release.advertised_tool_count, 78)
        self.assertEqual(len(release.tool_contracts), 78)
        self.assertEqual(
            release.policy.classification_counts,
            {
                "automatic_read": 24,
                "held_for_canary": 2,
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
            {"ha_search", "ha_get_operation_status"},
        )

    def test_standalone_and_addon_catalogs_share_exact_normalized_identity(self):
        release = load_reviewed_upstream_release_registry().by_version[VERSION]
        results = []
        for tools in (capture(VERSION)["tools"], addon_tools()):
            results.append(
                validate_reviewed_release_catalog(
                    release,
                    observed_server_name="ha-mcp",
                    observed_upstream_version=VERSION,
                    observed_protocol_version=PROTOCOL,
                    tools=tools,
                )
            )

        self.assertTrue(all(result.valid for result in results))
        self.assertEqual(
            [result.observed_raw_catalog_fingerprint for result in results],
            [STANDALONE_RAW, ADDON_RAW],
        )
        self.assertEqual(
            {result.normalized_catalog_fingerprint for result in results},
            {NORMALIZED},
        )
        self.assertTrue(
            all(result.reviewed_accounted_count == 78 for result in results)
        )
        self.assertTrue(
            all(
                result.aggregate_fingerprint_model
                == REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1
                for result in results
            )
        )

    def test_hacs_write_surface_is_reclassified_as_one_persistent_tool(self):
        old = next(
            item
            for item in capture("8.0.0")["tools"]
            if item["name"] == "ha_manage_hacs"
        )
        new = next(
            item
            for item in capture(VERSION)["tools"]
            if item["name"] == "ha_manage_hacs"
        )
        policy = load_reviewed_upstream_release_registry().by_version[
            VERSION
        ].policy.by_name["ha_manage_hacs"]

        self.assertNotEqual(old["description"], new["description"])
        self.assertNotEqual(old["inputSchema"], new["inputSchema"])
        self.assertEqual(old["annotations"], new["annotations"])
        self.assertEqual(old["outputSchema"], new["outputSchema"])
        self.assertEqual(
            old["inputSchema"]["properties"]["action"]["enum"],
            ["download", "add_repository"],
        )
        self.assertEqual(
            new["inputSchema"]["properties"]["action"]["enum"],
            ["download", "add_repository", "remove"],
        )
        self.assertEqual(policy.classification, "persistent_write")
        self.assertFalse(policy.reviewed_annotations.read_only)

    async def test_exact_release_registers_24_reads_but_never_hacs_write(self):
        transport = FakeTransport(capture(VERSION)["tools"], version=VERSION)
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=transport,
            release_registry=load_reviewed_upstream_release_registry(),
            admission_validator=lambda _catalog: None,
        )
        server = native_server()

        await gateway.initialize(server)

        health = gateway.health_snapshot()
        self.assertEqual(health["dynamically_exposed_count"], 24)
        self.assertEqual(health["held_read_count"], 2)
        self.assertEqual(health["fallback_count"], 0)
        self.assertNotIn("ha_manage_hacs", gateway._registered_names)
        self.assertNotIn("ha_manage_hacs", registered_tools(server))
        runtime_references = []
        for path in (BETA / "ha_mcp_engineering").rglob("*.py"):
            if "ha_manage_hacs" in path.read_text(encoding="utf-8"):
                runtime_references.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(runtime_references, [])

    async def test_unknown_8_1_1_does_not_inherit_exact_release_trust(self):
        transport = FakeTransport(capture(VERSION)["tools"], version="8.1.1")
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=transport,
            release_registry=load_reviewed_upstream_release_registry(),
            admission_validator=lambda _catalog: None,
        )
        server = native_server()

        await gateway.initialize(server)

        health = gateway.health_snapshot()
        self.assertEqual(health["version_status"], "rejected_unreviewed")
        self.assertEqual(health["dynamically_exposed_count"], 0)
        self.assertEqual(health["fallback_count"], 0)
        self.assertFalse(gateway._registered_names)

    def test_dashboard_v3_exact_release_and_addon_policy_state_are_admitted(self):
        attestations = tuple(
            (item, "builtin") for item in load_attestations()
        )
        standalone = next(
            item
            for item in capture(VERSION)["tools"]
            if item["name"] == "ha_config_get_dashboard"
        )
        addon = next(
            item for item in addon_tools() if item["name"] == standalone["name"]
        )
        for descriptor in (standalone, addon):
            decision = decide_admission(
                server_name="ha-mcp",
                server_version=VERSION,
                protocol_version=PROTOCOL,
                tool=descriptor,
                attestations=attestations,
            )
            self.assertTrue(decision.accepted)
            self.assertEqual(decision.contract_family, CONTRACT_FAMILY_V3)
            self.assertEqual(
                decision.contract.runtime_fingerprint,
                "806f6d6b0b54cd49162684834e650f8ca7c8f2735b36e8772263b1bbe00a5569",
            )


class _Rest:
    async def request(self, method: str, path: str):
        if (method, path) != ("GET", "/config"):
            raise AssertionError("unexpected REST request")
        return {"location_name": "Synthetic Home", "version": "2026.7.4"}


class _WebSocket:
    async def command(self, payload: dict):
        if payload == {"type": "get_services"}:
            return {"automation": {"reload": {}}}
        if payload == {"type": "get_states"}:
            return []
        raise AssertionError("unexpected WebSocket request")


def configured_lifecycle(
    transport: FakeMcpTransport,
) -> ReviewedOperationalLifecycleProvider:
    provider = ReviewedOperationalLifecycleProvider()
    provider.configure(
        lifecycle_settings(
            "http://abcdef12-ha-mcp:9583/synthetic-upstream-secret/mcp"
        ),
        transport=transport,
    )
    return provider


def lifecycle_gateway(
    provider: ReviewedOperationalLifecycleProvider,
) -> OperationalLifecycleGateway:
    async def validate_configuration():
        return {"result": "valid", "errors": None}

    async def self_identity():
        return SupervisorSelfAddonIdentity(
            slug="df26dea6_hass_mcp_engineering_beta",
            name="HA MCP Engineering Server Beta",
            version="2.2.0-beta.21",
            repository="df26dea6",
        )

    return OperationalLifecycleGateway(
        provider,
        _Rest(),
        _WebSocket(),
        configuration_validator=validate_configuration,
        runtime_snapshot=lambda: {
            "upstream_version": VERSION,
            "upstream_protocol": PROTOCOL,
            "upstream_catalog_fingerprint": STANDALONE_RAW,
            "upstream_admission_status": "admitted_exact",
            "fallback_count": 0,
        },
        process_instance_id="ha-mcp-8.1.0-review",
        self_addon_identity_resolver=self_identity,
    )


class ExactEightOneLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_release_reuses_reviewed_structured_response_model(self):
        provider = configured_lifecycle(FakeMcpTransport(VERSION))

        addon = await provider.get_addon(UPSTREAM_ADDON_SLUG)

        self.assertEqual(addon["version"], VERSION)
        self.assertEqual(addon["upstream_addon_identity"]["status"], "bound")
        health = provider.health_snapshot()
        self.assertEqual(
            health["lifecycle_addon_response_contract_model"],
            LIFECYCLE_ADDON_RESPONSE_MODEL_STRUCTURED_V1,
        )
        self.assertEqual(
            health["lifecycle_addon_response_envelope_variant"],
            LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED,
        )
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_tagged_tree_old_version_never_becomes_installed_identity(self):
        transport = FakeMcpTransport(VERSION)
        upstream = next(
            addon
            for addon in transport.addons
            if addon["slug"] == UPSTREAM_ADDON_SLUG
        )
        upstream["version"] = "8.0.0"
        provider = configured_lifecycle(transport)

        addon = await provider.get_addon(UPSTREAM_ADDON_SLUG)

        self.assertEqual(addon["version"], "8.0.0")
        self.assertEqual(
            addon["upstream_addon_identity"]["status"], "conflicting"
        )
        self.assertNotIn(
            "installed_version", addon["upstream_addon_identity"]
        )
        self.assertEqual(sum(provider.health_snapshot()["dispatch_counts"].values()), 0)

    async def test_version_disagreement_has_bounded_current_planning_impact(self):
        transport = FakeMcpTransport(VERSION)
        next(
            addon
            for addon in transport.addons
            if addon["slug"] == UPSTREAM_ADDON_SLUG
        )["version"] = "8.0.0"
        provider = configured_lifecycle(transport)
        gateway = lifecycle_gateway(provider)
        with tempfile.TemporaryDirectory() as directory:
            repository = ChangePlanRepository(Path(directory) / "plans")
            service = ChangeGovernanceService(
                repository,
                LegacyGateway(),
                AuditLogger(
                    str(Path(directory) / "audit.jsonl"),
                    "synthetic-access-secret-value",
                ),
                lifecycle_gateway=gateway,
            )

            with self.assertRaises(GovernanceError):
                await service.create_addon_restart_plan(
                    addon_slug=UPSTREAM_ADDON_SLUG,
                    expiration_minutes=5,
                )
            self.assertEqual(repository.list(), [])

            with self.assertRaises(GovernanceError):
                await service.create_addon_restart_plan(
                    addon_slug="local_test_addon",
                    expiration_minutes=5,
                )
            self.assertEqual(repository.list(), [])

            engineering_slug = "df26dea6_hass_mcp_engineering_beta"
            engineering_addon = next(
                addon
                for addon in transport.addons
                if addon["slug"] == engineering_slug
            )
            engineering_addon["version"] = "2.2.0-beta.21"
            self_restart_plan = await service.create_addon_restart_plan(
                addon_slug=engineering_slug,
                expiration_minutes=5,
            )

            reload_plan = await service.create_reload_plan(
                reload_target="automation",
                expiration_minutes=5,
            )
            restart_plan = await service.create_home_assistant_restart_plan(
                expiration_minutes=5,
            )

            self.assertEqual(len(repository.list()), 3)
        self.assertTrue(self_restart_plan["proposal_only"])
        self.assertFalse(
            self_restart_plan["provider_dispatch_occurred"]
        )
        self.assertTrue(reload_plan["proposal_only"])
        self.assertTrue(restart_plan["proposal_only"])
        self.assertFalse(reload_plan["provider_dispatch_occurred"])
        self.assertFalse(restart_plan["provider_dispatch_occurred"])
        self.assertNotIn(
            "ha_manage_addon", [name for name, _arguments in transport.calls]
        )
        self.assertNotIn(
            "ha_reload_core", [name for name, _arguments in transport.calls]
        )
        self.assertNotIn(
            "ha_restart", [name for name, _arguments in transport.calls]
        )
        health = provider.health_snapshot()
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)


if __name__ == "__main__":
    unittest.main()
