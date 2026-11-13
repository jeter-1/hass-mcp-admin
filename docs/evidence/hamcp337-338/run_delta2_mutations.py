"""HAMCP-337/338 delta evidence: Codex delta review of 0397095a on PR #411
(F1 residual, D1 and D2, all Medium).

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp337-338/run_delta2_mutations.py

Everything runs in a disposable ``git clone --shared`` of HEAD under
``.artifacts/mutation-hamcp337-338-delta2/``; the tree under test is never
mutated.  The tests are plan-only or pure analyzer calls against an offline
fake dashboard gateway; no Home Assistant, network, configuration or secret
is read, and no plan is approved or applied.

1. Regressions: the corrected test module, run against the reviewed head
   (0397095a), must fail each named regression test by an assertion.  The
   D2 tests convert a refusal or crash into an assertion failure, so an
   ``ERROR:`` still invalidates a result.  Guard tests that pass on the
   reviewed head (explicit tap overrides on chips, unrelated area-card
   edits) are not regressions; they are mutant targets below.
2. Mutants: each mutant reopens one reviewed defect in the corrected source
   and is killed only by an assertion failure in its named test; an error,
   import failure or survival is not a kill.
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
OUT = ROOT / ".artifacts/mutation-hamcp337-338-delta2"
REVIEWED = "0397095a2dab591811b44dcfc650695451fd711d"
MODULE = "tests.test_hamcp337_338_dashboard_risk_rows"
TEST_FILE = "tests/test_hamcp337_338_dashboard_risk_rows.py"
RISK = "hass_mcp_engineering_beta/ha_mcp_engineering/f3_dashboard/risk.py"
EVIDENCE = ROOT / "docs/evidence/hamcp337-338/delta2-mutation-results.json"
F1 = "EffectiveDefaultActionTests."
CHIPS = F1 + "test_header_footer_hold_or_double_tap_keeps_default_tap_toggle"
CHIP_GUARD = F1 + "test_header_footer_explicit_tap_override_is_preserved"
ROW_FALLBACK = F1 + "test_service_row_uses_the_falsy_action_fallback"
ROW_TAP = F1 + "test_service_row_explicit_tap_override_is_preserved"
ELEMENT = F1 + "test_service_element_uses_the_nullish_action_fallback"
AREA_STRINGS = F1 + "test_area_control_strings_that_are_not_domains_are_entities"
AREA_FALSY = F1 + "test_falsy_area_controls_use_the_default_domains"
ROW_TYPE = F1 + "test_falsy_row_type_uses_the_domain_default_row"
FILTER = F1 + "test_entity_filter_inner_card_type_follows_the_spread_default"
D1 = "AreaControlContextIdentityTests."
RETARGET = D1 + "test_area_retarget_is_a_changed_high_consequence_control"
EXCLUSION = D1 + "test_exclusion_changes_are_changed_high_consequence_controls"
UNRELATED = D1 + "test_unrelated_edits_leave_the_control_unchanged"
D2 = "NestedTypeDataPlanningTests."
EXISTING = D2 + "test_existing_nested_type_data_still_plans_unrelated_edits"
NEW = D2 + "test_new_nested_type_data_plans_with_its_own_review"
MALFORMED = D2 + "test_malformed_types_are_unclassified_and_valid_types_still_classify"

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


regressions = [
    ("F1 residual", CHIPS), ("F1 residual", ROW_FALLBACK), ("F1 residual", ROW_TAP),
    ("F1 residual", ELEMENT), ("F1 residual", AREA_STRINGS), ("F1 residual", AREA_FALSY),
    ("F1 residual", ROW_TYPE), ("F1 residual", FILTER),
    ("D1", RETARGET), ("D1", EXCLUSION),
    ("D2", EXISTING), ("D2", NEW), ("D2", MALFORMED),
]
EXCLUDE_BINDING = ('                    exclude_entities={\n'
                   '                        "present": "exclude_entities" in value,\n'
                   '                        "value": value.get("exclude_entities"),\n'
                   '                    },\n')
ACTION_GUARD = ('    if action_name is not None and not isinstance(action_name, str):\n'
                '        # HAMCP-337 D2: arbitrary JSON is data, never a crash.\n'
                '        category = RiskCategory.UNKNOWN\n'
                '        reason = "unknown_frontend_action_type"\n'
                '    elif action_name in {None, "none"}:\n')
mutants = [
    ("F1 residual", "any chip gesture suppresses the default tap (the reviewed behavior)", RISK,
     '                if "tap_action" in chip:\n',
     "                if any(key in chip for key in _ACTION_KEYS):\n", CHIPS),
    ("F1 residual", "chips ignore their own tap_action", RISK,
     '                if "tap_action" in chip:\n                    continue\n', "", CHIP_GUARD),
    ("F1 residual", "service rows select by key presence (the reviewed behavior)", RISK,
     '            if not _js_falsy(value.get("action"))\n', '            if "action" in value\n',
     ROW_FALLBACK),
    ("F1 residual", "a service row's own tap_action no longer replaces its service", RISK,
     '        if "tap_action" in value:\n            return None\n', "", ROW_TAP),
    ("F1 residual", "service elements select by key presence", RISK,
     '            if value.get("action") is not None\n', '            if "action" in value\n', ELEMENT),
    ("F1 residual", "service elements use the row's falsy fallback", RISK,
     '            if value.get("action") is not None\n',
     '            if not _js_falsy(value.get("action"))\n', ELEMENT),
    ("F1 residual", "every area-control string is a domain (the reviewed behavior)", RISK,
     "                    elif isinstance(control, str) and control in _AREA_CONTROL_DOMAINS:\n",
     "                    elif isinstance(control, str):\n", AREA_STRINGS),
    ("F1 residual", "only absent area controls use the defaults", RISK,
     "            if _js_falsy(controls):\n", "            if controls is None:\n", AREA_FALSY),
    ("F1 residual", "only null or empty row types use the domain default", RISK,
     "    if _js_falsy(row_type):\n", '    if row_type in (None, ""):\n', ROW_TYPE),
    ("F1 residual", "a non-object entity-filter card renders nothing", RISK,
     'inner.get("type", "entities") if isinstance(inner, dict) else "entities"\n',
     'inner.get("type", "entities") if isinstance(inner, dict) else None\n', FILTER),
    ("F1 residual", "a null entity-filter card type defaults to entities", RISK,
     'inner.get("type", "entities") if isinstance(inner, dict) else "entities"\n',
     '(inner.get("type") or "entities") if isinstance(inner, dict) else "entities"\n', FILTER),
    ("D1", "the area is not bound to the area-controls finding (the reviewed behavior)", RISK,
     '                    area={"present": "area" in value, "value": value.get("area")},\n', "",
     RETARGET),
    ("D1", "exclusions are not bound to the area-controls finding", RISK,
     EXCLUDE_BINDING, "", EXCLUSION),
    ("D1", "the whole card is bound to the area-controls finding", RISK,
     "                    container_card_type=value.get(\"type\"),\n",
     "                    container_card_type=value,\n", UNRELATED),
    ("D2", "service shapes compare an unhashable type (the reviewed behavior)", RISK,
     "    if not isinstance(control_type, str):\n        return None\n    if control_type in _SERVICE_ROW_TYPES:\n",
     "    if control_type in _SERVICE_ROW_TYPES:\n", EXISTING),
    ("D2", "rows compare an unhashable type", RISK,
     "    elif not isinstance(row_type, str):\n        return None\n", "", EXISTING),
    ("D2", "actions compare an unhashable action type", RISK,
     ACTION_GUARD, '    if action_name in {None, "none"}:\n', NEW),
    ("D2", "a malformed row type renders the domain default", RISK,
     "    elif not isinstance(row_type, str):\n        return None\n",
     "    elif not isinstance(row_type, str):\n        row_type = None\n", MALFORMED),
]

results = {"head": HEAD, "reviewed_head": REVIEWED, "python": sys.version.split()[0],
           "regressions": [], "mutants": [], "control": None}
corrected_tests = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{HEAD}:{TEST_FILE}"])
checkout(REVIEWED)
(COPY / TEST_FILE).write_bytes(corrected_tests)
for index, (finding, test) in enumerate(regressions, 1):
    code, raw = run(test, f"regression-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "passed")
    results["regressions"].append({"id": index, "finding": finding, "revision": REVIEWED, "test": test,
                                   "classification": classification, "exit_code": code})
    print("regression", index, finding, test, classification, flush=True)

checkout(HEAD)
for index, (finding, name, file, before, after, test) in enumerate(mutants, 1):
    path = COPY / file
    original = path.read_text()
    assert original.count(before) == 1, (name, original.count(before))
    path.write_text(original.replace(before, after))
    code, raw = run(test, f"mutant-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "survived")
    results["mutants"].append({"id": index, "finding": finding, "name": name, "file": file, "test": test,
                               "classification": classification, "exit_code": code})
    path.write_text(original)
    print("mutant", index, finding, name, classification, flush=True)

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
