"""HAMCP-209 mutation runner (offline, deterministic).

Each mutation is an exact textual replacement in shipped source (every old
fragment must match exactly once), applied to a disposable copy of the
working tree outside the repository. The named test modules then run; a
mutation is killed when they fail. Nothing in the repository is modified.
The machinery is the HAMCP-171 runner's.

Usage:
    python docs/evidence/hamcp209-mutations/run_mutations.py \
        --work ~/.cache/hamcp209/mut --output ~/.cache/hamcp209/mut/results.json

Optional ``--only ID [ID ...]`` reruns a subset; ``--workers N`` sets the
number of tree copies run concurrently (default 2).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
E = "hass_mcp_engineering_beta/ha_mcp_engineering"
PROVIDER = f"{E}/dependency/provider.py"
WEBSOCKET = f"{E}/clients/websocket.py"
SERVICE = f"{E}/governance/service.py"
RESOURCES = f"{E}/governance/resources.py"

T_INDEX = "tests.test_hamcp209_fast_dependency_index"


def m(identifier, item, path, replacements, tests=(T_INDEX,)):
    return {
        "id": identifier,
        "design_item": item,
        "path": path,
        "replacements": [list(pair) for pair in replacements],
        "tests": list(tests),
    }


MUTATIONS = [
    m("M1-per-automation-rest-loop-restored",
      "brief: the per-automation REST loop restored (request-count test must fail)", PROVIDER, [(
        "            if reason is None and not (tracked or journal_overflow):\n",
        "            if False:\n",
    )]),
    m("M2a-missing-automation-reported-complete",
      "brief: a missing automation (loaded config not found) read as an empty, complete config", PROVIDER, [(
        "                or not isinstance(search_result, dict)\n"
        "            ):\n"
        "                return None, \"loaded_config_unreadable\"\n",
        "                or not isinstance(search_result, dict)\n"
        "            ):\n"
        "                return {\"id\": internal_id}, None\n",
    )]),
    m("M2b-pending-written-automation-never-checked",
      "design 4: a written automation absent from the loaded inventory reported as complete", PROVIDER, [(
        "        for key in sorted(set(pending_snapshot) - inventory_ids):\n",
        "        for key in ():\n",
    )]),
    m("M2c-pending-not-loaded-reported-unavailable",
      "design 4: with no other automation, a written-not-loaded automation is partial, not unavailable", PROVIDER, [(
        "            if results or pending_not_loaded or journal_overflow\n",
        "            if results or journal_overflow\n",
    )]),
    m("M3-silent-fallback", "brief: the stored fallback is reported, never silent", PROVIDER, [(
        "            fallback_reasons[reason] += 1\n",
        "",
    )]),
    m("M4-journal-ignored", "design 4: a pending write is read from stale loaded config", PROVIDER, [(
        "            tracked = journal_key in pending_snapshot\n",
        "            tracked = False\n",
    )]),
    m("M5-blueprint-search-ignored", "design 2: a blueprint instance is read from substituted loaded config", PROVIDER, [(
        "            if blueprints or \"use_blueprint\" in config:\n",
        "            if \"use_blueprint\" in config:\n",
    )]),
    m("M6-identity-unchecked", "design 2: a loaded config for another id is accepted", PROVIDER, [(
        "            if str(config.get(\"id\")) != str(internal_id):\n",
        "            if False:\n",
    )]),
    m("M7-allowlist-open", "security: the batch admits any command", WEBSOCKET, [(
        "    if type(payload) is not dict:\n        return False\n    command_type",
        "    return True\n    command_type",
    )]),
    m("M8-governed-write-not-journaled", "design 4: the governed automation write skips the journal", SERVICE, [(
        "        DEPENDENCY_ANALYSIS.note_automation_write(automation_id)\n",
        "",
    )]),
    m("M9-resource-write-not-journaled", "design 4: the resource automation write skips the journal", RESOURCES, [(
        "            DEPENDENCY_ANALYSIS.note_automation_write(resource_id)\n",
        "            pass\n",
    )]),
    # Correction batch (independent review of 5617aa7): H1, M1, M2.
    m("R1-sanitizer-decision-bypassed",
      "review: the shared-sanitizer decision in the effect display filter always passes", PROVIDER, [(
        "        return all(\n"
        "            redact_data(spelling, secret=self._secret) == spelling\n",
        "        return True or all(\n"
        "            redact_data(spelling, secret=self._secret) == spelling\n",
    )]),
    m("H1a-denylist-instead-of-allowlist",
      "H1: values the sanitizer does not recognize are shown verbatim (denylist)", PROVIDER, [
        ("        prefix = f\"{path}|{selector}|\"\n",
         "        prefix = f\"{path}|{selector}|\"\n"
         "        if self._clean(value):\n"
         "            return token\n"),
        ("        if not found or not self._path(path):\n"
         "            return _effect_digest(token)\n",
         "        if not found or not self._path(path):\n"
         "            return _effect_digest(token)\n"
         "        if self._clean(value):\n"
         "            return token\n"),
        ("    def service(self, value: str) -> str:\n",
         "    def service(self, value: str) -> str:\n"
         "        if self._clean(value):\n"
         "            return value\n"),
        ("    def domain(self, value: str) -> str:\n",
         "    def domain(self, value: str) -> str:\n"
         "        if self._clean(value):\n"
         "            return value\n"),
      ]),
    m("H1b-hash-prefix-trusted",
      "H1: a digest-shaped configuration string is trusted as a generated digest", PROVIDER, [(
        "            and value.lower() not in self._leaves\n",
        "            and True\n",
    )]),
    m("H1c-target-position-bypasses-filter",
      "H1: the effect-target position is copied verbatim", PROVIDER, [(
        "                        effect_display.target(str(item))\n",
        "                        str(item)\n",
    )]),
    m("H1d-data-position-bypasses-filter",
      "H1: the effect-data position is copied verbatim", PROVIDER, [(
        "                        effect_display.data(str(item))\n",
        "                        str(item)\n",
    )]),
    m("H1e-service-position-bypasses-filter",
      "H1: the service position is copied verbatim", PROVIDER, [(
        "                        effect_display.service(str(item))\n",
        "                        str(item)\n",
    )]),
    m("H1f-domain-position-bypasses-filter",
      "H1: the action-domain position is copied verbatim", PROVIDER, [(
        "                        effect_display.domain(str(item))\n",
        "                        str(item)\n",
    )]),
    m("H1g-literal-provenance-ignored",
      "H1: identifier grammar alone admits a lowercased projection of a configured value", PROVIDER, [(
        "            and value in self._literals\n"
        "            and self._clean(value)\n",
        "            and self._clean(value)\n",
    )]),
    m("M1a-unverified-loaded-reported-complete",
      "M1: a stored read standing in for unreadable loaded configuration is complete", PROVIDER, [(
        "            elif failure is None and reason in _UNVERIFIED_LOADED_REASONS:\n"
        "                unverified_loaded.add(id(state))\n",
        "",
    )]),
    m("M1c-batch-failure-reported-unsupported",
      "M1: a batch read that raises is reported as an unsupported client (complete)", PROVIDER, [(
        "                loaded.clear()\n"
        "                loaded_source_failure = \"loaded_source_unavailable\"\n",
        "                loaded.clear()\n"
        "                loaded_source_failure = \"loaded_source_unsupported\"\n",
    )]),
    m("M1b-identity-mismatch-treated-stable",
      "M1: a loaded identity mismatch verifies the stored read", PROVIDER, [(
        "_STABLE_LOADED_REASONS = frozenset({\n"
        "    \"blueprint_instance\",\n"
        "    \"loaded_entity_unavailable\",\n"
        "})\n",
        "_STABLE_LOADED_REASONS = frozenset({\n"
        "    \"blueprint_instance\",\n"
        "    \"loaded_entity_unavailable\",\n"
        "    \"loaded_identity_mismatch\",\n"
        "})\n",
    )]),
    m("M2d-overflow-cleared-after-build",
      "M2: the journal overflow is cleared after a build (the pending write is forgotten)", PROVIDER, [(
        "        for key in verified:\n"
        "            if key in snapshot and self._pending_writes.get(key) == snapshot[key]:\n"
        "                del self._pending_writes[key]\n",
        "        for key in verified:\n"
        "            if key in snapshot and self._pending_writes.get(key) == snapshot[key]:\n"
        "                del self._pending_writes[key]\n"
        "        self._pending_write_overflow = False\n",
    )]),
    m("M2e-pending-cleared-after-partial-build",
      "M2: pending writes are cleared after a partial build", PROVIDER, [(
        "        self._settle_pending_writes(pending_snapshot, verified_pending)\n",
        "        self._settle_pending_writes(pending_snapshot, set(pending_snapshot))\n",
    )]),
    m("M2f-overflow-not-reported",
      "M2: an overflowed journal does not make the build partial", PROVIDER, [(
        "        if journal_overflow:\n"
        "            # Identities beyond the journal cap are unknown;",
        "        if False:\n"
        "            # Identities beyond the journal cap are unknown;",
    )]),
    m("M2g-unreconciled-read-failure-not-reported",
      "M2: a journaled write whose read failed is not reported unreconciled", PROVIDER, [(
        "                if str(attrs.get(\"id\")) in pending_snapshot:\n",
        "                if False:\n",
    )]),
]

_FAILURE_LINE = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)", re.MULTILINE)


def apply(tree: Path, mutation: dict) -> dict[Path, str]:
    target = tree / mutation["path"]
    original = target.read_text(encoding="utf-8")
    text = original
    for old, new in mutation["replacements"]:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{mutation['id']}: fragment matched {count} times: {old!r}")
        text = text.replace(old, new)
    target.write_text(text, encoding="utf-8")
    return {target: original}


def run_tests(tree: Path, modules, timeout: int, *, failfast: bool) -> tuple[int, str, float]:
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="tests")
    command = [sys.executable, "-m", "unittest"] + (["-f"] if failfast else []) + list(modules)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=tree, env=environment, capture_output=True, text=True, timeout=timeout,
        )
        code, output = completed.returncode, completed.stdout + completed.stderr
    except subprocess.TimeoutExpired:
        code, output = -1, "TIMEOUT"
    return code, output, time.monotonic() - started


def copy_tree(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        ROOT, destination,
        ignore=shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc"),
        symlinks=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--only", nargs="*")
    arguments = parser.parse_args()
    work = arguments.work.expanduser().resolve()
    if work == ROOT or ROOT in work.parents:
        raise SystemExit("--work must be outside the repository")
    log_dir = arguments.output.expanduser().resolve().parent / "mutation-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    selected = [item for item in MUTATIONS if not arguments.only or item["id"] in arguments.only]
    if len({item["id"] for item in MUTATIONS}) != len(MUTATIONS):
        raise SystemExit("duplicate mutation id")
    trees: "queue.Queue[Path]" = queue.Queue()
    for index in range(arguments.workers):
        tree = work / f"tree-{index}"
        copy_tree(tree)
        trees.put(tree)
    # The unmutated baseline must pass every module the mutations use.
    baseline_modules = sorted({module for item in selected for module in item["tests"]})
    tree = trees.get()
    code, output, seconds = run_tests(tree, baseline_modules, arguments.timeout, failfast=False)
    trees.put(tree)
    baseline_log = log_dir / "baseline.log"
    baseline_log.write_text(output, encoding="utf-8")
    baseline = {"log": baseline_log.name, "modules": baseline_modules, "returncode": code, "seconds": round(seconds, 1),
                "summary": output.strip().splitlines()[-1] if output.strip() else ""}
    print(f"baseline rc={code} {baseline['summary']} ({seconds:.0f}s)", flush=True)
    if code != 0:
        print(output[-4000:])
        return 2

    def one(mutation: dict) -> dict:
        tree = trees.get()
        try:
            originals = apply(tree, mutation)
            try:
                code, output, seconds = run_tests(
                    tree, mutation["tests"], arguments.timeout, failfast=True
                )
            finally:
                for path, text in originals.items():
                    path.write_text(text, encoding="utf-8")
        finally:
            trees.put(tree)
        failures = [f"{kind} {name} ({where})" for kind, name, where in _FAILURE_LINE.findall(output)]
        import_error = "ImportError" in output or "SyntaxError" in output or "Failed to import" in output
        log_path = log_dir / f"{mutation['id']}.log"
        log_path.write_text(output, encoding="utf-8")
        result = {
            **mutation,
            "log": log_path.name,
            "returncode": code,
            "killed": code != 0,
            "killed_by_import_or_syntax_error": import_error,
            "first_failures": failures[:3],
            "seconds": round(seconds, 1),
        }
        state = "KILLED" if result["killed"] else "SURVIVED"
        print(f"{state:8} {mutation['id']:34} {seconds:5.0f}s {failures[:1]}", flush=True)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=arguments.workers) as pool:
        results = list(pool.map(one, selected))
    survived = [item["id"] for item in results if not item["killed"]]
    report = {
        "baseline": baseline,
        "mutation_count": len(results),
        "killed": len(results) - len(survived),
        "survived": survived,
        "python": sys.version.split()[0],
        "results": results,
    }
    output_path = arguments.output.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"{report['killed']}/{len(results)} killed; survived: {survived}")
    return 0 if not survived else 1


if __name__ == "__main__":
    raise SystemExit(main())
