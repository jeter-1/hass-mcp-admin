from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))
sys.path.insert(0, str(ROOT / "tests"))

from ha_mcp_engineering.ha_mcp_readmission.registry import (  # noqa: E402
    REGISTRY_URL,
    SignedReleaseRegistry,
    TRUST_ANCHOR_KEY_ID,
)
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.tools import (  # noqa: E402
    ENGINEERING_STATIC_TOOL_COUNT,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
    schema_fingerprint,
)
from signed_registry_fixtures import RegistrySigner  # noqa: E402
from test_ha_mcp_production_readmission import (  # noqa: E402
    _GatewayTransport,
    _capture_for_release,
    _settings,
    _signed_entry_for,
)


EXACT_8_4_1_READS = frozenset(
    {
        "ha_config_get_automation",
        "ha_config_get_calendar_events",
        "ha_config_get_category",
        "ha_config_get_label",
        "ha_config_get_scene",
        "ha_config_get_script",
        "ha_config_list_dashboard_resources",
        "ha_config_list_groups",
        "ha_config_list_helpers",
        "ha_eval_template",
        "ha_get_automation_traces",
        "ha_get_blueprint",
        "ha_get_device",
        "ha_get_entity",
        "ha_get_entity_exposure",
        "ha_get_hacs_info",
        "ha_get_history",
        "ha_get_overview",
        "ha_get_skill_guide",
        "ha_get_state",
        "ha_get_todo",
        "ha_get_zone",
        "ha_list_floors_areas",
        "ha_list_services",
        "ha_search",
    }
)
UNCHANGED_8_4_1_READS = EXACT_8_4_1_READS - {
    "ha_config_list_helpers",
    "ha_get_overview",
    "ha_get_skill_guide",
    "ha_search",
}


