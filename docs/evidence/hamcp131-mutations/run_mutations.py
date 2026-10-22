"""HAMCP-131 mutation runner (offline, deterministic).

Each mutation is a set of exact textual replacements (every old fragment must
match exactly once in its file), applied to a disposable copy of the tracked
working tree outside the repository. The HAMCP-131 suites, the HAMCP-191
sanitizer site table and the HAMCP-200 path-safety suite then run; a mutation
is killed when they fail. Nothing in the repository is modified. The machinery
is the HAMCP-132 runner's.

The task's five required classes each have mutations:
- ``W*``: a window-edge off-by-one (incident start or end, the locate
  probes, the history slice start, the 1 ms overlap, the slice end);
- ``C*``: a cursor that skips or repeats an entry or state;
- ``U*``: an unavailable or failed source silently omitted;
- ``Z*``: sanitizing before the merge instead of at the single exit, the
  exit's known secrets dropped, or a redaction that leaves the result complete;
- ``S*``: a slug passed as a raw path (guard removed, interpolated template,
  plain string path).

``A1`` probes the static authority table.

The independent review's correction batch adds:
- ``R1*``: history continuation staleness checked by the last-row anchor only
  (the in-slice range digest or the finished-slice re-verification removed);
- ``R2*``: a merged incident page that can emit an entry earlier than one
  already returned (the deferred-source hold or the finished-source cutoff
  removed). A source is deferred only once the shared call budget is spent,
  so through the gateway no source can read more on that page; the two hold
  guards (``_walk`` bounding the merge at the window start, ``R2a``, and
  ``_fill`` reading nothing more, ``R2c``) are defense in depth, each killed
  by a direct test;
- ``R3``: an inclusive coverage bound claimed while tied states may remain;
- ``R4``: a non-string ``min_level`` escaping as a ``TypeError``.

Usage:
    python docs/evidence/hamcp131-mutations/run_mutations.py \\
        --work ~/.cache/hamcp131/mut --output docs/evidence/hamcp131-mutations/results.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
E = "hass_mcp_engineering_beta/ha_mcp_engineering"
INC = f"{E}/incident_logs/service.py"
CON = f"{E}/incident_logs/contracts.py"
TRN = f"{E}/incident_logs/transport.py"
HIS = f"{E}/history_slices/service.py"
HCON = f"{E}/history_slices/contracts.py"
MODULES = ("test_hamcp131_incident_logs", "test_hamcp131_history_slices",
           "test_hamcp191_shared_sanitizer", "test_hamcp200_path_safety")
WINDOW = "window-edge off-by-one"
CURSOR = "cursor skip or duplicate"
OMITTED = "an unavailable source silently omitted"
EXIT = "sanitizing before merge instead of at the single exit"
SLUG = "a slug passed as a raw path"
AUTHORITY = "static authority table"
STALE = "anchor-only continuation staleness"
ORDER = "a merged page emitting an earlier entry"
TIE = "an inclusive boundary claimed complete"
TYPING = "min_level type errors escaping"
PASSTHROUGH = 'type("R", (), {"value": envelope, "failed_closed": False, "redacted_field_count": 0})'
EXIT_CALL = "            result = sanitize_untrusted_data(envelope, known_secrets=self.known_secrets, registry=registry)\n"


def m(identifier, item, *replacements):
    return {"id": identifier, "class": item, "replacements": list(replacements)}


MUTATIONS = [
    m("W1-incident-end-inclusive", WINDOW,
      (INC, '            if entry.ts_ms >= args["end_ms"]:', '            if entry.ts_ms > args["end_ms"]:')),
    m("W2-incident-start-exclusive", WINDOW,
      (INC, 'entry.ts_ms >= args["start_ms"]))', 'entry.ts_ms > args["start_ms"]))')),
    m("W3-locate-head-at-start-not-retention-edge", WINDOW,
      (INC, "        if head.ts_ms >= start:", "        if head.ts_ms > start:")),
    m("W4-locate-tail-at-start-treated-after-tail", WINDOW,
      (INC, "        if scan.tail_ts < start:", "        if scan.tail_ts <= start:")),
    m("W5-locate-tail-page-start-edge", WINDOW,
      (INC, "        if first.ts_ms < start:", "        if first.ts_ms <= start:")),
    m("W6-history-state-at-slice-start-pre-slice", WINDOW,
      (HIS, "            if ts_us < start_us:\n                if position.checked:",
       "            if ts_us <= start_us:\n                if position.checked:")),
    m("W7-history-overlap-millisecond-lost", WINDOW,
      (HIS, "c.format_us(lower - c.OVERLAP_US)", "c.format_us(lower)")),
    m("W8-history-slice-end-inclusive", WINDOW,
      (HCON, "return lower, min(end_us, lower + SLICE_SECONDS * 1_000_000)",
       "return lower, min(end_us, lower + SLICE_SECONDS * 1_000_000) + 1")),
    m("C1-consumed-anchor-returned-again", CURSOR,
      (INC, "start_index = 0 if not anchor.consumed and first_page else 1",
       "start_index = 0 if first_page else 1")),
    m("C2-resume-repeats-last-returned-entry", CURSOR,
      (INC, '"lv": views[scan.position][count - 1]["level"],\n                                            "r": 1}',
       '"lv": views[scan.position][count - 1]["level"],\n                                            "r": 0}')),
    m("C3-resume-skips-next-entry", CURSOR,
      (INC, "            consumed[position] = index + 1\n",
       "            consumed[position] = min(index + 2, len(scan.items))\n")),
    m("C4-resume-cursor-points-past-entry", CURSOR,
      (INC, '{"c": item.cursor, "k": item.index, "d": item.digest,',
       '{"c": item.cursor, "k": item.index + 1, "d": item.digest,')),
    m("C5-history-overlap-state-repeated", CURSOR,
      (HIS, "            states = states[1:]\n", "            states = states[0:]\n")),
    m("C6-history-offset-skips-a-state", CURSOR,
      (HIS, "request_offset = position.o - overlap\n", "request_offset = position.o - overlap + 1\n")),
    m("C7-history-cut-page-resumes-after-dropped-states", CURSOR,
      (HIS, "next_position = kept[-1][3] if cut else position", "next_position = position")),
    m("C8-history-slice-start-check-skipped", CURSOR,
      (HIS, "            if before != position.x:\n                raise Discontinuity",
       "            if False:\n                raise Discontinuity")),
    m("U1-unavailable-source-rows-dropped", OMITTED,
      (INC, '            for key in args["sources"]:\n                reason = c.unavailable_reason(key)',
       '            for key in ():\n                reason = c.unavailable_reason(key)')),
    m("U2-unavailable-source-omission-dropped", OMITTED,
      (INC, '                if row["status"] != "complete":',
       '                if row["status"] not in ("complete", "unavailable"):')),
    m("U3-failed-source-row-dropped", OMITTED,
      (INC, "                source_rows.append(self._source_row(scan, consumed, skipped, outside, emitted))",
       "                if scan.failure is None:\n"
       "                    source_rows.append(self._source_row(scan, consumed, skipped, outside, emitted))")),
    m("U4-source-gap-omission-dropped", OMITTED,
      (INC, "                for gap in row.get(\"gaps\", ()):\n                    reasons.append(",
       "                for gap in ():\n                    reasons.append(")),
    m("U5-history-failure-omission-dropped", OMITTED,
      (HIS, "            if scan.failure:\n                reasons.append(scan.failure)",
       "            if False:\n                reasons.append(scan.failure)")),
    m("Z1-incident-entries-sanitized-before-merge", EXIT,
      (INC, "                entries_out.append(row)\n",
       "                entries_out.append(sanitize_untrusted_data(row, known_secrets=self.known_secrets,"
       " registry=registry).value)\n"),
      (INC, EXIT_CALL, f"            result = {PASSTHROUGH}\n")),
    m("Z2-history-states-sanitized-before-merge", EXIT,
      (HIS, "                scan.emitted.append((ts_us, state, position.k, position.copy()))",
       "                scan.emitted.append((ts_us, sanitize_untrusted_data(state, known_secrets=self.known_secrets)"
       ".value, position.k, position.copy()))"),
      (HIS, EXIT_CALL, f"            result = {PASSTHROUGH}\n")),
    m("Z3-incident-exit-known-secrets-dropped", EXIT,
      (INC, EXIT_CALL, EXIT_CALL.replace("known_secrets=self.known_secrets", "known_secrets=()"))),
    m("Z4-history-exit-known-secrets-dropped", EXIT,
      (HIS, EXIT_CALL, EXIT_CALL.replace("known_secrets=self.known_secrets", "known_secrets=()"))),
    m("Z5-incident-redaction-leaves-result-complete", EXIT,
      (INC, '            if result.redacted_field_count and "redacted_by_policy" not in omissions:',
       '            if False:')),
    m("Z6-incident-line-sanitizer-known-secrets-dropped", EXIT,
      (INC, "sanitize_log_lines(lines, known_secrets=self.known_secrets)", "sanitize_log_lines(lines, known_secrets=())")),
    m("S1-slug-guard-removed", SLUG,
      (CON, '        path_segment(slug, field="sources")\n', "        pass\n"),
      (CON, '    if slug != "self" and not _SLUG.fullmatch(slug):', "    if False:")),
    m("S2-slug-interpolated-into-template", SLUG,
      (TRN, '    if source_key == c.SOURCE_SELF:\n        return rest_path("/addons/self/logs", query=QUERY)',
       '    if source_key.startswith("addon:"):\n'
       '        return rest_path(f"/addons/{source_key[6:]}/logs", query=QUERY)')),
    m("S3-slug-as-plain-string-path", SLUG,
      (TRN, '        return rest_path("/addons/self/logs", query=QUERY)',
       '        return "/addons/" + source_key[6:] + "/logs"')),
    m("A1-other-addon-treated-readable", AUTHORITY,
      (CON, "    if source_key in READABLE_ROUTES:\n        return None",
       '    if source_key in READABLE_ROUTES or source_key.startswith("addon:"):\n        return None')),
    m("R1a-history-in-slice-range-anchor-only", STALE,
      (HIS, "        verify = scan.verify_range\n", "        verify = False\n")),
    m("R1b-history-finished-slices-not-reverified", STALE,
      (HIS, "        for index, offset_from, total, chained in position.g:\n",
       "        for index, offset_from, total, chained in ():\n")),
    m("R2a-deferred-source-merge-bound-dropped", ORDER,
      (INC, '        bounds += [args["start_ms"] - 1 for scan in scans if scan.deferred]\n',
       "        bounds += []\n")),
    m("R2c-deferred-source-fill-guard-dropped", ORDER,
      (INC, "            if not open_scans or any(scan.deferred for scan in scans):\n",
       "            if not open_scans:\n")),
    m("R2b-cutoff-only-holds-open-sources", ORDER,
      (INC, "            if cutoff is not None and ts > cutoff:\n                break\n",
       "            if cutoff is not None and self._open(scan) and ts > cutoff:\n"
       "                heapq.heappop(heap)\n                continue\n")),
    m("R3-inclusive-bound-always-claimed", TIE,
      (HIS, '"inclusive": lookahead is not None and lookahead > last}', '"inclusive": True}')),
    m("R4-min-level-type-check-removed", TYPING,
      (CON, "(type(min_level) is not str or min_level not in LEVEL_RANK)", "min_level not in LEVEL_RANK")),
]

_FAILURE_LINE = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)", re.MULTILINE)


def apply(tree: Path, mutation: dict) -> dict[Path, str]:
    originals: dict[Path, str] = {}
    for path, old, new in mutation["replacements"]:
        target = tree / path
        originals.setdefault(target, target.read_text(encoding="utf-8"))
        text = target.read_text(encoding="utf-8")
        if text.count(old) != 1:
            for restore, original in originals.items():
                restore.write_text(original, encoding="utf-8")
            raise SystemExit(f"{mutation['id']}: fragment matched {text.count(old)} times in {path}")
        target.write_text(text.replace(old, new), encoding="utf-8")
    return originals


def run_tests(tree: Path, timeout: int, *, failfast: bool) -> tuple[int, str, float]:
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=os.pathsep.join(("tests", "hass_mcp_engineering_beta")))
    command = [sys.executable, "-m", "unittest"] + (["-f"] if failfast else []) + list(MODULES)
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=tree, env=environment, capture_output=True,
                                   text=True, timeout=timeout)
        code, output = completed.returncode, completed.stdout + completed.stderr
    except subprocess.TimeoutExpired:
        code, output = -1, "TIMEOUT"
    return code, output, time.monotonic() - started


def tracked_top_level() -> set[str]:
    listing = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return {name.split("/", 1)[0] for name in listing.decode("utf-8").split("\0") if name}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    arguments = parser.parse_args()
    work = arguments.work.expanduser().resolve()
    if work == ROOT or ROOT in work.parents:
        raise SystemExit("--work must be outside the repository")
    tree, log_dir = work / "tree", work / "mutation-logs"
    if tree.exists():
        shutil.rmtree(tree)
    top = tracked_top_level()
    common = shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc")

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = set(common(directory, names))
        if Path(directory) == ROOT:  # untracked top-level entries stay out
            skipped |= {name for name in names if name not in top}
        return skipped

    shutil.copytree(ROOT, tree, symlinks=True, ignore=ignore)
    log_dir.mkdir(parents=True, exist_ok=True)
    if len({item["id"] for item in MUTATIONS}) != len(MUTATIONS):
        raise SystemExit("duplicate mutation id")
    for mutation in MUTATIONS:  # every fragment matches once before anything runs
        for path, text in apply(tree, mutation).items():
            path.write_text(text, encoding="utf-8")

    code, output, seconds = run_tests(tree, arguments.timeout, failfast=False)
    baseline_log = log_dir / "baseline.log"
    baseline_log.write_text(output, encoding="utf-8")
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    ran = re.search(r"^Ran (\d+) tests?", output, re.MULTILINE)
    baseline = {"log_path": baseline_log.relative_to(work).as_posix(), "modules": list(MODULES), "returncode": code,
                "tests": int(ran.group(1)) if ran else None, "seconds": round(seconds, 1), "summary": summary}
    print(f"baseline rc={code} {summary} ({seconds:.0f}s)", flush=True)
    if code != 0:
        print(output[-4000:])
        return 2

    results = []
    for mutation in MUTATIONS:
        originals = apply(tree, mutation)
        try:
            code, output, seconds = run_tests(tree, arguments.timeout, failfast=True)
        finally:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")
        failures = [f"{kind} {name} ({where})" for kind, name, where in _FAILURE_LINE.findall(output)]
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
