"""HAMCP-501 W6 F1: guard mutants of native search's route body-match rule.

Codex r1 F1 (High): ha-mcp 9.2.0 scores a numeric body leaf without the
secret scrub, and its automation, script and scene pages carry no
degraded-scrub flag. The guarded code is the A'+ trigger
(``body_match_unsafe``), the helper probe and page scrub states
(``probe_scrub``, ``worst_scrub`` and the scan's scrub, in
``search/subcalls.py``), the body codes and the name-only rescore
(``search/configs.py``), the reply's reason codes (``search/reply.py``), the
raw page classifier (``page_scrub_state`` in
``providers/upstream_reads_9_2_keys.py``) and the gateway's scrub capture
(``providers/upstream_read_gateway.py``). Each mutant is one textual edit of
one module, served by an in-memory import hook (the files on disk are not
changed). The gateway imports ``search.subcalls``, so each mutant runs in a
fresh interpreter, which imports the mutated module through the hook and then
runs ``tests/test_hamcp501_w6_f1_body_matching.py`` and
``tests/test_hamcp501_search_subcall_readers.py``, both under both
descriptors. The suites stop at their first failing test, which the report
names; the mutant is killed when one test fails.

``EQUIVALENT`` holds an edit that changes no observable outcome, with the
reason; it is run and reported, not required to fail.

Usage, from the repository root::

    python -I docs/evidence/hamcp501/f1/body_match_mutation_check.py

Exit status 1 when a non-equivalent mutant survives, 2 when an anchor is not
found exactly once or the baseline fails.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[4]
ENGINEERING = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
SUITES = ("tests.test_hamcp501_w6_f1_body_matching", "tests.test_hamcp501_search_subcall_readers")
# module key -> (package, file)
TARGETS = {
    "subcalls": ("ha_mcp_engineering.search", ENGINEERING / "search/subcalls.py"),
    "configs": ("ha_mcp_engineering.search", ENGINEERING / "search/configs.py"),
    "reply": ("ha_mcp_engineering.search", ENGINEERING / "search/reply.py"),
    "keys": ("ha_mcp_engineering.providers", ENGINEERING / "providers/upstream_reads_9_2_keys.py"),
    "gateway": ("ha_mcp_engineering.providers", ENGINEERING / "providers/upstream_read_gateway.py"),
}
MODULE_NAMES = {
    "subcalls": "subcalls", "configs": "configs", "reply": "reply",
    "keys": "upstream_reads_9_2_keys", "gateway": "upstream_read_gateway",
}
SOURCES = {key: path.read_text(encoding="utf-8") for key, (_, path) in TARGETS.items()}

# name -> (module key, original text, mutated text)
MUTANTS = {
    # The A'+ trigger.
    "trigger_exact_arm_removed": (
        "subcalls", "    if all(char in _NUMERIC_CHARS for char in q):\n        return True\n", ""),
    "trigger_fuzzy_arm_removed": (
        "subcalls", "    return not exact_match and any(\n", "    return False and any(\n"),
    "trigger_charset_digits_only": (
        "subcalls", 'frozenset("0123456789.+-e:t ")', 'frozenset("0123456789")'),
    "trigger_charset_no_time": (
        "subcalls", 'frozenset("0123456789.+-e:t ")', 'frozenset("0123456789.+-e")'),
    "trigger_empty_query_unsafe": (
        "subcalls", "    if not q:\n        return False\n", "    if not q:\n        return True\n"),
    # Scrub states.
    "worst_odd_value_clean": (
        "subcalls", "            state = SCRUB_UNVERIFIED\n        if _SCRUB_ORDER",
        "            state = SCRUB_CLEAN\n        if _SCRUB_ORDER"),
    "worst_order_degraded_below_unverified": (
        "subcalls", "{SCRUB_CLEAN: 0, SCRUB_UNVERIFIED: 1, SCRUB_DEGRADED: 2}",
        "{SCRUB_CLEAN: 0, SCRUB_UNVERIFIED: 2, SCRUB_DEGRADED: 1}"),
    "scan_page_scrub_dropped": (
        "subcalls", "        worst = worst_scrub(worst, page.scrub)\n", ""),
    "page_scrub_unchecked": (
        "subcalls", "        and type(page.scrub) is str\n        and page.scrub in SCRUB_STATES\n", ""),
    "helper_not_a_body_type": (
        "subcalls", 'BODY_TYPES = ("automation", "script", "scene", "helper")',
        'BODY_TYPES = ("automation", "script", "scene")'),
    # The helper probe.
    "probe_no_reader_clean": (
        "subcalls", "    if reader is None:\n        return SCRUB_UNVERIFIED\n",
        "    if reader is None:\n        return SCRUB_CLEAN\n"),
    "probe_failure_clean": (
        "subcalls", "    except Exception:\n        return SCRUB_UNVERIFIED\n",
        "    except Exception:\n        return SCRUB_CLEAN\n"),
    "probe_invalid_page_clean": (
        "subcalls", '_page_valid("helper", page, PROBE_SIZE) else SCRUB_UNVERIFIED',
        '_page_valid("helper", page, PROBE_SIZE) else SCRUB_CLEAN'),
    "probe_wrong_type": (
        "subcalls", 'arguments = page_arguments("helper", query, exact_match, 0, PROBE_SIZE)',
        'arguments = page_arguments("automation", query, exact_match, 0, PROBE_SIZE)'),
    "probes_not_read": (
        "configs", "    probed = not unsafe_query and any(", "    probed = False and any("),
    "probe_before_skipped": (
        "configs", "    probes = []\n    if probed:\n", "    probes = []\n    if False:\n"),
    "probe_after_skipped": (
        "configs", "        outcomes[search_type] = outcome\n    if probed:\n",
        "        outcomes[search_type] = outcome\n    if False:\n"),
    "probes_ignored": (
        "configs", "        *probes, *(outcome.scrub", "        *(outcome.scrub"),
    "page_scrubs_ignored": (
        "configs", "        *probes, *(outcome.scrub for search_type, outcome in outcomes.items()\n"
        "                   if search_type in subcalls.BODY_TYPES)))", "        *probes))"),
    # The body codes.
    "numeric_code_skipped": (
        "configs", '    if unsafe_query:\n        return "body_numeric_query"\n', ""),
    "degraded_code_skipped": (
        "configs", '    if scrub == subcalls.SCRUB_DEGRADED:\n        return "body_scrub_degraded"\n', ""),
    "unverified_code_skipped": (
        "configs", '    if scrub != subcalls.SCRUB_CLEAN:\n        return "body_scrub_unverified"\n', ""),
    "code_on_every_type": (
        "configs", "        if (code is not None and search_type in subcalls.BODY_TYPES\n",
        "        if (code is not None\n"),
    "not_admitted_coded": (
        "configs", "\n                and outcome.status != subcalls.NOT_ADMITTED):", "):"),
    "code_reason_not_added": (
        "configs", '            reasons.add("not_searched", search_type, code, None)\n', ""),
    "unverified_code_unlisted": (
        "reply", '"body_numeric_query", "body_scrub_degraded", "body_scrub_unverified",',
        '"body_numeric_query", "body_scrub_degraded",'),
    # The name-only rescore.
    "rows_not_rescored": (
        "configs", "            rows = tuple(kept for kept in (_name_only(row, request) for row in rows)\n"
        "                         if kept is not None)\n", ""),
    "unmatched_row_kept": (
        "configs", "    if score is None:\n        return None\n    return {**row,",
        '    if score is None:\n        score = row["score"]\n    return {**row,'),
    "route_score_kept": (
        "configs", 'return {**row, "score": score,', 'return {**row, "score": row["score"],'),
    "match_in_config_kept": (
        "configs", '"match_in_name": True, "match_in_config": False}', '"match_in_name": True}'),
    "name_tier_fuzzy_threshold_lowered": (
        "configs", "ROUTE_FUZZY_THRESHOLD = 70\n", "ROUTE_FUZZY_THRESHOLD = 60\n"),
    "page_withheld_kept_under_code": (
        "configs", "            withheld = _kept_withheld(search_type, rows)\n", ""),
    "flow_withheld_without_flow_row": (
        "configs", '    if search_type == "helper" and any("entity_id" not in row for row in rows):',
        '    if search_type == "helper":'),
    # The raw page classifier.
    "keys_non_dict_clean": (
        "keys", "    if type(payload) is not dict:\n        return SCRUB_UNVERIFIED\n",
        "    if type(payload) is not dict:\n        return SCRUB_CLEAN\n"),
    "keys_odd_warnings_clean": (
        "keys", "for text in warnings):\n        return SCRUB_UNVERIFIED\n",
        "for text in warnings):\n        return SCRUB_CLEAN\n"),
    "keys_degraded_not_read": (
        "keys", "        return SCRUB_DEGRADED\n", "        return SCRUB_UNVERIFIED\n"),
    "keys_any_warning_neutral": (
        "keys", "text in SEARCH_NEUTRAL_PAGE_WARNINGS or text == mirror", "True"),
    "keys_mirror_without_partial": (
        "keys", 'if payload.get("partial") is True and type(reason) is str', "if type(reason) is str"),
    "keys_no_warnings_clean": (
        "keys", "    if warnings and all(", "    if all("),
    "keys_unknown_clean": (
        "keys", "        return SCRUB_CLEAN\n    return SCRUB_UNVERIFIED\n",
        "        return SCRUB_CLEAN\n    return SCRUB_CLEAN\n"),
    # The gateway's scrub capture.
    "gateway_preset_scrub_accepted": (
        "gateway", "                or search_page.scrub is not None\n", ""),
    "gateway_scrub_not_read": (
        "gateway", "                        search_page.scrub = keys_9_2.page_scrub_state(payload)\n", ""),
    "gateway_scrub_fixed_clean": (
        "gateway", "search_page.scrub = keys_9_2.page_scrub_state(payload)",
        "search_page.scrub = keys_9_2.SCRUB_CLEAN"),
    "gateway_invalid_scrub_accepted": (
        "gateway", "\n        or type(capture.scrub) is not str or capture.scrub not in keys_9_2.SCRUB_STATES\n",
        "\n"),
    "gateway_scrub_not_carried": (
        "gateway", "        scrub=capture.scrub,\n", ""),
}

EQUIVALENT: dict = {}

# A failing child's stderr is shown only with these lines removed.
_HIDDEN = re.compile(r"secret|token|bearer|password|https?:", re.IGNORECASE)


class _Mutant(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves one module from mutated source text; writes no bytecode."""

    def __init__(self, key: str, source: str) -> None:
        package, path = TARGETS[key]
        self.fullname = f"{package}.{MODULE_NAMES[key]}"
        self.path = str(path)
        self.source = source

    def find_spec(self, fullname, path, target=None):
        if fullname != self.fullname:
            return None
        return importlib.util.spec_from_file_location(fullname, self.path, loader=self)

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        exec(compile(self.source, self.path, "exec"), module.__dict__)


