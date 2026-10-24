"""HAMCP-219 review 1 H1/M1: the reviewer's observer harness probes.

``review_probes/hamcp208_219_review1/reviewer_harness_probes.py`` is the
reviewer's probe byte for byte (its digest is pinned below); unittest
discovery does not collect it. It drives the real observer wrappers through
Core's executor and thread-safe callback round trip (H1), and records standalone
repairs ``storage_save``/``storage_write`` events (M1). Both observers must
refuse every case under strict verification; a window is retried only by the
acceptance driver, and only once, never by tolerating the store.
"""
import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "review_probes" / "hamcp208_219_review1" / "reviewer_harness_probes.py"
# sha256 of reviewer_harness_probes.py in the review 1 bundle.
PROBE_SHA256 = "d0f4bb198d007a5ebceab61f790b1d9dac4f13128b97c105a51bbe673068be3c"
# The reviewer's import path (.:tests:hass_mcp_engineering_beta), with the
# repository root as the working directory the probe resolves fixtures from.
PATH = [str(ROOT), str(ROOT / "tests"), str(ROOT / "hass_mcp_engineering_beta")]
RUN_PROBE = ("import runpy, sys; sys.path[0:0] = sys.argv[1:4]; sys.argv[:] = sys.argv[4:]; "
             "runpy.run_path(sys.argv[0], run_name='__main__')")


def output(result):
    # Child output for failure messages, without a "Ran N tests" line that
    # Full/Evidence's test-count parser could mistake for the outer summary.
    text = result.stdout + result.stderr
    return re.sub(r"^Ran ", "Isolated child test summary: ", text, flags=re.M)[-6000:]


class ReviewerHarnessProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = subprocess.run([sys.executable, "-I", "-B", "-c", RUN_PROBE, *PATH, str(PROBE)], cwd=ROOT,
                                    capture_output=True, text=True, timeout=120, check=False)

    def test_probe_is_the_reviewers_file_byte_for_byte(self):
        self.assertEqual(hashlib.sha256(PROBE.read_bytes()).hexdigest(), PROBE_SHA256)

    def test_probe_is_not_collected_by_discovery(self):
        self.assertIsNone(re.fullmatch(r"test.*\.py", PROBE.name))

    def test_both_reviewer_probes_pass_unchanged(self):
        self.assertEqual(self.result.returncode, 0, output(self.result))
        self.assertRegex(self.result.stderr, r"(?m)^Ran 2 tests in ", output(self.result))
        self.assertRegex(self.result.stderr, r"(?m)^OK$", output(self.result))
        for name in ("test_command_executor_roundtrip_must_not_pass_as_background",
                     "test_non_delayed_repairs_save_kinds_must_be_refused"):
            self.assertRegex(self.result.stderr, r"(?m)^%s \(.*\) \.\.\. ok$" % name, output(self.result))

    def test_command_caused_write_really_happened_and_both_observers_refused_it(self):
        reports = [json.loads(line) for line in self.result.stdout.splitlines() if line.startswith("{")]
        self.assertEqual(sorted(report["observer"] for report in reports), ["alarmo", "dashboard"],
                         output(self.result))
        for report in reports:
            with self.subTest(observer=report["observer"]):
                self.assertIs(report["command_caused_file_write"], True)
                self.assertIs(report["accepted"], False)
                # The context loss the probe exercises: no event carries command origin.
                self.assertIn({"kind": "storage_delay_save", "origin": "background",
                               "store": "repairs.issue_registry"}, report["events"])


if __name__ == "__main__":
    unittest.main()
