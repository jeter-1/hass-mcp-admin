"""HAMCP-514: a generic CoreCapabilityUnavailable names the unavailable provider.

``server_info`` and ``get_server_health`` failed with ``provider_unavailable``
and empty details, so the client could not tell which provider was missing.
Any Core unavailability that is neither a pending signed admission nor an open
restart window now names ``home_assistant_core`` and the Core capabilities the
call needed. It stays non-retryable, and nothing reaches the application.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    AVAILABILITY_EXPOSURE_TOOL,
)

# Imported as modules so that their test classes are not collected here.
import tests.test_ha_core_2026_9_integration as core9  # noqa: E402
import tests.test_hamcp069a_entity_metadata_readmission as readmission  # noqa: E402
from tests.test_hamcp498_core_restart_retryable import WINDOW_KEYS, call  # noqa: E402


GENERIC_KEYS = {"provider", "core_capabilities"}


class GenericCoreUnavailableTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def gateway_without(self, capability_id: str):
        partial = [
            item for item in core9._core_2026_9_evidence()
            if item["capability_id"] != capability_id
        ]
        runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            core9._snapshot("2026.9.0", evidence=partial)
        )
        self.assertEqual(runtime.health_projection()["recovery"]["recovery_state"], "stable")
        return runtime, *readmission.gateway_for(runtime)

    def assert_names_core(self, payload: dict, capabilities: list[str]) -> None:
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "CoreCapabilityUnavailable")
        self.assertEqual(payload["error_code"], "provider_unavailable")
        self.assertFalse(payload["retryable"])
        self.assertEqual(
            payload["details"],
            {"provider": "home_assistant_core", "core_capabilities": capabilities},
        )

    async def test_server_info_and_health_name_the_provider(self):
        runtime, gateway, app = await self.gateway_without("core.basic_websocket_read")
        payload = await call(gateway, "get_server_health", {"check_ha": True})
        self.assert_names_core(
            payload, ["core.basic_rest_read", "core.basic_websocket_read"]
        )
        self.assertEqual(app.calls, [])

        runtime, gateway, app = await self.gateway_without("core.basic_rest_read")
        payload = await call(gateway, "server_info", {})
        self.assert_names_core(payload, ["core.basic_rest_read"])
        self.assertEqual(app.calls, [])
        self.assertEqual(runtime.health_snapshot()["fallback_count"], 0)

    async def test_a_call_without_core_requirements_is_not_refused(self):
        _runtime, gateway, app = await self.gateway_without("core.basic_rest_read")
        health = await call(gateway, "get_server_health", {"check_ha": False})
        self.assertTrue(health["success"])
        self.assertEqual(len(app.calls), 1)

    async def test_the_window_keeps_its_own_details(self):
        runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            core9._core_2026_9_snapshot()
        )
        gateway, app = readmission.gateway_for(runtime)
        runtime.request_reconciliation(connection_changed=True)
        payload = await call(gateway, "server_info", {})
        self.assertTrue(payload["retryable"])
        self.assertEqual(set(payload["details"]), WINDOW_KEYS)
        self.assertTrue(GENERIC_KEYS.isdisjoint(payload["details"]))
        self.assertEqual(app.calls, [])

    async def test_a_pending_signed_admission_keeps_its_own_details(self):
        runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            core9._core_2026_9_snapshot()
        )
        gateway, app = readmission.gateway_for(runtime)
        payload = await call(
            gateway, AVAILABILITY_EXPOSURE_TOOL,
            {"operation": "disable", "entity_ids": ["light.porch"]},
        )
        self.assertEqual(payload["details"]["reason_code"], "pending_signed_registry_admission")
        self.assertTrue(GENERIC_KEYS.isdisjoint(payload["details"]))
        self.assertEqual(app.calls, [])


if __name__ == "__main__":
    unittest.main()
