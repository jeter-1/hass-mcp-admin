"""Core registry one-tap pipeline checks (HAMCP-278); offline and key-free.

This wraps, without changing, the preparation utility's non-signing checks and
the Engineering runtime's own journal verifier. It never reads the owner's
private key, fetches, publishes or contacts Home Assistant. The workflow
supplies files it fetched; this module only verifies and summarizes them.

Subcommands:

- ``verify-candidate``: digest-bind the reviewed candidate and evidence, check
  the candidate against the current ``registry`` journal with the utility's
  existing transition checks, and render the approval summary.
- ``verify-signed``: require the runtime verifier to accept the signed journal
  as the exact successor of the predecessor, carrying exactly the candidate.
- ``dry-run-sign``: sign a re-keyed copy of the candidate with an ephemeral key
  generated in-process; the real key is never referenced.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import sys
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_core_release_registry as utility  # noqa: E402

from ha_mcp_engineering.ha_core_readmission.registry import (  # noqa: E402
    CORE_REGISTRY_URL, CoreReleaseRegistry,
)
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402
from ha_mcp_engineering.signed_registry.models import parse_utc_timestamp  # noqa: E402

# Raw Ed25519 public-key SHA-256 of Josh's Core registry key, as committed in
# docs/evidence/hamcp161|182|222/core-public-key.txt. Rotation is a separate
# owner operation and needs a reviewed change here.
OWNER_CORE_KEY_SHA256 = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"
CORE_VERSION = re.compile(r"(20[0-9]{2})\.(1[0-2]|[1-9])\.(0|[1-9][0-9]*)")
SHA256 = re.compile(r"[0-9a-f]{64}")
CLOCK_SKEW = timedelta(seconds=300)
APPROVAL_LINE = (
    "Approving signs this exact candidate with Josh's Core registry key; "
    "a separate job then pushes it to the registry branch."
)


class Refusal(Exception):
    """A bounded, fixed refusal reason; never carries supplied values."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_public_key(raw: bytes, *, pinned: bool) -> Ed25519PublicKey:
    try:
        key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(raw.strip(), validate=True))
    except Exception:
        raise Refusal("public key is not a base64 Ed25519 key") from None
    if pinned and sha256(key.public_bytes_raw()) != OWNER_CORE_KEY_SHA256:
        raise Refusal("public key is not the committed owner Core key")
    return key


def version_key(version: str) -> tuple[int, int, int]:
    match = CORE_VERSION.fullmatch(version)
    if match is None:
        raise Refusal("Core version is not an exact stable release")
    return tuple(int(part) for part in match.groups())


def require_digest(raw: bytes, expected: str, what: str) -> None:
    if not SHA256.fullmatch(expected or ""):
        raise Refusal(f"{what} SHA-256 input is malformed")
    if sha256(raw) != expected:
        raise Refusal(f"{what} SHA-256 does not match the reviewed value")


def parse_candidate(raw: bytes) -> dict:
    """Closed candidate shape, mirroring the utility's signing precheck."""
    try:
        value = utility.strict_json(raw)
    except Exception:
        raise Refusal("candidate is not strict JSON") from None
    if not isinstance(value, dict):
        raise Refusal("candidate fields invalid")
    extension = value.get("operation") == "extend-capabilities"
    fields = {"model", "operation", "public_key_sha256",
              "previous_journal_sha256", "review_evidence_sha256", "envelope"}
    if extension:
        fields.add("extension_review")
    if set(value) != fields or value["model"] != (
            utility.EXTENSION_MODEL if extension else utility.MODEL):
        raise Refusal("candidate fields invalid")
    if value["operation"] not in {"add", "revoke", "extend-capabilities"}:
        raise Refusal("candidate operation invalid")
    if not isinstance(value["envelope"], dict):
        raise Refusal("candidate envelope invalid")
    return value


