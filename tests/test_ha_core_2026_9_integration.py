"""Core 2026.9 source authority and production readmission integration tests."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from jsonschema import validate
from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.ha_core_readmission import (  # noqa: E402
    CORE_2026_9_1_AUTHORITY,
    CORE_2026_9_AUTHORITY,
    CORE_CAPABILITY_PROFILES,
    DELEGATED_CORE_REQUIREMENTS,
    DEVICE_DEPENDENT_DELEGATED_TOOLS,
    CoreAuthoritySource,
    CoreAuthorityStatus,
    CoreDisposition,
    CoreReadmissionCoordinator,
    CoreRuntime,
    assess_device_entity_semantics,
    assess_device_registry,
    assess_ha_mcp_device_projection,
    compiled_exact_authority,
    delegated_requirements,
    f3_requirements,
    static_tool_requirements,
    stable_observation,
)
from ha_mcp_engineering.ha_core_readmission.device_registry import (  # noqa: E402
    MAX_DEVICE_RECORDS,
)
from ha_mcp_engineering.ha_core_readmission.source import (  # noqa: E402
    AUTOMATION_CONTRACT_PROBE_ENTITY_ID,
    AiohttpCoreSnapshotSource,
    capability_evidence_for_probes,
)
from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    AdapterCapabilityDescriptor,
)
from ha_mcp_engineering.health import HealthRegistry  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    _CoreDispatchAuthorityGuard,
    _CoreVerificationAdapter,
)
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools import (  # noqa: E402
    ENGINEERING_STATIC_TOOL_COUNT,
    registered_tools,
)
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)
from tests.test_ha_core_capability_auto_readmission import (  # noqa: E402
    _evidence,
    _snapshot,
)
from tests.f3_synthetic_adapter import (  # noqa: E402
    prepared_dashboard_operation,
)
from tests.test_ha_mcp_production_readmission import (  # noqa: E402
    _capture_for_release,
)
from tests.test_readonly_upstream_gateway import (  # noqa: E402
    FakeTransport,
    settings,
)


AUTHORITY_FIXTURE = ROOT / "tests" / "fixtures" / "ha_core_2026_9_authority.json"
DEVICE_FIXTURE = ROOT / "tests" / "fixtures" / "ha_core_2026_9_device_registry.json"
LANE_FIXTURE = ROOT / "tests" / "fixtures" / "ha_core_2026_9_disposable_lane.json"


class _CoreBoundDashboardOperation:
    """Bind the generic F3 fixture to the production dashboard identity."""

    capability_id = "update_existing_dashboard"
    capability_identity = "update_existing_dashboard"

    def __init__(self) -> None:
        self._prepared = prepared_dashboard_operation()

    def __getattr__(self, name: str):
        return getattr(self._prepared, name)


def _fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _core_2026_9_evidence(version: str = "2026.9.0") -> list[dict]:
    registry = _fixture(DEVICE_FIXTURE)
    return capability_evidence_for_probes(
        version=version,
        rest_config={"version": version},
        states=[
            {
                "entity_id": "sensor.synthetic",
                "state": "ready",
                "attributes": {},
            }
        ],
        services=[{"domain": "light", "services": {}}],
        websocket_config={"version": version},
        websocket_results={
            "areas": [{"area_id": "garage"}],
            "floors": [{"floor_id": "ground"}],
            "labels": [{"label_id": "synthetic"}],
            "entities": registry["entities"],
            "devices": registry["devices"],
            "dashboards": [{"url_path": "synthetic-dashboard"}],
            "automation": {"id": "synthetic"},
            "dashboard": {"views": []},
        },
    )


def _core_2026_9_snapshot(
    *,
    version: str = "2026.9.0",
    session_id: str = "core-session-2026-9",
) -> dict:
    return _snapshot(
        version,
        session_id=session_id,
        evidence=_core_2026_9_evidence(version),
    )


def _effective_projection(fixture: dict) -> dict:
    devices = {item["id"]: item for item in fixture["devices"]}

    def device_area(device_id: str) -> str | None:
        device = devices[device_id]
        if device["area_id"] is not None:
            return device["area_id"]
        parent = device["parent_device_id"]
        return devices[parent]["area_id"] if parent is not None else None

    return {
        "devices": [
            {
                "device_id": device_id,
                "parent_device_id": device["parent_device_id"],
                "effective_area_id": device_area(device_id),
            }
            for device_id, device in sorted(devices.items())
        ],
        "entities": [
            {
                "entity_id": entity["entity_id"],
                "effective_area_id": (
                    entity["area_id"]
                    if entity["area_id"] is not None
                    else device_area(entity["device_id"])
                ),
            }
            for entity in sorted(
                fixture["entities"], key=lambda item: item["entity_id"]
            )
        ],
    }


def _contains_projection(value: object, expected: object) -> bool:
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(
            _contains_projection(item, expected)
            for pair in value.items()
            for item in pair
        )
    if isinstance(value, list):
        return any(_contains_projection(item, expected) for item in value)
    return False


class _MutableSource:
    def __init__(self, snapshot: dict):
        self.snapshot = snapshot
        self.calls = 0

    async def capture_core_snapshot(self):
        self.calls += 1
        return deepcopy(self.snapshot)


class _SyntheticWebSocket:
    def __init__(self, response: dict):
        self.response = response
        self.sent: list[dict] = []
        self.receive_calls = 0

    async def send_json(self, value: dict) -> None:
        self.sent.append(value)

    async def receive_json(self) -> dict:
        self.receive_calls += 1
        return deepcopy(self.response)


class _RecordingMcpApp:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, scope, receive, send) -> None:
        self.calls.append(dict(scope))
        await receive()
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "core-route-test",
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps({"success": True})}
                    ],
                    "isError": False,
                },
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {"type": "http.response.body", "body": body, "more_body": False}
        )


class Core20269AuthorityTests(unittest.TestCase):
    def test_immutable_authority_fixture_matches_compiled_profile(self):
        authority = _fixture(AUTHORITY_FIXTURE)
        self.assertEqual(
            authority["home_assistant_core"]["releases"]["2026.9.0"],
            CORE_2026_9_AUTHORITY,
        )
        self.assertEqual(
            authority["home_assistant_core"]["releases"]["2026.9.1"],
            CORE_2026_9_1_AUTHORITY,
        )
        self.assertEqual(
            authority["ha_mcp"]["source_commit"],
            "eac7a3aa7063432e9af17e7d7726040e909c7b8f",
        )
        self.assertTrue(
            authority["ha_mcp"]["child_device_change"]["included_in_tag"]
        )
        self.assertGreater(
            authority["ha_mcp"]["child_device_change"][
                "parent_device_id_source_match_count"
            ],
            0,
        )
        self.assertGreater(
            authority["ha_mcp"]["child_device_change"][
                "effective_area_source_match_count"
            ],
            0,
        )

    def test_core_2026_9_exact_profiles_are_fully_admitted(self):
        observation = stable_observation(
            _core_2026_9_snapshot(), _core_2026_9_snapshot()
        )
        result = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES).reconcile(
            observation, compiled_exact_authority("2026.9.0")
        )
        self.assertEqual(result.disposition, CoreDisposition.ADMITTED_EXACT)
        decisions = {
            item.capability_id: item for item in result.generation.decisions
        }
        self.assertEqual(len(decisions), len(CORE_CAPABILITY_PROFILES))
        self.assertTrue(
            all(
                decision.disposition == CoreDisposition.ADMITTED_EXACT
                for decision in decisions.values()
            )
        )

    def test_core_2026_9_1_patch_profiles_are_fully_admitted(self):
        observation = stable_observation(
            _core_2026_9_snapshot(version="2026.9.1"),
            _core_2026_9_snapshot(version="2026.9.1"),
        )
        result = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES).reconcile(
            observation, compiled_exact_authority("2026.9.1")
        )
        self.assertEqual(result.disposition, CoreDisposition.ADMITTED_EXACT)
        self.assertTrue(
            all(
                item.disposition == CoreDisposition.ADMITTED_EXACT
                for item in result.generation.decisions
            )
        )

    def test_pre_2026_9_profiles_remain_fully_admitted(self):
        for version in ("2026.7.2", "2026.8.0", "2026.8.1"):
            with self.subTest(version=version):
                snapshot = _snapshot(version, evidence=_evidence())
                result = CoreReadmissionCoordinator(
                    CORE_CAPABILITY_PROFILES
                ).reconcile(
                    stable_observation(snapshot, deepcopy(snapshot)),
                    compiled_exact_authority(version),
                )
                self.assertEqual(result.disposition, CoreDisposition.ADMITTED_EXACT)
                self.assertTrue(
                    all(item.disposition.admitted for item in result.generation.decisions)
                )

    def test_unknown_core_release_has_no_authority(self):
        self.assertEqual(compiled_exact_authority("2026.9.2"), ())

    def test_probe_projection_refuses_duplicate_or_incomplete_surfaces(self):
        registry = _fixture(DEVICE_FIXTURE)
        evidence = capability_evidence_for_probes(
            version="2026.9.0",
            rest_config={"version": "2026.9.0"},
            states=[
                {"entity_id": "sensor.duplicate", "state": "on", "attributes": {}},
                {"entity_id": "sensor.duplicate", "state": "off", "attributes": {}},
            ],
            services=[{"domain": "light", "services": {}}],
            websocket_config={"version": "2026.9.0"},
            websocket_results={
                "areas": [{"area_id": "garage"}],
                "floors": [{"floor_id": "ground"}],
                "labels": [{"label_id": "synthetic"}],
                "entities": registry["entities"],
                "devices": registry["devices"],
                "dashboards": None,
                "automation": {"id": "synthetic"},
                "dashboard": {"views": []},
            },
        )
        admitted = {item["capability_id"] for item in evidence}
        self.assertNotIn("core.basic_rest_read", admitted)
        self.assertNotIn("core.state_service_discovery", admitted)
        self.assertNotIn("core.direct_entity_state_read", admitted)
        self.assertNotIn("core.dashboard_configuration_read", admitted)


class Core20269SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_monitor_authenticates_without_ha_command(self):
        class LifecycleWebSocket:
            def __init__(self):
                self.responses = [
                    {"type": "auth_required"},
                    {"type": "auth_ok", "ha_version": "2026.9.1"},
                ]
                self.sent = []
                self.receive_calls = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

            async def receive_json(self):
                return self.responses.pop(0)

            async def send_json(self, value):
                self.sent.append(deepcopy(value))

            async def receive(self):
                self.receive_calls += 1
                return object()

        class LifecycleSession:
            def __init__(self, websocket):
                self.websocket = websocket
                self.ws_connect_calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

            def ws_connect(self, url, **kwargs):
                self.ws_connect_calls.append((url, kwargs))
                return self.websocket

        websocket = LifecycleWebSocket()
        session = LifecycleSession(websocket)
        configured = settings()
        source = AiohttpCoreSnapshotSource(configured)
        with patch(
            "ha_mcp_engineering.ha_core_readmission.source.aiohttp.ClientSession",
            return_value=session,
        ):
            await source.wait_for_connection_change("2026.9.1")

        self.assertEqual(
            websocket.sent,
            [{"type": "auth", "access_token": configured.ha_token}],
        )
        self.assertEqual(websocket.receive_calls, 1)
        self.assertEqual(len(session.ws_connect_calls), 1)
        self.assertEqual(
            session.ws_connect_calls[0][0], configured.websocket_url
        )

    async def test_exact_missing_resources_prove_empty_installation_read_contracts(
        self,
    ):
        source = AiohttpCoreSnapshotSource(object())
        automation_websocket = _SyntheticWebSocket(
            {
                "id": 9,
                "type": "result",
                "success": False,
                "error": {"code": "not_found", "message": "Entity not found"},
            }
        )
        dashboard_websocket = _SyntheticWebSocket(
            {
                "id": 8,
                "type": "result",
                "success": False,
                "error": {
                    "code": "config_not_found",
                    "message": "Unknown config specified: None",
                },
            }
        )

        automation = await source._command(
            automation_websocket,
            9,
            "automation/config",
            {"entity_id": AUTOMATION_CONTRACT_PROBE_ENTITY_ID},
            expected_errors=(("not_found", "Entity not found"),),
        )
        dashboard = await source._command(
            dashboard_websocket,
            8,
            "lovelace/config",
            expected_errors=(
                ("config_not_found", "Unknown config specified: None"),
                ("config_not_found", "No config found."),
            ),
        )
        evidence = capability_evidence_for_probes(
            version="2026.9.1",
            rest_config={"version": "2026.9.1"},
            states=[],
            services=[],
            websocket_config={"version": "2026.9.1"},
            websocket_results={
                "areas": [],
                "floors": [],
                "labels": [],
                "entities": [],
                "devices": [],
                "dashboards": [],
                "automation": automation,
                "dashboard": dashboard,
            },
        )
        admitted = {item["capability_id"] for item in evidence}

        self.assertIn("core.automation_configuration_read", admitted)
        self.assertIn("core.dashboard_configuration_read", admitted)
        self.assertEqual(
            automation_websocket.sent,
            [
                {
                    "id": 9,
                    "type": "automation/config",
                    "entity_id": AUTOMATION_CONTRACT_PROBE_ENTITY_ID,
                }
            ],
        )
        self.assertEqual(
            dashboard_websocket.sent,
            [{"id": 8, "type": "lovelace/config"}],
        )
        source_snapshot = _snapshot("2026.9.1", evidence=evidence)
        runtime_source = _MutableSource(source_snapshot)
        runtime = CoreRuntime()
        runtime.configure(object(), source=runtime_source)
        await runtime.reconcile_once("startup")
        for tool_name in (
            "list_automations",
            "get_automation_config",
            "list_dashboards",
            "get_dashboard_config",
            "create_dashboard_update_plan",
        ):
            with self.subTest(tool_name=tool_name):
                authority = runtime.acquire(static_tool_requirements(tool_name))
                self.assertIsNotNone(authority)
                self.assertTrue(runtime.release(authority))

    async def test_unexpected_missing_resource_envelopes_remain_withheld(self):
        source = AiohttpCoreSnapshotSource(object())
        malformed = (
            {
                "id": 9,
                "type": "result",
                "success": False,
                "error": {"code": "unknown", "message": "Entity not found"},
            },
            {
                "id": 9,
                "type": "result",
                "success": False,
                "error": {"code": "not_found", "message": "Changed message"},
            },
            {
                "id": 9,
                "type": "result",
                "success": False,
                "error": {
                    "code": "not_found",
                    "message": "Entity not found",
                    "extra": True,
                },
            },
            {
                "id": 9,
                "type": "result",
                "success": False,
                "error": {"code": "not_found", "message": "Entity not found"},
                "extra": True,
            },
        )
        for response in malformed:
            with self.subTest(response=response):
                with self.assertRaisesRegex(
                    RuntimeError, "core_probe_command_failed"
                ):
                    await source._command(
                        _SyntheticWebSocket(response),
                        9,
                        "automation/config",
                        {"entity_id": AUTOMATION_CONTRACT_PROBE_ENTITY_ID},
                        expected_errors=(("not_found", "Entity not found"),),
                    )

    async def test_unexpected_websocket_frame_fails_without_unbounded_skip(self):
        websocket = _SyntheticWebSocket(
            {"id": 99, "type": "result", "success": True, "result": {}}
        )
        source = AiohttpCoreSnapshotSource(object())

        with self.assertRaisesRegex(RuntimeError, "core_probe_command_failed"):
            await source._command(websocket, 1, "get_config")

        self.assertEqual(websocket.receive_calls, 1)
        self.assertEqual(
            websocket.sent,
            [{"id": 1, "type": "get_config"}],
        )

    def test_core_probe_source_contains_no_write_or_generic_forwarding(self):
        source = (
            BETA
            / "ha_mcp_engineering"
            / "ha_core_readmission"
            / "source.py"
        ).read_text(encoding="utf-8")
        self.assertIn("session.get(", source)
        self.assertNotIn("session.post(", source)
        self.assertNotIn("session.put(", source)
        self.assertNotIn("session.delete(", source)
        self.assertNotIn("call_service", source)
        self.assertNotIn("ha_config_set", source)

    def test_disposable_lane_is_immutable_bounded_and_nonproduction(self):
        lane = _fixture(LANE_FIXTURE)
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version["8.4.3"]
        self.assertEqual(
            lane["home_assistant_images"],
            {
                "2026.9.0": (
                    "ghcr.io/home-assistant/home-assistant:2026.9.0@"
                    + CORE_2026_9_AUTHORITY["image_index_digest"]
                ),
                "2026.9.1": (
                    "ghcr.io/home-assistant/home-assistant:2026.9.1@"
                    + CORE_2026_9_1_AUTHORITY["image_index_digest"]
                ),
            },
        )
        self.assertEqual(
            lane["ha_mcp_image"],
            "ghcr.io/homeassistant-ai/ha-mcp:8.4.3@"
            + release.image_index_digest,
        )
        self.assertFalse(lane["production_credentials_allowed"])
        self.assertFalse(lane["fallback_allowed"])
        self.assertFalse(lane["production_mutation_allowed"])
        self.assertTrue(lane["disposable_reversible_mutation_allowed"])
        self.assertEqual(
            lane["network"],
            "dedicated_internal_docker_bridge_with_loopback_only_test_ports",
        )
        self.assertEqual(
            lane["execution_requirement"],
            "exact_head_ci_before_release_readiness",
        )
        self.assertEqual(
            set(lane["required_scenarios"]),
            {
                "rest_websocket_identity_agreement",
                "two_snapshot_stability",
                "normal_device_registry",
                "child_device_registry",
                "parent_device_identity",
                "effective_area_inheritance",
                "entity_child_device_relationship",
                "multiple_children",
                "missing_parent_refusal",
                "parent_cycle_refusal",
                "state_json_semantics",
                "template_dictionary_access",
                "template_state_object_semantics",
                "template_area_device_location_helpers",
                "probatio_configuration_validation_success",
                "probatio_configuration_validation_warning",
                "probatio_configuration_validation_error",
                "probatio_configuration_validation_malformed",
                "dependency_standard_helper_negative_control",
                "dependency_consequential_helper_control",
                "typed_helper_forward_verified_readback",
                "typed_helper_duplicate_suppression",
                "typed_helper_exact_restoration",
                "f3_exact_readback",
                "dashboard_read_continuity",
                "dashboard_reversible_write_continuity",
                "catalog_all_reviewed_read_retention",
                "held_operation_status_unregistered",
                "timeout_refusal",
                "connection_loss_refusal",
                "malformed_evidence_refusal",
                "zero_fallback",
            },
        )

    def test_future_reviewed_device_authority_restores_only_delegated_reads(self):
        profile = next(
            item
            for item in CORE_CAPABILITY_PROFILES
            if item.capability_id == "core.delegated_device_effective_area"
        )
        snapshot = _snapshot("2026.9.2", evidence=_evidence())
        selection = next(
            item
            for item in compiled_exact_authority("2026.9.1")
            if item.capability_ids == (profile.capability_id,)
        )
        authority = (
            replace(
                selection,
                subject_version="2026.9.2",
                evidence_fingerprint="sha256:" + "a" * 64,
                source=CoreAuthoritySource.VERIFIED_COMPATIBILITY,
                status=CoreAuthorityStatus.POSITIVE,
                reason_code="verified_compatible_release",
            ),
        )
        coordinator = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES)
        result = coordinator.reconcile(
            stable_observation(snapshot, deepcopy(snapshot)), authority
        )
        delegated = result.generation.decision_for(profile.capability_id)
        direct = result.generation.decision_for("core.direct_device_registry_read")
        self.assertEqual(delegated.disposition, CoreDisposition.ADMITTED_COMPATIBLE)
        self.assertEqual(direct.disposition, CoreDisposition.UNAVAILABLE)

    def test_expired_revoked_and_malformed_authority_fail_closed(self):
        observation = stable_observation(
            _core_2026_9_snapshot(), _core_2026_9_snapshot()
        )
        base = list(compiled_exact_authority("2026.9.0"))
        target = next(
            index
            for index, item in enumerate(base)
            if item.capability_ids == ("core.basic_rest_read",)
        )
        for status in (CoreAuthorityStatus.EXPIRED, CoreAuthorityStatus.REVOKED):
            with self.subTest(status=status.value):
                authority = list(base)
                authority[target] = replace(
                    authority[target], status=status, reason_code="authority_withheld"
                )
                result = CoreReadmissionCoordinator(
                    CORE_CAPABILITY_PROFILES
                ).reconcile(observation, tuple(authority))
                self.assertFalse(
                    result.generation.decision_for(
                        "core.basic_rest_read"
                    ).disposition.admitted
                )
        malformed = list(base)
        malformed[target] = replace(malformed[target], adapter_id="uncompiled-adapter")
        result = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES).reconcile(
            observation, tuple(malformed)
        )
        self.assertTrue(
            all(
                item.reason_code == "authority_bundle_invalid"
                for item in result.generation.decisions
            )
        )


class Core20269DeviceSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = _fixture(DEVICE_FIXTURE)

    def test_complete_child_device_and_entity_semantics(self):
        devices = assess_device_registry(self.fixture["devices"])
        entities = assess_device_entity_semantics(
            self.fixture["devices"], self.fixture["entities"]
        )
        self.assertTrue(devices.complete)
        self.assertEqual(devices.child_count, 2)
        self.assertTrue(entities.complete)
        projection = assess_ha_mcp_device_projection(
            self.fixture["devices"],
            self.fixture["entities"],
            _effective_projection(self.fixture),
        )
        self.assertTrue(projection.complete)

    def test_ha_mcp_8_4_1_flat_projection_cannot_satisfy_contract(self):
        projection = _effective_projection(self.fixture)
        for item in projection["devices"]:
            source = next(
                candidate
                for candidate in self.fixture["devices"]
                if candidate["id"] == item["device_id"]
            )
            item["parent_device_id"] = None
            item["effective_area_id"] = source["area_id"]
        for item in projection["entities"]:
            source = next(
                candidate
                for candidate in self.fixture["entities"]
                if candidate["entity_id"] == item["entity_id"]
            )
            item["effective_area_id"] = source["area_id"]
        result = assess_ha_mcp_device_projection(
            self.fixture["devices"], self.fixture["entities"], projection
        )
        self.assertFalse(result.complete)
        self.assertEqual(
            result.reason_code, "delegated_effective_area_semantics_changed"
        )

    def test_missing_parent_cycle_duplicate_and_conflicting_identity_fail(self):
        missing = deepcopy(self.fixture["devices"])
        missing[1]["parent_device_id"] = "missing-parent"
        self.assertEqual(
            assess_device_registry(missing).reason_code, "child_parent_missing"
        )

        children = deepcopy(self.fixture["devices"][1:])
        children[0]["parent_device_id"] = children[1]["id"]
        children[1]["parent_device_id"] = children[0]["id"]
        self.assertEqual(
            assess_device_registry(children).reason_code, "device_parent_cycle"
        )

        duplicate = [
            deepcopy(self.fixture["devices"][0]),
            deepcopy(self.fixture["devices"][0]),
        ]
        self.assertEqual(
            assess_device_registry(duplicate).reason_code,
            "device_identity_duplicate",
        )

        conflicting = [
            deepcopy(self.fixture["devices"][0]),
            deepcopy(self.fixture["devices"][1]),
        ]
        conflicting[1]["id"] = conflicting[0]["id"]
        self.assertEqual(
            assess_device_registry(conflicting).reason_code,
            "device_identity_duplicate",
        )

    def test_malformed_incomplete_and_oversized_records_fail(self):
        incomplete = deepcopy(self.fixture["devices"])
        incomplete[1].pop("parent_device_id")
        self.assertEqual(
            assess_device_registry(incomplete).reason_code,
            "device_parent_discriminator_missing",
        )
        extra = deepcopy(self.fixture["devices"])
        extra[1]["unreviewed"] = True
        self.assertEqual(
            assess_device_registry(extra).reason_code,
            "child_device_record_incomplete",
        )
        self.assertEqual(
            assess_device_registry([{}] * (MAX_DEVICE_RECORDS + 1)).reason_code,
            "device_registry_oversized_or_invalid",
        )

    def test_entity_duplicates_missing_devices_and_projection_drift_fail(self):
        duplicate = [
            deepcopy(self.fixture["entities"][0]),
            deepcopy(self.fixture["entities"][0]),
        ]
        self.assertEqual(
            assess_device_entity_semantics(
                self.fixture["devices"], duplicate
            ).reason_code,
            "entity_identity_duplicate",
        )
        missing = deepcopy(self.fixture["entities"])
        missing[0]["device_id"] = "missing-device"
        self.assertEqual(
            assess_device_entity_semantics(
                self.fixture["devices"], missing
            ).reason_code,
            "entity_device_missing",
        )
        projection = _effective_projection(self.fixture)
        projection["entities"][0]["effective_area_id"] = "wrong-area"
        self.assertEqual(
            assess_ha_mcp_device_projection(
                self.fixture["devices"], self.fixture["entities"], projection
            ).reason_code,
            "delegated_entity_effective_area_semantics_changed",
        )


class Core20269RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def _runtime(self, snapshot: dict) -> tuple[CoreRuntime, _MutableSource]:
        source = _MutableSource(snapshot)
        runtime = CoreRuntime()
        runtime.configure(object(), source=source)
        await runtime.reconcile_once("startup")
        return runtime, source

    async def test_transition_retires_old_generation_but_keeps_siblings(self):
        old = _snapshot("2026.8.1", evidence=_evidence())
        runtime, source = await self._runtime(old)
        old_authority = runtime.acquire(("core.basic_rest_read",))
        self.assertIsNotNone(old_authority)
        old_generation = runtime.health_snapshot()["current_generation"]
        plan_created_at = datetime.now(timezone.utc).isoformat()

        source.snapshot = _core_2026_9_snapshot()
        await runtime.reconcile_once("identity_or_connection_change")
        health = runtime.health_snapshot()
        self.assertGreater(health["current_generation"], old_generation)
        self.assertEqual(health["compatible_count"], len(CORE_CAPABILITY_PROFILES))
        self.assertEqual(health["fallback_count"], 0)
        self.assertIsNone(runtime.consume(old_authority))
        self.assertIsNotNone(runtime.acquire(("core.basic_rest_read",)))
        self.assertIsNone(
            runtime.acquire(
                ("core.basic_rest_read",), plan_created_at=plan_created_at
            )
        )
        self.assertIsNotNone(
            runtime.acquire(("core.delegated_device_effective_area",))
        )
        self.assertIsNotNone(runtime.acquire(("core.template_semantics",)))

    async def test_connection_monitor_retires_authority_before_reprobe(self):
        class ConnectionLifecycleSource(_MutableSource):
            def __init__(self, snapshot):
                super().__init__(snapshot)
                self.monitor_started = asyncio.Event()
                self.connection_lost = asyncio.Event()
                self.reprobe_started = asyncio.Event()
                self.release_reprobe = asyncio.Event()
                self.mark_calls = 0

            async def wait_for_connection_change(self, expected_version):
                self.assert_expected_version = expected_version
                self.monitor_started.set()
                await self.connection_lost.wait()
                self.connection_lost.clear()

            def mark_connection_changed(self):
                self.mark_calls += 1

            async def capture_core_snapshot(self):
                self.calls += 1
                if self.calls == 3:
                    self.reprobe_started.set()
                    await self.release_reprobe.wait()
                return deepcopy(self.snapshot)

        source = ConnectionLifecycleSource(
            _snapshot("2026.8.1", evidence=_evidence())
        )
        runtime = CoreRuntime()
        runtime.configure(object(), source=source)
        await runtime.reconcile_once("startup")
        old_authority = runtime.acquire(("core.basic_rest_read",))
        self.assertIsNotNone(old_authority)
        old_generation = runtime.health_snapshot()["current_generation"]

        supervisor = asyncio.create_task(
            runtime.supervise(interval_seconds=3_600)
        )
        try:
            await asyncio.wait_for(source.monitor_started.wait(), timeout=1)
            self.assertEqual(source.assert_expected_version, "2026.8.1")
            source.snapshot = _core_2026_9_snapshot(
                version="2026.9.1",
                session_id="core-session-after-reconnect",
            )
            source.connection_lost.set()
            await asyncio.wait_for(source.reprobe_started.wait(), timeout=1)

            self.assertEqual(source.mark_calls, 1)
            self.assertIsNone(runtime.consume(old_authority))
            self.assertIsNone(runtime.acquire(("core.basic_rest_read",)))

            source.release_reprobe.set()
            for _ in range(20):
                await asyncio.sleep(0)
                observation = runtime.current_observation
                if observation is not None and observation.version == "2026.9.1":
                    break
            observation = runtime.current_observation
            self.assertIsNotNone(observation)
            self.assertEqual(observation.version, "2026.9.1")
            self.assertGreater(
                runtime.health_snapshot()["current_generation"],
                old_generation,
            )
            next_authority = runtime.acquire(("core.basic_rest_read",))
            self.assertIsNotNone(next_authority)
            self.assertTrue(runtime.release(next_authority))
        finally:
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)

    async def test_connection_change_cannot_publish_inflight_old_snapshot(self):
        class InflightSnapshotSource(_MutableSource):
            def __init__(self, snapshot):
                super().__init__(snapshot)
                self.capture_started = asyncio.Event()
                self.release_capture = asyncio.Event()

            async def capture_core_snapshot(self):
                self.calls += 1
                captured = deepcopy(self.snapshot)
                if self.calls == 3:
                    self.capture_started.set()
                    await self.release_capture.wait()
                return captured

        source = InflightSnapshotSource(
            _snapshot("2026.8.1", evidence=_evidence())
        )
        runtime = CoreRuntime()
        runtime.configure(object(), source=source)
        await runtime.reconcile_once("startup")
        old_authority = runtime.acquire(("core.basic_rest_read",))
        self.assertIsNotNone(old_authority)

        inflight = asyncio.create_task(runtime.reconcile_once("periodic"))
        await asyncio.wait_for(source.capture_started.wait(), timeout=1)
        runtime.request_reconciliation(connection_changed=True)
        source.snapshot = _core_2026_9_snapshot(
            version="2026.9.1",
            session_id="core-session-after-stale-capture",
        )
        source.release_capture.set()
        await asyncio.wait_for(inflight, timeout=1)

        self.assertIsNone(runtime.consume(old_authority))
        self.assertIsNone(runtime.acquire(("core.basic_rest_read",)))
        self.assertIn(
            {
                "event_type": "core_reconciliation",
                "reason_code": "core_connection_generation_stale",
                "generation": None,
            },
            runtime.health_snapshot()["recent_events"],
        )

        await runtime.reconcile_once("identity_or_connection_change")
        observation = runtime.current_observation
        self.assertIsNotNone(observation)
        self.assertEqual(observation.version, "2026.9.1")
        replacement = runtime.acquire(("core.basic_rest_read",))
        self.assertIsNotNone(replacement)
        self.assertTrue(runtime.release(replacement))

    async def test_repeated_reconciliation_is_idempotent_and_redacted(self):
        runtime, source = await self._runtime(_core_2026_9_snapshot())
        generation = runtime.health_snapshot()["current_generation"]
        await runtime.reconcile_once("periodic")
        health = runtime.health_snapshot()
        self.assertEqual(health["current_generation"], generation)
        self.assertEqual(source.calls, 4)
        encoded = json.dumps(health)
        self.assertNotIn("core-session-2026-9", encoded)
        self.assertNotIn("sensor.synthetic", encoded)
        self.assertNotIn("garage", encoded)

    async def test_public_health_uses_bounded_core_projection(self):
        runtime, _source = await self._runtime(_core_2026_9_snapshot())
        projection = runtime.health_projection()
        encoded = json.dumps(projection, sort_keys=True)

        self.assertLessEqual(len(encoded.encode("utf-8")), 4_096)
        self.assertEqual(projection["compatible_count"], len(CORE_CAPABILITY_PROFILES))
        self.assertEqual(projection["fallback_count"], 0)
        self.assertNotIn("authority_profiles", projection)
        self.assertNotIn("compatible_capabilities", projection)
        self.assertNotIn("recent_events", projection)
        self.assertNotIn("core-session-2026-9", encoded)
        self.assertNotIn("sensor.synthetic", encoded)
        self.assertNotIn("garage", encoded)

        class ProjectionOnlyCore:
            def health_projection(self) -> dict:
                return projection

            def health_snapshot(self) -> dict:
                raise AssertionError("public health must not expose the full snapshot")

        health = HealthRegistry(core_readmission=ProjectionOnlyCore()).snapshot(
            {"checked": False, "status": "not_checked"}
        )
        self.assertEqual(health["home_assistant_core_authority"], projection)

    async def test_material_reconciliation_audit_is_bounded_and_redacted(self):
        source = _MutableSource(_core_2026_9_snapshot())
        events: list[dict] = []
        runtime = CoreRuntime()
        runtime.configure(
            object(),
            source=source,
            audit_sink=lambda event: events.append(deepcopy(event)) or True,
        )

        await runtime.reconcile_once("startup")
        await runtime.reconcile_once("periodic")

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(
            event["event"], "home_assistant_core_authority_reconciled"
        )
        summary = event["analysis_summary"]
        self.assertEqual(summary["observed_core_version"], "2026.9.0")
        self.assertEqual(summary["admitted_count"], len(CORE_CAPABILITY_PROFILES))
        self.assertEqual(summary["withheld_count"], 0)
        self.assertEqual(summary["fallback_count"], 0)
        encoded = json.dumps(event, sort_keys=True)
        self.assertLessEqual(len(encoded.encode("utf-8")), 8_192)
        self.assertNotIn("core-session-2026-9", encoded)
        self.assertNotIn("sensor.synthetic", encoded)
        self.assertNotIn("garage", encoded)

    async def test_single_use_atomic_route_set_and_target_binding(self):
        runtime, _source = await self._runtime(
            _snapshot("2026.8.1", evidence=_evidence())
        )
        authority = runtime.acquire(
            (
                "core.f3_mutation_verification",
                "core.governed_configuration_operation",
            ),
            target={"target_type": "automation", "target_id": "synthetic"},
        )
        self.assertIsNotNone(authority)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = tuple(pool.map(runtime.consume, (authority, authority)))
        winners = [item for item in (first, second) if item is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(winners[0]), 2)
        self.assertTrue(runtime.finish(winners[0]))

        next_authority = runtime.acquire(
            ("core.governed_configuration_operation",),
            target={"target_type": "automation", "target_id": "one"},
        )
        self.assertIsNotNone(next_authority)
        forged = replace(
            next_authority,
            target_fingerprint=runtime.target_fingerprint(
                {"target_type": "automation", "target_id": "two"}
            ),
        )
        self.assertIsNone(runtime.consume(forged))
        self.assertTrue(runtime.release(next_authority))

    async def test_f3_requirements_use_exact_closed_capability_identities(self):
        class Prepared:
            def __init__(
                self,
                *,
                capability_id: str = "",
                capability_identity: str = "",
                operation: str,
            ) -> None:
                self.capability_id = capability_id
                self.capability_identity = capability_identity
                self.operation = operation

        verification = "core.f3_mutation_verification"
        governed = (
            "create_automation_configuration",
            "update_automation_configuration",
            "create_script_configuration",
            "update_script_configuration",
            "create_input_boolean_configuration",
            "update_input_boolean_configuration",
            "create_input_number_configuration",
            "update_input_number_configuration",
        )
        for operation in governed:
            with self.subTest(operation=operation):
                self.assertEqual(
                    f3_requirements(
                        Prepared(
                            capability_identity=operation,
                            operation=operation,
                        )
                    ),
                    tuple(
                        sorted(
                            (
                                verification,
                                "core.governed_configuration_operation",
                            )
                        )
                    ),
                )

        operational = {
            "create_full_home_assistant_backup": "create_full_backup",
            "reload_home_assistant_configuration_domain": "controlled_reload",
            "restart_installed_home_assistant_addon": "restart_addon",
            "restart_home_assistant_core": "restart_home_assistant",
        }
        for capability_id, operation in operational.items():
            with self.subTest(operation=operation):
                self.assertEqual(
                    f3_requirements(
                        Prepared(
                            capability_id=capability_id,
                            operation=operation,
                        )
                    ),
                    tuple(
                        sorted(
                            (
                                verification,
                                "core.governed_configuration_operation",
                            )
                        )
                    ),
                )

        helper = Prepared(
            capability_id="set_exact_input_boolean_state",
            operation="set_input_boolean_state",
        )
        self.assertEqual(
            f3_requirements(helper),
            tuple(sorted((verification, "core.typed_helper_operation"))),
        )

        dashboard = Prepared(
            capability_id="update_existing_dashboard",
            capability_identity="update_existing_dashboard",
            operation="update_dashboard",
        )
        self.assertEqual(
            f3_requirements(dashboard),
            tuple(sorted((verification, "core.dashboard_configuration_read"))),
        )

        unsupported = f3_requirements(
            Prepared(
                capability_id="set_exact_input_boolean_state",
                operation="create_input_boolean_configuration",
            )
        )
        self.assertEqual(
            unsupported,
            tuple(sorted((verification, "core.unsupported_f3_operation"))),
        )

    async def test_unknown_version_and_unstable_observation_fail_closed(self):
        runtime, source = await self._runtime(
            _snapshot("2026.9.2", evidence=_evidence())
        )
        self.assertEqual(runtime.health_snapshot()["compatible_count"], 0)
        source.snapshot = _snapshot(
            "2026.9.0", config_version="2026.8.1", evidence=[]
        )
        await runtime.reconcile_once("identity_or_connection_change")
        health = runtime.health_snapshot()
        self.assertFalse(health["identity_agreement"])
        self.assertEqual(health["compatible_count"], 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_f3_predispatch_reprobes_and_core_2026_9_is_admitted(self):
        runtime, source = await self._runtime(_core_2026_9_snapshot())
        guard = _CoreDispatchAuthorityGuard(
            runtime, datetime.now(timezone.utc).isoformat()
        )
        authority = await guard.acquire(_CoreBoundDashboardOperation(), object())
        self.assertIsNotNone(authority)
        self.assertEqual(source.calls, 4)
        self.assertTrue(runtime.release(authority))
        health = runtime.health_snapshot()
        self.assertEqual(health["counters"]["lease_failures"], 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_f3_readback_uses_current_core_2026_9_authority(self):
        runtime, source = await self._runtime(_core_2026_9_snapshot())

        class Adapter:
            capabilities = AdapterCapabilityDescriptor(
                adapter_id="synthetic_core_verification_adapter",
                contract_model="f3-operation-adapter-v1",
                operation_family="synthetic_core_verification",
                supported_operations=("update_dashboard",),
                rollback_supported=False,
                readback_recovery_supported=True,
                exact_provider_contract_required=True,
            )

            def __init__(self):
                self.observe_calls = 0

            async def observe(self, _prepared, _dispatch):
                self.observe_calls += 1
                return "authoritative-observation"

        adapter = Adapter()
        guarded = _CoreVerificationAdapter(adapter, runtime)
        result = await guarded.observe(_CoreBoundDashboardOperation(), object())
        self.assertEqual(result, "authoritative-observation")
        self.assertEqual(adapter.observe_calls, 1)
        self.assertEqual(source.calls, 4)


class CoreRuntimeLoopReconfigurationTests(unittest.TestCase):
    def test_reconfiguration_rebinds_async_primitives_to_the_serving_loop(self):
        runtime = CoreRuntime()

        async def exercise_once(session_id: str) -> None:
            source = _MutableSource(
                _snapshot(
                    "2026.8.1",
                    session_id=session_id,
                    evidence=_evidence(),
                )
            )
            runtime.configure(object(), source=source)
            await runtime.reconcile_once("startup")
            supervisor = asyncio.create_task(
                runtime.supervise(interval_seconds=3_600)
            )
            await asyncio.sleep(0)
            runtime.request_reconciliation()
            for _ in range(10):
                await asyncio.sleep(0)
                if source.calls == 4:
                    break
            self.assertEqual(source.calls, 4)
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)

        asyncio.run(exercise_once("core-loop-one"))
        asyncio.run(exercise_once("core-loop-two"))


class Core20269CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    def test_current_tool_accounting_and_core_requirements_are_exact(self):
        registry = load_reviewed_upstream_release_registry()
        policy = registry.by_version["8.4.3"].policy
        counts = policy.classification_counts
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT, 51)
        self.assertEqual(counts["automatic_read"], 25)
        self.assertEqual(counts["held_for_canary"], 1)
        self.assertEqual(
            {
                item.upstream_name
                for item in policy.tools
                if item.classification == "held_for_canary"
            },
            {"ha_get_operation_status"},
        )
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT + counts["automatic_read"], 76)
        self.assertEqual(len(DELEGATED_CORE_REQUIREMENTS), 26)
        self.assertEqual(
            {
                item.upstream_name
                for item in policy.tools
                if item.classification in {"automatic_read", "held_for_canary"}
            },
            set(DELEGATED_CORE_REQUIREMENTS),
        )
        self.assertEqual(
            DEVICE_DEPENDENT_DELEGATED_TOOLS,
            {
                "ha_get_device",
                "ha_get_entity",
                "ha_get_entity_exposure",
                "ha_get_overview",
                "ha_search",
            },
        )
        self.assertEqual(
            static_tool_requirements("list_devices"),
            ("core.direct_device_registry_read",),
        )
        for tool in DEVICE_DEPENDENT_DELEGATED_TOOLS:
            self.assertEqual(
                delegated_requirements(tool),
                ("core.delegated_device_effective_area",),
            )

    def test_repository_context_reports_current_exact_catalog(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "codex-context.py"),
                "--repo-root",
                str(ROOT),
                "--format",
                "json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        counts = json.loads(result.stdout)["tool_counts"]
        self.assertEqual(counts["reviewed_upstream_version"], "8.4.3")
        self.assertEqual(counts["reviewed_stock_catalog"], 78)
        self.assertEqual(counts["expected_delegated_reads"], 25)
        self.assertEqual(counts["expected_connector_total"], 76)

    async def test_missing_dashboard_evidence_withholds_only_dashboard_resources(self):
        evidence = [
            item
            for item in _core_2026_9_evidence()
            if item["capability_id"] != "core.dashboard_configuration_read"
        ]
        runtime, _source = await Core20269RuntimeTests()._runtime(
            _snapshot("2026.9.0", evidence=evidence)
        )
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version["8.4.3"]
        capture = _capture_for_release(release)
        review = _fixture(ROOT / release.artifact_evidence_resource)
        captured_by_name = {item["name"]: item for item in capture["tools"]}
        tools = [
            captured_by_name[name]
            for name in review["runtime_catalog"]["runtime_tool_order"]
        ]
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=FakeTransport(tools, version="8.4.3"),
            release_registry=registry,
            core_runtime=runtime,
        )
        server = FastMCP("core-2026-9-partial-evidence-test")

        health = await gateway.initialize(server)

        self.assertEqual(
            len(registered_tools(server)),
            24,
            msg=json.dumps(health, sort_keys=True),
        )
        self.assertIsNone(
            registered_tools(server).get("ha_config_list_dashboard_resources")
        )
        self.assertEqual(health["core_withheld_read_count"], 1)
        self.assertEqual(health["last_discovery_failure_category"], None)
        self.assertEqual(health["fallback_count"], 0)

    async def test_catalog_withdrawal_and_restoration_are_capability_local(self):
        runtime, _source = await Core20269RuntimeTests()._runtime(
            _core_2026_9_snapshot()
        )
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version["8.4.1"]
        capture = _capture_for_release(release)
        review = _fixture(ROOT / release.artifact_evidence_resource)
        captured_by_name = {item["name"]: item for item in capture["tools"]}
        tools = [
            captured_by_name[name]
            for name in review["runtime_catalog"]["runtime_tool_order"]
        ]
        transport = FakeTransport(tools, version="8.4.1")
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=transport,
            release_registry=registry,
            core_runtime=runtime,
        )
        server = FastMCP("core-2026-9-catalog-test")
        await gateway.initialize(server)
        self.assertIsNotNone(
            registered_tools(server).get("ha_get_state"),
            msg=json.dumps(gateway.health_snapshot(), sort_keys=True),
        )
        self.assertIsNone(registered_tools(server).get("ha_get_device"))
        health = gateway.health_snapshot()
        self.assertEqual(health["core_withheld_read_count"], 5)
        self.assertEqual(health["dynamically_exposed_count"], 20)
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT + 20, 71)
        self.assertEqual(
            {item["tool"] for item in health["core_withheld_tools"]},
            set(DEVICE_DEPENDENT_DELEGATED_TOOLS),
        )
        result = json.loads(
            await registered_tools(server).get("ha_get_state").run(
                {"entity_id": "sensor.synthetic"}
            )
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(transport.calls), 1)
        self.assertTrue(
            all(call[0] != "ha_get_device" for call in transport.calls)
        )
        self.assertIsNone(
            registered_tools(server).get("ha_get_operation_status")
        )
        upstream_generation = gateway._admission_generation

        release = registry.by_version["8.4.3"]
        capture = _capture_for_release(release)
        review = _fixture(ROOT / release.artifact_evidence_resource)
        captured_by_name = {item["name"]: item for item in capture["tools"]}
        transport.catalog = replace(
            transport.catalog,
            server_version="8.4.3",
            tools=tuple(
                captured_by_name[name]
                for name in review["runtime_catalog"]["runtime_tool_order"]
            ),
        )
        restored_health = await gateway.initialize(server)
        self.assertIsNotNone(
            registered_tools(server).get("ha_get_state"),
            msg=json.dumps(restored_health, sort_keys=True),
        )
        self.assertIsNotNone(registered_tools(server).get("ha_get_device"))
        self.assertEqual(
            gateway.health_snapshot()["dynamically_exposed_count"], 25
        )
        self.assertGreater(gateway._admission_generation, upstream_generation)
        counters = runtime.health_snapshot()["counters"]
        self.assertEqual(counters["catalog_withdrawals"], 5)
        self.assertEqual(counters["catalog_restorations"], 5)
        self.assertEqual(gateway.health_snapshot()["fallback_count"], 0)

    async def test_core_2026_9_device_reads_require_corrected_ha_mcp_release(self):
        snapshot = _snapshot("2026.9.0", evidence=_evidence())
        positive_authority = tuple(
            replace(
                item,
                status=CoreAuthorityStatus.POSITIVE,
                reason_code="synthetic_positive_core_authority",
            )
            for item in compiled_exact_authority("2026.9.0")
        )
        source = _MutableSource(snapshot)
        runtime = CoreRuntime()
        runtime.configure(
            object(),
            source=source,
            authority_provider=lambda _version: positive_authority,
        )
        await runtime.reconcile_once("startup")
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version["8.4.1"]
        capture = _capture_for_release(release)
        review = _fixture(ROOT / release.artifact_evidence_resource)
        captured_by_name = {item["name"]: item for item in capture["tools"]}
        tools = [
            captured_by_name[name]
            for name in review["runtime_catalog"]["runtime_tool_order"]
        ]
        transport = FakeTransport(tools, version="8.4.1")
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=transport,
            release_registry=registry,
            core_runtime=runtime,
        )
        server = FastMCP("core-2026-9-legacy-ha-mcp-test")
        await gateway.initialize(server)

        self.assertIsNone(registered_tools(server).get("ha_get_device"))
        self.assertIsNotNone(registered_tools(server).get("ha_get_state"))
        self.assertEqual(
            {
                item["tool"] for item in gateway.health_snapshot()["core_withheld_tools"]
            },
            set(DEVICE_DEPENDENT_DELEGATED_TOOLS),
        )
        self.assertEqual(transport.calls, [])
        self.assertEqual(gateway.health_snapshot()["fallback_count"], 0)

    async def test_call_time_device_adapter_drift_fails_before_dispatch(self):
        runtime, _source = await Core20269RuntimeTests()._runtime(
            _core_2026_9_snapshot()
        )
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version["8.4.3"]
        capture = _capture_for_release(release)
        review = _fixture(ROOT / release.artifact_evidence_resource)
        captured_by_name = {item["name"]: item for item in capture["tools"]}
        tools = [
            captured_by_name[name]
            for name in review["runtime_catalog"]["runtime_tool_order"]
        ]
        transport = FakeTransport(tools, version="8.4.3")
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=transport,
            release_registry=registry,
            core_runtime=runtime,
        )
        server = FastMCP("core-2026-9-call-time-drift-test")
        await gateway.initialize(server)
        tool = registered_tools(server).get("ha_get_device")
        self.assertIsNotNone(tool)

        gateway._exposed["ha_get_device"] = replace(
            gateway._exposed["ha_get_device"],
            adapter_version="8.4.1",
        )
        value = json.loads(await tool.run({"device_id": "synthetic-device"}))

        self.assertFalse(value["success"])
        self.assertEqual(value["error_code"], "provider_prohibited")
        self.assertEqual(len(transport.attempts), 1)
        self.assertEqual(transport.calls, [])
        self.assertEqual(gateway.health_snapshot()["fallback_count"], 0)

    async def test_all_device_consumers_preserve_child_and_effective_area_semantics(self):
        runtime, _source = await Core20269RuntimeTests()._runtime(
            _core_2026_9_snapshot()
        )
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version["8.4.3"]
        capture = _capture_for_release(release)
        review = _fixture(ROOT / release.artifact_evidence_resource)
        captured_by_name = {item["name"]: item for item in capture["tools"]}
        tools = [
            captured_by_name[name]
            for name in review["runtime_catalog"]["runtime_tool_order"]
        ]
        transport = FakeTransport(tools, version="8.4.3")
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=transport,
            release_registry=registry,
            core_runtime=runtime,
        )
        server = FastMCP("core-2026-9-device-consumer-test")
        await gateway.initialize(server)

        child = "synthetic-child-device"
        parent = "synthetic-parent-device"
        entity = "switch.synthetic-child"
        area = "synthetic-effective-area"
        cases = {
            "ha_get_device": {
                "arguments": {"device_id": child},
                "payload": {
                    "success": True,
                    "device": {
                        "device_id": child,
                        "parent_device_id": parent,
                        "area_id": area,
                    },
                    "entities": [{"entity_id": entity, "device_id": child}],
                },
            },
            "ha_get_overview": {
                "arguments": {"detail_level": "full", "domains": ["switch"]},
                "payload": {
                    "success": True,
                    "area_analysis": {
                        area: [{"entity_id": entity, "device_id": child}]
                    },
                },
            },
            "ha_search": {
                "arguments": {
                    "query": entity,
                    "domain_filter": "switch",
                    "exact_match": True,
                    "result_fields": ["entity_id", "area"],
                    "limit": 20,
                },
                "payload": {
                    "success": True,
                    "partial": False,
                    "entities": [
                        {"entity_id": entity, "device_id": child, "area_id": area}
                    ],
                },
            },
            "ha_get_entity": {
                "arguments": {"entity_id": entity},
                "payload": {
                    "success": True,
                    "entities": [
                        {"entity_id": entity, "device_id": child, "area_id": area}
                    ],
                },
            },
            "ha_get_entity_exposure": {
                "arguments": {"entity_id": entity},
                "payload": {
                    "success": True,
                    "entity_id": entity,
                    "device_id": child,
                    "effective_area_id": area,
                    "exposed_to": [],
                },
            },
        }
        for name, case in cases.items():
            with self.subTest(tool=name):
                validate(
                    instance=case["arguments"],
                    schema=captured_by_name[name]["inputSchema"],
                )
                transport.result = {
                    "content": [
                        {"type": "text", "text": json.dumps(case["payload"])}
                    ],
                    "isError": False,
                }
                tool = registered_tools(server).get(name)
                self.assertIsNotNone(tool)
                value = json.loads(await tool.run(case["arguments"]))
                self.assertTrue(value["success"])
                self.assertTrue(_contains_projection(value["data"], entity))
                self.assertTrue(_contains_projection(value["data"], child))
                self.assertTrue(_contains_projection(value["data"], area))

        self.assertEqual(len(transport.calls), len(cases))
        self.assertEqual(gateway.health_snapshot()["fallback_count"], 0)

class Core20269StaticRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    @staticmethod
    async def _call(gateway: AuthenticatedMcpGateway, tool_name: str) -> bytes:
        configured = settings()
        request = {
            "jsonrpc": "2.0",
            "id": "core-route-test",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": {}},
        }
        delivered = False
        messages: list[dict] = []

        async def receive() -> dict:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {
                "type": "http.request",
                "body": json.dumps(request).encode("utf-8"),
                "more_body": False,
            }

        async def send(message: dict) -> None:
            messages.append(message)

        await gateway(
            {
                "type": "http",
                "method": "POST",
                "path": f"/{configured.access_secret}/mcp",
                "raw_path": f"/{configured.access_secret}/mcp".encode("ascii"),
                "headers": [(b"content-type", b"application/json")],
                "client": ("127.0.0.1", 1),
            },
            receive,
            send,
        )
        return b"".join(
            item.get("body", b"")
            for item in messages
            if item.get("type") == "http.response.body"
        )

    async def test_static_gateway_admits_reviewed_core_2026_9_routes(self):
        runtime, _source = await Core20269RuntimeTests()._runtime(
            _core_2026_9_snapshot()
        )
        app = _RecordingMcpApp()
        configured = settings()
        gateway = AuthenticatedMcpGateway(
            app,
            configured,
            AuditLogger("unused", configured.access_secret, enabled=False),
            core_runtime=runtime,
        )

        admitted_device = await self._call(gateway, "list_devices")
        self.assertIn(b'"isError": false', admitted_device)
        self.assertEqual(len(app.calls), 1)

        admitted = await self._call(gateway, "list_areas")
        self.assertIn(b'"isError": false', admitted)
        self.assertEqual(len(app.calls), 2)
        self.assertEqual(runtime.health_snapshot()["fallback_count"], 0)

    async def test_held_canary_cannot_dispatch_without_core_authority(self):
        runtime, _source = await Core20269RuntimeTests()._runtime(
            _snapshot("2026.9.2", evidence=_evidence())
        )
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version["8.4.3"]
        capture = _capture_for_release(release)
        review = _fixture(ROOT / release.artifact_evidence_resource)
        captured_by_name = {item["name"]: item for item in capture["tools"]}
        tools = [
            captured_by_name[name]
            for name in review["runtime_catalog"]["runtime_tool_order"]
        ]
        transport = FakeTransport(tools, version="8.4.3")
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=transport,
            release_registry=registry,
            core_runtime=runtime,
        )
        server = FastMCP("core-held-canary-test")
        await gateway.initialize(server)

        result = json.loads(
            await gateway.run_held_read_canary(
                upstream_tool_name="ha_get_operation_status",
                expected_compatibility_entry_id=release.entry_id,
                arguments={"operation_id": "synthetic-operation"},
            )
        )

        self.assertFalse(result["success"])
        self.assertEqual(
            result["details"]["failure_category"],
            "prohibited_delegation",
        )
        self.assertFalse(
            result["details"]["canary_evidence"]["dispatch_occurred"]
        )
        self.assertEqual(transport.calls, [])
        self.assertEqual(transport.attempts, [])
        self.assertEqual(gateway.health_snapshot()["fallback_count"], 0)


if __name__ == "__main__":
    unittest.main()
