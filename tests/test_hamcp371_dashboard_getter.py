"""HAMCP-371 S10 (N30): the 9.2.0 dashboard getter's counts and warning rule.

``upstream_reads_9_2_dashboard.apply`` is reached from one 9.2.0 gate in the
shared ``upstream_dashboard.py`` getter. These tests cover:

* the module itself: the reviewed get-mode key set, the four reviewed warning
  templates (and near misses), per-occurrence replacement, the provider's own
  partial sentence kept last and uncounted, and the 999 cap;
* the gate's wiring through the real getter, the real pinned MCP
  ClientSession and the synthetic 9.1 peer of ``test_hamcp319_dashboard``.
  Before SB the 9.2.0 getter was not reachable end-to-end, so the first
  wiring tests patch the module's ``RELEASES`` to the peer's 9.1.2 (the call
  site). Since SB (reviewed 9.2.0 attestation, 9.2.0 in
  ``EXACT_GETTER_RELEASES``) a 9.2.0 peer reaches the gate unpatched: the
  peer serves the committed 9.2.0 catalog and the 9.1 reply shape, which the
  byte-identical 9.2.0 getter descriptor (S4 diff) allows;
* the gate is keyed on the module's ``RELEASES``: with 9.2.0 taken out of
  it, ``apply`` is never called and the injected payload returns no count
  fields. HAMCP-437: only 9.2.0 is admitted (D1), so the 9.1.2-patched wiring
  cases now run on the 9.2.0 peer and the 8.5.0/9.1.x comparison is retired.

Synthetic values only; no endpoint is contacted.
"""
from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from ha_mcp_engineering.providers import upstream_dashboard  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2_dashboard as dash_9_2  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2_keys as keys  # noqa: E402
import test_hamcp319_dashboard as dash  # noqa: E402

STRATEGY = ("Strategy dashboard views are generated at runtime; only the dashboard "
            "base render path is available.")
FALLBACK = ("2 dashboard view(s) have no usable stable path; their render paths "
            "use fragile numeric indexes.")
AMBIGUOUS = ("3 dashboard view(s) share a duplicate configured path; numeric render "
             "paths are returned for those ambiguous views.")
HINT = ("Large config (12,345 bytes). Pass view_path=... to fetch a single view, or "
        "use ha_config_get_dashboard(entity_id=...) to find card positions, then "
        "ha_config_set_dashboard(python_transform=...) instead of full config "
        "replacement.")
OWN = "Dashboard content was sanitized or bounded and is not complete."
COUNTS = ("fields_withheld", "fields_dropped_by_policy", "upstream_warnings_replaced")


def _apply(payload, warnings, completeness="complete"):
    data, metadata = {"url_path": "operations"}, {"completeness": completeness}
    out, state = dash_9_2.apply(payload, data, metadata, list(warnings), completeness)
    return data, metadata, out, state


