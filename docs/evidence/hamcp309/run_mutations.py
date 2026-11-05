"""HAMCP-309 mutations. Copies repository inputs; never mutates the tree under test.

Run from the repository root. A mutant is killed only by an assertion failure in
its named test; an error, import failure or survival is not a kill.
"""
import json, shutil, subprocess, sys
from pathlib import Path

ROOT = Path.cwd(); OUT = ROOT / '.artifacts/mutation-hamcp309'
shutil.rmtree(OUT, ignore_errors=True); COPY = OUT / 'copy'; COPY.mkdir(parents=True)
listed = lambda *extra: subprocess.check_output(['git', 'ls-files', '-z', *extra]).decode().split('\0')
for name in sorted((set(listed()) | set(listed('--others', '--exclude-standard'))) - {''}):
    path = ROOT / name
    if path.is_file():
        target = COPY / name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(path, target)

WORKFLOW = 'scripts/core_evidence_workflow.py'
STOP = '''        if any(official(r) and r["tag_name"] == predecessor for r in batch):
            return releases
'''
EXHAUSTED = '''        if len(batch) < 100:
            # Exhausted: the listing is the complete candidate set.
            return releases
    raise ValueError("release discovery did not reach the signed predecessor within the page bound")'''
OLD_LOOP = '''        if len(batch) < 100:
            break
    else:
        raise ValueError("release discovery exceeds pagination bound")
    return releases'''
RECORD = 'r["tag_name"] == predecessor for r in batch'
SENTINEL = 'version_key(r["tag_name"]) <= version_key(predecessor) for r in batch'
SHORT = STOP.replace('return releases', 'return releases[:len(releases) - len(batch)]')
GUARD = '            if not ordered:'
D = 'ReleaseDiscovery.'
mutants = [
    # Round 1 (Codex M1): the stop is the predecessor's own record, never a version comparison.
    ('restore the version-sentinel stop', WORKFLOW, RECORD, SENTINEL,
     D + 'test_lower_backport_before_higher_release_is_not_a_boundary'),
    ('restore the version-sentinel stop (no record)', WORKFLOW, RECORD, SENTINEL,
     D + 'test_records_below_predecessor_without_its_record_refused'),
    ('drop the ordering guard', WORKFLOW, GUARD, '            if False:', D + 'test_listing_order_violation_refused'),
    ('stop before the predecessor page', WORKFLOW, STOP, SHORT,
     D + 'test_lower_backport_before_higher_release_is_not_a_boundary'),
    ('stop before the predecessor page (orders)', WORKFLOW, STOP, SHORT,
     D + 'test_both_publication_orders_select_numeric_maximum'),
    ('published_at preferred over created_at', WORKFLOW, 'release.get("created_at") or release.get("published_at")',
     'release.get("published_at") or release.get("created_at")', D + 'test_published_at_used_only_without_created_at'),
    # Original round: page cap and early stop, re-targeted at the round-1 loop.
    ('restore the 10-page raise', WORKFLOW, STOP + EXHAUSTED, OLD_LOOP, D + 'test_target_on_page_one_of_over_1000_releases'),
    ('restore the 10-page raise (prepare)', WORKFLOW, STOP + EXHAUSTED, OLD_LOOP,
     D + 'test_scheduled_prepare_over_1000_releases_reaches_o3'),
    ('drop the early stop', WORKFLOW, STOP, '', D + 'test_target_on_page_one_of_over_1000_releases'),
    ('drop the early stop (prepare)', WORKFLOW, STOP, '', D + 'test_scheduled_prepare_over_1000_releases_reaches_o3'),
    ('accept a prerelease (selection)', 'scripts/core_release_evidence.py', 'if not r.get("draft") and not r.get("prerelease")',
     'if not r.get("draft")', D + 'test_exact_tag_prerelease_or_draft_refused_before_sources'),
    ('accept a prerelease (scheduled selection)', 'scripts/core_release_evidence.py', 'if not r.get("draft") and not r.get("prerelease")',
     'if not r.get("draft")', D + 'test_target_on_page_one_of_over_1000_releases'),
    ('accept a prerelease (discovery boundary)', WORKFLOW, 'return (not release.get("draft") and not release.get("prerelease")',
     'return (not release.get("draft")', D + 'test_prereleases_and_drafts_never_end_discovery'),
    ('exact tag identity unchecked', WORKFLOW, 'if not isinstance(release, dict) or release.get("tag_name") != requested:',
     'if release is None:', D + 'test_exact_tag_identity_and_shape_refused'),
    ('request shapes the path unchecked', WORKFLOW, '''        if not stable_version(requested):
            raise ValueError("requested release is not an official stable release")
        release =''', '        release =', D + 'test_malformed_request_refused_before_any_read'),
]
results = []
for i, (name, file, before, after, test) in enumerate(mutants, 1):
    path = COPY / file; original = path.read_text()
    assert original.count(before) == 1, (name, original.count(before))
    path.write_text(original.replace(before, after))
    for cache in COPY.rglob('__pycache__'):
        shutil.rmtree(cache)
    proc = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'tests.test_core_release_evidence.' + test],
                          cwd=COPY, capture_output=True, timeout=120)
    raw = proc.stdout + proc.stderr; (OUT / f'{i:02d}.log').write_bytes(raw)
    killed = proc.returncode and b'FAIL:' in raw and b'AssertionError' in raw and b'ERROR:' not in raw
    classification = 'assertion' if killed else ('invalid' if proc.returncode else 'survived')
    results.append({'id': i, 'name': name, 'file': file, 'test': test, 'classification': classification,
                    'exit_code': proc.returncode})
    path.write_text(original)
    print(i, name, classification, flush=True)
# Control: the unmutated copy passes the whole module.
proc = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'tests.test_core_release_evidence'],
                      cwd=COPY, capture_output=True, timeout=300)
(OUT / 'control.log').write_bytes(proc.stdout + proc.stderr)
results.append({'id': 0, 'name': 'control (unmutated copy)', 'classification': 'pass' if proc.returncode == 0 else 'fail',
                'exit_code': proc.returncode})
(OUT / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
if proc.returncode or any(r['classification'] != 'assertion' for r in results if r['id']):
    sys.exit(1)
