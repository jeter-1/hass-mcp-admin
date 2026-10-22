"""HAMCP-069B mutations: copy the worktree, apply one edit, expect failure."""
import concurrent.futures, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

SRC = Path('/home/josh/repos/hass-mcp-admin-HAMCP-069B')
RT = 'hass_mcp_engineering_beta/ha_mcp_engineering/'
BASE = Path(__file__).resolve().parent
TMP = '/home/josh/hamcp069b-work/tmp-evidence'
EX = 'tests.test_hamcp069b_entity_availability_exposure_execution'
AD = 'tests.test_hamcp069b_entity_availability_exposure_admission'
R1 = 'tests.test_hamcp069b_review1_corrections'
BOTH = [EX, AD, R1]  # HAMCP-069B review 1: every mutant runs all three modules.
G = 'governance/entity_availability_exposure.py'
P = 'governance/entity_availability_exposure_planning.py'
S = 'governance/service.py'
M = [
 ('baseline', [], BOTH),
 ('skip_readback',
  [(G, '    if not tolerated:\n        return observed, []', '    if not tolerated:\n        return approved, []')], BOTH),
 ('tolerate_any_new_exposure_key',
  [(G, '    for assistant in TOLERATED_NEW_KEY_ASSISTANTS:',
       '    for assistant in ("cloud.alexa", "cloud.google_assistant", "conversation"):')], BOTH),
 ('drop_protected_floor',
  [(P, '    if floor is not None:\n        reasons.append(floor)', '    if False:\n        reasons.append(floor)')], BOTH),
 ('drop_safety_sensor_protection',
  [(P, '        reasons.append(f"safety_sensor_{device_class}")', '        pass')], BOTH),
 ('drop_feeds_protected_automation',
  [(P, '            reasons.add(f"feeds_protected_automation:{source_id}")', '            pass')], BOTH),
 ('planner_never_elevated',
  [(P, '"policy": "elevated" if basis else "standard",', '"policy": "standard",')], BOTH),
 ('ignore_stale_state',
  [('f3_configuration/strategies.py',
    '            raise ValueError("entity availability and exposure are update-only")\n        super().__init__(action)\n',
    '            raise ValueError("entity availability and exposure are update-only")\n        super().__init__(action)\n\n'
    '    def fingerprint(self, config):\n        return "sha256:" + "0" * 64\n')], BOTH),
 ('wrong_assistant_in_command',
  [(G, '        "assistants": [assistant],', '        "assistants": ["conversation"],')], BOTH),
 ('planner_allows_multiple_config_entries',
  [(P, '        if len(groups) != 1:', '        if len(groups) < 1:')], BOTH),
 ('policy_allows_multiple_config_entries',
  [('governance/policy.py', '        if len(entries) != 1:\n            return False\n    return True',
    '        if len(entries) < 1:\n            return False\n    return True')], BOTH),
 ('accept_unregistered_entity',
  [(P, '    if current is None:\n        # OD-10', '    if False:\n        # OD-10')], BOTH),
 ('allow_alexa',
  [(P, '            raise Refusal("assistant_not_supported", field="assistant")', '            pass')], BOTH),
 ('cap_8_to_9',
  [(P, 'MAX_ENTITIES = 8\n', 'MAX_ENTITIES = 9\n')], BOTH),
 ('skip_stale_check_at_preflight',
  [('f3_configuration/adapter.py',
    '                return current, "resource_identity_mismatch"\n            if self.strategy.fingerprint(current) != (',
    '                return current, "resource_identity_mismatch"\n'
    '            if operation.resource_type in ("entity_registry_availability", "entity_assistant_exposure"):\n'
    '                return current, "state_matches_plan"\n'
    '            if self.strategy.fingerprint(current) != (')], BOTH),
 ('drop_send_allowlist',
  [(G, '        if command.get("type") not in ALLOWED_COMMANDS:', '        if False:')], BOTH),
 # Reviewer (review 1): drop the valve protected floor.
 ('review_valve_floor_excluded',
  [('governance/physical_effects.py',
    '    if domain in SENSITIVE_ENTITY_DOMAINS:\n        return f"sensitive_domain_{domain}"',
    '    if domain in SENSITIVE_ENTITY_DOMAINS and domain != "valve":\n        return f"sensitive_domain_{domain}"')], BOTH),
 # H1: incomplete evidence treated as complete.
 ('r1_predicate_always_complete',
  [(P, '    if snapshot is None:\n        return ["dependency_index_unavailable"]\n    gaps: set[str] = set()',
       '    return []\n    gaps: set[str] = set()')], BOTH),
 ('r1_predicate_ignores_overflow',
  [(P, '    if getattr(snapshot, "obligation_overflow_count", 0):\n        gaps.add("obligation_ledger_truncated")',
       '    if False:\n        gaps.add("obligation_ledger_truncated")')], BOTH),
 ('r1_predicate_ignores_opaque_consumer',
  [(P, '        elif outcome == "bounded_semantic_opaque":\n            gaps.add("unresolved_dynamic_consumer")', '')], BOTH),
 ('r1_predicate_ignores_script_evidence',
  [(P, '    if scripts is None or scripts.coverage.completeness == "unavailable":',
       '    if False:')], BOTH),
 # M1: the service-layer gate removed.
 ('r1_service_gate_check_removed',
  [(S, '        if not available:\n            raise GovernanceError(\n                ErrorCode.PROVIDER_UNAVAILABLE,',
       '        if False:\n            raise GovernanceError(\n                ErrorCode.PROVIDER_UNAVAILABLE,')], BOTH),
 ('r1_service_gate_removed_entirely',
  [(S, '        lease = self._availability_exposure_planning_lease(request)',
       '        from ..f3_runtime.runtime import _rollback_planning_lease\n        lease = _rollback_planning_lease(None, ())'),
   (S, '        self._availability_exposure_core(request.resource_type)\n        if _before_persist is None:\n            raise GovernanceError(ErrorCode.CONFIGURATION_VALIDATION_FAILED)',
       '        pass'),
   ('f3_runtime/runtime.py', '        self.service._availability_exposure_core(request.resource_type)', '        pass')], BOTH),
 ('r1_persist_lease_not_forwarded',
  [(S, '            _availability_exposure_context=prepared,\n            _before_persist=_before_persist,\n',
       '            _availability_exposure_context=prepared,\n')], BOTH),
]
only = set(sys.argv[1:])
if only:
    M = [m for m in M if m[0] in only]
(BASE / 'logs').mkdir(exist_ok=True)

def run(case):
    name, edits, modules = case
    with tempfile.TemporaryDirectory(prefix='069b-mut-', dir=TMP) as td:
        dest = Path(td) / 'source'
        shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns('.git', '__pycache__', '.artifacts', 'core'))
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
                r = subprocess.run(['/home/josh/hamcp217-work/venv-runtime/bin/python', '-m', 'unittest', *modules],
                                   cwd=dest, env=env, stdout=fh, stderr=subprocess.STDOUT, timeout=900)
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
for name, edits, _ in M:
    for f, old, _new in edits:
        n = (SRC / RT / f).read_text().count(old)
        assert n == 1, (name, f, n)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    rows = list(pool.map(run, M))
(BASE / ('mutations.json' if not only else 'mutations-partial.json')).write_text(json.dumps(rows, indent=2))
