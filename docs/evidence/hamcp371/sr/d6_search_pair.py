#!/usr/bin/env python3
"""HAMCP-371 D6 (architect rulings 2026-11-15 22:26 CST): derive the 9.2.0 ha_search pair.

Every binding of ``upstream_reads_9_2``'s search pair comes from committed files,
computed with Engineering's own fingerprint functions; nothing is hand-entered.

- ``legacy`` is the reviewed 9.2.0 descriptor in the registry capture. Its
  values must reproduce the registry row: its tool contract, catalog
  fingerprint and strict full-contract fingerprint.
- ``unified`` is the ha_search descriptor ha-mcp 9.2.0 served in S6 profile B
  (``run-b-9.2.0-2``, receipt sha256 ``c707f5f7…``), where the component
  reported ``search``, ``device_registry_child_semantics`` and
  ``search_unified``. It must differ from legacy only in ``inputSchema``
  (``config_time_budget``, ``include_config`` and ``search_types`` removed, the
  ``query`` description replaced) and ``description``
  (``src/ha_mcp/transforms/component_search.py`` at 51e3aac7).
- A variant's catalog-level fingerprints are those of the reviewed 77-tool
  catalog, in the reviewed runtime wire order, with ha_search replaced by that
  variant's descriptor. For legacy this is the reviewed catalog itself, which
  checks the method. The receipt served 80 tools; its own catalog-level
  fingerprints are recorded as observations only.

Usage: ``d6_search_pair.py`` prints the JSON; ``--check FILE`` exits 1 unless
FILE equals it byte for byte.
"""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.providers import upstream_reads_9_2 as reads  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    catalog_fingerprint,
    load_reviewed_upstream_release_registry,
    reviewed_tool_contracts_from_capture,
    schema_fingerprint,
)

VERSION = "9.2.0"
MODEL = "ha-mcp-9.2.0-search-descriptor-pair-v1"
EVIDENCE = ROOT / "docs/evidence/upstream-read-compatibility"
REVIEW = EVIDENCE / "ha-mcp-9.2.0-contract-review.json"
RECEIPT = (ROOT / "docs/evidence/hamcp371/s6/run-b-9.2.0-2/out/catalog/peer-9.2.0-1.json",
           "c707f5f78003f446e66fce096561eea18c4fbfe9b8f8df5a16ca883b4c45fee4")
LEGACY_PARAMETERS = {"config_time_budget", "include_config", "search_types"}
CONTRACT_KEYS = ("input_schema_fingerprint", "description_fingerprint",
                 "annotation_fingerprint", "output_contract_fingerprint",
                 "runtime_contract_fingerprint", "runtime_contract_field_fingerprints")


def fail(reason):
    raise SystemExit(f"d6_search_pair: {reason}")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path):
    return path.relative_to(ROOT).as_posix()


def sse_result(body_text):
    data = [line[5:].strip() for line in body_text.splitlines() if line.startswith("data:")]
    if len(data) != 1:
        fail("expected one SSE data line")
    return json.loads(data[0])["result"]


def receipt_tools():
    path, digest = RECEIPT
    if sha256(path) != digest:
        fail("receipt sha256 mismatch")
    record = json.loads(path.read_bytes())
    info = sse_result(record["initialize"]["body_text"])
    if info["serverInfo"] != {"name": "ha-mcp", "version": VERSION}:
        fail("receipt serverInfo is not ha-mcp 9.2.0")
    pages = [sse_result(page["body_text"]) for page in record["tools_list_pages"]]
    if len(pages) != 1 or pages[0].get("nextCursor"):
        fail("receipt has more than one tools/list page")
    tools = pages[0]["tools"]
    if [t["name"] for t in tools] != record["tool_names_in_wire_order"]:
        fail("receipt tools differ from the recorded wire order")
    return tools, info["protocolVersion"]


def contract_values(contract):
    value = {key: getattr(contract, key) for key in CONTRACT_KEYS}
    value["runtime_contract_field_fingerprints"] = dict(
        value["runtime_contract_field_fingerprints"])
    return value


def computed(contract):
    value = {key: contract[key] for key in CONTRACT_KEYS}
    value["runtime_contract_field_fingerprints"] = dict(
        value["runtime_contract_field_fingerprints"])
    return value


