"""HAMCP-447 (2.11.0 Lane B2): guard mutants of native dashboard search.

Approved by Josh as relayed by the architect, 2026-11-27: dashboards are
searched natively, YAML dashboards are skipped with a closed code, the
default dashboard is read and matched (d1), and the websocket client admits
``lovelace/config`` in batches in two shapes only (w1).

The guarded code is ``search/dashboards.py`` (the read plan and the match),
the dashboard configuration read in ``search/core_reads.py``, its contract
in ``search/core_contracts.py``, the wiring in ``search/configs.py``, the
helper row's ``entry_id`` in ``search/subcalls.py``, the batch shapes in
``clients/websocket.py``, Row 25's names and the keys helper's buckets in
``providers/upstream_reads_9_2_keys.py`` and the dashboard profile
requirement in ``ha_core_readmission/routes.py``. Each mutant edits one
module, served by an in-memory import hook (the files on disk are not
changed). Several of these modules are imported by most of the package, so
each mutant runs in a fresh interpreter, which imports the mutated module
through the hook and then runs
``tests/test_hamcp447_native_dashboard_search.py``; the suite must fail at
least one test. The mutants remove or weaken a guard: a YAML, mode, title,
path, default or bound rule of the read plan, a disclosure count, the match
threshold, the deadline, a result or outcome check, the withheld body, an
argument, chunk or failure rule of the configuration read, the never-sent
``force``, the path grammar, the dashboard profile, the listed title, the
wiring, the batch shapes, Row 25's names or the profile requirement.

``EQUIVALENT`` holds an edit that changes no observable outcome, with the
reason; it is run and reported, not required to fail. It is empty.

Usage, from the repository root::

    python -I docs/evidence/hamcp447/dashboard_mutation_check.py

Exit status 1 when a non-equivalent mutant survives, 2 when the baseline
fails or an anchor is not found exactly once.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
PACKAGE = "ha_mcp_engineering"
SUITE = "tests.test_hamcp447_native_dashboard_search"
MODULES = (
    "search.dashboards", "search.core_reads", "search.core_contracts", "search.configs",
    "search.subcalls", "clients.websocket", "providers.upstream_reads_9_2_keys",
    "ha_core_readmission.routes",
)
SOURCES = {
    name: (PACKAGE_ROOT / (name.replace(".", "/") + ".py")).read_text(encoding="utf-8")
    for name in MODULES
}
DASH, READS, CONTRACTS, CONFIGS, SUBCALLS, WEBSOCKET, KEYS, ROUTES = MODULES

# name -> (module, ((original text, mutated text), ...))
MUTANTS = {
    # dashboards.plan: what is read, and what is counted.
    "yaml_dashboard_read": (DASH, (
        ("        if mode == YAML_MODE:\n", "        if False:\n"),
        ("if (mode != STORAGE_MODE or type(title)", "if (type(title)"))),
    "yaml_uncounted": (DASH, (
        ('    reasons.add("not_searched", "dashboard", "yaml_dashboards_skipped", yaml)\n', ""),)),
    "mode_unchecked": (DASH, (("if (mode != STORAGE_MODE or type(title)", "if (type(title)"),)),
    "title_type_unchecked": (DASH, (
        ("type(title) is not str or title is INVALID", "title is INVALID"),)),
    "url_path_unchecked": (DASH, (
        ("or type(url_path) is not str or c.URL_PATH.fullmatch(url_path) is None\n",
         "or type(url_path) is not str\n"),)),
    "default_clash_unchecked": (DASH, (
        ("                or (read_default and url_path == DEFAULT_URL_PATH)\n", ""),)),
    "bound_unchecked": (DASH, (("\n                or listed >= c.MAX_DASHBOARDS):", "):"),)),
    "default_read_with_lovelace": (DASH, ((" and SERVED_AS_DEFAULT not in rows", ""),)),
    "default_read_with_dropped": (DASH, (
        ("read_default = not listing.dropped and ", "read_default = "),)),
    "dropped_undisclosed": (DASH, (
        ("    if listing.dropped:\n"
         "        # A dropped row may be the one Core would serve as the default.\n"
         '        reasons.add("not_searched", "dashboard", "read_failed", None)\n', ""),)),
    "listing_status_unchecked": (DASH, (
        ("if (type(listing) is not ReadResult or listing.status not in (c.COMPLETE, c.PARTIAL)\n",
         "if (type(listing) is not ReadResult\n"),)),
    "plan_failed_uncounted": (DASH, (
        ('    reasons.add("not_searched", "dashboard", "read_failed", failed)\n    return targets',
         "    return targets"),)),
    "title_fallback_dropped": (DASH, (
        ("targets.append((url_path, title or url_path))", "targets.append((url_path, title))"),)),
    # dashboards.search_dashboards: the match and its disclosure.
    "args_unchecked": (DASH, (
        ("if type(query_lower) is not str or not query_lower or type(exact_match) is not bool:",
         "if False:"),)),
    "threshold_fixed": (DASH, (
        ("scoring.EXACT_MATCH_THRESHOLD if exact_match else scoring.FUZZY_THRESHOLD",
         "scoring.FUZZY_THRESHOLD"),)),
    "below_threshold_kept": (DASH, ((" and score >= threshold:", ":"),)),
    "deadline_unset": (DASH, (
        ("deadline=monotonic() + subcalls.SCAN_DEADLINE_SECONDS", "deadline=None"),)),
    "result_unchecked": (DASH, (
        ("    if (type(result) is not ReadResult or result.status not in (c.COMPLETE, c.PARTIAL)\n"
         "            or not isinstance(result.value, Mapping)):\n",
         "    if False:\n"),)),
    "unusable_result_uncounted": (DASH, (
        ('        reasons.add("not_searched", "dashboard", "read_failed", len(targets))\n',
         "        pass\n"),)),
    "item_outcome_unchecked": (DASH, (
        ('        if outcome not in ("read", "not_found"):\n            failed += 1\n            continue\n',
         ""),)),
    "default_path_unreported": (DASH, (
        ('"url_path": DEFAULT_URL_PATH if target is None else target', '"url_path": target'),)),
    "body_not_withheld": (DASH, (
        ('"match_in_references": False}, True))', '"match_in_references": False}, False))'),)),
    "search_failed_uncounted": (DASH, (
        ('    reasons.add("not_searched", "dashboard", "read_failed", failed)\n    return records',
         "    return records"),)),
    # core_reads.dashboard_configs and its outcomes.
    "declared_unchecked": (READS, (
        ('        name = "dashboard_config"\n        self._declared(name, "config")\n',
         '        name = "dashboard_config"\n'),)),
    "targets_unbounded": (READS, (
        ("if (targets is None or len(targets) > c.MAX_DASHBOARDS + 1\n", "if (targets is None\n"),)),
    "target_types_unchecked": (READS, (
        ("                or not all(target is None or type(target) is str for target in targets)\n",
         ""),)),
    "duplicate_targets_allowed": (READS, (
        ("                or len(set(targets)) != len(targets) or not callable(measure)\n",
         "                or not callable(measure)\n"),)),
    "measure_unchecked": (READS, (
        (" or not callable(measure)\n                or not (deadline is None",
         "\n                or not (deadline is None"),)),
    "deadline_arguments_unchecked": (READS, (
        ("                        or (type(deadline) in (int, float) and callable(monotonic)))):",
         "                        or True)):"),)),
    "deadline_ignored": (READS, (
        ("if deadline is not None and monotonic() >= deadline:", "if False:"),)),
    "timeout_rest_unmarked": (READS, (
        ('                items.update(dict.fromkeys(targets[start:], "failed"))\n                break',
         "                break"),)),
    "timeout_misreported": (READS, (
        ('                reasons.append("timeout")\n', '                reasons.append("command_failed")\n'),)),
    "chunk_length_unchecked": (READS, (
        ("if reason is None and (type(raw) is not list or len(raw) != len(chunk)):",
         "if reason is None and type(raw) is not list:"),)),
    "chunk_failure_unmarked": (READS, (
        ('                items.update(dict.fromkeys(chunk, "failed"))\n                continue',
         "                continue"),)),
    "all_failed_partial": (READS, (
        ("        if failed == len(targets):\n"
         '            return self._record(_unavailable(name, reasons[0] if reasons else "command_failed"))\n',
         ""),)),
    "config_item_failures_silent": (READS, (
        ('        partial = {"item_failures"} if failed else set()\n'
         "        return self._record(_finished(name, MappingProxyType(scores), partial, items=items))",
         "        partial = set()\n"
         "        return self._record(_finished(name, MappingProxyType(scores), partial, items=items))"),)),
    "outcome_shape_unchecked": (READS, (
        ("    if type(outcome) is not tuple or len(outcome) != 2:\n        return \"failed\", None\n"
         "    ok, value = outcome\n",
         "    ok, value = outcome\n"),)),
    "config_not_dict_scored": (READS, (
        ('        if type(value) is not dict:\n            return "failed", None\n        score = measure(value)',
         "        score = measure(value)"),)),
    "config_score_unchecked": (READS, (
        ('return ("read", score) if type(score) is int else ("failed", None)', 'return "read", score'),)),
    "not_found_any_false": (READS, (
        ('"not_found" if ok is False and value == "config_not_found" else "failed"',
         '"not_found" if ok is False else "failed"'),)),
    # core_contracts: the payload, the grammar, the profile and the fields.
    "force_sent": (CONTRACTS, (
        ('    return {"type": read.command, "url_path": url_path}\n',
         '    return {"type": read.command, "url_path": url_path, "force": False}\n'),)),
    "config_url_path_unchecked": (CONTRACTS, (
        ("    if type(url_path) is not str or read.argument.fullmatch(url_path) is None:\n",
         "    if type(url_path) is not str:\n"),)),
    "url_path_grammar_widened": (CONTRACTS, (
        ('URL_PATH = re.compile(r"[a-z0-9_-]{1,256}\\Z")', 'URL_PATH = re.compile(r"[a-z0-9_/-]{1,256}\\Z")'),)),
    "dashboard_bound_raised": (CONTRACTS, (("MAX_DASHBOARDS = 500\n", "MAX_DASHBOARDS = 501\n"),)),
    "listing_requirement_basic": (CONTRACTS, (
        ('_rows("dashboards", "lovelace/dashboards/list", DASHBOARD, True,',
         '_rows("dashboards", "lovelace/dashboards/list", BASIC_WEBSOCKET, True,'),)),
    "config_requirement_basic": (CONTRACTS, (
        ('"lovelace/config", DASHBOARD, True, "config",', '"lovelace/config", BASIC_WEBSOCKET, True, "config",'),)),
    "title_field_dropped": (CONTRACTS, (
        ('"dashboards": MappingProxyType({"mode": STR, "title": STR}),',
         '"dashboards": MappingProxyType({"mode": STR}),'),)),
    # configs: the wiring.
    "dashboard_reads_always": (CONFIGS, (
        ('    if "dashboard" in types:\n        names.update(("dashboards", "dashboard_config"))\n',
         '    if True:\n        names.update(("dashboards", "dashboard_config"))\n'),)),
    "config_read_undeclared": (CONFIGS, (
        ('        names.update(("dashboards", "dashboard_config"))\n', '        names.update(("dashboards",))\n'),)),
    "dashboard_subcall_scanned": (CONFIGS, (
        ('if search_type not in types or search_type == "dashboard":', "if search_type not in types:"),)),
    "wiring_skipped": (CONFIGS, (
        ('    if "dashboard" in types:\n        # HAMCP-447: over the list',
         "    if False:\n        # HAMCP-447: over the list"),)),
    # subcalls: the flow-helper row's entry_id.
    "helper_entry_id_dropped": (SUBCALLS, (
        ('"helper": frozenset({"entity_id", "entry_id", "helper_type",',
         '"helper": frozenset({"entity_id", "helper_type",'),)),
    "entry_id_kind_unchecked": (SUBCALLS, (('"match_in_references": bool, "entry_id": str,',
                                            '"match_in_references": bool, "entry_id": object,'),)),
    # clients/websocket.py: the two batch shapes (w1).
    "ws_force_admitted": (WEBSOCKET, (
        ('    "lovelace/config": (frozenset({"type"}), frozenset({"type", "url_path"})),\n',
         '    "lovelace/config": (frozenset({"type"}), frozenset({"type", "url_path"}),\n'
         '                        frozenset({"type", "url_path", "force"})),\n'),)),
    "ws_url_path_unchecked": (WEBSOCKET, (
        ("return isinstance(url_path, str) and URL_PATH_PATTERN.fullmatch(url_path) is not None",
         "return isinstance(url_path, str)"),)),
    "ws_default_shape_refused": (WEBSOCKET, (
        ('        if "url_path" not in payload:\n            return True\n',
         '        if "url_path" not in payload:\n            return False\n'),)),
    # Row 25 and the keys helper's buckets.
    "keys_dashboards_bucket_admitted": (KEYS, (
        ('"automations", "scripts", "scenes", "helpers",',
         '"automations", "scripts", "scenes", "dashboards", "helpers",'),)),
    "row25_entry_id_dropped": (KEYS, (
        ("'entity_total_matches', 'entry_id', 'errors',", "'entity_total_matches', 'errors',"),)),
    "row25_identity_dropped": (KEYS, (
        ("'success', 'title', 'url_path', 'warnings'", "'success', 'warnings'"),)),
    # routes.static_tool_requirements: the dashboard profile.
    "profile_requirement_dropped": (ROUTES, (
        ("        requirements = (*requirements, *core_contracts.requirements_for(reads))\n",
         "        pass\n"),)),
    "refused_call_holds_profile": (ROUTES, (
        ("        except (engine.SearchError, ValueError):\n            reads = ()\n",
         '        except (engine.SearchError, ValueError):\n            reads = ("dashboards",)\n'),)),
}

EQUIVALENT: dict = {}


class _Mutant(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves one module from mutated source text; writes no bytecode."""

    def __init__(self, module: str, source: str) -> None:
        self.fullname = f"{PACKAGE}.{module}"
        self.path = str(PACKAGE_ROOT / (module.replace(".", "/") + ".py"))
        self.source = source

    def find_spec(self, fullname, path, target=None):
        if fullname != self.fullname:
            return None
        return importlib.util.spec_from_file_location(fullname, self.path, loader=self)

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        exec(compile(self.source, self.path, "exec"), module.__dict__)


