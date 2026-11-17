#!/usr/bin/env python3
"""HAMCP-371 P4 (ruling 2026-11-16 06:35 CST): offline parity from the committed SR-b launch 2 receipts.

Inputs are only the committed ``run-b-2`` files: ``receipt.json``, each setup's
``out/reads/<id>.json`` (bound to the run's ``SHA256SUMS`` and to the receipt's
``engineering_exchange_sha256``, the sha256 of the stored file), the upstream
``out/proxy/<sequence>.response`` of each read (bound to ``SHA256SUMS`` and to the
receipt's ``raw_response_sha256``), ``out/reads/_health.json`` and
``out/catalog/peer.json``. Nothing is launched; no container, network or Home
Assistant is used.

1. The 9.2.0 health record's exposed tool count. Its public projection blanks the
   tool names by design (``catalog_name_projection_bounded``) and keeps
   ``catalog_name_projection_fingerprint``. The names are recovered in-process:
   Engineering's ``UpstreamReadGateway`` at the branch head admits each setup's
   committed ``out/catalog/peer.json`` tools from a loopback peer (the
   ``tests/test_hamcp371_admission_core`` helper, as P1 does), and the
   ``schema_fingerprint`` of its name projection must equal the lane record's
   fingerprint. This step runs Engineering code on committed bytes; it is not a
   reading of the receipts alone, and is reported as such. The recovered names
   are compared with ``p1-catalog-admission.json``.
2. One row per read r01 to r26: for each setup, the result class, the public result's
   key paths, ``data.fields_withheld``, ``data.fields_dropped_by_policy`` and the
   warning codes. Key paths follow the S7 output-key contract walk
   (``s7/generate_output_key_contract.py``): no descent into the read's open
   containers or policy drops; a map container's data keys become ``*`` and only its
   row values are walked; the ``qualify`` rule of system health names each ``info``
   key as ``*.info.<domain.key>``. List items are ``[]``. ``timing`` and
   ``request_id`` values vary per call; their key paths are kept.
3. A read is flagged when a key path in the 8.5.0 public result is absent from the
   9.2.0 public result, or when 9.2.0 withholds more than 8.5.0. Each flagged read is
   matched against ``EXPLAINED``, which cites the ruling that makes the difference
   intended; any flagged path or count that is not covered is ``unexplained``. An
   entry holds only when its recorded upstream condition is true of the 9.2.0
   upstream response: the keys it names were not sent, or (rebuild reads) the
   withheld names, computed with Engineering's ``upstream_reads_9_2_platform``,
   number exactly ``data.fields_withheld``. The names the shipped rebuild leaves
   out on 8.5.0, uncounted, are recorded beside them.

``--write`` writes ``p4-parity.json`` and ``p4-parity.md``; the default compares with
the committed files. Exit 0 when every flag is explained, 1 when any is unexplained,
2 on any binding or comparison failure.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]
SR = PurePosixPath("docs/evidence/hamcp371/sr")
RUN = SR / "run-b-2"
P1 = SR / "p1-catalog-admission.json"
S7 = ROOT / "docs/evidence/hamcp371/s7/generate_output_key_contract.py"
OUTPUT_JSON = SR / "p4-parity.json"
OUTPUT_MD = SR / "p4-parity.md"
SCHEMA = "hamcp371.sr.p4_parity.v1"
SETUPS = ("8.5.0", "9.2.0")
READ_RANGE = range(1, 27)
P1_EXPOSED = 28
WITHHELD = "fields_withheld"
DROPPED = "fields_dropped_by_policy"
REPLACED = "upstream_warnings_replaced"

_spec = importlib.util.spec_from_file_location("hamcp371_s7_contract", S7)
s7 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s7)

from ha_mcp_engineering.providers import upstream_reads_9_2_platform as platform_9_2  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import schema_fingerprint  # noqa: E402

PROJECTION_FIELDS = ("held_tools", "core_withheld_tools", "live_canary_required_tools",
                     "quarantined_tools", "missing_tools", "unreviewed_tools", "exposed_tools",
                     "collision_mappings", "blocked_tools")
HAMCP319 = "docs/HAMCP_319_PHASE2_DESIGN.md"
S7_README = "docs/evidence/hamcp371/s7/README.md"


class P4Error(Exception):
    pass


# Flagged differences the rulings make intended: read id -> (paths, withheld, citation).
# ``paths`` are 8.5.0 key paths allowed to be absent on 9.2.0; ``withheld`` is True
# when a higher 9.2.0 withheld count is covered. Filled from the run, never widened
# to cover a path that the citation does not name.
# ``upstream_absent``: keys the 9.2.0 upstream response must not carry for the entry
# to hold. ``rebuild``: the withheld count must equal Engineering's withheld names.
EXPLAINED: dict[str, dict] = {
    "r08-groups": {
        "paths": ("data.groups[].all",), "upstream_absent": ("all",),
        "citation": (f"Upstream 9.2.0 does not send `all` (checked here). {HAMCP319} ledger row 8 "
                     "(`all` and `order` are no longer guaranteed; when absent they are disclosed "
                     "as unavailable, never synthesized) and [AC 4]; the E1 ruling 2026-11-15 13:25 "
                     "CST, accepted at 34abf281, keeps `unavailable_fields` recomputed by Engineering "
                     "(S7 row ha_config_list_groups).")},
    "r10-template": {
        "paths": ("data.evaluation_time", "data.request_id"),
        "upstream_absent": ("evaluation_time", "request_id"),
        "citation": (f"Upstream 9.2.0 does not send them (checked here). {HAMCP319} ledger row 10 "
                     "and F2 [AC 4]: eval-template `request_id` and `evaluation_time` are reported "
                     "unavailable and never synthesized; S7 row ha_eval_template recomputes "
                     "`unavailable_fields` (E1, 34abf281).")},
    "r18-skill-guide": {
        "paths": ("data.skill_content_hint",), "upstream_absent": ("skill_content_hint",),
        "citation": (f"Upstream 9.2.0 does not send `skill_content_hint` (checked here). R4, Josh "
                     f"decided 2026-11-06 ({HAMCP319} line 106; ledger row 18): the guide is file-only "
                     "on 9.x and nothing is synthesized; the S7 rebuild row ha_get_skill_guide copies "
                     "`skill_content_hint` only when sent and drops `how_to_use` by policy.")},
    "r20-system-health": {
        "withheld": True, "rebuild": True,
        "citation": ("D5, ruling 2026-11-15 13:25 CST, platform part accepted at 34abf281: the closed "
                     "rebuild is kept and each `info` key outside `_PLATFORM_KEYS`, and each other "
                     "upstream name outside the contract, is withheld and counted on 9.2.0 (count "
                     f"unit: distinct names; S7 notes it as an interpretation pending ruling, {S7_README}). "
                     "The same names are left out on 8.5.0 by the shipped rebuild, uncounted.")},
    "r26-integrations": {
        "withheld": True, "rebuild": True,
        "citation": ("D5 (as r20) and the S7 rebuild rule that the shipped `_ENTRY` allowlist is the "
                     f"admitted set ({S7_README}): the config-entry keys 9.2.0 adds upstream are "
                     "withheld and counted; three are left out on 8.5.0 too, uncounted.")},
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_sums(root: Path) -> dict[str, str]:
    sums = {}
    for line in (root / RUN / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(None, 1)
        sums[name.lstrip("*")] = digest
    return sums


def public_result(record: dict) -> dict:
    """The public result: the tool's single text content, parsed."""
    result = record["exchange"]["response"]["result"]
    texts = [item["text"] for item in result.get("content", []) if item.get("type") == "text"]
    if len(texts) != 1:
        raise P4Error(f"expected one text content item, found {len(texts)}")
    value = json.loads(texts[0])
    structured = result.get("structuredContent")
    if structured is not None:
        inner = structured.get("result")
        parsed = json.loads(inner) if isinstance(inner, str) else inner
        if parsed != value:
            raise P4Error("structuredContent differs from the text content")
    return value


