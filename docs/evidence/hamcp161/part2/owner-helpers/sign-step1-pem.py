"""Owner-run PEM input adapter for the exact reviewed HAMCP-161 step 1.

Uses the unchanged reviewed signer. No network, key output, converted key files,
environment export, repository edits, publication, or overwrite of signed output.
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import warnings

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path("/home/josh/repos/hass-mcp-admin/.claude/worktrees/bridge-cse_014c98dy1HEXGCFiGhDwAYXA")
OWNER = Path(__file__).resolve().parent
HEAD = "e47b8c03aaedcc0591a4a68216bdfcaa5ab54152"
PUBLIC_SHA = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"
CANDIDATE_SHA = "faa529e8957178882b532731f154e1d7952755012e2a8ba30450d5dc68027d8b"
PREVIOUS_SHA = "1d3530345c0d923bda17951c28ddf913e5c8e07d6fa69ca2d258302b418bbf4a"
SIGNER_SHA = "6e09d3f96e79253f730604ffb10caffe69d77c183fc734f5517a3733cdf4b368"
CHECKER_SHA = "79de12007444cc387be99e07cab2e731ace778715d94226226a961fb83c0fb94"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition):
    if not condition:
        raise ValueError("check refused")


def load_matching_key(path, expected_public_sha, password_reader=None):
    with path.open("rb") as stream:
        pem = stream.read(65537)
    require(0 < len(pem) <= 65536)
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except TypeError:
        reader = password_reader or getpass.getpass
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = reader("PEM passphrase (hidden): ").encode("utf-8")
        key = serialization.load_pem_private_key(pem, password=password)
    require(isinstance(key, Ed25519PrivateKey))
    require(sha(key.public_key().public_bytes_raw()) == expected_public_sha)
    return key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pem", type=Path, required=True)
    args = parser.parse_args()
    phase = "public preflight"
    output = OWNER / "journal-1.json"
    try:
        require(not output.exists())
        actual_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        require(actual_head == HEAD)
        subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=ROOT, check=True)
        signer = ROOT / "scripts/prepare_core_release_registry.py"
        checker = ROOT / "docs/evidence/hamcp161/check_step.py"
        candidate = OWNER / "candidate-1-add-2026.10.1-r1.json"
        previous = ROOT / "upstream-trust/ha-core-release-registry.json"
        candidate_raw, previous_raw = candidate.read_bytes(), previous.read_bytes()
        require(sha(signer.read_bytes()) == SIGNER_SHA)
        require(sha(checker.read_bytes()) == CHECKER_SHA)
        require(sha(candidate_raw) == CANDIDATE_SHA)
        require(sha(previous_raw) == PREVIOUS_SHA)
        spec = importlib.util.spec_from_file_location("hamcp161_reviewed_signer", signer)
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        check_args = ["--previous", str(previous), "--candidate", str(candidate)]
        subprocess.run([sys.executable, "-B", str(checker), "candidate", "1", *check_args],
                       cwd=ROOT, check=True)
        value = prepare.strict_json(candidate_raw)
        now = datetime.now(timezone.utc)
        require(prepare.parse_utc_timestamp(value["envelope"]["generated_at"]) <= now
                < prepare.parse_utc_timestamp(value["envelope"]["expires_at"]))
        phase = "PEM loading / reviewed public-key match"
        key = load_matching_key(args.pem, PUBLIC_SHA)
        print("PEM key matches the reviewed Core public key.", flush=True)
        phase = "reviewed signing"
        signed_raw = prepare.sign_candidate(
            candidate_raw, expected_sha256=CANDIDATE_SHA, previous=previous_raw, key=key)
        del key
        phase = "new signed-file write"
        prepare.write_new(output, signed_raw)
        print("Prepared output SHA256: " + sha(signed_raw), flush=True)
        phase = "public verification of signed output"
        subprocess.run([sys.executable, "-B", str(checker), "signed", "1", *check_args,
                        "--signed", str(output)], cwd=ROOT, check=True)
        return 0
    except (Exception, KeyboardInterrupt):
        print("STOP: " + phase + " failed. No private-key details are displayed.")
        if output.exists():
            print("journal-1.json exists; retain it for public verification before any retry.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
