"""HAMCP-155 mutations: copy the worktree, apply one edit, expect failure.

Groups follow the four HAMCP-165 review lessons (admission at every layer,
drift at dispatch, evidence/readback, protected effects) plus rollback,
the no-default expected effect and the executor pre-intent refusal.
Review 1 adds M3 (entry admission before persistence), M1 (captured entity
identity), M2 (bounded config-check allowlist) and the dormant wiring. The
fff324b merge adds the sm_ seam mutants (planning-family exclusivity, one
diff branch per family, entry admission over every dormant profile). A path
starting with "/" is repository-relative; every other path is under RT.
"""
import concurrent.futures, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

SRC = Path('/home/josh/repos/hass-mcp-admin-HAMCP-155')
RT = 'hass_mcp_engineering_beta/ha_mcp_engineering/'
BASE = Path(__file__).resolve().parent
TMP = '/var/tmp/hamcp155-tmp/mut'
PY = '/home/josh/repos/hass-mcp-admin-HAMCP-155/venv-r1/bin/python'
ALL = [
    'tests.test_hamcp155_configuration_delete_execution',
    'tests.test_hamcp155_configuration_delete_admission',
    'tests.test_hamcp155_configuration_delete_policy',
    'tests.test_hamcp155_configuration_delete_lane',
    'tests.test_hamcp155_review1_probes',
    'tests.test_hamcp069b_review1_corrections',
    'tests.test_hamcp155_merge_seams',
]
S = 'governance/service.py'
D = 'f3_configuration/delete.py'
A = 'f3_configuration/adapter.py'
C = 'governance/configuration_delete.py'
P = 'governance/configuration_delete_planning.py'
PE = 'governance/physical_effects.py'
PO = 'governance/policy.py'
RU = 'f3_runtime/runtime.py'
EXE = 'f3/executor.py'
RO = 'ha_core_readmission/routes.py'
LN = '/scripts/configuration_delete_contract_acceptance.py'
AL = '/scripts/alarmo_inspection_contract_acceptance.py'
HN = '/scripts/real_ha_contract_tests.py'
M = [
 ('baseline', []),
 # Lesson 1: admission at every layer.
 ('l1_service_first_check_removed',
  [(S, '        self._configuration_delete_core()\n        try:\n            request = configuration_delete_planning.validate_request(arguments)',
       '        try:\n            request = configuration_delete_planning.validate_request(arguments)')]),
 ('l1_service_core_always_available',
  [(S, '                and core.route_status((configuration_delete.PROFILE,)).get(\n                    "available"\n                )\n                is True',
       '                or True')]),
 ('l1_delete_builder_check_removed',
  [(S, '        self._configuration_delete_core()\n        if _before_persist is None:\n            raise GovernanceError(ErrorCode.CONFIGURATION_VALIDATION_FAILED)\n        kinds',
       '        if _before_persist is None:\n            raise GovernanceError(ErrorCode.CONFIGURATION_VALIDATION_FAILED)\n        kinds')]),
 ('l1_generic_builder_check_removed',
  [(S, '            # HAMCP-155: admission first for the internal builder too, before\n            # any read or persistence.\n            self._configuration_delete_core()',
       '            pass')]),
 ('l1_runtime_admission_always_true',
  [(RU, '        return isinstance(status, dict) and status.get("available") is True',
        '        return True')]),
 ('l1_adapter_preflight_admission_removed',
  [(D, '        if not self._admitted():\n            return self._preflight_rejected(',
       '        if False:\n            return self._preflight_rejected(')]),
 ('l1_predispatch_admission_removed',
  [(D, '        if not self._admitted():\n            return (CORE_ADMISSION_UNAVAILABLE,)',
       '        if False:\n            return (CORE_ADMISSION_UNAVAILABLE,)')]),
 ('l1_observe_admission_removed',
  [(D, '        if not self._admitted():\n            return self._incomplete_observation(',
       '        if False:\n            return self._incomplete_observation(')]),
 ('l1_admission_callback_ignored',
  [(D, '            return self._admission() is True', '            return True')]),
 # Lesson 2: drift at dispatch.
 ('l2_predispatch_drift_removed',
  [(D, '        _state, state_code = await self._live_state_code(operation)\n        if state_code != "state_matches_plan":\n            return (state_code,)\n        return ()',
       '        return ()')]),
 ('l2_stale_pre_image_accepted',
  [(D, '            return current, "stale_target_state"\n        return current, "state_matches_plan"',
       '            return current, "state_matches_plan"\n        return current, "state_matches_plan"')]),
 ('l2_registry_entry_change_ignored',
  [(D, '            entry.get("entity_id") != intent["entity_id"]\n            or entry.get("id") != intent["registry_entry_id"]',
       '            False')]),
 ('l2_consumer_evidence_change_ignored',
  [(D, '        if consumer_fingerprint != intent["consumer_evidence_fingerprint"]:',
       '        if False:')]),
 # Lesson 3: every claimed read is asserted.
 ('l3_absence_ignores_state',
  [(C, '        and facts.get("registry_entry_absent") is True\n        and facts.get("state_absent") is True',
       '        and facts.get("registry_entry_absent") is True')]),
 ('l3_absence_ignores_registry',
  [(C, '        and facts.get("registry_entry_absent") is True\n        and facts.get("state_absent") is True',
       '        and facts.get("state_absent") is True')]),
 ('l3_readback_skipped',
  [(D, '        absent = delete_contract.absence_proven(facts)\n',
       '        absent = True\n        facts = {"config_absent": True, "registry_entry_absent": True, "state_absent": True}\n')]),
 ('l3_config_presence_ignored',
  [(D, '                if config is not None:\n                    present_fingerprint',
       '                if False:\n                    present_fingerprint')]),
 # Lesson 4: a delete is classified by the effects it removes.
 ('l4_unsupported_action_is_no_effect',
  [(PE, '    if action not in SUPPORTED_ACTIONS:\n        # HAMCP-155',
        '    if action not in SUPPORTED_ACTIONS:\n        return NONE_COMPARISON\n        # HAMCP-155')]),
 ('l4_delete_after_side_kept',
  [(PE, '        normalized_proposed = None\n', '        normalized_proposed = normalized_current\n')]),
 ('l4_policy_delete_branch_removed',
  [(PO, '    if operation.action == CONFIGURATION_DELETE_ACTION:\n        return _configuration_delete_operation_policy(operation)\n',
        '')]),
 ('l4_protected_basis_dropped',
  [(P, '    if comparison.protected:\n        basis.append(BASIS_PROTECTED_EFFECT)',
       '    if False:\n        basis.append(BASIS_PROTECTED_EFFECT)')]),
 ('l4_dependency_basis_dropped',
  [(P, '    if dependency_reasons:\n        basis.append(BASIS_PROTECTED_DEPENDENCY)',
       '    if False:\n        basis.append(BASIS_PROTECTED_DEPENDENCY)')]),
 ('l4_planner_never_elevated',
  [(P, '"policy": "elevated" if basis else "standard",', '"policy": "standard",')]),
 ('l4_ordinary_delete_low_risk',
  [(P, 'level=RiskLevel.HIGH if elevated else RiskLevel.MEDIUM,', 'level=RiskLevel.HIGH if elevated else RiskLevel.LOW,')]),
 ('l4_removed_effect_statement_dropped',
  [(P, '    if removed:\n        statements.append(', '    if False:\n        statements.append(')]),
 ('l4_policy_marker_basis_unchecked',
  [(PO, '    if tuple(item for item in basis.split(",") if item) != expected:\n        return incomplete',
        '    if False:\n        return incomplete')]),
 ('l4_mixed_delete_plan_allowed',
  [(PO, '        op.action != CONFIGURATION_DELETE_ACTION\n        or op.resource_type not in CONFIGURATION_DELETE_RESOURCE_TYPES\n        or op.depends_on',
        '        op.resource_type not in CONFIGURATION_DELETE_RESOURCE_TYPES')]),
 # Rollback recreates the exact captured pre-image, never over a live target.
 ('rb_recreate_from_intent',
  [(RU, '                    "proposed_config": deepcopy(pre_image),', '                    "proposed_config": deepcopy(operation.proposed_config),')]),
 ('rb_live_target_overwritten',
  [(RU, '            if current is not None:\n                # Recreated or replaced since: never overwrite it.',
        '            if False:\n                # Recreated or replaced since: never overwrite it.')]),
 ('rb_unverified_child_restored',
  [(RU, '                or record.normalized_outcome != "succeeded_verified"\n                or record.dispatch_intent is None\n                or record.dispatch_count != 1\n                or not eligible',
        '                or not eligible')]),
 # No adapter defaults an expected effect.
 ('fx_base_adapter_defaults_update',
  [(A, '        raise ValueError("configuration action has no reviewed effect")', '        return "configuration_resource_updated"')]),
 ('fx_delete_adapter_defaults_delete',
  [(D, '        raise ValueError("configuration delete adapter supports delete only")', '        return delete_contract.EXPECTED_EFFECT')]),
 # Executor: the adapter's final pre-intent refusal is stored with its codes.
 ('ex_pre_intent_refusal_ignored',
  [(EXE, '                else self._pre_intent_refusal_codes(dispatch)', '                else None')]),
 ('ex_pre_intent_codes_dropped',
  [(EXE, '            diagnostic_codes=codes or (code,),', '            diagnostic_codes=(code,),')]),
 # DELETE stays reachable only through the closed gateway.
 ('gw_generic_write_deletes',
  [('governance/resources.py', '        raise ValueError("action must be create or update")',
    '        return await self.configuration_delete.delete(resource_type, resource_id)')]),
 # Review 1 M3: an unadmitted first apply refuses before any persistence.
 ('m3_entry_refusal_removed',
  [(RU, '        if existing is None:\n            self._refuse_unadmitted_entry(plan)\n',
        '        if existing is None:\n')]),
 ('m3_entry_delete_profile_dropped',
  [(RO, '        CONFIGURATION_DELETE_PROFILE,\n        LABEL_PROFILE,\n    }\n)',
        '        LABEL_PROFILE,\n    }\n)')]),
 ('m3_entry_availability_profile_dropped',
  [(RO, '        AVAILABILITY_PROFILE,\n        EXPOSURE_PROFILE,\n        CONFIGURATION_DELETE_PROFILE,\n',
        '        EXPOSURE_PROFILE,\n        CONFIGURATION_DELETE_PROFILE,\n')]),
 ('m3_entry_status_ignored',
  [(RU, '        try:\n            return core.route_status((profile,)).get("available") is True',
        '        try:\n            return True')]),
 # Review 1 M1: absence is proven at the entity identity captured before the delete.
 ('m1_absent_ignores_entity_identity',
  [(LN, '            and type(readback["entity_id"]) is str\n            and entity_of(readback["entity_id"].split(".", 1)[0], readback["entity_id"]))',
        '            )')]),
 ('m1_absent_ignores_registry_identity',
  [(LN, '            and readback["registry_entity_id"] is None and readback["state_status"] == 404',
        '            and readback["state_status"] == 404')]),
 ('m1_state_route_guesses',
  [(LN, '    return rest_path("/states/{}", entity_id, kinds=("entity_id",))',
        '    return rest_path("/states/{}", entity_id.split(".", 1)[0] + "." + "hamcp155_guessed", kinds=("entity_id",))')]),
 ('m1_readback_guesses_without_capture',
  [(LN, '        if registered is None:\n            raise ValueError("No captured or registered entity identity to read back")',
        '        if registered is None:\n            registered = resource_type + "." + target_id')]),
 ('m1_readback_identity_unbound',
  [(LN, '        assert value["readback"]["entity_id"] == value["entity_id"]\n', '')]),
 ('m1_recreate_identity_unbound',
  [(LN, '        assert (scenarios["recreate_" + resource_type]["entity_id"]\n                == scenarios["delete_" + resource_type]["entity_id"])',
        '        pass')]),
 # Review 1 M2: only the exact config check is allowlisted, and it is bounded.
 ('m2_config_check_counted_as_write',
  [(LN, '        if (method, str(path)) == CHECK_CONFIG:', '        if False:')]),
 ('m2_any_post_allowlisted',
  [(LN, '        if (method, str(path)) == CHECK_CONFIG:', '        if method == "POST":')]),
 ('m2_recreate_checks_unbounded',
  [(LN, '        assert value["config_checks"] == RECREATE_CONFIG_CHECKS\n', '')]),
 ('m2_delete_checks_unbounded',
  [(LN, '        assert value["write_routes_bound"] is True and value["config_checks"] == 0\n',
        '        assert value["write_routes_bound"] is True\n')]),
 # Review 1 wiring: dormant never runs; enabled must pass and be retained.
 ('wr_harness_runs_while_dormant',
  [(HN, '            if os.environ.get("REAL_HA_SEQ13_LANES") == "enabled":\n                import seq13_contract_acceptance as seq13',
        '            if True:\n                import seq13_contract_acceptance as seq13')]),
 ('wr_harness_failure_tolerated',
  [(HN, '                if seq13_results["seq13_configuration_delete"].get("result") != "PASS":',
        '                if False:')]),
 ('wr_harness_receipt_not_retained',
  [(HN, '            retained.update(seq13_results)\n', '')]),
 ('wr_cleanup_enabled_check_bypassed',
  [(AL, '    if key not in value or standalone is None or not Path(standalone).is_file():\n        raise ValueError("Required seq-13 configuration delete receipt missing")',
        '    if key not in value:\n        return {}')]),
 ('wr_cleanup_byte_identity_unchecked',
  [(AL, '    if Path(standalone).read_bytes() != canonical:', '    if False:')]),
 ('wr_cleanup_dormant_receipt_accepted',
  [(AL, '        if key in value:\n            raise ValueError("Seq-13 configuration delete receipt retained while dormant")',
        '        if False:\n            raise ValueError("Seq-13 configuration delete receipt retained while dormant")')]),
 # Merge seams (fff324b union): four exclusive planning families, one diff
 # branch each, and entry admission over every dormant profile.
 ('sm_family_count_two_allowed',
  [(S, '            )\n            > 1\n', '            )\n            > 2\n')]),
 ('sm_family_count_drops_delete',
  [(S, '                    _label_context,\n                    _configuration_delete_context,\n                )\n            )\n            > 1',
       '                    _label_context,\n                )\n            )\n            > 1')]),
 ('sm_label_rollback_allowed',
  [(S, '            or (_label_context is not None and _rollback_context is not None)\n', '')]),
 ('sm_delete_rollback_allowed',
  [(S, '            or (\n                _configuration_delete_context is not None\n                and _rollback_context is not None\n            )\n', '')]),
 ('sm_delete_diff_branch_skipped',
  [(S, '                if _configuration_delete_context is not None:\n                    item = _configuration_delete_context.item(operation_id)\n                    diff = {',
       '                if False:\n                    item = _configuration_delete_context.item(operation_id)\n                    diff = {')]),
 ('sm_availability_diff_branch_skipped',
  [(S, '                elif _availability_exposure_context is not None:\n                    item = _availability_exposure_context.item(operation_id)\n                    diff = {',
       '                elif False:\n                    item = _availability_exposure_context.item(operation_id)\n                    diff = {')]),
 ('sm_entry_drops_label',
  [(RO, '        CONFIGURATION_DELETE_PROFILE,\n        LABEL_PROFILE,\n    }', '        CONFIGURATION_DELETE_PROFILE,\n    }')]),
 ('sm_entry_skips_contract_v2',
  [(RU, '        if plan.contract_version == 2:\n            for profile in f3_entry_admission_profiles(',
        '        if False:\n            for profile in f3_entry_admission_profiles(')]),
]
only = set(sys.argv[1:])
if only:
    M = [m for m in M if m[0] in only]
