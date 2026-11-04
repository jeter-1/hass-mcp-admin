"""HAMCP-306: get_dashboard_config sizes a response by the limit's own measure.

The general response limit (``dump_json_with_truncation``) admits a response
whose compact, ASCII-escaped JSON fits. get_dashboard_config used to refuse a
configuration by its indented size plus a 16,000-character reserve while it
reported the raw configuration's compact size, so a ~30 KB nested dashboard
was refused with a reported size of half the limit. These tests pin one
measure for the check and the report, at and around the boundary.
"""

import dataclasses
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.models.responses import (  # noqa: E402
    Timing,
    dump_json_with_truncation,
)
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.providers import upstream_dashboard  # noqa: E402
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    RESPONSE_ENVELOPE_RESERVE,
    UPSTREAM_DASHBOARD,
    _engineering_config_hash,
    _upstream_config_hash,
)
from ha_mcp_engineering.tools import dashboard as dashboard_tools  # noqa: E402

from test_rc3a_dashboard_provider import FakeTransport, settings  # noqa: E402


LIMIT = 60_000
FIXED_TIMESTAMP = "2026-11-04T18:00:00.123456Z"


def compact_size(value):
    """The general limit's fit measure (``models.responses._compact_json``)."""

    return len(json.dumps(value, separators=(",", ":"), default=str))


def nested_dashboard(target, *, padding=0):
    """A sections dashboard shaped like a real one: deep cards, non-ASCII text."""

    views = []
    index = 0
    while True:
        cards = []
        for card in range(6):
            cards.append({
                "type": "vertical-stack",
                "cards": [{
                    "type": "horizontal-stack",
                    "cards": [
                        {
                            "type": "tile",
                            "entity": f"sensor.temperature_salon_{index}_{card}",
                            "name": f"Température · Salon {index}/{card} — °C",
                            "features": [{"type": "target-temperature"}],
                            "tap_action": {"action": "more-info"},
                        },
                        {
                            "type": "conditional",
                            "conditions": [{
                                "condition": "state",
                                "entity": f"binary_sensor.porte_{index}_{card}",
                                "state": "on",
                            }],
                            "card": {
                                "type": "markdown",
                                "content": f"Porte ouverte ⚠️ {index}",
                            },
                        },
                    ],
                }],
            })
        views.append({
            "title": f"Vue {index} – Maison",
            "path": f"view-{index}",
            "type": "sections",
            "sections": [{"type": "grid", "cards": cards}],
        })
        index += 1
        config = {"views": views, "padding": "p" * padding}
        if compact_size(config) >= target:
            return config


def payload_for(config, url_path="home-admin-dashboard"):
    return {
        "success": True,
        "action": "get",
        "url_path": url_path,
        "config": config,
    }


class DashboardReadSizeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        METRICS.reset()
        timestamp = patch.object(
            upstream_dashboard, "_utc_now", return_value=FIXED_TIMESTAMP
        )
        timestamp.start()
        self.addCleanup(timestamp.stop)

    async def asyncTearDown(self):
        UPSTREAM_DASHBOARD.configure(settings(""))

    async def read(self, config, *, limit=LIMIT):
        """Read through the public tool exactly as a client would."""

        transport = FakeTransport(payload=payload_for(config))
        UPSTREAM_DASHBOARD.configure(
            settings(response_size_limit=limit), transport=transport
        )
        with patch.object(
            dashboard_tools, "SETTINGS", settings(response_size_limit=limit)
        ):
            rendered = await dashboard_tools.get_dashboard_config(
                "home-admin-dashboard"
            )
        self.assertEqual(transport.tool_dispatch_count, 1)
        return rendered, json.loads(rendered)

    async def estimate(self, config):
        """The success-path estimate, read with ample room."""

        _rendered, result = await self.read(config, limit=1_000_000)
        self.assertTrue(result["success"])
        return result["data"]["estimated_serialized_size"]

    async def padded_to(self, size):
        """A dashboard whose estimate is exactly ``size`` characters."""

        base = nested_dashboard(size - 12_000)
        probe = await self.estimate(base)
        padding = size - probe
        self.assertGreater(padding, 0)
        config = dict(base, padding="p" * padding)
        self.assertEqual(await self.estimate(config), size)
        return config

    def assert_returned(self, rendered, result, config, *, limit):
        self.assertTrue(result["success"], result.get("error_code"))
        data = result["data"]
        self.assertTrue(data["configuration_returned"])
        self.assertEqual(data["configuration"], config)
        self.assertEqual(data["config_hash"], _upstream_config_hash(config))
        self.assertEqual(
            data["engineering_config_hash"], _engineering_config_hash(config)
        )
        self.assertEqual(result["metadata"]["completeness"], "complete")
        # The response really fits, untruncated, and the estimate bounds it.
        self.assertLessEqual(len(rendered), limit)
        self.assertFalse(dump_json_with_truncation(result, limit)[1])
        self.assertLessEqual(compact_size(result), data["estimated_serialized_size"])
        self.assertLessEqual(data["estimated_serialized_size"], limit)

    def assert_refused(self, result, config, *, limit):
        self.assertFalse(result["success"])
        self.assertEqual(
            result["error_code"], "upstream_dashboard_response_too_large"
        )
        details = result["details"]
        self.assertFalse(details["configuration_returned"])
        self.assertEqual(details["response_limit"], limit)
        self.assertGreater(details["estimated_serialized_size"], limit)
        self.assertEqual(details["config_hash"], _upstream_config_hash(config))
        self.assertEqual(
            details["engineering_config_hash"], _engineering_config_hash(config)
        )
        self.assertTrue(result["metadata"]["upstream_dispatch_occurred"])
        self.assertNotIn('"configuration":', json.dumps(result))

    async def test_thirty_kilobyte_nested_dashboard_is_returned(self):
        config = nested_dashboard(29_907)
        self.assertGreaterEqual(compact_size(config), 29_907)
        self.assertLess(compact_size(config), 31_000)
        # The pre-HAMCP-306 check refused exactly this shape.
        self.assertGreater(
            len(json.dumps(config, indent=2)) + RESPONSE_ENVELOPE_RESERVE, LIMIT
        )
        rendered, result = await self.read(config)
        self.assert_returned(rendered, result, config, limit=LIMIT)
        self.assertLess(result["data"]["estimated_serialized_size"], LIMIT // 2 + 4_000)

    async def test_oversized_dashboard_refusal_reports_the_checked_measure(self):
        config = nested_dashboard(70_000)
        _rendered, result = await self.read(config)
        self.assert_refused(result, config, limit=LIMIT)
        # The reported size is the success-path measure of the same response,
        # and that measure bounds the response the limit would have rendered.
        _rendered, roomy = await self.read(config, limit=1_000_000)
        reported = result["details"]["estimated_serialized_size"]
        self.assertEqual(reported, roomy["data"]["estimated_serialized_size"])
        self.assertGreaterEqual(reported, compact_size(roomy))

    async def test_reported_size_is_the_exact_admission_threshold(self):
        config = nested_dashboard(40_000)
        size = await self.estimate(config)
        rendered, result = await self.read(config, limit=size)
        self.assert_returned(rendered, result, config, limit=size)
        self.assertEqual(result["data"]["estimated_serialized_size"], size)
        _rendered, result = await self.read(config, limit=size - 1)
        self.assert_refused(result, config, limit=size - 1)
        self.assertEqual(result["details"]["estimated_serialized_size"], size)

    async def test_boundary_at_the_default_response_limit(self):
        at_limit = await self.padded_to(LIMIT)
        rendered, result = await self.read(at_limit)
        self.assert_returned(rendered, result, at_limit, limit=LIMIT)
        self.assertEqual(result["data"]["estimated_serialized_size"], LIMIT)

        under = dict(at_limit, padding=at_limit["padding"][:-1])
        rendered, result = await self.read(under)
        self.assert_returned(rendered, result, under, limit=LIMIT)
        self.assertEqual(result["data"]["estimated_serialized_size"], LIMIT - 1)

        over = dict(at_limit, padding=at_limit["padding"] + "p")
        _rendered, result = await self.read(over)
        self.assert_refused(result, over, limit=LIMIT)
        self.assertEqual(result["details"]["estimated_serialized_size"], LIMIT + 1)

    async def test_estimate_bounds_a_worst_case_tool_envelope(self):
        config = await self.padded_to(LIMIT)
        huge = 1.7976931348623157e308
        worst = Timing(**{
            item.name: (
                huge if "float" in str(item.type)
                else 2**64 if "int" in str(item.type)
                else True
            )
            for item in dataclasses.fields(Timing)
        })
        with patch.object(dashboard_tools, "timing_since", return_value=worst), \
                patch.object(
                    dashboard_tools, "current_request_id", return_value="r" * 128
                ):
            rendered, result = await self.read(config)
        self.assertEqual(result["request_id"], "r" * 128)
        self.assert_returned(rendered, result, config, limit=LIMIT)

    async def test_sanitized_configuration_is_what_is_measured(self):
        secret = "synthetic-engineering-access-secret-value"
        config = nested_dashboard(30_000)
        config = dict(config, note=f"leak {secret}")
        rendered, result = await self.read(config)
        self.assertTrue(result["success"])
        self.assertNotIn(secret, rendered)
        self.assertEqual(result["metadata"]["completeness"], "partial")
        self.assertLessEqual(
            compact_size(result), result["data"]["estimated_serialized_size"]
        )


if __name__ == "__main__":
    unittest.main()
