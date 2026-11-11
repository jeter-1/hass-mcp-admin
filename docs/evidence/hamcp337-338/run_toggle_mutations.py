"""HAMCP-337 toggle-entity evidence (architect follow-up on 144d5639, PR #411).

handle-action.ts (frontend a67b691a) toggles the card or row
``config.entity``; an ``entity`` inside a toggle action is never read.

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp337-338/run_toggle_mutations.py

Everything runs in a disposable ``git clone --shared`` of HEAD under
``.artifacts/mutation-hamcp337-338-toggle/``; the tree under test is never
mutated.  The tests are plan-only against an offline fake dashboard
gateway; no Home Assistant, network, configuration or secret is read, and
no plan is approved or applied.

1. Regressions: the corrected test module, run against 144d5639, must fail
   each named test by an assertion (an ``ERROR:`` invalidates a result).
2. Mutants: each mutant reopens the defect, or replaces the exact rule with
   a looser one, and is killed only by an assertion failure in its named
   test.
3. Control: the unmutated corrected copy passes the whole module.

``base_vs_head_planning.py`` separately proves that nothing which planned on
the release base is refused on HEAD.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / ".artifacts/mutation-hamcp337-338-toggle"
REVIEWED = "144d56391ea1d69dad9054c337ee0183fc9d7c02"
MODULE = "tests.test_hamcp337_338_dashboard_risk_rows"
TEST_FILE = "tests/test_hamcp337_338_dashboard_risk_rows.py"
RISK = "hass_mcp_engineering_beta/ha_mcp_engineering/f3_dashboard/risk.py"
EVIDENCE = ROOT / "docs/evidence/hamcp337-338/toggle-mutation-results.json"
CARD_ENTITY = "ToggleTargetEntityTests.test_toggle_naming_another_entity_classifies_the_card_entity"
EXACT = "ToggleTargetEntityTests.test_toggle_never_follows_the_action_entity"
FIX = "        domain = _entity_domain(card_entity)\n        category = _domain_control_category(domain)\n"

shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
COPY = OUT / "copy"
subprocess.check_call(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(COPY)])


def checkout(revision: str) -> None:
    subprocess.check_call(["git", "-C", str(COPY), "checkout", "-q", "--force", revision])
    subprocess.check_call(["git", "-C", str(COPY), "clean", "-q", "-fdx"])


def run(test: str, log: str) -> tuple[int, bytes]:
    for cache in COPY.rglob("__pycache__"):
        shutil.rmtree(cache)
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", f"{MODULE}.{test}" if test else MODULE],
                          cwd=COPY, capture_output=True, timeout=1800)
    raw = proc.stdout + proc.stderr
    (OUT / log).write_bytes(raw)
    return proc.returncode, raw


def assertion_failure(code: int, raw: bytes) -> bool:
    return bool(code) and b"FAIL:" in raw and b"AssertionError" in raw and b"ERROR:" not in raw


regressions = [CARD_ENTITY, EXACT]
mutants = [
    ("the action entity selects the toggle target (the reviewed behavior)",
     FIX, "        category = _domain_control_category(domain)\n", CARD_ENTITY),
    ("the action entity is used when the card has no entity",
     FIX, "        domain = _entity_domain(card_entity) or domain\n"
          "        category = _domain_control_category(domain)\n", EXACT),
    ("either entity in a high domain makes the toggle high (union, not the exact rule)",
     FIX, "        domain = domain if domain in _HIGH_ENTITY_DOMAINS else _entity_domain(card_entity)\n"
          "        category = _domain_control_category(domain)\n", EXACT),
]

results = {"head": HEAD, "reviewed_head": REVIEWED, "python": sys.version.split()[0],
           "regressions": [], "mutants": [], "control": None}
corrected_tests = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{HEAD}:{TEST_FILE}"])
checkout(REVIEWED)
(COPY / TEST_FILE).write_bytes(corrected_tests)
for index, test in enumerate(regressions, 1):
    code, raw = run(test, f"regression-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "passed")
    failed = raw.decode(errors="replace").count("\nFAIL: ")
    results["regressions"].append({"id": index, "revision": REVIEWED, "test": test,
                                   "classification": classification, "exit_code": code,
                                   "failed_subtests": failed})
    print("regression", index, test, classification, failed, flush=True)

checkout(HEAD)
for index, (name, before, after, test) in enumerate(mutants, 1):
    path = COPY / RISK
    original = path.read_text()
    assert original.count(before) == 1, (name, original.count(before))
    path.write_text(original.replace(before, after))
    code, raw = run(test, f"mutant-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "survived")
    results["mutants"].append({"id": index, "name": name, "file": RISK, "test": test,
                               "classification": classification, "exit_code": code})
    path.write_text(original)
    print("mutant", index, name, classification, flush=True)

code, raw = run("", "control.log")
ran = [line for line in raw.decode(errors="replace").splitlines() if line.startswith("Ran ")]
results["control"] = {"name": "control (unmutated corrected copy, whole module)",
                      "classification": "pass" if code == 0 else "fail", "exit_code": code,
                      "ran": ran[-1] if ran else None}
EVIDENCE.write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results["control"]), flush=True)
if (code or any(item["classification"] != "assertion" for item in results["regressions"])
        or any(item["classification"] != "assertion" for item in results["mutants"])):
    sys.exit(1)
