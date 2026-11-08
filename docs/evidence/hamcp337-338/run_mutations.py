"""HAMCP-337/338 delta evidence: Codex whole-PR review F1 and F2 (both
Medium) on PR #411, reviewed at fc88e858.

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp337-338/run_mutations.py

Everything runs in a disposable ``git clone --shared`` of HEAD under
``.artifacts/mutation-hamcp337-338/``; the tree under test is never mutated.
The tests are plan-only or pure analyzer calls against an offline fake
dashboard gateway; no Home Assistant, network, configuration or secret is
read, and no plan is approved or applied.

1. Regressions: the corrected test module, run against the reviewed head
   (fc88e858), must fail each named public regression test by an assertion.
   The F2 unit tests call the corrected ``_card_path(document, tokens)``
   signature and are not regressions; F2's regression is the public
   refusal test.
2. Mutants: each mutant reopens one reviewed defect in the corrected source
   and is killed only by an assertion failure in its named test; an error,
   import failure or survival is not a kill.
3. Control: the unmutated corrected copy passes the whole module.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / ".artifacts/mutation-hamcp337-338"
REVIEWED = "fc88e858ad2b1b243fb15a23774887bd219d2fc8"
MODULE = "tests.test_hamcp337_338_dashboard_risk_rows"
TEST_FILE = "tests/test_hamcp337_338_dashboard_risk_rows.py"
RISK = "hass_mcp_engineering_beta/ha_mcp_engineering/f3_dashboard/risk.py"
SHAPES = "NativeControlShapeTests."
F1_TYPED = "TypedNativeRowPlanningTests.test_typed_lock_and_cover_rows_plan_elevated_review"
F1_MATRIX = SHAPES + "test_typed_row_matrix_for_the_owner_directed_domains"
F1_WRAPPED = SHAPES + "test_conditional_and_entity_filter_rows_are_classified"
F1_BUTTONS = SHAPES + "test_buttons_header_and_footer_toggle_every_chip"
F1_GROUP = SHAPES + "test_toggle_group_card_targets_each_entity"
F1_AREA = SHAPES + "test_area_controls_feature_is_classified_from_its_domains"
F1_ALARM = SHAPES + "test_alarm_panel_card_renders_arm_and_disarm_controls"
F1_SERVICE = SHAPES + "test_service_rows_and_elements_are_classified_by_service"
F1_ICON = SHAPES + "test_tile_icon_gestures_are_action_keys"
F2_PUBLIC = "RiskAnalysisRefusalTests.test_digit_only_mapping_key_is_not_echoed_as_an_index"
F2_POSITIONS = "StructuralCardPathTests.test_card_path_keeps_only_proven_list_positions"
F2_WRAPPERS = "StructuralCardPathTests.test_wrappers_are_kept_only_for_object_values"

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
    ("F1", F1_TYPED), ("F1", F1_MATRIX), ("F1", F1_WRAPPED), ("F1", F1_BUTTONS),
    ("F1", F1_GROUP), ("F1", F1_AREA), ("F1", F1_ALARM), ("F1", F1_SERVICE),
    ("F1", F1_ICON), ("F2", F2_PUBLIC),
]
WALK = ("            if not isinstance(child, list) or index + 1 >= len(tokens):\n"
        "                break\n"
        "            position = tokens[index + 1]\n"
        "            if (\n"
        "                not _structural_index(position)\n"
        "                or str(int(position)) != position\n"
        "                or int(position) >= len(child)\n"
        "            ):\n"
        "                break\n"
        '            segments.append(f"/{token}/{position}")\n'
        "            node = child[int(position)]\n")
SPELLING_WALK = ("            if index + 1 >= len(tokens):\n"
                 "                break\n"
                 "            position = tokens[index + 1]\n"
                 "            if not _structural_index(position):\n"
                 "                break\n"
                 '            segments.append(f"/{token}/{position}")\n'
                 "            node = child[int(position)] if isinstance(child, list) else child.get(position)\n")
mutants = [
    ("F1", "typed rows are skipped again (the reviewed behavior)", RISK,
     '        row_type = row.get("type")\n    else:\n',
     '        row_type = row.get("type")\n        if "type" in row:\n            return None\n    else:\n',
     F1_TYPED),
    ("F1", "lock-entity and cover-entity rows are not classified", RISK,
     "    if row_type in _ENTITY_ROW_TYPE_CONTROLS:\n", "    if False:\n", F1_TYPED),
    ("F1", "toggle-entity rows are not classified", RISK,
     "    if row_type in {None, \"\", _ENTITY_ROW_TOGGLE_TYPE}:\n",
     "    if row_type in {None, \"\"}:\n", F1_TYPED),
    ("F1", "a conditional row's wrapped row is not unwrapped", RISK,
     "            and row.get(\"type\") == _CONDITIONAL_ROW_TYPE\n", "            and False\n", F1_WRAPPED),
    ("F1", "entity-filter rows are not classified", RISK,
     '    if card_type == "entity-filter":\n', "    if False:\n", F1_WRAPPED),
    ("F1", "buttons header/footer chips are not classified", RISK,
     '        and value.get("type") == "buttons"\n', "        and False\n", F1_BUTTONS),
    ("F1", "toggle-group cards are not classified", RISK,
     '    if value.get("type") == "toggle-group" and', "    if False and", F1_GROUP),
    ("F1", "area-controls defaults omit the cover classes", RISK,
     "            if controls is None:\n                domains.add(\"cover\")\n",
     "            if controls is None:\n                pass\n", F1_AREA),
    ("F1", "the alarm-panel card is display-only again", RISK,
     '    if value.get("type") == "alarm-panel" and', "    if False and", F1_ALARM),
    ("F1", "service rows and elements are not classified", RISK,
     "    if value.get(\"type\") not in _SERVICE_CONTROL_TYPES:\n", "    if True:\n", F1_SERVICE),
    ("F1", "tile icon gestures are not action keys", RISK,
     '        "icon_tap_action",\n', "", F1_ICON),
    ("F2", "the walk trusts token spelling instead of the document (reviewed behavior)", RISK,
     WALK, SPELLING_WALK, F2_PUBLIC),
    ("F2", "non-canonical index spellings are kept", RISK,
     "                or str(int(position)) != position\n", "", F2_POSITIONS),
    ("F2", "wrapper tokens are kept for non-object values", RISK,
     "        elif token in STRUCTURAL_CARD_WRAPPERS and isinstance(child, dict):\n",
     "        elif token in STRUCTURAL_CARD_WRAPPERS:\n", F2_WRAPPERS),
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
(OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results["control"]), flush=True)
if (code or any(item["classification"] != "assertion" for item in results["regressions"])
        or any(item["classification"] != "assertion" for item in results["mutants"])):
    sys.exit(1)
