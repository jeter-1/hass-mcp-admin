"""Reduce the raw proxy log to a value-free structural record.

The raw log (state/proxy.jsonl) carries the throwaway token in auth frames and
headers, so it is not committed. This keeps only order, kind, method, path,
status, body key names, body byte counts and WS message types.
"""
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
with open(src, encoding="utf-8") as fin, open(dst, "w", encoding="utf-8", newline="\n") as fout:
    for line in fin:
        d = json.loads(line)
        out = {"seq": d["seq"], "kind": d["kind"]}
        if d["kind"] == "marker":
            out["label"] = d["label"]
        elif d["kind"] == "http":
            out["method"] = d["method"]
            out["path"] = d["path"].split("?")[0]
            out["status"] = d["status"]
            rb = d.get("req_body")
            out["req_keys"] = sorted(rb) if isinstance(rb, dict) else None
            out["resp_bytes"] = len(json.dumps(d.get("resp_body"), separators=(",", ":")))
        elif d["kind"] in ("ws_in", "ws_out"):
            j = d.get("json") or {}
            out["conn"] = d["conn"]
            out["type"] = j.get("type")
            out["keys"] = sorted(j)
        else:
            out["conn"] = d["conn"]
        fout.write(json.dumps(out, sort_keys=True) + "\n")
