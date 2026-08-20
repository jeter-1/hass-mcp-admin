#!/usr/bin/env python3
"""Classify a promotion regression capture against the versioned manifest.

This checker is deliberately incapable of contacting anything. It reads
``promotion/promotion_regression_manifest.yaml`` and a capture file recorded by
whoever holds live MCP access, then classifies every sentinel as CONFIRMED,
REGRESSION, KNOWN_FAILING, UNEXPECTED_PASS, or NOT_CAPTURED.

Keeping dispatch out of this process is the point: the register requires the
promotion checks to be read-only against the live target, and a program with no
transport cannot write by mistake. The manifest carries the exact call to make;
a human or an interactive agent session makes it, records the raw responses, and
this program does the arithmetic.

Commands
    validate   Structurally validate the manifest against its JSON schema.
    plan       Print the ordered observations to perform, with their procedures.
    template   Print an empty capture skeleton on stdout.
    evaluate   Classify a capture file and print the outcome.

Exit codes
    0  every sentinel was captured and no REGRESSION was found
    1  at least one REGRESSION: promotion must not proceed
    2  no REGRESSION, but the run is incomplete (a sentinel was NOT_CAPTURED)
    3  usage error, unreadable input, or an invalid manifest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
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

OUTCOME_ORDER = (
    REGRESSION,
    NOT_CAPTURED,
    UNEXPECTED_PASS,
    KNOWN_FAILING,
    CONFIRMED,
)

OUTCOME_HEADINGS = {
    REGRESSION: "REGRESSION - previously accepted behavior broke. Blocks promotion.",
    NOT_CAPTURED: (
        "NOT_CAPTURED - the check was not performed. The run is incomplete; "
        "this is not a regression."
    ),
    UNEXPECTED_PASS: (
        "UNEXPECTED_PASS - a known-failing sentinel passed. A human must "
        "confirm the fix and flip its status in the manifest."
    ),
    KNOWN_FAILING: (
        "KNOWN_FAILING - a tracked open deficiency still fails. Informational; "
        "not a promotion blocker by itself."
    ),
    CONFIRMED: "CONFIRMED - expected to pass, and it passed.",
}

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_INCOMPLETE = 2
EXIT_USAGE = 3

_SELECTOR = re.compile(r"^(?P<field>[^\[\]]+)\[(?P<key>[^=\[\]]+)=(?P<value>[^\[\]]*)\]$")


class CheckerError(RuntimeError):
    """A manifest, capture, or invocation problem that stops the run."""


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def load_manifest(path: Path) -> dict[str, Any]:
    """Load the YAML manifest without executing anything inside it."""

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment problem
        raise CheckerError(
            "PyYAML is required to read the manifest. Install it with "
            "'python -m pip install -r hass_mcp_engineering_beta/requirements.txt'."
        ) from exc
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckerError(f"Cannot read manifest {path}: {exc}") from exc
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CheckerError(f"Manifest {path} is not valid YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise CheckerError(f"Manifest {path} must be a mapping.")
    return value


def load_json(path: Path, label: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckerError(f"Cannot read {label} {path}: {exc}") from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CheckerError(f"{label} {path} is not valid JSON: {exc}") from exc


def validate_manifest(manifest: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Return schema errors plus the cross-references the schema cannot express."""

    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - environment problem
        raise CheckerError(
            "jsonschema is required to validate the manifest. Install it with "
            "'python -m pip install -r hass_mcp_engineering_beta/requirements.txt'."
        ) from exc
    validator = jsonschema.Draft202012Validator(schema)
    errors = [
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(manifest), key=lambda item: list(item.path))
    ]
    errors.extend(_reference_errors(manifest))
    return errors


