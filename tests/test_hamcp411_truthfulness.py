"""HAMCP-411 truthfulness bundle (2.8.0 item I3).

- HAMCP-411 (HAAC-E041): an oversize get_history returns whole rows marked
  partial with counts and a continuation, never a cut result read complete.
- HAMCP-412 (HAAC-E027): a device-configuration refusal's public error_code
  names its category, within the existing codes.
- HAMCP-413 (HAAC-E046): a concurrent history or incident-log read is a
  retryable busy refusal, not an unavailable provider.
- HAMCP-408 (HAAC-E051): a reviewed delegated read that is not published says
  the upstream (or that tool) is not admitted; dispatch stays at zero.
- HAMCP-409: values kept from the last successful discovery are marked stale
  while discovery fails.
- HAMCP-414: provider_routing.standard_ha_mcp_delegation follows the current
  delegated-read admission.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering import secret_paths as _hamcp288_secret_paths  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    delegated_tools_admitted,
    replace_dynamic_upstream_capabilities,
    reviewed_delegated_read_names,
)
from ha_mcp_engineering.device_configuration.contracts import (  # noqa: E402
    REFUSAL_CODES,
    DeviceConfigurationRefusal,
)
from ha_mcp_engineering.errors import ErrorCode  # noqa: E402
from ha_mcp_engineering.health import HealthRegistry  # noqa: E402
from ha_mcp_engineering.history_slices.contracts import HistorySlicesError  # noqa: E402
from ha_mcp_engineering.incident_logs.contracts import IncidentLogsError  # noqa: E402
from ha_mcp_engineering.models.responses import dump_json  # noqa: E402
from ha_mcp_engineering.providers.dispatch import CanonicalProviderDispatcher  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.tool_framework import map_exception, run_structured  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402

from tests.test_hamcp164_core_gate_fail_closed import (  # noqa: E402
    RecordingCore,
    call,
    failure_payload,
    gateway_for,
    tool_result,
)
from tests.test_readonly_upstream_gateway import (  # noqa: E402
    SequencedDiscoveryTransport,
    catalog_tool,
    policy,
    policy_entry,
    settings,
)


def history_rows(count: int, *, with_time: bool = True) -> list[dict]:
    rows = []
    for index in range(count):
        row = {"entity_id": "sensor.synthetic", "state": f"{index:06d}"}
        if with_time:
            row["last_changed"] = (
                f"2026-11-16T{index // 3600 % 24:02d}:{index // 60 % 60:02d}:"
                f"{index % 60:02d}+00:00"
            )
        rows.append(row)
    return rows


class BoundedHistoryTests(unittest.TestCase):
    """HAMCP-411 (HAAC-E041)."""

    def bounded(self, data):
        limited = replace(compatibility.SETTINGS, response_size_limit=60_000)
        with patch.object(compatibility, "SETTINGS", limited):
            return compatibility._bounded_history("sensor.synthetic", data)

    def test_history_inside_the_budget_is_returned_unchanged(self):
        data = [history_rows(5)]
        self.assertEqual(json.loads(self.bounded(data)), data)

    def test_oversize_history_returns_whole_rows_marked_partial(self):
        rows = history_rows(3000)
        envelope = json.loads(self.bounded([rows]))
        metadata = envelope["metadata"]
        returned = envelope["data"][0]
        self.assertEqual(metadata["completeness"], "partial")
        self.assertIs(metadata["truncated"], True)
        self.assertIs(metadata["has_more"], True)
        self.assertEqual(metadata["total_states"], 3000)
        self.assertEqual(metadata["returned_states"], len(returned))
        self.assertGreater(len(returned), 0)
        self.assertLess(len(returned), 3000)
        # Whole rows, in order, exactly as received: nothing cut inside a row.
        self.assertEqual(returned, rows[: len(returned)])
        following = rows[len(returned)]["last_changed"]
        self.assertEqual(metadata["cursor"], following)
        self.assertEqual(
            metadata["continuation"],
            {
                "tool": "get_history_slices",
                "arguments": {"entity_id": "sensor.synthetic", "start": following},
            },
        )
        self.assertIn("first %d of 3000" % len(returned), envelope["warnings"][0])
        self.assertLessEqual(
            len(json.dumps([returned], separators=(",", ":"))),
            60_000 - compatibility.HISTORY_ENVELOPE_RESERVE,
        )

    def test_oversize_history_without_a_time_has_no_cursor(self):
        envelope = json.loads(self.bounded([history_rows(3000, with_time=False)]))
        self.assertIsNone(envelope["metadata"]["cursor"])
        self.assertIsNone(envelope["metadata"]["continuation"])
        self.assertIn("Request fewer hours", envelope["warnings"][0])

    def test_other_shapes_keep_the_generic_bound(self):
        data = [history_rows(1500), history_rows(1500)]
        with patch.object(compatibility, "dump", return_value="generic") as dump:
            self.assertEqual(self.bounded(data), "generic")
        dump.assert_called_once_with(data)

    def test_dispatcher_never_reports_a_size_bounded_result_complete(self):
        rows = [history_rows(2000)]

        async def run():
            return json.loads(
                await CanonicalProviderDispatcher().execute(
                    "get_history",
                    lambda: dump_json(rows),
                    arguments={"entity_id": "sensor.synthetic", "hours": 24, "minimal": True},
                    response_limit=60_000,
                )
            )

        out = asyncio.run(run())
        self.assertEqual(out["metadata"]["source_coverage"][0]["completeness"], "partial")
        self.assertTrue(out["warnings"])

    def test_dispatcher_render_bound_is_reported_truncated(self):
        rows = [[{"entity_id": "sensor.synthetic", "state": "x" * 50}] * 200]

        async def run():
            return json.loads(
                await CanonicalProviderDispatcher().execute(
                    "get_history",
                    lambda: dump_json(rows),
                    arguments={"entity_id": "sensor.synthetic", "hours": 24, "minimal": True},
                    response_limit=12_000,
                )
            )

        out = asyncio.run(run())
        self.assertIs(out["response_completeness"]["truncated"], True)
        self.assertIn("/data", out["response_completeness"]["omitted_paths"])
        self.assertNotIn("data", out)

    def test_dispatcher_keeps_complete_when_nothing_was_bounded(self):
        rows = [history_rows(3)]

        async def run():
            return json.loads(
                await CanonicalProviderDispatcher().execute(
                    "get_history",
                    lambda: dump_json(rows),
                    arguments={"entity_id": "sensor.synthetic", "hours": 24, "minimal": True},
                    response_limit=60_000,
                )
            )

        out = asyncio.run(run())
        self.assertEqual(out["metadata"]["source_coverage"][0]["completeness"], "complete")
        self.assertEqual(out["data"], rows)
        self.assertNotIn("response_completeness", out)


class DeviceConfigurationRefusalCodeTests(unittest.TestCase):
    """HAMCP-412 (HAAC-E027)."""

    def refusal(self, category):
        async def run():
            raise DeviceConfigurationRefusal(category)

        return json.loads(
            asyncio.run(
                run_structured(
                    "control_device_configuration", "synthetic", run,
                    metadata={"provider": "synthetic"}, response_limit=60_000,
                )
            )
        )

    def test_public_error_code_names_the_refusal(self):
        cases = {
            "device_configuration_not_config_category": "unsupported_operation",
            "device_configuration_state_changed": "stale_target_state",
            "device_configuration_entity_not_registered": "entity_not_found",
            "device_configuration_target_disabled": "configuration_conflict",
            "device_configuration_undo_unavailable": "rollback_not_available",
            "device_configuration_capacity_exhausted": "rate_limit_exceeded",
            "device_configuration_connector_authorization_required": "authorization_failure",
            "device_configuration_provider_contract_mismatch": "provider_unavailable",
        }
        for category, code in cases.items():
            with self.subTest(category=category):
                out = self.refusal(category)
                self.assertFalse(out["success"])
                self.assertEqual(out["error_code"], code)
                self.assertEqual(out["details"]["category"], category)
                self.assertEqual(
                    out["details"]["status"],
                    "provider_unavailable" if code == "provider_unavailable" else "refused",
                )

    def test_fan_spelling_maps_like_the_device_configuration_spelling(self):
        out = self.refusal("fan_not_config_category")
        self.assertEqual(out["details"]["category"], "device_configuration_not_config_category")
        self.assertEqual(out["error_code"], "unsupported_operation")

    def test_unlisted_category_stays_invalid_request(self):
        category = "device_configuration_operation_id_expired"
        self.assertNotIn(category, REFUSAL_CODES)
        out = self.refusal(category)
        self.assertEqual(out["error_code"], "invalid_request")
        self.assertEqual(out["details"]["status"], "refused")

    def test_every_mapped_code_is_an_existing_code(self):
        self.assertTrue(set(REFUSAL_CODES.values()) <= set(ErrorCode))


class BusyReadTests(unittest.TestCase):
    """HAMCP-413 (HAAC-E046); HAMCP-513: the shared read_busy (409) code."""

    def test_concurrent_read_is_a_retryable_busy_refusal(self):
        for error_type in (HistorySlicesError, IncidentLogsError):
            with self.subTest(error=error_type.__name__):
                code, message, retryable, details = map_exception(
                    error_type("read_in_progress")
                )
                self.assertEqual(code, ErrorCode.READ_BUSY)
                self.assertIs(retryable, True)
                self.assertIn("retry after it completes", message)
                self.assertEqual(details["reason"], "read_in_progress")
                self.assertIs(details["fallback_occurred"], False)

    def test_other_reasons_keep_their_codes(self):
        cases = (
            (HistorySlicesError, "history_route_unavailable", ErrorCode.PROVIDER_UNAVAILABLE),
            (HistorySlicesError, "history_unavailable", ErrorCode.ANALYSIS_UNAVAILABLE),
            (IncidentLogsError, "authority_unavailable", ErrorCode.PROVIDER_UNAVAILABLE),
            (IncidentLogsError, "sources_unavailable", ErrorCode.ANALYSIS_UNAVAILABLE),
        )
        for error_type, reason, expected in cases:
            with self.subTest(error=error_type.__name__, reason=reason):
                code, _message, _retryable, details = map_exception(error_type(reason))
                self.assertEqual(code, expected)
                self.assertEqual(details["reason"], reason)


class DelegatedNotAdmittedTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-408 (HAAC-E051)."""

    async def asyncTearDown(self):
        replace_dynamic_upstream_capabilities((), {})

    def refusal(self, body, app, core) -> dict:
        self.assertIs(tool_result(body)["isError"], True)
        payload = failure_payload(body)
        self.assertFalse(payload["retryable"])
        details = payload["details"]
        self.assertFalse(details["core_authority_acquired"])
        self.assertFalse(details["provider_dispatch_occurred"])
        self.assertFalse(details["upstream_attempted"])
        self.assertEqual(details["home_assistant_request_count"], 0)
        self.assertEqual(details["upstream_request_count"], 0)
        self.assertEqual(app.calls, [])
        self.assertEqual(core.acquired, [])
        return payload

    async def test_not_published_read_with_admitted_upstream_names_the_tool(self):
        replace_dynamic_upstream_capabilities(
            (), {"configured": True, "dynamically_exposed_count": 1}
        )
        self.assertTrue(delegated_tools_admitted())
        core = RecordingCore()
        gateway, app = gateway_for(core)
        payload = self.refusal(await call(gateway, "ha_get_state"), app, core)
        self.assertEqual(payload["error"], "DelegatedToolNotAdmitted")
        self.assertEqual(payload["error_code"], "provider_unavailable")
        self.assertEqual(payload["details"]["reason"], "delegated_tool_not_admitted")

    async def test_upstream_not_admitted_says_so(self):
        replace_dynamic_upstream_capabilities(
            (), {"configured": True, "dynamically_exposed_count": 0}
        )
        self.assertFalse(delegated_tools_admitted())
        core = RecordingCore()
        gateway, app = gateway_for(core)
        payload = self.refusal(await call(gateway, "ha_mcp__ha_get_state"), app, core)
        self.assertEqual(payload["error"], "UpstreamNotAdmitted")
        self.assertEqual(payload["details"]["reason"], "upstream_not_admitted")

    async def test_unreviewed_name_stays_unmapped_even_when_admitted(self):
        replace_dynamic_upstream_capabilities(
            (), {"configured": True, "dynamically_exposed_count": 1}
        )
        for name in ("ha_call_service", "ha_mcp__ha_call_service", "synthetic_unknown_tool"):
            with self.subTest(tool=name):
                self.assertNotIn(name, reviewed_delegated_read_names())
                core = RecordingCore()
                gateway, app = gateway_for(core)
                payload = self.refusal(await call(gateway, name), app, core)
                self.assertEqual(payload["error"], "CoreRequirementUnmapped")
                self.assertEqual(payload["error_code"], "unsupported_operation")

    def test_reviewed_names_are_reads_only(self):
        names = reviewed_delegated_read_names()
        self.assertIn("ha_get_state", names)
        self.assertIn("ha_mcp__ha_get_state", names)
        self.assertNotIn("ha_call_service", names)
        self.assertNotIn("ha_config_set_automation", names)


