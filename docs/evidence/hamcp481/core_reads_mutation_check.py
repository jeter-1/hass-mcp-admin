"""HAMCP-481 W2: guard mutants of ``search/core_reads.py`` and ``search/core_contracts.py``.

Each mutant is one textual edit of a module, compiled in memory (the files on
disk are not changed). ``tests/test_hamcp481_core_reads.py`` runs against each
one, and the mutant must fail at least one test. The mutants remove or weaken
a guard: an authority check, an argument grammar, a bound, a projection rule
or a closed vocabulary.

HAMCP-501 W6 S1 = b+ (approved by Josh as relayed by the architect,
2026-11-26) removed the ``automation/config``, ``script/config`` and scene
configuration reads, and with them the mutants config_extra_keys_silent,
any_error_is_not_found, scene_reply_unchecked, scene_id_unchecked,
scene_404_not_expected, scene_id_widened and rest_scene_any_arity.
batch_argument_unchecked is re-anchored to the split batch-payload check.

Usage, from the repository root::

    python -I docs/evidence/hamcp481/core_reads_mutation_check.py

Exit status 1 when a non-equivalent mutant survives.
"""

from __future__ import annotations

from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

import test_hamcp481_core_reads as suite  # noqa: E402

SEARCH = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/search"
SOURCES = {
    "core_reads": (SEARCH / "core_reads.py").read_text(encoding="utf-8"),
    "core_contracts": (SEARCH / "core_contracts.py").read_text(encoding="utf-8"),
}
ORIGINAL = (suite.c, suite.r)

# name -> (module, original text, mutated text)
MUTANTS = {
    "anonymous_allowed": ("core_reads", 'if current_caller_id() == "anonymous":', "if False:"),
    "generation_unchecked": (
        "core_reads", 'or status.get("generation") is None or observation is None', "or observation is None"),
    "authorizer_not_called": (
        "core_reads", "                or not telemetry.authorize_core_dispatch()):", "                ):"),
    "declared_requirements_widened": (
        "core_reads", "self.requirements = c.requirements_for(self.reads)",
        "self.requirements = c.requirements_for(c.READS)"),
    "no_authority_check_before_dispatch": (
        "core_reads",
        '            raise c.CoreReadError("authority_unavailable")\n        telemetry = current_telemetry()',
        "            pass\n        telemetry = current_telemetry()"),
    "no_authority_check_after_dispatch": (
        "core_reads",
        '            raise c.CoreReadError("authority_unavailable")\n        return raw, reason',
        "            pass\n        return raw, reason"),
    "undeclared_read_allowed": (
        "core_reads", "if read is None or read.shape != shape or name not in self.reads:",
        "if read is None or read.shape != shape:"),
    "wrong_shape_allowed": (
        "core_reads", "if read is None or read.shape != shape or name not in self.reads:",
        "if read is None or name not in self.reads:"),
    "duplicate_reads_allowed": (
        "core_reads", "                or len(set(names)) != len(names)):", "                ):"),
    "no_inventory_bound": ("core_reads", "    if len(raw) > c.MAX_ITEMS:", "    if False:"),
    "inventory_bound_count_dropped": (
        "core_reads", '"inventory_bound_exceeded", observed=len(raw))', '"inventory_bound_exceeded")'),
    "inventory_bound_count_off_by_one": (
        "core_reads", '"inventory_bound_exceeded", observed=len(raw))',
        '"inventory_bound_exceeded", observed=c.MAX_ITEMS)'),
    "observed_not_recorded": (
        "core_reads", '"observed": result.observed,', '"observed": None,'),
    "row_key_unchecked": (
        "core_reads", "        if not _row_key_valid(read, identity):", "        if identity is None:"),
    "duplicate_row_overwrites": ("core_reads", "        if identity in rows:", "        if False:"),
    "kind_unchecked": ("core_reads", "            if not _kind_valid(kind, value):", "            if False:"),
    "bool_is_int": (
        "core_reads", "return value is None or type(value) is int",
        "return value is None or isinstance(value, int)"),
    "every_field_optional": ("core_reads", "                if field_name not in optional:", "                if False:"),
    "missing_field_silent": (
        "core_reads",
        '                    projected[field_name] = INVALID\n                    partial.add("field_drift")\n',
        "                    projected[field_name] = INVALID\n"),
    "populated_but_unusable_is_complete": ("core_reads", "    if raw and not rows:", "    if False:"),
    "unknown_related_type_silent": (
        "core_reads",
        '        if item_type not in c.CORE_RELATED_TYPES:\n            partial.add("field_drift")',
        "        if item_type not in c.CORE_RELATED_TYPES:\n            continue"),
    "related_bucket_unchecked": (
        "core_reads",
        "elif type(items) is list and all(type(item) is str and c.ROW_ID.fullmatch(item) for item in items):",
        "elif type(items) is list:"),
    "aliases_keep_sentinel": (
        "core_reads", "return tuple(item for item in aliases if type(item) is str)", "return tuple(aliases)"),
    "aliases_unchecked": (
        "core_reads",
        "if type(aliases) is not list or not all(item is None or type(item) is str for item in aliases):",
        "if type(aliases) is not list:"),
    "unrequested_entries_silent": (
        "core_reads", '    if not set(raw) <= set(chunk):\n        partial.add("field_drift")\n', ""),
    "missing_entries_silent": (
        "core_reads",
        '        if entity_id not in raw:\n            partial.add("field_drift")\n            continue',
        "        if entity_id not in raw:\n            continue"),
    "bad_entry_silent": (
        "core_reads",
        '            entries[entity_id] = INVALID\n            partial.add("field_drift")',
        "            entries[entity_id] = INVALID"),
    "chunk_failure_silent": ("core_reads", '        if reasons:\n            partial.add("item_failures")\n', ""),
    "entries_bound_doubled": ("core_reads", "len(entity_ids) > c.MAX_ITEMS", "len(entity_ids) > c.MAX_ITEMS * 2"),
    "duplicate_entries_allowed": (
        "core_reads", "                or len(set(entity_ids)) != len(entity_ids)):", "                ):"),
    "batch_bound_widened": (
        "core_reads", "len(items) > c.MAX_BATCH_ITEMS", "len(items) > c.MAX_BATCH_ITEMS + 1"),
    "duplicate_batch_items_allowed": (
        "core_reads", " or len(set(items)) != len(items)):", "):"),
    "batch_length_unchecked": (
        "core_reads", "if type(raw) is not list or len(raw) != len(items):", "if type(raw) is not list:"),
    "batch_all_failed_is_complete": ("core_reads", "        if failed == len(items):", "        if False:"),
    "batch_item_failure_silent": (
        "core_reads", 'partial = {"item_failures"} if failed else set()', "partial = set()"),
    "timeout_misreported": (
        "core_reads", '        except HomeAssistantTimeoutError:\n            reason = "timeout"',
        '        except HomeAssistantTimeoutError:\n            reason = "command_failed"'),
    "failed_read_metered_complete": (
        "core_reads", 'raw, reason, outcome = None, None, "failed"', 'raw, reason, outcome = None, None, "complete"'),
    "unavailable_reason_open": (
        "core_reads", 'reason if reason in c.UNAVAILABLE_REASONS else "source_unavailable"', "reason"),
    "partial_reason_open": (
        "core_reads", '        partial = (partial & c.PARTIAL_REASONS) | {"field_drift"}', "        pass"),
    "batch_argument_unchecked": (
        "core_contracts", '    if read.argument.fullmatch(item) is None:\n        raise CoreReadError("invalid_arguments")\n',
        ""),
    "batch_read_unlisted": (
        "core_contracts", "    if name not in BATCH_READS or type(item) is not str:",
        "    if type(item) is not str:"),
    "entries_chunk_unbounded": (
        "core_contracts", "not 1 <= len(entity_ids) <= GET_ENTRIES_CHUNK", "not 1 <= len(entity_ids)"),
    "entries_ids_unchecked": (
        "core_contracts",
        "or not all(type(item) is str and STRICT_ENTITY_ID.fullmatch(item) for item in entity_ids)",
        "or not all(type(item) is str for item in entity_ids)"),
    "error_reason_open": (
        "core_contracts", '        if reason not in self.REASONS:\n            reason = "authority_unavailable"\n', ""),
    "device_requirement_downgraded": (
        "core_contracts", '"config/device_registry/list", DEVICE_REGISTRY, True,',
        '"config/device_registry/list", NON_DEVICE_REGISTRY, True,'),
    "row_id_allows_control": (
        "core_contracts", 'ROW_ID = re.compile(r"[^\\x00-\\x1f\\x7f]{1,255}\\Z")',
        'ROW_ID = re.compile(r"[\\s\\S]{1,255}\\Z")'),
    "rest_states_any_arity": (
        "core_reads", '    if name == "states" and not values:', '    if name == "states":'),
    "has_entity_name_required": (
        "core_contracts", 'frozenset({"has_entity_name"})', "frozenset()"),
}
# Equivalent: ``asyncio.CancelledError`` is a ``BaseException`` (Python 3.8+),
# so ``except Exception`` never catches it; the explicit re-raise is defensive.
EQUIVALENT = {
    "no_explicit_cancel_reraise": (
        "core_reads", "        except asyncio.CancelledError:\n            raise\n", ""),
}


