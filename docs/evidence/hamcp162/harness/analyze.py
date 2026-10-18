#!/usr/bin/env python3
"""HAMCP-162 D1 offline analysis of the disposable capture (host side, stdlib only).

Usage: analyze.py <scratch-dir> <upstream_tool_policy.py> <out.json>

Reads out/catalog-*.json, out/cases-*.json, fixtures/cases.json and the proxy
log, and computes: catalog fingerprints, the R4 complete-descriptor bindings of
ha_config_get_yaml / ha_list_files / ha_read_file with A/B stability, the
8.4.3-vs-8.5.0 descriptor and behaviour diff, the corpus/key parsed-value check,
and per-case component-call slices. No network access.
"""
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

W, POLICY, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
spec = importlib.util.spec_from_file_location("upstream_tool_policy", POLICY)
utp = importlib.util.module_from_spec(spec)
sys.modules["upstream_tool_policy"] = utp
spec.loader.exec_module(utp)

TOOLS = ("ha_config_get_yaml", "ha_list_files", "ha_read_file")
CLASS = {"ha_config_get_yaml": "mixed_or_requires_wrapper", "ha_list_files": "prohibited",
         "ha_read_file": "prohibited"}
FIELDS = ("inputSchema", "outputSchema", "description", "annotations", "title", "_meta", "execution")
VERSIONS = ("8.4.3", "8.5.0")
STOCK = {"8.4.3": "4b75e198df50a633ab94d51f006961c5fc31a1edfcf524b4dc925a48799e98f7",
         "8.5.0": "0d7ecdb7f58b9b3f92fe05e0f3afbd213da6d5841263219d8c12c3dea1d77845"}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def load(p):
    return json.loads(Path(p).read_text())


def fp(v):
    return utp.schema_fingerprint(v)


def binding(tool, version, label, capture_path):
    raw = capture_path.read_bytes()
    out = {
        "binding_model": "hamcp162-optional-tool-binding-v1",
        "tool": tool["name"],
        "version": version,
        "capture_label": label,
        "capture_resource": capture_path.name,
        "capture_sha256": sha(raw),
        "classification": CLASS[tool["name"]],
        "runtime_contract_fingerprint_model": utp.RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2,
        "runtime_contract_fingerprint": utp.runtime_contract_fingerprint(
            tool, model=utp.RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2),
        "runtime_contract_fingerprint_v1": utp.runtime_contract_fingerprint(
            tool, model=utp.RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1),
        "top_level_fields": sorted(tool),
        "diagnostic_fingerprints": {
            **{f"/{f}": fp({"present": f in tool, "value": tool.get(f)}) for f in FIELDS},
            **utp.runtime_contract_field_fingerprints(tool),
        },
        "policy_projection": utp.runtime_policy_state_fingerprint_projection(
            ((tool.get("_meta") or {}).get("ha_mcp") or {}).get("policy")),
    }
    if tool["name"] == "ha_config_get_yaml":
        out["wrapper_token"] = f"ha-mcp-{version}-yaml-fragment-read-v1"
    return out


def tools_by_name(cat):
    return {t["name"]: t for t in cat["tools"]}


def text_of(resp):
    if not resp.get("inline"):
        return None
    res = resp["message"].get("result") or {}
    return {"isError": res.get("isError"), "structured": res.get("structuredContent"),
            "text": [c.get("text") for c in res.get("content", []) if c.get("type") == "text"]}



def _strip(v):
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items() if k not in ("modified", "size", "total_lines")}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v


def norm(resp):
    """Behaviour projection: drop wall-clock and log-size fields that vary per run."""
    if not resp.get("inline"):
        return json.dumps({k: resp.get(k) for k in ("isError", "match_shapes", "structured_top_level")},
                          sort_keys=True)
    msg = resp["message"]
    res = msg.get("result")
    if res is None:
        return json.dumps(msg.get("error"), sort_keys=True)
    body = res.get("structuredContent")
    if body is None:
        texts = [c.get("text") for c in res.get("content", []) if c.get("type") == "text"]
        try:
            body = [json.loads(t) for t in texts]
        except (TypeError, ValueError):
            body = texts
    body = _strip(body)
    if isinstance(body, dict) and body.get("path") == "home-assistant.log":
        body.pop("content", None)
    return json.dumps({"isError": res.get("isError"), "body": body}, sort_keys=True)


