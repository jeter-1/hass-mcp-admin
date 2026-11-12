"""Per-call comparison of the two captures of one S6 profile A run (HAMCP-371).

Reads capture files as data only (run with ``python3 -I``).  Prints JSON.
"""
import json
import sys
from pathlib import Path


def payload(call):
    exchange = call.get("exchange") or {}
    for message in exchange.get("messages") or []:
        if "error" in message:
            return {"json_rpc_error": message["error"]}, None, "json_rpc_error"
        result = message.get("result")
        if isinstance(result, dict):
            text = None
            for item in result.get("content") or []:
                if item.get("type") == "text":
                    text = item.get("text")
                    break
            try:
                parsed = json.loads(text) if text is not None else None
            except ValueError:
                parsed = None
            structured = result.get("structuredContent")
            return (structured if structured is not None else parsed), {
                "is_error": result.get("isError"),
                "has_structured_content": structured is not None,
                "text_is_json": parsed is not None,
                "text_equals_structured": (structured is None or parsed == structured),
                "content_items": len(result.get("content") or []),
                "result_keys": sorted(result)}, "result"
    return None, None, "no_message"


def paths(value, prefix="", out=None):
    out = set() if out is None else out
    if isinstance(value, dict):
        for key, item in value.items():
            here = prefix + "." + key if prefix else key
            out.add(here)
            paths(item, here, out)
    elif isinstance(value, list):
        for item in value:
            paths(item, prefix + "[]", out)
    return out


def scalars(value, prefix="", out=None):
    out = {} if out is None else out
    if isinstance(value, dict):
        for key, item in value.items():
            scalars(item, prefix + "." + key if prefix else key, out)
    elif isinstance(value, list):
        out[prefix + "#len"] = len(value)
        for index, item in enumerate(value):
            scalars(item, f"{prefix}[{index}]", out)
    else:
        out[prefix] = value
    return out


def error_shape(data):
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    if isinstance(error, dict):
        return {"success": data.get("success"), "error_type": "object",
                "code": error.get("code"), "error_keys": sorted(error),
                "top_level_keys": sorted(data)}
    if error is not None:
        return {"success": data.get("success"), "error_type": type(error).__name__,
                "top_level_keys": sorted(data)}
    return None


def describe(directory, name):
    call = json.loads((directory / name).read_text(encoding="utf-8"))
    data, meta, kind = payload(call)
    exchange = call.get("exchange") or {}
    return {"status": exchange.get("status"), "exchange_failure": call.get("exchange_failure"),
            "message_kind": kind, "meta": meta, "data": data,
            "error": error_shape(data) if kind == "result" else data,
            "wire_arguments": call.get("wire_arguments"),
            "upstream_tool": call.get("upstream_tool"), "body_bytes": exchange.get("body_bytes")}


def compare(root, folder):
    old_dir, new_dir = root / "9.1.2" / folder, root / "9.2.0" / folder
    names = sorted(path.name for path in old_dir.iterdir() if not path.name.startswith("_"))
    assert names == sorted(p.name for p in new_dir.iterdir() if not p.name.startswith("_"))
    rows = []
    for name in names:
        old, new = describe(old_dir, name), describe(new_dir, name)
        old_paths, new_paths = paths(old["data"]), paths(new["data"])
        old_values, new_values = scalars(old["data"]), scalars(new["data"])
        changed = sorted(key for key in old_values.keys() & new_values.keys()
                         if old_values[key] != new_values[key])
        rows.append({
            "id": name[:-5], "upstream_tool": old["upstream_tool"],
            "same_wire_arguments": old["wire_arguments"] == new["wire_arguments"],
            "http_status": [old["status"], new["status"]],
            "is_error": [(old["meta"] or {}).get("is_error"), (new["meta"] or {}).get("is_error")],
            "message_kind": [old["message_kind"], new["message_kind"]],
            "has_structured_content": [(old["meta"] or {}).get("has_structured_content"),
                                       (new["meta"] or {}).get("has_structured_content")],
            "text_equals_structured": [(old["meta"] or {}).get("text_equals_structured"),
                                       (new["meta"] or {}).get("text_equals_structured")],
            "body_bytes": [old["body_bytes"], new["body_bytes"]],
            "payload_identical": old["data"] == new["data"],
            "key_paths": [len(old_paths), len(new_paths)],
            "key_paths_only_in_9_1_2": sorted(old_paths - new_paths),
            "key_paths_only_in_9_2_0": sorted(new_paths - old_paths),
            "shared_scalar_paths_with_different_values": len(changed),
            "different_value_paths_sample": changed[:12],
            "error": [old["error"], new["error"]],
            "top_level_keys": [sorted(old["data"]) if isinstance(old["data"], dict) else None,
                               sorted(new["data"]) if isinstance(new["data"], dict) else None]})
    return rows


def main():
    root = Path(sys.argv[1])
    out = {"calls": compare(root, "calls"),
           "calls_after_core_restart": compare(root, "calls-after-core-restart")}
    for version in ("9.1.2", "9.2.0"):
        init = json.loads((root / version / "calls" / "_initialize.json").read_text("utf-8"))
        result = (init.get("messages") or [{}])[0].get("result") or {}
        out.setdefault("initialize", {})[version] = {
            "protocolVersion": result.get("protocolVersion"),
            "serverInfo": result.get("serverInfo"),
            "capabilities": result.get("capabilities"), "status": init.get("status")}
    json.dump(out, sys.stdout, indent=1, sort_keys=True)


main()
