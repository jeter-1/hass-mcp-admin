"""HAMCP-222: replay check_step.py over the ephemeral-key rehearsal (build_journals.py output).

Every planned step must pass in both modes; every mutation must be refused with
"FAILED:" and a non-zero exit, including repeated entries, revocations and JSON
members. Public keys only. Prints a JSON report.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
CHECK = HERE.parent / "check_step.py"
sys.path[:0] = [str(HERE.parents[3] / "hass_mcp_engineering_beta")]
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--rehearsal", type=Path, required=True)
args = parser.parse_args()
run = args.rehearsal
public = run / "public-key.txt"
fingerprint = hashlib.sha256(base64.b64decode(public.read_text().strip())).hexdigest()
previous = {1: run / "journal-twin-seq12.json", 2: run / "planned/journal-step1.json"}


def check(mode, step, candidate, *, prev=None, signed=None, key=public, fp=fingerprint):
    argv = [sys.executable, "-I", str(CHECK), mode, str(step), "--previous", str(prev or previous[step]),
            "--candidate", str(candidate), "--public-key", str(key), "--rehearsal-key-sha256", fp]
    if signed:
        argv += ["--signed", str(signed)]
    done = subprocess.run(argv, capture_output=True, text=True)
    lines = (done.stdout + done.stderr).strip().splitlines()
    return done.returncode, lines[-1] if lines else ""


report = {"planned": [], "controls": []}
for step in (1, 2):
    candidate = run / f"planned/candidate-step{step}.json"
    for mode in ("candidate", "signed"):
        code, last = check(mode, step, candidate, signed=run / f"planned/journal-step{step}.json")
        report["planned"].append({"step": step, "mode": mode, "exit": code, "last": last})


def mutated(step, change):
    value = json.loads((run / f"planned/candidate-step{step}.json").read_bytes())
    change(value)
    path = Path(tmp) / f"mutant-{len(report['controls'])}-{id(change)}.json"
    path.write_bytes(canonical_json(value) + b"\n")
    return path


def entry(v, version):
    return next(e for e in v["envelope"]["entries"] if e["version"] == version)


def add_reference(version):
    def change(v):
        refs = entry(v, version)["capabilities"]
        refs.append({**refs[-1], "profile_version": 2})
    return change


def drop_reference(version):
    def change(v):
        entry(v, version)["capabilities"].pop()
    return change


def swap_references(version):
    def change(v):
        refs = entry(v, version)["capabilities"]
        refs[-1], refs[-2] = refs[-2], refs[-1]
    return change


def touch(version, **fields):
    def change(v):
        entry(v, version).update(fields)
    return change


def drop_tombstone(v):
    v["envelope"]["revocations"] = v["envelope"]["revocations"][:-1]


def duplicate_revocation(v):
    v["envelope"]["revocations"].append(v["envelope"]["revocations"][0])


def duplicate_entry(version, where):
    # A repeated release entry, identical or conflicting, either order.
    def change(v):
        entries = v["envelope"]["entries"]
        index = next(i for i, e in enumerate(entries) if e["version"] == version)
        reviewed = entries[index]
        conflicting = {**reviewed, "capabilities": reviewed["capabilities"][:-1]}
        if where == "identical":
            entries.append(entries[0])
        elif where == "conflicting after":
            entries.insert(index + 1, conflicting)
        else:
            entries.insert(index, conflicting)
    return change


def duplicate_member():
    # A repeated JSON member that plain json.loads would collapse (last wins).
    raw = (run / "planned/candidate-step1.json").read_bytes()
    model = json.loads(raw)["model"]
    path = Path(tmp) / "duplicate-member.json"
    path.write_bytes(b"{" + json.dumps({"model": model}, separators=(",", ":")).encode()[1:-1]
                     + b"," + raw[1:])
    return path


def noncanonical():
    path = Path(tmp) / "noncanonical.json"
    path.write_text(json.dumps(json.loads((run / "planned/candidate-step1.json").read_bytes()), indent=1))
    return path


def as_extension(v):
    step1 = json.loads((run / "planned/candidate-step1.json").read_bytes())
    v.update(operation="extend-capabilities", model=step1["model"],
             extension_review=step1["extension_review"])


with tempfile.TemporaryDirectory() as tmp:
    other_key = Path(tmp) / "other.txt"
    other_key.write_text(base64.b64encode(bytes(range(32))).decode() + "\n")
    controls = [
        # Fencing, key and canonical form.
        ("step 1 candidate checked as step 2", lambda: check("candidate", 2, run / "planned/candidate-step1.json")),
        ("step 2 candidate checked as step 1", lambda: check("candidate", 1, run / "planned/candidate-step2.json")),
        ("step 2 candidate against sequence 12", lambda: check(
            "candidate", 2, run / "planned/candidate-step2.json", prev=previous[1])),
        ("step 1 signed output offered for step 2", lambda: check(
            "signed", 2, run / "planned/candidate-step2.json", signed=run / "planned/journal-step1.json")),
        ("step 2 signed output offered for step 1", lambda: check(
            "signed", 1, run / "planned/candidate-step1.json", signed=run / "planned/journal-step2.json")),
        ("signed mode without --signed", lambda: check("signed", 1, run / "planned/candidate-step1.json")),
        ("reviewed fingerprint with another public key", lambda: check(
            "candidate", 1, run / "planned/candidate-step1.json", key=other_key)),
        ("production fingerprint with the rehearsal key", lambda: check(
            "candidate", 1, run / "planned/candidate-step1.json",
            fp="7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac")),
        ("non-canonical candidate bytes", lambda: check("candidate", 1, noncanonical())),
        ("duplicate JSON member", lambda: check("candidate", 1, duplicate_member())),
        # Step 1: extend 2026.10.1 by exactly the four sequence-13 references.
        ("extension relabeled add", lambda: check("candidate", 1, mutated(1, lambda v: v.update(operation="add")))),
        ("extension model swapped", lambda: check(
            "candidate", 1, mutated(1, lambda v: v.update(model="core-registry-review-candidate-v1")))),
        ("extension_review removed", lambda: check(
            "candidate", 1, mutated(1, lambda v: v.pop("extension_review")))),
        ("extension_review altered", lambda: check(
            "candidate", 1, mutated(1, lambda v: v["extension_review"].update(review_artifacts_sha256=["0" * 64])))),
        ("fifth reference appended to 2026.10.1", lambda: check("candidate", 1, mutated(1, add_reference("2026.10.1")))),
        ("only three of the four references", lambda: check("candidate", 1, mutated(1, drop_reference("2026.10.1")))),
        ("sequence-13 references reordered", lambda: check("candidate", 1, mutated(1, swap_references("2026.10.1")))),
        ("sibling 2026.10.0 altered", lambda: check("candidate", 1, mutated(1, drop_reference("2026.10.0")))),
        ("2026.10.1 image digest altered", lambda: check(
            "candidate", 1, mutated(1, touch("2026.10.1", image_index_digest="sha256:" + "1" * 64)))),
        ("revocations altered on extension", lambda: check("candidate", 1, mutated(1, drop_tombstone))),
        ("revocation repeated on extension", lambda: check("candidate", 1, mutated(1, duplicate_revocation))),
        ("expiry beyond 90 days", lambda: check(
            "candidate", 1, mutated(1, lambda v: v["envelope"].update(expires_at="2027-12-31T00:00:00Z")))),
        ("sequence skipped", lambda: check(
            "candidate", 1, mutated(1, lambda v: v["envelope"].update(sequence=14)))),
        ("identical release entry appended", lambda: check(
            "candidate", 1, mutated(1, duplicate_entry("2026.10.1", "identical")))),
        ("conflicting 2026.10.1 entry after the reviewed one", lambda: check(
            "candidate", 1, mutated(1, duplicate_entry("2026.10.1", "conflicting after")))),
        ("conflicting 2026.10.1 entry before the reviewed one", lambda: check(
            "candidate", 1, mutated(1, duplicate_entry("2026.10.1", "conflicting before")))),
        # Step 2: add 2026.10.3 with the signed 2026.10.1 contracts.
        ("add relabeled extension", lambda: check("candidate", 2, mutated(2, as_extension))),
        ("add model swapped", lambda: check(
            "candidate", 2, mutated(2, lambda v: v.update(model="core-registry-extension-candidate-v1")))),
        ("2026.10.3 with 25 references", lambda: check("candidate", 2, mutated(2, drop_reference("2026.10.3")))),
        ("2026.10.3 with 27 references", lambda: check("candidate", 2, mutated(2, add_reference("2026.10.3")))),
        ("2026.10.3 references reordered", lambda: check("candidate", 2, mutated(2, swap_references("2026.10.3")))),
        ("2026.10.3 image digest altered", lambda: check(
            "candidate", 2, mutated(2, touch("2026.10.3", image_index_digest="sha256:" + "1" * 64)))),
        ("2026.10.3 source commit altered", lambda: check(
            "candidate", 2, mutated(2, touch("2026.10.3", source_commit="1" * 40)))),
        ("2026.10.3 entry_id altered", lambda: check(
            "candidate", 2, mutated(2, touch("2026.10.3", entry_id="ha-core-2026.10.3-reviewed-v2")))),
        ("2026.10.3 evidence hash altered", lambda: check(
            "candidate", 2, mutated(2, touch("2026.10.3", evidence_sha256="0" * 64)))),
        ("sibling 2026.10.1 altered on add", lambda: check("candidate", 2, mutated(2, drop_reference("2026.10.1")))),
        ("2026.10.0 dropped on add", lambda: check("candidate", 2, mutated(2, lambda v: v["envelope"].update(
            entries=[e for e in v["envelope"]["entries"] if e["version"] != "2026.10.0"])))),
        ("revocations altered on add", lambda: check("candidate", 2, mutated(2, drop_tombstone))),
        ("revocation repeated on add", lambda: check("candidate", 2, mutated(2, duplicate_revocation))),
        ("identical 2026.10.3 entry appended", lambda: check(
            "candidate", 2, mutated(2, duplicate_entry("2026.10.3", "identical")))),
        ("conflicting 2026.10.3 entry before the reviewed one", lambda: check(
            "candidate", 2, mutated(2, duplicate_entry("2026.10.3", "conflicting before")))),
        ("step 2 expiry beyond 90 days", lambda: check(
            "candidate", 2, mutated(2, lambda v: v["envelope"].update(expires_at="2027-12-31T00:00:00Z")))),
    ]

    for label, action in controls:
        code, last = action()
        report["controls"].append({"control": label, "exit": code, "last": last,
                                   "refused": code != 0 and last.startswith("FAILED:")})

report["ok"] = (all(p["exit"] == 0 and p["last"] == "OK" for p in report["planned"])
                and all(c["refused"] for c in report["controls"]))
print(json.dumps(report, indent=1))
raise SystemExit(0 if report["ok"] else 1)
