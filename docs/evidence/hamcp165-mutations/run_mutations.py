"""HAMCP-165 mutations: copy the worktree, apply one edit, expect failure."""
import concurrent.futures, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

SRC = Path('/home/josh/repos/hass-mcp-admin/.claude/worktrees/HAMCP-165-labels')
RT = 'hass_mcp_engineering_beta/ha_mcp_engineering/'
BASE = Path(__file__).resolve().parent
TMP = '/var/tmp/hamcp165-tmp'
PY = '/var/tmp/hamcp165-env/bin/python'
# The reviewer probes that exercise the runtime.  The seq-13 receipt probe
# reads git HEAD, which the .git-free copy lacks; it runs in the suites instead.
PROBES = 'tests.test_hamcp165_review1_reviewer_probes.ReviewerProbes.'
MODULES = ['tests.test_hamcp165_label_registry', 'tests.test_hamcp165_review1_corrections',
           *(PROBES + name for name in (
               'test_admitted_production_client_control',
               'test_generic_internal_builder_requires_admission',
               'test_apply_without_core_cannot_write',
               'test_withdrawn_core_apply_and_restart_read_nothing',
               'test_target_drift_observed_by_final_namespace_read_is_refused',
               'test_seq13_invalid_values_produce_refusal_receipt'))]
