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
envelope identity. The review correction adds ``H*`` (omissions appended after
sanitization, a redaction that leaves the report complete), ``M*`` (a value
whose YAML 1.1 reading differs reported as ``fails_core_schema`` or as a
definite value, each YAML 1.1 form ignored; after the owner decisions of
2026-10-20, a plain integer made spelling-dependent, a quoted string or
``y``/``n`` treated as a YAML 1.1 boolean or number, the integer-spelling
disclosure dropped) and the independent reviewer's own
``REVIEW-1``.

Slice 2 (``get_configuration_definitions``) adds ``tests/test_hamcp132_
configuration_definitions.py`` and the ``B*`` mutations: ``B-L*`` (``db_url``,
a credential key, a userinfo URI, a reference or a reader redaction leaked,
the known secrets dropped), ``B-M*`` (a heuristic reported as exact, an
ambiguity guessed, confidence overstated, the UI readability claim changed),
``B-P*`` (a failed source reported complete, a failure, package, include or
unprojected definition not disclosed, a total failure returned as a report),
``B-H*`` (omissions appended after sanitization, a redaction or truncation
that leaves the report complete), ``B-D*`` (diagnosis or capability claims
beyond their signals, an upstream ``disabled_by`` echoed), ``B-S*`` (the YAML
path set widened, the Core gate or either pre-SDK validation removed, a
domain outside the closed set, audit or annotations loosened), ``B-A*``
(authority rechecks and caller checks) and ``B-B*`` (output bounds, the
listed count, the size-based trim).

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
DEFS = f"{E}/configuration_definitions"
DPROJ, DSVC, DMATCH, DDIAG, DCON = (f"{DEFS}/{name}.py" for name in
                                    ("projection", "service", "matching", "diagnosis", "contracts"))
