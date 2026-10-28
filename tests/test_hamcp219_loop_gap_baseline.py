"""HAMCP-219: loop-gap bounds relative to same-child baselines.

The six event-loop responsiveness bodies bound each heartbeat gap by a fixed
allowance plus a baseline measured in the same child for that same gap
(``support.loop_gap``): the unstalled idle gap plus the scheduler run delay,
and, where collection stays enabled, the loop thread's major-collection CPU.
The baseline's contribution is capped, so every bound stays at or below
350 ms whatever the runner reports. Every body must still reject a 400 ms loop
stall, including when every baseline reports an arbitrarily noisy runner; the
HAMCP-208 review 1 probe covers three bodies byte for byte, and
``support/loop_stall_probe.py`` applies the same stall to all six.

Wall allowance + cap (ceiling): alarmo and baseline .30 + .05 (.35);
continuation .10 + .10 (.20); catalog, native and upstream .25 + .10 (.35).
Loop-thread CPU: catalog, native and upstream .10 + min(gen-2 CPU, .10)
(ceiling .20); alarmo and baseline .10 fixed per gap and continuation .10
total, all with collection held off. Measured idle detection floors
(allowance + idle baseline, 25 unloaded runs per body, median / max): alarmo
.3109 / .3112 s, baseline .3059 / .3061 s, continuation .1056 / .1058 s,
catalog, native and upstream .2556 / .2559 s wall and .1000 / .1000 s CPU.
Details in ``support/loop_gap.py``.
"""
import asyncio
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from support import loop_gap
from support.real_time import real_time_measurement

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "support" / "loop_stall_probe.py"
PATH = [str(ROOT), str(ROOT / "tests"), str(ROOT / "hass_mcp_engineering_beta")]
RUN_PROBE = ("import runpy, sys; sys.path[0:0] = sys.argv[1:4]; sys.argv[:] = sys.argv[4:]; "
             "runpy.run_path(sys.argv[0], run_name='__main__')")
VERDICT = re.compile(r"^LOOP_STALL_PROBE (\w+) STALL \[([^\]]*)\] NOISY (True|False) BODY_PASSED (True|False)$", re.M)
BODIES = ("alarmo", "baseline", "continuation", "catalog", "native", "upstream")


def output(result):
    # Child output for failure messages, without a "Ran N tests" line that
    # Full/Evidence's test-count parser could mistake for the outer summary.
    text = result.stdout + result.stderr
    return re.sub(r"^Ran ", "Isolated child test summary: ", text, flags=re.M)[-6000:]


class BoundTests(unittest.TestCase):
    def test_bound_is_allowance_plus_baseline(self):
        self.assertEqual(loop_gap.gap_bound(.03125, .25), .28125)

    def test_baseline_contribution_is_capped_below_a_400ms_stall(self):
        for allowance, cap in ((.1, .1), (.25, .1), (.3, .05)):
            for baseline in (0, .05, cap, 1, 10, float("inf")):
                with self.subTest(allowance=allowance, cap=cap, baseline=baseline):
                    bound = loop_gap.gap_bound(baseline, allowance, cap)
                    self.assertLessEqual(bound, allowance + cap)
                    self.assertLessEqual(bound, loop_gap.CEILING_SECONDS)
                    self.assertLess(bound, loop_gap.STALL_SECONDS)

    def test_allowance_and_cap_that_could_approach_a_stall_are_refused(self):
        for allowance, cap in ((.3, .1), (.25, .15), (.4, 0), (1, .1)):
            with self.subTest(allowance=allowance, cap=cap), self.assertRaises(ValueError):
                loop_gap.gap_bound(0, allowance, cap)

    def test_invalid_allowance_cap_or_baseline_is_refused(self):
        for baseline, allowance, cap in ((float("nan"), .1, .1), (-.01, .1, .1), (0, 0, .1),
                                         (0, float("nan"), .1), (0, .1, -.01), (0, .1, float("inf"))):
            with self.subTest(baseline=baseline, allowance=allowance, cap=cap), self.assertRaises(ValueError):
                loop_gap.gap_bound(baseline, allowance, cap)


