"""HAMCP-182: replay check_step.py over the ephemeral-key rehearsal (build_journals.py output).

Every planned step must pass in both modes; every mutation must be refused with
"FAILED:" and a non-zero exit. Public keys only. Prints a JSON report.
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
previous = {1: run / "journal-twin-seq9.json", 2: run / "planned/journal-step1.json",
            3: run / "planned/journal-step2.json"}


def check(mode, step, candidate, *, prev=None, signed=None, key=public, fp=fingerprint):
    argv = [sys.executable, str(CHECK), mode, str(step), "--previous", str(prev or previous[step]),
            "--candidate", str(candidate), "--public-key", str(key), "--rehearsal-key-sha256", fp]
    if signed:
        argv += ["--signed", str(signed)]
    done = subprocess.run(argv, capture_output=True, text=True)
    lines = (done.stdout + done.stderr).strip().splitlines()
    return done.returncode, lines[-1] if lines else ""


report = {"planned": [], "controls": []}
for step in (1, 2, 3):
    candidate = run / f"planned/candidate-step{step}.json"
    for mode in ("candidate", "signed"):
        code, last = check(mode, step, candidate, signed=run / f"planned/journal-step{step}.json")
        report["planned"].append({"step": step, "mode": mode, "exit": code, "last": last})


def mutated(step, change):
    value = json.loads((run / f"planned/candidate-step{step}.json").read_bytes())
    change(value)
    path = Path(tmp) / f"mutant-{len(report['controls'])}.json"
    path.write_bytes(canonical_json(value) + b"\n")
    return path


def add_second_reference(v):
    entry = next(e for e in v["envelope"]["entries"] if e["version"] == "2026.10.1")
    entry["capabilities"].append({**entry["capabilities"][-1], "profile_version": 2})


def touch_sibling(v):
    entry = next(e for e in v["envelope"]["entries"] if e["version"] == "2026.10.0")
    entry["capabilities"] = entry["capabilities"][:-1]


def drop_tombstone(v):
    v["envelope"]["revocations"] = v["envelope"]["revocations"][:-1]


with tempfile.TemporaryDirectory() as tmp:
    other_key = Path(tmp) / "other.txt"
    other_key.write_text(base64.b64encode(bytes(range(32))).decode() + "\n")
    controls = [
        ("step 1 candidate checked as step 2", lambda: check("candidate", 2, run / "planned/candidate-step1.json")),
        ("step 2 candidate against sequence 9", lambda: check(
            "candidate", 2, run / "planned/candidate-step2.json", prev=previous[1])),
        ("step 1 signed output offered for step 2", lambda: check(
            "signed", 2, run / "planned/candidate-step2.json", signed=run / "planned/journal-step1.json")),
        ("reviewed fingerprint with another public key", lambda: check(
            "candidate", 1, run / "planned/candidate-step1.json", key=other_key)),
        ("production fingerprint with the rehearsal key", lambda: check(
            "candidate", 1, run / "planned/candidate-step1.json",
            fp="7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac")),
        ("non-canonical candidate bytes", lambda: check("candidate", 1, _noncanonical())),
        ("extension relabeled add", lambda: check("candidate", 1, mutated(1, lambda v: v.update(operation="add")))),
        ("extension model swapped", lambda: check(
            "candidate", 1, mutated(1, lambda v: v.update(model="core-registry-review-candidate-v1")))),
        ("extension_review removed", lambda: check(
            "candidate", 1, mutated(1, lambda v: v.pop("extension_review")))),
        ("extension_review altered", lambda: check(
            "candidate", 1, mutated(1, lambda v: v["extension_review"].update(review_artifacts_sha256=["0" * 64])))),
        ("extra reference appended", lambda: check("candidate", 1, mutated(1, add_second_reference))),
        ("sibling entry altered", lambda: check("candidate", 1, mutated(1, touch_sibling))),
        ("revocations altered on extension", lambda: check("candidate", 1, mutated(1, drop_tombstone))),
        ("expiry beyond 90 days", lambda: check(
            "candidate", 1, mutated(1, lambda v: v["envelope"].update(expires_at="2027-12-31T00:00:00Z")))),
        ("tombstone reason altered", lambda: check(
            "candidate", 3, mutated(3, lambda v: v["envelope"]["revocations"][-1].update(reason="other")))),
        ("tombstone dropped on revoke", lambda: check("candidate", 3, mutated(3, drop_tombstone))),
    ]

    def _noncanonical():
        path = Path(tmp) / "noncanonical.json"
        path.write_text(json.dumps(json.loads((run / "planned/candidate-step1.json").read_bytes()), indent=1))
        return path

    for label, action in controls:
        code, last = action()
        report["controls"].append({"control": label, "exit": code, "last": last,
                                   "refused": code != 0 and last.startswith("FAILED:")})

report["ok"] = (all(p["exit"] == 0 and p["last"] == "OK" for p in report["planned"])
                and all(c["refused"] for c in report["controls"]))
print(json.dumps(report, indent=1))
raise SystemExit(0 if report["ok"] else 1)
