"""HAMCP-130 mutation runner (offline, deterministic).

Each mutation is a set of exact textual replacements (every old fragment must
match exactly once in its file), applied to a disposable copy of the working
tree outside the repository. ``tests/test_hamcp130_backup_inventory.py`` then
runs; a mutation is killed when it fails. Nothing in the repository is
modified. The machinery is the HAMCP-191 runner's, reduced to one module.

The two brief mutations are ``F*`` (a failed read reported as complete) and
``N*`` (the newest-full-backup logic picking a partial); ``S*`` and ``W*``
probe the sanitizer's known-secret seeding and the fixed read contract.

Usage:
    python docs/evidence/hamcp130-mutations/run_mutations.py \
        --work ~/hamcp130-work/mut --output docs/evidence/hamcp130-mutations/results.json
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
E = "hass_mcp_engineering_beta/ha_mcp_engineering/backup_inventory"
REPORT = f"{E}/report.py"
SERVICE = f"{E}/service.py"
PROJECTION = f"{E}/projection.py"
CONTRACTS = f"{E}/contracts.py"
MODULE = "test_hamcp130_backup_inventory"

MUTATIONS = [
    {"id": "F1-report-always-complete", "design_item": "brief: a failed read reported as complete",
     "replacements": [(REPORT, '"completeness": "complete" if complete else "partial",',
                       '"completeness": "complete",')]},
    {"id": "F2-failed-source-status-complete", "design_item": "brief: a failed read reported as complete",
     "replacements": [(SERVICE, '"status": "unavailable" if reason else "complete",', '"status": "complete",')]},
    {"id": "F3-both-completeness-defenses-removed", "design_item": "brief: a failed read reported as complete",
     "replacements": [
         (SERVICE, '                projector.omit("source_unavailable:" + source["source_id"])\n',
          '                pass\n'),
         (REPORT, 'complete = (all(source["status"] == "complete" for source in sources)\n'
                  '                and not p.omissions and redaction is None)',
          'complete = not p.omissions and redaction is None'),
     ]},
    {"id": "F4-audit-result-success-on-partial", "design_item": "brief: a failed read reported as complete",
     "replacements": [(SERVICE, 'telemetry.result_status = "partial" if result["completeness"] != "complete" else "success"',
                       'telemetry.result_status = "success"')]},
    {"id": "F5-unreadable-location-not-an-omission", "design_item": "brief: a failed read reported as complete",
     "replacements": [(PROJECTION, '    if errors:\n        p.omit("storage_location_unreadable", len(errors))\n', '')]},
    {"id": "F6-record-cap-silent", "design_item": "brief: a failed read reported as complete",
     "replacements": [(PROJECTION, '            p.omit("backup_records_not_read", len(items) - c.MAX_BACKUP_RECORDS)',
                       '            pass')]},
    {"id": "F7-permission-failure-partial", "design_item": "permission failure fails closed",
     "replacements": [(SERVICE, '            raise c.BackupInventoryError("permission_failure")', '            pass')]},
    {"id": "N1-newest-full-is-newest", "design_item": "brief: newest-full-backup logic picks a partial",
     "replacements": [(REPORT, 'newest_full = next((b for b in dated if b["content_scope"] == "full"), None)',
                       'newest_full = next(iter(dated), None)')]},
    {"id": "N2-newest-full-accepts-unknown", "design_item": "brief: newest-full-backup logic picks a partial",
     "replacements": [(REPORT, 'newest_full = next((b for b in dated if b["content_scope"] == "full"), None)',
                       'newest_full = next((b for b in dated if b["content_scope"] != "partial"), None)')]},
    {"id": "N3-classify-ignores-database", "design_item": "brief: newest-full-backup logic picks a partial",
     "replacements": [(PROJECTION, 'if (backup["homeassistant_included"] is False or backup["database_included"] is False\n',
                       'if (backup["homeassistant_included"] is False\n')]},
    {"id": "N4-classify-any-folder-full", "design_item": "brief: newest-full-backup logic picks a partial",
     "replacements": [(PROJECTION, 'if set(backup["folders"]) >= set(c.CORE_FOLDERS):',
                       'if backup["folders"]:')]},
    {"id": "N5-classify-ignores-failed-addons", "design_item": "brief: newest-full-backup logic picks a partial",
     "replacements": [(PROJECTION, '            or backup["failed_addons"] or backup["failed_folders"]):\n        return "partial"',
                       '            or backup["failed_folders"]):\n        return "partial"')]},
    {"id": "N6-newest-full-oldest-first", "design_item": "brief: newest-full-backup logic picks a partial",
     "replacements": [(REPORT, 'newest_full = next((b for b in dated if b["content_scope"] == "full"), None)',
                       'newest_full = next((b for b in reversed(dated) if b["content_scope"] == "full"), None)')]},
    {"id": "S1-key-not-a-known-secret", "design_item": "encryption key withheld from user text",
     "replacements": [(SERVICE, 'projector = Projector(known_secrets=self.known_secrets + ((key,) if key else ()))',
                       'projector = Projector(known_secrets=self.known_secrets)')]},
    {"id": "S2-redaction-not-partial", "design_item": "redaction makes the report partial",
     "replacements": [(REPORT, 'and not p.omissions and redaction is None)', 'and not p.omissions)')]},
    {"id": "W1-extra-command", "design_item": "only the two fixed reads",
     "replacements": [(CONTRACTS, 'COMMANDS = (CONFIG_COMMAND, INFO_COMMAND)',
                       'COMMANDS = (CONFIG_COMMAND, INFO_COMMAND, MappingProxyType({"type": "backup/generate"}))')]},
    {"id": "W2-info-command-changed", "design_item": "only the two fixed reads",
     "replacements": [(CONTRACTS, 'INFO_COMMAND = MappingProxyType({"type": "backup/info"})',
                       'INFO_COMMAND = MappingProxyType({"type": "backup/details"})')]},
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
    command = [sys.executable, "-m", "unittest"] + (["-f"] if failfast else []) + [MODULE]
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=tree, env=environment, capture_output=True,
                                   text=True, timeout=timeout)
        code, output = completed.returncode, completed.stdout + completed.stderr
    except subprocess.TimeoutExpired:
        code, output = -1, "TIMEOUT"
    return code, output, time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    arguments = parser.parse_args()
    work = arguments.work.expanduser().resolve()
    if work == ROOT or ROOT in work.parents:
        raise SystemExit("--work must be outside the repository")
    tree, log_dir = work / "tree", work / "mutation-logs"
    if tree.exists():
        shutil.rmtree(tree)
    shutil.copytree(ROOT, tree, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc"))
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
    baseline = {"log_path": str(baseline_log), "module": MODULE, "returncode": code,
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
            **mutation, "log_path": str(log_path), "returncode": code, "killed": code != 0,
            "killed_by_import_or_syntax_error": any(
                marker in output for marker in ("ImportError", "SyntaxError", "Failed to import")),
            "first_failures": failures[:3], "seconds": round(seconds, 1),
        })
        print(f"{'KILLED' if code else 'SURVIVED':8} {mutation['id']:42} {failures[:1]}", flush=True)

    survived = [item["id"] for item in results if not item["killed"]]
    document = {"baseline": baseline, "mutation_count": len(results),
                "killed": sum(item["killed"] for item in results), "survived": survived,
                "python": platform.python_version(), "results": results}
    arguments.output.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