class Module(unittest.TestCase):

    def test_releases_and_row(self):
        self.assertEqual(dash_9_2.RELEASES, frozenset({"9.2.0"}))
        self.assertEqual(dash_9_2.ROW.admitted, frozenset({
            "success", "action", "url_path", "config", "config_hash",
            "config_size_bytes", "render_paths", "hint", "resolved_from",
            "stored_config", "message", "warnings", "view_index", "view_path",
            "title", "render_path", "stable", "strategy_dashboard",
            "ambiguous_path", "invalid_path"}))
        self.assertIn("config", dash_9_2.ROW.opened)
        self.assertFalse(dash_9_2.ROW.drops)

    def test_reviewed_templates_pass(self):
        texts = [STRATEGY, FALLBACK, AMBIGUOUS]
        data, metadata, out, state = _apply({"warnings": texts, "hint": HINT}, texts + [HINT])
        self.assertEqual(out, texts + [HINT])
        self.assertEqual(state, "complete")
        self.assertEqual(metadata["completeness"], "complete")
        self.assertEqual({k: data[k] for k in COUNTS}, dict.fromkeys(COUNTS, 0))

    def test_near_misses_are_replaced(self):
        misses = [
            STRATEGY + " ",
            "x " + STRATEGY,
            FALLBACK.replace("2 ", "0 ", 1),
            FALLBACK.replace("2 ", "02 ", 1),
            AMBIGUOUS.replace("3 ", "three ", 1),
            HINT.replace("12,345", "12345"),
            HINT.replace("12,345", "999"),
            HINT + " Then call ha_config_set_dashboard.",
            "hamcp371 synthetic unreviewed warning",
        ]
        for text in misses:
            with self.subTest(text=text):
                data, metadata, out, state = _apply({"warnings": [text]}, [text])
                self.assertEqual(out, [keys.REPLACED_WARNING, keys.REPLACED_PARTIAL])
                self.assertEqual(data["upstream_warnings_replaced"], 1)
                self.assertEqual(state, "partial")
                self.assertEqual(metadata["completeness"], "partial")

    def test_replaced_per_occurrence_with_bound_and_non_strings(self):
        # The provider bounds the list to 20 and drops values it cannot render;
        # every upstream value not passed through is counted.
        raw = [FALLBACK] + ["hamcp371 x"] * 24 + [7]
        sanitized = [FALLBACK] + ["hamcp371 x"] * 19
        data, _metadata, out, _state = _apply({"warnings": raw}, sanitized)
        self.assertEqual(out, [FALLBACK, keys.REPLACED_WARNING, keys.REPLACED_PARTIAL])
        self.assertEqual(data["upstream_warnings_replaced"], 25)
        data, _m, out, _s = _apply({"warnings": "hamcp371 one string"}, ["hamcp371 one string"])
        self.assertEqual(data["upstream_warnings_replaced"], 1)
        data, _m, _o, _s = _apply({"warnings": ["hamcp371"] * 1500}, ["hamcp371"] * 20)
        self.assertEqual(data["upstream_warnings_replaced"], keys.COUNT_CAP)

    def test_own_partial_sentence_stays_last_and_uncounted(self):
        data, _m, out, state = _apply({"warnings": [STRATEGY]}, [STRATEGY, OWN], "partial")
        self.assertEqual(out, [STRATEGY, OWN])
        self.assertEqual(data["upstream_warnings_replaced"], 0)
        self.assertEqual(state, "partial")
        data, _m, out, _s = _apply(
            {"warnings": ["hamcp371 x"]}, ["hamcp371 x", OWN], "partial")
        self.assertEqual(out, [keys.REPLACED_WARNING, OWN, keys.REPLACED_PARTIAL])
        self.assertEqual(data["upstream_warnings_replaced"], 1)
        # An upstream copy of the provider's sentence is not trusted as its own.
        data, _m, out, _s = _apply({"warnings": [OWN]}, [OWN])
        self.assertEqual(out, [keys.REPLACED_WARNING, keys.REPLACED_PARTIAL])

    def test_withheld_names(self):
        payload = {
            "success": True, "config": {"hamcp371_in_config": {"deep": 1}},
            "render_paths": [{"url_path": "operations", "hamcp371_row": 1},
                             {"hamcp371_row": 2, "hamcp371_other": 3}],
            "hamcp371_top": {"hamcp371_nested": 1},
        }
        data, _m, _o, _s = _apply(payload, [])
        # hamcp371_row (distinct), hamcp371_other, hamcp371_top and the name
        # inside its withheld value; config is open and not walked.
        self.assertEqual(data["fields_withheld"], 4)
        self.assertEqual(data["fields_dropped_by_policy"], 0)
        many = {f"hamcp371_{i}": i for i in range(1500)}
        data, _m, _o, _s = _apply(many, [])
        self.assertEqual(data["fields_withheld"], keys.COUNT_CAP)


