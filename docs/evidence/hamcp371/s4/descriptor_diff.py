"""Mechanical comparison of two S4 capture sets (HAMCP-371).

Standard library only.  Run with ``python3 -I``; the inputs are captures taken
from upstream images and are untrusted data.  The tool compares, it does not
review: it states which descriptor fields differ and how, and judges nothing.

Usage::

    python3 -I descriptor_diff.py OLD_DIR NEW_DIR > descriptor-diff.json

Each directory holds ``capture.json`` (review_upstream_read_release.py
``capture`` output), ``observation.json`` (mcp_raw_observation.py output) and
``image-resources.json`` (the lane's resource inventory).
"""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

DESCRIPTOR_FIELDS = ("title", "description", "inputSchema", "outputSchema", "annotations", "_meta")


def load(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SystemExit(f"duplicate JSON member in {path.name}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def text_diff(old: Any, new: Any) -> list[str]:
    old_lines = old.splitlines() if isinstance(old, str) else []
    new_lines = new.splitlines() if isinstance(new, str) else []
    return list(difflib.unified_diff(old_lines, new_lines, "old", "new", lineterm="", n=0))[2:]


def schema_diff(old: Any, new: Any) -> dict[str, Any]:
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    old_properties = old.get("properties") if isinstance(old.get("properties"), dict) else {}
    new_properties = new.get("properties") if isinstance(new.get("properties"), dict) else {}
    changed = {}
    for name in sorted(set(old_properties) & set(new_properties)):
        before, after = old_properties[name], new_properties[name]
        if before == after:
            continue
        before = before if isinstance(before, dict) else {"": before}
        after = after if isinstance(after, dict) else {"": after}
        keys = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
        entry: dict[str, Any] = {"keys_changed": keys}
        if "description" in keys:
            entry["description_diff"] = text_diff(before.get("description"), after.get("description"))
        for key in keys:
            if key != "description":
                entry.setdefault("values", {})[key] = {"old": before.get(key), "new": after.get(key)}
        changed[name] = entry
    other = sorted(
        key for key in set(old) | set(new) if key != "properties" and old.get(key) != new.get(key)
    )
    return {
        "properties_added": {name: new_properties[name] for name in sorted(set(new_properties) - set(old_properties))},
        "properties_removed": sorted(set(old_properties) - set(new_properties)),
        "properties_changed": changed,
        "other_keys_changed": {key: {"old": old.get(key), "new": new.get(key)} for key in other},
    }


def tools_diff(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, Any]:
    before = {tool["name"]: tool for tool in old}
    after = {tool["name"]: tool for tool in new}
    changed: dict[str, Any] = {}
    for name in sorted(set(before) & set(after)):
        if before[name] == after[name]:
            continue
        fields = sorted(
            key for key in set(before[name]) | set(after[name])
            if before[name].get(key) != after[name].get(key)
        )
        entry: dict[str, Any] = {
            "fields_changed": fields,
            "sha256": {
                key: {"old": digest(before[name].get(key)), "new": digest(after[name].get(key))}
                for key in fields
            },
        }
        if "description" in fields:
            entry["description_diff"] = text_diff(
                before[name].get("description"), after[name].get("description")
            )
        for key in ("inputSchema", "outputSchema"):
            if key in fields:
                entry[key + "_diff"] = schema_diff(before[name].get(key), after[name].get(key))
        for key in fields:
            if key not in ("description", "inputSchema", "outputSchema"):
                entry.setdefault("values", {})[key] = {
                    "old": before[name].get(key), "new": after[name].get(key)}
        changed[name] = entry
    unknown = sorted(
        {key for tool in list(before.values()) + list(after.values()) for key in tool}
        - set(DESCRIPTOR_FIELDS) - {"name"}
    )
    return {
        "old_count": len(before),
        "new_count": len(after),
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed_count": len(changed),
        "unchanged_count": len(set(before) & set(after)) - len(changed),
        "descriptor_fields_outside_the_known_set": unknown,
        "changed": changed,
    }


def initialize_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    before = old["initialize"]["response"]["result"]
    after = new["initialize"]["response"]["result"]
    keys = sorted(set(before) | set(after))
    return {
        "result_keys": {"old": sorted(before), "new": sorted(after)},
        "keys_changed": [key for key in keys if before.get(key) != after.get(key)],
        "serverInfo": {"old": before.get("serverInfo"), "new": after.get("serverInfo")},
        "protocolVersion": {"old": before.get("protocolVersion"), "new": after.get("protocolVersion")},
        "capabilities_equal": before.get("capabilities") == after.get("capabilities"),
        "capabilities": {"old": before.get("capabilities"), "new": after.get("capabilities")},
        "instructions_sha256": {
            "old": digest(before.get("instructions")), "new": digest(after.get("instructions"))},
        "instructions_diff": text_diff(before.get("instructions"), after.get("instructions")),
        "response_header_names": {
            "old": sorted(name.lower() for name, _ in old["initialize"]["response_headers"]),
            "new": sorted(name.lower() for name, _ in new["initialize"]["response_headers"]),
        },
        "wire_order_equal": old["tool_names_in_wire_order"] == new["tool_names_in_wire_order"],
        "tools_list_pages": {"old": len(old["tools_list_pages"]), "new": len(new["tools_list_pages"])},
    }


def resources_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    before = {item["path"]: item for item in old["files"]}
    after = {item["path"]: item for item in new["files"]}

    def name(distribution: str) -> str:
        return distribution.rsplit("-", 1)[0]

    old_packages = {name(item): item for item in old["installed_distributions"]}
    new_packages = {name(item): item for item in new["installed_distributions"]}
    return {
        "tree_sha256": {"old": old["tree_sha256"], "new": new["tree_sha256"]},
        "file_count": {"old": old["file_count"], "new": new["file_count"]},
        "total_bytes": {"old": old["total_bytes"], "new": new["total_bytes"]},
        "files_added": sorted(set(after) - set(before)),
        "files_removed": sorted(set(before) - set(after)),
        "files_changed": sorted(
            path for path in set(before) & set(after) if before[path] != after[path]),
        "links": {"old": old["links"], "new": new["links"]},
        "distributions_added": sorted(
            new_packages[key] for key in set(new_packages) - set(old_packages)),
        "distributions_removed": sorted(
            old_packages[key] for key in set(old_packages) - set(new_packages)),
        "distributions_changed": {
            key: {"old": old_packages[key], "new": new_packages[key]}
            for key in sorted(set(old_packages) & set(new_packages))
            if old_packages[key] != new_packages[key]
        },
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit(__doc__)
    old_dir, new_dir = Path(argv[0]), Path(argv[1])
    old_capture, new_capture = load(old_dir / "capture.json"), load(new_dir / "capture.json")
    result = {
        "comparison": "mechanical; states differences, reviews nothing",
        "old": {key: old_capture[key] for key in (
            "server_name", "server_version", "protocol_version", "tool_count", "catalog_fingerprint")},
        "new": {key: new_capture[key] for key in (
            "server_name", "server_version", "protocol_version", "tool_count", "catalog_fingerprint")},
        "initialize": initialize_diff(
            load(old_dir / "observation.json"), load(new_dir / "observation.json")),
        "tools": tools_diff(old_capture["tools"], new_capture["tools"]),
        "error_shapes": {
            "equal": old_capture["error_shapes"] == new_capture["error_shapes"],
            "old": old_capture["error_shapes"],
            "new": new_capture["error_shapes"],
        },
        "image_resources": resources_diff(
            load(old_dir / "image-resources.json"), load(new_dir / "image-resources.json")),
    }
    sys.stdout.write(json.dumps(result, indent=1, sort_keys=True, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
