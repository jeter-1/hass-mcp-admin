"""Complete dashboard approval-projection and rendering regressions."""

from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

from ha_mcp_engineering.approval_web import _render_review  # noqa: E402
from ha_mcp_engineering.f3_dashboard.approval_projection import (  # noqa: E402
    build_dashboard_approval_projection,
    validate_dashboard_approval_projection,
)
from ha_mcp_engineering.f3_dashboard.errors import (  # noqa: E402
    ApprovalProjectionError,
)
from ha_mcp_engineering.f3_dashboard.patch import (  # noqa: E402
    compile_dashboard_patch,
)
from f3_dashboard_support import (  # noqa: E402
    home_dashboard_patch_operations,
    load_home_dashboard,
)


class DashboardApprovalProjectionTests(unittest.TestCase):
    def test_realistic_home_operations_are_projected_completely(self):
        compilation = compile_dashboard_patch(
            load_home_dashboard(), home_dashboard_patch_operations()
        )
        projection = build_dashboard_approval_projection(compilation)
        operations = validate_dashboard_approval_projection(
            projection,
            expected_preread_sha256=compilation.preread_sha256,
            expected_patch_sha256=compilation.canonical_patch_sha256,
            expected_resulting_sha256=compilation.resulting_sha256,
        )

        self.assertTrue(projection["complete"])
        self.assertEqual(projection["operation_count"], 4)
        self.assertEqual(len(operations), 4)
        cleaner = operations[0]["proposed"]["value"]
        self.assertEqual(cleaner["content"], "Cleaner")
        outdoor = operations[1]["proposed"]["value"]
        self.assertEqual(
            outdoor["entity"], "sensor.local_outdoor_temperature"
        )
        removed = operations[2]
        self.assertEqual(removed["previous"]["value"]["content"], "Prompted")
        self.assertEqual(removed["proposed"], {"state": "absent"})
        attention = operations[3]["proposed"]["value"]
        self.assertEqual(attention["title"], "Needs Attention")
        self.assertEqual(
            projection["binding"]["projection_sha256"],
            "37235872dd72e7bdc856a6af668ffb61655207b9d65529257dde0fc944dbcd8a",
        )

    def test_complete_values_are_html_escaped_and_never_previewed(self):
        malicious_card = {
            "type": "markdown",
            "content": "<script>window.evil = true</script>",
            "items": ["one", "two", {"nested": "complete"}],
        }
        baseline = {
            "insert": [],
            "replace": {"card": {"type": "markdown", "content": "old"}},
            "remove": {"card": {"type": "tile", "entity": "sensor.old"}},
        }
        compilation = compile_dashboard_patch(
            baseline,
            [
                {
                    "operation_id": "insert-card",
                    "operation": "add",
                    "path": "/insert/-",
                    "value": malicious_card,
                },
                {
                    "operation_id": "replace-card",
                    "operation": "replace",
                    "path": "/replace/card",
                    "value": malicious_card,
                },
                {
                    "operation_id": "remove-card",
                    "operation": "remove",
                    "path": "/remove/card",
                },
            ],
        )
        projection = build_dashboard_approval_projection(compilation)
        html = _render_review(
            "",
            {
                "operation": "update_dashboard",
                "target_type": "dashboard",
                "target_id": "synthetic",
                "dashboard_review": {"approval_projection": projection},
            },
            "csrf",
        )

        self.assertIn("Complete declared dashboard changes", html)
        self.assertIn("insert-card", html)
        self.assertIn("replace-card", html)
        self.assertIn("remove-card", html)
        self.assertIn("&lt;script&gt;window.evil = true&lt;/script&gt;", html)
        self.assertNotIn("<script>window.evil = true</script>", html)
        self.assertIn('&quot;one&quot;', html)
        self.assertIn('&quot;nested&quot;: &quot;complete&quot;', html)
        self.assertNotIn("<collection preview omitted>", html)
        self.assertIn("<em>absent</em>", html)
        self.assertIn("Approve exact plan", html)

    def test_incomplete_projection_disables_approval(self):
        compilation = compile_dashboard_patch(
            {"items": []},
            [
                {
                    "operation_id": "insert",
                    "operation": "add",
                    "path": "/items/-",
                    "value": {"type": "tile", "entity": "sensor.test"},
                }
            ],
        )
        projection = build_dashboard_approval_projection(compilation)
        malformed = deepcopy(projection)
        malformed["operations"][0].pop("proposed")
        html = _render_review(
            "",
            {
                "operation": "update_dashboard",
                "target_type": "dashboard",
                "target_id": "synthetic",
                "dashboard_review": {"approval_projection": malformed},
            },
            "csrf",
        )

        self.assertIn("Approval is disabled", html)
        self.assertNotIn("Approve exact plan", html)
        with self.assertRaises(ApprovalProjectionError):
            validate_dashboard_approval_projection(malformed)

    def test_projection_value_or_binding_tamper_fails_closed(self):
        compilation = compile_dashboard_patch(
            {"items": []},
            [
                {
                    "operation_id": "insert",
                    "operation": "add",
                    "path": "/items/-",
                    "value": {"type": "tile", "entity": "sensor.test"},
                }
            ],
        )
        projection = build_dashboard_approval_projection(compilation)
        mutations = (
            lambda value: value["operations"][0]["proposed"]["value"].update(
                {"entity": "sensor.tampered"}
            ),
            lambda value: value["binding"].update(
                {"resulting_sha256": "0" * 64}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                tampered = deepcopy(projection)
                mutate(tampered)
                with self.assertRaises(ApprovalProjectionError):
                    validate_dashboard_approval_projection(
                        tampered,
                        expected_resulting_sha256=(
                            compilation.resulting_sha256
                        ),
                    )

    def test_projection_with_protected_content_fails_planning_boundary(self):
        compilation = compile_dashboard_patch(
            {"items": []},
            [
                {
                    "operation_id": "insert",
                    "operation": "add",
                    "path": "/items/-",
                    "value": {"type": "custom:test", "token": "secret"},
                }
            ],
        )
        with self.assertRaises(ApprovalProjectionError) as caught:
            build_dashboard_approval_projection(compilation)
        self.assertEqual(
            caught.exception.reason,
            "approval_projection_contains_protected_data",
        )

    def test_projection_overflow_fails_instead_of_truncating(self):
        compilation = compile_dashboard_patch(
            {"items": []},
            [
                {
                    "operation_id": "insert",
                    "operation": "add",
                    "path": "/items/-",
                    "value": {"type": "markdown", "content": "x" * 512},
                }
            ],
        )
        with patch(
            "ha_mcp_engineering.f3_dashboard.approval_projection."
            "MAX_DASHBOARD_APPROVAL_PROJECTION_BYTES",
            128,
        ), self.assertRaises(ApprovalProjectionError) as caught:
            build_dashboard_approval_projection(compilation)

        self.assertEqual(
            caught.exception.reason, "approval_projection_too_large"
        )
        self.assertEqual(
            caught.exception.constraint, "approval_projection_bytes"
        )
        self.assertGreater(caught.exception.observed, caught.exception.limit)


if __name__ == "__main__":
    unittest.main()
