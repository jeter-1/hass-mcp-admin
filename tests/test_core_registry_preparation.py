"""Owner-reviewed data workflow exercised with ephemeral keys only."""

from copy import deepcopy
import asyncio
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests"),
               str(ROOT / "hass_mcp_engineering_beta")]
import prepare_core_release_registry as prepare
from core_registry_fixtures import CoreSigner, core_entry, core_revocation, NOW
from ha_mcp_engineering.ha_core_readmission import CORE_IDENTITY
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.ha_core_readmission.registry_models import CoreRegistryEnvelope
from ha_mcp_engineering.ha_mcp_readmission.registry import ReleaseRegistryOperationalError
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

    def test_explicit_typed_references_can_be_prepared_without_changing_registry_schema(self):
        from ha_mcp_engineering.ha_core_readmission.registry_models import CoreReleaseEntry
        entry = core_entry('2026.9.3', typed_operations=True)
        entry['evidence_sha256'] = hashlib.sha256(self.evidence).hexdigest()
        self.entry = entry
        candidate = self.candidate()
        self.assertNotIn(b'"signature"', candidate)
        parsed = prepare.parse_journal(self.sign(candidate), self.signer.private_key.public_key())
        self.assertEqual(len(parsed.accepted.entries[0].capabilities), 19)
        invalid = deepcopy(entry)
        invalid['capabilities'][-1]['adapter_id'] = 'unknown-adapter'
        with self.assertRaisesRegex(ValueError, 'exact capability'):
            prepare.require_known_contracts(CoreReleaseEntry.from_mapping(invalid))

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
        # Independent review reproduced redundant-source exhaustion at the
        # 40th renewal. Repeated copies must not consume the eight-source bound.
        for _ in range(55):
            raw = self.sign(self.candidate(raw), raw)
        parsed = prepare.parse_journal(raw, self.signer.private_key.public_key())
        self.assertEqual(len(parsed.envelopes), 32)
        self.assertGreater(parsed.envelopes[0].sequence, 1)
        self.assertEqual(parsed.accepted.revocations[0].version, "2026.9.2")
        self.assertTrue(parsed.revocation_sources)
        self.assertEqual(len(parsed.revocation_sources), 1)
        self.assert_runtime_denials(raw, {"2026.9.2"}, positive="2026.9.3")

    def assert_runtime_denials(self, raw, denied, *, positive="2026.9.2"):
        async def fetch(_url, _maximum):
            return raw

        with tempfile.TemporaryDirectory() as directory:
            kwargs = dict(enabled=True, public_key=self.signer.public_key_base64,
                          cache_path=Path(directory) / "core.json", now=lambda: NOW)
            registry = CoreReleaseRegistry(**kwargs, fetcher=fetch)
            self.assertTrue(asyncio.run(registry.refresh()))
            for current in (registry, CoreReleaseRegistry(**kwargs)):
                for version in denied:
                    self.assertTrue(current.authority().revoked(CORE_IDENTITY, version))
                    self.assertTrue(all(s.status.value == "deny_only"
                                        for s in current.selections(version)))
                self.assertEqual(len(current.selections(positive)), 17)

    def compactable_journal(self, *, sources, first_denials):
        # Synthetic, authenticated checkpoint with a full chain; the first
        # envelope is retired by the next signing operation.
        envelopes = []
        previous = "sha256:" + "a" * 64
        for sequence in range(20, 52):
            raw = self.signer.raw(sequence=sequence, previous_registry_sha256=previous,
                                  generated_at=NOW, entries=[self.entry],
                                  revocations=first_denials if sequence == 20 else [])
            envelopes.append(raw)
            previous = CoreRegistryEnvelope.from_mapping(json.loads(raw)).content_digest
        return self.signer.journal_raw(envelopes=envelopes, revocation_sources=sources)

    def denial_source(self, sequence, versions):
        return self.signer.raw(sequence=sequence,
                               previous_registry_sha256="sha256:" + "b" * 64,
                               generated_at=NOW, entries=[],
                               revocations=[core_revocation(v) for v in versions])

    def test_compaction_preserves_overlapping_and_distinct_denials_deterministically(self):
        versions = ["2026.8.1", "2026.9.0", "2026.9.1"]
        older = self.denial_source(2, versions[:2])
        newer = self.denial_source(3, [versions[0]])
        first = [core_revocation(versions[1]), core_revocation(versions[2])]
        retained_sets = []
        for sources in ([older, newer], [newer, older]):
            previous = self.compactable_journal(sources=sources, first_denials=first)
            candidate = self.candidate(previous)
            raw = self.sign(candidate, previous)
            parsed = prepare.parse_journal(raw, self.signer.private_key.public_key())
            retained_sets.append([s.to_mapping() for s in parsed.revocation_sources])
            self.assertEqual(len(parsed.revocation_sources), 2)
            self.assertEqual({r.version for s in parsed.revocation_sources for r in s.revocations},
                             set(versions))
            self.assertEqual({r.version for r in parsed.accepted.revocations}, set(versions))
            self.assert_runtime_denials(raw, set(versions))
        self.assertEqual(retained_sets[0], retained_sets[1])

    def test_genuinely_distinct_denial_sources_still_refuse_capacity_overflow(self):
        sources = [self.denial_source(n, [f"2026.7.{n}"]) for n in range(2, 10)]
        previous = self.compactable_journal(
            sources=sources, first_denials=[core_revocation("2026.8.1")]
        )
        before = hashlib.sha256(previous).hexdigest()
        candidate = self.candidate(previous)
        with self.assertRaisesRegex(ReleaseRegistryOperationalError,
                                    "registry_revocation_history_capacity_exhausted"):
            self.sign(candidate, previous)
        self.assertEqual(hashlib.sha256(previous).hexdigest(), before)
        self.assertEqual(len(prepare.parse_journal(
            previous, self.signer.private_key.public_key()
        ).revocation_sources), 8)

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


