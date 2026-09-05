from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
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

from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    McpDashboardHandshake,
    McpDashboardRead,
)
from ha_mcp_engineering.f3_dashboard.provider import EXACT_CONTRACTS  # noqa: E402
from ha_mcp_engineering.ha_mcp_readmission.registry import (  # noqa: E402
    REGISTRY_URL,
    SignedReleaseRegistry,
    TRUST_ANCHOR_KEY_ID,
)
from ha_mcp_engineering.providers.upstream_contracts import (  # noqa: E402
    CONTRACT_FAMILY,
    decide_admission,
    load_attestations,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
)
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.tools import ENGINEERING_STATIC_TOOL_COUNT  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS,
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


EXACT_READS = frozenset(
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


class _DashboardTransport:
    def __init__(self, tools: list[dict]) -> None:
        self.handshake = McpDashboardHandshake(
            protocol_version="2025-03-26",
            server_name="ha-mcp",
            server_version="8.4.3",
            tools=tuple(deepcopy(tools)),
            connection_latency_ms=1.0,
        )
        self.calls = 0

    async def execute_dashboard_read(self, arguments, capability_validator):
        capability_validator(self.handshake)
        self.calls += 1
        return McpDashboardRead(
            handshake=self.handshake,
            call_result={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": True,
                                "action": "list",
                                "dashboards": [],
                                "count": 0,
                            }
                        ),
                    }
                ],
                "isError": False,
            },
            tool_call_latency_ms=1.0,
        )


