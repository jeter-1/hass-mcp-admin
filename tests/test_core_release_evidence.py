"""HAMCP-279 offline contracts: useful inheritance and fail-closed boundaries."""
import ast
import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import core_release_evidence as e
from core_lane_bindings import REVIEWED_CORE_BINDINGS, reviewed_lane_binding

RUN = {'GITHUB_RUN_ID': '279', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_SHA': 'a' * 40}


def provenance():
    return e.read_json(ROOT / 'tests/fixtures/core_2026_10_3_lane_provenance.json')


def refs():
    j = e.read_json(ROOT / 'upstream-trust/ha-core-release-registry.json')
    tip = next(x for x in j['envelopes'] if x['sequence'] == 15)
    return next(x for x in tip['entries'] if x['version'] == '2026.10.3')['capabilities']


def sources():
    hashes = {p: 'b' * 64 for p in e.manifest()['paths']}
    return ({'version': '2026.10.2', 'commit': 'c'*40, 'tag_commit': 'c'*40, 'hashes': hashes},
            {'version': '2026.10.3', 'commit': provenance()['source_commit'],
             'tag_commit': provenance()['source_commit'], 'hashes': dict(hashes)})


def ctx(mode='main'):
    a, b = sources()
    return {'model': 'core-evidence-context-v1', 'run': dict(RUN),
            'manifest_sha256': e.digest(e.canonical(e.manifest())), 'anchor': '2026.10.3',
            'binding': asdict(REVIEWED_CORE_BINDINGS['2026.10.3']), 'provenance': provenance(),
            'predecessor': '2026.10.2', 'mode': mode, 'proof': e.compare_sources(a,b), 'fixture': False}


class Detection(unittest.TestCase):
    def test_attempt_survives_artifact_expiry(self):
        from core_evidence_workflow import attempt_releases
        self.assertEqual(attempt_releases([], [{'name': 'core-release-attempt-2026.10.4'}]), {'2026.10.4'})
        self.assertEqual(attempt_releases([{'name': 'core-release-attempt-2026.10.4'}], []), {'2026.10.4'})
        self.assertEqual(attempt_releases([], [{'name': 'core-release-attempt-2026.10.0b1'}, {'name':'prepare'}]), set())
    def test_stable_filters(self):
        for v in ['2026.10.4', '2027.1.0']:
            self.assertTrue(e.stable_version(v))
        for v in ['2026.10.0b1','2026.10.0rc1','2026.10.0.dev0','2026.13.0','2026.01.0','v2026.10.0','2026.10.1\n','2026.10.01',None]:
            with self.subTest(v=v): self.assertFalse(e.stable_version(v))
    def test_published_noop(self):
        self.assertIsNone(e.select_release([{'tag_name':'2026.10.4'}],{'2026.10.4'},[]))
    def test_pending_and_success_noop(self):
        for status,conclusion in [('queued',None),('in_progress',None),('waiting',None),('completed','success')]:
            with self.subTest(status=status):
                self.assertIsNone(e.select_release([{'tag_name':'2026.10.4'}],[],[{'release':'2026.10.4','status':status,'conclusion':conclusion}]))
    def test_failed_can_retry(self):
        self.assertEqual(e.select_release([{'tag_name':'2026.10.4'}],[],[{'release':'2026.10.4','status':'completed','conclusion':'failure'}]),'2026.10.4')
    def test_single_numeric_latest_and_prerelease(self):
        values=[{'tag_name':'2026.10.9'},{'tag_name':'2026.10.10'},{'tag_name':'2026.11.0','prerelease':True},{'tag_name':'2026.12.0','draft':True}]
        self.assertEqual(e.select_release(values,[],[]),'2026.10.10')
    def test_requested_must_be_release(self):
        with self.assertRaises(ValueError): e.select_release([],[],[],'2026.10.4')


class RefusalLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / '.artifacts').mkdir(exist_ok=True)

    def history(self, *, refusal=True, conclusion='failure', job_conclusion='failure'):
        from core_evidence_workflow import observed_attempts
        jobs = [{'name': 'core-release-attempt-2026.10.4', 'conclusion': 'success'}]
        if refusal:
            jobs.append({'name': 'core-release-refused-2026.10.4', 'conclusion': job_conclusion})
        return observed_attempts({'status':'completed', 'conclusion':conclusion}, [], jobs)

    def test_refusal_terminal_after_artifact_expiry(self):
        runs = self.history()
        self.assertEqual(runs[0]['outcome'], 'refused')
        self.assertIsNone(e.select_release([{'tag_name':'2026.10.4'}], [], runs))
        self.assertEqual(e.select_release([{'tag_name':'2026.10.4'}, {'tag_name':'2026.10.5'}], [], runs), '2026.10.5')

    def test_explicit_dispatch_retries_refusal_only(self):
        releases = [{'tag_name':'2026.10.4'}]
        self.assertEqual(e.select_release(releases, [], self.history(), '2026.10.4', retry_refused=True), '2026.10.4')
        self.assertIsNone(e.select_release(releases, [], self.history(), retry_refused=True))
        self.assertIsNone(e.select_release(releases, [], self.history(), '2026.10.4'))
        self.assertIsNone(e.select_release(releases, [], self.history(refusal=False, conclusion='success'), '2026.10.4', retry_refused=True))
        pending=self.history();pending[0]['status']='in_progress'
        self.assertIsNone(e.select_release(releases, [], pending, '2026.10.4', retry_refused=True))

    def test_ordinary_failure_retryable_not_terminal(self):
        for job_conclusion in ('skipped', None, 'success'):
            runs = self.history(job_conclusion=job_conclusion)
            self.assertEqual(runs[0]['outcome'], 'attempt')
            self.assertEqual(e.select_release([{'tag_name':'2026.10.4'}], [], runs), '2026.10.4')
        runs = self.history(refusal=False)
        self.assertEqual(runs[0]['outcome'], 'attempt')
        self.assertEqual(e.select_release([{'tag_name':'2026.10.4'}], [], runs), '2026.10.4')

    def prepare_case(self, directory, *, missing=False, error=None, prior=None, event='schedule', requested=None):
        import core_evidence_workflow as w
        before=e.read_json(ROOT/'docs/evidence/hamcp279/2026.10.3.json')
        # Synthetic workflow fixture covers the current expanded inventory;
        # historical measurements below remain tied to their original 337 paths.
        before['hashes']={p:before['hashes'].get(p,'b'*64) for p in e.manifest()['paths']}
        before['tag_commit']=before['commit']
        after=copy.deepcopy(before);after.update(version='2026.10.4',commit='f'*40,tag_commit='f'*40)
        path=next(iter(after['hashes']))
        if error != 'provenance':
            if missing:del after['hashes'][path]
            else:after['hashes'][path]='d'*64
        if error == 'tag':after['tag_commit']='a'*40
        history=[] if prior is None else [{'id':278,'created_at':'2026-11-02','status':'completed','conclusion':'failure'}]
        def api(endpoint, **kwargs):
            if error == 'api':raise ValueError('API unavailable')
            if '/releases?' in endpoint:return [{'tag_name':'2026.10.4','published_at':'2026-11-01T00:00:00Z'}]
            if endpoint.endswith('/releases/tags/2026.10.4'):return {'tag_name':'2026.10.4','published_at':'2026-11-01'}
            if '/git/ref/heads/' in endpoint:return None
            if '/workflows/' in endpoint:return {'workflow_runs':history}
            if '/artifacts?' in endpoint:return {'artifacts':[]}
            if '/jobs?' in endpoint:
                self.assertIn('filter=all',endpoint)
                return {'jobs':[{'name':'core-release-refused-2026.10.4','conclusion':prior}]}
            raise AssertionError(endpoint)
        env={**RUN,'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':w.REPOSITORY,
             'GITHUB_EVENT_NAME':event,'GITHUB_OUTPUT':str(directory/'outputs'),
             'GITHUB_STEP_SUMMARY':str(directory/'summary')}
        with patch.dict(os.environ,env,clear=True), patch.object(w,'registry_input',return_value=((ROOT/'upstream-trust/ha-core-release-registry.json').read_bytes(),False)), patch.object(w,'github',side_effect=api), patch.object(w,'source',side_effect=[before,after]) as source, patch.object(w,'image_provenance',side_effect=ValueError('provenance failure')) as image:
            if error:
                with self.assertRaises(ValueError):w.prepare(directory,False,requested)
                self.assertFalse((directory/'outputs').exists())
            else:
                w.prepare(directory,False,requested)
                image.assert_not_called()
            return path, source.call_count

    def test_refusal_prepare_and_final_job_fail_with_files_and_commits(self):
        for missing in (False,True):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory(dir=ROOT/'.artifacts') as tmp:
                d=Path(tmp);path,_=self.prepare_case(d,missing=missing)
                values=dict(line.split('=',1) for line in (d/'outputs').read_text().splitlines())
                self.assertEqual(values['refused'],'true');self.assertEqual(values['run'],'false')
                self.assertEqual(values['release'],'2026.10.4');self.assertFalse((d/'release.json').exists())
                proof=e.read_json(d/'comparison.json')
                result=subprocess.run([sys.executable,str(ROOT/'scripts/core_evidence_workflow.py'),'refuse','--directory',tmp],capture_output=True,text=True,timeout=10,
                                      env={**os.environ,'GITHUB_STEP_SUMMARY':str(d/'final-summary')})
                self.assertEqual(result.returncode,1)
                for expected in ('binding PR required: '+path,proof['predecessor_commit'],proof['release_commit']):
                    self.assertIn(expected,result.stdout);self.assertIn(expected,(d/'summary').read_text())
                    self.assertIn(expected,(d/'final-summary').read_text())

    def test_schedule_skips_but_explicit_dispatch_may_refuse_again(self):
        for event,requested,retries in [('schedule',None,False),('workflow_dispatch',None,False),('workflow_dispatch','2026.10.4',True)]:
            with self.subTest(event=event,requested=requested),tempfile.TemporaryDirectory(dir=ROOT/'.artifacts') as tmp:
                d=Path(tmp);_,calls=self.prepare_case(d,prior='failure',event=event,requested=requested)
                values=dict(line.split('=',1) for line in (d/'outputs').read_text().splitlines())
                self.assertEqual(values['refused'],str(retries).lower())
                self.assertEqual(calls,2 if retries else 0)
                self.assertEqual(values['release'],'2026.10.4' if retries else 'none')

    def test_api_tag_and_provenance_errors_do_not_record_refusal(self):
        for error in ('api','tag','provenance'):
            with self.subTest(error=error),tempfile.TemporaryDirectory(dir=ROOT/'.artifacts') as tmp:
                self.prepare_case(Path(tmp),error=error)

    def test_notification_job_fails_only_for_refusal(self):
        import yaml
        jobs=yaml.safe_load((ROOT/'.github/workflows/core-release-evidence.yml').read_text())['jobs']
        job=jobs['refusal']
        self.assertEqual(job['name'],'core-release-refused-${{ needs.prepare.outputs.release }}')
        self.assertEqual(job['needs'],'prepare')
        self.assertIn("needs.prepare.outputs.refused == 'true'",job['if'])
        self.assertIn("github.event_name != 'pull_request'",job['if'])
        self.assertNotIn('continue-on-error',job)
        commands=[s for s in job['steps'] if 'run' in s]
        self.assertEqual(len(commands),1);self.assertNotIn('continue-on-error',commands[0])
        self.assertEqual(commands[0]['run'],'python3 scripts/core_evidence_workflow.py refuse --directory "$RUNNER_TEMP/core-evidence-input"')
        self.assertEqual(job['permissions'],{'contents':'read'})
        self.assertIn("needs.prepare.outputs.refused != 'true'",jobs['attempt']['if'])

    def test_permanent_provenance_docs_and_owner_setup(self):
        for name in ('CORE_RELEASE_REGISTRY.md','CORE_REGISTRY_ONE_TAP_RUNBOOK.md'):
            doc=(ROOT/'docs'/name).read_text()
            self.assertIn('`registry-candidates/*` branches are permanent provenance records.',doc)
            self.assertRegex(doc,r'Never delete\s+them, including after publication or in cleanup sweeps')
            self.assertNotIn('preserve these records until the release is present',doc)
        doc=(ROOT/'docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md').read_text()
        for text in ('OWNER SETUP','Josh must create','blocks deletion and force-push','allowing the workflow to create','HAMCP-284','Codex does not change repository settings'):
            self.assertIn(text,doc)