class ReportTests(unittest.TestCase):
    def test_report_selects_the_gap_closest_to_its_own_bound(self):
        # .30 against .25 + .1 leaves 50 ms; .27 against .25 + 0 is already past.
        report = loop_gap.gap_report([.30, .27, .01], [.2, 0, 0], .25)
        self.assertEqual((report["gap_seconds"], report["baseline_seconds"]), (.27, 0))
        self.assertEqual(report["bound_seconds"], .25)
        self.assertEqual((report["maximum_gap_seconds"], report["maximum_baseline_seconds"]), (.30, .2))
        self.assertFalse(report["gap_seconds"] < report["bound_seconds"])

    def test_a_400ms_gap_fails_however_large_its_baseline(self):
        for allowance, cap in ((.1, .1), (.25, .1), (.3, .05)):
            with self.subTest(allowance=allowance, cap=cap):
                report = loop_gap.gap_report([.01, .4], [0, 1e9], allowance, cap)
                self.assertEqual(report["gap_seconds"], .4)
                self.assertGreater(report["gap_seconds"], report["bound_seconds"])

    def test_every_gap_needs_its_own_baseline(self):
        for gaps, baselines in (([], []), ([.1], []), ([.1, .2], [0])):
            with self.subTest(gaps=gaps), self.assertRaises(ValueError):
                loop_gap.gap_report(gaps, baselines, .25)

    def test_message_reports_gap_bound_and_baseline(self):
        message = loop_gap.gap_message("Wall", loop_gap.gap_report([.30], [.0625], .25))
        for text in ("Wall gap 0.3000s", "bound 0.3125s", "allowance 0.250s", "baseline 0.0625s",
                     "cap 0.100s", "longest wall gap 0.3000s"):
            self.assertIn(text, message)

    def test_child_failure_carries_measurements_and_assertion(self):
        result = SimpleNamespace(stdout="Ran 1 test\n" + "{\"wall_gap\": 1}\n",
                                 stderr="Traceback\nAssertionError: 0.3 not less than 0.25 : Wall gap\nFAILED\n")
        message = loop_gap.child_failure("Body failed.", result)
        self.assertEqual(message.splitlines(),
                         ["Body failed.", "{\"wall_gap\": 1}", "AssertionError: 0.3 not less than 0.25 : Wall gap"])


class RunnerDelayTests(unittest.TestCase):
    def test_runner_delay_is_a_non_negative_monotonic_total(self):
        first = loop_gap.runner_delay()
        sum(range(100000))
        self.assertGreaterEqual(first, 0)
        self.assertGreaterEqual(loop_gap.runner_delay(), first)

    def test_runner_delay_is_zero_without_schedstat(self):
        with patch.object(loop_gap.os, "listdir", side_effect=OSError):
            self.assertEqual(loop_gap.runner_delay(), 0.0)


@real_time_measurement("measures an idle event-loop heartbeat")
class BaselineTests(unittest.IsolatedAsyncioTestCase):
    async def test_unstalled_gap_is_a_positive_idle_gap(self):
        self.assertGreater(await loop_gap.unstalled_gap(.005, seconds=.05), 0)

    async def test_a_stall_during_the_baseline_is_measured_but_capped(self):
        # A stall inside the baseline window raises the baseline, but only up
        # to the cap: the bound still rejects a 400 ms stall in the window.
        original = asyncio.sleep
        stalled = []

        async def sleep(delay, *args):
            if not stalled:
                stalled.append(True)
                time.sleep(.2)
            return await original(delay, *args)

        with patch.object(loop_gap.asyncio, "sleep", sleep):
            measured = await loop_gap.unstalled_gap(.005, seconds=.05)
        self.assertGreaterEqual(measured, .2)
        self.assertLess(loop_gap.gap_bound(measured, .25), loop_gap.STALL_SECONDS)


@real_time_measurement("runs the measured responsiveness bodies with and without a 400 ms stall")
class StallProbeTests(unittest.TestCase):
    def probe(self, name, *, stall, noisy):
        result = subprocess.run([sys.executable, "-I", "-B", "-c", RUN_PROBE, *PATH, str(PROBE), name,
                                 "1" if stall else "0", "1" if noisy else "0"],
                                cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
        verdict = VERDICT.findall(result.stdout)
        self.assertEqual(len(verdict), 1, output(result))
        self.assertEqual(verdict[0][0], name, output(result))
        self.assertEqual(verdict[0][2], str(noisy), output(result))
        injected = [float(value) for value in verdict[0][1].split(",") if value.strip()]
        return result, injected, verdict[0][3] == "True"

    def assert_stall_rejected(self, name, *, noisy):
        result, injected, passed = self.probe(name, stall=True, noisy=noisy)
        self.assertEqual(len(injected), 1, output(result))
        self.assertGreaterEqual(injected[0], .4, output(result))
        self.assertFalse(passed, "The 400 ms loop stall survived:\n" + output(result))
        self.assertEqual(result.returncode, 1, output(result))

    def test_every_body_rejects_a_400ms_stall(self):
        for name in BODIES:
            with self.subTest(body=name):
                self.assert_stall_rejected(name, noisy=False)

    def test_every_body_rejects_a_400ms_stall_on_a_maximally_noisy_runner(self):
        for name in BODIES:
            with self.subTest(body=name):
                self.assert_stall_rejected(name, noisy=True)

    def test_unstalled_bodies_pass_in_the_same_probe(self):
        # Control: the same child and patches, with no stall injected.
        for name in BODIES:
            with self.subTest(body=name):
                result, injected, passed = self.probe(name, stall=False, noisy=True)
                self.assertEqual(injected, [], output(result))
                self.assertTrue(passed, output(result))
                self.assertEqual(result.returncode, 0, output(result))

    def test_probe_is_not_collected_by_discovery(self):
        self.assertIsNone(re.fullmatch(r"test.*\.py", PROBE.name))


if __name__ == "__main__":
    unittest.main()
