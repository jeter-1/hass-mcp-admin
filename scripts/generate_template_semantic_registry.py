#!/usr/bin/env python3
"""Regenerate the checked-in template semantic registry byte-for-byte."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "template_semantic_registry_source.json"
EXPECTED_SOURCE_SHA256 = (
    "dbe59676e34f83a232ef5d893f686d6315717ffba14975ff25d60c74d1705708"
)
EXPECTED_SOURCES = (
    (
        "2026.7.2",
        "f9122fb28dd30d3833b3b313924befbc82157f97",
        "sha256:1476924357b46e80735c13e94232ba5c853cac052e9df4bb28d50fa56348097b",
    ),
    (
        "2026.8.0",
        "4a9dce13f61d03960ad5d2710e2af9fd2a78af54",
        "sha256:a21689ef0510df9760ee11bab4d6b2fef3ed5c1a29ed9c3224271597a23729eb",
    ),
    (
        "2026.8.1",
        "53998d7710b4ac280658511c24a2a3e2651f9873",
        "sha256:6340a3de3917a9b19368e767310a96dd090f6a19aca8aeadf87fd1145cec9682",
    ),
)
EXPECTED_JINJA = (
    "3.1.6",
    "15206881c006c79667fe5154fe80c01c65410679",
    "2d4ce43010630478ee88b463f731389fa18953f4",
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


def _load_reviewed_registry() -> dict[str, object]:
    raw = SOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_SOURCE_SHA256:
        raise ValueError("reviewed semantic-registry declaration changed")
    value = json.loads(raw)
    if value.get("model") != "home-assistant-template-semantic-registry-v1":
        raise ValueError("semantic registry model mismatch")
    home_assistant = value.get("home_assistant")
    if not isinstance(home_assistant, dict):
        raise ValueError("Home Assistant provenance is missing")
    sources = home_assistant.get("supported_versions")
    if not isinstance(sources, list):
        raise ValueError("Home Assistant source versions are missing")
    observed = tuple(
        (
            item.get("tag"),
            item.get("commit"),
            item.get("exact_ci_image_digest"),
        )
        for item in sources
        if isinstance(item, dict)
    )
    if observed != EXPECTED_SOURCES:
        raise ValueError("Home Assistant source versions do not match CI")
    jinja = value.get("jinja")
    if not isinstance(jinja, dict) or (
        jinja.get("version"), jinja.get("commit"), jinja.get("tag_object")
    ) != EXPECTED_JINJA:
        raise ValueError("Jinja parser provenance is invalid")
    for item in (*sources, jinja):
        blobs = item.get("source_blobs")
        if not isinstance(blobs, dict) or not blobs or not all(
            isinstance(path, str)
            and path
            and isinstance(blob, str)
            and _SHA1.fullmatch(blob)
            for path, blob in blobs.items()
        ):
            raise ValueError("reviewed source blob provenance is invalid")
    semantics = value.get("semantics")
    if not isinstance(semantics, dict):
        raise ValueError("semantic vocabulary is missing")
    globals_ = semantics.get("globals")
    if not isinstance(globals_, dict) or not REQUIRED_GLOBALS.issubset(globals_):
        raise ValueError("semantic vocabulary is incomplete")
    provenance = value.get("source_provenance")
    if not isinstance(provenance, dict) or provenance.get(
        "registry_generator"
    ) != "scripts/generate_template_semantic_registry.py":
        raise ValueError("registry generation provenance is invalid")
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
