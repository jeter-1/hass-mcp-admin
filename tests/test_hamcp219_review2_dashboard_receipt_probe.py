"""HAMCP-219 review 2 (M3): the reviewer's dashboard receipt probe.

``review_probes/hamcp208_219_review2/dashboard_receipt_probe.py`` is the
reviewer's probe byte for byte (its digest is pinned below); unittest discovery
does not collect it. It executes the driver's real ``call``/``verify_case``
bodies with synthetic observations and, during the rerun, deletes or alters the
retained discarded window. ``call`` still returns (the probe asserts it), and
only an explicit offline replay of the saved window rejects the three altered
variants. The driver now performs that replay itself before acceptance:
``DisposableRetainedWindowReplayTests`` in ``test_dashboard_disposable_acceptance``
runs the same variants through ``verify_retained_windows`` and requires refusal.
"""
import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "review_probes" / "hamcp208_219_review2" / "dashboard_receipt_probe.py"
# sha256 of dashboard_receipt_probe.py in the review 2 bundle.
PROBE_SHA256 = "95b38abc72d52665a3f5e2a42ba7c9fb0d98dee145a61bee81f93cb6a539f4a1"
# The reviewer's import path (.:tests:hass_mcp_engineering_beta) and argument (the repository root).
PATH = [str(ROOT), str(ROOT / "tests"), str(ROOT / "hass_mcp_engineering_beta")]
RUN_PROBE = ("import runpy, sys; sys.path[0:0] = sys.argv[1:4]; sys.argv[:] = sys.argv[4:]; "
             "runpy.run_path(sys.argv[0], run_name='__main__')")


def output(result):
    text = result.stdout + result.stderr
    return re.sub(r"^Ran ", "Isolated child test summary: ", text, flags=re.M)[-6000:]


class ReviewerDashboardReceiptProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = subprocess.run([sys.executable, "-I", "-B", "-c", RUN_PROBE, *PATH, str(PROBE), str(ROOT)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)

    def test_probe_is_the_reviewers_file_byte_for_byte(self):
        self.assertEqual(hashlib.sha256(PROBE.read_bytes()).hexdigest(), PROBE_SHA256)

    def test_probe_is_not_collected_by_discovery(self):
        self.assertIsNone(re.fullmatch(r"test.*\.py", PROBE.name))

    def test_probe_passes_unchanged_and_the_saved_evidence_replay_rejects_every_altered_variant(self):
        self.assertEqual(self.result.returncode, 0, output(self.result))
        reports = [json.loads(line) for line in self.result.stdout.splitlines()]
        self.assertEqual(reports, [
            {"variant": variant, "production_call_accepted": True, "runs": 2,
             "explicit_offline_replay_rejects": variant != "control"}
            for variant in ("control", "missing_observation", "illegal_event", "illegal_wire")], output(self.result))


if __name__ == "__main__":
    unittest.main()
