"""Beta 35 approval-page theme and authority-boundary regressions."""

from __future__ import annotations

import re
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.approval_web import (  # noqa: E402
    _page,
    _render_review,
    create_approval_application,
)


_VARIABLE = re.compile(r"(--[a-z-]+):\s*(#[0-9a-f]{6});")


def _luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]

    def linear(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(value) for value in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: str, second: str) -> float:
    lighter, darker = sorted(
        (_luminance(first), _luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _review_fixture() -> dict[str, object]:
    return {
        "title": "Beta 35 approval theme",
        "description": "Presentation-only fixture",
        "plan_id": "beta35-plan",
        "plan_hash": "a" * 64,
        "plan_version": 1,
        "approval_kind": "apply",
        "approval_action": "plan_approval",
        "operation": "update_configuration",
        "target_type": "automation",
        "target_id": "automation.beta35_fixture",
        "risk_level": "moderate",
        "policy_class": "standard_admin",
        "risk_delta": "unchanged",
        "physical_consequence": "none",
        "policy_reason_codes": [],
        "policy_decision_hash": "b" * 64,
        "approval_bundle_state": "pending_plan_approval",
        "same_principal_requirement": False,
        "expires_at": "2026-08-12T22:00:00Z",
        "challenge_expires_at": "2026-08-12T21:00:00Z",
        "request_note": "Review requested",
        "validation_valid": True,
        "apply_allowed": False,
        "approval_state": "external_pending",
        "challenge_id": "beta35-challenge",
        "changed_fields": [
            {"field": "description", "before": "Before", "after": "After"}
        ],
        "warnings": ["Review this bounded warning."],
    }


class ApprovalThemeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = _page(
            "Approval theme",
            '<p class="danger">Bounded warning</p><pre><code>diff</code></pre>',
            prefix="/api/hassio_ingress/testtoken123",
        )
        self.styles = re.search(
            r"<style>(.*?)</style>", self.page, re.DOTALL
        ).group(1)

    def _palettes(self) -> tuple[dict[str, str], dict[str, str]]:
        dark_match = re.search(
            r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root\s*\{(.*?)\}\s*\}",
            self.styles,
            re.DOTALL,
        )
        self.assertIsNotNone(dark_match)
        light_block = self.styles[: dark_match.start()]
        return dict(_VARIABLE.findall(light_block)), dict(
            _VARIABLE.findall(dark_match.group(1))
        )

    def test_page_declares_system_light_and_dark_color_scheme(self):
        self.assertIn('<meta name="color-scheme" content="light dark">', self.page)
        self.assertIn("color-scheme: light dark", self.styles)
        self.assertIn("@media (prefers-color-scheme: dark)", self.styles)
        self.assertNotIn("<script", self.page)
        self.assertNotIn("http://", self.styles)
        self.assertNotIn("https://", self.styles)

    def test_light_and_dark_palettes_cover_review_surfaces(self):
        light, dark = self._palettes()
        expected = {
            "--approval-page-background",
            "--approval-surface",
            "--approval-surface-muted",
            "--approval-text",
            "--approval-muted-text",
            "--approval-border",
            "--approval-link",
            "--approval-link-hover",
            "--approval-primary-background",
            "--approval-primary-text",
            "--approval-primary-hover",
            "--approval-reject-background",
            "--approval-reject-text",
            "--approval-reject-hover",
            "--approval-danger-text",
            "--approval-danger-surface",
            "--approval-danger-border",
            "--approval-focus",
            "--approval-disabled-background",
            "--approval-disabled-text",
        }
        self.assertEqual(set(light), expected)
        self.assertEqual(set(dark), expected)
        self.assertTrue(all(light[name] != dark[name] for name in expected))
        for selector in (
            "html {",
            "body {",
            ".muted, small {",
            "nav {",
            "table {",
            "th, td {",
            "code {",
            "pre {",
            "details {",
            "summary {",
            "button {",
            "button:hover {",
            "button:active {",
            "button.danger {",
            "button.danger:hover {",
            "button:disabled,",
            "a:focus-visible,",
            ".danger {",
            "p.danger {",
        ):
            self.assertIn(selector, self.styles)

    def test_text_control_warning_and_focus_contrast_is_bounded(self):
        light, dark = self._palettes()
        text_pairs = (
            ("--approval-text", "--approval-page-background"),
            ("--approval-text", "--approval-surface"),
            ("--approval-muted-text", "--approval-page-background"),
            ("--approval-link", "--approval-page-background"),
            ("--approval-primary-text", "--approval-primary-background"),
            ("--approval-reject-text", "--approval-reject-background"),
            ("--approval-danger-text", "--approval-danger-surface"),
            ("--approval-disabled-text", "--approval-disabled-background"),
        )
        non_text_pairs = (
            ("--approval-border", "--approval-surface"),
            ("--approval-focus", "--approval-surface"),
        )
        for palette in (light, dark):
            for foreground, background in text_pairs:
                self.assertGreaterEqual(
                    _contrast(palette[foreground], palette[background]),
                    4.5,
                    f"{foreground} on {background}",
                )
            for foreground, background in non_text_pairs:
                self.assertGreaterEqual(
                    _contrast(palette[foreground], palette[background]),
                    3.0,
                    f"{foreground} on {background}",
                )

    def test_review_routes_forms_and_hidden_authority_fields_are_unchanged(self):
        app = create_approval_application(object())
        routes = {
            route.path: frozenset(route.methods or ())
            for route in app.routes
        }
        self.assertEqual(
            routes,
            {
                "/": frozenset({"GET", "HEAD"}),
                "/plans/{plan_id}": frozenset({"GET", "HEAD"}),
                "/plans/{plan_id}/approve": frozenset({"POST"}),
                "/plans/{plan_id}/reject": frozenset({"POST"}),
                "/f3": frozenset({"GET", "HEAD"}),
                "/f3/{child_id}": frozenset({"GET", "HEAD"}),
                "/f3/{child_id}/reconcile": frozenset({"POST"}),
            },
        )

        html = _render_review(
            "/api/hassio_ingress/testtoken123",
            _review_fixture(),
            "beta35-csrf",
        )
        forms = re.findall(
            r'<form method="([^"]+)" action="([^"]+)">(.*?)</form>',
            html,
            re.DOTALL,
        )
        self.assertEqual(
            [(method, action) for method, action, _ in forms],
            [
                (
                    "post",
                    "/api/hassio_ingress/testtoken123/plans/beta35-plan/approve",
                ),
                (
                    "post",
                    "/api/hassio_ingress/testtoken123/plans/beta35-plan/reject",
                ),
            ],
        )
        expected_hidden = {
            "challenge_id": "beta35-challenge",
            "plan_hash": "a" * 64,
            "approval_kind": "apply",
            "approval_action": "plan_approval",
            "csrf": "beta35-csrf",
        }
        for _method, _action, form in forms:
            self.assertEqual(
                dict(
                    re.findall(
                        r'<input type="hidden" name="([^"]+)" value="([^"]*)">',
                        form,
                    )
                ),
                expected_hidden,
            )
        self.assertNotIn("onclick=", html)
        self.assertNotIn("formaction=", html)


if __name__ == "__main__":
    unittest.main()
