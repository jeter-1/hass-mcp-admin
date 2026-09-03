"""Complete dashboard approval projection and inert rendering regressions."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

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


class DashboardApprovalProjectionTests(unittest.TestCase):
    def _compilation(self):
        return compile_dashboard_patch(
            {
                "title": "Before",
                "views": [{"cards": [{"type": "tile", "entity": "sensor.old"}]}],
                "remove_me": {"nested": [1, 2, 3]},
            },
            [
                {
                    "operation_id": "rename-title",
                    "operation": "replace",
                    "path": "/title",
                    "value": "After",
                },
                {
                    "operation_id": "append-card",
                    "operation": "add",
                    "path": "/views/0/cards/-",
                    "value": {
                        "type": "map",
                        "entities": ["device_tracker.one", "device_tracker.two"],
                    },
                },
                {
                    "operation_id": "remove-section",
                    "operation": "remove",
                    "path": "/remove_me",
                },
            ],
        )

    def test_complete_values_and_bindings_are_deterministic(self):
        compilation = self._compilation()
        first = build_dashboard_approval_projection(compilation)
        second = build_dashboard_approval_projection(compilation)
        operations = validate_dashboard_approval_projection(
            first,
            expected_preread_sha256=compilation.preread_sha256,
            expected_patch_sha256=compilation.canonical_patch_sha256,
            expected_resulting_sha256=compilation.resulting_sha256,
        )
        self.assertEqual(first, second)
        self.assertTrue(first["complete"])
        self.assertEqual(len(operations), 3)
        self.assertEqual(
            operations[1]["proposed"]["value"]["entities"],
            ["device_tracker.one", "device_tracker.two"],
        )
        self.assertEqual(operations[2]["proposed"], {"state": "absent"})

    def test_complete_values_are_html_escaped_and_inert(self):
        compilation = compile_dashboard_patch(
            {"cards": []},
            [
                {
                    "operation_id": "append-card",
                    "operation": "add",
                    "path": "/cards/-",
                    "value": {
                        "type": "markdown",
                        "content": "<script>window.evil=true</script>",
                        "nested": ["one", {"complete": True}],
                    },
                }
            ],
        )
        projection = build_dashboard_approval_projection(compilation)
        html = _render_review(
            "",
            {
                "operation": "update_dashboard",
                "target_type": "dashboard",
                "target_id": "synthetic-dashboard",
                "dashboard_review": {"approval_projection": projection},
            },
            "csrf",
        )
        self.assertIn("Complete declared dashboard changes", html)
        self.assertIn("&lt;script&gt;window.evil=true&lt;/script&gt;", html)
        self.assertNotIn("<script>window.evil=true</script>", html)
        self.assertIn("&quot;complete&quot;: true", html)
        self.assertIn("Approve exact plan", html)

    def test_numeric_array_insertion_projects_complete_displaced_suffix(self):
        compilation = compile_dashboard_patch(
            {"items": ["existing", {"nested": [1, 2]}]},
            [
                {
                    "operation_id": "insert-first",
                    "operation": "add",
                    "path": "/items/0",
                    "value": "inserted",
                }
            ],
        )
        projection = build_dashboard_approval_projection(compilation)
        operation = projection["operations"][0]

        self.assertEqual(
            operation["previous"],
            {"state": "value", "value": ["existing", {"nested": [1, 2]}]},
        )
        self.assertEqual(
            operation["proposed"],
            {
                "state": "value",
                "value": ["inserted", "existing", {"nested": [1, 2]}],
            },
        )
        self.assertEqual(
            validate_dashboard_approval_projection(
                projection,
                expected_preread_sha256=compilation.preread_sha256,
                expected_patch_sha256=compilation.canonical_patch_sha256,
                expected_resulting_sha256=compilation.resulting_sha256,
            ),
            (operation,),
        )

    def test_projection_tamper_or_incompleteness_disables_approval(self):
        projection = build_dashboard_approval_projection(self._compilation())
        for mutation in ("value", "missing"):
            with self.subTest(mutation=mutation):
                malformed = deepcopy(projection)
                if mutation == "value":
                    malformed["operations"][0]["proposed"]["value"] = "tampered"
                else:
                    malformed["operations"][0].pop("proposed")
                with self.assertRaises(ApprovalProjectionError):
                    validate_dashboard_approval_projection(malformed)
                html = _render_review(
                    "",
                    {
                        "operation": "update_dashboard",
                        "dashboard_review": {"approval_projection": malformed},
                    },
                    "csrf",
                )
                self.assertIn("Approval is disabled", html)
                self.assertNotIn("Approve exact plan", html)

    def test_protected_content_and_overflow_fail_during_planning(self):
        protected = compile_dashboard_patch(
            {"cards": []},
            [
                {
                    "operation_id": "append-card",
                    "operation": "add",
                    "path": "/cards/-",
                    "value": {"content": "super-secret"},
                }
            ],
        )
        with self.assertRaises(ApprovalProjectionError) as secret:
            build_dashboard_approval_projection(
                protected, known_secrets=("super-secret",)
            )
        self.assertEqual(
            secret.exception.reason,
            "approval_projection_contains_protected_data",
        )

        with patch(
            "ha_mcp_engineering.f3_dashboard.approval_projection."
            "MAX_DASHBOARD_APPROVAL_PROJECTION_BYTES",
            128,
        ), self.assertRaises(ApprovalProjectionError) as overflow:
            build_dashboard_approval_projection(self._compilation())
        self.assertEqual(overflow.exception.reason, "approval_projection_too_large")
        self.assertGreater(overflow.exception.observed, overflow.exception.limit)


if __name__ == "__main__":
    unittest.main()
