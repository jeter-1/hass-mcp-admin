#!/usr/bin/env python3
"""Regenerate the checked-in template semantic registry byte-for-byte.

Generation never verifies a declared provenance tuple against another copy of
the same declaration.  Every referenced source is checked against an
independent witness:

* Jinja path/blob pairs are recomputed as git blob SHA-1 values from the
  installed pinned ``Jinja2`` distribution, and the filter/test vocabulary is
  derived from that same installed package rather than hand-listed.
* Home Assistant path/blob pairs are checked against the immutable captured
  evidence in ``docs/evidence/home-assistant-template-source-blobs.json``,
  which records bytes served by the official repository together with the
  locally recomputed blob SHA-1 of those bytes.

A referenced path that does not exist at a supported tag, or a path attributed
to the wrong blob, fails generation.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version as package_version
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "template_semantic_registry_source.json"
HOME_ASSISTANT_EVIDENCE = (
    ROOT / "docs" / "evidence" / "home-assistant-template-source-blobs.json"
)
HOME_ASSISTANT_EVIDENCE_MODEL = "home-assistant-template-source-evidence-v1"
REGISTRY_MODEL = "home-assistant-template-semantic-registry-v1"
SEMANTIC_REGISTRY_CATEGORIES = frozenset(
    {
        "attribute_item_access",
        "dependency_neutral",
        "dynamic_filter_test_dispatch",
        "entity_set_producer",
        "provenance_preserving",
        "state_entity_access",
    }
)
REQUIRED_GLOBALS = frozenset(
    {
        "states",
        "state_attr",
        "state_translated",
        "state_attr_translated",
        "is_state",
        "is_state_attr",
        "has_value",
        "expand",
        "closest",
        "distance",
        "area_entities",
        "device_entities",
        "floor_entities",
        "integration_entities",
        "label_entities",
    }
)
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _git_blob_sha1(data: bytes) -> str:
    header = ("blob %d" % len(data)).encode("ascii") + b"\x00"
    return hashlib.sha1(header + data).hexdigest()


def _load_home_assistant_evidence() -> dict[str, dict[str, Any]]:
    """Return captured official-source evidence keyed by release tag."""

    raw = HOME_ASSISTANT_EVIDENCE.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("model") != (
        HOME_ASSISTANT_EVIDENCE_MODEL
    ):
        raise ValueError("Home Assistant source evidence model is invalid")
    versions = value.get("versions")
    if not isinstance(versions, list) or not versions:
        raise ValueError("Home Assistant source evidence is empty")
    captured: dict[str, dict[str, Any]] = {}
    for item in versions:
        if not isinstance(item, dict):
            raise ValueError("Home Assistant source evidence entry is invalid")
        tag = item.get("tag")
        commit = item.get("commit")
        records = item.get("paths")
        absent = item.get("absent_paths", [])
        if (
            not isinstance(tag, str)
            or not _SHA1.fullmatch(str(commit))
            or not isinstance(records, dict)
            or not isinstance(absent, list)
        ):
            raise ValueError("Home Assistant source evidence entry is invalid")
        for path, record in records.items():
            if (
                not isinstance(path, str)
                or not isinstance(record, dict)
                or not _SHA1.fullmatch(str(record.get("blob")))
                or not _SHA256.fullmatch(str(record.get("content_sha256")))
                or not isinstance(record.get("size"), int)
                or record["size"] < 0
            ):
                raise ValueError(
                    "Home Assistant source evidence record is invalid"
                )
        captured[tag] = {
            "commit": commit,
            "paths": records,
            "absent_paths": set(absent),
        }
    return captured


def _verify_home_assistant_sources(value: dict[str, Any]) -> None:
    captured = _load_home_assistant_evidence()
    home_assistant = value.get("home_assistant")
    if not isinstance(home_assistant, dict):
        raise ValueError("Home Assistant provenance is missing")
    versions = home_assistant.get("supported_versions")
    if not isinstance(versions, list) or not versions:
        raise ValueError("Home Assistant source versions are missing")
    declared_paths = value["source_provenance"]["home_assistant_paths"]
    if not isinstance(declared_paths, list) or not declared_paths:
        raise ValueError("Home Assistant source paths are missing")
    if len(set(declared_paths)) != len(declared_paths):
        raise ValueError("Home Assistant source paths are duplicated")
    for item in versions:
        if not isinstance(item, dict):
            raise ValueError("Home Assistant source version is invalid")
        tag = str(item.get("tag"))
        witness = captured.get(tag)
        if witness is None:
            raise ValueError(
                "no captured source evidence for Home Assistant tag " + tag
            )
        if item.get("commit") != witness["commit"]:
            raise ValueError(
                "declared commit for " + tag + " contradicts captured evidence"
            )
        blobs = item.get("source_blobs")
        if not isinstance(blobs, dict) or set(blobs) != set(declared_paths):
            raise ValueError(
                "declared source blobs for " + tag + " do not cover every path"
            )
        for path, blob in sorted(blobs.items()):
            if path in witness["absent_paths"]:
                raise ValueError(
                    path + " does not exist at Home Assistant tag " + tag
                )
            record = witness["paths"].get(path)
            if record is None:
                raise ValueError(
                    "no captured evidence for "
                    + path
                    + " at Home Assistant tag "
                    + tag
                )
            if blob != record["blob"]:
                raise ValueError(
                    "declared blob for "
                    + path
                    + " at "
                    + tag
                    + " contradicts captured evidence"
                )
        # A blob identifies exact content, so two distinct paths sharing one
        # blob at the same tag is the signature of a copied attribution.
        seen: dict[str, str] = {}
        for path, blob in sorted(blobs.items()):
            if blob in seen:
                raise ValueError(
                    path + " and " + seen[blob] + " share one blob at " + tag
                )
            seen[blob] = path


def _verify_jinja_sources(value: dict[str, Any]) -> None:
    """Recompute declared Jinja blobs from the installed pinned package."""

    import jinja2

    jinja = value.get("jinja")
    if not isinstance(jinja, dict):
        raise ValueError("Jinja parser provenance is missing")
    declared_version = str(jinja.get("version"))
    installed = package_version("Jinja2")
    if declared_version != installed:
        raise ValueError(
            "declared Jinja "
            + declared_version
            + " is not the installed "
            + installed
        )
    if not _SHA1.fullmatch(str(jinja.get("commit"))) or not _SHA1.fullmatch(
        str(jinja.get("tag_object"))
    ):
        raise ValueError("Jinja parser provenance is invalid")
    package = Path(jinja2.__file__).resolve().parent
    blobs = jinja.get("source_blobs")
    if not isinstance(blobs, dict) or not blobs:
        raise ValueError("Jinja source blobs are missing")
    for path, blob in sorted(blobs.items()):
        if not path.startswith("src/jinja2/") or not _SHA1.fullmatch(str(blob)):
            raise ValueError(
                "Jinja source blob declaration " + path + " is invalid"
            )
        module = package / path.split("/")[-1]
        if not module.is_file():
            raise ValueError(path + " does not exist in the installed package")
        if _git_blob_sha1(module.read_bytes()) != blob:
            raise ValueError(
                "declared blob for "
                + path
                + " contradicts the installed package"
            )


def _derive_jinja_vocabulary(
    value: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    """Expand reviewed per-function categories over every bound Jinja name.

    Jinja binds several names to one implementation: ``d`` and ``default``,
    ``e`` and ``escape``, ``count`` and ``length``, and the comparison test
    aliases.  Deriving names from the pinned tables keeps every alias
    classified by construction instead of depending on a hand-kept list.
    """

    from jinja2.defaults import DEFAULT_FILTERS, DEFAULT_TESTS

    semantics = value["semantics"]
    derived: list[dict[str, str]] = []
    for table, declaration_key, extra_key in (
        (DEFAULT_FILTERS, "jinja_filter_functions", "home_assistant_filters"),
        (DEFAULT_TESTS, "jinja_test_functions", "home_assistant_tests"),
    ):
        declaration = semantics.get(declaration_key)
        extra = semantics.get(extra_key)
        if not isinstance(declaration, dict) or not declaration:
            raise ValueError(declaration_key + " declaration is missing")
        if not isinstance(extra, dict):
            raise ValueError(extra_key + " declaration is missing")
        observed = {
            getattr(function, "__name__", repr(function))
            for function in table.values()
        }
        missing = observed.difference(declaration)
        if missing:
            raise ValueError(
                declaration_key + " does not classify " + repr(sorted(missing))
            )
        unused = set(declaration).difference(observed)
        if unused:
            raise ValueError(
                declaration_key + " classifies unknown " + repr(sorted(unused))
            )
        expanded = {
            name: declaration[getattr(function, "__name__", repr(function))]
            for name, function in table.items()
        }
        collisions = set(expanded).intersection(extra)
        if collisions:
            raise ValueError(
                extra_key + " redeclares standard " + repr(sorted(collisions))
            )
        expanded.update(extra)
        if any(
            category not in SEMANTIC_REGISTRY_CATEGORIES
            for category in expanded.values()
        ):
            raise ValueError(declaration_key + " category is invalid")
        derived.append(dict(sorted(expanded.items())))
    return derived[0], derived[1]


def _load_reviewed_registry() -> dict[str, Any]:
    raw = SOURCE.read_bytes()
    value = json.loads(raw)
    if value.get("model") != REGISTRY_MODEL:
        raise ValueError("semantic registry model mismatch")
    provenance = value.get("source_provenance")
    if not isinstance(provenance, dict) or provenance.get(
        "registry_generator"
    ) != "scripts/generate_template_semantic_registry.py":
        raise ValueError("registry generation provenance is invalid")
    semantics = value.get("semantics")
    if not isinstance(semantics, dict):
        raise ValueError("semantic vocabulary is missing")
    if "filters" in semantics or "tests" in semantics:
        raise ValueError(
            "filters and tests are derived and must not be declared"
        )
    globals_ = semantics.get("globals")
    if not isinstance(globals_, dict) or not REQUIRED_GLOBALS.issubset(
        globals_
    ):
        raise ValueError("semantic vocabulary is incomplete")
    _verify_home_assistant_sources(value)
    _verify_jinja_sources(value)
    filters, tests = _derive_jinja_vocabulary(value)
    semantics["filters"] = filters
    semantics["tests"] = tests
    return value


def canonical_bytes() -> bytes:
    value = _load_reviewed_registry()
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--print-sha256",
        action="store_true",
        help="print the deterministic generated registry digest",
    )
    args = parser.parse_args()
    content = canonical_bytes()
    args.output.write_bytes(content)
    if args.print_sha256:
        print(hashlib.sha256(content).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
