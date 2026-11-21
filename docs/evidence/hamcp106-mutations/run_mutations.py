"""HAMCP-106 mutations: copy the worktree, apply one edit, expect failure.

Modeled on the HAMCP-165 runner: every mutant is a disposable copy of this
worktree (``.git``, ``.artifacts`` and ``__pycache__`` skipped) with one or
more exact-string edits; every anchor must match exactly once, validated
before anything runs.  Per AGENTS.md (HAMCP-237) the work directory, each
mutant's TMPDIR and HOME, and the logs live under this worktree's ignored
``.artifacts/hamcp106-mut/``.  A mutant is killed when the test modules exit
non-zero without an import error; a timeout (900 s) is recorded, never a kill.

Mutant classes:
- ``A*`` inactive gate: removed, checked after input validation, not
  rechecked at plan persistence, or removed from the lowest builder;
- ``T*`` target refusal: lovelace (input layer, every layer), YAML, unknown,
  ambiguous url_path, ambiguous dashboard id;
- ``V*`` requested values: title trimmed, empty or whitespace-only title
  allowed, title bound loosened or tightened, icon pattern weakened, icon
  length bound removed, icon null-clear refused or never sent;
- ``W*`` write command and provider boundary: all four fields sent,
  command type check removed, unreviewed field accepted, vocabulary widened,
  write identity check removed, 404 mapped to indeterminate, unauthorized
  mapped to indeterminate, permission code dropped;
- ``N*`` no-op: a request that changes nothing creates a plan;
- ``D*`` disclosure: before/after swapped, require_admin and sidebar
  statements swapped, require_admin text weakened, changed/requested flags
  wrong, content disclosure dropped;
- ``P*`` policy: standard_admin changed to elevated_admin, multi-operation
  plans not prohibited, generic builder accepts the resource type;
- ``B*`` binding, preflight and readback: content fingerprint dropped from
  the binding projection or replaced by a constant, non-title fields dropped
  from the projection, read_unstable check and second list read removed,
  strategy preflight skipped, preflight comparison and missing-row checks
  removed, preflight compare ignoring content, readback skipped;
- ``R*`` rollback: wrong values or fields restored, baseline-drift check
  removed, unverified forward allowed (task check only, every check);
- ``S*`` screening: requested and current title screening removed;
- ``F*`` F3 and Core admission wiring: unsigned-profile requirement and
  entry admission dropped, pending-admission tool entry dropped, content-read
  profile dropped from F3 and static tool requirements, runtime content
  reader accepting another dashboard's fingerprint;
- ``H*`` HAMCP-255 columnar audit record: reason-code index collapsed,
  profiles or vocabularies unsorted, the last profile dropped, withheld
  profiles dropped, adapter ids re-added, an unconventional profile id never
  written, every profile id written;
- ``G*`` (review round 1, R1-H1) generic resource-write gate: refusal not
  enforced, F3 context unchecked, admission unchecked, authorizer presence
  unchecked, the no-F3 apply inventory entry dropped, the runtime's Core
  wiring dropped;
- ``M*`` (review round 1, R1-M2) unrestorable baseline: planned anyway, or
  checked for unchanged fields too.

Usage (from the worktree root):
    systemd-run --user --unit hamcp106-mut --property=MemoryMax=3G \\
        --property=OOMPolicy=continue --working-directory=<worktree> \\
        .artifacts/venv/bin/python docs/evidence/hamcp106-mutations/run_mutations.py [ids...]
"""
import concurrent.futures, json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE.parents[2]  # this worktree
RT = 'hass_mcp_engineering_beta/ha_mcp_engineering/'
WORK = SRC / '.artifacts' / 'hamcp106-mut'
TMP = WORK / 'tmp'
LOGS = WORK / 'logs'
PY = str(SRC / '.artifacts' / 'venv' / 'bin' / 'python')
TIMEOUT = 900
MODULES = [
    'tests.test_hamcp106_dashboard_metadata',
    'tests.test_seq15_contract_acceptance',
    'tests.test_hamcp255_reconciliation_audit',
    'tests.test_hamcp155_merge_seams',
    'tests.test_hamcp164_core_gate_fail_closed',
    'tests.test_hamcp242_core_authority_itemization',
    'tests.test_hamcp181_health_core_admission',
    'tests.test_hamcp191_batch3',
    'tests.test_f3_configuration_identity',
    # Review round 1 (R1-M3): the shared final persistence boundary.
    'tests.test_hamcp165_review1_corrections',
]
C = 'governance/dashboard_metadata.py'
P = 'governance/dashboard_metadata_planning.py'
S = 'governance/service.py'
PO = 'governance/policy.py'
RS = 'governance/resources.py'
GR = 'governance/runtime.py'
ST = 'f3_configuration/strategies.py'
F3 = 'f3_runtime/runtime.py'
RO = 'ha_core_readmission/routes.py'
CR = 'ha_core_readmission/runtime.py'

