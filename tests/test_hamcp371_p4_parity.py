"""HAMCP-371 P4: offline parity of the committed SR-b launch 2 receipts."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from tests.support.process_isolation import Guard, restore_after_class

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs/evidence/hamcp371/sr/p4_parity.py"
SPEC = importlib.util.spec_from_file_location("hamcp371_p4_parity", SCRIPT)
p4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p4)


# HAMCP-371 (ruling 2026-11-16 08:18, T1 item 3): every gateway this module builds
# publishes the capability catalog; each class and test restores what it changed,
# and the module proves the process-wide state after it equals the state before.
_BEFORE = None


def setUpModule():
    global _BEFORE
    _BEFORE = Guard().capture()


def tearDownModule():
    changes = Guard.changes(_BEFORE)
    if changes:
        raise AssertionError(f"{__name__} left process-wide state: " + "; ".join(changes))


class P4ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        restore_after_class(cls)
        cls.report = p4.build(ROOT)

    def setUp(self):
        self.addCleanup(Guard.restore, Guard().capture())

    def test_committed_receipts_have_every_difference_explained(self):
        self.assertEqual(self.report["result"], "all_explained", self.report["unexplained"])
        self.assertEqual(self.report["flagged"], ["r08-groups", "r10-template", "r18-skill-guide",
                                                  "r20-system-health", "r26-integrations"])
        self.assertEqual(len(self.report["reads"]), 26)

    def test_health_record_agrees_with_p1_and_the_lane_fingerprint(self):
        health = self.report["health_9_2_0"]
        self.assertEqual(set(health["exposed_counts"].values()), {28})
        self.assertTrue(health["agrees_with_p1"])
        for setup in p4.SETUPS:
            self.assertTrue(health["recovered_names"][setup]["fingerprint_matches_lane"])
            self.assertEqual(health["recovered_names"][setup]["peer_tool_calls"], 0)

    def test_rebuild_counts_equal_engineerings_withheld_names(self):
        rows = {row["id"]: row for row in self.report["reads"]}
        for read_id, count in (("r20-system-health", 18), ("r26-integrations", 13)):
            upstream = rows[read_id]["upstream"]["9.2.0"]["outside_rebuild_contract"]
            self.assertEqual(len(upstream), count)
            self.assertEqual(rows[read_id]["setups"]["9.2.0"]["fields_withheld"], count)

    def test_a_flag_without_a_citation_is_unexplained(self):
        with mock.patch.dict(p4.EXPLAINED, clear=True):
            report = p4.build(ROOT)
        self.assertEqual(report["result"], "unexplained_differences")
        self.assertEqual(report["unexplained"], self.report["flagged"])

    def test_a_citation_fails_when_its_upstream_condition_does_not_hold(self):
        rules = deepcopy(p4.EXPLAINED)
        rules["r10-template"]["upstream_absent"] = ("result",)
        with mock.patch.dict(p4.EXPLAINED, rules, clear=True):
            report = p4.build(ROOT)
        row = next(item for item in report["reads"] if item["id"] == "r10-template")
        self.assertEqual(row["explanation"]["status"], "unexplained")
        self.assertEqual(row["explanation"]["failed_conditions"],
                         ["9.2.0 upstream does not send result"])

    def test_an_uncovered_path_is_unexplained(self):
        rules = deepcopy(p4.EXPLAINED)
        rules["r10-template"]["paths"] = ("data.request_id",)
        with mock.patch.dict(p4.EXPLAINED, rules, clear=True):
            report = p4.build(ROOT)
        row = next(item for item in report["reads"] if item["id"] == "r10-template")
        self.assertEqual(row["explanation"]["unexplained_paths"], ["data.evaluation_time"])

    def test_a_changed_read_file_fails_its_binding(self):
        sums = p4.run_sums(ROOT)
        sums["9.2.0/out/reads/r01-automation.json"] = "0" * 64
        with mock.patch.object(p4, "run_sums", return_value=sums):
            with self.assertRaisesRegex(p4.P4Error, "does not match"):
                p4.build(ROOT)

    def test_committed_outputs_equal_a_fresh_build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(p4.main([]), 0)


if __name__ == "__main__":
    unittest.main()
