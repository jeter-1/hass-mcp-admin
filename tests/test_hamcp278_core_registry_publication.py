"""HAMCP-278: Core registry one-tap signing and registry-branch publication.

Offline only: ephemeral test keys, synthetic Core entries and disposable local
git repositories. Nothing here reads the owner's private key, contacts GitHub or
Home Assistant, or pushes anywhere but a temporary bare repository.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import redirect_stdout
import copy
import dataclasses
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests"), str(ROOT / "hass_mcp_engineering_beta")]

import core_registry_one_tap as tap  # noqa: E402
import core_registry_seed_secret as seed  # noqa: E402
import prepare_core_release_registry as prepare  # noqa: E402
from core_registry_fixtures import CoreSigner, core_entry, NOW  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import registry as core_registry  # noqa: E402
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

WORKFLOW_PATH = ROOT / ".github" / "workflows" / "core-release-registry-publish.yml"
PUBLISH_SCRIPT = ROOT / "scripts" / "core_registry_publish.sh"
CHECK_SCRIPT = ROOT / "scripts" / "check_core_registry_branch.sh"
COMMITTED_PUBLIC_KEY = ROOT / "docs" / "evidence" / "hamcp222" / "core-public-key.txt"
REGISTRY_URL = (
    "https://raw.githubusercontent.com/jeter-1/hass-mcp-admin/"
    "registry/upstream-trust/ha-core-release-registry.json"
)
PRE_278_MAIN_URL = (
    "https://raw.githubusercontent.com/jeter-1/hass-mcp-admin/"
    "main/upstream-trust/ha-core-release-registry.json"
)
JOURNAL_PATH = "upstream-trust/ha-core-release-registry.json"
REGISTRY_DOC = ROOT / "docs" / "CORE_RELEASE_REGISTRY.md"
RUNBOOK_DOC = ROOT / "docs" / "CORE_REGISTRY_ONE_TAP_RUNBOOK.md"
ACCEPTANCE_DOC = ROOT / "docs" / "V2_4_1_BETA57_ACCEPTANCE.md"
REAL_GIT = shutil.which("git")
# Stands in for git on PATH: logs every push and can model a lost push
# acknowledgment, an unavailable remote read, or a concurrent registry update.
FAKE_GIT = """#!/usr/bin/env bash
sub=""
for argument in "$@"; do
  case "$argument" in push|ls-remote) sub="$argument"; break ;; esac
done
if [[ "$sub" == push ]]; then printf '%s\\n' "$*" >> "$FAKE_GIT_LOG"; fi
case "$sub:$FAKE_GIT_MODE" in
  push:accept-then-fail|push:accept-then-fail-unreadable) "$REAL_GIT" "$@" >/dev/null 2>&1; exit 1 ;;
  push:move-first) "$REAL_GIT" -C "$FAKE_MOVER" push --quiet origin HEAD:refs/heads/registry >/dev/null 2>&1 || exit 97 ;;
  ls-remote:accept-then-fail-unreadable) exit 128 ;;
