"""Create-only publication attempt records; a failed attempt never authorizes a rebuild.

The annotated tag is audit/dispatch state, not a release tag. Creation uses the
Git refs API's create operation, never update/force. An uncertain POST stops;
there is no automatic mutation retry and no cleanup of consumed authority.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from publication_handoff import PREFIX, REPOSITORY, check_binding, git, read_api, sha
from validate_ci_provenance import Refusal, positive_id, require, unique_keys


def ref(version):
    require(isinstance(version, str) and re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}", version),
            "invalid_version")
    return "tags/engineering-publication-attempt/v" + version


def post(route, payload):
    try:
        result = subprocess.run(
            ["gh", "api", route, "--hostname", "github.com", "--method", "POST", "--input", "-"],
            input=json.dumps(payload), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal("claim_write_uncertain_no_retry") from exc
    require(result.returncode == 0 and len(result.stdout) < 16384, "claim_write_refused_or_uncertain")
    return json.loads(result.stdout, object_pairs_hook=unique_keys)


def identity(env, release, version, handoff=""):
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY
            and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/.github/workflows/publish-rc-image.yml@refs/heads/main"
            and env.get("GITHUB_WORKFLOW_SHA") == env.get("GITHUB_SHA"),
            "wrong_claim_repository_or_ref")
    require(env.get("GITHUB_EVENT_NAME") in {"push", "workflow_dispatch", "workflow_run"},
            "wrong_claim_event")
    ref(version)
    value = {
        "schema": 1, "release_sha": sha(release), "version": version,
        "workflow_sha": sha(env.get("GITHUB_SHA")),
        "run_id": positive_id(int(env["GITHUB_RUN_ID"])),
        "run_attempt": positive_id(int(env["GITHUB_RUN_ATTEMPT"])),
        "event": env["GITHUB_EVENT_NAME"],
    }
    if value["event"] == "workflow_run":
        require(isinstance(handoff, str) and 0 < len(handoff) <= 2048, "handoff_binding_missing")
        value["handoff"] = check_binding(json.loads(handoff, object_pairs_hook=unique_keys), release)
        value["schema"] = 2
    else:
        require(not handoff, "unexpected_handoff_binding")
    return value


def check(value):
    require(isinstance(value, dict), "claim_shape")
    automatic = value.get("event") == "workflow_run"
    fields = {"schema", "release_sha", "version", "workflow_sha", "run_id", "run_attempt", "event"}
    require(set(value) == fields | ({"handoff"} if automatic else set()), "claim_shape")
    require(type(value["schema"]) is int and value["schema"] == (2 if automatic else 1), "claim_schema")
    ref(value["version"])
    sha(value["release_sha"])
    sha(value["workflow_sha"])
    positive_id(value["run_id"])
    positive_id(value["run_attempt"])
    require(value["event"] in {"push", "workflow_dispatch", "workflow_run"}, "claim_event")
    if automatic:
        check_binding(value["handoff"], value["release_sha"])


def read(version, api=read_api):
    reference = api(f"{PREFIX}/git/ref/{ref(version)}")
    require(reference.get("ref") == "refs/" + ref(version)
            and reference.get("object", {}).get("type") == "tag", "claim_reference_mismatch")
    tag_sha = sha(reference["object"].get("sha"))
    tag = api(f"{PREFIX}/git/tags/{tag_sha}")
    require(tag.get("sha") == tag_sha and tag.get("tag") == ref(version)[5:]
            and tag.get("object", {}).get("type") == "commit", "claim_tag_mismatch")
    message = tag.get("message")
    require(isinstance(message, str) and len(message) <= 4096, "claim_message_bound")
    value = json.loads(message, object_pairs_hook=unique_keys)
    check(value)
    require(value["version"] == version
            and tag["object"].get("sha") == value["release_sha"], "claim_target_mismatch")
    return value


def create(value, mutate=post, api=read_api):
    check(value)
    version = value["version"]
    tag = mutate(f"{PREFIX}/git/tags", {
        "tag": ref(version)[5:], "message": json.dumps(value, sort_keys=True),
        "object": value["release_sha"], "type": "commit",
    })
    tag_sha = sha(tag.get("sha"))
    # GitHub rejects creation when this ref already exists, including an
    # identical target. Thus concurrency does not depend on a prior GET.
    result = mutate(f"{PREFIX}/git/refs", {"ref": "refs/" + ref(version), "sha": tag_sha})
    require(result.get("ref") == "refs/" + ref(version)
            and result.get("object", {}).get("sha") == tag_sha, "claim_create_unverified")
    require(read(version, api) == value, "claim_readback_mismatch")


def resume(value, run_id, run_attempt, workflow_sha, source_event, api=read_api):
    claim = read(value["version"], api)
    expected = {
        "schema": 1, "release_sha": value["release_sha"], "version": value["version"],
        "run_id": positive_id(run_id), "run_attempt": positive_id(run_attempt),
        "workflow_sha": sha(workflow_sha), "event": source_event,
    }
    if source_event == "workflow_run":
        expected.update(schema=2, handoff=claim.get("handoff"))
    require(claim == expected, "recovery_claim_mismatch")
    return claim.get("handoff")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("create", "resume"))
    parser.add_argument("--release", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--workflow-sha")
    parser.add_argument("--event")
    parser.add_argument("--handoff", default="")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        value = identity(os.environ, args.release, args.version, args.handoff)
        binding = value.get("handoff")
        if args.operation == "create":
            create(value)
        else:
            # Historical owner-dispatch runs predate claims. Preserve only that
            # legacy recovery; a new run cannot opt out by omitting its marker.
            marker = git("ls-tree", "--name-only", sha(args.workflow_sha), "--",
                         "scripts/publication_claim.py")
            if marker:
                binding = resume(value, args.run_id, args.attempt, args.workflow_sha, args.event)
            else:
                require(args.event == "workflow_dispatch", "unclaimed_automatic_recovery")
        if args.github_output:
            with args.github_output.open("a") as output:
                encoded = json.dumps(binding, sort_keys=True, separators=(",", ":")) if binding else ""
                output.write("source_handoff=" + encoded + "\n")
        print("Publication attempt authority verified; no rebuild retry is authorized.")
    except (Refusal, ValueError, KeyError, TypeError, AttributeError, OSError):
        parser.exit(1, "Publication claim refused or uncertain; inspect existing attempt before recovery.\n")


if __name__ == "__main__":
    main()
