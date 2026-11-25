"""CI-only detector, preparation and unsigned candidate assembly (HAMCP-279)."""
import argparse
import base64
import copy
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from core_release_evidence import (EXTERNAL_DEPENDENCY, LANE_GROUPS, ROOT, WITHHOLDABLE, canonical,
    compare_sources, context, derive_binding, digest, groups, manifest, passing_subset, read_json,
    run_identity, select_release, slot, stable_version, validate_provenance, verify_context, version_key)
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


def group_matrix(rows, withhold):
    """D1: one lane-groups row per case and lane group, in groups.json order, carrying its lanes row's
    exact context binding. An owner-withheld group gets no row, so it has no producer."""
    names = [name for name in groups() if name in LANE_GROUPS and name not in withhold]
    return json.dumps({"include": [{"case": row["case"], "group": name, "context_sha256": row["context_sha256"],
                                    "core_source_commit": row["core_source_commit"],
                                    "core_source_sha256": row["core_source_sha256"]}
                                   for row in rows for name in names]})


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


# Scheduled discovery reads newest-first and stops only after the page holding the
# newest signed version's own release record. Version comparison never ends the scan:
# a lower backport can be listed before a higher release. The bound therefore limits
# releases created after that predecessor (100 per page), not Core's history.
# Created-at order is enforced only across official stable records: GitHub's listing
# does not keep prereleases in created_at order (2026.4.0b10 follows 2026.4.0b9).
RELEASE_PAGE_BOUND = 10


def official(release):
    return (not release.get("draft") and not release.get("prerelease")
            and stable_version(release.get("tag_name")))


def listing_timestamp(value):
    # The one parser for both fields: an ISO 8601 string carrying a UTC offset.
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        stamp = None
    if stamp is None or stamp.utcoffset() is None:
        raise ValueError("release listing order cannot be established")
    return stamp


def listed_at(release):
    # created_at is the order checked. published_at is used only when created_at is
    # absent (missing or null); a present but invalid created_at refuses, never falls back.
    field = "published_at" if release.get("created_at") is None else "created_at"
    return listing_timestamp(release.get(field))


def discover_releases(entries, requested):
    if requested is not None:
        # Exact tag only, no listing; the version is checked before it shapes the path.
        if not stable_version(requested):
            raise ValueError("requested release is not an official stable release")
        release = github(f"{CORE}/releases/tags/{requested}", optional=True)
        if not isinstance(release, dict) or release.get("tag_name") != requested:
            raise ValueError("requested release is not an official stable release")
        return [release]
    if not entries:
        raise ValueError("binding PR required: no signed predecessor")
    predecessor = max((e["version"] for e in entries), key=version_key)
    releases = []; previous = None
    for page in range(1, RELEASE_PAGE_BOUND + 1):
        batch = github(f"{CORE}/releases?per_page=100&page={page}"); releases.extend(batch)
        for release in batch:
            stamp = listed_at(release)  # Every record read must carry a valid timestamp.
            if not official(release):
                # Never selectable and never the stop record: its position changes neither
                # the pages read nor the version selected, so it is not order-compared.
                continue
            if previous is not None and stamp > previous:
                raise ValueError("release listing is not newest-first")
            previous = stamp
        if any(official(r) and r["tag_name"] == predecessor for r in batch):
            return releases
        if len(batch) < 100:
            # Exhausted: the listing is the complete candidate set.
            return releases
    raise ValueError("release discovery did not reach the signed predecessor within the page bound")


# The workflow_dispatch withhold_groups input (HAMCP-443 part C): exactly these values, nothing parsed.
WITHHOLD_INPUTS = {"": [], "marketplace_read": ["marketplace_read"]}


def withhold_input(value):
    """The dispatch input as prepare's withhold list; any other value refuses."""
    if value not in WITHHOLD_INPUTS:
        raise ValueError("withhold_groups must be '' or 'marketplace_read'")
    return list(WITHHOLD_INPUTS[value])


