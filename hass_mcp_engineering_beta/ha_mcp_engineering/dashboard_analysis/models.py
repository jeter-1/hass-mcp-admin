"""Immutable, projected evidence without raw dashboard or inventory bodies."""

from dataclasses import dataclass
import re

from ..sanitization import sanitize_untrusted_data
from . import contracts as c


_POINTER = re.compile(r"(?:/(?:views|cards|sections|card|entities|entity|conditions|visibility|"
    r"tap_action|hold_action|double_tap_action|icon_tap_action|icon_hold_action|icon_double_tap_action|"
    r"target|entity_id|data|service_data|header|footer|badges|features|[0-9]{1,5}))*\Z")
_HASH = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]{8,16}(?:Z|\+00:00)\Z")
_ENUMS = {
    "kind": {"entity_reference", "control", "coverage_gap", "states", "registry", "dashboard"},
    "availability": {"present", "unavailable", "state_unknown", "registry_only", "registry_disabled",
                     "absent_from_observed_inventories", "unassessed"},
    "provenance": {"explicit_configuration", "inferred_builtin_default", "unsupported_or_dynamic"},
    "interaction": {"none", "more-info", "toggle", "perform-action", "call-service", "navigate",
                    "url", "assist", "fire-dom-event", "inline_helper", "header_toggle"},
    "target_expansion": {"literal", "unknown", "not_applicable"},
    "confirmation": {"absent", "present", "unresolved"},
    "rule_id": {"literal_entity", "literal_target", "condition_entity", "button_default", "tile_default",
                "tile_icon_default", "entities_helper", "entities_header", "entities_service",
                "explicit_action", "coverage"},
    "reason": {"unsupported_component", "dynamic_value", "malformed_selector", "source_partial",
               "structural_limit", "item_limit", "reference_limit", "source_gate_pending"},
    "provider": {"direct_ha_api", "upstream_dashboard"},
}
_COUNTS = {"examined", "retained", "omitted", "invalid", "duplicate"}
_FLAGS = {"complete", "sanitized", "processing_truncated", "non_atomic", "conditional", "source_projection_exact"}
_SOURCE_KEYS = {"kind", "provider", "complete", "failure", "started_at", "finished_at", "sanitized",
                "processing_truncated", *_COUNTS}
_ITEM_KEYS = {"kind", "id", "pointer", "entity_id", "availability", "provenance", "interaction",
              "service", "target_expansion", "confirmation", "rule_id", "reason", "conditional",
              "source_projection_exact", "target_references"}
_HEADER_KEYS = {"model", "requested_path", "canonical_path", "core_version", "frontend_commit",
                "config_hash", "engineering_config_hash", "projection_hash", "coverage", "sources",
                "collection_started_at", "collection_finished_at", "non_atomic"}


def _scalar(key, value, known_secrets):
    valid = False
    if key in _ENUMS:
        valid = type(value) is str and value in _ENUMS[key]
    elif key in _COUNTS:
        valid = type(value) is int and 0 <= value <= c.INVENTORY_NODES
    elif key in _FLAGS:
        valid = type(value) is bool
    elif key == "failure":
        valid = value is None or (type(value) is str and value in c.REASONS)
    elif type(value) is str and len(value) <= 256:
        valid = bool(
            (key == "model" and value == c.MODEL)
            or (key in {"requested_path", "canonical_path"} and c.PATH.fullmatch(value))
            or (key in {"entity_id", "service"} and c.ENTITY.fullmatch(value))
            or (key in {"engineering_config_hash", "projection_hash", "id"} and _HASH.fullmatch(value))
            or (key == "config_hash" and re.fullmatch(r"[0-9a-f]{16}", value))
            or (key == "frontend_commit" and re.fullmatch(r"[0-9a-f]{40}", value))
            or (key == "core_version" and re.fullmatch(r"[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}", value))
            or (key == "pointer" and _POINTER.fullmatch(value))
            or (key in {"started_at", "finished_at", "collection_started_at", "collection_finished_at"}
                and _TIME.fullmatch(value)))
    if not valid:
        raise c.AnalysisError("malformed_response")
    if type(value) is str:
        clean = sanitize_untrusted_data(value, known_secrets=known_secrets, max_string=256)
        if clean.value != value or clean.failed_closed:
            raise c.AnalysisError("malformed_response")


def validate_projection(header, items, *, known_secrets=()):
    """Reject raw or unrecognized channels at the immutable-snapshot boundary."""
    if type(header) is not dict or set(header) - _HEADER_KEYS or not {"model", "requested_path", "canonical_path"} <= set(header):
        raise c.AnalysisError("malformed_response")
    for key, value in header.items():
        if key == "coverage":
            if type(value) is not dict or set(value) != {"references", "availability", "controls"}:
                raise c.AnalysisError("malformed_response")
            if any(type(v) is not str or v not in {"complete", "partial", "unassessed"} for v in value.values()):
                raise c.AnalysisError("malformed_response")
        elif key == "sources":
            if type(value) is not list or len(value) > 3:
                raise c.AnalysisError("malformed_response")
            for source in value:
                if type(source) is not dict or set(source) - _SOURCE_KEYS:
                    raise c.AnalysisError("malformed_response")
                for k, v in source.items():
                    _scalar(k, v, known_secrets)
        else:
            _scalar(key, value, known_secrets)
    for item in items:
        if type(item) is not dict or set(item) - _ITEM_KEYS or not {"kind", "pointer"} <= set(item):
            raise c.AnalysisError("malformed_response")
        if type(item["kind"]) is not str or item["kind"] not in {"entity_reference", "control", "coverage_gap"}:
            raise c.AnalysisError("malformed_response")
        for key, value in item.items():
            if key == "target_references":
                if type(value) is not list or len(value) > c.UNIQUE_REFERENCES:
                    raise c.AnalysisError("malformed_response")
                for entity in value:
                    _scalar("entity_id", entity, known_secrets)
            else:
                _scalar(key, value, known_secrets)


@dataclass(frozen=True)
class Inventory:
    kind: str
    # Sorted immutable pairs: entity ID and a closed state/disabled category.
    records: tuple[tuple[str, str], ...]
    complete: bool
    examined: int
    omitted: int
    invalid: int
    duplicate: int
    failure: str | None = None

    def metadata(self):
        return {"kind": self.kind, "provider": "direct_ha_api", "complete": self.complete,
                "examined": self.examined, "retained": len(self.records),
                "omitted": self.omitted, "invalid": self.invalid,
                "duplicate": self.duplicate, "failure": self.failure}


@dataclass(frozen=True)
class FrozenReport:
    """The producer must supply only validated, sanitized projections."""
    header: bytes
    items: tuple[bytes, ...]
    authority: tuple
    digest: str


@dataclass(frozen=True)
class Snapshot:
    report: FrozenReport
    caller: str
    path: str
    expires: float
