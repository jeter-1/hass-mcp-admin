"""HAMCP-208 review 1 M2: the reviewer's 400 ms event-loop stall probe.

``review_probes/hamcp208_219_review1/timing_block_probe.py`` is the reviewer's
probe byte for byte (its digest is pinned below); unittest discovery does not
collect it. It wraps the measured call of three responsiveness tests with one
400 ms ``time.sleep`` on the loop thread, which costs no CPU time, runs the
unchanged test body and exits 1 only when that test rejects the stall. A CPU
time bound alone lets the stall pass; the loop-gap heartbeat must catch it.
"""
import hashlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "review_probes" / "hamcp208_219_review1" / "timing_block_probe.py"
# sha256 of timing_block_probe.py in the review 1 bundle (bundle-sha256.json).
PROBE_SHA256 = "f5343d45c2003a6fcd880771ea9cb3ee5c4a7b22c6731f57ecfbd15f3bf12c2e"
# The reviewer's import path (.:tests:hass_mcp_engineering_beta). -I ignores
# PYTHONPATH and inherited startup settings, so the fresh child receives the
# path explicitly and puts it first, as the existing child-run tests do.
PATH = [str(ROOT), str(ROOT / "tests"), str(ROOT / "hass_mcp_engineering_beta")]
RUN_PROBE = ("import runpy, sys; sys.path[0:0] = sys.argv[1:4]; sys.argv[:] = sys.argv[4:]; "
             "runpy.run_path(sys.argv[0], run_name='__main__')")
RUN_TESTS = "import sys, unittest; sys.path[0:0] = sys.argv[1:4]; del sys.argv[1:4]; unittest.main(module=None)"
MEASURED = {
    "alarmo": "test_alarmo_inspection_pagination.PaginationTests."
              "_maximum_report_fitting_is_responsive_and_preserves_counts",
    "baseline": "test_automation_baseline_capture.CaptureTests._maximum_capture_bounded_pages_responsive",
    "continuation": "test_rc2dev4_release_hardening.DependencyBakeAcceptanceTests."
                    "test_cursor_continuation_is_under_100ms_and_never_rebuilds",
}
VERDICT = re.compile(r"^INJECTED_LOOP_BLOCK_SECONDS \[([^\]]*)\] MUTATION_SURVIVED (True|False)$", re.M)


def child(code, *args):
    return subprocess.run([sys.executable, "-I", "-B", "-c", code, *PATH, *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=120, check=False)


def output(result):
    # Child output for failure messages, without a "Ran N tests" line that
    # Full/Evidence's test-count parser could mistake for the outer summary.
    text = result.stdout + result.stderr
    return re.sub(r"^Ran ", "Isolated child test summary: ", text, flags=re.M)[-6000:]


class TimingBlockProbeTests(unittest.TestCase):
    def test_probe_is_the_reviewers_file_byte_for_byte(self):
        self.assertEqual(hashlib.sha256(PROBE.read_bytes()).hexdigest(), PROBE_SHA256)

    def test_probe_is_not_collected_by_discovery(self):
        self.assertIsNone(re.fullmatch(r"test.*\.py", PROBE.name))

    def assert_stall_rejected(self, mode):
        result = child(RUN_PROBE, str(PROBE), mode)
        verdict = VERDICT.findall(result.stdout)
        self.assertEqual(len(verdict), 1, output(result))
        injected = [float(value) for value in verdict[0][0].split(",") if value.strip()]
        self.assertEqual(len(injected), 1, output(result))
        self.assertGreaterEqual(injected[0], .4, output(result))
        self.assertEqual(verdict[0][1], "False", "The 400 ms loop stall survived:\n" + output(result))
        self.assertEqual(result.returncode, 1, output(result))

    def test_alarmo_projection_stall_is_rejected(self):
        self.assert_stall_rejected("alarmo")

    def test_baseline_capture_stall_is_rejected(self):
        self.assert_stall_rejected("baseline")

    def test_cursor_continuation_stall_is_rejected(self):
        self.assert_stall_rejected("continuation")

    def test_unmodified_measured_bodies_pass_in_the_same_child_setup(self):
        # Control: the same isolated child and import path, with no injection.
        result = child(RUN_TESTS, *MEASURED.values())
        self.assertEqual(result.returncode, 0, output(result))
        self.assertRegex(result.stderr, r"(?m)^Ran 3 tests in ")


if __name__ == "__main__":
    unittest.main()
