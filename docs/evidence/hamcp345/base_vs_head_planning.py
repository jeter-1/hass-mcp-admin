"""HAMCP-345 evidence: nothing that planned on the release base is newly
refused on HEAD, and an unrelated edit to a dashboard that already holds a
HAMCP-345 shape keeps its base classification.

HAMCP-345 adds findings (valve controls, group rows, the entities-card
header toggle and dashboard/view/section strategies).  Every finding,
changed or not, counts toward the 256-finding evidence bound, so a
dashboard could in principle move from planned to refused.  This check
plans a deterministic corpus of perturbed HAMCP-345 shapes on both the
release base (``origin/release/2.6.0`` at 4b8db82b) and HEAD and fails if

* a variant that planned on the base does not plan on HEAD (either
  scenario), or
* the "existing" scenario (the shape already stored, plus an unrelated
  dashboard-title replace) changes disposition, policy class or
  high-consequence count: unchanged findings must not escalate.

Classification changes in the "inserted" scenario are the intended effect
and are recorded, not failed.

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp345/base_vs_head_planning.py

Both trees are disposable ``git clone --shared`` checkouts under
``.artifacts/base-vs-head-hamcp345/``.  Every plan is created against the
offline fake dashboard gateway of ``tests/test_dashboard_update_mvp.py``
(present at both revisions); no Home Assistant, network, configuration or
secret is read, and no plan is approved or applied.  Writes and
best-practice reads are asserted to stay at zero.

Card-level shapes are planned inside a single view; strategy shapes
replace the whole dashboard (``dashboard`` level), the view, or a section.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / ".artifacts/base-vs-head-hamcp345"
BASE = "4b8db82bc30882b44214ba2d87867fa4e82af3cb"
EVIDENCE = ROOT / "docs/evidence/hamcp345/base-vs-head-results.json"

VALVE = "valve.synthetic_water_main"
GROUP = "group.synthetic_doors"
LOCK = "lock.synthetic_front_door"
FAN = "fan.synthetic"
CARDS = [
    {"type": "entities", "entities": [VALVE, {"entity": VALVE}, {"type": "valve-entity", "entity": VALVE}]},
    {"type": "entities", "entities": [{"type": "button", "entity": VALVE}, {"type": "buttons", "entities": [VALVE, {"entity": VALVE}]}]},
    {"type": "entities", "entities": [GROUP, {"type": "group-entity", "entity": GROUP}, {"type": "conditional", "conditions": [], "row": {"entity": GROUP}}]},
    {"type": "entities", "title": "Synthetic", "entities": [FAN, "light.synthetic"]},
    {"type": "entities", "title": "Synthetic", "show_header_toggle": True, "entities": [VALVE, GROUP, LOCK]},
    {"type": "entity-filter", "entities": [VALVE, FAN], "state_filter": ["open"], "card": {"type": "entities", "title": "Synthetic"}},
    {"type": "tile", "entity": VALVE},
    {"type": "button", "entity": VALVE},
    {"type": "picture-glance", "image": "/local/synthetic.png", "entities": [VALVE, {"entity": GROUP}]},
    {"type": "toggle-group", "entities": [VALVE, GROUP]},
    {"type": "grid", "cards": [{"type": "tile", "entity": "sensor.synthetic_reading"}]},
]
# Whole-dashboard, view and section strategy shapes, planned as the stored
# configuration (existing) or as the proposed one (inserted).
STRATEGIES = [
    {"strategy": {"type": "original-states"}},
    {"views": [{"title": "Synthetic", "strategy": {"type": "areas"}}]},
    {"views": [{"title": "Synthetic", "strategy": {"type": "iframe", "url": "https://example.invalid"}}]},
    {"views": [{"title": "Synthetic", "type": "sections", "sections": [{"strategy": {"type": "common-controls"}}]}]},
]
KEYS = (
    "type", "entity", "entities", "card", "row", "cards", "tap_action", "icon_tap_action",
    "title", "show_header_toggle", "force_dialog", "strategy", "views", "sections",
)
VALUES = (
    {"type": "synthetic"}, {"description": "synthetic"}, ["synthetic"], {}, [], 5, 0, True,
    False, None, "", {"type": {"description": "synthetic"}}, {"type": ["synthetic"]},
)


def object_nodes(value, path=()):
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from object_nodes(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from object_nodes(item, (*path, index))


def node_at(value, path):
    for token in path:
        value = value[token]
    return value


def perturb(kind: str, index: int, shape: dict) -> list[dict]:
    variants = [{"kind": kind, "index": index, "node": None, "key": None, "value": None, "shape": shape}]
    for path, node in object_nodes(shape):
        for key in KEYS:
            if key not in node and key != "type":
                continue
            for value in VALUES:
                variant = deepcopy(shape)
                node_at(variant, path)[key] = deepcopy(value)
                variants.append({"kind": kind, "index": index, "node": list(path), "key": key,
                                 "value": value, "shape": variant})
    return variants


def corpus() -> list[dict]:
    variants = []
    for index, card in enumerate(CARDS):
        variants.extend(perturb("card", index, card))
    for index, strategy in enumerate(STRATEGIES):
        variants.extend(perturb("dashboard", index, strategy))
    return variants


DRIVER = r'''
import asyncio, json, sys, tempfile
from copy import deepcopy
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(root / "tests"))
from ha_mcp_engineering.audit import AuditLogger
from ha_mcp_engineering.errors import GovernanceError
from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.storage import ChangePlanRepository
from test_dashboard_update_mvp import (
    _DashboardGateway, _UnusedConfigurationGateway, _unrelated_lifecycle_provider_identity,
)

def dashboard(cards):
    return {"title": "Synthetic dashboard", "views": [{"title": "Synthetic", "cards": cards}]}

async def plan(configuration, operations):
    with tempfile.TemporaryDirectory(dir=sys.argv[3]) as temporary:
        directory = Path(temporary)
        gateway = _DashboardGateway(version="8.5.0")
        gateway.configuration = deepcopy(configuration)
        repository = ChangePlanRepository(directory / "plans")
        service = ChangeGovernanceService(
            repository, _UnusedConfigurationGateway(),
            AuditLogger(str(directory / "audit.jsonl"), "test-access-secret"),
            dashboard_gateway=gateway, provider_identity_reader=_unrelated_lifecycle_provider_identity,
        )
        try:
            created = await service.create_dashboard_update_plan(
                title="Synthetic dashboard change", description="Plan-only offline corpus.",
                url_path="home-admin-dashboard", patch_operations=operations, expiration_minutes=30,
            )
        except GovernanceError as error:
            details = error.details if isinstance(error.details, dict) else {}
            outcome = {"outcome": "refused", "code": str(getattr(error.code, "value", error.code)),
                       "reason": details.get("dashboard_error_code") or details.get("reason")}
        except Exception as error:
            outcome = {"outcome": "crash", "type": type(error).__name__}
        else:
            update = repository.get(created["plan_id"]).proposed_config["dashboard_update"]
            outcome = {"outcome": "planned", "disposition": update["risk"]["disposition"],
                       "policy_class": created["policy_decision"]["policy_class"],
                       "high": update["risk"]["high_consequence_action_count"]}
        assert gateway.write_count == 0 and gateway.best_practice_count == 0
        return outcome

async def main():
    variants = json.loads(Path(sys.argv[1]).read_text())
    results = []
    retitle = [{"operation_id": "retitle", "operation": "replace", "path": "/title",
                "value": "Renamed synthetic dashboard"}]
    for variant in variants:
        shape = variant["shape"]
        if variant["kind"] == "card":
            existing = await plan(dashboard([shape]), retitle)
            inserted = await plan(dashboard([]), [
                {"operation_id": "insert", "operation": "add", "path": "/views/0/cards/0", "value": shape}])
        else:
            stored = {"title": "Synthetic dashboard", **deepcopy(shape)}
            existing = await plan(stored, retitle)
            key = "strategy" if "strategy" in shape else "views"
            operation = "replace" if key == "views" else "add"
            inserted = await plan(dashboard([]), [
                {"operation_id": "insert", "operation": operation, "path": "/" + key, "value": shape[key]}])
        results.append({"existing": existing, "inserted": inserted})
    Path(sys.argv[2]).write_text(json.dumps(results))

asyncio.run(main())
'''


def checkout(name: str, revision: str) -> Path:
    copy = OUT / name
    subprocess.check_call(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(copy)])
    subprocess.check_call(["git", "-C", str(copy), "checkout", "-q", "--force", revision])
    return copy


def main() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    variants = corpus()
    (OUT / "corpus.json").write_text(json.dumps(variants))
    (OUT / "driver.py").write_text(DRIVER)
    # One revision at a time: this is one heavy job under host admission.
    for name, revision in (("base", BASE), ("head", head)):
        copy = checkout(name, revision)
        scratch = OUT / f"{name}-tmp"
        scratch.mkdir()
        with (OUT / f"{name}.log").open("wb") as log:
            code = subprocess.run(
                [sys.executable, "-B", str(OUT / "driver.py"), str(OUT / "corpus.json"),
                 str(OUT / f"{name}.json"), str(scratch)],
                cwd=copy, stdout=log, stderr=subprocess.STDOUT, timeout=1700).returncode
        if code:
            print(f"{name} driver failed; see {OUT / (name + '.log')}", file=sys.stderr)
            return 2
    base = json.loads((OUT / "base.json").read_text())
    current = json.loads((OUT / "head.json").read_text())
    regressions, escalations, transitions = [], [], Counter()
    for variant, before, after in zip(variants, base, current, strict=True):
        for scenario in ("existing", "inserted"):
            left, right = before[scenario], after[scenario]
            transitions[f"{scenario}: {left['outcome']} -> {right['outcome']}"] += 1
            if left["outcome"] == "planned" and right["outcome"] != "planned":
                regressions.append({"scenario": scenario, "kind": variant["kind"], "index": variant["index"],
                                    "node": variant["node"], "key": variant["key"],
                                    "value": variant["value"], "base": left, "head": right})
            elif left["outcome"] == "planned" and left != right:
                transitions[f"{scenario}: planned with changed classification"] += 1
                if scenario == "existing":
                    escalations.append({"index": variant["index"], "kind": variant["kind"],
                                        "node": variant["node"], "key": variant["key"],
                                        "value": variant["value"], "base": left, "head": right})
    reasons = Counter(
        f"{scenario}: {side}={item[scenario].get('reason')}"
        for side, rows in (("base", base), ("head", current))
        for item in rows for scenario in ("existing", "inserted")
        if item[scenario]["outcome"] != "planned"
    )
    summary = {
        "base": BASE, "head": head, "python": sys.version.split()[0],
        "cards": len(CARDS), "strategies": len(STRATEGIES), "variants": len(variants), "plans_per_revision": 2 * len(variants),
        "transitions": dict(sorted(transitions.items())),
        "non_planned_reasons": dict(sorted(reasons.items())),
        "newly_refused": len(regressions), "newly_refused_examples": regressions[:20],
        "existing_scenario_changed": len(escalations), "existing_scenario_changed_examples": escalations[:20],
    }
    EVIDENCE.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: summary[key] for key in ("variants", "transitions", "newly_refused", "existing_scenario_changed")}, indent=2))
    return 1 if regressions or escalations else 0


if __name__ == "__main__":
    sys.exit(main())
