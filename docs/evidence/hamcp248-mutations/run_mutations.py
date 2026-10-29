"""HAMCP-248 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery: a disposable copy of the tracked
tree, every fragment matching exactly once before anything runs, and
fail-fast suites per mutant. ``test_hamcp248_final_fence`` runs first, then
the existing baseline capture, transport, store, usability and comparator
suites the change touches.

Per AGENTS.md (HAMCP-237), the work directory and every mutant's TMPDIR live
under the worktree's ignored ``.artifacts/``; the copy skips ``.artifacts``,
so it never copies itself.

Classes:
- ``K*``: keepalive handling on the capture websocket (a PING not answered,
  answered with another payload, refused again, not charged; an unsolicited
  PONG refused; the per-read bound removed, moved or reset);
- ``R*``: a non-keepalive frame relabelled (a close, a local error or an
  oversize frame reported as ``malformed_response``; a binary frame
  accepted);
- ``M*``: the compare-row reasons (a missing digest also claiming a
  fingerprint-model mismatch, per stored and per recorded comparison).

``M6`` is expected to survive as an equivalent mutant. A validated record
with a digest carries the contract's model (``validation.py``,
``record_fingerprint_contract_mismatch``), so the per-row recorded check
fires only when the caller's model differs from the contract's, and then the
comparison-level ``fingerprint_model_mismatch`` is already on every row
(row reasons are a sorted set).

Usage:
    systemd-run --user --scope -p MemoryMax=3G -p OOMPolicy=continue \\
        python docs/evidence/hamcp248-mutations/run_mutations.py \\
        --work .artifacts/hamcp248-mut --output docs/evidence/hamcp248-mutations/results.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hamcp131-mutations"))

import run_mutations as base  # noqa: E402

m = base.m
E = base.E
CL = f"{E}/clients/audit_baseline.py"
CC = f"{E}/audit_baseline/capture_contracts.py"
CP = f"{E}/audit_baseline/comparison.py"

K = "keepalive handling on the capture websocket"
R = "a non-keepalive frame relabelled"
M = "a compare-row reason that is not the true one"

MUTATIONS = [
    m("K1-ping-not-answered", K,
      (CL, "                    await self.websocket.pong(frame.data)", "                    pass")),
    m("K2-ping-answered-with-another-payload", K,
      (CL, "await self.websocket.pong(frame.data)", 'await self.websocket.pong(b"")')),
    m("K3-keepalive-refused-again", K,
      (CL, "            if frame.type in (aiohttp.WSMsgType.PING, aiohttp.WSMsgType.PONG):",
       "            if False:")),
    m("K4-unsolicited-pong-refused", K,
      (CL, "            if frame.type in (aiohttp.WSMsgType.PING, aiohttp.WSMsgType.PONG):",
       "            if frame.type in (aiohttp.WSMsgType.PING,):")),
    m("K5-unsolicited-pong-answered", K,
      (CL, "                if frame.type == aiohttp.WSMsgType.PING:\n                    await",
       "                if True:\n                    await")),
    m("K6-keepalive-bound-removed", K,
      (CL, "                if controls > c.CONTROL_FRAMES:", "                if False:")),
    m("K7-keepalive-bound-off-by-one", K,
      (CL, "                if controls > c.CONTROL_FRAMES:", "                if controls >= c.CONTROL_FRAMES:")),
    m("K8-keepalive-bound-reset-per-frame", K,
      (CL, "            frame = await self.websocket.receive()\n",
       "            frame = await self.websocket.receive()\n            controls = 0\n")),
    m("K9-keepalive-bytes-not-charged", K,
      (CL, "                self.charge(len(frame.data))\n", "")),
    m("K10-keepalive-bound-raised", K,
      (CC, "CONTROL_FRAMES = 4", "CONTROL_FRAMES = 64")),
    m("R1-close-reported-malformed", R,
      (CL, "aiohttp.WSMsgType.CLOSED):\n                raise c.CaptureError(\"source_unavailable\")",
       "aiohttp.WSMsgType.CLOSED):\n                raise c.CaptureError(\"malformed_response\")")),
    m("R2-local-error-reported-malformed", R,
      (CL, "WebSocketError):\n                    raise c.CaptureError(\"source_unavailable\")",
       "WebSocketError):\n                    raise c.CaptureError(\"malformed_response\")")),
    m("R3-oversize-frame-reported-malformed", R,
      (CL, "MESSAGE_TOO_BIG:\n                    raise c.CaptureError(\"response_limit\")",
       "MESSAGE_TOO_BIG:\n                    raise c.CaptureError(\"malformed_response\")")),
    m("R4-error-frame-without-protocol-check", R,
      (CL, "                if not isinstance(frame.data, aiohttp.WebSocketError):\n",
       "                if False:\n")),
    m("R5-binary-frame-accepted", R,
      (CL, "            if frame.type == aiohttp.WSMsgType.TEXT:\n                return frame",
       "            if frame.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):\n"
       "                return frame")),
    m("M1-stored-missing-digest-claims-model-mismatch", M,
      (CP, "    if (earlier.configuration_digest is not None and later.configuration_digest is not None\n"
           "            and earlier.fingerprint_model != later.fingerprint_model):",
       "    if earlier.fingerprint_model != later.fingerprint_model:")),
    m("M2-stored-missing-earlier-digest-claims-model-mismatch", M,
      (CP, "    if (earlier.configuration_digest is not None and later.configuration_digest is not None\n",
       "    if (later.configuration_digest is not None\n")),
    m("M3-stored-missing-later-digest-claims-model-mismatch", M,
      (CP, "    if (earlier.configuration_digest is not None and later.configuration_digest is not None\n",
       "    if (earlier.configuration_digest is not None\n")),
    m("M4-stored-model-mismatch-never-claimed", M,
      (CP, "            and earlier.fingerprint_model != later.fingerprint_model):",
       "            and False):")),
    m("M5-recorded-missing-digest-claims-model-mismatch", M,
      (CP, "if record.configuration_digest is not None and record.fingerprint_model != fingerprint_model:",
       "if record.fingerprint_model != fingerprint_model:")),
    m("M6-recorded-model-mismatch-never-claimed", M,
      (CP, "if record.configuration_digest is not None and record.fingerprint_model != fingerprint_model:",
       "if False:")),
]
base.MODULES = ("test_hamcp248_final_fence", "test_automation_baseline_transport",
                "test_automation_baseline_capture", "test_hamcp228_baseline_store",
                "test_hamcp235_baseline_usability", "test_automation_audit_baseline")
base.MUTATIONS = MUTATIONS

_run_tests = base.run_tests
SCRATCH: Path | None = None


def run_tests(tree: Path, timeout: int, *, failfast: bool) -> tuple[int, str, float]:
    scratch = tempfile.mkdtemp(prefix="mutant-", dir=SCRATCH)
    previous = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = scratch
    try:
        return _run_tests(tree, timeout, failfast=failfast)
    finally:
        if previous is None:
            del os.environ["TMPDIR"]
        else:
            os.environ["TMPDIR"] = previous
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    """The HAMCP-131 main, with the work directory under ``.artifacts/``."""

    global SCRATCH
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--only", action="append", default=[],
                        help="run only these mutation ids (repeatable); the baseline always runs")
    arguments = parser.parse_args()
    root = base.ROOT
    work = arguments.work.expanduser().resolve()
    artifacts = root / ".artifacts"
    if artifacts not in work.parents:
        raise SystemExit("--work must be under the worktree's .artifacts/")
    tree, log_dir, SCRATCH = work / "tree", work / "mutation-logs", work / "tmp"
    if tree.exists():
        shutil.rmtree(tree)
    top = base.tracked_top_level()
    common = shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc")

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = set(common(directory, names))
        if Path(directory) == root:  # untracked top-level entries stay out
            skipped |= {name for name in names if name not in top}
        return skipped

    shutil.copytree(root, tree, symlinks=True, ignore=ignore)
    log_dir.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if len({item["id"] for item in MUTATIONS}) != len(MUTATIONS):
        raise SystemExit("duplicate mutation id")
    for mutation in MUTATIONS:  # every fragment matches once before anything runs
        for path, text in base.apply(tree, mutation).items():
            path.write_text(text, encoding="utf-8")

    code, output, seconds = run_tests(tree, arguments.timeout, failfast=False)
    baseline_log = log_dir / "baseline.log"
    baseline_log.write_text(output, encoding="utf-8")
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    ran = re.search(r"^Ran (\d+) tests?", output, re.MULTILINE)
    baseline = {"log_path": baseline_log.relative_to(work).as_posix(), "modules": list(base.MODULES),
                "returncode": code, "tests": int(ran.group(1)) if ran else None,
                "seconds": round(seconds, 1), "summary": summary}
    print(f"baseline rc={code} {summary} ({seconds:.0f}s)", flush=True)
    if code != 0:
        print(output[-4000:])
        return 2

    results = []
    unknown = set(arguments.only) - {item["id"] for item in MUTATIONS}
    if unknown:
        raise SystemExit(f"unknown mutation ids: {sorted(unknown)}")
    for mutation in MUTATIONS:
        if arguments.only and mutation["id"] not in arguments.only:
            continue
        originals = base.apply(tree, mutation)
        try:
            code, output, seconds = run_tests(tree, arguments.timeout, failfast=True)
        finally:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")
        failures = [f"{kind} {name} ({where})" for kind, name, where in base._FAILURE_LINE.findall(output)]
        log_path = log_dir / f"{mutation['id']}.log"
        log_path.write_text(output, encoding="utf-8")
        results.append({
            # A timeout is not a kill: the fail-fast suites only run to the
            # limit when no earlier test failed.
            **mutation, "log_path": log_path.relative_to(work).as_posix(), "returncode": code,
            "killed": code != 0 and output != "TIMEOUT", "timed_out": output == "TIMEOUT",
            "killed_by_import_or_syntax_error": any(
                marker in output for marker in ("ImportError", "SyntaxError", "Failed to import")),
            "first_failures": failures[:3], "seconds": round(seconds, 1),
        })
        print(f"{'KILLED' if results[-1]['killed'] else 'SURVIVED':8} {mutation['id']:52} {failures[:1]}", flush=True)

    survived = [item["id"] for item in results if not item["killed"]]
    document = {"baseline": baseline, "mutation_count": len(results),
                "killed": sum(item["killed"] for item in results), "survived": survived,
                "python": platform.python_version(), "results": results}
    arguments.output.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
