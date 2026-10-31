"""HAMCP-071 mutations: copy the worktree, apply one edit, expect failure.

Modeled on the HAMCP-106 runner: every mutant is a disposable copy of this
worktree (``.git``, ``.artifacts`` and ``__pycache__`` skipped) with one or
more exact-string edits; every anchor must match exactly once, validated
before anything runs.  Per AGENTS.md (HAMCP-237) the work directory, each
mutant's TMPDIR and HOME, and the logs live under this worktree's ignored
``.artifacts/hamcp071-mut/``.  A mutant is killed when the test modules exit
non-zero without an import error; a timeout (900 s) is recorded, never a kill.

Mutant classes:
- ``A*`` inactive gate: removed at tool entry, always available, not
  rechecked at plan persistence, removed from the generic builder, removed
  from the rollback route;
- ``T*`` target refusal: integration (non-homeassistant platform) scene,
  YAML-only scene, ambiguous unique_id, disabled entry, entity_id
  mismatch, absent scene, unstable read;
- ``V*`` requested values: whitespace-only name, name bound removed, name
  stored trimmed, icon pattern weakened, icon clear ignored, scene member
  allowed, member bound removed, bare requested state allowed, non-finite
  float allowed, unknown member allowed, removal of an absent member
  allowed, empty scene allowed;
- ``W*`` write command and provider boundary: method check removed, body
  ``id`` allowed or sent, metadata or identity change allowed, command and
  target id mismatch allowed, 4xx mapped to indeterminate, 401/403 mapped to
  rejected, WebSocket vocabulary widened, send boundary mark dropped;
- ``N*`` no-op: a request that changes nothing creates a plan;
- ``D*`` disclosure: before/after swapped, unchanged members listed,
  consumers dropped or an unavailable index reported as no consumers,
  activation flag wrong, never-sent list or reload statement dropped,
  requested flag wrong;
- ``P*`` policy: protection floor unchecked, elevated planned as standard,
  unresolved device class assumed harmless, elevated classified standard,
  multi-operation scene plans not prohibited;
- ``B*`` binding, preflight and readback: preflight comparison removed or
  type-inexact, missing scene or contract error not stale, strategy
  preflight skipped, already-reached type-inexact, exactly_equal
  type-inexact, config dropped from the binding projection;
- ``R*`` rollback: drift check removed, unverified or undispatched forward
  allowed, forward fingerprint unchecked, no-change not mapped to drift,
  restore of the wrong configuration, lease recheck dropped;
- ``S*`` screening: requested name, current config, requested member
  value, consumer evidence and mapping keys unscreened;
- ``G*`` (HAMCP-106 R1-H1 pattern) resource-write gate: refusal not
  enforced, F3 context unchecked, admission unchecked, authorizer presence
  unchecked, no-F3 apply inventory entry dropped, runtime Core wiring
  dropped, F3 profile mapping dropped, apply Core status entry dropped;
- ``H*`` (HAMCP-071 R1) guard removals: the F3 write permit (presence,
  reuse, intent, target, command, consumption), read admission, the switch
  rule (planner, floor, domain match, statement), and the cached-only
  consumer reader and its gap; ``H23``-``H31`` (D4) the mode-helper rule
  (each domain, planner, floor, substring and widened match, statement,
  members before the edit).
- ``O*`` (HAMCP-071 option C) verified means the stored configuration reads
  back exactly and the registry identity is unchanged: the registry entry
  id or entity_id dropped from the binding projection, the verification
  projection reduced to identity only, the response identity unchecked; and
  the scope statement on every surface: dropped from the apply result, the
  execution task, the retained result, the plan disclosure or the catalog,
  ``loaded_state`` claimed verified, or the scope stamped on other families.

Retired by option C (the code they mutated was removed): T5, T9, T10, B9,
B10, H8-H16 and E1-E10 (the loaded-scene reads, the post-save reload latch,
poll and evidence).  H6 was re-anchored to the kept permit consumption.

Usage (from the worktree root):
    systemd-run --user --unit hamcp071-mut --property=MemoryMax=3G \\
        --property=OOMPolicy=continue --working-directory=<worktree> \\
        .artifacts/venv/bin/python docs/evidence/hamcp071-mutations/run_mutations.py \\
        [--out NAME.json] [ids...]
"""
import concurrent.futures, json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE.parents[2]  # this worktree
RT = 'hass_mcp_engineering_beta/ha_mcp_engineering/'
WORK = SRC / '.artifacts' / 'hamcp071-mut'
TMP = WORK / 'tmp'
LOGS = WORK / 'logs'
PY = str(SRC / '.artifacts' / 'venv' / 'bin' / 'python')
TIMEOUT = 900
MODULES = [
    'tests.test_hamcp071_scene_configuration',
    'tests.test_hamcp242_core_authority_itemization',
    'tests.test_hamcp164_core_gate_fail_closed',
    'tests.test_hamcp155_merge_seams',
    'tests.test_f3_configuration_identity',
]