def _reference_errors(manifest: dict[str, Any]) -> list[str]:
    observations = manifest.get("observations")
    sentinels = manifest.get("sentinels")
    if not isinstance(observations, list) or not isinstance(sentinels, list):
        return []
    known: set[str] = set()
    errors: list[str] = []
    for observation in observations:
        identifier = observation.get("id")
        if identifier in known:
            errors.append(f"observations: duplicate observation id {identifier!r}.")
        known.add(identifier)
    seen_sentinels: set[str] = set()
    referenced: set[str] = set()
    for sentinel in sentinels:
        identifier = sentinel.get("id")
        if identifier in seen_sentinels:
            errors.append(f"sentinels: duplicate sentinel id {identifier!r}.")
        seen_sentinels.add(identifier)
        target = sentinel.get("observation")
        referenced.add(target)
        if target not in known:
            errors.append(
                f"sentinels/{identifier}: observation {target!r} is not declared."
            )
        for index, check in enumerate(sentinel.get("checks") or []):
            reference = check.get("observation")
            if reference is None:
                continue
            referenced.add(reference)
            if reference not in known:
                errors.append(
                    f"sentinels/{identifier}/checks/{index}: observation "
                    f"{reference!r} is not declared."
                )
    for identifier in sorted(known - referenced):
        errors.append(f"observations/{identifier}: no sentinel uses this observation.")
    return errors


# --------------------------------------------------------------------------
# Path resolution
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    found: bool
    value: Any = None
    reason: str = ""


def resolve_path(root: Any, path: str) -> Resolution:
    """Resolve a dotted path with optional ``field[key=value]`` list selectors."""

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
            key = selector.group("key")
            wanted = selector.group("value")
            matches = [
                item
                for item in candidates
                if isinstance(item, dict) and str(item.get(key)) == wanted
            ]
            if not matches:
                return Resolution(False, reason=f"no list item matching {location}")
            if len(matches) > 1:
                return Resolution(
                    False, reason=f"{len(matches)} list items match {location}"
                )
            current = matches[0]
            continue
        if isinstance(current, list):
            if not segment.lstrip("-").isdigit():
                return Resolution(False, reason=f"{location} is not a list index")
            index = int(segment)
            if not -len(current) <= index < len(current):
                return Resolution(False, reason=f"index out of range at {location}")
            current = current[index]
            continue
        if not isinstance(current, dict) or segment not in current:
            return Resolution(False, reason=f"no field at {location}")
        current = current[segment]
    return Resolution(True, current)


# --------------------------------------------------------------------------
# Predicates
# --------------------------------------------------------------------------


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def strict_equal(left: Any, right: Any) -> bool:
    """Compare without Python's bool/int equivalence.

    ``0 == False`` and ``1 == True`` are true in Python, which would let a
    counter of 1 satisfy a check expecting ``true``. Promotion evidence must not
    be that forgiving.
    """

    if _is_bool(left) != _is_bool(right):
        return False
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _numeric(value: Any) -> float | None:
    if _is_bool(value) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    path: str
    operator: str
    detail: str


def evaluate_check(
    check: dict[str, Any],
    response: Any,
    responses_by_observation: dict[str, Any],
) -> CheckResult:
    """Evaluate one manifest check against one recorded response."""

    path = check["path"]
    operator = check["operator"]
    resolved = resolve_path(response, path)

    if operator == "absent":
        passed = not resolved.found or resolved.value is None
        return CheckResult(
            passed,
            path,
            operator,
            "absent" if passed else f"present with {resolved.value!r}",
        )

    if not resolved.found:
        return CheckResult(False, path, operator, f"unresolved ({resolved.reason})")

    observed = resolved.value

    if operator == "present":
        passed = observed is not None
        return CheckResult(
            passed, path, operator, "present" if passed else "resolved to null"
        )

    if operator == "equals":
        expected = check["value"]
        passed = strict_equal(observed, expected)
        return CheckResult(
            passed, path, operator, f"expected {expected!r}, observed {observed!r}"
        )

    if operator == "not_equals":
        expected = check["value"]
        passed = not strict_equal(observed, expected)
        return CheckResult(
            passed, path, operator, f"must differ from {expected!r}, observed {observed!r}"
        )

    if operator == "one_of":
        allowed = check["values"]
        passed = any(strict_equal(observed, item) for item in allowed)
        return CheckResult(
            passed, path, operator, f"expected one of {allowed!r}, observed {observed!r}"
        )

    if operator in {"gte", "lte", "gt", "lt"}:
        left = _numeric(observed)
        right = _numeric(check["value"])
        if left is None or right is None:
            return CheckResult(
                False,
                path,
                operator,
                f"needs two numbers, observed {observed!r} against {check['value']!r}",
            )
        comparison = {
            "gte": left >= right,
            "lte": left <= right,
            "gt": left > right,
            "lt": left < right,
        }[operator]
        return CheckResult(
            comparison, path, operator, f"observed {observed!r} against {check['value']!r}"
        )

    if operator == "matches":
        pattern = check["value"]
        if not isinstance(observed, str) or not isinstance(pattern, str):
            return CheckResult(
                False, path, operator, f"needs a string, observed {observed!r}"
            )
        passed = re.fullmatch(pattern, observed) is not None
        return CheckResult(
            passed, path, operator, f"pattern {pattern!r} against {observed!r}"
        )

    if operator == "equals_observation_path":
        reference_observation = check["observation"]
        reference_path = check["reference_path"]
        if reference_observation not in responses_by_observation:
            return CheckResult(
                False,
                path,
                operator,
                f"reference observation {reference_observation!r} was not captured",
            )
        reference = resolve_path(
            responses_by_observation[reference_observation], reference_path
        )
        if not reference.found:
            return CheckResult(
                False,
                path,
                operator,
                f"reference {reference_observation}:{reference_path} unresolved "
                f"({reference.reason})",
            )
        passed = strict_equal(observed, reference.value)
        return CheckResult(
            passed,
            path,
            operator,
            f"observed {observed!r} against {reference_observation}:{reference_path} "
            f"= {reference.value!r}",
        )

    raise CheckerError(f"Unsupported operator {operator!r} on path {path!r}.")


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def classify(expected_status: str, observed_pass: bool) -> str:
    """Map an expected status and an observed result onto a register outcome."""

    if expected_status == "expected_pass":
        return CONFIRMED if observed_pass else REGRESSION
    if expected_status == "expected_fail":
        return UNEXPECTED_PASS if observed_pass else KNOWN_FAILING
    raise CheckerError(f"Unsupported expected_status {expected_status!r}.")