DTOOL = f"{E}/tools/configuration_definitions.py"
MODULES = ("test_hamcp132_recorder_settings", "test_hamcp132_unregistered_entities",
           "test_hamcp132_configuration_definitions")
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
FINAL = "H1: the complete final envelope is sanitized once, nothing appended after"
SPELLING = "M1: a value whose YAML 1.1 reading differs is parser_dependent"
REVIEW = "independent review: each Core verdict stays associated with its requested entity"
B_LEAK = "slice 2: db_url and credentials withheld in every position"
B_MATCH = "slice 2: match quality stated, never overclaimed"
B_PARTIAL = "slice 2: a failing source is partial with an exact reason; packages and !include disclosed"
B_FINAL = "slice 2: the complete final envelope is sanitized once, nothing appended after"
B_DIAG = "slice 2: diagnosis and capability rest only on their signals"
B_STATIC = "slice 2: fixed reads only, Core gate, pre-SDK validation, read-only metadata"
B_AUTH = "slice 2: authority and caller checks"
B_BUDGET = "slice 2: bounded output"


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
      (SERVICE, 'sanitize_untrusted_data(envelope, known_secrets=self.known_secrets,',
       'sanitize_untrusted_data(envelope, known_secrets=(),')),
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
      (SERVICE, 'envelope["complete"] = not projector.omissions and all(', 'envelope["complete"] = True or all(')),
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
      # The collapsed lists are returned without the omission.
      (SERVICE, '                projector.omit("filter_lists_not_listed_response_bytes")\n                changed = True',
       '                return sanitize_untrusted_data({**envelope, "filters": filters}, known_secrets=self.known_secrets,'
       ' max_string=c.MAX_FILTER_TEXT).value')),
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
      (RECON, 'sanitize_untrusted_data(envelope, known_secrets=self.known_secrets, max_string=255)',
       'sanitize_untrusted_data(envelope, known_secrets=(), max_string=255)')),
    m("R12-byte-budget-trim-silent", ROW7,
      (RECON, '                omit("entities_not_listed_response_bytes")', '                pass')),
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
      (ROUTES, '    "get_unregistered_entities": (\n'
               '        "core.basic_rest_read", "core.basic_websocket_read", "core.non_device_registry_read",\n    ),',
       '    "get_unregistered_entities": (\n        "core.basic_rest_read", "core.basic_websocket_read",\n    ),')),
    m("S7-pre-sdk-validation-removed", ROW8,
      (ROUTING, 'if tool_name == "get_recorder_settings" or tool_name == "get_unregistered_entities" '
                'or tool_name == "get_configuration_definitions":',
       'if False:')),
    m("S8-audit-records-entity-ids", ROW8,
      (ROUTING, '"profile": "recorder-settings-read-v1",\n'
                '                        "entity_count": len(entity_ids) if type(entity_ids) is list and len(entity_ids) <= 50 else None,',
       '"profile": "recorder-settings-read-v1",\n'
       '                        "entity_count": len(entity_ids) if type(entity_ids) is list and len(entity_ids) <= 50 else None,'
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
      (GATEWAY, 'if yaml_path not in allowed_paths:', 'if yaml_path is None:')),
    m("Y2-yaml-fallback-envelope-accepted", YAML,
      (PROJECTION, '            or metadata.get("fallback_occurred") is not False\n', '')),
    m("Y3-yaml-other-path-accepted", YAML,
      (PROJECTION, 'or type(data) is not dict or data.get("yaml_path") != yaml_path',
       'or type(data) is not dict')),
    m("Y4-yaml-error-code-reflected", YAML,
      (PROJECTION, 'return None, code if code in c.YAML_FAILURE_CODES else "yaml_read_failed"',
       'return None, code if code else "yaml_read_failed"')),
    m("H1-recorder-omissions-appended-after-sanitization", FINAL,
      (SERVICE, 'envelope = {**report, "omissions": list(projector.omissions)}', 'envelope = dict(report)'),
      (SERVICE, 'return sanitized.value', 'return {**sanitized.value, "omissions": list(projector.omissions)}')),
    m("H2-reconciliation-omissions-appended-after-sanitization", FINAL,
      (RECON, 'envelope = {**result, "omissions": list(omissions)}', 'envelope = dict(result)'),
      (RECON, 'return sanitized.value', 'return {**sanitized.value, "omissions": list(omissions)}')),
    m("H3-recorder-redaction-not-partial", FINAL,
      (SERVICE, 'and "redacted_by_policy" not in projector.omissions:', 'and False:')),
    m("H4-reconciliation-redaction-not-partial", FINAL,
      (RECON, 'and "redacted_by_policy" not in omissions:', 'and False:')),
    m("M1-parser-dependent-reported-fails-core-schema", SPELLING,
      (PROJECTION, 'if any(outcome != outcomes[0] for outcome in outcomes[1:]):\n                # The reader',
       'if outcomes[0][0] and any(outcome != outcomes[0] for outcome in outcomes[1:]):\n                # The reader')),
    m("M2-parser-dependent-reported-definite", SPELLING,
      (PROJECTION, 'if any(outcome != outcomes[0] for outcome in outcomes[1:]):\n                # The reader',
       'if False:\n                # The reader')),
    m("M3-parser-dependent-filter-list-fails-core-schema", SPELLING,
      (PROJECTION, 'return "parser_dependent" if dependent else None', 'return "fails_core_schema" if dependent else None')),
    m("M4-yaml11-boolean-family-ignored", SPELLING,
      (PROJECTION, 'if _YAML11_BOOL.match(value):', 'if False:')),
    m("M5-yaml11-sexagesimal-ignored", SPELLING,
      (PROJECTION, '|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)\\Z""", re.X)', ')\\Z""", re.X)')),
    m("M6-plain-integer-reported-spelling-dependent", SPELLING,
      (PROJECTION, '    if type(value) is float and math.isfinite(value):',
       '    if type(value) in (int, float) and math.isfinite(value):')),
    m("M7-exponent-float-ignored", SPELLING,
      (PROJECTION, 'if type(value) is float and math.isfinite(value):', 'if False:')),
    m("M8-parser-dependent-omission-dropped", SPELLING,
      (PROJECTION, '                self.omit(f"setting_parser_dependent:{key}")', '                pass')),
    m("M9-quoted-string-treated-as-unquoted", SPELLING,
      (PROJECTION, '        if _YAML12_NOT_STRING.match(value):\n            return None\n', '')),
    m("M10-spec-booleans-y-n-read-as-yaml11-booleans", SPELLING,
      (PROJECTION, '|on|On|ON|off|Off|OFF)\\Z")', '|on|On|ON|off|Off|OFF|y|Y|n|N)\\Z")')),
    m("M11-numeric-spelling-disclosure-dropped", SPELLING,
      (SERVICE, '"yaml_numeric_spelling": c.YAML_NUMERIC_SPELLING_LIMITATION,', '')),
    m("M12-yaml12-boolean-quoting-lost", SPELLING,
      (PROJECTION, '(?:true|True|TRUE|false|False|FALSE\n', '(?:\n')),
    m("M13-yaml12-float-quoting-lost", SPELLING,
      (PROJECTION, '|[-+]?(?:[0-9][0-9_]*)\\.[0-9_]*(?:[eE][-+]?[0-9]+)?\n', '\n')),
    m("REVIEW-1-reverse-entity-options-association", REVIEW,
      (PROJECTION, 'ok, value = outcomes[index]', 'ok, value = outcomes[-index - 1]')),
    # --- Slice 2: get_configuration_definitions ---------------------------
    m("B-L1-db-url-projected-as-field", B_LEAK,
      (DPROJ, '            if type(key) is str and key in c.WITHHELD_KEYS:\n'
              '                record["withheld"].append(f"{path}.{key}")\n'
              '            elif type(key) is str and key in allowed:',
       '            if type(key) is str and (key in allowed or key in c.WITHHELD_KEYS):')),
    # Removing only the nested ``WITHHELD_KEYS`` test is an equivalent mutant:
    # ``_credential_shaped`` also matches ``db_url`` and its spellings (run 1
    # survivor, checked directly). The mutant removes both nested guards.
    m("B-L2-nested-db-url-kept", B_LEAK,
      (DPROJ, 'if type(key) is not str or key in c.WITHHELD_KEYS or _credential_shaped(key):',
       'if type(key) is not str:')),
    m("B-L3-nested-credential-key-kept", B_LEAK,
      (DPROJ, 'if type(key) is not str or key in c.WITHHELD_KEYS or _credential_shaped(key):',
       'if type(key) is not str or key in c.WITHHELD_KEYS:')),
    m("B-L4-userinfo-uri-guard-removed", B_LEAK,
      (DPROJ, '        if _userinfo_text(value):\n            record["withheld"].append(path)\n'
              '            return {"withheld": "credential_uri"}\n', '')),
    m("B-L5-secret-reference-projected", B_LEAK,
      (DPROJ, '        if _is_reference(value):', '        if False:')),
    m("B-L6-reader-redaction-projected", B_LEAK,
      (DPROJ, '        if _reader_marker(value):', '        if False:')),
    m("B-L7-known-secrets-dropped", B_LEAK,
      (DSVC, 'sanitize_untrusted_data(envelope, known_secrets=self.known_secrets,',
       'sanitize_untrusted_data(envelope, known_secrets=(),')),
    m("B-M1-heuristic-reported-exact", B_MATCH,
      (DMATCH, 'return {"method": "heuristic", "confidence": confidence,',
       'return {"method": "heuristic", "confidence": "exact",')),
    m("B-M2-heuristic-reported-exact-unique-id", B_MATCH,
      (DMATCH, 'return {"method": "heuristic", "confidence": confidence,',
       'return {"method": "exact_unique_id", "confidence": confidence,')),
    m("B-M3-ambiguous-name-guessed", B_MATCH,
      (DMATCH, '        if len(candidates) > 1:\n', '        if False:\n')),
    m("B-M4-shared-unique-id-guessed", B_MATCH,
      (DMATCH, 'return {"method": "ambiguous", "confidence": None, "definition_ids": found[:MAX_CANDIDATES],',
       'return {"method": "exact_unique_id", "confidence": "exact", "definition_ids": found[:1],')),
    m("B-M5-high-confidence-on-incomplete-sources", B_MATCH,
      (DMATCH, 'elif platform is not None and self.yaml_complete and self.registry is not None:',
       'elif platform is not None:')),
    m("B-M6-collision-suffix-not-low", B_MATCH,
      (DMATCH, '        if suffix:\n            confidence = "low"\n', '        if False:\n            confidence = "low"\n')),
    m("B-M7-platform-filter-removed", B_MATCH,
      (DMATCH, '        if platform is not None:\n            candidates = [',
       '        if False:\n            candidates = [')),
    m("B-M8-heuristic-applied-to-registered-entity", B_MATCH,
      (DMATCH, '            return {"method": "unmatched", "confidence": None, "definition_ids": [],\n'
               '                    "reason": "no_read_definition_has_this_unique_id"}',
       '            return self._heuristic(entity_id, self.by_derived.get(entity_id, []), False) or {\n'
       '                "method": "unmatched", "confidence": None, "definition_ids": [],\n'
       '                "reason": "no_read_definition_has_this_unique_id"}')),
    m("B-M9-ui-entry-claimed-readable", B_MATCH,
      (DMATCH, '"readability": "definition_not_readable_without_flow",', '"readability": "read",')),
    m("B-P1-failed-source-reported-complete", B_PARTIAL,
      (DSVC, 'status = "complete" if reason is None or (absent_ok and reason == "not_found") else (',
       'status = "complete" if True else (')),
    m("B-P2-source-failure-not-disclosed", B_PARTIAL,
      (DSVC, '            if source["status"] != "complete":\n                omit(',
       '            if False:\n                omit('),
      (DSVC, 'source["status"] == "complete" for source in report["sources"])',
       'True for source in report["sources"])')),
    m("B-P3-failed-yaml-source-reported-complete", B_PARTIAL,
      (DSVC, '"unavailable" if envelope is None else "complete" if complete else "partial",', '"complete",')),
    m("B-P4-packages-not-disclosed", B_PARTIAL,
      (DSVC, '            omit("definitions_in_packages_not_read" if package_state == "present"\n'
             '                 else "packages_not_determined")', '            pass')),
    m("B-P5-whole-path-include-not-disclosed", B_PARTIAL,
      (DSVC, '            omit(f"include_not_followed:{path}")', '            pass')),
    m("B-P6-value-include-not-disclosed", B_PARTIAL,
      (DSVC, '            omit("definition_values_include_not_followed")', '            pass')),
    m("B-P7-unprojected-definitions-not-disclosed", B_PARTIAL,
      (DSVC, '            omit("definitions_not_projected")', '            pass')),
    m("B-P8-total-failure-returned-as-report", B_PARTIAL,
      (DSVC, '        if all(source["status"] in {"unavailable", "refused"} for source in sources):\n',
       '        if False:\n')),
    m("B-H1-omissions-appended-after-sanitization", B_FINAL,
      (DSVC, 'envelope = {**report, "omissions": list(omissions)}', 'envelope = dict(report)'),
      (DSVC, 'return sanitized.value', 'return {**sanitized.value, "omissions": list(omissions)}')),
    m("B-H2-redaction-not-partial", B_FINAL,
      (DSVC, 'and "redacted_by_policy" not in omissions:', 'and False:')),
    m("B-H3-text-bound-dropped", B_FINAL,
      (DSVC, 'max_string=c.MAX_TEXT)', 'max_string=None)')),
    m("B-D1-unmatched-definition-reported-entity-level", B_DIAG,
      (DDIAG, 'if definition is None and entry_id is None and match.get("method") != "not_applicable":',
       'if False:')),
    m("B-D2-dependencies-not-checked", B_DIAG,
      (DDIAG, '        if missing or unavailable:\n', '        if False:\n')),
    m("B-D3-registry-disabled-ignored", B_DIAG,
      (DDIAG, '    if registry_row is not None and registry_row.get("disabled_by") is not None:',
       '    if False:')),
    m("B-D4-upstream-disabled-by-echoed", B_DIAG,
      (DSVC, '"disabled_by": disabled_by if disabled_by in diagnosis.DISABLED_BY else (',
       '"disabled_by": disabled_by if True else (')),
    m("B-D5-transient-entry-called-deployment", B_DIAG,
      (DDIAG, '    if entry["state"] in TRANSIENT_ENTRY_STATES:\n', '    if False:\n')),
    m("B-D6-platform-not-loaded-skipped", B_DIAG,
      (DDIAG, '            if f"{integration}.{domain}" not in components:', '            if False:')),
    m("B-D7-states-failure-ignored", B_DIAG,
      (DDIAG, '    if not states_read:\n', '    if False:\n')),
    m("B-D8-unparsed-version-called-feature-gap", B_DIAG,
      (DDIAG, 'if version is not None and version < HELPERS_LIST_VERSION:',
       'if version is None or version < HELPERS_LIST_VERSION:')),
    m("B-D9-capability-transient-called-deployment", B_DIAG,
      (DDIAG, '            if any(item[0] == "transient" for item in classifications):', '            if False:')),
    m("B-D10-state-value-copied", B_DIAG,
      (DSVC, 'states[entity_id] = state if state in {"unavailable", "unknown"} else "available"',
       'states[entity_id] = state if type(state) is str else "available"')),
    m("B-S1-yaml-path-set-widened", B_STATIC,
      (GATEWAY, '    "homeassistant.packages", "homeassistant",\n})\n\n',
       '    "homeassistant.packages", "homeassistant", "recorder",\n})\n\n')),
    m("B-S2-core-gate-removed", B_STATIC,
      (ROUTES, '    "get_configuration_definitions": (\n'
               '        "core.basic_rest_read", "core.basic_websocket_read", "core.non_device_registry_read",\n'
               '    ),\n', '')),
    m("B-S3-tool-pre-sdk-validation-removed", B_STATIC,
      (DTOOL, '            validate_arguments(arguments)\n', '            pass\n')),
    m("B-S4-gateway-pre-sdk-validation-removed", B_STATIC,
      (ROUTING, '                    validate_hamcp132(raw_parameters)',
       '                    validate_hamcp132(raw_parameters) if tool_name != "get_configuration_definitions" else None')),
    m("B-S5-entries-domain-unchecked", B_STATIC,
      (DCON, '    if domain not in INTEGRATIONS and domain != COMPONENT_DOMAIN:\n', '    if False:\n')),
    m("B-S6-integration-set-open", B_STATIC,
      (DCON, 'all(type(item) is str and item in INTEGRATIONS for item in integrations)',
       'all(type(item) is str for item in integrations)')),
    m("B-S7-audit-copies-integrations", B_STATIC,
      (ROUTING, '"integration_count": len(integrations) if type(integrations) is list and len(integrations) <= 12 else None,',
       '"integration_count": integrations,')),
    m("B-S9-audit-copies-entity-ids", B_STATIC,
      (ROUTING, '"profile": "configuration-definitions-read-v1",\n'
                '                        "entity_count": len(entity_ids) if type(entity_ids) is list and len(entity_ids) <= 50 else None,',
       '"profile": "configuration-definitions-read-v1",\n'
       '                        "entity_count": len(entity_ids) if type(entity_ids) is list and len(entity_ids) <= 50 else None,'
       ' "entity_ids": entity_ids,')),
    m("B-S8-annotation-not-read-only", B_STATIC,
      (DTOOL, 'ToolAnnotations(readOnlyHint=True,', 'ToolAnnotations(readOnlyHint=False,')),
    m("B-A1-yaml-no-recheck-after-read", B_AUTH,
      (DSVC, '            result = None\n        if self.authority() != authority:\n'
             '            raise c.ConfigurationDefinitionsError("authority_unavailable")\n',
       '            result = None\n')),
    m("B-A2-core-read-no-recheck-after-read", B_AUTH,
      (DSVC, '        if self.authority() != authority:\n'
             '            raise c.ConfigurationDefinitionsError("authority_unavailable")\n        return raw, reason',
       '        return raw, reason')),
    m("B-A3-anonymous-accepted", B_AUTH,
      (DSVC, 'if current_caller_id() == "anonymous":', 'if False:')),
    m("B-A4-missing-authorizer-accepted", B_AUTH,
      (DSVC, '                or telemetry is None or telemetry.core_dispatch_authorizer is None\n',
       '                or telemetry is None\n')),
    m("B-B1-definition-limit-removed", B_BUDGET,
      (DSVC, '        if len(listed) > c.MAX_DEFINITIONS:', '        if False:')),
    m("B-B2-ui-entry-limit-removed", B_BUDGET,
      (DSVC, '        if len(ui_entries) > c.MAX_UI_ENTRIES:', '        if False:')),
    m("B-B3-response-budget-removed", B_BUDGET,
      (DSVC, '            if size > c.RESPONSE_BUDGET:', '            if False:')),
    m("B-B4-listed-count-stale", B_BUDGET,
      (DSVC, '                        if key == "definitions":\n', '                        if False:\n')),
    m("B-B5-trim-drops-everything", B_BUDGET,
      (DSVC, '    return max(dropped, 1)', '    return len(rows)')),
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
