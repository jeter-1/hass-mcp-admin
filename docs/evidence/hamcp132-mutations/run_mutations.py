"""HAMCP-132 mutation runner (offline, deterministic).

Each mutation is a set of exact textual replacements (every old fragment must
match exactly once in its file), applied to a disposable copy of the working
tree outside the repository. ``tests/test_hamcp132_recorder_settings.py`` and
``tests/test_hamcp132_unregistered_entities.py`` then run; a mutation is
killed when they fail. Nothing in the repository is modified. The machinery is
the HAMCP-130 runner's, with two modules.

Every row of the design's section 7 table has at least one mutation:
``D*`` (a default reported as ``configured``, or applied when packages,
an include, truncation or an unavailable file make it unknown), ``L*``
(``db_url`` or a referenced secret leaked, the sanitizer's known secrets
dropped), ``P*`` (a failed YAML, ``recorder/info`` or ``entity_options``
source reported as complete), ``F*`` (filter precedence: 4a and 4b swapped,
exclude domains honoured in 4a, the 4b ``include.entities`` override lost),
``V*`` (drift silently resolved), ``U*`` (``unknown_command`` treated as
"not recording"), ``R*`` (a registry-less entity dropped, a location
asserted, a ``search/related`` failure, an ambiguous entry guessed or a
truncation reported as complete) and ``S*`` (a command reachable outside the
allowlist, a loosened identity pattern, the Core gate or pre-SDK validation
removed). ``A*`` probe the authority checks, ``Y*`` the fixed YAML paths and
envelope identity.

Usage:
    python docs/evidence/hamcp132-mutations/run_mutations.py \
        --work ~/hamcp132-work/mut --output docs/evidence/hamcp132-mutations/results.json
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
E = "hass_mcp_engineering_beta/ha_mcp_engineering"
PROJECTION = f"{E}/recorder_settings/projection.py"
SERVICE = f"{E}/recorder_settings/service.py"
FILTER = f"{E}/recorder_settings/entity_filter.py"
RECON = f"{E}/entity_reconciliation/service.py"
WEBSOCKET = f"{E}/clients/websocket.py"
ROUTES = f"{E}/ha_core_readmission/routes.py"
ROUTING = f"{E}/routing.py"
GATEWAY = f"{E}/providers/upstream_read_gateway.py"
MODULES = ("test_hamcp132_recorder_settings", "test_hamcp132_unregistered_entities")
ROW1 = "design 7: core_default only when the source is complete"
ROW2 = "design 7: db_url withheld"
ROW3 = "design 7: a failing source is partial with an exact reason"
ROW4 = "design 7: filter cases 1-6 against Core's truth table"
ROW5 = "design 7: disagreement gives filter_drift"
ROW6 = "design 7: unknown_command means recorder not set up"
ROW7 = "design 7: reconciliation platform, search failure partial"
ROW8 = "design 7: no write reachability, Core gate, exact batch allowlist"
AUTH = "authority and caller checks"
YAML = "fixed YAML paths and envelope identity"


def m(identifier, item, *replacements):
    return {"id": identifier, "design_item": item, "replacements": list(replacements)}


MUTATIONS = [
    m("D1-default-reported-configured", ROW1,
      (PROJECTION, '{"value": default, "origin": "core_default", "pin": f.CORE_PIN}',
       '{"value": default, "origin": "configured", "pin": f.CORE_PIN}')),
    m("D2-default-applied-with-packages-present", ROW1,
      (PROJECTION, '        if packages == "absent":\n            return None',
       '        if packages != "unreadable":\n            return None')),
    m("D3-default-applied-with-include", ROW1,
      (PROJECTION, '        if self._opaque(recorder):\n            return "include_not_followed"',
       '        if False:\n            return "include_not_followed"')),
    m("D4-default-applied-yaml-unavailable", ROW1,
      (PROJECTION, '            if reason is None:\n                result[key] = {"value": default,',
       '            if reason in (None, "yaml_unavailable"):\n                result[key] = {"value": default,')),
    m("D5-default-applied-when-truncated", ROW1,
      (PROJECTION, '        if recorder["found"] and _truncated(recorder):\n            return "yaml_truncated"',
       '        if False:\n            return "yaml_truncated"')),
    m("D5b-filter-computed-when-truncated", ROW1,
      (PROJECTION, '        if recorder["found"] and _truncated(recorder):\n            self.omit("filter_unknown:yaml_truncated")',
       '        if False:\n            self.omit("filter_unknown:yaml_truncated")')),
    m("D6-filter-computed-with-packages-present", ROW1,
      (PROJECTION, '        if packages != "absent":\n            # Packages merge',
       '        if packages == "unreadable":\n            # Packages merge')),
    m("D7-db-default-location-with-packages", ROW1,
      (PROJECTION, 'db.update({"origin": "core_default_location"} if reason is None',
       'db.update({"origin": "core_default_location"} if reason in (None, "packages_present")')),
    m("D8-packages-unreadable-reported-complete", ROW1,
      (SERVICE, '"complete" if package_state in {"absent", "present"} else "unavailable",', '"complete",')),
    m("L1-db-url-value-returned", ROW2,
      (PROJECTION, '            db["origin"] = "configured"',
       '            db.update(origin="configured", value=written[c.DB_URL_KEY])')),
    m("L2-secret-reference-returned", ROW2,
      (PROJECTION, 'return {"value": None, "origin": "configured", "value_withheld": True,',
       'return {"value": value, "origin": "configured", "value_withheld": True,')),
    m("L3-recorder-known-secrets-dropped", ROW2,
      (SERVICE, 'sanitize_untrusted_data(result, known_secrets=self.known_secrets,',
       'sanitize_untrusted_data(result, known_secrets=(),')),
    m("L4-unrecognized-keys-reported", ROW2,
      (PROJECTION, '            result["unrecognized_key_count"] = unrecognized',
       '            result["unrecognized_key_count"] = unrecognized\n'
       '            result["unrecognized"] = {k: v for k, v in written.items() if k not in c.RECOGNIZED_KEYS}')),
    m("P1-failed-yaml-source-complete", ROW3,
      (SERVICE, '"unavailable" if recorder is None else', '"complete" if recorder is None else')),
    m("P2-failed-recorder-info-complete", ROW3,
      (SERVICE, '"complete" if runtime["status"] == "available" else (', '"complete" if True else (')),
    m("P3-partial-entity-options-complete", ROW3,
      (SERVICE, '"complete" if answered == len(entity_ids) else "partial",', '"complete",')),
    m("P4-report-always-complete", ROW3,
      (SERVICE, 'result["complete"] = not projector.omissions and all(', 'result["complete"] = True or all(')),
    m("P5-source-omission-dropped", ROW3,
      (SERVICE, '                projector.omit(f"source_{source[\'status\']}:{source[\'source\']}")',
       '                pass')),
    m("P6-audit-success-on-partial", ROW3,
      (SERVICE, 'telemetry.result_status = "success" if result["complete"] else "partial"',
       'telemetry.result_status = "success"')),
    m("P7-batch-error-text-reflected", ROW3,
      (SERVICE, 'except HomeAssistantApiError:\n            reason = "command_failed"',
       'except HomeAssistantApiError as exc:\n            reason = str(exc) or "command_failed"')),
    m("P8-entity-reply-shape-loosened", ROW3,
      (PROJECTION, 'set(value) == {"recording_disabled_by"}', '"recording_disabled_by" in value')),
    m("P9-response-budget-trim-silent", ROW3,
      (SERVICE, '            projector.omit("filter_lists_not_listed_response_bytes")', '            pass')),
    m("F1-cases-4a-4b-swapped", ROW4,
      (FILTER, '    if case == INCLUDE_DOMAIN_OR_GLOB:\n        def entity_filter_4a',
       '    if case == EXCLUDE_DOMAIN_OR_GLOB:\n        def entity_filter_4a'),
      (FILTER, '    if case == EXCLUDE_DOMAIN_OR_GLOB:\n        def entity_filter_4b',
       '    if case == INCLUDE_DOMAIN_OR_GLOB:\n        def entity_filter_4b')),
    m("F2-exclude-domains-honoured-in-4a", ROW4,
      (FILTER, '                entity_id not in exclude_e\n                and (',
       '                entity_id not in exclude_e\n                and split_entity_id(entity_id)[0] not in exclude_d\n'
       '                and (')),
    m("F3-4b-include-entities-override-lost", ROW4,
      (FILTER, '                return entity_id in include_e\n', '                return False\n')),
    m("F4-exclude-domains-ignored-flag-lost", ROW4,
      (PROJECTION, '"exclude_domains_ignored": (recorder_filter.case == f.INCLUDE_DOMAIN_OR_GLOB',
       '"exclude_domains_ignored": (False')),
    m("V1-drift-resolved-as-agree", ROW5,
      (PROJECTION, 'row["agreement"] = "agree" if core == computed else "filter_drift"', 'row["agreement"] = "agree"')),
    m("V2-drift-omission-dropped", ROW5,
      (PROJECTION, '                self.omit("filter_drift:" + entity_id)', '                pass')),
    m("V3-core-answer-replaced-by-computed", ROW5,
      (PROJECTION, '{"entity_id": entity_id, "core_recorded": core,',
       '{"entity_id": entity_id, "core_recorded": computed if computed is not None else core,')),
    m("U1-unknown-command-not-recording", ROW6,
      (PROJECTION, '            return {"status": "unavailable",\n'
                   '                    "reason": "recorder_not_set_up" if value == "unknown_command" else "command_failed"}',
       '            return ({"status": "available", "recording": False} if value == "unknown_command"\n'
       '                    else {"status": "unavailable", "reason": "command_failed"})')),
    m("U2-entity-unknown-command-not-recorded", ROW6,
      (PROJECTION, '                    reason = "recorder_not_set_up" if value == "unknown_command" else "command_failed"',
       '                    core, reason = (False, None) if value == "unknown_command" else (None, "command_failed")')),
    m("R1-directly-written-entity-dropped", ROW7,
      (RECON, 'row.update(platform=None, platform_status="directly_written_state")', 'continue')),
    m("R2-definition-location-asserted", ROW7,
      (RECON, '"definition_location": "not_determinable",', '"definition_location": "configuration.yaml",')),
    m("R3-partial-search-reported-complete", ROW7,
      (RECON, 'sources[-1].update(status="partial" if failed < len(searched) else "unavailable",',
       'sources[-1].update(status="complete" if failed < len(searched) else "unavailable",')),
    m("R4-search-unavailable-silent", ROW7,
      (RECON, '                omit("config_entry_unknown:search_related_unavailable")', '                pass')),
    m("R5-ambiguous-entry-guessed", ROW7,
      (RECON, '        return None, "ambiguous"', '        return entries[0], "resolved"')),
    m("R6-limit-truncation-silent", ROW7,
      (RECON, '            omit("entities_not_listed_limit")', '            pass')),
    m("R7-one-inventory-enough", ROW7,
      (RECON, 'if states is None or registry is None:', 'if states is None and registry is None:')),
    m("R8-malformed-registry-silent", ROW7,
      (RECON, '            omit("malformed_registry_rows_not_reconciled")', '            pass')),
    m("R9-invalid-platform-value-reported", ROW7,
      (RECON, 'platform if type(platform) is str and c.DOMAIN.fullmatch(platform) else None',
       'platform if type(platform) is str else None')),
    m("R10-domains-string-split", ROW7,
      (RECON, '"domains": list(domains) if type(domains) is tuple else domains}', '"domains": list(domains)}')),
    m("R11-reconciliation-known-secrets-dropped", ROW7,
      (RECON, 'sanitize_untrusted_data(result, known_secrets=self.known_secrets, max_string=255)',
       'sanitize_untrusted_data(result, known_secrets=(), max_string=255)')),
    m("R12-byte-budget-trim-silent", ROW7,
      (RECON, '            omit("entities_not_listed_response_bytes")', '            pass')),
    m("S1-purge-reachable-in-batch", ROW8,
      (WEBSOCKET, '    "recorder/info": frozenset({"type"}),\n}',
       '    "recorder/info": frozenset({"type"}),\n    "recorder/purge": frozenset({"type"}),\n}'),
      (WEBSOCKET, 'if command_type == "recorder/info":', 'if command_type in {"recorder/info", "recorder/purge"}:')),
    m("S2-entity-options-identity-loosened", ROW8,
      (WEBSOCKET, '    "recorder/entity_options/get": STRICT_ENTITY_ID,\n}',
       '    "recorder/entity_options/get": re.compile(r".+"),\n}')),
    m("S3-search-entity-identity-loosened", ROW8,
      (WEBSOCKET, '    "entity": STRICT_ENTITY_ID,\n}', '    "entity": re.compile(r".+"),\n}')),
    m("S4-recorder-info-extra-keys", ROW8,
      (WEBSOCKET, '    if command_type == "recorder/info":\n        return True\n',
       '    if command_type == "recorder/info":\n        return True\n'
       '    if command_type == "recorder/entity_options/get" and "entity_id" in payload:\n        return True\n')),
    m("S5-recorder-core-gate-narrowed", ROW8,
      (ROUTES, '"get_recorder_settings": ("core.basic_rest_read", "core.basic_websocket_read"),',
       '"get_recorder_settings": ("core.basic_websocket_read",),')),
    m("S6-reconciliation-core-gate-narrowed", ROW8,
      (ROUTES, '        "core.basic_rest_read", "core.basic_websocket_read", "core.non_device_registry_read",\n    ),',
       '        "core.basic_rest_read", "core.basic_websocket_read",\n    ),')),
    m("S7-pre-sdk-validation-removed", ROW8,
      (ROUTING, 'if tool_name == "get_recorder_settings" or tool_name == "get_unregistered_entities":', 'if False:')),
    m("S8-audit-records-entity-ids", ROW8,
      (ROUTING, '"entity_count": len(entity_ids) if type(entity_ids) is list and len(entity_ids) <= 50 else None,',
       '"entity_count": len(entity_ids) if type(entity_ids) is list and len(entity_ids) <= 50 else None,'
       ' "entity_ids": entity_ids,')),
    m("A1-recorder-no-recheck-after-batch", AUTH,
      (SERVICE, '        if self.authority() != authority:\n            raise c.RecorderSettingsError("authority_unavailable")\n'
                '        return outcomes, reason', '        return outcomes, reason')),
    m("A2-recorder-no-recheck-after-yaml", AUTH,
      (SERVICE, '        if self.authority() != authority:\n            raise c.RecorderSettingsError("authority_unavailable")\n'
                '        return gateway_envelope(result, path)', '        return gateway_envelope(result, path)')),
    m("A3-recorder-anonymous-accepted", AUTH,
      (SERVICE, 'if current_caller_id() == "anonymous":', 'if False:')),
    m("A4-recorder-missing-authorizer-accepted", AUTH,
      (SERVICE, 'or telemetry is None or telemetry.core_dispatch_authorizer is None\n', 'or telemetry is None\n')),
    m("A5-reconciliation-no-recheck-after-read", AUTH,
      (RECON, '        if self.authority() != authority:\n            raise c.EntityReconciliationError("authority_unavailable")\n'
              '        return raw', '        return raw')),
    m("A6-reconciliation-anonymous-accepted", AUTH,
      (RECON, 'if current_caller_id() == "anonymous":', 'if False:')),
    m("A7-recorder-tuple-or-string-split", AUTH,
      (SERVICE, '{"entity_ids": list(entity_ids) if type(entity_ids) is tuple else entity_ids}',
       '{"entity_ids": list(entity_ids)}')),
    m("Y1-yaml-path-allowlist-removed", YAML,
      (GATEWAY, 'if yaml_path not in YAML_CONFIG_PATHS:', 'if yaml_path is None:')),
    m("Y2-yaml-fallback-envelope-accepted", YAML,
      (PROJECTION, '            or metadata.get("fallback_occurred") is not False\n', '')),
    m("Y3-yaml-other-path-accepted", YAML,
      (PROJECTION, 'or type(data) is not dict or data.get("yaml_path") != yaml_path',
       'or type(data) is not dict')),
    m("Y4-yaml-error-code-reflected", YAML,
      (PROJECTION, 'return None, code if code in c.YAML_FAILURE_CODES else "yaml_read_failed"',
       'return None, code if code else "yaml_read_failed"')),
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
    command = [sys.executable, "-m", "unittest"] + (["-f"] if failfast else []) + list(MODULES)
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
    baseline = {"log_path": str(baseline_log), "modules": list(MODULES), "returncode": code,
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