def key_paths(value, tool: str) -> list[str]:
    """Key paths under the S7 walk rules for the read's public tool."""
    if tool in s7.READS:
        opened, maps, drops = s7.spec(tool)
        qualify = s7.READS[tool].get("qualify")
    else:
        opened, maps, drops, qualify = frozenset(), {}, frozenset(), None
    found: set[str] = set()

    def join(prefix: str, key: str) -> str:
        return f"{prefix}.{key}" if prefix else key

    def walk(v, prefix: str, outer=None):
        if isinstance(v, dict):
            for k, x in v.items():
                path = join(prefix, k)
                found.add(path)
                if k in opened or k in drops:
                    continue
                if qualify and outer == qualify[0] and k == qualify[1] and isinstance(x, dict):
                    qualified(x, path)
                elif k in maps:
                    rows(x, maps[k], path)
                else:
                    walk(x, path, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, prefix + "[]", outer)

    def qualified(v, prefix: str):
        for block in v.values():
            row = join(prefix, "*")
            if not isinstance(block, dict):
                walk(block, row)
                continue
            for k, x in block.items():
                path = join(row, k)
                found.add(path)
                if k == qualify[2] and isinstance(x, dict):
                    found.update(join(path, name) for name in x)
                else:
                    walk(x, path, k)

    def rows(v, levels: int, prefix: str):
        if levels == 0 or not isinstance(v, dict):
            walk(v, prefix)
            return
        for x in v.values():
            rows(x, levels - 1, join(prefix, "*"))

    walk(value, "")
    return sorted(found)


