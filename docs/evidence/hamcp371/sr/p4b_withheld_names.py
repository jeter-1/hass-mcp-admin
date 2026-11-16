#!/usr/bin/env python3
"""HAMCP-371 P4-B (ruling 2026-11-16 08:18 CST): the names 9.2.0 withholds on r20 and r26.

Read-only, from the committed ``run-b-2`` receipts only. For ``r20-system-health`` (18
names) and ``r26-integrations`` (13 names) the withheld names are the P4 rebuild
names: Engineering's ``upstream_reads_9_2_platform.names`` of the 9.2.0 upstream
response minus its ``KNOWN`` set, as ``p4_parity.upstream_row`` computes them (that
response is bound to ``SHA256SUMS`` and to the receipt's ``raw_response_sha256``).
Their count must equal the 9.2.0 public result's ``data.fields_withheld``.

For each name the table records, with no value printed:

- every JSON path in the 9.2.0 upstream response where the name occurs (``[]`` for
  list items; a health name ``<domain>.<key>`` sits under ``health_info.data.<domain>``,
  at ``info.<key>`` or, for ``manage_url``-style names, beside ``info``);
- the value type or types, the number of occurrences, and the size: string length,
  object key count or list length (scalars have no size);
- whether the 8.5.0 control's public result shows the name, and whether the 9.2.0
  public result does;
- the sensitivity marks: ``could_carry`` is the reviewed judgement by name and
  meaning (credential, URL with userinfo, token, hostname, IP address, file path);
  ``observed`` is what the lane's values actually matched, by pattern, without
  printing them. The lane ran a disposable Core 2026.11.0, so ``observed`` is no
  bound on what a household install sends.

``--write`` writes ``p4b-withheld-names.json`` and ``p4b-withheld-names.md``; the
default compares with the committed files. Exit 0 on a match, 2 on any binding or
comparison failure. Needs Engineering's dependencies (the worktree venv).
"""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[4]
SR = PurePosixPath("docs/evidence/hamcp371/sr")
OUTPUT_JSON = SR / "p4b-withheld-names.json"
OUTPUT_MD = SR / "p4b-withheld-names.md"
SCHEMA = "hamcp371.sr.p4b_withheld_names.v1"
READS = {"r20-system-health": 18, "r26-integrations": 13}
CATEGORIES = ("credential", "url_userinfo", "token", "hostname", "ip_address", "file_path")

SPEC = importlib.util.spec_from_file_location("hamcp371_p4_parity", ROOT / SR / "p4_parity.py")
p4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p4)

# Reviewed judgement by name and meaning: which listed categories the value could
# carry on some install, whatever the lane showed. Every withheld name must have an
# entry; a name added upstream without one fails the build.
COULD_CARRY = {
    "r20-system-health": {
        "component_count": ((), "integer count"),
        "homeassistant.config_dir": (("file_path",), "the configuration directory path"),
        "homeassistant.container_arch": ((), "architecture label"),
        "homeassistant.timezone": ((), "IANA zone name"),
        "homeassistant.user": ((), "the OS account Core runs as; an account name, "
                               "not a listed category"),
        "lovelace.dashboards": ((), "integer count"),
        "lovelace.mode": ((), "storage or yaml"),
        "lovelace.resources": ((), "integer count"),
        "lovelace.views": ((), "integer count"),
        "manage_url": (("hostname", "url_userinfo"), "a URL; relative in Core's system "
                       "health, but an absolute URL could carry a hostname or userinfo"),
        "marketplace.disabled": ((), "status text"),
        "message": (("hostname", "ip_address", "file_path"), "free text from upstream"),
        "network.error": (("hostname", "ip_address", "url_userinfo"),
                          "an error object whose text can quote a host, address or URL"),
        "recorder.current_recorder_run": ((), "a timestamp value object"),
        "recorder.database_engine": ((), "engine name"),
        "recorder.database_version": ((), "version text"),
        "recorder.estimated_db_size": ((), "size text"),
        "recorder.oldest_recorder_run": ((), "a timestamp value object"),
    },
    "r26-integrations": {
        "created_at": ((), "epoch seconds"),
        "error_reason_translation_domain": ((), "integration domain"),
        "error_reason_translation_key": ((), "translation key"),
        "error_reason_translation_placeholders": (
            ("hostname", "ip_address", "url_userinfo", "file_path"),
            "placeholder values an integration fills from its own error"),
        "loaded": ((), "integer count in state_summary"),
        "log_level_raw": ((), "integer log level"),
        "modified_at": ((), "epoch seconds"),
        "num_subentries": ((), "integer count"),
        "reason": (("hostname", "ip_address", "url_userinfo", "file_path"),
                   "free-text setup or error reason"),
        "setup_error": ((), "integer count in state_summary"),
        "supported_subentry_types": ((), "subentry type flags"),
        "supports_remove_device": ((), "boolean"),
        "unique_id": (("hostname", "ip_address", "credential"),
                      "integration-defined; can be a host, address, serial, MAC or account id"),
    },
}