class Beta58HaMcp843ContinuityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.compiled = load_reviewed_upstream_release_registry()
        self.release = self.compiled.by_version["8.4.3"]
        self.previous = self.compiled.by_version["8.4.1"]
        self.capture = _capture_for_release(self.release)
        review = json.loads(
            (ROOT / self.release.artifact_evidence_resource).read_text(
                encoding="utf-8"
            )
        )
        by_name = {item["name"]: item for item in self.capture["tools"]}
        self.capture["tools"] = [
            by_name[name]
            for name in review["runtime_catalog"]["runtime_tool_order"]
        ]
        self.signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        self.now = datetime(2026, 9, 4, 20, tzinfo=timezone.utc)
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
        version: str,
        tools: list[dict],
        entry: dict | None,
    ):
        transport = _GatewayTransport(tools, version=version)
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=self._registry(entry),
        )
        snapshot = await gateway.initialize(FastMCP("beta58-8.4.3"))
        return gateway, transport, snapshot

    def test_exact_release_authority_and_beta57_falsification(self) -> None:
        self.assertEqual(
            self.release.source_commit,
            "eac7a3aa7063432e9af17e7d7726040e909c7b8f",
        )
        self.assertEqual(
            self.release.source_tag_object,
            "a4c06d0756f9feca01eda9406f9714bd75cd06a9",
        )
        self.assertEqual(
            self.release.image_index_digest,
            "sha256:d5cea47a0115e5d161c2b319ee637b1b0a5bcfafe1597cb490299bbbc6329456",
        )
        self.assertEqual(self.capture["tool_count"], 78)
        self.assertEqual(
            self.capture["catalog_fingerprint"],
            "4b75e198df50a633ab94d51f006961c5fc31a1edfcf524b4dc925a48799e98f7",
        )
        self.assertEqual(
            schema_fingerprint(
                [item["name"] for item in self.capture["tools"]]
            ),
            EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS["8.4.3"],
        )
        self.assertEqual(
            schema_fingerprint(self.capture["error_shapes"]),
            self.previous.error_contract_fingerprint,
        )
        old = self.previous.tool_contracts_by_name
        new = self.release.tool_contracts_by_name
        self.assertEqual(
            {name for name in EXACT_READS if new[name] == old[name]},
            EXACT_READS - {"ha_search"},
        )
        self.assertNotEqual(new["ha_search"], old["ha_search"])
        self.assertEqual(len(EXACT_READS - {"ha_search"}), 24)
        self.assertNotIn("8.4.3", {"8.0.0", "8.1.0", "8.1.1", "8.2.0", "8.4.1"})

    async def test_exact_8_4_3_restores_25_reads_without_dispatch(self) -> None:
        gateway, transport, snapshot = await self._initialize(
            version="8.4.3", tools=self.capture["tools"], entry=None
        )
        registered = set(gateway._registered_tool_registry.snapshot())
        self.assertEqual(registered, EXACT_READS)
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT, 51)
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT + len(registered), 76)
        self.assertNotIn("ha_get_operation_status", registered)
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)

    async def test_signed_future_release_reuses_only_exact_binary_contracts(self) -> None:
        entry = _signed_entry_for(self.release, version="8.4.4")
        gateway, transport, snapshot = await self._initialize(
            version="8.4.4", tools=self.capture["tools"], entry=entry
        )
        self.assertEqual(
            set(gateway._registered_tool_registry.snapshot()), EXACT_READS
        )
        self.assertEqual(snapshot["readmission_authority_source"], "signed_registry")
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)

    async def test_future_blueprint_transition_is_capability_scoped(self) -> None:
        tools = [
            deepcopy(item)
            for item in self.capture["tools"]
            if item["name"] != "ha_get_blueprint"
        ]
        tools.append(
            {
                "name": "ha_manage_blueprints",
                "description": "Synthetic future mixed read/write blueprint tool.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"operation": {"type": "string"}},
                },
            }
        )
        entry = _signed_entry_for(self.release, version="8.4.4")
        gateway, transport, snapshot = await self._initialize(
            version="8.4.4", tools=tools, entry=entry
        )
        registered = set(gateway._registered_tool_registry.snapshot())
        self.assertEqual(registered, EXACT_READS - {"ha_get_blueprint"})
        self.assertNotIn("ha_manage_blueprints", registered)
        self.assertEqual(snapshot["dynamically_exposed_count"], 24)
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)

    async def test_exact_dashboard_read_and_setter_authority_are_separate(self) -> None:
        transport = _DashboardTransport(self.capture["tools"])
        provider = UpstreamDashboardProvider()
        provider.configure(
            _settings(self.signer.public_key_base64), transport=transport
        )
        result = await provider.list_dashboards(limit=5, response_limit=60_000)
        self.assertEqual(result.completeness, "complete")
        self.assertEqual(transport.calls, 1)
        self.assertEqual(
            result.provider_authority["upstream_version"], "8.4.3"
        )
        self.assertEqual(
            EXACT_CONTRACTS["8.4.3"].compatibility_entry,
            "ha-mcp-v8.4.3-d5cea47a",
        )

    def test_signed_dashboard_data_selects_only_one_compiled_family(self) -> None:
        current = next(
            item for item in load_attestations() if item.upstream_version == "8.4.3"
        )
        future = replace(
            current,
            entry_id="ha-mcp-v8.4.4-synthetic",
            upstream_version="8.4.4",
            source_tag="v8.4.4",
        )
        tool = next(
            item
            for item in self.capture["tools"]
            if item["name"] == "ha_config_get_dashboard"
        )
        decision = decide_admission(
            server_name="ha-mcp",
            server_version="8.4.4",
            protocol_version="2025-03-26",
            tool=tool,
            attestations=((future, "remote_fresh"),),
        )
        self.assertTrue(decision.accepted)
        conflict = replace(
            future,
            entry_id="ha-mcp-v8.4.4-conflict",
            contract_family=CONTRACT_FAMILY,
        )
        refused = decide_admission(
            server_name="ha-mcp",
            server_version="8.4.4",
            protocol_version="2025-03-26",
            tool=tool,
            attestations=(
                (future, "remote_fresh"),
                (conflict, "remote_fresh"),
            ),
        )
        self.assertFalse(refused.accepted)
        self.assertEqual(refused.failure_category, "conflicting_attestation_entry")


if __name__ == "__main__":
    unittest.main()