class Beta56HaMcp841CompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.compiled = load_reviewed_upstream_release_registry()
        self.release = self.compiled.by_version["8.4.1"]
        self.previous = self.compiled.by_version["8.2.0"]
        self.capture = _capture_for_release(self.release)
        self.signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        self.now = datetime(2026, 9, 2, 22, tzinfo=timezone.utc)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def _registry(self, entry: dict | None) -> SignedReleaseRegistry:
        raw = self.signer.journal_raw(
            sequence=1,
            generated_at=self.now - timedelta(minutes=1),
            expires_at=self.now + timedelta(days=1),
            entries=[] if entry is None else [entry],
            revocations=[],
        )

        async def fetcher(url: str, maximum: int) -> bytes:
            self.assertEqual(url, REGISTRY_URL)
            self.assertLessEqual(len(raw), maximum)
            return raw

        return SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=Path(self.temporary.name) / "registry-cache.json",
            fetcher=fetcher,
            now=lambda: self.now,
        )

    async def _initialize(
        self,
        *,
        version: str = "8.4.1",
        entry: dict | None = None,
        tools: list[dict] | None = None,
        catalog_complete: bool = True,
    ) -> tuple[UpstreamReadGateway, _GatewayTransport, dict]:
        transport = _GatewayTransport(
            tools if tools is not None else self.capture["tools"],
            version=version,
            catalog_complete=catalog_complete,
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=self._registry(entry),
        )
        snapshot = await gateway.initialize(FastMCP("beta56-8.4.1"))
        return gateway, transport, snapshot

    def test_exact_release_identity_catalog_and_error_evidence(self) -> None:
        self.assertEqual(
            self.release.source_tag_object,
            "030d1437462b2cdf24b274d1463510dea6c472e1",
        )
        self.assertEqual(
            self.release.source_commit,
            "701a7c26ac0e2309c7883a627d31873ab1510077",
        )
        self.assertEqual(
            self.release.image_revision,
            "10cd3d1207f8270ae6e35c0c40d7fc6dc411e9e3",
        )
        self.assertEqual(
            self.release.image_index_digest,
            "sha256:7823b36587a6e62efed271b26f3f72380b49f47364e5385580584e7ab2c60722",
        )
        self.assertEqual(self.release.allowed_protocol_versions, ("2025-03-26",))
        self.assertEqual(self.capture["tool_count"], 78)
        self.assertEqual(
            self.capture["catalog_fingerprint"],
            "4303ead3f32c46658530a422ae37eec0d34d3f2e494a2122a7011593a568bf59",
        )
        self.assertEqual(
            schema_fingerprint(self.capture["error_shapes"]),
            "03000635a7b0a506c12a6f99ce86433a09683693a0e61d4265b1f11ec52b2d46",
        )
        self.assertEqual(
            self.capture["error_shapes"]["invalid_search"][
                "shape_fingerprint"
            ],
            "fc0f1e8bf02be61d2056f1c6f11fb7b861a74ecd98978a5a38076617ac5bf939",
        )
        for probe in (
            "missing_state",
            "missing_automation",
            "missing_registry_entity",
        ):
            self.assertEqual(
                self.capture["error_shapes"][probe],
                _capture_for_release(self.previous)["error_shapes"][probe],
            )
        self.assertEqual(self.release.dashboard_attestation_status, "quarantined")
        self.assertEqual(
            dict(self.release.provider_dispositions),
            {
                "backup": "held",
                "dashboard": "held",
                "lifecycle": "held",
                "read_gateway": "admitted",
            },
        )

    def test_beta55_release_wide_error_gate_falsification(self) -> None:
        """Record the exact gate that rejected truthful 8.4.1 evidence."""

        legacy_release_wide_match = (
            self.release.error_contract_fingerprint
            == self.previous.error_contract_fingerprint
            and self.release.entity_lookup_missing_resource_status
            == self.previous.entity_lookup_missing_resource_status
        )
        normalized = {
            "authority_source": "signed_registry",
            "compiled_error_contract": (
                self.previous.error_contract_fingerprint
            ),
            "observed_error_contract": self.release.error_contract_fingerprint,
            "dynamically_exposed_count": (
                len(EXACT_8_4_1_READS) if legacy_release_wide_match else 0
            ),
            "registered_tools": (
                sorted(EXACT_8_4_1_READS)
                if legacy_release_wide_match
                else []
            ),
            "fallback_count": 0,
            "transport_calls": 0,
        }
        self.assertEqual(
            normalized,
            {
                "authority_source": "signed_registry",
                "compiled_error_contract": (
                    "b1134b2e121e7f1827970ef5c7bac7f9437272e1c0a030d458167f9c2b2d0a9b"
                ),
                "observed_error_contract": (
                    "03000635a7b0a506c12a6f99ce86433a09683693a0e61d4265b1f11ec52b2d46"
                ),
                "dynamically_exposed_count": 0,
                "registered_tools": [],
                "fallback_count": 0,
                "transport_calls": 0,
            },
        )

    def test_21_descriptors_are_exact_and_four_reads_are_independent(self) -> None:
        old = self.previous.tool_contracts_by_name
        new = self.release.tool_contracts_by_name
        for name in UNCHANGED_8_4_1_READS:
            self.assertEqual(new[name], old[name], name)
        for name in EXACT_8_4_1_READS - UNCHANGED_8_4_1_READS:
            self.assertNotEqual(new[name], old[name], name)
        automatic = {
            item.upstream_name
            for item in self.release.policy.tools
            if item.classification == "automatic_read"
        }
        self.assertEqual(automatic, EXACT_8_4_1_READS)
        self.assertEqual(
            {
                item.upstream_name
                for item in self.release.policy.tools
                if item.classification == "held_for_canary"
            },
            {"ha_get_operation_status"},
        )

    async def test_compiled_exact_release_registers_only_25_reads(self) -> None:
        gateway, transport, snapshot = await self._initialize()
        registered = set(gateway._registered_tool_registry.snapshot())
        self.assertEqual(registered, EXACT_8_4_1_READS)
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT, 51)
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT + len(registered), 76)
        for unreachable in (
            "ha_get_operation_status",
            "ha_get_app",
            "ha_manage_app",
            "ha_call_service",
            "ha_bulk_control",
            "ha_config_set_dashboard",
        ):
            self.assertNotIn(unreachable, registered)
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)

    async def test_truthful_8_4_1_evidence_restores_21_on_8_2_profile(self) -> None:
        entry = _signed_entry_for(self.release, version="8.4.2")
        entry["policy_resource"] = self.previous.policy_resource
        entry["policy_sha256"] = self.previous.policy_sha256
        gateway, transport, snapshot = await self._initialize(
            version="8.4.2",
            entry=entry,
        )
        registered = set(gateway._registered_tool_registry.snapshot())
        self.assertEqual(registered, UNCHANGED_8_4_1_READS)
        self.assertEqual(snapshot["dynamically_exposed_count"], 21)
        self.assertEqual(snapshot["readmission_authority_source"], "signed_registry")
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)

    async def test_catalog_uncertainty_and_unknown_addition_fail_closed(self) -> None:
        selected = next(
            item
            for item in self.capture["tools"]
            if item["name"] == "ha_get_state"
        )
        cases = (
            ("incomplete", self.capture["tools"], False, 0),
            (
                "duplicate_reviewed",
                self.capture["tools"] + [deepcopy(selected)],
                True,
                24,
            ),
            (
                "unknown_addition",
                self.capture["tools"]
                + [
                    {
                        "name": "ha_synthetic_future_write",
                        "description": "Synthetic unreviewed write.",
                        "inputSchema": {"type": "object"},
                    }
                ],
                True,
                25,
            ),
            (
                "reordered",
                list(reversed(self.capture["tools"])),
                True,
                0,
            ),
            (
                "malformed",
                self.capture["tools"] + [{"description": "missing name"}],
                True,
                0,
            ),
        )
        for name, tools, complete, expected_count in cases:
            with self.subTest(name=name, count=len(tools)):
                gateway, transport, snapshot = await self._initialize(
                    tools=tools,
                    catalog_complete=complete,
                )
                registered = gateway._registered_tool_registry.snapshot()
                self.assertNotIn("ha_synthetic_future_write", registered)
                self.assertEqual(len(registered), expected_count)
                self.assertEqual(
                    snapshot["dynamically_exposed_count"], expected_count
                )
                self.assertEqual(snapshot["fallback_count"], 0)
                self.assertEqual(transport.calls, 0)

    async def test_protocol_broadening_is_refused(self) -> None:
        transport = _GatewayTransport(
            self.capture["tools"],
            version="8.4.1",
            protocol_version="2025-11-25",
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=self._registry(None),
        )
        snapshot = await gateway.initialize(FastMCP("beta56-protocol-refusal"))
        self.assertEqual(snapshot["dynamically_exposed_count"], 0)
        self.assertEqual(gateway._registered_tool_registry.snapshot(), {})
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)


if __name__ == "__main__":
    unittest.main()
