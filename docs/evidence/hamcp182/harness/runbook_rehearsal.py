"""HAMCP-182: run the owner runbook's exact commands end to end with an EPHEMERAL PEM key.

The production key is never read. A fresh Ed25519 key re-signs the production
sequence-9 envelopes (as build_journals.py does), is written as an unencrypted
and a passphrase-encrypted PEM in a private temporary directory, and then each
runbook step runs as a subprocess, exactly as the runbook spells it:

  prepare_core_release_registry.py extend-capabilities|revoke -> check_step.py candidate
  -> sign_step.py (step 1, 3 unencrypted PEM; step 2 encrypted PEM) -> check_step.py signed

Signer refusals are also exercised, and no output may contain key material or
the passphrase. Prints a JSON report; the key and PEMs are deleted at exit.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
import prepare_core_release_registry as prepare  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey, Ed25519PublicKey)
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True, help="new directory for journals and report")
args = parser.parse_args()

REASON = "outside the CI support window; retired by owner direction (HAMCP-182)"
STEPS = {1: ("extend-capabilities", "extend", "2026.10.1"),
         2: ("extend-capabilities", "extend", "2026.10.0"),
         3: ("revoke", "revoke", "2026.9.4")}
PASSPHRASE = base64.b64encode(os.urandom(18)).decode()
key = Ed25519PrivateKey.generate()
fingerprint = hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()
SECRETS = [
    PASSPHRASE,
    base64.b64encode(key.private_bytes_raw()).decode(),
    key.private_bytes_raw().hex(),
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def twin(production: Path, production_key: Path) -> bytes:
    raw = production.read_bytes()
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(production_key.read_bytes().strip()))
    accepted = prepare.parse_journal(raw, public).accepted
    value = json.loads(raw)
    resign = lambda e: prepare.signed({k: v for k, v in e.items() if k != "signature"}, key)  # noqa: E731
    out = canonical_json(prepare.signed({
        **{k: v for k, v in value.items() if k not in {"signature", "envelopes", "revocation_sources"}},
        "envelopes": [resign(e) for e in value["envelopes"]],
        "revocation_sources": [resign(e) for e in value["revocation_sources"]],
    }, key)) + b"\n"
    assert prepare.parse_journal(out, key.public_key()).accepted.content_digest == accepted.content_digest
    return out


def run(argv, *, stdin=None):
    done = subprocess.run([sys.executable, *map(str, argv)], input=stdin, capture_output=True,
                          text=True, cwd=ROOT, start_new_session=True)  # no tty: getpass reads stdin
    text = done.stdout + done.stderr
    leaked = [i for i, s in enumerate(SECRETS) if s in text] + (["PEM"] if "PRIVATE KEY" in text else [])
    return {"argv": [Path(a).name if os.sep in str(a) else str(a) for a in argv],
            "exit": done.returncode, "output": text.strip().splitlines(), "leaked": leaked}


args.out.mkdir(parents=True, exist_ok=False)
report = {"ephemeral_public_key_sha256": fingerprint, "steps": [], "signer_controls": []}
with tempfile.TemporaryDirectory() as private:
    private = Path(private)
    os.chmod(private, 0o700)
    plain_pem, encrypted_pem = private / "core-key.pem", private / "core-key-encrypted.pem"
    for path, encryption in ((plain_pem, serialization.NoEncryption()),
                             (encrypted_pem, serialization.BestAvailableEncryption(PASSPHRASE.encode()))):
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8, encryption))
        os.chmod(path, 0o600)
    other_pem = private / "other.pem"
    other_pem.write_bytes(Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ec_pem = private / "ec.pem"
    ec_pem.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public_key = args.out / "public-key.txt"
    public_key.write_text(base64.b64encode(key.public_key().public_bytes_raw()).decode() + "\n")
    previous = args.out / "journal-0-seq9-twin.json"
    previous.write_bytes(twin(ROOT / "upstream-trust/ha-core-release-registry.json",
                              EVIDENCE / "core-public-key.txt"))
    seq9 = previous
    check = [EVIDENCE / "check_step.py"]
    rehearsal = ["--public-key", public_key, "--rehearsal-key-sha256", fingerprint]
    first_candidate = None
    for step, (operation, kind, version) in STEPS.items():
        candidate = args.out / f"candidate-{step}-{kind}-{version}.json"
        signed = args.out / f"journal-{step}.json"
        prepare_argv = [ROOT / "scripts/prepare_core_release_registry.py", operation,
                        "--entry", EVIDENCE / f"inputs/entry-{kind}-{version}.json",
                        "--evidence", EVIDENCE / f"inputs/evidence-{kind}-{version}.json",
                        "--public-key", public_key, "--previous", previous, "--output", candidate]
        if operation == "revoke":
            prepare_argv += ["--reason", REASON]
        record = {"step": step, "operation": f"{kind} {version}", "prepare": run(prepare_argv)}
        record["check_candidate"] = run(check + ["candidate", step, "--previous", previous,
                                                 "--candidate", candidate] + rehearsal)
        expected = record["check_candidate"]["output"][-2].removeprefix("Candidate SHA256: ")
        pem = encrypted_pem if step == 2 else plain_pem
        record["sign"] = run([EVIDENCE / "sign_step.py", "--key-pem", pem, "--previous", previous,
                              "--candidate", candidate, "--expected-candidate-sha256", expected,
                              "--output", signed, "--rehearsal-key-sha256", fingerprint],
                             stdin=PASSPHRASE + "\n" if step == 2 else None)
        record["check_signed"] = run(check + ["signed", step, "--previous", previous, "--candidate",
                                              candidate, "--signed", signed] + rehearsal)
        record["pem"] = "encrypted (passphrase via hidden prompt)" if step == 2 else "unencrypted"
        record["candidate_sha256"], record["journal_sha256"] = expected, sha(signed.read_bytes())
        record["ok"] = (all(record[k]["exit"] == 0 for k in ("prepare", "check_candidate", "sign", "check_signed"))
                        and record["check_signed"]["output"][-1] == "OK")
        report["steps"].append(record)
        if step == 1:
            first_candidate = (candidate, expected)
        previous = signed

    candidate, expected = first_candidate
    scratch = args.out / "refused"
    scratch.mkdir()

    def sign(name, pem, *, prev=seq9, expected_sha=expected, output=None, stdin=None):
        result = run([EVIDENCE / "sign_step.py", "--key-pem", pem, "--previous", prev,
                      "--candidate", candidate, "--expected-candidate-sha256", expected_sha,
                      "--output", output or scratch / f"{name}.json",
                      "--rehearsal-key-sha256", fingerprint], stdin=stdin)
        result["control"] = name
        result["refused"] = result["exit"] != 0 and not (output or scratch / f"{name}.json").exists()
        return result

    existing = scratch / "existing.json"
    existing.write_bytes(b"{}\n")
    report["signer_controls"] = [
        sign("another Ed25519 PEM", other_pem),
        sign("non-Ed25519 (P-256) PEM", ec_pem),
        sign("encrypted PEM, wrong passphrase", encrypted_pem, stdin="wrong\n"),
        sign("wrong expected candidate sha", plain_pem, expected_sha="0" * 64),
        sign("stale previous journal (step 1 candidate on sequence 12)", plain_pem, prev=previous),
    ]
    out = run([EVIDENCE / "sign_step.py", "--key-pem", plain_pem, "--previous", seq9,
               "--candidate", candidate, "--expected-candidate-sha256", expected,
               "--output", scratch / "default-fingerprint.json"])
    out.update(control="ephemeral PEM against the default (production) fingerprint",
               refused=out["exit"] != 0 and not (scratch / "default-fingerprint.json").exists())
    report["signer_controls"].append(out)
    out = run([EVIDENCE / "sign_step.py", "--key-pem", plain_pem, "--previous", seq9,
               "--candidate", candidate, "--expected-candidate-sha256", expected, "--output", existing,
               "--rehearsal-key-sha256", fingerprint])
    out.update(control="output already exists", refused=out["exit"] != 0 and existing.read_bytes() == b"{}\n")
    report["signer_controls"].append(out)

report["no_secret_in_any_output"] = not any(
    r["leaked"] for s in report["steps"] for r in (s["prepare"], s["check_candidate"], s["sign"], s["check_signed"])
) and not any(c["leaked"] for c in report["signer_controls"])
report["ok"] = (all(s["ok"] for s in report["steps"]) and all(c["refused"] for c in report["signer_controls"])
                and report["no_secret_in_any_output"])
(args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
print(json.dumps(report, indent=1))
raise SystemExit(0 if report["ok"] else 1)
