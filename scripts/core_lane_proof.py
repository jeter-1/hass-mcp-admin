"""HAMCP-443 D3: the CI-mode proof is set equality against the authenticated baseline.

With B = the context's authenticated baseline entry and G = the group's capability ids:
1. G is a subset of ids(B);
2. predecessor == B's references minus exactly those whose capability_id is in G (signed order);
3. extended == predecessor plus exactly the COMPILED references for G, each equal to B's.
Rule 4 (both authorities run: the predecessor refuses, the extended one admits) and rule 6
(test signing in memory) stay in each acceptance run(); rule 5 (the running Core's version and
digest equal the context's) is the CI lane host's. Refusal codes are code-owned.
"""
import os
from pathlib import Path

from core_release_evidence import CONTEXT_ENV, canonical, groups, read_json

PROVENANCE_KEYS = ("server_name", "version", "source_commit", "source_tree", "source_archive_sha256",
                   "image_index_digest", "architecture_manifests")


class ProofRefusal(ValueError):
    pass


def same(left, right):
    # Canonical bytes: dict equality alone would accept True == 1 or 1 == 1.0.
    return canonical(left) == canonical(right)


def authenticated_baseline(ctx, directory=None):
    """B: baseline.json must equal one accepted entry of the signed predecessor.json journal."""
    from core_evidence_workflow import journal  # The one journal verification; never a second.
    directory = Path(os.environ[CONTEXT_ENV]).parent if directory is None else Path(directory)
    signed, _ = journal((directory / "predecessor.json").read_bytes())
    baseline = read_json(directory / "baseline.json")
    if not any(same(baseline, entry.to_mapping()) for entry in signed.accepted.entries):
        raise ProofRefusal("baseline_not_signed")
    provenance = ctx["provenance"]
    expected = provenance["version"] if ctx["fixture"] else ctx["predecessor"]
    if baseline.get("version") != expected:
        raise ProofRefusal("baseline_version_mismatch")
    if ctx["mode"] == "main" or ctx["fixture"]:
        if any(not same(baseline.get(k), provenance[k]) for k in PROVENANCE_KEYS):
            raise ProofRefusal("baseline_provenance_mismatch")
    if ctx["mode"] == "derived" and baseline.get("source_commit") != ctx["proof"]["predecessor_commit"]:
        raise ProofRefusal("baseline_commit_mismatch")
    return baseline


def verify_transition(baseline, group, predecessor, extended):
    mapping = groups()
    if group not in mapping:
        raise ProofRefusal("unknown_group")
    wanted = mapping[group]
    signed = baseline["capabilities"]
    ids = [ref["capability_id"] for ref in signed]
    if len(ids) != len(set(ids)):
        raise ProofRefusal("baseline_duplicate_profile")
    if not set(wanted) <= set(ids):  # Rule 1.
        raise ProofRefusal("group_not_in_baseline")
    if not same(predecessor, [ref for ref in signed if ref["capability_id"] not in wanted]):  # Rule 2.
        raise ProofRefusal("predecessor_not_baseline_minus_group")
    added = extended[len(predecessor):]
    if (not same(extended[:len(predecessor)], predecessor)  # Rule 3.
            or sorted(ref["capability_id"] for ref in added) != sorted(wanted)
            or any(not same(ref, next(r for r in signed if r["capability_id"] == ref["capability_id"]))
                   for ref in added)):
        raise ProofRefusal("extended_not_predecessor_plus_compiled")


def transition(baseline, group, compiled):
    """(predecessor, extended) for one group; compiled references come from the Engineering code."""
    wanted = groups().get(group)
    if wanted is None:
        raise ProofRefusal("unknown_group")
    if sorted(ref["capability_id"] for ref in compiled) != sorted(wanted):
        raise ProofRefusal("compiled_group_mismatch")
    predecessor = [ref for ref in baseline["capabilities"] if ref["capability_id"] not in wanted]
    extended = predecessor + list(compiled)
    verify_transition(baseline, group, predecessor, extended)
    return predecessor, extended


def release_entry(baseline, ctx):
    """The test-signed entry: B's semantics under the context's own release provenance."""
    return {**baseline, **ctx["provenance"]}
