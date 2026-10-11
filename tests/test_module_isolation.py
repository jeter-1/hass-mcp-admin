"""Modules that once passed only after another module altered ``sys.path``.

Module-level sharding can place any test module first in its process. Each
module named here must make ``ha_mcp_engineering`` importable itself, so each
one runs alone in a fresh interpreter with no inherited ``PYTHONPATH``, loaded
as a shard loads it: ``unittest discover -s tests -t tests`` from the root.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
ISOLATED_MODULES = (
    "test_beta23_compatibility_family_admission",
    "test_beta39_obligation_resource_bounds",
    "test_beta39_template_semantic_registry",
    "test_upstream_review_candidate",
)


def run_alone(module):
    clean = dict(os.environ)
    clean.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "tests",
         "-p", f"{module}.py"],
        cwd=ROOT,
        env=clean,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=300,
    )


class ModuleIsolationTests(unittest.TestCase):
    def test_each_module_passes_alone_in_a_fresh_interpreter(self):
        for module in ISOLATED_MODULES:
            with self.subTest(module=module):
                self.assertTrue((ROOT / "tests" / f"{module}.py").is_file())
                result = run_alone(module)
                self.assertEqual(result.returncode, 0, result.stdout[-4000:])
                ran = re.search(r"^Ran (\d+) tests? in ", result.stdout, re.MULTILINE)
                self.assertIsNotNone(ran, result.stdout[-4000:])
                self.assertGreater(int(ran.group(1)), 0)
                self.assertRegex(result.stdout, r"(?m)^OK\b")

    def test_check_is_not_satisfied_by_the_environment(self):
        # Without a module's own path setup, ha_mcp_engineering must not be
        # importable here; otherwise the check above would enforce nothing.
        clean = dict(os.environ)
        clean.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-c", "import ha_mcp_engineering"],
            cwd=ROOT,
            env=clean,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No module named 'ha_mcp_engineering'", result.stdout)


if __name__ == "__main__":
    unittest.main()