class GetterGate(dash.Harness):
    """The real getter, ClientSession and synthetic 9.1 peer."""

    def _peer(self, version, extra=None):
        peer = dash.DashboardPeer(version)
        config = deepcopy(peer.configuration)

        def get(args):
            # The shipped peer reply, then the injected keys.
            peer.get_payload = None
            try:
                payload, error = peer._get(args)
            finally:
                peer.get_payload = get
            payload = deepcopy(payload)
            payload.update(deepcopy(extra or {}))
            return payload, error

        peer.get_payload = get
        self.assertEqual(peer.configuration, config)
        return peer

    async def _get(self, peer):
        provider = self.provider(peer)
        return await provider.get_dashboard_config(
            url_path=dash.URL_PATH, force_reload=True, response_limit=60_000)

    INJECTED = {
        "hamcp371_top": {"hamcp371_secret": "synthetic-not-a-secret-371"},
        "render_paths": [{"url_path": "operations", "view_index": 0, "view_path": None,
                          "title": "Home", "render_path": "operations/0", "stable": False,
                          "hamcp371_row": True}],
        "warnings": [FALLBACK, "hamcp371 unreviewed upstream warning"],
        "hint": HINT,
    }

    async def test_gate_adds_counts_and_rule_through_the_getter(self):
        got = await self._get(self._peer("9.2.0"))
        self.assertEqual({k: got.data[k] for k in COUNTS}, dict.fromkeys(COUNTS, 0))
        self.assertEqual(got.warnings, [])
        self.assertEqual(got.completeness, "complete")
        got = await self._get(self._peer("9.2.0", self.INJECTED))
        self.assertEqual(got.data["fields_withheld"], 3)
        self.assertEqual(got.data["fields_dropped_by_policy"], 0)
        self.assertEqual(got.data["upstream_warnings_replaced"], 1)
        self.assertEqual(got.warnings, [FALLBACK, HINT, keys.REPLACED_WARNING,
                                        keys.REPLACED_PARTIAL])
        self.assertEqual(got.completeness, "partial")
        self.assertEqual(got.metadata["completeness"], "partial")
        text = json.dumps([got.data, got.warnings, got.metadata])
        for absent in ("hamcp371_top", "hamcp371_secret", "hamcp371_row",
                       "synthetic-not-a-secret-371", "hamcp371 unreviewed"):
            self.assertNotIn(absent, text)
        # The reported size includes the counts (one measure, HAMCP-306).
        self.assertEqual(
            got.data["estimated_serialized_size"],
            upstream_dashboard._estimated_configuration_response_size(
                got.data, got.warnings, got.metadata))
        self.balanced()

    async def test_920_peer_reaches_the_gate_unpatched(self):
        with patch.dict(dash.URLS, {"9.2.0": dash.STRICT_URL}), patch.object(
                dash, "NINE_ONE", (*dash.NINE_ONE, "9.2.0")):
            got = await self._get(self._peer("9.2.0"))
            self.assertEqual({k: got.data[k] for k in COUNTS}, dict.fromkeys(COUNTS, 0))
            self.assertEqual(got.completeness, "complete")
            got = await self._get(self._peer("9.2.0", self.INJECTED))
        self.assertEqual({k: got.data[k] for k in COUNTS},
                         {"fields_withheld": 3, "fields_dropped_by_policy": 0,
                          "upstream_warnings_replaced": 1})
        self.assertEqual(got.warnings, [FALLBACK, HINT, keys.REPLACED_WARNING,
                                        keys.REPLACED_PARTIAL])
        self.assertEqual(got.completeness, "partial")
        self.assertNotIn("synthetic-not-a-secret-371", json.dumps([got.data, got.warnings]))
        self.balanced()

    async def test_gate_still_fails_closed_on_size(self):
        big = {"title": "Before", "views": [{"title": "Home", "cards": [
            {"type": "markdown", "content": "x" * 1500} for _ in range(30)]}]}
        peer = self._peer("9.2.0")
        peer.configuration = big
        provider = self.provider(peer)
        with self.assertRaises(upstream_dashboard.DashboardProviderError) as raised:
            await provider.get_dashboard_config(
                url_path=dash.URL_PATH, force_reload=True, response_limit=20_000)
        self.assertEqual(raised.exception.code.value, "upstream_dashboard_response_too_large")
        self.assertIs(raised.exception.details["configuration_returned"], False)

    async def test_a_release_outside_the_module_set_never_calls_it(self):
        with patch.object(dash_9_2, "RELEASES", frozenset()), patch.object(
                dash_9_2, "apply", side_effect=AssertionError("9.2.0 only")) as spy:
            got = await self._get(self._peer("9.2.0", self.INJECTED))
            spy.assert_not_called()
        self.assertFalse(set(COUNTS) & set(got.data))
        # Without the module the upstream text passes after sanitizing.
        self.assertEqual(got.warnings, [FALLBACK, "hamcp371 unreviewed upstream warning", HINT])
        self.assertEqual(got.completeness, "complete")
        self.assertNotIn("hamcp371_top", json.dumps(got.data))


if __name__ == "__main__":
    unittest.main()