def warning_codes(value: dict) -> dict:
    """Envelope warnings, ``data.warnings`` and ``metadata.warning_codes`` as recorded."""
    data = value.get("data") if isinstance(value.get("data"), dict) else {}
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}

    def codes(items):
        out = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                out.append(str(item.get("code", item)))
            else:
                out.append(str(item))
        return out

    return {"envelope": codes(value.get("warnings")), "data": codes(data.get("warnings")),
            "metadata": codes(metadata.get("warning_codes"))}


def read_row(root: Path, sums: dict, setup: str, entry: dict) -> dict:
    rel = f"{setup}/out/reads/{entry['id']}.json"
    raw = (root / RUN / rel).read_bytes()
    if sums.get(rel) != _sha256(raw):
        raise P4Error(f"{rel} does not match {RUN}/SHA256SUMS")
    record = json.loads(raw)
    value = public_result(record)
    data = value.get("data") if isinstance(value.get("data"), dict) else {}
    tool = entry["tool"]
    return {
        "file": f"{RUN}/{rel}",
        "sha256": sums[rel],
        "tool": tool,
        "result_class": entry["result_class"],
        "receipt_withheld": entry.get("withheld") or {},
        "exchange_sha256_matches_receipt": sums[rel] == entry["engineering_exchange_sha256"],
        "key_paths": key_paths(value, tool),
        "fields_withheld": data.get(WITHHELD),
        "fields_dropped_by_policy": data.get(DROPPED),
        "upstream_warnings_replaced": data.get(REPLACED),
        "warning_codes": warning_codes(value),
        "content_chars": len(data["content"]) if isinstance(data.get("content"), str) else None,
    }


def sse_payload(body: str):
    """The tool payload of one upstream SSE response: structured content, else its one text."""
    data = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
    if len(data) != 1:
        raise P4Error(f"expected one SSE data line, found {len(data)}")
    result = json.loads(data[0])["result"]
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    texts = [item["text"] for item in result.get("content", []) if item.get("type") == "text"]
    if len(texts) != 1:
        raise P4Error(f"expected one upstream text content item, found {len(texts)}")
    return json.loads(texts[0])


