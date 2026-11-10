"""HAMCP-345 mutation evidence: dashboard risk breadth.

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp345/run_mutations.py

Everything runs in a disposable ``git clone --shared`` of HEAD under
``.artifacts/mutation-hamcp345/``; the tree under test is never mutated.
The tests are plan-only or pure analyzer calls against an offline fake
dashboard gateway; no Home Assistant, network, configuration or secret is
read, and no plan is approved or applied.

1. Regressions: the new test module, run against the release base
   (4b8db82b), must fail each named positive test by an assertion; an
   ``ERROR:`` invalidates a result.  Guard tests that also pass on the base
   (explicit action overrides, platform groups, hidden header toggles,
   iframe/map strategies, residuals) are not regressions; they are mutant
   targets below.
2. Mutants: each mutant removes or weakens one HAMCP-345 rule in the HEAD
   source and is killed only by an assertion failure in its named test; an
   error, import failure or survival is not a kill.
3. Control: the unmutated HEAD copy passes the whole module.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / ".artifacts/mutation-hamcp345"
BASE = "4b8db82bc30882b44214ba2d87867fa4e82af3cb"
MODULE = "tests.test_hamcp345_dashboard_risk_breadth"
TEST_FILE = "tests/test_hamcp345_dashboard_risk_breadth.py"
RISK = "hass_mcp_engineering_beta/ha_mcp_engineering/f3_dashboard/risk.py"
EVIDENCE = ROOT / "docs/evidence/hamcp345/mutation-results.json"

V = "ValveControlTests."
G = "GroupRowTests."
H = "HeaderToggleTests."
S = "StrategyTests."
R = "DocumentedResidualTests."
P = "PlanLevelTests."
ROWS = V + "test_entities_rows"
BUTTONS = V + "test_buttons_row_buttons"
SHAPES = V + "test_header_footer_toggle_group_and_area_controls"
CARDS = V + "test_card_defaults"
OVERRIDES = V + "test_explicit_actions_replace_the_default"
ROW_NOT_CARD = V + "test_button_row_is_not_also_a_button_card"
VALVE_UNCHANGED = V + "test_unchanged_valve_control_is_not_newly_changed"
GROUPS = G + "test_group_rows_disclose_unknown_members"
GROUP_TAP = G + "test_group_row_with_its_own_tap_action_is_classified_from_it"
NOT_OVER = G + "test_platform_groups_and_ordinary_rows_are_not_over_classified"
HEADER_TRUE = H + "test_explicit_true_shows_the_toggle"
HEADER_DEFAULT = H + "test_title_and_two_toggleable_rows_show_it_by_default"
HEADER_DOMAINS = H + "test_one_finding_per_toggled_domain"
HEADER_FILTER = H + "test_entity_filter_inner_card"
HEADER_HIDDEN = H + "test_hidden_header_toggle_is_not_classified"
HEADER_IDENTITY = H + "test_unchanged_header_toggle_is_not_newly_changed"
STRATEGIES = S + "test_strategies_disclose_at_every_level"
NO_CONTROL = S + "test_iframe_and_map_strategies_are_not_over_classified"
STRATEGY_IDENTITY = S + "test_unchanged_strategy_is_not_newly_changed"
DEPTH = S + "test_deep_section_nesting_is_disclosed_or_refused_never_dropped"
LIGHT_RESIDUAL = R + "test_light_and_switch_defaults_on_button_shapes_are_unclassified"
COUNTS = R + "test_display_only_counts"

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
    ("valve", ROWS), ("valve", BUTTONS), ("valve", SHAPES), ("valve", CARDS),
    ("valve", ROW_NOT_CARD), ("valve", VALVE_UNCHANGED), ("valve", P + "test_water_main_valve_tile_is_high_consequence"),
    ("valve", P + "test_valve_row_is_high_consequence"),
    ("group", GROUPS), ("group", P + "test_group_row_requires_manual_review"),
    ("header toggle", HEADER_TRUE), ("header toggle", HEADER_DEFAULT), ("header toggle", HEADER_DOMAINS),
    ("header toggle", HEADER_FILTER), ("header toggle", HEADER_IDENTITY),
    ("header toggle", P + "test_header_toggle_is_elevated"),
    ("strategy", STRATEGIES), ("strategy", STRATEGY_IDENTITY), ("strategy", DEPTH),
    ("strategy", P + "test_strategy_view_requires_manual_review"),
    ("residual counts", COUNTS),
]
GROUP_DEFAULT_ROW = ("        if domain == _GROUP_DOMAIN:\n"
                     "            return entity, domain, RiskCategory.UNKNOWN, _GROUP_MEMBERS_REASON\n"
                     "        if domain not in _ENTITY_ROW_CONTROL_DOMAINS:\n")
mutants = [
    ("valve", "valve is not a row control domain", RISK,
     '{"lock", "cover", "alarm_control_panel", "light", "switch", "valve"}',
     '{"lock", "cover", "alarm_control_panel", "light", "switch"}', ROWS),
    ("valve", "the valve-entity row type is not a control", RISK,
     '    "valve-entity": "entity_row_valve_control",\n', "", ROWS),
    ("valve", "a button row ignores its own tap_action", RISK,
     '        if isinstance(row, dict) and "tap_action" in row:\n            return None\n', "", OVERRIDES),
    ("valve", "buttons-row buttons ignore their own tap_action", RISK,
     '                    if "tap_action" in button:\n                        continue\n', "", OVERRIDES),
    ("valve", "the button card ignores its own tap_action", RISK,
     '    elif value.get("type") == _BUTTON_TYPE and "tap_action" not in value:\n',
     '    elif value.get("type") == _BUTTON_TYPE:\n', OVERRIDES),
    ("valve", "the tile icon default is not classified", RISK,
     '    elif value.get("type") == "tile" and "icon_tap_action" not in value:\n',
     '    elif False:\n', CARDS),
    ("valve", "picture-glance ignores force_dialog", RISK,
     '        and _js_falsy(value.get("force_dialog"))\n', "", OVERRIDES),
    ("valve", "picture-glance treats null force_dialog as set", RISK,
     '        and _js_falsy(value.get("force_dialog"))\n',
     '        and "force_dialog" not in value\n', CARDS),
    ("valve", "a button row is also classified as a button card", RISK,
     "    if tokens in classified_locations:\n", "    if False:\n", ROW_NOT_CARD),
    ("valve", "light/switch defaults are classified like valves", RISK,
     "_DEFAULT_TOGGLE_HIGH_DOMAINS = _DOMAINS_TOGGLE & _HIGH_ENTITY_DOMAINS\n",
     "_DEFAULT_TOGGLE_HIGH_DOMAINS = _DOMAINS_TOGGLE\n", LIGHT_RESIDUAL),
    ("group", "a default group row goes quiet", RISK, GROUP_DEFAULT_ROW,
     "        if domain not in _ENTITY_ROW_CONTROL_DOMAINS:\n", GROUPS),
    ("group", "the group-entity row type is not analyzed", RISK,
     "    if row_type in {None, _ENTITY_ROW_TOGGLE_TYPE, _GROUP_ROW_TYPE}:\n",
     "    if row_type in {None, _ENTITY_ROW_TOGGLE_TYPE}:\n", GROUPS),
    ("group", "platform groups are over-classified by name", RISK, GROUP_DEFAULT_ROW,
     GROUP_DEFAULT_ROW.replace("if domain == _GROUP_DOMAIN:", 'if domain == _GROUP_DOMAIN or "group" in str(entity):'),
     NOT_OVER),
    ("group", "a group button row ignores its own tap_action", RISK,
     '        if isinstance(row, dict) and "tap_action" in row:\n            return None\n', "", GROUP_TAP),
    ("header toggle", "the default ignores the title", RISK,
     '            shown = "title" in header_config and len(toggleable) >= 2\n',
     "            shown = len(toggleable) >= 2\n", HEADER_HIDDEN),
    ("header toggle", "one toggleable row shows the default toggle", RISK,
     '            shown = "title" in header_config and len(toggleable) >= 2\n',
     '            shown = "title" in header_config and len(toggleable) >= 1\n', HEADER_HIDDEN),
    ("header toggle", "an explicit key shows the toggle whatever its value", RISK,
     '            shown = not _js_falsy(header_config["show_header_toggle"])\n',
     "            shown = True\n", HEADER_HIDDEN),
    ("header toggle", "an explicit key never shows the toggle", RISK,
     '            shown = not _js_falsy(header_config["show_header_toggle"])\n',
     "            shown = False\n", HEADER_TRUE),
    ("header toggle", "group members are treated as a plain toggle", RISK,
     "                    reason = _HEADER_TOGGLE_GROUP_REASON\n",
     "                    category = RiskCategory.TOGGLE\n"
     '                    reason = "header_toggle_toggle_control"\n', HEADER_DOMAINS),
    ("header toggle", "the toggled entities are not bound", RISK,
     "                        toggled_entities=by_domain[domain],\n", "", HEADER_IDENTITY),
    ("header toggle", "the entity-filter inner card is ignored", RISK,
     '        header_config = value.get("card")\n', "        header_config = None\n", HEADER_FILTER),
    ("strategy", "every strategy is treated as control-free", RISK,
     "            and strategy.get(\"type\") in _NO_CONTROL_STRATEGY_TYPES\n", "", STRATEGIES),
    ("strategy", "iframe and map are not exempt", RISK,
     "            and strategy.get(\"type\") in _NO_CONTROL_STRATEGY_TYPES\n",
     "            and False\n", NO_CONTROL),
    ("strategy", "view sections are not visited", RISK,
     '                for index, section in enumerate(view["sections"])\n',
     '                for index, section in enumerate([])\n', STRATEGIES),
    ("strategy", "nested sections are not visited", RISK,
     '                for index, child in enumerate(section["sections"])\n',
     '                for index, child in enumerate([])\n', STRATEGIES),
    ("strategy", "the strategy configuration is not bound", RISK,
     '                        "strategy": strategy,\n', "", STRATEGY_IDENTITY),
]

results = {"head": HEAD, "base": BASE, "python": sys.version.split()[0],
           "regressions": [], "mutants": [], "control": None}
new_tests = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{HEAD}:{TEST_FILE}"])
checkout(BASE)
(COPY / TEST_FILE).write_bytes(new_tests)
for index, (area, test) in enumerate(regressions, 1):
    code, raw = run(test, f"regression-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "passed")
    results["regressions"].append({"id": index, "area": area, "revision": BASE, "test": test,
                                   "classification": classification, "exit_code": code})
    print("regression", index, area, test, classification, flush=True)

checkout(HEAD)
for index, (area, name, file, before, after, test) in enumerate(mutants, 1):
    path = COPY / file
    original = path.read_text()
    assert original.count(before) == 1, (name, original.count(before))
    path.write_text(original.replace(before, after))
    code, raw = run(test, f"mutant-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "survived")
    results["mutants"].append({"id": index, "area": area, "name": name, "file": file, "test": test,
                               "classification": classification, "exit_code": code})
    path.write_text(original)
    print("mutant", index, area, name, classification, flush=True)

code, raw = run("", "control.log")
ran = [line for line in raw.decode(errors="replace").splitlines() if line.startswith("Ran ")]
results["control"] = {"name": "control (unmutated HEAD copy, whole module)",
                      "classification": "pass" if code == 0 else "fail", "exit_code": code,
                      "ran": ran[-1] if ran else None}
EVIDENCE.write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results["control"]), flush=True)
if (code or any(item["classification"] != "assertion" for item in results["regressions"])
        or any(item["classification"] != "assertion" for item in results["mutants"])):
    sys.exit(1)