def predecessor_entry(tip, version: str):
    """Same-version entry, else the nearest lower released version."""
    if tip is None:
        return None
    entries = {entry.version: entry for entry in tip.entries}
    if version in entries:
        return entries[version]
    lower = [v for v in entries if version_key(v) < version_key(version)]
    return entries[max(lower, key=version_key)] if lower else None


def verify_candidate(*, candidate: bytes, evidence: bytes, predecessor: bytes,
                     public_key: Ed25519PublicKey, core_version: str,
                     candidate_sha256: str, evidence_sha256: str,
                     now: datetime | None = None) -> dict:
    """Run the utility's non-signing checks against the current journal."""
    timestamp = now or datetime.now(timezone.utc)
    version_key(core_version)
    require_digest(candidate, candidate_sha256, "candidate")
    require_digest(evidence, evidence_sha256, "evidence")
    value = parse_candidate(candidate)
    if value["review_evidence_sha256"] != evidence_sha256:
        raise Refusal("candidate does not bind the reviewed evidence")
    if value["public_key_sha256"] != sha256(public_key.public_bytes_raw()):
        raise Refusal("candidate was prepared for a different Core key")
    if value["previous_journal_sha256"] != sha256(predecessor):
        raise Refusal("candidate predecessor is not the current registry journal")
    try:
        current = utility.parse_journal(predecessor, public_key)
    except Exception:
        raise Refusal("current registry journal does not verify") from None
    tip = current.accepted
    envelope = value["envelope"]
    if (envelope.get("sequence") != tip.sequence + 1
            or envelope.get("previous_registry_sha256") != tip.content_digest):
        raise Refusal("candidate is not the next sequence of the current journal")
    try:
        utility.validate_transition(value, current, timestamp)
    except ValueError as exc:
        raise Refusal(f"utility transition check refused: {exc}") from None
    except Exception:
        raise Refusal("utility transition check refused the candidate") from None
    try:
        generated = parse_utc_timestamp(envelope["generated_at"])
        expires = parse_utc_timestamp(envelope["expires_at"])
    except Exception:
        raise Refusal("candidate timestamps invalid") from None
    if (not generated - CLOCK_SKEW <= timestamp < expires
            or expires - generated > timedelta(days=90)):
        raise Refusal("candidate is expired or not yet valid")
    before = {e.version: e.to_mapping() for e in tip.entries}
    after = {e["version"]: e for e in envelope["entries"]}
    if value["operation"] == "revoke":
        denied = {r["version"] for r in envelope["revocations"]}
        prior = {r.release_identity[1] for r in tip.revocations}
        if core_version not in denied or core_version in after or (
                core_version in prior and core_version not in before):
            raise Refusal("candidate does not revoke the requested Core version")
    else:
        target = after.get(core_version)
        if target is None or target["evidence_sha256"] != evidence_sha256:
            raise Refusal("candidate does not change the requested Core version")
        if any(before.get(v) != after[v] for v in after if v != core_version):
            raise Refusal("candidate changes a different Core version")
    carried = [c["capability_id"] for c in after.get(core_version, {}).get("capabilities", [])]
    source = predecessor_entry(tip, core_version)
    previous = [] if source is None else [c.capability_id for c in source.capabilities]
    return {
        "core_version": core_version,
        "operation": value["operation"],
        "predecessor_sequence": tip.sequence,
        "predecessor_sha256": tip.content_digest,
        "new_sequence": envelope["sequence"],
        "candidate_sha256": candidate_sha256,
        "evidence_sha256": evidence_sha256,
        "generated_at": envelope["generated_at"],
        "expires_at": envelope["expires_at"],
        "source_version": None if source is None else source.version,
        "carried": carried,
        "withheld": [c for c in previous if c not in carried],
        "added": [c for c in carried if c not in previous],
    }


