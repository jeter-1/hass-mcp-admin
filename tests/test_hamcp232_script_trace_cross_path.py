"""HAMCP-232/145 review H1: the HAMCP-225 cross-path credential suite on the script-trace path.

Script traces have no screen of their own: ``list_script_traces`` and
``get_script_trace`` leave through the HAMCP-225 trace exit
(``trace_normalization.sanitize_trace_detail``). This module runs the
HAMCP-225 cross-path cases unchanged (``cases()`` and ``POSITIONS`` are
imported from ``test_hamcp225_cross_path_credentials``, which collects every
HAMCP-132 and HAMCP-224 reviewer credential case) through both script-trace
reads:

* ``trace/get``: every case in every trace position, as a value and as a
  dictionary key, through the service and through the registered public
  tool; and
* ``trace/list``: every case in every header field, as a value and as a
  dictionary key.

Each must be withheld and the read partial. The one exception is the same
for every trace path: the HAMCP-225 owner decision (correction batch 2)
screens Home Assistant's own identifiers (``IDENTITY_KEYS``, here a list
header's ``run_id``) in the shared screen's default mode, so a case there is
withheld exactly when that mode withholds it. A known secret in an
identifier the service returns is withheld too. Synthetic values only;
offline.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(TESTS), str(TESTS.parent / "hass_mcp_engineering_beta"), str(TESTS.parent)]

import test_hamcp225_cross_path_credentials as cross  # noqa: E402
import test_hamcp232_script_traces as traces  # noqa: E402
from ha_mcp_engineering import script_traces as st  # noqa: E402
from ha_mcp_engineering import trace_normalization  # noqa: E402
from ha_mcp_engineering.configuration_definitions import credentials  # noqa: E402
from ha_mcp_engineering.tools import script_traces as public  # noqa: E402


START = "2026-10-26T10:00:00+00:00"


def _header(**extra):
    return {**traces.short_trace(traces.RUN_ID, START), **extra}


LIST_POSITIONS = {
    "state": lambda v: _header(state=v),
    "last_step": lambda v: _header(last_step=v),
    "script_execution": lambda v: _header(script_execution=v),
    "error": lambda v: _header(error=v),
    "error_nested": lambda v: _header(error={"message": v}),
    "error_key": lambda v: _header(error={v: "benign"}),
    "extra_key": lambda v: {**_header(), v: "benign"},
    "timestamp_key": lambda v: _header(timestamp={"start": START, v: START}),
}

# Home Assistant's own identifiers: the shared screen's default mode.
IDENTITY_POSITIONS = {
    "run_id": lambda v: _header(run_id=v),
}


class ScriptTraceCrossPathTests(traces.ServiceTestCase):
    def service(self, client):
        return st.ScriptTraceService(client, self.core, known_secrets=cross.KNOWN_SECRETS)

    def test_cases_are_the_hamcp225_cross_path_cases(self):
        self.assertGreaterEqual(len(cross.cases()), 100)
        self.assertTrue(set(IDENTITY_POSITIONS) <= trace_normalization.IDENTITY_KEYS)
        self.assertFalse(set(LIST_POSITIONS) & trace_normalization.IDENTITY_KEYS)
        for positions in (cross.POSITIONS, LIST_POSITIONS):
            self.assertTrue(any(name.endswith("_key") for name in positions))
            self.assertTrue(any(not name.endswith("_key") for name in positions))

    async def test_every_case_in_every_position_is_withheld_by_get_script_trace(self):
        for name, (value, needle) in cross.cases().items():
            for position in cross.POSITIONS:
                with self.subTest(case=name, position=position):
                    client = traces.FakeClient(cross.planted(traces.RUN_ID, position, value))
                    result = await self.service(client).get_trace(traces.SCRIPT_ID, traces.RUN_ID)
                    self.assertFalse(cross.holds(result, needle))
                    self.assertEqual(result["completeness"], "partial")
                    self.assertEqual(self.telemetry.completeness, "partial")

    async def test_every_case_in_every_position_is_withheld_by_the_public_tool(self):
        for name, (value, needle) in cross.cases().items():
            for position in cross.POSITIONS:
                with self.subTest(case=name, position=position):
                    client = traces.FakeClient(cross.planted(traces.RUN_ID, position, value))
                    with patch.object(st.SCRIPT_TRACES, "service", self.service(client)):
                        rendered = await public.registered_get_tool().run(
                            {"script_id": traces.SCRIPT_ID, "run_id": traces.RUN_ID})
                    envelope = json.loads(rendered)
                    self.assertFalse(cross.holds(envelope, needle))
                    self.assertEqual(envelope["metadata"]["completeness"], "partial")

    async def test_every_case_in_every_header_field_is_withheld_by_list_script_traces(self):
        for name, (value, needle) in cross.cases().items():
            for position, build in LIST_POSITIONS.items():
                with self.subTest(case=name, position=position):
                    client = traces.FakeClient([build(value)])
                    result = await self.service(client).list_traces(traces.SCRIPT_ID)
                    self.assertFalse(cross.holds(result, needle))
                    self.assertEqual(result["completeness"], "partial")

    async def test_identifier_header_fields_follow_the_shared_default_mode(self):
        withheld = 0
        for name, (value, needle) in cross.cases().items():
            expected = credentials.screen(value, cross.KNOWN_SECRETS, labelled=False) is not None
            withheld += expected
            for position, build in IDENTITY_POSITIONS.items():
                with self.subTest(case=name, position=position):
                    client = traces.FakeClient([build(value)])
                    result = await self.service(client).list_traces(traces.SCRIPT_ID)
                    self.assertEqual(cross.holds(result, needle), not expected)
                    self.assertEqual(result["completeness"] == "partial", expected)
        self.assertGreater(withheld, len(cross.cases()) // 2)

    async def test_known_secret_identifiers_are_withheld_in_every_response(self):
        script_secret = "synthetic_known_script_key"
        run_secret = "SyntheticKnownRunIdentifier"
        service = st.ScriptTraceService(traces.FakeClient({"safe": True}), self.core,
                                        known_secrets=(script_secret, run_secret))
        result = await service.get_trace(script_secret, run_secret)
        self.assertFalse(cross.holds(result, script_secret))
        self.assertFalse(cross.holds(result, run_secret))
        self.assertEqual(result["completeness"], "partial")
        listing = st.ScriptTraceService(
            traces.FakeClient([traces.short_trace(run_secret, START, item_id=script_secret)]),
            self.core, known_secrets=(script_secret, run_secret))
        listed = await listing.list_traces(script_secret)
        self.assertFalse(cross.holds(listed, script_secret))
        self.assertFalse(cross.holds(listed, run_secret))
        self.assertEqual(listed["completeness"], "partial")

    async def test_benign_identifiers_and_text_stay_complete(self):
        detail = cross.planted(traces.RUN_ID, "variables", "Kitchen 20 °C, Küche\nordinary text")
        result = await self.service(traces.FakeClient(detail)).get_trace(
            traces.SCRIPT_ID, traces.RUN_ID)
        self.assertEqual(result["completeness"], "complete")
        self.assertEqual((result["script_id"], result["run_id"]), (traces.SCRIPT_ID, traces.RUN_ID))
        self.assertEqual(result["trace"]["config"]["variables"]["message"],
                         "Kitchen 20 °C, Küche\nordinary text")

    async def test_both_reads_use_the_one_hamcp225_exit(self):
        seen = []
        original = trace_normalization.sanitize_trace_detail

        def spy(value, **kwargs):
            seen.append(value)
            return original(value, **kwargs)

        with patch.object(st, "sanitize_trace_detail", spy):
            await self.service(traces.FakeClient({"safe": True})).get_trace(
                traces.SCRIPT_ID, traces.RUN_ID)
            await self.service(traces.FakeClient([_header()])).list_traces(traces.SCRIPT_ID)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]["script_id"], traces.SCRIPT_ID)
        self.assertEqual(seen[0]["run_id"], traces.RUN_ID)
        self.assertEqual(seen[1]["script_id"], traces.SCRIPT_ID)
        self.assertIs(st.sanitize_trace_detail, original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