G = 'governance/label_registry.py'
P = 'governance/label_registry_planning.py'
S = 'governance/service.py'
RS = 'governance/resources.py'
ST = 'f3_configuration/strategies.py'
AD = 'f3_configuration/adapter.py'
RT3 = 'f3_runtime/runtime.py'
M = [
 ('baseline', []),
 # Task: the service-layer gate removed.
 ('gate_check_removed',
  [(S, '        if not available:\n            raise GovernanceError(\n                ErrorCode.PROVIDER_UNAVAILABLE,\n'
       '                details={\n                    "reason": label_registry.PENDING_ADMISSION_REASON,',
       '        if False:\n            raise GovernanceError(\n                ErrorCode.PROVIDER_UNAVAILABLE,\n'
       '                details={\n                    "reason": label_registry.PENDING_ADMISSION_REASON,')]),
 ('gate_not_checked_first',
  [(S, '        self._label_core()\n        try:\n            request = label_registry_planning.validate_request(arguments)',
       '        try:\n            request = label_registry_planning.validate_request(arguments)')]),
 # Persistence: two independent layers (the label builder's own entry check
 # and the lowest builder's post-policy recheck) each refuse; a mutant that
 # removes only the label-level layer is equivalent, so both are removed.
 ('gate_not_rechecked_at_persist',
  [(S, '        self._label_core()\n        if _before_persist is None:\n            raise GovernanceError(ErrorCode.CONFIGURATION_VALIDATION_FAILED)',
       '        if _before_persist is None:\n            raise GovernanceError(ErrorCode.CONFIGURATION_VALIDATION_FAILED)'),
   (S, '        if dormant_profiles:\n            # The final persistence boundary re-checks admission itself.\n',
       '        if False:\n            # The final persistence boundary re-checks admission itself.\n')]),
 ('config_persist_recheck_removed',
  [(S, '        if dormant_profiles:\n            # The final persistence boundary re-checks admission itself.\n',
       '        if False:\n            # The final persistence boundary re-checks admission itself.\n')]),
 ('builder_accepts_no_persist_lease',
  [(S, '        self._label_core()\n        if _before_persist is None:\n            raise GovernanceError(ErrorCode.CONFIGURATION_VALIDATION_FAILED)',
       '        self._label_core()\n        if False:\n            raise GovernanceError(ErrorCode.CONFIGURATION_VALIDATION_FAILED)'),
   (S, '        if dormant_profiles and _before_persist is None:\n',
       '        if False:\n')]),
 # Task: readback skipped.
 ('readback_skipped',
  [(RS, '            normalized_observed = (\n                normalize_resource_config(resource_type, observed)\n                if observed_available\n                else None\n            )',
        '            normalized_observed = (\n                normalized_approved if resource_type == "label_registry_entry" else\n'
        '                normalize_resource_config(resource_type, observed)\n                if observed_available\n                else None\n            )')]),
 ('generated_id_mismatch_ignored',
  [(G, '        returned = result.get("label_id") if isinstance(result, dict) else None\n        if returned != label_id:',
       '        returned = result.get("label_id") if isinstance(result, dict) else None\n        if False:')]),
 # Task: a duplicate name accepted.
 ('duplicate_name_accepted_on_create',
  [(P, '            if holders:\n                if set(holders) & touched:', '            if False:\n                if set(holders) & touched:')]),
 ('duplicate_name_accepted_on_rename',
  [(P, '                if holders:\n                    raise _refuse(\n                        "duplicate_label_request"\n'
       '                        if set(holders) & touched\n                        else "label_name_in_use",',
       '                if False:\n                    raise _refuse(\n                        "duplicate_label_request"\n'
       '                        if set(holders) & touched\n                        else "label_name_in_use",')]),
 ('name_compare_case_sensitive',
  [(G, '    return name.casefold().replace(" ", "")', '    return name')]),
 ('in_plan_duplicate_create_accepted',
  [(P, '        normalize_name(item.values["name"])', '        id(item)')]),
 # Task: stale state ignored.
 # A constant fingerprint is killed only at plan creation (like 069B's).
 ('stale_fingerprint_constant',
  [(ST, '    validation_contract = "label_registry_entry_validation_v1"\n',
        '    validation_contract = "label_registry_entry_validation_v1"\n\n'
        '    def fingerprint(self, config):\n        return "sha256:" + "0" * 64\n')]),
 # The targeted mutant: the dispatch-time stale comparison skipped for labels.
 # Stale target: the adapter's generic comparison and the review1 M3 final
 # namespace read both judge the exact target; both are removed together.
 ('stale_target_state_ignored',
  [(AD, '            if self.strategy.fingerprint(current) != (\n',
        '            if operation.resource_type != "label_registry_entry" and self.strategy.fingerprint(current) != (\n'),
   (RS, '        if target_changed:\n            return ("stale_target_state",)',
        '        if False:\n            return ("stale_target_state",)')]),
 ('namespace_change_ignored',
  [(RS, '        if snapshot.fingerprint != bindings["namespace_fingerprint"]:\n            return (label_registry.NAMESPACE_CHANGED,)',
        '        if False:\n            return (label_registry.NAMESPACE_CHANGED,)')]),
 ('preflight_evidence_skipped',
  [(ST, '        check = getattr(gateway, "label_preflight_evidence", None)\n',
        '        return ()\n        check = getattr(gateway, "label_preflight_evidence", None)\n')]),
 # Boundary extras.
 ('send_allowlist_dropped',
  [(G, '        if command.get("type") not in ALLOWED_COMMANDS:\n            raise LabelRegistryContractError("command outside the closed vocabulary")',
       '        if False:\n            raise LabelRegistryContractError("command outside the closed vocabulary")')]),
 ('cap_8_to_9',
  [(P, 'MAX_CHILDREN = 8\n', 'MAX_CHILDREN = 9\n')]),
 ('policy_contract_always_complete',
  [('governance/policy.py', '    if not 1 <= len(operations) <= LABEL_MAX_CHILDREN:\n        return False\n    kinds',
    '    return True\n    if not 1 <= len(operations) <= LABEL_MAX_CHILDREN:\n        return False\n    kinds')]),
 # Review1 H1: the F3 dispatch admission gate for dormant profiles.
 ('dispatch_admission_removed',
  [(RT3, '        adapter = self._unsigned_profile_admission(plan, adapter)\n        dispatch_guard',
         '        dispatch_guard')]),
 ('no_core_treated_as_admitted',
  [(RT3, '    if core_runtime is None:\n        return UNSIGNED_PROFILE_CORE_AUTHORITY_UNAVAILABLE',
         '    if core_runtime is None:\n        return None')]),
 ('status_failure_treated_as_admitted',
  [(RT3, '    except Exception:\n        admitted = False\n    return None if admitted',
         '    except Exception:\n        admitted = True\n    return None if admitted')]),
 ('dispatch_boundary_recheck_removed',
  [(RT3, '        async def admitted_before_dispatch() -> None:\n            self._require()\n',
         '        async def admitted_before_dispatch() -> None:\n')]),
 ('recovery_admission_removed',
  [(RT3, '        adapter = _ReadbackOnlyRecoveryAdapter(\n            self._unsigned_profile_admission(plan, adapter)\n        )',
         '        adapter = _ReadbackOnlyRecoveryAdapter(adapter)')]),
 ('held_verification_delegated',
  [(RT3, '        code = self._refusal()\n        if code is None and not (\n',
         '        code = self._refusal()\n        return await self._adapter.verify(prepared, observation)\n        if code is None and not (\n')]),
 # Review1 M1: admission first in the lowest configuration builder.
 ('configuration_builder_admission_removed',
  [(S, '        if label:\n            self._label_core()\n        for resource_type in sorted(availability_exposure):\n            self._availability_exposure_core(resource_type)\n',
       '')]),
 # Review1 M3: the final read judges the exact target.
 ('final_read_target_comparison_removed',
  [(RS, '        if target_changed:\n            return ("stale_target_state",)',
        '        if False:\n            return ("stale_target_state",)')]),
]
only = set(sys.argv[1:])
if only:
    M = [m for m in M if m[0] in only]
LOGS = Path(TMP) / 'hamcp165-mut-logs'  # logs stay outside the repository
LOGS.mkdir(exist_ok=True)


def run(case):
    name, edits = case
    with tempfile.TemporaryDirectory(prefix='hamcp165-mut-', dir=TMP) as td:
        dest = Path(td) / 'source'
        shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns('.git', '__pycache__', '.artifacts', 'core', 'logs'))
        for f, old, new in edits:
            p = dest / RT / f
            s = p.read_text()
            assert s.count(old) == 1, (name, f, s.count(old))
            p.write_text(s.replace(old, new))
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': TMP,
               'PYTHONPATH': '.:tests:hass_mcp_engineering_beta', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1'}
        log = LOGS / (name + '.log')
        with log.open('w') as fh:
            try:
                r = subprocess.run([PY, '-m', 'unittest', *MODULES], cwd=dest, env=env,
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
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    rows = list(pool.map(run, M))
(BASE / ('results.json' if not only else 'results-partial.json')).write_text(json.dumps(rows, indent=2))
