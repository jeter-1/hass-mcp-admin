"""HAMCP-315 O3 union-manifest decision; offline, exact-source hash records."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_release_evidence as evidence


def source(version):
    return evidence.read_json(ROOT / "docs/evidence/hamcp315/core-sources" / (version + ".json"))


class UnionComparison(unittest.TestCase):
    def verify(self, old, new):
        proof = evidence.compare_sources(old, new)
        self.assertTrue(proof["inherited"])
        evidence.verify_proof(proof, old["version"], new["version"], new["commit"])
        return proof

    def test_october_self_accepts_21_shared_absences(self):
        old = source("2026.10.3")
        proof = self.verify(old, old)
        self.assertEqual(len(proof["files"]), 535)  # HAMCP-297: + device configuration entity
        absent = [r for r in proof["files"] if r["predecessor_sha256"] is None]
        # HAMCP-290: 51 Marketplace profile files are new in 2026.11; the 21 shared absences stay.
        # HAMCP-297: components/switch/entity.py is new in 2026.11 too (22 non-Marketplace absences).
        marketplace = set(evidence.manifest()["profiles"]["core.marketplace_read"])
        self.assertEqual(len(absent), 73)
        self.assertEqual(len([r for r in absent if r["path"] not in marketplace]), 22)
        self.assertIn("homeassistant/components/switch/entity.py", [r["path"] for r in absent])
        self.assertTrue(all(r["release_sha256"] is None for r in absent))

    def test_synthetic_november_patch_accepts_shared_condition_absence(self):
        old = source("2026.11.0")
        # Synthetic patch identity ONLY; no fictitious release or image is evidence.
        new = {**copy.deepcopy(old), "version": "2026.11.1", "commit": "a" * 40, "tag_commit": "a" * 40}
        proof = self.verify(old, new)
        absent = [r["path"] for r in proof["files"] if r["predecessor_sha256"] is None]
        self.assertEqual(absent, ["homeassistant/helpers/condition.py"])

    def test_cross_layout_additions_and_removal_require_judgments(self):
        old, new = source("2026.10.3"), source("2026.11.0")
        paths = set(evidence.manifest()["paths"])
        additions = paths - old["hashes"].keys()
        removals = paths - new["hashes"].keys()
        self.assertEqual(len(additions), 73)  # HAMCP-297: + components/switch/entity.py
        self.assertEqual(removals, {"homeassistant/helpers/condition.py"})
        for a, b in ((old, new), (new, old)):
            proof = evidence.compare_sources(a, b)
            self.assertFalse(proof["inherited"])
            self.assertEqual(proof["outcome"], "binding PR required")
            self.assertTrue((additions | removals) <= set(proof["differing_files"]))
            # A forged inherit flag cannot bypass the verifier either.
            proof.update(inherited=True, differing_files=[])
            with self.assertRaisesRegex(ValueError, "byte identity"):
                evidence.verify_proof(proof, a["version"], b["version"], b["commit"])

    def test_present_changed_bytes_and_malformed_values_still_refuse(self):
        old = source("2026.10.3")
        name = next(iter(old["hashes"]))
        for value in ("a" * 64, "", "bad", False, 3):
            new = copy.deepcopy(old); new["hashes"][name] = value
            self.assertIn(name, evidence.compare_sources(old, new)["differing_files"])
        for value in ("", "bad", False, 3):
            self.assertFalse(evidence.equal_source_hashes(value, value))

    def test_absence_must_be_explicit_in_complete_proof_rows(self):
        old = source("2026.10.3")
        for key in ("predecessor_sha256", "release_sha256"):
            proof = evidence.compare_sources(old, old)
            row = next(r for r in proof["files"] if r[key] is None)
            del row[key]
            with self.assertRaises(ValueError):
                evidence.verify_proof(proof, old["version"], old["version"], old["commit"])

    def test_derived_acceptance_preparation_uses_real_october_hashes(self):
        import core_evidence_workflow as workflow
        old = evidence.read_json(ROOT / "docs/evidence/hamcp279/2026.10.1.json")
        old["tag_commit"] = old["commit"]
        new = source("2026.10.3")
        (ROOT / ".artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".artifacts") as tmp:
            root = Path(tmp)
            run = {"GITHUB_RUN_ID": "315", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40}
            env = {**run, "GITHUB_OUTPUT": str(root / "out"), "GITHUB_STEP_SUMMARY": str(root / "summary")}
            with patch.dict(os.environ, env, clear=True), patch.object(workflow, "source", side_effect=[old, new]):
                workflow.prepare(root / "prepared", True, None)
                fixture = evidence.read_json(root / "prepared/fixture.json")
                self.assertEqual(evidence.verify_context(fixture, run), fixture)
            self.assertTrue(fixture["fixture"])
            self.assertEqual(fixture["mode"], "derived")
            self.assertEqual(len(fixture["proof"]["files"]), 535)  # HAMCP-297: + device configuration entity


class ExistenceGuard(unittest.TestCase):
    def test_committed_manifest_has_a_bound_release_witness_for_every_path(self):
        # Runs in existing complete CI discovery; manifest() also calls this
        # guard in the actual evidence prepare/verify paths, not just this test.
        value = evidence.manifest()
        self.assertEqual(len(value["paths"]), 535)  # HAMCP-297: + device configuration entity
        self.assertTrue(set(value["paths"]) <= source("2026.10.3")["hashes"].keys() | source("2026.11.0")["hashes"].keys())

    def test_typo_absent_from_every_bound_release_fails_naming_path(self):
        original = evidence.read_json
        typo = "homeassistant/components/input_nubmer/typo.py"
        def read(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if Path(path) == ROOT / "ci/core-contract-read-files.json":
                value["paths"] = sorted(value["paths"] + [typo])
            return value
        with patch.object(evidence, "read_json", side_effect=read):
            with self.assertRaisesRegex(ValueError, typo.replace(".", r"\.")):
                evidence.manifest()

    def test_unbound_or_mismatched_source_records_cannot_supply_coverage(self):
        original = evidence.read_json
        for change in ({"version": "2026.12.99"}, {"commit": "f" * 40},
                       {"archive_sha256": "f" * 64}, {"hashes": {"homeassistant/typo.py": "bad"}}):
            def read(path, *args, **kwargs):
                value = original(path, *args, **kwargs)
                if Path(path).parent.name == "core-sources": value.update(change)
                return value
            with self.subTest(change=change), patch.object(evidence, "read_json", side_effect=read):
                with self.assertRaises(ValueError): evidence.manifest()


if __name__ == "__main__":
    unittest.main()
