"""CI-only detector, preparation and unsigned candidate assembly (HAMCP-279)."""
import argparse
import base64
import copy
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

from core_release_evidence import (ROOT, canonical, compare_sources, context, derive_binding, digest,
    groups, manifest, passing_subset, read_json, run_identity, select_release, slot, stable_version,
    validate_provenance, verify_context, version_key)
from core_evidence_sources import CORE, REPOSITORY, github, image_provenance, source


def write(path, value):
    Path(path).write_bytes(canonical(value))


def output(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a") as file:
        for key, value in values.items():
            file.write(key + "=" + (str(value).lower() if isinstance(value, bool) else str(value)) + "\n")


def summary(message):
    print(message, flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as file:
            file.write(message + "\n")


def journal(raw):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from prepare_core_release_registry import parse_journal
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(
        (ROOT / "docs/evidence/hamcp222/core-public-key.txt").read_bytes().strip(), validate=True))
    value = parse_journal(raw, key)
    return value, key


def branch_file(ref, path):
    # Fixed repository and exact bounded branch names, never caller URLs.
    if ref not in ("registry", "main") and not (
            ref.startswith("registry-candidates/") and stable_version(ref.removeprefix("registry-candidates/"))):
        raise ValueError("unexpected source branch")
    value = github(f"repos/{REPOSITORY}/contents/{path}?ref={ref}", optional=True)
    if value is None:
        return None
    if value.get("type") != "file" or value.get("encoding") != "base64" or value["size"] > 2 * 1024 * 1024:
        raise ValueError("invalid branch artifact")
    raw = base64.b64decode(value["content"], validate=False)
    if len(raw) != value["size"]:
        raise ValueError("branch artifact size mismatch")
    return raw


def registry_input():
    ref = github(f"repos/{REPOSITORY}/git/ref/heads/registry", optional=True)
    if ref is None:
        return (ROOT / "upstream-trust/ha-core-release-registry.json").read_bytes(), False
    raw = branch_file("registry", "upstream-trust/ha-core-release-registry.json")
    if raw is None:
        raise ValueError("registry branch has no journal")
    return raw, True


def bound_entry(entry):
    from core_lane_bindings import REVIEWED_CORE_BINDINGS, provenance_name
    version = entry["version"]
    binding = REVIEWED_CORE_BINDINGS.get(version)
    if binding is not None:
        provenance = read_json(ROOT / "tests/fixtures" / provenance_name(version))
        if any(entry[k] != provenance[k] for k in provenance if k != "entry_id"):
            raise ValueError("signed predecessor disagrees with main binding provenance")
        return version, {"binding": asdict(binding), "provenance": provenance}
    # Previously inherited bindings are recovered ONLY from the evidence whose
    # exact bytes Josh signed into this predecessor's entry, never from live schema.
    raw = branch_file("registry-candidates/" + version, "core-review-evidence.json")
    if raw is None or digest(raw) != entry["evidence_sha256"]:
        raise ValueError("binding PR required: predecessor has no authenticated binding evidence")
    evidence = json.loads(raw)
    ctx = evidence["context"]
    verify_context(ctx, expected=ctx["run"])
    if (ctx.get("fixture") or ctx["provenance"]["version"] != version
            or any(entry[k] != ctx["provenance"][k] for k in ctx["provenance"] if k != "entry_id")):
        raise ValueError("predecessor binding evidence mismatch")
    return ctx["anchor"], {"binding": ctx["binding"], "provenance": ctx["provenance"]}


def new_context(anchor, binding, predecessor, mode, proof=None, fixture=False):
    value = {"model": "core-evidence-context-v1", "run": run_identity(),
             "manifest_sha256": digest(canonical(manifest())), "anchor": anchor,
             "binding": binding["binding"], "provenance": binding["provenance"],
             "predecessor": predecessor, "mode": mode, "proof": proof, "fixture": fixture}
    return verify_context(value)


def matrix_row(ctx, name):
    from support_window import load
    current = load()["ha_mcp"]["current"]
    if current["version"] != ctx["binding"]["child_consumer"]:
        raise ValueError("paired ha-mcp binding requires a binding PR")
    b = slot(ctx)
    return {"case": name, "lane": b["lane"], "support_slot": "release", "ha_version": b["version"],
            "ha_image": b["image"], "ha_mcp_version": current["version"],
            "ha_mcp_image": f"ghcr.io/homeassistant-ai/ha-mcp:{current['version']}@{current['image_index_digest']}",
            "ha_mcp_source_commit": current["source_commit"], "ha_mcp_source_sha256": current["source_archive_sha256"],
            "core_source_commit": b["source_commit"], "core_source_sha256": b["source_archive_sha256"],
            "alarmo": True, "context_sha256": digest(canonical(ctx))}


def attempt_releases(artifacts, jobs):
    prefix = "core-release-attempt-"
    return {item["name"][len(prefix):] for item in [*artifacts, *jobs]
            if item["name"].startswith(prefix) and stable_version(item["name"][len(prefix):])}


def observed_attempts(run, artifacts, jobs):
    # Only the deliberately failing refusal job is terminal. An ordinary failed
    # attempt (API, identity, provenance, setup, lanes) remains retryable.
    prefix = "core-release-refused-"
    refused = {job["name"][len(prefix):] for job in jobs
               if job["name"].startswith(prefix) and job.get("conclusion") == "failure"
               and stable_version(job["name"][len(prefix):])}
    return [{"release": release, "status": run["status"], "conclusion": run["conclusion"],
             "outcome": "refused" if release in refused else "attempt"}
            for release in sorted(attempt_releases(artifacts, jobs) | refused)]


def refusal_message(proof):
    if proof["inherited"] is not False or not proof["differing_files"]:
        raise ValueError("not an O3 refusal")
    return ("binding PR required: " + ", ".join(proof["differing_files"]) +
            "\nPredecessor commit: " + proof["predecessor_commit"] +
            "\nRelease commit: " + proof["release_commit"])


def fail_refusal(directory):
    message = refusal_message(read_json(directory / "comparison.json"))
    summary(message)
    raise SystemExit(message)


def prepare(directory, acceptance, requested):
    directory.mkdir(parents=True, exist_ok=True)
    if acceptance:
        raw = (ROOT / "upstream-trust/ha-core-release-registry.json").read_bytes(); exists = False
    else:
        if os.environ.get("GITHUB_REF") != "refs/heads/main" or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
            raise ValueError("automated detection requires protected main")
        raw, exists = registry_input()
    signed, _ = journal(raw)
    entries = [e.to_mapping() for e in signed.accepted.entries]
    (directory / "predecessor.json").write_bytes(raw)
    if acceptance:
        target = next(e for e in entries if e["version"] == "2026.10.3")
        anchor, binding = bound_entry(target)
        older = source("2026.10.1", directory); newer = source("2026.10.3", directory)
        proof = compare_sources(older, newer)
        if proof["inherited"]:
            raise ValueError("real negative unexpectedly inherited")
        write(directory / "negative.json", proof)
        summary("Real negative: binding PR required — " + ", ".join(proof["differing_files"]))
        ctx = new_context(anchor, binding, target["version"], "main")
        write(directory / "coverage.json", ctx)
        # Synthetic predecessor source ONLY. The actual target, digest and version
        # remain .3; no fictitious image or fabricated real-tag identity is claimed.
        identical = {**newer, "version": "2026.10.2"}
        fixture_proof = compare_sources(identical, newer)
        synthetic = copy.deepcopy(binding); synthetic["provenance"]["version"] = "2026.10.2"
        derived = derive_binding(synthetic, binding["provenance"], fixture_proof)
        fixture_ctx = new_context(anchor, derived, "2026.10.2", "derived", fixture_proof, fixture=True)
        write(directory / "fixture.json", fixture_ctx)
        write(directory / "baseline.json", target)
        write(directory / "state.json", {"registry_exists": False, "acceptance": True})
        rows = [matrix_row(ctx, "coverage"), matrix_row(fixture_ctx, "fixture")]
        output(refused=False, run=True, release="2026.10.3", matrix=json.dumps({"include": rows}), registry_exists=False)
        return
    releases = []
    for page in range(1, 11):
        batch = github(f"{CORE}/releases?per_page=100&page={page}"); releases.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise ValueError("release discovery exceeds pagination bound")
    newest = select_release(releases, {e["version"] for e in entries}, [], requested)
    if newest is None:
        summary("No newer eligible stable Core release.")
        output(refused=False, run=False, release="none", matrix='{"include":[]}', registry_exists=exists)
        return
    release_metadata = next(r for r in releases if r["tag_name"] == newest)
    # Candidate branches are a durable successful-assembly marker after artifact expiry.
    if github(f"repos/{REPOSITORY}/git/ref/heads/registry-candidates/{newest}", optional=True) is not None:
        summary("Candidate branch already exists; no repeated dispatch. Inspect its original run for recovery.")
        output(refused=False, run=False, release="none", matrix='{"include":[]}', registry_exists=exists)
        return
    runs = []
    for page in range(1, 11):
        batch = github(f"repos/{REPOSITORY}/actions/workflows/core-release-evidence.yml/runs?per_page=100&page={page}")["workflow_runs"]
        for r in batch:
            if str(r["id"]) == os.environ["GITHUB_RUN_ID"] or r["created_at"] < release_metadata["published_at"]:
                continue
            # Production jobs upload their selected release marker under this exact
            # title even for scheduled detection; inspect retained run artifacts below.
            artifacts = github(f"repos/{REPOSITORY}/actions/runs/{r['id']}/artifacts?per_page=100")["artifacts"]
            # Job names survive artifact expiration. Keep a durable selected-release
            # receipt in GitHub's retained run record, including O3 refusals.
            jobs = []
            for job_page in range(1, 11):
                page_jobs = github(f"repos/{REPOSITORY}/actions/runs/{r['id']}/jobs?filter=all&per_page=100&page={job_page}")["jobs"]
                jobs.extend(page_jobs)
                if len(page_jobs) < 100:
                    break
            else:
                raise ValueError("job history exceeds pagination bound")
            runs.extend(observed_attempts(r, artifacts, jobs))
        if len(batch) < 100:
            break
    else:
        raise ValueError("run discovery exceeds pagination bound")
    version = select_release(releases, {e["version"] for e in entries}, runs, newest,
                             retry_refused=(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
                                            and requested == newest))
    if version is None:
        summary("No eligible stable release (already published, pending, succeeded or terminally refused).")
        output(refused=False, run=False, release="none", matrix='{"include":[]} ', registry_exists=exists)
        return
    candidates = [e for e in entries if version_key(e["version"]) < version_key(version)]
    if not candidates:
        raise ValueError("binding PR required: no signed predecessor")
    predecessor = max(candidates, key=lambda e: version_key(e["version"]))
    anchor, binding = bound_entry(predecessor)
    before = source(predecessor["version"], directory); after = source(version, directory)
    if before["commit"] != predecessor["source_commit"]:
        raise ValueError("signed predecessor tag SHA mismatch")
    proof = compare_sources(before, after)
    write(directory / "comparison.json", proof)
    if not proof["inherited"]:
        summary(refusal_message(proof))
        output(refused=True, run=False, release=version, matrix='{"include":[]}', registry_exists=exists)
        return
    index, platforms, receipts = image_provenance(version, after["commit"])
    provenance = {"entry_id": "core-" + version + "-" + index[7:15], "server_name": "home-assistant-core",
                  "version": version, "source_commit": after["commit"], "source_tree": after["tree"],
                  "source_archive_sha256": after["archive_sha256"], "image_index_digest": index,
                  "architecture_manifests": platforms}
    derived = derive_binding(binding, provenance, proof)
    ctx = new_context(anchor, derived, predecessor["version"], "derived", proof)
    write(directory / "release.json", ctx); write(directory / "oci.json", receipts)
    write(directory / "baseline.json", predecessor)
    write(directory / "state.json", {"registry_exists": exists, "acceptance": False})
    summary("O3 inherits " + predecessor["version"] + " → " + version + ("; pre-cutover: artifact only" if not exists else ""))
    output(refused=False, run=True, release=version, matrix=json.dumps({"include": [matrix_row(ctx, "release")]}), registry_exists=exists)


def assemble(directory, receipts, case):
    ctx = context(); baseline = read_json(directory / "baseline.json")
    cleanup = read_json(receipts / "cleanup.json")
    if cleanup != {"clean": True, "run": ctx["run"], "context_sha256": os.environ["CORE_EVIDENCE_CONTEXT_SHA256"]}:
        raise ValueError("cleanup proof mismatch")
    results = {}; receipt_hashes = {}
    for name in groups():
        path = receipts / (name + ".json")
        if not path.exists():
            results[name] = False
            continue
        r = read_json(path)
        if (r.get("model") != "core-evidence-group-v1" or r["run"] != ctx["run"]
                or r["context_sha256"] != os.environ["CORE_EVIDENCE_CONTEXT_SHA256"]
                or r["release"] != ctx["provenance"]["version"] or r["group"] != name
                or r["profiles"] != groups()[name] or type(r["passed"]) is not bool):
            raise ValueError("lane receipt binding mismatch")
        results[name] = r["passed"]; receipt_hashes[name] = digest(path.read_bytes())
    refs = passing_subset(baseline["capabilities"], results)
    evidence = {"model": "core-automated-release-evidence-v1", "context": ctx,
                "predecessor_entry_sha256": digest(canonical(baseline)), "lane_groups": results,
                "receipt_sha256": receipt_hashes, "previous_capabilities": baseline["capabilities"],
                "selected_capabilities": refs,
                "withheld": [p["capability_id"] for p in baseline["capabilities"] if p not in refs]}
    state = read_json(directory / "state.json")
    if state["acceptance"] and refs != baseline["capabilities"]:
        raise ValueError("acceptance requires all 31 exact signed profile references")
    if not refs:
        raise ValueError("no passing profiles; no candidate")
    entry = {**baseline, **ctx["provenance"], "capabilities": refs,
             "evidence_sha256": digest(canonical(evidence))}
    if state["acceptance"]:
        # Existing add renews the exact .3 entry; its original entry_id is retained.
        entry["entry_id"] = baseline["entry_id"]
    out = receipts / "candidate"; out.mkdir()
    write(out / "entry.json", entry); write(out / "core-review-evidence.json", evidence)
    subprocess.run([sys.executable, str(ROOT / "scripts/prepare_core_release_registry.py"), "add",
        "--entry", str(out / "entry.json"), "--evidence", str(out / "core-review-evidence.json"),
        "--public-key", str(ROOT / "docs/evidence/hamcp222/core-public-key.txt"),
        "--previous", str(directory / "predecessor.json"), "--output", str(out / "core-candidate.json")], check=True)
    hashes = {"candidate_sha256": digest((out / "core-candidate.json").read_bytes()),
              "evidence_sha256": digest((out / "core-review-evidence.json").read_bytes())}
    write(out / "comparison.json", {"case": case, "selected": refs, "predecessor": baseline["capabilities"],
                                    "equal": refs == baseline["capabilities"], "run": ctx["run"], **hashes})
    summary(f"{case}: {len(refs)} selected; WITHHELD: " + (", ".join(evidence["withheld"]) or "none"))
    output(**hashes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "assemble", "refuse"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--acceptance", action="store_true")
    parser.add_argument("--release", default="")
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--case", default="release")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.directory, args.acceptance, args.release or None)
    elif args.command == "refuse":
        fail_refusal(args.directory)
    else:
        assemble(args.directory, args.receipts, args.case)
