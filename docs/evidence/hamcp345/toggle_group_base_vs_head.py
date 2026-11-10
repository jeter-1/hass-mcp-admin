"""HAMCP-345 evidence: no toggle-group shape gets a lower disposition, and
no card a lower high-consequence count, on HEAD than on the release base.

Owner rulings 2026-11-10: a toggle-group card is classified by what the card
sends (one service from ``entities[0]`` for the whole list); a list whose
domains differ requires manual review when it names a domain the risk module
classifies or holds an entry with no domain; a mixed list counts one
high-consequence action per high-consequence entry it lists; no shape may
get a lower disposition than on base and no card's count may fall below
base.  This check runs the pure ``analyze_dashboard_risk`` function of the
release base (``release/2.6.0`` at cb9d6783, the PR merge-base) and of HEAD
over the same toggle-group configurations and fails if

* HEAD's disposition ranks below the base's for any case
  (``standard_review`` < ``elevated_manual_review`` <
  ``manual_review_required``),
* HEAD's ``high_consequence_action_count`` is below the base's for any
  case,
* HEAD raises where the base returned a disposition, or
* a proposed mixed list of unclassified domains only (no classified domain
  and no entry without a domain) differs from the base in disposition,
  count or changed findings.

Cases:

* ``inserted``: every entity list of length 0-2 over ``POOL`` and of length
  3 over ``TRIPLE_POOL``, added to an empty view; the same lists inside a
  ``vertical-stack`` and a ``conditional`` card; and ``entities`` values
  that are not a list.
* ``existing``: the same card already stored, plus an unrelated card.
* ``transition``: every ordered pair of lists of length 0-2 over
  ``PAIR_POOL`` (reorder, append, remove, retarget), and every ordered pair
  of the ``NAMED`` lists.

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
LOCK2 = "lock.synthetic_back_door"
COVER = "cover.synthetic_garage"
COVER2 = "cover.synthetic_shade"
ALARM = "alarm_control_panel.synthetic"
VALVE = "valve.synthetic_water_main"
VALVE2 = "valve.synthetic_second"
GARAGE = "garage_door.synthetic"
GROUP = "group.synthetic_doors"
SWITCH = "switch.synthetic"
FAN = "fan.synthetic"
INPUT_BOOLEAN = "input_boolean.synthetic"
SENSOR = "sensor.synthetic"
BINARY_SENSOR = "binary_sensor.synthetic"
POOL = [
    LIGHT, "light.synthetic_second", SWITCH, FAN, INPUT_BOOLEAN,
    LOCK, LOCK2, COVER, COVER2, ALARM, VALVE, VALVE2, GARAGE,
    GROUP, "group.synthetic_windows", SENSOR, BINARY_SENSOR,
    "nodot", "", ".synthetic", 5, None, {"entity": LOCK}, [LOCK],
]
TRIPLE_POOL = [LIGHT, SWITCH, LOCK, LOCK2, COVER, ALARM, VALVE, GARAGE, GROUP, FAN, SENSOR, 5]
PAIR_POOL = [
    LIGHT, SWITCH, FAN, INPUT_BOOLEAN, LOCK, COVER, ALARM, VALVE, GARAGE, GROUP,
    SENSOR, 5,
]
NOT_A_LIST = [None, LOCK, {"0": LOCK}, 5]
# "A domain the risk module classifies" (direct-control domains, the
# high-consequence entity domains and ``group``), written out here so that
# this check does not depend on the module under test.
CLASSIFIED = {
    "alarm_control_panel", "cover", "garage_door", "group", "light", "lock",
    "switch", "valve",
}
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
    # Second correction: unclassified-only mixed lists keep the base result.
    "unclassified pair (sensor + binary_sensor)": [SENSOR, BINARY_SENSOR],
    "unclassified first, classified later (fan + light)": [FAN, LIGHT],
    "unclassified first, classified later (sensor + lock)": [SENSOR, LOCK],
    "classified first, unclassified later (light + fan)": [LIGHT, FAN],
    "classified first, unclassified later (lock + sensor)": [LOCK, SENSOR],
    "malformed in an unclassified list (fan, 5)": [FAN, 5],
    "malformed in an unclassified list (5, fan)": [5, FAN],
    "object entry in an unclassified list (fan, {entity: fan})": [FAN, {"entity": FAN}],
    # Second correction: one high-consequence action per listed entry.
    "lock + cover": [LOCK, COVER],
    "cover + lock": [COVER, LOCK],
    "lock + alarm_control_panel": [LOCK, ALARM],
    "lock + cover + alarm_control_panel": [LOCK, COVER, ALARM],
    "lock + lock + light": [LOCK, LOCK2, LIGHT],
    "light + lock + lock": [LIGHT, LOCK, LOCK2],
    "light + lock + cover": [LIGHT, LOCK, COVER],
    "cover + alarm_control_panel + light": [COVER, ALARM, LIGHT],
    "cover + cover + switch": [COVER, COVER2, SWITCH],
    "fan + lock + cover": [FAN, LOCK, COVER],
    "group + lock + cover": [GROUP, LOCK, COVER],
    "malformed + lock + cover": [5, LOCK, COVER],
    "light + lock + valve": [LIGHT, LOCK, VALVE],
    "lock + valve": [LOCK, VALVE],
    "valve + lock": [VALVE, LOCK],
    "lock + garage_door": [LOCK, GARAGE],
    "valve + valve + light": [VALVE, VALVE2, LIGHT],
    "light + garage_door": [LIGHT, GARAGE],
    "single domain (garage_door, garage_door)": [GARAGE, "garage_door.synthetic_second"],
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
            "count": risk.high_consequence_action_count,
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


def domain(entity) -> str | None:
    if not isinstance(entity, str) or "." not in entity:
        return None
    return entity.split(".", 1)[0] or None


def unclassified_mixed(entities) -> bool:
    """A list whose domains differ, with a domain on every entry and none of
    them one the risk module classifies."""

    if not isinstance(entities, list):
        return False
    domains = [domain(entity) for entity in entities]
    return (
        len(set(domains)) > 1
        and None not in domains
        and not CLASSIFIED.intersection(domains)
    )


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
    # Every ordered pair of the named lists that the loop above did not cover.
    for before in NAMED.values():
        for after in NAMED.values():
            if before != after and not (before in pairs and after in pairs):
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
    lower_count, higher_count, counts = [], 0, Counter()
    unclassified, unclassified_differ = 0, []
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
        elif "error" not in before:
            if RANK[right] < RANK[left]:
                lower.append(record)
            counts[f"{case['scenario']}: {before['count']} -> {after['count']}"] += 1
            if after["count"] < before["count"]:
                lower_count.append(record)
            elif after["count"] > before["count"]:
                higher_count += 1
        proposed = case["entities"][1] if case["scenario"] == "transition" else case["entities"]
        if unclassified_mixed(proposed):
            unclassified += 1
            if before != after:
                unclassified_differ.append(record)
        key = named_keys.get(json.dumps(case["entities"]))
        if key is not None and case["scenario"] in ("inserted", "existing"):
            named.setdefault(key, {"entities": case["entities"]})[case["scenario"]] = {
                "base": left, "head": right,
                "base_count": before.get("count"), "head_count": after.get("count"),
                "head_changed": after.get("changed"),
            }
    summary = {
        "base": BASE, "head": head, "python": sys.version.split()[0],
        "order": "standard_review < elevated_manual_review < manual_review_required",
        "cases": len(cases), "cases_by_scenario": dict(sorted(scenarios.items())),
        "transitions": dict(sorted(transitions.items())),
        "head_lower_than_base": len(lower), "head_lower_than_base_examples": lower[:20],
        "count": "high_consequence_action_count, base -> head",
        "count_transitions": dict(sorted(counts.items())),
        "head_count_lower_than_base": len(lower_count),
        "head_count_lower_than_base_examples": lower_count[:20],
        "head_count_higher_than_base": higher_count,
        "head_raises_where_base_did_not": len(errors),
        "head_raises_where_base_did_not_examples": errors[:20],
        "unclassified_mixed_lists": unclassified,
        "unclassified_mixed_lists_differing_from_base": len(unclassified_differ),
        "unclassified_mixed_lists_differing_from_base_examples": unclassified_differ[:20],
        "named_shapes": {key: named[key] for key in NAMED},
    }
    EVIDENCE.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: summary[key] for key in (
        "cases", "cases_by_scenario", "transitions", "head_lower_than_base",
        "count_transitions", "head_count_lower_than_base", "head_count_higher_than_base",
        "head_raises_where_base_did_not", "unclassified_mixed_lists",
        "unclassified_mixed_lists_differing_from_base")}, indent=2))
    return 1 if lower or lower_count or errors or unclassified_differ else 0


if __name__ == "__main__":
    sys.exit(main())