def upstream_row(root: Path, sums: dict, setup: str, entry: dict) -> dict:
    """The read's upstream tool response, bound to SHA256SUMS and to the receipt."""
    calls = [item for item in entry["upstream"] if item.get("tool")]
    if len(calls) != 1:
        raise P4Error(f"{setup} {entry['id']}: {len(calls)} upstream tool calls")
    call = calls[0]
    rel = f"{setup}/out/proxy/{call['sequence']:06d}.response"
    raw = (root / RUN / rel).read_bytes()
    if sums.get(rel) != _sha256(raw) or call["raw_response_sha256"] != _sha256(raw):
        raise P4Error(f"{rel} does not match SHA256SUMS and the receipt")
    payload = sse_payload(raw.decode("utf-8"))
    tool = entry["tool"]
    row = {"file": f"{RUN}/{rel}", "sha256": sums[rel], "sequence": call["sequence"],
           "names": sorted(s7.names(payload, tool)),
           "content_chars": (len(payload["content"]) if isinstance(payload, dict)
                             and isinstance(payload.get("content"), str) else None)}
    if tool in platform_9_2.KNOWN:
        row["outside_rebuild_contract"] = sorted(platform_9_2.names(payload, tool)
                                                 - platform_9_2.KNOWN[tool])
    return row


def fingerprint_check(root: Path, receipt: dict, sums: dict) -> dict:
    """Recover the exposed names in-process and match the lane's name-projection fingerprint."""
    import test_hamcp371_admission_core as core  # noqa: PLC0415 - test helper, imported on use
    out = {"method": ("in-process: Engineering UpstreamReadGateway at the branch head admits the "
                      "committed out/catalog/peer.json tools from a loopback peer "
                      "(tests/test_hamcp371_admission_core._gateway); no launch; not receipts-only")}
    for setup in SETUPS:
        rel = f"{setup}/out/catalog/peer.json"
        raw = (root / RUN / rel).read_bytes()
        if sums.get(rel) != _sha256(raw):
            raise P4Error(f"{rel} does not match {RUN}/SHA256SUMS")
        pages = [sse_payload_result(page["body_text"]) for page in json.loads(raw)["tools_list_pages"]]
        tools = tuple(tool for page in pages for tool in page["tools"])
        health, exposed, _result, peer = asyncio.run(core._gateway(setup, tools))
        fingerprint = schema_fingerprint({name: health.get(name, []) for name in PROJECTION_FIELDS})
        lane = public_result(json.loads((root / RUN / f"{setup}/out/reads/_health.json").read_bytes()))
        recorded = lane["data"]["upstream_read_gateway"]["catalog_name_projection_fingerprint"]
        out[setup] = {"catalog": f"{RUN}/{rel}", "catalog_tools": len(tools),
                      "exposed_count": len(exposed), "exposed_tools": sorted(exposed),
                      "held_tools": sorted(health.get("held_tools", [])),
                      "blocked_count": len(health.get("blocked_tools", [])),
                      "collision_count": len(health.get("collision_mappings", [])),
                      "peer_tool_calls": len(peer.calls),
                      "fingerprint": fingerprint, "lane_fingerprint": recorded,
                      "fingerprint_matches_lane": fingerprint == recorded}
    return out


def sse_payload_result(body: str) -> dict:
    data = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
    if len(data) != 1:
        raise P4Error("expected one SSE data line in a tools/list page")
    return json.loads(data[0])["result"]


