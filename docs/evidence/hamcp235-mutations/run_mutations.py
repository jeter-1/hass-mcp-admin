"""HAMCP-235 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery: a disposable copy of the tracked
tree, every fragment matching exactly once before anything runs, and
fail-fast suites per mutant. ``test_hamcp235_baseline_usability`` (the
live-shaped suite) runs first, then the HAMCP-225/226/228 suites.

Per AGENTS.md (HAMCP-237), the work directory and every mutant's TMPDIR live
under the worktree's ignored ``.artifacts/``; the copy skips ``.artifacts``,
so it never copies itself.

The task's classes each have mutations:
- ``G*``: a comparison-level caveat produced as a per-row ``UNKNOWN`` (an
  unestablished installation or authority continuity as a row reason), or
  the caveat block lost, narrowed or the wrong rows marked ``timed_out``;
- ``V*``: a volatile input in the resume or capture fence (the authority
  generation, an automation's on/off state, the declared or unmapped count),
  or the first-fence anchor lost;
- ``R*``: a refused resume without its fixed category, or with an unlisted
  one;
- ``F*``: the final fence (its reserve, its identity-first reads, its
  recorded reason, identity drift refused).

Run 1 (``run1.log``): baseline 229 tests OK; 43 of 43 mutants killed, none
by an import or syntax error. ``G13``/``G14`` needed the earlier-side timeout
test added before the run.

Review 1 corrections (R1-M1, owner decision D4; R1-M2): ``V8``-``V11`` and
``F1``-``F4`` follow the corrected code; ``V12``-``V14``, ``R10`` and
``F10``-``F13`` are new: an inherited row taking the current fence's anchor,
an anchorless resume accepted or refused under another category, the reserve
reverted to its round-1 sizing, and the window not bounding in-flight reads.
Run 2 (``run2.log``, ``results.json``): baseline 236 tests OK; 51 of 51
mutants killed, none by an import or syntax error.

Usage:
    systemd-run --user --scope -p MemoryMax=3G -p OOMPolicy=continue \\
        python docs/evidence/hamcp235-mutations/run_mutations.py \\
        --work .artifacts/hamcp235-mut --output docs/evidence/hamcp235-mutations/results.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hamcp131-mutations"))

import run_mutations as base  # noqa: E402

m = base.m
E = base.E
AB = f"{E}/audit_baseline"
PROV = f"{AB}/capture_provider.py"
SERV = f"{AB}/capture_service.py"
COMP = f"{AB}/comparison.py"
CONTRACTS = f"{AB}/capture_contracts.py"

G = "a comparison-level caveat as a per-row UNKNOWN, or the caveat lost"
V = "a volatile input in the resume or capture fence"
R = "a refusal without its category"
F = "the final fence weakened"

MUTATIONS = [
    # -- G: the owner decision (option a): caveats once, rows by digest
    m("G1-unestablished-identity-as-row-mismatch", G,
      (COMP, '    installation_mismatch = installation_reason == "installation_identity_mismatch"\n',
       "    installation_mismatch = not installation_comparable\n")),
    m("G2-authority-continuity-as-row-reason", G,
      (COMP, "            if reasons:\n                classification = Classification.UNKNOWN\n"
             "            elif old.configuration_digest == new.configuration_digest:\n",
       "            if not authority_continuity_comparable:\n"
       '                reasons.append("authority_continuity_unestablished")\n'
       "            if reasons:\n                classification = Classification.UNKNOWN\n"
       "            elif old.configuration_digest == new.configuration_digest:\n")),
    m("G3-recorded-unestablished-capture-as-row-reason", G,
      (COMP, "            # HAMCP-235: installation identity and authority continuity are\n"
             "            # comparison-level caveats, not row reasons.\n",
       "            if not capture.installation.established:\n"
       '                reasons.append("capture_installation_unestablished")\n')),
    m("G4-recorded-authority-as-row-reason", G,
      (COMP, "            # HAMCP-235: installation identity and authority continuity are\n"
             "            # comparison-level caveats, not row reasons.\n",
       "            if not authority_continuous:\n"
       '                reasons.append("capture_authority_continuity_unestablished")\n')),
    m("G5-stored-caveats-dropped", G,
      (COMP, '        caveats=_caveats((("earlier", earlier), ("later", later))),\n', "        caveats=(),\n")),
    m("G6-installation-caveat-earlier-side-only", G,
      (COMP, "installation = [side for side, item in pairs if", "installation = [side for side, item in pairs[:1] if")),
    m("G7-authority-caveat-dropped", G,
      (COMP, 'if item.consistency.get("authority_drift") != "none_observed_at_capture_fences"]', "if False]")),
    m("G8-non-atomic-caveat-dropped", G,
      (COMP, "atomic = [side for side, item in pairs if item.capture_non_atomic]",
       "atomic = [side for side, item in pairs if False]")),
    m("G9-recorded-caveats-dropped", G,
      (COMP, '"caveats": list(_caveats((("capture", capture),), unverified_sides=("recorded_fingerprints",))),',
       '"caveats": [],')),
    m("G10-recorded-side-not-unverified", G,
      (COMP, 'unverified_sides=("recorded_fingerprints",))),', "unverified_sides=())),")),
    m("G11-timed-out-reason-dropped", G,
      (SERV, 'row["reasons"] = sorted({*row["reasons"], "timed_out"})', 'row["reasons"] = row["reasons"]')),
    m("G12-timed-out-on-every-unknown", G,
      (SERV, 'if row["classification"] == "UNKNOWN" and row["configuration_id"] in timed_out:',
       'if row["classification"] == "UNKNOWN":')),
    m("G13-timeouts-from-earlier-only", G,
      (SERV, "timed_out = self._read_timeouts(earlier) | self._read_timeouts(later)",
       "timed_out = self._read_timeouts(earlier)")),
    m("G14-timeouts-from-later-only", G,
      (SERV, "timed_out = self._read_timeouts(earlier) | self._read_timeouts(later)",
       "timed_out = self._read_timeouts(later)")),
    m("G15-installation-mismatch-row-reason-dropped", G,
      (COMP, "    if installation_mismatch:\n        reasons.append(\"installation_identity_mismatch\")\n",
       "")),
    # -- V: only stable identity fences a resume or counts as inventory drift
    m("V1-generation-change-refuses-resume", V,
      (PROV, '            if prior["authority"]["generation"] != authority[0]:\n                authority_drift = "detected"\n',
       '            if prior["authority"]["generation"] != authority[0]:\n'
       '                raise c.CaptureError("resume_inventory_changed")\n')),
    m("V2-generation-change-not-recorded-as-drift", V,
      (PROV, '            if prior["authority"]["generation"] != authority[0]:\n                authority_drift = "detected"\n',
       '            if prior["authority"]["generation"] != authority[0]:\n                pass\n')),
    m("V3-enabled-state-in-resume-mapping", V,
      (PROV, 'before = {row["configuration_id"]: row["entity_id"] for row in prior["records"]}\n'
             '        after = {row["configuration_id"]: row["entity_id"] for row in records}\n',
       'before = {row["configuration_id"]: (row["entity_id"], row["enabled_state"]["state"]) for row in prior["records"]}\n'
       '        after = {row["configuration_id"]: (row["entity_id"], row["enabled_state"]["state"]) for row in records}\n')),
    m("V4-declared-count-refuses-resume", V,
      (PROV, "                changes = self.resume_changes(base, first[\"identity\"], records)\n",
       '                if prior["inventory"]["declared_count"] != len(states) + first["states"]["invalid_count"]:\n'
       '                    raise c.CaptureError("resume_inventory_changed")\n'
       "                changes = self.resume_changes(base, first[\"identity\"], records)\n")),
    m("V5-on-off-state-in-stable-inventory", V,
      (PROV, '{entity: (row["id"], row["id_withheld"]) for entity, row in projected["records"].items()}',
       '{entity: (row["id"], row["id_withheld"], row.get("state")) for entity, row in projected["records"].items()}')),
    m("V7-full-states-compared-at-final-fence", V,
      (PROV, "self.stable_states(first) == self.stable_states(last)", 'first["states"] == last["states"]')),
    m("V8-unestablished-base-anchor-ignored", V,
      (PROV, '        return base["diagnostics"].get("first_fence_installation_id")\n', "        return None\n")),
    m("V9-anchored-base-never-established", V,
      (PROV, 'identity_established = identity_established and anchor == first["identity"]',
       "identity_established = False")),
    m("V10-first-fence-anchor-not-recorded", V,
      (PROV, '"first_fence_installation_id": anchor,', '"first_fence_installation_id": None,')),
    m("V11-legacy-base-without-anchor-established", V,
      (PROV, 'identity_established = identity_established and anchor == first["identity"]',
       "identity_established = identity_established")),
    # Review 1 (R1-M1) and owner decision D4 (Josh, 2026-10-28).
    m("V12-inherited-rows-take-the-current-anchor", V,
      (PROV, 'anchor = first["identity"] if base is None else self.inherited_anchor(base)',
       'anchor = first["identity"]')),
    m("V13-anchorless-resume-accepted", V,
      (SERV, "                if self.provider.inherited_anchor(base) is None:\n", "                if False:\n")),
    m("V14-anchorless-refused-as-installation-changed", V,
      (SERV, 'resume_changes=("installation_anchor_missing",))', 'resume_changes=("installation_changed",))')),
    # -- R: a refused resume names its fixed category
    m("R1-inventory-refusal-without-categories", R,
      (PROV, 'raise c.CaptureError("resume_inventory_changed", resume_changes=changes)',
       'raise c.CaptureError("resume_inventory_changed")')),
    m("R2-core-refusal-without-category", R,
      (PROV, 'raise c.CaptureError("resume_core_changed", resume_changes=("core_version_changed",))',
       'raise c.CaptureError("resume_core_changed")')),
    m("R3-categories-not-in-details", R,
      (CONTRACTS, '            details["resume_changes"] = changes\n', "            pass\n")),
    m("R4-unlisted-category-kept", R,
      (CONTRACTS, "changes = sorted(set(resume_changes) & RESUME_CHANGES)", "changes = sorted(set(resume_changes))")),
    m("R5-categories-on-any-reason", R,
      (CONTRACTS, 'if reason in {"resume_inventory_changed", "resume_core_changed"} and changes:', "if changes:")),
    m("R6-added-not-detected", R,
      (PROV, 'changes.add("automation_added")', "pass")),
    m("R7-removed-not-detected", R,
      (PROV, 'changes.add("automation_removed")', "pass")),
    m("R8-mapping-change-not-detected", R,
      (PROV, 'changes.add("mapping_changed")', "pass")),
    m("R9-installation-change-not-detected", R,
      (PROV, 'changes.add("installation_changed")', "pass")),
    m("R10-anchor-missing-not-a-listed-category", R,
      (CONTRACTS, '"installation_changed", "installation_anchor_missing",', '"installation_changed",')),
    # -- F: the final fence keeps its reserve, its identity and its reason
    m("F1-reserve-ignored-by-scheduler", F,
      (PROV, "if time.monotonic() >= window:", "if time.monotonic() - started >= c.COLLECTION_SECONDS - 10:")),
    m("F2-first-fence-not-measured", F,
      (PROV, "c.FINAL_FENCE_RESERVE_MIN, 2 * (time.monotonic() - fence_started)))", "c.FINAL_FENCE_RESERVE_MIN, 0))")),
    m("F3-reserve-maximum-unclamped", F,
      (PROV, "reserve = min(c.FINAL_FENCE_RESERVE_MAX, max(", "reserve = min(1e9, max(")),
    m("F4-reserve-minimum-unclamped", F,
      (PROV, "c.FINAL_FENCE_RESERVE_MIN, 2 * (time.monotonic() - fence_started)))",
       "0, 2 * (time.monotonic() - fence_started)))")),
    # Review 1 (R1-M2): the reserve covers the final identity reads only.
    m("F10-reserve-reverted-to-round-1-sizing", F,
      (CONTRACTS, "FINAL_FENCE_RESERVE_MIN = READ_SECONDS\nFINAL_FENCE_RESERVE_MAX = len(IDENTITY_READS) * READ_SECONDS\n",
       "FINAL_FENCE_RESERVE_MIN = 10\nFINAL_FENCE_RESERVE_MAX = 25\n"),
      (PROV, '            reserve = min(c.FINAL_FENCE_RESERVE_MAX, max(\n                c.FINAL_FENCE_RESERVE_MIN, 2 * (time.monotonic() - fence_started)))\n            await fence("before", c.FENCE_READS[len(c.IDENTITY_READS):], first)\n',
       '            await fence("before", c.FENCE_READS[len(c.IDENTITY_READS):], first)\n'
       '            reserve = min(c.FINAL_FENCE_RESERVE_MAX, max(\n'
       '                c.FINAL_FENCE_RESERVE_MIN, c.READ_SECONDS + 2 * (time.monotonic() - fence_started)))\n')),
    m("F11-reserve-bounds-reverted-to-round-1", F,
      (CONTRACTS, "FINAL_FENCE_RESERVE_MIN = READ_SECONDS\nFINAL_FENCE_RESERVE_MAX = len(IDENTITY_READS) * READ_SECONDS\n",
       "FINAL_FENCE_RESERVE_MIN = 10\nFINAL_FENCE_RESERVE_MAX = 25\n")),
    m("F12-reserve-measured-over-the-full-fence", F,
      (PROV, '            reserve = min(c.FINAL_FENCE_RESERVE_MAX, max(\n                c.FINAL_FENCE_RESERVE_MIN, 2 * (time.monotonic() - fence_started)))\n            await fence("before", c.FENCE_READS[len(c.IDENTITY_READS):], first)\n',
       '            await fence("before", c.FENCE_READS[len(c.IDENTITY_READS):], first)\n'
       '            reserve = min(c.FINAL_FENCE_RESERVE_MAX, max(\n'
       '                c.FINAL_FENCE_RESERVE_MIN, 2 * (time.monotonic() - fence_started)))\n')),
    m("F13-window-does-not-bound-in-flight-reads", F,
      (PROV, "async with asyncio.timeout(max(0.0, window - time.monotonic())):", "async with asyncio.timeout(None):")),
    m("F5-identity-needs-full-final-fence", F,
      (PROV, 'identity_verified = "identity" in last', "identity_verified = inventory_complete")),
    m("F6-final-fence-reason-dropped", F,
      (PROV, "                fence_reason = exc.reason\n", "                fence_reason = None\n")),
    m("F7-identity-drift-swallowed", F,
      (PROV, '"identity_unverified", "identity_drift"}:', '"identity_unverified"}:')),
    m("F8-identity-drift-unchecked", F,
      (PROV, '                if first["identity"] != last["identity"]:\n                    raise c.CaptureError("identity_drift")\n',
       "")),
    m("F9-identity-only-reported-unavailable", F,
      (PROV, '"identity_only" if identity_verified else "unavailable"', '"unavailable"')),
]

base.MODULES = ("test_hamcp235_baseline_usability", "test_hamcp228_baseline_store",
                "test_automation_baseline_capture", "test_automation_audit_baseline",
                "test_hamcp226_228_review1_corrections", "test_hamcp226_228_review2_corrections",
                "test_hamcp226_228_review2_delta_extra", "test_hamcp226_named_sources")
base.MUTATIONS = MUTATIONS

_run_tests = base.run_tests
SCRATCH: Path | None = None


def run_tests(tree: Path, timeout: int, *, failfast: bool) -> tuple[int, str, float]:
    scratch = tempfile.mkdtemp(prefix="mutant-", dir=SCRATCH)
    previous = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = scratch
    try:
        return _run_tests(tree, timeout, failfast=failfast)
    finally:
        if previous is None:
            del os.environ["TMPDIR"]
        else:
            os.environ["TMPDIR"] = previous
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    """The HAMCP-131 main, with the work directory under ``.artifacts/``."""

    global SCRATCH
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    arguments = parser.parse_args()
    root = base.ROOT
    work = arguments.work.expanduser().resolve()
    artifacts = root / ".artifacts"
    if artifacts not in work.parents:
        raise SystemExit("--work must be under the worktree's .artifacts/")
    tree, log_dir, SCRATCH = work / "tree", work / "mutation-logs", work / "tmp"
    if tree.exists():
        shutil.rmtree(tree)
    top = base.tracked_top_level()
    common = shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc")

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = set(common(directory, names))
        if Path(directory) == root:  # untracked top-level entries stay out
            skipped |= {name for name in names if name not in top}
        return skipped

    shutil.copytree(root, tree, symlinks=True, ignore=ignore)
    log_dir.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if len({item["id"] for item in MUTATIONS}) != len(MUTATIONS):
        raise SystemExit("duplicate mutation id")
    for mutation in MUTATIONS:  # every fragment matches once before anything runs
        for path, text in base.apply(tree, mutation).items():
            path.write_text(text, encoding="utf-8")

    code, output, seconds = run_tests(tree, arguments.timeout, failfast=False)
    baseline_log = log_dir / "baseline.log"
    baseline_log.write_text(output, encoding="utf-8")
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    ran = re.search(r"^Ran (\d+) tests?", output, re.MULTILINE)
    baseline = {"log_path": baseline_log.relative_to(work).as_posix(), "modules": list(base.MODULES),
                "returncode": code, "tests": int(ran.group(1)) if ran else None,
                "seconds": round(seconds, 1), "summary": summary}
    print(f"baseline rc={code} {summary} ({seconds:.0f}s)", flush=True)
    if code != 0:
        print(output[-4000:])
        return 2

    results = []
    for mutation in MUTATIONS:
        originals = base.apply(tree, mutation)
        try:
            code, output, seconds = run_tests(tree, arguments.timeout, failfast=True)
        finally:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")
        failures = [f"{kind} {name} ({where})" for kind, name, where in base._FAILURE_LINE.findall(output)]
        log_path = log_dir / f"{mutation['id']}.log"
        log_path.write_text(output, encoding="utf-8")
        results.append({
            **mutation, "log_path": log_path.relative_to(work).as_posix(), "returncode": code, "killed": code != 0,
            "killed_by_import_or_syntax_error": any(
                marker in output for marker in ("ImportError", "SyntaxError", "Failed to import")),
            "first_failures": failures[:3], "seconds": round(seconds, 1),
        })
        print(f"{'KILLED' if code else 'SURVIVED':8} {mutation['id']:48} {failures[:1]}", flush=True)

    survived = [item["id"] for item in results if not item["killed"]]
    document = {"baseline": baseline, "mutation_count": len(results),
                "killed": sum(item["killed"] for item in results), "survived": survived,
                "python": platform.python_version(), "results": results}
    arguments.output.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
