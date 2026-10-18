"""Bind the promoted image digest to the same-run build and anonymous verification.

The token-holding promote job publishes only the digest that this check emits.
Fresh publication requires the build job's digest to equal the digest that the
read-only verify-image job resolved and verified, and every contributing job to
have run in this exact run attempt. Digest recovery requires the owner-supplied
digest and no fresh build. Standard library only; no network, credentials,
registry access or publication authority is introduced here.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
ATTEMPT = re.compile(r"[1-9][0-9]{0,2}\Z")
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}\Z")
BUILD_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
NOTES = re.compile(r"docs/[A-Za-z0-9._/-]+_RELEASE_NOTES\.md\Z")
PLATFORMS = ("linux/amd64", "linux/arm64", "linux/arm/v7")


class Refusal(ValueError):
    pass


def require(condition, category):
    if not condition:
        raise Refusal(category)


def exact(env, name, pattern, category):
    value = env.get(name, "")
    require(isinstance(value, str) and pattern.fullmatch(value), category)
    return value


def checkout_head(root):
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal("checkout_identity_unavailable") from exc
    return result.stdout.strip()


def bind(env, root, head=checkout_head):
    """Return the one digest the promote job may publish, or refuse."""
    mode = env.get("SOURCE_MODE")
    require(mode in {"build", "resume_digest"}, "source_mode_unsupported")

    # A rerun of failed jobs reuses earlier job outputs under a new attempt.
    # Every digest-bearing job must belong to this exact attempt.
    current = exact(env, "CURRENT_RUN_ATTEMPT", ATTEMPT, "run_attempt_invalid")
    require(env.get("CLAIM_RUN_ATTEMPT") == current, "claim_attempt_mismatch")
    require(env.get("APPROVED_RUN_ATTEMPT") == current, "verification_attempt_mismatch")

    release = exact(env, "DETECTED_RELEASE_SHA", COMMIT, "release_sha_invalid")
    require(env.get("PREPARED_RELEASE_SHA") == release, "prepared_release_mismatch")
    require(env.get("SOURCE_MAIN_SHA") == release, "source_main_mismatch")
    require(head(root) == release, "checkout_release_mismatch")

    version = exact(env, "PREPARED_VERSION", VERSION, "version_invalid")
    require(env.get("DETECTED_EXPECTED_VERSION") == version, "version_mismatch")
    require(env.get("PREPARED_RELEASE_TAG") == "v" + version, "release_tag_mismatch")

    build_time = exact(env, "BUILD_TIME", BUILD_TIME, "build_time_invalid")
    platforms = env.get("BUILD_PLATFORMS", "").split(",")
    require(set(platforms) <= set(PLATFORMS) and len(set(platforms)) == len(platforms)
            and {"linux/amd64", "linux/arm64"} <= set(platforms), "platforms_invalid")

    notes = exact(env, "RELEASE_NOTES_PATH", NOTES, "release_notes_invalid")
    notes_path = Path(root, notes)
    require(".." not in Path(notes).parts and notes_path.is_file()
            and not notes_path.is_symlink(), "release_notes_unavailable")

    built = env.get("BUILT_DIGEST", "")
    recovered = env.get("RECOVERY_DIGEST", "")
    if mode == "build":
        require(env.get("BUILD_RUN_ATTEMPT") == current, "build_attempt_mismatch")
        require(DIGEST.fullmatch(built) and not recovered, "built_digest_invalid")
        require(not env.get("DETECTED_RECOVERY_BUILD_TIME"), "fresh_build_carries_recovery")
        digest = built
    else:
        require(not built and not env.get("BUILD_RUN_ATTEMPT"), "recovery_attempted_build")
        require(DIGEST.fullmatch(recovered), "recovery_digest_invalid")
        require(env.get("DETECTED_RECOVERY_BUILD_TIME") == build_time, "recovery_build_time_mismatch")
        digest = recovered

    require(env.get("APPROVED_DIGEST") == digest, "approved_digest_mismatch")
    require(env.get("VERIFIED_MANIFEST_DIGEST") == digest, "verified_manifest_digest_mismatch")
    require(env.get("SOURCE_IMAGE_VERIFIED") == "true", "source_image_unverified")
    return {
        "digest": digest,
        "source_image_published": "true" if mode == "build" else "false",
        "source_image_reused": "true" if mode == "resume_digest" else "false",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        bound = bind(os.environ, args.repo_root)
        with args.github_output.open("a", encoding="utf-8") as output:
            for key, value in bound.items():
                output.write(f"{key}={value}\n")
        print("Promotion digest is bound to this attempt's build and anonymous verification.")
    except Refusal as exc:
        # Categories are fixed in this module; never echo arbitrary inputs.
        parser.exit(1, "Publication digest binding refused: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
