"""Deterministic offline Home Assistant template semantic registry."""

from __future__ import annotations

from functools import lru_cache
import hashlib
from importlib.metadata import version as package_version
import json
from pathlib import Path
import re
from typing import Any


SEMANTIC_REGISTRY_FILE = Path(__file__).with_name(
    "template_semantic_registry.json"
)
SEMANTIC_REGISTRY_MODEL = "home-assistant-template-semantic-registry-v1"
EXPECTED_SEMANTIC_REGISTRY_SHA256 = (
    "e0976d69feaac262dcc3090787a152f34265644dfe19ab7018c9c78d9d4be2bc"
)
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
SUPPORTED_HOME_ASSISTANT_TEMPLATE_SOURCES = (
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
_REQUIRED_STATE_GLOBALS = frozenset(
    {
        "closest",
        "distance",
        "expand",
        "has_value",
        "is_state",
        "is_state_attr",
        "state_attr",
        "state_attr_translated",
        "state_translated",
        "states",
    }
)
_REQUIRED_ENTITY_SET_GLOBALS = frozenset(
    {
        "area_entities",
        "device_entities",
        "floor_entities",
        "integration_entities",
        "label_entities",
    }
)
_SHA1 = re.compile(r"^[0-9a-f]{40}$")


def _validate_registry(value: dict[str, Any], raw: bytes) -> None:
    if hashlib.sha256(raw).hexdigest() != EXPECTED_SEMANTIC_REGISTRY_SHA256:
        raise RuntimeError("template semantic registry digest mismatch")
    if value.get("model") != SEMANTIC_REGISTRY_MODEL:
        raise RuntimeError("template semantic registry model mismatch")
    jinja = value.get("jinja", {})
    if jinja.get("version") != "3.1.6" or package_version("Jinja2") != "3.1.6":
        raise RuntimeError("template semantic registry Jinja version mismatch")
    if jinja.get("commit") != "15206881c006c79667fe5154fe80c01c65410679":
        raise RuntimeError("template semantic registry Jinja commit mismatch")
    if jinja.get("tag_object") != "2d4ce43010630478ee88b463f731389fa18953f4":
        raise RuntimeError("template semantic registry Jinja tag mismatch")
    supported = value.get("home_assistant", {}).get("supported_versions", [])
    actual_sources = tuple(
        (
            item.get("tag"),
            item.get("commit"),
            item.get("exact_ci_image_digest"),
        )
        for item in supported
    )
    if actual_sources != SUPPORTED_HOME_ASSISTANT_TEMPLATE_SOURCES:
        raise RuntimeError("template semantic registry Home Assistant sources mismatch")
    for item in (*supported, jinja):
        blobs = item.get("source_blobs", {})
        if not blobs or not all(
            isinstance(path, str)
            and path
            and isinstance(blob, str)
            and _SHA1.fullmatch(blob)
            for path, blob in blobs.items()
        ):
            raise RuntimeError("template semantic registry source provenance is invalid")
    environment = value.get("home_assistant", {}).get("parser_environment", {})
    if environment != {
        "base": "jinja2.sandbox.ImmutableSandboxedEnvironment",
        "extensions": ["jinja2.ext.do", "jinja2.ext.loopcontrols"],
        "loader": None,
        "parse_only": True,
    }:
        raise RuntimeError("template semantic registry parser environment mismatch")
    semantics = value.get("semantics", {})
    for surface in ("globals", "filters", "tests", "attributes"):
        entries = semantics.get(surface)
        if not isinstance(entries, dict) or not entries:
            raise RuntimeError("template semantic registry surface is missing")
        if any(category not in SEMANTIC_REGISTRY_CATEGORIES for category in entries.values()):
            raise RuntimeError("template semantic registry category is invalid")
    globals_ = semantics["globals"]
    if not _REQUIRED_STATE_GLOBALS.issubset(globals_) or not all(
        globals_[name] == "state_entity_access" for name in _REQUIRED_STATE_GLOBALS
    ):
        raise RuntimeError("template semantic registry state vocabulary is incomplete")
    if not _REQUIRED_ENTITY_SET_GLOBALS.issubset(globals_) or not all(
        globals_[name] == "entity_set_producer"
        for name in _REQUIRED_ENTITY_SET_GLOBALS
    ):
        raise RuntimeError("template semantic registry entity-set vocabulary is incomplete")
    canonical = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if raw != canonical:
        raise RuntimeError("template semantic registry is not canonical")


@lru_cache(maxsize=1)
def semantic_registry() -> dict[str, Any]:
    raw = SEMANTIC_REGISTRY_FILE.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("template semantic registry root is invalid")
    _validate_registry(value, raw)
    return value


def semantic_registry_sha256() -> str:
    return hashlib.sha256(SEMANTIC_REGISTRY_FILE.read_bytes()).hexdigest()


def semantic_category(surface: str, name: str) -> str:
    value = (
        semantic_registry()
        .get("semantics", {})
        .get(surface, {})
        .get(name)
    )
    return value if isinstance(value, str) else "unknown"


def semantic_registry_identity() -> dict[str, str]:
    return {
        "model": SEMANTIC_REGISTRY_MODEL,
        "sha256": semantic_registry_sha256(),
        "jinja_version": "3.1.6",
    }


__all__ = [
    "SEMANTIC_REGISTRY_FILE",
    "SEMANTIC_REGISTRY_MODEL",
    "SEMANTIC_REGISTRY_CATEGORIES",
    "EXPECTED_SEMANTIC_REGISTRY_SHA256",
    "SUPPORTED_HOME_ASSISTANT_TEMPLATE_SOURCES",
    "semantic_category",
    "semantic_registry",
    "semantic_registry_identity",
    "semantic_registry_sha256",
]
