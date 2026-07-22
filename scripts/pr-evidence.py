"""Generate a bounded, fact-derived draft pull-request evidence document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


MAX_CHANGED_FILES = 200
MAX_VALIDATION_STEPS = 100
MAX_FIELD_CHARS = 500
MAX_DOCUMENT_CHARS = 60_000
VALIDATION_SCHEMA_VERSION = 2
VALIDATION_TIER = "Evidence"
PASSED_EVIDENCE_STEPS = (
    "Verify base reference",
    "Bind clean Evidence snapshot",
    "Verify protected-path scope",
    "Compile repository Python",
    "Run complete unittest suite",
    "Validate add-on metadata",
    "Parse repository YAML",
    "Check installed dependency consistency",
    "Scan for high-confidence secret patterns",
    "Parse PowerShell syntax",
    "Check Git whitespace",
    "Check staged Git whitespace",
    "Check committed Git whitespace",
    "Check changed text whitespace",
    "Recheck Evidence snapshot",
)
PASSED_EVIDENCE_COVERAGE = (
    ("full_local_gate", "executed_locally"),
    ("docker_image_build", "delegated_to_ci"),
    ("multiarchitecture_build", "delegated_to_ci"),
    ("disposable_home_assistant", "delegated_to_ci"),
    ("exact_image", "delegated_to_ci"),
    ("publication", "not_applicable"),
    ("provenance", "delegated_to_ci"),
    ("deployment", "not_applicable"),
)
FAILED_EVIDENCE_COVERAGE = (
    ("full_local_gate", "failed_locally"),
    *PASSED_EVIDENCE_COVERAGE[1:],
)


class EvidenceError(RuntimeError):
    pass


def run_git(repo_root: Path, *args: str, required: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if required:
            raise EvidenceError("Git is unavailable for PR evidence") from exc
        return ""
    if result.returncode:
        if required:
            raise EvidenceError("Git could not determine required PR evidence")
        return ""
    return result.stdout.strip()


def clean_field(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    patterns = (
        r"github_pat_[A-Za-z0-9_]+",
        r"gh[pousr]_[A-Za-z0-9]+",
        r"sk-[A-Za-z0-9_-]{16,}",
        r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+",
    )
    for pattern in patterns:
        text = re.sub(pattern, lambda match: match.group(1) + "[redacted]" if match.lastindex else "[redacted]", text)
    return text[:MAX_FIELD_CHARS]


def changed_files(repo_root: Path, base: str, head: str) -> tuple[list[str], bool]:
    commands = (
        ("diff", "--name-only", f"{base}...{head}"),
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    )
    values: list[str] = []
    for command in commands:
        output = run_git(repo_root, *command, required=command[0] == "diff" and "..." in command[-1])
        values.extend(
            line.strip().replace("\\", "/")
            for line in output.splitlines()
            if line.strip()
        )
    unique = list(dict.fromkeys(values))
    return unique[:MAX_CHANGED_FILES], len(unique) > MAX_CHANGED_FILES


def output_path_is_safe(repo_root: Path, output_path: Path) -> bool:
    try:
        relative = output_path.relative_to(repo_root).as_posix()
    except ValueError:
        return True
    return bool(
        run_git(
            repo_root,
            "check-ignore",
            "--",
            relative,
            required=False,
        )
    )


def same_repository_root(recorded_root: Any, actual_root: Path) -> bool:
    if not isinstance(recorded_root, str):
        return False
    candidate = Path(recorded_root)
    if not candidate.is_absolute():
        return False
    try:
        return candidate.resolve(strict=True).samefile(
            actual_root.resolve(strict=True)
        )
    except (OSError, RuntimeError, ValueError):
        return False


def untrusted_validation(reasons: list[str]) -> tuple[None, str]:
    detail = "; ".join(reasons)
    return None, (
        f"Validation evidence was not accepted ({detail}); no local test result "
        "is claimed. Rerun the Evidence tier for this exact clean repository, "
        "base, and head."
    )


def read_validation(
    path: Path,
    *,
    repo_root: Path,
    base_ref: str,
    base_sha: str,
    head_sha: str,
    working_tree_dirty: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "Validation evidence is missing; no local test result is claimed."
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "Validation evidence is unreadable; no local test result is claimed."
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        return None, "Validation evidence has an unsupported shape; no local test result is claimed."
    reasons: list[str] = []
    if (
        type(data.get("schema_version")) is not int
        or data.get("schema_version") != VALIDATION_SCHEMA_VERSION
    ):
        reasons.append("unsupported schema version")
    if data.get("tier") != VALIDATION_TIER:
        reasons.append("wrong validation tier")
    if not same_repository_root(data.get("repository_root"), repo_root):
        reasons.append("different repository worktree")
    if data.get("base_ref") != base_ref:
        reasons.append("different base reference")
    if data.get("base_sha") != base_sha:
        reasons.append("different base commit")
    if data.get("head_sha") != head_sha:
        reasons.append("different head commit")
    if data.get("working_tree") != "clean" or working_tree_dirty:
        reasons.append("working tree is not a bound clean snapshot")
    if data.get("overall_status") not in {"passed", "failed"}:
        reasons.append("unsupported overall status")
    coverage = data.get("coverage")
    if not isinstance(coverage, list):
        reasons.append("unsupported coverage shape")
    protected_paths = data.get("authorized_protected_paths")
    if not isinstance(protected_paths, list) or not all(
        isinstance(item, str) for item in protected_paths
    ):
        reasons.append("unsupported protected-path declaration shape")

    steps = data["steps"]
    step_shape_valid = bool(steps)
    derived_failure = False
    for step in steps:
        if not isinstance(step, dict):
            step_shape_valid = False
            continue
        status = step.get("status")
        exit_code = step.get("exit_code")
        if (
            not isinstance(step.get("name"), str)
            or not isinstance(step.get("command"), str)
            or status not in {"passed", "failed"}
            or not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
        ):
            step_shape_valid = False
            continue
        if (status == "passed" and exit_code != 0) or (
            status == "failed" and exit_code == 0
        ):
            step_shape_valid = False
        derived_failure = derived_failure or status == "failed"
    expected_overall = "failed" if derived_failure else "passed"
    if not step_shape_valid or data.get("overall_status") != expected_overall:
        reasons.append("internally inconsistent step results")
    if data.get("overall_status") == "passed":
        step_names = tuple(
            step.get("name") for step in steps if isinstance(step, dict)
        )
        if step_names != PASSED_EVIDENCE_STEPS:
            reasons.append("incomplete passed Evidence step contract")
        if isinstance(coverage, list):
            coverage_pairs = tuple(
                (item.get("check"), item.get("status"))
                for item in coverage
                if isinstance(item, dict)
            )
            coverage_shape_valid = len(coverage_pairs) == len(coverage) and all(
                isinstance(item.get("evidence"), str)
                for item in coverage
                if isinstance(item, dict)
            )
            if not coverage_shape_valid or coverage_pairs != PASSED_EVIDENCE_COVERAGE:
                reasons.append("incomplete passed Evidence coverage contract")
    elif isinstance(coverage, list):
        coverage_pairs = tuple(
            (item.get("check"), item.get("status"))
            for item in coverage
            if isinstance(item, dict)
        )
        coverage_shape_valid = len(coverage_pairs) == len(coverage) and all(
            isinstance(item.get("evidence"), str)
            for item in coverage
            if isinstance(item, dict)
        )
        if not coverage_shape_valid or coverage_pairs != FAILED_EVIDENCE_COVERAGE:
            reasons.append("inconsistent failed Evidence coverage contract")
    if reasons:
        return untrusted_validation(reasons)
    return data, None


def path_impact(paths: list[str]) -> dict[str, bool]:
    engineering_runtime = "hass_mcp_engineering_beta/ha_mcp_engineering/"
    return {
        "stable": any(path.startswith("hass_mcp_admin/") for path in paths),
        "runtime": any(
            path.startswith(engineering_runtime) and not path.endswith("/AGENTS.md")
            for path in paths
        ),
        "workflow": any(
            path.startswith(".github/workflows/")
            and path.lower().endswith((".yml", ".yaml"))
            for path in paths
        ),
        "release": any(
            path.startswith(".release/")
            or path in {
                "repository.yaml",
                "hass_mcp_engineering_beta/config.yaml",
                "hass_mcp_engineering_beta/Dockerfile",
            }
            for path in paths
        ),
        "deployment_script": "scripts/deploy-beta.ps1" in paths,
    }


def validation_markdown(data: dict[str, Any] | None, missing: str | None) -> list[str]:
    if data is None:
        return [missing or "Validation evidence is unavailable."]
    lines = [
        f"Overall local status: **{clean_field(data.get('overall_status', 'unknown'))}**",
        "",
        "| Step | Command | Status | Exit | Tests | Duration (s) | Note |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    steps = data.get("steps", [])[:MAX_VALIDATION_STEPS]
    for step in steps:
        if not isinstance(step, dict):
            continue
        cells = [
            clean_field(step.get("name", "unknown")),
            f"`{clean_field(step.get('command', 'unknown')).replace('|', '&#124;')}`",
            clean_field(step.get("status", "unknown")),
            clean_field(step.get("exit_code", "unknown")),
            clean_field(step.get("test_count", "unknown")),
            clean_field(step.get("duration_seconds", "unknown")),
            clean_field(step.get("note", "")),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    if len(data.get("steps", [])) > MAX_VALIDATION_STEPS:
        lines.append(f"\nValidation steps truncated after {MAX_VALIDATION_STEPS} entries.")
    return lines


def coverage_markdown(data: dict[str, Any] | None) -> list[str]:
    if data is None or not isinstance(data.get("coverage"), list):
        return ["- No matching validation evidence was accepted; CI-only status is unknown."]
    lines: list[str] = []
    for item in data["coverage"][:50]:
        if not isinstance(item, dict):
            continue
        lines.append(
            "- "
            f"{clean_field(item.get('check', 'unknown'))}: "
            f"**{clean_field(item.get('status', 'unknown'))}** - "
            f"{clean_field(item.get('evidence', 'no evidence recorded'))}"
        )
    return lines or ["- No CI-only coverage entries were recorded."]


def render_document(
    *,
    base_ref: str,
    base_sha: str,
    head_ref: str,
    head_sha: str,
    paths: list[str],
    paths_truncated: bool,
    working_tree_dirty: bool,
    validation: dict[str, Any] | None,
    missing_validation: str | None,
) -> str:
    impact = path_impact(paths)
    runtime = (
        "Runtime-sensitive source paths changed; focused runtime assessment is required."
        if impact["runtime"] or impact["stable"]
        else "No MCP runtime-source or stable packaging path changed in the derived file set."
    )
    security = (
        "Runtime or workflow-YAML paths changed; complete a focused security review."
        if impact["runtime"] or impact["workflow"]
        else "The derived file set contains no runtime source or workflow YAML; review remains required for development tooling behavior."
    )
    compatibility = (
        "Stable-v1 paths changed; compatibility must be proven before submission."
        if impact["stable"]
        else "No `hass_mcp_admin/` stable-v1 path changed in the derived file set."
    )
    deployment = (
        "The deployment script changed; inspect its diff to confirm operations are unchanged. This generator performed no deployment."
        if impact["deployment_script"]
        else "No deployment script or deployment metadata change was detected. This generator performed no deployment."
    )
    scope_note = (
        "The working tree is dirty; the file list includes uncommitted/untracked paths and must be regenerated after commit."
        if working_tree_dirty
        else "The working tree is clean; the file list reflects the base-to-head Git diff."
    )
    lines = [
        "# Draft PR Evidence",
        "",
        "## Summary",
        "",
        "<!-- Replace with a concise outcome-focused summary. -->",
        "",
        "## Problem Addressed",
        "",
        "<!-- Explain the repository problem this change solves. -->",
        "",
        "## Baseline and Head",
        "",
        f"- Base: `{clean_field(base_ref)}` at `{base_sha}`",
        f"- Head: `{clean_field(head_ref)}` at `{head_sha}`",
        f"- Scope note: {scope_note}",
        "",
        "## Scope and Changed Files",
        "",
    ]
    lines.extend(f"- `{clean_field(path)}`" for path in paths)
    if not paths:
        lines.append("- No changed paths were derived.")
    if paths_truncated:
        lines.append(f"- File list truncated after {MAX_CHANGED_FILES} paths.")
    lines.extend(
        [
            "",
            "## Added Workflow Capabilities",
            "",
            "<!-- Describe the operator-visible capabilities added by this change. -->",
            "",
            "## Runtime Impact",
            "",
            runtime,
            "",
            "## Security and Authorization Boundaries",
            "",
            security,
            "",
            "## Compatibility Impact",
            "",
            compatibility,
            "",
            "## Tests and Local Evidence",
            "",
        ]
    )
    lines.extend(validation_markdown(validation, missing_validation))
    lines.extend(["", "## CI-only Checks", ""])
    lines.extend(coverage_markdown(validation))
    lines.extend(
        [
            "",
            "CI pass/fail state is not determined by this local generator; inspect the draft PR checks for this exact head SHA.",
            "",
            "## Explicit Non-actions",
            "",
            "Confirm each statement before submission; the generator does not infer external actions from Git:",
            "",
            "- [ ] No live Home Assistant or deployed MCP environment was accessed.",
            "- [ ] No secret, GitHub setting, image, release, tag, attestation, promotion, merge, or deployment was changed.",
            "- [ ] The pull request is draft and unmerged.",
            "",
            "## Known Limitations",
            "",
            "- Local evidence does not prove CI-only Docker, multiarchitecture, disposable-HA, exact-image, publication, provenance, or deployment checks.",
            "- <!-- Add change-specific limitations or write `None known`. -->",
            "",
            "## Deployment Impact",
            "",
            deployment,
            "",
            "## Rollback",
            "",
            "Revert the commits in this PR. Generated `.artifacts/` evidence is ignored and can be removed locally.",
            "",
            "## Independent-review Stop Point",
            "",
            "Stop after an independent reviewer has examined the complete diff, negative/failure-path coverage, protected-path comparison, and CI for this exact head. Do not mark ready, approve, merge, release, publish, promote, or deploy without separate authorization.",
        ]
    )
    document = "\n".join(lines) + "\n"
    if len(document) > MAX_DOCUMENT_CHARS:
        document = (
            document[: MAX_DOCUMENT_CHARS - 80]
            + "\n\n[Document truncated at the bounded output limit.]\n"
        )
    return document


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--output", type=Path, default=Path(".artifacts/pr-evidence.md"))
    parser.add_argument("--validation", type=Path, default=Path(".artifacts/validation.json"))
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository path; intended primarily for deterministic fixture tests.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    try:
        actual_root = Path(
            run_git(repo_root, "rev-parse", "--show-toplevel")
        ).resolve()
        output_path = args.output
        if not output_path.is_absolute():
            output_path = actual_root / output_path
        output_path = output_path.resolve()
        if not output_path_is_safe(actual_root, output_path):
            raise EvidenceError(
                "In-repository PR evidence output must be ignored by Git"
            )
        base_sha = run_git(actual_root, "rev-parse", f"{args.base}^{{commit}}")
        head_sha = run_git(actual_root, "rev-parse", f"{args.head}^{{commit}}")
        paths, paths_truncated = changed_files(actual_root, base_sha, head_sha)
        working_tree_dirty = bool(run_git(actual_root, "status", "--porcelain", required=True))
        validation_path = args.validation
        if not validation_path.is_absolute():
            validation_path = actual_root / validation_path
        validation, missing_validation = read_validation(
            validation_path,
            repo_root=actual_root,
            base_ref=args.base,
            base_sha=base_sha,
            head_sha=head_sha,
            working_tree_dirty=working_tree_dirty,
        )
        if (
            run_git(actual_root, "rev-parse", f"{args.base}^{{commit}}") != base_sha
            or run_git(actual_root, "rev-parse", f"{args.head}^{{commit}}") != head_sha
            or bool(run_git(actual_root, "status", "--porcelain", required=True))
            != working_tree_dirty
        ):
            raise EvidenceError("Git context changed while generating PR evidence; rerun")
        document = render_document(
            base_ref=args.base,
            base_sha=base_sha,
            head_ref=args.head,
            head_sha=head_sha,
            paths=paths,
            paths_truncated=paths_truncated,
            working_tree_dirty=working_tree_dirty,
            validation=validation,
            missing_validation=missing_validation,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(document, encoding="utf-8", newline="\n")
    except (EvidenceError, OSError) as exc:
        print(f"PR evidence unavailable: {exc}", file=sys.stderr)
        return 2
    try:
        display_path = output_path.relative_to(actual_root).as_posix()
    except ValueError:
        display_path = output_path.name
    print(f"Wrote bounded PR evidence to {display_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