def child(name: str | None) -> int:
    """Run the suites in this interpreter, ``name``'s module mutated."""

    sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]
    sys.dont_write_bytecode = True
    finder = None
    if name is not None:
        key, original, mutated = {**MUTANTS, **EQUIVALENT}[name]
        finder = _Mutant(key, SOURCES[key].replace(original, mutated))
        sys.meta_path.insert(0, finder)
    suites = [importlib.import_module(suite) for suite in SUITES]
    if finder is not None:
        module = sys.modules[finder.fullname]
        if module.__spec__.loader is not finder:
            raise RuntimeError("mutant not loaded")
    result = unittest.TestResult()
    result.failfast = True
    loader = unittest.TestLoader()
    unittest.TestSuite(loader.loadTestsFromModule(suite) for suite in suites).run(result)
    failing = result.failures + result.errors
    print(json.dumps({"failing": len(failing), "run": result.testsRun,
                      "first": failing[0][0].id() if failing else None}))
    return 0


def failures_for(name: str | None) -> tuple[int, int, str | None]:
    command = [sys.executable, "-I", __file__, "--child"] + ([name] if name else [])
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=900, env=dict(os.environ),
    )
    lines = [line for line in completed.stdout.splitlines() if line.startswith('{"failing"')]
    if completed.returncode != 0 or len(lines) != 1:
        print(f"{name}: child failed (exit {completed.returncode})", file=sys.stderr)
        shown = [line for line in completed.stderr[-4000:].splitlines() if not _HIDDEN.search(line)]
        print("\n".join(shown), file=sys.stderr)
        raise SystemExit(2)
    record = json.loads(lines[0])
    return record["failing"], record["run"], record["first"]


def main() -> int:
    for name, (key, original, _) in {**MUTANTS, **EQUIVALENT}.items():
        if SOURCES[key].count(original) != 1:
            print(f"{name}: anchor not found once", file=sys.stderr)
            return 2
    baseline, run, _ = failures_for(None)
    print(f"baseline: failing tests={baseline} of {run}")
    if baseline:
        return 2
    survivors = []
    for group, mutants in (("mutant", MUTANTS), ("equivalent", EQUIVALENT)):
        for name in mutants:
            started = time.monotonic()
            count, run, first = failures_for(name)
            killed = f"killed by {first} after {run} tests" if count else f"survived {run} tests"
            print(f"{group} {name}: {killed} ({time.monotonic() - started:.1f}s)", flush=True)
            if group == "mutant" and count == 0:
                survivors.append(name)
    print(f"mutants: {len(MUTANTS)}, equivalent: {len(EQUIVALENT)}")
    print("survivors: " + (", ".join(survivors) or "none"))
    return 1 if survivors else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        raise SystemExit(child(sys.argv[2] if len(sys.argv) > 2 else None))
    raise SystemExit(main())
