"""Exact provider-specific vendoring; never accept the observed version itself."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts/disposable_ha_websockets_probe.py"


class DisposableWebsocketsProbeTests(unittest.TestCase):
    def probe(self, requested, source_version, vendor_version, *, import_shared=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(f'[project]\nversion = "{source_version}"\n')
            (root / "websockets.py").write_text('"""Synthetic shared package for import-isolation checks."""\n')
            vendor = root / "src/ha_mcp/_vendor/websockets"
            vendor.mkdir(parents=True)
            (vendor / "__init__.py").write_text(
                ("import websockets\n" if import_shared else "")
                + f'__version__ = "{vendor_version}"\n'
            )
            output = root / "evidence.json"
            result = subprocess.run(
                [sys.executable, str(PROBE), "--source-root", str(root),
                 "--upstream-version", requested, "--output", str(output)],
                capture_output=True, text=True, timeout=10,
                env={**os.environ, "PYTHONPATH": str(root)},
            )
            evidence = json.loads(output.read_text()) if output.exists() else None
            return result, evidence

    def test_each_reviewed_provider_preserves_shared_distribution(self):
        for upstream, vendor in (("8.2.0", "17.0.1"), ("8.4.3", "17.0.1"), ("8.5.0", "17.1")):
            with self.subTest(upstream=upstream):
                result, evidence = self.probe(upstream, upstream, vendor)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(evidence["upstream_version"], upstream)
                self.assertEqual(evidence["vendored_version"], vendor)
                self.assertTrue(evidence["shared_distribution_unchanged"])
                self.assertFalse(evidence["vendored_import_loaded_shared_package"])

    def test_changed_vendor_and_wrong_source_refuse_without_success_evidence(self):
        for requested, source, vendor in (
            ("8.5.0", "8.5.0", "17.0.1"),
            ("8.4.3", "8.4.3", "17.1"),
            ("8.5.0", "8.4.3", "17.1"),
            ("8.5.1", "8.5.1", "17.1"),
        ):
            with self.subTest(requested=requested, source=source, vendor=vendor):
                result, evidence = self.probe(requested, source, vendor)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(evidence)

    def test_shared_package_import_still_refuses(self):
        result, evidence = self.probe("8.5.0", "8.5.0", "17.1", import_shared=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vendored import loaded shared websockets", result.stderr)
        self.assertIsNone(evidence)

    def test_ci_passes_exact_matrix_provider_to_probe(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        job = workflow["jobs"]["real-ha-contract-tests"]
        step = next(item for item in job["steps"] if item.get("name") ==
                    "Verify exact ha-mcp component inside disposable Home Assistant")
        self.assertIn('--upstream-version "$REAL_HA_UPSTREAM_VERSION"', step["run"])
        self.assertEqual(job["env"]["REAL_HA_UPSTREAM_VERSION"], "${{ matrix.ha_mcp_version }}")
