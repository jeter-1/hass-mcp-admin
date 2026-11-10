"""Offline HAMCP-295 entity rename lane boundaries; no Docker, network or production key."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
import core_entity_rename_local as lane
import core_local_resources
import entity_rename_contract_acceptance as acceptance
from core_release_evidence import groups, manifest, passing_subset

PROVENANCE = json.loads((ROOT / "tests/fixtures/core_2026_11_0_lane_provenance.json").read_text())


class Contracts(unittest.TestCase):
    def test_pinned_fingerprint_and_group_never_lend_authority(self):
        ref = acceptance.reference()
        self.assertEqual(ref["contract_fingerprint"], lane.CONTRACT_FINGERPRINT)
        self.assertEqual(ref["capability_id"], lane.CAPABILITY)
        self.assertEqual(groups()["entity_registry_rename"], [ref["capability_id"]])
        self.assertEqual(passing_subset([ref], {"general": True}), [])
        self.assertEqual(passing_subset([ref], {"helper_value": True}), [])
        self.assertEqual(passing_subset([ref], {"entity_registry_rename": False}), [])
        self.assertEqual(passing_subset([ref], {"entity_registry_rename": True}), [ref])

    def test_manifest_profile_matches_committed_and_o3_source_hashes(self):
        proof = json.loads(lane.SOURCE_HASHES.read_text())
        profile = manifest()["profiles"][lane.CAPABILITY]
        self.assertEqual(profile, [row["path"] for row in proof["files"]])
        self.assertEqual(profile, sorted(set(profile)))
        self.assertIn("homeassistant/components/config/entity_registry.py", profile)
        self.assertIn("homeassistant/helpers/entity_registry.py", profile)
        self.assertEqual((proof["core_version"], proof["source_commit"], proof["source_archive_sha256"]),
                         (lane.VERSION, PROVENANCE["source_commit"], PROVENANCE["source_archive_sha256"]))
        record = json.loads((ROOT / "docs/evidence/hamcp315/core-sources/2026.11.0.json").read_text())
        for row in proof["files"]:
            self.assertEqual(record["hashes"][row["path"]], row["sha256"])

    def test_disposable_shape(self):
        # A known lane label (other lanes refuse unknown ownership), not 18123,
        # file-backed automation/script stores and an explicit Core memory bound.
        self.assertIn(lane.LABEL, core_local_resources.LANE_LABELS)
        self.assertEqual(lane.PORTS, (18124,))
        self.assertIn("automation: !include automations.yaml", lane.CONFIG)
        self.assertIn("script: !include scripts.yaml", lane.CONFIG)
        self.assertEqual(lane.CORE_MEMORY, "2g")
        self.assertFalse(lane.NETWORK_POSTURE["egress_required"])


class Inputs(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.profile = ["homeassistant/a.py", "homeassistant/b.py"]
        content = {p: ("synthetic " + p).encode() for p in self.profile}
        self.archive = root / "archive.tar.gz"
        with tarfile.open(self.archive, "w:gz") as tar:
            for path, data in content.items():
                info = tarfile.TarInfo("core-" + PROVENANCE["source_commit"] + "/" + path)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        self.manifest, self.hashes = root / "manifest.json", root / "hashes.json"
        self.hashes_map = {p: hashlib.sha256(d).hexdigest() for p, d in content.items()}

    def tearDown(self):
        self.directory.cleanup()

    def source(self, hashes):
        self.manifest.write_text(json.dumps({"paths": self.profile, "profiles": {lane.CAPABILITY: self.profile}}))
        self.hashes.write_text(json.dumps({
            "core_version": lane.VERSION, "source_commit": PROVENANCE["source_commit"],
            "source_archive_sha256": PROVENANCE["source_archive_sha256"],
            "files": [{"path": p, "sha256": h} for p, h in hashes.items()]}))
        with patch.object(lane, "MANIFEST", self.manifest), patch.object(lane, "SOURCE_HASHES", self.hashes):
            return lane.source_manifest(self.archive, PROVENANCE)

    def test_exact_source_hashes_pass_and_drift_refuses(self):
        self.assertEqual(self.source(self.hashes_map)["profile_paths"], 2)
        with self.assertRaisesRegex(lane.Refusal, "entity_rename_source_hash_mismatch"):
            self.source({**self.hashes_map, "homeassistant/a.py": "0" * 64})

    def test_input_hash_and_fingerprint_refuse_before_journal(self):
        with patch.object(lane, "sha256", return_value="0" * 64):
            with self.assertRaisesRegex(lane.Refusal, "input_hash_mismatch"):
                lane.inputs("journal", "archive")
        digests = iter((lane.JOURNAL_SHA256, PROVENANCE["source_archive_sha256"]))
        drifted = {**acceptance.reference(), "contract_fingerprint": "sha256:" + "0" * 64}
        with patch.object(lane, "sha256", side_effect=lambda _p: next(digests)), \
                patch.object(lane, "compiled_reference", return_value=drifted), \
                patch.object(lane, "source_manifest") as sources:
            with self.assertRaisesRegex(lane.Refusal, "profile_fingerprint_mismatch"):
                lane.inputs("journal", "archive")
            sources.assert_not_called()


class LocalOnly(unittest.TestCase):
    def test_ci_context_and_dirty_checkout_never_start(self):
        argv = ["--mode", "local", "--pull", "never", "--journal", "j", "--source-archive", "a",
                "--run-dir", str(ROOT / ".artifacts/never-created")]
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(lane, "command") as command:
            with self.assertRaisesRegex(lane.Refusal, "local_only_closed_lane"):
                lane.main(argv)
            command.assert_not_called()
        dirty = type("R", (), {"stdout": b" M x\n", "returncode": 0})()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "", "CORE_EVIDENCE_CONTEXT": ""}), \
                patch.object(lane, "command", return_value=dirty), \
                patch.object(lane, "inputs") as inputs:
            with self.assertRaisesRegex(lane.Refusal, "clean_committed_checkout_required"):
                lane.main(argv)
            inputs.assert_not_called()
        self.assertFalse((ROOT / ".artifacts/never-created").exists())


if __name__ == "__main__":
    unittest.main()