def render_summary(facts: dict, *, dry_run: bool) -> str:
    lines = ["## Core registry approval summary", ""]
    if dry_run:
        lines += ["**DRY RUN.** An ephemeral test key signs a re-keyed copy; "
                  "nothing is pushed and the owner key is not used.", ""]
    rows = (
        ("Core version", facts["core_version"]),
        ("Operation", facts["operation"]),
        ("Predecessor sequence", facts["predecessor_sequence"]),
        ("Predecessor entry hash", facts["predecessor_sha256"]),
        ("New sequence", facts["new_sequence"]),
        ("Candidate SHA-256", facts["candidate_sha256"]),
        ("Evidence SHA-256", facts["evidence_sha256"]),
        ("Generated", facts["generated_at"]),
        ("Expires", facts["expires_at"]),
        ("Compared with entry", facts["source_version"] or "none"),
    )
    lines += ["| Field | Value |", "| --- | --- |"]
    lines += [f"| {name} | `{value}` |" for name, value in rows]
    lines += ["", f"### Profiles carried ({len(facts['carried'])})", ""]
    lines += [f"- `{c}`" for c in facts["carried"]] or ["- none"]
    if facts["withheld"]:
        lines += ["", "> [!CAUTION]",
                  f"> **WITHHELD: {len(facts['withheld'])} profile(s) in the "
                  f"predecessor's `{facts['source_version']}` entry are ABSENT "
                  "from this candidate.**", ">"]
        lines += [f"> - `{c}`" for c in facts["withheld"]]
    else:
        lines += ["", "Withheld profiles: none."]
    if facts["added"]:
        lines += ["", f"### Profiles added ({len(facts['added'])})", ""]
        lines += [f"- `{c}`" for c in facts["added"]]
    lines += ["", f"**{APPROVAL_LINE}**", ""]
    return "\n".join(lines)


def runtime_successor(*, journal: bytes, predecessor: bytes, candidate: dict,
                      public_key: Ed25519PublicKey, now: datetime | None = None):
    """The Engineering runtime verifier must accept journal after predecessor."""
    encoded = base64.b64encode(public_key.public_bytes_raw()).decode("ascii")

    async def check():
        served = [predecessor]

        async def fetch(url, maximum):
            if url != CORE_REGISTRY_URL:
                raise Refusal("runtime fetched an unexpected location")
            return served[0]

        with tempfile.TemporaryDirectory() as directory:
            registry = CoreReleaseRegistry(
                enabled=True, public_key=encoded,
                cache_path=Path(directory) / "core-registry-cache.json",
                fetcher=fetch, now=None if now is None else (lambda: now))
            if not await registry.refresh() or registry.snapshot()["refresh_status"] != "accepted":
                raise Refusal("runtime verifier refused the predecessor journal")
            before = registry.authority()
            served[0] = journal
            if not await registry.refresh() or registry.snapshot()["refresh_status"] != "accepted":
                raise Refusal("runtime verifier refused the signed journal as the successor")
            return before, registry.authority()

    before, after = asyncio.run(check())
    tip = after.envelope
    if (after.sequence != before.sequence + 1
            or tip.previous_registry_sha256 != before.content_digest):
        raise Refusal("signed journal is not the exact successor")
    if tip.unsigned_mapping() != candidate["envelope"]:
        raise Refusal("signed journal does not carry exactly the reviewed candidate")
    return after


def rekey_journal(predecessor: bytes, public_key: Ed25519PublicKey,
                  key: Ed25519PrivateKey) -> bytes:
    """Re-sign a verified journal; content digests and chain links are unchanged."""
    try:
        current = utility.parse_journal(predecessor, public_key)
    except Exception:
        raise Refusal("current registry journal does not verify") from None
    unsigned = current.unsigned_mapping()
    for field in ("envelopes", "revocation_sources"):
        unsigned[field] = [utility.signed(e.unsigned_mapping(), key)
                           for e in getattr(current, field)]
    return canonical_json(utility.signed(unsigned, key)) + b"\n"


