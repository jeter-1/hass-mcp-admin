"""Modules that once passed only because of what ran before them.

Module-level sharding can place any test module first in its process, or after
any other module. Each module in ``ISOLATED_MODULES`` must make
``ha_mcp_engineering`` importable itself, so each one runs alone in a fresh
interpreter with no inherited ``PYTHONPATH``, loaded as a shard loads it:
``unittest discover -s tests -t tests`` from the root. Each module in
``GATEWAY_MODULES`` initializes an upstream read gateway, which publishes its
catalog process-wide, and must restore the empty catalog for later modules.
"""

from __future__ import annotations

import json
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
GATEWAY_MODULES = ("test_beta58_ha_mcp_8_4_3_continuity",)
# Runs one module the way a shard does, recording the largest dynamic catalog
# it published and the catalog it leaves behind for the next module.
GATEWAY_DRIVER = """
import json, sys, unittest
sys.path[:0] = ["tests", ".", "hass_mcp_engineering_beta"]
from ha_mcp_engineering import capabilities
published = [0]
replace = capabilities.replace_dynamic_upstream_capabilities
def recording(values, gateway_summary):
    values = tuple(values)
    published[0] = max(published[0], len(values))
    replace(values, gateway_summary)
capabilities.replace_dynamic_upstream_capabilities = recording
suite = unittest.TestLoader().discover("tests", pattern=sys.argv[1] + ".py", top_level_dir="tests")
result = unittest.TextTestRunner(stream=sys.stderr).run(suite)
print(json.dumps({
    "ran": result.testsRun,
    "ok": result.wasSuccessful(),
    "published": published[0],
    "left": len(capabilities.dynamic_upstream_capabilities()),
}))
"""


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

    def test_gateway_modules_leave_no_dynamic_catalog_behind(self):
        clean = dict(os.environ)
        clean.pop("PYTHONPATH", None)
        for module in GATEWAY_MODULES:
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-c", GATEWAY_DRIVER, module],
                    cwd=ROOT,
                    env=clean,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=300,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-4000:])
                report = json.loads(result.stdout.strip().splitlines()[-1])
                self.assertTrue(report["ok"], result.stderr[-4000:])
                self.assertGreater(report["ran"], 0)
                # The module really published a catalog, so an empty catalog
                # afterwards proves its cleanup rather than an idle run.
                self.assertGreater(report["published"], 0)
                self.assertEqual(report["left"], 0)

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
