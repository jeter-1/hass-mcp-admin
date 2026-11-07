"""HAMCP-232/145 review 1: the one superseded method of the reviewer's PR314 suite.

Synthetic clients only; no live Home Assistant.

The reviewer's suite (``test_pr314_independent_review``) is pinned by hash.
One of its methods, ``FlowReview.test_partial_internal_intent_never_submitted``,
expected an incomplete internal options intent to pass create and approval and
then never be submitted at apply. The owner decided otherwise (review 1 H3):
an incomplete intent is refused at create, before any options flow opens or
any apply runs, which is the stricter behavior. It ran here as an expected
failure until HAMCP-312 rewrote it to assert that refusal, so it now passes,
and turns the build red if create ever accepts an incomplete intent again.
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

import test_pr314_independent_review as reviewer  # noqa: E402

# Owner decision, HAMCP-232/145 review 1 H3 (recorded in the PR body and the
# reviewer delta brief). HAMCP-312 rewrote this reviewer method to assert it.
# The class is never bound at module level here, so discovery does not collect
# the reviewer's FlowReview a second time from this module.
REWRITTEN_CLASS = "FlowReview"
REWRITTEN = "test_partial_internal_intent_never_submitted"

REVIEWER_SUITE = "test_pr314_independent_review.py"
REVIEWER_SUITE_SHA256 = "95fe8281faaf54540c4ac456495415d42c80b5f4227d255b3bb53b3b516be465"


def reviewer_cases() -> list[type[unittest.TestCase]]:
    return [value for _name, value in inspect.getmembers(reviewer, inspect.isclass)
            if issubclass(value, unittest.TestCase) and value.__module__ == reviewer.__name__]


class ReviewerSuiteTests(unittest.TestCase):
    def test_reviewer_suite_is_unchanged(self):
        self.assertEqual(hashlib.sha256((TESTS / REVIEWER_SUITE).read_bytes()).hexdigest(),
                         REVIEWER_SUITE_SHA256)

    def test_no_reviewer_method_is_an_expected_failure(self):
        cases = reviewer_cases()
        self.assertEqual(sorted(case.__name__ for case in cases),
                         ["FlowReview", "IntentReview", "TraceReview", "TriggerReview"])
        expecting = []
        for case in cases:
            for name in dir(case):
                if name.startswith("test") and getattr(getattr(case, name),
                                                       "__unittest_expecting_failure__", False):
                    expecting.append((case.__name__, name))
        self.assertEqual(expecting, [])

    def test_the_rewritten_method_passes_with_the_refusal_at_create(self):
        result = unittest.TestResult()
        getattr(reviewer, REWRITTEN_CLASS)(REWRITTEN).run(result)
        self.assertEqual((result.testsRun, result.expectedFailures, result.unexpectedSuccesses,
                          result.failures, result.errors, result.skipped),
                         (1, [], [], [], [], []))
        source = inspect.getsource(getattr(getattr(reviewer, REWRITTEN_CLASS), REWRITTEN))
        self.assertIn("with self.assertRaises(GovernanceError) as refused:await self.create_options", source)
        self.assertNotIn("await self.approve(created)", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
