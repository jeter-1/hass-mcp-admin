"""HAMCP-182: run the owner runbook's exact commands end to end with an EPHEMERAL PEM key.

The production key is never read. A fresh Ed25519 key re-signs the production
sequence-9 envelopes (as build_journals.py does), is written as an unencrypted
and a passphrase-encrypted PEM in a private temporary directory, and then each
runbook step runs as a subprocess, exactly as the runbook spells it:

  prepare_core_release_registry.py extend-capabilities|revoke -> check_step.py candidate
  -> sign_step.py (step 1, 3 unencrypted PEM; step 2 encrypted PEM) -> check_step.py signed

Step 2 types the passphrase into a real pseudo-terminal after the hidden prompt
appears and proves it was not echoed. Signer misuse refusals are exercised, and
(H1, review R1) so are the hidden-input refusals: non-TTY stdin, echo control
failing or ineffective, and getpass's echoed fallback must each refuse before
any passphrase is read. Three source mutations that weaken one layer each must
make at least one of those controls fail. No output may contain key material or
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
sys.path[:0] = [str(HERE), str(EVIDENCE), str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
import prepare_core_release_registry as prepare  # noqa: E402
from terminal import on_pipe, on_pty, signer_command  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey, Ed25519PublicKey)
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402
from sign_step import NO_HIDDEN_INPUT  # noqa: E402

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


def names(argv):
    return [Path(a).name if os.sep in str(a) else str(a) for a in argv]


def leaks(text: str) -> list:
    return [i for i, s in enumerate(SECRETS) if s in text] + (["PEM"] if "PRIVATE KEY" in text else [])


def run(argv):
    done = subprocess.run([sys.executable, *map(str, argv)], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, cwd=ROOT, start_new_session=True)
    text = done.stdout + done.stderr
    return {"argv": names(argv), "exit": done.returncode, "output": text.strip().splitlines(),
            "leaked": leaks(text)}


def run_signer(argv, *, answer=None, stdin="pty", scenario="none", mutation=()):
    """sign_step.py with the passphrase typed on a PTY after the prompt, or pre-loaded on a pipe."""
    command = signer_command(EVIDENCE / "sign_step.py", argv, scenario=scenario, mutation=mutation)
    runner = on_pty if stdin == "pty" else on_pipe
    result = runner(command, answer=None if answer is None else answer.encode(), cwd=ROOT)
    result["leaked"] = leaks("\n".join(result["output"])) + (["passphrase echoed"] if result.get("echoed") else [])
    return {"argv": names(argv), "scenario": scenario, **result}


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
        sign_argv = ["--key-pem", pem, "--previous", previous, "--candidate", candidate,
                     "--expected-candidate-sha256", expected, "--output", signed,
                     "--rehearsal-key-sha256", fingerprint]
        record["sign"] = (run_signer(sign_argv, answer=PASSPHRASE) if step == 2
                          else run([EVIDENCE / "sign_step.py", *sign_argv]))
        record["check_signed"] = run(check + ["signed", step, "--previous", previous, "--candidate",
                                              candidate, "--signed", signed] + rehearsal)
        record["pem"] = ("encrypted (passphrase typed on a PTY at the hidden prompt)" if step == 2
                         else "unencrypted (no prompt)")
        record["candidate_sha256"], record["journal_sha256"] = expected, sha(signed.read_bytes())
        record["ok"] = (all(record[k]["exit"] == 0 for k in ("prepare", "check_candidate", "sign", "check_signed"))
                        and record["check_signed"]["output"][-1] == "OK"
                        and (step != 2 or (record["sign"]["prompt_shown"] and record["sign"]["answered"]
                                           and not record["sign"]["echoed"])))
        report["steps"].append(record)
        if step == 1:
            first_candidate = (candidate, expected)
        previous = signed

    candidate, expected = first_candidate
    scratch = args.out / "refused"
    scratch.mkdir()

    def sign(name, pem, *, prev=seq9, expected_sha=expected, output=None, answer=None):
        argv = ["--key-pem", pem, "--previous", prev, "--candidate", candidate,
                "--expected-candidate-sha256", expected_sha,
                "--output", output or scratch / f"{name}.json", "--rehearsal-key-sha256", fingerprint]
        result = (run_signer(argv, answer=answer) if answer is not None
                  else run([EVIDENCE / "sign_step.py", *argv]))
        result["control"] = name
        result["refused"] = result["exit"] != 0 and not (output or scratch / f"{name}.json").exists()
        return result

    existing = scratch / "existing.json"
    existing.write_bytes(b"{}\n")
    report["signer_controls"] = [
        sign("another Ed25519 PEM", other_pem),
        sign("non-Ed25519 (P-256) PEM", ec_pem),
        sign("encrypted PEM, wrong passphrase (typed on a PTY)", encrypted_pem, answer="wrong-throwaway"),
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

    # H1 (review R1): hidden input is guaranteed or nothing is read. The answer is the
    # real throwaway passphrase, so a fallback would sign; it is offered only after a prompt.
    hidden_cases = [
        ("non-TTY stdin: passphrase pre-loaded on a pipe", "pipe", PASSPHRASE, "none"),
        ("non-TTY stdin: /dev/null", "pipe", None, "none"),
        ("PTY, echo control fails (review R1 reproduction)", "pty", PASSPHRASE, "echo control fails"),
        ("PTY, echo control leaves ECHO on", "pty", PASSPHRASE, "echo flag not cleared"),
        ("PTY, echo control fails inside getpass after pre-flight", "pty", PASSPHRASE,
         "echo control fails after pre-flight"),
        ("PTY, getpass echoed-input fallback forced", "pty", PASSPHRASE, "getpass fallback forced"),
    ]

    def hidden_controls(directory, mutation=()):
        directory.mkdir()
        results = []
        for number, (name, stdin, answer, scenario) in enumerate(hidden_cases):
            output = directory / f"hidden-{number}.json"
            result = run_signer(["--key-pem", encrypted_pem, "--previous", seq9, "--candidate", candidate,
                                 "--expected-candidate-sha256", expected, "--output", output,
                                 "--rehearsal-key-sha256", fingerprint],
                                answer=answer, stdin=stdin, scenario=scenario, mutation=mutation)
            result["control"] = name
            result["refused"] = (result["exit"] != 0 and not output.exists() and not result["traceback"]
                                 and not result.get("prompt_shown") and not result.get("answered")
                                 and not result.get("echoed") and result["output"][-1:] == [NO_HIDDEN_INPUT]
                                 and (answer is None or stdin == "pty" or result["answer_left_unread"]))
            results.append(result)
        return results

    report["hidden_input_controls"] = hidden_controls(scratch / "hidden")
    mutations = {
        "pre-fix signer: plain getpass (fallback restored)": [(
            "        passphrase = read_passphrase()\n",
            '        passphrase = getpass.getpass("PEM passphrase (hidden): ").encode()\n')],
        "GetPassWarning not fatal (fallback restored)": [(
            '        warnings.simplefilter("error", getpass.GetPassWarning)\n', "")],
        "hidden-input pre-flight removed": [(
            "    require_hidden_input()\n    with warnings", "    with warnings")],
    }
    report["mutations"] = []
    for number, (name, mutation) in enumerate(mutations.items()):
        results = hidden_controls(scratch / f"mutation-{number}", mutation)
        report["mutations"].append({
            "mutation": name,
            "controls_failed": [r["control"] for r in results if not r["refused"]],
            # Expected under a mutation: the throwaway passphrase may be echoed (redacted here).
            "echoed": [r["control"] for r in results if r.get("echoed")],
            "signed": [r["control"] for r in results if r["exit"] == 0],
        })
        report["mutations"][-1]["detected"] = bool(report["mutations"][-1]["controls_failed"])

report["no_secret_in_any_output"] = not any(
    r["leaked"] for s in report["steps"] for r in (s["prepare"], s["check_candidate"], s["sign"], s["check_signed"])
) and not any(c["leaked"] for c in report["signer_controls"] + report["hidden_input_controls"])
report["ok"] = (all(s["ok"] for s in report["steps"]) and all(c["refused"] for c in report["signer_controls"])
                and all(c["refused"] for c in report["hidden_input_controls"])
                and all(m["detected"] for m in report["mutations"])
                and report["no_secret_in_any_output"])
(args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
print(json.dumps(report, indent=1))
raise SystemExit(0 if report["ok"] else 1)
