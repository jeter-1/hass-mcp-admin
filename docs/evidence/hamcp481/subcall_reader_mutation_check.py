"""HAMCP-501 W4: guard mutants of the gateway's native search page readers.

The guarded code is ``UpstreamReadGateway.search_subcall_reader``, its page
argument and page result checks, the ``search_page`` branches of
``UpstreamReadGateway.execute`` (``providers/upstream_read_gateway.py``) and
``bucket_withheld_names`` (``providers/upstream_reads_9_2_keys.py``). Each
mutant is one textual edit of one module, served by an in-memory import hook
(the files on disk are not changed). The gateway is imported by most of the
package and the test support, so each mutant runs in a fresh interpreter,
which imports the mutated module through the hook and then runs
``tests/test_hamcp501_search_subcall_readers.py``; the suite must fail at
least one test. The mutants remove or weaken a guard: argument validation,
an authority check, the execute precondition, the D1 capture, the D2
sanitizer rule, the D3 cross-check, the page's partial source, a paging or
bucket rule, the factory's route rules, audit attribution, child request
inheritance, the keys helper's bucket rule, or (HAMCP-501 W6 S1, an
architect decision) the page-size ladder: the ladder limit and aligned
offset, the page's own size, the too-large flag the size bound alone sets,
and the ``page_too_large`` signal, the scan's only step-down trigger. The
per-type size's mutants (argument_size_one_type and result_size_one_type)
went with the per-type size.

HAMCP-447 (2.11.0 Lane B2): dashboards are searched natively, so the keys
helper admits four buckets; keys_bucket_unchecked and
keys_entity_buckets_refused are re-anchored to them.
missing_authorizer_allowed is re-anchored to the page read's indented check:
HAMCP-510 added the same check to the fixed reads.

The suite's AST reachability tests parse the files on disk, so a mutant does
not reach them; every mutant in ``MUTANTS`` is killed by a behavior test.
``EQUIVALENT`` holds the edit that changes no observable outcome, with the
reason; it is run and reported, not required to fail.

Usage, from the repository root::

    python -I docs/evidence/hamcp481/subcall_reader_mutation_check.py

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
PROVIDERS = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/providers"
PACKAGE = "ha_mcp_engineering.providers"
SUITE = "tests.test_hamcp501_search_subcall_readers"
MODULES = ("upstream_read_gateway", "upstream_reads_9_2_keys")
SOURCES = {name: (PROVIDERS / f"{name}.py").read_text(encoding="utf-8") for name in MODULES}
GATEWAY, KEYS = MODULES

# name -> (module, original text, mutated text)
MUTANTS = {
    # Page arguments: exactly ``search.subcalls.page_arguments``.
    "page_arguments_unmatched": (
        GATEWAY, "    if set(arguments) != set(expected) or arguments != expected:\n        return None\n", ""),
    "blank_query_allowed": (
        GATEWAY, "type(query) is not str or not query.strip() or", "type(query) is not str or"),
    "exact_match_not_bool": (GATEWAY, " or type(exact_match) is not bool\n", "\n"),
    "offset_unbounded": (
        GATEWAY, "or type(offset) is not int or not 0 <= offset < SEARCH_SUBCALL_MAX_ROWS",
        "or type(offset) is not int"),
    "argument_types_unchecked": (
        GATEWAY, "    if any(type(arguments[key]) is not type(value) for key, value in expected.items()):\n"
                 "        return None\n", ""),
    # Page results: D3, the paging rules and the other buckets.
    "d3_flat_has_more_unchecked": (GATEWAY, "        or has_more is not flat_has_more\n", ""),
    "rows_unbounded": (
        GATEWAY, "type(rows) is not list or len(rows) > size", "type(rows) is not list"),
    "partial_unchecked": (GATEWAY, "        or type(partial) is not bool\n", ""),
    "partial_from_metadata": (
        GATEWAY, '    partial = data.get("partial")\n',
        '    partial = result.get("metadata", {}).get("completeness") == "partial"\n'),
    "total_unchecked": (GATEWAY, "        or type(total) is not int or total < 0\n", ""),
    "other_bucket_unchecked": (
        GATEWAY, "if bucket != capture.bucket and data.get(bucket, []) != []:", "if False:"),
    "paging_unchecked": (GATEWAY, "    if not consistent:\n        raise SearchSubcallPageFailure", "    if False:\n        raise SearchSubcallPageFailure"),
    "short_page_claims_more": (
        GATEWAY, "len(rows) == size and offset + len(rows) < total",
        "offset + len(rows) < total"),
    "last_page_short_of_total": (
        GATEWAY, "consistent = offset + len(rows) == total or (not rows and offset >= total)",
        "consistent = True"),
    "failure_category_dropped": (
        GATEWAY, 'category if type(category) is str else "invalid_response"', '"invalid_response"'),
    "capture_unchecked": (
        GATEWAY, "type(data) is not dict or type(capture.withheld_names) is not frozenset\n",
        "type(data) is not dict\n"),
    # The factory.
    "duplicate_route_allowed": (
        GATEWAY, '                if route.entry.upstream_name == "ha_search"\n'
                 '                and route.entry.classification == "automatic_read"\n            ]\n'
                 "            if len(routes) != 1:\n",
        '                if route.entry.upstream_name == "ha_search"\n'
        '                and route.entry.classification == "automatic_read"\n            ]\n'
        "            if not routes:\n"),
    "factory_any_release": (
        GATEWAY, "        if _reads(route.entry) is not reads_9_2 or not reads_9_2.is_adapter(route.entry):\n"
                 "            return None\n\n        current = self._bounded_route_current",
        "\n        current = self._bounded_route_current"),
    # The page reader: authority, child request and audit.
    "stale_route_unchecked": (
        GATEWAY, '                if not current():\n                    raise SearchSubcallPageFailure("provider_authority_changed")\n'
                 "                parent = current_telemetry()",
        "                parent = current_telemetry()"),
    "pre_dispatch_authority_unchecked": (
        GATEWAY, "                    if not telemetry.authorize_core_dispatch():\n"
                 '                        raise SearchSubcallPageFailure("provider_authority_changed")\n'
                 "                    capture = ",
        "                    capture = "),
    "post_dispatch_authority_unchecked": (
        GATEWAY, "                    if not current() or not telemetry.authorize_core_dispatch():\n"
                 '                        raise SearchSubcallPageFailure("provider_authority_changed")\n'
                 "                    page = ",
        "                    page = "),
    "parent_authority_missing_allowed": (
        GATEWAY, "                if parent is None or parent.core_dispatch_authorizer is None:\n",
        "                if False:\n"),
    "missing_authorizer_allowed": (
        GATEWAY, "                if parent is None or parent.core_dispatch_authorizer is None:\n",
        "                if parent is None:\n"),
    "caller_not_inherited": (
        GATEWAY, '                telemetry.tool_name = "search_subcall_page_read"\n'
                 "                telemetry.caller_id = parent.caller_id\n",
        '                telemetry.tool_name = "search_subcall_page_read"\n'),
    "authorizer_not_inherited": (
        GATEWAY, "                telemetry.core_dispatch_authorizer = parent.core_dispatch_authorizer\n"
                 '                telemetry.audit_context["parent_request_id"] = parent.request_id\n'
                 '                status, category = "failed_or_cancelled", None\n',
        '                telemetry.audit_context["parent_request_id"] = parent.request_id\n'
        '                status, category = "failed_or_cancelled", None\n'),
    "child_parent_dropped": (
        GATEWAY, '                telemetry.audit_context["parent_request_id"] = parent.request_id\n'
                 '                status, category = "failed_or_cancelled", None\n',
        '                status, category = "failed_or_cancelled", None\n'),
    "child_unnamed": (GATEWAY, 'telemetry.tool_name = "search_subcall_page_read"\n', "pass\n"),
    "audit_parent_dropped": (
        GATEWAY, '"parent_request_id": parent.request_id,\n'
                 '                                "provider": PROVIDER_ID, "upstream_tool": "ha_search",',
        '"parent_request_id": None,\n'
        '                                "provider": PROVIDER_ID, "upstream_tool": "ha_search",'),
    "audit_status_fixed": (GATEWAY, '                    status = "success"\n', "                    pass\n"),
    "audit_dispatch_count_fixed": (
        GATEWAY, '"failure_category": category,\n'
                 '                                "provider_dispatch_count": telemetry.provider_dispatch_count,',
        '"failure_category": category,\n'
        '                                "provider_dispatch_count": 1,'),
    "audit_category_dropped": (
        GATEWAY, "                except SearchSubcallPageFailure as failure:\n                    category = failure.category\n",
        "                except SearchSubcallPageFailure as failure:\n                    category = None\n"),
    # execute(): the precondition, parent authority, D1 and D2.
    "execute_precondition_removed": (
        GATEWAY, "                type(search_page) is not _SearchPageCapture\n"
                 "                or require_unmodified_script\n"
                 '                or policy_entry.upstream_name != "ha_search"\n'
                 "                or _reads(policy_entry) is not reads_9_2\n"
                 "                or not reads_9_2.is_adapter(policy_entry)\n",
        "                False\n"),
    "execute_capture_type_unchecked": (
        GATEWAY, "                type(search_page) is not _SearchPageCapture\n                or require_unmodified_script\n",
        "                require_unmodified_script\n"),
    "execute_any_tool": (GATEWAY, '                or policy_entry.upstream_name != "ha_search"\n', ""),
    "parent_authority_not_required": (
        GATEWAY, "                    require_unmodified_script or search_page is not None\n",
        "                    require_unmodified_script\n"),
    "withheld_names_not_captured": (
        GATEWAY, "keys_9_2.bucket_withheld_names(\n                            payload, search_page.bucket\n                        )",
        "frozenset()"),
    "d2_unchecked": (
        GATEWAY, "                if search_page is not None and (\n"
                 "                    sanitation.redaction_applied or sanitation.truncated_field_count\n"
                 "                ):\n",
        "                if False:\n"),
    "d2_truncation_unchecked": (
        GATEWAY, "sanitation.redaction_applied or sanitation.truncated_field_count\n",
        "sanitation.redaction_applied\n"),
    "d2_redaction_unchecked": (
        GATEWAY, "sanitation.redaction_applied or sanitation.truncated_field_count\n",
        "sanitation.truncated_field_count\n"),
    # bucket_withheld_names.
    "keys_bucket_unchecked": (
        KEYS, 'if type(payload) is not dict or bucket not in (\n'
              '        "automations", "scripts", "scenes", "helpers",\n    ):',
        "if type(payload) is not dict:"),
    # HAMCP-501 W6 S1: the entity-type buckets are admitted.
    "keys_entity_buckets_refused": (
        KEYS, '"automations", "scripts", "scenes", "helpers",', '"helpers",'),
    "keys_records_unchecked": (
        KEYS, "    records = payload.get(bucket)\n    if type(records) is not list:\n        raise ValueError",
        "    records = payload.get(bucket) or []\n    if False:\n        raise ValueError"),
    "keys_admitted_counted": (
        KEYS, "for name in _names(records, row, set()) - row.admitted - row.drops\n",
        "for name in _names(records, row, set()) - row.drops\n"),
    # HAMCP-501 W6 S1 (an architect decision): the page-size ladder.
    "argument_size_any": (
        GATEWAY, "or type(size) is not int or size not in SEARCH_SUBCALL_PAGE_SIZE_LADDER",
        "or type(size) is not int or size < 1"),
    "result_size_first": (
        GATEWAY, 'page_arguments["offset"], page_arguments["limit"]\n',
        'page_arguments["offset"], SEARCH_SUBCALL_PAGE_SIZE_LADDER[0]\n'),
    "audit_limit_dropped": (
        GATEWAY, '                                "limit": page_arguments["limit"],\n',
        '                                "limit": None,\n'),
    "too_large_flag_ignored": (
        GATEWAY, '        if capture.too_large is True and category == "response_too_large":\n'
        "            raise SearchSubcallPageTooLarge()\n", ""),
    "too_large_without_flag": (
        GATEWAY, 'capture.too_large is True and category == "response_too_large"',
        'category == "response_too_large"'),
    "too_large_any_category": (
        GATEWAY, 'capture.too_large is True and category == "response_too_large"',
        "capture.too_large is True"),
    "too_large_not_step_down": (
        GATEWAY, "class SearchSubcallPageTooLarge(SearchSubcallPageFailure, SearchPageTooLarge):",
        "class SearchSubcallPageTooLarge(SearchSubcallPageFailure):"),
    "too_large_category_dropped": (
        GATEWAY, '{"provider_authority_changed", "page_too_large"}', '{"provider_authority_changed"}'),
    "flagged_success_accepted": (
        GATEWAY, "\n        or capture.too_large is not False\n        or type(capture.scrub)",
        "\n        or type(capture.scrub)"),
    "preset_flag_accepted": (
        GATEWAY, "                search_page.too_large is not False\n                or search_page.scrub is not None",
        "                search_page.scrub is not None"),
    "size_flag_unset": (
        GATEWAY, "                        # HAMCP-501 W6 S1: the size bound alone refused the page.\n"
        "                        search_page.too_large = True\n", "                        pass\n"),
    "render_flag_unset": (
        GATEWAY, "                if truncated:\n                    search_page.too_large = True\n",
        "                if truncated:\n"),
    "render_cut_served": (
        GATEWAY, "                if truncated:\n                    search_page.too_large = True\n"
        '                    raise _GatewayFailure("response_too_large", dispatched=True)\n'
        "                return rendered\n", "                return rendered\n"),
    "render_branch_skipped": (
        GATEWAY, "            if search_page is not None:\n"
        "                # HAMCP-501 W6 S1: a sub-call page is rendered",
        "            if False:\n                # HAMCP-501 W6 S1: a sub-call page is rendered"),
}

EQUIVALENT = {
    # ``page_arguments(..., offset // size, size)`` carries an offset that is
    # a multiple of the ladder size, so the equality check that follows
    # refuses any unaligned offset as well.
    "offset_unaligned": (GATEWAY, "\n        or offset % size\n", "\n"),
}


class _Mutant(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves one module from mutated source text; writes no bytecode."""

    def __init__(self, module: str, source: str) -> None:
        self.fullname = f"{PACKAGE}.{module}"
        self.path = str(PROVIDERS / f"{module}.py")
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
    """Run the suite in this interpreter, ``name``'s module mutated."""

    sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]
    sys.dont_write_bytecode = True
    finder = None
    if name is not None:
        target, original, mutated = {**MUTANTS, **EQUIVALENT}[name]
        finder = _Mutant(target, SOURCES[target].replace(original, mutated))
        sys.meta_path.insert(0, finder)
    suite = importlib.import_module(SUITE)
    if finder is not None:
        module = sys.modules[finder.fullname]
        if module.__spec__.loader is not finder:
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
    baseline, run = failures_for(None)
    print(f"baseline: failing tests={baseline} of {run}")
    if baseline:
        return 2
    for name, (target, original, _) in {**MUTANTS, **EQUIVALENT}.items():
        if SOURCES[target].count(original) != 1:
            print(f"{name}: anchor not found once", file=sys.stderr)
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