class CoreCapabilityExtensionTests(unittest.TestCase):
    """Actual preparer/signer/selector; all keys and evidence are synthetic."""

    setUp = CoreRegistryPreparationTests.setUp
    candidate = CoreRegistryPreparationTests.candidate
    sign = CoreRegistryPreparationTests.sign

    def extension(self, *, previous=None):
        from ha_mcp_engineering.ha_core_readmission.profiles import CORE_INTEGRATION_INSPECTION_PROFILES
        old = core_entry("2026.9.4", typed_operations=True)
        old["evidence_sha256"] = hashlib.sha256(self.evidence).hexdigest()
        old["image_index_digest"] = "sha256:" + "d" * 64
        sibling = core_entry("2026.9.3", typed_operations=True)
        sibling["image_index_digest"] = "sha256:" + "f" * 64
        if previous is None:
            previous = self.signer.journal_raw(envelopes=[self.signer.raw(entries=[sibling, old], generated_at=NOW)])
        prepare.parse_journal(previous, self.signer.private_key.public_key())
        profile = CORE_INTEGRATION_INSPECTION_PROFILES[0]
        reference = {k: profile.to_mapping()[k] for k in old["capabilities"][0]}
        evidence = {
            "model": prepare.EXTENSION_EVIDENCE_MODEL,
            "version": old["version"],
            "previous_entry_sha256": hashlib.sha256(canonical_json(old)).hexdigest(),
            "previous_capabilities": deepcopy(old["capabilities"]),
            "added_capabilities": [reference],
            "review_artifacts_sha256": [hashlib.sha256(b"synthetic integration and review").hexdigest()],
        }
        raw = canonical_json(evidence)
        new = {**deepcopy(old), "evidence_sha256": hashlib.sha256(raw).hexdigest(),
               "capabilities": [*deepcopy(old["capabilities"]), reference]}
        return previous, old, sibling, new, raw

    def prepare_extension(self, previous, new, evidence, **kwargs):
        return canonical_json(prepare.prepare_candidate(
            entry=new, evidence=evidence, previous=previous,
            public_key=self.signer.private_key.public_key(),
            operation="extend-capabilities", now=NOW, **kwargs))

    def test_extension_preserves_history_sibling_refs_and_actual_cached_selection(self):
        from unittest.mock import patch
        from ha_mcp_engineering.ha_core_readmission.profiles import CORE_CAPABILITY_PROFILES, CORE_TYPED_OPERATION_PROFILES
        previous, old, sibling, new, evidence = self.extension()
        raw = self.sign(self.prepare_extension(previous, new, evidence), previous)
        parsed = prepare.parse_journal(raw, self.signer.private_key.public_key())
        prior = prepare.parse_journal(previous, self.signer.private_key.public_key())
        self.assertEqual(parsed.envelopes[0].to_mapping(), prior.envelopes[0].to_mapping())
        self.assertEqual([e.to_mapping() for e in parsed.accepted.entries], [sibling, new])
        self.assertEqual(new["capabilities"][:-1], old["capabilities"])
        async def fetch(_url, _maximum):
            return raw
        with tempfile.TemporaryDirectory() as directory:
            kwargs = dict(enabled=True, public_key=self.signer.public_key_base64,
                          cache_path=Path(directory)/"core.json", now=lambda: NOW)
            registry = CoreReleaseRegistry(**kwargs, fetcher=fetch)
            self.assertTrue(asyncio.run(registry.refresh()))
            for current in (registry, CoreReleaseRegistry(**kwargs)):
                self.assertEqual(len(current.selections("2026.9.4")), 20)
                self.assertEqual(len(current.selections("2026.9.3")), 19)
                # Same selector with the prior executable's compiled reference set.
                with patch("ha_mcp_engineering.ha_core_readmission.registry.CORE_RUNTIME_CAPABILITY_PROFILES",
                           CORE_CAPABILITY_PROFILES + CORE_TYPED_OPERATION_PROFILES):
                    self.assertEqual(len(current.selections("2026.9.4")), 19)
                    self.assertNotIn("core.integration_inspection_metadata_read",
                                     {x.capability_ids[0] for x in current.selections("2026.9.4")})

    def test_extension_changes_selection_binding_and_requires_fresh_planning(self):
        previous, old, sibling, new, evidence = self.extension()
        extended = self.sign(self.prepare_extension(previous, new, evidence), previous)
        payload = previous
        async def fetch(_url, _maximum):
            return payload
        with tempfile.TemporaryDirectory() as directory:
            registry = CoreReleaseRegistry(enabled=True, public_key=self.signer.public_key_base64,
                cache_path=Path(directory)/"core.json", now=lambda: NOW, fetcher=fetch)
            self.assertTrue(asyncio.run(registry.refresh()))
            old_token = registry.selection_token("2026.9.4")
            payload = extended
            self.assertTrue(asyncio.run(registry.refresh()))
            self.assertNotEqual(registry.selection_token("2026.9.4"), old_token)
            self.assertEqual(len(registry.selections("2026.9.4")), 20)

    def test_all_signing_labels_refuse_hand_edited_extension_and_recomputed_hash(self):
        previous, old, sibling, new, evidence = self.extension()
        candidate = prepare.strict_json(self.prepare_extension(previous, new, evidence))
        mutations = {
            "identity": lambda v: v["envelope"]["entries"][1].update(source_commit="a"*40),
            "probe": lambda v: v["envelope"]["entries"][1].update(probe_profile_id="unknown"),
            "sibling": lambda v: v["envelope"]["entries"][0].update(evidence_sha256="e"*64),
            "remove_sibling": lambda v: v["envelope"]["entries"].pop(0),
            "ref_order": lambda v: v["envelope"]["entries"][1]["capabilities"].reverse(),
            "ref_removal": lambda v: v["envelope"]["entries"][1]["capabilities"].pop(0),
            "ref_fingerprint": lambda v: v["envelope"]["entries"][1]["capabilities"][0].update(contract_fingerprint="sha256:"+"b"*64),
            "evidence_digest": lambda v: v.update(review_evidence_sha256="a"*64),
            "review_manifest": lambda v: v["extension_review"].update(version="2026.9.3"),
            "review_artifact": lambda v: v["extension_review"].update(review_artifacts_sha256=["c"*64]),
        }
        for name, mutate in mutations.items():
            value = deepcopy(candidate); mutate(value)
            with self.subTest(case=name), self.assertRaises(ValueError):
                self.sign(canonical_json(value), previous)
        for label in ("add", "revoke"):
            value = deepcopy(candidate)
            value.update(model=prepare.MODEL, operation=label)
            value.pop("extension_review")
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.sign(canonical_json(value), previous)

    def test_extension_refuses_zero_additions_duplicates_unknown_and_wrong_release(self):
        previous, old, sibling, new, evidence = self.extension()
        cases = [deepcopy(new) for _ in range(6)]
        cases[0]["capabilities"] = deepcopy(old["capabilities"])
        cases[1]["capabilities"].append(deepcopy(new["capabilities"][-1]))
        cases[2]["capabilities"][-1]["adapter_id"] = "unknown"
        cases[3]["version"] = "2026.9.5"
        cases[4]["image_index_digest"] = "sha256:"+"a"*64
        cases[5]["architecture_manifests"]["linux/amd64"] = "sha256:"+"b"*64
        for index, entry in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                self.prepare_extension(previous, entry, evidence)
        with self.assertRaises(ValueError):
            self.prepare_extension(None, new, evidence)

    def test_extension_rejects_stale_or_revoked_predecessor_and_bad_review_binding(self):
        previous, old, sibling, new, evidence = self.extension()
        expired = self.signer.journal_raw(envelopes=[self.signer.raw(
            entries=[sibling, old], generated_at=NOW-timedelta(days=91), expires_at=NOW-timedelta(days=1))])
        revoked = self.signer.journal_raw(envelopes=[self.signer.raw(
            entries=[sibling], revocations=[core_revocation("2026.9.4")], generated_at=NOW)])
        for prior in (expired, revoked):
            with self.subTest(kind=prior[-16:]), self.assertRaises(ValueError):
                self.prepare_extension(prior, new, evidence)
        for key, value in (("previous_entry_sha256", "b"*64), ("previous_capabilities", []),
                           ("added_capabilities", []), ("review_artifacts_sha256", [])):
            review = json.loads(evidence);review[key] = value;raw = canonical_json(review)
            entry = {**new, "evidence_sha256": hashlib.sha256(raw).hexdigest()}
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.prepare_extension(previous, entry, raw)

    def test_extension_evidence_bounds_and_preserves_input_on_refusal(self):
        previous, old, sibling, new, evidence = self.extension()
        original = deepcopy(new)
        for raw in (evidence+b"\n", b"x"*(prepare.MAX_EVIDENCE_BYTES+1)):
            entry = {**new, "evidence_sha256": hashlib.sha256(raw).hexdigest()}
            with self.assertRaises(ValueError):
                self.prepare_extension(previous, entry, raw)
        self.assertEqual(new, original)
        candidate = self.prepare_extension(previous, new, evidence)
        with self.assertRaises(ValueError):
            prepare.sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                previous=previous, key=self.signer.private_key, now=NOW+timedelta(days=91))
        with self.assertRaises(ValueError):
            prepare.sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                previous=None, key=self.signer.private_key, now=NOW)

    def test_add_signing_rejects_preparation_bypass_but_keeps_identical_renewal(self):
        previous = self.sign(self.candidate())
        good = self.candidate(previous)
        self.assertTrue(self.sign(good, previous))
        for change in ("source_commit", "evidence_sha256"):
            value = prepare.strict_json(good)
            value["envelope"]["entries"][0][change] = "b"*(40 if change=="source_commit" else 64)
            with self.subTest(field=change), self.assertRaises(ValueError):
                self.sign(canonical_json(value), previous)

    def test_extension_preserves_prior_revocation_and_original_journal_bytes(self):
        previous, old, sibling, new, evidence = self.extension()
        previous = self.signer.journal_raw(envelopes=[self.signer.raw(
            entries=[sibling, old], revocations=[core_revocation("2026.9.2")], generated_at=NOW)])
        digest = hashlib.sha256(previous).hexdigest()
        candidate = self.prepare_extension(previous, new, evidence)
        raw = self.sign(candidate, previous)
        parsed = prepare.parse_journal(raw, self.signer.private_key.public_key())
        self.assertEqual(parsed.accepted.revocations[0].version, "2026.9.2")
        self.assertEqual(hashlib.sha256(previous).hexdigest(), digest)
        value = prepare.strict_json(candidate);value["envelope"]["revocations"] = []
        with self.assertRaises(ValueError):
            self.sign(canonical_json(value), previous)


if __name__ == "__main__":
    unittest.main()
