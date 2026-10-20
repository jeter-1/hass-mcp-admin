"""HAMCP-191 T4: ordinary data is byte-identical to beta.19.

``tests/fixtures/hamcp191/compat_corpus.json`` holds outputs that the exact
base-revision sanitizer produced (see its ``provenance``). This test reruns
the generator's comparison against this tree in a child process, so that the
generator's module reloading never disturbs this process's imports, and
requires:

- every ordinary and plain-redaction sample is byte-identical to the base;
- every changed sample carries an owner-decision reason, really changes, and
  leaks no synthetic secret, while the P1 and P2 samples leaked on the base.

The re-scan half of T4 (Engineering markers survive by identity; equal-text
forgeries collide) is ``IdentityRecordTests`` in
``test_hamcp191_shared_sanitizer.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "hamcp191" / "compat_corpus.json"
GENERATOR = ROOT / "docs" / "evidence" / "hamcp191" / "generate_corpus.py"
BASE_REVISION = "3c4c27b702a0be33ad5f55868377eb4197d4ef38"


class CompatibilityCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "comparison.json"
            completed = subprocess.run(
                [sys.executable, "-I", str(GENERATOR), "compare", "--tree", str(ROOT),
                 "--fixture", str(FIXTURE), "--output", str(output)],
                capture_output=True, text=True, timeout=300,
            )
            cls.completed = completed
            cls.result = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None

    def test_fixture_provenance_names_the_exact_base(self):
        provenance = self.fixture["provenance"]
        self.assertEqual(provenance["base_revision"], BASE_REVISION)
        self.assertIn("generate_corpus.py record", provenance["generator"])

    def test_comparison_runs_and_reports_nothing_unexpected(self):
        self.assertEqual(self.completed.returncode, 0, self.completed.stdout + self.completed.stderr)
        self.assertIsNotNone(self.result)
        self.assertEqual(self.result["unexpected"], [])

    def test_ordinary_and_plain_redaction_samples_are_byte_identical(self):
        stable = [entry for entry in self.fixture["samples"] if entry["class"] in ("ordinary", "redacted")]
        self.assertGreaterEqual(sum(entry["class"] == "ordinary" for entry in stable), 30)
        self.assertGreaterEqual(sum(entry["class"] == "redacted" for entry in stable), 10)
        self.assertEqual(self.result["byte_identical"], len(stable))
        for entry in stable:
            # Ordinary outputs are reviewable text; redaction outputs are digests.
            outputs = entry["base"] if entry["class"] == "ordinary" else entry["base_sha256"]
            self.assertNotIn("base" if entry["class"] == "redacted" else "base_sha256", entry)
            self.assertLessEqual({"sanitize_untrusted_data", "redact_data", "logbook.project"}, set(outputs))

    def test_every_changed_sample_is_explained_and_leaks_nothing(self):
        changed = {entry["id"]: entry for entry in self.result["changed_by_decision"]}
        recorded = [entry for entry in self.fixture["samples"] if entry["class"] == "changed"]
        self.assertEqual(set(changed), {entry["id"] for entry in recorded})
        for entry in recorded:
            with self.subTest(sample=entry["id"]):
                self.assertIn(entry["reason"], self.fixture["reasons"])
                self.assertNotIn("base", entry)  # Digests only: the base output may hold a secret.
                self.assertTrue(changed[entry["id"]]["changed_entry_points"])
                self.assertFalse(changed[entry["id"]]["head_leaks_secret"])

    def test_p1_and_p2_leaked_on_the_base_and_not_on_the_head(self):
        leaks = [entry for entry in self.result["changed_by_decision"] if entry["reason"] in ("P1", "P2")]
        self.assertEqual({entry["reason"] for entry in leaks}, {"P1", "P2"})
        for entry in leaks:
            with self.subTest(sample=entry["id"]):
                self.assertTrue(entry["base_leaks_secret"])
                self.assertFalse(entry["head_leaks_secret"])


if __name__ == "__main__":
    unittest.main()