esac
exec "$REAL_GIT" "$@"
"""
HAVE_SHELL_TOOLS = all(shutil.which(tool) for tool in ("bash", "git", "sha256sum"))


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def evidence_path(version: str, evidence: bytes) -> str:
    return f"upstream-trust/evidence/ha-core-{version}-{sha(evidence)}.json"


def chain(now=NOW, *, typed_predecessor=False, typed_successor=True, signer=None):
    """Sequence 1 (2026.9.2) signed, plus an unsigned add candidate for 2026.9.3."""
    signer = signer or CoreSigner()
    key = signer.private_key
    public_key = key.public_key()
    earlier = now - timedelta(hours=1)
    ev1 = b'{"synthetic_review":"2026.9.2"}'
    e1 = core_entry("2026.9.2", typed_operations=typed_predecessor)
    e1["evidence_sha256"] = sha(ev1)
    c1 = canonical_json(prepare.prepare_candidate(
        entry=e1, evidence=ev1, previous=None, public_key=public_key, now=earlier))
    j1 = prepare.sign_candidate(c1, expected_sha256=sha(c1), previous=None, key=key, now=earlier)
    ev2 = b'{"synthetic_review":"2026.9.3"}'
    e2 = core_entry("2026.9.3", typed_operations=typed_successor)
    e2["evidence_sha256"] = sha(ev2)
    e2["image_index_digest"] = "sha256:" + "8" * 64
    c2 = canonical_json(prepare.prepare_candidate(
        entry=e2, evidence=ev2, previous=j1, public_key=public_key, now=now)) + b"\n"
    return SimpleNamespace(signer=signer, key=key, public_key=public_key, now=now,
                           ev1=ev1, e1=e1, j1=j1, ev2=ev2, e2=e2, c2=c2)


def sign(data, candidate=None, previous=None, key=None):
    candidate = data.c2 if candidate is None else candidate
    return prepare.sign_candidate(
        candidate, expected_sha256=sha(candidate),
        previous=data.j1 if previous is None else previous,
        key=key or data.key, now=data.now)


def verify(data, **overrides):
    arguments = dict(candidate=data.c2, evidence=data.ev2, predecessor=data.j1,
                     public_key=data.public_key, core_version="2026.9.3",
                     candidate_sha256=sha(data.c2), evidence_sha256=sha(data.ev2),
                     now=data.now)
    arguments.update(overrides)
    return tap.verify_candidate(**arguments)


def recandidate(data, mutate):
    value = json.loads(data.c2)
    mutate(value)
    return canonical_json(value) + b"\n"


def fingerprint(key) -> str:
    return sha(key.public_key().public_bytes_raw())


def public_key_file(directory: Path, public_key) -> Path:
    path = directory / "public-key.txt"
    path.write_bytes(base64.b64encode(public_key.public_bytes_raw()) + b"\n")
    return path


class RuntimeUrlTests(unittest.TestCase):
    def test_url_constant_is_exact_registry_branch_with_no_override(self):
        self.assertEqual(core_registry.CORE_REGISTRY_URL, REGISTRY_URL)
        self.assertEqual(core_registry.CORE_REGISTRY_BINDING.url, REGISTRY_URL)
        self.assertEqual(tap.CORE_REGISTRY_URL, REGISTRY_URL)
        # Only the branch segment differs from the pre-278 main URL.
        self.assertEqual(REGISTRY_URL.replace("/registry/", "/main/", 1), PRE_278_MAIN_URL)
        parameters = set(inspect.signature(core_registry.CoreReleaseRegistry.__init__).parameters)
        self.assertEqual(parameters, {"self", "enabled", "public_key", "cache_path", "fetcher", "now"})
        source = (ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/registry.py"
                  ).read_text(encoding="utf-8")
        for forbidden in ("environ", "getenv", "/main/upstream-trust"):
            self.assertNotIn(forbidden, source)
        config = yaml.safe_load((ROOT / "hass_mcp_engineering_beta/config.yaml").read_text(encoding="utf-8"))
        core_options = {k for k in (*config["options"], *config["schema"]) if k.startswith("ha_core_release_registry")}
        self.assertEqual(core_options, {"ha_core_release_registry_enabled",
                                        "ha_core_release_registry_public_key"})

    def test_identical_bytes_verify_identically_from_either_url(self):
        data = chain()
        raw = sign(data)

        async def observe(binding_url):
            requested = []

            async def fetch(url, maximum):
                requested.append(url)
                return {PRE_278_MAIN_URL: raw, REGISTRY_URL: raw}[url]

            binding = dataclasses.replace(core_registry.CORE_REGISTRY_BINDING, url=binding_url)
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(core_registry, "CORE_REGISTRY_BINDING", binding):
                registry = core_registry.CoreReleaseRegistry(
                    enabled=True, public_key=data.signer.public_key_base64,
                    cache_path=Path(directory) / "cache.json", fetcher=fetch, now=lambda: NOW)
                self.assertTrue(await registry.refresh())
                authority = registry.authority()
                return requested, registry.snapshot()["refresh_status"], authority.sequence, \
                    authority.content_digest, authority.envelope.unsigned_mapping()

        old, new = asyncio.run(observe(PRE_278_MAIN_URL)), asyncio.run(observe(REGISTRY_URL))
        self.assertEqual(old[0], [PRE_278_MAIN_URL])
        self.assertEqual(new[0], [REGISTRY_URL])
        self.assertEqual(old[1:], new[1:])
        self.assertEqual(new[1:3], ("accepted", 2))

    def test_runtime_refuses_a_fetch_from_any_other_location(self):
        data = chain()

        async def fetch_elsewhere():
            async def fetch(url, maximum):
                raise AssertionError("must not fetch")

            with tempfile.TemporaryDirectory() as directory:
                registry = core_registry.CoreReleaseRegistry(
                    enabled=True, public_key=data.signer.public_key_base64,
                    cache_path=Path(directory) / "cache.json", fetcher=fetch, now=lambda: NOW)
                return await registry._fetch_bytes(PRE_278_MAIN_URL, 4096)

        with self.assertRaises(Exception):
            asyncio.run(fetch_elsewhere())


class CandidateVerificationTests(unittest.TestCase):
    def setUp(self):
        self.data = chain()

    def test_candidate_facts_and_summary_with_no_withheld_profiles(self):
        facts = verify(self.data)
        self.assertEqual((facts["operation"], facts["predecessor_sequence"], facts["new_sequence"]),
                         ("add", 1, 2))
        self.assertEqual(facts["source_version"], "2026.9.2")
        self.assertEqual(facts["withheld"], [])
        self.assertTrue(facts["added"])
        summary = tap.render_summary(facts, dry_run=False)
        self.assertIn("Withheld profiles: none.", summary)
        self.assertNotIn("[!CAUTION]", summary)
        self.assertNotIn("DRY RUN", summary)
        self.assertIn(f"**{tap.APPROVAL_LINE}**", summary)
        self.assertEqual(tap.APPROVAL_LINE,
                         "Approving signs this exact candidate with Josh's Core registry key; "
                         "a separate job then pushes it to the registry branch.")
        self.assertIn(f"| Candidate SHA-256 | `{sha(self.data.c2)}` |", summary)
        self.assertIn(f"| Evidence SHA-256 | `{sha(self.data.ev2)}` |", summary)

    def test_summary_renders_prominent_withheld_block(self):
        data = chain(typed_predecessor=True, typed_successor=False)
        facts = verify(data)
        self.assertTrue(facts["withheld"])
        self.assertEqual(facts["added"], [])
        summary = tap.render_summary(facts, dry_run=True)
        self.assertIn("**DRY RUN.**", summary)
        self.assertIn("> [!CAUTION]", summary)
        self.assertIn(f"> **WITHHELD: {len(facts['withheld'])} profile(s)", summary)
        for capability in facts["withheld"]:
            self.assertIn(f"> - `{capability}`", summary)
        self.assertNotIn("Withheld profiles: none.", summary)
        self.assertLess(summary.index("[!CAUTION]"), summary.index(tap.APPROVAL_LINE))

    def test_digest_mismatch_refused(self):
        for overrides, reason in (
            ({"candidate_sha256": "0" * 64}, "candidate SHA-256 does not match"),
            ({"evidence_sha256": "0" * 64}, "evidence SHA-256 does not match"),
            ({"candidate": self.data.c2 + b" "}, "candidate SHA-256 does not match"),
            ({"evidence": self.data.ev2 + b" "}, "evidence SHA-256 does not match"),
            ({"candidate_sha256": "A" * 64}, "candidate SHA-256 input is malformed"),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(tap.Refusal, reason):
                verify(self.data, **overrides)

    def test_wrong_key_or_fingerprint_refused(self):
        other = Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(tap.Refusal, "prepared for a different Core key"):
            verify(self.data, public_key=other.public_key())
        encoded = base64.b64encode(other.public_key().public_bytes_raw())
        with self.assertRaisesRegex(tap.Refusal, "not the committed owner Core key"):
            tap.load_public_key(encoded, pinned=True)
        committed = tap.load_public_key(COMMITTED_PUBLIC_KEY.read_bytes(), pinned=True)
        self.assertEqual(sha(committed.public_bytes_raw()), tap.OWNER_CORE_KEY_SHA256)
        self.assertEqual(seed.OWNER_CORE_KEY_SHA256, tap.OWNER_CORE_KEY_SHA256)
        # A journal signed by another key never verifies as the successor.
        foreign = tap.rekey_journal(sign(self.data), self.data.public_key, other)
        with self.assertRaisesRegex(tap.Refusal, "refused the signed journal"):
            tap.runtime_successor(journal=foreign, predecessor=self.data.j1,
                                  candidate=tap.parse_candidate(self.data.c2),
                                  public_key=self.data.public_key, now=NOW)

    def test_broken_chain_refused(self):
        for mutate in (
            lambda v: v["envelope"].__setitem__("previous_registry_sha256", "0" * 64),
            lambda v: v["envelope"].__setitem__("sequence", 3),
        ):
            candidate = recandidate(self.data, mutate)
            with self.subTest(), self.assertRaisesRegex(tap.Refusal, "not the next sequence"):
                verify(self.data, candidate=candidate, candidate_sha256=sha(candidate))
        candidate = recandidate(self.data, lambda v: v.__setitem__("previous_journal_sha256", "0" * 64))
        with self.assertRaisesRegex(tap.Refusal, "not the current registry journal"):
            verify(self.data, candidate=candidate, candidate_sha256=sha(candidate))

    def test_sequence_replay_refused(self):
        j2 = sign(self.data)
        # The already-published candidate cannot be replayed against its own successor.
        with self.assertRaisesRegex(tap.Refusal, "not the current registry journal"):
            verify(self.data, predecessor=j2)
        candidate = recandidate(self.data, lambda v: v.__setitem__("previous_journal_sha256", sha(j2)))
        with self.assertRaisesRegex(tap.Refusal, "not the next sequence"):
            verify(self.data, candidate=candidate, candidate_sha256=sha(candidate), predecessor=j2)
        # The runtime verifier refuses rolling back to the earlier journal.
        with self.assertRaisesRegex(tap.Refusal, "refused the signed journal"):
            tap.runtime_successor(journal=self.data.j1, predecessor=j2,
                                  candidate=tap.parse_candidate(self.data.c2),
                                  public_key=self.data.public_key, now=NOW)

    def test_re_adding_a_revoked_release_refused(self):
        data = self.data
        revoke = canonical_json(prepare.prepare_candidate(
            entry=data.e1, evidence=data.ev1, previous=data.j1, public_key=data.public_key,
            operation="revoke", reason="Synthetic test-only withdrawal.", now=NOW)) + b"\n"
        j2 = sign(data, candidate=revoke)
        tip = prepare.parse_journal(j2, data.public_key).accepted
        envelope = tip.unsigned_mapping()
        envelope.update(sequence=3, previous_registry_sha256=tip.content_digest,
                        entries=[data.e1])
        readd = canonical_json({
            "model": prepare.MODEL, "operation": "add",
            "public_key_sha256": sha(data.public_key.public_bytes_raw()),
            "previous_journal_sha256": sha(j2), "review_evidence_sha256": sha(data.ev1),
            "envelope": envelope}) + b"\n"
        with self.assertRaisesRegex(tap.Refusal, "utility transition check refused: "
                                    "(registry_entry_revocation_contradiction|revoked release)"):
            verify(data, candidate=readd, candidate_sha256=sha(readd), evidence=data.ev1,
                   evidence_sha256=sha(data.ev1), predecessor=j2, core_version="2026.9.2")

    def test_revocation_candidate_verifies_for_its_target_only(self):
        data = self.data
        revoke = canonical_json(prepare.prepare_candidate(
            entry=data.e1, evidence=data.ev1, previous=data.j1, public_key=data.public_key,
            operation="revoke", reason="Synthetic test-only withdrawal.", now=NOW)) + b"\n"
        arguments = dict(candidate=revoke, candidate_sha256=sha(revoke), evidence=data.ev1,
                         evidence_sha256=sha(data.ev1))
        facts = verify(data, core_version="2026.9.2", **arguments)
        self.assertEqual((facts["operation"], facts["carried"]), ("revoke", []))
        self.assertIn("[!CAUTION]", tap.render_summary(facts, dry_run=False))
        with self.assertRaisesRegex(tap.Refusal, "does not revoke the requested Core version"):
            verify(data, core_version="2026.9.4", **arguments)

    def test_requested_version_expired_and_unparseable_candidates_refused(self):
        with self.assertRaisesRegex(tap.Refusal, "does not change the requested Core version"):
            verify(self.data, core_version="2026.9.4")
        with self.assertRaisesRegex(tap.Refusal, "exact stable release"):
            verify(self.data, core_version="2026.9.3b1")
        with self.assertRaisesRegex(tap.Refusal, "expired or not yet valid|transition check"):
            verify(self.data, now=NOW + timedelta(days=91))
        with self.assertRaisesRegex(tap.Refusal, "expired or not yet valid"):
            verify(self.data, now=NOW - timedelta(hours=1))
        for raw in (b"[]", b'{"model": 1}', b"{bad"):
            with self.subTest(raw=raw), self.assertRaises(tap.Refusal):
                verify(self.data, candidate=raw, candidate_sha256=sha(raw))
        with self.assertRaisesRegex(tap.Refusal, "does not verify"):
            garbage = b'{"not":"a journal"}'
            candidate = recandidate(self.data, lambda v: v.__setitem__("previous_journal_sha256", sha(garbage)))
            verify(self.data, candidate=candidate, candidate_sha256=sha(candidate), predecessor=garbage)


class SigningAndDryRunTests(unittest.TestCase):
    def setUp(self):
        self.data = chain()

    def test_signed_successor_accepted_by_runtime_verifier(self):
        j2 = sign(self.data)
        after = tap.runtime_successor(journal=j2, predecessor=self.data.j1,
                                      candidate=tap.parse_candidate(self.data.c2),
                                      public_key=self.data.public_key, now=NOW)
        self.assertEqual(after.sequence, 2)

    def test_signed_journal_must_carry_exactly_the_reviewed_candidate(self):
        other = recandidate(self.data, lambda v: v["envelope"].__setitem__(
            "expires_at", "2026-10-26T12:00:00Z"))
        j2 = sign(self.data, candidate=other)
        with self.assertRaisesRegex(tap.Refusal, "does not carry exactly the reviewed candidate"):
            tap.runtime_successor(journal=j2, predecessor=self.data.j1,
                                  candidate=tap.parse_candidate(self.data.c2),
                                  public_key=self.data.public_key, now=NOW)

    def test_dry_run_uses_only_an_ephemeral_key_and_preserves_content(self):
        journal, predecessor, ephemeral = tap.dry_run_sign(
            candidate=self.data.c2, predecessor=self.data.j1,
            public_key=self.data.public_key, now=NOW)
        self.assertNotEqual(ephemeral.public_bytes_raw(), self.data.public_key.public_bytes_raw())
        after = tap.runtime_successor(journal=journal, predecessor=predecessor,
                                      candidate=tap.parse_candidate(self.data.c2),
                                      public_key=ephemeral, now=NOW)
        self.assertEqual(after.sequence, 2)
        real = prepare.parse_journal(sign(self.data), self.data.public_key)
        dry = prepare.parse_journal(journal, ephemeral)
        self.assertEqual([e.content_digest for e in dry.envelopes],
                         [e.content_digest for e in real.envelopes])
        # The dry-run journal never verifies under the owner (here: fixture) key.
        with self.assertRaises(tap.Refusal):
            tap.runtime_successor(journal=journal, predecessor=self.data.j1,
                                  candidate=tap.parse_candidate(self.data.c2),
                                  public_key=self.data.public_key, now=NOW)

    def test_one_tap_module_never_references_the_signing_secret(self):
        source = (ROOT / "scripts" / "core_registry_one_tap.py").read_text(encoding="utf-8")
        for forbidden in ("HA_CORE_RELEASE_REGISTRY_SIGNING_KEY", "environ", "getenv",
                          "urlopen", "requests", "subprocess"):
            self.assertNotIn(forbidden, source)


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(shutil.rmtree, self.tmp)

    def run_cli(self, *arguments):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "core_registry_one_tap.py"),
                               *arguments], capture_output=True, text=True, cwd=ROOT, timeout=120)

    def test_cli_refuses_digest_mismatch_against_the_committed_key(self):
        candidate = self.tmp / "candidate.json"
        evidence = self.tmp / "evidence.json"
        candidate.write_bytes(b'{"synthetic":"candidate"}\n')
        evidence.write_bytes(b'{"synthetic":"evidence"}')
        result = self.run_cli(
            "verify-candidate", "--candidate", str(candidate), "--evidence", str(evidence),
            "--predecessor", str(ROOT / JOURNAL_PATH), "--public-key", str(COMMITTED_PUBLIC_KEY),
            "--summary", str(self.tmp / "summary.md"), "--outputs", str(self.tmp / "outputs"),
            "--core-version", "2026.10.4", "--candidate-sha256", "0" * 64,
            "--evidence-sha256", sha(evidence.read_bytes()))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Core registry one-tap refused: candidate SHA-256 does not match the reviewed value.",
                      result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.tmp / "summary.md").exists())

    def test_cli_refuses_an_unpinned_key_outside_the_dry_run(self):
        data = chain()
        paths = {}
        for name, raw in (("journal", sign(data)), ("predecessor", data.j1), ("candidate", data.c2)):
            paths[name] = self.tmp / f"{name}.json"
            paths[name].write_bytes(raw)
        key = public_key_file(self.tmp, data.public_key)
        result = self.run_cli("verify-signed", "--journal", str(paths["journal"]),
                              "--predecessor", str(paths["predecessor"]),
                              "--candidate", str(paths["candidate"]), "--public-key", str(key))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public key is not the committed owner Core key", result.stderr)

    def test_cli_verify_candidate_dry_run_sign_and_verify_signed(self):
        now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=1)
        data = chain(now, typed_predecessor=True, typed_successor=False)
        files = {"candidate": data.c2, "evidence": data.ev2, "predecessor": data.j1}
        for name, raw in files.items():
            (self.tmp / f"{name}.json").write_bytes(raw)
        key = public_key_file(self.tmp, data.public_key)
        summary, outputs = self.tmp / "summary.md", self.tmp / "outputs"
        with patch.object(tap, "OWNER_CORE_KEY_SHA256", fingerprint(data.key)), \
                redirect_stdout(io.StringIO()):
            tap.main(["verify-candidate", "--candidate", str(self.tmp / "candidate.json"),
                      "--evidence", str(self.tmp / "evidence.json"),
                      "--predecessor", str(self.tmp / "predecessor.json"), "--public-key", str(key),
                      "--summary", str(summary), "--outputs", str(outputs),
                      "--core-version", "2026.9.3", "--candidate-sha256", sha(data.c2),
                      "--evidence-sha256", sha(data.ev2), "--dry-run"])
            tap.main(["dry-run-sign", "--candidate", str(self.tmp / "candidate.json"),
                      "--predecessor", str(self.tmp / "predecessor.json"), "--public-key", str(key),
                      "--output-dir", str(self.tmp / "ephemeral")])
        rendered = summary.read_text(encoding="utf-8")
        self.assertIn("**DRY RUN.**", rendered)
        self.assertIn("[!CAUTION]", rendered)
        self.assertIn(tap.APPROVAL_LINE, rendered)
        self.assertIn("new_sequence=2\n", outputs.read_text(encoding="utf-8"))
        self.assertRegex(outputs.read_text(encoding="utf-8"), r"withheld_count=[1-9][0-9]*\n")
        ephemeral = self.tmp / "ephemeral"
        self.assertEqual(sorted(p.name for p in ephemeral.iterdir()),
                         ["ephemeral-predecessor.json", "ephemeral-public-key.txt",
                          "ha-core-release-registry.json"])
        result = self.run_cli("verify-signed", "--ephemeral",
                              "--journal", str(ephemeral / "ha-core-release-registry.json"),
                              "--predecessor", str(ephemeral / "ephemeral-predecessor.json"),
                              "--candidate", str(self.tmp / "candidate.json"),
                              "--public-key", str(ephemeral / "ephemeral-public-key.txt"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("accepted sequence 2", result.stdout)


class SeedSecretTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.key = Ed25519PrivateKey.generate()
        self.pin = patch.object(seed, "OWNER_CORE_KEY_SHA256", fingerprint(self.key))
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def pem(self, key, encryption=None):
        path = self.tmp / "key.pem"
        path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            encryption or serialization.NoEncryption()))
        return path

    def test_writes_base64_raw_seed_without_newline_that_the_signer_reads(self):
        out = io.BytesIO()
        seed.main(["--pem", str(self.pem(self.key))], stdout=out)
        value = out.getvalue()
        self.assertFalse(value.endswith(b"\n"))
        raw = base64.b64decode(value, validate=True)
        self.assertEqual(len(raw), 32)
        self.assertEqual(Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes_raw(),
                         self.key.public_key().public_bytes_raw())

    def test_encrypted_pem_uses_hidden_prompt(self):
        path = self.pem(self.key, serialization.BestAvailableEncryption(b"synthetic-passphrase"))
        prompts = []
        out = io.BytesIO()
        seed.main(["--pem", str(path)], stdout=out,
                  password_reader=lambda prompt: prompts.append(prompt) or "synthetic-passphrase")
        self.assertEqual(prompts, ["PEM passphrase (hidden): "])
        self.assertEqual(base64.b64decode(out.getvalue(), validate=True), self.key.private_bytes_raw())
        with self.assertRaisesRegex(seed.Refusal, "could not be decrypted"):
            seed.main(["--pem", str(path)], stdout=io.BytesIO(), password_reader=lambda prompt: "wrong")

    def test_refusals_write_nothing(self):
        class Terminal(io.BytesIO):
            def isatty(self):
                return True

        terminal = Terminal()
        with self.assertRaisesRegex(seed.Refusal, "standard output is a terminal"):
            seed.main(["--pem", str(self.pem(self.key))], stdout=terminal)
        self.assertEqual(terminal.getvalue(), b"")
        cases = (
            (self.pem(Ed25519PrivateKey.generate()), "not the committed owner Core key"),
        )
        for path, reason in cases:
            out = io.BytesIO()
            with self.subTest(reason=reason), self.assertRaisesRegex(seed.Refusal, reason):
                seed.main(["--pem", str(path)], stdout=out)
            self.assertEqual(out.getvalue(), b"")
        ec_path = self.tmp / "ec.pem"
        ec_path.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        garbage = self.tmp / "garbage.pem"
        garbage.write_bytes(b"not a key")
        empty = self.tmp / "empty.pem"
        empty.write_bytes(b"")
        for path, reason in ((ec_path, "not an Ed25519 key"), (garbage, "not a readable private-key PEM"),
                             (empty, "empty or too large")):
            out = io.BytesIO()
            with self.subTest(reason=reason), self.assertRaisesRegex(seed.Refusal, reason):
                seed.main(["--pem", str(path)], stdout=out)
            self.assertEqual(out.getvalue(), b"")

    def test_refusal_messages_never_carry_key_material(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "core_registry_seed_secret.py"),
             "--pem", str(self.pem(self.key))], capture_output=True, timeout=60, cwd=ROOT)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"Nothing was written.", result.stderr)
        encoded = base64.b64encode(self.key.private_bytes_raw())
        self.assertNotIn(encoded, result.stderr)
        self.assertNotIn(b"PRIVATE KEY", result.stderr)


class RegistryRepository:
    """A disposable local bare repository with main and the publish helpers."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
                    "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
                    "GIT_TERMINAL_PROMPT": "0"}
        self.env.pop("GITHUB_STEP_SUMMARY", None)
        self.data = chain()
        self.j2 = sign(self.data)
        self.remote = self.tmp / "remote.git"
        self.git("init", "--quiet", "--bare", str(self.remote))
        self.seed_dir = self.tmp / "seed"
        self.git("init", "--quiet", "-b", "main", str(self.seed_dir))
        self.write(self.seed_dir / "README.md", b"synthetic main\n")
        self.write(self.seed_dir / JOURNAL_PATH, self.data.j1)
        self.write(self.seed_dir / evidence_path("2026.9.2", self.data.ev1), self.data.ev1)
        self.git("-C", str(self.seed_dir), "add", "-A")
        self.git("-C", str(self.seed_dir), "commit", "--quiet", "-m", "synthetic main")
        self.git("-C", str(self.seed_dir), "push", "--quiet", str(self.remote), "main:refs/heads/main")
        self.main_tip = self.ref("main")

    def git(self, *arguments, check=True):
        return subprocess.run(["git", *arguments], env=self.env, check=check,
                              capture_output=True, text=True, timeout=60).stdout.strip()

    @staticmethod
    def write(path: Path, raw: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def ref(self, name):
        return self.git("-C", str(self.remote), "rev-parse", "--verify", "--quiet",
                        f"refs/heads/{name}", check=False)

    def create_registry(self):
        """The runbook's cutover: an orphan commit holding main's upstream-trust tree."""
        repo = str(self.seed_dir)
        tree = self.git("-C", repo, "rev-parse", "HEAD:upstream-trust")
        root = subprocess.run(["git", "-C", repo, "mktree"], input=f"040000 tree {tree}\tupstream-trust\n",
                              env=self.env, check=True, capture_output=True, text=True).stdout.strip()
        commit = self.git("-C", repo, "commit-tree", root, "-m", "Core registry branch from main")
        self.git("-C", repo, "push", "--quiet", str(self.remote), f"{commit}:refs/heads/registry")
        return commit

    def signed_dir(self, *, journal=None, evidence=None, extra=None, name="signed"):
        signed = self.tmp / name
        evidence = self.data.ev2 if evidence is None else evidence
        files = {evidence_path("2026.9.3", self.data.ev2): evidence,
                 JOURNAL_PATH: self.j2 if journal is None else journal, **(extra or {})}
        for relative, raw in files.items():
            self.write(signed / "files" / relative, raw)
        manifest = "".join(f"{sha(raw)}  {relative}\n" for relative, raw in sorted(files.items()))
        (signed / "SHA256SUMS").write_text(manifest, encoding="utf-8")
        return signed

    def publish(self, mode="publish", signed=None, script=PUBLISH_SCRIPT, **overrides):
        signed = signed or self.signed_dir()
        manifest = sha((signed / "SHA256SUMS").read_bytes())
        env = {**self.env, "SIGNED_DIR": str(signed), "MANIFEST_SHA256": manifest,
               "REVERIFIED_MANIFEST_SHA256": manifest, "PREDECESSOR_SHA256": sha(self.data.j1),
               "CORE_VERSION": "2026.9.3", "EVIDENCE_SHA256": sha(self.data.ev2),
               "REMOTE_URL": str(self.remote),
               "WORK_DIR": str(self.tmp / f"work-{len(list(self.tmp.glob('work-*')))}")}
        env.update(overrides)
        return subprocess.run(["bash", str(script), mode], env=env, capture_output=True,
                              text=True, timeout=120, cwd=self.tmp)

    def assert_refused(self, result, reason, registry_tip):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(reason, result.stderr)
        self.assertIn("registry is unchanged", result.stderr)
        self.assertEqual(self.ref("registry"), registry_tip)
        self.assertEqual(self.ref("main"), self.main_tip)

    def check(self, mode):
        return subprocess.run(["bash", str(CHECK_SCRIPT), mode, str(self.remote)], env=self.env,
                              capture_output=True, text=True, timeout=120)

    def fake_git(self, mode="passthrough", mover=None):
        """PATH overrides that route the publish script's git through FAKE_GIT."""
        directory = self.tmp / "fake-bin"
        directory.mkdir(exist_ok=True)
        wrapper = directory / "git"
        wrapper.write_text(FAKE_GIT, encoding="utf-8")
        wrapper.chmod(0o755)
        self.push_log = self.tmp / "pushes.log"
        self.push_log.write_text("", encoding="utf-8")
        self.summary = self.tmp / "summary.md"
        return {"PATH": f"{directory}{os.pathsep}{self.env['PATH']}", "REAL_GIT": REAL_GIT,
                "FAKE_GIT_MODE": mode, "FAKE_GIT_LOG": str(self.push_log),
                "FAKE_MOVER": str(mover or self.tmp), "GITHUB_STEP_SUMMARY": str(self.summary)}

    def assert_pushes(self, count):
        """At most one push, ever: non-force, the exact commit, to registry only."""
        pushes = self.push_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(pushes), count, pushes)
        for line in pushes:
            arguments = line.split()
            self.assertRegex(arguments[-1], r"^[0-9a-f]{40}:refs/heads/registry$")
            self.assertFalse({"--force", "-f", "--force-with-lease", "--mirror", "--all", "--tags"}
                             & set(arguments), line)
            self.assertFalse(any(argument.startswith(("+", "--force")) for argument in arguments), line)

    def mover(self):
        """A clone holding a concurrent registry update, not yet pushed."""
        mover = self.tmp / "mover"
        self.git("clone", "--quiet", "--branch", "registry", str(self.remote), str(mover))
        self.write(mover / JOURNAL_PATH, self.data.j1 + b" ")
        self.git("-C", str(mover), "commit", "--quiet", "-am", "synthetic concurrent change")
        return mover

    def attempted(self, result):
        match = re.search(r"Attempted commit ([0-9a-f]{40})", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        return match.group(1)



@unittest.skipUnless(HAVE_SHELL_TOOLS, "bash, git and sha256sum are required")
class RegistryBranchTests(RegistryRepository, unittest.TestCase):
    """The publish and check scripts against a disposable local bare repository."""

    def test_end_to_end_ephemeral_sign_verify_and_one_path_publish(self):
        base = self.create_registry()
        self.assertEqual(self.check("cutover").returncode, 0)
        facts = verify(self.data)
        self.assertEqual(facts["new_sequence"], 2)
        tap.runtime_successor(journal=self.j2, predecessor=self.data.j1,
                              candidate=tap.parse_candidate(self.data.c2),
                              public_key=self.data.public_key, now=NOW)
        assembled = self.publish("assemble")
        self.assertEqual(assembled.returncode, 0, assembled.stderr)
        self.assertIn("nothing was pushed", assembled.stdout)
        self.assertEqual(self.ref("registry"), base)
        result = self.publish("publish")
        self.assertEqual(result.returncode, 0, result.stderr)
        tip = self.ref("registry")
        self.assertNotEqual(tip, base)
        self.assertEqual(self.ref("main"), self.main_tip)
        remote = str(self.remote)
        self.assertEqual(self.git("-C", remote, "rev-list", "--parents", "-n", "1", tip), f"{tip} {base}")
        self.assertEqual(self.git("-C", remote, "diff-tree", "--no-commit-id", "--name-status", "-r", base, tip),
                         f"A\t{evidence_path('2026.9.3', self.data.ev2)}\nM\t{JOURNAL_PATH}")
        published = subprocess.run(["git", "-C", remote, "cat-file", "blob", f"{tip}:{JOURNAL_PATH}"],
                                   env=self.env, check=True, capture_output=True, timeout=60).stdout
        self.assertEqual(published, self.j2)
        last = self.check("last-publish")
        self.assertEqual(last.returncode, 0, last.stderr)
        # A second identical run refuses: the registry moved past the verified predecessor.
        self.assert_refused(self.publish("publish"), "registry journal changed since verification", tip)

    def test_refusal_before_push_reports_unchanged_and_pushes_nothing(self):
        self.create_registry()
        mover = self.mover()
        self.git("-C", str(mover), "push", "--quiet", "origin", "registry")
        moved = self.ref("registry")
        result = self.publish("publish", **self.fake_git())
        self.assertEqual(result.returncode, 1)
        self.assert_refused(result, "registry journal changed since verification", moved)
        self.assertIn("Outcome: **UNCHANGED** (refused before any push", self.summary.read_text())
        self.assert_pushes(0)

    def test_accepted_push_with_client_failure_reports_published(self):
        base = self.create_registry()
        result = self.publish("publish", **self.fake_git("accept-then-fail"))
        self.assertEqual(result.returncode, 0, result.stderr)
        tip = self.ref("registry")
        self.assertNotEqual(tip, base)
        self.assertEqual(self.git("-C", str(self.remote), "rev-parse", f"{tip}^"), base)
        self.assertIn(f"Published {tip} to registry.", result.stdout)
        self.assertIn("push client reported failure (status 1)", result.stderr)
        self.assertNotIn("unchanged", (result.stdout + result.stderr).lower())
        self.assertIn("Outcome: **PUBLISHED** (confirmed by a remote read; the push client reported failure)",
                      self.summary.read_text())
        self.assert_pushes(1)
        self.assertEqual(self.ref("main"), self.main_tip)

    def test_accepted_push_with_unavailable_readback_reports_uncertain(self):
        base = self.create_registry()
        result = self.publish("publish", **self.fake_git("accept-then-fail-unreadable"))
        self.assertEqual(result.returncode, 3, result.stderr)
        attempted = self.attempted(result)
        self.assertEqual(self.ref("registry"), attempted)  # it did land; the script cannot know
        self.assertNotEqual(attempted, base)
        self.assertIn("UNCERTAIN", result.stderr)
        self.assertIn("observed registry tip unavailable", result.stderr)
        self.assertIn("Inspect the registry branch before any further publication.", result.stderr)
        self.assertNotIn("unchanged", (result.stdout + result.stderr).lower())
        summary = self.summary.read_text()
        self.assertIn("Outcome: **UNCERTAIN**", summary)
        self.assertIn(attempted, summary)
        self.assertNotIn("UNCHANGED", summary)
        self.assert_pushes(1)

    def test_rejected_push_with_predecessor_tip_reports_confirmed_unchanged(self):
        base = self.create_registry()
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho synthetic rejection >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        result = self.publish("publish", **self.fake_git())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.ref("registry"), base)
        self.assertIn("UNCHANGED (confirmed)", result.stderr)
        self.assertNotEqual(self.attempted(result), base)
        self.assertNotIn("refused before", result.stderr)
        self.assertIn("Outcome: **UNCHANGED** (confirmed by a remote read after the failed push)",
                      self.summary.read_text())
        self.assert_pushes(1)

    def test_push_lost_to_a_concurrent_update_reports_uncertain_and_keeps_it(self):
        self.create_registry()
        mover = self.mover()
        moved = self.git("-C", str(mover), "rev-parse", "HEAD")
        result = self.publish("publish", **self.fake_git("move-first", mover=mover))
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(self.ref("registry"), moved)  # the concurrent update is not overwritten
        self.assertIn(f"observed registry tip {moved}", result.stderr)
        self.assertNotEqual(self.attempted(result), moved)
        self.assertNotIn("unchanged", (result.stdout + result.stderr).lower())
        self.assertIn("Outcome: **UNCERTAIN**", self.summary.read_text())
        self.assert_pushes(1)

    def test_registry_absent_refused_as_pre_cutover(self):
        result = self.publish("publish")
        self.assert_refused(result, "pre-cutover", "")
        self.assertNotEqual(self.check("cutover").returncode, 0)

    def test_signed_journal_identical_to_registry_refused(self):
        base = self.create_registry()
        self.assert_refused(self.publish("publish", signed=self.signed_dir(journal=self.data.j1)),
                            "commit path set is not exactly the journal (modified) and evidence (added)", base)

    def test_registry_changed_between_verification_and_publication_refused(self):
        self.create_registry()
        mover = self.tmp / "mover"
        self.git("clone", "--quiet", "--branch", "registry", str(self.remote), str(mover))
        self.write(mover / JOURNAL_PATH, self.data.j1 + b" ")
        self.git("-C", str(mover), "commit", "--quiet", "-am", "synthetic concurrent change")
        self.git("-C", str(mover), "push", "--quiet", "origin", "registry")
        moved = self.ref("registry")
        self.assert_refused(self.publish("publish"), "registry journal changed since verification", moved)
        self.assertNotEqual(self.check("cutover").returncode, 0)

    def test_manifest_and_digest_mismatches_refused(self):
        base = self.create_registry()
        for overrides, reason in (
            ({"MANIFEST_SHA256": "0" * 64}, "does not match the signing job"),
            ({"REVERIFIED_MANIFEST_SHA256": "0" * 64}, "does not match the reverify job"),
            ({"PREDECESSOR_SHA256": "0" * 64}, "registry journal changed since verification"),
            ({"CORE_VERSION": "2026.9.3b0"}, "not an exact stable release"),
            ({"EVIDENCE_SHA256": "0" * 64}, "path set is not exactly"),
        ):
            with self.subTest(reason=reason):
                self.assert_refused(self.publish("publish", **overrides), reason, base)
        tampered = self.signed_dir(evidence=self.data.ev2 + b" ", name="tampered")
        self.assert_refused(self.publish("publish", signed=tampered),
                            "evidence digest does not match the reviewed value", base)
        mismatch = self.signed_dir(name="mismatch")
        (mismatch / "files" / JOURNAL_PATH).write_bytes(self.j2 + b" ")
        self.assert_refused(self.publish("publish", signed=mismatch),
                            "files do not match their manifest", base)

    def test_path_set_with_anything_else_refused(self):
        base = self.create_registry()
        extra = self.signed_dir(extra={"README.md": b"injected\n"}, name="extra")
        self.assert_refused(self.publish("publish", signed=extra),
                            "signed manifest path set is not exactly the journal and evidence", base)
        unlisted = self.signed_dir(name="unlisted")
        self.write(unlisted / "files" / ".github" / "workflows" / "x.yml", b"injected\n")
        self.assert_refused(self.publish("publish", signed=unlisted),
                            "signed artifact path set is not exactly the journal and evidence", base)
        linked = self.signed_dir(name="linked")
        target = linked / "files" / JOURNAL_PATH
        target.unlink()
        target.symlink_to(self.tmp / "seed" / JOURNAL_PATH)
        self.assert_refused(self.publish("publish", signed=linked), "contains a link or special file", base)

    def test_existing_evidence_file_refused(self):
        repo = str(self.seed_dir)
        self.write(self.seed_dir / evidence_path("2026.9.3", self.data.ev2), self.data.ev2)
        self.git("-C", repo, "add", "-A")
        self.git("-C", repo, "commit", "--quiet", "-m", "synthetic duplicate evidence")
        base = self.create_registry()
        self.assert_refused(self.publish("publish"), "evidence file already exists", base)

    def test_push_target_other_than_registry_refused(self):
        base = self.create_registry()
        source = PUBLISH_SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r'^readonly TARGET_REF="refs/heads/registry"$', source, re.M)), 1)
        pushes = [line.strip() for line in source.splitlines() if re.search(r"\bpush\s+--", line)]
        self.assertEqual(pushes, ['git -C "$WORK_DIR" "${credentials[@]}" push --quiet --no-verify -- '
                                  '"$REMOTE_URL" "$refspec" || push_status=$?'])
        lines = source.splitlines()
        push_line = next(i for i, line in enumerate(lines) if line.strip() == pushes[0])
        self.assertEqual(lines[push_line - 2].strip(), "dispatched=1")
        self.assertNotIn("refuse", "\n".join(lines[push_line:]))
        self.assertEqual(source.count('timeout "$READBACK_SECONDS"'), 1)
        self.assertNotRegex(source, r"\b(python|pip)[0-9.]*\b(?! or package)")
        injected = self.tmp / "injected-publish.sh"
        injected.write_text(source.replace('TARGET_REF="refs/heads/registry"', 'TARGET_REF="refs/heads/main"'),
                            encoding="utf-8")
        result = self.publish("publish", script=injected)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("push target is not the registry branch", result.stderr)
        self.assertEqual(self.ref("main"), self.main_tip)
        self.assertEqual(self.ref("registry"), base)

    def test_mode_and_inputs_required(self):
        base = self.create_registry()
        self.assert_refused(self.publish("push"), "mode must be assemble or publish", base)
        self.assert_refused(self.publish("publish", REMOTE_URL=""), "REMOTE_URL is required", base)
        existing = self.tmp / "work-existing"
        existing.mkdir()
        self.assert_refused(self.publish("publish", WORK_DIR=str(existing)), "work directory already exists", base)

    def test_check_script_refuses_unexpected_registry_content(self):
        repo = str(self.seed_dir)
        self.write(self.seed_dir / "upstream-trust" / "notes.txt", b"not registry data\n")
        self.git("-C", repo, "add", "-A")
        self.git("-C", repo, "commit", "--quiet", "-m", "synthetic extra path")
        self.create_registry()
        result = self.check("cutover")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected path", result.stderr)

    def test_check_script_last_publish_refuses_the_cutover_commit(self):
        self.create_registry()
        result = self.check("last-publish")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("single-parent", result.stderr)


