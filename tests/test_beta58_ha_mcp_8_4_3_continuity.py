"""Signed future-release reuse continuity, formerly on ha-mcp 8.4.3 (HAMCP-437).

I2a-D removes the 8.4.3 row, so per ANSWER 20261122-0005-01 A3:

- signed future-release reuse, drift, expiry, the capability-scoped blueprint
  transition and the signed dashboard family run on the frozen 8.2.0 fixture
  (``support/frozen_signed_reuse``, the same fixture as Ruling 2), as the
  synthetic future release 8.2.1. 8.2.0 has no platform-read adapters, so the
  8.4.3 withholding of the three version-bound platform wrappers
  (``platform_read_binding_absent``) has no frozen equivalent and is not
  asserted here;
- the public health projection and the exact dashboard read authority guard
  shipped behaviour and run on the shipped 9.2.0 row;
- retired: the 8.4.3 exact authority and exact-count tests, and the four
  legacy signed-entry tests, whose ``_LEGACY_SIGNED_POLICY_BINDINGS`` table was
  removed (``SignedReuseDormancyTests`` in test_ha_mcp_production_readmission
  covers that a signed entry without positive authority is refused).

The module name is kept because test_module_isolation runs it as a gateway
module.
"""

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
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    INTERNAL_ONLY_DELEGATED_READS,
    RETIRED_DELEGATED_READS,
)
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
from ha_mcp_engineering.capabilities import (  # noqa: E402
    replace_dynamic_upstream_capabilities,
)
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)
from signed_registry_fixtures import RegistrySigner  # noqa: E402
from support import frozen_signed_reuse  # noqa: E402
from support.ha_mcp_captures import capture as shipped_capture  # noqa: E402
from test_ha_mcp_production_readmission import (  # noqa: E402
    _GatewayTransport,
    _settings,
    _signed_entry_for,
)


