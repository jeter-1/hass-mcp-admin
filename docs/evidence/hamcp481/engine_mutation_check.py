"""HAMCP-501 W3 and W6 S1: guard mutants of the native search engine modules.

The modules are ``search/engine.py``, ``entities.py``, ``configs.py``,
``subcalls.py`` and ``reply.py``. Each mutant is one textual edit of one
module, served by an in-memory import hook (the files on disk are not
changed). For each mutant the five modules and
``tests/test_hamcp481_native_search_engine.py`` are imported afresh, so every
module and the suite see the same classes, and the suite must fail at least
one test. The mutants remove or weaken a guard: an argument check, a refusal,
a fail-closed source rule, a hidden-entity rule, a bound, a partial-reason
disclosure, a projection, a fit rule, the final sanitizer check, the
exception rule, the labels read rule or a sub-call row rule.

The suite's AST reachability tests parse the files on disk, so a mutant does
not reach them; every mutant in ``MUTANTS`` is killed by a behavior test.
``EQUIVALENT`` holds an edit that changes no observable outcome, with the
reason; it is run and reported, not required to fail. It is empty: its one
edit, the configuration-body time budget, went with the body reads.

HAMCP-501 W6 S1 = b+ (approved by Josh as relayed by the architect,
2026-11-26): native search reads no automation, script or scene body, so
the mutants of that code went with it: unavailable_batch_used,
scene_read_exception_swallowed, graph_items_not_last,
body_key_grammar_unchecked, chunk_timeout_unbounded,
chunk_timeout_mislabelled, not_found_body_read_failed, non_dict_body_kept,
scene_owner_unchecked and the equivalent time_budget_unchecked.
row_kind_unchecked is re-anchored to the new row loop, and the S1 guards
of the entity-type sub-call rows have their own mutants. Every type's own
row keys have theirs: entity_row_keys_unchecked became row_keys_any_type.
The page-size ladder (an architect decision, in place of a per-type page
size) has its own: the ladder sizes, the page arguments, the step-down
trigger, the spans, the alignment, the call bound and the full-page rule.
The per-type size's mutants (page_rows_any_type, page_sizes_not_derived and
page_arguments_one_size) went with it.

HAMCP-522 (2.11.0 Lane B1): the area modes draw their candidates from the
state rows, and ``state_rows`` keeps no set of malformed ids. So
hidden_area_member_listed drops ``kept()`` from the area candidates,
malformed_row_listed keeps a malformed state row, and
dropped_state_rows_uncounted counts only the malformed rows.

Usage, from the repository root::

    python -I docs/evidence/hamcp481/engine_mutation_check.py

Exit status 1 when a non-equivalent mutant survives, 2 when the baseline
fails or an anchor is not found exactly once.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

PACKAGE = "ha_mcp_engineering.search"
SUITE = "tests.test_hamcp481_native_search_engine"
SEARCH = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/search"
MODULES = ("reply", "subcalls", "entities", "configs", "engine")
SOURCES = {name: (SEARCH / f"{name}.py").read_text(encoding="utf-8") for name in MODULES}

# name -> (module, original text, mutated text)
MUTANTS = {
    # engine.py: argument grammar and the run rules.
    "flag_not_bool": ("engine", "        return type(value) is bool\n", "        return True\n"),
    "limit_zero": (
        "engine", "return type(value) is int and value >= 1", "return type(value) is int and value >= 0"),
    "offset_negative": ("engine", "return type(value) is int and value >= 0", "return type(value) is int"),
    "per_domain_limit_any": ("engine", "return value is None or type(value) is int", "return True"),
    "budget_range_unchecked": (
        "engine", "math.isfinite(value)\n                             and _BUDGET_RANGE[0] <= value <= _BUDGET_RANGE[1])",
        "math.isfinite(value))"),
    "json_list_split_as_csv": (
        "engine", 'if not value.strip().startswith("[") and allow_csv:', "if allow_csv:"),
    "unknown_argument_allowed": (
        "engine", "    if not all(type(name) is str and name in DEFAULTS for name in arguments):", "    if False:"),
    "empty_search_types_allowed": (
        "engine", '        if not search_types:\n            raise SearchError("empty_search_types", parameter="search_types")',
        "        pass"),
    "unknown_search_type_allowed": (
        "engine", "if not all(item in configs.CONFIG_TYPES for item in search_types):", "if False:"),
    "unknown_result_field_allowed": (
        "engine", "if not all(item in entities.RESULT_FIELDS for item in result_fields):", "if False:"),
    "entity_runs_on_explicit_types": (
        "engine", "entity_runs = bool(query_text or intent) and not explicit",
        "entity_runs = bool(query_text or intent)"),
    "config_runs_on_entity_intent": (
        "engine", "config_runs = bool(query_text) and (explicit or not intent)", "config_runs = bool(query_text)"),
    "malformed_state_row_unreported": (
        "engine", 'reasons.add("source_unavailable", None, "malformed_state_row", states.count)', "pass"),
    "authority_source_mislabelled": (
        "engine", 'source = "core_authority" if exc.reason == "authority_unavailable" else "core_reads"',
        'source = "core_reads"'),
    "by_domain_withheld_uncounted": ("engine", 'names.add("by_domain")', "pass"),
    "alias_read_dropped": ("engine", 'names.add("entity_registry_entries")', "pass"),
    # W3-H2 (Cursor r1): an exception other than CoreReadError propagates.
    "unexpected_error_succeeds": (
        "engine", '        raise SearchError("source_unavailable", source=source) from None\n',
        '        raise SearchError("source_unavailable", source=source) from None\n'
        '    except Exception:\n        return {"success": True}\n'),
    "unexpected_error_mapped": (
        "engine", '        raise SearchError("source_unavailable", source=source) from None\n',
        '        raise SearchError("source_unavailable", source=source) from None\n'
        '    except Exception:\n        raise SearchError("source_unavailable", source="core_reads") from None\n'),
    # entities.py: fail-closed sources, hidden entities and projection.
    "inventory_bound_not_refused": ("entities", 'if result.reason == "inventory_bound_exceeded":', "if False:"),
    "dropped_registry_rows_accepted": (
        "entities", "if result.status not in (c.COMPLETE, c.PARTIAL) or result.dropped:",
        "if result.status not in (c.COMPLETE, c.PARTIAL):"),
    "invalid_registry_field_accepted": (
        "entities", "return all(row.get(name) is not INVALID for row in rows.values() for name in fields)",
        "return True"),
    "invalid_state_row_kept": (
        "entities", "if state is INVALID or attributes is INVALID or type(name) is not str:",
        "if type(name) is not str:"),
    "dropped_state_rows_uncounted": ("entities", "result.dropped + malformed)", "malformed)"),
    "member_id_grammar_unchecked": (
        "entities", "if all(type(item) is str and _MEMBER_ID.fullmatch(item) for item in raw):",
        "if all(type(item) is str for item in raw):"),
    "hidden_member_not_redacted": (
        "entities", "redacted = members is not None and bool(denied.intersection(members))", "redacted = False"),
    "alias_read_identity_unchecked": (
        "entities", 'if type(result) is not ReadResult or result.read != "entity_registry_entries":',
        "if type(result) is not ReadResult:"),
    "alias_gap_unreported": (
        "entities", 'reasons.add("source_unavailable", "entity", "entity_aliases", len(entity_ids) - len(known))',
        "pass"),
    "include_hidden_ignored": (
        "entities", "denied = frozenset() if request.include_hidden else hidden", "denied = hidden"),
    # HAMCP-501 W6b: one ``kept()`` filter serves the query and listing modes.
    "hidden_scored": (
        "entities", "        return (entity_id not in denied\n                and (domain",
        "        return (True\n                and (domain"),
    "hidden_area_member_listed": (
        "entities", "if kept(entity_id, row.state) and entity_id in registry", "if entity_id in registry"),
    "malformed_row_listed": (
        "entities", "            malformed += 1\n            continue\n", "            malformed += 1\n"),
    "listing_hidden_unpenalized": (
        "entities", "row.state, scoring.apply_hidden_penalty(100, entity_id in hidden),", "row.state, 100,"),
    "query_hidden_unpenalized": (
        "entities", "scoring.apply_hidden_penalty(tier, entity_id in hidden),", "tier,"),
    "domain_filter_dropped": (
        "entities", "                and (domain is None or scoring.entity_domain(entity_id) == domain)\n", ""),
    "result_fields_unprojected": (
        "entities", "record = {key: value for key, value in record.items() if key in keep}",
        "record = dict(record)"),
    # W3-M1 (Cursor r1): labels are read only for a labels request read valid.
    "labels_read_unrequested": (
        "entities", 'if "labels" in self.requested and "labels" not in self.null:', "if True:"),
    "labels_read_when_null": (
        "entities", 'if "labels" in self.requested and "labels" not in self.null:',
        'if "labels" in self.requested:'),
    # configs.py: the graph, the storage helpers and the sub-calls.
    "graph_query_unchecked": ("configs", "if STRICT_ENTITY_ID.fullmatch(query) is not None:", "if True:"),
    "graph_invalid_ids_used": (
        "configs", "and all(ids is not INVALID for ids in item.value.values())):", "):"),
    "graph_id_prefix_unchecked": (
        "configs", 'STRICT_ENTITY_ID.fullmatch(entity_id) and entity_id.startswith(f"{search_type}.")',
        "STRICT_ENTITY_ID.fullmatch(entity_id)"),
    "helper_invalid_name_used": ("configs", "if name is INVALID or original_name is INVALID:", "if False:"),
    "helper_dropped_rows_unreported": (
        "configs", 'reasons.add("not_searched", "helper", "read_failed", result.dropped)', "pass"),
    "helper_invalid_value_used": ("configs", "if any(value is INVALID for value in helper.values()):", "if False:"),
    "subcall_not_admitted_unreported": ("configs", "if outcome.status == subcalls.NOT_ADMITTED:", "if False:"),
    "upstream_partial_unreported": ("configs", "if outcome.upstream_partial:", "if False:"),
    # HAMCP-501 W6 S1: the per-type incomplete code and the entity-row ids.
    "subcall_incomplete_unreported": ("configs", "    elif outcome.status == subcalls.INCOMPLETE:", "    elif False:"),
    "script_id_unfilled": (
        "configs", '    if search_type == "script" and record.get("script_id") is None:\n'
                   '        record["script_id"] = slug\n    elif', "    if"),
    "scene_id_unfilled": (
        "configs", '    elif search_type == "scene" and record.get("scene_id") is None:\n', "    elif False:\n"),
    "scene_id_registry_unused": (
        "configs", '        record["scene_id"] = slug_map.get(slug) or slug\n    # The component',
        '        record["scene_id"] = slug\n    # The component'),
    "references_default_dropped": ("configs", '    record.setdefault("match_in_references", False)\n', ""),
    "dashboard_scan_unheld": ("configs", "needed=request.offset + request.limit", "needed=1"),
    "dashboard_total_uncorrected": (
        "configs", "total = len(tagged) - dashboard_held + dashboard_total", "total = len(tagged)"),
    "config_order_reversed": (
        "configs", 'tagged.sort(key=lambda item: -item[1]["score"])', 'tagged.sort(key=lambda item: item[1]["score"])'),
    # subcalls.py: the page and row shapes and the scan stops.
    "row_extra_key_accepted": (
        "subcalls", "if type(row) is not dict or not set(row) <= _ROW_KEYS[search_type]:",
        "if type(row) is not dict:"),
    # HAMCP-501 W6 S1: every type's row keys are its own.
    "row_keys_any_type": (
        "subcalls", "not set(row) <= _ROW_KEYS[search_type]:", 'not set(row) <= set(_ROW_KINDS) | {"score"}:'),
    "helper_row_keys_widened": (
        "subcalls", '"helper": frozenset({"entity_id", "helper_type", "name",',
        '"helper": frozenset({"entity_id", "helper_type", "name", "friendly_name",'),
    "dashboard_row_keys_widened": (
        "subcalls", '"dashboard": frozenset({"score",', '"dashboard": frozenset({"score", "name",'),
    "row_score_unchecked": ("subcalls", "    if type(score) is not int:\n        return False\n", ""),
    "row_kind_unchecked": ("subcalls", "        if type(value) is not _ROW_KINDS[key]:", "        if False:"),
    "helper_type_optional": ("subcalls", 'return search_type != "helper" or "helper_type" in row', "return True"),
    # HAMCP-501 W6 S1: the entity-type rows and the repeated-row stop.
    "nullable_widened": ("subcalls", "(value is None and key in _NULLABLE)", "value is None"),
    "entity_rows_unchecked": (
        "subcalls", "    if search_type in ENTITY_TYPES:\n        entity_id", "    if False:\n        entity_id"),
    "entity_id_grammar_unchecked": (
        "subcalls", "                and STRICT_ENTITY_ID.fullmatch(entity_id) is not None\n", ""),
    "entity_id_prefix_unchecked": (
        "subcalls", '\n                and entity_id.startswith(f"{search_type}."))', ")"),
    "page_repeat_allowed": (
        "subcalls", "if len(set(page_ids)) != len(page_ids) or not seen.isdisjoint(page_ids):",
        "if not seen.isdisjoint(page_ids):"),
    "scan_repeat_allowed": (
        "subcalls", "if len(set(page_ids)) != len(page_ids) or not seen.isdisjoint(page_ids):",
        "if len(set(page_ids)) != len(page_ids):"),
    "seen_not_updated": ("subcalls", "        seen.update(page_ids)\n", ""),
    "page_total_negative": ("subcalls", "        and page.config_total_matches >= 0\n", ""),
    "page_size_unbounded": ("subcalls", "        and len(page.rows) <= size\n", ""),
    "withheld_names_untyped": ("subcalls", "        and all(type(name) is str for name in page.withheld_names)\n", ""),
    "empty_page_with_more": (
        "subcalls", "        and (len(page.rows) == size or not page.has_more)\n", ""),
    # HAMCP-501 W6 S1: a page that claims more rows is full.
    "short_page_with_more": (
        "subcalls", "len(page.rows) == size or not page.has_more", "page.rows or not page.has_more"),
    "reader_none_called": (
        "subcalls", "    if reader is None:\n        return SubcallOutcome(search_type, NOT_ADMITTED)\n", ""),
    "row_bound_unchecked": (
        "subcalls", "if remaining <= 0 or scanned >= MAX_SCAN_ROWS or calls", "if remaining <= 0 or calls"),
    "scan_deadline_unchecked": ("subcalls", "if remaining <= 0 or scanned", "if scanned"),
    # HAMCP-501 W6 S1 (an architect decision): the page-size ladder.
    "ladder_misaligned": ("subcalls", "PAGE_SIZE_LADDER = (150, 30, 6, 1)", "PAGE_SIZE_LADDER = (150, 40, 6, 1)"),
    "page_arguments_any_size": ("subcalls", "or size not in PAGE_SIZE_LADDER):", "or size < 1):"),
    "page_offset_not_scaled": ("subcalls", '"offset": page * size,', '"offset": page,'),
    "page_limit_fixed": ("subcalls", '"limit": size,', '"limit": PAGE_SIZE_LADDER[0],'),
    "call_bound_unchecked": ("subcalls", " or calls >= MAX_PAGE_CALLS:", ":"),
    "calls_not_counted": ("subcalls", "        calls += 1\n", ""),
    "page_index_unaligned": (
        "subcalls", "exact_match, offset // size, size)", "exact_match, offset // PAGE_SIZE_LADDER[0], size)"),
    "any_failure_steps_down": ("subcalls", "        except PageTooLarge:\n", "        except Exception:\n"),
    "step_down_ignored": ("subcalls", "if level == len(PAGE_SIZE_LADDER) - 1:", "if True:"),
    "oversized_row_skipped": (
        "subcalls", "                # One row too large for a page of its own.\n"
        "                return finished(INCOMPLETE)\n",
        "                offset += size\n                continue\n"),
    "step_down_level_kept": ("subcalls", "            level += 1\n", ""),
    "span_end_wrong": (
        "subcalls", "span_ends.append(offset + size)", "span_ends.append(offset + PAGE_SIZE_LADDER[level + 1])"),
    "span_not_popped": ("subcalls", "            span_ends.pop()\n            level -= 1\n",
                        "            span_ends.pop()\n"),
    "span_pop_once": (
        "subcalls", "while span_ends and offset == span_ends[-1]:", "if span_ends and offset == span_ends[-1]:"),
    "offset_advance_fixed": ("subcalls", "        offset += size\n", "        offset += PAGE_SIZE_LADDER[0]\n"),
    "snapshot_move_ignored": ("subcalls", "elif page.config_total_matches != first_total:", "elif False:"),
    "storage_helpers_kept": (
        "subcalls", 'if row["helper_type"] not in STORAGE_HELPER_TYPES)', ")"),
    "dashboard_scan_not_stopped": (
        "subcalls", 'if search_type == "dashboard" and len(rows) >= needed:', "if False:"),
    # reply.py: sanitization, the fit, paging and projection.
    "sanitizer_failure_ignored": (
        "reply", '        if result.failed_closed:\n            raise SearchError("sanitization_failed")\n', ""),
    "error_parameter_echoed": ("reply", "        if parameter in self.PARAMETERS:", "        if parameter is not None:"),
    "error_source_unchecked": (
        "reply", "        if type(source) is str and source.isidentifier():", "        if source is not None:"),
    "error_observed_unchecked": (
        "reply", "        if type(observed) is int and observed >= 0:", "        if observed is not None:"),
    "error_reason_unchecked": (
        "reply", '        if reason not in self.CODES:\n            reason = "sanitization_failed"\n', ""),
    "zero_count_reason_kept": ("reply", "        if count == 0:\n            return\n", ""),
    "record_lists_uncapped": (
        "reply", "            while size > RECORD_BUDGET_BYTES and type(items) is list and items:",
        "            while False:"),
    "more_matches_unpaged": ("reply", "    elif surface.page_end < surface.total:", "    elif False:"),
    "paging_unreported": (
        "reply", 'reasons.add("paged", surface.name, code, max(surface.total - next_offset, 1))', "pass"),
    "config_bodies_uncounted": ("reply", "            if item.body_read and spec.include_config:", "            if False:"),
    "echo_truncation_unreported": (
        "reply", 'echo_reasons.add("truncated", None, code, result.truncated_field_count)', "pass"),
    "unkept_bucket_emitted": ("reply", "            if keep is not None and bucket not in keep:", "            if False:"),
    "fit_unchecked": ("reply", "            if emitted and used + cost + FIT_MARGIN_BYTES > budget:", "            if False:"),
    "first_record_not_admitted": (
        "reply", "emitted = any(other.record is not None for other in kept[surface.name])", "emitted = True"),
    "fields_unprojected": (
        "reply", "        reply = {key: value for key, value in reply.items() if key in keep}\n", "        pass\n"),
    # W3-H1 (Cursor r1): the final pass over the assembled reply changes nothing.
    "final_check_removed": (
        "reply", "    check = clean(reply, None)\n"
        "    if check.redacted_field_count or check.truncated_field_count or check.value != reply:\n"
        '        raise SearchError("sanitization_failed")\n    return check.value\n',
        "    return reply\n"),
    "final_redaction_ignored": (
        "reply", "if check.redacted_field_count or check.truncated_field_count or check.value != reply:",
        "if check.truncated_field_count or check.value != reply:"),
    "final_truncation_ignored": (
        "reply", "if check.redacted_field_count or check.truncated_field_count or check.value != reply:",
        "if check.redacted_field_count or check.value != reply:"),
    "final_change_ignored": (
        "reply", "if check.redacted_field_count or check.truncated_field_count or check.value != reply:",
        "if check.redacted_field_count or check.truncated_field_count:"),
}
# Run and reported, but not required to fail a test. Empty since HAMCP-501
# W6 S1 (see the module docstring).
EQUIVALENT: dict = {}


class _Mutant(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves one module from mutated source text; writes no bytecode."""

    def __init__(self, module: str, source: str) -> None:
        self.fullname = f"{PACKAGE}.{module}"
        self.path = str(SEARCH / f"{module}.py")
        self.source = source

    def find_spec(self, fullname, path, target=None):
        if fullname != self.fullname:
            return None
        return importlib.util.spec_from_file_location(fullname, self.path, loader=self)

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        exec(compile(self.source, self.path, "exec"), module.__dict__)


