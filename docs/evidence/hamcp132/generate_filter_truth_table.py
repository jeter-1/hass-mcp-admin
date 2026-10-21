"""HAMCP-132 recorder entity-filter truth table from Core's own source.

Extracts ``_convert_globs_to_pattern``, ``generate_filter`` and
``_generate_filter_from_sets_and_pattern_lists`` by AST from the pinned
Home Assistant Core ``homeassistant/helpers/entityfilter.py`` (verified by
SHA-256 before anything is executed), compiles only those three functions
with ``split_entity_id`` (``homeassistant/core.py``), ``lru_cache`` and
``MAX_EXPECTED_ENTITY_IDS`` stubbed, and evaluates every fixed configuration
below for every fixed entity ID. The output is committed beside this script
and ``tests/test_hamcp132_recorder_settings.py`` checks the Engineering port
(``recorder_settings/entity_filter.py``) against it.

Configurations are already schema-validated lists (Core's
``FILTER_SCHEMA``/``INCLUDE_EXCLUDE_FILTER_SCHEMA`` output); schema coercion
is tested separately.

Usage (Core source tree at tag 2026.10.1):
    python docs/evidence/hamcp132/generate_filter_truth_table.py \
        --core <core-2026.10.1>/homeassistant/helpers/entityfilter.py \
        --output docs/evidence/hamcp132/filter-truth-table.json
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import functools
import hashlib
import json
from pathlib import Path
import re

CORE_VERSION = "2026.10.1"
CORE_COMMIT = "abe1fdd7cd6b4de1715c87b6fd87d92699514ef9"
CORE_PATH = "homeassistant/helpers/entityfilter.py"
CORE_SHA256 = "0ce66e76729c568f4c23d90166f378524cf7a6ebd67b11110ab2c6a20a1136c2"
FUNCTIONS = ("_convert_globs_to_pattern", "generate_filter", "_generate_filter_from_sets_and_pattern_lists")

ENTITIES = (
    "light.kitchen", "light.porch", "light.excluded_one",
    "sensor.temperature", "sensor.power_meter", "sensor.excluded_power", "sensor.kept_power",
    "switch.heater", "switch.pump",
    "binary_sensor.door", "automation.wake", "climate.hall",
)


def lists(domains=(), entities=(), entity_globs=()):
    return {"domains": list(domains), "entities": list(entities), "entity_globs": list(entity_globs)}


# id -> (Core branch the configuration selects, include, exclude)
CONFIGURATIONS = {
    "case1_no_filter": ("1", lists(), lists()),
    "case2_include_domain": ("2", lists(domains=["light"]), lists()),
    "case2_include_entity_and_glob": ("2", lists(entities=["switch.heater"], entity_globs=["sensor.*_power"]),
                                      lists()),
    "case3_exclude_domain": ("3", lists(), lists(domains=["light"])),
    "case3_exclude_entity_and_glob": ("3", lists(), lists(entities=["switch.pump"],
                                                          entity_globs=["sensor.*power*"])),
    "case4a_include_domain_exclude_entity": ("4a", lists(domains=["light"]),
                                             lists(entities=["light.excluded_one"])),
    "case4a_exclude_domains_ignored": ("4a", lists(domains=["light"]), lists(domains=["sensor", "light"])),
    "case4a_include_glob_exclude_glob": ("4a", lists(entity_globs=["sensor.*"]),
                                         lists(entity_globs=["sensor.excluded_*"])),
    "case4a_domain_include_glob_exclude": ("4a", lists(domains=["sensor"]),
                                           lists(entity_globs=["sensor.excluded_*"])),
    "case4a_include_entity_beats_exclude_entity": (
        "4a", lists(domains=["switch"], entities=["light.excluded_one"]),
        lists(entities=["light.excluded_one", "switch.pump"])),
    "case4b_exclude_domain_include_entity_override": ("4b", lists(entities=["sensor.kept_power"]),
                                                      lists(domains=["sensor"])),
    "case4b_exclude_glob_include_entity": ("4b", lists(entities=["sensor.excluded_power", "light.porch"]),
                                           lists(entity_globs=["sensor.excluded_*"], entities=["light.porch"])),
    "case6_include_entities_with_exclude_entities": ("6", lists(entities=["switch.heater", "light.kitchen"]),
                                                     lists(entities=["light.kitchen", "switch.pump"])),
}


def extract(source: str) -> str:
    tree = ast.parse(source)
    found = {node.name: node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS}
    if set(found) != set(FUNCTIONS):
        raise SystemExit(f"functions not found: {sorted(set(FUNCTIONS) - set(found))}")
    module = ast.Module(body=[found[name] for name in FUNCTIONS], type_ignores=[])
    return ast.unparse(module)


def split_entity_id(entity_id: str) -> tuple[str, str]:
    """``homeassistant/core.py`` ``split_entity_id`` at the pin."""

    domain, _, object_id = entity_id.partition(".")
    if not domain or not object_id:
        raise ValueError(f"Invalid entity ID {entity_id}")
    return domain, object_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    raw = arguments.core.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CORE_SHA256:
        raise SystemExit(f"{CORE_PATH} sha256 {digest} is not the pinned {CORE_SHA256}")
    code = extract(raw.decode("utf-8"))
    namespace = {
        "__builtins__": __builtins__, "annotations": None,
        "Callable": object, "fnmatch": fnmatch, "re": re, "lru_cache": functools.lru_cache,
        "MAX_EXPECTED_ENTITY_IDS": 16384, "split_entity_id": split_entity_id,
        "partial": functools.partial, "operator": __import__("operator"),
    }
    exec(compile("from __future__ import annotations\n" + code, CORE_PATH, "exec"), namespace)
    generate = namespace["generate_filter"]
    cases = []
    for case_id, (branch, include, exclude) in CONFIGURATIONS.items():
        entity_filter = generate(include["domains"], include["entities"], exclude["domains"],
                                 exclude["entities"], include["entity_globs"], exclude["entity_globs"])
        cases.append({"id": case_id, "core_branch": branch, "include": include, "exclude": exclude,
                      "recorded": {entity_id: bool(entity_filter(entity_id)) for entity_id in ENTITIES}})
    document = {
        "schema": "hamcp132-filter-truth-table-v1",
        "provenance": {
            "core_version": CORE_VERSION, "core_commit": CORE_COMMIT, "source_path": CORE_PATH,
            "source_sha256": CORE_SHA256, "functions_extracted": list(FUNCTIONS),
            "extracted_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "stubs": ["split_entity_id (homeassistant/core.py)", "lru_cache (functools)",
                      "MAX_EXPECTED_ENTITY_IDS"],
            "generator": "docs/evidence/hamcp132/generate_filter_truth_table.py",
        },
        "entities": list(ENTITIES),
        "cases": cases,
    }
    arguments.output.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