def tags(newer_than, count):
    """Synthetic official stable tags, newest first, above or at/below a floor."""
    values = [f'{y}.{m}.{p}' for y in range(2045, 2009, -1) for m in range(12, 0, -1) for p in range(9, -1, -1)]
    key = e.version_key(newer_than)
    return [v for v in values if (e.version_key(v) > key) == (count > 0)][:abs(count)]


def stamp(index):
    # Newest first: each later listing position was created one minute earlier.
    return (datetime(2026, 11, 4, tzinfo=timezone.utc) - timedelta(minutes=index)).isoformat().replace('+00:00', 'Z')


ABSENT = object()  # A record value that removes the listing key entirely.


class ReleaseDiscovery(unittest.TestCase):
    """HAMCP-309: exact-tag dispatch and early-stop scheduled discovery."""
    @classmethod
    def setUpClass(cls):
        (ROOT / '.artifacts').mkdir(exist_ok=True)
        import core_evidence_workflow as w
        signed, _ = w.journal((ROOT / 'upstream-trust/ha-core-release-registry.json').read_bytes())
        cls.entries = [x.to_mapping() for x in signed.accepted.entries]
        cls.published = {x['version'] for x in cls.entries}
        assert max(cls.published, key=e.version_key) == '2026.10.3'

    def large_history(self):
        # Prerelease and draft decoys first, then target, predecessor and >1,000 older releases.
        head = [{'tag_name': '2026.11.0b1', 'prerelease': True}, {'tag_name': '2026.11.0', 'prerelease': True},
                {'tag_name': '2026.12.0', 'draft': True}, {'tag_name': '2026.10.4'}, {'tag_name': '2026.10.3'}]
        return head + [{'tag_name': v} for v in tags('2026.10.3', -1250)]

    def api(self, history, calls, tag=None):
        def answer(endpoint, **kwargs):
            calls.append((endpoint, kwargs))
            if '/releases/tags/' in endpoint:
                return tag
            match = re.fullmatch(r'repos/home-assistant/core/releases\?per_page=100&page=(\d+)', endpoint)
            if match is None:
                raise AssertionError(endpoint)
            page = int(match.group(1))
            return [{k: v for k, v in {'created_at': stamp(i), 'published_at': stamp(i), **r}.items() if v is not ABSENT}
                    for i, r in enumerate(history[(page - 1) * 100:page * 100], (page - 1) * 100)]
        return answer

    def discover(self, history, requested=None, tag=None, entries=None):
        import core_evidence_workflow as w
        calls = []
        with patch.object(w, 'github', side_effect=self.api(history, calls, tag)):
            try:
                releases = w.discover_releases(self.entries if entries is None else entries, requested)
            except ValueError as error:
                self.fail(f'discovery refused: {error}')
        return releases, calls

    def refused(self, pattern, history, requested=None, tag=None, entries=None):
        import core_evidence_workflow as w
        calls = []
        with patch.object(w, 'github', side_effect=self.api(history, calls, tag)):
            with self.assertRaisesRegex(ValueError, pattern):
                w.discover_releases(self.entries if entries is None else entries, requested)
        return calls

    def prepare(self, directory, history, requested=None, tag=None):
        import core_evidence_workflow as w
        before = e.read_json(ROOT / 'docs/evidence/hamcp279/2026.10.3.json'); before['tag_commit'] = before['commit']
        after = copy.deepcopy(before); after.update(version='2026.10.4', commit='f' * 40, tag_commit='f' * 40)
        after['hashes'][next(iter(after['hashes']))] = 'd' * 64
        calls = []; listing = self.api(history, calls, tag)
        def api(endpoint, **kwargs):
            if '/git/ref/heads/' in endpoint:return None
            if '/workflows/' in endpoint:return {'workflow_runs': []}
            return listing(endpoint, **kwargs)
        env = {**RUN, 'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': w.REPOSITORY, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
               'GITHUB_OUTPUT': str(directory / 'outputs'), 'GITHUB_STEP_SUMMARY': str(directory / 'summary')}
        raw = (ROOT / 'upstream-trust/ha-core-release-registry.json').read_bytes()
        with patch.dict(os.environ, env, clear=True), patch.object(w, 'registry_input', return_value=(raw, False)), \
                patch.object(w, 'github', side_effect=api), patch.object(w, 'source', side_effect=[before, after]) as source, \
                patch.object(w, 'image_provenance', side_effect=ValueError('provenance failure')):
            w.prepare(directory, False, requested)
        return calls, [c.args[0] for c in source.call_args_list]

    def test_target_on_page_one_of_over_1000_releases(self):
        history = self.large_history()
        self.assertGreater(len(history), 1000)
        releases, calls = self.discover(history)
        self.assertEqual([c[0] for c in calls], ['repos/home-assistant/core/releases?per_page=100&page=1'])
        self.assertEqual(len(releases), 100)
        self.assertEqual(e.select_release(releases, self.published, []), '2026.10.4')

    def test_scheduled_prepare_over_1000_releases_reaches_o3(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.artifacts') as tmp:
            d = Path(tmp)
            try:
                calls, sources = self.prepare(d, self.large_history())
            except ValueError as error:
                self.fail(f'prepare refused: {error}')
            self.assertEqual(sources, ['2026.10.3', '2026.10.4'])
            self.assertEqual([c[0] for c in calls], ['repos/home-assistant/core/releases?per_page=100&page=1'])
            values = dict(line.split('=', 1) for line in (d / 'outputs').read_text().splitlines())
            self.assertEqual((values['release'], values['refused']), ('2026.10.4', 'true'))

    def test_prereleases_and_drafts_never_end_discovery(self):
        decoys = [{'tag_name': f'2026.11.0b{i}', 'prerelease': True} for i in range(97)]
        decoys += [{'tag_name': '2026.10.3', 'prerelease': True}, {'tag_name': '2026.10.3', 'draft': True},
                   {'tag_name': 'not-a-version'}]
        history = decoys + [{'tag_name': '2026.10.4'}, {'tag_name': '2026.10.3'}] + [{'tag_name': v} for v in tags('2026.10.3', -300)]
        releases, calls = self.discover(history)
        self.assertEqual(len(calls), 2)
        self.assertEqual(e.select_release(releases, self.published, []), '2026.10.4')

    def test_predecessor_never_reached_within_bound_refused(self):
        import core_evidence_workflow as w
        history = [{'tag_name': v} for v in tags('2026.10.3', 1100)]
        calls = self.refused('did not reach the signed predecessor', history)
        self.assertEqual([c[0] for c in calls],
                         [f'repos/home-assistant/core/releases?per_page=100&page={n}' for n in range(1, w.RELEASE_PAGE_BOUND + 1)])
        self.assertEqual(w.RELEASE_PAGE_BOUND, 10)

    def test_lower_backport_before_higher_release_is_not_a_boundary(self):
        # Codex M1 counterexample: page 1 ends nothing although 2026.9.9 <= 2026.10.3.
        history = ([{'tag_name': '2026.9.9'}, {'tag_name': '2026.10.4'}]
                   + [{'tag_name': f'2026.11.0b{i}', 'prerelease': True} for i in range(98)]
                   + [{'tag_name': '2026.11.0'}, {'tag_name': '2026.10.3'}])
        releases, calls = self.discover(history)
        self.assertEqual(len(calls), 2)
        self.assertEqual(e.select_release(releases, self.published, []), '2026.11.0')

    def test_both_publication_orders_select_numeric_maximum(self):
        betas = [{'tag_name': f'2026.11.0b{i}', 'prerelease': True} for i in range(99)]
        for first, second in (('2026.10.4', '2026.11.0'), ('2026.11.0', '2026.10.4')):
            with self.subTest(first=first):
                history = betas + [{'tag_name': first}, {'tag_name': second}, {'tag_name': '2026.10.3'}]
                releases, calls = self.discover(history)
                self.assertEqual(len(calls), 2)
                self.assertEqual(e.select_release(releases, self.published, []), '2026.11.0')

    def test_records_below_predecessor_without_its_record_refused(self):
        import core_evidence_workflow as w
        history = [{'tag_name': v} for v in tags('2026.10.3', -1101) if v != '2026.10.3']
        self.assertEqual((len(history), any(r['tag_name'] == '2026.10.3' for r in history)), (1100, False))
        calls = self.refused('did not reach the signed predecessor', history)
        self.assertEqual(len(calls), w.RELEASE_PAGE_BOUND)

    def test_listing_order_violation_refused(self):
        newer = '2026-11-05T00:00:00Z'
        within = [{'tag_name': '2026.10.4'}, {'tag_name': '2026.11.0', 'created_at': newer}, {'tag_name': '2026.10.3'}]
        across = ([{'tag_name': f'2026.11.0b{i}', 'prerelease': True} for i in range(100)]
                  + [{'tag_name': '2026.11.0', 'created_at': newer}, {'tag_name': '2026.10.3'}])
        fallback = [{'tag_name': '2026.10.4', 'created_at': None, 'published_at': '2026-11-03T00:00:00Z'},
                    {'tag_name': '2026.11.0', 'created_at': None, 'published_at': newer}, {'tag_name': '2026.10.3'}]
        for name, history in (('within', within), ('across', across), ('fallback', fallback)):
            with self.subTest(name=name):
                self.refused('release listing is not newest-first', history)

    def test_published_at_used_only_without_created_at(self):
        history = [{'tag_name': '2026.11.0', 'created_at': None, 'published_at': '2026-11-03T00:00:00Z'},
                   {'tag_name': '2026.10.3', 'created_at': '2026-11-02T00:00:00Z', 'published_at': '2026-11-05T00:00:00Z'}]
        releases, _ = self.discover(history)
        self.assertEqual(e.select_release(releases, self.published, []), '2026.11.0')

    def test_unorderable_listing_refused(self):
        for record in ({'created_at': None, 'published_at': None}, {'created_at': 'yesterday'},
                       {'created_at': 7}, {'created_at': '2026-11-03'}):
            with self.subTest(record=record):
                history = [{'tag_name': '2026.11.0'}, {'tag_name': '2026.10.4', **record}, {'tag_name': '2026.10.3'}]
                self.refused('release listing (order cannot be established|is not newest-first)', history)

    # Round 2 (Codex focused re-review): its eight failing probes, then the explicit contract cases.
    def test_naive_timestamps_alone_or_uniformly_naive_refused(self):
        for history in ([{'tag_name': '2026.11.0', 'created_at': '2026-11-04T00:00:00'}],
                        [{'tag_name': '2026.11.0', 'created_at': '2026-11-04'},
                         {'tag_name': '2026.10.3', 'created_at': '2026-11-03'}],
                        [{'tag_name': '2026.11.0', 'created_at': ABSENT, 'published_at': '2026-11-04T00:00:00'}]):
            with self.subTest(history=history):
                self.refused('release listing order cannot be established', history)

    def test_present_invalid_created_at_never_falls_back(self):
        for bad in ('', 0, False, [], {}, 'yesterday', 7, '2026-11-04T00:00:00', '2026-11-04'):
            with self.subTest(created_at=bad):
                history = [{'tag_name': '2026.11.0', 'created_at': bad, 'published_at': '2026-11-04T00:00:00Z'}]
                self.refused('release listing order cannot be established', history)

    def test_invalid_published_at_fallback_refused(self):
        for absent in (None, ABSENT):
            for bad in ('2026-11-04T00:00:00', '2026-11-04', 'yesterday', '', 7, False, ABSENT, None):
                with self.subTest(created_at=absent, published_at=bad):
                    history = [{'tag_name': '2026.11.0', 'created_at': absent, 'published_at': bad}]
                    self.refused('release listing order cannot be established', history)

    def test_absent_created_at_falls_back_to_valid_published_at(self):
        for absent in (None, ABSENT):
            with self.subTest(created_at=absent):
                history = [{'tag_name': '2026.11.0', 'created_at': absent, 'published_at': '2026-11-04T06:00:00+06:00'},
                           {'tag_name': '2026.10.3', 'created_at': '2026-11-04T00:00:00Z', 'published_at': ABSENT}]
                releases, calls = self.discover(history)
                self.assertEqual(len(calls), 1)
                self.assertEqual(e.select_release(releases, self.published, []), '2026.11.0')

    def test_one_parser_reads_both_fields(self):
        import core_evidence_workflow as w
        history = [{'tag_name': '2026.11.0', 'created_at': None, 'published_at': '2026-11-03T00:00:00Z'},
                   {'tag_name': '2026.10.3', 'created_at': '2026-11-02T00:00:00Z'}]
        with patch.object(w, 'listing_timestamp', wraps=w.listing_timestamp) as parser:
            self.discover(history)
        self.assertEqual([c.args for c in parser.call_args_list], [('2026-11-03T00:00:00Z',), ('2026-11-02T00:00:00Z',)])

    def test_complete_short_listing_is_unchanged(self):
        releases, calls = self.discover([{'tag_name': '2026.10.4'}])
        self.assertEqual(len(calls), 1)
        self.assertEqual(e.select_release(releases, self.published, []), '2026.10.4')

    def test_no_signed_predecessor_refused_without_reads(self):
        self.assertEqual(self.refused('no signed predecessor', self.large_history(), entries=[]), [])

    def test_exact_tag_success_without_listing(self):
        release = {'tag_name': '2026.10.4', 'published_at': '2026-11-01'}
        releases, calls = self.discover(self.large_history(), '2026.10.4', release)
        self.assertEqual(calls, [('repos/home-assistant/core/releases/tags/2026.10.4', {'optional': True})])
        self.assertEqual(releases, [release])
        self.assertEqual(e.select_release(releases, self.published, [], '2026.10.4'), '2026.10.4')
        with tempfile.TemporaryDirectory(dir=ROOT / '.artifacts') as tmp:
            try:
                calls, sources = self.prepare(Path(tmp), self.large_history(), '2026.10.4', release)
            except ValueError as error:
                self.fail(f'prepare refused: {error}')
            self.assertEqual(sources, ['2026.10.3', '2026.10.4'])
            self.assertEqual([c[0] for c in calls], ['repos/home-assistant/core/releases/tags/2026.10.4'])

    def test_exact_tag_404_refused(self):
        calls = self.refused('not an official stable release', self.large_history(), '2026.10.4', None)
        self.assertEqual(calls, [('repos/home-assistant/core/releases/tags/2026.10.4', {'optional': True})])

    def test_exact_tag_identity_and_shape_refused(self):
        for tag in ({'tag_name': '2026.10.5'}, {'tag_name': '2026.10.4\n'}, [{'tag_name': '2026.10.4'}], {}):
            with self.subTest(tag=tag):
                self.refused('not an official stable release', [], '2026.10.4', tag)

    def test_malformed_request_refused_before_any_read(self):
        for requested in ('2026.11.0b1', '2026.10.4/../../x', 'v2026.10.4', '2026.10.04', ''):
            with self.subTest(requested=requested):
                self.assertEqual(self.refused('not an official stable release', [], requested, {'tag_name': requested}), [])

    def test_exact_tag_prerelease_or_draft_refused_before_sources(self):
        for flag in ('prerelease', 'draft'):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory(dir=ROOT / '.artifacts') as tmp:
                with self.assertRaisesRegex(ValueError, 'not an official stable release'):
                    self.prepare(Path(tmp), [], '2026.10.4', {'tag_name': '2026.10.4', flag: True})
                self.assertFalse((Path(tmp) / 'outputs').exists())


class O3(unittest.TestCase):
    def test_identical_inherits_complete_proof(self):
        a,b=sources(); proof=e.compare_sources(a,b)
        self.assertTrue(proof['inherited']); self.assertEqual(proof['differing_files'],[])
        self.assertEqual(len(proof['files']),len(e.manifest()['paths']))
        e.verify_proof(proof,a['version'],b['version'],b['commit'])
    def test_differing_names_file_and_refuses(self):
        a,b=sources(); p=next(iter(a['hashes'])); b['hashes'][p]='d'*64
        proof=e.compare_sources(a,b)
        self.assertFalse(proof['inherited']); self.assertEqual(proof['differing_files'],[p]); self.assertEqual(proof['outcome'],'binding PR required')
        with self.assertRaises(ValueError): e.verify_proof(proof,a['version'],b['version'],b['commit'])
    def test_missing_file_refuses_even_both_missing(self):
        for both in (False,True):
            a,b=sources(); p=next(iter(a['hashes'])); del b['hashes'][p]
            if both: del a['hashes'][p]
            self.assertEqual(e.compare_sources(a,b)['differing_files'],[p])
    def test_tag_sha_mismatch(self):
        a,b=sources(); b['tag_commit']='d'*40
        with self.assertRaises(ValueError): e.compare_sources(a,b)
    def test_malformed_hash_refuses(self):
        a,b=sources(); p=next(iter(a['hashes'])); a['hashes'][p]=b['hashes'][p]='bad'
        self.assertFalse(e.compare_sources(a,b)['inherited'])
    def test_derived_swaps_only_provenance_and_index(self):
        a,b=sources(); old={'binding':asdict(REVIEWED_CORE_BINDINGS['2026.10.1']),'provenance':provenance()}
        old['provenance'].update(version=a['version'],source_commit=a['commit'])
        original=copy.deepcopy(old); new=e.derive_binding(old,provenance(),e.compare_sources(a,b))
        self.assertEqual(old,original)
        for key in ('device_registry','typed_operations','child_consumer'): self.assertEqual(new['binding'][key],old['binding'][key])
        self.assertEqual(new['provenance'],provenance())
        self.assertEqual(new['binding']['image_index_digest'],provenance()['image_index_digest'])
    def test_predecessor_sha_mismatch(self):
        a,b=sources(); old={'binding':asdict(REVIEWED_CORE_BINDINGS['2026.10.3']),'provenance':provenance()};old['provenance']['version']=a['version']
        with self.assertRaises(ValueError):e.derive_binding(old,provenance(),e.compare_sources(a,b))
    def test_real_pair_negative_four_paths(self):
        p=ROOT/'docs/evidence/hamcp279'; a=e.read_json(p/'2026.10.1.json');b=e.read_json(p/'2026.10.3.json')
        a['tag_commit']=a['commit'];b['tag_commit']=b['commit']
        result=e.compare_sources(a,b,paths=sorted(a['hashes']))
        self.assertEqual(result['differing_files'],[
            'homeassistant/auth/__init__.py','homeassistant/components/cloud/alexa_config.py',
            'homeassistant/components/config/auth_provider_homeassistant.py','homeassistant/helpers/config_validation.py'])
        self.assertFalse(result['inherited'])
    def test_recorded_hit_rate_recomputes(self):
        p=ROOT/'docs/evidence/hamcp279'; r=e.read_json(p/'results.json'); passed=0
        for pair in r['pairs']:
            a=e.read_json(p/(pair['predecessor']+'.json')); b=e.read_json(p/(pair['release']+'.json'))
            a['tag_commit']=a['commit'];b['tag_commit']=b['commit']
            historical=sorted(e.read_json(p/'2026.10.3.json')['hashes']); self.assertEqual(len(historical),337)
            self.assertTrue(set(historical)<=set(e.manifest()['paths']))
            actual=e.compare_sources(a,b,paths=historical);self.assertEqual(actual['inherited'],pair['inherited']); passed+=actual['inherited']
        self.assertEqual((passed,len(r['pairs'])),(2,11))


class ProofAdmission(unittest.TestCase):
    def test_main_and_derived_admitted(self):
        for mode in ['main','derived']:
            self.assertEqual(e.verify_context(ctx(mode),RUN)['mode'],mode)
    def test_binding_without_proof_refuses(self):
        value=ctx('derived');value['proof']={}
        with self.assertRaises(ValueError):e.verify_context(value,RUN)
    def test_wrong_run_attempt_head_refuses(self):
        for key in RUN:
            value=ctx();value['run'][key]='different'
            with self.subTest(key=key),self.assertRaises(ValueError):e.verify_context(value,RUN)
    def test_missing_extra_proof_file_refuses(self):
        for extra in (False,True):
            value=ctx('derived')
            if extra:value['proof']['files'].append(value['proof']['files'][0])
            else:value['proof']['files'].pop()
            with self.assertRaises(ValueError):e.verify_context(value,RUN)
    def test_forged_equal_flag_refuses(self):
        value=ctx('derived');value['proof']['files'][0]['release_sha256']='d'*64
        with self.assertRaises(ValueError):e.verify_context(value,RUN)
    def test_semantic_field_changes_refuse(self):
        for key,val in [('device_registry','child_devices'),('typed_operations',False),('child_consumer','8.4.3')]:
            value=ctx('derived');value['binding'][key]=val
            with self.subTest(key=key),self.assertRaises(ValueError):e.verify_context(value,RUN)
    def test_main_provenance_cannot_be_substituted(self):
        value=ctx();value['provenance']['source_commit']='d'*40
        with self.assertRaises(ValueError):e.verify_context(value,RUN)
    def test_context_hash_checked_and_unbound_refusal_retained(self):
        with patch.dict(os.environ,{},clear=True):self.assertIsNone(reviewed_lane_binding('2026.12.99'))
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'binding.json'; p.write_bytes(e.canonical(ctx()))
            env={**RUN,e.CONTEXT_ENV:str(p),'CORE_EVIDENCE_CONTEXT_SHA256':e.digest(p.read_bytes())}
            with patch.dict(os.environ,env,clear=True):
                self.assertEqual(reviewed_lane_binding('2026.10.3'),REVIEWED_CORE_BINDINGS['2026.10.3'])
                with self.assertRaises(ValueError):reviewed_lane_binding('2026.12.99')
            env['CORE_EVIDENCE_CONTEXT_SHA256']='0'*64
            with patch.dict(os.environ,env,clear=True),self.assertRaises(ValueError):e.context()


class Withholding(unittest.TestCase):
    def test_all_pass_equals_exact_signed_31(self):
        self.assertEqual(e.passing_subset(refs(),dict.fromkeys(e.groups(),True)),refs())
    def test_general_nineteen_stand_or_fall(self):
        result=dict.fromkeys(e.groups(),True);result['general']=False
        selected=e.passing_subset(refs(),result)
        self.assertEqual(len(selected),12);self.assertFalse(set(e.groups()['general'])&{p['capability_id'] for p in selected})
    def test_shared_exposure_lane_withholds_both(self):
        result=dict.fromkeys(e.groups(),True);result['availability_exposure']=False
        self.assertEqual(len(e.passing_subset(refs(),result)),29)
    def test_each_group_independently_withheld(self):
        for group,ids in e.groups().items():
            result=dict.fromkeys(e.groups(),True);result[group]=False
            selected=e.passing_subset(refs(),result)
            self.assertEqual(selected,[p for p in refs() if p['capability_id'] not in ids])
    def test_never_add_from_larger_passed_set(self):
        predecessor=refs()[:26]
        self.assertEqual(e.passing_subset(predecessor,dict.fromkeys(e.groups(),True)),predecessor)
    def test_no_missing_or_truthy_pass(self):
        self.assertEqual(e.passing_subset(refs(),{}),[])
        self.assertEqual(e.passing_subset(refs(),dict.fromkeys(e.groups(),'PASS')),[])
    def test_unmapped_or_duplicate_refuses(self):
        for values in (refs()+[refs()[0]],refs()+[{'capability_id':'unknown'}]):
            with self.assertRaises(ValueError):e.passing_subset(values,{})


class Handoff(unittest.TestCase):
    def test_fixed_targets_computed_hashes(self):
        r=e.handoff_request('2026.10.4',b'candidate',b'evidence',registry_exists=True,dry_run=False)
        self.assertEqual(r['ref'],'registry-candidates/2026.10.4');self.assertEqual(r['workflow'],'core-release-registry-publish.yml');self.assertEqual(r['workflow_ref'],'main')
        self.assertEqual(r['inputs'],{'core_version':'2026.10.4','candidate_ref':r['ref'],'candidate_sha256':e.digest(b'candidate'),'evidence_sha256':e.digest(b'evidence'),'dry_run':'false'})
    def test_pre_cutover_and_dry_run_never_handoff(self):
        for exists,dry in [(False,False),(False,True),(True,True)]:
            self.assertIsNone(e.handoff_request('2026.10.4',b'c',b'e',registry_exists=exists,dry_run=dry))
    def test_target_injection_refused(self):
        for version in ['../main','2026.10.4\nmain','2026.10.4:main']:
            with self.assertRaises(ValueError):e.handoff_request(version,b'c',b'e',registry_exists=True,dry_run=False)
    def test_real_handoff_pre_cutover_has_no_subprocess(self):
        import core_evidence_handoff as h
        with tempfile.TemporaryDirectory() as tmp:
            for f in h.FILES:(Path(tmp)/f).write_bytes(b'{}')
            with patch.dict(os.environ,{'CORE_VERSION':'2026.10.4','DRY_RUN':'false'}),patch.object(h,'github',return_value=None),patch.object(h.subprocess,'run') as run:
                self.assertEqual(h.handoff(tmp),'artifact-only');run.assert_not_called()
    def test_actual_push_and_dispatch_targets_static(self):
        tree=ast.parse((ROOT/'scripts/core_evidence_handoff.py').read_text())
        strings={x.value for x in ast.walk(tree) if isinstance(x,ast.Constant) and isinstance(x.value,str)}
        self.assertIn('HEAD:refs/heads/',strings);self.assertIn('core-release-registry-publish.yml',strings)
        self.assertNotIn('--force',strings);self.assertNotIn('HEAD:refs/heads/registry',strings)
        self.assertIn('registry-candidates/',(ROOT/'scripts/core_release_evidence.py').read_text())


class HandoffExecution(unittest.TestCase):
    def invoke(self, *, existing=False, changed=False, dry=False):
        import core_evidence_handoff as h
        prior=b'previous signed journal'
        evidence=e.canonical({'context':{'fixture':False,'provenance':{'version':'2026.10.4'}}})
        candidate=e.canonical({'review_evidence_sha256':e.digest(evidence),'previous_journal_sha256':e.digest(prior)})
        env={'CORE_VERSION':'2026.10.4','DRY_RUN':'true' if dry else 'false','GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':h.REPOSITORY,'GITHUB_EVENT_NAME':'schedule','CANDIDATE_SHA256':e.digest(candidate),'EVIDENCE_SHA256':e.digest(evidence)}
        calls=[]
        def run(argv,cwd):
            calls.append(argv)
            if 'ls-tree' in argv:return b'core-candidate.json\ncore-review-evidence.json\n'
            if 'show' in argv:
                if changed:return b'different'
                return candidate if argv[-1].endswith('core-candidate.json') else evidence
            return b''
        with tempfile.TemporaryDirectory() as tmp:
            for name,raw in zip(h.FILES,(candidate,evidence)):(Path(tmp)/name).write_bytes(raw)
            with patch.dict(os.environ,env,clear=True),patch.object(h,'github',side_effect=[{'object':{}},{'object':{}} if existing else None]),patch.object(h,'branch_file',return_value=prior),patch.object(h,'run',side_effect=run),patch.object(h.subprocess,'run') as dispatch:
                if changed:
                    with self.assertRaises(ValueError):h.handoff(tmp)
                    dispatch.assert_not_called()
                else:
                    outcome=h.handoff(tmp)
                    if dry:
                        dispatch.assert_not_called();self.assertEqual(calls,[])
                    else:
                        self.assertEqual(outcome,'dispatched-for-owner-approval')
                        argv=dispatch.call_args.args[0]
                        self.assertEqual(argv,['gh','workflow','run','core-release-registry-publish.yml','--repo',h.REPOSITORY,'--ref','main','--json'])
                        inputs=json.loads(dispatch.call_args.kwargs['input'])
                        self.assertEqual(inputs['candidate_sha256'],e.digest(candidate));self.assertEqual(inputs['evidence_sha256'],e.digest(evidence))
        pushes=[a for a in calls if 'push' in a]
        if existing or changed or dry:self.assertEqual(pushes,[])
        else:
            self.assertEqual(len(pushes),1);self.assertEqual(pushes[0][-1],'HEAD:refs/heads/registry-candidates/2026.10.4');self.assertNotIn('--force',pushes[0])
    def test_new_branch_exact_push_and_dispatch(self):self.invoke()
    def test_existing_identical_no_push(self):self.invoke(existing=True)
    def test_existing_different_no_push_or_dispatch(self):self.invoke(existing=True,changed=True)
    def test_dry_run_no_push_or_dispatch(self):self.invoke(dry=True)


class OCIProvenance(unittest.TestCase):
    def test_historical_digest_chain_and_subjects(self):
        import core_evidence_sources as source
        directory=ROOT/'docs/evidence/hamcp222/artifacts-2026.10.3/raw/oci'
        files={ 'sha256:'+e.digest(p.read_bytes()):p.read_bytes() for p in directory.glob('*.json')}
        index=(directory/'index-2026.10.3.json').read_bytes()
        def get(url,headers=None):
            if '/token?' in url:return b'{"token":"synthetic-public-pull-token"}'
            ref=url.rsplit('/',1)[-1]
            return index if ref=='2026.10.3' else files[ref]
        with patch.object(source,'_get',side_effect=get):
            sha,platforms,_=source.image_provenance('2026.10.3',provenance()['source_commit'])
        self.assertEqual(sha,provenance()['image_index_digest']);self.assertEqual(platforms,provenance()['architecture_manifests'])
    def test_arbitrary_version_or_commit_refused_before_network(self):
        import core_evidence_sources as source
        with patch.object(source,'_get') as get:
            for v,c in [('../escape','a'*40),('2026.10.3','bad')]:
                with self.assertRaises(ValueError):source.image_provenance(v,c)
            get.assert_not_called()


class UnsignedAssembly(unittest.TestCase):
    def test_real_31_and_fixture_use_unchanged_add(self):
        import core_evidence_workflow as w
        for mode,case in [('main','coverage'),('derived','fixture')]:
            value=ctx(mode);value['fixture']=case=='fixture'
            with tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp); prepared=base/'prepared'; receipts=base/'receipts';prepared.mkdir();receipts.mkdir()
                raw=(ROOT/'upstream-trust/ha-core-release-registry.json').read_bytes()
                signed,_=w.journal(raw)
                entry=next(e.to_mapping() for e in signed.accepted.entries if e.version=='2026.10.3')
                (prepared/'predecessor.json').write_bytes(raw)
                w.write(prepared/'baseline.json',entry);w.write(prepared/'state.json',{'acceptance':True,'registry_exists':False})
                context_path=base/'context.json';context_path.write_bytes(e.canonical(value));sha=e.digest(context_path.read_bytes())
                w.write(receipts/'cleanup.json',{'clean':True,'run':RUN,'context_sha256':sha})
                for name,ids in e.groups().items():
                    w.write(receipts/(name+'.json'),{'model':'core-evidence-group-v1','group':name,'passed':True,'run':RUN,'release':'2026.10.3','context_sha256':sha,'profiles':ids})
                env={**RUN,e.CONTEXT_ENV:str(context_path),'CORE_EVIDENCE_CONTEXT_SHA256':sha,'GITHUB_OUTPUT':str(base/'out')}
                with patch.dict(os.environ,env,clear=True):w.assemble(prepared,receipts,case)
                candidate=e.read_json(receipts/'candidate/core-candidate.json')
                self.assertEqual(candidate['operation'],'add')
                self.assertNotIn('signature',candidate['envelope'])
                target=next(x for x in candidate['envelope']['entries'] if x['version']=='2026.10.3')
                self.assertEqual(target['capabilities'],refs())
                review=(receipts/'candidate/core-review-evidence.json').read_bytes()
                self.assertEqual(target['evidence_sha256'],e.digest(review))