def dry_run_sign(*, candidate: bytes, predecessor: bytes,
                 public_key: Ed25519PublicKey, now: datetime | None = None):
    """Exercise the utility's real signing path with an ephemeral key only."""
    key = Ed25519PrivateKey.generate()
    ephemeral_predecessor = rekey_journal(predecessor, public_key, key)
    value = parse_candidate(candidate)
    value["public_key_sha256"] = sha256(key.public_key().public_bytes_raw())
    value["previous_journal_sha256"] = sha256(ephemeral_predecessor)
    rekeyed = canonical_json(value) + b"\n"
    try:
        journal = utility.sign_candidate(
            rekeyed, expected_sha256=sha256(rekeyed),
            previous=ephemeral_predecessor, key=key, now=now)
    except ValueError as exc:
        raise Refusal(f"utility signing refused: {exc}") from None
    return journal, ephemeral_predecessor, key.public_key()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify-candidate")
    for name in ("candidate", "evidence", "predecessor", "public-key", "summary", "outputs"):
        verify.add_argument(f"--{name}", type=Path, required=True)
    for name in ("core-version", "candidate-sha256", "evidence-sha256"):
        verify.add_argument(f"--{name}", required=True)
    verify.add_argument("--dry-run", action="store_true")
    signed = commands.add_parser("verify-signed")
    for name in ("journal", "predecessor", "candidate", "public-key"):
        signed.add_argument(f"--{name}", type=Path, required=True)
    signed.add_argument("--ephemeral", action="store_true",
                        help="Dry run only: accept the dry run's ephemeral public key")
    dry = commands.add_parser("dry-run-sign")
    for name in ("candidate", "predecessor", "public-key", "output-dir"):
        dry.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "verify-candidate":
        facts = verify_candidate(
            candidate=utility.read_bounded(args.candidate),
            evidence=utility.read_bounded(args.evidence, utility.MAX_EVIDENCE_BYTES),
            predecessor=utility.read_bounded(args.predecessor),
            public_key=load_public_key(utility.read_bounded(args.public_key, 256), pinned=True),
            core_version=args.core_version, candidate_sha256=args.candidate_sha256,
            evidence_sha256=args.evidence_sha256)
        with args.summary.open("a", encoding="utf-8") as stream:
            stream.write(render_summary(facts, dry_run=args.dry_run))
        with args.outputs.open("a", encoding="utf-8") as stream:
            stream.write(f"new_sequence={facts['new_sequence']}\n"
                         f"withheld_count={len(facts['withheld'])}\n")
        print(f"Candidate verified for sequence {facts['new_sequence']}.")
    elif args.command == "verify-signed":
        public_key = load_public_key(utility.read_bounded(args.public_key, 256),
                                     pinned=not args.ephemeral)
        after = runtime_successor(
            journal=utility.read_bounded(args.journal),
            predecessor=utility.read_bounded(args.predecessor),
            candidate=parse_candidate(utility.read_bounded(args.candidate)),
            public_key=public_key)
        print(f"Runtime verifier accepted sequence {after.sequence} as the successor.")
    else:
        public_key = load_public_key(utility.read_bounded(args.public_key, 256), pinned=True)
        candidate = utility.read_bounded(args.candidate)
        predecessor = utility.read_bounded(args.predecessor)
        journal, ephemeral_predecessor, ephemeral_key = dry_run_sign(
            candidate=candidate, predecessor=predecessor, public_key=public_key)
        runtime_successor(journal=journal, predecessor=ephemeral_predecessor,
                          candidate=parse_candidate(candidate), public_key=ephemeral_key)
        args.output_dir.mkdir()
        utility.write_new(args.output_dir / "ha-core-release-registry.json", journal)
        utility.write_new(args.output_dir / "ephemeral-predecessor.json", ephemeral_predecessor)
        utility.write_new(args.output_dir / "ephemeral-public-key.txt", base64.b64encode(
            ephemeral_key.public_bytes_raw()) + b"\n")
        print("Dry run signed with an ephemeral key; the owner key was not used.")


if __name__ == "__main__":
    try:
        main()
    except Refusal as exc:
        raise SystemExit(f"Core registry one-tap refused: {exc}.") from None
    except Exception:
        # Never export supplied values or a traceback.
        raise SystemExit("Core registry one-tap refused; review inputs and bounds.") from None
