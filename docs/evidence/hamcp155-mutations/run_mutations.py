"""HAMCP-155 mutations: copy the worktree, apply one edit, expect failure.

Groups follow the four HAMCP-165 review lessons (admission at every layer,
drift at dispatch, evidence/readback, protected effects) plus rollback,
the no-default expected effect and the executor pre-intent refusal.
"""
import concurrent.futures, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

SRC = Path('/home/josh/repos/hass-mcp-admin-HAMCP-155')
RT = 'hass_mcp_engineering_beta/ha_mcp_engineering/'
BASE = Path(__file__).resolve().parent
TMP = '/var/tmp/hamcp155-tmp/mut'
PY = '/home/josh/hamcp069b-work/venv-r1/bin/python'
ALL = [
    'tests.test_hamcp155_configuration_delete_execution',
    'tests.test_hamcp155_configuration_delete_admission',
    'tests.test_hamcp155_configuration_delete_policy',
    'tests.test_hamcp155_configuration_delete_lane',
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
]
only = set(sys.argv[1:])
if only:
    M = [m for m in M if m[0] in only]
(BASE / 'logs').mkdir(exist_ok=True)


def run(case):
    name, edits = case
    with tempfile.TemporaryDirectory(prefix='155-mut-', dir=TMP) as td:
        dest = Path(td) / 'source'
        shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns('.git', '__pycache__', '.artifacts', 'core', 'logs'))
        # The lane receipt binds the source revision: commit the unmutated
        # copy to a throwaway repository so HEAD resolves as in a checkout.
        git = ['git', '-c', 'user.name=mutation', '-c', 'user.email=mutation@invalid',
               '-c', 'commit.gpgsign=false']
        for args in (['init', '-q'], ['add', '-A'], ['commit', '-q', '-m', 'unmutated']):
            subprocess.run([*git, *args], cwd=dest, check=True, stdout=subprocess.DEVNULL)
        for f, old, new in edits:
            p = dest / RT / f
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
        n = (SRC / RT / f).read_text().count(old)
        assert n == 1, (name, f, n)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    rows = list(pool.map(run, M))
(BASE / ('mutations.json' if not only else 'mutations-partial.json')).write_text(json.dumps(rows, indent=2))
