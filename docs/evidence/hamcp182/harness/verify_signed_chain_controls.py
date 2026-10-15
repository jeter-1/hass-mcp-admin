"""HAMCP-182 Part 2: negative controls for harness/verify_signed_chain.py.

Each control copies the real signed files into a private temporary directory,
applies one tampering, and runs the verifier as a subprocess. Controls past the
file-hash pins run an in-memory copy of the verifier whose FILE_SHA256 check is
removed (never written to disk), so the signature, chain and transition checks
are what must refuse. PUBLIC key only.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
VERIFIER = HERE / "verify_signed_chain.py"
PIN_CHECK = ('        require(sha(data) == FILE_SHA256[name], '
             'f"{name} SHA256 {sha(data)} is not the recorded output")\n')

parser = argparse.ArgumentParser()
parser.add_argument("--baseline", type=Path, required=True)
parser.add_argument("--signed-dir", type=Path, required=True)
parser.add_argument("--now", required=True)
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode() + b"\n"


def flip(signature: str) -> str:
    raw = bytearray(base64.b64decode(signature))
    raw[0] ^= 1
    return base64.b64encode(bytes(raw)).decode()


def edit(name: str, change):
    def apply(d: Path) -> None:
        value = json.loads((d / name).read_bytes())
        change(value)
        (d / name).write_bytes(canonical(value))
    return apply


def run(d: Path, baseline: Path, *, unpinned: bool, now: str, installed: Path | None = None):
    source = VERIFIER.read_text()
    if unpinned:
        assert source.count(PIN_CHECK) == 1, "pin check not found"
        source = source.replace(PIN_CHECK, "        pass\n")
    # The (possibly unpinned) source arrives on stdin and runs as if it were the verifier file.
    bootstrap = ("import sys; exec(compile(sys.stdin.read(), {0!r}, 'exec'), "
                 "{{'__file__': {0!r}, '__name__': '__main__'}})").format(str(VERIFIER))
    command = [sys.executable, "-B", "-c", bootstrap,
               "--baseline", str(baseline), "--signed-dir", str(d), "--now", now]
    if installed is not None:
        command += ["--installed", str(installed)]
    done = subprocess.run(command, input=source, capture_output=True, text=True, timeout=300)
    lines = (done.stdout + done.stderr).strip().splitlines()
    return done.returncode, lines[-1] if lines else ""


def tip(value):
    return value["envelopes"][-1]


CONTROLS = [
    # (name, pinned?, mutation, now override, installed mutation)
    ("baseline: real files pass", False, None, None, None),
    ("pinned: one byte appended to journal-3", True,
     lambda d: (d / "journal-3.json").write_bytes((d / "journal-3.json").read_bytes() + b" "), None, None),
    ("pinned: journal-2 bytes in place of journal-3", True,
     lambda d: shutil.copy(d / "journal-2.json", d / "journal-3.json"), None, None),
    ("pinned: installed registry differs from journal-3", True, None, None, "differ"),
    ("unpinned: seq-12 envelope signature flipped", False,
     edit("journal-3.json", lambda v: tip(v).update(signature=flip(tip(v)["signature"]))), None, None),
    ("unpinned: journal-3 outer signature flipped", False,
     edit("journal-3.json", lambda v: v.update(signature=flip(v["signature"]))), None, None),
    ("unpinned: seq-12 revocation reason edited", False,
     edit("journal-3.json", lambda v: tip(v)["revocations"][-1].update(reason="edited")), None, None),
    ("unpinned: seq-11 envelope dropped from journal-3", False,
     edit("journal-3.json", lambda v: v["envelopes"].pop(-2)), None, None),
    ("unpinned: journal-1 non-canonical whitespace", False,
     lambda d: (d / "journal-1.json").write_bytes(
         json.dumps(json.loads((d / "journal-1.json").read_bytes()), indent=1).encode() + b"\n"), None, None),
    ("unpinned: candidate-3 envelope differs from seq 12", False,
     edit("candidate-3-revoke-2026.9.4.json", lambda v: v["envelope"].update(generated_at="2026-10-15T13:40:22Z")),
     None, None),
    ("unpinned: --now after seq-10 expiry", False, None, "2027-01-13T10:31:21Z", None),
    ("unpinned: --now before seq-12 generated_at", False, None, "2026-10-15T13:40:20Z", None),
]


def main() -> None:
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, pinned, mutate, now, installed in CONTROLS:
            d = Path(tmp) / str(len(results))
            shutil.copytree(args.signed_dir, d)
            baseline = Path(tmp) / f"baseline-{len(results)}.json"
            shutil.copy(args.baseline, baseline)
            if mutate is not None:
                mutate(d)
            target = None
            if installed == "differ":
                target = Path(tmp) / f"installed-{len(results)}.json"
                target.write_bytes((d / "journal-3.json").read_bytes()[:-1])
            code, last = run(d, baseline, unpinned=not pinned, now=now or args.now, installed=target)
            expect_ok = mutate is None and now is None and installed is None
            passed = (code == 0 and last == "OK") if expect_ok else (code != 0 and last.startswith("FAILED: "))
            results.append({"control": name, "exit": code, "last_line": last, "expected": "OK" if expect_ok
                            else "FAILED", "passed": passed})
    args.report.write_text(json.dumps({"now": args.now, "controls": results,
                                       "passed": all(r["passed"] for r in results)}, indent=1) + "\n")
    for r in results:
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['control']}: exit {r['exit']} {r['last_line']}")
    print(f"{sum(r['passed'] for r in results)}/{len(results)} controls as expected")
    sys.exit(0 if all(r["passed"] for r in results) else 1)


main()