def admit_withhold(withhold, acceptance, requested):
    """D6: an owner withhold is a manual dispatch for one named production release only."""
    withhold = list(withhold)
    if not withhold:
        return []
    if acceptance or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or not requested:
        raise ValueError("withhold requires a manual dispatch for one named production release")
    if len(set(withhold)) != len(withhold) or not set(withhold) <= WITHHOLDABLE:
        raise ValueError("withhold names a group that may not be withheld")
    return sorted(withhold)


def prepare(directory, acceptance, requested, withhold=()):
    withhold = admit_withhold(withhold, acceptance, requested)
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
        target = next(e for e in entries if e["version"] == "2026.11.0")
        anchor, binding = bound_entry(target)
        older = source("2026.10.3", directory); newer = source("2026.11.0", directory)
        proof = compare_sources(older, newer)
        if proof["inherited"]:
            raise ValueError("real negative unexpectedly inherited")
        write(directory / "negative.json", proof)
        summary("Real negative: binding PR required — " + ", ".join(proof["differing_files"]))
        ctx = new_context(anchor, binding, target["version"], "main")
        write(directory / "coverage.json", ctx)
        # Synthetic predecessor source ONLY. The actual target, digest and version
        # remain 2026.11.0; no fictitious image or fabricated real-tag identity is claimed.
        identical = {**newer, "version": "2026.10.4"}
        fixture_proof = compare_sources(identical, newer)
        synthetic = copy.deepcopy(binding); synthetic["provenance"]["version"] = "2026.10.4"
        derived = derive_binding(synthetic, binding["provenance"], fixture_proof)
        fixture_ctx = new_context(anchor, derived, "2026.10.4", "derived", fixture_proof, fixture=True)
        write(directory / "fixture.json", fixture_ctx)
        write(directory / "baseline.json", target)
        write(directory / "state.json", {"registry_exists": False, "acceptance": True, "withhold": []})
        rows = [matrix_row(ctx, "coverage"), matrix_row(fixture_ctx, "fixture")]
        output(refused=False, run=True, release="2026.11.0", matrix=json.dumps({"include": rows}),
               groups=group_matrix(rows, []), registry_exists=False)
        return
    releases = discover_releases(entries, requested)
    newest = select_release(releases, {e["version"] for e in entries}, [], requested)
    if newest is None:
        summary("No newer eligible stable Core release.")
        output(refused=False, run=False, release="none", matrix='{"include":[]}', groups='{"include":[]}', registry_exists=exists)
        return
    release_metadata = next(r for r in releases if r["tag_name"] == newest)
    # Candidate branches are a durable successful-assembly marker after artifact expiry.
    if github(f"repos/{REPOSITORY}/git/ref/heads/registry-candidates/{newest}", optional=True) is not None:
        summary("Candidate branch already exists; no repeated dispatch. Inspect its original run for recovery.")
        output(refused=False, run=False, release="none", matrix='{"include":[]}', groups='{"include":[]}', registry_exists=exists)
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
                                            and requested == newest), withhold=withhold)
    if version is None:
        summary("No eligible stable release (already published, pending, succeeded or terminally refused).")
        output(refused=False, run=False, release="none", matrix='{"include":[]} ', groups='{"include":[]}', registry_exists=exists)
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
    from core_lane_bindings import REVIEWED_CORE_BINDINGS, provenance_name
    if version in REVIEWED_CORE_BINDINGS:
        # A reviewed binding PR (HAMCP-489): the release is its own main anchor, pinned by its committed
        # fixture, which must equal the live source and image. Any disagreement refuses; never derived.
        fixture = read_json(ROOT / "tests/fixtures" / provenance_name(version))
        index, platforms, receipts = image_provenance(version, after["commit"])
        if (fixture["version"], fixture["source_commit"], fixture["source_tree"], fixture["source_archive_sha256"],
                fixture["image_index_digest"], fixture["architecture_manifests"]) != (
                version, after["commit"], after["tree"], after["archive_sha256"], index, platforms):
            raise ValueError("reviewed_binding_mismatch")
        ctx = new_context(version, {"binding": asdict(REVIEWED_CORE_BINDINGS[version]), "provenance": fixture},
                          predecessor["version"], "main")
        write(directory / "release.json", ctx); write(directory / "oci.json", receipts)
        write(directory / "baseline.json", predecessor)
        write(directory / "state.json", {"registry_exists": exists, "acceptance": False, "withhold": withhold})
        if withhold:
            summary(withhold_consequence(withhold))
        summary("Reviewed binding " + version + " (main) over " + predecessor["version"]
                + ("; pre-cutover: artifact only" if not exists else ""))
        row = matrix_row(ctx, "release")
        output(refused=False, run=True, release=version, matrix=json.dumps({"include": [row]}),
               groups=group_matrix([row], withhold), registry_exists=exists)
        return
    if not proof["inherited"]:
        summary(refusal_message(proof))
        output(refused=True, run=False, release=version, matrix='{"include":[]}', groups='{"include":[]}', registry_exists=exists)
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
    write(directory / "state.json", {"registry_exists": exists, "acceptance": False, "withhold": withhold})
    if withhold:
        summary(withhold_consequence(withhold))
    summary("O3 inherits " + predecessor["version"] + " → " + version + ("; pre-cutover: artifact only" if not exists else ""))
    row = matrix_row(ctx, "release")
    output(refused=False, run=True, release=version, matrix=json.dumps({"include": [row]}),
           groups=group_matrix([row], withhold), registry_exists=exists)


