"""Strict offline validation and legacy-baseline normalization."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .models import (
    BASELINE_SCHEMA,
    FINGERPRINT_MODEL,
    FINGERPRINT_SERIALIZATION,
    LEGACY_BASELINE_SCHEMA,
    LEGACY_DECLARED_FINGERPRINT_MODEL,
    LEGACY_UNRESOLVED_FINGERPRINT_MODEL,
    LEGACY_UNRESOLVED_SERIALIZATION,
    AutomationRecord,
    Baseline,
    FingerprintContract,
    InstallationAssurance,
    InventoryScope,
    RecordCoverage,
)


MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 1_000
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000
MAX_STRING_BYTES = 16_384
MAX_LIMITATIONS = 128
MAX_LIMITATION_BYTES = 1_024

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_BARE_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,256}\Z")
_ENTITY_ID = re.compile(r"automation\.[a-z0-9_]+\Z")

LEGACY_CANONICALIZATION = (
    "recursive object-key lexical sort; array order preserved; compact JSON; "
    "JSON scalar types preserved; UTF-8 bytes"
)
LEGACY_INPUT_SCOPE = "entire data object returned by Engineering get_automation_config"
LEGACY_EXCLUDED_FIELDS = (
    "automation operational enabled/on-off state",
    "last_triggered and other runtime entity-state metadata",
    "entity-registry metadata",
    "provider/timing/request envelope metadata",
)

NUMBER_SERIALIZATION = (
    "Python json.dumps finite-number encoding; integer and float JSON types are "
    "preserved (for example 1 differs from 1.0)"
)


class BaselineValidationError(ValueError):
    """Fixed-code validation failure that never reflects input content."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BaselineValidationError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise BaselineValidationError("non_finite_number")


def _strict_loads(raw: bytes) -> Any:
    if len(raw) > MAX_INPUT_BYTES:
        raise BaselineValidationError("input_size_limit_exceeded")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise BaselineValidationError("invalid_utf8") from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_rejecting_object,
            parse_constant=_reject_constant,
        )
    except BaselineValidationError:
        raise
    except (json.JSONDecodeError, ValueError, TypeError, OverflowError, RecursionError):
        raise BaselineValidationError("invalid_json") from None
    _validate_json_limits(value)
    return value


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise BaselineValidationError("invalid_unicode") from None


def _validate_json_limits(value: Any) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise BaselineValidationError("json_node_limit_exceeded")
        if depth > MAX_JSON_DEPTH:
            raise BaselineValidationError("json_depth_limit_exceeded")
        if isinstance(item, str):
            if _utf8_size(item) > MAX_STRING_BYTES:
                raise BaselineValidationError("json_string_limit_exceeded")
        elif item is None or isinstance(item, (bool, int)):
            continue
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise BaselineValidationError("non_finite_number")
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise BaselineValidationError("json_key_invalid")
                if _utf8_size(key) > MAX_STRING_BYTES:
                    raise BaselineValidationError("json_string_limit_exceeded")
                stack.append((child, depth + 1))
        else:
            raise BaselineValidationError("non_json_value")


def canonical_configuration_bytes(value: Any) -> bytes:
    """Return the exact v1 finite-JSON fingerprint representation.

    This intentionally matches the valid-JSON subset of the existing Python
    ``json.dumps(..., sort_keys=True, separators=(\",\", \":\"))`` model.
    Python's default ``ensure_ascii=True`` is retained.  It is not RFC 8785 and
    must receive a new model identifier if any serialization rule changes.
    """

    _validate_json_limits(value)
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise BaselineValidationError("configuration_not_json") from None
    return rendered.encode("utf-8")


def canonical_configuration_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_configuration_bytes(value)).hexdigest()


def _require_dict(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BaselineValidationError(code)
    return value


def _require_list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list):
        raise BaselineValidationError(code)
    return value