# The frozen 8.2.0 policy's read routes: the shipped 9.2.0 reads without the
# three platform reads 8.2.0 predates, plus the retired ha_get_hacs_info,
# which 8.2.0 still classifies as a read.
FROZEN_READS = frozenset(
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
# HAMCP-290: the delegated ha_get_hacs_info is retired on every reviewed
# release (Josh decision 2026-11-08); it stays a reviewed policy read but is
# never exposed, so gateway exposure compares against EXPOSED_READS.
# HAMCP-501 W6 (G1, as relayed by the architect): with native ha_search
# registered, the delegated ha_search is internal-only on every version: it
# stays admitted and routed (gateway._internal), never published.
EXPOSED_READS = FROZEN_READS - RETIRED_DELEGATED_READS - INTERNAL_ONLY_DELEGATED_READS
# The synthetic future release that signed reuse admits on the frozen row.
FUTURE = "8.2.1"
SHIPPED = "9.2.0"


class _DashboardTransport:
    def __init__(self, tools: list[dict], *, version: str = SHIPPED) -> None:
        self.handshake = McpDashboardHandshake(
            protocol_version="2025-03-26",
            server_name="ha-mcp",
            server_version=version,
            tools=tuple(deepcopy(tools)),
            connection_latency_ms=1.0,
        )
        self.calls = 0

    async def execute_dashboard_read(self, arguments, capability_validator):
        capability_validator(self.handshake)
        self.calls += 1
        # One row: 9.2.0 reports a failed registry read as an empty list, so
        # an empty list is partial by design.
        row = {
            "id": "operations",
            "url_path": "operations",
            "mode": "storage",
            "title": "Operations",
            "require_admin": False,
            "show_in_sidebar": True,
        }
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
                                "dashboards": [row],
                                "count": 1,
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
        self.compiled = frozen_signed_reuse.registry()
        self.release = self.compiled.by_version[frozen_signed_reuse.VERSION]
        self.capture = frozen_signed_reuse.capture(self.release)
        self.signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        self.now = datetime(2026, 9, 4, 20, tzinfo=timezone.utc)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # Each initialized gateway publishes its catalog process-wide.
        self.addCleanup(replace_dynamic_upstream_capabilities, (), {})

    def _registry(
        self, entry: dict | None, *, revocations: list[dict] | None = None
    ) -> SignedReleaseRegistry:
        raw = self.signer.journal_raw(
            sequence=1,
            generated_at=self.now - timedelta(minutes=1),
            expires_at=self.now + timedelta(days=1),
            entries=[] if entry is None else [entry],
            revocations=revocations or [],
        )

        async def fetcher(url: str, maximum: int) -> bytes:
            self.assertEqual(url, REGISTRY_URL)
            self.assertLessEqual(len(raw), maximum)
            return raw

        return SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            # A distinct cache per registry: a cached journal with the same
            # sequence must not mask the entry or revocations under test.
            cache_path=Path(
                tempfile.mkdtemp(dir=self.temporary.name)
            ) / "registry-cache.json",
            fetcher=fetcher,
            now=lambda: self.now,
        )

    async def _initialize(
        self,
        *,
        version: str,
        tools: list[dict],
        entry: dict | None,
        revocations: list[dict] | None = None,
    ):
        transport = _GatewayTransport(tools, version=version)
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=self._registry(
                entry, revocations=revocations
            ),
        )
        snapshot = await gateway.initialize(FastMCP("beta58-signed-reuse"))
        return gateway, transport, snapshot

    def test_frozen_row_read_routes_are_pinned(self) -> None:
        reads = {e.exposed_name for e in self.release.policy.tools if e.is_read_route}
        self.assertEqual(reads, FROZEN_READS)
        self.assertEqual(
            self.release.policy.classification_counts["automatic_read"],
            len(FROZEN_READS),
        )

    async def test_public_gateway_health_bounds_catalog_rows(self) -> None:
        compiled = load_reviewed_upstream_release_registry()
        release = compiled.by_version[SHIPPED]
        transport = _GatewayTransport(
            shipped_capture(SHIPPED)["tools"], version=SHIPPED
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            replace(
                _settings(self.signer.public_key_base64),
                ha_mcp_release_registry_enabled=False,
            ),
            transport=transport,
            release_registry=compiled,
            signed_release_registry=None,
        )
        await gateway.initialize(FastMCP("beta58-public-health"))

        internal = gateway.health_snapshot()
        public = gateway.health_projection()

        self.assertGreater(len(internal["blocked_tools"]), 0)
        for field in (
            "held_tools",
            "core_withheld_tools",
            "live_canary_required_tools",
            "quarantined_tools",
            "missing_tools",
            "unreviewed_tools",
            "exposed_tools",
            "collision_mappings",
            "blocked_tools",
        ):
            self.assertEqual(public[field], [])
        self.assertTrue(public["catalog_name_projection_bounded"])
        self.assertRegex(
            public["catalog_name_projection_fingerprint"], r"^[0-9a-f]{64}$"
        )
        self.assertLess(len(json.dumps(public, indent=2)), 9_000)
        reads = {
            e.exposed_name for e in release.policy.tools if e.is_read_route
        } - RETIRED_DELEGATED_READS - INTERNAL_ONLY_DELEGATED_READS  # HAMCP-501 W6
        self.assertEqual(public["dynamically_exposed_count"], len(reads))
        self.assertEqual(public["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)

    async def test_signed_future_release_reuses_only_exact_binary_contracts(self) -> None:
        # Owner decision D8: signed-only reuse keeps exactly the frozen row's
        # delegated reads; no read is narrowed or withheld.
        entry = _signed_entry_for(self.release, version=FUTURE)
        gateway, transport, snapshot = await self._initialize(
            version=FUTURE, tools=self.capture["tools"], entry=entry
        )
        registered = set(gateway._registered_tool_registry.snapshot())
        self.assertEqual(registered, EXPOSED_READS)
        # HAMCP-501 W6: D8 still holds; ha_search is kept, internal-only.
        self.assertEqual(set(gateway._internal), set(INTERNAL_ONLY_DELEGATED_READS))
        self.assertEqual(snapshot["admission_status"], "admitted_exact")
        self.assertEqual(snapshot["readmission_authority_source"], "signed_registry")
        self.assertEqual(snapshot["dynamically_exposed_count"], len(EXPOSED_READS))
        self.assertEqual(snapshot["quarantined_automatic_read_count"], 0)
        # Classification accounting is the frozen row's view.
        self.assertEqual(
            snapshot["policy_classifications"],
            dict(self.release.policy.classification_counts),
        )
        self.assertTrue(snapshot["reviewed_tool_accounting_valid"])
        self.assertEqual(snapshot["readmission_withheld_read_count"], 0)
        self.assertEqual(snapshot["readmission_withheld_reason_counts"], {})
        self.assertEqual(snapshot["readmission_withheld_tools"], [])
        public = gateway.health_projection()["automatic_readmission"]
        self.assertEqual(public["readmission_withheld_read_count"], 0)
        self.assertEqual(public["readmission_withheld_reason_counts"], {})
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)

    async def test_signed_reuse_dispatches_drifts_and_expires_like_base(self) -> None:
        entry = _signed_entry_for(self.release, version=FUTURE)
        gateway, transport, _snapshot = await self._initialize(
            version=FUTURE, tools=self.capture["tools"], entry=entry
        )
        telemetry, token = begin_request("beta58-signed-reuse")
        try:
            result = json.loads(
                await gateway._registered_tool_registry.snapshot()[
                    "ha_list_services"
                ].run({})
            )
        finally:
            end_request(token)
        self.assertTrue(result["success"], result)
        self.assertEqual(
            telemetry.audit_context["upstream_version_evidence"],
            "signed_registry",
        )
        self.assertEqual(transport.calls, 1)

        tools = deepcopy(self.capture["tools"])
        drifted = next(item for item in tools if item["name"] == "ha_get_state")
        drifted["description"] += " synthetic drift"
        gateway, transport, snapshot = await self._initialize(
            version=FUTURE, tools=tools, entry=entry
        )
        self.assertEqual(
            set(gateway._registered_tool_registry.snapshot()),
            EXPOSED_READS - {"ha_get_state"},
        )
        self.assertEqual(snapshot["readmission_withheld_read_count"], 0)
        self.assertEqual(transport.calls, 0)

        registry = self._registry(entry)
        self.now += timedelta(days=2)
        transport = _GatewayTransport(self.capture["tools"], version=FUTURE)
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=registry,
        )
        snapshot = await gateway.initialize(FastMCP("beta58-signed-expired"))
        self.assertEqual(gateway._registered_tool_registry.snapshot(), {})
        self.assertEqual(snapshot["dynamically_exposed_count"], 0)
        self.assertEqual(snapshot["readmission_withheld_read_count"], 0)
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
        entry = _signed_entry_for(self.release, version=FUTURE)
        gateway, transport, snapshot = await self._initialize(
            version=FUTURE, tools=tools, entry=entry
        )
        registered = set(gateway._registered_tool_registry.snapshot())
        self.assertEqual(registered, EXPOSED_READS - {"ha_get_blueprint"})
        self.assertNotIn("ha_manage_blueprints", registered)
        self.assertEqual(
            snapshot["dynamically_exposed_count"], len(EXPOSED_READS) - 1
        )
        self.assertEqual(snapshot["readmission_withheld_read_count"], 0)
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)

    async def test_exact_dashboard_read_and_setter_authority_are_separate(self) -> None:
        transport = _DashboardTransport(shipped_capture(SHIPPED)["tools"])
        provider = UpstreamDashboardProvider()
        provider.configure(
            _settings(self.signer.public_key_base64), transport=transport
        )
        result = await provider.list_dashboards(limit=5, response_limit=60_000)
        self.assertEqual(result.completeness, "complete")
        self.assertEqual(transport.calls, 1)
        authority = result.provider_authority
        self.assertEqual(authority["upstream_version"], SHIPPED)
        self.assertEqual(
            authority["compatibility_entry"],
            EXACT_CONTRACTS[SHIPPED].compatibility_entry,
        )
        self.assertEqual(
            EXACT_CONTRACTS[SHIPPED].compatibility_entry,
            "ha-mcp-v9.2.0-9de5bf6c",
        )
        self.assertNotEqual(
            authority["getter_contract_hash"], authority["setter_contract_hash"]
        )

    def test_signed_dashboard_data_selects_only_one_compiled_family(self) -> None:
        current = next(
            item
            for item in load_attestations()
            if item.upstream_version == frozen_signed_reuse.VERSION
        )
        future = replace(
            current,
            entry_id=f"ha-mcp-v{FUTURE}-synthetic",
            upstream_version=FUTURE,
            source_tag=f"v{FUTURE}",
        )
        tool = next(
            item
            for item in self.capture["tools"]
            if item["name"] == "ha_config_get_dashboard"
        )
        decision = decide_admission(
            server_name="ha-mcp",
            server_version=FUTURE,
            protocol_version="2025-03-26",
            tool=tool,
            attestations=((future, "remote_fresh"),),
        )
        self.assertTrue(decision.accepted)
        self.assertNotEqual(current.contract_family, CONTRACT_FAMILY)
        conflict = replace(
            future,
            entry_id=f"ha-mcp-v{FUTURE}-conflict",
            contract_family=CONTRACT_FAMILY,
        )
        refused = decide_admission(
            server_name="ha-mcp",
            server_version=FUTURE,
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