def producer_name(case, group):
    return "core-evidence-group-" + case + "-" + group


def owned_names(value):
    return (isinstance(value, list) and value and value == sorted(set(value))
            and all(isinstance(v, str) and v for v in value))


def group_receipt_paths(receipts, case, withhold):
    """D8's fixed producer map. The seven lane groups come only from their own lane-groups
    artifact (exactly <group>.json and cleanup-<group>.json); the other groups only from this
    lanes run's top level. A wrong producer, a duplicate or any stray entry refuses.

    Every lane group's artifact must be present, in every mode (HAMCP-443 part C). The one
    exception is an owner-withheld group: prepare gives it no lane-groups row, so its artifact
    must be absent, and one that is present refuses."""
    run_groups = [name for name in groups() if name not in LANE_GROUPS]
    producers = {producer_name(case, name): name for name in LANE_GROUPS}
    allowed = {"cleanup.json"} | {name + ".json" for name in run_groups} | set(producers)
    present = {entry.name for entry in receipts.iterdir()}
    if present - allowed:
        raise ValueError("group receipt from an unmapped producer: " + ", ".join(sorted(present - allowed)))
    paths, cleanups = {}, {}
    for directory, name in producers.items():
        folder = receipts / directory
        paths[name] = folder / (name + ".json")
        if name in withhold:
            if directory in present:
                raise ValueError("withheld lane group has an artifact: " + name)
            continue  # assemble records the group as not passed
        if directory not in present:
            raise ValueError("lane-groups artifact missing: " + name)
        if not folder.is_dir() or folder.is_symlink() or {e.name for e in folder.iterdir()} != {
                name + ".json", "cleanup-" + name + ".json"}:
            raise ValueError("lane-groups artifact contents mismatch: " + name)
        cleanups[name] = folder / ("cleanup-" + name + ".json")
    for name in run_groups:
        paths[name] = receipts / (name + ".json")
    if any(path.is_symlink() for path in [*paths.values(), *cleanups.values(), receipts / "cleanup.json"]):
        raise ValueError("group receipt is not a regular file")
    return paths, cleanups


def owner_withhold(state):
    withhold = state["withhold"]
    if (not isinstance(withhold, list) or withhold != sorted(set(withhold))
            or not set(withhold) <= WITHHOLDABLE or (withhold and state["acceptance"])):
        raise ValueError("invalid owner withhold")
    return withhold


def withhold_consequence(withhold):
    profiles = [p for name in withhold for p in groups()[name]]
    return ("OWNER OVERRIDE: withheld " + ", ".join(withhold) + ". The signed release will lack "
            + ", ".join(profiles) + ", and every later automated candidate will lack it too, because "
            "automated evidence never adds a profile back. Only a manual signing restores it.")


