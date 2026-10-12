"""Derive every compatibility CI matrix from ``ci/support-window.yaml``.

The support window names current, candidate and rollback slots for Core and
ha-mcp. Pull-request runs cover current + candidate; every other event (nightly
schedule, workflow_dispatch, and the reusable release/publication gate) also
covers rollback. Nothing here reads or changes admission, registry or HOLD state:
the reviewed ha-mcp registry is only cross-checked so a window pin can never
select an image the registry does not already describe.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent
WINDOW = "ci/support-window.yaml"
SLOTS = ("current", "candidate", "rollback")
TIERS = {"pr": ("current", "candidate"), "full": SLOTS}
# Alarmo acceptance follows these Core slots (HAMCP-159): current on every run,
# rollback in the full tier. The candidate is evaluated without Alarmo evidence.
ALARMO_SLOTS = ("current", "rollback")
RUNNERS = {"amd64": "ubuntu-latest", "arm64": "ubuntu-24.04-arm"}
CORE_IMAGE = "ghcr.io/home-assistant/home-assistant"
HA_MCP_IMAGE = "ghcr.io/homeassistant-ai/ha-mcp"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,2}\.[0-9]{1,3}")
CORE_KEYS = {"version", "image_index_digest"}
HA_MCP_KEYS = {"version", "image_index_digest", "source_commit", "addon_runtime"}
POWER_KEYS = {"kind", "profile", "architectures"}
LIVE_KEYS = {"kind", "addon_detail_profile", "secret_path", "addon_amd64_index_digest",
             "addon_amd64_manifest_digest", "addon_arm64_index_digest", "addon_arm64_manifest_digest"}


class Refusal(ValueError):
    pass


def require(condition, category):
    if not condition:
        raise Refusal(category)


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    keys = [loader.construct_object(key, deep=deep) for key, _ in node.value]
    require(len(keys) == len(set(keys)), "duplicate_key")
    return loader.construct_mapping(node, deep=deep)


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def acceptance_codes():
    """Reuse the add-on acceptance script's own pairing authority."""

    spec = importlib.util.spec_from_file_location("ha_mcp_850_container_acceptance",
                                                  ROOT / "scripts/ha_mcp_850_container_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load(root=ROOT):
    path = Path(root, WINDOW)
    require(path.is_file() and not path.is_symlink(), "window_unavailable")
    window = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    require(isinstance(window, dict) and set(window) == {"schema_version", "core", "ha_mcp"}
            and window["schema_version"] == 1, "window_schema")
    for component, keys in (("core", CORE_KEYS), ("ha_mcp", HA_MCP_KEYS)):
        slots = window[component]
        require(isinstance(slots, dict) and set(slots) == set(SLOTS), component + "_slots")
        versions = []
        for slot in SLOTS:
            entry = slots[slot]
            require(isinstance(entry, dict) and keys <= set(entry) <= keys | {"source_archive_sha256"},
                    component + "_" + slot + "_keys")
            require(isinstance(entry["version"], str) and VERSION.fullmatch(entry["version"]),
                    component + "_" + slot + "_version")
            require(isinstance(entry["image_index_digest"], str) and DIGEST.fullmatch(entry["image_index_digest"]),
                    component + "_" + slot + "_digest")
            versions.append(entry["version"])
            if component == "ha_mcp":
                require(isinstance(entry["source_commit"], str) and COMMIT.fullmatch(entry["source_commit"]),
                        "ha_mcp_" + slot + "_source")
                archive = entry.get("source_archive_sha256")
                require(archive is None or isinstance(archive, str) and re.fullmatch(r"[0-9a-f]{64}", archive),
                        "ha_mcp_" + slot + "_archive")
                lanes = entry["addon_runtime"]
                require(isinstance(lanes, list) and lanes, "ha_mcp_" + slot + "_addon_runtime")
                for lane in lanes:
                    require(isinstance(lane, dict) and lane.get("kind") in {"candidate_power", "live_detail"},
                            "addon_runtime_kind")
                    if lane["kind"] == "candidate_power":
                        require(set(lane) == POWER_KEYS and isinstance(lane["profile"], str)
                                and isinstance(lane["architectures"], list) and lane["architectures"]
                                and len(set(lane["architectures"])) == len(lane["architectures"])
                                and set(lane["architectures"]) <= set(RUNNERS), "addon_power_lane")
                    else:
                        require(set(lane) == LIVE_KEYS and all(
                            DIGEST.fullmatch(str(lane[k])) for k in LIVE_KEYS if k.endswith("_digest"))
                            and re.fullmatch(r"live-" + re.escape(entry["version"]), str(lane["addon_detail_profile"]))
                            and re.fullmatch(r"/[a-z0-9-]{1,64}/mcp", str(lane["secret_path"])), "addon_live_lane")
        require(len(set(versions)) == len(versions), component + "_slot_versions_not_distinct")
    require("source_archive_sha256" in window["ha_mcp"]["current"], "ha_mcp_current_archive_required")
    for slot in SLOTS:
        core = window["core"][slot]
        fixture = Path(root, "tests/fixtures", "core_" + core["version"].replace(".", "_") + "_lane_provenance.json")
        require(fixture.is_file(), "core_" + slot + "_lane_provenance_missing")
        entry = json.loads(fixture.read_text(encoding="utf-8"))
        require(entry.get("version") == core["version"]
                and entry.get("image_index_digest") == core["image_index_digest"], "core_" + slot + "_provenance_mismatch")
    return window


def tier_for(event):
    """Pull requests run the PR tier; every other event fails toward the full window."""

    require(isinstance(event, str) and re.fullmatch(r"[a-z_]{1,64}", event), "event_invalid")
    return "pr" if event == "pull_request" else "full"


def core_lane_provenance(window, slot, root=ROOT):
    """The slot's committed lane provenance; ``load`` already bound it to the pin."""

    version = window["core"][slot]["version"]
    path = Path(root, "tests/fixtures", "core_" + version.replace(".", "_") + "_lane_provenance.json")
    entry = json.loads(path.read_text(encoding="utf-8"))
    require(entry.get("version") == version
            and entry.get("image_index_digest") == window["core"][slot]["image_index_digest"]
            and isinstance(entry.get("source_commit"), str) and COMMIT.fullmatch(entry["source_commit"])
            and isinstance(entry.get("source_archive_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", entry["source_archive_sha256"]),
            "core_" + slot + "_provenance_mismatch")
    return entry


def alarmo_slot(version, root=ROOT):
    """Return the exact Alarmo-bearing Core slot for ``version``, or refuse.

    Exact string match against the window; no prefix, family or wildcard.
    """

    window = load(root)
    for slot in ALARMO_SLOTS:
        core = window["core"][slot]
        if version == core["version"]:
            entry = core_lane_provenance(window, slot, root)
            return {"slot": slot, "version": version,
                    "lane": "ha-" + version.replace(".", "-"),
                    "image": f"{CORE_IMAGE}:{version}@{core['image_index_digest']}",
                    "image_index_digest": core["image_index_digest"],
                    "source_commit": entry["source_commit"],
                    "source_archive_sha256": entry["source_archive_sha256"],
                    "provenance": entry}
    raise Refusal("alarmo_core_not_in_window_slot")


def real_ha_matrix(window, tier, root=ROOT):
    upstream = window["ha_mcp"]["current"]
    rows = []
    for slot in TIERS[tier]:
        core = window["core"][slot]
        provenance = core_lane_provenance(window, slot, root)
        rows.append({
            "lane": "ha-" + core["version"].replace(".", "-"),
            "support_slot": slot,
            "ha_version": core["version"],
            "ha_image": f"{CORE_IMAGE}:{core['version']}@{core['image_index_digest']}",
            "ha_mcp_version": upstream["version"],
            "ha_mcp_image": f"{HA_MCP_IMAGE}:{upstream['version']}@{upstream['image_index_digest']}",
            "ha_mcp_source_commit": upstream["source_commit"],
            "ha_mcp_source_sha256": upstream["source_archive_sha256"],
            "core_source_commit": provenance["source_commit"],
            "core_source_sha256": provenance["source_archive_sha256"],
            "alarmo": slot in ALARMO_SLOTS,
        })
    return {"include": rows}


def gateway_matrix(window, tier, registry_matrix):
    require(isinstance(registry_matrix, dict) and isinstance(registry_matrix.get("include"), list),
            "registry_matrix_malformed")
    by_version = {}
    for row in registry_matrix["include"]:
        require(isinstance(row, dict) and row.get("upstream_version") not in by_version, "registry_matrix_duplicate")
        by_version[row["upstream_version"]] = row
    rows = []
    for slot in TIERS[tier]:
        pin = window["ha_mcp"][slot]
        row = by_version.get(pin["version"])
        require(row is not None, "ha_mcp_" + slot + "_not_in_reviewed_registry")
        require(row.get("image_index_digest") == pin["image_index_digest"]
                and row.get("source_commit") == pin["source_commit"], "ha_mcp_" + slot + "_registry_mismatch")
        rows.append({**row, "support_slot": slot})
    return {"include": rows}


def addon_matrix(window, tier, codes=None):
    codes = codes or acceptance_codes()
    core = window["core"]["current"]["version"]
    rows = []
    for slot in TIERS[tier]:
        pin = window["ha_mcp"][slot]
        for lane in pin["addon_runtime"]:
            if lane["kind"] == "live_detail":
                rows.append({"upstream_version": pin["version"], "support_slot": slot,
                             **{k: v for k, v in lane.items() if k != "kind"}})
                continue
            try:
                code = codes.version_code(pin["version"])
                resource = code + codes.core_code(core, pin["version"]) + codes.profile_code(pin["version"], lane["profile"])
            except codes.Refusal as exc:
                raise Refusal("addon_" + slot + "_pair_unsupported") from exc
            for architecture in lane["architectures"]:
                rows.append({
                    "upstream_version": pin["version"],
                    "support_slot": slot,
                    "architecture": architecture,
                    "runner": RUNNERS[architecture],
                    "candidate_power": True,
                    "candidate_code": code,
                    "candidate_core_version": core,
                    "candidate_profile": lane["profile"],
                    "candidate_resource_code": resource,
                    "candidate_source": pin["source_commit"],
                })
    return {"include": rows}


def write_outputs(path, values):
    with Path(path).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            text = json.dumps(value, sort_keys=True, separators=(",", ":")) if not isinstance(value, str) else value
            require("\n" not in text, "output_newline")
            stream.write(f"{key}={text}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=sorted(TIERS))
    parser.add_argument("--event", help="derive the tier from this event (default GITHUB_EVENT_NAME)")
    parser.add_argument("--registry-matrix", type=Path, help="reviewed-registry ci-matrix JSON file")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        window = load()
        tier = args.tier or tier_for(args.event or os.environ.get("GITHUB_EVENT_NAME"))
        values = {"tier": tier, "real_ha": real_ha_matrix(window, tier), "addon": addon_matrix(window, tier)}
        if args.registry_matrix:
            values["gateway"] = gateway_matrix(window, tier, json.loads(args.registry_matrix.read_text(encoding="utf-8")))
        if args.github_output:
            write_outputs(args.github_output, values)
        print(json.dumps(values, sort_keys=True, indent=1))
    except (Refusal, OSError, ValueError, yaml.YAMLError) as exc:
        category = str(exc) if isinstance(exc, Refusal) else "window_unreadable"
        parser.exit(1, "Support window refused: " + category + "\n")


if __name__ == "__main__":
    main()
