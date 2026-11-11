"""HAMCP-327 R1 correction evidence: baseline failures and mutations.

Run from the repository root with the task venv's Python. Everything runs in a
disposable ``git clone --shared`` under ``.artifacts/mutation-hamcp327``; the
tree under test is never modified.

Phase 1 (baseline): the reviewed head's runtime with the corrected tests. Each
H1/M1 regression test must fail by an assertion; the H1 fixture test must pass
(the fixture shape is genuine on the reviewed head).

Phase 2 (mutations): the corrected commit, one source mutation at a time that
reopens a finding. A mutant is killed only by an assertion failure in its named
test; an error, import failure or survival is not a kill. L1 is a test defect,
so its evidence is one mutant run against both the reviewed-head test (expected
to survive: the defect) and the repaired test (expected to be killed).

Phase 3 (control): the unmutated corrected copy passes both modules.
"""
import json, os, shutil, subprocess, sys
from pathlib import Path

REVIEWED = '4943cdcd543008b53a25a7c30c092ff451c25153'
FIXED = '64a1bf2fa7b6139e9677f54343bd006cd6787cdd'
ROOT = Path.cwd(); OUT = ROOT / '.artifacts/mutation-hamcp327'
shutil.rmtree(OUT, ignore_errors=True); OUT.mkdir(parents=True); COPY = OUT / 'copy'
git = lambda *args: subprocess.run(['git', '-C', str(COPY), *args], check=True, capture_output=True)
subprocess.run(['git', 'clone', '-q', '--shared', '--no-checkout', str(ROOT), str(COPY)], check=True)
git('checkout', '-q', '--detach', FIXED)
RUNTIME = 'hass_mcp_engineering_beta/ha_mcp_engineering/helper_value'
SAFETY = RUNTIME + '/safety.py'; SERVICE = RUNTIME + '/service.py'
FAIL_TO_ASK = 'tests.test_hamcp327_fail_to_ask.'; TYPED = 'tests.test_typed_helper_value.'
GAP = FAIL_TO_ASK + 'UnattributedGapTests.'; DISCLOSE = FAIL_TO_ASK + 'ResultDisclosureTests.'
PRESERVE = FAIL_TO_ASK + 'FailurePreservationTests.'
ASSESS = TYPED + 'HelperBoundaryTests.test_assess_only_exact_evidence_warns'
L1_TEST = PRESERVE + 'test_unacknowledged_guest_mode_never_dispatches_in_any_evidence_state'
env = dict(os.environ, TMPDIR=str(ROOT / '.artifacts/tmp'), PYTHONDONTWRITEBYTECODE='1')


def run(test, log, timeout=300):
    for cache in COPY.rglob('__pycache__'):
        shutil.rmtree(cache)
    proc = subprocess.run([sys.executable, '-B', '-m', 'unittest', test], cwd=COPY, env=env,
                          capture_output=True, timeout=timeout)
    raw = proc.stdout + proc.stderr; (OUT / log).write_bytes(raw)
    if proc.returncode == 0:
        return 'pass', proc.returncode
    killed = b'FAIL:' in raw and b'AssertionError' in raw and b'ERROR:' not in raw
    return ('assertion' if killed else 'invalid'), proc.returncode


results = {'reviewed_head': REVIEWED, 'fixed_commit': FIXED, 'python': sys.version.split()[0],
           'baseline': [], 'mutants': [], 'control': []}
# Phase 1: reviewed-head runtime, corrected tests.
git('checkout', REVIEWED, '--', RUNTIME)
baseline = [
    ('H1', GAP + 'test_fixture_reproduces_the_unattributed_gap_shape', 'pass'),
    ('H1', GAP + 'test_literal_critical_consumer_asks_despite_unrelated_gap', 'assertion'),
    ('H1', ASSESS, 'assertion'),
    ('M1', DISCLOSE + 'test_no_change_lists_known_consumers_from_real_index', 'assertion'),
    ('M1', DISCLOSE + 'test_reconciliation_discloses_and_never_replays', 'assertion'),
    ('M1', DISCLOSE + 'test_reconciliation_with_unavailable_evidence_is_unchanged', 'assertion'),
    ('M1', PRESERVE + 'test_already_current_value_with_failing_reader_is_still_no_change', 'assertion'),
    ('M1', TYPED + 'HelperTests.test_value_already_current_is_no_change_without_dispatch', 'assertion'),
    ('M1', TYPED + 'HelperTests.test_safety_critical_noop_does_not_warn', 'assertion'),
    ('M1', TYPED + 'HelperTests.test_repeated_request_and_detail_reads_never_mutate', 'assertion'),
    ('L1', L1_TEST, 'pass'),
]
for i, (finding, test, expected) in enumerate(baseline, 1):
    classification, code = run(test, f'baseline-{i:02d}.log')
    results['baseline'].append({'id': i, 'finding': finding, 'test': test, 'expected': expected,
                                'classification': classification, 'exit_code': code})
    print('baseline', i, finding, test.rsplit('.', 1)[1], classification, flush=True)
git('checkout', FIXED, '--', RUNTIME)

INEXACT = '''_INEXACT_ANALYSIS_SCOPES = frozenset({"coverage_failure_consequence_scope", "unreadable_consequence_scope",
                                      "conservative_consequence_scope"})'''