def _require_str(value: Any, code: str, *, safe_id: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise BaselineValidationError(code)
    if _utf8_size(value) > MAX_STRING_BYTES:
        raise BaselineValidationError("json_string_limit_exceeded")
    if safe_id and not _SAFE_ID.fullmatch(value):
        raise BaselineValidationError(code)
    return value


def _require_bool(value: Any, code: str) -> bool:
    if type(value) is not bool:
        raise BaselineValidationError(code)
    return value


def _require_int(value: Any, code: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise BaselineValidationError(code)
    return value


def _optional_int(value: Any, code: str, *, minimum: int = 0) -> int | None:
    if value is None:
        return None
    return _require_int(value, code, minimum=minimum)


def _timestamp(value: Any, code: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    text = _require_str(value, code)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise BaselineValidationError(code) from None
    if parsed.tzinfo is None:
        raise BaselineValidationError(code)
    return text


def _digest(value: Any, code: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    text = _require_str(value, code)
    if not _DIGEST.fullmatch(text):
        raise BaselineValidationError(code)
    return text


def _safe_limitations(value: Any, code: str) -> tuple[str, ...]:
    items = _require_list(value, code)
    if len(items) > MAX_LIMITATIONS:
        raise BaselineValidationError("limitation_count_exceeded")
    retained: list[str] = []
    for item in items:
        text = _require_str(item, code)
        if _utf8_size(text) > MAX_LIMITATION_BYTES:
            raise BaselineValidationError("limitation_size_exceeded")
        retained.append(text)
    return tuple(retained)


def _fingerprint_contract() -> FingerprintContract:
    return FingerprintContract(
        model=FINGERPRINT_MODEL,
        algorithm="sha256",
        serialization=FINGERPRINT_SERIALIZATION,
        input_scope="configuration_data_object_only",
        object_key_order="lexicographic",
        array_order="preserved",
        unicode_escaping="ensure_ascii=true",
        number_serialization=NUMBER_SERIALIZATION,
        non_finite_numbers="rejected",
        excluded_fields=LEGACY_EXCLUDED_FIELDS,
        raw_configuration_persisted=False,
    )


def _validate_fingerprint_contract(value: Any) -> FingerprintContract:
    raw = _require_dict(value, "fingerprint_contract_invalid")
    required = {
        "model", "algorithm", "serialization", "input_scope",
        "object_key_order", "array_order", "unicode_escaping",
        "number_serialization", "non_finite_numbers", "excluded_fields",
        "raw_configuration_persisted",
    }
    if set(raw) != required:
        raise BaselineValidationError("fingerprint_contract_fields_invalid")
    expected = _fingerprint_contract()
    candidate = FingerprintContract(
        model=_require_str(raw["model"], "fingerprint_model_invalid", safe_id=True),
        algorithm=_require_str(raw["algorithm"], "fingerprint_algorithm_invalid", safe_id=True),
        serialization=_require_str(raw["serialization"], "fingerprint_serialization_invalid", safe_id=True),
        input_scope=_require_str(raw["input_scope"], "fingerprint_scope_invalid", safe_id=True),
        object_key_order=_require_str(raw["object_key_order"], "fingerprint_key_order_invalid", safe_id=True),
        array_order=_require_str(raw["array_order"], "fingerprint_array_order_invalid", safe_id=True),
        unicode_escaping=_require_str(raw["unicode_escaping"], "fingerprint_unicode_invalid"),
        number_serialization=_require_str(raw["number_serialization"], "fingerprint_number_invalid"),
        non_finite_numbers=_require_str(raw["non_finite_numbers"], "fingerprint_nonfinite_invalid", safe_id=True),
        excluded_fields=tuple(
            _require_str(item, "fingerprint_exclusion_invalid")
            for item in _require_list(raw["excluded_fields"], "fingerprint_exclusions_invalid")
        ),
        raw_configuration_persisted=_require_bool(
            raw["raw_configuration_persisted"], "fingerprint_raw_flag_invalid"
        ),
    )
    if candidate.model == FINGERPRINT_MODEL:
        if candidate != expected:
            raise BaselineValidationError("fingerprint_contract_mismatch")
    else:
        # Unknown fingerprint models remain structurally loadable so the
        # comparator can report an explicit compatibility UNKNOWN. They are
        # never accepted by canonical_configuration_digest().
        if candidate.algorithm != "sha256" or candidate.raw_configuration_persisted:
            raise BaselineValidationError("fingerprint_contract_unsupported")
    return candidate


def _legacy_contract(value: Any) -> FingerprintContract:
    """Validate the retained declaration without upgrading ambiguous bytes.

    The October 1 artifact names a model and states sorted object keys, preserved
    array order, compact JSON and UTF-8 bytes, but it does not retain the encoder
    implementation or settle Unicode escaping / exact numeric rendering.  Raw
    configuration bodies are absent, so those details cannot be recovered by
    recomputation.  Keep the declared digests structurally usable but assign a
    deliberately incompatible unresolved model.
    """

    raw = _require_dict(value, "legacy_fingerprint_contract_invalid")
    if (
        raw.get("model") != LEGACY_DECLARED_FINGERPRINT_MODEL
        or raw.get("algorithm") != "sha256"
    ):
        raise BaselineValidationError("legacy_fingerprint_contract_unrecognized")
    if raw.get("canonicalization") != LEGACY_CANONICALIZATION:
        raise BaselineValidationError("legacy_fingerprint_contract_unrecognized")
    if raw.get("input") != LEGACY_INPUT_SCOPE:
        raise BaselineValidationError("legacy_fingerprint_contract_unrecognized")
    excluded = raw.get("excluded")
    if not isinstance(excluded, list) or tuple(excluded) != LEGACY_EXCLUDED_FIELDS:
        raise BaselineValidationError("legacy_fingerprint_contract_unrecognized")
    if raw.get("raw_configuration_persisted") is not False:
        raise BaselineValidationError("legacy_fingerprint_contract_unrecognized")
    return FingerprintContract(
        model=LEGACY_UNRESOLVED_FINGERPRINT_MODEL,
        algorithm="sha256",
        serialization=LEGACY_UNRESOLVED_SERIALIZATION,
        input_scope="configuration_data_object_only",
        object_key_order="lexicographic",
        array_order="preserved",
        unicode_escaping="unresolved_from_retained_artifact",
        number_serialization=(
            "reported JSON scalar types preserved; exact numeric encoder rendering unresolved"
        ),
        non_finite_numbers="unresolved",
        excluded_fields=LEGACY_EXCLUDED_FIELDS,
        raw_configuration_persisted=False,
    )


def _record_coverage(value: Any) -> RecordCoverage:
    raw = _require_dict(value, "record_coverage_invalid")
    required = {
        "completeness", "fallback_occurred", "warning_count",
        "redacted", "truncated", "omitted",
    }
    if set(raw) != required:
        raise BaselineValidationError("record_coverage_fields_invalid")
    completeness = _require_str(raw["completeness"], "record_completeness_invalid", safe_id=True)
    if completeness not in {"complete", "partial", "failed", "unknown"}:
        raise BaselineValidationError("record_completeness_invalid")
    return RecordCoverage(
        completeness=completeness,
        fallback_occurred=_require_bool(raw["fallback_occurred"], "record_fallback_invalid"),
        warning_count=_require_int(raw["warning_count"], "record_warning_count_invalid"),
        redacted=_require_bool(raw["redacted"], "record_redacted_invalid"),
        truncated=_require_bool(raw["truncated"], "record_truncated_invalid"),
        omitted=_require_bool(raw["omitted"], "record_omitted_invalid"),
    )


def _record(value: Any) -> AutomationRecord:
    raw = _require_dict(value, "record_invalid")
    required = {
        "configuration_id", "entity_id", "mapping_status",
        "configuration", "enabled_state",
    }
    if set(raw) != required:
        raise BaselineValidationError("record_fields_invalid")
    configuration_id = _require_str(raw["configuration_id"], "configuration_id_invalid", safe_id=True)
    entity_id = raw["entity_id"]
    if entity_id is not None:
        entity_id = _require_str(entity_id, "entity_id_invalid")
        if not _ENTITY_ID.fullmatch(entity_id):
            raise BaselineValidationError("entity_id_invalid")
    mapping_status = _require_str(raw["mapping_status"], "mapping_status_invalid", safe_id=True)
    if mapping_status not in {"verified", "missing", "contradictory"}:
        raise BaselineValidationError("mapping_status_invalid")

    config = _require_dict(raw["configuration"], "record_configuration_invalid")
    config_required = {
        "status", "digest", "fingerprint_model", "collected_at",
        "collection_time_status", "provider", "coverage",
    }
    if set(config) != config_required:
        raise BaselineValidationError("record_configuration_fields_invalid")
    config_status = _require_str(config["status"], "configuration_status_invalid", safe_id=True)
    if config_status not in {"readable", "partial", "unreadable", "omitted"}:
        raise BaselineValidationError("configuration_status_invalid")
    digest = _digest(config["digest"], "configuration_digest_invalid", nullable=True)
    model = config["fingerprint_model"]
    if model is not None:
        model = _require_str(model, "record_fingerprint_model_invalid", safe_id=True)
    if config_status == "readable" and (digest is None or model is None):
        raise BaselineValidationError("readable_configuration_digest_missing")
    if digest is not None and model is None:
        raise BaselineValidationError("configuration_digest_model_missing")
    collected_at = _timestamp(config["collected_at"], "configuration_collected_at_invalid", nullable=True)
    time_status = _require_str(config["collection_time_status"], "collection_time_status_invalid", safe_id=True)
    if time_status not in {"observed", "capture_interval_only", "unavailable"}:
        raise BaselineValidationError("collection_time_status_invalid")
    provider = config["provider"]
    if provider is not None:
        provider = _require_str(provider, "provider_invalid", safe_id=True)
    coverage = _record_coverage(config["coverage"])
    if coverage.comparison_complete and config_status != "readable":
        raise BaselineValidationError("record_coverage_contradiction")

    enabled = _require_dict(raw["enabled_state"], "enabled_state_invalid")
    if set(enabled) != {"state", "collected_at", "collection_time_status"}:
        raise BaselineValidationError("enabled_state_fields_invalid")
    state = enabled["state"]
    if state is not None:
        state = _require_str(state, "enabled_state_value_invalid", safe_id=True)
        if state not in {"on", "off", "unknown", "unavailable"}:
            raise BaselineValidationError("enabled_state_value_invalid")
    enabled_at = _timestamp(enabled["collected_at"], "enabled_collected_at_invalid", nullable=True)
    enabled_time_status = _require_str(
        enabled["collection_time_status"], "enabled_time_status_invalid", safe_id=True
    )
    if enabled_time_status not in {"observed", "capture_interval_only", "unavailable"}:
        raise BaselineValidationError("enabled_time_status_invalid")

    return AutomationRecord(
        configuration_id=configuration_id,
        entity_id=entity_id,
        mapping_status=mapping_status,
        configuration_status=config_status,
        configuration_digest=digest,
        fingerprint_model=model,
        collected_at=collected_at,
        collection_time_status=time_status,
        provider=provider,
        coverage=coverage,
        enabled_state=state,
        enabled_collected_at=enabled_at,
        enabled_collection_time_status=enabled_time_status,
    )


def _validate_record_identities(records: tuple[AutomationRecord, ...]) -> None:
    config_ids: set[str] = set()
    verified_entities: dict[str, str] = {}
    for record in records:
        if record.configuration_id in config_ids:
            raise BaselineValidationError("duplicate_canonical_identity")
        config_ids.add(record.configuration_id)
        if record.identity_verified and record.entity_id:
            prior = verified_entities.get(record.entity_id)
            if prior is not None and prior != record.configuration_id:
                raise BaselineValidationError("contradictory_entity_mapping")
            verified_entities[record.entity_id] = record.configuration_id


def _new_baseline(value: dict[str, Any]) -> Baseline:
    required = {
        "schema", "baseline_id", "source_artifact", "capture", "installation",
        "inventory", "fingerprint_contract", "records", "consistency", "authority",
        "limitations", "structural_assertions",
    }
    if set(value) != required:
        raise BaselineValidationError("baseline_fields_invalid")
    if value.get("schema") != BASELINE_SCHEMA:
        raise BaselineValidationError("baseline_schema_unsupported")
    baseline_id = _require_str(value["baseline_id"], "baseline_id_invalid", safe_id=True)

    source_artifact = value["source_artifact"]
    source_sha: str | None = None
    source_baseline_id: str | None = None
    if source_artifact is not None:
        source = _require_dict(source_artifact, "source_artifact_invalid")
        if set(source) != {
            "schema", "baseline_id", "sha256", "internal_material_sha256",
            "configuration_hashes_recomputable",
        }:
            raise BaselineValidationError("source_artifact_fields_invalid")
        _require_str(source["schema"], "source_artifact_schema_invalid", safe_id=True)
        source_baseline_id = _require_str(
            source["baseline_id"], "source_baseline_id_invalid", safe_id=True
        )
        source_sha = _digest(source["sha256"], "source_artifact_digest_invalid")
        internal_digest = source["internal_material_sha256"]
        if internal_digest is not None:
            _digest(internal_digest, "source_internal_digest_invalid")
        _require_bool(
            source["configuration_hashes_recomputable"],
            "source_hash_recomputable_invalid",
        )

    capture = _require_dict(value["capture"], "capture_invalid")
    if set(capture) != {"started_at", "ended_at", "non_atomic"}:
        raise BaselineValidationError("capture_fields_invalid")
    capture_start = _timestamp(capture["started_at"], "capture_start_invalid")
    capture_end = _timestamp(capture["ended_at"], "capture_end_invalid")
    assert capture_start is not None and capture_end is not None
    if datetime.fromisoformat(capture_start.replace("Z", "+00:00")) > datetime.fromisoformat(
        capture_end.replace("Z", "+00:00")
    ):
        raise BaselineValidationError("capture_interval_invalid")
    non_atomic = _require_bool(capture["non_atomic"], "capture_non_atomic_invalid")

    installation_raw = _require_dict(value["installation"], "installation_invalid")
    if set(installation_raw) != {"status", "installation_id", "method", "limitations"}:
        raise BaselineValidationError("installation_fields_invalid")
    installation_status = _require_str(
        installation_raw["status"], "installation_status_invalid", safe_id=True
    )
    if installation_status not in {"established", "unestablished"}:
        raise BaselineValidationError("installation_status_invalid")
    installation_id = installation_raw["installation_id"]
    if installation_id is not None:
        installation_id = _require_str(
            installation_id, "installation_id_invalid", safe_id=True
        )
    if installation_status == "established" and installation_id is None:
        raise BaselineValidationError("installation_id_missing")
    if installation_status == "unestablished" and installation_id is not None:
        raise BaselineValidationError("installation_assurance_contradiction")
    installation = InstallationAssurance(
        status=installation_status,
        installation_id=installation_id,
        method=_require_str(installation_raw["method"], "installation_method_invalid", safe_id=True),
        limitations=_safe_limitations(
            installation_raw["limitations"], "installation_limitations_invalid"
        ),
    )

    inventory_raw = _require_dict(value["inventory"], "inventory_invalid")
    if set(inventory_raw) != {
        "scope", "discovery_method", "completeness", "declared_count",
        "limit", "limit_reached", "omitted_count", "limitations",
    }:
        raise BaselineValidationError("inventory_fields_invalid")
    inventory_completeness = _require_str(
        inventory_raw["completeness"], "inventory_completeness_invalid", safe_id=True
    )
    if inventory_completeness not in {"complete", "partial", "unknown"}:
        raise BaselineValidationError("inventory_completeness_invalid")
    inventory = InventoryScope(
        scope=_require_str(inventory_raw["scope"], "inventory_scope_invalid", safe_id=True),
        discovery_method=_require_str(
            inventory_raw["discovery_method"], "inventory_discovery_invalid", safe_id=True
        ),
        completeness=inventory_completeness,
        declared_count=_require_int(inventory_raw["declared_count"], "inventory_count_invalid"),
        limit=_optional_int(inventory_raw["limit"], "inventory_limit_invalid", minimum=1),
        limit_reached=_require_bool(inventory_raw["limit_reached"], "inventory_limit_reached_invalid"),
        omitted_count=_optional_int(inventory_raw["omitted_count"], "inventory_omitted_invalid"),
        limitations=_safe_limitations(inventory_raw["limitations"], "inventory_limitations_invalid"),
    )
    if inventory.completeness == "complete" and (
        inventory.limit_reached or inventory.omitted_count != 0
    ):
        raise BaselineValidationError("inventory_completeness_contradiction")

    contract = _validate_fingerprint_contract(value["fingerprint_contract"])
    raw_records = _require_list(value["records"], "records_invalid")
    if len(raw_records) > MAX_RECORDS:
        raise BaselineValidationError("record_limit_exceeded")
    records = tuple(_record(item) for item in raw_records)
    _validate_record_identities(records)
    if any(
        record.configuration_digest is not None
        and record.fingerprint_model != contract.model
        for record in records
    ):
        raise BaselineValidationError("record_fingerprint_contract_mismatch")
    if inventory.declared_count < len(records):
        raise BaselineValidationError("inventory_record_count_contradiction")
    if inventory.completeness == "complete" and inventory.declared_count != len(records):
        raise BaselineValidationError("inventory_record_count_contradiction")

    consistency_raw = _require_dict(value["consistency"], "consistency_invalid")
    if set(consistency_raw) != {
        "inventory_drift", "authority_drift", "limitations"
    }:
        raise BaselineValidationError("consistency_fields_invalid")
    inventory_drift = _require_str(
        consistency_raw["inventory_drift"], "inventory_drift_invalid", safe_id=True
    )
    authority_drift = _require_str(
        consistency_raw["authority_drift"], "authority_drift_invalid", safe_id=True
    )
    allowed_drift = {"none_observed_at_capture_fences", "detected", "unknown"}
    if inventory_drift not in allowed_drift or authority_drift not in allowed_drift:
        raise BaselineValidationError("consistency_drift_status_invalid")
    consistency = {
        "inventory_drift": inventory_drift,
        "authority_drift": authority_drift,
        "limitations": list(
            _safe_limitations(consistency_raw["limitations"], "consistency_limitations_invalid")
        ),
    }

    authority_raw = _require_dict(value["authority"], "authority_invalid")
    authority_fields = {
        "status", "home_assistant_core", "generation", "registry_sequence",
        "compatible_count", "fallback_count", "verification_failure_count",
        "retirement_count", "limitations",
    }
    if set(authority_raw) != authority_fields:
        raise BaselineValidationError("authority_fields_invalid")
    core_version = authority_raw["home_assistant_core"]
    if core_version is not None:
        core_version = _require_str(core_version, "authority_core_version_invalid", safe_id=True)
    authority = {
        "status": _require_str(authority_raw["status"], "authority_status_invalid", safe_id=True),
        "home_assistant_core": core_version,
        "generation": _optional_int(authority_raw["generation"], "authority_generation_invalid"),
        "registry_sequence": _optional_int(authority_raw["registry_sequence"], "authority_registry_sequence_invalid"),
        "compatible_count": _optional_int(authority_raw["compatible_count"], "authority_compatible_count_invalid"),
        "fallback_count": _optional_int(authority_raw["fallback_count"], "authority_fallback_count_invalid"),
        "verification_failure_count": _optional_int(authority_raw["verification_failure_count"], "authority_verification_failure_count_invalid"),
        "retirement_count": _optional_int(authority_raw["retirement_count"], "authority_retirement_count_invalid"),
        "limitations": list(_safe_limitations(authority_raw["limitations"], "authority_limitations_invalid")),
    }
    limitations = _safe_limitations(value["limitations"], "baseline_limitations_invalid")
    assertions_raw = _require_dict(
        value["structural_assertions"], "structural_assertions_invalid"
    )
    if set(assertions_raw) != {
        "source_internal_material_digest_verified",
        "configuration_hashes_recomputed",
        "record_count_verified",
        "fingerprint_model_assignment",
    }:
        raise BaselineValidationError("structural_assertions_fields_invalid")
    source_digest_verified = assertions_raw["source_internal_material_digest_verified"]
    if source_digest_verified is not None:
        source_digest_verified = _require_bool(
            source_digest_verified, "source_digest_verified_invalid"
        )
    structural_assertions = {
        "source_internal_material_digest_verified": source_digest_verified,
        "configuration_hashes_recomputed": _require_bool(
            assertions_raw["configuration_hashes_recomputed"],
            "configuration_hashes_recomputed_invalid",
        ),
        "record_count_verified": _require_bool(
            assertions_raw["record_count_verified"], "record_count_verified_invalid"
        ),
        "fingerprint_model_assignment": _require_str(
            assertions_raw["fingerprint_model_assignment"],
            "fingerprint_model_assignment_invalid",
        ),
    }

    return Baseline(
        schema=BASELINE_SCHEMA,
        baseline_id=baseline_id,
        source_artifact_sha256=source_sha,
        source_baseline_id=source_baseline_id,
        capture_start=capture_start,
        capture_end=capture_end,
        capture_non_atomic=non_atomic,
        installation=installation,
        inventory=inventory,
        fingerprint_contract=contract,
        records=records,
        consistency=consistency,
        authority=authority,
        limitations=limitations,
        structural_assertions=dict(structural_assertions),
    )


def _legacy_internal_material(value: dict[str, Any]) -> dict[str, Any]:
    try:
        records = sorted(value["records"], key=lambda row: row["entity_id"])
        return {
            "fingerprint_contract": value["fingerprint_contract"],
            "records": records,
            "capture_interval": {
                "start": value["capture"]["collection_start_inventory_at_utc"],
                "end": value["capture"]["collection_end_inventory_at_utc"],
            },
            "authority": value["authority"],
        }
    except (KeyError, TypeError):
        raise BaselineValidationError("legacy_integrity_material_invalid") from None


def _legacy_normalized_dict(value: dict[str, Any], artifact_sha256: str) -> dict[str, Any]:
    if value.get("schema") != LEGACY_BASELINE_SCHEMA:
        raise BaselineValidationError("baseline_schema_unsupported")
    baseline_id = _require_str(value.get("baseline_id"), "baseline_id_invalid", safe_id=True)
    legacy_digest = _require_str(value.get("baseline_sha256"), "legacy_baseline_digest_invalid")
    if not _BARE_DIGEST.fullmatch(legacy_digest):
        raise BaselineValidationError("legacy_baseline_digest_invalid")
    material = _legacy_internal_material(value)
    actual_material_digest = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if actual_material_digest != legacy_digest:
        raise BaselineValidationError("legacy_material_digest_mismatch")
    if baseline_id.startswith("haab-") and not baseline_id.endswith(legacy_digest[:16]):
        raise BaselineValidationError("legacy_baseline_id_digest_mismatch")

    contract = _legacy_contract(value.get("fingerprint_contract"))
    capture = _require_dict(value.get("capture"), "legacy_capture_invalid")
    capture_start = _timestamp(
        capture.get("collection_start_inventory_at_utc"), "legacy_capture_start_invalid"
    )
    capture_end = _timestamp(
        capture.get("collection_end_inventory_at_utc"), "legacy_capture_end_invalid"
    )
    assert capture_start is not None and capture_end is not None
    configuration_read_count = _require_int(
        capture.get("configuration_read_count"), "legacy_configuration_read_count_invalid"
    )
    configuration_read_success_count = _require_int(
        capture.get("configuration_read_success_count"),
        "legacy_configuration_read_success_count_invalid",
    )
    configuration_read_failure_count = _require_int(
        capture.get("configuration_read_failure_count"),
        "legacy_configuration_read_failure_count_invalid",
    )
    if configuration_read_success_count + configuration_read_failure_count != configuration_read_count:
        raise BaselineValidationError("legacy_configuration_read_accounting_mismatch")

    coverage = _require_dict(value.get("coverage"), "legacy_coverage_invalid")
    declared_count = _require_int(
        coverage.get("established_live_inventory"), "legacy_inventory_count_invalid"
    )
    gaps = coverage.get("disclosed_discovery_gaps", [])
    gap_strings = [item for item in gaps if isinstance(item, str)] if isinstance(gaps, list) else []
    limit_reached = any(
        "bounded at 100" in item and "returned 100" in item for item in gap_strings
    )
    inventory_limit = 100 if limit_reached else None
    inventory_completeness = "partial" if limit_reached else "unknown"
    inventory_limitations = [
        "legacy_inventory_completeness_not_independently_established",
    ]
    if limit_reached:
        inventory_limitations.append("legacy_inventory_limit_reached")

    mapping = _require_dict(value.get("mapping"), "legacy_mapping_invalid")
    missing = mapping.get("missing_mappings")
    contradictory = mapping.get("contradictory_mappings")
    if not isinstance(missing, list) or not isinstance(contradictory, list):
        raise BaselineValidationError("legacy_mapping_invalid")
    mappings_globally_clean = not missing and not contradictory
    automation_inventory_count = _require_int(
        mapping.get("automation_inventory_count"), "legacy_mapping_inventory_count_invalid"
    )
    entity_registry_inventory_count = _require_int(
        mapping.get("entity_registry_inventory_count"), "legacy_mapping_registry_count_invalid"
    )
    mapping_match_count = _require_int(
        mapping.get("entity_to_configuration_id_matches"), "legacy_mapping_match_count_invalid"
    )

    raw_records = _require_list(value.get("records"), "records_invalid")
    if len(raw_records) > MAX_RECORDS:
        raise BaselineValidationError("record_limit_exceeded")
    normalized_records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    verified_entities: set[str] = set()
    captured_status_count = 0
    complete_record_count = 0
    fallback_record_count = 0
    warning_record_count = 0
    redaction_record_count = 0
    enabled_on_count = 0
    enabled_off_count = 0
    for row in raw_records:
        row = _require_dict(row, "legacy_record_invalid")
        configuration_id = _require_str(
            row.get("configuration_id"), "configuration_id_invalid", safe_id=True
        )
        if configuration_id in seen_ids:
            raise BaselineValidationError("duplicate_canonical_identity")
        seen_ids.add(configuration_id)
        entity_id = _require_str(row.get("entity_id"), "entity_id_invalid")
        if not _ENTITY_ID.fullmatch(entity_id):
            raise BaselineValidationError("entity_id_invalid")
        if mappings_globally_clean:
            if entity_id in verified_entities:
                raise BaselineValidationError("contradictory_entity_mapping")
            verified_entities.add(entity_id)
        digest = _digest(
            row.get("configuration_fingerprint"), "configuration_digest_invalid"
        )
        completeness = _require_str(
            row.get("completeness"), "record_completeness_invalid", safe_id=True
        )
        fallback = _require_bool(row.get("fallback_occurred"), "record_fallback_invalid")
        warning_count = _require_int(row.get("warning_count"), "record_warning_count_invalid")
        redaction_or_truncation = _require_bool(
            row.get("redaction_or_truncation_marker_present"),
            "record_redaction_invalid",
        )
        status = _require_str(row.get("status"), "legacy_record_status_invalid", safe_id=True)
        fully_readable = bool(
            status == "captured"
            and completeness == "complete"
            and not fallback
            and warning_count == 0
            and not redaction_or_truncation
        )
        captured_status_count += int(status == "captured")
        complete_record_count += int(fully_readable)
        fallback_record_count += int(fallback)
        warning_record_count += int(warning_count > 0)
        redaction_record_count += int(redaction_or_truncation)
        enabled_state = row.get("enabled_state")
        if enabled_state not in {"on", "off", "unknown", "unavailable", None}:
            raise BaselineValidationError("enabled_state_value_invalid")
        enabled_on_count += int(enabled_state == "on")
        enabled_off_count += int(enabled_state == "off")
        normalized_records.append(
            {
                "configuration_id": configuration_id,
                "entity_id": entity_id,
                "mapping_status": "verified" if mappings_globally_clean else "contradictory",
                "configuration": {
                    "status": "readable" if fully_readable else "partial",
                    "digest": digest,
                    "fingerprint_model": contract.model,
                    "collected_at": None,
                    "collection_time_status": "unavailable",
                    "provider": _require_str(row.get("provider"), "provider_invalid", safe_id=True),
                    "coverage": {
                        "completeness": "complete" if fully_readable else "partial",
                        "fallback_occurred": fallback,
                        "warning_count": warning_count,
                        "redacted": redaction_or_truncation,
                        "truncated": redaction_or_truncation,
                        "omitted": False,
                    },
                },
                "enabled_state": {
                    "state": enabled_state,
                    "collected_at": None,
                    "collection_time_status": "capture_interval_only",
                },
            }
        )

    if declared_count < len(normalized_records):
        raise BaselineValidationError("inventory_record_count_contradiction")
    if configuration_read_count != len(normalized_records):
        raise BaselineValidationError("legacy_configuration_read_count_mismatch")
    if configuration_read_success_count != captured_status_count:
        raise BaselineValidationError("legacy_configuration_read_success_mismatch")
    if configuration_read_failure_count != len(normalized_records) - captured_status_count:
        raise BaselineValidationError("legacy_configuration_read_failure_mismatch")
    if automation_inventory_count != declared_count:
        raise BaselineValidationError("legacy_mapping_inventory_count_mismatch")
    if entity_registry_inventory_count < mapping_match_count:
        raise BaselineValidationError("legacy_mapping_registry_count_mismatch")
    if mappings_globally_clean and mapping_match_count != len(normalized_records):
        raise BaselineValidationError("legacy_mapping_match_count_mismatch")
    expected_coverage = {
        "captured_complete": complete_record_count,
        "unknown": len(normalized_records) - complete_record_count,
        "fallback_records": fallback_record_count,
        "warning_records": warning_record_count,
        "redaction_or_truncation_records": redaction_record_count,
        "operational_state_on": enabled_on_count,
        "operational_state_off": enabled_off_count,
    }
    for field, expected in expected_coverage.items():
        if _require_int(coverage.get(field), f"legacy_coverage_{field}_invalid") != expected:
            raise BaselineValidationError("legacy_coverage_accounting_mismatch")

    legacy_consistency = _require_dict(value.get("consistency"), "legacy_consistency_invalid")
    start_inventory_count = _require_int(
        legacy_consistency.get("start_inventory_count"), "legacy_start_inventory_count_invalid"
    )
    end_inventory_count = _require_int(
        legacy_consistency.get("end_inventory_count"), "legacy_end_inventory_count_invalid"
    )
    added_during_capture = _require_list(
        legacy_consistency.get("added_during_capture"), "legacy_added_during_capture_invalid"
    )
    removed_during_capture = _require_list(
        legacy_consistency.get("removed_during_capture"), "legacy_removed_during_capture_invalid"
    )
    _require_list(
        legacy_consistency.get("enabled_state_changes_during_capture"),
        "legacy_enabled_state_changes_invalid",
    )
    mapping_drift_detected = _require_bool(
        legacy_consistency.get("mapping_drift_detected"), "legacy_mapping_drift_invalid"
    )
    authority_drift_detected = _require_bool(
        legacy_consistency.get("authority_drift_detected"), "legacy_authority_drift_invalid"
    )
    atomic_snapshot = _require_bool(
        legacy_consistency.get("atomic_snapshot"), "legacy_atomic_snapshot_invalid"
    )
    if atomic_snapshot:
        raise BaselineValidationError("legacy_atomic_snapshot_claim_invalid")
    if not added_during_capture and not removed_during_capture and not mapping_drift_detected:
        if start_inventory_count != declared_count or end_inventory_count != declared_count:
            raise BaselineValidationError("legacy_inventory_fence_count_mismatch")

    source_hash = "sha256:" + artifact_sha256
    normalized_material = {
        "source_artifact_sha256": source_hash,
        "source_baseline_id": baseline_id,
        "legacy_material_sha256": "sha256:" + legacy_digest,
        "capture_start": capture_start,
        "capture_end": capture_end,
        "records": normalized_records,
        "inventory_completeness": inventory_completeness,
        "installation_status": "unestablished",
    }
    normalized_id_digest = hashlib.sha256(
        json.dumps(
            normalized_material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    normalized_id = "aabv1-" + normalized_id_digest[:20]

    authority = value.get("authority")
    if not isinstance(authority, dict):
        raise BaselineValidationError("legacy_authority_invalid")
    start_authority = authority.get("start") if isinstance(authority.get("start"), dict) else {}
    end_authority = authority.get("end") if isinstance(authority.get("end"), dict) else {}

    def stable_authority_field(name: str):
        before = start_authority.get(name)
        after = end_authority.get(name)
        return before if before == after else None

    normalized_authority = {
        "status": "owner_capture_assertion_not_independently_reverified",
        "home_assistant_core": stable_authority_field("home_assistant_core"),
        "generation": stable_authority_field("generation"),
        "registry_sequence": stable_authority_field("registry_sequence"),
        "compatible_count": stable_authority_field("compatible_count"),
        "fallback_count": stable_authority_field("fallback_count"),
        "verification_failure_count": stable_authority_field("cumulative_verification_failures"),
        "retirement_count": stable_authority_field("cumulative_retirements"),
        "limitations": [
            "authority_values_are_retained_capture_assertions",
            "authority_values_are_not_installation_identity",
        ],
    }

    return {
        "schema": BASELINE_SCHEMA,
        "baseline_id": normalized_id,
        "source_artifact": {
            "schema": LEGACY_BASELINE_SCHEMA,
            "baseline_id": baseline_id,
            "sha256": source_hash,
            "internal_material_sha256": "sha256:" + legacy_digest,
            "configuration_hashes_recomputable": False,
        },
        "capture": {
            "started_at": capture_start,
            "ended_at": capture_end,
            "non_atomic": True,
        },
        "installation": {
            "status": "unestablished",
            "installation_id": None,
            "method": "legacy_artifact_missing_installation_identity",
            "limitations": [
                "installation_identity_not_retained",
                "core_version_and_entity_names_are_not_installation_identity",
            ],
        },
        "inventory": {
            "scope": "home_assistant_runtime_automations",
            "discovery_method": "legacy_list_automations_plus_entity_registry",
            "completeness": inventory_completeness,
            "declared_count": declared_count,
            "limit": inventory_limit,
            "limit_reached": limit_reached,
            "omitted_count": None,
            "limitations": inventory_limitations,
        },
        "fingerprint_contract": {
            **asdict(contract),
            "excluded_fields": list(contract.excluded_fields),
        },
        "records": normalized_records,
        "consistency": {
            "inventory_drift": (
                "none_observed_at_capture_fences"
                if not mapping_drift_detected
                and not added_during_capture
                and not removed_during_capture
                else "unknown"
            ),
            "authority_drift": (
                "none_observed_at_capture_fences"
                if not authority_drift_detected
                else "unknown"
            ),
            "limitations": [
                "legacy_consistency_values_are_retained_capture_assertions",
                "sequential_capture_can_miss_change_and_revert_within_interval",
            ],
        },
        "authority": normalized_authority,
        "limitations": [
            "raw_configuration_bodies_not_retained_hashes_not_recomputable",
            "per_record_collection_times_not_retained",
            "legacy_mapping_status_uses_retained_global_mapping_assertion_not_raw_registry_records",
            "sequential_capture_not_atomic",
            "blueprint_body_and_effective_behavior_not_covered_by_unchanged_configuration_digest",
            "hash_equality_proves_captured_representation_equality_not_interval_continuity",
        ],
        "structural_assertions": {
            "source_internal_material_digest_verified": True,
            "configuration_hashes_recomputed": False,
            "record_count_verified": True,
            "fingerprint_model_assignment": (
                "retained declaration normalized to an incompatible unresolved legacy model; "
                "Unicode escaping and exact numeric encoder details cannot be recovered without raw configurations"
            ),
        },
    }


def _read_input(path: str | Path) -> bytes:
    try:
        with Path(path).open("rb") as source:
            raw = source.read(MAX_INPUT_BYTES + 1)
    except OSError:
        raise BaselineValidationError("input_unavailable") from None
    if len(raw) > MAX_INPUT_BYTES:
        raise BaselineValidationError("input_size_limit_exceeded")
    return raw


def normalize_legacy_baseline(path: str | Path) -> dict[str, Any]:
    raw = _read_input(path)
    value = _strict_loads(raw)
    value = _require_dict(value, "baseline_root_invalid")
    artifact_sha256 = hashlib.sha256(raw).hexdigest()
    normalized = _legacy_normalized_dict(value, artifact_sha256)
    # Validate the derived object through the same public contract before return.
    _new_baseline(normalized)
    return normalized


def load_baseline(path: str | Path) -> Baseline:
    raw = _read_input(path)
    value = _strict_loads(raw)
    value = _require_dict(value, "baseline_root_invalid")
    schema = value.get("schema")
    if schema == BASELINE_SCHEMA:
        return _new_baseline(value)
    if schema == LEGACY_BASELINE_SCHEMA:
        normalized = _legacy_normalized_dict(value, hashlib.sha256(raw).hexdigest())
        return _new_baseline(normalized)
    raise BaselineValidationError("baseline_schema_unsupported")
