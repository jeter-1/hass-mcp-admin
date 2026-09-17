"""Bind same-run full-discovery evidence before avoiding duplicate publication work.

This is a GitHub job-output receipt, not a signature or a replacement for the
complete CI aggregate. Historical release sources always run their own suite.
No API, credentials, publishing, or recovery authority is introduced here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess

REPOSITORY = "jeter-1/hass-mcp-admin"
WORKFLOW = ".github/workflows/publish-rc-image.yml"
COMMAND = "python -m unittest discover -s tests -v"
BOUND_FILES = (
    ".github/workflows/ci.yml", WORKFLOW,
    "scripts/publication_validation.py",
    "tests/requirements.lock",
    "hass_mcp_engineering_beta/requirements.lock",
)
MAX_RECEIPT = 4096


class Refusal(ValueError):
    pass


def require(condition, category):
    if not condition:
        raise Refusal(category)


def git(root, *args):
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
        )
        require(len(result.stdout) <= 2_000_000, "git_output_bound")
        return result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal("git_identity_unavailable") from exc


def sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value), "invalid_commit")
    return value


def positive(value):
    require(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value), "invalid_run_identity")
    return value


def context(env):
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY, "wrong_repository")
    workflow_ref = env.get("GITHUB_WORKFLOW_REF", "")
    require(isinstance(workflow_ref, str) and len(workflow_ref) <= 512
            and workflow_ref.startswith(REPOSITORY + "/.github/workflows/")
            and "\n" not in workflow_ref, "wrong_workflow_ref")
    event = env.get("GITHUB_EVENT_NAME")
    require(event in {"pull_request", "push", "workflow_dispatch"}, "unsupported_event")
    return {
        "repository": REPOSITORY,
        "run_id": positive(env.get("GITHUB_RUN_ID")),
        "run_attempt": positive(env.get("GITHUB_RUN_ATTEMPT")),
        "trigger_sha": sha(env.get("GITHUB_SHA")),
        "workflow_sha": sha(env.get("GITHUB_WORKFLOW_SHA")),
        "workflow_ref": workflow_ref,
        "event": event,
    }


def source_identity(root, commit):
    sha(commit)
    tree = git(root, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    sha(tree)
    hashes = {
        path: hashlib.sha256(git(root, "show", f"{commit}:{path}")).hexdigest()
        for path in BOUND_FILES
    }
    return {"source_sha": commit, "source_tree": tree, "file_sha256": hashes}


def clean_head(root):
    require(not git(root, "status", "--porcelain=v1"), "dirty_validation_source")
    return sha(git(root, "rev-parse", "HEAD").decode().strip())


def record(root, env, python_version=None):
    require(env.get("FULL_SUITE_OUTCOME") == "success", "full_suite_did_not_succeed")
    bound = context(env)
    head = clean_head(root)
    require(head == bound["trigger_sha"], "validation_checkout_mismatch")
    return {
        "schema_version": 1, "status": "success", "command": COMMAND,
        "python_version": python_version or platform.python_version(),
        **bound, **source_identity(root, head),
    }


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_receipt_field")
        result[key] = value
    return result


def decode(raw):
    require(isinstance(raw, str) and 0 < len(raw.encode()) <= MAX_RECEIPT,
            "missing_or_oversized_validation_receipt")
    try:
        result = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise Refusal("malformed_validation_receipt") from exc
    require(isinstance(result, dict), "malformed_validation_receipt")
    return result


def decide(root, env, installed_lock, python_version=None):
    # Never use failure, skip, cancellation or an old run as permission to
    # publish after an isolated suite. The complete CI dependency stays required.
    require(env.get("COMPLETE_CI_RESULT") == "success", "complete_ci_did_not_succeed")
    bound = context(env)
    require(bound["workflow_ref"] == REPOSITORY + "/" + WORKFLOW + "@refs/heads/main"
            and bound["event"] in {"push", "workflow_dispatch"}
            and bound["workflow_sha"] == bound["trigger_sha"], "wrong_publication_workflow")
    expected = {
        "schema_version": 1, "status": "success", "command": COMMAND,
        "python_version": python_version or platform.python_version(),
        **bound, **source_identity(root, bound["trigger_sha"]),
    }
    receipt = decode(env.get("SOURCE_VALIDATION_RECEIPT"))
    require(type(receipt.get("schema_version")) is int and receipt == expected,
            "validation_receipt_identity_mismatch")
    require(installed_lock.is_file() and not installed_lock.is_symlink()
            and installed_lock.stat().st_size <= 2_000_000, "installed_lock_unavailable")
    require(hashlib.sha256(installed_lock.read_bytes()).hexdigest()
            == expected["file_sha256"]["tests/requirements.lock"], "installed_lock_mismatch")
    release = clean_head(root)
    require(release == sha(env.get("DETECTED_RELEASE_SHA")), "release_checkout_mismatch")
    if release != bound["trigger_sha"]:
        # Even an equal source tree does not transfer results to another commit.
        return "validate_release_source"
    require(source_identity(root, release) == {
        key: expected[key] for key in ("source_sha", "source_tree", "file_sha256")
    }, "release_source_mismatch")
    return "reuse_same_run"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("record", "decide"))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--installed-lock", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "record":
            require(args.github_output is not None, "missing_output_path")
            receipt = json.dumps(record(args.repo_root, os.environ), sort_keys=True, separators=(",", ":"))
            require(len(receipt.encode()) <= MAX_RECEIPT, "receipt_output_bound")
            with args.github_output.open("a", encoding="utf-8") as stream:
                stream.write("source_validation=" + receipt + "\n")
            print("Recorded successful full discovery with exact source and workflow identity.")
        else:
            require(args.installed_lock is not None, "missing_installed_lock")
            print(decide(args.repo_root, os.environ, args.installed_lock))
    except Refusal as exc:
        # Categories are fixed in this module; never print arbitrary receipt data.
        parser.exit(1, "Publication validation refused: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