WARNING = 'warning = critical if exact else [item for item in critical if _literal_inexact(item)]'
LITERAL = 'return bool(names - _SCOPE_ONLY_RELATIONSHIPS) and bool(names & _INEXACT_ANALYSIS_SCOPES)'
GATE = 'safety_critical = bool(binding.get("physical_consequence") == SAFETY_CRITICAL and warning)'
NOT_EVALUATED = 'if safety is None or safety.get("evaluated") is not True:'
DISCLOSURE_CALL = '            safety = await self._disclosure(request.entity_id, reason)'
GUARDED_READ = '''        try:
            safety = assess(await self.dependency_reader(entity_id))
        except Exception as exc:
            safety = assess({"available": False, "failure_category": type(exc).__name__[:64]})'''
GATE_KEYS = '_GATE_KEYS = frozenset({"safety_critical", "confirmation_reason"})'
MARKER = '{"evaluated": False, "reason": reason, "disclosure": DISCLOSURE}'
mutants = [
    # H1: a literal critical consumer under an unrelated gap's conservative scope asks.
    ('H1', 'conservative scope is not an inexact-analysis marker', SAFETY, INEXACT,
     '_INEXACT_ANALYSIS_SCOPES = frozenset({"coverage_failure_consequence_scope", "unreadable_consequence_scope"})',
     GAP + 'test_literal_critical_consumer_asks_despite_unrelated_gap', 'fixed'),
    ('H1', 'conservative scope is not an inexact-analysis marker (assess)', SAFETY, INEXACT,
     '_INEXACT_ANALYSIS_SCOPES = frozenset({"coverage_failure_consequence_scope", "unreadable_consequence_scope"})',
     ASSESS, 'fixed'),
    ('H1', 'zero exact obligations never warn', SAFETY, WARNING, 'warning = critical if exact else []',
     GAP + 'test_literal_critical_consumer_asks_despite_unrelated_gap', 'fixed'),
    ('H1', 'a scope marker alone warns (no literal check)', SAFETY, LITERAL,
     'return bool(names & _INEXACT_ANALYSIS_SCOPES)', ASSESS, 'fixed'),
    # M1: no-change and reconciliation results disclose consumers, informationally.
    ('M1', 'no-change result carries no disclosure', SERVICE, NOT_EVALUATED, 'if safety is None:',
     DISCLOSE + 'test_no_change_lists_known_consumers_from_real_index', 'fixed'),
    ('M1', 'no-change result carries no disclosure (typed)', SERVICE, NOT_EVALUATED, 'if safety is None:',
     TYPED + 'HelperTests.test_value_already_current_is_no_change_without_dispatch', 'fixed'),
    ('M1', 'reconciliation carries no disclosure', SERVICE, DISCLOSURE_CALL,
     DISCLOSURE_CALL + ' if safety else {"evaluated": False, "reason": reason}',
     DISCLOSE + 'test_reconciliation_discloses_and_never_replays', 'fixed'),
    ('M1', 'reconciliation carries no disclosure (typed)', SERVICE, DISCLOSURE_CALL,
     DISCLOSURE_CALL + ' if safety else {"evaluated": False, "reason": reason}',
     TYPED + 'HelperTests.test_repeated_request_and_detail_reads_never_mutate', 'fixed'),
    ('M1', 'a disclosure read failure fails the no-op', SERVICE, GUARDED_READ,
     '        safety = assess(await self.dependency_reader(entity_id))',
     PRESERVE + 'test_already_current_value_with_failing_reader_is_still_no_change', 'fixed'),
    ('M1', 'disclosure carries gate fields', SERVICE, GATE_KEYS, '_GATE_KEYS = frozenset()',
     DISCLOSE + 'test_no_change_lists_known_consumers_from_real_index', 'fixed'),
    ('M1', 'disclosure claims an evaluated gate', SERVICE, MARKER, MARKER.replace('False', 'True'),
     DISCLOSE + 'test_reconciliation_discloses_and_never_replays', 'fixed'),
    # L1: available evidence that should warn never does. The reviewed-head test
    # cannot see it (its first state recursed into unavailable evidence).
    ('L1', 'available evidence never warns (reviewed-head test)', SAFETY, GATE, 'safety_critical = False',
     L1_TEST, 'reviewed'),
    ('L1', 'available evidence never warns (repaired test)', SAFETY, GATE, 'safety_critical = False',
     L1_TEST, 'fixed'),
]
TESTS = 'tests/test_hamcp327_fail_to_ask.py'
for i, (finding, name, file, before, after, test, test_version) in enumerate(mutants, 1):
    path = COPY / file; original = path.read_text()
    assert original.count(before) == 1, (name, original.count(before))
    path.write_text(original.replace(before, after))
    if test_version == 'reviewed':
        git('checkout', REVIEWED, '--', TESTS)
    classification, code = run(test, f'mutant-{i:02d}.log')
    expected = 'survived' if test_version == 'reviewed' else 'assertion'
    classification = 'survived' if classification == 'pass' else classification
    results['mutants'].append({'id': i, 'finding': finding, 'name': name, 'file': file, 'test': test,
                               'test_version': test_version, 'expected': expected,
                               'classification': classification, 'exit_code': code})
    path.write_text(original); git('checkout', FIXED, '--', TESTS)
    print('mutant', i, finding, name, classification, flush=True)

git('diff', '--exit-code', FIXED)  # the copy is back to the corrected commit
for i, module in enumerate(('tests.test_hamcp327_fail_to_ask', 'tests.test_typed_helper_value'), 1):
    classification, code = run(module, f'control-{i:02d}.log', timeout=900)
    results['control'].append({'module': module, 'classification': classification, 'exit_code': code})
    print('control', module, classification, flush=True)
(OUT / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
ok = (all(r['classification'] == r['expected'] for r in results['baseline'] + results['mutants'])
      and all(r['classification'] == 'pass' for r in results['control']))
print('ALL AS EXPECTED' if ok else 'UNEXPECTED RESULT', flush=True)
sys.exit(0 if ok else 1)
