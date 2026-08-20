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
MAX_FAILED_CHECKS_RENDERED = 12

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
    allowed_paths = _required_paths(manifest)
    entries = capture.get("observations")
    if not isinstance(entries, dict):
        return errors
    unknown = sorted(set(entries) - set(declarations))
    if unknown:
        errors.append("observations: undeclared observation ids: " + ", ".join(unknown))
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
        expected_names = set(fixed) | set(local)
        if set(arguments) != expected_names:
            errors.append(
                f"observations/{identifier}: invocation arguments must be exactly "
                + ", ".join(sorted(expected_names))
                + "."
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
        if entry.get("status") != "captured":
            continue
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
    return errors


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
                note=("required projected evidence is incomplete" if not conclusive else ""),
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
    if report.counts[REGRESSION]:
        return EXIT_REGRESSION
    if report.counts[NOT_CAPTURED]:
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
            if entry.note:
                lines.append(f"      {_one_line(entry.note)}")
            for check in entry.failed_checks[:MAX_FAILED_CHECKS_RENDERED]:
                lines.append(
                    f"      failed {check.path} [{check.operator}]: {_one_line(check.detail)}"
                )
    lines.append("")
    if counts[REGRESSION]:
        lines.append("Result: regression present. Do not treat this capture as clean evidence.")
    elif counts[NOT_CAPTURED]:
        lines.append("Result: no regression found, but required evidence is not captured.")
    else:
        lines.append("Result: complete operator-attested capture; no regression detected.")
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
        "promotion_blocked": bool(report.counts[REGRESSION]),
        "run_complete": not report.counts[NOT_CAPTURED],
        "sentinels": [
            {
                "id": entry.sentinel_id,
                "title": entry.title,
                "outcome": entry.outcome,
                "expected_status": entry.expected_status,
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
        lines.append("     capture paths     :")
        lines.extend(f"       - {path}" for path in sorted(paths[observation["id"]]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _placeholder_for(schema: dict[str, Any], name: str) -> Any:
    if "const" in schema:
        return schema["const"]
    if schema.get("type") == "integer":
        return schema.get("minimum", 0)
    return f"REPLACE-WITH-{name.upper()}"


def render_template(manifest: dict[str, Any]) -> str:
    observations: dict[str, Any] = {}
    for observation in manifest["observations"]:
        arguments = dict(observation.get("arguments") or {})
        for name, argument_schema in (observation.get("operator_arguments") or {}).items():
            arguments[name] = _placeholder_for(argument_schema, name)
        observations[observation["id"]] = {
            "observation_id": observation["id"],
            "tool": observation["tool"],
            "arguments": arguments,
            "status": "not_captured",
            "not_recorded_reason": "REPLACE-WITH-REASON-OR-CAPTURED-EVIDENCE",
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
