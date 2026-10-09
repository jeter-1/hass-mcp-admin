"""Generate synthetic wire records using exact, hash-verified Core getters.

Offline only. Extracts named pure getters into synthetic namespaces; does not
import or start Core. No household records or persisted governance formats.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
import tarfile
import textwrap
from types import SimpleNamespace
from typing import Any

COMMIT = "6a811d3359c7b2076dc9e1cf900843a129c044af"
ARCHIVE_SHA256 = "96778e8c130c16e86619e82741d308e41c76d2963da649f0748f7b51fb9703b9"
REGISTRY_PATH = "homeassistant/helpers/device_registry.py"
NAMING_PATH = "homeassistant/helpers/registry.py"


def _member(archive, name):
    matches = [m for m in archive.getmembers()
               if m.isfile() and m.name.split("/", 1)[-1] == name]
    if len(matches) != 1 or matches[0].size > 1_000_000:
        raise ValueError("Invalid pinned member")
    return archive.extractfile(matches[0]).read()


def _getter(source, class_name, name, namespace):
    lines = source.splitlines(keepends=True)
    start_class = next(i for i, s in enumerate(lines)
                       if s.startswith("class " + class_name + "("))
    end_class = next((i for i in range(start_class + 1, len(lines))
                     if lines[i].startswith("class ")), len(lines))
    start = next(i for i in range(start_class + 1, end_class)
                 if lines[i].startswith("    def " + name + "("))
    end = start + 1
    while end < end_class and (not lines[end].strip() or lines[end].startswith("        ")):
        end += 1
    raw = "".join(lines[start:end])
    node = ast.parse(textwrap.dedent(raw))
    if len(node.body) != 1 or not isinstance(node.body[0], ast.FunctionDef):
        raise ValueError("Invalid pinned getter")
    env = dict(namespace)
    exec(compile(node, REGISTRY_PATH + ":" + class_name, "exec"), env)
    return env[name], {"first_line": start + 1, "last_line": end,
                       "verbatim_sha256": hashlib.sha256(raw.encode()).hexdigest()}


def generate(archive_path: Path):
    if archive_path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("Archive exceeds bound")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("Exact Core archive required")
    with tarfile.open(archive_path) as archive:
        raw = _member(archive, REGISTRY_PATH)
        naming = _member(archive, NAMING_PATH)
    source = raw.decode()
    enum = re.search(r"^class NextNamePart\(StrEnum\):\n(?:[ \t].*\n|\n)+",
                     naming.decode(), re.MULTILINE)
    if enum is None:
        raise ValueError("Pinned naming enum missing")
    env = {"StrEnum": StrEnum}
    exec(compile(enum.group(), NAMING_PATH, "exec"), env)
    namespace = {"Any": Any, "NextNamePart": env["NextNamePart"]}
    provenance = {"version": "2026.10.0", "source_commit": COMMIT,
                  "archive_sha256": ARCHIVE_SHA256,
                  "source_url": "https://github.com/home-assistant/core/tree/" + COMMIT,
                  "source_license": "Apache-2.0",
                  "member_sha256": {REGISTRY_PATH: hashlib.sha256(raw).hexdigest(),
                                    NAMING_PATH: hashlib.sha256(naming).hexdigest()},
                  "getters": {}, "full_core_execution": False,
                  "method": "Verbatim pure getters; synthetic namespace inputs; JSON wire round-trip",
                  "generator": "scripts/generate_core_2026_10_device_fixture.py"}
    getters = {}
    for cls in ("DeviceEntry", "ChildDeviceEntry"):
        getters[cls] = {}
        for name in ("dict_repr", "next_name_part"):
            fn, proof = _getter(source, cls, name, namespace)
            getters[cls][name] = fn
            provenance["getters"][cls + "." + name] = proof
    devices = []
    for device_id, area, parent in (
        ("parent-area", "synthetic-area", None),
        ("parent-none", None, None),
        ("child-own", "synthetic-other-area", "parent-none"),
        ("child-inherit", None, "parent-area"),
        ("child-none", None, "parent-none"),
    ):
        cls = "DeviceEntry" if parent is None else "ChildDeviceEntry"
        dt = datetime(2026, 9, 1, tzinfo=timezone.utc)
        data = dict(id=device_id, area_id=area, parent_device_id=parent,
                    config_entry_id="synthetic-config", config_subentry_id=None,
                    _config_entries={"synthetic-config"},
                    _config_entries_subentries={"synthetic-config": {None}},
                    identifiers={("synthetic", device_id)}, labels=set(),
                    connections=set(), created_at=dt, modified_at=dt)
        for key in ("configuration_url", "disabled_by", "entry_type", "hw_version",
                    "manufacturer", "model", "model_id", "name_by_user", "name",
                    "serial_number", "sw_version", "via_device_id"):
            data[key] = None
        obj = SimpleNamespace(**data)
        obj.next_name_part = getters[cls]["next_name_part"](obj)
        devices.append(json.loads(json.dumps(getters[cls]["dict_repr"](obj))))
    fixture = {"devices": devices, "entities": [
        {"entity_id": "sensor.synthetic_inherited", "device_id": "child-inherit", "area_id": None},
        {"entity_id": "sensor.synthetic_own", "device_id": "child-own", "area_id": None},
        {"entity_id": "sensor.synthetic_override", "device_id": "child-inherit", "area_id": "synthetic-override"},
        {"entity_id": "sensor.synthetic_unassigned", "device_id": "child-none", "area_id": None},
    ]}
    raw_fixture = (json.dumps(fixture, indent=2, sort_keys=True) + "\n").encode()
    provenance["records_sha256"] = hashlib.sha256(raw_fixture).hexdigest()
    return raw_fixture, (json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    fixture, provenance = generate(args.archive)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "records.json").write_bytes(fixture)
    (args.output_dir / "provenance.json").write_bytes(provenance)


if __name__ == "__main__":
    main()