def derive():
    release = load_reviewed_upstream_release_registry().by_version[VERSION]
    capture_path = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering" / release.capture_resource
    if not capture_path.is_file():
        capture_path = EVIDENCE / Path(release.capture_resource).name
    if "sha256:" + sha256(capture_path) != release.capture_sha256 and (
            sha256(capture_path) != release.capture_sha256):
        fail("reviewed capture sha256 differs from the registry row")
    capture = json.loads(capture_path.read_bytes())
    review = json.loads(REVIEW.read_bytes())
    if "sha256:" + sha256(REVIEW) != release.artifact_evidence_sha256 and (
            sha256(REVIEW) != release.artifact_evidence_sha256):
        fail("contract review sha256 differs from the registry row")
    order = review["runtime_catalog"]["runtime_tool_order"]
    by_name = {tool["name"]: tool for tool in capture["tools"]}
    if sorted(order) != sorted(by_name) or len(order) != release.advertised_tool_count:
        fail("reviewed runtime order does not name the reviewed catalog")

    served, protocol = receipt_tools()
    if (protocol,) != release.allowed_protocol_versions:
        fail("receipt protocol is not the reviewed protocol")
    served_search = [tool for tool in served if tool["name"] == "ha_search"]
    if len(served_search) != 1:
        fail("receipt does not serve exactly one ha_search")
    descriptors = {"legacy": by_name["ha_search"], "unified": served_search[0]}

    legacy, unified = descriptors["legacy"], descriptors["unified"]
    if set(legacy) != set(unified) or any(
            legacy[key] != unified[key] for key in legacy
            if key not in {"inputSchema", "description"}):
        fail("unified differs from legacy outside inputSchema and description")
    trimmed = deepcopy(legacy["inputSchema"])
    for name in LEGACY_PARAMETERS:
        trimmed["properties"].pop(name)
    trimmed["properties"]["query"]["description"] = (
        unified["inputSchema"]["properties"]["query"]["description"])
    if unified["inputSchema"] != trimmed or unified["description"] == legacy["description"]:
        fail("unified inputSchema is not the legacy schema with the three parameters removed")

    reviewed = release.tool_contracts_by_name
    variants = {}
    for name, descriptor in descriptors.items():
        tools = [descriptor if tool == "ha_search" else by_name[tool] for tool in order]
        contracts = reviewed_tool_contracts_from_capture(
            {"tools": tools}, release.policy,
            runtime_contract_fingerprint_model=release.runtime_contract_fingerprint_model)
        if any(computed(contracts[tool]) != contract_values(reviewed[tool])
               for tool in contracts if tool != "ha_search"):
            fail(f"{name}: a sibling contract differs from the registry")
        search = contracts["ha_search"]
        if schema_fingerprint(descriptor) != search["runtime_contract_fingerprint"]:
            fail(f"{name}: raw descriptor fingerprint differs from the runtime model fingerprint")
        variants[name] = {
            "descriptor": descriptor,
            "descriptor_sha256": schema_fingerprint(descriptor),
            **computed(search),
            "catalog_fingerprint": catalog_fingerprint(tools),
            "strict_full_contract_fingerprint": schema_fingerprint({"tools": tools}),
        }

    legacy_values = variants["legacy"]
    if ({key: legacy_values[key] for key in CONTRACT_KEYS}
            != contract_values(reviewed["ha_search"])):
        fail("legacy does not reproduce the registry ha_search contract")
    if (legacy_values["catalog_fingerprint"] != release.catalog_fingerprint
            or legacy_values["catalog_fingerprint"] != capture["catalog_fingerprint"]
            or legacy_values["strict_full_contract_fingerprint"]
            != release.strict_full_contract_fingerprint):
        fail("legacy does not reproduce the registry catalog fingerprints")
    for key in ("annotation_fingerprint", "output_contract_fingerprint",
                "runtime_contract_field_fingerprints"):
        if variants["unified"][key] != legacy_values[key]:
            fail(f"unified {key} differs from legacy")

    entry = release.policy.by_name["ha_search"]
    if entry.input_schema_fingerprint != legacy_values["input_schema_fingerprint"] or (
            reads.INPUT_FINGERPRINTS["ha_search"] != legacy_values["input_schema_fingerprint"]):
        fail("compiled ha_search entry does not carry the legacy input fingerprint")
    public_schema = schema_fingerprint(reads.PUBLIC_SCHEMAS["ha_search"])
    pair = {
        "descriptors": {name: value["runtime_contract_fingerprint"]
                        for name, value in variants.items()},
        "model": MODEL, "protocol": protocol, "public_schema_fingerprint": public_schema,
        "source": release.source_commit, "version": VERSION,
    }
    return {
        "check": "HAMCP-371 D6 ha-mcp 9.2.0 ha_search descriptor pair",
        "ruling": "architect rulings 2026-11-15 22:26 CST (R-D6-1 to R-D6-3, pair requirements)",
        "inputs": {
            "reviewed_capture": {"path": rel(capture_path), "sha256": sha256(capture_path)},
            "contract_review": {"path": rel(REVIEW), "sha256": sha256(REVIEW)},
            "profile_b_receipt": {"path": rel(RECEIPT[0]), "sha256": RECEIPT[1]},
        },
        "release": {
            "version": release.version, "source_commit": release.source_commit,
            "allowed_protocol_versions": list(release.allowed_protocol_versions),
            "policy_resource": release.policy_resource, "policy_sha256": release.policy_sha256,
            "runtime_contract_fingerprint_model": release.runtime_contract_fingerprint_model,
        },
        "descriptor_pair": pair,
        "descriptor_pair_sha256": schema_fingerprint(pair),
        "entry_fingerprint": schema_fingerprint(asdict(entry)),
        "reference_contract_fingerprint": schema_fingerprint(asdict(reviewed["ha_search"])),
        "public_schema_fingerprint": public_schema,
        "variants": variants,
        "receipt_observation": {
            "tool_count": len(served),
            "catalog_fingerprint": catalog_fingerprint(served),
            "strict_full_contract_fingerprint": schema_fingerprint({"tools": served}),
            "note": "diagnostic only; the receipt also served ha_config_get_yaml, "
                    "ha_list_files and ha_read_file, which are not reviewed",
        },
    }


def render():
    return json.dumps(derive(), indent=1, sort_keys=True) + "\n"


def main(argv):
    text = render()
    if argv[:1] == ["--check"] and len(argv) == 2:
        return 0 if Path(argv[1]).read_text(encoding="utf-8") == text else 1
    if argv:
        fail("usage: d6_search_pair.py [--check FILE]")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
