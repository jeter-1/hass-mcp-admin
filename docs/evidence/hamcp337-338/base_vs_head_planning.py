"""HAMCP-337 D2 evidence: nothing that planned on the release base is newly
refused on HEAD.

Codex's delta review of 0397095a (PR #411) found that nested non-string
``type`` values which planned before #411 were refused as
``dashboard_provider_identity_unavailable``.  This check plans a
deterministic corpus of perturbed dashboards on both the release base
(``origin/release/2.6.0`` at 50e127e5) and HEAD, and fails if any variant
that planned on the base does not plan on HEAD.

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp337-338/base_vs_head_planning.py

Both trees are disposable ``git clone --shared`` checkouts under
``.artifacts/base-vs-head-hamcp337-338/``.  Every plan is created against
the offline fake dashboard gateway of ``tests/test_dashboard_update_mvp.py``
(present at both revisions); no Home Assistant, network, configuration or
secret is read, and no plan is approved or applied.  Writes and
best-practice reads are asserted to stay at zero.

The corpus takes a fixed set of native and custom card shapes and, at every
object node of each card, replaces (or adds) each selected key with each
malformed JSON value below.  Each variant is planned twice: as an existing
card under an unrelated dashboard-title replace, and as a newly inserted
card.  Classification differences (for example a lock row that now plans
elevated) are recorded but are not failures; only a base ``planned`` that
is not a HEAD ``planned`` is.
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
OUT = ROOT / ".artifacts/base-vs-head-hamcp337-338"
BASE = "50e127e5fd3fef0e22036082f440d29c50e91ffc"
EVIDENCE = ROOT / "docs/evidence/hamcp337-338/base-vs-head-results.json"

LOCK = "lock.synthetic_front_door"
COVER = "cover.synthetic_garage"
CARDS = [
    {"type": "entities", "entities": [LOCK, {"entity": COVER}, {"type": "lock-entity", "entity": LOCK}]},
    {"type": "entities", "entities": [{"type": "conditional", "conditions": [], "row": {"entity": LOCK}}]},
    {"type": "entities", "entities": [{"type": "call-service", "name": "Unlock", "action": "lock.unlock"}]},
    {"type": "entities", "entities": [], "header": {"type": "buttons", "entities": [LOCK, {"entity": COVER}]}},
    {"type": "entity-filter", "entities": [LOCK], "state_filter": ["unlocked"], "card": {"type": "entities"}},
    {"type": "toggle-group", "entities": [LOCK, "light.synthetic"]},
    {"type": "area", "area": "synthetic_room_a", "features": [{"type": "area-controls", "controls": ["cover-garage", {"entity_id": LOCK}]}]},
    {"type": "alarm-panel", "entity": "alarm_control_panel.synthetic_home"},
    {"type": "picture-elements", "image": "/local/synthetic.png", "elements": [{"type": "service-button", "title": "Unlock", "action": "lock.unlock"}]},
    {"type": "tile", "entity": LOCK, "tap_action": {"action": "toggle"}, "hold_action": {"action": "perform-action", "perform_action": "lock.unlock"}},
    {"type": "tile", "entity": "sensor.synthetic_reading", "tap_action": {"action": "more-info"}},
    {"type": "custom:synthetic-card", "entity": "sensor.synthetic_reading", "schema": {"type": "object", "items": [{"type": "string"}]}},
    {"type": "conditional", "conditions": [], "card": {"type": "button", "entity": LOCK, "tap_action": {"action": "toggle"}}},
]
KEYS = (
    "type", "action", "service", "perform_action", "controls", "card", "row", "entity",
    "entity_id", "tap_action", "hold_action", "area", "exclude_entities", "entities",
    "features", "header", "elements", "schema",
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


def corpus() -> list[dict]:
    variants = []
    for card_index, card in enumerate(CARDS):
        for path, node in object_nodes(card):
            for key in KEYS:
                if key not in node and key != "type":
                    continue
                for value in VALUES:
                    variant = deepcopy(card)
                    node_at(variant, path)[key] = deepcopy(value)
                    variants.append({"card_index": card_index, "node": list(path), "key": key,
                                     "value": value, "card": variant})
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
    for variant in variants:
        card = variant["card"]
        existing = await plan(dashboard([card]), [
            {"operation_id": "retitle", "operation": "replace", "path": "/title", "value": "Renamed synthetic dashboard"}])
        inserted = await plan(dashboard([]), [
            {"operation_id": "insert", "operation": "add", "path": "/views/0/cards/0", "value": card}])
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
    regressions, transitions = [], Counter()
    for variant, before, after in zip(variants, base, current, strict=True):
        for scenario in ("existing", "inserted"):
            left, right = before[scenario], after[scenario]
            transitions[f"{scenario}: {left['outcome']} -> {right['outcome']}"] += 1
            if left["outcome"] == "planned" and right["outcome"] != "planned":
                regressions.append({"scenario": scenario, "card_index": variant["card_index"],
                                    "node": variant["node"], "key": variant["key"],
                                    "value": variant["value"], "base": left, "head": right})
            elif left["outcome"] == "planned" and left != right:
                transitions[f"{scenario}: planned with changed classification"] += 1
    reasons = Counter(
        f"{scenario}: {side}={item[scenario].get('reason')}"
        for side, rows in (("base", base), ("head", current))
        for item in rows for scenario in ("existing", "inserted")
        if item[scenario]["outcome"] != "planned"
    )
    summary = {
        "base": BASE, "head": head, "python": sys.version.split()[0],
        "cards": len(CARDS), "variants": len(variants), "plans_per_revision": 2 * len(variants),
        "transitions": dict(sorted(transitions.items())),
        "non_planned_reasons": dict(sorted(reasons.items())),
        "newly_refused": len(regressions), "newly_refused_examples": regressions[:20],
    }
    EVIDENCE.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: summary[key] for key in ("variants", "transitions", "newly_refused")}, indent=2))
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
