from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.providers.ha_2026_8_device_compatibility import (  # noqa: E402
    ADAPTER_ID,
    CompositeDeviceCompatibilityError,
    REVIEWED_UPSTREAM_VERSIONS,
    adapt_ha_get_device_composite_result,
)


def empty_composite_payload():
    return {
        "success": True,
        "device": {
            "device_id": "legacy-composite-id",
            "config_entries": ["entry-a", "entry-b"],
            "entities": [],
        },
        "entities": [],
        "entity_count": 0,
        "queried_by": "device_id",
        "queried_entity_id": None,
    }


class HomeAssistant20268DeviceCompatibilityTests(
    unittest.IsolatedAsyncioTestCase
):
    def test_exact_reviewed_upstream_versions_include_8_2_0(self):
        self.assertEqual(
            REVIEWED_UPSTREAM_VERSIONS,
            frozenset({"8.1.0", "8.1.1", "8.2.0"}),
        )

    async def test_exact_adapter_restores_both_split_entity_memberships(self):
        rest = AsyncMock()
        rest.request.return_value = {"version": "2026.8.0"}
        websocket = AsyncMock()
        websocket.command.side_effect = [
            {
                "legacy-composite-id": {
                    "split_ids": ["split-b", "split-a"],
                    "primary_id": None,
                }
            },
            [
                {
                    "entity_id": "switch.fixture_b",
                    "device_id": "split-b",
                    "name": None,
                    "original_name": "Fixture B",
                    "platform": "beta23_device_fixture",
                },
                {
                    "entity_id": "light.unrelated",
                    "device_id": "unrelated-device",
                    "platform": "test",
                },
                {
                    "entity_id": "switch.fixture_a",
                    "device_id": "split-a",
                    "name": "Fixture A",
                    "original_name": "Ignored original",
                    "platform": "beta23_device_fixture",
                },
            ],
        ]

        adapted, adapter = await adapt_ha_get_device_composite_result(
            empty_composite_payload(),
            arguments={"device_id": "legacy-composite-id"},
            upstream_version="8.1.1",
            rest_client=rest,
            websocket_client=websocket,
        )

        self.assertEqual(adapter, ADAPTER_ID)
        self.assertEqual(adapted["entity_count"], 2)
        self.assertEqual(
            [item["entity_id"] for item in adapted["entities"]],
            ["switch.fixture_a", "switch.fixture_b"],
        )
        self.assertEqual(adapted["device"]["entities"], adapted["entities"])
        self.assertEqual(
            [call.args[0]["type"] for call in websocket.command.await_args_list],
            [
                "config/device_registry/list_composite_splits",
                "config/entity_registry/list",
            ],
        )

    async def test_other_home_assistant_version_does_not_apply(self):
        rest = AsyncMock()
        rest.request.return_value = {"version": "2026.7.2"}
        websocket = AsyncMock()
        payload = empty_composite_payload()

        adapted, adapter = await adapt_ha_get_device_composite_result(
            payload,
            arguments={"device_id": "legacy-composite-id"},
            upstream_version="8.1.1",
            rest_client=rest,
            websocket_client=websocket,
        )

        self.assertIs(adapted, payload)
        self.assertIsNone(adapter)
        websocket.command.assert_not_awaited()

    async def test_unreviewed_upstream_version_does_not_inherit(self):
        rest = AsyncMock()
        websocket = AsyncMock()
        payload = empty_composite_payload()

        adapted, adapter = await adapt_ha_get_device_composite_result(
            payload,
            arguments={"device_id": "legacy-composite-id"},
            upstream_version="8.1.2",
            rest_client=rest,
            websocket_client=websocket,
        )

        self.assertIs(adapted, payload)
        self.assertIsNone(adapter)
        rest.request.assert_not_awaited()
        websocket.command.assert_not_awaited()

    async def test_complete_upstream_result_is_never_rewritten(self):
        rest = AsyncMock()
        websocket = AsyncMock()
        payload = empty_composite_payload()
        payload["entities"] = [{"entity_id": "switch.fixture_a"}]
        payload["device"]["entities"] = payload["entities"]
        payload["entity_count"] = 1

        adapted, adapter = await adapt_ha_get_device_composite_result(
            payload,
            arguments={"device_id": "legacy-composite-id"},
            upstream_version="8.1.1",
            rest_client=rest,
            websocket_client=websocket,
        )

        self.assertIs(adapted, payload)
        self.assertIsNone(adapter)
        rest.request.assert_not_awaited()
        websocket.command.assert_not_awaited()

    async def test_exact_malformed_split_evidence_fails_closed(self):
        rest = AsyncMock()
        rest.request.return_value = {"version": "2026.8.0"}
        websocket = AsyncMock()
        websocket.command.return_value = {
            "legacy-composite-id": {"split_ids": ["duplicate", "duplicate"]}
        }

        with self.assertRaises(CompositeDeviceCompatibilityError):
            await adapt_ha_get_device_composite_result(
                empty_composite_payload(),
                arguments={"device_id": "legacy-composite-id"},
                upstream_version="8.1.1",
                rest_client=rest,
                websocket_client=websocket,
            )
