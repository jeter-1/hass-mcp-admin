#!/usr/bin/env python3
"""HAMCP-371 P1 (ruling 2026-11-15 20:17 CST): Engineering's startup catalog admission, offline.

Each saved D1-flag tools/list (S6 profile B) is served verbatim, in wire order and
as one page as it was captured, by the test suite's loopback peer
(tests/support/ha_mcp_redirect_peer.py). Engineering's UpstreamReadGateway then
runs its own ``initialize`` against it, the startup path, with the built-in
reviewed release registry of this checkout (signed registry off). No Home
Assistant, no network beyond 127.0.0.1, and every secret is synthetic.

P1 re-run gate (architect rulings 2026-11-15 22:26 CST, after D6): 9.2.0 is
admitted_exact with 28 exposed, which are the 27 Engineering exposes on the
reviewed 8.5.0 catalog (ha_search among them) plus ha_config_get_yaml;
ha_list_files and ha_read_file are withheld and ha_get_operation_status is held.

Prints one JSON document; exit 0 when the 9.2.0 gate passes, else 1.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

import test_hamcp319_no_redirect_useful as useful  # noqa: E402
import test_hamcp371_admission_core as core  # noqa: E402

S6 = ROOT / "docs/evidence/hamcp371/s6"
CATALOGS = (
    ("9.2.0", S6 / "run-b-9.2.0-2/out/catalog/peer-9.2.0-1.json",
     "c707f5f78003f446e66fce096561eea18c4fbfe9b8f8df5a16ca883b4c45fee4"),
    ("9.1.2", S6 / "run-b-9.1.2-1/out/catalog/peer-9.1.2-1.json",
     "2b824bbc90c1508bd006d795fdd3faef0af9b8b7cef0f13b628a771ae4024479"),
)
FIELDS = (
    "admission_status", "version_status", "upstream_advertised_tool_count",
    "reviewed_policy_entry_count", "reviewed_accounted_tool_count",
    "reviewed_stock_catalog_tool_count", "catalog_comparison_status",
    "upstream_server_name", "upstream_server_version", "reviewed_upstream_version",
    "protocol_version", "observed_catalog_fingerprint", "reviewed_catalog_fingerprint",
    "catalog_diff_field_counts", "dashboard_attestation_status",
    "exposed_tools", "held_tools", "optional_tools", "quarantined_tools",
    "unreviewed_tools", "readmission_withheld_tools", "core_withheld_tools",
)
WITHHELD = ("ha_list_files", "ha_read_file")
HELD = ["ha_get_operation_status"]


def sse_result(body_text):
    data = [line[5:].strip() for line in body_text.splitlines() if line.startswith("data:")]
    if len(data) != 1:
        raise SystemExit("expected one SSE data line")
    return json.loads(data[0])["result"]


def saved(version, path, digest):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SystemExit(f"{path.name}: sha256 mismatch")
    record = json.loads(raw)
    info = sse_result(record["initialize"]["body_text"])
    if info["serverInfo"] != {"name": "ha-mcp", "version": version}:
        raise SystemExit(f"{path.name}: serverInfo is not ha-mcp {version}")
    if info["protocolVersion"] != record["negotiated_protocol_version"]:
        raise SystemExit(f"{path.name}: protocol mismatch")
    pages = [sse_result(page["body_text"]) for page in record["tools_list_pages"]]
    if len(pages) != 1 or pages[0].get("nextCursor"):
        raise SystemExit(f"{path.name}: expected one tools/list page")
    tools = tuple(pages[0]["tools"])
    if [t["name"] for t in tools] != record["tool_names_in_wire_order"] or (
            len(tools) != record["tool_count"]):
        raise SystemExit(f"{path.name}: tools differ from the recorded wire order")
    return tools, info["protocolVersion"]


def main():
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                          check=True, text=True).stdout.strip()
    registry = core.REGISTRY_FILE.read_bytes()
    result = {"check": "HAMCP-371 P1 offline startup catalog admission", "head": head,
              "registry": {"path": core.REGISTRY_FILE.relative_to(ROOT).as_posix(),
                           "sha256": hashlib.sha256(registry).hexdigest()},
              "signed_release_registry": "off (built-in reviewed registry)", "catalogs": {}}
    for version, path, digest in CATALOGS:
        tools, protocol = saved(version, path, digest)
        health, exposed, _result, peer = asyncio.run(core._gateway(version, tools))
        row = {"source": path.relative_to(ROOT).as_posix(), "sha256": digest,
               "served_tools": len(tools), "served_protocol": protocol,
               "peer_tool_calls": len(peer.calls), "exposed_tool_count": len(exposed)}
        row.update({field: health.get(field) for field in FIELDS})
        result["catalogs"][version] = row
    _health, reference, _result, _peer = asyncio.run(
        core._gateway("8.5.0", useful.reviewed_catalog("8.5.0")))
    result["reference_8_5_0_exposed"] = sorted(reference)
    row = result["catalogs"]["9.2.0"]
    exposed = set(row["exposed_tools"])
    checks = {
        "admitted_exact": row["admission_status"] == "admitted_exact",
        "exposed_28": row["exposed_tool_count"] == len(exposed) == 28,
        "exposed_is_reference_27_plus_ha_config_get_yaml": (
            len(reference) == 27 and "ha_search" in reference
            and exposed == set(reference) | {"ha_config_get_yaml"}),
        "ha_list_files_and_ha_read_file_withheld": all(
            row["optional_tools"].get(name) == "prohibited_withheld" and name not in exposed
            for name in WITHHELD),
        "ha_get_operation_status_held": row["held_tools"] == HELD and not set(HELD) & exposed,
    }
    result["p1_gate"] = checks
    passed = all(checks.values())
    result["p2"] = "9.2.0 P1 gate passed: SR-b may start" if passed else (
        "ADMISSION BLOCKER: the 9.2.0 P1 gate failed under the D1 flags")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