report = {"catalogs": {}, "bindings": {}, "stability": {}, "descriptor_diff_843_850": {},
          "behaviour_diff_843_850": [], "corpus_check": {}, "component_calls": {}}

# Catalogs.
for p in sorted((W / "out").glob("catalog-*.json")):
    cat = load(p)
    names = sorted(t["name"] for t in cat["tools"])
    report["catalogs"][cat["label"]] = {
        "file_sha256": sha(p.read_bytes()), "server": cat["initialize_result"]["serverInfo"],
        "protocolVersion": cat["initialize_result"].get("protocolVersion"),
        "tool_count": len(names), "catalog_fingerprint": utp.catalog_fingerprint(cat["tools"]),
        "tool_order_fingerprint": fp([t["name"] for t in cat["tools"]]),
        "filesystem_tools_present": [n for n in names if n in TOOLS + ("ha_write_file", "ha_delete_file")],
        "tool_names": names,
    }
for v in VERSIONS:
    off = report["catalogs"][f"{v}-OFF"]
    off["matches_reviewed_stock_fingerprint"] = off["catalog_fingerprint"] == STOCK[v]
    on = set(report["catalogs"][f"{v}-A"]["tool_names"])
    report["catalogs"][f"{v}-A"]["beta_only_tools"] = sorted(on - set(off["tool_names"]))
    report["catalogs"][f"{v}-A"]["off_only_tools"] = sorted(set(off["tool_names"]) - on)

# Bindings and A/B stability.
for v in VERSIONS:
    a = tools_by_name(load(W / "out" / f"catalog-{v}-A.json"))
    b = tools_by_name(load(W / "out" / f"catalog-{v}-B.json"))
    for name in TOOLS:
        ba = binding(a[name], v, f"{v}-A", W / "out" / f"catalog-{v}-A.json")
        bb = binding(b[name], v, f"{v}-B", W / "out" / f"catalog-{v}-B.json")
        report["bindings"][f"{v}:{name}"] = ba
        varying = sorted(k for k in ba["diagnostic_fingerprints"]
                         if ba["diagnostic_fingerprints"][k] != bb["diagnostic_fingerprints"][k])
        report["stability"][f"{v}:{name}"] = {
            "A_equals_B_complete_v1": ba["runtime_contract_fingerprint_v1"] == bb["runtime_contract_fingerprint_v1"],
            "A_equals_B_v2": ba["runtime_contract_fingerprint"] == bb["runtime_contract_fingerprint"],
            "varying_fields": varying,
        }
    report["stability"][f"{v}:catalog"] = {
        f"A_equals_{run}": report["catalogs"][f"{v}-A"]["catalog_fingerprint"]
        == report["catalogs"][f"{v}-{run}"]["catalog_fingerprint"]
        for run in ("B", "A2", "S", "S2") if f"{v}-{run}" in report["catalogs"]}
for name in TOOLS:
    x, y = report["bindings"][f"8.4.3:{name}"], report["bindings"][f"8.5.0:{name}"]
    report["descriptor_diff_843_850"][name] = {
        "complete_equal": x["runtime_contract_fingerprint_v1"] == y["runtime_contract_fingerprint_v1"],
        "differing_fields": sorted(k for k in x["diagnostic_fingerprints"]
                                   if x["diagnostic_fingerprints"][k] != y["diagnostic_fingerprints"][k]),
    }
    a843 = tools_by_name(load(W / "out" / "catalog-8.4.3-A.json"))[name]
    a850 = tools_by_name(load(W / "out" / "catalog-8.5.0-A.json"))[name]
    if a843.get("description") != a850.get("description"):
        import difflib
        report["descriptor_diff_843_850"][name]["description_diff"] = list(difflib.unified_diff(
            (a843.get("description") or "").splitlines(), (a850.get("description") or "").splitlines(),
            "8.4.3", "8.5.0", lineterm="", n=0))
    for f in ("inputSchema", "outputSchema", "annotations", "_meta"):
        if a843.get(f) != a850.get(f):
            report["descriptor_diff_843_850"][name][f"{f}_843"] = a843.get(f)
            report["descriptor_diff_843_850"][name][f"{f}_850"] = a850.get(f)

