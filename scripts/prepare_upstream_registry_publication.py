"""Construct and verify one coherent data-only registry commit in a clean worktree."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.manage_upstream_trust_registry import (  # noqa: E402
    RegistryOperationError,
    _canonical_file,
    _strict_json_loads,
    allowed_output_paths,
    paths_for_root,
    verify_committed_registry,
    verify_signed_artifact_directory,
)


BRANCH_PATTERN = re.compile(r"^data/upstream-registry-[a-z]+-[0-9]+$")


def _run_git(repository: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        raise RegistryOperationError(
            "publication_git_operation_failed",
            category="publication_git_operation_failed",
        ) from None


def _load_manifest(directory: Path) -> dict[str, Any]:
    raw = (directory / "publication-manifest.json").read_bytes()
    value = _strict_json_loads(raw)
    if not isinstance(value, dict) or raw != _canonical_file(value):
        raise RegistryOperationError(
            "publication_manifest_invalid", category="publication_manifest_invalid"
        )
    return value


def construct_verified_publication_commit(
    *,
    repository: Path,
    signed_artifacts: Path,
    worktree: Path,
    branch: str,
    environment: Mapping[str, str],
    now: datetime | None = None,
) -> dict[str, Any]:
    if not BRANCH_PATTERN.fullmatch(branch):
        raise RegistryOperationError("publication_branch_invalid")
    if worktree.exists():
        raise RegistryOperationError("publication_worktree_not_clean")
    artifact_verification = verify_signed_artifact_directory(
        signed_artifacts,
        environment=environment,
        now=now or datetime.now(timezone.utc),
    )
    manifest = _load_manifest(signed_artifacts)
    if manifest.get("verified") is not True or manifest.get("data_only") is not True:
        raise RegistryOperationError("publication_artifacts_unverified")
    sequence = manifest.get("new_sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise RegistryOperationError("publication_manifest_invalid")
    if artifact_verification["sequence"] != sequence:
        raise RegistryOperationError("publication_manifest_binding_mismatch")
    changed_paths = manifest.get("changed_paths")
    expected = allowed_output_paths(paths_for_root(repository), sequence)
    if changed_paths != expected or len(changed_paths) != 4:
        raise RegistryOperationError("publication_path_allowlist_invalid")
    source_tree = signed_artifacts / "tree"
    for relative in changed_paths:
        source = source_tree / relative
        if not source.is_file():
            raise RegistryOperationError("publication_output_class_missing")

    _run_git(repository, "worktree", "add", "--detach", str(worktree), "refs/remotes/origin/main")
    committed = False
    try:
        for relative in changed_paths:
            source = source_tree / relative
            target = worktree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        status = _run_git(
            worktree,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        actual = sorted(
            line[3:].replace("\\", "/")
            for line in status.splitlines()
            if len(line) >= 4
        )
        if actual != sorted(changed_paths):
            raise RegistryOperationError("publication_unrelated_change")
        verify_committed_registry(
            paths=paths_for_root(worktree),
            environment=environment,
            now=now or datetime.now(timezone.utc),
        )
        _run_git(worktree, "switch", "-c", branch)
        _run_git(worktree, "config", "user.name", "github-actions[bot]")
        _run_git(
            worktree,
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        )
        _run_git(worktree, "add", "--", *changed_paths)
        staged = sorted(
            item
            for item in _run_git(worktree, "diff", "--cached", "--name-only").splitlines()
            if item
        )
        if staged != sorted(changed_paths):
            raise RegistryOperationError("publication_partial_output_set")
        _run_git(
            worktree,
            "commit",
            "-m",
            f"Operate upstream trust registry: {manifest['operation']}",
        )
        committed = True
        verify_committed_registry(
            paths=paths_for_root(worktree),
            environment=environment,
            now=now or datetime.now(timezone.utc),
        )
        committed_paths = sorted(
            item
            for item in _run_git(
                worktree,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "HEAD",
            ).splitlines()
            if item
        )
        if committed_paths != sorted(changed_paths):
            raise RegistryOperationError("publication_commit_set_mismatch")
        commit_sha = _run_git(worktree, "rev-parse", "HEAD")
        return {
            "schema_version": 1,
            "branch": branch,
            "commit_sha": commit_sha,
            "workflow_base_sha": manifest["workflow_base_sha"],
            "expected_current_sequence": manifest["expected_current_sequence"],
            "new_sequence": sequence,
            "changed_paths": changed_paths,
            "complete_set_verified": True,
            "committed": True,
        }
    except Exception:
        if committed:
            # The disposable worktree is abandoned by the caller; no remote mutation occurred.
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--signed-artifacts", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = construct_verified_publication_commit(
            repository=args.repository,
            signed_artifacts=args.signed_artifacts,
            worktree=args.worktree,
            branch=args.branch,
            environment=os.environ,
        )
    except (OSError, ValueError) as exc:
        print(getattr(exc, "category", "publication_preparation_failed"), file=sys.stderr)
        return 2
    args.summary.write_bytes(_canonical_file(result))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