@dataclass
class SentinelResult:
    sentinel_id: str
    title: str
    outcome: str
    expected_status: str
    observation: str
    checks: list[CheckResult] = field(default_factory=list)
    deficiency: dict[str, Any] | None = None
    note: str = ""

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [item for item in self.checks if not item.passed]


@dataclass
class Report:
    manifest_target: dict[str, Any]
    capture_metadata: dict[str, Any]
    results: list[SentinelResult]
    unknown_observations: list[str] = field(default_factory=list)

    def by_outcome(self, outcome: str) -> list[SentinelResult]:
        return [item for item in self.results if item.outcome == outcome]

    @property
    def counts(self) -> dict[str, int]:
        return {outcome: len(self.by_outcome(outcome)) for outcome in OUTCOME_ORDER}


def _decode_response(raw: Any) -> Any:
    """Accept either a decoded object or the raw JSON string a tool returned."""

    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise CheckerError(
                f"A recorded response is a string that is not valid JSON: {exc}"
            ) from exc
    return raw


def collect_responses(
    capture: dict[str, Any], known: set[str]
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Split a capture into recorded responses, skip reasons, and unknown ids."""

    observations = capture.get("observations")
    if not isinstance(observations, dict):
        raise CheckerError("The capture file must contain an 'observations' mapping.")
    responses: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    unknown: list[str] = []
    for identifier, entry in observations.items():
        if identifier not in known:
            unknown.append(identifier)
            continue
        if not isinstance(entry, dict):
            raise CheckerError(
                f"Capture entry {identifier!r} must be a mapping with a "
                "'response' or a 'not_recorded_reason'."
            )
        if "response" in entry and entry["response"] is not None:
            responses[identifier] = _decode_response(entry["response"])
        else:
            skipped[identifier] = str(
                entry.get("not_recorded_reason") or "no reason recorded"
            )
    return responses, skipped, sorted(unknown)


def evaluate(manifest: dict[str, Any], capture: dict[str, Any]) -> Report:
    """Classify every sentinel in the manifest against one capture."""

    observations = {item["id"]: item for item in manifest["observations"]}
    responses, skipped, unknown = collect_responses(capture, set(observations))

    results: list[SentinelResult] = []
    for sentinel in manifest["sentinels"]:
        required = {sentinel["observation"]}
        for check in sentinel["checks"]:
            if check.get("observation"):
                required.add(check["observation"])
        missing = sorted(name for name in required if name not in responses)
        if missing:
            reasons = "; ".join(
                f"{name}: {skipped.get(name, 'not present in the capture')}"
                for name in missing
            )
            results.append(
                SentinelResult(
                    sentinel_id=sentinel["id"],
                    title=sentinel["title"],
                    outcome=NOT_CAPTURED,
                    expected_status=sentinel["expected_status"],
                    observation=sentinel["observation"],
                    deficiency=sentinel.get("deficiency"),
                    note=reasons,
                )
            )
            continue
        response = responses[sentinel["observation"]]
        checks = [
            evaluate_check(check, response, responses) for check in sentinel["checks"]
        ]
        observed_pass = all(item.passed for item in checks)
        results.append(
            SentinelResult(
                sentinel_id=sentinel["id"],
                title=sentinel["title"],
                outcome=classify(sentinel["expected_status"], observed_pass),
                expected_status=sentinel["expected_status"],
                observation=sentinel["observation"],
                checks=checks,
                deficiency=sentinel.get("deficiency"),
            )
        )

    return Report(
        manifest_target=dict(manifest.get("target") or {}),
        capture_metadata={
            key: capture.get(key)
            for key in ("capture_version", "captured_at", "captured_by", "target")
            if capture.get(key) is not None
        },
        results=results,
        unknown_observations=unknown,
    )


def exit_code(report: Report) -> int:
    counts = report.counts
    if counts[REGRESSION]:
        return EXIT_REGRESSION
    if counts[NOT_CAPTURED]:
        return EXIT_INCOMPLETE
    return EXIT_OK


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_text(report: Report) -> str:
    target = report.manifest_target
    labels = {key: f"capture {key}" for key in report.capture_metadata}
    width = max([len("manifest target"), *(len(item) for item in labels.values())])
    lines = [
        "Promotion regression check",
        f"  {'manifest target':<{width}} : {target.get('release', 'unknown')} "
        f"({target.get('tag', 'no tag')})",
        f"  {'manifest build':<{width}} : {target.get('build_sha', 'unknown')}",
    ]
    for key, value in sorted(report.capture_metadata.items()):
        lines.append(f"  {labels[key]:<{width}} : {value}")
    counts = report.counts
    lines.append("")
    lines.append("Summary")
    lines.append(f"  REGRESSION      {counts[REGRESSION]:>3}   promotion blocker")
    lines.append(f"  NOT_CAPTURED    {counts[NOT_CAPTURED]:>3}   run incomplete")
    lines.append(f"  UNEXPECTED_PASS {counts[UNEXPECTED_PASS]:>3}   needs a human status flip")
    lines.append(f"  KNOWN_FAILING   {counts[KNOWN_FAILING]:>3}   tracked, informational")
    lines.append(f"  CONFIRMED       {counts[CONFIRMED]:>3}")

    if report.unknown_observations:
        lines.append("")
        lines.append(
            "Capture contains observation ids the manifest does not declare: "
            + ", ".join(report.unknown_observations)
        )

    for outcome in OUTCOME_ORDER:
        entries = report.by_outcome(outcome)
        if not entries:
            continue
        lines.append("")
        lines.append(f"--- {OUTCOME_HEADINGS[outcome]}")
        for entry in entries:
            lines.append(f"  [{entry.sentinel_id}] {entry.title}")
            if entry.deficiency:
                register_item = entry.deficiency.get("register_item")
                related = entry.deficiency.get("related_register_items") or []
                related_text = (
                    f" (also #{', #'.join(str(item) for item in related)})"
                    if related
                    else ""
                )
                lines.append(
                    f"      deficiency #{register_item}{related_text}: "
                    f"{_one_line(entry.deficiency.get('summary', ''))}"
                )
            if entry.note:
                lines.append(f"      not captured: {entry.note}")
            for check in entry.failed_checks:
                lines.append(f"      failed {check.path} [{check.operator}]: {check.detail}")
    lines.append("")
    if counts[REGRESSION]:
        lines.append(
            "Result: REGRESSION present. Do not promote until each one is "
            "explained or fixed."
        )
    elif counts[NOT_CAPTURED]:
        lines.append(
            "Result: no regression found, but the run is incomplete. Capture "
            "the missing observations before treating this as a promotion gate."
        )
    else:
        lines.append("Result: complete run, no regression.")
    if counts[UNEXPECTED_PASS]:
        lines.append(
            "Note: an expected_fail sentinel passed. Confirm the underlying fix "
            "live, then flip its expected_status in the manifest by hand."
        )
    return "\n".join(lines)


def _one_line(value: str) -> str:
    return " ".join(str(value).split())


def render_json(report: Report) -> str:
    return json.dumps(
        {
            "manifest_target": report.manifest_target,
            "capture": report.capture_metadata,
            "counts": report.counts,
            "unknown_observations": report.unknown_observations,
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
                            "detail": check.detail,
                        }
                        for check in entry.failed_checks
                    ],
                }
                for entry in report.results
            ],
        },
        indent=2,
    )


def render_plan(manifest: dict[str, Any]) -> str:
    users: dict[str, list[str]] = {}
    for sentinel in manifest["sentinels"]:
        users.setdefault(sentinel["observation"], []).append(sentinel["id"])
    target = manifest.get("target") or {}
    lines = [
        "Promotion regression observations to perform",
        f"  target: {target.get('release', 'unknown')} ({target.get('tag', 'no tag')})",
        "",
        "Perform each call against the deployed server, then record the raw",
        "response in a capture file under observations.<id>.response.",
        "",
    ]
    for index, observation in enumerate(manifest["observations"], start=1):
        lines.append(f"{index}. {observation['id']}  ->  {observation['tool']}")
        arguments = observation.get("arguments") or {}
        rendered = json.dumps(arguments, sort_keys=True) if arguments else "{}"
        lines.append(f"     arguments    : {rendered}")
        lines.append(f"     effect class : {observation['effect_class']}")
        if observation.get("target_binding") == "operator_local":
            lines.append(
                f"     operator input: {_one_line(observation.get('target_note', ''))}"
            )
        if observation.get("precondition"):
            lines.append(
                f"     PRECONDITION : {_one_line(observation['precondition'])}"
            )
        lines.append(f"     procedure    : {_one_line(observation['procedure'])}")
        lines.append(
            "     sentinels    : " + ", ".join(users.get(observation["id"], []))
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_template(manifest: dict[str, Any]) -> str:
    return json.dumps(
        {
            "capture_version": 1,
            "captured_at": "REPLACE-WITH-UTC-TIMESTAMP",
            "captured_by": "REPLACE-WITH-OPERATOR-OR-SESSION",
            "target": {
                "release": (manifest.get("target") or {}).get("release"),
                "build_sha": (manifest.get("target") or {}).get("build_sha"),
            },
            "observations": {
                observation["id"]: {
                    "tool": observation["tool"],
                    "response": None,
                    "not_recorded_reason": "REPLACE-WITH-RESPONSE-OR-EXPLAIN",
                }
                for observation in manifest["observations"]
            },
        },
        indent=2,
    )


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promotion_regression_check.py",
        description=(
            "Classify a promotion regression capture against the versioned "
            "manifest. This program never contacts a live target."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Path to the manifest (default: promotion/promotion_regression_manifest.yaml).",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help="Path to the manifest JSON schema (default: promotion/manifest_schema.json).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="Validate the manifest structure.")
    subparsers.add_parser("plan", help="Print the observations to perform.")
    subparsers.add_parser("template", help="Print an empty capture skeleton.")
    evaluate_parser = subparsers.add_parser(
        "evaluate", help="Classify a recorded capture."
    )
    evaluate_parser.add_argument(
        "--capture", type=Path, required=True, help="Path to the capture JSON file."
    )
    evaluate_parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="Output format."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        manifest = load_manifest(arguments.manifest)
        schema = load_json(arguments.schema, "schema")
        errors = validate_manifest(manifest, schema)
        if errors:
            print(f"Manifest {arguments.manifest} is invalid:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            return EXIT_USAGE
        if arguments.command == "validate":
            print(
                f"Manifest {arguments.manifest} is valid: "
                f"{len(manifest['observations'])} observations, "
                f"{len(manifest['sentinels'])} sentinels."
            )
            return EXIT_OK
        if arguments.command == "plan":
            print(render_plan(manifest), end="")
            return EXIT_OK
        if arguments.command == "template":
            print(render_template(manifest))
            return EXIT_OK
        capture = load_json(arguments.capture, "capture")
        if not isinstance(capture, dict):
            raise CheckerError("The capture file must contain a JSON object.")
        report = evaluate(manifest, capture)
        print(render_json(report) if arguments.format == "json" else render_text(report))
        return exit_code(report)
    except CheckerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