def fallback_steps() -> dict[int, str]:
    """The local fallback recipe's fenced bash steps, keyed by step number."""
    text = REGISTRY_DOC.read_text(encoding="utf-8")
    return {int(number): body for body, number in
            re.findall(r"```bash\n(# fallback step (\d): .*?)```", text, re.S)}


@unittest.skipUnless(HAVE_SHELL_TOOLS, "bash, git and sha256sum are required")
class LocalFallbackRecipeTests(RegistryRepository, unittest.TestCase):
    """R1-L2: the documented local fallback, run verbatim against a bare remote.

    Step 2 signs with the owner key and verifies against the committed public
    key, which tests cannot hold; it is replaced by the same runtime verifier
    with an ephemeral key. Steps 1 and 3 run exactly as written.
    """

    def run_step(self, number, fb):
        env = {**self.env, "V": "2026.9.3", "CANDIDATE": str(self.tmp / "core-candidate.json"),
               "EVIDENCE": str(self.tmp / "core-review-evidence.json"), "REMOTE": str(self.remote),
               "FB": str(fb)}
        return subprocess.run(["bash", "-euo", "pipefail", "-c", fallback_steps()[number]], env=env,
                              cwd=ROOT, capture_output=True, text=True, timeout=120)

    def prepare(self):
        base = self.create_registry()
        (self.tmp / "core-candidate.json").write_bytes(self.data.c2)
        (self.tmp / "core-review-evidence.json").write_bytes(self.data.ev2)
        fb = self.tmp / "fallback"
        fb.mkdir()
        step1 = self.run_step(1, fb)
        self.assertEqual(step1.returncode, 0, step1.stderr)
        self.assertEqual((fb / "predecessor.json").read_bytes(), self.data.j1)
        # Step 2 stand-in: the runtime verifier accepts the signed successor.
        tap.runtime_successor(journal=self.j2, predecessor=(fb / "predecessor.json").read_bytes(),
                              candidate=tap.parse_candidate(self.data.c2),
                              public_key=self.data.public_key, now=NOW)
        (fb / "signed-core-journal.json").write_bytes(self.j2)
        return base, fb

    def test_recipe_is_bounded_and_reuses_the_guarded_script(self):
        steps = fallback_steps()
        self.assertEqual(sorted(steps), [0, 1, 2, 3])
        self.assertIn("REMOTE=https://github.com/jeter-1/hass-mcp-admin.git\n", steps[0])
        recipe = "".join(steps[number] for number in (1, 2, 3))
        self.assertNotIn("https://", recipe)
        self.assertNotRegex(recipe, r"\bmain\b|--force|\bgit\b[^\n]*\bpush\b|\bcommit\b")
        self.assertIn("core_registry_one_tap.py verify-signed", steps[2])
        self.assertIn("--public-key docs/evidence/hamcp222/core-public-key.txt", steps[2])
        self.assertTrue(steps[3].rstrip().endswith("bash scripts/core_registry_publish.sh publish"))

    def test_recipe_publishes_one_child_commit_to_registry(self):
        base, fb = self.prepare()
        result = self.run_step(3, fb)
        self.assertEqual(result.returncode, 0, result.stderr)
        tip = self.ref("registry")
        self.assertIn(f"Published {tip} to registry.", result.stdout)
        remote = str(self.remote)
        self.assertEqual(self.git("-C", remote, "rev-list", "--parents", "-n", "1", tip), f"{tip} {base}")
        self.assertEqual(self.git("-C", remote, "diff-tree", "--no-commit-id", "--name-status", "-r", base, tip),
                         f"A\t{evidence_path('2026.9.3', self.data.ev2)}\nM\t{JOURNAL_PATH}")
        self.assertEqual(self.ref("main"), self.main_tip)
        self.assertEqual(self.check("last-publish").returncode, 0)

    def test_recipe_refuses_a_concurrent_predecessor_change_without_overwriting_it(self):
        _, fb = self.prepare()
        mover = self.mover()
        self.git("-C", str(mover), "push", "--quiet", "origin", "registry")
        moved = self.ref("registry")
        result = self.run_step(3, fb)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("registry journal changed since verification", result.stderr)
        self.assertIn("registry is unchanged", result.stderr)
        self.assertEqual(self.ref("registry"), moved)
        self.assertEqual(self.ref("main"), self.main_tip)


