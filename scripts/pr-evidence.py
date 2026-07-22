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


def read_validation(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "Validation evidence is missing; no local test result is claimed."
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "Validation evidence is unreadable; no local test result is claimed."
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        return None, "Validation evidence has an unsupported shape; no local test result is claimed."
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
        return ["- Missing validation evidence; CI-only status is unknown."]
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
        base_sha = run_git(actual_root, "rev-parse", f"{args.base}^{{commit}}")
        head_sha = run_git(actual_root, "rev-parse", f"{args.head}^{{commit}}")
        paths, paths_truncated = changed_files(actual_root, args.base, args.head)
        working_tree_dirty = bool(run_git(actual_root, "status", "--porcelain", required=True))
        validation_path = args.validation
        if not validation_path.is_absolute():
            validation_path = actual_root / validation_path
        validation, missing_validation = read_validation(validation_path)
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
        output_path = args.output
        if not output_path.is_absolute():
            output_path = actual_root / output_path
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