def fresh_suite(target: str | None, source: str | None):
    """Import the five modules and the suite afresh, ``target`` mutated."""

    package = importlib.import_module(PACKAGE)
    for name in MODULES:
        sys.modules.pop(f"{PACKAGE}.{name}", None)
        if name in vars(package):
            delattr(package, name)
    sys.modules.pop(SUITE, None)
    tests = sys.modules.get("tests")
    if tests is not None and SUITE.rpartition(".")[2] in vars(tests):
        delattr(tests, SUITE.rpartition(".")[2])
    finder = _Mutant(target, source) if target is not None else None
    path = list(sys.path)
    if finder is not None:
        sys.meta_path.insert(0, finder)
    try:
        suite = importlib.import_module(SUITE)
    finally:
        sys.path[:] = path
        if finder is not None:
            sys.meta_path.remove(finder)
    if finder is not None:
        module = sys.modules[finder.fullname]
        if module.__spec__.loader is not finder or getattr(suite, target) is not module:
            raise RuntimeError("mutant not loaded")
    return suite


def failures_for(target: str | None, mutated: str | None) -> int:
    suite = fresh_suite(target, mutated)
    result = unittest.TestResult()
    unittest.TestLoader().loadTestsFromModule(suite).run(result)
    return len(result.failures) + len(result.errors)


def main() -> int:
    baseline = failures_for(None, None)
    print(f"baseline: failing tests={baseline}")
    if baseline:
        return 2
    survivors = []
    for group, mutants in (("mutant", MUTANTS), ("equivalent", EQUIVALENT)):
        for name, (target, original, mutated) in mutants.items():
            if SOURCES[target].count(original) != 1:
                print(f"{name}: anchor not found once", file=sys.stderr)
                return 2
            started = time.monotonic()
            count = failures_for(target, SOURCES[target].replace(original, mutated))
            print(f"{group} {name}: failing tests={count} ({time.monotonic() - started:.1f}s)")
            if group == "mutant" and count == 0:
                survivors.append(name)
    print(f"mutants: {len(MUTANTS)}, equivalent: {len(EQUIVALENT)}")
    print("survivors: " + (", ".join(survivors) or "none"))
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
