"""E1: for each S10-changed key, up to three S6 receipt JSON paths where the upstream tool returned it.

Run: python3 -I docs/evidence/hamcp371/s10/e1_receipts.py <repository root>. Output: e1-receipts.txt.
"""
import json, sys
from pathlib import Path
root = Path(sys.argv[1]) / "docs/evidence/hamcp371/s6"
C = json.load(open(Path(sys.argv[1]) / "tests/fixtures/ha_mcp_9_2_0/output-key-contract.json"))
up = {t: r["upstream_tool"] for t, r in C["reads"].items()}
ENT = ["ha_get_entity", "ha_get_entity_exposure", "ha_get_overview", "ha_get_state", "ha_get_todo",
       "ha_get_zone", "ha_list_floors_areas", "ha_list_services"]
ASK = {("ha_config_list_groups", "all"), ("ha_config_list_groups", "unavailable_fields"),
       ("ha_eval_template", "unavailable_fields"), ("ha_list_services", "response"),
       ("ha_list_services", "optional"), ("ha_list_services", "integration"),
       ("ha_list_services", "collapsed")}
ASK |= {(t, "warnings") for t in ENT} | {(t, "partial") for t in ENT}
hits = {k: [] for k in ASK}
def walk(v, path, tool, f):
    if isinstance(v, dict):
        for k, x in v.items():
            p = f"{path}/{k}"
            if (tool, k) in hits and len(hits[(tool, k)]) < 3:
                hits[(tool, k)].append(f"{f}:{p}")
            walk(x, p, tool, f)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            walk(x, f"{path}/{i}", tool, f)
files = 0
for f in sorted(root.rglob("*.json")):
    try:
        rec = json.load(open(f))
    except Exception:
        continue
    ex = rec.get("exchange") if isinstance(rec, dict) else None
    if not isinstance(ex, dict):
        continue
    name = rec.get("upstream_tool")
    res = ((ex.get("response") or {}).get("result") or {}).get("structuredContent")
    if name is None or res is None:
        continue
    files += 1
    for t, u in up.items():
        if u == name:
            walk(res, "/exchange/response/result/structuredContent", t, str(f.relative_to(root)))
print("receipts walked:", files)
for k in sorted(hits):
    print(k, hits[k] or "NO RECEIPT")