class ManifestAndWorkflow(unittest.TestCase):
    def test_every_signed_profile_has_source_mapping(self):
        m=e.manifest();self.assertEqual({r['capability_id'] for r in refs()} | {'core.typed_helper_value_operation'},
                                       set(m['profiles'])-{'binding_judgments'})
        self.assertEqual(len(m['paths']),456)
    def test_mechanical_literal_core_paths_covered(self):
        paths=set(e.manifest()['paths'])
        files=list((ROOT/'scripts').glob('*contract*.py'))+[ROOT/'scripts/generate_core_2026_10_device_fixture.py']
        for file in files:
            for node in ast.walk(ast.parse(file.read_text())):
                if isinstance(node,ast.Constant) and isinstance(node.value,str) and re.fullmatch(r'homeassistant/[a-zA-Z0-9_/]+\.py',node.value):
                    self.assertIn(node.value,paths,f'{file.name}:{node.lineno} reads unlisted Core source')
    def test_no_signing_environment_or_secret_and_pinned_actions(self):
        import yaml
        w=yaml.safe_load((ROOT/'.github/workflows/core-release-evidence.yml').read_text())
        self.assertEqual(w['permissions'],{'contents':'read'})
        self.assertEqual(w['jobs']['prepare']['permissions'],{'contents':'read','actions':'read'})
        self.assertEqual(w['jobs']['lanes']['permissions'],{'contents':'read'})
        self.assertEqual(w['jobs']['handoff']['permissions'],{'contents':'write','actions':'write'})
        for name,job in w['jobs'].items():
            self.assertNotIn('environment',job);self.assertEqual(job['runs-on'],'ubuntu-24.04')
            self.assertNotIn('secrets.',json.dumps(job))
            for step in job['steps']:
                if 'uses' in step:self.assertRegex(step['uses'],r'@[0-9a-f]{40}$')
        handoff=w['jobs']['handoff']['if'];self.assertIn("github.event_name != 'pull_request'",handoff);self.assertIn("registry_exists == 'true'",handoff)
    def test_disposable_cleanup_always_and_group_switches(self):
        import yaml
        job=yaml.safe_load((ROOT/'.github/workflows/core-release-evidence.yml').read_text())['jobs']['lanes']
        self.assertEqual(job['env']['REAL_HA_SEQ13_LANES'],'enabled')
        cleanup=[s for s in job['steps'] if 'cleanup' in s.get('name','').lower() or 'removed' in s.get('name','')]
        self.assertTrue(cleanup);self.assertTrue(all(s['if']=='always()' for s in cleanup))


if __name__=='__main__':unittest.main()
