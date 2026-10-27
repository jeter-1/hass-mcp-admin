"""HAMCP-226/228 review 1: the reviewer's probes, unchanged, as permanent tests.

``review_probes/hamcp226_228_review1/`` holds the reviewer's five probe files
byte for byte (their digests are pinned below); unittest discovery does not
collect them. Each runs in an isolated child interpreter from the repository
root, the way the reviewer ran them, and must pass:

- ``reviewer_probes.py``: H1 registry platform and orphan identifier screening,
  H2 missing-index survival, M1 same-generation Core version change, M2 compare
  continuation.
- ``additional_probes.py``: H1 in the real tool envelope; M3 reduced-cap
  overflow accounting.
- ``full_bound_probe.py``: M3 at the production limits (501 scripts).
- ``registry_corpus_probe.py``: H1 with every HAMCP-132 corpus case planted in
  the registry ``platform`` field of the real tool response.
- ``restart_probe.py``: M1 across a fresh runtime with the real allocator.
"""
import hashlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "tests" / "review_probes" / "hamcp226_228_review1"
# sha256 of each probe in the review 1 bundle (.artifacts/reviews/pr317-2e6363cd).
PROBE_SHA256 = {
    "reviewer_probes.py": "4e1df0dca9ed4da9eb32cf0e49d87d2c17782e57050fc908248b5d11efba42ea",
    "additional_probes.py": "7cb12751728691a7bbdf33760968ed7a4af3785e73cfd2161518187d0e84618e",
    "full_bound_probe.py": "510a11e9a5e08ce1b9221f0ea230347b748cb586d5df648b4a5a6a9198ff6b86",
    "registry_corpus_probe.py": "a8603647da1f7b36060f70ee59041b8f6e00135251ee0a3e71f5998a80b5c7f5",
    "restart_probe.py": "ba4d819ce546d13cf6f9e0b5fc8d693378602c0de10d3c77c2e886edd9b4601d",
}
# unittest probes and the number of tests each defines.
UNITTEST_PROBES = {"reviewer_probes.py": 5, "additional_probes.py": 2, "full_bound_probe.py": 1,
                   "restart_probe.py": 1}
# The reviewer's import path (cwd/tests and cwd/hass_mcp_engineering_beta, set
# by each probe itself) with the repository root as the working directory.
PATH = [str(ROOT), str(ROOT / "tests"), str(ROOT / "hass_mcp_engineering_beta")]
RUN_PROBE = ("import runpy, sys; sys.path[0:0] = sys.argv[1:4]; sys.argv[:] = sys.argv[4:]; "
             "runpy.run_path(sys.argv[0], run_name='__main__')")


def run(name):
    return subprocess.run([sys.executable, "-I", "-B", "-c", RUN_PROBE, *PATH, str(PROBES / name)],
                          cwd=ROOT, capture_output=True, text=True, timeout=900, check=False)


def output(result):
    text = result.stdout + result.stderr
    return re.sub(r"^Ran ", "Isolated child test summary: ", text, flags=re.M)[-6000:]


class ReviewerProbeFileTests(unittest.TestCase):
    def test_probes_are_the_reviewers_files_byte_for_byte(self):
        self.assertEqual(sorted(p.name for p in PROBES.glob("*.py")), sorted(PROBE_SHA256))
        for name, digest in PROBE_SHA256.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((PROBES / name).read_bytes()).hexdigest(), digest)

    def test_probes_are_not_collected_by_discovery(self):
        for name in PROBE_SHA256:
            with self.subTest(name=name):
                self.assertIsNone(re.fullmatch(r"test.*\.py", name))


class ReviewerProbeRunTests(unittest.TestCase):
    def test_each_unittest_probe_passes_unchanged(self):
        for name, count in UNITTEST_PROBES.items():
            with self.subTest(name=name):
                result = run(name)
                self.assertEqual(result.returncode, 0, output(result))
                self.assertRegex(result.stderr, rf"(?m)^Ran {count} tests? in ", output(result))
                self.assertRegex(result.stderr, r"(?m)^OK$", output(result))

    def test_registry_corpus_probe_reports_no_leaking_case(self):
        result = run("registry_corpus_probe.py")
        self.assertEqual(result.returncode, 0, output(result))
        self.assertEqual(result.stdout.strip().splitlines()[-1], "Leaking registry_platform corpus cases: []",
                         output(result))


if __name__ == "__main__":
    unittest.main()