def load(module_name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(f"ha_mcp_engineering.search.hamcp481_{module_name}_mutant")
    module.__package__ = "ha_mcp_engineering.search"
    sys.modules[module.__name__] = module
    exec(compile(source, module.__name__, "exec"), module.__dict__)
    return module


def failures_for(target: str, mutated: str) -> int:
    contracts = load("core_contracts", mutated) if target == "core_contracts" else ORIGINAL[0]
    reads = load("core_reads", mutated if target == "core_reads" else SOURCES["core_reads"])
    reads.c = contracts
    suite.c, suite.r = contracts, reads
    suite.CoreReadSession, suite.INVALID = reads.CoreReadSession, reads.INVALID
    try:
        result = unittest.TestResult()
        unittest.TestLoader().loadTestsFromModule(suite).run(result)
        return len(result.failures) + len(result.errors)
    finally:
        suite.c, suite.r = ORIGINAL
        suite.CoreReadSession, suite.INVALID = ORIGINAL[1].CoreReadSession, ORIGINAL[1].INVALID


def main() -> int:
    baseline = failures_for("core_reads", SOURCES["core_reads"])
    print(f"baseline: failing tests={baseline}")
    if baseline:
        return 2
    survivors = []
    for group, mutants in (("mutant", MUTANTS), ("equivalent", EQUIVALENT)):
        for name, (target, original, mutated) in mutants.items():
            if SOURCES[target].count(original) != 1:
                print(f"{name}: anchor not found once", file=sys.stderr)
                return 2
            count = failures_for(target, SOURCES[target].replace(original, mutated))
            print(f"{group} {name}: failing tests={count}")
            if group == "mutant" and count == 0:
                survivors.append(name)
    print("survivors: " + (", ".join(survivors) or "none"))
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
