#!/usr/bin/env python3
"""Offline evaluator for the versioned promotion regression manifest.

The program has no transport, subprocess, credential, or write path.  An
authorized operator supplies a bounded projection of fields observed from the
declared read-only calls.  This evaluator validates that attested capture,
binds it to the exact manifest and target, and classifies each sentinel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPOSITORY_ROOT / "promotion" / "promotion_regression_manifest.yaml"
DEFAULT_SCHEMA = REPOSITORY_ROOT / "promotion" / "manifest_schema.json"

CONFIRMED = "CONFIRMED"
REGRESSION = "REGRESSION"
KNOWN_FAILING = "KNOWN_FAILING"
UNEXPECTED_PASS = "UNEXPECTED_PASS"
NOT_CAPTURED = "NOT_CAPTURED"

OUTCOME_ORDER = (REGRESSION, NOT_CAPTURED, UNEXPECTED_PASS, KNOWN_FAILING, CONFIRMED)
OUTCOME_HEADINGS = {
    REGRESSION: "REGRESSION - accepted behavior failed or a known failure changed.",
    NOT_CAPTURED: "NOT_CAPTURED - required evidence is missing or inconclusive.",
    UNEXPECTED_PASS: "UNEXPECTED_PASS - a recorded deficiency may now be fixed.",
    KNOWN_FAILING: "KNOWN_FAILING - the exact recorded failure signature matched.",
    CONFIRMED: "CONFIRMED - the desired contract passed.",
}

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_INCOMPLETE = 2
EXIT_USAGE = 3

CAPTURE_SCHEMA_VERSION = 1
MAX_CAPTURE_BYTES = 256 * 1024
MAX_OBSERVATION_BYTES = 24 * 1024
MAX_VALUE_BYTES = 2 * 1024
MAX_REPORT_BYTES = 96 * 1024
MAX_PROJECTION_SOURCE_BYTES = 128 * 1024
MAX_PROJECTION_NODES = 4096
MAX_PROJECTION_DEPTH = 24
MAX_PROJECTION_CANDIDATES = 100
MAX_WAIT_TEMPLATE_BYTES = 60_000
MAX_FAILED_CHECKS_RENDERED = 12

BLOCKING = "blocking"
TRACKED_NONBLOCKING = "tracked_nonblocking"

_AUTHORITY_SOURCE_SHA256 = (
    "ff17d026a98ad4f55c8ddc8c3f6131ebbb59bccce6920e1372211c9f9f458ca1"
)
_AUTHORITY_PROMOTION_BLOCKERS = frozenset({1, 2, 3, 4, 5})
_AUTHORITY_TRACKED_NONBLOCKING = frozenset({19})

_F3_TERMINAL_STATES = frozenset({
    "succeeded_verified",
    "failed_pre_dispatch",
    "failed_post_dispatch",
    "manual_review_required",
    "cancelled_pre_dispatch",
})
_F3_CHILD_STATES = _F3_TERMINAL_STATES | frozenset({
    "not_started",
    "created",
    "preflight",
    "dispatching",
    "observing",
    "verifying",
    "waiting_for_lock",
    "compensating",
    "partial_application",
    "compensated",
    "superseded",
})

_SELECTOR = re.compile(
    r"^(?P<field>[^\[\]]+)\[(?P<key>[^=\[\]]+)=(?P<value>[^\[\]]*)\]$"
)
_PLACEHOLDER = re.compile(r"(?:replace|placeholder|todo|unknown)", re.IGNORECASE)
_SENSITIVE_KEY = re.compile(
    r"(?:^|[._-])(?:authorization|password|passwd|secret|token|api[_-]?key|cookie)(?:$|[._-])",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:\bBearer\s+\S{12,}|\b(?:sk|ghp|gho|github_pat)_[A-Za-z0-9_-]{12,}|"
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)


class CheckerError(RuntimeError):
    """A manifest, capture, or invocation error that prevents a verdict."""


def _jsonschema_module():
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - environment problem
        raise CheckerError("jsonschema is required to validate promotion evidence.") from exc
    return jsonschema


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment problem
        raise CheckerError("PyYAML is required to read the promotion manifest.") from exc
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CheckerError(f"Cannot read manifest {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CheckerError(f"Manifest {path} is not valid YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise CheckerError(f"Manifest {path} must be a mapping.")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CheckerError(f"JSON contains a duplicate key {key!r}.")
        value[key] = item
    return value


def load_json(path: Path, label: str, *, maximum_bytes: int | None = None) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CheckerError(f"Cannot read {label} {path}: {exc}") from exc
    if maximum_bytes is not None and len(raw) > maximum_bytes:
        raise CheckerError(
            f"{label.capitalize()} exceeds the {maximum_bytes}-byte input bound."
        )
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except UnicodeDecodeError as exc:
        raise CheckerError(f"{label.capitalize()} {path} is not UTF-8.") from exc
    except ValueError as exc:
        raise CheckerError(f"{label.capitalize()} {path} is not valid JSON: {exc}") from exc


def canonical_manifest_digest(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _canonical_fingerprint(model: str, value: Any) -> str:
    """Hash typed structured evidence without delimiter ambiguity."""
    encoded = json.dumps(
        {"model": model, "value": value},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_manifest(manifest: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    validator = _jsonschema_module().Draft202012Validator(schema)
    errors = [
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(manifest), key=lambda item: list(item.path))
    ]
    errors.extend(_reference_errors(manifest))
    return errors


def _all_checks(sentinel: dict[str, Any]) -> list[dict[str, Any]]:
    return list(sentinel.get("desired_checks") or []) + list(
        sentinel.get("known_failure_checks") or []
    )


def _reference_errors(manifest: dict[str, Any]) -> list[str]:
    observations = manifest.get("observations")
    sentinels = manifest.get("sentinels")
    if not isinstance(observations, list) or not isinstance(sentinels, list):
        return []
    errors: list[str] = []
    known: dict[str, dict[str, Any]] = {}
    for observation in observations:
        identifier = observation.get("id")
        if identifier in known:
            errors.append(f"observations: duplicate observation id {identifier!r}.")
        known[identifier] = observation
        if observation.get("effect_class") != "read_only":
            errors.append(
                f"observations/{identifier}: default observations must be read_only."
            )
        overlap = set(observation.get("arguments") or {}) & set(
            observation.get("operator_arguments") or {}
        )
        if overlap:
            errors.append(
                f"observations/{identifier}: fixed and operator arguments overlap: "
                + ", ".join(sorted(overlap))
            )
    referenced: set[str] = set()
    seen: set[str] = set()
    for sentinel in sentinels:
        identifier = sentinel.get("id")
        if identifier in seen:
            errors.append(f"sentinels: duplicate sentinel id {identifier!r}.")
        seen.add(identifier)
        primary = sentinel.get("observation")
        referenced.add(primary)
        if primary not in known:
            errors.append(f"sentinels/{identifier}: unknown observation {primary!r}.")
        for index, check in enumerate(_all_checks(sentinel)):
            target = check.get("source_observation", primary)
            referenced.add(target)
            if target not in known:
                errors.append(
                    f"sentinels/{identifier}/checks/{index}: unknown observation {target!r}."
                )
            reference = check.get("observation")
            if reference is not None:
                referenced.add(reference)
                if reference not in known:
                    errors.append(
                        f"sentinels/{identifier}/checks/{index}: unknown reference "
                        f"observation {reference!r}."
                    )
            if check.get("operator") == "equals_invocation_argument":
                argument = check.get("invocation_argument")
                declaration = known.get(target) or {}
                declared = set(declaration.get("arguments") or {}) | set(
                    declaration.get("operator_arguments") or {}
                )
                if argument not in declared:
                    errors.append(
                        f"sentinels/{identifier}/checks/{index}: invocation argument "
                        f"{argument!r} is not declared by {target!r}."
                    )
    for identifier in sorted(set(known) - referenced):
        errors.append(f"observations/{identifier}: no sentinel uses this observation.")

    projection_contracts = manifest.get("projection_contracts") or []
    contract_ids: set[str] = set()
    contract_by_observation: dict[str, dict[str, Any]] = {}
    for contract in projection_contracts:
        identifier = contract.get("id")
        observation = contract.get("observation")
        if identifier in contract_ids:
            errors.append(f"projection_contracts: duplicate id {identifier!r}.")
        contract_ids.add(identifier)
        if observation not in known:
            errors.append(
                f"projection_contracts/{identifier}: unknown observation {observation!r}."
            )
        if observation in contract_by_observation:
            errors.append(
                f"projection_contracts/{identifier}: observation {observation!r} "
                "already has a projection contract."
            )
        contract_by_observation[observation] = contract

    required_projection_paths: dict[str, set[str]] = {}
    for sentinel in sentinels:
        primary = sentinel.get("observation")
        for check in _all_checks(sentinel):
            if str(check.get("path") or "").startswith("projection."):
                target = check.get("source_observation", primary)
                required_projection_paths.setdefault(target, set()).add(check["path"])
    for observation, paths in sorted(required_projection_paths.items()):
        contract = contract_by_observation.get(observation)
        if contract is None:
            errors.append(
                f"observations/{observation}: projected fields lack a derivation contract."
            )
            continue
        declared = set(contract.get("output_paths") or [])
        if declared != paths:
            errors.append(
                f"projection_contracts/{contract.get('id')}: output paths must exactly "
                f"match used projection paths for {observation!r}."
            )
    for observation, contract in sorted(contract_by_observation.items()):
        if observation not in required_projection_paths:
            errors.append(
                f"projection_contracts/{contract.get('id')}: no sentinel uses its outputs."
            )

    canary_ids: set[str] = set()
    for canary in manifest.get("separately_authorized_canaries") or []:
        identifier = canary.get("id")
        if identifier in canary_ids:
            errors.append(
                f"separately_authorized_canaries: duplicate id {identifier!r}."
            )
        canary_ids.add(identifier)
        if identifier in seen:
            errors.append(
                f"separately_authorized_canaries/{identifier}: must not also be a sentinel."
            )
        if canary.get("tool") in {item.get("tool") for item in observations}:
            errors.append(
                f"separately_authorized_canaries/{identifier}: write-classified tool "
                "must not appear in default observations."
            )

    snapshot = manifest.get("product_authority_snapshot") or {}
    blockers = list(snapshot.get("promotion_blockers") or [])
    tracked = list(snapshot.get("tracked_nonblocking") or [])
    if snapshot.get("source_sha256") != _AUTHORITY_SOURCE_SHA256:
        errors.append(
            "product_authority_snapshot: source digest is not the reviewed authority snapshot."
        )
    blocker_items = {item.get("register_item") for item in blockers}
    tracked_items = {item.get("register_item") for item in tracked}
    if blocker_items != _AUTHORITY_PROMOTION_BLOCKERS:
        errors.append(
            "product_authority_snapshot: promotion blocker inventory must be exactly "
            "1, 2, 3, 4, and 5."
        )
    if tracked_items != _AUTHORITY_TRACKED_NONBLOCKING:
        errors.append(
            "product_authority_snapshot: tracked nonblocking inventory must be exactly 19."
        )
    for requirement in blockers:
        if requirement.get("promotion_disposition") != BLOCKING:
            errors.append(
                "product_authority_snapshot: every promotion blocker must be blocking."
            )
    for requirement in tracked:
        if requirement.get("promotion_disposition") != TRACKED_NONBLOCKING:
            errors.append(
                "product_authority_snapshot: tracked item 19 must be tracked_nonblocking."
            )
    confirmed = {
        item.get("id")
        for item in sentinels
        if item.get("register_section") == "confirmed_regression_passes"
    }
    if set(snapshot.get("confirmed_regression_passes") or []) != confirmed:
        errors.append(
            "product_authority_snapshot: confirmed regression-pass inventory is incomplete."
        )

    requirements = blockers + tracked
    requirement_items: set[int] = set()
    requirement_targets: set[tuple[str, str]] = set()
    sentinel_by_id = {item.get("id"): item for item in sentinels}
    canary_by_id = {
        item.get("id"): item
        for item in manifest.get("separately_authorized_canaries") or []
    }
    for requirement in requirements:
        register_item = requirement.get("register_item")
        representation = requirement.get("representation")
        identifier = requirement.get("id")
        target_key = (str(representation), str(identifier))
        if register_item in requirement_items:
            errors.append(
                f"product_authority_snapshot: duplicate register item {register_item!r}."
            )
        requirement_items.add(register_item)
        if target_key in requirement_targets:
            errors.append(
                "product_authority_snapshot: duplicate requirement target "
                f"{representation}/{identifier}."
            )
        requirement_targets.add(target_key)
        target = (
            sentinel_by_id.get(identifier)
            if representation == "sentinel"
            else canary_by_id.get(identifier)
        )
        if target is None:
            errors.append(
                "product_authority_snapshot: requirement "
                f"#{register_item} references unknown {representation} {identifier!r}."
            )
            continue
        deficiency = target.get("deficiency") or {}
        if deficiency.get("register_item") != register_item:
            errors.append(
                f"product_authority_snapshot: requirement #{register_item} does not "
                "match its declared deficiency."
            )
        if sorted(deficiency.get("related_register_items") or []) != sorted(
            requirement.get("related_register_items") or []
        ):
            errors.append(
                f"product_authority_snapshot: requirement #{register_item} has "
                "contradictory related-item evidence."
            )
        if target.get("promotion_disposition") != requirement.get(
            "promotion_disposition"
        ):
            errors.append(
                f"product_authority_snapshot: requirement #{register_item} has "
                "a contradictory promotion disposition."
            )
        if representation == "required_canary" and not target.get(
            "required_for_promotion"
        ):
            errors.append(
                f"product_authority_snapshot: requirement #{register_item} canary "
                "is not required for promotion."
            )
    if len(requirements) != len(requirement_items):
        errors.append(
            "product_authority_snapshot: each register item must map exactly once."
        )
    return errors


def _required_paths(manifest: dict[str, Any]) -> dict[str, set[str]]:
    paths = {item["id"]: set() for item in manifest["observations"]}
    for sentinel in manifest["sentinels"]:
        primary = sentinel["observation"]
        for check in _all_checks(sentinel):
            paths[check.get("source_observation", primary)].add(check["path"])
            if check.get("operator") == "equals_observation_path":
                paths[check["observation"]].add(check["reference_path"])
    return paths


def _required_canary_paths(manifest: dict[str, Any]) -> dict[str, set[str]]:
    return {
        item["id"]: {check["path"] for check in item.get("desired_checks") or []}
        for item in manifest.get("separately_authorized_canaries") or []
    }


def _schema_errors(instance: Any, schema: dict[str, Any]) -> list[str]:
    validator = _jsonschema_module().Draft202012Validator(schema)
    return [
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path))
    ]


def _encoded_size(value: Any) -> int:
    try:
        return len(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise CheckerError("Capture contains a value that cannot be encoded safely.") from exc


def _capture_value_errors(value: Any, path: str = "capture") -> list[str]:
    errors: list[str] = []
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
            errors.append(f"{path}: string exceeds the {MAX_VALUE_BYTES}-byte bound.")
        if _SENSITIVE_VALUE.search(value):
            errors.append(f"{path}: value resembles credential material.")
    elif isinstance(value, dict):
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                errors.append(f"{path}.{key}: sensitive field names are not allowed.")
            errors.extend(_capture_value_errors(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_capture_value_errors(item, f"{path}.{index}"))
    return errors


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or _PLACEHOLDER.search(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_capture(
    manifest: dict[str, Any], capture: dict[str, Any], schema: dict[str, Any]
) -> list[str]:
    capture_schema = (schema.get("$defs") or {}).get("capture")
    if not isinstance(capture_schema, dict):
        raise CheckerError("The promotion schema does not declare $defs.capture.")
    errors = _schema_errors(capture, capture_schema)
    if _encoded_size(capture) > MAX_CAPTURE_BYTES:
        errors.append(f"<root>: capture exceeds the {MAX_CAPTURE_BYTES}-byte bound.")
    errors.extend(_capture_value_errors(capture))
    if capture.get("capture_schema_version") != CAPTURE_SCHEMA_VERSION:
        errors.append("capture_schema_version: unsupported capture schema version.")
    if capture.get("manifest_version") != manifest.get("manifest_version"):
        errors.append("manifest_version: capture does not match the manifest.")
    if capture.get("manifest_digest") != canonical_manifest_digest(manifest):
        errors.append("manifest_digest: capture is not bound to this exact manifest.")
    target = capture.get("target") if isinstance(capture.get("target"), dict) else {}
    manifest_target = manifest.get("target") or {}
    for key in ("release", "build_sha"):
        if target.get(key) != manifest_target.get(key):
            errors.append(f"target/{key}: capture does not match the manifest target.")
    if not _valid_timestamp(capture.get("captured_at")):
        errors.append("captured_at: must be a non-placeholder timezone-aware timestamp.")
    for key in ("captured_by", "session_id"):
        value = capture.get(key)
        if not isinstance(value, str) or not value.strip() or _PLACEHOLDER.search(value):
            errors.append(f"{key}: must be a non-placeholder operator/session attribution.")

    declarations = {item["id"]: item for item in manifest.get("observations") or []}
    projection_contracts = {
        item["observation"]: item
        for item in manifest.get("projection_contracts") or []
    }
    allowed_paths = _required_paths(manifest)
    entries = capture.get("observations")
    if not isinstance(entries, dict):
        return errors
    unknown = sorted(set(entries) - set(declarations))
    if unknown:
        errors.append("observations: undeclared observation ids: " + ", ".join(unknown))
    captured_task_ids: dict[str, str] = {}
    for identifier, entry in entries.items():
        declaration = declarations.get(identifier)
        if declaration is None or not isinstance(entry, dict):
            continue
        if _encoded_size(entry) > MAX_OBSERVATION_BYTES:
            errors.append(
                f"observations/{identifier}: exceeds the {MAX_OBSERVATION_BYTES}-byte bound."
            )
        if entry.get("observation_id") != identifier:
            errors.append(f"observations/{identifier}: observation_id mismatch.")
        if entry.get("tool") != declaration.get("tool"):
            errors.append(f"observations/{identifier}: tool does not match the manifest.")
        arguments = entry.get("arguments")
        if not isinstance(arguments, dict):
            continue
        fixed = declaration.get("arguments") or {}
        local = declaration.get("operator_arguments") or {}
        status = entry.get("status")
        projection_contract = projection_contracts.get(identifier)
        if (
            status == "captured"
            and projection_contract is not None
            and projection_contract.get("evidence_availability")
            == "unavailable_pending_authoritative_capture"
        ):
            errors.append(
                f"observations/{identifier}: authoritative projection identity is "
                "unavailable and cannot be captured from synthetic evidence."
            )
        required_names = set(fixed) | (set(local) if status == "captured" else set())
        allowed_names = set(fixed) | set(local)
        if not required_names.issubset(arguments) or not set(arguments).issubset(
            allowed_names
        ):
            errors.append(
                f"observations/{identifier}: invocation arguments must contain "
                + ", ".join(sorted(required_names))
                + " and no undeclared arguments."
            )
        for name, expected in fixed.items():
            if name not in arguments or not strict_equal(arguments[name], expected):
                errors.append(
                    f"observations/{identifier}/arguments/{name}: fixed argument mismatch."
                )
        for name, argument_schema in local.items():
            if name not in arguments:
                continue
            for issue in _schema_errors(arguments[name], argument_schema):
                errors.append(
                    f"observations/{identifier}/arguments/{name}/{issue}"
                )
            if isinstance(arguments[name], str) and _PLACEHOLDER.search(arguments[name]):
                errors.append(
                    f"observations/{identifier}/arguments/{name}: placeholder was not resolved."
                )
        if status != "captured":
            continue
        if declaration.get("tool") == "get_execution_task":
            task_id = arguments.get("task_id")
            if isinstance(task_id, str):
                previous = captured_task_ids.get(task_id)
                if previous is not None:
                    errors.append(
                        f"observations/{identifier}/arguments/task_id: duplicates "
                        f"captured task identity used by {previous!r}."
                    )
                captured_task_ids[task_id] = identifier
        evidence = entry.get("evidence") or {}
        absent = entry.get("absent_paths") or []
        unexpected_paths = sorted((set(evidence) | set(absent)) - allowed_paths[identifier])
        if unexpected_paths:
            errors.append(
                f"observations/{identifier}: evidence paths are not allowlisted: "
                + ", ".join(unexpected_paths)
            )
        overlap = sorted(set(evidence) & set(absent))
        if overlap:
            errors.append(
                f"observations/{identifier}: paths cannot be both present and absent: "
                + ", ".join(overlap)
            )

    canary_declarations = {
        item["id"]: item
        for item in manifest.get("separately_authorized_canaries") or []
    }
    canary_paths = _required_canary_paths(manifest)
    canary_entries = capture.get("canaries")
    if not isinstance(canary_entries, dict):
        return errors
    unknown_canaries = sorted(set(canary_entries) - set(canary_declarations))
    if unknown_canaries:
        errors.append("canaries: undeclared canary ids: " + ", ".join(unknown_canaries))
    for identifier, entry in canary_entries.items():
        declaration = canary_declarations.get(identifier)
        if declaration is None or not isinstance(entry, dict):
            continue
        if _encoded_size(entry) > MAX_OBSERVATION_BYTES:
            errors.append(
                f"canaries/{identifier}: exceeds the {MAX_OBSERVATION_BYTES}-byte bound."
            )
        if entry.get("canary_id") != identifier:
            errors.append(f"canaries/{identifier}: canary_id mismatch.")
        if entry.get("tool") != declaration.get("tool"):
            errors.append(f"canaries/{identifier}: tool does not match the manifest.")
        if (
            declaration.get("availability") == "unavailable_pending_reviewed_protocol"
            and entry.get("status") == "captured"
        ):
            errors.append(
                f"canaries/{identifier}: unavailable requirement cannot be captured "
                "without a reviewed protocol."
            )
        arguments = entry.get("arguments")
        if not isinstance(arguments, dict):
            continue
        fixed = declaration.get("fixed_arguments") or {}
        local = declaration.get("operator_arguments") or {}
        status = entry.get("status")
        required_names = set(fixed) | (set(local) if status == "captured" else set())
        allowed_names = set(fixed) | set(local)
        if not required_names.issubset(arguments) or not set(arguments).issubset(allowed_names):
            errors.append(
                f"canaries/{identifier}: invocation arguments must contain "
                + ", ".join(sorted(required_names))
                + " and no undeclared arguments."
            )
        for name, expected in fixed.items():
            if name not in arguments or not strict_equal(arguments[name], expected):
                errors.append(f"canaries/{identifier}/arguments/{name}: fixed argument mismatch.")
        for name, argument_schema in local.items():
            if name not in arguments:
                continue
            for issue in _schema_errors(arguments[name], argument_schema):
                errors.append(f"canaries/{identifier}/arguments/{name}/{issue}")
            if isinstance(arguments[name], str) and _PLACEHOLDER.search(arguments[name]):
                errors.append(
                    f"canaries/{identifier}/arguments/{name}: placeholder was not resolved."
                )
        if status != "captured":
            continue
        evidence = entry.get("evidence") or {}
        absent = entry.get("absent_paths") or []
        unexpected = sorted((set(evidence) | set(absent)) - canary_paths[identifier])
        if unexpected:
            errors.append(
                f"canaries/{identifier}: evidence paths are not allowlisted: "
                + ", ".join(unexpected)
            )
        overlap = sorted(set(evidence) & set(absent))
        if overlap:
            errors.append(
                f"canaries/{identifier}: paths cannot be both present and absent: "
                + ", ".join(overlap)
            )
    return errors


def _projection_contract(
    manifest: dict[str, Any], observation_id: str
) -> dict[str, Any]:
    matches = [
        item
        for item in manifest.get("projection_contracts") or []
        if item.get("observation") == observation_id
    ]
    if len(matches) != 1:
        raise CheckerError(
            f"Observation {observation_id!r} does not have exactly one projection contract."
        )
    return matches[0]


def _bounded_projection_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CheckerError(f"Projection source {label} must be a nonempty string.")
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        raise CheckerError(
            f"Projection source {label} exceeds the {MAX_VALUE_BYTES}-byte value bound."
        )
    if _SENSITIVE_VALUE.search(value):
        raise CheckerError(f"Projection source {label} resembles credential material.")
    return value


def _projection_source_errors(value: Any, path: str = "projection_source") -> list[str]:
    errors: list[str] = []
    stack: list[tuple[Any, str, int]] = [(value, path, 0)]
    nodes = 0
    while stack:
        item, item_path, depth = stack.pop()
        if depth > MAX_PROJECTION_DEPTH:
            errors.append(
                f"{item_path}: exceeds the {MAX_PROJECTION_DEPTH}-level depth bound."
            )
            break
        nodes += 1
        if nodes > MAX_PROJECTION_NODES:
            errors.append(
                f"{path}: exceeds the {MAX_PROJECTION_NODES}-node bound."
            )
            break
        if isinstance(item, str):
            maximum = (
                MAX_WAIT_TEMPLATE_BYTES
                if item_path.endswith(".wait_template")
                else MAX_VALUE_BYTES
            )
            if len(item.encode("utf-8")) > maximum:
                errors.append(
                    f"{item_path}: string exceeds the {maximum}-byte bound."
                )
            if _SENSITIVE_VALUE.search(item):
                errors.append(f"{item_path}: value resembles credential material.")
        elif isinstance(item, dict):
            for key in sorted(item, reverse=True):
                if _SENSITIVE_KEY.search(str(key)):
                    errors.append(
                        f"{item_path}.{key}: sensitive field names are not allowed."
                    )
                stack.append((item[key], f"{item_path}.{key}", depth + 1))
        elif isinstance(item, list):
            for index in range(len(item) - 1, -1, -1):
                stack.append((item[index], f"{item_path}.{index}", depth + 1))
    return errors


def _derive_dependency_projection(source: Any) -> dict[str, Any]:
    """Project only fields exposed by entity_dependency_analysis."""
    if not isinstance(source, dict) or source.get("success") is not True:
        raise CheckerError("Dependency projection requires a successful public response.")
    data = source.get("data")
    if not isinstance(data, dict):
        raise CheckerError("Dependency projection response is missing data.")
    overview, pagination, index = (
        data.get("overview"), data.get("pagination"), data.get("index")
    )
    coverage = data.get("source_coverage")
    if not all(isinstance(item, dict) for item in (overview, pagination, index)):
        raise CheckerError("Dependency projection response is missing public evidence fields.")
    if not isinstance(coverage, list) or len(coverage) > MAX_PROJECTION_CANDIDATES:
        raise CheckerError("Dependency source_coverage is missing or outside bounds.")

    def integer(mapping: dict[str, Any], name: str, maximum: int = 100_000) -> int:
        value = mapping.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
            raise CheckerError(f"Dependency public field {name} is missing or outside bounds.")
        return value

    normalized_coverage: list[dict[str, Any]] = []
    expected_sources = {"automation", "blueprint"}
    for item in coverage:
        source_type = item.get("source_type")
        if source_type not in expected_sources:
            continue
        completeness = item.get("completeness")
        ledger_completeness = item.get("obligation_ledger_completeness")
        fallback = item.get("fallback_occurred")
        if completeness not in {"complete", "partial", "unavailable", "unsupported"}:
            raise CheckerError("Dependency public completeness is missing or unsupported.")
        if ledger_completeness not in {
            "complete", "partial", "unavailable", "unsupported"
        }:
            raise CheckerError(
                "Dependency public obligation-ledger completeness is missing or unsupported."
            )
        if not isinstance(fallback, bool):
            raise CheckerError("Dependency public fallback evidence is missing.")
        normalized_coverage.append({
            "source_type": source_type,
            "completeness": completeness,
            "failed_item_count": integer(item, "failed_item_count"),
            "obligation_ledger_completeness": ledger_completeness,
            "obligation_ledger_failed_item_count": integer(
                item, "obligation_ledger_failed_item_count"
            ),
            "fallback_occurred": fallback,
        })
    if {item["source_type"] for item in normalized_coverage} != expected_sources:
        raise CheckerError("Dependency public coverage lacks automation or blueprint evidence.")
    if len(normalized_coverage) != len(expected_sources):
        raise CheckerError("Dependency public coverage contains duplicate source evidence.")
    normalized_coverage.sort(key=lambda item: item["source_type"])

    material = {
        "overview": {
            "coverage_complete": overview.get("coverage_complete"),
            "direct_reference_count": integer(overview, "direct_reference_count"),
            "indirect_reference_count": integer(overview, "indirect_reference_count"),
            "unique_source_count": integer(overview, "unique_source_count"),
            "unresolved_dynamic_reference_count": integer(
                overview, "unresolved_dynamic_reference_count"
            ),
        },
        "source_coverage": normalized_coverage,
        "pagination": {
            "total": integer(pagination, "total"),
            "has_more": pagination.get("has_more"),
        },
        "index": {
            "refreshed": index.get("refreshed"),
            "cache_hit": index.get("cache_hit"),
        },
    }
    if not all(
        isinstance(value, bool)
        for value in (
            material["overview"]["coverage_complete"],
            material["pagination"]["has_more"],
            material["index"]["refreshed"],
            material["index"]["cache_hit"],
        )
    ):
        raise CheckerError("Dependency public boolean evidence is missing.")
    summary = "; ".join(
        f"{item['source_type']}={item['completeness']}/"
        f"failed:{item['failed_item_count']}/"
        f"ledger:{item['obligation_ledger_completeness']}/"
        f"ledger_failed:{item['obligation_ledger_failed_item_count']}/"
        f"fallback:{str(item['fallback_occurred']).lower()}"
        for item in normalized_coverage
    )
    if len(summary.encode("utf-8")) > MAX_VALUE_BYTES:
        raise CheckerError("Dependency public summary exceeds its bound.")
    return {
        "projection.observable_coverage_summary": summary,
        "projection.observable_evidence_fingerprint": _canonical_fingerprint(
            "dependency_public_evidence_v2", material
        ),
        "projection.observable_dependency_source_count": material["overview"]["unique_source_count"],
    }


def _derive_f3_child_projection(
    source: Any, expected_parent_task_id: str, expected_child_count: int
) -> dict[str, Any]:
    """Project the exact bounded child lifecycle from get_execution_task."""
    if not isinstance(source, dict) or source.get("success") is not True:
        raise CheckerError("F3 child projection requires a successful public response.")
    data = source.get("data")
    if not isinstance(data, dict):
        raise CheckerError("F3 child projection response is missing data.")
    task_id = data.get("task_id")
    children = data.get("f3_children")
    if not isinstance(task_id, str) or re.fullmatch(r"[a-f0-9]{32}", task_id) is None:
        raise CheckerError("F3 child projection task identity is missing or malformed.")
    if task_id != expected_parent_task_id:
        raise CheckerError("F3 child projection does not match the expected parent task.")
    if not isinstance(children, list) or not 1 <= len(children) <= 8:
        raise CheckerError("F3 child projection child list is missing or outside bounds.")
    if len(children) != expected_child_count:
        raise CheckerError("F3 child projection child list is incomplete or unexpected.")

    normalized: list[dict[str, Any]] = []
    operations: set[str] = set()
    child_execution_ids: set[str] = set()
    ordinals: set[int] = set()
    for child in children:
        if not isinstance(child, dict):
            raise CheckerError("F3 child projection contains a malformed child.")
        operation_id = child.get("operation_id")
        child_execution_id = child.get("child_execution_id")
        ordinal = child.get("ordinal")
        state = child.get("state")
        outcome = child.get("normalized_outcome")
        dispatch_count = child.get("dispatch_count")
        if (
            not isinstance(child_execution_id, str)
            or re.fullmatch(r"[a-f0-9]{64}", child_execution_id) is None
            or child_execution_id in child_execution_ids
        ):
            raise CheckerError(
                "F3 child execution identity is missing, malformed, or duplicated."
            )
        if (
            not isinstance(operation_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", operation_id) is None
            or operation_id in operations
        ):
            raise CheckerError("F3 child operation identity is missing, malformed, or duplicated.")
        if (
            isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or not 0 <= ordinal < 8
            or ordinal in ordinals
        ):
            raise CheckerError("F3 child ordinal is missing, outside bounds, or duplicated.")
        if state not in _F3_CHILD_STATES:
            raise CheckerError("F3 child state is missing or unsupported.")
        if outcome is not None and outcome not in _F3_TERMINAL_STATES:
            raise CheckerError("F3 child normalized outcome is unsupported.")
        if (
            isinstance(dispatch_count, bool)
            or not isinstance(dispatch_count, int)
            or not 0 <= dispatch_count <= 1
        ):
            raise CheckerError("F3 child dispatch count is missing or outside bounds.")
        operations.add(operation_id)
        child_execution_ids.add(child_execution_id)
        ordinals.add(ordinal)
        normalized.append({
            "identity": {
                "public_task_id": task_id,
                "child_execution_id": child_execution_id,
                "operation_id": operation_id,
                "ordinal": ordinal,
            },
            "state": state,
            "normalized_outcome": outcome,
            "dispatch_count": dispatch_count,
        })
    if ordinals != set(range(len(children))):
        raise CheckerError("F3 child ordinals are not a complete bounded sequence.")
    normalized.sort(key=lambda item: item["identity"]["ordinal"])
    return {
        "projection.f3_child_count": len(normalized),
        "projection.f3_child_lifecycle_fingerprint": _canonical_fingerprint(
            "f3_child_lifecycle_v1", normalized
        ),
        "projection.f3_all_zero_dispatch": all(
            item["dispatch_count"] == 0 for item in normalized
        ),
        "projection.f3_all_terminal": all(
            item["state"] in _F3_TERMINAL_STATES for item in normalized
        ),
        "projection.f3_all_cancelled_pre_dispatch": all(
            item["state"] == "cancelled_pre_dispatch"
            and item["normalized_outcome"] == "cancelled_pre_dispatch"
            for item in normalized
        ),
    }


def _json_pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _derive_wait_template_projection(source: Any, pointer: str) -> dict[str, Any]:
    if not re.fullmatch(r"/action/(?:0|[1-9][0-9]*)", pointer):
        raise CheckerError("Wait-template contract has a malformed action pointer.")
    segments = pointer.lstrip("/").split("/")
    if not isinstance(source, dict):
        raise CheckerError("Wait-template projection source must be a mapping.")
    parent = source.get(segments[0])
    if not isinstance(parent, list):
        raise CheckerError("Wait-template action parent must be the action sequence.")
    direct_wait_actions = [
        candidate
        for candidate in parent
        if isinstance(candidate, dict) and "wait_template" in candidate
    ]
    if len(direct_wait_actions) != 1:
        raise CheckerError("Expected exactly one direct wait-template action.")
    index = int(segments[1])
    if index >= len(parent) or not isinstance(parent[index], dict):
        raise CheckerError("Expected wait-template action is missing or moved.")
    action = parent[index]
    if "wait_template" not in action or not isinstance(action["wait_template"], str):
        raise CheckerError("Expected action does not contain a string wait_template.")
    template = unicodedata.normalize(
        "NFC", action["wait_template"].replace("\r\n", "\n").replace("\r", "\n")
    )
    if len(template.encode("utf-8")) > MAX_WAIT_TEMPLATE_BYTES:
        raise CheckerError(f"wait_template exceeds the {MAX_WAIT_TEMPLATE_BYTES}-byte bound.")
    if _SENSITIVE_VALUE.search(template):
        raise CheckerError("wait_template resembles credential material.")
    preimage = (
        b"wait_template_structure_v1\0"
        + pointer.encode("utf-8")
        + b"\0"
        + template.encode("utf-8")
    )
    return {
        "projection.wait_template_count": 1,
        "projection.wait_template_action_path": pointer,
        "projection.wait_template_length": len(template.encode("utf-8")),
        "projection.wait_template_semantic_digest": "sha256:"
        + hashlib.sha256(preimage).hexdigest(),
    }


def derive_projection(
    manifest: dict[str, Any], observation_id: str, source: Any
) -> dict[str, Any]:
    contract = _projection_contract(manifest, observation_id)
    maximum = min(
        int(contract["maximum_source_bytes"]), MAX_PROJECTION_SOURCE_BYTES
    )
    if _encoded_size(source) > maximum:
        raise CheckerError(
            f"Projection source exceeds the {maximum}-byte contract bound."
        )
    source_errors = _projection_source_errors(source)
    if source_errors:
        raise CheckerError("Projection source is invalid: " + source_errors[0])
    algorithm = contract["algorithm"]
    if algorithm == "dependency_public_evidence_v2":
        projection = _derive_dependency_projection(source)
    elif algorithm == "f3_child_lifecycle_v1":
        projection = _derive_f3_child_projection(
            source,
            str(contract.get("expected_parent_task_id") or ""),
            int(contract.get("expected_child_count") or 0),
        )
    elif algorithm == "wait_template_structure_v1":
        projection = _derive_wait_template_projection(
            source, str(contract.get("expected_action_pointer") or "")
        )
    else:  # validated manifests cannot reach this branch
        raise CheckerError(f"Unsupported projection algorithm {algorithm!r}.")
    if set(projection) != set(contract["output_paths"]):
        raise CheckerError(
            f"Projection algorithm {algorithm!r} did not produce its exact declared outputs."
        )
    return projection


@dataclass(frozen=True)
class Resolution:
    found: bool
    value: Any = None
    reason: str = ""


def resolve_path(root: Any, path: str) -> Resolution:
    """Resolve a direct projected key or a dotted raw-value path."""

    if isinstance(root, dict) and path in root:
        return Resolution(True, root[path])
    current = root
    walked: list[str] = []
    for segment in path.split("."):
        walked.append(segment)
        location = ".".join(walked)
        selector = _SELECTOR.match(segment)
        if selector is not None:
            field_name = selector.group("field")
            if not isinstance(current, dict) or field_name not in current:
                return Resolution(False, reason=f"no field at {location}")
            candidates = current[field_name]
            if not isinstance(candidates, list):
                return Resolution(False, reason=f"{location} is not a list")
            key, wanted = selector.group("key"), selector.group("value")
            matches = [
                item
                for item in candidates
                if isinstance(item, dict) and str(item.get(key)) == wanted
            ]
            if len(matches) != 1:
                return Resolution(False, reason=f"{len(matches)} list items match {location}")
            current = matches[0]
        elif isinstance(current, list):
            if not segment.lstrip("-").isdigit():
                return Resolution(False, reason=f"{location} is not a list index")
            index = int(segment)
            if not -len(current) <= index < len(current):
                return Resolution(False, reason=f"index out of range at {location}")
            current = current[index]
        elif isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return Resolution(False, reason=f"no field at {location}")
    return Resolution(True, current)


def strict_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _safe_value(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        rendered = repr(value)
        if len(rendered) <= 160:
            return rendered
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"<{type(value).__name__}:sha256:{digest}>"


@dataclass(frozen=True)
class CapturedObservation:
    identifier: str
    tool: str
    arguments: dict[str, Any]
    status: str
    evidence: dict[str, Any]
    absent_paths: frozenset[str]
    reason: str = ""


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    conclusive: bool
    path: str
    operator: str
    detail: str


def _resolve_capture(observation: CapturedObservation, path: str) -> tuple[Resolution, bool]:
    resolved = resolve_path(observation.evidence, path)
    if resolved.found:
        return resolved, True
    if path in observation.absent_paths:
        return Resolution(False, reason="captured as absent"), True
    return Resolution(False, reason="required projected field was not captured"), False


def evaluate_check(
    check: dict[str, Any],
    observation: CapturedObservation,
    observations: dict[str, CapturedObservation],
) -> CheckResult:
    path, operator = check["path"], check["operator"]
    resolved, conclusive = _resolve_capture(observation, path)
    if not conclusive:
        return CheckResult(False, False, path, operator, resolved.reason)
    if operator == "absent":
        passed = not resolved.found or resolved.value is None
        return CheckResult(passed, True, path, operator, "absent" if passed else "present")
    if not resolved.found:
        return CheckResult(False, True, path, operator, "captured field is absent")
    observed = resolved.value
    if operator == "present":
        return CheckResult(observed is not None, True, path, operator, "present")
    if operator == "equals":
        expected = check["value"]
        passed = strict_equal(observed, expected)
        return CheckResult(
            passed,
            True,
            path,
            operator,
            f"expected {_safe_value(expected)}, observed {_safe_value(observed)}",
        )
    if operator == "not_equals":
        expected = check["value"]
        passed = not strict_equal(observed, expected)
        return CheckResult(passed, True, path, operator, "values differ" if passed else "values match")
    if operator == "one_of":
        allowed = check["values"]
        passed = any(strict_equal(observed, item) for item in allowed)
        return CheckResult(
            passed,
            True,
            path,
            operator,
            f"expected one of {_safe_value(allowed)}, observed {_safe_value(observed)}",
        )
    if operator in {"gte", "lte", "gt", "lt"}:
        left, right = _numeric(observed), _numeric(check["value"])
        if left is None or right is None:
            return CheckResult(False, True, path, operator, "numeric comparison required")
        passed = {"gte": left >= right, "lte": left <= right, "gt": left > right, "lt": left < right}[operator]
        return CheckResult(passed, True, path, operator, f"observed {left} against {right}")
    if operator == "matches":
        pattern = check["value"]
        passed = isinstance(observed, str) and isinstance(pattern, str) and re.fullmatch(pattern, observed) is not None
        return CheckResult(passed, True, path, operator, "pattern matched" if passed else "pattern did not match")
    if operator == "equals_observation_path":
        other = observations.get(check["observation"])
        if other is None or other.status != "captured":
            return CheckResult(False, False, path, operator, "reference observation not captured")
        reference, reference_conclusive = _resolve_capture(other, check["reference_path"])
        if not reference_conclusive:
            return CheckResult(False, False, path, operator, "reference field not captured")
        if not reference.found:
            return CheckResult(False, True, path, operator, "reference field is absent")
        passed = strict_equal(observed, reference.value)
        return CheckResult(passed, True, path, operator, "cross-observation values match" if passed else "cross-observation values differ")
    if operator == "equals_invocation_argument":
        argument = resolve_path(observation.arguments, check["invocation_argument"])
        if not argument.found:
            raise CheckerError(
                f"Invocation argument {check['invocation_argument']!r} was not captured."
            )
        passed = strict_equal(observed, argument.value)
        return CheckResult(passed, True, path, operator, "matches invocation" if passed else "does not match invocation")
    raise CheckerError(f"Unsupported operator {operator!r} on path {path!r}.")


def classify(
    expected_status: str,
    desired_pass: bool,
    known_failure_pass: bool | None = None,
    *,
    conclusive: bool = True,
) -> str:
    if not conclusive:
        return NOT_CAPTURED
    if expected_status == "expected_pass":
        return CONFIRMED if desired_pass else REGRESSION
    if expected_status == "expected_fail":
        if desired_pass:
            return UNEXPECTED_PASS
        return KNOWN_FAILING if known_failure_pass is True else REGRESSION
    raise CheckerError(f"Unsupported expected_status {expected_status!r}.")


@dataclass
class SentinelResult:
    sentinel_id: str
    title: str
    outcome: str
    expected_status: str
    observation: str
    desired_checks: list[CheckResult] = field(default_factory=list)
    known_failure_checks: list[CheckResult] = field(default_factory=list)
    deficiency: dict[str, Any] | None = None
    promotion_disposition: str | None = None
    availability: str = "captured"
    note: str = ""

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [
            item
            for item in self.desired_checks + self.known_failure_checks
            if not item.passed
        ]


@dataclass
class Report:
    manifest_target: dict[str, Any]
    capture_metadata: dict[str, Any]
    results: list[SentinelResult]

    def by_outcome(self, outcome: str) -> list[SentinelResult]:
        return [item for item in self.results if item.outcome == outcome]

    @property
    def counts(self) -> dict[str, int]:
        return {outcome: len(self.by_outcome(outcome)) for outcome in OUTCOME_ORDER}

    @property
    def regression_present(self) -> bool:
        return bool(self.counts[REGRESSION])

    @property
    def evidence_complete(self) -> bool:
        return not bool(self.counts[NOT_CAPTURED])

    @property
    def review_required(self) -> bool:
        return bool(self.counts[UNEXPECTED_PASS])

    @property
    def blocking_known_failures(self) -> list[SentinelResult]:
        return [
            item
            for item in self.results
            if item.outcome == KNOWN_FAILING
            and item.promotion_disposition == BLOCKING
        ]

    @property
    def blocking_known_failure_count(self) -> int:
        return len(self.blocking_known_failures)

    @property
    def blocking_known_failure_present(self) -> bool:
        return bool(self.blocking_known_failure_count)

    @property
    def blocking_unverified_requirements(self) -> list[SentinelResult]:
        return [
            item
            for item in self.results
            if item.outcome == NOT_CAPTURED
            and item.promotion_disposition == BLOCKING
        ]

    @property
    def blocking_unverified_requirement_count(self) -> int:
        return len(self.blocking_unverified_requirements)

    @property
    def blocking_unverified_requirement_present(self) -> bool:
        return bool(self.blocking_unverified_requirement_count)

    @property
    def unresolved_blocking_requirements(self) -> list[SentinelResult]:
        return [
            item
            for item in self.results
            if item.promotion_disposition == BLOCKING
            and item.outcome != CONFIRMED
        ]

    @property
    def promotion_eligible(self) -> bool:
        return (
            self.evidence_complete
            and not self.regression_present
            and not self.review_required
            and not self.blocking_known_failure_present
            and not self.blocking_unverified_requirement_present
        )


def _captured_observations(capture: dict[str, Any]) -> dict[str, CapturedObservation]:
    values: dict[str, CapturedObservation] = {}
    for identifier, entry in capture["observations"].items():
        values[identifier] = CapturedObservation(
            identifier=identifier,
            tool=entry["tool"],
            arguments=dict(entry["arguments"]),
            status=entry["status"],
            evidence=dict(entry.get("evidence") or {}),
            absent_paths=frozenset(entry.get("absent_paths") or ()),
            reason=str(entry.get("not_recorded_reason") or ""),
        )
    return values


def evaluate(
    manifest: dict[str, Any],
    capture: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> Report:
    schema = schema or load_json(DEFAULT_SCHEMA, "schema")
    errors = validate_capture(manifest, capture, schema)
    if errors:
        raise CheckerError("Capture is invalid:\n  - " + "\n  - ".join(errors[:30]))
    observations = _captured_observations(capture)
    results: list[SentinelResult] = []
    for sentinel in manifest["sentinels"]:
        primary = observations.get(sentinel["observation"])
        if primary is None or primary.status != "captured":
            results.append(
                SentinelResult(
                    sentinel_id=sentinel["id"],
                    title=sentinel["title"],
                    outcome=NOT_CAPTURED,
                    expected_status=sentinel["expected_status"],
                    observation=sentinel["observation"],
                    deficiency=sentinel.get("deficiency"),
                    promotion_disposition=sentinel.get("promotion_disposition"),
                    availability="not_captured",
                    note=(primary.reason if primary else "observation entry is missing"),
                )
            )
            continue

        def run(check: dict[str, Any]) -> CheckResult:
            source = observations.get(
                check.get("source_observation", sentinel["observation"])
            )
            if source is None or source.status != "captured":
                return CheckResult(
                    False,
                    False,
                    check["path"],
                    check["operator"],
                    "required observation not captured",
                )
            return evaluate_check(check, source, observations)

        desired = [run(check) for check in sentinel["desired_checks"]]
        known = [run(check) for check in sentinel.get("known_failure_checks") or []]
        desired_conclusive = all(item.conclusive for item in desired)
        known_conclusive = all(item.conclusive for item in known)
        desired_pass = all(item.passed for item in desired)
        known_pass = all(item.passed for item in known) if known else None
        # Once every desired check conclusively passes, the old failure
        # signature is irrelevant: this is an unexpected pass that requires
        # human confirmation.  Known-failure evidence is required only when an
        # expected-fail sentinel still fails its desired contract.
        conclusive = desired_conclusive and (
            sentinel["expected_status"] != "expected_fail"
            or desired_pass
            or known_conclusive
        )
        incomplete_note = ""
        if not conclusive:
            source_ids = {
                check.get("source_observation", sentinel["observation"])
                for check in _all_checks(sentinel)
            }
            reasons = sorted({
                observations[source_id].reason
                for source_id in source_ids
                if source_id in observations
                and observations[source_id].status != "captured"
                and observations[source_id].reason
            })
            incomplete_note = (
                "; ".join(reasons)[:500]
                if reasons
                else "required projected evidence is incomplete"
            )
        results.append(
            SentinelResult(
                sentinel_id=sentinel["id"],
                title=sentinel["title"],
                outcome=classify(
                    sentinel["expected_status"],
                    desired_pass,
                    known_pass,
                    conclusive=conclusive,
                ),
                expected_status=sentinel["expected_status"],
                observation=sentinel["observation"],
                desired_checks=desired,
                known_failure_checks=known,
                deficiency=sentinel.get("deficiency"),
                promotion_disposition=sentinel.get("promotion_disposition"),
                availability="captured" if conclusive else "not_captured",
                note=incomplete_note,
            )
        )
    canary_capture = capture.get("canaries") or {}
    for canary in manifest.get("separately_authorized_canaries") or []:
        if not canary.get("required_for_promotion", False):
            continue
        entry = canary_capture.get(canary["id"])
        if not isinstance(entry, dict) or entry.get("status") != "captured":
            results.append(
                SentinelResult(
                    sentinel_id="canary:" + canary["id"],
                    title=canary["title"],
                    outcome=NOT_CAPTURED,
                    expected_status="expected_pass",
                    observation=canary["id"],
                    deficiency=canary.get("deficiency"),
                    promotion_disposition=canary.get("promotion_disposition"),
                    availability=str(canary.get("availability") or "unknown"),
                    note=(
                        str((entry or {}).get("not_recorded_reason") or "required separately authorized canary not captured")
                    ),
                )
            )
            continue
        captured = CapturedObservation(
            identifier=canary["id"],
            tool=entry["tool"],
            arguments=dict(entry["arguments"]),
            status="captured",
            evidence=dict(entry.get("evidence") or {}),
            absent_paths=frozenset(entry.get("absent_paths") or ()),
            reason="",
        )
        desired = [evaluate_check(check, captured, {canary["id"]: captured}) for check in canary["desired_checks"]]
        conclusive = all(item.conclusive for item in desired)
        results.append(
            SentinelResult(
                sentinel_id="canary:" + canary["id"],
                title=canary["title"],
                outcome=classify(
                    "expected_pass", all(item.passed for item in desired), conclusive=conclusive
                ),
                expected_status="expected_pass",
                observation=canary["id"],
                desired_checks=desired,
                deficiency=canary.get("deficiency"),
                promotion_disposition=canary.get("promotion_disposition"),
                availability=str(canary.get("availability") or "unknown"),
                note=("required canary evidence is incomplete" if not conclusive else ""),
            )
        )
    return Report(
        manifest_target=dict(manifest.get("target") or {}),
        capture_metadata={
            key: capture[key]
            for key in (
                "capture_schema_version",
                "manifest_version",
                "manifest_digest",
                "captured_at",
                "captured_by",
                "session_id",
                "target",
            )
        },
        results=results,
    )


def exit_code(report: Report) -> int:
    if report.regression_present:
        return EXIT_REGRESSION
    if not report.promotion_eligible:
        return EXIT_INCOMPLETE
    return EXIT_OK


def _one_line(value: Any) -> str:
    return " ".join(str(value).split())[:320]


def render_text(report: Report) -> str:
    target = report.manifest_target
    lines = [
        "Promotion regression check",
        f"  manifest target : {target.get('release', 'unknown')}",
        f"  manifest build  : {target.get('build_sha', 'unknown')}",
        f"  captured at     : {report.capture_metadata.get('captured_at', 'unknown')}",
        f"  operator/session: {_one_line(report.capture_metadata.get('captured_by', ''))} / "
        f"{_one_line(report.capture_metadata.get('session_id', ''))}",
        "",
        "Summary",
    ]
    counts = report.counts
    for outcome in OUTCOME_ORDER:
        lines.append(f"  {outcome:<15} {counts[outcome]:>3}")
    lines.append(
        f"  {'BLOCKING_KNOWN':<15} {report.blocking_known_failure_count:>3}"
    )
    lines.append(
        f"  {'BLOCKING_UNVERIFIED':<19} "
        f"{report.blocking_unverified_requirement_count:>3}"
    )
    for outcome in OUTCOME_ORDER:
        entries = report.by_outcome(outcome)
        if not entries:
            continue
        lines.extend(("", f"--- {OUTCOME_HEADINGS[outcome]}"))
        for entry in entries:
            lines.append(f"  [{entry.sentinel_id}] {entry.title}")
            if entry.deficiency:
                lines.append(
                    f"      deficiency #{entry.deficiency.get('register_item')}: "
                    f"{_one_line(entry.deficiency.get('summary', ''))}"
                )
            if entry.promotion_disposition:
                lines.append(
                    f"      promotion disposition: {entry.promotion_disposition}"
                )
            lines.append(f"      evidence availability: {entry.availability}")
            if (
                entry.promotion_disposition == BLOCKING
                and entry.outcome != CONFIRMED
            ):
                lines.append("      independently prevents promotion: true")
            if entry.note:
                lines.append(f"      {_one_line(entry.note)}")
            for check in entry.failed_checks[:MAX_FAILED_CHECKS_RENDERED]:
                lines.append(
                    f"      failed {check.path} [{check.operator}]: {_one_line(check.detail)}"
                )
    lines.append("")
    if report.regression_present:
        lines.append("Result: regression present; promotion is not eligible.")
    elif not report.evidence_complete:
        if report.blocking_unverified_requirement_present:
            lines.append(
                "Result: blocking requirements are unverified; promotion is not eligible."
            )
        else:
            lines.append("Result: required evidence is incomplete; promotion is not eligible.")
    elif report.review_required:
        lines.append("Result: human review is required; promotion is not eligible.")
    elif report.blocking_known_failure_present:
        lines.append(
            "Result: exact known promotion blockers remain; promotion is not eligible."
        )
    else:
        lines.append("Result: complete accepted operator-attested evidence; promotion eligible.")
    if counts[UNEXPECTED_PASS]:
        lines.append(
            "Unexpected passes require human confirmation and a reviewed manifest update; "
            "the checker does not change status automatically."
        )
    rendered = "\n".join(lines)
    return rendered[:MAX_REPORT_BYTES]


def render_json(report: Report) -> str:
    value = {
        "manifest_target": {
            key: report.manifest_target.get(key) for key in ("release", "tag", "build_sha")
        },
        "capture": report.capture_metadata,
        "counts": report.counts,
        "regression_present": report.regression_present,
        "evidence_complete": report.evidence_complete,
        "review_required": report.review_required,
        "blocking_known_failure_present": report.blocking_known_failure_present,
        "blocking_known_failure_count": report.blocking_known_failure_count,
        "blocking_known_failure_ids": [
            item.sentinel_id for item in report.blocking_known_failures
        ],
        "blocking_unverified_requirement_present": (
            report.blocking_unverified_requirement_present
        ),
        "blocking_unverified_requirement_count": (
            report.blocking_unverified_requirement_count
        ),
        "blocking_unverified_requirement_ids": [
            item.sentinel_id for item in report.blocking_unverified_requirements
        ],
        "unresolved_blocking_requirement_ids": [
            item.sentinel_id for item in report.unresolved_blocking_requirements
        ],
        "promotion_eligible": report.promotion_eligible,
        "promotion_blocked": not report.promotion_eligible,
        "run_complete": report.evidence_complete,
        "sentinels": [
            {
                "id": entry.sentinel_id,
                "title": entry.title,
                "outcome": entry.outcome,
                "expected_status": entry.expected_status,
                "promotion_disposition": entry.promotion_disposition,
                "availability": entry.availability,
                "independently_prevents_promotion": (
                    entry.promotion_disposition == BLOCKING
                    and entry.outcome != CONFIRMED
                ),
                "observation": entry.observation,
                "deficiency": entry.deficiency,
                "note": entry.note,
                "failed_checks": [
                    {
                        "path": check.path,
                        "operator": check.operator,
                        "detail": _one_line(check.detail),
                    }
                    for check in entry.failed_checks[:MAX_FAILED_CHECKS_RENDERED]
                ],
            }
            for entry in report.results
        ],
    }
    rendered = json.dumps(value, indent=2)
    if len(rendered.encode("utf-8")) > MAX_REPORT_BYTES:
        raise CheckerError("Bounded report size was exceeded.")
    return rendered


def render_plan(manifest: dict[str, Any]) -> str:
    users: dict[str, list[str]] = {}
    for sentinel in manifest["sentinels"]:
        for check in _all_checks(sentinel):
            users.setdefault(
                check.get("source_observation", sentinel["observation"]), []
            ).append(sentinel["id"])
            if check.get("operator") == "equals_observation_path":
                users.setdefault(check["observation"], []).append(sentinel["id"])
    paths = _required_paths(manifest)
    projection_contracts = {
        item["observation"]: item for item in manifest.get("projection_contracts") or []
    }
    target = manifest.get("target") or {}
    lines = [
        "Promotion regression observations (manual, separately authorized, read-only)",
        f"  target: {target.get('release')} @ {target.get('build_sha')}",
        "  manifest digest: " + canonical_manifest_digest(manifest),
        "",
        "Record only the allowlisted projected fields. Do not retain complete raw responses.",
        "",
    ]
    for index, observation in enumerate(manifest["observations"], start=1):
        lines.append(f"{index}. {observation['id']} -> {observation['tool']}")
        lines.append(
            "     fixed arguments   : "
            + json.dumps(observation.get("arguments") or {}, sort_keys=True)
        )
        if observation.get("operator_arguments"):
            lines.append(
                "     operator arguments: "
                + ", ".join(sorted(observation["operator_arguments"]))
            )
        lines.append("     effect class      : read_only")
        lines.append("     procedure         : " + _one_line(observation["procedure"]))
        projection_contract = projection_contracts.get(observation["id"])
        if projection_contract is not None:
            lines.append(
                "     projection        : "
                f"{projection_contract['algorithm']} (contract v{projection_contract['version']})"
            )
            if projection_contract["evidence_availability"] == "available":
                lines.append(
                    "     derive offline    : python scripts/promotion_regression_check.py "
                    f"project --observation {observation['id']} --source /path/outside/repo/source.json"
                )
            else:
                lines.append(
                    "     evidence status   : "
                    + _one_line(projection_contract["unavailable_reason"])
                )
        lines.append("     capture paths     :")
        lines.extend(f"       - {path}" for path in sorted(paths[observation["id"]]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_template(manifest: dict[str, Any]) -> str:
    observations: dict[str, Any] = {}
    for observation in manifest["observations"]:
        arguments = dict(observation.get("arguments") or {})
        projection_contract = next(
            (
                item
                for item in manifest.get("projection_contracts") or []
                if item.get("observation") == observation["id"]
            ),
            None,
        )
        reason = "REPLACE-WITH-REASON-OR-CAPTURED-EVIDENCE"
        if (
            projection_contract is not None
            and projection_contract.get("evidence_availability")
            == "unavailable_pending_authoritative_capture"
        ):
            reason = projection_contract["unavailable_reason"]
        observations[observation["id"]] = {
            "observation_id": observation["id"],
            "tool": observation["tool"],
            "arguments": arguments,
            "status": "not_captured",
            "not_recorded_reason": reason,
        }
    return json.dumps(
        {
            "capture_schema_version": CAPTURE_SCHEMA_VERSION,
            "manifest_version": manifest["manifest_version"],
            "manifest_digest": canonical_manifest_digest(manifest),
            "captured_at": "REPLACE-WITH-UTC-TIMESTAMP",
            "captured_by": "REPLACE-WITH-OPERATOR",
            "session_id": "REPLACE-WITH-SESSION",
            "target": {
                "release": manifest["target"]["release"],
                "build_sha": manifest["target"]["build_sha"],
            },
            "observations": observations,
            "canaries": {
                canary["id"]: {
                    "canary_id": canary["id"],
                    "tool": canary["tool"],
                    "arguments": dict(canary.get("fixed_arguments") or {}),
                    "status": "not_captured",
                    "not_recorded_reason": "SEPARATE-AUTHORIZATION-AND-EVIDENCE-REQUIRED",
                }
                for canary in manifest.get("separately_authorized_canaries") or []
                if canary.get("required_for_promotion", False)
            },
        },
        indent=2,
        sort_keys=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promotion_regression_check.py",
        description="Validate and classify operator-supplied promotion evidence offline.",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    commands.add_parser("plan")
    commands.add_parser("template")
    project_parser = commands.add_parser("project")
    project_parser.add_argument("--observation", required=True)
    project_parser.add_argument("--source", type=Path, required=True)
    evaluate_parser = commands.add_parser("evaluate")
    evaluate_parser.add_argument("--capture", type=Path, required=True)
    evaluate_parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(arguments.manifest)
        schema = load_json(arguments.schema, "schema")
        errors = validate_manifest(manifest, schema)
        if errors:
            raise CheckerError("Manifest is invalid:\n  - " + "\n  - ".join(errors[:40]))
        if arguments.command == "validate":
            print(
                f"Manifest is valid: {len(manifest['observations'])} observations, "
                f"{len(manifest['sentinels'])} sentinels, "
                f"digest {canonical_manifest_digest(manifest)}."
            )
            return EXIT_OK
        if arguments.command == "plan":
            print(render_plan(manifest), end="")
            return EXIT_OK
        if arguments.command == "template":
            print(render_template(manifest))
            return EXIT_OK
        if arguments.command == "project":
            contract = _projection_contract(manifest, arguments.observation)
            maximum = min(
                int(contract["maximum_source_bytes"]), MAX_PROJECTION_SOURCE_BYTES
            )
            source = load_json(
                arguments.source, "projection source", maximum_bytes=maximum
            )
            print(
                json.dumps(
                    derive_projection(manifest, arguments.observation, source),
                    indent=2,
                    sort_keys=True,
                )
            )
            return EXIT_OK
        capture = load_json(arguments.capture, "capture", maximum_bytes=MAX_CAPTURE_BYTES)
        if not isinstance(capture, dict):
            raise CheckerError("Capture must be a JSON object.")
        report = evaluate(manifest, capture, schema)
        print(render_json(report) if arguments.format == "json" else render_text(report))
        return exit_code(report)
    except CheckerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