M = [
 ('baseline', []),
 # ---- A: inactive gate.
 ('A1_gate_check_removed',
  [(S, '        if not available:\n            raise GovernanceError(\n                ErrorCode.PROVIDER_UNAVAILABLE,\n'
       '                details={\n                    "reason": dashboard_metadata_contract.PENDING_ADMISSION_REASON,',
       '        if False:\n            raise GovernanceError(\n                ErrorCode.PROVIDER_UNAVAILABLE,\n'
       '                details={\n                    "reason": dashboard_metadata_contract.PENDING_ADMISSION_REASON,')]),
 ('A2_gate_after_validation',
  [(S, '        self._dashboard_metadata_core()\n        try:\n            request = dashboard_metadata_planning.validate_request(arguments)\n'
       '        except entity_metadata_planning.EntityMetadataRefusal as exc:\n            raise self._entity_metadata_refusal_error(exc, {}) from None\n',
       '        try:\n            request = dashboard_metadata_planning.validate_request(arguments)\n'
       '        except entity_metadata_planning.EntityMetadataRefusal as exc:\n            raise self._entity_metadata_refusal_error(exc, {}) from None\n'
       '        self._dashboard_metadata_core()\n')]),
 ('A3_persist_entry_recheck_removed',
  [(S, '        self._dashboard_metadata_core()\n        if _before_persist is None or len(prepared.items) != 1:',
       '        if _before_persist is None or len(prepared.items) != 1:')]),
 ('A4_final_persist_recheck_removed',
  [(S, '        if dormant_profiles:\n            # The final persistence boundary re-checks admission itself.\n',
       '        if False:\n            # The final persistence boundary re-checks admission itself.\n')]),
 ('A5_lowest_builder_gate_removed',
  [(S, '        if dashboard_metadata:\n            self._dashboard_metadata_core()\n', '')]),
 # ---- T: target refusals.
 ('T1_lovelace_input_refusal_removed',
  [(P, '    if url_path == contract.DEFAULT_DASHBOARD_URL_PATH:\n        raise Refusal(',
       '    if False:\n        raise Refusal(')]),
 ('T2_lovelace_refusal_removed_everywhere',
  [(P, '    if url_path == contract.DEFAULT_DASHBOARD_URL_PATH:\n        raise Refusal(',
       '    if False:\n        raise Refusal('),
   (C, '    if url_path == DEFAULT_DASHBOARD_URL_PATH:\n        raise DashboardMetadataContractError("dashboard_metadata_default_dashboard")',
       '    if False:\n        raise DashboardMetadataContractError("dashboard_metadata_default_dashboard")'),
   (C, '    if url_path == DEFAULT_DASHBOARD_URL_PATH:\n        errors.append(',
       '    if False:\n        errors.append(')]),
 ('T3_yaml_refusal_removed',
  [(C, '    if mode == "yaml":\n        raise DashboardMetadataContractError("dashboard_metadata_yaml_dashboard")',
       '    if False:\n        raise DashboardMetadataContractError("dashboard_metadata_yaml_dashboard")')]),
 ('T4_unknown_target_refusal_removed',
  [(P, '    if current is None:\n        raise Refusal("dashboard_metadata_target_not_found", kind="not_found")',
       '    if False:\n        raise Refusal("dashboard_metadata_target_not_found", kind="not_found")')]),
 ('T5_ambiguous_url_path_accepted',
  [(C, '    if len(matches) > 1:\n        raise DashboardMetadataContractError("dashboard_metadata_target_ambiguous")',
       '    if False:\n        raise DashboardMetadataContractError("dashboard_metadata_target_ambiguous")')]),
 ('T6_ambiguous_dashboard_id_accepted',
  [(C, '    if len(same_id) != 1:\n        raise DashboardMetadataContractError("dashboard_metadata_target_ambiguous")',
       '    if False:\n        raise DashboardMetadataContractError("dashboard_metadata_target_ambiguous")')]),
 # ---- V: requested values.
 ('V1_title_trimmed',
  [(P, '        values.append((name, arguments[name]))',
       '        values.append((name, arguments[name].strip() if name == "title" else arguments[name]))')]),
 ('V2_empty_title_allowed',
  [(C, '            and bool(value.strip())\n', '            and True\n')]),
 ('V3_whitespace_title_allowed',
  [(C, '            and bool(value.strip())\n', '            and bool(value)\n')]),
 ('V4_title_bound_loosened',
  [(C, '            and len(value) <= MAX_TITLE_LENGTH\n        )\n    if field == "icon":\n        return value is None or (',
       '            and len(value) <= MAX_TITLE_LENGTH + 1\n        )\n    if field == "icon":\n        return value is None or (')]),
 ('V5_title_bound_tightened',
  [(C, '            and len(value) <= MAX_TITLE_LENGTH\n        )\n    if field == "icon":\n        return value is None or (',
       '            and len(value) < MAX_TITLE_LENGTH\n        )\n    if field == "icon":\n        return value is None or (')]),
 ('V6_icon_pattern_colon_optional',
  [(C, 'ICON_PATTERN = re.compile(r"^[a-z0-9-]{1,31}:[a-z0-9-]{1,63}$")',
       'ICON_PATTERN = re.compile(r"^([a-z0-9-]{1,31}:)?[a-z0-9-]{1,63}$")')]),
 ('V7_icon_pattern_case_insensitive',
  [(C, 'ICON_PATTERN = re.compile(r"^[a-z0-9-]{1,31}:[a-z0-9-]{1,63}$")',
       'ICON_PATTERN = re.compile(r"^[a-z0-9-]{1,31}:[a-z0-9-]{1,63}$", re.I)')]),
 ('V8_icon_length_bound_removed',
  [(C, '            isinstance(value, str)\n            and len(value) <= MAX_ICON_LENGTH\n',
       '            isinstance(value, str)\n')]),
 ('V9_icon_null_clear_refused',
  [(C, '    if field == "icon":\n        return value is None or (\n            isinstance(value, str)\n            and len(value) <= MAX_ICON_LENGTH',
       '    if field == "icon":\n        return (\n            isinstance(value, str)\n            and len(value) <= MAX_ICON_LENGTH')]),
 ('V10_icon_null_never_sent',
  [(C, '        **{field: proposed[field] for field in changed},',
       '        **{field: proposed[field] for field in changed if proposed[field] is not None},')]),
 # ---- W: write command and provider boundary.
 ('W1_all_four_fields_sent',
  [(C, '        **{field: proposed[field] for field in changed},',
       '        **{field: proposed[field] for field in FIELDS},')]),
 ('W2_command_type_check_removed',
  [(C, '    if command.get("type") != UPDATE_COMMAND:\n        errors.append(',
       '    if False:\n        errors.append(')]),
 ('W3_unreviewed_command_field_accepted',
  [(C, '    if not fields <= set(FIELDS):\n', '    if False:\n')]),
 ('W4_vocabulary_widened',
  [(C, 'ALLOWED_COMMANDS = frozenset({LIST_COMMAND, UPDATE_COMMAND})',
       'ALLOWED_COMMANDS = frozenset({LIST_COMMAND, UPDATE_COMMAND, "lovelace/config/save", "lovelace/dashboards/delete"})')]),
 ('W5_write_identity_check_removed',
  [(C, '        if returned != command["dashboard_id"]:\n', '        if False:\n')]),
 ('W6_not_found_mapped_indeterminate',
  [(C, '                if details.get("status") == 404:\n', '                if False:\n')]),
 ('W7_unauthorized_mapped_indeterminate',
  [(C, '        except AuthorizationError as exc:\n            details = exc.details if isinstance(exc.details, dict) else {}\n'
       '            raise ConfigurationMutationNotDispatchedError(',
       '        except AuthorizationError as exc:\n            details = exc.details if isinstance(exc.details, dict) else {}\n'
       '            raise EntityMetadataUpdateErrorResult(')]),
 ('W8_permission_denied_code_dropped',
  [(ST, '            and details.get("reason") == dashboard_metadata.UNAUTHORIZED_REASON\n        ):\n            return ("provider_permission_denied",)',
        '            and details.get("reason") == dashboard_metadata.UNAUTHORIZED_REASON\n        ):\n            return ()')]),
 # ---- N: no-op.
 ('N1_noop_creates_plan',
  [(P, '    if not changed:\n        raise Refusal(\n            NO_CHANGE,', '    if False:\n        raise Refusal(\n            NO_CHANGE,')]),
 # ---- D: disclosure.
 ('D1_before_after_swapped',
  [(P, '            "before": current[name],\n            "after": proposed[name],',
       '            "before": proposed[name],\n            "after": current[name],')]),
 ('D2_require_admin_statements_swapped',
  [(P, '                REQUIRE_ADMIN_TRUE_DISCLOSURE\n                if after\n',
       '                REQUIRE_ADMIN_TRUE_DISCLOSURE\n                if not after\n')]),
 ('D3_sidebar_statements_swapped',
  [(P, 'SIDEBAR_HIDDEN_DISCLOSURE if not after else SIDEBAR_SHOWN_DISCLOSURE',
       'SIDEBAR_HIDDEN_DISCLOSURE if after else SIDEBAR_SHOWN_DISCLOSURE')]),
 ('D4_require_admin_false_text_weakened',
  [(P, '    "Non-admin users will be able to see and open this dashboard. This "\n'
       '    "setting controls visibility only; it does not limit which devices "\n'
       '    "non-admin users can control."\n',
       '    "Non-admin users will be able to see and open this dashboard."\n')]),
 ('D5_changed_flag_is_requested',
  [(P, '            "changed": name in changed,', '            "changed": name in requested,')]),
 ('D6_requested_flag_always_true',
  [(P, '            "requested": name in requested,', '            "requested": True,')]),
 ('D7_content_disclosure_dropped',
  [(P, '            "content": CONTENT_DISCLOSURE,', '            "content": "",')]),
 # ---- P: policy.
 ('P1_policy_elevated_admin',
  [(PO, '    return OperationPolicyClassification(\n        ApprovalPolicyClass.STANDARD_ADMIN,\n        RiskDelta.LOW,\n'
        '        PhysicalConsequence.NONE,\n        ("dashboard_metadata_standard_policy",',
        '    return OperationPolicyClassification(\n        ApprovalPolicyClass.ELEVATED_ADMIN,\n        RiskDelta.LOW,\n'
        '        PhysicalConsequence.NONE,\n        ("dashboard_metadata_standard_policy",')]),
 ('P2_multi_operation_not_prohibited',
  [(PO, '        if len(plan.operations) != 1 and any(\n            op.resource_type == dashboard_metadata_contract.RESOURCE_TYPE',
        '        if False and any(\n            op.resource_type == dashboard_metadata_contract.RESOURCE_TYPE')]),
 ('P3_generic_builder_accepts_type',
  [(S, '                else SUPPORTED_CONFIGURATION_RESOURCES\n                if _entity_metadata_context is None',
       '                else SUPPORTED_CONFIGURATION_RESOURCES | {dashboard_metadata_contract.RESOURCE_TYPE}\n'
       '                if _entity_metadata_context is None')]),
 # ---- B: binding, preflight and readback.
 ('B1_projection_drops_content_fingerprint',
  [(C, '    return {key: state[key] for key in STATE_KEYS}',
       '    return {key: state[key] for key in STATE_KEYS if key != "content_fingerprint"}')]),
 ('B2_bound_fingerprint_constant',
  [(C, '        "content_fingerprint": content_fingerprint,\n    }',
       '        "content_fingerprint": "0" * 64,\n    }')]),
 ('B3_projection_drops_non_title_fields',
  [(C, '    return {key: state[key] for key in STATE_KEYS}',
       '    return {key: state[key] for key in ("url_path", "id", "mode", "title", "content_fingerprint")}')]),
 ('B4_read_unstable_check_removed',
  [(C, '        if again != row:\n', '        if False:\n')]),
 ('B5_second_list_read_removed',
  [(C, '        again = resolve_row(await self.rows(), url_path)\n', '        again = row\n')]),
 ('B6_strategy_preflight_skipped',
  [(ST, '        check = getattr(gateway, "dashboard_metadata_preflight_evidence", None)\n',
        '        return ()\n        check = getattr(gateway, "dashboard_metadata_preflight_evidence", None)\n')]),
 ('B7_preflight_comparison_removed',
  [(RS, '        if dashboard_metadata.normalize_state(observed) != (\n            dashboard_metadata.normalize_state(current)\n        ):',
        '        if False:')]),
 ('B8_preflight_ignores_content',
  [(RS, '        if dashboard_metadata.normalize_state(observed) != (\n            dashboard_metadata.normalize_state(current)\n        ):',
        '        if {**observed, "content_fingerprint": None} != {**current, "content_fingerprint": None}:')]),
 ('B9_preflight_missing_row_passes',
  [(RS, '        if observed is None:\n            return ("stale_target_state",)\n        if dashboard_metadata.normalize_state(observed)',
        '        if observed is None:\n            return ()\n        if dashboard_metadata.normalize_state(observed)')]),
 ('B10_readback_skipped',
  [(RS, '            normalized_observed = (\n                normalize_resource_config(resource_type, observed)\n                if observed_available\n                else None\n            )',
        '            normalized_observed = (\n                normalized_approved if resource_type == "dashboard_metadata" else\n'
        '                normalize_resource_config(resource_type, observed)\n                if observed_available\n                else None\n            )')]),
 # ---- R: rollback.
 ('R1_rollback_restores_forward_values',
  [(F3, '        restore = {name: deepcopy(prior[name]) for name in changed}',
        '        restore = {name: deepcopy(forward_result[name]) for name in changed}')]),
 ('R2_rollback_restores_first_field_only',
  [(F3, '        restore = {name: deepcopy(prior[name]) for name in changed}',
        '        restore = {name: deepcopy(prior[name]) for name in changed[:1]}')]),
 ('R3_rollback_baseline_drift_check_removed',
  [(F3, '            if dashboard_metadata_contract.normalize_state(\n                item.current\n'
        '            ) != dashboard_metadata_contract.normalize_state(forward_result):',
        '            if False:')]),
 ('R4_rollback_task_state_check_removed',
  [(F3, '        operation = source_plan.operations[0]\n        if (\n            task is None\n'
        '            or task.state != ExecutionTaskState.SUCCEEDED_VERIFIED\n'
        '            or task.terminal_outcome != "succeeded_verified"\n        ):\n'
        '            raise refuse(\n                "forward_not_dispatched"\n                if task is not None\n'
        '                and task.terminal_outcome == ENTITY_METADATA_NOT_DISPATCHED_OUTCOME\n'
        '                else "forward_not_verified"\n            )\n        record = next(',
        '        operation = source_plan.operations[0]\n        if (\n            task is None\n        ):\n'
        '            raise refuse(\n                "forward_not_dispatched"\n                if task is not None\n'
        '                and task.terminal_outcome == ENTITY_METADATA_NOT_DISPATCHED_OUTCOME\n'
        '                else "forward_not_verified"\n            )\n        record = next(')]),
 ('R5_rollback_unverified_forward_allowed',
  [(F3, '        operation = source_plan.operations[0]\n        if (\n            task is None\n'
        '            or task.state != ExecutionTaskState.SUCCEEDED_VERIFIED\n'
        '            or task.terminal_outcome != "succeeded_verified"\n        ):\n'
        '            raise refuse(\n                "forward_not_dispatched"\n                if task is not None\n'
        '                and task.terminal_outcome == ENTITY_METADATA_NOT_DISPATCHED_OUTCOME\n'
        '                else "forward_not_verified"\n            )\n        record = next(',
        '        operation = source_plan.operations[0]\n        if (\n            task is None\n        ):\n'
        '            raise refuse(\n                "forward_not_dispatched"\n                if task is not None\n'
        '                and task.terminal_outcome == ENTITY_METADATA_NOT_DISPATCHED_OUTCOME\n'
        '                else "forward_not_verified"\n            )\n        record = next('),
   (F3, '            raise refuse("forward_not_verified")\n        if record.dispatch_intent is None or record.dispatch_count != 1:\n'
        '            raise refuse("forward_not_dispatched")\n        prior = operation.current_config\n'
        '        forward_result = operation.proposed_config\n',
        '            pass\n        prior = operation.current_config\n        forward_result = operation.proposed_config\n'),
   (F3, '        if record.evidence.get("resulting_state_fingerprint") != (\n            resource_fingerprint(\n'
        '                dashboard_metadata_contract.RESOURCE_TYPE, forward_result',
        '        if False and record.evidence.get("resulting_state_fingerprint") != (\n            resource_fingerprint(\n'
        '                dashboard_metadata_contract.RESOURCE_TYPE, forward_result')]),
 # ---- S: untrusted title screening.
 ('S1_requested_title_screening_removed',
  [(P, '    if isinstance(title, str) and untrusted_text.screen(title) is not None:\n',
       '    if False:\n')]),
 ('S2_current_title_screening_removed',
  [(P, '    if untrusted_text.screen(current["title"]) is not None:\n', '    if False:\n')]),
 # ---- F: F3 and Core admission wiring.
 ('F1_unsigned_profile_requirement_dropped',
  [(F3, '        dashboard_metadata_contract.RESOURCE_TYPE: dashboard_metadata_contract.PROFILE,\n', '')]),
 ('F2_f3_entry_admission_dropped',
  [(F3, '                ENTITY_METADATA_RESOURCE_TYPE,\n                dashboard_metadata_contract.RESOURCE_TYPE,\n            )',
        '                ENTITY_METADATA_RESOURCE_TYPE,\n            )')]),
 ('F3_pending_admission_tool_dropped',
  [(RO, '        DASHBOARD_METADATA_TOOL: (\n            DASHBOARD_METADATA_RESOURCE_TYPE,\n            DASHBOARD_METADATA_PROFILE,\n        ),\n', '')]),
 ('F4_f3_content_read_profile_dropped',
  [(RO, '        values.add(DASHBOARD_METADATA_PROFILE)\n        values.add("core.dashboard_configuration_read")\n',
        '        values.add(DASHBOARD_METADATA_PROFILE)\n')]),
 ('F5_static_tool_content_read_dropped',
  [(RO, '    DASHBOARD_METADATA_TOOL: (\n        "core.dashboard_configuration_read",\n        DASHBOARD_METADATA_PROFILE,\n    ),',
        '    DASHBOARD_METADATA_TOOL: (DASHBOARD_METADATA_PROFILE,),')]),
 ('F6_content_reader_target_unchecked',
  [(GR, '        if not isinstance(data, dict) or data.get("url_path") != url_path:',
        '        if not isinstance(data, dict):')]),
 # ---- H: HAMCP-255 columnar reconciliation audit record.
 ('H1_reason_index_collapsed',
  [(CR, '            reasons.index(reason),\n', '            0,\n')]),
 ('H2_profiles_unsorted',
  [(CR, '    for capability_id, disposition, reason, source, profile_id in sorted(\n'
        '        rows, key=lambda row: row[0]\n    ):',
        '    for capability_id, disposition, reason, source, profile_id in (\n'
        '        rows\n    ):')]),
 ('H3_vocabularies_unsorted',
  [(CR, '        return sorted({row[column] for row in rows}, key=lambda value: str(value))',
        '        return list(dict.fromkeys(row[column] for row in rows))')]),
 ('H4_last_profile_dropped',
  [(CR, '        "profiles": profiles,\n    }', '        "profiles": dict(list(profiles.items())[:-1]),\n    }')]),
 ('H5_withheld_profiles_dropped',
  [(CR, '        profiles[capability_id] = entry\n',
        '        if disposition.startswith("admitted"):\n            profiles[capability_id] = entry\n')]),
 ('H6_adapter_ids_readded',
  [(CR, '                "dispositions": _audit_disposition_groups(decisions),\n',
        '                "dispositions": _audit_disposition_groups(decisions),\n'
        '                "adapters": {item.capability_id: item.adapter_id for item in decisions},\n')]),
 ('H7_unconventional_profile_id_dropped',
  [(CR, '        if profile_id != _conventional_profile_id(capability_id):\n', '        if False:\n')]),
 ('H8_every_profile_id_written',
  [(CR, '        if profile_id != _conventional_profile_id(capability_id):\n', '        if True:\n')]),
 # ---- G: review round 1, R1-H1 generic resource-write gate.
 ('G1_write_refusal_not_enforced',
  [(C, '        refusal = self.write_refusal()\n        if refusal is not None:\n',
       '        refusal = self.write_refusal()\n        if False:\n')]),
 ('G2_write_f3_context_unchecked',
  [(C, '            return F3_CONTEXT_REQUIRED_REASON\n', '            pass\n')]),
 ('G3_write_admission_unchecked',
  [(C, '        if not admitted:\n            return PENDING_ADMISSION_REASON\n',
       '        if False:\n            return PENDING_ADMISSION_REASON\n')]),
 ('G4_write_authorizer_presence_unchecked',
  [(C, '            or telemetry.core_dispatch_authorizer is None\n', '')]),
 ('G5_no_f3_inventory_entry_dropped',
  [(S, '        # HAMCP-106 R1-H1.\n        dashboard_metadata_contract.RESOURCE_TYPE,\n', '')]),
 ('G6_runtime_core_wiring_dropped',
  [(GR, '            resource_gateway.dashboard_metadata.core_runtime = core_runtime\n', '')]),
 # ---- M: review round 1, R1-M2 unrestorable baseline.
 ('M1_unrestorable_baseline_planned',
  [(P, '    if unrestorable and not restore:\n', '    if False:\n')]),
 ('M2_unchanged_fields_checked_too',
  [(P, '        name for name in changed\n        if not contract.requested_value_valid',
       '        name for name in contract.FIELDS\n        if not contract.requested_value_valid')]),
]

