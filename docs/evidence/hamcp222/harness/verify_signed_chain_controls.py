"""HAMCP-222 publication: negative controls for harness/verify_signed_chain.py.

Each control copies the real signed files into a private temporary directory,
applies one tampering, and runs the verifier as a subprocess. PUBLIC key only for
the real files; nothing here reads the owner's private key.

Three kinds of control:
  pinned    the verifier as committed; the file-hash pins must refuse;
  unpinned  an in-memory copy of the verifier without the file-hash pin check
            (never written to disk), so the Ed25519 signatures and canonical form
            must refuse;
  resigned  the real chain's unsigned content, optionally changed at sequence
            13/14, re-linked and re-signed with an in-memory ephemeral key; the
            in-memory verifier trusts that key, has no file-hash pins and pins the
            resulting content digests, so the transition and admission checks
            themselves must refuse. The unchanged re-signed chain must pass, which
            proves the re-signing is faithful.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

HERE = Path(__file__).resolve().parent
VERIFIER = HERE / "verify_signed_chain.py"
PIN_CHECK = ('        require(sha(data) == FILE_SHA256[name], '
             'f"{name} SHA256 {sha(data)} is not the recorded output")\n')
KEY_PIN = 'PUBLIC_KEY_SHA256 = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"\n'
DIGEST_PINS = {13: "sha256:55311b896557ba43435d44b34f62df018afd1b784c382aa01536f49153a9ac8a",
               14: "sha256:734210cf33344c62886377e6abea5d4df776585d5ba687f22087778fa3e93199"}
NAMES = {12: "baseline.json", 13: "journal-1.json", 14: "journal-2.json"}

parser = argparse.ArgumentParser()
parser.add_argument("--baseline", type=Path, required=True)
parser.add_argument("--signed-dir", type=Path, required=True)
parser.add_argument("--candidate-1", type=Path, required=True)
parser.add_argument("--now", required=True)
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unsigned(value: dict) -> dict:
    return {k: v for k, v in value.items() if k != "signature"}


def flip(signature: str) -> str:
    raw = bytearray(base64.b64decode(signature))
    raw[0] ^= 1
    return base64.b64encode(bytes(raw)).decode()


def tip(value):
    return value["envelopes"][-1]


def edit(name: str, change):
    def apply(d: Path) -> None:
        value = json.loads((d / name).read_bytes())
        change(value)
        (d / name).write_bytes(canonical(value) + b"\n")
    return apply


def run(d: Path, *, source: str, now: str, public_key: Path | None = None,
        installed: Path | None = None) -> tuple[int, str]:
    # The (possibly modified) source arrives on stdin and runs as if it were the verifier file.
    bootstrap = ("import sys; exec(compile(sys.stdin.read(), {0!r}, 'exec'), "
                 "{{'__file__': {0!r}, '__name__': '__main__'}})").format(str(VERIFIER))
    command = [sys.executable, "-B", "-c", bootstrap, "--baseline", str(d / NAMES[12]),
               "--candidate-1", str(d / "candidate-1-extend-2026.10.1.json"),
               "--signed-dir", str(d), "--now", now]
    if public_key is not None:
        command += ["--public-key", str(public_key)]
    if installed is not None:
        command += ["--installed", str(installed)]
    done = subprocess.run(command, input=source, capture_output=True, text=True, timeout=300)
    lines = (done.stdout + done.stderr).strip().splitlines()
    return done.returncode, lines[-1] if lines else ""


def unpinned_source() -> str:
    source = VERIFIER.read_text()
    assert source.count(PIN_CHECK) == 1, "pin check not found"
    return source.replace(PIN_CHECK, "        pass\n")


def resign(d: Path, change) -> tuple[str, Path]:
    """Rebuild baseline, journal-1/2 and both candidates from the real unsigned content,
    apply `change` to the sequence-13/14 envelopes, re-link and re-sign with a new key."""
    journals = {s: json.loads((d / n).read_bytes()) for s, n in NAMES.items()}
    envelopes = [unsigned(e) for e in journals[14]["envelopes"]]
    change(envelopes)
    key = Ed25519PrivateKey.generate()
    public_raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    for before, after in zip(envelopes, envelopes[1:]):
        after["previous_registry_sha256"] = "sha256:" + sha(canonical(before))
    signed = [{**e, "signature": base64.b64encode(key.sign(canonical(e))).decode()} for e in envelopes]
    raw = {}
    for sequence, name in NAMES.items():
        header = unsigned(journals[sequence])
        body = {**header, "envelopes": signed[:sequence]}
        body["signature"] = base64.b64encode(key.sign(canonical(body))).decode()
        raw[sequence] = canonical(body) + b"\n"
        (d / name).write_bytes(raw[sequence])
    for sequence, name in ((13, "candidate-1-extend-2026.10.1.json"), (14, "candidate-2-add-2026.10.3.json")):
        candidate = json.loads((d / name).read_bytes())
        candidate.update(envelope=envelopes[sequence - 1], previous_journal_sha256=sha(raw[sequence - 1]),
                         public_key_sha256=sha(public_raw))
        (d / name).write_bytes(canonical(candidate) + b"\n")
    (d / "public-key.txt").write_text(base64.b64encode(public_raw).decode() + "\n")
    source = unpinned_source()
    assert source.count(KEY_PIN) == 1, "key pin not found"
    source = source.replace(KEY_PIN, f'PUBLIC_KEY_SHA256 = "{sha(public_raw)}"\n')
    for sequence, pinned in DIGEST_PINS.items():
        assert source.count(f'"{pinned}"') == 1, f"seq {sequence} digest pin not found"
        source = source.replace(f'"{pinned}"', '"sha256:' + sha(canonical(envelopes[sequence - 1])) + '"')
    return source, d / "public-key.txt"


def entry(envelopes, sequence, version):
    return next(e for e in envelopes[sequence - 1]["entries"] if e["version"] == version)


def each(sequences, version, change):
    def apply(envelopes):
        for sequence in sequences:
            change(entry(envelopes, sequence, version))
    return apply


def new_refs(envelopes):
    return entry(envelopes, 13, "2026.10.1")["capabilities"][22:]


def also_extend_100(envelopes):
    refs = new_refs(envelopes)
    for sequence in (13, 14):
        entry(envelopes, sequence, "2026.10.0")["capabilities"] += copy.deepcopy(refs)


def add_1012(envelopes):
    extra = {**copy.deepcopy(entry(envelopes, 14, "2026.10.3")), "version": "2026.10.2",
             "entry_id": "ha-core-2026.10.2-reviewed-v1"}
    envelopes[13]["entries"].append(extra)


def drop_delete(entry_):
    entry_["capabilities"] = [c for c in entry_["capabilities"]
                              if c["capability_id"] != "core.configuration_delete_operation"]


def swap_order(entry_):
    caps = entry_["capabilities"]
    caps[22], caps[23] = caps[23], caps[22]


def drop_revocation(envelopes):
    envelopes[13]["revocations"] = envelopes[13]["revocations"][:-1]


def other_fingerprint(entry_):
    entry_["capabilities"][25] = {**entry_["capabilities"][25], "contract_fingerprint": "sha256:" + "0" * 64}


def unpinned(d: Path, now: str):
    return run(d, source=unpinned_source(), now=now)


CONTROLS = [
    # (name, expect OK?, kind, mutation, now override)
    ("pinned: real files pass", True, "pinned", None, None),
    ("pinned: one byte appended to journal-2", False, "pinned",
     lambda d: (d / "journal-2.json").write_bytes((d / "journal-2.json").read_bytes() + b" "), None),
    ("pinned: journal-1 bytes in place of journal-2", False, "pinned",
     lambda d: shutil.copy(d / "journal-1.json", d / "journal-2.json"), None),
    ("pinned: installed registry differs from journal-2", False, "installed", None, None),
    ("unpinned: seq-14 envelope signature flipped", False, "unpinned",
     edit("journal-2.json", lambda v: tip(v).update(signature=flip(tip(v)["signature"]))), None),
    ("unpinned: journal-2 outer signature flipped", False, "unpinned",
     edit("journal-2.json", lambda v: v.update(signature=flip(v["signature"]))), None),
    ("unpinned: seq-13 envelope dropped from journal-2", False, "unpinned",
     edit("journal-2.json", lambda v: v["envelopes"].pop(-2)), None),
    ("unpinned: journal-1 non-canonical whitespace", False, "unpinned",
     lambda d: (d / "journal-1.json").write_bytes(
         json.dumps(json.loads((d / "journal-1.json").read_bytes()), indent=1).encode() + b"\n"), None),
    ("unpinned: candidate-2 envelope differs from seq 14", False, "unpinned",
     edit("candidate-2-add-2026.10.3.json", lambda v: v["envelope"].update(generated_at="2026-10-25T10:29:40Z")),
     None),
    ("unpinned: --now after seq-14 expiry", False, "unpinned", None, "2027-01-23T10:29:39Z"),
    ("unpinned: --now before seq-14 generated_at", False, "unpinned", None, "2026-10-25T10:29:38Z"),
    ("resigned: unchanged content passes", True, "resigned", lambda e: None, None),
    ("resigned: seq 13/14 also extend 2026.10.0", False, "resigned", also_extend_100, None),
    ("resigned: seq 14 2026.10.3 without the delete reference", False, "resigned",
     each((14,), "2026.10.3", drop_delete), None),
    ("resigned: seq 13/14 2026.10.1 without the delete reference", False, "resigned",
     each((13, 14), "2026.10.1", drop_delete), None),
    ("resigned: seq 13/14 2026.10.1 new references reordered", False, "resigned",
     each((13, 14), "2026.10.1", swap_order), None),
    ("resigned: seq 14 also adds 2026.10.2", False, "resigned", add_1012, None),
    ("resigned: seq 14 drops the 2026.9.4 revocation", False, "resigned", drop_revocation, None),
    ("resigned: seq 14 2026.10.3 other probe digest", False, "resigned",
     each((14,), "2026.10.3", lambda e: e.update(probe_profile_sha256="sha256:" + "1" * 64)), None),
    ("resigned: seq 14 2026.10.3 delete contract fingerprint differs", False, "resigned",
     each((14,), "2026.10.3", other_fingerprint), None),
    ("resigned: seq 13/14 2026.10.1 other evidence binding", False, "resigned",
     each((13, 14), "2026.10.1", lambda e: e.update(evidence_sha256="2" * 64)), None),
]


def main() -> None:
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, expect_ok, kind, mutate, now in CONTROLS:
            d = Path(tmp) / str(len(results))
            shutil.copytree(args.signed_dir, d)
            shutil.copy(args.baseline, d / NAMES[12])
            shutil.copy(args.candidate_1, d / "candidate-1-extend-2026.10.1.json")
            if kind == "resigned":
                source, public_key = resign(d, mutate)
                code, last = run(d, source=source, now=now or args.now, public_key=public_key,
                                 installed=d / "journal-2.json")
            elif kind == "installed":
                target = Path(tmp) / f"installed-{len(results)}.json"
                target.write_bytes((d / "journal-2.json").read_bytes()[:-1])
                code, last = run(d, source=VERIFIER.read_text(), now=now or args.now, installed=target)
            else:
                if mutate is not None:
                    mutate(d)
                source = VERIFIER.read_text() if kind == "pinned" else unpinned_source()
                code, last = run(d, source=source, now=now or args.now)
            passed = (code == 0 and last == "OK") if expect_ok else (code != 0 and last.startswith("FAILED: "))
            results.append({"control": name, "exit": code, "last_line": last,
                            "expected": "OK" if expect_ok else "FAILED", "passed": passed})
    args.report.write_text(json.dumps({"now": args.now, "controls": results,
                                       "passed": all(r["passed"] for r in results)}, indent=1) + "\n")
    for r in results:
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['control']}: exit {r['exit']} {r['last_line']}")
    print(f"{sum(r['passed'] for r in results)}/{len(results)} controls as expected")
    sys.exit(0 if all(r["passed"] for r in results) else 1)


main()