C = 'governance/scene_configuration.py'
P = 'governance/scene_configuration_planning.py'
PO = 'governance/policy.py'
RS = 'governance/resources.py'
ST = 'f3_configuration/strategies.py'
RU = 'f3_runtime/runtime.py'
S = 'governance/service.py'
GR = 'governance/runtime.py'
AD = 'f3_configuration/adapter.py'
GW = 'f3_configuration/gateway.py'
CAP = 'capabilities.py'

_PRE = ('        if not scene_configuration.exactly_equal(\n'
        '            scene_configuration.normalize_state(observed),\n'
        '            scene_configuration.normalize_state(current),\n'
        '        ):\n')
_REACHED = ('            return scene_configuration.exactly_equal(\n'
            '                scene_configuration.normalize_state(observed),\n'
            '                scene_configuration.normalize_state(proposed_config),\n'
            '            )\n')
_FWD = ('            or record.normalized_outcome != "succeeded_verified"\n'
        '        ):\n'
        '            raise refuse("forward_not_verified")\n'
        '        if record.dispatch_intent is None or record.dispatch_count != 1:\n'
        '            raise refuse("forward_not_dispatched")\n'
        '        prior = operation.current_config\n'
        '        forward_result = operation.proposed_config\n'
        '        if (\n'
        '            not isinstance(prior, dict)\n'
        '            or not isinstance(forward_result, dict)\n'
        '            or scene_configuration_contract.state_errors(')
_DRIFT = ('            if not scene_configuration_contract.exactly_equal(\n'
          '                scene_configuration_contract.normalize_state(item.current),\n')

