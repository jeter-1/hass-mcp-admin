"""HAMCP-345 evidence: no toggle-group shape gets a lower disposition on
HEAD than on the release base.

Owner ruling 2026-11-10: a toggle-group card is classified by what the card
sends (one service from ``entities[0]`` for the whole list); a list whose
domains differ requires manual review; no shape may get a lower disposition
than on base.  This check runs the pure ``analyze_dashboard_risk`` function
of the release base (``release/2.6.0`` at cb9d6783, the PR merge-base) and
of HEAD over the same toggle-group configurations and fails if

* HEAD's disposition ranks below the base's for any case
  (``standard_review`` < ``elevated_manual_review`` <
  ``manual_review_required``), or
* HEAD raises where the base returned a disposition.

Cases:

* ``inserted``: every entity list of length 0-2 over ``POOL`` and of length
  3 over ``TRIPLE_POOL``, added to an empty view; the same lists inside a
  ``vertical-stack`` and a ``conditional`` card; and ``entities`` values
  that are not a list.
* ``existing``: the same card already stored, plus an unrelated card.
* ``transition``: every ordered pair of lists of length 0-2 over
  ``PAIR_POOL`` (reorder, append, remove, retarget).

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp345/toggle_group_base_vs_head.py

Both trees are disposable ``git clone --shared`` checkouts under
``.artifacts/toggle-group-base-vs-head-hamcp345/``.  Only the pure analysis
function is called: no plan is created, and no Home Assistant, network,
configuration or secret is read.  All entity IDs are synthetic.
"""
from __future__ import annotations

from collections import Counter
from itertools import product
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / ".artifacts/toggle-group-base-vs-head-hamcp345"
BASE = "cb9d67833082d517d7155e99481d4fd517c5d43d"
EVIDENCE = ROOT / "docs/evidence/hamcp345/toggle-group-base-vs-head-results.json"
RANK = {"standard_review": 0, "elevated_manual_review": 1, "manual_review_required": 2}

LIGHT = "light.synthetic_lamp"
LOCK = "lock.synthetic_front_door"
VALVE = "valve.synthetic_water_main"
GROUP = "group.synthetic_doors"
FAN = "fan.synthetic"
POOL = [
    LIGHT, "light.synthetic_second", "switch.synthetic", FAN, "input_boolean.synthetic",
    LOCK, "lock.synthetic_back_door", "cover.synthetic_garage",
    "alarm_control_panel.synthetic", VALVE, "valve.synthetic_second",
    GROUP, "group.synthetic_windows", "sensor.synthetic",
    "nodot", "", ".synthetic", 5, None, {"entity": LOCK}, [LOCK],
]
TRIPLE_POOL = [LIGHT, LOCK, VALVE, GROUP, FAN, 5]
PAIR_POOL = [
    LIGHT, "switch.synthetic", FAN, LOCK, "cover.synthetic_garage", VALVE, GROUP,
    "sensor.synthetic", 5,
]
NOT_A_LIST = [None, LOCK, {"0": LOCK}, 5]
# The shapes named in the correction request, reported one by one.
NAMED = {
    "single domain high (lock, lock)": [LOCK, "lock.synthetic_back_door"],
    "single domain high (valve only)": [VALVE],
    "single domain high (valve, valve)": [VALVE, "valve.synthetic_second"],
    "single domain toggle (light, light)": [LIGHT, "light.synthetic_second"],
    "group entity only": [GROUP],
    "group, group": [GROUP, "group.synthetic_windows"],
    "mixed light + lock": [LIGHT, LOCK],
    "light + valve": [LIGHT, VALVE],
    "valve + light": [VALVE, LIGHT],
    "light + light + valve": [LIGHT, "light.synthetic_second", VALVE],
    "lock first (lock + light)": [LOCK, LIGHT],
    "light + group + lock": [LIGHT, GROUP, LOCK],
    "group + lock": [GROUP, LOCK],
    "unclassified pair (fan + input_boolean)": [FAN, "input_boolean.synthetic"],
    "unclassified single domain (fan, fan)": [FAN, FAN],
    "malformed first (5, lock)": [5, LOCK],
    "malformed later (lock, 5)": [LOCK, 5],
    "malformed only (5, None)": [5, None],
    "object entry ({entity: lock}, light)": [{"entity": LOCK}, LIGHT],
    "empty list": [],
}

DRIVER = r'''
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.f3_dashboard.risk import analyze_dashboard_risk

results = []
for current, proposed in json.loads(Path(sys.argv[1]).read_text()):
    try:
        risk = analyze_dashboard_risk(current, proposed)
    except Exception as error:
        results.append({"error": type(error).__name__})
    else:
        results.append({
            "disposition": risk.disposition.value,
            "changed": sorted(
                [item.category.value, item.path, item.reason_code]
                for item in risk.findings if item.introduced_or_changed
            ),
        })
Path(sys.argv[2]).write_text(json.dumps(results))
'''


