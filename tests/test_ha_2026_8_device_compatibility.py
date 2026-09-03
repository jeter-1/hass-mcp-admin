from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.providers.ha_2026_8_device_compatibility import (  # noqa: E402
    ADAPTER_ID,
    ADAPTER_IDS_BY_HA_VERSION,
    CompositeDeviceCompatibilityError,
    HA_2026_8_1_ADAPTER_ID,
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
    def _clients(self, version, *, split_contract=None, entity_rows=None):
        rest = AsyncMock()
        rest.request.return_value = {"version": version}
        websocket = AsyncMock()
        websocket.command.side_effect = [
            {
                "legacy-composite-id": split_contract
                if split_contract is not None
                else {
                    "split_ids": ["split-b", "split-a"],
                    "primary_id": "split-a",
                }
            },
            entity_rows
            if entity_rows is not None
            else [
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
        return rest, websocket

    def test_exact_reviewed_upstream_versions_include_8_4_1(self):
        self.assertEqual(
            REVIEWED_UPSTREAM_VERSIONS,
            frozenset({"8.1.0", "8.1.1", "8.2.0", "8.4.1"}),
        )

    def test_adapter_ids_are_owned_by_exact_home_assistant_releases(self):
        self.assertEqual(
            dict(ADAPTER_IDS_BY_HA_VERSION),
            {
                "2026.8.0": ADAPTER_ID,
                "2026.8.1": HA_2026_8_1_ADAPTER_ID,
            },
        )
        self.assertNotEqual(ADAPTER_ID, HA_2026_8_1_ADAPTER_ID)

    async def test_each_exact_adapter_restores_split_entity_memberships(self):
        for version, expected_adapter in ADAPTER_IDS_BY_HA_VERSION.items():
            for upstream_version in ("8.2.0", "8.4.1"):
                with self.subTest(
                    version=version,
                    upstream_version=upstream_version,
                ):
                    await self._assert_adapter_restores_memberships(
                        version=version,
                        upstream_version=upstream_version,
                        expected_adapter=expected_adapter,
                    )

    async def _assert_adapter_restores_memberships(
        self,
        *,
        version,
        upstream_version,
        expected_adapter,
    ):
        rest, websocket = self._clients(version)

        adapted, adapter = await adapt_ha_get_device_composite_result(
            empty_composite_payload(),
            arguments={"device_id": "legacy-composite-id"},
            upstream_version=upstream_version,
            rest_client=rest,
            websocket_client=websocket,
        )

        self.assertEqual(adapter, expected_adapter)
        self.assertEqual(adapted["entity_count"], 2)
        self.assertEqual(
            [item["entity_id"] for item in adapted["entities"]],
            ["switch.fixture_a", "switch.fixture_b"],
        )
        self.assertEqual(
            adapted["device"]["device_id"], "legacy-composite-id"
        )
        self.assertEqual(
            adapted["device"]["entities"], adapted["entities"]
        )
        self.assertEqual(
            [
                call.args[0]["type"]
                for call in websocket.command.await_args_list
            ],
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

    async def test_future_patch_release_does_not_inherit_2026_8_1(self):
        rest = AsyncMock()
        rest.request.return_value = {"version": "2026.8.2"}
        websocket = AsyncMock()
        payload = empty_composite_payload()

        adapted, adapter = await adapt_ha_get_device_composite_result(
            payload,
            arguments={"device_id": "legacy-composite-id"},
            upstream_version="8.2.0",
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

    async def test_missing_composite_source_fields_fail_closed(self):
        payload = empty_composite_payload()
        del payload["device"]["config_entries"]

        with self.assertRaises(CompositeDeviceCompatibilityError):
            await adapt_ha_get_device_composite_result(
                payload,
                arguments={"device_id": "legacy-composite-id"},
                upstream_version="8.2.0",
                rest_client=AsyncMock(),
                websocket_client=AsyncMock(),
            )

    async def test_non_target_releases_do_not_validate_candidate_source(self):
        source_variants = (
            ("missing", None),
            ("malformed", "entry-a"),
            ("duplicate", ["entry-a", "entry-a"]),
            ("ambiguous", ["entry-a", ""]),
        )
        for version in ("2026.7.2", "2026.8.2"):
            for label, config_entries in source_variants:
                with self.subTest(version=version, source=label):
                    payload = empty_composite_payload()
                    if config_entries is None:
                        del payload["device"]["config_entries"]
                    else:
                        payload["device"]["config_entries"] = config_entries
                    rest = AsyncMock()
                    rest.request.return_value = {"version": version}
                    websocket = AsyncMock()

                    adapted, adapter = await adapt_ha_get_device_composite_result(
                        payload,
                        arguments={"device_id": "legacy-composite-id"},
                        upstream_version="8.2.0",
                        rest_client=rest,
                        websocket_client=websocket,
                    )

                    self.assertIs(adapted, payload)
                    self.assertIsNone(adapter)
                    rest.request.assert_awaited_once_with("GET", "/config")
                    websocket.command.assert_not_awaited()

    async def test_owned_releases_fail_closed_on_malformed_candidate_source(
        self,
    ):
        source_variants = (
            ("missing", None),
            ("malformed", "entry-a"),
            ("duplicate", ["entry-a", "entry-a"]),
            ("ambiguous", ["entry-a", ""]),
        )
        for version in ADAPTER_IDS_BY_HA_VERSION:
            for label, config_entries in source_variants:
                with self.subTest(version=version, source=label):
                    payload = empty_composite_payload()
                    if config_entries is None:
                        del payload["device"]["config_entries"]
                    else:
                        payload["device"]["config_entries"] = config_entries
                    rest = AsyncMock()
                    rest.request.return_value = {"version": version}
                    websocket = AsyncMock()

                    with self.assertRaises(CompositeDeviceCompatibilityError):
                        await adapt_ha_get_device_composite_result(
                            payload,
                            arguments={"device_id": "legacy-composite-id"},
                            upstream_version="8.2.0",
                            rest_client=rest,
                            websocket_client=websocket,
                        )

                    rest.request.assert_awaited_once_with("GET", "/config")
                    websocket.command.assert_not_awaited()

    async def test_reviewed_query_identity_is_explicit_null(self):
        for version, expected_adapter in ADAPTER_IDS_BY_HA_VERSION.items():
            with self.subTest(version=version):
                payload = empty_composite_payload()
                self.assertIn("queried_entity_id", payload)
                self.assertIsNone(payload["queried_entity_id"])
                rest, websocket = self._clients(version)

                _adapted, adapter = await adapt_ha_get_device_composite_result(
                    payload,
                    arguments={"device_id": "legacy-composite-id"},
                    upstream_version="8.2.0",
                    rest_client=rest,
                    websocket_client=websocket,
                )

                self.assertEqual(adapter, expected_adapter)

    async def test_owned_releases_reject_nonreviewed_query_identity(self):
        missing = object()
        query_identity_variants = (
            ("missing", missing),
            ("non_null", "switch.fixture_a"),
            ("conflicting", "switch.conflicting"),
            ("empty_string", ""),
            ("wrong_type", 7),
            ("malformed", ["switch.conflicting"]),
        )
        for version in ADAPTER_IDS_BY_HA_VERSION:
            for label, queried_entity_id in query_identity_variants:
                with self.subTest(version=version, query_identity=label):
                    payload = empty_composite_payload()
                    if queried_entity_id is missing:
                        del payload["queried_entity_id"]
                    else:
                        payload["queried_entity_id"] = queried_entity_id
                    rest = AsyncMock()
                    rest.request.return_value = {"version": version}
                    websocket = AsyncMock()

                    with self.assertRaises(CompositeDeviceCompatibilityError):
                        await adapt_ha_get_device_composite_result(
                            payload,
                            arguments={"device_id": "legacy-composite-id"},
                            upstream_version="8.2.0",
                            rest_client=rest,
                            websocket_client=websocket,
                        )

                    rest.request.assert_awaited_once_with("GET", "/config")
                    websocket.command.assert_not_awaited()

    async def test_non_target_releases_do_not_validate_query_identity(self):
        for version in ("2026.7.2", "2026.8.2"):
            for queried_entity_id in (None, "switch.conflicting", 7):
                with self.subTest(
                    version=version,
                    queried_entity_id=queried_entity_id,
                ):
                    payload = empty_composite_payload()
                    if queried_entity_id is None:
                        del payload["queried_entity_id"]
                    else:
                        payload["queried_entity_id"] = queried_entity_id
                    rest = AsyncMock()
                    rest.request.return_value = {"version": version}
                    websocket = AsyncMock()

                    adapted, adapter = await adapt_ha_get_device_composite_result(
                        payload,
                        arguments={"device_id": "legacy-composite-id"},
                        upstream_version="8.2.0",
                        rest_client=rest,
                        websocket_client=websocket,
                    )

                    self.assertIs(adapted, payload)
                    self.assertIsNone(adapter)
                    rest.request.assert_awaited_once_with("GET", "/config")
                    websocket.command.assert_not_awaited()

    async def test_split_count_and_primary_identity_are_exact(self):
        for split_contract in (
            {"split_ids": ["split-a"], "primary_id": "split-a"},
            {
                "split_ids": ["split-a", "split-b", "split-c"],
                "primary_id": "split-a",
            },
            {
                "split_ids": ["split-a", "split-b"],
                "primary_id": "not-a-split",
            },
        ):
            with self.subTest(split_contract=split_contract):
                rest, websocket = self._clients(
                    "2026.8.1", split_contract=split_contract
                )
                with self.assertRaises(CompositeDeviceCompatibilityError):
                    await adapt_ha_get_device_composite_result(
                        empty_composite_payload(),
                        arguments={"device_id": "legacy-composite-id"},
                        upstream_version="8.2.0",
                        rest_client=rest,
                        websocket_client=websocket,
                    )

    async def test_duplicate_split_entity_identity_fails_closed(self):
        rows = [
            {
                "entity_id": "switch.duplicate",
                "device_id": "split-a",
                "platform": "beta23_device_fixture",
            },
            {
                "entity_id": "switch.duplicate",
                "device_id": "split-b",
                "platform": "beta23_device_fixture",
            },
        ]
        rest, websocket = self._clients("2026.8.1", entity_rows=rows)

        with self.assertRaises(CompositeDeviceCompatibilityError):
            await adapt_ha_get_device_composite_result(
                empty_composite_payload(),
                arguments={"device_id": "legacy-composite-id"},
                upstream_version="8.2.0",
                rest_client=rest,
                websocket_client=websocket,
            )

    async def test_all_split_bound_entities_are_preserved(self):
        rows = [
            {
                "entity_id": "sensor.fixture_extra",
                "device_id": "split-a",
                "platform": "beta23_device_fixture",
            },
            {
                "entity_id": "switch.fixture_a",
                "device_id": "split-a",
                "platform": "beta23_device_fixture",
            },
            {
                "entity_id": "switch.fixture_b",
                "device_id": "split-b",
                "platform": "beta23_device_fixture",
            },
            {
                "entity_id": "light.unrelated",
                "device_id": "unrelated-device",
                "platform": "test",
            },
        ]
        rest, websocket = self._clients("2026.8.1", entity_rows=rows)

        adapted, adapter = await adapt_ha_get_device_composite_result(
            empty_composite_payload(),
            arguments={"device_id": "legacy-composite-id"},
            upstream_version="8.2.0",
            rest_client=rest,
            websocket_client=websocket,
        )

        self.assertEqual(adapter, HA_2026_8_1_ADAPTER_ID)
        self.assertEqual(
            [item["entity_id"] for item in adapted["entities"]],
            [
                "sensor.fixture_extra",
                "switch.fixture_a",
                "switch.fixture_b",
            ],
        )
        self.assertEqual(adapted["entity_count"], 3)

    async def test_empty_split_entity_join_fails_closed(self):
        rest, websocket = self._clients("2026.8.1", entity_rows=[])

        with self.assertRaises(CompositeDeviceCompatibilityError):
            await adapt_ha_get_device_composite_result(
                empty_composite_payload(),
                arguments={"device_id": "legacy-composite-id"},
                upstream_version="8.2.0",
                rest_client=rest,
                websocket_client=websocket,
            )

    async def test_malformed_runtime_version_fails_closed(self):
        rest = AsyncMock()
        rest.request.return_value = {"version": ["2026.8.1"]}

        with self.assertRaises(CompositeDeviceCompatibilityError):
            await adapt_ha_get_device_composite_result(
                empty_composite_payload(),
                arguments={"device_id": "legacy-composite-id"},
                upstream_version="8.2.0",
                rest_client=rest,
                websocket_client=AsyncMock(),
            )
