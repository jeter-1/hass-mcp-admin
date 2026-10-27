"""HAMCP-232/145 review 1: the one superseded method of the reviewer's PR314 suite.

Synthetic clients only; no live Home Assistant.

The reviewer's suite (``test_pr314_independent_review``) is kept byte for
byte. One of its methods, ``FlowReview.test_partial_internal_intent_never_submitted``,
expects an incomplete internal options intent to pass create and approval and
then never be submitted at apply. The owner decided otherwise (review 1 H3):
an incomplete intent is refused at create, before any options flow opens or
any apply runs, which is the stricter behavior. That method therefore errors
at its create call, so it is registered below as an expected failure: it
still runs, still has to fail, and turns the build red if create ever accepts
an incomplete intent again.
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
# reviewer delta brief): superseded, so expected to fail.
# The class is never bound at module level here, so discovery does not collect
# the reviewer's FlowReview a second time from this module.
SUPERSEDED_CLASS = "FlowReview"
SUPERSEDED = "test_partial_internal_intent_never_submitted"
_method = getattr(getattr(reviewer, SUPERSEDED_CLASS), SUPERSEDED)
if not getattr(_method, "__unittest_expecting_failure__", False):
    setattr(getattr(reviewer, SUPERSEDED_CLASS), SUPERSEDED, unittest.expectedFailure(_method))

REVIEWER_SUITE = "test_pr314_independent_review.py"
REVIEWER_SUITE_SHA256 = "b5f8dc94ea9114518f2fedcd44ad303764436b8f038b5f61dd5ab375e41b341b"


def reviewer_cases() -> list[type[unittest.TestCase]]:
    return [value for _name, value in inspect.getmembers(reviewer, inspect.isclass)
            if issubclass(value, unittest.TestCase) and value.__module__ == reviewer.__name__]


class ReviewerSuiteTests(unittest.TestCase):
    def test_reviewer_suite_is_unchanged(self):
        self.assertEqual(hashlib.sha256((TESTS / REVIEWER_SUITE).read_bytes()).hexdigest(),
                         REVIEWER_SUITE_SHA256)

    def test_only_the_superseded_method_is_an_expected_failure(self):
        cases = reviewer_cases()
        self.assertEqual(sorted(case.__name__ for case in cases),
                         ["FlowReview", "IntentReview", "TraceReview", "TriggerReview"])
        expecting = []
        for case in cases:
            for name in dir(case):
                if name.startswith("test") and getattr(getattr(case, name),
                                                       "__unittest_expecting_failure__", False):
                    expecting.append((case.__name__, name))
        self.assertEqual(expecting, [(SUPERSEDED_CLASS, SUPERSEDED)])

    def test_the_superseded_method_still_runs_and_fails_at_create(self):
        result = unittest.TestResult()
        getattr(reviewer, SUPERSEDED_CLASS)(SUPERSEDED).run(result)
        self.assertEqual((len(result.expectedFailures), result.unexpectedSuccesses, result.failures, result.errors),
                         (1, [], [], []))
        # It fails because create refuses the incomplete intent (the owner's
        # stricter behavior), not for an unrelated reason.
        traceback = result.expectedFailures[0][1]
        self.assertIn("GovernanceError", traceback)
        self.assertIn("create_options", traceback)


if __name__ == "__main__":
    unittest.main(verbosity=2)