def card(entities) -> dict:
    return {"type": "toggle-group", "entities": entities}


def dashboard(*cards) -> dict:
    return {"views": [{"cards": list(cards)}]}


def lists(pool, lengths) -> list[list]:
    return [list(items) for length in lengths for items in product(pool, repeat=length)]


def corpus() -> list[dict]:
    cases = []
    singles = lists(POOL, (0, 1, 2)) + lists(TRIPLE_POOL, (3,)) + list(NAMED.values())
    markdown = {"type": "markdown", "content": "x"}
    for entities in singles:
        shape = card(entities)
        cases.append({"scenario": "inserted", "entities": entities,
                      "pair": [dashboard(), dashboard(shape)]})
        cases.append({"scenario": "inserted in vertical-stack", "entities": entities,
                      "pair": [dashboard(), dashboard({"type": "vertical-stack", "cards": [shape]})]})
        cases.append({"scenario": "inserted in conditional", "entities": entities,
                      "pair": [dashboard(),
                               dashboard({"type": "conditional", "conditions": [], "card": shape})]})
        cases.append({"scenario": "existing", "entities": entities,
                      "pair": [dashboard(shape), dashboard(shape, markdown)]})
    for value in NOT_A_LIST:
        cases.append({"scenario": "inserted", "entities": value,
                      "pair": [dashboard(), dashboard(card(value))]})
    pairs = lists(PAIR_POOL, (0, 1, 2))
    for before in pairs:
        for after in pairs:
            if before != after:
                cases.append({"scenario": "transition", "entities": [before, after],
                              "pair": [dashboard(card(before)), dashboard(card(after))]})
    return cases


def checkout(name: str, revision: str) -> Path:
    copy = OUT / name
    subprocess.check_call(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(copy)])
    subprocess.check_call(["git", "-C", str(copy), "checkout", "-q", "--force", revision])
    return copy


def main() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    cases = corpus()
    (OUT / "corpus.json").write_text(json.dumps([case["pair"] for case in cases]))
    (OUT / "driver.py").write_text(DRIVER)
    for name, revision in (("base", BASE), ("head", head)):
        copy = checkout(name, revision)
        with (OUT / f"{name}.log").open("wb") as log:
            code = subprocess.run(
                [sys.executable, "-B", str(OUT / "driver.py"), str(OUT / "corpus.json"),
                 str(OUT / f"{name}.json")],
                cwd=copy, stdout=log, stderr=subprocess.STDOUT, timeout=900).returncode
        if code:
            print(f"{name} driver failed; see {OUT / (name + '.log')}", file=sys.stderr)
            return 2
    base = json.loads((OUT / "base.json").read_text())
    current = json.loads((OUT / "head.json").read_text())
    lower, errors, transitions, scenarios = [], [], Counter(), Counter()
    named, named_keys = {}, {json.dumps(value): key for key, value in NAMED.items()}
    for case, before, after in zip(cases, base, current, strict=True):
        scenarios[case["scenario"]] += 1
        left = before.get("disposition", "error")
        right = after.get("disposition", "error")
        transitions[f"{case['scenario']}: {left} -> {right}"] += 1
        record = {"scenario": case["scenario"], "entities": case["entities"],
                  "base": before, "head": after}
        if "error" in after:
            if "error" not in before:
                errors.append(record)
        elif "error" not in before and RANK[right] < RANK[left]:
            lower.append(record)
        key = named_keys.get(json.dumps(case["entities"]))
        if key is not None and case["scenario"] in ("inserted", "existing"):
            named.setdefault(key, {"entities": case["entities"]})[case["scenario"]] = {
                "base": left, "head": right,
                "head_changed": after.get("changed"),
            }
    summary = {
        "base": BASE, "head": head, "python": sys.version.split()[0],
        "order": "standard_review < elevated_manual_review < manual_review_required",
        "cases": len(cases), "cases_by_scenario": dict(sorted(scenarios.items())),
        "transitions": dict(sorted(transitions.items())),
        "head_lower_than_base": len(lower), "head_lower_than_base_examples": lower[:20],
        "head_raises_where_base_did_not": len(errors),
        "head_raises_where_base_did_not_examples": errors[:20],
        "named_shapes": {key: named[key] for key in NAMED},
    }
    EVIDENCE.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: summary[key] for key in (
        "cases", "transitions", "head_lower_than_base", "head_raises_where_base_did_not")}, indent=2))
    return 1 if lower or errors else 0


if __name__ == "__main__":
    sys.exit(main())