# Behaviour diff and corpus check.
exp = load(W / "fixtures" / "cases.json")
runs = {v: load(W / "out" / f"cases-{v}.json") for v in VERSIONS}
by = {v: {c["id"]: c for c in runs[v]["cases"]} for v in VERSIONS}
for cid in by["8.5.0"]:
    c3, c5 = by["8.4.3"].get(cid), by["8.5.0"][cid]
    if c3 is None:
        report["behaviour_diff_843_850"].append({"id": cid, "diff": "missing in 8.4.3"})
    elif norm(c3["response"]) != norm(c5["response"]):
        report["behaviour_diff_843_850"].append({"id": cid, "843": norm(c3["response"])[:600],
                                                 "850": norm(c5["response"])[:600]})
supp = {v: [] for v in VERSIONS}
for suffix in ("supp", "supp2"):
    got = {v: load(W / "out" / f"cases-{v}-{suffix}.json") for v in VERSIONS
           if (W / "out" / f"cases-{v}-{suffix}.json").exists()}
    if len(got) != 2:
        continue
    s3 = {c["id"]: c for c in got["8.4.3"]["cases"]}
    report[f"{suffix}_behaviour_diff_843_850"] = [
        {"id": c["id"], "843": norm(s3[c["id"]]["response"])[:600], "850": norm(c["response"])[:600]}
        for c in got["8.5.0"]["cases"] if norm(s3[c["id"]]["response"]) != norm(c["response"])]
    report[f"{suffix}_results_850"] = {c["id"]: json.loads(norm(c["response"])) for c in got["8.5.0"]["cases"]
                                       if not c["id"].startswith(("supp2:read_file", "supp:config_symlink_inside:read"))}
    for v in VERSIONS:
        supp[v] += got[v]["cases"]
for v in VERSIONS:
    chk = {}
    for case, section, expected in (("main:corpus", "corpus", {k: e["value"] for k, e in exp["corpus"].items()}),
                                    ("main:keys", "keys", exp["keys"])):
        t = text_of(by[v][case]["response"])
        parsed = (t["structured"] or {}).get("matches", [{}])[0].get("parsed") if t else None
        if parsed is None and t:
            parsed = json.loads(t["text"][0])["matches"][0]["parsed"]
        bad = sorted(k for k in expected if (parsed or {}).get(k) != expected[k])
        chk[section] = {"expected": len(expected), "returned": len(parsed or {}),
                        "verbatim_mismatches": bad[:20], "mismatch_count": len(bad)}
    report["corpus_check"][v] = chk

# Component calls per case (proxy log slice by recorded seq bounds).
rows = [json.loads(line) for line in (W / "state" / "proxy.jsonl").read_text().splitlines()]
by_seq = {int(r["seq"]): r for r in rows}
for v in VERSIONS:
    calls = {}
    for c in runs[v]["cases"] + supp[v]:
        lo, hi = c["proxy_seq"]
        hits = [by_seq[s] for s in range(lo, hi + 1) if s in by_seq and by_seq[s].get("kind") == "http"]
        calls[c["id"]] = [{"method": h["method"], "path": h["path"].split("?")[0], "status": h["status"],
                           "request_keys": sorted((h.get("req_body") or {}).get("json") or {}),
                           "response_bytes": (h.get("resp_body") or {}).get("bytes")} for h in hits]
    report["component_calls"][v] = calls
    summary = {}
    for cid, hs in calls.items():
        key = " ".join(f'{h["path"].rsplit("/", 1)[-1]}:{h["status"]}' for h in hs) or "(none)"
        summary.setdefault(key, []).append(cid)
    report["component_calls"][f"{v}:summary"] = {k: {"count": len(ids), "examples": ids[:6]}
                                                  for k, ids in sorted(summary.items())}

report["yaml11_runtime_view_ha"] = runs["8.5.0"]["ha_yaml11_runtime_view"]
OUT.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
print(json.dumps({"catalogs": {k: {x: v[x] for x in ("tool_count", "catalog_fingerprint",
                                                      "matches_reviewed_stock_fingerprint")
                                   if x in v} for k, v in report["catalogs"].items()},
                  "stability": report["stability"],
                  "descriptor_diff": {k: v["differing_fields"] for k, v in report["descriptor_diff_843_850"].items()},
                  "behaviour_diff_count": len(report["behaviour_diff_843_850"]),
                  "supp_diff_count": len(report.get("supp_behaviour_diff_843_850", [])),
                  "supp2_diff_count": len(report.get("supp2_behaviour_diff_843_850", [])),
                  "corpus_check": report["corpus_check"]}, indent=1))