_URL_USERINFO = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s@]+@", re.I)
_HOSTNAME = re.compile(r"(?<![\w.-])(?:[a-z0-9-]+\.)+(?:local|lan|home|arpa|com|net|org|io|"
                       r"internal|[a-z]{2})(?![\w-])", re.I)
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6 = re.compile(r"(?<![0-9a-f:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-f:])", re.I)
_PATH = re.compile(r"(?:^|\s)(?:/[\w.-]+){1,}|[A-Za-z]:\\")
_TOKEN = re.compile(r"[A-Za-z0-9_\-]{32,}|eyJ[A-Za-z0-9_\-]+\.")
_CREDENTIAL_KEY = re.compile(r"pass|secret|token|key|auth|cred", re.I)


def _ip(text: str) -> bool:
    for found in (*_IPV4.findall(text), *_IPV6.findall(text)):
        try:
            ipaddress.ip_address(found)
            return True
        except ValueError:
            continue
    return False


def observed(values: list) -> list[str]:
    """Listed categories the lane values match by pattern; values are never returned."""
    texts, keys = [], []

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                keys.append(str(k))
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            texts.append(v)

    for value in values:
        walk(value)
    found = set()
    for text in texts:
        if _URL_USERINFO.search(text):
            found.add("url_userinfo")
        if _HOSTNAME.search(text):
            found.add("hostname")
        if _ip(text):
            found.add("ip_address")
        if _PATH.search(text):
            found.add("file_path")
        if _TOKEN.search(text):
            found.add("token")
    if any(_CREDENTIAL_KEY.search(key) for key in keys):
        found.add("credential")
    return [name for name in CATEGORIES if name in found]


def occurrences(payload, name: str, tool: str) -> list[tuple[str, object]]:
    """(path, value) for every place the walker names ``name``."""
    found = []
    qualify = p4.platform_9_2.QUALIFY.get(tool)

    def walk(v, path, outer=None):
        if isinstance(v, dict):
            for k, x in v.items():
                here = f"{path}.{k}"
                if k == name:
                    found.append((here, x))
                if qualify and outer == qualify[0] and k == qualify[1] and isinstance(x, dict):
                    qualified(x, here)
                else:
                    walk(x, here, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, f"{path}[]", outer)

    def qualified(v, path):
        for data_key, block in v.items():
            here = f"{path}.{data_key}"
            if not isinstance(block, dict):
                walk(block, here)
                continue
            for k, x in block.items():
                inner = f"{here}.{k}"
                if k == name:
                    found.append((inner, x))
                if k == qualify[2] and isinstance(x, dict):
                    for key, item in x.items():
                        if f"{data_key}.{key}" == name:
                            found.append((f"{inner}.{key}", item))
                else:
                    walk(x, inner, k)

    walk(payload, "$")
    return found


def _type(value) -> str:
    return {dict: "object", list: "array", str: "string", bool: "boolean", int: "integer",
            float: "number", type(None): "null"}[type(value)]


def _size(value):
    if isinstance(value, (str, list, dict)):
        return len(value)
    return None


def shown(public: dict, name: str, tool: str) -> list[str]:
    """Paths where the public result names ``name`` (its ``data`` only)."""
    data = public.get("data") or {}
    if tool == p4.platform_9_2.HEALTH and "." in name:
        domain, key = name.split(".", 1)
        platform = data.get("platform") or {}
        return [f"$.data.platform.{domain}.{key}"] if key in (platform.get(domain) or {}) else []
    return sorted({path.replace("$", "$.data", 1) for path, _ in occurrences(data, name, tool)})


