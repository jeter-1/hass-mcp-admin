"""HAMCP-247 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery through the HAMCP-235 wrapper shape:
a disposable copy of the tracked tree, every fragment matching exactly once
before anything runs, and fail-fast suites per mutant.
``test_hamcp247_list_capabilities_bounded`` (the live-shaped suite) runs
first, then the catalog suites that call ``list_capabilities``.

Per AGENTS.md (HAMCP-237), the work directory and every mutant's TMPDIR live
under the worktree's ignored ``.artifacts/``; the copy skips ``.artifacts``,
so it never copies itself.

Mutation classes:
- ``C*``: a filter that no longer narrows a section it applies to;
- ``P*``: the gateway per-tool projection (narrowing, summary, totals);
- ``S*``: the section selector and the summary view;
- ``B*``: the response budget and the summary/filtered size guard;
- ``O*``: an oversize section's bounded page and its next-page retrieval;
- ``G*``: the gateway's per-field pages (each omitted field by its own
  identity, never a substitute content set) and its size fallbacks;
- ``K*``: the ``detail_cursor`` encoding and its refusals (stale, malformed,
  section, filter or position mismatch, cursor without a section);
- ``E*``: the tool entry point.

Results (Python 3.12.14, six modules):
- Round 0 (the first, ``no_narrower_filter`` candidate; baseline 303 tests
  OK), kept as history in ``round0-*``: ``round0-run1.log``, 48 mutants, 36
  killed and 12 survived. C3 was equivalent (dead ``upstream_tool`` branch
  removed, C3 redefined); P3, P6, P7, B5-B8, O8, O10, O11 and O14 gained
  tests, and the limit sweep found a requested oversize section 14
  characters over (now marked before sizing; O15 reverts that).
  ``round0-run2-survivors.log`` re-ran those 12 (partial): all killed.
  ``round0-run3.log`` / ``round0-results.json``: 49 of 49 killed.
- Round 1 (the ``detail_cursor`` paging corrections, R1-M1 and R1-M2; the
  round-0 classes kept and re-pointed at the paging code, plus ``G*`` and
  ``K*``; 79 mutants): ``round1-run1.log`` / ``round1-run1-results.json``,
  77 killed and 2 survived. G10 was equivalent (a second field-drop loop
  in the gateway fallback was dead: the loop before it had already dropped
  every nonempty field), so the dead loop was removed and G10 redefined
  (the fallback without its field cursors). K6 (the cursor length bound
  not checked) gained an over-long, correctly signed cursor case.
- ``run-final.log`` / ``results.json``: the final full run on the final
  code, baseline 322 tests OK: 79 mutants, 79 killed, none survived, none
  by an import or syntax error.

Every run used a disposable copy of the tracked tree under ``.artifacts/``;
no fragment was applied to the task worktree, whose status and diff hashes
were recorded before and verified unchanged after each run.

Usage:
    systemd-run --user --unit=hamcp247-mut -p MemoryMax=3G -p OOMPolicy=continue \\
        python docs/evidence/hamcp247-mutations/run_mutations.py \\
        --work .artifacts/hamcp247-mut --output docs/evidence/hamcp247-mutations/results.json
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
CAP = f"{E}/capabilities.py"
COMPAT = f"{E}/tools/compatibility.py"

C = "a filter no longer narrows a section it applies to"
P = "the gateway per-tool projection weakened"
S = "the section selector or summary view weakened"
B = "the response budget or size guard weakened"
O = "an oversize section not paged by its actual rows"
G = "an oversize gateway field not paged by its own identity"
K = "a cursor accepted or issued untruthfully"
EP = "the tool entry point"

MUTATIONS = [
    # -- C: a filter narrows every section it applies to
    m("C1-matrix-not-narrowed", C,
      (CAP, 'if item["tool"] in selected\n', "if True\n")),
    m("C2-gateway-not-narrowed", C,
      (CAP, '        gateway = _project_gateway(\n            _UPSTREAM_READ_GATEWAY_SUMMARY, selected, scope="selected_tools"\n        )\n',
       "        gateway = dict(_UPSTREAM_READ_GATEWAY_SUMMARY)\n")),
    m("C3-selection-omits-dynamic-tools", C,
      (CAP, 'item["tool"] for item in (*tools, *beta_native, *dynamic_tools)', 'item["tool"] for item in (*tools, *beta_native)')),
    m("C4-beta-native-not-narrowed", C,
      (CAP, "        for item in BETA_NATIVE_CAPABILITIES\n        if _matches(item, normalized_status, normalized_category)\n",
       "        for item in BETA_NATIVE_CAPABILITIES\n")),
    m("C5-category-ignored", C,
      (CAP, 'not category or item.get("category") == category', "True")),
    m("C6-selection-only-canonical", C,
      (CAP, "for item in (*tools, *beta_native, *dynamic_tools)", "for item in tools")),
    # -- P: the gateway per-tool projection
    m("P1-list-rows-not-narrowed", P,
      (CAP, "value[field] = [row for row in rows if _gateway_row_names(row) & names]", "value[field] = list(rows)")),
    m("P2-dict-rows-not-narrowed", P,
      (CAP, "value[field] = {key: item for key, item in rows.items() if key in names}", "value[field] = dict(rows)")),
    m("P3-nested-readmission-not-narrowed", P,
      (CAP, "                if _gateway_row_names(row) & names\n            ]\n", "            ]\n")),
    m("P4-summary-keeps-detail", P,
      (CAP, '        if scope == "summary":\n            del value[field]\n', "        if False:\n            del value[field]\n")),
    m("P5-totals-not-stated", P,
      (CAP, "        totals[field] = len(rows)\n", "        pass\n")),
    m("P6-row-names-tool-key-only", P,
      (CAP, '_GATEWAY_ROW_NAME_KEYS = ("tool", "upstream_name", "exposed_name")', '_GATEWAY_ROW_NAME_KEYS = ("tool",)')),
    m("P7-summary-keeps-nested-readmission", P,
      (CAP, '        if scope == "summary":\n            del readmission["readmission_withheld_tools"]\n',
       '        if False:\n            del readmission["readmission_withheld_tools"]\n')),
    m("P8-string-rows-not-named", P,
      (CAP, "    if isinstance(row, str):\n        return {row}\n", "")),
    # -- S: the section selector and the summary view
    m("S1-unknown-section-accepted", S,
      (CAP, "    if section and section not in CATALOG_SECTIONS:\n", "    if False:\n")),
    m("S2-section-not-normalized", S,
      (CAP, "section = detail_section.strip().lower()", "section = detail_section")),
    m("S3-section-view-keeps-other-sections", S,
      (CAP, '                del data[name]\n                included[name] = "omitted"\n', '                included[name] = "omitted"\n')),
    m("S4-section-request-drops-filters", S,
      (CAP, '"request": {"detail_section": name, **active},', '"request": {"detail_section": name},')),
    m("S5-summary-keeps-full-sections", S,
      (CAP, '            del data[name]\n            included[name] = "omitted"\n', '            included[name] = "omitted"\n')),
    m("S6-tool-index-omits-dynamic", S,
      (CAP, '                *catalog["dynamic_upstream"],\n            )\n        ]\n', "            )\n        ]\n")),
    m("S7-gateway-item-count-wrong", S,
      (CAP, '                if name == "upstream_read_gateway"\n', "                if False\n")),
    m("S8-filtered-view-labelled-summary", S,
      (CAP, 'view = "section" if section else "filtered" if active else "summary"',
       'view = "section" if section else "summary"')),
    m("S9-server-version-missing", S,
      (CAP, '        "version": SERVER_VERSION,\n        "schema_version": SCHEMA_VERSION,\n',
       '        "schema_version": SCHEMA_VERSION,\n')),
    # -- B: the response budget and size guard
    m("B1-no-envelope-reserve", B,
      (CAP, "CATALOG_ENVELOPE_RESERVE_CHARS = 4_000", "CATALOG_ENVELOPE_RESERVE_CHARS = 0")),
    m("B2-budget-measured-indented", B,
      (CAP, 'return len(json.dumps({"data": value}, separators=(",", ":"), default=str))',
       'return len(json.dumps({"data": value}, indent=2, default=str))')),
    m("B3-size-statement-not-counted", B,
      (CAP, "    elif _rendered_chars(data) > budget:\n",
       '    elif _rendered_chars({k: v for k, v in data.items() if k != "size_limit"}) > budget:\n'),
      (CAP, "            if _rendered_chars(data) <= budget:\n",
       '            if _rendered_chars({k: v for k, v in data.items() if k != "size_limit"}) <= budget:\n')),
    m("B4-no-size-guard", B,
      (CAP, "    elif _rendered_chars(data) > budget:\n", "    elif False:\n")),
    m("B5-tool-lists-dropped-first", B,
      (CAP, "for name in (n for n in _DROP_ORDER if n in data):", "for name in (n for n in reversed(_DROP_ORDER) if n in data):")),
    m("B6-drops-past-fit", B,
      (CAP, "            if _rendered_chars(data) <= budget:\n                break\n",
       "            if False:\n                break\n")),
    m("B7-tool-index-retrieval-incomplete", B,
      (CAP, '                ("tools", "beta_native", "dynamic_upstream")\n                if name == "tool_index"\n',
       '                ("tools",)\n                if name == "tool_index"\n')),
    m("B8-dropped-section-not-marked", B,
      (CAP, '                data["sections"][name]["included"] = "omitted_size_limit"\n', "                pass\n")),
    m("B9-dropped-section-no-retrieval", B,
      (CAP, '                retrieval.append(_request(data["sections"][part]["request"]))\n', "                pass\n")),
    # -- O: an oversize section is paged by its actual rows
    m("O1-page-not-marked-paged", O,
      (CAP, '    serve(page, "paged")\n', '    serve(page, "full")\n')),
    m("O2-page-retrieval-not-in-size-limit", O,
      (CAP, '            data["size_limit"]["retrieval"].extend(value["retrieval"])\n', "            pass\n")),
    m("O3-page-not-in-omitted-sections", O,
      (CAP, '            data["size_limit"]["omitted_sections"].append(section)\n', "            pass\n")),
    m("O4-final-page-carries-retrieval", O,
      (CAP, "[] if next_offset is None else [next_request(field, next_offset)]",
       "[next_request(field, next_offset or 0)]")),
    m("O5-page-skips-a-row", O,
      (CAP, "            None if final else offset + count,\n", "            None if final else offset + count + 1,\n")),
    m("O6-page-repeats-a-row", O,
      (CAP, "            None if final else offset + count,\n", "            None if final else offset + count - 1,\n")),
    m("O7-final-page-not-tried-first", O,
      (CAP, "    if fits(candidate):\n        return candidate\n", "    if False:\n        return candidate\n")),
    m("O8-bisection-takes-one-row", O,
      (CAP, "    low, high = 1, remaining - 1\n", "    low, high = 1, 1\n")),
    m("O9-page-size-not-checked", O,
      (CAP, 'lambda value: fits(value, "paged")', "lambda value: True")),
    m("O10-page-ignores-offset", O,
      (CAP, "        chosen = items[offset:offset + count]\n", "        chosen = items[:count]\n")),
    m("O11-mapping-paged-as-list", O,
      (CAP, "            dict(chosen) if isinstance(rows, dict) else chosen,\n", "            chosen,\n")),
    m("O12-fits-ignores-size-statement", O,
      (CAP, '                    "retrieval": pending,\n', '                    "retrieval": [],\n')),
    m("O13-page-sized-before-label", O,
      (CAP, 'section: {**data["sections"][section], "included": included},',
       'section: {**data["sections"][section], "included": "full"},')),
    m("O14-row-too-large-reason-missing", O,
      (CAP, '            "reason": "page_row_exceeds_response_limit",\n', "")),
    m("O15-first-page-not-paged", O,
      (CAP, 'if view == "section" and (state is not None or _rendered_chars(data) > budget):',
       'if view == "section" and state is not None:')),
    m("O16-section-always-paged", O,
      (CAP, 'if view == "section" and (state is not None or _rendered_chars(data) > budget):',
       'if view == "section":')),
    # -- G: an oversize gateway pages each omitted field by its own identity
    m("G1-gateway-field-not-dropped", G,
      (CAP, "            omitted[field] = len(value.pop(field))\n", "            omitted[field] = len(value[field])\n")),
    m("G2-readmission-not-dropped", G,
      (CAP, '            omitted[field] = len(readmission.pop("readmission_withheld_tools"))\n',
       '            omitted[field] = len(readmission["readmission_withheld_tools"])\n')),
    m("G3-readmission-never-paged", G,
      (CAP, "    if _gateway_field_rows(value, _READMISSION_FIELD):\n        candidates.append(_READMISSION_FIELD)\n", "")),
    m("G4-drops-smallest-first", G,
      (CAP, "candidates.sort(key=lambda field: _rendered_chars(value[field]), reverse=True)",
       "candidates.sort(key=lambda field: _rendered_chars(value[field]), reverse=False)")),
    m("G5-drops-past-fit", G,
      (CAP, '        if fits(value, "partial"):\n            break\n', "        if False:\n            break\n")),
    m("G6-gateway-returned-oversize", G,
      (CAP, '    if fits(value, "partial"):\n        return value, "partial"\n', '    if True:\n        return value, "partial"\n')),
    m("G7-empty-field-dropped", G,
      (CAP, "        if isinstance(value.get(field), (list, dict)) and value[field]\n",
       "        if isinstance(value.get(field), (list, dict))\n")),
    m("G8-field-cursor-pages-another-field", G,
      (CAP, "        _gateway_field_rows(data[section], field)\n",
       '        _gateway_field_rows(data[section], "exposed_tools")\n')),
    m("G9-field-cursor-pages-unfiltered-rows", G,
      (CAP, "        _gateway_field_rows(data[section], field)\n",
       "        _gateway_field_rows(_UPSTREAM_READ_GATEWAY_SUMMARY, field)\n")),
    m("G10-fallback-without-field-cursors", G,
      (CAP, '        "retrieval": pending,\n    }\n', '        "retrieval": [],\n    }\n')),
    m("G11-detail-flag-missing", G,
      (CAP, '    value["detail_complete"] = False\n', "")),
    m("G12-fallback-returned-oversize", G,
      (CAP, '    if not fits(record, "omitted_size_limit"):\n        # The limit',
       '    if False:\n        # The limit')),
    # -- K: the cursor and its refusals
    m("K1-stale-cursor-accepted", K,
      (CAP, '        if state["fp"] != fingerprint:\n', "        if False:\n")),
    m("K2-filter-mismatch-accepted", K,
      (CAP, '        if (state["st"], state["ca"]) != (\n', '        if False and (state["st"], state["ca"]) != (\n')),
    m("K3-section-mismatch-accepted", K,
      (CAP, '        if state["s"] != section:\n', "        if False:\n")),
    m("K4-digest-not-checked", K,
      (CAP, "        if hashlib.sha256(payload).hexdigest()[:12] != digest:\n", "        if False:\n")),
    m("K5-prefix-not-checked", K,
      (CAP, " or not cursor.startswith(_CURSOR_PREFIX):", ":")),
    m("K6-length-not-checked", K,
      (CAP, "if len(cursor) > _CURSOR_MAX_CHARS or ", "if ")),
    m("K7-negative-offset-accepted", K,
      (CAP, '            or state["o"] < 0\n', "")),
    m("K8-bool-offset-accepted", K,
      (CAP, '            or type(state["o"]) is not int\n', '            or not isinstance(state["o"], int)\n')),
    m("K9-extra-keys-accepted", K,
      (CAP, "set(state) != _CURSOR_KEYS", "not set(state) >= _CURSOR_KEYS")),
    m("K10-out-of-range-accepted", K,
      (CAP, "    if state is not None and offset >= count:\n", "    if False:\n")),
    m("K11-fingerprint-ignores-core-admission", K,
      (CAP, "        build_capability_catalog(core_admission=core_admission),\n        sort_keys=True,\n",
       "        build_capability_catalog(),\n        sort_keys=True,\n")),
    m("K12-cursor-without-section-accepted", K,
      (CAP, "    if cursor and not section:\n", "    if False:\n")),
    m("K13-next-cursor-drops-filter", K,
      (CAP, '                        "st": active.get("status", ""),\n', '                        "st": "",\n')),
    m("K14-field-section-pairing-unchecked", K,
      (CAP, '            or (state["f"] is None) != (state["s"] != "upstream_read_gateway")\n', "")),
    m("K15-cursor-not-stripped", K,
      (CAP, "    cursor = detail_cursor.strip()\n", "    cursor = detail_cursor\n")),
    m("K16-unknown-field-accepted", K,
      (CAP, '            or (state["f"] is not None and state["f"] not in GATEWAY_PAGE_FIELDS)\n', "")),
    # -- E: the tool entry point
    m("E1-cap-not-passed-to-view", EP,
      (COMPAT, "            response_limit=SETTINGS.response_size_limit,\n        )\n", "        )\n")),
    m("E2-section-not-forwarded", EP,
      (COMPAT, "            detail_section=detail_section,\n", "")),
    m("E3-cursor-not-forwarded", EP,
      (COMPAT, "            detail_cursor=detail_cursor,\n", "")),
]

base.MODULES = ("test_hamcp247_list_capabilities_bounded", "test_hamcp181_catalog_core_admission",
                "test_rc1_compatibility", "test_upstream_8_5_compatibility",
                "test_readonly_upstream_gateway", "test_beta_v2")
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
    parser.add_argument("--only", nargs="*", default=None,
                        help="run only these mutation ids (a partial, non-evidence run)")
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
    selected = MUTATIONS if arguments.only is None else [
        item for item in MUTATIONS if item["id"] in set(arguments.only)]
    for mutation in selected:
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