def health_record(root: Path, sums: dict, receipt: dict) -> dict:
    rel = "9.2.0/out/reads/_health.json"
    raw = (root / RUN / rel).read_bytes()
    if sums.get(rel) != _sha256(raw):
        raise P4Error(f"{rel} does not match {RUN}/SHA256SUMS")
    data = public_result(json.loads(raw))["data"]
    gateway = data["upstream_read_gateway"]
    p1 = json.loads((root / P1).read_text(encoding="utf-8"))["catalogs"]["9.2.0"]
    counts = {"upstream_read_gateway.dynamically_exposed_count": gateway["dynamically_exposed_count"],
              "dynamic_upstream_tool_count": data["dynamic_upstream_tool_count"],
              "delegated_tools.admitted_tool_count": data["delegated_tools"]["admitted_tool_count"]}
    recovered = fingerprint_check(root, receipt, sums)
    names_9_2 = recovered["9.2.0"]
    agree = (set(counts.values()) == {P1_EXPOSED} and p1["exposed_tool_count"] == P1_EXPOSED
             and gateway["optional_tools"] == p1["optional_tools"]
             and gateway["admission_status"] == p1["admission_status"]
             and gateway["catalog_comparison_status"] == p1["catalog_comparison_status"]
             and gateway["observed_catalog_fingerprint"] == p1["observed_catalog_fingerprint"]
             and names_9_2["fingerprint_matches_lane"]
             and names_9_2["exposed_tools"] == sorted(p1["exposed_tools"]))
    return {
        "file": f"{RUN}/{rel}",
        "sha256": sums[rel],
        "exchange_sha256_matches_receipt": (
            receipt["setups"]["9.2.0"]["health"]["engineering_exchange_sha256"] == sums[rel]),
        "exposed_counts": counts,
        "exposed_tools_in_record": gateway["exposed_tools"],
        "catalog_name_projection_bounded": gateway["catalog_name_projection_bounded"],
        "catalog_name_projection_fingerprint": gateway["catalog_name_projection_fingerprint"],
        "admission_status": gateway["admission_status"],
        "catalog_comparison_status": gateway["catalog_comparison_status"],
        "observed_catalog_fingerprint": gateway["observed_catalog_fingerprint"],
        "optional_tools": gateway["optional_tools"],
        "p1": {"file": str(P1), "exposed_tool_count": p1["exposed_tool_count"],
               "exposed_tools": p1["exposed_tools"], "held_tools": p1["held_tools"]},
        "recovered_names": recovered,
        "recovered_names_equal_p1": names_9_2["exposed_tools"] == sorted(p1["exposed_tools"]),
        "agrees_with_p1": agree,
    }


def compare(rows: dict) -> dict:
    control, candidate = rows["8.5.0"], rows["9.2.0"]
    missing = sorted(set(control["key_paths"]) - set(candidate["key_paths"]))
    added = sorted(set(candidate["key_paths"]) - set(control["key_paths"]))
    more_withheld = (candidate["fields_withheld"] or 0) > (control["fields_withheld"] or 0)
    return {"missing_on_9_2_0": missing, "added_on_9_2_0": added,
            "withholds_more_on_9_2_0": more_withheld,
            "flagged": bool(missing or more_withheld)}


def explain(read_id: str, diff: dict, rows: dict, upstream: dict | None) -> dict:
    if not diff["flagged"]:
        return {"status": "not_flagged"}
    rule = EXPLAINED.get(read_id)
    if rule is None:
        return {"status": "unexplained", "unexplained_paths": diff["missing_on_9_2_0"],
                "unexplained_withheld": diff["withholds_more_on_9_2_0"]}
    paths = [p for p in diff["missing_on_9_2_0"] if p not in rule.get("paths", ())]
    withheld = diff["withholds_more_on_9_2_0"] and not rule.get("withheld", False)
    conditions = []
    sent = set(upstream["9.2.0"]["names"]) if upstream else set()
    for key in rule.get("upstream_absent", ()):
        conditions.append({"condition": f"9.2.0 upstream does not send {key}",
                           "holds": upstream is not None and key not in sent})
    if rule.get("rebuild"):
        names = (upstream or {}).get("9.2.0", {}).get("outside_rebuild_contract")
        count = rows["9.2.0"]["fields_withheld"]
        conditions.append({"condition": "9.2.0 fields_withheld equals Engineering's withheld names",
                           "holds": names is not None and len(names) == count})
    failed = [item["condition"] for item in conditions if not item["holds"]]
    if paths or withheld or failed:
        return {"status": "unexplained", "unexplained_paths": paths,
                "unexplained_withheld": withheld, "failed_conditions": failed,
                "conditions": conditions, "citation": rule["citation"]}
    return {"status": "explained", "conditions": conditions, "citation": rule["citation"]}