(BASE / 'logs').mkdir(exist_ok=True)


def run(case):
    name, edits = case
    with tempfile.TemporaryDirectory(prefix='155-mut-', dir=TMP) as td:
        dest = Path(td) / 'source'
        shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns('.git', '__pycache__', '.artifacts', 'core', 'logs', 'venv-r1'))
        # The lane receipt binds the source revision: commit the unmutated
        # copy to a throwaway repository so HEAD resolves as in a checkout.
        git = ['git', '-c', 'user.name=mutation', '-c', 'user.email=mutation@invalid',
               '-c', 'commit.gpgsign=false']
        for args in (['init', '-q'], ['add', '-A'], ['commit', '-q', '-m', 'unmutated']):
            subprocess.run([*git, *args], cwd=dest, check=True, stdout=subprocess.DEVNULL)
        for f, old, new in edits:
            p = dest / f[1:] if f.startswith('/') else dest / RT / f
            s = p.read_text()
            assert s.count(old) == 1, (name, f, s.count(old))
            p.write_text(s.replace(old, new))
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': TMP,
               'PYTHONPATH': '.:tests:hass_mcp_engineering_beta', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1'}
        log = BASE / 'logs' / (name + '.log')
        with log.open('w') as fh:
            try:
                r = subprocess.run([PY, '-m', 'unittest', *ALL], cwd=dest, env=env,
                                   stdout=fh, stderr=subprocess.STDOUT, timeout=900)
                code = r.returncode
            except subprocess.TimeoutExpired:
                code = 124
        out = log.read_text()
        row = {'name': name, 'exit': code, 'tests': re.findall(r'Ran (\d+) tests?', out),
               'result': re.findall(r'^FAILED .*|^OK.*$', out, re.M),
               'import_error': 'Failed to import test module' in out or 'ImportError' in out,
               'failing': sorted(set(re.findall(r'^(?:FAIL|ERROR): (\w+)', out, re.M)))}
        row['killed'] = (code != 0 and not row['import_error']) if name != 'baseline' else code == 0
        print(json.dumps(row), flush=True)
        return row


# Validate every anchor before running anything.
for name, edits in M:
    for f, old, _new in edits:
        n = (SRC / f[1:] if f.startswith('/') else SRC / RT / f).read_text().count(old)
        assert n == 1, (name, f, n)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    rows = list(pool.map(run, M))
(BASE / ('mutations.json' if not only else 'mutations-partial.json')).write_text(json.dumps(rows, indent=2))
