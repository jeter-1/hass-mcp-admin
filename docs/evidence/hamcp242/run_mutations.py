"""HAMCP-242 mutation runner (offline).

Applies one textual mutant at a time to the coordinator or routes module, runs
the HAMCP-242 suite and the updated 2026.9 health test, records which tests
kill it, then restores the original bytes.  Run from the repository root with
the CI test environment; logs go under ``.artifacts/hamcp242/mutants/``.

    python docs/evidence/hamcp242/run_mutations.py
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path.cwd()
LOGS = ROOT / ".artifacts/hamcp242/mutants"
RESULT = ROOT / "docs/evidence/hamcp242/mutations.json"
C = "hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/coordinator.py"
R = "hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/routes.py"
CHECK1 = '''        if len(items) != assessment[count_key]:
            raise CoreReadmissionError(PROJECTION_INCONSISTENT)
'''
CHECK2 = '''    if (
        len(entries) != assessment["withheld_count"]
        or len({item["capability_id"] for item in entries}) != len(entries)
    ):
        raise CoreReadmissionError(PROJECTION_INCONSISTENT)
'''
DUPLICATE = '''
        or len({item["capability_id"] for item in entries}) != len(entries)'''
PARTITION = '''            if (
                assessment["compatible_count"] + assessment["withheld_count"]
                != evaluated
            ):'''
CONTAIN = '''            if exc.code != PROJECTION_INCONSISTENT:
                raise
'''
REFUSED = "            refused = {key: assessment[key] for key in REFUSED_HEALTH_KEYS}\n"
INDICATOR = '''            refused["projection_degraded"] = True
'''
REFUSAL_REASON = '''            refused["projection_reason_code"] = PROJECTION_INCONSISTENT
'''
HELD = '''    (CoreDisposition.HELD.value, "held_capabilities", "held_count"),
'''
QUAR = '''    (
        CoreDisposition.QUARANTINED.value,
        "quarantined_capabilities",
        "quarantined_count",
    ),
'''
UNAV = '''    (
        CoreDisposition.UNAVAILABLE.value,
        "unavailable_capabilities",
        "unavailable_count",
    ),
'''
TOOLS = '''                "affected_tools": list(
                    static_tools_requiring(item["capability_id"])
                ),
'''
RET = "    return entries[:limit], len(entries) > limit\n"
SORT = '    entries.sort(key=lambda item: item["capability_id"])\n'
MUTANTS = {
    # 1. list omits one disposition class (guard intact, then guard removed)
    "omit_held": [(C, HELD, "")],
    "omit_quarantined": [(C, QUAR, "")],
    "omit_unavailable": [(C, UNAV, "")],
    "omit_held_unguarded": [(C, HELD, ""), (C, CHECK1, ""), (C, CHECK2, "")],
    "omit_quarantined_unguarded": [(C, QUAR, ""), (C, CHECK1, ""), (C, CHECK2, "")],
    "omit_unavailable_unguarded": [(C, UNAV, ""), (C, CHECK1, ""), (C, CHECK2, "")],
    # 2. missing or wrong affected_tools
    "tools_empty": [(C, TOOLS, '                "affected_tools": [],\n')],
    "tools_drop_last": [(C, TOOLS, '                "affected_tools": list(static_tools_requiring(item["capability_id"]))[:-1],\n')],
    "tools_drop_first": [(C, TOOLS, '                "affected_tools": list(static_tools_requiring(item["capability_id"]))[1:],\n')],
    "tools_unsorted": [(R, "    return tuple(\n        sorted(\n            name\n", "    return tuple(reversed(\n        sorted(\n            name\n"), (R, "        )\n    )\n\n\ndef delegated_requirements", "        )\n    ))\n\n\ndef delegated_requirements")],
    "tools_include_delegated": [(R, "            for name, values in STATIC_TOOL_CORE_REQUIREMENTS.items()\n            if capability_id in values", "            for name, values in {**STATIC_TOOL_CORE_REQUIREMENTS, **DELEGATED_CORE_REQUIREMENTS}.items()\n            if capability_id in values")],
    "tools_key_missing": [(C, TOOLS, "")],
    # 3. counts and list disagree
    "drop_last_entry_unguarded": [(C, CHECK1, ""), (C, CHECK2, ""), (C, RET, "    entries = entries[:-1]\n" + RET)],
    "duplicate_entry_unguarded": [(C, CHECK1, ""), (C, CHECK2, ""), (C, SORT, SORT + "    entries = entries + entries[:1]\n")],
    "no_class_count_check": [(C, CHECK1, "")],
    "no_total_check": [(C, CHECK2, "")],
    "no_duplicate_check": [(C, DUPLICATE, "")],
    "no_partition_check": [(C, PARTITION, "            if False:")],
    "wrong_disposition_label": [(C, '''                "disposition": disposition,''', '''                "disposition": "unavailable",''')],
    "reason_dropped": [(C, '''                "reason_code": item["reason_code"],''', '''                "reason_code": "withheld",''')],
    "unsorted": [(C, SORT, "")],
    # 4. truncation flag never set / bound wrong
    "truncated_never": [(C, RET, "    return entries[:limit], False\n")],
    "truncated_off_by_one": [(C, RET, "    return entries[:limit], len(entries) >= limit\n")],
    "no_bound": [(C, RET, "    return entries, len(entries) > limit\n")],
    "limit_63": [(C, "WITHHELD_CAPABILITIES_LIMIT = MAX_CAPABILITIES\n", "WITHHELD_CAPABILITIES_LIMIT = MAX_CAPABILITIES - 1\n")],
    "semantics_missing": [(C, '''        projection["count_semantics"] = COUNT_SEMANTICS\n''', "")],
    # 5. R1-H1: the refusal stays inside the Core-authority block
    "refusal_escapes_block": [(C, CONTAIN, "            raise\n")],
    "refusal_contains_every_code": [(C, CONTAIN, "")],
    "refused_block_keeps_counts": [(C, REFUSED, '            refused = {key: assessment[key] for key in (*REFUSED_HEALTH_KEYS, "compatible_count", "withheld_count")}\n')],
    "refused_block_keeps_disposition": [(C, REFUSED, '            refused = {key: assessment[key] for key in (*REFUSED_HEALTH_KEYS, "disposition")}\n')],
    "refused_block_fabricates_empty_list": [(C, INDICATOR, INDICATOR + '            refused["withheld_capabilities"] = []\n')],
    "refused_block_without_indicator": [(C, INDICATOR, "")],
    "refused_block_without_reason": [(C, REFUSAL_REASON, "")],
    "refused_block_drops_generation": [(C, REFUSED, '            refused = {key: assessment[key] for key in REFUSED_HEALTH_KEYS if key != "current_generation"}\n')],
}
TESTS = [
    "tests.test_hamcp242_core_authority_itemization",
    "tests.test_ha_core_2026_9_integration.Core20269RuntimeTests."
    "test_public_health_uses_bounded_core_projection",
]
NAMED = re.compile(r"^(?:FAIL|ERROR): (test_\w+)", re.MULTILINE)
BROKEN = re.compile(r"\b(?:SyntaxError|IndentationError|ImportError|NameError)\b")


def run(name: str) -> dict:
    log = LOGS / f"{name}.log"
    started = time.monotonic()
    with log.open("w") as handle:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", *TESTS],
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=600,
            check=False,
        )
    text = log.read_text()
    ran = re.search(r"^Ran (\d+) tests?", text, re.MULTILINE)
    return {
        "returncode": proc.returncode,
        "tests": int(ran.group(1)) if ran else 0,
        "killed_by": sorted(set(NAMED.findall(text))),
        "broken": bool(BROKEN.search(text)),
        "seconds": round(time.monotonic() - started, 1),
        "log_path": log.relative_to(ROOT).as_posix(),
    }


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    backup = {path: (ROOT / path).read_bytes() for path in (C, R)}
    baseline = run("baseline")
    if baseline["returncode"]:
        print("baseline fails; refusing to run mutants")
        return 1
    results = {}
    try:
        for name, edits in MUTANTS.items():
            texts = {path: data.decode() for path, data in backup.items()}
            for path, old, new in edits:
                if texts[path].count(old) != 1:
                    raise SystemExit(f"{name}: anchor is not unique")
                texts[path] = texts[path].replace(old, new)
            for path, text in texts.items():
                (ROOT / path).write_text(text)
            outcome = run(name)
            outcome["status"] = (
                "killed"
                if outcome["returncode"] and outcome["killed_by"] and not outcome["broken"]
                else "invalid" if outcome["broken"] else "survived"
            )
            results[name] = outcome
            print(f"{name}: {outcome['status']} {outcome['killed_by'][:3]}", flush=True)
    finally:
        for path, data in backup.items():
            (ROOT / path).write_bytes(data)
    summary = {
        "baseline": baseline,
        "mutation_count": len(results),
        "killed": sum(item["status"] == "killed" for item in results.values()),
        "mutants": results,
        "tests": TESTS,
    }
    RESULT.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(f"killed {summary['killed']}/{summary['mutation_count']}")
    return 0 if summary["killed"] == summary["mutation_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
