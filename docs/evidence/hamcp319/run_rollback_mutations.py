"""HAMCP-319 #404 F1 evidence: mutants that reopen the rollback-probe false pass.

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp319/run_rollback_mutations.py

Everything runs in a disposable ``git clone --shared`` of HEAD under
``.artifacts/mutation-hamcp319-rollback/``; the tree under test is never
mutated. Offline: no network, container, Home Assistant or MCP endpoint is
used, and nothing reads configuration or a secret.

1. Mutants: each mutant reopens one part of the #404 F1 false pass in
   ``scripts/exact_image_admission_lanes.py`` and is killed only by an
   assertion failure in its named test; an error, import failure or survival
   is not a kill.
2. Control: the unmutated copy passes the whole module.

The results are written to ``.artifacts/mutation-hamcp319-rollback/results.json``
and copied by hand to ``docs/evidence/hamcp319/rollback-mutation-results.json``.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / ".artifacts/mutation-hamcp319-rollback"
REVIEWED = "62da9b6cb55c2f509ea16adb6e304a7e5c30f2ad"  # release/2.5.0 after #404, where F1 was found
MODULE = "tests.test_hamcp319_exact_image_admission_lanes"
LANES = "scripts/exact_image_admission_lanes.py"
T = "RollbackReadyPathTests."

shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
COPY = OUT / "copy"
subprocess.check_call(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(COPY)])
subprocess.check_call(["git", "-C", str(COPY), "checkout", "-q", "--force", HEAD])
ENV = {**os.environ, "PYTHONPATH": os.pathsep.join([str(COPY / "hass_mcp_engineering_beta"), str(COPY / "tests")]),
       "TMPDIR": str(OUT)}


def run(test: str, log: str) -> tuple[int, bytes]:
    for cache in COPY.rglob("__pycache__"):
        shutil.rmtree(cache)
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", f"{MODULE}.{test}" if test else MODULE],
                          cwd=COPY, env=ENV, capture_output=True, timeout=900)
    raw = proc.stdout + proc.stderr
    (OUT / log).write_bytes(raw)
    return proc.returncode, raw


def assertion_failure(code: int, raw: bytes) -> bool:
    return bool(code) and b"FAIL:" in raw and b"AssertionError" in raw and b"ERROR:" not in raw


EXACT_SET = ('    delegated = delegated_reads(catalog, static_names)\n'
             '    require(not delegated - ROLLBACK_EXPECTED_DELEGATED_READS, "fail_closed_delegated_read_exposed")\n'
             '    require(not ROLLBACK_EXPECTED_DELEGATED_READS - delegated, '
             '"fail_closed_expected_delegated_read_missing")\n'
             '    gateway = unadmitted_gateway(catalog.get("health_is_error"), catalog.get("health_text"))\n')
SENTINEL_ONLY = ('    names = [name for page in catalog["pages"] for name in page]\n'
                 '    require("ha_get_state" not in names, "fail_closed_delegated_read_exposed")\n'
                 '    require("admitted_exact" not in str(catalog.get("health_text") or ""), '
                 '"fail_closed_gateway_admitted")\n'
                 '    delegated, gateway = set(), {"admission_status": None}\n')
HEALTH_PRESENT = ('    require(isinstance(text, str) and text.strip() != "", "fail_closed_health_missing")\n'
                  '    try:\n        envelope = json.loads(text)\n    except ValueError:\n'
                  '        raise LaneFailure("fail_closed_health_malformed") from None\n')
HEALTH_LENIENT = ('    try:\n        envelope = json.loads(text)\n    except (TypeError, ValueError):\n'
                  '        return {"admission_status": "unavailable"}\n')
ADMITTED = '    require(status not in ADMITTED_STATUSES, "fail_closed_gateway_admitted")\n'
UNADMITTED = '    require(status in UNADMITTED_STATUSES, "fail_closed_gateway_unadmission_unproven")\n'
mutants = [
    ("revert to the sentinel-only check (#404 head: ha_get_state and admitted_exact literals)", EXACT_SET,
     SENTINEL_ONLY, T + "test_codex_reproduction_fails_writer_and_gate"),
    ("exact set compared in one direction only (missing expected read accepted)",
     '    require(not ROLLBACK_EXPECTED_DELEGATED_READS - delegated, "fail_closed_expected_delegated_read_missing")\n',
     "", T + "test_missing_expected_read_fails_writer_and_gate"),
    ("drop the health-validity check: missing or malformed health read as unadmitted", HEALTH_PRESENT,
     HEALTH_LENIENT, T + "test_missing_health_fails_writer_and_gate"),
    ("drop the health-validity check: an errored tool result accepted",
     '    require(is_error is False, "fail_closed_health_error")\n', "",
     T + "test_errored_health_fails"),
    ("drop the health-validity check: a failure envelope accepted",
     'envelope.get("success") is True and "error_code" not in envelope', "True",
     T + "test_errored_health_fails"),
    ("drop the admitted check (admission judged by the admitted marker's absence)", ADMITTED + UNADMITTED, "",
     T + "test_admitted_health_fails_writer_and_gate"),
    ("drop the explicit unadmitted allowlist (unknown or missing status accepted)", UNADMITTED, "",
     T + "test_unadmission_must_be_positively_established"),
    ("drop the zero-exposed check", '    require(exposed == (0, 0) and gateway.get("generic_delegation_available") '
     'is False,\n            "fail_closed_gateway_exposes_reads")\n', "",
     T + "test_unadmission_must_be_positively_established"),
    ("ignore later catalog pages in the evaluation", "    names = [name for page in pages for name in page]\n",
     "    names = list(pages[0])\n", T + "test_later_page_delegated_read_fails_writer_and_gate"),
    ("ignore later catalog pages in the reader (first page only)",
     '        cursor = getattr(listed, "nextCursor", None)\n', "        cursor = None\n",
     T + "test_delegated_read_on_later_catalog_page_fails"),
    ("accept an incomplete catalog",
     'catalog.get("catalog_complete") is True and len(pages) <= MAX_CATALOG_PAGES', "len(pages) <= MAX_CATALOG_PAGES",
     T + "test_incomplete_catalog_fails_writer_and_gate"),
]

results = {"head": HEAD, "reviewed_head": REVIEWED, "python": sys.version.split()[0], "mutants": [],
           "control": None}
path = COPY / LANES
original = path.read_text()
for index, (name, before, after, test) in enumerate(mutants, 1):
    assert original.count(before) == 1, (name, original.count(before))
    path.write_text(original.replace(before, after))
    code, raw = run(test, f"mutant-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "survived")
    results["mutants"].append({"id": index, "name": name, "file": LANES, "test": test,
                               "classification": classification, "exit_code": code})
    path.write_text(original)
    print("mutant", index, name, classification, flush=True)

code, raw = run("", "control.log")
ran = [line for line in raw.decode(errors="replace").splitlines() if line.startswith("Ran ")]
results["control"] = {"name": "control (unmutated copy, whole module)",
                      "classification": "pass" if code == 0 else "fail", "exit_code": code,
                      "ran": ran[-1] if ran else None}
(OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results["control"]), flush=True)
if code or any(item["classification"] != "assertion" for item in results["mutants"]):
    sys.exit(1)
