"""HAMCP-290 mutation runner (offline, deterministic).

Copies the working tree (tracked top-level entries, without ``.git`` and
``.artifacts``) to a disposable directory under the worktree's ignored
``.artifacts/`` (HAMCP-237), checks that every fragment matches exactly once
before anything runs, then applies one mutant at a time and runs the
HAMCP-290 suites without fail-fast.  The machinery is the HAMCP-253 runner's.

Only an assertion failure counts as a kill: a mutant is killed when the
output has at least one ``FAIL:`` line.  A mutant that produces only
``ERROR:`` lines (an exception, import or syntax error) is reported as
``error_only`` and is not counted as killed.  A timeout is not a kill.

Mutants listed in ``EQUIVALENT`` are expected to survive; each carries the
reason it cannot change observable behaviour.
Mutants listed in ``FAIL_CLOSED_AT_IMPORT`` are detected before any test
can run: the runtime refuses to import.  They are reported separately (never
as killed) and accepted only when the log shows that exact import guard.

Classes:

- ``F*``: the single fallback predicate (exact ``unknown_command`` only, no
  third command, no fallback on any other failure or malformed envelope);
- ``B*``: acquisition and input bounds (frame bytes and count, record count,
  JSON strictness, deadline, argument keys and ranges, paging);
- ``P*``: the installed-only projection, sanitizer, truncation, search over
  sanitized text, completeness, fingerprint and ordering;
- ``A*``: admission (anonymous caller, signed profile, dispatch authority,
  generation re-checks, Core identity, the static requirement);
- ``R*``: the retirement of the delegated ``ha_get_hacs_info`` read (Josh,
  2026-11-08): the exposure-loop condition and its distinct disposition, the
  dispatch guard, and the retired-name declaration;
- ``C*``: the closed command vocabulary (constant payloads, no forwarded
  argument);
- ``T*``: attribution (backend, ``fallback_occurred``) and never reflecting a
  refused value;
- ``W*``: the rename wiring of the Engineering-owned
  ``get_marketplace_inventory`` (registry, route constant, capability and
  routing entries).

Usage:

    systemd-run --user --unit=hamcp290-mutations -p MemoryMax=3G \\
        -p MemorySwapMax=0 -p OOMPolicy=continue -p RuntimeMaxSec=3600 \\
        --working-directory=<worktree> /bin/bash -c \\
        '<python> docs/evidence/hamcp290-mutations/run_mutations.py \\
         --work .artifacts/hamcp290-mut \\
         --output docs/evidence/hamcp290-mutations/results.json > .artifacts/hamcp290-mut.log 2>&1'
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
E = "hass_mcp_engineering_beta/ha_mcp_engineering"
MI = f"{E}/marketplace_inventory.py"
TI = f"{E}/tools/marketplace_inventory.py"
GW = f"{E}/providers/upstream_read_gateway.py"
RT = f"{E}/ha_core_readmission/routes.py"
REG = f"{E}/tools/registry.py"
CAP = f"{E}/capabilities.py"
ROU = f"{E}/providers/routing.py"
MODULES = (
    "test_hamcp290_marketplace_inventory.py",
    "test_readonly_upstream_gateway.py",
    "test_hamcp164_core_gate_fail_closed.py",
)
# Each import-guard mutant names the exact guard text its log must show.
_IMPORT_GUARDS: dict[str, tuple[str, ...]] = {
    "A9-static-requirement-removed": (
        "RuntimeError: Static Core requirements must be non-empty: ['get_marketplace_inventory']",),
    "W1-registry-name-reverted": (
        "ha_mcp_engineering/tools/registry.py",
        "in _validate_mapping",
        "McpSdkCompatibilityError: The pinned MCP SDK compatibility contract is unavailable; "
        "startup is blocked."),
}
_FAILURE_LINE = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)", re.MULTILINE)


def m(identifier, item, *replacements):
    return {"id": identifier, "class": item, "replacements": replacements}


F = "fallback predicate widened"
B = "acquisition or input bound weakened"
P = "projection, sanitizer or paging weakened"
A = "admission removed"
C = "command vocabulary widened"
T = "attribution wrong or refused value reflected"
R = "delegated read retirement weakened"
W = "rename wiring reverted"

MUTATIONS = [
    # --- fallback --------------------------------------------------------
    m("F1-any-failure-code-falls-back", F,
      (MI, "    if code == FALLBACK_ERROR_CODE:\n        return None", "    if True:\n        return None")),
    m("F2-not-loaded-falls-back", F,
      (MI, '    if code == "not_loaded":\n        raise MarketplaceError("marketplace_not_loaded")',
       '    if code == "not_loaded":\n        return None')),
    m("F3-message-text-selects-fallback", F,
      (MI, "    if code == FALLBACK_ERROR_CODE:\n",
       '    if code == FALLBACK_ERROR_CODE or "unknown command" in str(error.get("message", "")).lower():\n')),
    m("F4-missing-error-mapping-falls-back", F,
      (MI, 'code = error.get("code") if type(error) is dict else None',
       'code = error.get("code") if type(error) is dict else FALLBACK_ERROR_CODE')),
    m("F5-hacs-unknown-command-read-as-empty", F,
      (MI, '                            if store is None:\n'
           '                                raise MarketplaceError("marketplace_unavailable", fallback_occurred=True)',
       '                            if store is None:\n                                store = []')),
    m("F6-success-with-non-list-result", F,
      (MI, 'if "error" in message or "result" not in message or type(message["result"]) is not list:',
       'if "error" in message or "result" not in message:')),
    m("F7-reply-id-not-matched", F,
      (MI, 'or type(message.get("id")) is not int or message["id"] != expected_id',
       'or type(message.get("id")) is not int')),
    m("F8-failure-with-result-accepted", F,
      (MI, '    if "result" in message:\n        raise MarketplaceError("malformed_response")\n', "")),
    # --- bounds ----------------------------------------------------------
    m("B1-reply-record-count-unbounded", B,
      (MI, '        if len(message["result"]) > MAX_RECORDS:\n            raise MarketplaceError("response_too_large")',
       '        if False:\n            raise MarketplaceError("response_too_large")')),
    m("B2-projection-record-count-unbounded", B,
      (MI, '    if len(store) > MAX_RECORDS:\n        raise MarketplaceError("response_too_large")',
       '    if False:\n        raise MarketplaceError("response_too_large")')),
    m("B3-frame-bytes-unbounded", B,
      (MI, "max_msg_size=FRAME_BYTES + 1,", "max_msg_size=0,"),
      (MI, 'if len(frame.data.encode("utf-8")) > (AUTH_BYTES if auth else FRAME_BYTES):', "if False:")),
    m("B4-auth-frame-bound-widened", B,
      (MI, "(AUTH_BYTES if auth else FRAME_BYTES)", "FRAME_BYTES")),
    m("B5-frame-count-unbounded", B,
      (MI, "                            if frames > MAX_FRAMES:", "                            if False:")),
    m("B6-duplicate-json-keys-accepted", B,
      (MI, '                if key in result:\n                    raise MarketplaceError("malformed_response")',
       '                if False:\n                    raise MarketplaceError("malformed_response")')),
    m("B7-non-finite-constants-accepted", B,
      (MI, "parse_constant=constant, parse_float=float_value)", "parse_float=float_value)")),
    m("B8-deadline-not-capped", B,
      (MI, 'deadline = min(MAX_SECONDS, float(getattr(self.settings, "ha_timeout_seconds", MAX_SECONDS)))',
       'deadline = float(getattr(self.settings, "ha_timeout_seconds", MAX_SECONDS))')),
    m("B9-limit-unbounded", B,
      (MI, 'limit=_bounded_int(arguments.get("limit", DEFAULT_LIMIT), 1, MAX_LIMIT)',
       'limit=_bounded_int(arguments.get("limit", DEFAULT_LIMIT), 1, 10**6)')),
    m("B10-negative-offset-accepted", B,
      (MI, 'offset=_bounded_int(arguments.get("offset", 0), 0, MAX_OFFSET)',
       'offset=_bounded_int(arguments.get("offset", 0), -(10**6), MAX_OFFSET)')),
    m("B11-bool-accepted-as-int", B,
      (MI, "    if type(value) is not int or not low <= value <= high:",
       "    if not isinstance(value, int) or not low <= value <= high:")),
    m("B12-query-length-unbounded", B,
      (MI, "    if type(value) is not str or len(value) > MAX_QUERY:", "    if type(value) is not str:")),
    m("B13-control-characters-accepted", B,
      (MI, '    if any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Co", "Cn") for char in value):',
       "    if False:")),
    m("B14-unknown-argument-keys-accepted", B,
      (MI, "    if type(arguments) is not dict or not set(arguments) <= ARGUMENT_KEYS:",
       "    if type(arguments) is not dict:")),
    m("B15-schema-admits-unknown-keys", B,
      (TI, '    tool.parameters["additionalProperties"] = False\n', "")),
    m("B16-page-not-limited", B,
      (MI, "    page = matches[request.offset:request.offset + request.limit]",
       "    page = matches[request.offset:]")),
    m("B17-next-offset-on-last-page", B,
      (MI, '"next_offset": end if has_more else None,', '"next_offset": end,')),
    # --- projection ------------------------------------------------------
    m("P1-uninstalled-records-listed", P,
      (MI, '        if not _strict_bool(record, "installed"):\n            continue\n',
       '        _strict_bool(record, "installed")\n')),
    m("P2-truthy-installed-accepted", P,
      (MI, '        if not _strict_bool(record, "installed"):\n',
       '        if not record.get("installed"):\n')),
    m("P3-dot-segment-full-name-accepted", P,
      (MI, '\n            or any(part in {".", ".."} for part in full_name.split("/"))):', "):")),
    m("P4-duplicate-installed-ids-accepted", P,
      (MI, '        if repository_id in seen:\n            raise MarketplaceError("malformed_response")',
       '        if False:\n            raise MarketplaceError("malformed_response")')),
    m("P5-dropped-field-projected", P,
      (MI, '    "available_version", "pending_upgrade", "custom", "description",\n)',
       '    "available_version", "pending_upgrade", "custom", "description", "local_path",\n)'),
      (MI, '            "description": _optional_text(record, "description"),\n        })',
       '            "description": _optional_text(record, "description"),\n'
       '            "local_path": _optional_text(record, "local_path"),\n        })')),
    m("P6-description-unsanitized", P,
      (MI, '("available_version", VERSION_CHARS), ("description", DESCRIPTION_CHARS))',
       '("available_version", VERSION_CHARS))')),
    m("P7-name-not-truncated", P,
      (MI, '("name", NAME_CHARS)', '("name", None)')),
    m("P8-search-over-unsanitized-text", P,
      (MI, "    matches = [record for record in records if _matches(record, request)]",
       '    raw = {item["id"]: item for item in project_installed(store)}\n'
       '    matches = [record for record in records if _matches(raw[record["id"]], request)]')),
    m("P9-known-secrets-dropped", P,
      (MI, "known_secrets=known_secrets, max_string=bound", "known_secrets=(), max_string=bound")),
    m("P10-partial-never-flagged", P,
      (MI, "    partial_reasons = [reason for reason, present in ((\"sanitized_text\", text_partial),\n"
           "                                                      (\"more_pages\", has_more)) if present]",
       "    partial_reasons = []")),
    m("P14-truncation-alone-complete", P,  # Codex D1 (#409)
      (MI, " or screen.truncated > 0 or screen.failed_closed", " or screen.failed_closed")),
    m("P15-more-pages-complete", P,  # D1 paging (#409)
      (MI, '("more_pages", has_more)) if present]', '("more_pages", False)) if present]')),
    m("P16-completeness-ignores-paging", P,  # D1 paging (#409)
      (MI, '"completeness": "partial" if partial_reasons else "complete",',
       '"completeness": "partial" if text_partial else "complete",')),
    m("P17-more-pages-warning-dropped", P,  # D1 paging (#409)
      (TI, '        if "more_pages" in value["partial_reasons"]:\n            warnings.append(_MORE_PAGES)\n', "")),
    m("P11-fingerprint-over-page-only", P,
      (MI, '"inventory_fingerprint": inventory_fingerprint(backend, records),',
       '"inventory_fingerprint": inventory_fingerprint(backend, page),')),
    m("P12-unsorted-results", P,
      (MI, "    records.sort(key=_sort_key)\n", "")),
    m("P13-category-filter-ignored", P,
      (MI, '    if request.category is not None and record["category"] != request.category:\n        return False\n', "")),
    # --- admission -------------------------------------------------------
    m("A1-anonymous-caller-admitted", A,
      (MI, '        if current_caller_id() == "anonymous":\n            raise MarketplaceError("authority_unavailable")',
       '        if False:\n            raise MarketplaceError("authority_unavailable")')),
    m("A2-profile-availability-ignored", A,
      (MI, 'if (status.get("available") is not True or type(version) is not str or not version',
       "if (type(version) is not str or not version")),
    m("A3-dispatch-authority-ignored", A,
      (MI, "\n                or telemetry is None or telemetry.core_dispatch_authorizer is None"
           "\n                or not telemetry.authorize_core_dispatch()):", "):")),
    m("A4-no-recheck-before-each-command", A,
      (MI, '                            authorize()\n                            on_dispatch(payload["type"])',
       '                            on_dispatch(payload["type"])')),
    m("A5-no-recheck-before-publishing", A,
      (MI, "                if self.authority() != authority:\n"
           '                    failure = MarketplaceError("authority_unavailable")',
       "                if False:\n"
       '                    failure = MarketplaceError("authority_unavailable")')),
    m("A6-read-recheck-noop", A,
      (MI, "            if self.authority() != authority:\n"
           '                raise MarketplaceError("authority_unavailable")',
       "            if False:\n"
       '                raise MarketplaceError("authority_unavailable")')),
    m("A7-core-identity-unchecked", A,
      (MI, '                        if auth.get("ha_version") != core_version:', "                        if False:")),
    m("A8-service-requirement-empty", A,
      (MI, "CORE_REQUIREMENTS = (PROFILE,)", "CORE_REQUIREMENTS = ()")),
    m("A9-static-requirement-removed", A,
      (RT, "    MARKETPLACE_TOOL: (MARKETPLACE_PROFILE,),", "    MARKETPLACE_TOOL: (),")),
    # --- retirement of the delegated read (Josh, 2026-11-08) -------------
    m("R1-retired-read-exposed", R,
      (GW, "                if entry.upstream_name in RETIRED_DELEGATED_READS:\n"
           "                    # HAMCP-290: a retired delegated read",
       "                if False:\n"
       "                    # HAMCP-290: a retired delegated read")),
    m("R2-retired-read-withheld-only-beside-native-route", R,
      (GW, "                if entry.upstream_name in RETIRED_DELEGATED_READS:\n"
           "                    # HAMCP-290: a retired delegated read",
       "                if entry.upstream_name in RETIRED_DELEGATED_READS and entry.upstream_name in base_names:\n"
       "                    # HAMCP-290: a retired delegated read")),
    m("R3-retired-read-withheld-only-before-9-1", R,
      (GW, "                if entry.upstream_name in RETIRED_DELEGATED_READS:\n"
           "                    # HAMCP-290: a retired delegated read",
       "                if entry.upstream_name in RETIRED_DELEGATED_READS and not catalog.server_version.startswith(\"9.\"):\n"
       "                    # HAMCP-290: a retired delegated read")),
    m("R4-retirement-disposition-not-distinct", R,
      (GW, '                            "disposition": "retired_delegated_read",',
       '                            "disposition": "core_withheld",')),
    m("R5-retired-read-not-recorded-withheld", R,
      (GW, '                            "tool": entry.upstream_name,\n'
           '                            "disposition": "retired_delegated_read",',
       '                            "tool": "retired",\n'
           '                            "disposition": "retired_delegated_read",')),
    m("R6-dispatch-guard-removed", R,
      (GW, "                or policy_entry.upstream_name in RETIRED_DELEGATED_READS\n", "")),
    m("R7-retired-names-emptied", R,
      (RT, 'RETIRED_DELEGATED_READ_TOOLS: tuple[str, ...] = ("ha_get_hacs_info",)',
       "RETIRED_DELEGATED_READ_TOOLS: tuple[str, ...] = ()")),
    m("R8-retired-read-counted-as-core-withdrawal", R,
      (GW, '            str(item.get("tool", "")) for item in core_withheld\n'
           "        } - RETIRED_DELEGATED_READS\n",
       '            str(item.get("tool", "")) for item in core_withheld\n'
           "        }\n")),
    # --- command vocabulary ----------------------------------------------
    m("C1-marketplace-command-swapped", C,
      (MI, 'MARKETPLACE_LIST_COMMAND = "marketplace/repositories/list"',
       'MARKETPLACE_LIST_COMMAND = "marketplace/info"')),
    m("C2-hacs-command-swapped", C,
      (MI, 'HACS_LIST_COMMAND = "hacs/repositories/list"', 'HACS_LIST_COMMAND = "hacs/info"')),
    m("C3-argument-forwarded", C,
      (MI, 'await websocket.send_json({"id": command_id, **payload})',
       'await websocket.send_json({"id": command_id, **payload, "category": "integration"})')),
    m("C4-dispatch-allowlist-skipped", C,
      (MI, "            if command not in COMMANDS or len(commands) >= MAX_COMMANDS:", "            if False:")),
    # --- attribution -----------------------------------------------------
    m("T1-hacs-answer-labelled-core", T,
      (MI, "                            backend = BACKEND_HACS\n", "")),
    m("T2-fallback-flag-dropped", T,
      (MI, '        page["fallback_occurred"] = backend == BACKEND_HACS', '        page["fallback_occurred"] = False')),
    m("T3-tool-metadata-fallback-dropped", T,
      (TI, '        metadata["fallback_occurred"] = value["fallback_occurred"]',
       '        metadata["fallback_occurred"] = False')),
    m("T4-refusal-after-fallback-unattributed", T,
      (MI, "            if fallback and not exc.fallback_occurred:", "            if False:")),
    # Codex D2 (#409): post-dispatch failures follow the dispatch ledger.
    m("T6-ledger-attribution-dropped", T,
      (MI, "            raise MarketplaceError(failure.reason,\n"
           "                                   fallback_occurred=HACS_LIST_COMMAND in sent) from None",
       "            raise failure")),
    m("T7-ledger-only-adds-fallback", T,
      (MI, "fallback_occurred=HACS_LIST_COMMAND in sent)",
       "fallback_occurred=failure.fallback_occurred or HACS_LIST_COMMAND in sent)")),
    m("T8-ledger-counts-first-command", T,
      (MI, "fallback_occurred=HACS_LIST_COMMAND in sent)", "fallback_occurred=bool(sent))")),
    m("T9-final-authority-exception-unclosed", T,
      (MI, '            except Exception:\n                failure = MarketplaceError("provider_error")\n', "")),
    # Codex D2 residual (#409): attribution follows the frame written.
    m("T10-attribution-from-admitted-not-sent", T,
      (MI, "fallback_occurred=HACS_LIST_COMMAND in sent)", "fallback_occurred=HACS_LIST_COMMAND in commands)")),
    m("T11-sent-recorded-before-write", T,
      (MI, '                            await websocket.send_json({"id": command_id, **payload})\n'
           '                            on_sent(payload["type"])\n',
       '                            on_sent(payload["type"])\n'
       '                            await websocket.send_json({"id": command_id, **payload})\n')),
    m("T12-reader-fallback-on-selection", T,
      (MI, "                            fallback = fallback or payload is HACS_PAYLOAD\n", ""),
      (MI, "                            backend = BACKEND_HACS\n",
       "                            fallback = True\n                            backend = BACKEND_HACS\n")),
    m("T13-sent-ledger-unordered", T,
      (MI, "            if len(sent) >= len(commands) or commands[len(sent)] != command:", "            if False:")),
    m("T5-refused-value-reflected", T,
      (TI, '            async def invalid():\n                raise MarketplaceError("invalid_arguments")',
       '            async def invalid():\n                raise ValueError(repr(arguments))')),
    # --- rename wiring ---------------------------------------------------
    m("W1-registry-name-reverted", W,
      (REG, '"get_marketplace_inventory": marketplace_inventory_tool(),',
       '"ha_get_hacs_info": marketplace_inventory_tool(),')),
    m("W2-route-constant-reverted", W,
      (RT, 'MARKETPLACE_TOOL = "get_marketplace_inventory"', 'MARKETPLACE_TOOL = "ha_get_hacs_info"')),
    m("W3-capability-name-reverted", W,
      (CAP, '        "tool": "get_marketplace_inventory",\n        "category": "evidence",',
       '        "tool": "ha_get_hacs_info",\n        "category": "evidence",')),
    m("W4-routing-key-reverted", W,
      (ROU, '    "get_marketplace_inventory": {\n        "policy_id": "marketplace_inventory_read_v1"',
       '    "ha_get_hacs_info": {\n        "policy_id": "marketplace_inventory_read_v1"')),
    m("W5-run-structured-name-reverted", W,
      (TI, '        "get_marketplace_inventory", "Returned a sanitized page',
       '        "ha_get_hacs_info", "Returned a sanitized page')),
]

# Expected survivors, each with the reason it cannot change behaviour.
EQUIVALENT: dict[str, str] = {
    "B5-frame-count-unbounded": (
        "MarketplaceReader.read calls receive() at most four times by construction "
        "(auth_required, auth_ok, at most two command results: one per send, and send "
        "runs at most twice), so the frames > MAX_FRAMES guard can never fire. It stays "
        "as defense in depth against a future edit; no input can reach it."),
}

# Not equivalent: detected, but before any test can run, so no FAIL line can exist.
FAIL_CLOSED_AT_IMPORT: dict[str, str] = {
    "A9-static-requirement-removed": (
        "ha_core_readmission/routes.py refuses to import with RuntimeError "
        "'Static Core requirements must be non-empty: [get_marketplace_inventory]', so the "
        "Engineering runtime cannot start; every suite errors at import."),
    "W1-registry-name-reverted": (
        "the reviewed FastMCP registry adapter refuses a tool whose registry key differs "
        "from its Tool.name (mcp_sdk_compatibility._validate_mapping), so tools/registry.py "
        "raises McpSdkCompatibilityError at import and the Engineering runtime cannot "
        "start; every suite errors at import."),
}


def tracked_top_level() -> set[str]:
    output = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout
    return {line.split("/", 1)[0] for line in output.splitlines() if line}


def apply(tree: Path, mutation: dict) -> dict[Path, str]:
    """Apply one mutant; return the originals. Each fragment must match once."""

    originals: dict[Path, str] = {}
    current: dict[Path, str] = {}
    for relative, old, new in mutation["replacements"]:
        path = tree / relative
        text = current.get(path, path.read_text(encoding="utf-8"))
        originals.setdefault(path, text)
        if text.count(old) != 1:
            raise SystemExit(f"{mutation['id']}: fragment matches {text.count(old)} times in {relative}")
        current[path] = text.replace(old, new)
    for path, text in current.items():
        path.write_text(text, encoding="utf-8")
    return originals


def run_tests(tree: Path, scratch: Path, timeout: int) -> tuple[int, str, float]:
    started = time.monotonic()
    outputs, code = [], 0
    for module in MODULES:
        directory = scratch / f"run-{os.getpid()}-{time.monotonic_ns()}"
        directory.mkdir(parents=True)
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", module],
                cwd=tree, capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "TMPDIR": str(directory)},
            )
        except subprocess.TimeoutExpired:
            return 124, "TIMEOUT", time.monotonic() - started
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        outputs.append(f"### {module}\n{completed.stdout}{completed.stderr}")
        code = code or completed.returncode
    return code, "\n".join(outputs), time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--only", action="append", default=[],
                        help="run only these mutation ids (repeatable); the baseline always runs")
    arguments = parser.parse_args()
    work = arguments.work.expanduser().resolve()
    if ROOT / ".artifacts" not in work.parents:
        raise SystemExit("--work must be under the worktree's .artifacts/")
    tree, log_dir, scratch = work / "tree", work / "mutation-logs", work / "tmp"
    if tree.exists():
        shutil.rmtree(tree)
    top = tracked_top_level()
    common = shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc")

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = set(common(directory, names))
        if Path(directory) == ROOT:  # untracked top-level entries stay out
            skipped |= {name for name in names if name not in top}
        return skipped

    shutil.copytree(ROOT, tree, symlinks=True, ignore=ignore)
    log_dir.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    identifiers = [item["id"] for item in MUTATIONS]
    if len(set(identifiers)) != len(identifiers):
        raise SystemExit("duplicate mutation id")
    if set(FAIL_CLOSED_AT_IMPORT) != set(_IMPORT_GUARDS):
        raise SystemExit("import-guard reasons and guard texts disagree")
    if (set(EQUIVALENT) | set(FAIL_CLOSED_AT_IMPORT)) - set(identifiers):
        raise SystemExit("equivalent or import-guard id without a mutant")
    unknown = set(arguments.only) - set(identifiers)
    if unknown:
        raise SystemExit(f"unknown mutation ids: {sorted(unknown)}")
    for mutation in MUTATIONS:  # every fragment matches once before anything runs
        for path, text in apply(tree, mutation).items():
            path.write_text(text, encoding="utf-8")

    code, output, seconds = run_tests(tree, scratch, arguments.timeout)
    (log_dir / "baseline.log").write_text(output, encoding="utf-8")
    ran = [int(value) for value in re.findall(r"^Ran (\d+) tests?", output, re.MULTILINE)]
    baseline = {"modules": list(MODULES), "returncode": code, "tests": sum(ran),
                "seconds": round(seconds, 1)}
    print(f"baseline rc={code} tests={sum(ran)} ({seconds:.0f}s)", flush=True)
    if code != 0:
        print(output[-4000:])
        return 2

    results = []
    for mutation in MUTATIONS:
        if arguments.only and mutation["id"] not in arguments.only:
            continue
        originals = apply(tree, mutation)
        try:
            code, output, seconds = run_tests(tree, scratch, arguments.timeout)
        finally:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")
        found = _FAILURE_LINE.findall(output)
        assertions = [f"FAIL {name} ({where})" for kind, name, where in found if kind == "FAIL"]
        errors = [f"ERROR {name} ({where})" for kind, name, where in found if kind == "ERROR"]
        timed_out = output == "TIMEOUT"
        killed = bool(assertions) and not timed_out
        verdict = ("killed" if killed else "timeout" if timed_out
                   else "error_only" if errors or code != 0 else "survived")
        log_path = log_dir / f"{mutation['id']}.log"
        log_path.write_text(output, encoding="utf-8")
        results.append({
            "id": mutation["id"], "class": mutation["class"],
            "replacements": [list(item) for item in mutation["replacements"]],
            "verdict": verdict, "killed": killed, "returncode": code,
            "assertion_failures": len(assertions), "errors": len(errors),
            "first_assertion_failures": assertions[:3], "first_errors": errors[:3],
            "expected_equivalent": mutation["id"] in EQUIVALENT,
            "equivalence_reason": EQUIVALENT.get(mutation["id"]),
            "log_path": log_path.relative_to(work).as_posix(), "seconds": round(seconds, 1),
        })
        print(f"{verdict.upper():10} {mutation['id']:52} {(assertions or errors)[:1]}", flush=True)

    not_killed = [item["id"] for item in results if not item["killed"]]
    # An import-guard mutant counts only when its log shows that exact guard.
    import_guard = [item["id"] for item in results if item["id"] in FAIL_CLOSED_AT_IMPORT
                    and item["verdict"] == "error_only"
                    and all(text in (work / item["log_path"]).read_text(encoding="utf-8")
                            for text in _IMPORT_GUARDS[item["id"]])]
    unexpected = [identifier for identifier in not_killed
                  if identifier not in EQUIVALENT and identifier not in import_guard]
    document = {
        "baseline": baseline, "mutation_count": len(results),
        "killed": sum(item["killed"] for item in results),
        "not_killed": not_killed, "equivalent": {key: EQUIVALENT[key] for key in not_killed if key in EQUIVALENT},
        "fail_closed_at_import": {key: FAIL_CLOSED_AT_IMPORT[key] for key in import_guard},
        "unexpected_survivors": unexpected,
        "kill_rule": "at least one FAIL (assertion) line; ERROR-only and timeouts are not kills",
        "python": platform.python_version(), "results": results,
    }
    arguments.output.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
