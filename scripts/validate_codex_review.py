"""Validate and render the bounded Codex independent-review verdict."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path, PurePosixPath
import sys


SEVERITIES = frozenset({"Critical", "High", "Medium", "Low"})
BLOCKING_SEVERITIES = frozenset({"Critical", "High"})
FINDING_FIELDS = frozenset(
    {
        "severity",
        "title",
        "path",
        "line",
        "evidence",
        "impact",
        "cause",
        "correction",
        "verification",
    }
)
TEXT_LIMITS = {
    "title": 300,
    "path": 500,
    "evidence": 4000,
    "impact": 2000,
    "cause": 2000,
    "correction": 2000,
    "verification": 2000,
}


class ReviewValidationError(RuntimeError):
    pass


def _bounded_text(value: object, *, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ReviewValidationError(f"{field} must be a string")
    if (not allow_empty and not value.strip()) or len(value) > TEXT_LIMITS[field]:
        raise ReviewValidationError(f"{field} is empty or exceeds its bound")
    return value


def _validate_path(value: object) -> str:
    path = _bounded_text(value, field="path", allow_empty=True)
    if not path:
        return path
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or "\\" in path:
        raise ReviewValidationError("finding path must be repository-relative")
    return path


def validate(payload: object) -> tuple[dict[str, object], list[dict[str, object]]]:
    if not isinstance(payload, dict) or set(payload) != {
        "verdict",
        "summary",
        "findings",
    }:
        raise ReviewValidationError("review must contain only verdict, summary, findings")
    verdict = payload["verdict"]
    if verdict not in {"pass", "fail"}:
        raise ReviewValidationError("verdict must be pass or fail")
    summary = payload["summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 4000:
        raise ReviewValidationError("summary is empty or exceeds its bound")
    findings = payload["findings"]
    if not isinstance(findings, list) or len(findings) > 20:
        raise ReviewValidationError("findings must be an array with at most 20 items")

    validated: list[dict[str, object]] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict) or set(finding) != FINDING_FIELDS:
            raise ReviewValidationError(f"finding {index} has an invalid field set")
        severity = finding["severity"]
        if severity not in SEVERITIES:
            raise ReviewValidationError(f"finding {index} has an invalid severity")
        line = finding["line"]
        if line is not None and (
            not isinstance(line, int) or isinstance(line, bool) or line < 1
        ):
            raise ReviewValidationError(f"finding {index} has an invalid line")
        item: dict[str, object] = {
            "severity": severity,
            "path": _validate_path(finding["path"]),
            "line": line,
        }
        for field in FINDING_FIELDS - {"severity", "path", "line"}:
            item[field] = _bounded_text(finding[field], field=field)
        validated.append(item)

    blockers = [
        finding
        for finding in validated
        if finding["severity"] in BLOCKING_SEVERITIES
    ]
    expected_verdict = "fail" if blockers else "pass"
    if verdict != expected_verdict:
        raise ReviewValidationError(
            "verdict is inconsistent with the Critical/High finding set"
        )
    normalized = {"verdict": verdict, "summary": summary, "findings": validated}
    return normalized, blockers


def _safe(value: object) -> str:
    return html.escape(str(value), quote=True).replace("\n", "<br>")


def render(payload: dict[str, object], blockers: list[dict[str, object]]) -> str:
    verdict = str(payload["verdict"]).upper()
    lines = [
        "## Codex independent review",
        "",
        f"**Verdict:** {verdict}",
        "",
        _safe(payload["summary"]),
        "",
    ]
    findings = payload["findings"]
    assert isinstance(findings, list)
    if not findings:
        lines.append("No findings.")
    for finding in findings:
        assert isinstance(finding, dict)
        location = str(finding["path"] or "repository")
        if finding["line"] is not None:
            location = f"{location}:{finding['line']}"
        lines.extend(
            [
                f"### {_safe(finding['severity'])}: {_safe(finding['title'])}",
                "",
                f"- Location: `{_safe(location)}`",
                f"- Evidence: {_safe(finding['evidence'])}",
                f"- Impact: {_safe(finding['impact'])}",
                f"- Cause: {_safe(finding['cause'])}",
                f"- Required correction: {_safe(finding['correction'])}",
                f"- Verification: {_safe(finding['verification'])}",
                "",
            ]
        )
    lines.append(f"Blocking findings: {len(blockers)}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = json.loads(args.review.read_text(encoding="utf-8"))
        validated, blockers = validate(payload)
        rendered = render(validated, blockers)
    except (OSError, json.JSONDecodeError, ReviewValidationError) as exc:
        print(f"Codex independent review is invalid: {exc}", file=sys.stderr)
        return 1

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8", newline="") as stream:
            stream.write(rendered)
    print(f"Codex independent review verdict: {validated['verdict']}")
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