class DelegationHealthTests(unittest.TestCase):
    """HAMCP-414."""

    def tearDown(self):
        replace_dynamic_upstream_capabilities((), {})

    def delegation(self):
        snapshot = HealthRegistry().snapshot({"status": "not_checked"})
        return snapshot["provider_routing"]["standard_ha_mcp_delegation"]

    def test_follows_current_delegated_read_admission(self):
        replace_dynamic_upstream_capabilities(
            (), {"configured": True, "dynamically_exposed_count": 3}
        )
        self.assertEqual(self.delegation(), "available")
        replace_dynamic_upstream_capabilities(
            (), {"configured": True, "dynamically_exposed_count": 0}
        )
        self.assertEqual(self.delegation(), "unavailable")
        replace_dynamic_upstream_capabilities((), {"configured": False})
        self.assertEqual(self.delegation(), "unavailable")


class StaleObservationTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-409."""

    async def asyncTearDown(self):
        replace_dynamic_upstream_capabilities((), {})

    async def admitted(self):
        entry = policy_entry("ha_get_state")
        transport = SequencedDiscoveryTransport([catalog_tool(entry.upstream_name)], [])
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(), transport=transport, policy=policy(entry),
            admission_validator=lambda _catalog: None,
        )
        server = FastMCP("hamcp409-test")
        state = await gateway.initialize(server)
        return gateway, server, transport, state

    async def test_successful_discovery_is_current(self):
        _gateway, _server, _transport, state = await self.admitted()
        self.assertEqual(state["upstream_observation_status"], "current")
        self.assertEqual(state["upstream_observation_stale_fields"], [])
        self.assertIsNone(state["upstream_observation_stale_since"])

    async def test_failed_discovery_marks_kept_values_stale(self):
        gateway, server, transport, before = await self.admitted()
        transport.outcomes = ["timeout"]
        state = await gateway.initialize(server)
        self.assertEqual(state["last_discovery_failure_category"], "timeout")
        self.assertEqual(state["upstream_observation_status"], "stale")
        self.assertEqual(
            set(state["upstream_observation_stale_fields"]),
            {
                "observed_upstream_server_name",
                "observed_upstream_server_version",
                "observed_protocol_version",
                "observed_catalog_fingerprint",
                "upstream_advertised_tool_count",
                "observed_advertised_tool_count",
            },
        )
        since = state["upstream_observation_stale_since"]
        self.assertIsInstance(since, str)
        # The kept values are still reported, now marked stale.
        self.assertEqual(
            state["observed_upstream_server_version"],
            before["observed_upstream_server_version"],
        )
        transport.outcomes = ["connection_failed"]
        again = await gateway.initialize(server)
        self.assertEqual(again["upstream_observation_status"], "stale")
        self.assertEqual(again["upstream_observation_stale_since"], since)
        # The next successful discovery is current again.
        current = await gateway.initialize(server)
        self.assertEqual(current["upstream_observation_status"], "current")
        self.assertEqual(current["upstream_observation_stale_fields"], [])
        self.assertIsNone(current["upstream_observation_stale_since"])

    async def test_identity_observed_on_failure_leaves_only_counts_stale(self):
        gateway, _server, _transport, _state = await self.admitted()
        with gateway._lock:
            gateway._mark_observation_stale_locked(identity_observed=True)
            state = dict(gateway._state)
        self.assertEqual(state["upstream_observation_status"], "stale")
        self.assertEqual(
            set(state["upstream_observation_stale_fields"]),
            {
                "observed_catalog_fingerprint",
                "upstream_advertised_tool_count",
                "observed_advertised_tool_count",
            },
        )

    async def test_never_observed_stays_not_observed(self):
        entry = policy_entry("ha_get_state")
        transport = SequencedDiscoveryTransport(
            [catalog_tool(entry.upstream_name)], ["timeout"]
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(), transport=transport, policy=policy(entry),
            admission_validator=lambda _catalog: None,
        )
        state = await gateway.initialize(FastMCP("hamcp409-never"))
        self.assertEqual(state["last_discovery_failure_category"], "timeout")
        self.assertEqual(state["upstream_observation_status"], "not_observed")
        self.assertEqual(state["upstream_observation_stale_fields"], [])
        self.assertIsNone(state["upstream_observation_stale_since"])


def tearDownModule():
    _hamcp288_secret_paths.reset()


if __name__ == "__main__":
    unittest.main()