def gate_text(path: Path, start: str, end: str) -> str:
    text = path.read_text(encoding="utf-8")
    first = text.index(start)
    section = text[first:text.index(end, first)]
    return " ".join(section.replace("> ", " ").split())


class CoreUpgradeGateDocumentTests(unittest.TestCase):
    """R1-M1: the no-Core-upgrade gate precedes deployment and fails main-only releases."""

    def test_runbook_gate_precedes_every_deployment_step_in_order(self):
        text = RUNBOOK_DOC.read_text(encoding="utf-8")
        gate = text.index("## 0. STOP: no Home Assistant Core upgrade before the gate")
        self.assertLess(gate, text.index("## 1. Prerequisites"))
        self.assertLess(gate, text.index("## 6. Deploy and confirm the runtime reads `registry`"))
        section = gate_text(RUNBOOK_DOC, "## 0. STOP", "## 1. Prerequisites")
        self.assertIn("**Do not upgrade Home Assistant Core** until all four of these are done, in this order:",
                      section)
        ordered = ["1. sequence 16 is live on `main`",
                   "2. `registry` is created byte-identical from `main`, and the `cutover` hash check passes",
                   "3. this beta is deployed",
                   "4. Core health shows the **same** `sequence`, now fetched from `registry`"]
        positions = [section.index(item) for item in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("**Every later Core upgrade:** before upgrading, the exact target release, its sequence "
                      "and its reviewed profiles must already be published on `registry`", section)
        self.assertIn("A release present only on `main` does **not** count", section)
        self.assertIn("retires the prior authority, and the verified cache does not authorize a new version",
                      section)

    def test_acceptance_checklist_fails_a_release_only_on_main(self):
        section = gate_text(ACCEPTANCE_DOC, "## 4. Owner cutover", "If a step fails")
        item = section[section.index("6. Core-upgrade gate (runbook step 0"):]
        self.assertIn("the exact target release, its sequence and its reviewed profiles were published on "
                      "`registry`", item)
        self.assertIn("**FAIL if the target release exists only on `main`**", item)

    def test_registry_cutover_section_mirrors_the_gate(self):
        section = gate_text(REGISTRY_DOC, "### Canonical source, frozen `main` copy and rollback",
                            "### Manual flow for each publication")
        self.assertIn("**No Home Assistant Core upgrade until the cutover is complete**", section)
        self.assertIn("a release present only on `main` does not count", section)
        self.assertIn("Observing a new Core version retires the prior authority, and the verified cache "
                      "does not authorize a new version", section)

    def test_publication_outcomes_are_qualified_in_the_docs(self):
        registry = " ".join(REGISTRY_DOC.read_text(encoding="utf-8").split())
        self.assertNotIn("Every refusal leaves `registry` unchanged", registry)
        self.assertNotIn("publish the same two files to `registry` by hand", registry)
        self.assertIn("Only this outcome leaves `registry` unchanged.", registry)
        for document in (registry, " ".join(RUNBOOK_DOC.read_text(encoding="utf-8").split())):
            for outcome in ("**PUBLISHED**", "**UNCHANGED**", "**UNCERTAIN**"):
                self.assertIn(outcome, document)


MAIN_GUARD = ("github.ref == 'refs/heads/main' && github.repository == 'jeter-1/hass-mcp-admin' "
              "&& github.event_name == 'workflow_dispatch'")
REAL_JOBS = {"sign", "reverify", "publish"}
DRY_JOBS = {"dry-run-sign", "dry-run-publish"}
PINNED_ACTION = re.compile(r"actions/(checkout|setup-python|upload-artifact|download-artifact)@[0-9a-f]{40}")


def run_text(job) -> str:
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


class UniqueKeyLoader(yaml.SafeLoader):
    """Refuses duplicate mapping keys, which PyYAML would silently overwrite."""


def _unique_mapping(loader, node, deep=False):
    keys = [loader.construct_object(key, deep=deep) for key, _ in node.value]
    duplicates = sorted({str(k) for k in keys if keys.count(k) > 1})
    if duplicates:
        raise yaml.constructor.ConstructorError(None, None, f"duplicate keys {duplicates}", node.start_mark)
    return loader.construct_mapping(node, deep=deep)


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_workflow(source: str):
    return yaml.load(source, Loader=UniqueKeyLoader)


def workflow_violations(document) -> list[str]:
    """Structural authority checks for the one-tap workflow (HAMCP-278)."""
    found = []
    triggers = document.get("on", document.get(True)) or {}
    if set(triggers) != {"workflow_dispatch", "workflow_call"}:
        found.append("triggers must be workflow_dispatch and workflow_call only")
    dispatch = (triggers.get("workflow_dispatch") or {}).get("inputs", {})
    if dispatch.get("dry_run", {}).get("default") is not True:
        found.append("dry_run must default to true")
    if document.get("permissions") != {"contents": "read"}:
        found.append("top-level permissions must be contents: read")
    jobs = document.get("jobs", {})
    if set(jobs) != {"verify", *REAL_JOBS, *DRY_JOBS}:
        found.append("unexpected job set")
    for name, job in jobs.items():
        text, runs = yaml.safe_dump(job), run_text(job)
        expected = {"contents": "write"} if name == "publish" else {"contents": "read"}
        if job.get("permissions") != expected:
            found.append(f"{name}: permissions")
        if ("environment" in job) != (name == "sign"):
            found.append(f"{name}: environment")
        if ("secrets." in text) != (name == "sign"):
            found.append(f"{name}: secrets reference")
        if bool(re.search(r"github\.token|GH_TOKEN|GITHUB_TOKEN", text)) != (name == "publish"):
            found.append(f"{name}: token reference")
        guard = str(job.get("if", ""))
        if not guard.startswith(MAIN_GUARD):
            found.append(f"{name}: protected-main dispatch guard")
        if name in REAL_JOBS and not guard.endswith("&& inputs.dry_run == false"):
            found.append(f"{name}: must not run in a dry run")
        if name in DRY_JOBS and not guard.endswith("&& inputs.dry_run == true"):
            found.append(f"{name}: must run only in a dry run")
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses and not PINNED_ACTION.fullmatch(uses):
                found.append(f"{name}: action not SHA-pinned or not allowed")
            if uses and uses.startswith("actions/checkout@") and \
                    (step.get("with") or {}).get("persist-credentials") is not False:
                found.append(f"{name}: checkout persists credentials")
        if re.search(r"set -x|set -o xtrace|xtrace", runs):
            found.append(f"{name}: shell tracing")
        if re.search(r"\bgit\b[^\n]*\bpush\b", runs):
            found.append(f"{name}: direct git push")
        if ("core_registry_publish.sh publish" in runs) != (name == "publish"):
            found.append(f"{name}: publish mode")
        if ("prepare_core_release_registry.py sign" in runs) != (name == "sign"):
            found.append(f"{name}: signer invocation")
        if name == "publish" and ("setup-python" in text or re.search(r"\b(python|pip)\b", runs)):
            found.append("publish: runs Python")
    sign_job = jobs.get("sign", {})
    if "secrets." in yaml.safe_dump(sign_job.get("env") or {}):
        found.append("sign: the secret must not be in job env")
    if sign_job.get("environment") != "core-release-registry-signing":
        found.append("sign: environment name")
    secret_steps = [s for s in sign_job.get("steps", []) if "secrets." in yaml.safe_dump(s)]
    if len(secret_steps) != 1 or (secret_steps[0].get("env") or {}) != {
            "HA_CORE_RELEASE_REGISTRY_SIGNING_KEY": "${{ secrets.HA_CORE_RELEASE_REGISTRY_SIGNING_KEY }}"} \
            or "set +x" not in str(secret_steps[0].get("run", "")):
        found.append("sign: the secret must live in exactly one untraced step env")
    if set(jobs.get("publish", {}).get("needs", [])) != {"verify", "sign", "reverify"}:
        found.append("publish: must need verify, sign and reverify")
    if set(jobs.get("reverify", {}).get("needs", [])) != {"verify", "sign"}:
        found.append("reverify: must need verify and sign")
    for name, signer, reverified in (
            ("publish", "needs.sign.outputs.manifest_sha256",
             "needs.reverify.outputs.reverified_manifest_sha256"),
            ("dry-run-publish", "needs.dry-run-sign.outputs.manifest_sha256",
             "steps.reverify.outputs.reverified_manifest_sha256")):
        steps = jobs.get(name, {}).get("steps") or [{}]
        env = steps[-1].get("env") or {}
        binding = {
            "PREDECESSOR_SHA256": "${{ needs.verify.outputs.predecessor_sha256 }}",
            "MANIFEST_SHA256": "${{ %s }}" % signer,
            "REVERIFIED_MANIFEST_SHA256": "${{ %s }}" % reverified,
            "CORE_VERSION": "${{ inputs.core_version }}",
            "EVIDENCE_SHA256": "${{ inputs.evidence_sha256 }}",
        }
        if any(env.get(key) != value for key, value in binding.items()):
            found.append(f"{name}: publication digests must bind verify's predecessor and the signed manifest")
    return found


class WorkflowStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.document = load_workflow(cls.source)

    def test_committed_workflow_has_no_violation(self):
        self.assertEqual(workflow_violations(self.document), [])
        self.assertNotIn("pull_request", self.source)
        self.assertIn("::error::pre-cutover:", self.source)
        self.assertIn("^registry-candidates/", self.source)
        self.assertIn("docs/evidence/hamcp222/core-public-key.txt", self.source)

    def test_duplicate_keys_are_refused(self):
        # A duplicated job key (for example a second `env:` carrying the secret)
        # would otherwise be dropped by the parser and hidden from every check.
        injected = self.source.replace(
            "  dry-run-sign:\n    needs: verify\n",
            "  dry-run-sign:\n    needs: verify\n    env:\n"
            "      K: ${{ secrets.HA_CORE_RELEASE_REGISTRY_SIGNING_KEY }}\n", 1)
        self.assertNotEqual(injected, self.source)
        with self.assertRaisesRegex(yaml.constructor.ConstructorError, "duplicate keys"):
            load_workflow(injected)
        self.assertEqual(self.source.count("secrets."), 1)

    def test_injected_violations_are_detected(self):
        def job(name):
            return lambda d: d["jobs"][name]

        def add_env(name, key, value, step=-1):
            def mutate(d):
                d["jobs"][name]["steps"][step].setdefault("env", {})[key] = value
            return mutate

        def triggers(d):
            return d.get("on", d.get(True))

        cases = {
            "pull_request_target trigger": lambda d: triggers(d).__setitem__("pull_request_target", {}),
            "pull_request trigger": lambda d: triggers(d).__setitem__("pull_request", {}),
            "dry run default false": lambda d: triggers(d)["workflow_dispatch"]["inputs"]["dry_run"]
            .__setitem__("default", False),
            "environment on publish": lambda d: job("publish")(d).__setitem__(
                "environment", "core-release-registry-signing"),
            "environment on verify": lambda d: job("verify")(d).__setitem__(
                "environment", "core-release-registry-signing"),
            "environment on dry-run-sign": lambda d: job("dry-run-sign")(d).__setitem__(
                "environment", "core-release-registry-signing"),
            "secret in verify": add_env("verify", "K", "${{ secrets.HA_CORE_RELEASE_REGISTRY_SIGNING_KEY }}"),
            "secret in publish": add_env("publish", "K", "${{ secrets.HA_CORE_RELEASE_REGISTRY_SIGNING_KEY }}"),
            "secret in dry-run-sign": add_env("dry-run-sign", "K",
                                              "${{ secrets.HA_CORE_RELEASE_REGISTRY_SIGNING_KEY }}"),
            "secret in a second sign step": add_env("sign", "K",
                                                    "${{ secrets.HA_CORE_RELEASE_REGISTRY_SIGNING_KEY }}", 6),
            "secret at sign job env": lambda d: job("sign")(d)["env"].__setitem__(
                "HA_CORE_RELEASE_REGISTRY_SIGNING_KEY", "${{ secrets.HA_CORE_RELEASE_REGISTRY_SIGNING_KEY }}"),
            "write on sign": lambda d: job("sign")(d).__setitem__("permissions", {"contents": "write"}),
            "token on sign": add_env("sign", "GH_TOKEN", "${{ github.token }}"),
            "write on dry-run-publish": lambda d: job("dry-run-publish")(d).__setitem__(
                "permissions", {"contents": "write"}),
            "token on dry-run-publish": add_env("dry-run-publish", "GH_TOKEN", "${{ github.token }}"),
            "dry run pushes": lambda d: job("dry-run-publish")(d)["steps"][-1].__setitem__(
                "run", "bash scripts/core_registry_publish.sh publish"),
            "publish runs python": lambda d: job("publish")(d)["steps"].insert(
                1, {"run": "python -c 'print(1)'"}),
            "publish sets up python": lambda d: job("publish")(d)["steps"].insert(
                1, {"uses": "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"}),
            "publish signs": lambda d: job("publish")(d)["steps"].insert(
                1, {"run": "bash -c 'prepare_core_release_registry.py sign'"}),
            "direct git push": lambda d: job("publish")(d)["steps"][-1].__setitem__(
                "run", "git push origin HEAD:registry"),
            "unpinned action": lambda d: job("reverify")(d)["steps"][0].__setitem__(
                "uses", "actions/checkout@v5"),
            "third-party action": lambda d: job("verify")(d)["steps"].insert(
                0, {"uses": "someone/action@" + "a" * 40}),
            "persisted credentials": lambda d: job("publish")(d)["steps"][0]["with"].__setitem__(
                "persist-credentials", True),
            "unguarded sign": lambda d: job("sign")(d).__setitem__("if", "inputs.dry_run == false"),
            "sign in a dry run": lambda d: job("sign")(d).__setitem__("if", MAIN_GUARD),
            "dry run on the real path": lambda d: job("dry-run-sign")(d).__setitem__(
                "if", MAIN_GUARD + " && inputs.dry_run == false"),
            "publish without reverify": lambda d: job("publish")(d).__setitem__("needs", ["verify", "sign"]),
            "publish predecessor from another job": add_env(
                "publish", "PREDECESSOR_SHA256", "${{ needs.sign.outputs.manifest_sha256 }}"),
            "publish manifest unbound from sign": add_env(
                "publish", "MANIFEST_SHA256", "${{ needs.reverify.outputs.reverified_manifest_sha256 }}"),
            "publish evidence digest unbound": add_env(
                "publish", "EVIDENCE_SHA256", "${{ needs.verify.outputs.predecessor_sha256 }}"),
            "dry-run predecessor from another job": add_env(
                "dry-run-publish", "PREDECESSOR_SHA256", "${{ needs.dry-run-sign.outputs.manifest_sha256 }}"),
            "tracing in sign": lambda d: job("sign")(d)["steps"][5].__setitem__(
                "run", "set -x\n" + job("sign")(d)["steps"][5]["run"]),
            "untraced guard removed": lambda d: job("sign")(d)["steps"][5].__setitem__(
                "run", job("sign")(d)["steps"][5]["run"].replace("set +x\n", "")),
            "write at top level": lambda d: d.__setitem__("permissions", {"contents": "write"}),
            "extra job": lambda d: d["jobs"].__setitem__("extra", copy.deepcopy(d["jobs"]["verify"])),
        }
        for label, mutate in cases.items():
            document = copy.deepcopy(self.document)
            mutate(document)
            with self.subTest(label=label):
                self.assertTrue(workflow_violations(document), label)

    def test_sign_step_index_used_by_injections_is_the_secret_step(self):
        step = self.document["jobs"]["sign"]["steps"][5]
        self.assertIn("HA_CORE_RELEASE_REGISTRY_SIGNING_KEY", step.get("env", {}))


if __name__ == "__main__":
    unittest.main()
