"""Materialize and validate a staged release candidate without publishing it."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile


PROMOTION_SCRIPT = Path("scripts/promote_next_release.py")
RELEASE_COUPLED_TESTS = (
    "tests.test_beta25_ha_search_promotion.Beta25SearchPromotionTests."
    "test_beta25_is_staged_without_changing_published_versions",
    "tests.test_beta25_ha_search_promotion.Beta25SearchPromotionTests."
    "test_beta25_generated_release_state_is_exact",
    "tests.test_f3_adapter_invariants.F3AdapterIsolationTests."
    "test_versions_and_secure_dependency_pins_are_unchanged",
    "tests.test_f3_dashboard_runtime_invariants.DashboardRuntimeInvariantTests."
    "test_versions_and_secure_dependency_pins_are_unchanged",
    "tests.test_rc1_publication.AutomatedPromotionWorkflowTests."
    "test_feature_pr_or_promoted_source_is_version_consistent",
    "tests.test_rc1_publication.AutomatedPromotionWorkflowTests."
    "test_staged_or_advertised_version_is_ordered",
)


class CandidateValidationError(RuntimeError):
    pass


def run(
    arguments: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()
        raise CandidateValidationError(
            f"Command failed ({result.returncode}): {' '.join(arguments)}\n{detail}"
        )
    return result.stdout.strip()


def load_promotion_module(repo_root: Path):
    path = repo_root / PROMOTION_SCRIPT
    spec = importlib.util.spec_from_file_location(
        "isolated_candidate_release_authority",
        path,
    )
    if spec is None or spec.loader is None:
        raise CandidateValidationError("Unable to load promotion authority")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tracked_paths(repo_root: Path) -> tuple[Path, ...]:
    raw = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if raw.returncode != 0:
        raise CandidateValidationError("Unable to enumerate tracked source files")
    result: list[Path] = []
    for value in raw.stdout.split(b"\0"):
        if not value:
            continue
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CandidateValidationError("A tracked path is not UTF-8") from exc
        pure = PurePosixPath(text)
        if pure.is_absolute() or ".." in pure.parts:
            raise CandidateValidationError("A tracked path escapes the repository")
        result.append(Path(*pure.parts))
    if not result:
        raise CandidateValidationError("The repository contains no tracked files")
    return tuple(result)


def copy_tracked_source(repo_root: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    for relative in tracked_paths(repo_root):
        source = repo_root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)


def validate(
    repo_root: Path,
    python_executable: str,
    *,
    require_materialized: bool = False,
) -> tuple[str, bool]:
    promotion = load_promotion_module(repo_root)
    if not (repo_root / promotion.NEXT_VERSION_PATH).exists():
        advertised = promotion.advertised_version(repo_root)
        promotion.validate_document_authority(repo_root, advertised)
        return advertised, False
    current, candidate = promotion.validate_candidate(repo_root)
    if require_materialized:
        raise CandidateValidationError(
            "The release declaration is not final review state. Run "
            "'python scripts/promote_next_release.py --apply', review and commit "
            "the bounded version updates, and remove .release/next-version before "
            "marking the pull request ready."
        )
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    with tempfile.TemporaryDirectory(
        prefix="hass-mcp-promotion-candidate-"
    ) as directory:
        snapshot = Path(directory) / "repository"
        copy_tracked_source(repo_root, snapshot)
        for arguments in (
            ["git", "init", "--initial-branch=main"],
            ["git", "config", "user.name", "Promotion Candidate Validator"],
            [
                "git",
                "config",
                "user.email",
                "promotion-candidate@example.invalid",
            ],
            ["git", "add", "-A"],
            ["git", "commit", "-m", "Record staged source"],
        ):
            run(arguments, cwd=snapshot, environment=environment)
        baseline = run(
            ["git", "rev-parse", "HEAD"],
            cwd=snapshot,
            environment=environment,
        )

        run(
            [python_executable, str(PROMOTION_SCRIPT), "--apply"],
            cwd=snapshot,
            environment=environment,
        )
        for arguments in (
            ["git", "add", "-A"],
            [
                "git",
                "commit",
                "-m",
                f"Promote HA MCP Engineering Server {candidate}",
            ],
        ):
            run(arguments, cwd=snapshot, environment=environment)

        if (snapshot / promotion.NEXT_VERSION_PATH).exists():
            raise CandidateValidationError(
                "The promoted candidate retained the staged declaration"
            )
        if promotion.advertised_version(snapshot) != candidate:
            raise CandidateValidationError(
                "The promoted candidate did not converge on the staged version"
            )
        promotion.validate_document_authority(snapshot, candidate)

        run(
            [
                python_executable,
                "scripts/validate_addon_metadata.py",
                "--repo-root",
                ".",
                "--base-ref",
                baseline,
                "--expected-version",
                candidate,
                "--deployed-version",
                current,
            ],
            cwd=snapshot,
            environment=environment,
        )
        run(
            [
                python_executable,
                "-m",
                "compileall",
                "-q",
                "hass_mcp_admin",
                "hass_mcp_engineering_beta",
                "tests",
            ],
            cwd=snapshot,
            environment=environment,
        )
        run(
            [python_executable, "-m", "unittest", "-v", *RELEASE_COUPLED_TESTS],
            cwd=snapshot,
            environment=environment,
        )
        run(
            ["git", "diff", "--check", f"{baseline}..HEAD"],
            cwd=snapshot,
            environment=environment,
        )
    return candidate, True


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument(
        "--require-materialized",
        action="store_true",
        help="Reject a staged declaration; final reviewed release state is required.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        version, materialized = validate(
            args.repo_root.resolve(),
            args.python_executable,
            require_materialized=args.require_materialized,
        )
    except Exception as exc:
        print(f"Promotion candidate validation failed: {exc}", file=sys.stderr)
        return 1
    if materialized:
        print(f"Validated isolated promotion candidate {version}.")
    else:
        print(f"Validated exact advertised release {version}; no staged candidate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