M = [
 ('baseline', []),
 # ---- A: inactive gate.
 ('A1_entry_gate_removed',
  [(S, '        self._scene_configuration_core()\n        try:\n'
       '            request = scene_configuration_planning.validate_request(arguments)\n',
       '        try:\n'
       '            request = scene_configuration_planning.validate_request(arguments)\n')]),
 ('A2_core_gate_always_available',
  [(S, '        if not available:\n            raise GovernanceError(\n'
       '                ErrorCode.PROVIDER_UNAVAILABLE,\n                details={\n'
       '                    "reason": scene_configuration_contract.PENDING_ADMISSION_REASON,\n',
       '        if False:\n            raise GovernanceError(\n'
       '                ErrorCode.PROVIDER_UNAVAILABLE,\n                details={\n'
       '                    "reason": scene_configuration_contract.PENDING_ADMISSION_REASON,\n')]),
 ('A3_persist_recheck_removed',
  [(S, '        self._scene_configuration_core()\n'
       '        if _before_persist is None or len(prepared.items) != 1:\n',
       '        if _before_persist is None or len(prepared.items) != 1:\n')]),
 ('A4_generic_builder_gate_removed',
  [(S, '        if scene_configuration:\n            self._scene_configuration_core()\n',
       '        if scene_configuration:\n            pass\n')]),
 # Isolated control for A3 (equivalent: the generic builder's first
 # statement repeats the check with no await between): both removed.
 ('A3A4_both_persist_gates_removed',
  [(S, '        self._scene_configuration_core()\n'
       '        if _before_persist is None or len(prepared.items) != 1:\n',
       '        if _before_persist is None or len(prepared.items) != 1:\n'),
   (S, '        if scene_configuration:\n            self._scene_configuration_core()\n',
       '        if scene_configuration:\n            pass\n')]),
 ('A5_rollback_gate_removed',
  [(RU, '        self.service._scene_configuration_core()\n', '')]),
 # ---- T: target refusal.
 ('T1_integration_scene_allowed',
  [(C, '        if entry.get("platform") != "homeassistant":\n'
       '            raise SceneConfigurationContractError("scene_configuration_not_ui_managed")\n',
       '        if False:\n'
       '            raise SceneConfigurationContractError("scene_configuration_not_ui_managed")\n')]),
 ('T2_yaml_scene_reported_absent',
  [(C, '            if entry is not None:\n                # Loaded by the homeassistant',
       '            if False:\n                # Loaded by the homeassistant')]),
 ('T3_ambiguous_allowed',
  [(C, '        if len(matches) != 1:\n'
       '            raise SceneConfigurationContractError("scene_configuration_target_ambiguous")\n',
       '        if False:\n'
       '            raise SceneConfigurationContractError("scene_configuration_target_ambiguous")\n')]),
 ('T4_disabled_allowed',
  [(C, '        if entry.get("disabled_by") is not None:\n'
       '            raise SceneConfigurationContractError("scene_configuration_target_disabled")\n',
       '        if False:\n'
       '            raise SceneConfigurationContractError("scene_configuration_target_disabled")\n')]),
 ('T6_entity_mismatch_allowed',
  [(P, '    if request.entity_id is not None and current["entity_id"] != request.entity_id:\n',
       '    if False:\n')]),
 ('T7_absent_not_refused',
  [(P, '    if current is None:\n        raise Refusal("scene_configuration_target_not_found"',
       '    if False:\n        raise Refusal("scene_configuration_target_not_found"')]),
 ('T8_read_unstable_removed',
  [(C, '        if not exactly_equal(await self.read_config(config_id), config):\n',
       '        if False:\n')]),
 # ---- V: requested values.
 ('V1_whitespace_name_allowed',
  [(C, 'isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_NAME_LENGTH',
       'isinstance(value, str) and bool(value) and len(value) <= MAX_NAME_LENGTH')]),
 ('V2_name_bound_removed',
  [(C, 'isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_NAME_LENGTH',
       'isinstance(value, str) and bool(value.strip())')]),
 ('V3_name_stored_trimmed',
  [(P, '        config["name"] = values["name"]\n',
       '        config["name"] = values["name"].strip()\n')]),
 ('V4_icon_pattern_weakened',
  [(C, '        and ICON_PATTERN.fullmatch(value) is not None\n',
       '        and ":" in value\n')]),
 ('V5_icon_clear_ignored',
  [(P, '        if values["icon"] is None:\n            config.pop("icon", None)\n',
       '        if values["icon"] is None:\n            pass\n')]),
 ('V6_scene_member_allowed',
  [(P, '            if member.startswith("scene."):\n', '            if False:\n')]),
 ('V7_member_bound_removed',
  [(P, '            or len(entities) > contract.MAX_ENTITIES\n', '')]),
 ('V8_requested_bare_state_allowed',
  [(C, '    if requested:\n        return False\n', '')]),
 ('V9_nonfinite_float_allowed',
  [(C, '        return not math.isfinite(value)\n', '        return False\n')]),
 ('V10_unknown_member_allowed',
  [(P, '        if unknown:\n            raise Refusal(UNKNOWN_ENTITIES',
       '        if False:\n            raise Refusal(UNKNOWN_ENTITIES')]),
 ('V11_absent_member_removal_unrefused',
  [(P, '            if member not in entities:\n                raise Refusal(\n'
       '                    "scene_configuration_entity_not_in_scene", field="entities"\n'
       '                )\n            del entities[member]\n',
       '            entities.pop(member, None)\n')]),
 ('V12_empty_scene_allowed',
  [(P, '    if not entities:\n        raise Refusal("scene_configuration_entities_empty"',
       '    if False:\n        raise Refusal("scene_configuration_entities_empty"')]),
 # ---- W: write command and provider boundary.
 ('W1_method_check_removed',
  [(C, '    if command["method"] != WRITE_METHOD:\n', '    if False:\n')]),
 ('W2_body_id_allowed',
  [(C, '    if type(body) is not dict or not set(body) <= BODY_KEYS or "id" in body:\n',
       '    if type(body) is not dict or not set(body) <= BODY_KEYS | {"id"}:\n')]),
 ('W3_body_sends_id',
  [(C, '    return {key: config[key] for key in config if key != "id"}\n',
       '    return dict(config)\n')]),
 ('W4_metadata_change_allowed',
  [(C, '        raise SceneConfigurationContractError("scene_configuration_metadata_changed")\n',
       '        pass\n')]),
 ('W5_identity_change_allowed',
  [(C, '            raise SceneConfigurationContractError("scene_configuration_identity_changed")\n',
       '            pass\n')]),
 ('W6_command_target_mismatch_allowed',
  [(C, '            or command.get("config_id") != config_id\n', '')]),
 ('W7_4xx_indeterminate',
  [(C, '                if 400 <= status < 500:\n', '                if False:\n')]),
 ('W8_unauthorized_as_rejected',
  [(C, '                                if status in (401, 403)\n',
       '                                if status in ()\n')]),
 ('W9_ws_vocabulary_widened',
  [(C, '        if command.get("type") not in ALLOWED_COMMANDS:\n', '        if False:\n')]),
 ('W10_send_boundary_unmarked',
  [(C, '            mark_provider_send()  # OD-6(ii): the exact send boundary\n', '')]),
 # ---- N: no-op.
 ('N1_noop_creates_plan',
  [(P, '    if contract.exactly_equal(current["config"], config):\n        raise Refusal(\n',
       '    if False:\n        raise Refusal(\n')]),
 # ---- D: disclosure.
 ('D1_before_after_swapped',
  [(P, '            "before": current["config"].get(name),\n'
       '            "after": proposed["config"].get(name),\n',
       '            "before": proposed["config"].get(name),\n'
       '            "after": current["config"].get(name),\n')]),
 ('D2_unchanged_members_listed',
  [(P, '            if contract.exactly_equal(before[member], after[member]):\n',
       '            if False:\n')]),
 ('D3_consumers_dropped',
  [(P, '        "consumers": await consumer_disclosure(gateway, current["entity_id"]),\n',
       '        "consumers": {},\n')]),
 ('D4_unavailable_index_as_no_consumers',
  [(P, '            "status": "unavailable",\n            "consumers": [],\n'
       '            "consumer_count": None,\n            "evidence_complete": False,\n',
       '            "status": "complete",\n            "consumers": [],\n'
       '            "consumer_count": 0,\n            "evidence_complete": True,\n')]),
 ('D5_activation_flag_wrong',
  [(P, '        "activated": False,\n', '        "activated": True,\n')]),
 ('D6_never_sent_dropped',
  [(P, '            "never_sent": list(contract.NEVER_SENT),\n', '')]),
 ('D7_reload_statement_dropped',
  [(P, '        "reload": RELOAD_DISCLOSURE,\n        "id_assignment"',
       '        "id_assignment"')]),
 ('D8_requested_flag_wrong',
  [(P, '            "requested": name in requested,\n', '            "requested": True,\n')]),
 # ---- P: policy.
 ('P1_protection_floor_unchecked',
  [(PO, '    if reasons != sorted(set(reasons)) or not set(floor) <= set(reasons):\n',
        '    if reasons != sorted(set(reasons)):\n')]),
 ('P2_elevated_planned_standard',
  [(P, '    if reasons:\n        statements = [\n',
       '    if False:\n        statements = [\n')]),
 ('P3_unresolved_device_class_harmless',
  [(P, '                reasons.add("device_class_unresolved")\n', '                pass\n')]),
 ('P4_elevated_classified_standard',
  [(PO, '            ApprovalPolicyClass.ELEVATED_ADMIN,\n            RiskDelta.HIGH,\n'
        '            PhysicalConsequence.INDIRECT,\n            # Policy reason codes',
        '            ApprovalPolicyClass.STANDARD_ADMIN,\n            RiskDelta.HIGH,\n'
        '            PhysicalConsequence.INDIRECT,\n            # Policy reason codes')]),
 ('P5_multi_operation_allowed',
  [(PO, '        if len(plan.operations) != 1 and any(\n'
        '            op.resource_type == scene_configuration_contract.RESOURCE_TYPE\n',
        '        if False and any(\n'
        '            op.resource_type == scene_configuration_contract.RESOURCE_TYPE\n')]),
 # ---- B: binding, preflight and readback.
 ('B1_preflight_compare_removed',
  [(RS, _PRE, _PRE.replace('        if not ', '        if False and not ', 1))]),
 ('B2_preflight_type_inexact',
  [(RS, _PRE, '        if not (\n'
              '            scene_configuration.normalize_state(observed)\n'
              '            == scene_configuration.normalize_state(current)\n'
              '        ):\n')]),
 ('B3_preflight_absent_not_stale',
  [(RS, '        if observed is None:\n            return ("stale_target_state",)\n'
        '        if not scene_configuration.exactly_equal(',
        '        if observed is None:\n            return ()\n'
        '        if not scene_configuration.exactly_equal(')]),
 ('B4_preflight_contract_error_not_stale',
  [(RS, '        except scene_configuration.SceneConfigurationContractError:\n'
        '            return ("stale_target_state",)\n',
        '        except scene_configuration.SceneConfigurationContractError:\n'
        '            return ()\n')]),
 ('B5_strategy_preflight_skipped',
  [(ST, '        check = getattr(gateway, "scene_configuration_preflight_evidence", None)\n',
        '        return ()\n')]),
 ('B6_already_reached_type_inexact',
  [(ST, _REACHED, '            return (\n'
                  '                scene_configuration.normalize_state(observed)\n'
                  '                == scene_configuration.normalize_state(proposed_config)\n'
                  '            )\n')]),
 ('B7_exactly_equal_type_inexact',
  [(C, '    return canonical(left) == canonical(right)\n', '    return left == right\n')]),
 ('B8_projection_drops_config',
  [(C, '    return json.loads(canonical({key: state[key] for key in STATE_KEYS}))\n',
       '    return json.loads(canonical({key: state[key] for key in STATE_KEYS if key != "config"}))\n')]),
 # ---- R: rollback.
 ('R1_rollback_drift_unchecked',
  [(RU, _DRIFT, _DRIFT.replace('            if not ', '            if False and not ', 1))]),
 ('R2_rollback_unverified_forward',
  [(RU, _FWD, _FWD.replace('            or record.normalized_outcome != "succeeded_verified"\n', '', 1))]),
 ('R3_rollback_undispatched_forward',
  [(RU, _FWD, _FWD.replace(
      'if record.dispatch_intent is None or record.dispatch_count != 1:', 'if False:', 1))]),
 ('R4_rollback_fingerprint_unchecked',
  [(RU, '        if record.evidence.get("resulting_state_fingerprint") != (\n'
        '            resource_fingerprint(\n'
        '                scene_configuration_contract.RESOURCE_TYPE, forward_result\n',
        '        if False and record.evidence.get("resulting_state_fingerprint") != (\n'
        '            resource_fingerprint(\n'
        '                scene_configuration_contract.RESOURCE_TYPE, forward_result\n')]),
 ('R5_rollback_no_change_not_drift',
  [(RU, '                    "scene_configuration_rollback_baseline_drift"\n'
        '                    if exc.reason_code == scene_configuration_planning.NO_CHANGE\n'
        '                    else exc.reason_code\n',
        '                    exc.reason_code\n')]),
 ('R6_restore_wrong_config',
  [(P, '        return deepcopy(request.restore_config)\n',
       '        return deepcopy(current["config"])\n')]),
 ('R7_rollback_lease_unchecked',
  [(RU, '            lease.require_current()\n'
        '            created = await self.service._create_scene_configuration_plan(\n',
        '            created = await self.service._create_scene_configuration_plan(\n')]),
 # ---- S: screening.
 ('S1_requested_name_unscreened',
  [(P, '        if _withheld(arguments["name"]):\n', '        if False:\n')]),
 ('S2_current_config_unscreened',
  [(P, '    if _withheld(current["config"]):\n', '    if False:\n')]),
 ('S3_member_value_unscreened',
  [(P, '            if _withheld(value):\n', '            if False:\n')]),
 ('S4_consumers_unscreened',
  [(P, '    screened, withheld = untrusted_text.screen_tree(deepcopy(evidence))\n',
       '    screened, withheld = deepcopy(evidence), 0\n')]),
 ('S5_mapping_keys_unscreened',
  [(P, '        return any(_withheld(key) or _withheld(item) for key, item in value.items())\n',
       '        return any(_withheld(item) for item in value.values())\n')]),
 # ---- G: resource-write gate (HAMCP-106 R1-H1 pattern).
 ('G1_write_refusal_not_enforced',
  [(C, '        refusal = self.write_refusal(config_id, command)\n        if refusal is not None:\n',
       '        refusal = self.write_refusal(config_id, command)\n        if False:\n')]),
 ('G2_write_f3_context_unchecked',
  [(C, '            return F3_CONTEXT_REQUIRED_REASON\n', '            pass\n')]),
 ('G3_write_admission_unchecked',
  [(C, '        if self.read_refusal() is not None:\n            return PENDING_ADMISSION_REASON\n',
       '        if False:\n            return PENDING_ADMISSION_REASON\n')]),
 ('G4_write_authorizer_presence_unchecked',
  [(C, '            or telemetry.core_dispatch_authorizer is None\n', '')]),
 ('G5_no_f3_inventory_entry_dropped',
  [(S, '        # HAMCP-071.\n        scene_configuration_contract.RESOURCE_TYPE,\n    }\n', '    }\n')]),
 ('G6_runtime_core_wiring_dropped',
  [(GR, '            resource_gateway.scene_configuration.core_runtime = core_runtime\n', '')]),
 ('G7_f3_profile_mapping_dropped',
  [(RU, '        scene_configuration_contract.RESOURCE_TYPE: (\n'
        '            scene_configuration_contract.PROFILE\n        ),\n', '')]),
 ('G8_apply_core_status_entry_dropped',
  [(RU, '                dashboard_metadata_contract.RESOURCE_TYPE,\n'
        '                scene_configuration_contract.RESOURCE_TYPE,\n            )\n'
        '            for operation in plan.operations\n',
        '                dashboard_metadata_contract.RESOURCE_TYPE,\n            )\n'
        '            for operation in plan.operations\n')]),
 # ---- H: HAMCP-071 R1 guard removals (permit, read admission, reload
 # evidence, switch rule, consumer cache).
 ('H1_permit_check_removed',
  [(C, '            return WRITE_PERMIT_REQUIRED_REASON\n        telemetry', '            pass\n        telemetry')]),
 ('H2_permit_reuse_allowed',
  [(C, '            or slot.consumed\n', '')]),
 ('H3_permit_intent_unchecked',
  [(C, '            or permit.intent != DISPATCH_INTENT\n', '')]),
 ('H4_permit_target_unchecked',
  [(C, '            or permit.config_id != config_id\n', '')]),
 ('H5_permit_command_unchecked',
  [(C, '            or permit.arguments_hash != stable_hash(command)\n', '')]),
 ('H6_permit_never_consumed',
  [(C, '        _WRITE_PERMIT.get().consumed = True  # type: ignore[union-attr]\n', '')]),
 ('H7_read_admission_unchecked',
  [(C, '        if self.read_refusal() is not None:\n'
       '            raise SceneConfigurationContractError(PENDING_ADMISSION_REASON)\n',
       '        if False:\n'
       '            raise SceneConfigurationContractError(PENDING_ADMISSION_REASON)\n')]),
 ('H17_switch_reason_dropped_in_planner',
  [(P, '        reasons.update(_member_rule_reasons(entity_id))\n    return sorted(reasons)\n\n\ndef classify',
       '        reasons.update(_member_rule_reasons(entity_id) - {SWITCH_REASON})\n    return sorted(reasons)\n\n\ndef classify')]),
 ('H18_switch_reason_dropped_from_floor',
  [(P, '        reasons.update(_member_rule_reasons(entity_id))\n    return sorted(reasons)\n\n\ndef _domain',
       '        reasons.update(_member_rule_reasons(entity_id) - {SWITCH_REASON})\n    return sorted(reasons)\n\n\ndef _domain')]),
 ('H19_switch_rule_on_substring',
  [(P, '    return _domain(entity_id) == SWITCH_DOMAIN\n',
       '    return isinstance(entity_id, str) and SWITCH_DOMAIN in entity_id\n')]),
 ('H20_switch_statement_dropped',
  [(P, '        if switches:\n', '        if False:\n')]),
 ('H21_consumer_reader_refreshes',
  [(GR, '                read_cached_dependency_snapshot\n            )\n',
        '                read_runtime_dependency_snapshot\n            )\n')]),
 ('H22_consumer_gap_not_disclosed',
  [(C, '        if snapshot is None:\n            evidence["evidence_gaps"] = sorted(\n',
       '        if False:\n            evidence["evidence_gaps"] = sorted(\n')]),
 # ---- H23-H31: HAMCP-071 D4 (Josh) mode-helper rule: each domain's check
 # removed, the rule removed in the planner or the floor, a substring or
 # widened domain match, the statement and the before-edit members.
 ('H23_input_boolean_domain_removed',
  [(P, 'frozenset({"input_boolean", "input_select"})', 'frozenset({"input_select"})')]),
 ('H24_input_select_domain_removed',
  [(P, 'frozenset({"input_boolean", "input_select"})', 'frozenset({"input_boolean"})')]),
 ('H25_mode_helper_check_removed',
  [(P, '    if _is_mode_helper(entity_id):\n        reasons.add(MODE_HELPER_REASON)\n', '')]),
 ('H26_mode_helper_reason_dropped_in_planner',
  [(P, '        reasons.update(_member_rule_reasons(entity_id))\n    return sorted(reasons)\n\n\ndef classify',
       '        reasons.update(_member_rule_reasons(entity_id) - {MODE_HELPER_REASON})\n    return sorted(reasons)\n\n\ndef classify')]),
 ('H27_mode_helper_reason_dropped_from_floor',
  [(P, '        reasons.update(_member_rule_reasons(entity_id))\n    return sorted(reasons)\n\n\ndef _domain',
       '        reasons.update(_member_rule_reasons(entity_id) - {MODE_HELPER_REASON})\n    return sorted(reasons)\n\n\ndef _domain')]),
 ('H28_mode_helper_rule_on_substring',
  [(P, '    return _domain(entity_id) in MODE_HELPER_DOMAINS\n',
       '    return isinstance(entity_id, str) and any(\n'
       '        domain in entity_id for domain in MODE_HELPER_DOMAINS)\n')]),
 ('H29_mode_helper_rule_on_every_input_domain',
  [(P, '    return _domain(entity_id) in MODE_HELPER_DOMAINS\n',
       '    return str(_domain(entity_id)).startswith("input_")\n')]),
 ('H30_mode_helper_statement_dropped',
  [(P, '        if mode_helpers:\n', '        if False:\n')]),
 ('H31_mode_helper_disclosure_after_edit_only',
  [(P, '    mode_helpers = mode_helper_members(members)\n',
       '    mode_helpers = mode_helper_members(config["entities"])\n')]),
 # ---- O (HAMCP-071 option C): verified means stored config exact plus
 # registry identity unchanged, and every surface states the scope.
 ('O1_projection_drops_registry_entry_id',
  [(C, '    return json.loads(canonical({key: state[key] for key in STATE_KEYS}))\n',
       '    return json.loads(canonical({key: state[key] for key in STATE_KEYS'
       ' if key != "registry_entry_id"}))\n')]),
 ('O2_projection_drops_entity_id',
  [(C, '    return json.loads(canonical({key: state[key] for key in STATE_KEYS}))\n',
       '    return json.loads(canonical({key: state[key] for key in STATE_KEYS'
       ' if key != "entity_id"}))\n')]),
 ('O3_verification_compares_identity_only',
  [(RS, '        return scene_configuration.normalize_state(config)\n',
        '        return (lambda s: s and {k: v for k, v in s.items() if k != "config"})'
        '(scene_configuration.normalize_state(config))\n')]),
 ('O4_response_identity_unchecked',
  [(RS, '        return config.get("config_id") == resource_id\n', '        return True\n')]),
 ('O5_scope_dropped_from_apply_result',
  [(RU, '        scope = self.scene_scope_fields(task)\n        if scope:\n',
        '        scope = {}\n        if scope:\n')]),
 ('O6_scope_dropped_from_execution_task',
  [(RU, '        value.update(self.scene_scope_fields(task))\n', '')]),
 ('O7_scope_dropped_from_retained_result',
  [(S, '            scope = self.f3_runtime.scene_scope_fields(task)\n',
       '            scope = {}\n')]),
 ('O8_scope_dropped_from_plan_disclosure',
  [(P, '            **contract.verification_scope_fields(),\n', '')]),
 ('O9_loaded_state_dropped_from_catalog',
  [(CAP, '        "loaded_state": "not_verified",\n', '')]),
 ('O10_loaded_state_claimed_verified',
  [(C, 'LOADED_STATE = "not_verified"\n', 'LOADED_STATE = "verified"\n')]),
 ('O11_scope_stamped_on_every_family',
  [(RU, '        if declarations and all(\n', '        if True or declarations and all(\n')]),
]

args = sys.argv[1:]
OUT = None
if '--out' in args:
    i = args.index('--out')
    OUT = args[i + 1]
    del args[i:i + 2]
only = set(args)
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
            (BASE / (OUT or 'results.json')).write_text(json.dumps(rows, indent=2) + '\n')
            print('baseline failed; mutants not run', flush=True)
            return 2
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        rows += list(pool.map(run, [m for m in M if m[0] != 'baseline']))
    (BASE / (OUT or ('results.json' if not only else 'results-partial.json'))).write_text(
        json.dumps(rows, indent=2) + '\n')
    mutants = [r for r in rows if r['name'] != 'baseline']
    print(f"done: {sum(bool(r['killed']) for r in mutants)}/{len(mutants)} killed; survivors: "
          f"{[r['name'] for r in mutants if not r['killed']]}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
