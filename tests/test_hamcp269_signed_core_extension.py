"""Owner-signed sequence 15 and CI key separation; public production key only."""

import asyncio
import base64
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

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import prepare_core_release_registry as prepare
from core_registry_fixtures import CoreSigner, NOW
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.ha_mcp_readmission.registry import ReleaseRegistryOperationalError
from ha_mcp_engineering.signed_registry import canonical_json

EVIDENCE = ROOT / "docs/evidence/hamcp269"
PUBLIC_KEY = ROOT / "docs/evidence/hamcp222/core-public-key.txt"
SIGNED_SHA256 = "a4dfbb143ce6cd932b91bf49c4b3d0fe9b889f690a27188c547c4700e8b62dc4"
ADDITIONS = (
    "core.config_entry_helper_options_operation",
    "core.typed_input_select_option_operation",
    "core.script_trace_read",
    "core.dashboard_metadata_operation",
    "core.scene_configuration_operation",
)


class SignedCoreSequence15Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(PUBLIC_KEY.read_bytes().strip(), validate=True))
        cls.raw = (ROOT / "upstream-trust/ha-core-release-registry.json").read_bytes()
        cls.journal = prepare.parse_journal(cls.raw, cls.key)
        cls.previous_raw = (EVIDENCE / "previous-seq14-journal.json").read_bytes()
        cls.previous = prepare.parse_journal(cls.previous_raw, cls.key)
        cls.candidate_raw = (EVIDENCE / "candidate-seq15-2026.10.3.json").read_bytes()
        cls.candidate = prepare.strict_json(cls.candidate_raw)
        # Historical signing-time checks must remain deterministic after expiry.
        cls.now = prepare.parse_utc_timestamp(cls.journal.accepted.generated_at) + timedelta(seconds=1)

    def test_signed_bytes_match_reviewed_candidate_and_verified_predecessor(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), SIGNED_SHA256)
        self.assertEqual(hashlib.sha256(self.candidate_raw).hexdigest(),
                         "224305a5cb41d53a58311246bcf5a31ee9e0e6678efbc310464f119014adb168")
        self.assertEqual(self.previous.accepted.sequence, 14)
        self.assertEqual(self.journal.accepted.sequence, 15)
        self.assertEqual(self.candidate["previous_journal_sha256"], hashlib.sha256(self.previous_raw).hexdigest())
        self.assertEqual(self.journal.accepted.previous_registry_sha256, self.previous.accepted.content_digest)
        signed = json.loads(self.raw)
        previous = json.loads(self.previous_raw)
        self.assertEqual(signed["envelopes"][:-1], previous["envelopes"])
        self.assertEqual(signed["revocation_sources"], previous["revocation_sources"])
        self.assertEqual({k: v for k, v in signed["envelopes"][-1].items() if k != "signature"},
                         self.candidate["envelope"])
        prepare.validate_transition(self.candidate, self.previous, self.now)

    def test_only_core_10_3_extends_the_unchanged_ordered_prefix(self):
        before = {e.version: e.to_mapping() for e in self.previous.accepted.entries}
        after = {e.version: e.to_mapping() for e in self.journal.accepted.entries}
        self.assertEqual(list(before), list(after))
        self.assertEqual([v for v in before if before[v] != after[v]], ["2026.10.3"])
        self.assertEqual({v: len(e["capabilities"]) for v, e in after.items()},
                         {"2026.10.0": 22, "2026.10.1": 26, "2026.10.3": 31})
        old, new = before["2026.10.3"], after["2026.10.3"]
        self.assertEqual(new["capabilities"][:26], old["capabilities"])
        self.assertEqual({k for k in old if old[k] != new[k]}, {"capabilities", "evidence_sha256"})
        added = new["capabilities"][26:]
        self.assertEqual(tuple(r["capability_id"] for r in added), ADDITIONS)
        profiles = {p.capability_id: p.to_mapping() for p in CORE_RUNTIME_CAPABILITY_PROFILES}
        for reference in added:
            self.assertEqual(reference, {k: profiles[reference["capability_id"]][k] for k in reference})
        self.assertEqual(self.journal.accepted.revocations, self.previous.accepted.revocations)

    def test_canonical_evidence_binds_all_retained_review_artifacts(self):
        raw = (EVIDENCE / "evidence-extend-2026.10.3.json").read_bytes()
        self.assertEqual(raw, canonical_json(self.candidate["extension_review"]))
        self.assertFalse(raw.endswith(b"\n"))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.candidate["review_evidence_sha256"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "eb68cf9285c2f5e8db355eef21a2da822a091834f55030c169a3519154bcb1af")
        hashes = self.candidate["extension_review"]["review_artifacts_sha256"]
        self.assertEqual(len(hashes), 10)
        files = list((EVIDENCE / "review-artifacts").iterdir())
        self.assertEqual(len(files), 10)
        self.assertEqual({hashlib.sha256(p.read_bytes()).hexdigest() for p in files}, set(hashes))

    def test_runtime_selector_admits_31_for_10_3_but_keeps_10_1_at_26(self):
        async def fetch(_url, _maximum):
            return self.raw

        with tempfile.TemporaryDirectory() as directory:
            kwargs = dict(enabled=True, public_key=PUBLIC_KEY.read_text().strip(),
                          cache_path=Path(directory) / "registry.json", now=lambda: self.now)
            registry = CoreReleaseRegistry(**kwargs, fetcher=fetch)
            self.assertTrue(asyncio.run(registry.refresh()))
            for current in (registry, CoreReleaseRegistry(**kwargs)):
                self.assertEqual(len(current.selections("2026.10.3")), 31)
                self.assertEqual(len(current.selections("2026.10.1")), 26)
                selected = {cap for item in current.selections("2026.10.3") for cap in item.capability_ids}
                self.assertTrue(set(ADDITIONS) <= selected)
                self.assertTrue(all(item.status.value == "positive" for item in current.selections("2026.10.3")))

    def test_ephemeral_signed_record_is_refused_by_committed_production_key(self):
        # Positive control: the identical valid Core journal authenticates with
        # the ephemeral CI key; changing only the trust anchor must refuse it.
        signer = CoreSigner()
        raw = signer.journal_raw(envelopes=[signer.raw(generated_at=NOW)])
        accepted = prepare.parse_journal(raw, signer.private_key.public_key())
        self.assertEqual(accepted.accepted.sequence, 1)
        self.assertEqual(hashlib.sha256(self.key.public_bytes_raw()).hexdigest(),
                         "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac")
        with self.assertRaisesRegex(ReleaseRegistryOperationalError, "^registry_invalid_signature$"):
            prepare.parse_journal(raw, self.key)
