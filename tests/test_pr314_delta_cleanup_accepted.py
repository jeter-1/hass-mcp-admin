"""HAMCP-232/145 correction 2: the two owner-accepted probes of the PR314 delta suite.

Synthetic clients only; no live Home Assistant.

The reviewer's delta cleanup probes (``test_pr314_delta_cleanup``) are kept
byte for byte. Two of them, ``Cleanup.test_lost_planning_open_is_cleaned_after_restart``
and ``Cleanup.test_lost_dispatch_open_is_cleaned_after_restart``, require an
options flow whose open response was lost to be gone after a restart and three
sweeps. Pinned Core 2026.10.1 has no read-only command that lists in-progress
options flows (``flow/progress`` and ``flow/subscribe`` cover config flows
only), so Engineering cannot learn a flow id it never received. The owner
accepted that residual (correction 2, determination (b)): the flow is reported
as possibly left open on the entry, it holds no configuration change, and Home
Assistant discards it at its next restart. Those two probes are registered
below as expected failures: they still run, still have to fail at the
surviving-flow assertion, and turn the build red if the residual disappears
unnoticed.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import sys
import unittest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS.parent / "hass_mcp_engineering_beta"))

import test_pr314_delta_cleanup as reviewer  # noqa: E402

# Owner decision, HAMCP-232/145 correction 2 (b) (recorded in the PR body and
# the acceptance document): accepted residual, so expected to fail.
# The classes are never bound at module level here, so discovery does not
# collect the reviewer's probes a second time from this module.
ACCEPTED_CLASS = "Cleanup"
ACCEPTED = (
    "test_lost_dispatch_open_is_cleaned_after_restart",
    "test_lost_planning_open_is_cleaned_after_restart",
)
for _name in ACCEPTED:
    _method = getattr(getattr(reviewer, ACCEPTED_CLASS), _name)
    if not getattr(_method, "__unittest_expecting_failure__", False):
        setattr(getattr(reviewer, ACCEPTED_CLASS), _name, unittest.expectedFailure(_method))

REVIEWER_SUITE = "test_pr314_delta_cleanup.py"
REVIEWER_SUITE_SHA256 = "e29c568e691e5634cfbd98266cabc5dce7f7fb32cd58e377991e7aaf2a78cfd8"


def reviewer_cases() -> list[type[unittest.TestCase]]:
    return [value for _name, value in inspect.getmembers(reviewer, inspect.isclass)
            if issubclass(value, unittest.TestCase) and value.__module__ == reviewer.__name__]


class DeltaProbeSuiteTests(unittest.TestCase):
    def test_reviewer_suite_is_unchanged(self):
        self.assertEqual(hashlib.sha256((TESTS / REVIEWER_SUITE).read_bytes()).hexdigest(),
                         REVIEWER_SUITE_SHA256)

    def test_only_the_lost_open_probes_are_expected_failures(self):
        cases = reviewer_cases()
        self.assertEqual(sorted(case.__name__ for case in cases), ["Admission", "Cleanup"])
        expecting = []
        for case in cases:
            for name in dir(case):
                if name.startswith("test") and getattr(getattr(case, name),
                                                       "__unittest_expecting_failure__", False):
                    expecting.append((case.__name__, name))
        self.assertEqual(sorted(expecting), [(ACCEPTED_CLASS, name) for name in ACCEPTED])

    def test_the_lost_open_probes_still_run_and_fail_at_the_surviving_flow(self):
        for name in ACCEPTED:
            with self.subTest(name):
                result = unittest.TestResult()
                getattr(reviewer, ACCEPTED_CLASS)(name).run(result)
                self.assertEqual((len(result.expectedFailures), result.unexpectedSuccesses,
                                  result.failures, result.errors), (1, [], [], []))
                # They fail only because the unknown flow is still open after
                # recovery (the accepted residual), not for an unrelated reason.
                traceback = result.expectedFailures[0][1]
                self.assertIn("AssertionError", traceback)
                self.assertIn("Open flow survives", traceback)
                self.assertIn("recover_and_assert_empty", traceback)

    def test_the_known_flow_probe_passes(self):
        result = unittest.TestResult()
        reviewer.Cleanup("test_known_flow_failed_abort_is_retried_after_restart").run(result)
        self.assertEqual((result.testsRun, result.expectedFailures, result.failures, result.errors),
                         (1, [], [], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