only = set(sys.argv[1:])
if only:
    M = [m for m in M if m[0] in only]


def run(case):
    name, edits = case
    with tempfile.TemporaryDirectory(prefix='mut-', dir=TMP) as td:
        dest = Path(td) / 'source'
        shutil.copytree(SRC, dest, symlinks=True,
                        ignore=shutil.ignore_patterns('.git', '.artifacts', '__pycache__'))
        for f, old, new in edits:
            p = dest / RT / f
            s = p.read_text()
            assert s.count(old) == 1, (name, f, s.count(old))
            p.write_text(s.replace(old, new))
        scratch = Path(td) / 'tmp'
        scratch.mkdir()
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(scratch), 'PYTHONDONTWRITEBYTECODE': '1',
               'TMPDIR': str(scratch), 'PYTHONPATH': '.:tests:hass_mcp_engineering_beta',
               'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1'}
        log = LOGS / (name + '.log')
        start = time.monotonic()
        with log.open('w') as fh:
            try:
                r = subprocess.run([PY, '-m', 'unittest', *MODULES], cwd=dest, env=env,
                                   stdout=fh, stderr=subprocess.STDOUT, timeout=TIMEOUT)
                code = r.returncode
            except subprocess.TimeoutExpired:
                code = 124
        seconds = round(time.monotonic() - start, 1)
        out = log.read_text()
        row = {'name': name, 'exit': code, 'timed_out': code == 124, 'seconds': seconds,
               'tests': re.findall(r'Ran (\d+) tests?', out),
               'result': re.findall(r'^FAILED .*|^OK.*$', out, re.M),
               'import_error': 'Failed to import test module' in out or 'ImportError' in out
               or 'SyntaxError' in out
               or 'IndentationError' in out,
               'failing': sorted(set(re.findall(r'^(?:FAIL|ERROR): (\w+)', out, re.M)))}
        if name == 'baseline':
            row['killed'] = None
            row['baseline_ok'] = code == 0
        else:
            row['killed'] = code not in (0, 124) and not row['import_error']
        print(json.dumps(row), flush=True)
        return row


def main():
    for d in (TMP, LOGS):
        d.mkdir(parents=True, exist_ok=True)
    # Validate every anchor before running anything.
    for name, edits in M:
        for f, old, _new in edits:
            n = (SRC / RT / f).read_text().count(old)
            assert n == 1, (name, f, n)
    names = [m[0] for m in M]
    assert len(names) == len(set(names)), 'duplicate mutant name'
    print(f'validated {len(M)} cases', flush=True)
    rows = []
    if M and M[0][0] == 'baseline':
        rows.append(run(M[0]))
        if not rows[0]['baseline_ok']:
            (BASE / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')
            print('baseline failed; mutants not run', flush=True)
            return 2
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows += list(pool.map(run, [m for m in M if m[0] != 'baseline']))
    (BASE / ('results.json' if not only else 'results-partial.json')).write_text(
        json.dumps(rows, indent=2) + '\n')
    mutants = [r for r in rows if r['name'] != 'baseline']
    print(f"done: {sum(bool(r['killed']) for r in mutants)}/{len(mutants)} killed; survivors: "
          f"{[r['name'] for r in mutants if not r['killed']]}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