def build(root: Path = ROOT) -> dict:
    report = p4.build(root)
    if report["result"] != "all_explained":
        raise p4.P4Error("P4 is not all_explained")
    sums = p4.run_sums(root)
    receipt = json.loads((root / p4.RUN / "receipt.json").read_bytes())
    entries = {setup: {entry["id"]: entry for entry in receipt["setups"][setup]["reads"]}
               for setup in p4.SETUPS}
    rows = {row["id"]: row for row in report["reads"]}
    reads = []
    for read_id, count in READS.items():
        row = rows[read_id]
        tool = row["tool"]
        upstream = p4.upstream_row(root, sums, "9.2.0", entries["9.2.0"][read_id])
        names = upstream["outside_rebuild_contract"]
        if len(names) != count or row["setups"]["9.2.0"]["fields_withheld"] != count:
            raise p4.P4Error(f"{read_id}: {len(names)} withheld names, ruled {count}")
        if sorted(COULD_CARRY[read_id]) != names:
            raise p4.P4Error(f"{read_id}: the reviewed sensitivity list does not match the names")
        payload = p4.sse_payload((root / upstream["file"]).read_text(encoding="utf-8"))
        public = {setup: p4.public_result(json.loads(
            (root / p4.RUN / setup / "out/reads" / f"{read_id}.json").read_bytes()))
            for setup in p4.SETUPS}
        table = []
        for name in names:
            found = occurrences(payload, name, tool)
            if not found:
                raise p4.P4Error(f"{read_id}: {name} has no path in the upstream response")
            values = [value for _, value in found]
            sizes = [size for size in map(_size, values) if size is not None]
            could, meaning = COULD_CARRY[read_id][name]
            table.append({
                "name": name,
                "paths": sorted({path for path, _ in found}),
                "types": sorted({_type(value) for value in values}),
                "occurrences": len(found),
                "size": None if not sizes else {"min": min(sizes), "max": max(sizes)},
                "shown_by_8_5_0_public": shown(public["8.5.0"], name, tool),
                "shown_by_9_2_0_public": shown(public["9.2.0"], name, tool),
                "could_carry": list(could),
                "observed": observed(values),
                "meaning": meaning,
            })
        reads.append({"id": read_id, "tool": tool, "withheld": count,
                      "upstream_9_2_0": {key: upstream[key] for key in ("file", "sha256",
                                                                        "sequence")},
                      "names": table})
    return {"schema": SCHEMA,
            "ruling": "architect ruling 2026-11-16 08:18 CST, P4-B",
            "inputs": {"run": str(p4.RUN), "p4": str(p4.OUTPUT_JSON)},
            "categories": list(CATEGORIES),
            "reads": reads}


def _cell(paths: list[str]) -> str:
    return "<br>".join(f"`{path}`" for path in paths) if paths else "no"


def markdown(report: dict) -> str:
    lines = ["# HAMCP-371 P4-B: the names 9.2.0 withholds on r20 and r26", "",
             "Generated by `p4b_withheld_names.py` from the committed `run-b-2` receipts "
             "(architect ruling 2026-11-16 08:18 CST, P4-B). No value is printed. Size is "
             "the string length, object key count or list length (min-max across "
             "occurrences); scalars have none. *Could carry* is the reviewed judgement by "
             "name and meaning; *observed* is what the lane's values matched by pattern "
             "against a disposable Core 2026.11.0, and is no bound on a household install.",
             ""]
    for read in report["reads"]:
        up = read["upstream_9_2_0"]
        lines += [f"## {read['id']} (`{read['tool']}`), {read['withheld']} withheld", "",
                  f"9.2.0 upstream response `{up['file']}` (sha256 `{up['sha256']}`).", "",
                  "| Name | JSON path in the 9.2.0 upstream response | Type | Occurrences | "
                  "Size | Shown by the 8.5.0 public result | Shown by the 9.2.0 public result "
                  "| Could carry | Observed | Meaning |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for item in read["names"]:
            size = item["size"]
            size = ("-" if size is None else str(size["min"]) if size["min"] == size["max"]
                    else f"{size['min']}-{size['max']}")
            lines.append(
                f"| `{item['name']}` | {_cell(item['paths'])} | {', '.join(item['types'])} | "
                f"{item['occurrences']} | {size} | {_cell(item['shown_by_8_5_0_public'])} | "
                f"{_cell(item['shown_by_9_2_0_public'])} | "
                f"{', '.join(item['could_carry']) or 'none'} | "
                f"{', '.join(item['observed']) or 'none'} | {item['meaning']} |")
        marked = [item["name"] for item in read["names"]
                  if item["could_carry"] or item["observed"]]
        lines += ["", f"Marked: {', '.join(f'`{name}`' for name in marked) or 'none'}.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help=f"write {OUTPUT_JSON} and {OUTPUT_MD}")
    args = parser.parse_args(argv)
    try:
        report = build(ROOT)
    except p4.P4Error as exc:
        print(json.dumps({"p4b_error": str(exc)}, indent=1))
        return 2
    text = json.dumps(report, indent=1, sort_keys=True) + "\n"
    md = markdown(report)
    if args.write:
        (ROOT / OUTPUT_JSON).write_text(text, encoding="utf-8")
        (ROOT / OUTPUT_MD).write_text(md, encoding="utf-8")
    elif ((ROOT / OUTPUT_JSON).read_text(encoding="utf-8") != text
          or (ROOT / OUTPUT_MD).read_text(encoding="utf-8") != md):
        print(json.dumps({"p4b_error": "the committed outputs differ from a fresh build"}))
        return 2
    print(md, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
