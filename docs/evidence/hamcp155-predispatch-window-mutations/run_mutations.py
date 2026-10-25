"""HAMCP-155 pre-dispatch authority window mutations.

Copy the worktree, apply one edit, run the 155 suites plus the window tests
under the enforcing fake Home Assistant clients, and expect failure.  Every
mutant weakens one property of the delete's pre-dispatch drift read: it runs
inside its own short Core-authority window (acquire, consume, revalidate,
finish), is refused when that authority is missing or withdrawn, never under
the not-yet-committed dispatch authority, and leaves the dispatch commit
unchanged.  ``fk_`` mutants remove the fakes' authority enforcement: the
window tests must then notice the authority-less read is no longer refused.
"""
import concurrent.futures, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

SRC = Path('/home/josh/repos/hass-mcp-admin-HAMCP-155-fix')
RT = 'hass_mcp_engineering_beta/ha_mcp_engineering/'
BASE = Path(__file__).resolve().parent
TMP = '/var/tmp/hamcp155fix-tmp/mut'
PY = '/home/josh/hamcp155-work/venv-r1/bin/python'
ALL = [
    'tests.test_hamcp155_predispatch_authority_window',
    'tests.test_hamcp155_configuration_delete_execution',
    'tests.test_hamcp155_configuration_delete_admission',
    'tests.test_hamcp155_configuration_delete_policy',
    'tests.test_hamcp155_configuration_delete_lane',
    'tests.test_hamcp155_review1_probes',
    'tests.test_hamcp155_merge_seams',
]
D = 'f3_configuration/delete.py'
A = 'f3_configuration/adapter.py'
RU = 'f3_runtime/runtime.py'
FK = '/tests/core_authority_fakes.py'
M = [
 ('baseline', []),
 # The re-read moved back outside any Core-authority window.
 ('pw_reread_outside_window',
  [(D, '    PREDISPATCH_READS_HOME_ASSISTANT = True', '    PREDISPATCH_READS_HOME_ASSISTANT = False')]),
 ('pw_adapter_ignores_window',
  [(A, '        if window is None:\n            return await self._predispatch_refusal(operation)',
       '        if True:\n            return await self._predispatch_refusal(operation)')]),
 ('pw_runtime_never_binds_window',
  [(RU, '            token = PREDISPATCH_READ_WINDOW.set(\n                lambda: _predispatch_read_window(\n                    self._core_runtime, guard, prepared, window\n                )\n            )',
        '            token = PREDISPATCH_READ_WINDOW.set(None)')]),
 # Refusal outcomes.
 ('pw_refusal_swallowed',
  [(A, '            return (PREDISPATCH_AUTHORITY_UNAVAILABLE,)', '            return ()')]),
 ('pw_refusal_code_changed',
  [(A, 'PREDISPATCH_AUTHORITY_UNAVAILABLE = "dispatch_authority_unavailable"',
       'PREDISPATCH_AUTHORITY_UNAVAILABLE = "stale_target_state"')]),
 # Window entry.
 ('pw_guard_none_allowed',
  [(RU, '        if guard is None or self._used or guard.commits is not None:',
        '        if self._used or (guard is not None and guard.commits is not None):')]),
 ('pw_reuse_allowed',
  [(RU, '        if guard is None or self._used or guard.commits is not None:',
        '        if guard is None or guard.commits is not None:')]),
 ('pw_after_commit_allowed',
  [(RU, '        if guard is None or self._used or guard.commits is not None:',
        '        if guard is None or self._used:')]),
 ('pw_missing_lease_allowed',
  [(RU, '        if authority is None:\n            raise PredispatchAuthorityRefused()\n        commits = self._core.consume(authority)',
        '        commits = self._core.consume(authority)')]),
 ('pw_unconsumed_lease_not_released',
  [(RU, '            self._core.release(authority)\n            raise PredispatchAuthorityRefused()',
        '            raise PredispatchAuthorityRefused()')]),
 ('pw_unconsumed_lease_admitted',
  [(RU, '        if commits is None:\n            self._core.release(authority)\n            raise PredispatchAuthorityRefused()\n',
        '')]),
 # Authorizer inside the window.
 ('pw_authorizer_ignores_lease',
  [(RU, '                return False\n            return current()\n', '                return False\n            return True\n')]),
 ('pw_authorizer_ignores_outer',
  [(RU, '            if outer is not None and outer() is not True:\n                return False\n            return current()',
        '            return current()')]),
 ('pw_authorizer_not_installed',
  [(RU, '            return current()\n\n        telemetry.core_dispatch_authorizer = authorize_core_dispatch\n',
        '            return current()\n\n')]),
 # Window exit.
 ('pw_dispatch_authorizer_not_restored',
  [(RU, '            self._dispatch_window._telemetry.core_dispatch_authorizer = (\n                self._dispatch_authorizer\n            )\n',
        '')]),
 ('pw_exit_recheck_removed',
  [(RU, '        if exc_type is None and not current:\n            raise PredispatchAuthorityRefused()',
        '        if False:\n            raise PredispatchAuthorityRefused()')]),
 ('pw_finish_skipped',
  [(RU, '        finally:\n            self._core.finish(self._commits)\n        if exc_type',
        '        finally:\n            pass\n        if exc_type')]),
 # The fakes stop enforcing the real client's authority check.
 ('fk_enforcement_removed',
  [(FK, '    if telemetry is not None and not telemetry.authorize_core_dispatch():',
        '    if False:')]),
]
only = set(sys.argv[1:])
if only:
    M = [m for m in M if m[0] in only]
(BASE / 'logs').mkdir(exist_ok=True)


def run(case):
    name, edits = case
    with tempfile.TemporaryDirectory(prefix='155w-mut-', dir=TMP) as td:
        dest = Path(td) / 'source'
        shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns('.git', '__pycache__', '.artifacts', 'core', 'logs'))
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
(BASE / ('results.json' if not only else 'mutations-partial.json')).write_text(json.dumps(rows, indent=2))
