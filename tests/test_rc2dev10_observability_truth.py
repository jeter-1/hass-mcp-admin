import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.capabilities import (  # noqa: E402
    CAPABILITY_PROVIDER_MATRIX,
    build_capability_catalog,
)
from ha_mcp_engineering.clients.mcp import McpDashboardHandshake  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import DashboardProviderError  # noqa: E402
from ha_mcp_engineering.providers.upstream_contracts import (  # noqa: E402
    CONTRACT_FAMILY,
    ContractValidationError,
    ReleaseAttestation,
    decide_admission,
    load_attestations,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    REVIEWED_PUBLISHED_RUNTIME_DESCRIPTOR_FINGERPRINT,
    REVIEWED_SCHEMA_FINGERPRINT,
    REVIEWED_SECURITY_CONTRACT_FINGERPRINT,
    UpstreamDashboardProvider,
    _reviewed_security_contract_projection,
    _stable_hash,
)


CONTRACTS = BETA / "ha_mcp_engineering" / "providers" / "contracts"
SEVEN_THIRTEEN = CONTRACTS / "ha_mcp_7_13_dashboard_read_v1.json"
SEVEN_THIRTEEN_DELTA = CONTRACTS / "ha_mcp_7_13_published_runtime_delta.json"
SEVEN_FOURTEEN_PUBLISHED = (
    CONTRACTS / "ha_mcp_7_14_published_runtime_descriptor.json"
)


def published_tool(version: str) -> dict:
    if version != "7.13.0":
        return json.loads(SEVEN_FOURTEEN_PUBLISHED.read_text(encoding="utf-8"))
    tool = json.loads(SEVEN_THIRTEEN.read_text(encoding="utf-8"))
    delta = json.loads(SEVEN_THIRTEEN_DELTA.read_text(encoding="utf-8"))
    tool.setdefault("_meta", {})["ha_mcp"] = delta["descriptor_delta"][
        "added"
    ]["/_meta/ha_mcp"]
    return tool


def settings() -> Settings:
    return Settings(
        ha_url="http://supervisor/core",
        ha_token="synthetic-supervisor-token",
        access_secret="synthetic-engineering-access-secret",
        port=8100,
        audit_path="audit.jsonl",
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
        upstream_dashboard_mcp_url=(
            "http://upstream:9583/synthetic-upstream-secret/mcp"
        ),
    )


class DiscoveryTransport:
    def __init__(self, version: str, tool: dict, protocol="2025-03-26"):
        self.handshake = McpDashboardHandshake(
            protocol_version=protocol,
            server_name="ha-mcp",
            server_version=version,
            tools=(tool,),
            connection_latency_ms=1.0,
        )

    async def discover(self):
        return self.handshake


class SelectedRegistry:
    def __init__(self, entry, source="remote_fresh"):
        self.entry = entry
        self.source = source
        self.enabled = False
        self.last_failure_category = None

    def effective_attestations(self):
        return ((self.entry, self.source),)

    def compatible_contract_fallback_rejection(self):
        return None

    def refresh_due(self):
        return False

    def snapshot(self):
        return {
            "registry_enabled": self.source != "builtin",
            "registry_refresh_status": (
                "success" if self.source == "remote_fresh" else "disabled"
            ),
        }


async def provider_health(version: str, tool: dict | None = None, registry=None):
    provider = UpstreamDashboardProvider()
    provider.configure(
        settings(),
        transport=DiscoveryTransport(version, tool or published_tool(version)),
        registry=registry,
    )
    await provider.refresh_capabilities()
    return provider.health_snapshot()


class BuiltinObservabilityMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_builtin_releases_select_truthful_legacy_evidence(self):
        entries = {entry.upstream_version: entry for entry in load_attestations()}
        for version in ("7.13.0", "7.14.0", "7.14.1"):
            with self.subTest(version=version):
                health = await provider_health(version)
                entry = entries[version]
                self.assertEqual(health["attestation_entry_id"], entry.entry_id)
                self.assertEqual(health["contract_family"], CONTRACT_FAMILY)
                self.assertEqual(health["trust_profile"], CONTRACT_FAMILY)
                self.assertEqual(health["attested_upstream_version"], version)
                self.assertEqual(health["observed_upstream_version"], version)
                self.assertEqual(
                    health["expected_input_schema_fingerprint"],
                    entry.raw_input_schema_fingerprint,
                )
                self.assertEqual(
                    health["expected_reviewed_security_contract_fingerprint"],
                    entry.reviewed_security_descriptor_fingerprint,
                )
                self.assertEqual(
                    health["expected_published_runtime_descriptor_fingerprint"],
                    entry.published_runtime_descriptor_fingerprint,
                )
                for field in (
                    "input_contract_match",
                    "security_contract_match",
                    "output_contract_match",
                    "runtime_contract_match",
                    "reviewed_contract_match",
                    "input_schema_match",
                    "reviewed_security_contract_match",
                    "published_runtime_descriptor_match",
                ):
                    self.assertTrue(health[field], field)
                self.assertNotEqual(
                    health["runtime_descriptor_drift"],
                    "runtime_descriptor_semantic_drift",
                )
                self.assertEqual(health["contract_status"], "valid")
                self.assertEqual(health["capability_status"], "available")
                self.assertEqual(
                    health["admission_status"], "admitted_builtin_attestation"
                )
                self.assertEqual(health["revocation_status"], "not_revoked")
                self.assertFalse(health["writes_allowed"])
                self.assertFalse(health["screenshots_allowed"])
                self.assertFalse(health["preference_writes_allowed"])
                self.assertEqual(health["allowlisted_tool_count"], 1)

    async def test_live_dev9_stale_713_expectation_regression(self):
        tool = published_tool("7.14.1")
        self.assertNotEqual(_stable_hash(tool["inputSchema"]), REVIEWED_SCHEMA_FINGERPRINT)
        self.assertNotEqual(
            _stable_hash(_reviewed_security_contract_projection(tool)),
            REVIEWED_SECURITY_CONTRACT_FINGERPRINT,
        )
        self.assertNotEqual(
            _stable_hash(tool), REVIEWED_PUBLISHED_RUNTIME_DESCRIPTOR_FINGERPRINT
        )
        health = await provider_health("7.14.1", tool)
        self.assertTrue(health["input_schema_match"])
        self.assertTrue(health["reviewed_security_contract_match"])
        self.assertTrue(health["published_runtime_descriptor_match"])
        self.assertNotEqual(
            health["runtime_descriptor_drift"],
            "runtime_descriptor_semantic_drift",
        )


