"""HAMCP-306: get_dashboard_config sizes a response by the limit's own measure.

The general response limit (``dump_json_with_truncation``) admits a response
whose compact, ASCII-escaped JSON fits. get_dashboard_config used to refuse a
configuration by its indented size plus a 16,000-character reserve while it
reported the raw configuration's compact size, so a ~30 KB nested dashboard
was refused with a reported size of half the limit. These tests pin one
measure for the check and the report, at and around the boundary, and bound
that measure against the real compact response: never below it, and at most
84 characters above it (HAMCP-306 round 1).
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
    CONFIGURATION_TOOL_GROWING_TIMING,
    RESPONSE_ENVELOPE_RESERVE,
    UPSTREAM_DASHBOARD,
    WIDEST_JSON_FLOAT,
    _engineering_config_hash,
    _upstream_config_hash,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from ha_mcp_engineering.tool_framework import timing_since  # noqa: E402
from ha_mcp_engineering.tools import dashboard as dashboard_tools  # noqa: E402

from test_rc3a_dashboard_provider import FakeTransport, settings  # noqa: E402


LIMIT = 60_000
FIXED_TIMESTAMP = "2026-11-04T18:00:00.123456Z"
# The largest documented gap between the estimate and the real compact
# response: four growing timing members, each at most 24 - 3 characters
# wider in the estimate than its narrowest real rendering ("0.0").
MAX_SLACK = 4 * (24 - 3)
CONTRACT_SLACK = 128


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


def non_ascii_dashboard(target):
    """A dashboard whose text is mostly CJK and emoji (6-12 escaped chars each)."""

    cards = []
    index = 0
    while True:
        cards.append({
            "type": "markdown",
            "title": f"居間の温度 {index} 🌡️",
            "content": "温度と湿度 · Ελληνικά · Привет · 🏠🔥❄️ " * 4,
        })
        index += 1
        config = {"title": "ホーム管理", "views": [{"cards": cards}]}
        if compact_size(config) >= target:
            return config


def near_limit_dashboard():
    """A nested dashboard whose complete response is just under 60,000."""

    base = nested_dashboard(50_000)
    return dict(base, padding="p" * (56_800 - compact_size(base)))


def fixed_timing(value):
    """The real tool timing with each still-growing member set to ``value``."""

    def build(started):
        timing = timing_since(started)
        for name in CONFIGURATION_TOOL_GROWING_TIMING:
            setattr(timing, name, value)
        return timing

    return build


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

    async def read(self, config, *, limit=LIMIT, request_id=None, timing=None):
        """Read through the public tool exactly as a client would.

        ``request_id`` runs the read inside a real request context, so the
        tool timing carries real upstream telemetry; ``timing`` pins the
        still-growing timing members for a deterministic response size.
        """

        transport = FakeTransport(payload=payload_for(config))
        UPSTREAM_DASHBOARD.configure(
            settings(response_size_limit=limit), transport=transport
        )
        token = None
        if request_id is not None:
            _telemetry, token = begin_request(request_id)
        try:
            with patch.object(
                dashboard_tools, "SETTINGS", settings(response_size_limit=limit)
            ), patch.object(
                dashboard_tools,
                "timing_since",
                timing_since if timing is None else fixed_timing(timing),
            ):
                rendered = await dashboard_tools.get_dashboard_config(
                    "home-admin-dashboard"
                )
        finally:
            if token is not None:
                end_request(token)
        self.assertEqual(transport.tool_dispatch_count, 1)
        result = json.loads(rendered)
        if request_id is not None:
            self.assertEqual(result["request_id"], request_id)
            self.assertGreaterEqual(result["timing"]["upstream_request_count"], 1)
        return rendered, result

    async def actual_size(self, config, **options):
        """The real compact size of the complete response, read with room."""

        _rendered, result = await self.read(config, limit=1_000_000, **options)
        self.assertTrue(result["success"])
        return compact_size(result)

    async def actual_size_of(self, base, size, **options):
        """``base`` padded so that its real compact response is ``size``."""

        config = dict(base, padding="")
        padding = size - await self.actual_size(config, **options)
        self.assertGreaterEqual(padding, 0)
        config = dict(base, padding="p" * padding)
        self.assertEqual(await self.actual_size(config, **options), size)
        return config

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

    async def test_estimate_bounds_the_real_response_within_the_slack(self):
        fixtures = {
            "small": {"views": [{"title": "Home", "cards": []}]},
            "nested_30kb": nested_dashboard(29_907),
            "non_ascii_heavy": non_ascii_dashboard(48_000),
            "near_limit": near_limit_dashboard(),
        }
        long_id = "r" * 128
        variants = {
            "no_request_context": {},
            "request_context": {"request_id": "hamcp306-slack"},
            "longest_request_id": {"request_id": long_id},
            "narrowest_timing": {"request_id": long_id, "timing": 0.0},
            "widest_timing": {"request_id": long_id, "timing": WIDEST_JSON_FLOAT},
        }
        slack = {}
        for name, config in fixtures.items():
            for variant, options in variants.items():
                with self.subTest(fixture=name, variant=variant):
                    rendered, result = await self.read(config, **options)
                    self.assert_returned(rendered, result, config, limit=LIMIT)
                    gap = (
                        result["data"]["estimated_serialized_size"]
                        - compact_size(result)
                    )
                    self.assertGreaterEqual(gap, 0)
                    self.assertLessEqual(gap, MAX_SLACK)
                    self.assertLessEqual(gap, CONTRACT_SLACK)
                    slack[name, variant] = gap
        # Pinned timing makes the gap exact: none at the widest rendering,
        # the documented maximum at the narrowest.
        for name in fixtures:
            self.assertEqual(slack[name, "widest_timing"], 0)
            self.assertEqual(slack[name, "narrowest_timing"], MAX_SLACK)

    async def test_complete_response_of_59612_characters_is_returned(self):
        """Codex R1: a 59,612-character response was refused at 60,001."""

        options = {"request_id": "r" * 128, "timing": 1.0}
        config = await self.actual_size_of(
            nested_dashboard(50_000), 59_612, **options
        )
        rendered, result = await self.read(config, **options)
        self.assert_returned(rendered, result, config, limit=LIMIT)
        self.assertEqual(compact_size(result), 59_612)
        self.assertLessEqual(
            result["data"]["estimated_serialized_size"], 59_612 + MAX_SLACK
        )

    async def test_response_over_the_limit_is_refused_in_the_same_measure(self):
        for timing in (0.0, WIDEST_JSON_FLOAT):
            options = {"request_id": "r" * 128, "timing": timing}
            config = await self.actual_size_of(
                nested_dashboard(50_000), LIMIT + 1, **options
            )
            with self.subTest(timing=timing):
                _rendered, result = await self.read(config, **options)
                self.assert_refused(result, config, limit=LIMIT)
                self.assertGreaterEqual(
                    result["details"]["estimated_serialized_size"], LIMIT + 1
                )

    async def test_actual_response_boundary_at_the_default_limit(self):
        # With the growing timing members at their widest the estimate is the
        # real compact size, so the actual-fit boundary is exact.
        options = {"request_id": "r" * 128, "timing": WIDEST_JSON_FLOAT}
        base = nested_dashboard(50_000)
        for size in (LIMIT - 1, LIMIT):
            config = await self.actual_size_of(base, size, **options)
            with self.subTest(actual=size):
                rendered, result = await self.read(config, **options)
                self.assert_returned(rendered, result, config, limit=LIMIT)
                self.assertEqual(result["data"]["estimated_serialized_size"], size)
        config = await self.actual_size_of(base, LIMIT + 1, **options)
        _rendered, result = await self.read(config, **options)
        self.assert_refused(result, config, limit=LIMIT)
        self.assertEqual(result["details"]["estimated_serialized_size"], LIMIT + 1)
        # At their narrowest the estimate is MAX_SLACK above the real size:
        # every response within LIMIT - MAX_SLACK is returned whatever the
        # timing renders as.
        options["timing"] = 0.0
        config = await self.actual_size_of(base, LIMIT - MAX_SLACK, **options)
        rendered, result = await self.read(config, **options)
        self.assert_returned(rendered, result, config, limit=LIMIT)
        self.assertEqual(result["data"]["estimated_serialized_size"], LIMIT)

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
