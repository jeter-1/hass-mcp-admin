"""HAMCP-252 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery: a disposable copy of the tracked
tree, every fragment matching exactly once before anything runs, and
fail-fast suites per mutant. ``test_hamcp252_sun_condition_verification``
runs first, then the HAMCP-249 reason, trigger-condition, beta 34 verifier,
configuration execution and F3 configuration lifecycle suites the change touches.

Per AGENTS.md (HAMCP-237), the work directory and every mutant's TMPDIR live
under the worktree's ignored ``.artifacts/``; the copy skips ``.artifacts``,
so it never copies itself.

Classes:
- ``V*``: the sun-condition admission loosened, dropped or mis-scoped (a sun
  field not compared, a value check removed, the fields admitted on other
  condition types or with a pass-through);
- ``L*``: the refusal location removed, misplaced or unbounded in the
  normalizer or the adapter;
- ``D*``: the stored location decoded unsafely or not at all.

Recorded runs (2026-10-30, copied tree under .artifacts/):
- ``results.json``: full run, 30 of 32 killed. Survivors: V10 (a non-string
  ``after``/``before`` that renders as a sun event) and L10 (path tokens
  outside the fixed vocabulary), each a missing test, not dead code.
- ``results-rerun.json``: ``--only V10 --only L10`` after adding
  ``test_non_string_values_that_render_as_sun_events_are_refused`` and
  ``test_path_encoder_refuses_tokens_outside_the_fixed_vocabulary``; both
  killed. Net: 32 of 32 killed, no survivors.

Usage:
    systemd-run --user --scope -p MemoryMax=3G -p OOMPolicy=continue \\
        python docs/evidence/hamcp252-mutations/run_mutations.py \\
        --work .artifacts/hamcp252-mut --output docs/evidence/hamcp252-mutations/results.json
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
AD = f"{E}/f3_configuration/adapter.py"
NZ = f"{E}/governance/normalize.py"
PR = f"{E}/f3_runtime/pre_dispatch_reasons.py"

V = "sun-condition admission loosened, dropped or mis-scoped"
L = "refusal location removed, misplaced or unbounded"
D = "stored location decoded unsafely or not at all"

SUN_GATE = (
    'if family == "condition" and value[family] == "sun":\n'
    "        # HAMCP-252: Home Assistant accepts"
)

MUTATIONS = [
    m("V1-sun-fields-not-admitted", V,
      (NZ, "allowed = allowed | AUTOMATION_SUN_CONDITION_FIELDS", "allowed = allowed")),
    m("V2-sun-fields-dropped-from-comparison", V,
      (NZ, "        _validate_simple_action(normalized, action_family)\n",
       "        _validate_simple_action(normalized, action_family)\n"
       "        for _field in AUTOMATION_SUN_CONDITION_FIELDS:\n"
       "            normalized.pop(_field, None)\n")),
    m("V3-sun-after-dropped-from-comparison", V,
      (NZ, "        _validate_simple_action(normalized, action_family)\n",
       "        _validate_simple_action(normalized, action_family)\n"
       "        normalized.pop(\"after\", None)\n")),
    m("V4-sun-values-compared-case-insensitively", V,
      (NZ, "        _validate_simple_action(normalized, action_family)\n",
       "        _validate_simple_action(normalized, action_family)\n"
       "        for _field in (\"after\", \"before\"):\n"
       "            if isinstance(normalized.get(_field), str):\n"
       "                normalized[_field] = normalized[_field].lower()\n")),
    m("V5-sun-fields-admitted-on-any-condition", V,
      (NZ, SUN_GATE, 'if family == "condition":\n        # HAMCP-252: Home Assistant accepts')),
    m("V6-sun-step-pass-through", V,
      (NZ, "allowed = allowed | AUTOMATION_SUN_CONDITION_FIELDS",
       "allowed = allowed | AUTOMATION_SUN_CONDITION_FIELDS | set(value)")),
    m("V7-sun-event-check-removed", V,
      (NZ, "                and value[field].lower() in AUTOMATION_SUN_EVENTS\n", "")),
    m("V8-sun-event-case-sensitive", V,
      (NZ, "and value[field].lower() in AUTOMATION_SUN_EVENTS",
       "and value[field] in AUTOMATION_SUN_EVENTS")),
    m("V9-sun-offset-check-removed", V,
      (NZ, "if field in value and not _valid_time_period(value[field]):",
       "if False:")),
    m("V10-sun-string-check-removed", V,
      (NZ, "                isinstance(value[field], str)\n                and value[field].lower()",
       "                str(value[field]).lower()")),
    m("L1-locate-ignored", L,
      (NZ, "        if self.path is None:\n            self.path = path",
       "        if False:\n            self.path = path")),
    m("L2-outer-step-overrides-inner", L,
      (NZ, "        if self.path is None:\n            self.path = path",
       "        if True:\n            self.path = path")),
    m("L3-adapter-location-not-stored", L,
      (AD, "                    *location,\n", "")),
    m("L4-fields-not-recorded", L,
      (NZ, "self.fields = tuple(sorted(str(field) for field in fields))", "self.fields = ()")),
    m("L5-unknown-field-names-leak", L,
      (NZ, "            field\n            if field in AUTOMATION_VERIFIER_KNOWN_FIELDS\n"
           "            else AUTOMATION_VERIFIER_UNRECOGNIZED_FIELD\n",
       "            field\n")),
    m("L6-field-cap-removed", L,
      (NZ, "for name in names[:MAX_AUTOMATION_VERIFIER_LOCATION_FIELDS]", "for name in names")),
    m("L7-fields-truncation-marker-dropped", L,
      (NZ, "        codes += (AUTOMATION_VERIFIER_FIELDS_TRUNCATED,)", "        pass")),
    m("L8-path-length-unbounded", L,
      (NZ, "        if len(candidate) > MAX_AUTOMATION_VERIFIER_CODE_LENGTH:\n"
           "            return code, AUTOMATION_VERIFIER_PATH_TRUNCATED",
       "        if False:\n            return code, AUTOMATION_VERIFIER_PATH_TRUNCATED")),
    m("L9-path-truncation-marker-dropped", L,
      (NZ, "            return code, AUTOMATION_VERIFIER_PATH_TRUNCATED", "            return (code,)")),
    m("L10-path-token-vocabulary-unchecked", L,
      (NZ, "        elif isinstance(token, str) and token in AUTOMATION_VERIFIER_PATH_TOKENS:",
       "        elif isinstance(token, str):")),
    m("L11-root-always-action", L,
      (NZ, 'root = "actions" if "actions" in config else "action"', 'root = "action"')),
    m("L12-else-branch-reported-as-then", L,
      (NZ, 'path=(*path, "else"),', 'path=(*path, "then"),')),
    m("L13-repeat-key-omitted-from-path", L,
      (NZ, 'path=(*path, "repeat", "sequence"),', 'path=(*path, "sequence"),')),
    m("L14-choose-index-omitted-from-path", L,
      (NZ, 'choice_path = (*path, "choose", index)', 'choice_path = (*path, "choose")')),
    m("L15-location-codes-dropped", L,
      (NZ, "    return _location_path_codes(exc.path) + _location_field_codes(exc.fields)",
       "    return ()")),
    m("D1-reason-location-dropped", D,
      (PR, '"verifier_location": None if rule is None else verifier_location(codes),',
       '"verifier_location": None,')),
    m("D2-location-without-rule", D,
      (PR, '"verifier_location": None if rule is None else verifier_location(codes),',
       '"verifier_location": verifier_location(codes),')),
    m("D3-decoder-accepts-unknown-path-tokens", D,
      (PR, "            (token in VERIFIER_PATH_TOKENS and token not in VERIFIER_PATH_ROOTS)\n"
           "            or _INDEX.fullmatch(token)\n",
       "            True\n")),
    m("D4-decoder-accepts-several-paths", D,
      (PR, "    if len(paths) == 1:", "    if paths:")),
    m("D5-decoder-leaks-unknown-field-names", D,
      (PR, "            name if name in VERIFIER_KNOWN_FIELDS else UNRECOGNIZED_FIELD\n", "            name\n")),
    m("D6-decoder-field-cap-removed", D,
      (PR, '"fields": fields[:MAX_LOCATION_FIELDS],', '"fields": fields,')),
    m("D7-decoder-path-truncation-ignored", D,
      (PR, '"path_truncated": path is not None and VERIFIER_PATH_TRUNCATED in codes,',
       '"path_truncated": False,')),
]

base.MODULES = ("test_hamcp252_sun_condition_verification", "test_hamcp249_pre_dispatch_reasons",
                "test_trigger_condition_verification", "test_beta34_automation_verification",
                "test_hamcp135_configuration_execution", "test_f3_configuration_lifecycle")
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
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--only", action="append", default=[],
                        help="run only these mutation ids (repeatable); the baseline always runs")
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
    unknown = set(arguments.only) - {item["id"] for item in MUTATIONS}
    if unknown:
        raise SystemExit(f"unknown mutation ids: {sorted(unknown)}")
    for mutation in MUTATIONS:
        if arguments.only and mutation["id"] not in arguments.only:
            continue
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
            # A timeout is not a kill: the fail-fast suites only run to the
            # limit when no earlier test failed.
            **mutation, "log_path": log_path.relative_to(work).as_posix(), "returncode": code,
            "killed": code != 0 and output != "TIMEOUT", "timed_out": output == "TIMEOUT",
            "killed_by_import_or_syntax_error": any(
                marker in output for marker in ("ImportError", "SyntaxError", "Failed to import")),
            "first_failures": failures[:3], "seconds": round(seconds, 1),
        })
        print(f"{'KILLED' if results[-1]['killed'] else 'SURVIVED':8} {mutation['id']:48} {failures[:1]}", flush=True)

    survived = [item["id"] for item in results if not item["killed"]]
    document = {"baseline": baseline, "mutation_count": len(results),
                "killed": sum(item["killed"] for item in results), "survived": survived,
                "python": platform.python_version(), "results": results}
    arguments.output.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