def build(root: Path = ROOT) -> dict:
    sums = run_sums(root)
    receipt_raw = (root / RUN / "receipt.json").read_bytes()
    receipt = json.loads(receipt_raw)
    reads = {}
    for setup in SETUPS:
        for entry in receipt["setups"][setup]["reads"]:
            number = int(entry["id"][1:3])
            if number in READ_RANGE:
                reads.setdefault(entry["id"], {})[setup] = read_row(root, sums, setup, entry)
    if sorted(int(k[1:3]) for k in reads) != list(READ_RANGE) or any(
            set(v) != set(SETUPS) for v in reads.values()):
        raise P4Error("run-b-2 does not hold r01 to r26 for both setups")
    table = []
    entries = {setup: {entry["id"]: entry for entry in receipt["setups"][setup]["reads"]}
               for setup in SETUPS}
    for read_id in sorted(reads):
        rows = reads[read_id]
        if any(not row["exchange_sha256_matches_receipt"] for row in rows.values()):
            raise P4Error(f"{read_id}: an exchange does not match its receipt sha256")
        diff = compare(rows)
        upstream = ({setup: upstream_row(root, sums, setup, entries[setup][read_id])
                     for setup in SETUPS} if diff["flagged"] else None)
        item = {"id": read_id, "tool": rows["9.2.0"]["tool"], "setups": rows,
                "difference": diff, "explanation": explain(read_id, diff, rows, upstream)}
        if upstream is not None:
            item["upstream"] = upstream
        table.append(item)
    unexplained = [row["id"] for row in table if row["explanation"]["status"] == "unexplained"]
    return {
        "schema": SCHEMA,
        "ruling": "architect ruling 2026-11-16 06:35 CST, P4",
        "inputs": {"run": str(RUN), "receipt_sha256": _sha256(receipt_raw),
                   "sha256sums_sha256": _sha256((root / RUN / "SHA256SUMS").read_bytes()),
                   "s7_walk": str(S7.relative_to(root)),
                   "s7_walk_sha256": _sha256(S7.read_bytes())},
        "health_9_2_0": health_record(root, sums, receipt),
        "reads": table,
        "flagged": [row["id"] for row in table if row["difference"]["flagged"]],
        "unexplained": unexplained,
        "result": "all_explained" if not unexplained else "unexplained_differences",
    }


