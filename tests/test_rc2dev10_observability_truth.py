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
    CONTRACT_FAMILY_V3,
    ContractValidationError,
    ReleaseAttestation,
    decide_admission,
    load_attestations,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
    _stable_hash,
)

sys.path.insert(0, str(ROOT / "tests"))
from test_rc3a_reviewed_contract import published_runtime_tool  # noqa: E402


# HAMCP-437 I2c-2: the cases run on the exact 9.2.0 release, the only one 2.9.0
# admits. A synthetic unattested or signed 9.2.x release presents the 9.2.0 tool.
REVIEWED = "9.2.0"


def published_tool(version: str = REVIEWED) -> dict:
    return published_runtime_tool()


def reviewed_entry() -> ReleaseAttestation:
    return next(
        item for item in load_attestations() if item.upstream_version == REVIEWED
    )


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
    async def test_builtin_release_selects_truthful_evidence(self):
        # HAMCP-437 I2c-2: the 9.2.0 attestation carries the raw input-schema
        # fingerprint only; it has no reviewed-security or published-runtime
        # descriptor evidence, so those two match flags are truthfully false.
        entries = {entry.upstream_version: entry for entry in load_attestations()}
        for version in (REVIEWED,):
            with self.subTest(version=version):
                health = await provider_health(version)
                entry = entries[version]
                self.assertEqual(health["attestation_entry_id"], entry.entry_id)
                self.assertEqual(health["contract_family"], CONTRACT_FAMILY_V3)
                self.assertEqual(health["trust_profile"], CONTRACT_FAMILY_V3)
                self.assertEqual(health["attested_upstream_version"], version)
                self.assertEqual(health["observed_upstream_version"], version)
                self.assertEqual(
                    health["expected_input_schema_fingerprint"],
                    entry.raw_input_schema_fingerprint,
                )
                self.assertEqual(
                    health["expected_input_schema_fingerprint"],
                    _stable_hash(published_tool(version)["inputSchema"]),
                )
                self.assertEqual(
                    health["expected_reviewed_security_contract_fingerprint"],
                    entry.reviewed_security_descriptor_fingerprint,
                )
                self.assertIsNone(entry.reviewed_security_descriptor_fingerprint)
                self.assertEqual(
                    health["expected_published_runtime_descriptor_fingerprint"],
                    entry.published_runtime_descriptor_fingerprint,
                )
                self.assertIsNone(entry.published_runtime_descriptor_fingerprint)
                for field in (
                    "input_contract_match",
                    "security_contract_match",
                    "output_contract_match",
                    "runtime_contract_match",
                    "reviewed_contract_match",
                    "input_schema_match",
                ):
                    self.assertTrue(health[field], field)
                for field in (
                    "reviewed_security_contract_match",
                    "published_runtime_descriptor_match",
                ):
                    self.assertFalse(health[field], field)
                self.assertEqual(
                    health["runtime_descriptor_drift"],
                    "descriptive_metadata_only",
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


class RegistryAndDriftTests(unittest.IsolatedAsyncioTestCase):
    def test_pre_dev10_signed_entry_remains_readable(self):
        value = dict(reviewed_entry().__dict__)
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
            server_version=REVIEWED,
            protocol_version="2025-03-26",
            tool=published_tool(),
            attestations=((restored, "remote_cached"),),
        )
        self.assertTrue(decision.accepted)

    def test_informational_fingerprints_are_bounded_and_strict_when_present(self):
        value = dict(reviewed_entry().__dict__)
        value["raw_input_schema_fingerprint"] = "not-a-fingerprint"
        with self.assertRaises(ContractValidationError):
            ReleaseAttestation.from_mapping(value)

    async def test_unattested_contract_health_reports_missing_release_authority(self):
        provider = UpstreamDashboardProvider()
        provider.configure(
            settings(),
            transport=DiscoveryTransport(
                "9.2.1",
                published_tool(),
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
        self.assertEqual(health["observed_upstream_version"], "9.2.1")
        self.assertEqual(health["revocation_status"], "not_evaluated")
        self.assertEqual(health["runtime_descriptor_drift"], "not_comparable")
        self.assertFalse(health["input_contract_match"])
        self.assertFalse(health["security_contract_match"])
        self.assertFalse(health["output_contract_match"])
        self.assertFalse(health["runtime_contract_match"])

    async def test_selected_remote_attestation_supplies_its_evidence_truthfully(self):
        # HAMCP-437 I2c-2: the selected signed entry is a synthetic 9.2.1 copy
        # of the 9.2.0 attestation. Health reports the evidence that entry
        # carries: the raw input-schema fingerprint matches, and the
        # reviewed-security and published-runtime families it lacks are false.
        entry = replace(
            reviewed_entry(),
            entry_id="ha-mcp-v9.2.1-synthetic",
            upstream_version="9.2.1",
            source_tag="v9.2.1",
        )
        for source in ("remote_fresh", "remote_cached"):
            with self.subTest(source=source):
                health = await provider_health(
                    "9.2.1",
                    published_tool(),
                    SelectedRegistry(entry, source),
                )
                self.assertEqual(
                    health["admission_status"],
                    "admitted_signed_registry_attestation",
                )
                self.assertEqual(health["admission_source"], source)
                self.assertEqual(health["trust_profile"], CONTRACT_FAMILY_V3)
                self.assertEqual(health["attestation_entry_id"], entry.entry_id)
                self.assertTrue(health["input_schema_match"])
                self.assertFalse(health["reviewed_security_contract_match"])
                self.assertFalse(health["published_runtime_descriptor_match"])
                self.assertTrue(health["runtime_contract_match"])

    async def test_revoked_selected_attestation_fails_closed_truthfully(self):
        entry = replace(reviewed_entry(), revoked=True)
        provider = UpstreamDashboardProvider()
        provider.configure(
            settings(),
            transport=DiscoveryTransport(REVIEWED, published_tool()),
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
        entry = reviewed_entry()
        poisoned = replace(
            entry,
            input_contract_fingerprint="0" * 64,
            raw_input_schema_fingerprint=_stable_hash(
                published_tool()["inputSchema"]
            ),
        )
        decision = decide_admission(
            server_name="ha-mcp",
            server_version=REVIEWED,
            protocol_version="2025-03-26",
            tool=published_tool(),
            attestations=((poisoned, "remote_fresh"),),
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(
            decision.failure_category, "upstream_input_contract_mismatch"
        )

    async def test_semantic_drift_matrix_rejects_on_the_exact_release(self):
        # HAMCP-437 I2c-2: on the exact 9.2.0 release the whole getter
        # descriptor is the used-tool contract, so every axis fails closed as a
        # runtime-contract mismatch; a protocol change keeps its own reason.
        # The 7.14.1 case that admitted descriptive prose drift is retired:
        # 9.2.0 refuses it (test_rc3a_reviewed_contract
        # test_descriptive_and_presentation_drift_fails_closed_on_the_exact_release).
        cases = {}
        raw_input = published_tool()
        raw_input["inputSchema"]["properties"]["include_screenshot"]["default"] = True
        cases["input"] = (raw_input, "upstream_runtime_contract_mismatch")
        security = published_tool()
        security["annotations"]["destructiveHint"] = True
        cases["security"] = (security, "upstream_runtime_contract_mismatch")
        output = published_tool()
        output["outputSchema"] = {"type": "object"}
        cases["output"] = (output, "upstream_runtime_contract_mismatch")
        runtime = published_tool()
        cases["runtime"] = (runtime, "unsupported_protocol_version")

        entries = tuple((entry, "builtin") for entry in load_attestations())
        for name, (tool, reason) in cases.items():
            protocol = "2024-11-05" if name == "runtime" else "2025-03-26"
            decision = decide_admission(
                server_name="ha-mcp",
                server_version=REVIEWED,
                protocol_version=protocol,
                tool=tool,
                attestations=entries,
            )
            self.assertFalse(decision.accepted, name)
            self.assertEqual(decision.failure_category, reason, name)

        health = await provider_health(REVIEWED, published_tool())
        self.assertEqual(health["admission_status"], "admitted_builtin_attestation")
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