class RegistryAndDriftTests(unittest.IsolatedAsyncioTestCase):
    def test_pre_dev10_signed_entry_remains_readable(self):
        value = dict(load_attestations()[0].__dict__)
        for key in (
            "raw_input_schema_fingerprint",
            "reviewed_security_descriptor_fingerprint",
            "fixture_runtime_descriptor_fingerprint",
            "published_runtime_descriptor_fingerprint",
        ):
            value.pop(key)
        restored = ReleaseAttestation.from_mapping(value)
        self.assertIsNone(restored.raw_input_schema_fingerprint)
        decision = decide_admission(
            server_name="ha-mcp",
            server_version="7.13.0",
            protocol_version="2025-03-26",
            tool=published_tool("7.13.0"),
            attestations=((restored, "remote_cached"),),
        )
        self.assertTrue(decision.accepted)

    def test_informational_fingerprints_are_bounded_and_strict_when_present(self):
        value = dict(load_attestations()[0].__dict__)
        value["raw_input_schema_fingerprint"] = "not-a-fingerprint"
        with self.assertRaises(ContractValidationError):
            ReleaseAttestation.from_mapping(value)

    async def test_unattested_contract_health_reports_missing_release_authority(self):
        provider = UpstreamDashboardProvider()
        provider.configure(
            settings(),
            transport=DiscoveryTransport(
                "7.14.2",
                published_tool("7.14.1"),
            ),
        )
        with self.assertRaises(DashboardProviderError):
            await provider.refresh_capabilities()
        health = provider.health_snapshot()
        self.assertEqual(
            health["admission_status"], "rejected_unknown_release"
        )
        self.assertEqual(
            health["validation_reason"], "upstream_attestation_missing"
        )
        self.assertEqual(health["capability_status"], "unavailable")
        self.assertIsNone(health["admission_source"])
        self.assertIsNone(health["attestation_entry_id"])
        self.assertIsNone(health["attested_upstream_version"])
        self.assertIsNone(health["attested_source_commit"])
        self.assertIsNone(health["attested_image_index_digest"])
        self.assertEqual(health["observed_upstream_version"], "7.14.2")
        self.assertEqual(health["revocation_status"], "not_evaluated")
        self.assertEqual(health["runtime_descriptor_drift"], "not_comparable")
        self.assertFalse(health["input_contract_match"])
        self.assertFalse(health["security_contract_match"])
        self.assertFalse(health["output_contract_match"])
        self.assertFalse(health["runtime_contract_match"])

    async def test_selected_remote_attestation_supplies_both_evidence_families(self):
        entry = replace(
            load_attestations()[-1],
            entry_id="ha-mcp-v7.14.2-synthetic",
            upstream_version="7.14.2",
            source_tag="v7.14.2",
        )
        for source in ("remote_fresh", "remote_cached"):
            with self.subTest(source=source):
                health = await provider_health(
                    "7.14.2",
                    published_tool("7.14.1"),
                    SelectedRegistry(entry, source),
                )
                self.assertEqual(
                    health["admission_status"],
                    "admitted_signed_registry_attestation",
                )
                self.assertEqual(health["admission_source"], source)
                self.assertEqual(health["trust_profile"], CONTRACT_FAMILY)
                self.assertTrue(health["input_schema_match"])
                self.assertTrue(health["reviewed_security_contract_match"])
                self.assertTrue(health["published_runtime_descriptor_match"])
                self.assertTrue(health["runtime_contract_match"])

    async def test_revoked_selected_attestation_fails_closed_truthfully(self):
        entry = replace(load_attestations()[-1], revoked=True)
        provider = UpstreamDashboardProvider()
        provider.configure(
            settings(),
            transport=DiscoveryTransport("7.14.1", published_tool("7.14.1")),
            registry=SelectedRegistry(entry),
        )
        with self.assertRaises(DashboardProviderError):
            await provider.refresh_capabilities()
        health = provider.health_snapshot()
        self.assertEqual(health["revocation_status"], "revoked")
        self.assertEqual(
            health["admission_status"], "rejected_revoked_attestation"
        )
        self.assertEqual(health["capability_status"], "unavailable")
        self.assertFalse(health["input_contract_match"])
        self.assertFalse(health["security_contract_match"])
        self.assertFalse(health["output_contract_match"])
        self.assertFalse(health["runtime_contract_match"])

    def test_informational_fingerprints_cannot_enable_admission(self):
        entry = load_attestations()[-1]
        poisoned = replace(
            entry,
            input_contract_fingerprint="0" * 64,
            raw_input_schema_fingerprint=_stable_hash(
                published_tool("7.14.1")["inputSchema"]
            ),
        )
        decision = decide_admission(
            server_name="ha-mcp",
            server_version="7.14.1",
            protocol_version="2025-03-26",
            tool=published_tool("7.14.1"),
            attestations=((poisoned, "remote_fresh"),),
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(
            decision.failure_category, "upstream_input_contract_mismatch"
        )

    async def test_semantic_drift_matrix_rejects_and_descriptive_drift_admits(self):
        cases = {}
        raw_input = published_tool("7.14.1")
        raw_input["inputSchema"]["properties"]["include_screenshot"]["default"] = True
        cases["input"] = (raw_input, "upstream_input_contract_mismatch")
        security = published_tool("7.14.1")
        security["annotations"]["destructiveHint"] = True
        cases["security"] = (security, "upstream_security_contract_mismatch")
        output = published_tool("7.14.1")
        output["outputSchema"] = {"type": "object"}
        cases["output"] = (output, "upstream_output_contract_mismatch")
        runtime = published_tool("7.14.1")
        cases["runtime"] = (runtime, "unsupported_protocol_version")

        entries = tuple((entry, "builtin") for entry in load_attestations())
        for name, (tool, reason) in cases.items():
            protocol = "2024-11-05" if name == "runtime" else "2025-03-26"
            decision = decide_admission(
                server_name="ha-mcp",
                server_version="7.14.1",
                protocol_version=protocol,
                tool=tool,
                attestations=entries,
            )
            self.assertFalse(decision.accepted, name)
            self.assertEqual(decision.failure_category, reason, name)

        descriptive = published_tool("7.14.1")
        descriptive["description"] = "Changed reviewed-release display prose only."
        health = await provider_health("7.14.1", descriptive)
        self.assertTrue(health["input_contract_match"])
        self.assertTrue(health["security_contract_match"])
        self.assertTrue(health["output_contract_match"])
        self.assertTrue(health["runtime_contract_match"])
        self.assertFalse(health["published_runtime_descriptor_match"])
        self.assertEqual(
            health["runtime_descriptor_drift"], "descriptive_metadata_only"
        )


class CapabilityMetadataTests(unittest.TestCase):
    def test_active_catalog_and_provider_matrix_use_contract_family(self):
        catalog = build_capability_catalog()
        active = [
            item
            for item in catalog["beta_native"]
            if item["tool"] in {"list_dashboards", "get_dashboard_config"}
        ]
        self.assertEqual(len(active), 2)
        for item in active:
            self.assertEqual(item["trust_profile"], CONTRACT_FAMILY)
            self.assertEqual(item["fallback"], "none")
        for item in CAPABILITY_PROVIDER_MATRIX:
            if item["tool"] in {"list_dashboards", "get_dashboard_config"}:
                self.assertEqual(item["trust_profile"], CONTRACT_FAMILY)
                self.assertNotIn("version-pinned", item["security_justification"])
                self.assertIn("exact compiled dashboard-read contract family", item["security_justification"])
                self.assertIn("exact release attestation authoritative", item["security_justification"])
        serialized = json.dumps({"catalog": catalog, "matrix": CAPABILITY_PROVIDER_MATRIX})
        self.assertNotIn("ha_mcp_7_13_dashboard_read_v1", serialized)


if __name__ == "__main__":
    unittest.main()
