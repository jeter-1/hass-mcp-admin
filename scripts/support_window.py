"""Derive every compatibility CI matrix from ``ci/support-window.yaml``.

The support window names current, candidate and rollback slots for Core and
ha-mcp. Pull-request runs cover current + candidate; every other event (nightly
schedule, workflow_dispatch, and the reusable release/publication gate) also
covers rollback. Nothing here reads or changes admission, registry or HOLD state:
the reviewed ha-mcp registry is only cross-checked so a window pin can never
select an image the registry does not already describe.

Every Core slot must match its exact entry in the one reviewed binding list,
``scripts/core_lane_bindings.py``; a slot without one is refused before any
matrix is generated, so no row advertises coverage the executable path would
not run (contract amendment, Josh 2026-10-11). The window never supplies a
binding: rotating listed Cores edits only the window, and a brand-new Core is
first added to that list as one deliberate review step.

The candidate and rollback slots of either component may be empty
(``candidate: null``, ``rollback: null``); current is always required. Josh
2026-11-19, HAMCP-406 (D1: restore-first recovery), amends HAMCP-173, which
allowed only an empty candidate: recovery restores the pre-update state rather
than running a rollback lane. An empty slot creates and requires nothing (no
lane, job, provenance or registry cross-check), and the distinct-version rule
applies to the non-empty slots only. An empty slot is an explicit ``null``; a
missing slot key still refuses.

HAMCP-437 (release 2.9.0, I2b lane ruling A): the legacy-URL exact-image read
gateway job and the add-on runtime job are retired with their 8.x scripts. The
``addon_runtime`` key left the ha-mcp slot schema and is refused if present.
``strict_full_matrix`` derives the exact-image strict-full lane (the section 5.1
``91_only`` configuration) from ha_mcp.current, on amd64 and arm64: it is the
exact-image read-gateway lane for the deployed ha-mcp, always active, and its
peer pins come only from the reviewed registry row that matches the current pin.
Every non-empty ha-mcp slot in the tier is still cross-checked against that
registry.

HAMCP-319 (design section 5.1; Josh 2026-11-07): the optional top-level
``admission`` key names an ha-mcp admission candidate that is deliberately NOT a
slot (HAMCP-424 keeps it for a future candidate). Its baseline must be
ha_mcp.current and its candidate must not be in a slot. ``admission_matrix``
derives the candidate's rows, ``dormant`` (visibly not executed) until the
reviewed registry carries the candidate version; without the key it derives no
row. Since HAMCP-437 no CI job consumes these rows: running a future candidate
needs its own reviewed lane (HAMCP-424).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import core_lane_bindings  # noqa: E402  (the one reviewed Core binding list)
WINDOW = "ci/support-window.yaml"
SLOTS = ("current", "candidate", "rollback")
TIERS = {"pr": ("current", "candidate"), "full": SLOTS}
# Alarmo acceptance (and the automation-baseline proof bound to it) follows every
# non-empty Core slot in the tier (HAMCP-159): current and candidate on pull
# requests, plus rollback in the full tier. Each slot is its own matrix job, and
# ``load`` has already refused any slot without a reviewed Core binding, so every
# advertised slot reaches the real-HA child contract that runs Alarmo.
ALARMO_SLOTS = SLOTS
# The Core candidate and rollback may each be explicitly empty (``null``): no
# Core lane, Alarmo job or provenance is then created or required for that slot.
# Josh 2026-11-19, HAMCP-406 (D1: restore-first recovery) added rollback.
OPTIONAL_CORE_SLOTS = frozenset({"candidate", "rollback"})
# Likewise the ha-mcp candidate (owner decision, Josh 2026-10-13, HAMCP-173) and
# rollback (Josh 2026-11-19, HAMCP-406, D1: restore-first recovery, amending
# HAMCP-173) may be empty: no registry cross-check is then made or required for
# that slot. Current is never optional.
OPTIONAL_HA_MCP_SLOTS = frozenset({"candidate", "rollback"})
OPTIONAL_SLOTS = {"core": OPTIONAL_CORE_SLOTS, "ha_mcp": OPTIONAL_HA_MCP_SLOTS}
RUNNERS = {"amd64": "ubuntu-24.04", "arm64": "ubuntu-24.04-arm"}
CORE_IMAGE = "ghcr.io/home-assistant/home-assistant"
HA_MCP_IMAGE = "ghcr.io/homeassistant-ai/ha-mcp"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,2}\.[0-9]{1,3}")
CORE_KEYS = {"version", "image_index_digest"}
HA_MCP_KEYS = {"version", "image_index_digest", "source_commit"}
# HAMCP-437 lane ruling A: the add-on runtime lanes are retired; a slot that
# still declares them refuses instead of being silently ignored.
RETIRED_HA_MCP_KEYS = frozenset({"addon_runtime"})
ADMISSION_KEYS = {"baseline_version", "candidate_version"}
# HAMCP-319 section 5.1: each configuration needs an exact-image lane on both
# architectures. The order is the matrix order and the manifest gate's contract.
# HAMCP-437 (release 2.9.0) keeps only 91_only; 85_only and side_by_side (with
# the beta.7 in-place rollback, HAMCP-405) are retired.
ADMISSION_CONFIGURATIONS = ("91_only",)
ADMISSION_ARCHITECTURES = ("amd64", "arm64")
# Which peers each configuration launches. "candidate" is the lane runners'
# name for the strict-selector peer role; on the strict-full lane that peer is
# ha_mcp.current.
ADMISSION_PEERS = {"91_only": ("candidate",)}


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


def _root(root):
    """Resolve the repository root at call time (tests may point ROOT elsewhere)."""

    return ROOT if root is None else root


def load(root=None):
    root = _root(root)
    path = Path(root, WINDOW)
    require(path.is_file() and not path.is_symlink(), "window_unavailable")
    window = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    require(isinstance(window, dict) and {"schema_version", "core", "ha_mcp"} <= set(window)
            <= {"schema_version", "core", "ha_mcp", "admission"}
            and window["schema_version"] == 1, "window_schema")
    for component, keys in (("core", CORE_KEYS), ("ha_mcp", HA_MCP_KEYS)):
        slots = window[component]
        require(isinstance(slots, dict) and set(slots) == set(SLOTS), component + "_slots")
        versions = []
        for slot in SLOTS:
            entry = slots[slot]
            if entry is None and slot in OPTIONAL_SLOTS[component]:
                continue
            if component == "ha_mcp" and isinstance(entry, dict):
                require(not RETIRED_HA_MCP_KEYS & set(entry), "ha_mcp_" + slot + "_addon_runtime_retired")
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
        require(len(set(versions)) == len(versions), component + "_slot_versions_not_distinct")
    require("source_archive_sha256" in window["ha_mcp"]["current"], "ha_mcp_current_archive_required")
    if "admission" in window:
        admission = window["admission"]
        require(isinstance(admission, dict) and set(admission) == ADMISSION_KEYS
                and all(isinstance(admission[k], str) and VERSION.fullmatch(admission[k]) for k in ADMISSION_KEYS),
                "admission_keys")
        slot_versions = {window["ha_mcp"][slot]["version"] for slot in ha_mcp_slots(window, SLOTS)}
        # The baseline peer is the deployed ha-mcp, never a second copy of a pin.
        require(admission["baseline_version"] == window["ha_mcp"]["current"]["version"],
                "admission_baseline_not_current")
        # The candidate is never also a slot pin: a slot pin is a deployed or
        # rollback-era version, and the candidate is under evaluation only.
        require(admission["candidate_version"] not in slot_versions, "admission_candidate_in_slot")
    for slot in core_slots(window, SLOTS):
        core = window["core"][slot]
        # The window never vouches for itself: every Core it names must already
        # have an exact lane binding in the reviewed list, or nothing (no matrix,
        # no Alarmo or baseline coverage) is generated for any slot.
        binding = core_lane_bindings.reviewed_lane_binding(core["version"])
        require(binding is not None, "core_" + slot + "_binding_unreviewed")
        require(binding.image_index_digest == core["image_index_digest"], "core_" + slot + "_binding_mismatch")
        fixture = Path(root, "tests/fixtures", core_lane_bindings.provenance_name(core["version"]))
        require(fixture.is_file(), "core_" + slot + "_lane_provenance_missing")
        entry = json.loads(fixture.read_text(encoding="utf-8"))
        require(entry.get("version") == core["version"]
                and entry.get("image_index_digest") == core["image_index_digest"], "core_" + slot + "_provenance_mismatch")
    return window


def core_slots(window, slots):
    """The non-empty Core slots among ``slots``, in order."""

    return tuple(slot for slot in slots if window["core"][slot] is not None)


def ha_mcp_slots(window, slots):
    """The non-empty ha-mcp slots among ``slots``, in order."""

    return tuple(slot for slot in slots if window["ha_mcp"][slot] is not None)


def tier_for(event):
    """Pull requests run the PR tier; every other event fails toward the full window."""

    require(isinstance(event, str) and re.fullmatch(r"[a-z_]{1,64}", event), "event_invalid")
    return "pr" if event == "pull_request" else "full"


def core_lane_provenance(window, slot, root=None):
    """The slot's committed lane provenance; ``load`` already bound it to the pin."""

    version = window["core"][slot]["version"]
    path = Path(_root(root), "tests/fixtures", core_lane_bindings.provenance_name(version))
    entry = json.loads(path.read_text(encoding="utf-8"))
    require(entry.get("version") == version
            and entry.get("image_index_digest") == window["core"][slot]["image_index_digest"]
            and isinstance(entry.get("source_commit"), str) and COMMIT.fullmatch(entry["source_commit"])
            and isinstance(entry.get("source_archive_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", entry["source_archive_sha256"]),
            "core_" + slot + "_provenance_mismatch")
    return entry


def alarmo_slot(version, root=None):
    """Return the exact Alarmo-bearing Core slot for ``version``, or refuse.

    Exact string match against the window; no prefix, family or wildcard.
    """

    window = load(root)
    for slot in core_slots(window, ALARMO_SLOTS):
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


def real_ha_matrix(window, tier, root=None):
    upstream = window["ha_mcp"]["current"]
    rows = []
    for slot in core_slots(window, TIERS[tier]):
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


def _registry_by_version(registry_matrix):
    require(isinstance(registry_matrix, dict) and isinstance(registry_matrix.get("include"), list),
            "registry_matrix_malformed")
    by_version = {}
    for row in registry_matrix["include"]:
        require(isinstance(row, dict) and row.get("upstream_version") not in by_version, "registry_matrix_duplicate")
        by_version[row["upstream_version"]] = row
    return by_version


def registry_rows(window, tier, registry_matrix):
    """The reviewed registry row of every non-empty ha-mcp slot in the tier, or refuse."""

    by_version = _registry_by_version(registry_matrix)
    rows = {}
    for slot in ha_mcp_slots(window, TIERS[tier]):
        pin = window["ha_mcp"][slot]
        row = by_version.get(pin["version"])
        require(row is not None, "ha_mcp_" + slot + "_not_in_reviewed_registry")
        require(row.get("image_index_digest") == pin["image_index_digest"]
                and row.get("source_commit") == pin["source_commit"], "ha_mcp_" + slot + "_registry_mismatch")
        rows[slot] = row
    return rows


def _peer_row_ok(row):
    digests = row.get("architecture_image_digests")
    return (isinstance(row.get("image_index_digest"), str) and DIGEST.fullmatch(row["image_index_digest"])
            and isinstance(row.get("source_commit"), str) and COMMIT.fullmatch(row["source_commit"])
            and isinstance(digests, dict)
            and all(isinstance(digests.get("linux/" + arch), str) and DIGEST.fullmatch(digests["linux/" + arch])
                    for arch in ADMISSION_ARCHITECTURES))


def strict_full_matrix(window, tier, registry_matrix):
    """The exact-image strict-full lane (section 5.1 ``91_only``) for ha_mcp.current.

    One active row per architecture. The peer is the reviewed registry row that
    matches the current pin; every other non-empty slot in the tier is
    cross-checked too, so no pin can name an image the registry does not
    describe.
    """

    current = registry_rows(window, tier, registry_matrix)["current"]
    require(_peer_row_ok(current), "ha_mcp_current_registry_malformed")
    rows = []
    for configuration in ADMISSION_CONFIGURATIONS:
        for architecture in ADMISSION_ARCHITECTURES:
            rows.append({
                "configuration": configuration,
                "architecture": architecture,
                "runner": RUNNERS[architecture],
                "support_slot": "current",
                "state": "active",
                "dormant_reason": "",
                "peers": {role: current for role in ADMISSION_PEERS[configuration]},
            })
    return {"include": rows}


def admission_matrix(window, registry_matrix):
    """The HAMCP-319 section 5.1 exact-image lanes: every configuration x architecture.

    Without an ``admission`` key no row is emitted. Each row carries its native
    runner, its state and the reviewed registry rows of the peers it launches.
    ``active`` needs every peer in the reviewed registry. A configuration that
    needs the candidate stays ``dormant`` while the registry lacks it; a dormant
    row carries a reason and no peer pins, so it can never run a peer or claim a
    result. A registry candidate row that is malformed or misbound refuses
    instead of silently staying dormant.
    """

    by_version = _registry_by_version(registry_matrix)
    admission = window.get("admission")
    if admission is None:
        return {"include": []}
    current = window["ha_mcp"]["current"]
    baseline = by_version.get(admission["baseline_version"])
    require(baseline is not None, "admission_baseline_not_in_reviewed_registry")
    require(baseline.get("image_index_digest") == current["image_index_digest"]
            and baseline.get("source_commit") == current["source_commit"], "admission_baseline_registry_mismatch")
    candidate = by_version.get(admission["candidate_version"])
    if candidate is not None:
        require(_peer_row_ok(candidate), "admission_candidate_registry_malformed")
    peers = {"baseline": baseline, "candidate": candidate}
    rows = []
    for configuration in ADMISSION_CONFIGURATIONS:
        needed = ADMISSION_PEERS[configuration]
        missing = [role for role in needed if peers[role] is None]
        for architecture in ADMISSION_ARCHITECTURES:
            row = {
                "configuration": configuration,
                "architecture": architecture,
                "runner": RUNNERS[architecture],
                "baseline_version": admission["baseline_version"],
                "candidate_version": admission["candidate_version"],
            }
            if missing:
                row.update(state="dormant", peers={}, dormant_reason=(
                    "not executed: the reviewed upstream registry has no ha-mcp "
                    + admission["candidate_version"]
                    + " entry yet (HAMCP-319 build admission signal absent)"))
            else:
                row.update(state="active", dormant_reason="",
                           peers={role: peers[role] for role in needed})
            rows.append(row)
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
        values = {"tier": tier, "real_ha": real_ha_matrix(window, tier)}
        if args.registry_matrix:
            registry = json.loads(args.registry_matrix.read_text(encoding="utf-8"))
            values["strict_full"] = strict_full_matrix(window, tier, registry)
            values["admission"] = admission_matrix(window, registry)
        if args.github_output:
            write_outputs(args.github_output, values)
        print(json.dumps(values, sort_keys=True, indent=1))
    except (Refusal, OSError, ValueError, yaml.YAMLError) as exc:
        category = str(exc) if isinstance(exc, Refusal) else "window_unreadable"
        parser.exit(1, "Support window refused: " + category + "\n")


if __name__ == "__main__":
    main()
