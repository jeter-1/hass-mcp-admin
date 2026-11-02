"""Fixed-target candidate branch + dispatch. Never signs or writes registry."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from core_release_evidence import canonical, digest, handoff_request, read_json
from core_evidence_sources import REPOSITORY, github
from core_evidence_workflow import branch_file

WORKFLOW = "core-release-registry-publish.yml"
REMOTE = "https://github.com/jeter-1/hass-mcp-admin.git"
FILES = ("core-candidate.json", "core-review-evidence.json")


def run(argv, cwd):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=90)
    if result.returncode:
        raise ValueError("candidate branch operation failed; inspect remote before retry")
    return result.stdout


def handoff(directory):
    directory = Path(directory)
    candidate = (directory / FILES[0]).read_bytes(); evidence = (directory / FILES[1]).read_bytes()
    version = os.environ["CORE_VERSION"]
    dry = os.environ.get("DRY_RUN") != "false"
    # Re-observe cutover, never trust only a boolean passed from another job.
    exists = github(f"repos/{REPOSITORY}/git/ref/heads/registry", optional=True) is not None
    request = handoff_request(version, candidate, evidence, registry_exists=exists, dry_run=dry)
    if request is None:
        return "artifact-only"
    if (os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}):
        raise ValueError("handoff requires main production event")
    if (digest(candidate) != os.environ.get("CANDIDATE_SHA256")
            or digest(evidence) != os.environ.get("EVIDENCE_SHA256")):
        raise ValueError("candidate hashes differ from same-run outputs")
    value = json.loads(candidate); review = json.loads(evidence)
    if (review["context"].get("fixture") or review["context"]["provenance"]["version"] != version
            or value["review_evidence_sha256"] != digest(evidence)):
        raise ValueError("fixture or mismatched evidence cannot be handed off")
    previous = branch_file("registry", "upstream-trust/ha-core-release-registry.json")
    if previous is None or digest(previous) != value["previous_journal_sha256"]:
        raise ValueError("registry predecessor changed; no push or dispatch")
    branch = request["ref"]
    # A fresh isolated repository prevents unrelated checkout content entering the branch.
    with tempfile.TemporaryDirectory(prefix="core-candidate-") as temp:
        run(["git", "init", "--quiet", temp], temp)
        remote = github(f"repos/{REPOSITORY}/git/ref/heads/{branch}", optional=True)
        if remote is not None:
            run(["git", "fetch", "--quiet", "--no-tags", "--depth=1", REMOTE, "refs/heads/" + branch], temp)
            names = run(["git", "ls-tree", "--name-only", "FETCH_HEAD"], temp).decode().splitlines()
            if sorted(names) != sorted(FILES):
                raise ValueError("existing candidate branch contains different files")
            for name, raw in zip(FILES, (candidate, evidence)):
                if run(["git", "show", "FETCH_HEAD:" + name], temp) != raw:
                    raise ValueError("existing candidate branch has different content")
        else:
            for name, raw in zip(FILES, (candidate, evidence)):
                (Path(temp) / name).write_bytes(raw)
            run(["git", "add", "--", *FILES], temp)
            run(["git", "-c", "user.name=github-actions[bot]", "-c",
                 "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit", "--quiet",
                 "-m", "HAMCP-279: unsigned Core " + version + " candidate"], temp)
            # Normal non-force push; a racing differing branch refuses. Never retry blindly.
            run(["git", "-c", "credential.helper=!gh auth git-credential", "push", REMOTE, "HEAD:refs/heads/" + branch], temp)
    # Exact workflow and main are constants, not values from a downloaded artifact.
    subprocess.run(["gh", "workflow", "run", WORKFLOW, "--repo", REPOSITORY, "--ref", "main", "--json"],
                   input=canonical(request["inputs"]), check=True, timeout=90)
    return "dispatched-for-owner-approval"


if __name__ == "__main__":
    import sys
    print(handoff(sys.argv[1]))
