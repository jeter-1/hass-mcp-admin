"""HAMCP-319 evidence: mutants against the PR #406 correction batch (Codex F1-F3).

Codex verdict 6731785026 on PR #406 found three Medium defects:

* F1: runtime admission on 9.1.0 bound the catalog order and the presence of
  unused tools instead of only the tools Engineering uses. The fix is 9.1.0
  only: 8.5.0 keeps its pre-existing order gate unchanged (architect ruling
  2026-11-08, relayed; outside #406 scope), and the 8.5.0 pins must stay
  green, so lifting the gate on 8.5.0 is itself a mutant.
* F2: an operation-phase redirect refusal latched before a step's outcome was
  recorded lost to a succeeding step when both were ready together.
* F3: dashboard analysis on 9.1.0 accepted an absent ``stored_config`` flag.

Each mutant undoes or weakens one part of a fix and must be killed by an
assertion failure in the named test; an error, import failure or survival is
not a kill. The control runs the unmutated copy of every named test class.

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp319/run_pr406_correction_mutations.py

Everything runs in a disposable ``git clone --shared`` of HEAD under
``.artifacts/mutation-hamcp319-pr406/``; the tree under test is never
mutated. Offline: no network, container, Home Assistant or MCP endpoint.
Results, including the git blob hash of every mutated file and test module,
are written to ``results.json`` there and copied by hand to
``docs/evidence/hamcp319/pr406-correction-mutation-results.json``.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / ".artifacts/mutation-hamcp319-pr406"
PKG = "hass_mcp_engineering_beta/ha_mcp_engineering/"
POLICY = PKG + "upstream_tool_policy.py"
GATEWAY = PKG + "providers/upstream_read_gateway.py"
ENDPOINT = PKG + "clients/ha_mcp_endpoint.py"
DASHBOARD = PKG + "providers/upstream_dashboard.py"
TESTS = {
    "exact": "test_hamcp319_used_tool_exact_admission",
    "catalog": "test_hamcp319_no_redirect_catalog",
    "matrix": "test_hamcp319_no_redirect_matrix",
    "dashboard": "test_hamcp319_dashboard",
}
CONTROL = (
    "test_hamcp319_used_tool_exact_admission.ReadGatewayExactAdmission",
    "test_hamcp319_no_redirect_catalog.OrderAcrossPagesTests",
    "test_hamcp319_no_redirect_matrix.NoRedirectLatchLinearization",
    "test_hamcp319_dashboard.AnalysisStoredConfigReleaseGate",
)

shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
COPY = OUT / "copy"
subprocess.check_call(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(COPY)])
subprocess.check_call(["git", "-C", str(COPY), "checkout", "-q", "--force", HEAD])
ENV = {**os.environ, "PYTHONPATH": os.pathsep.join([str(COPY / "hass_mcp_engineering_beta"), str(COPY / "tests")]),
       "TMPDIR": str(OUT)}


def blob(path: str) -> str:
    return subprocess.check_output(["git", "-C", str(COPY), "rev-parse", f"HEAD:{path}"], text=True).strip()


def run(tests: tuple[str, ...], log: str) -> tuple[int, bytes]:
    for cache in COPY.rglob("__pycache__"):
        shutil.rmtree(cache)
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", *tests],
                          cwd=COPY, env=ENV, capture_output=True, timeout=1800)
    raw = proc.stdout + proc.stderr
    (OUT / log).write_bytes(raw)
    return proc.returncode, raw


def assertion_failure(code: int, raw: bytes) -> bool:
    return bool(code) and b"FAIL:" in raw and b"AssertionError" in raw and b"ERROR:" not in raw


ORDER_EXEMPTION = ("            and selected_policy.reviewed_upstream_version\n"
                   "            not in USED_TOOL_ADMISSION_RELEASES\n")
ORDER_TAIL = ("            and schema_fingerprint(observed_reviewed_order)\n"
              "            != expected_order_fingerprint\n        )\n")
RELEASES = 'USED_TOOL_ADMISSION_RELEASES = frozenset({"9.1.0"})\n'
DUPLICATE = ("                    catalog_has_invalid_structure = True\n"
             "                observed_reviewed.setdefault(name, []).append(item)\n")
LATCH = ("            if self.latch_code is not None and (\n"
         "                self.latch_phase != \"cleanup\"\n"
         "                or task.cancelled()\n")
LATCH_REVERTED = ("            if self.latch_code is not None and (\n"
                  "                task.cancelled()\n")
GATE = ("            upstream_version in STORED_CONFIG_REQUIRED_RELEASES\n"
        "            and payload.get(\"stored_config\") is not True):\n")
GATE_FULL = ("    if (\"stored_config\" in payload and payload[\"stored_config\"] is not True) or (\n" + GATE)
GATE_ANCHOR = "    # HAMCP-319 section 6.3 (F4): no saved config is not an analyzable source.\n"
F1_EXACT = TESTS["exact"] + ".ReadGatewayExactAdmission."
F1_CATALOG = TESTS["catalog"] + ".OrderAcrossPagesTests."
F2 = TESTS["matrix"] + ".NoRedirectLatchLinearization."
F3 = TESTS["dashboard"] + ".AnalysisStoredConfigReleaseGate."

mutants = [
    # F1: on 9.1.0 catalog order and unused-tool presence are not runtime
    # admission conditions; 8.5.0's pre-existing order gate is unchanged.
    ("F1", "re-add the order check on 9.1.0", GATEWAY, ORDER_EXEMPTION, "",
     F1_EXACT + "test_reordered_catalog_is_admitted"),
    ("F1", "re-add the order check on 9.1.0 (paginated public catalog)", GATEWAY, ORDER_EXEMPTION, "",
     F1_CATALOG + "test_91_catalog_order_resorted_pages_admitted"),
    ("F1", "re-add the order check on 9.1.0 (unused tool removed)", GATEWAY,
     ORDER_EXEMPTION, "", F1_CATALOG + "test_91_unused_tool_changes_admitted"),
    ("F1", "re-add the order check on 9.1.0 (missing used read withholds only itself)", GATEWAY,
     ORDER_EXEMPTION, "", F1_CATALOG + "test_91_catalog_missing_used_read_withheld_alone"),
    ("F1", "empty the used-tool admission release set", POLICY, RELEASES,
     'USED_TOOL_ADMISSION_RELEASES = frozenset()\n',
     F1_CATALOG + "test_91_catalog_order_resorted_pages_admitted"),
    ("F1", "scope 8.5.0: lift the order gate on 8.5.0 too (exact admission pin)", POLICY, RELEASES,
     'USED_TOOL_ADMISSION_RELEASES = frozenset({"8.5.0", "9.1.0"})\n',
     F1_EXACT + "test_85_order_gate_unchanged"),
    ("F1", "scope 8.5.0: lift the order gate on 8.5.0 too (paginated public catalog pin)", POLICY, RELEASES,
     'USED_TOOL_ADMISSION_RELEASES = frozenset({"8.5.0", "9.1.0"})\n',
     F1_CATALOG + "test_85_catalog_order_parity"),
    ("F1", "scope 8.5.0: drop the order gate for every release", GATEWAY, ORDER_EXEMPTION + ORDER_TAIL,
     "            and False\n        )\n", F1_EXACT + "test_85_order_gate_unchanged"),
    ("F1", "require every reviewed tool, used or not, to be present", GATEWAY, ORDER_TAIL,
     ORDER_TAIL.rstrip("\n") + " or (\n            exact_catalog_order_required\n"
     "            and set(observed_reviewed_order) != set(policy_by_name)\n        )\n",
     F1_EXACT + "test_unused_tool_removal_is_admitted"),
    ("F1", "negative: accept a duplicate used tool on a reordered 9.1.0 catalog", GATEWAY, DUPLICATE,
     "                    pass\n                observed_reviewed.setdefault(name, []).append(item)\n",
     F1_EXACT + "test_reordered_catalog_still_refuses_changed_missing_duplicate"),
    # F2: an operation latch ready together with a succeeding step wins.
    ("F2", "drop the both-ready latch check (call_soon latch)", ENDPOINT, LATCH, LATCH_REVERTED,
     F2 + "test_operation_latch_and_step_both_ready_latch_wins"),
    ("F2", "drop the both-ready latch check (one set() wakes step and waiter)", ENDPOINT, LATCH, LATCH_REVERTED,
     F2 + "test_operation_latch_woken_together_with_step_latch_wins"),
    ("F2", "drop the both-ready latch check (latch set inside the succeeding step)", ENDPOINT, LATCH,
     LATCH_REVERTED, F2 + "test_operation_latch_set_inside_succeeding_step_wins"),
    ("F2", "drop the both-ready latch check (public concurrent GET redirect)", ENDPOINT, LATCH, LATCH_REVERTED,
     F2 + "test_public_get_redirect_concurrent_with_tool_result_refused"),
    ("F2", "let a cleanup latch flip a succeeding step", ENDPOINT, LATCH,
     LATCH.replace('self.latch_phase != "cleanup"', "self.latch_phase is not None"),
     F2 + "test_cleanup_latch_does_not_flip_a_succeeding_step"),
    # F3: 9.1.0 analysis requires literal stored_config true before any hash work.
    ("F3", "drop the version-gated stored_config requirement", DASHBOARD, GATE_FULL,
     "    if (\"stored_config\" in payload and payload[\"stored_config\"] is not True):\n",
     F3 + "test_91_without_literal_true_refused_before_hash_work"),
    ("F3", "accept null (and so an absent flag) on 9.1.0", DASHBOARD, GATE,
     GATE.replace("is not True", "not in (True, None)"),
     F3 + "test_91_without_literal_true_refused_before_hash_work"),
    ("F3", "accept a non-boolean truthy flag", DASHBOARD, GATE_FULL,
     "    if (\"stored_config\" in payload and not payload[\"stored_config\"]) or (\n"
     "            upstream_version in STORED_CONFIG_REQUIRED_RELEASES\n"
     "            and not payload.get(\"stored_config\")):\n",
     F3 + "test_91_without_literal_true_refused_before_hash_work"),
    ("F3", "decode without the admitted release context", DASHBOARD,
     '                upstream_version=metadata["upstream_version"])\n', "                upstream_version=None)\n",
     F3 + "test_91_without_literal_true_refused_before_hash_work"),
    ("F3", "empty the stored_config release set", DASHBOARD,
     'STORED_CONFIG_REQUIRED_RELEASES = frozenset({"9.1.0"})\n', "STORED_CONFIG_REQUIRED_RELEASES = frozenset()\n",
     F3 + "test_91_without_literal_true_refused_before_hash_work"),
    ("F3", "hash the configuration before the stored_config gate", DASHBOARD, GATE_ANCHOR,
     "    _upstream_config_hash(payload.get(\"config\") or {})\n" + GATE_ANCHOR,
     F3 + "test_91_without_literal_true_refused_before_hash_work"),
    ("F3", "positive: also require the flag on 8.5.0", DASHBOARD, GATE,
     GATE.replace("STORED_CONFIG_REQUIRED_RELEASES\n", 'STORED_CONFIG_REQUIRED_RELEASES | {"8.5.0"}\n'),
     F3 + "test_85_absent_flag_accepted"),
]

files = sorted({target for _f, _n, target, *_ in mutants}
               | {f"tests/{module}.py" for module in TESTS.values()})
results = {"head": HEAD, "tree": subprocess.check_output(
               ["git", "-C", str(COPY), "rev-parse", "HEAD^{tree}"], text=True).strip(),
           "python": sys.version.split()[0], "files": {path: blob(path) for path in files},
           "mutants": [], "control": None}
for index, (finding, name, target, before, after, test) in enumerate(mutants, 1):
    path = COPY / target
    original = path.read_text()
    assert original.count(before) == 1, (name, original.count(before))
    path.write_text(original.replace(before, after))
    code, raw = run((test,), f"mutant-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "survived")
    results["mutants"].append({"id": index, "finding": finding, "name": name, "file": target, "test": test,
                               "classification": classification, "exit_code": code})
    path.write_text(original)
    print("mutant", index, finding, name, classification, flush=True)

code, raw = run(CONTROL, "control.log")
ran = [line for line in raw.decode(errors="replace").splitlines() if line.startswith("Ran ")]
results["control"] = {"name": "control (unmutated copy, every named test class)", "tests": list(CONTROL),
                      "classification": "pass" if code == 0 else "fail", "exit_code": code,
                      "ran": ran[-1] if ran else None}
(OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results["control"]), flush=True)
if code or any(item["classification"] != "assertion" for item in results["mutants"]):
    sys.exit(1)