def assemble(directory, receipts, case):
    ctx = context(); baseline = read_json(directory / "baseline.json")
    sha = os.environ["CORE_EVIDENCE_CONTEXT_SHA256"]
    state = read_json(directory / "state.json")
    if set(state) != {"registry_exists", "acceptance", "withhold"}:
        raise ValueError("preparation state mismatch")
    withhold = owner_withhold(state)
    paths, cleanups = group_receipt_paths(receipts, case, withhold)
    cleanup = read_json(receipts / "cleanup.json")
    if cleanup != {"clean": True, "run": ctx["run"], "context_sha256": sha}:
        raise ValueError("cleanup proof mismatch")
    for name, path in cleanups.items():
        proof = read_json(path)
        owned = proof.get("owned")
        if (set(proof) != {"clean", "run", "context_sha256", "group", "owned"} or proof["clean"] is not True
                or proof["run"] != ctx["run"] or proof["context_sha256"] != sha or proof["group"] != name
                or not isinstance(owned, dict) or set(owned) != {"containers", "networks"}
                or not all(owned_names(v) for v in owned.values())):
            raise ValueError("lane-group cleanup proof mismatch: " + name)
    results = {}; receipt_hashes = {}; unavailable = []
    for name in groups():
        path = paths[name]
        if not path.exists():
            results[name] = False
            continue
        r = read_json(path)
        if (r.get("model") != "core-evidence-group-v1" or r["run"] != ctx["run"]
                or r["context_sha256"] != sha
                or r["release"] != ctx["provenance"]["version"] or r["group"] != name
                or r["profiles"] != groups()[name] or type(r["passed"]) is not bool):
            raise ValueError("lane receipt binding mismatch")
        results[name] = r["passed"]; receipt_hashes[name] = digest(path.read_bytes())
        if r["passed"] is False and r.get("receipt") == {"failure_type": EXTERNAL_DEPENDENCY} and name not in withhold:
            unavailable.append(name)
    if unavailable:
        # D6: a FAILED run, raised before any candidate exists; select_release re-selects it.
        summary("FAILED: external dependency unavailable for " + ", ".join(unavailable)
                + "; no candidate. The next scheduled run retries this release.")
        raise ValueError("external dependency unavailable: " + ", ".join(unavailable))
    for name in withhold:
        results[name] = False
    refs = passing_subset(baseline["capabilities"], results)
    evidence = {"model": "core-automated-release-evidence-v1", "context": ctx,
                "predecessor_entry_sha256": digest(canonical(baseline)), "lane_groups": results,
                "receipt_sha256": receipt_hashes, "previous_capabilities": baseline["capabilities"],
                "selected_capabilities": refs, "owner_withheld": withhold,
                "withheld": [p["capability_id"] for p in baseline["capabilities"] if p not in refs]}
    if withhold:
        summary(withhold_consequence(withhold))
    if state["acceptance"] and refs != baseline["capabilities"]:
        raise ValueError("acceptance requires every exact signed profile reference in the predecessor entry")
    if not refs:
        raise ValueError("no passing profiles; no candidate")
    entry = {**baseline, **ctx["provenance"], "capabilities": refs,
             "evidence_sha256": digest(canonical(evidence))}
    if state["acceptance"]:
        # Existing add renews the exact .3 entry; its original entry_id is retained.
        entry["entry_id"] = baseline["entry_id"]
    elif ctx["mode"] == "main" and ctx["predecessor"] != ctx["provenance"]["version"]:
        # A new reviewed anchor (HAMCP-489): a reviewed name, never the fixture's disposable entry_id.
        entry["entry_id"] = "ha-core-" + ctx["provenance"]["version"] + "-reviewed-v1"
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
    parser.add_argument("--withhold-groups", default=None)
    args = parser.parse_args()
    if args.withhold_groups is not None and args.command != "prepare":
        parser.error("--withhold-groups is a prepare input; assemble reads it from state.json")
    if args.command == "prepare":
        prepare(args.directory, args.acceptance, args.release or None, withhold_input(args.withhold_groups or ""))
    elif args.command == "refuse":
        fail_refusal(args.directory)
    else:
        assemble(args.directory, args.receipts, args.case)