def markdown(report: dict) -> str:
    health = report["health_9_2_0"]
    lines = [
        "# HAMCP-371 P4: SR-b launch 2 parity (8.5.0 control, 9.2.0)",
        "",
        f"Generated by `{SR}/p4_parity.py` from `{RUN}` only. Result: "
        f"**{report['result']}**.",
        "",
        "## 9.2.0 health record",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    for key, value in health["exposed_counts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines += [
        f"| names in the record | none (`exposed_tools` is `{health['exposed_tools_in_record']}`, "
        f"`catalog_name_projection_bounded` {str(health['catalog_name_projection_bounded']).lower()}) |",
        f"| admission / comparison | {health['admission_status']} / {health['catalog_comparison_status']} |",
        f"| P1 re-run exposed | {health['p1']['exposed_tool_count']} |",
        f"| agrees with P1 | {str(health['agrees_with_p1']).lower()} |",
        "",
        "Names recovered in-process (Engineering's gateway on the committed "
        "`out/catalog/peer.json`, loopback peer; this step runs Engineering code and is not "
        "receipts-only):",
        "",
        "| Setup | Catalog tools | Exposed | Held | Blocked | Fingerprint matches lane | Equal to P1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for setup in SETUPS:
        item = health["recovered_names"][setup]
        lines.append(
            f"| {setup} | {item['catalog_tools']} | {item['exposed_count']} | "
            f"{', '.join(f'`{t}`' for t in item['held_tools']) or 'none'} | {item['blocked_count']} | "
            f"{str(item['fingerprint_matches_lane']).lower()} | "
            f"{str(item['exposed_tools'] == sorted(health['p1']['exposed_tools'])).lower()} |")
    lines += [
        "",
        "9.2.0 exposed names: " + ", ".join(
            f"`{t}`" for t in health["recovered_names"]["9.2.0"]["exposed_tools"]) + ".",
        "",
        "## Reads r01 to r26",
        "",
        "Key paths: count per setup; `missing` is the number of 8.5.0 paths absent on "
        "9.2.0. Withheld and drop counts are `data.fields_withheld` and "
        "`data.fields_dropped_by_policy` (`-` when absent).",
        "",
        "| Read | Class 8.5.0 / 9.2.0 | Paths 8.5.0 / 9.2.0 | Missing | Added | "
        "Withheld 8.5.0 / 9.2.0 | Drops 8.5.0 / 9.2.0 | Warnings 8.5.0 / 9.2.0 | Status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    def n(value):
        return "-" if value is None else str(value)

    def w(codes):
        flat = codes["envelope"] + codes["data"] + codes["metadata"]
        return ", ".join(f"`{c}`" for c in flat) if flat else "none"

    for row in report["reads"]:
        a, b, d = row["setups"]["8.5.0"], row["setups"]["9.2.0"], row["difference"]
        lines.append(
            f"| {row['id']} | {a['result_class']} / {b['result_class']} | "
            f"{len(a['key_paths'])} / {len(b['key_paths'])} | {len(d['missing_on_9_2_0'])} | "
            f"{len(d['added_on_9_2_0'])} | {n(a['fields_withheld'])} / {n(b['fields_withheld'])} | "
            f"{n(a['fields_dropped_by_policy'])} / {n(b['fields_dropped_by_policy'])} | "
            f"{w(a['warning_codes'])} / {w(b['warning_codes'])} | {row['explanation']['status']} |")
    lines += ["", "## Flagged reads", ""]
    flagged = [row for row in report["reads"] if row["difference"]["flagged"]]
    if not flagged:
        lines.append("None.")
    for row in flagged:
        d, e = row["difference"], row["explanation"]
        lines.append(f"### {row['id']} ({e['status']})")
        lines.append("")
        if d["missing_on_9_2_0"]:
            lines.append("Missing on 9.2.0: " + ", ".join(f"`{p}`" for p in d["missing_on_9_2_0"]))
            lines.append("")
        if d["withholds_more_on_9_2_0"]:
            a, b = row["setups"]["8.5.0"], row["setups"]["9.2.0"]
            lines.append(f"Withheld: {n(a['fields_withheld'])} on 8.5.0, {b['fields_withheld']} on 9.2.0.")
            lines.append("")
        up = row.get("upstream") or {}
        for setup in SETUPS:
            if setup in up and "outside_rebuild_contract" in up[setup]:
                names = up[setup]["outside_rebuild_contract"]
                counted = row["setups"][setup]["fields_withheld"]
                lines.append(f"Upstream names outside the rebuild contract on {setup} "
                             f"({len(names)}; counted: {n(counted)}): "
                             + ", ".join(f"`{x}`" for x in names))
                lines.append("")
        for setup in SETUPS:
            sent = (up.get(setup) or {}).get("content_chars")
            if sent is not None:
                lines.append(f"`content` on {setup}: {sent} characters upstream, "
                             f"{n(row['setups'][setup]['content_chars'])} in the public result "
                             "(a size difference is not a P4 flag criterion; disclosed).")
                lines.append("")
        for item in e.get("conditions", []):
            lines.append(f"- {item['condition']}: {'holds' if item['holds'] else 'FAILS'}")
        if e.get("conditions"):
            lines.append("")
        if "citation" in e:
            lines.append(f"Ruling: {e['citation']}")
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help=f"write {OUTPUT_JSON} and {OUTPUT_MD}")
    args = parser.parse_args(argv)
    try:
        report = build(ROOT)
    except (P4Error, KeyError, ValueError) as exc:
        print(json.dumps({"p4_error": str(exc)}, indent=1))
        return 2
    text = json.dumps(report, indent=1, sort_keys=True) + "\n"
    table = markdown(report)
    if args.write:
        (ROOT / OUTPUT_JSON).write_text(text, encoding="utf-8")
        (ROOT / OUTPUT_MD).write_text(table, encoding="utf-8")
    else:
        for path, expected in ((OUTPUT_JSON, text), (OUTPUT_MD, table)):
            target = ROOT / path
            if target.exists() and target.read_text(encoding="utf-8") != expected:
                print(json.dumps({"p4_error": f"{path} differs from a fresh build"}, indent=1))
                return 2
    print(table, end="")
    return 0 if report["result"] == "all_explained" else 1


if __name__ == "__main__":
    sys.exit(main())