def mutated(module: str, edits) -> str:
    source = SOURCES[module]
    for original, replacement in edits:
        source = source.replace(original, replacement)
    return source


def child(name: str | None) -> int:
    """Run the suite in this interpreter, ``name``'s module mutated."""

    sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]
    sys.dont_write_bytecode = True
    finder = None
    if name is not None:
        module, edits = {**MUTANTS, **EQUIVALENT}[name]
        finder = _Mutant(module, mutated(module, edits))
        sys.meta_path.insert(0, finder)
    suite = importlib.import_module(SUITE)
    if finder is not None:
        module = sys.modules.get(finder.fullname)
        if module is None or module.__spec__.loader is not finder:
            raise RuntimeError("mutant not loaded")
    result = unittest.TestResult()
    unittest.TestLoader().loadTestsFromModule(suite).run(result)
    print(json.dumps({"failing": len(result.failures) + len(result.errors), "run": result.testsRun}))
    return 0


def failures_for(name: str | None) -> tuple[int, int]:
    command = [sys.executable, "-I", __file__, "--child"] + ([name] if name else [])
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=600, env=dict(os.environ),
    )
    lines = [line for line in completed.stdout.splitlines() if line.startswith('{"failing"')]
    if completed.returncode != 0 or len(lines) != 1:
        print(f"{name}: child failed (exit {completed.returncode})", file=sys.stderr)
        print(completed.stderr[-2000:], file=sys.stderr)
        raise SystemExit(2)
    record = json.loads(lines[0])
    return record["failing"], record["run"]


def main() -> int:
    for name, (module, edits) in {**MUTANTS, **EQUIVALENT}.items():
        if not edits or any(SOURCES[module].count(original) != 1 for original, _ in edits):
            print(f"{name}: anchor not found once", file=sys.stderr)
            return 2
    baseline, run = failures_for(None)
    print(f"baseline: failing tests={baseline} of {run}")
    if baseline:
        return 2
    survivors = []
    for group, mutants in (("mutant", MUTANTS), ("equivalent", EQUIVALENT)):
        for name in mutants:
            started = time.monotonic()
            count, run = failures_for(name)
            print(f"{group} {name}: failing tests={count} of {run} ({time.monotonic() - started:.1f}s)")
            if group == "mutant" and count == 0:
                survivors.append(name)
    print(f"mutants: {len(MUTANTS)}, equivalent: {len(EQUIVALENT)}")
    print("survivors: " + (", ".join(survivors) or "none"))
    return 1 if survivors else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        raise SystemExit(child(sys.argv[2] if len(sys.argv) > 2 else None))
    raise SystemExit(main())
