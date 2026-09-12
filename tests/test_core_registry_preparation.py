"""Owner-reviewed data workflow exercised with ephemeral keys only."""

from copy import deepcopy
from datetime import timedelta
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests"),
               str(ROOT / "hass_mcp_engineering_beta")]
import prepare_core_release_registry as prepare
from core_registry_fixtures import CoreSigner, core_entry, NOW
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.signed_registry import canonical_json


class CoreRegistryPreparationTests(unittest.TestCase):
    def setUp(self):
        self.signer = CoreSigner()
        self.evidence = b'{"synthetic_review":"test only"}'
        self.entry = core_entry()
        self.entry["evidence_sha256"] = hashlib.sha256(self.evidence).hexdigest()

    def candidate(self, previous=None, **kwargs):
        return canonical_json(prepare.prepare_candidate(
            entry=self.entry, evidence=self.evidence, previous=previous,
            public_key=self.signer.private_key.public_key(), now=NOW, **kwargs))

    def sign(self, candidate, previous=None, **kwargs):
        return prepare.sign_candidate(
            candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
            previous=previous, key=self.signer.private_key, now=NOW, **kwargs)

    def test_reviewed_candidate_signed_then_used_by_actual_runtime_selector(self):
        candidate = self.candidate()
        raw = self.sign(candidate)
        parsed = prepare.parse_journal(raw, self.signer.private_key.public_key())
        self.assertEqual(parsed.accepted.entries[0].to_mapping(), self.entry)
        self.assertNotIn(b"signature", candidate)

    def test_changed_candidate_key_evidence_or_journal_refused(self):
        candidate = self.candidate()
        with self.assertRaises(ValueError):
            prepare.sign_candidate(candidate + b" ", expected_sha256=hashlib.sha256(candidate).hexdigest(),
                                   previous=None, key=self.signer.private_key, now=NOW)
        with self.assertRaises(ValueError):
            prepare.sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                                   previous=None, key=CoreSigner().private_key, now=NOW)
        with self.assertRaises(ValueError):
            self.sign(candidate, self.sign(candidate))
        self.evidence = b"changed"
        with self.assertRaises(ValueError):
            self.candidate()

    def test_unknown_contract_cannot_be_prepared_and_input_not_mutated(self):
        original = deepcopy(self.entry)
        self.candidate()
        self.assertEqual(self.entry, original)
        self.entry["capabilities"][0]["adapter_id"] = "unknown"
        with self.assertRaises(ValueError):
            self.candidate()

    def test_older_writer_can_revoke_unknown_profile_without_altering_other_positives(self):
        self.entry["probe_profile_id"] = "future-unknown-profile"
        sibling = {**deepcopy(self.entry), "version": "2026.9.3", "entry_id": "synthetic-sibling",
                   "image_index_digest": "sha256:" + "f" * 64}
        previous = self.signer.journal_raw(envelopes=[self.signer.raw(entries=[self.entry, sibling])])
        candidate = self.candidate(previous, operation="revoke", reason="Synthetic withdrawal.")
        result = prepare.parse_journal(self.sign(candidate, previous), self.signer.private_key.public_key())
        self.assertEqual([e.to_mapping() for e in result.accepted.entries], [sibling])
        self.assertEqual(result.accepted.revocations[0].version, "2026.9.2")
        value = prepare.strict_json(candidate)
        value["envelope"]["entries"][0]["source_commit"] = "a" * 40
        with self.assertRaises(ValueError):
            self.sign(canonical_json(value), previous)

    def test_revocation_survives_renewal_and_checkpoint_compaction(self):
        raw = self.sign(self.candidate())
        raw = self.sign(self.candidate(raw, operation="revoke", reason="Synthetic withdrawal."), raw)
        with self.assertRaises(ValueError):
            self.candidate(raw)
        self.entry = {**self.entry, "entry_id": "synthetic-next", "version": "2026.9.3"}
        for _ in range(33):
            raw = self.sign(self.candidate(raw), raw)
        parsed = prepare.parse_journal(raw, self.signer.private_key.public_key())
        self.assertEqual(len(parsed.envelopes), 32)
        self.assertGreater(parsed.envelopes[0].sequence, 1)
        self.assertEqual(parsed.accepted.revocations[0].version, "2026.9.2")
        self.assertTrue(parsed.revocation_sources)

    def test_changed_release_cannot_replace_current_identity(self):
        raw = self.sign(self.candidate())
        self.entry["source_commit"] = "a" * 40
        with self.assertRaises(ValueError):
            self.candidate(raw)

    def test_expired_candidate_and_non_successor_refused(self):
        candidate = self.candidate()
        with self.assertRaises(ValueError):
            prepare.sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                                   previous=None, key=self.signer.private_key, now=NOW + timedelta(days=91))
        value = prepare.strict_json(candidate)
        value["envelope"]["sequence"] = 2
        with self.assertRaises(ValueError):
            self.sign(canonical_json(value))

    def test_strict_inputs_and_existing_output_preserved(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.assertRaises(ValueError):
                prepare.strict_json(raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            prepare.write_new(path, b"preserve")
            with self.assertRaises(FileExistsError):
                prepare.write_new(path, b"changed")
            self.assertEqual(path.read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
