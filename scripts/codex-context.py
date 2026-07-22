"""Report bounded, offline repository context for Codex and human operators."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit


MAX_CHANGED_PATHS = 200
UNKNOWN = "unknown"
DEV_RC_VERSION_RE = re.compile(
    r"^(?P<core>\d+\.\d+\.\d+)-rc(?P<rc>[1-9]\d*)-dev(?P<dev>[1-9]\d*)$"
)
FINAL_RC_VERSION_RE = re.compile(
    r"^(?P<core>\d+\.\d+\.\d+)-rc\.(?P<rc>[1-9]\d*)$"
)
STABLE_VERSION_RE = re.compile(r"^(?P<core>\d+\.\d+\.\d+)$")


class ContextError(RuntimeError):
    """A required repository fact could not be determined."""


def run_git(repo_root: Path, *args: str, required: bool = False) -> str | None:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", *args],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if required:
            raise ContextError("Git is unavailable for required repository state") from exc
        return None
    if result.returncode:
        if required:
            raise ContextError("Git could not determine required repository state")
        return None
    return result.stdout.rstrip()


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def simple_yaml_scalar(path: Path, key: str) -> str | None:
    text = read_text(path)
    if text is None:
        return None
    match = re.search(
        rf"(?m)^{re.escape(key)}:\s*[\"']?([^\"'\s#]+)",
        text,
    )
    return match.group(1) if match else None


def python_literal(path: Path, name: str) -> Any | None:
    text = read_text(path)
    if text is None:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        try:
            return ast.literal_eval(node.value)
        except (TypeError, ValueError):
            return None
    return None


def safe_remote(remote: str | None) -> str | None:
    if not remote:
        return None
    if "://" not in remote:
        return remote.split("@", 1)[-1]
    parsed = urlsplit(remote)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def repository_identity(remote: str | None, root: Path) -> str:
    if remote:
        normalized = remote.replace("\\", "/").removesuffix(".git").rstrip("/")
        if "://" in normalized:
            path = urlsplit(normalized).path.strip("/")
        elif ":" in normalized:
            path = normalized.split(":", 1)[1].strip("/")
        else:
            path = normalized.strip("/")
        parts = path.split("/")
        if len(parts) >= 2:
            return "/".join(parts[-2:])
    return root.name or UNKNOWN


def changed_paths(repo_root: Path) -> tuple[list[str], bool]:
    output = run_git(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        required=True,
    )
    assert output is not None
    paths: list[str] = []
    for line in output.splitlines():
        value = line[3:] if len(line) >= 4 else line
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        paths.append(value.strip('"').replace("\\", "/"))
    truncated = len(paths) > MAX_CHANGED_PATHS
    return paths[:MAX_CHANGED_PATHS], truncated


def section_bullets(path: Path, heading: str) -> list[str]:
    text = read_text(path)
    if text is None:
        return []
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)",
        text,
    )
    if not match:
        return []
    values: list[str] = []
    current: str | None = None
    for line in match.group(1).splitlines():
        if line.startswith("- "):
            if current is not None:
                values.append(current)
            current = line[2:].strip()
        elif current is not None and line.startswith("  "):
            current = f"{current} {line.strip()}"
        elif current is not None and line.strip():
            values.append(current)
            current = None
    if current is not None:
        values.append(current)
    return values


def workflow_jobs(path: Path) -> list[str]:
    text = read_text(path)
    if text is None or not re.search(r"(?m)^jobs:\s*$", text):
        return []
    jobs_text = text.split("\njobs:", 1)[-1]
    return re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", jobs_text)


def parse_release_version(version: str) -> dict[str, Any] | None:
    match = DEV_RC_VERSION_RE.fullmatch(version)
    if match:
        return {
            "kind": "development_rc",
            "core": match.group("core"),
            "rc": int(match.group("rc")),
            "dev": int(match.group("dev")),
        }
    match = FINAL_RC_VERSION_RE.fullmatch(version)
    if match:
        return {
            "kind": "final_rc",
            "core": match.group("core"),
            "rc": int(match.group("rc")),
            "dev": None,
        }
    match = STABLE_VERSION_RE.fullmatch(version)
    if match:
        return {
            "kind": "stable",
            "core": match.group("core"),
            "rc": None,
            "dev": None,
        }
    return None


def release_stage(version: str) -> str:
    identity = parse_release_version(version)
    if identity is None:
        return UNKNOWN
    if identity["kind"] == "development_rc":
        return f"RC{identity['rc']} development {identity['dev']}"
    if identity["kind"] == "final_rc":
        return f"RC{identity['rc']} final"
    return "stable"


def document_declares_version(path: Path, version: str) -> bool:
    text = read_text(path)
    if text is None:
        return False
    bounded_version = rf"(?<![A-Za-z0-9.+-]){re.escape(version)}(?![A-Za-z0-9.+-])"
    return re.search(bounded_version, text) is not None


def document_matches_version(
    path: Path,
    version: str,
    identity: dict[str, Any],
) -> bool:
    text = read_text(path)
    if text is None or not document_declares_version(path, version):
        return False
    heading_match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    if heading_match is None:
        return False
    heading = heading_match.group(1).lower()
    identity_markers = [version.lower()]
    if identity["kind"] == "development_rc":
        identity_markers.append(f"rc{identity['rc']}dev{identity['dev']}")
    if not any(
        re.search(
            rf"(?<![A-Za-z0-9.+-]){re.escape(marker)}(?![A-Za-z0-9.+-])",
            heading,
        )
        for marker in identity_markers
    ):
        return False
    identity_region = "\n".join(text.splitlines()[:12])
    if re.search(
        r"(?i)\b(?:historical only|immutable historical release|"
        r"not (?:active )?authority|cannot authorize|does not authorize)\b",
        identity_region,
    ):
        return False
    return True


def historical_documents(
    repo_root: Path,
    identity: dict[str, Any] | None,
) -> list[str]:
    if identity is None:
        return []

    docs_root = repo_root / "docs"
    development_pattern = re.compile(
        r"RC(?P<rc>[1-9]\d*)DEV(?P<dev>[1-9]\d*)_"
        r"(?:RELEASE_NOTES|ACCEPTANCE)\.md"
    )
    final_pattern = re.compile(
        r"RC(?P<rc>[1-9]\d*)_(?:RELEASE_NOTES|ACCEPTANCE)\.md"
    )
    selected: list[Path] = []
    for path in docs_root.glob("RC*_*.md"):
        development = development_pattern.fullmatch(path.name)
        final = final_pattern.fullmatch(path.name)
        is_historical = False
        candidate_version: str | None = None
        if development:
            candidate_version = (
                f"{identity['core']}-rc{development.group('rc')}-"
                f"dev{development.group('dev')}"
            )
        elif final:
            candidate_version = f"{identity['core']}-rc.{final.group('rc')}"
        if identity["kind"] == "stable":
            is_historical = development is not None or final is not None
        elif identity["kind"] == "development_rc":
            current_rc = identity["rc"]
            current_dev = identity["dev"]
            if development:
                candidate_rc = int(development.group("rc"))
                candidate_dev = int(development.group("dev"))
                is_historical = candidate_rc < current_rc or (
                    candidate_rc == current_rc and candidate_dev < current_dev
                )
            elif final:
                is_historical = int(final.group("rc")) <= current_rc
        elif identity["kind"] == "final_rc":
            current_rc = identity["rc"]
            if development:
                is_historical = int(development.group("rc")) < current_rc
            elif final:
                is_historical = int(final.group("rc")) < current_rc
        if (
            is_historical
            and candidate_version is not None
            and document_declares_version(path, candidate_version)
        ):
            selected.append(path)

    return [
        path.relative_to(repo_root).as_posix()
        for path in sorted(selected, key=lambda candidate: candidate.name)
    ]


def resolve_documents(repo_root: Path, version: str) -> dict[str, Any]:
    identity = parse_release_version(version)
    historical = historical_documents(repo_root, identity)
    result: dict[str, Any] = {
        "resolution_status": "unsupported",
        "active_release_notes": UNKNOWN,
        "active_acceptance_document": UNKNOWN,
        "historical_references": historical,
        "limitations": [],
    }

    if identity is None:
        result["limitations"].append(
            f"Unsupported Engineering version format {version!r}; stop release or "
            "deployment work until an exact repository convention is defined."
        )
    elif identity["kind"] == "stable":
        result["limitations"].append(
            f"The repository defines no exact stable release-notes and acceptance-document "
            f"convention for {version}; stop release or deployment work until one is "
            "established."
        )
    else:
        rc_number = identity["rc"]
        if identity["kind"] == "development_rc":
            stem = f"RC{rc_number}DEV{identity['dev']}"
        else:
            stem = f"RC{rc_number}"
        docs_root = repo_root / "docs"
        candidates = {
            "active_release_notes": docs_root / f"{stem}_RELEASE_NOTES.md",
            "active_acceptance_document": docs_root / f"{stem}_ACCEPTANCE.md",
        }
        for field, path in candidates.items():
            if document_matches_version(path, version, identity):
                result[field] = path.relative_to(repo_root).as_posix()
            elif path.is_file():
                result["limitations"].append(
                    f"{path.relative_to(repo_root).as_posix()} does not identify the exact "
                    f"Engineering version {version} and is not active authority."
                )

        known_count = sum(
            result[field] != UNKNOWN
            for field in ("active_release_notes", "active_acceptance_document")
        )
        result["resolution_status"] = (
            "exact" if known_count == 2 else "partial" if known_count == 1 else "missing"
        )

    if result["active_acceptance_document"] == UNKNOWN:
        result["limitations"].append(
            f"Exact acceptance authority for {version} is missing; stop release or "
            "deployment work. Never substitute release notes or a historical reference."
        )
    elif result["resolution_status"] != "exact":
        result["limitations"].append(
            f"Document resolution for {version} is {result['resolution_status']}; stop "
            "release or deployment work until both exact documents are known."
        )
    if historical:
        result["limitations"].append(
            "Historical references are informational only and cannot authorize current "
            "acceptance, release, or deployment work."
        )
    return result


def build_context(repo_root_hint: Path) -> dict[str, Any]:
    top = run_git(repo_root_hint, "rev-parse", "--show-toplevel", required=True)
    assert top is not None
    repo_root = Path(top).resolve()
    head = run_git(repo_root, "rev-parse", "HEAD", required=True)
    assert head is not None
    branch = run_git(repo_root, "symbolic-ref", "--short", "-q", "HEAD") or "detached"
    origin_main = run_git(repo_root, "rev-parse", "--verify", "origin/main^{commit}")
    remote = safe_remote(run_git(repo_root, "remote", "get-url", "origin"))
    paths, paths_truncated = changed_paths(repo_root)

    unknowns: list[dict[str, str]] = []
    inconsistencies: list[str] = []

    def known(value: Any, field: str, source: str) -> Any:
        if value is None:
            unknowns.append({"field": field, "source_required": source})
            return UNKNOWN
        return value

    stable_config = Path("hass_mcp_admin/config.yaml")
    engineering_config = Path("hass_mcp_engineering_beta/config.yaml")
    capabilities_path = Path(
        "hass_mcp_engineering_beta/ha_mcp_engineering/capabilities.py"
    )
    policy_path = Path(
        "hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy.json"
    )
    version_path = Path("hass_mcp_engineering_beta/ha_mcp_engineering/version.py")
    validator_path = Path("scripts/validate_addon_metadata.py")
    staged_version_path = Path(".release/next-version")

    stable_version = known(
        simple_yaml_scalar(repo_root / stable_config, "version"),
        "stable_version",
        stable_config.as_posix(),
    )
    engineering_version = known(
        simple_yaml_scalar(repo_root / engineering_config, "version"),
        "engineering_version",
        engineering_config.as_posix(),
    )
    stage = release_stage(engineering_version)

    canonical = python_literal(repo_root / capabilities_path, "CAPABILITIES")
    native = python_literal(repo_root / capabilities_path, "BETA_NATIVE_CAPABILITIES")
    planned = python_literal(repo_root / capabilities_path, "PLANNED_CAPABILITIES")
    canonical_count = len(canonical) if isinstance(canonical, (tuple, list)) else None
    native_count = len(native) if isinstance(native, (tuple, list)) else None
    planned_count = len(planned) if isinstance(planned, (tuple, list)) else None
    static_count = (
        canonical_count + native_count
        if canonical_count is not None and native_count is not None
        else None
    )

    policy: dict[str, Any] | None = None
    policy_text = read_text(repo_root / policy_path)
    if policy_text is not None:
        try:
            parsed = json.loads(policy_text)
            policy = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            policy = None
    if policy is None:
        unknowns.append(
            {
                "field": "upstream_policy",
                "source_required": policy_path.as_posix(),
            }
        )
        reviewed_version = None
        stock_count = None
        delegated_count = None
    else:
        reviewed_version = policy.get("reviewed_upstream_version")
        stock_count = policy.get("reviewed_stock_catalog_tool_count")
        tools = policy.get("tools")
        delegated_count = (
            sum(
                1
                for item in tools
                if isinstance(item, dict) and item.get("classification") == "automatic_read"
            )
            if isinstance(tools, list)
            else None
        )
        if isinstance(tools, list) and isinstance(stock_count, int) and len(tools) != stock_count:
            inconsistencies.append(
                "The reviewed upstream policy entry count differs from its stock catalog count."
            )

    total_count = (
        static_count + delegated_count
        if static_count is not None and delegated_count is not None
        else None
    )

    runtime_version = python_literal(repo_root / version_path, "SERVER_VERSION")
    validator_version = python_literal(repo_root / validator_path, "BETA_VERSION")
    declared_versions = {
        engineering_config.as_posix(): engineering_version,
        version_path.as_posix(): runtime_version if runtime_version is not None else UNKNOWN,
        validator_path.as_posix(): validator_version if validator_version is not None else UNKNOWN,
    }
    known_versions = {value for value in declared_versions.values() if value != UNKNOWN}
    if len(known_versions) > 1:
        inconsistencies.append("Authoritative Engineering version declarations disagree.")

    readme = read_text(repo_root / "README.md") or ""
    readme_stage = re.search(
        r"development stage is now hardened as\s*`([^`]+)`",
        readme,
        re.IGNORECASE,
    )
    if readme_stage and engineering_version != UNKNOWN and readme_stage.group(1) != engineering_version:
        inconsistencies.append(
            "README Engineering milestone text does not match authoritative version metadata."
        )

    documents = resolve_documents(repo_root, engineering_version)
    for field in ("active_release_notes", "active_acceptance_document"):
        if documents[field] == UNKNOWN:
            unknowns.append(
                {
                    "field": field,
                    "source_required": "an exact version-matched file under docs/",
                }
            )

    staged_text = read_text(repo_root / staged_version_path)
    staged_version = UNKNOWN
    if staged_text is not None:
        staged_lines = staged_text.splitlines()
        if (
            len(staged_lines) == 1
            and staged_lines[0]
            and staged_lines[0] == staged_lines[0].strip()
        ):
            staged_version = staged_lines[0]
        else:
            inconsistencies.append(
                "The staged release declaration must contain exactly one version."
            )
    if staged_version == UNKNOWN:
        staged_documents = {
            "resolution_status": "missing",
            "active_release_notes": UNKNOWN,
            "active_acceptance_document": UNKNOWN,
            "historical_references": [],
            "limitations": [
                "No valid staged release declaration is present; no staged promotion "
                "authority is active."
            ],
        }
    else:
        staged_documents = resolve_documents(repo_root, staged_version)

    instruction_path = repo_root / "AGENTS.md"
    local_commands = section_bullets(instruction_path, "Local Commands")
    protected_paths = section_bullets(instruction_path, "Frozen or Protected Paths")
    prohibited_actions = section_bullets(instruction_path, "Prohibited Actions")
    for field, value, source in (
        ("local_validation_commands", local_commands, "AGENTS.md#local-commands"),
        ("protected_paths", protected_paths, "AGENTS.md#frozen-or-protected-paths"),
        ("prohibited_actions", prohibited_actions, "AGENTS.md#prohibited-actions"),
    ):
        if not value:
            unknowns.append({"field": field, "source_required": source})

    ci_jobs = workflow_jobs(repo_root / ".github/workflows/ci.yml")
    if not ci_jobs:
        unknowns.append(
            {
                "field": "ci_validation_jobs",
                "source_required": ".github/workflows/ci.yml",
            }
        )

    return {
        "schema_version": 2,
        "repository": {
            "identity": repository_identity(remote, repo_root),
            "root": str(repo_root),
            "origin": remote or UNKNOWN,
            "branch": branch,
            "head_sha": head,
            "origin_main_sha": origin_main or UNKNOWN,
            "working_tree": "clean" if not paths else "dirty",
            "changed_paths": paths,
            "changed_paths_truncated": paths_truncated,
        },
        "versions": {
            "stable": stable_version,
            "engineering": engineering_version,
            "release_stage": stage,
            "staged": staged_version,
            "authoritative_declarations": declared_versions,
        },
        "tool_counts": {
            "canonical": known(
                canonical_count,
                "canonical_tool_count",
                f"{capabilities_path.as_posix()}:CAPABILITIES",
            ),
            "engineering_native": known(
                native_count,
                "engineering_native_tool_count",
                f"{capabilities_path.as_posix()}:BETA_NATIVE_CAPABILITIES",
            ),
            "planned": known(
                planned_count,
                "planned_tool_count",
                f"{capabilities_path.as_posix()}:PLANNED_CAPABILITIES",
            ),
            "static_registered": known(
                static_count,
                "static_registered_tool_count",
                capabilities_path.as_posix(),
            ),
            "reviewed_upstream_version": known(
                reviewed_version,
                "reviewed_upstream_version",
                policy_path.as_posix(),
            ),
            "reviewed_stock_catalog": known(
                stock_count,
                "reviewed_stock_catalog_tool_count",
                policy_path.as_posix(),
            ),
            "expected_delegated_reads": known(
                delegated_count,
                "expected_delegated_read_count",
                f"{policy_path.as_posix()} automatic_read entries",
            ),
            "expected_connector_total": known(
                total_count,
                "expected_connector_tool_count",
                f"{capabilities_path.as_posix()} plus {policy_path.as_posix()}",
            ),
            "expectation_note": (
                "The connector total assumes every reviewed automatic-read schema is present "
                "and exact-matching at startup; runtime admission remains fail-closed and may be lower."
            ),
        },
        "documents": documents,
        "staged_release": {
            "version": staged_version,
            "declaration": (
                staged_version_path.as_posix()
                if staged_version != UNKNOWN
                else UNKNOWN
            ),
            "documents": staged_documents,
        },
        "validation": {
            "local_commands": local_commands,
            "ci_only_jobs": ci_jobs,
            "ci_note": "CI job presence is not evidence that a job passed for this HEAD.",
        },
        "boundaries": {
            "protected_paths": protected_paths,
            "prohibited_actions": prohibited_actions,
        },
        "inconsistencies": inconsistencies,
        "unknowns": unknowns,
        "sources": {
            "stable_version": stable_config.as_posix(),
            "engineering_version": engineering_config.as_posix(),
            "tool_counts": capabilities_path.as_posix(),
            "upstream_policy": policy_path.as_posix(),
            "ci": ".github/workflows/ci.yml",
            "operator_policy": "AGENTS.md",
        },
    }


def markdown_list(values: list[str], empty: str = "- None") -> list[str]:
    return [f"- {value}" for value in values] if values else [empty]


def render_markdown(context: dict[str, Any]) -> str:
    repo = context["repository"]
    versions = context["versions"]
    counts = context["tool_counts"]
    validation = context["validation"]
    boundaries = context["boundaries"]
    documents = context["documents"]
    staged_release = context["staged_release"]
    staged_documents = staged_release["documents"]
    lines = [
        "# Codex Repository Context",
        "",
        "## Repository State",
        "",
        f"- Identity: `{repo['identity']}`",
        f"- Root: `{repo['root']}`",
        f"- Branch: `{repo['branch']}`",
        f"- HEAD: `{repo['head_sha']}`",
        f"- origin/main: `{repo['origin_main_sha']}`",
        f"- Working tree: **{repo['working_tree']}**",
        "- Changed paths:",
    ]
    lines.extend(f"  - `{path}`" for path in repo["changed_paths"])
    if not repo["changed_paths"]:
        lines.append("  - None")
    if repo["changed_paths_truncated"]:
        lines.append(f"  - Truncated after {MAX_CHANGED_PATHS} paths")
    lines.extend(
        [
            "",
            "## Version and Release Context",
            "",
            f"- Stable add-on: `{versions['stable']}`",
            f"- Engineering add-on: `{versions['engineering']}`",
            f"- Release stage: `{versions['release_stage']}`",
            f"- Staged Engineering release: `{staged_release['version']}`",
            f"- Staged declaration: `{staged_release['declaration']}`",
            f"- Resolution status: `{documents['resolution_status']}`",
            f"- Active release notes: `{documents['active_release_notes']}`",
            f"- Active acceptance document: `{documents['active_acceptance_document']}`",
            "- Historical references (informational only):",
        ]
    )
    lines.extend(f"  - `{path}`" for path in documents["historical_references"])
    if not documents["historical_references"]:
        lines.append("  - None")
    lines.append("- Limitations:")
    lines.extend(f"  - {value}" for value in documents["limitations"])
    if not documents["limitations"]:
        lines.append("  - None")
    lines.extend(
        [
            "",
            "### Staged Promotion Authority",
            "",
            f"- Resolution status: `{staged_documents['resolution_status']}`",
            f"- Active release notes: `{staged_documents['active_release_notes']}`",
            f"- Active acceptance document: `{staged_documents['active_acceptance_document']}`",
            "- Historical references (informational only):",
        ]
    )
    lines.extend(
        f"  - `{path}`" for path in staged_documents["historical_references"]
    )
    if not staged_documents["historical_references"]:
        lines.append("  - None")
    lines.append("- Limitations:")
    lines.extend(f"  - {value}" for value in staged_documents["limitations"])
    if not staged_documents["limitations"]:
        lines.append("  - None")
    lines.extend(
        [
            "",
            "## Tool Count Expectations",
            "",
            f"- Canonical: `{counts['canonical']}`",
            f"- Engineering native: `{counts['engineering_native']}`",
            f"- Static registered: `{counts['static_registered']}`",
            f"- Planned: `{counts['planned']}`",
            f"- Reviewed upstream: `{counts['reviewed_upstream_version']}`",
            f"- Reviewed upstream stock catalog: `{counts['reviewed_stock_catalog']}`",
            f"- Expected delegated reads: `{counts['expected_delegated_reads']}`",
            f"- Expected connector total: `{counts['expected_connector_total']}`",
            f"- Note: {counts['expectation_note']}",
            "",
            "## Validation and Boundaries",
            "",
            "### Local commands",
            "",
        ]
    )
    lines.extend(markdown_list(validation["local_commands"], "- unknown"))
    lines.extend(["", "### CI-only validation jobs", ""])
    lines.extend(markdown_list(validation["ci_only_jobs"], "- unknown"))
    lines.append(f"- Note: {validation['ci_note']}")
    lines.extend(["", "### Protected paths", ""])
    lines.extend(markdown_list(boundaries["protected_paths"], "- unknown"))
    lines.extend(["", "### Prohibited actions", ""])
    lines.extend(markdown_list(boundaries["prohibited_actions"], "- unknown"))
    lines.extend(["", "## Inconsistencies and Unknowns", ""])
    if context["inconsistencies"]:
        lines.extend(f"- Inconsistency: {item}" for item in context["inconsistencies"])
    if context["unknowns"]:
        lines.extend(
            f"- `{item['field']}`: unknown; requires `{item['source_required']}`"
            for item in context["unknowns"]
        )
    if not context["inconsistencies"] and not context["unknowns"]:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository path; intended primarily for deterministic fixture tests.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        context = build_context(args.repo_root.resolve())
    except ContextError as exc:
        print(f"Context unavailable: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(context, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(render_markdown(context), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
