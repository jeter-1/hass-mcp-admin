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
- ``O*``: an oversize section's truthful record and retrieval path;
- ``E*``: the tool entry point.

Results (Python 3.12.14, six modules, baseline 303 tests OK):
- ``run1.log``: the first 48 mutants against the first candidate: 36
  killed, 12 survived. C3 (selection also naming ``upstream_tool``) was
  equivalent: no catalog entry carries that key, so the dead branch was
  removed and C3 redefined. P3, P6 and P7 (gateway rows of shapes the
  live fixture lacks) gained ``GatewayProjectionTests``. B5-B8, O8, O10,
  O11 and O14 gained ``SizeGuardTests``, whose limit sweep also found a
  real defect: a requested oversize section was sized before its
  ``sections`` entry was marked ``omitted_size_limit`` (14 characters
  over). The entry is now marked before sizing (O15 reverts that).
- ``run2-survivors.log``: those 12 re-run with ``--only`` (a partial,
  non-evidence run): all killed.
- ``run3.log`` / ``results.json``: the final full run on the final code:
  49 mutants, 49 killed, none survived, none by an import or syntax error.

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
O = "an oversize section reported untruthfully or without retrieval"
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
      (CAP, "    if _rendered_chars(data) > budget:\n",
       '    if _rendered_chars({k: v for k, v in data.items() if k != "size_limit"}) > budget:\n'),
      (CAP, "                if _rendered_chars(data) <= budget:\n",
       '                if _rendered_chars({k: v for k, v in data.items() if k != "size_limit"}) <= budget:\n')),
    m("B4-no-size-guard", B,
      (CAP, "    if _rendered_chars(data) > budget:\n", "    if False:\n")),
    m("B5-tool-lists-dropped-first", B,
      (CAP, "for name in (n for n in _DROP_ORDER if n in data):", "for name in (n for n in reversed(_DROP_ORDER) if n in data):")),
    m("B6-drops-past-fit", B,
      (CAP, "                if _rendered_chars(data) <= budget:\n                    break\n",
       "                if False:\n                    break\n")),
    m("B7-tool-index-retrieval-incomplete", B,
      (CAP, '                    ("tools", "beta_native", "dynamic_upstream")\n                    if name == "tool_index"\n',
       '                    ("tools",)\n                    if name == "tool_index"\n')),
    m("B8-dropped-section-not-marked", B,
      (CAP, '                    data["sections"][name]["included"] = "omitted_size_limit"\n', "                    pass\n")),
    m("B9-dropped-section-no-retrieval", B,
      (CAP, '                    retrieval.append(_request(data["sections"][part]["request"]))\n', "                    pass\n")),
    # -- O: an oversize section's record and retrieval path
    m("O1-section-not-marked-omitted", O,
      (CAP, '            data["sections"][section]["included"] = "omitted_size_limit"\n', "            pass\n")),
    m("O2-section-retrieval-not-in-size-limit", O,
      (CAP, '            retrieval.extend(data[section]["retrieval"])\n', "            pass\n")),
    m("O3-partition-ignores-active-category", O,
      (CAP, '"category" if "category" not in active', '"category" if True')),
    m("O4-unpartitioned-rows-lost", O,
      (CAP, "            unpartitioned.append(item)\n", "            pass\n")),
    m("O5-unpartitioned-rows-kept-oversize", O,
      (CAP, '    if not fits(record):\n        del record["unpartitioned_items"]\n',
       '    if False:\n        del record["unpartitioned_items"]\n')),
    m("O6-no-narrower-reason-missing", O,
      (CAP, '        record["retrieval_unavailable_reason"] = "no_narrower_filter"\n', "        pass\n")),
    m("O7-gateway-fields-not-dropped", O,
      (CAP, "            omitted[field] = len(value.pop(field))\n", "            omitted[field] = len(value[field])\n")),
    m("O8-gateway-nested-readmission-not-dropped", O,
      (CAP, "        if not fits(value) and isinstance(readmission, dict) and isinstance(\n",
       "        if False and isinstance(readmission, dict) and isinstance(\n")),
    m("O9-gateway-returned-oversize", O,
      (CAP, "        if fits(value):\n            return value\n", "        if True:\n            return value\n")),
    m("O10-gateway-status-retrieval-loops", O,
      (CAP, 'else {"detail_section": "dynamic_upstream", **active}', 'else {"detail_section": section, **active}')),
    m("O11-gateway-drops-smallest-first", O,
      (CAP, "            reverse=True,\n        ):\n            if fits(value):\n",
       "            reverse=False,\n        ):\n            if fits(value):\n")),
    m("O12-matrix-rows-unclassified", O,
      (CAP, '    if section != "provider_matrix":\n', "    if True:\n")),
    m("O13-oversize-detail-flag-missing", O,
      (CAP, '        value["detail_complete"] = False\n', "")),
    m("O14-fits-ignores-size-statement", O,
      (CAP, '                            "retrieval": candidate.get("retrieval", []),\n',
       '                            "retrieval": [],\n')),
    m("O15-section-marked-after-sizing", O,
      (CAP, '            # Marked before sizing, so every candidate is sized as served.\n'
            '            data["sections"][section]["included"] = "omitted_size_limit"\n', ""),
      (CAP, "            data[section] = _oversize_section(section, data[section], active, fits)\n",
       "            data[section] = _oversize_section(section, data[section], active, fits)\n"
       '            data["sections"][section]["included"] = "omitted_size_limit"\n')),
    # -- E: the tool entry point
    m("E1-cap-not-passed-to-view", EP,
      (COMPAT, "            response_limit=SETTINGS.response_size_limit,\n        )\n", "        )\n")),
    m("E2-section-not-forwarded", EP,
      (COMPAT, "            detail_section=detail_section,\n", "")),
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
